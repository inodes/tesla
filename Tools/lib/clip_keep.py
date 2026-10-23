"""clip_keep.py - for every clip (a Recent run, a Saved clip, a Sentry event): what the app has kept from its video, how big the video is, and removing it.

A clip's video may go once what the app keeps from it exists (the rules are those of teslacam_deletable.py):

  metadata   the speed / position / steering of each minute is in <data>/teslacam_timeline (a parked Sentry clip has none to read)
  stills     the one-second stills of each minute are in <data>/teslacam_stills, the drive it lies in has its snapshots, and the minute is not newer than
             the last drive synced from Tessie (later video has no drive to keep snapshots in yet)
  signs      each minute the car went at review.min_kmh or more has been read for signs (app_review)

Everything here is worked out from what is kept on this computer (the footage log, the telemetry store, the stills folder, the sign records): no drive is read,
so the answer is the same with every drive away. Only start() touches a drive: it deletes the video files of the clips asked for, or copies them to the Archive
and then deletes them from the drive they were on, after checking again that it is allowed.

    ClipKeep(data_dir, tc, fg).status()      -> dict(clips={"recent"|"saved"|"sentry": {id: dict(n, bytes{role}, size, meta[done, of], stills[...], signs[...], ready, waiting)}})
    ClipKeep.start(action, role, items, roots)   action "delete" | "move"; items [{kind, id}]; returns the job status or dict(error=...)
    ClipKeep.job()                           -> the running or last job
"""
import os
import re
import shutil
import threading
import time

import app_config
import clip_clock as CC
import teslacam_deletable as TD
import teslacam_files as TF
import teslacam_ledger as TLG

GAP = 75                      # seconds between two minutes that still belong to one Recent run (as in app_teslacam)
TTL = 8.0                     # seconds an answer is reused
_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-([a-z_]+)$")
KINDS = ("recent", "saved", "sentry")


def _runs(stamps):
    """The stamps (sorted by time) cut into runs: a gap of more than GAP seconds starts a new one."""
    out, cur, last = [], [], None
    for st in sorted(stamps, key=CC.utc):
        t = CC.utc(st)
        if cur and t - last > GAP:
            out.append(cur)
            cur = []
        cur.append(st)
        last = t
    if cur:
        out.append(cur)
    return out


