"""clip_keep.py - for every clip (a Recent run, a Saved clip, a Sentry event): what the app has kept from its video, how big the video is, and removing it.

A clip's video may go once what the app keeps from it exists (the rules are those of teslacam_deletable.py):

  metadata   the speed / position / steering of each minute is in <data>/teslacam_timeline (a parked Sentry clip has none to read)
  stills     the one-second stills of each minute are in <data>/teslacam_stills, the drive it lies in has its snapshots, and the minute is not newer than
             the last drive synced from Tessie (later video has no drive to keep snapshots in yet)
  signs      each minute the car went at review.min_kmh or more has been read for signs (app_review)
  event      a horn or Sentry event has its full-size pictures of the moment (event_shots.py: all six cameras, or the one that saw it)

Everything here is worked out from what is kept on this computer (the footage log, the telemetry store, the stills folder, the sign records): no drive is read,
so the answer is the same with every drive away. Only start() touches a drive: it deletes the video files of the clips asked for, or copies them to the Archive
and then deletes them from the drive they were on, after checking again that it is allowed.

    ClipKeep(data_dir, tc, fg).status()      -> dict(clips={"recent"|"saved"|"sentry": {id: dict(n, bytes{role}, size, meta[done, of], stills[...], signs[...], shots[...], shot_cams, ready, waiting)}})
    ClipKeep.start(action, role, items, roots)   action "delete" | "move"; items [{kind, id}]; returns the job status or dict(error=...)
    ClipKeep.start_process(items, roots)     reads what is missing for the clips: metadata, then stills, then signs (from the video on a connected drive)
    ClipKeep.job()                           -> the running or last job
"""
import json
import os
import re
import shutil
import threading
import time

import app_config
import clip_clock as CC
import event_shots as ES
import teslacam_deletable as TD
import teslacam_files as TF
import teslacam_ledger as TLG
import teslacam_scan as TS
import teslacam_stills as ST

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
        self.shots = ES.EventShots(data_dir)

    # ---- what is kept -------------------------------------------------------------------------------------------
    def status(self, force=False):
        with self._lock:
            if not force and self._cache and time.time() - self._cache[0] < TTL:
                return self._cache[1]
        res = self._build()
        with self._lock:
            self._cache = (time.time(), res)
        return res

    def _events(self):
        """{(kind, event folder): event dict (ts, reason, cam)} from the lists of events kept for every drive; an entry that names a reason wins over one that does not."""
        out = {}
        for kind, files in (("sentry", ("sentry_index.json", "teslacam_index_A_sentry.json", "teslacam_index_J_sentry.json", "teslacam_index_U_sentry.json")),
                            ("saved", ("teslacam_index_A_saved.json", "teslacam_index_J_saved.json", "teslacam_index_U_saved.json"))):
            for fn in files:
                try:
                    with open(os.path.join(self.dir, fn), encoding="utf-8") as fh:
                        idx = json.load(fh)
                except (OSError, ValueError):
                    continue
                for eid, e in (idx.items() if isinstance(idx, dict) else ()):
                    if isinstance(e, dict) and (not out.get((kind, eid)) or (e.get("reason") and not out[(kind, eid)].get("reason"))):
                        out[(kind, eid)] = dict(e, id=eid)
        return out

    def _wanted(self, kind, cid, mins, ev):
        """(clip stamp, second, [cameras still to be pictured], [cameras wanted]) for an event whose video is still on a drive; None when it needs no pictures."""
        if not ev or kind == "recent":
            return None
        mom = ES.moment(ev, mins)
        if mom is None:
            return None
        want = ES.wanted(kind, ev, mins[mom[0]]["cams"])
        if not want:
            return None
        have = self.shots.have(kind, cid)
        return mom[0], mom[1], [c for c in want if c not in have], want

    def _build(self):
        events = self._events()
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
            meta, st_, sg, sh = [0, 0], [0, 0], [0, 0], [0, 0]
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
            have = self.shots.have(kind, cid) if kind != "recent" else set()
            w = self._wanted(kind, cid, mins, events.get((kind, cid))) if nbytes else None
            if w:
                sh = [len(w[3]) - len(w[2]), len(w[3])]
            elif have:
                sh = [len(have), len(have)]
            out[kind][cid] = dict(n=len(mins), first=min(mins), bytes=nbytes, size=max(nbytes.values()) if nbytes else 0, meta=meta, stills=st_, signs=sg, shots=sh,
                                  shot_cams=sorted(have), ready=all(x[0] >= x[1] for x in (meta, st_, sg, sh)), waiting=waiting)
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

    def _cams_of(self, kind, cid):
        """{stamp: cameras} the footage log holds for that clip on a drive (not gone)."""
        out = {}
        led = TLG.Ledger(self.dir)
        want = set(self._groups.get((kind, cid), []))
        pre = "%s|%s|" % (kind, "" if kind == "recent" else cid)
        for day in sorted({s[:10] for s in want}):
            for key, rec in led.day(day).items():
                if key.startswith(pre):
                    m = _FILE.match(key[len(pre):])
                    if m and m.group(1) in want and any(not x.get("gone") for x in rec["drives"].values()):
                        out.setdefault(m.group(1), []).append(m.group(2))
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

    def start(self, action, role, items, roots, confirmed=False):
        """Deleting or moving needs `confirmed` (the page sends it only after its confirm bar was answered): a request without it removes nothing."""
        if action in ("delete", "move") and confirmed is not True:
            return dict(error="Not confirmed: nothing was removed.")
        if self._thread and self._thread.is_alive():
            return dict(error="Something is already being done to the drives.")
        plan, err = self.check(action, role, items, roots)
        if err:
            return dict(error=err)
        files = [(k, sz, f) for _, _, fs in plan for k, sz, f in fs]
        self._stop = False
        self._job = dict(running=True, action=action, role=role, files=len(files), done=0, bytes=sum(sz for _, sz, _ in files), bytes_done=0, freed=0, errors=[], current="",
                         clips=len(plan), t0=time.time())
        self._thread = threading.Thread(target=self._run, args=(action, role, files, roots, [dict(kind=k, id=c) for k, c, _ in plan]), daemon=True)
        self._thread.start()
        return self.job()

    # ---- reading what is missing, for the clips asked for ---------------------------------------------------------
    def _paths(self, led, stamps, roots):
        """{stamp: {camera: path}} of the video of those minutes on the connected drives (the first drive in Archive, JOWUA, Tesla USB order that holds each file)."""
        out = {}
        days = sorted({s[:10] for s in stamps})
        for day in days:
            for key, rec in led.day(day).items():
                kind, ev, rest = key.split("|", 2)
                m = _FILE.match(rest)
                if not m or m.group(1) not in stamps or kind not in KINDS:
                    continue
                for r in "AJU":
                    x = rec["drives"].get(r)
                    if x is not None and not x.get("gone") and roots.get(r):
                        out.setdefault(m.group(1), {}).setdefault(m.group(2), TF.front_path(roots[r], dict(src=kind, ev=ev, stamp=m.group(1), cam=m.group(2))))
                        break
        return out

    def start_process(self, items, roots):
        """Reads what the clips still lack from their video on the connected drives: the metadata of each minute, then its stills, then its signs (the signs are looked for
        only in the minutes driven fast enough, so they come last). A clip whose video is on no connected drive is left as it is."""
        if self._thread and self._thread.is_alive():
            return dict(error="Something is already being done to the drives.")
        rv = self.tc._rv()
        if rv.status().get("running"):
            return dict(error="Signs are being read for a drive: wait for it or stop it.")
        st = self.status(force=True)["clips"]
        kind_of = {}
        for it in items:
            if it.get("id") not in (st.get(it.get("kind")) or {}):
                return dict(error="A selected clip is not in the footage log.")
            for stamp in self._groups.get((it["kind"], it["id"]), []):
                kind_of[stamp] = it["kind"]
        paths = self._paths(TLG.Ledger(self.dir), set(kind_of), roots)
        evs = [(it["kind"], it["id"]) for it in items if it["kind"] != "recent"]
        if not paths:
            return dict(error="The video of these clips is on no connected drive.")
        self._stop = False
        self._job = dict(running=True, action="process", step="", step_done=0, step_total=0, steps=[], files=0, done=0, bytes=0, bytes_done=0, freed=0, errors=[], current="",
                         clips=len(items), t0=time.time())
        self._thread = threading.Thread(target=self._process, args=(kind_of, paths, evs), daemon=True)
        self._thread.start()
        return self.job()

    def _step(self, name, total):
        self._job.update(step=name, step_done=0, step_total=total, current="")
        self._job["steps"].append(dict(step=name, total=total))

    def _process(self, kind_of, paths, evs=()):
        j = self._job
        store = self.tc._tl("A")
        stills = self.fg.stills
        stills.width, stills.quality = app_config.get("stills.width", 400), app_config.get("stills.quality", 6)
        first = lambda s: paths[s].get("front") or next(iter(paths[s].values()))      # telemetry is read from another camera when there is no front clip

        def prog(n, stamp):
            j["step_done"], j["current"] = n, stamp
        try:
            todo = [dict(stamp=s, path=first(s)) for s in sorted(paths, reverse=True) if kind_of[s] != "sentry" and store.summary(s) is None]
            self._step("Reading metadata", len(todo))
            if todo and not self._stop:
                TS.read(store, todo, self.dir, stop=lambda: self._stop, progress=prog)
            todo = [dict(stamp=s, role="", path=paths[s]["front"]) for s in sorted(paths, reverse=True) if "front" in paths[s] and not stills.has(s)]
            self._step("Making stills", len(todo))
            if todo and not self._stop:
                ST.make_many(stills, todo, int(app_config.get("stills.workers", 3)), stop=lambda: self._stop, progress=prog, by="clips")
            events = self._events()
            shots = []
            for kind, cid in evs:
                mins = {st: dict(cams=set(c)) for st, c in self._cams_of(kind, cid).items()}
                w = self._wanted(kind, cid, mins, events.get((kind, cid))) if mins else None
                got = {c: paths[w[0]][c] for c in (w[2] if w else ()) if w[0] in paths and c in paths[w[0]]}
                if got:
                    shots.append((kind, events[(kind, cid)], w[0], w[1], got))
            self._step("Saving event pictures", sum(len(x[4]) for x in shots))
            n = 0
            for kind, ev, stamp, sec, got in shots:
                if self._stop:
                    break
                j["current"] = ev["id"]
                n += len(self.shots.make(kind, ev, stamp, sec, got, stop=lambda: self._stop))
                j["step_done"] = n
            rv = self.tc._rv()
            min_kmh = float((app_config.get("review") or {}).get("min_kmh", 30.0))
            done = rv.scanned()
            items = []
            for s in sorted(paths):
                sm = store.summary(s)
                if "front" in paths[s] and isinstance(sm, dict) and sm.get("vmax", 0) >= min_kmh and s not in done:
                    ser = store.series(s)
                    items.append((s, paths[s]["front"], ser["v"] if ser else None))
            self._step("Reading signs", len(items))
            if items and not self._stop:
                rv.start(items)
                while rv.status().get("running"):
                    if self._stop:
                        rv.stop()
                    rs = rv.status()
                    j["step_done"], j["current"] = rs.get("done", 0), rs.get("current", "")
                    time.sleep(0.7)
                rs = rv.status()
                j["step_done"] = rs.get("done", 0)
                if rs.get("error"):
                    j["errors"].append(rs["error"])
        except Exception as e:                      # a drive pulled out, a tool missing: say so and stop
            j["errors"].append("%s: %s" % (type(e).__name__, e))
        finally:
            with self._lock:
                self._cache = None

    def job(self):
        j = dict(self._job)
        j["running"] = bool(self._thread and self._thread.is_alive())
        if "action" not in j:                                  # nothing done since the app started: say what the last job removed
            h = self.history(1)
            if h:
                j["last"] = h[0]
        return j

    def stop(self):
        self._stop = True
        return self.job()

    def _run(self, action, role, files, roots, clips=()):
        led = TLG.Ledger(self.dir)
        touched = set()
        j = self._job
        removed = []
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
                    removed.append(os.path.basename(src))
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
            self._log(action, role, clips, removed, j)
            with self._lock:
                self._cache = None

    # ---- the record of what was removed (kept, so it can be checked after the app was restarted) -----------------------
    LOG = "clip_delete_log.jsonl"

    def _log(self, action, role, clips, removed, j):
        """One line per delete / move job: when, what, which video files were removed from which drive, how much, and what could not be."""
        try:
            with open(os.path.join(self.dir, self.LOG), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(dict(t=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), action=action, role=role, clips=list(clips), removed=len(removed), files=removed[:500],
                                         freed=j.get("freed", 0), errors=j.get("errors", [])[:20], stopped=bool(self._stop)), ensure_ascii=False) + "\n")
        except OSError:
            pass

    def history(self, n=20):
        """The last `n` delete / move jobs, newest first."""
        try:
            with open(os.path.join(self.dir, self.LOG), encoding="utf-8") as fh:
                lines = fh.readlines()[-n:]
        except OSError:
            return []
        out = []
        for ln in reversed(lines):
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
        return out