class ClipKeep:
    def __init__(self, data_dir, tc, fg):
        self.dir, self.tc, self.fg = data_dir, tc, fg
        self._cache = None
        self._groups = {}
        self._lock = threading.Lock()
        self._job = dict(running=False)
        self._thread = None
        self._stop = False

    # ---- what is kept -------------------------------------------------------------------------------------------
    def status(self, force=False):
        with self._lock:
            if not force and self._cache and time.time() - self._cache[0] < TTL:
                return self._cache[1]
        res = self._build()
        with self._lock:
            self._cache = (time.time(), res)
        return res

    def _build(self):
        store = self.tc._tl("A")
        stills = self.fg.stills.stamps()
        scanned = self.tc._rv().scanned()
        windows = TD.drive_windows(self.dir)
        starts = [w[0] for w in windows]
        synced_until = max([w[1] for w in windows] or [0])
        min_kmh = float((app_config.get("review") or {}).get("min_kmh", 30.0))
        led = TLG.Ledger(self.dir)
        found = {}                                           # (kind, event) -> {stamp: dict(cams, bytes)}
        for day in led.days():
            for key, rec in led.day(day).items():
                kind, ev, rest = key.split("|", 2)
                m = _FILE.match(rest)
                if not m or kind not in KINDS:
                    continue
                node = found.setdefault((kind, ev), {}).setdefault(m.group(1), dict(cams=set(), bytes={}))
                node["cams"].add(m.group(2))
                for r, x in rec["drives"].items():
                    if not x.get("gone"):
                        node["bytes"][r] = node["bytes"].get(r, 0) + rec.get("size", 0)
        groups, out = {}, {k: {} for k in KINDS}
        for (kind, ev), mins in found.items():
            if kind == "recent":
                for run in _runs(mins):
                    groups[("recent", run[0])] = {st: mins[st] for st in run}
            else:
                groups[(kind, ev)] = mins
        self._groups = {k: sorted(v) for k, v in groups.items()}
        for (kind, cid), mins in groups.items():
            meta, st_, sg = [0, 0], [0, 0], [0, 0]
            waiting, nbytes = "", {}
            for st, node in mins.items():
                for r, b in node["bytes"].items():
                    nbytes[r] = nbytes.get(r, 0) + b
                sm = store.summary(st)
                if kind != "sentry" and node["cams"]:                      # a parked Sentry clip has no telemetry to read
                    meta[1] += 1
                    meta[0] += sm is not None
                if "front" in node["cams"]:
                    st_[1] += 1
                    t = CC.utc(st)
                    if st in stills and TD._snap_ok(windows, starts, t) and t <= synced_until:
                        st_[0] += 1
                    elif st in stills and t > synced_until:
                        waiting = "waiting for the drive from Tessie"
                    elif st in stills:
                        waiting = "waiting for the drive's snapshots"
                if isinstance(sm, dict) and sm.get("vmax", 0) >= min_kmh:
                    sg[1] += 1
                    sg[0] += st in scanned
            out[kind][cid] = dict(n=len(mins), first=min(mins), bytes=nbytes, size=max(nbytes.values()) if nbytes else 0, meta=meta, stills=st_, signs=sg,
                                  ready=all(x[0] >= x[1] for x in (meta, st_, sg)), waiting=waiting)
        return dict(clips=out)

    # ---- the files of a clip on one drive -----------------------------------------------------------------------
    def files(self, kind, cid, role, led=None):
        """[(key, size, file dict)] of the clip's video on `role` (as the footage log has it)."""
        led = led or TLG.Ledger(self.dir)
        stamps = self._groups.get((kind, cid))
        if stamps is None:
            self.status(force=True)
            stamps = self._groups.get((kind, cid)) or []
        want = set(stamps)
        ev = "" if kind == "recent" else cid
        pre = "%s|%s|" % (kind, ev)
        out = []
        for day in sorted({s[:10] for s in want}):
            for key, rec in led.day(day).items():
                if not key.startswith(pre):
                    continue
                m = _FILE.match(key[len(pre):])
                x = rec["drives"].get(role)
                if m and m.group(1) in want and x is not None and not x.get("gone"):
                    out.append((key, rec.get("size", 0), dict(src=kind, ev=ev, stamp=m.group(1), cam=m.group(2))))
        return out

    def _archive_has(self, led, key, size):
        e = led.day(key.split("|", 2)[2][:10]).get(key)
        x = e and e["drives"].get("A")
        return bool(x and not x.get("gone") and e.get("size") == size)

    # ---- delete / move ------------------------------------------------------------------------------------------
    def check(self, action, role, items, roots):
        """(plan, error): plan = [(kind, id, files)] when every clip may be handled, else a sentence saying which may not."""
        if action not in ("delete", "move"):
            return None, "Unknown action."
        if role not in roots or not roots[role]:
            return None, "That drive is not connected."
        if action == "move" and (role == "A" or not roots.get("A")):
            return None, "Moving needs the Archive connected, and a clip not already on it."
        if not items:
            return None, "Nothing is selected."
        st = self.status(force=True)["clips"]
        led = TLG.Ledger(self.dir)
        plan = []
        for it in items:
            kind, cid = it.get("kind"), it.get("id")
            rec = (st.get(kind) or {}).get(cid)
            if rec is None:
                return None, "A selected clip is not in the footage log."
            fs = self.files(kind, cid, role, led)
            if not fs:
                continue
            on_archive = role != "A" and all(self._archive_has(led, k, sz) for k, sz, _ in fs)
            if action == "delete" and not (rec["ready"] or on_archive):
                return None, "The video of %s %s is not all kept yet (metadata, stills, signs), and the Archive does not hold a copy." % (kind, cid)
            plan.append((kind, cid, fs))
        if not plan:
            return None, "There is no video of these on that drive."
        if action == "move":
            need = sum(sz for _, _, fs in plan for k, sz, _f in fs if not self._archive_has(led, k, sz))
            try:
                free = shutil.disk_usage(roots["A"]).free
            except OSError:
                return None, "The Archive cannot be read."
            if need + 2 * 1024 ** 3 > free:
                return None, "The Archive has %.1f GB free; this needs %.1f GB." % (free / 1024 ** 3, need / 1024 ** 3)
        return plan, ""

    def start(self, action, role, items, roots):
        if self._thread and self._thread.is_alive():
            return dict(error="Something is already being done to the drives.")
        plan, err = self.check(action, role, items, roots)
        if err:
            return dict(error=err)
        files = [(k, sz, f) for _, _, fs in plan for k, sz, f in fs]
        self._stop = False
        self._job = dict(running=True, action=action, role=role, files=len(files), done=0, bytes=sum(sz for _, sz, _ in files), bytes_done=0, freed=0, errors=[], current="",
                         clips=len(plan), t0=time.time())
        self._thread = threading.Thread(target=self._run, args=(action, role, files, roots), daemon=True)
        self._thread.start()
        return self.job()

    def job(self):
        j = dict(self._job)
        j["running"] = bool(self._thread and self._thread.is_alive())
        return j

    def stop(self):
        self._stop = True
        return self.job()

    def _run(self, action, role, files, roots):
        led = TLG.Ledger(self.dir)
        touched = set()
        j = self._job
        try:
            for key, size, f in files:
                if self._stop:
                    break
                src = TF.front_path(roots[role], f)
                j["current"] = os.path.basename(src)
                try:
                    if action == "move" and not self._archive_has(led, key, size):
                        dst = TF.front_path(roots["A"], f)
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        part = dst + ".part"
                        shutil.copyfile(src, part)
                        if os.path.getsize(part) != os.path.getsize(src):
                            os.remove(part)
                            raise OSError("the copy has a different size")
                        os.replace(part, dst)
                        touched.add(led.mark("A", key, os.path.getsize(dst), True, flush=False))
                    if action == "move" and not self._archive_has(led, key, size):
                        raise OSError("the Archive does not hold it")
                    os.remove(src)
                    j["freed"] += size
                    touched.add(led.mark(role, key, size, False, flush=False))
                except FileNotFoundError:
                    touched.add(led.mark(role, key, size, False, flush=False))
                except OSError as e:
                    j["errors"].append("%s: %s" % (os.path.basename(src), e))
                j["done"] += 1
                j["bytes_done"] += size
                if j["done"] % 40 == 0:
                    led.flush({d for d in touched if d})
        finally:
            led.flush({d for d in touched if d})
            with self._lock:
                self._cache = None
