"""clip_keep.py - for every clip (a Recent run, a Saved clip, a Sentry event): what the app has kept from its video, how big the video is, and removing it.

A clip's video may go once what the app keeps from it exists (the rules are those of teslacam_deletable.py):

  metadata   the speed / position / steering of each minute is in <data>/teslacam_timeline (a parked Sentry clip has none to read)
  stills     the one-second stills of each minute are in <data>/teslacam_stills, the drive it lies in has its snapshots, and the minute is not newer than
             the last drive synced from Tessie (later video has no drive to keep snapshots in yet)
  signs      each minute the car went at review.min_kmh or more (end_kmh in a drive's last end_s) has been read for signs (app_review)
  event      a horn or Sentry event has its full-size pictures of the moment (event_shots.py: all six cameras, or the one that saw it)
  window     a Saved or Sentry event has the pictures around its moment, one a second, 30 s either side, from the camera that saw it (event_stills.py)

Everything here is worked out from what is kept on this computer (the footage log, the telemetry store, the stills folder, the sign records): no drive is read,
so the answer is the same with every drive away. Only start() touches a drive: it deletes the video files of the clips asked for, or copies them to the Archive
and then deletes them from the drive they were on, after checking again that it is allowed.

    ClipKeep(data_dir, tc, fg).status()      -> dict(clips={"recent"|"saved"|"sentry": {id: dict(n, bytes{role}, files{role}, in_archive{role}, size, meta[done, of], stills[...], signs[...], shots[...], shot_cams, ready, waiting, wait_n, todo [something a press of Read what is missing can still do])}})
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
import drive_roles as DR
import run_cuts as RC
import home_ignore as IGN
import clips_purged as PG
import event_shots as ES
import event_stills as EV
import teslacam_deletable as TD
import teslacam_files as TF
import teslacam_ledger as TLG
import teslacam_scan as TS
import teslacam_stills as ST
import sign_speed as SS

GAP = 75                      # seconds between two minutes that still belong to one Recent run (as in app_teslacam)
TTL = 8.0                     # seconds an answer is reused
_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-([a-z_]+)$")
KINDS = ("recent", "saved", "sentry")


def _runs(stamps):
    """The stamps (sorted by time) cut into runs: a gap of more than GAP seconds starts a new one."""
    out, cur, last = [], [], None
    for st in sorted(stamps, key=CC.utc):
        t = CC.utc(st)
        if cur and (t - last > GAP or RC.is_cut(st)):
            out.append(cur)
            cur = []
        cur.append(st)
        last = t
    if cur:
        out.append(cur)
    return out


def writable(role):
    """The drives the app moves video from and deletes from: every drive it reads (a folder drive such as a rescue copy included)."""
    return role in DR.read_roles()


class ClipKeep:
    def __init__(self, data_dir, tc, fg):
        self.dir, self.tc, self.fg = data_dir, tc, fg
        self._cache = None
        self._gen = 0                   # counts the times what is kept changed: a build that began before the last change is not kept
        self._building = False
        self._flight = threading.Lock()
        self.on_change = None           # f() when a job ends: files moved, deleted or kept
        self._groups = {}
        self._lock = threading.Lock()
        self._job = dict(running=False)
        self._thread = None
        self._stop = False
        self.shots = ES.EventShots(data_dir)
        self.evst = EV.EventStills(data_dir)

    # ---- what is kept -------------------------------------------------------------------------------------------
    # the steps of an update and what each still has to do for one clip: the same numbers as the TeslaCam page's buttons (PSTEPS in app.js)
    STEP_LEFT = (
        ("telemetry", lambda c: max(0, c["meta"][1] - c["meta"][0])),
        ("stills", lambda c: max(0, c["stills"][1] - c["stills"][0] - (c.get("wait_n") or 0) - (c.get("snap_n") or 0))),
        ("snapshots", lambda c: c.get("snap_n") or 0),
        ("signs", lambda c: max(0, c["signs"][1] - c["signs"][0]) + (c.get("refine_n") or 0)),
        ("pictures", lambda c: max(0, (c.get("shots") or [0, 0])[1] - (c.get("shots") or [0, 0])[0]) + max(0, (c.get("window") or [0, 0])[1] - (c.get("window") or [0, 0])[0])),
    )

    def needs_by_drive(self, roots, clips=None, fresh=False):
        """{role: dict(clips, telemetry, stills, snapshots, signs, pictures)}: what an update would still do, per connected drive. One definition with the
        page's "N updates needed": a clip counts once, on the first connected drive (in read order) that holds its video, as the update reads it from there;
        a clip parked at Home (ignored) or with no video on a connected drive counts nowhere."""
        clips = (self.status(force=fresh) if clips is None else dict(clips=clips))["clips"]      # fresh: built now, not the answer kept from before (a count worked out once and kept must not come from an old look)
        conn = [r for r in DR.read_roles() if (roots or {}).get(r)]
        out = {r: dict(clips=0, **{k: 0 for k, _f in self.STEP_LEFT}) for r in conn}
        for kind in clips:
            for c in clips[kind].values():
                if c.get("ignored"):
                    continue
                r = next((x for x in conn if (c.get("bytes") or {}).get(x)), None)
                if r is None:
                    continue
                left = {k: f(c) for k, f in self.STEP_LEFT}
                if any(left.values()):
                    out[r]["clips"] += 1
                    for k, v in left.items():
                        out[r][k] += v
        return out

    def status(self, force=False):
        """What is kept per clip. An answer older than TTL is returned at once and rebuilt behind (the build takes seconds on a big library); after a job, or with
        nothing built yet, or with force, the caller waits for the build."""
        with self._lock:
            c = self._cache
            if not force and c:
                if time.time() - c[0] < TTL:
                    return c[1]
                if not self._building:
                    self._building = True
                    threading.Thread(target=self._refresh, daemon=True).start()
                return c[1]
        return self._rebuild()

    def invalidate(self):
        """What is kept changed (new cuts): the look already built is kept but counts as old, so it is answered at once and built again behind; a build already
        under way is not kept (it may have read the old state)."""
        with self._lock:
            self._gen += 1
            if self._cache:
                self._cache = (0, self._cache[1])

    def _settle(self):
        """A job changed what is on the drives: the old look is dropped and built again here, before the job counts as finished, so the page's next look finds it
        at once and no request has to start its own build."""
        with self._lock:
            self._gen += 1
            self._cache = None
        try:
            self._rebuild()
        except Exception:
            pass

    def _rebuild(self):
        t0 = time.time()
        with self._flight:                                   # one build at a time: whoever waited finds the one that finished meanwhile
            with self._lock:
                c = self._cache
                if c and c[0] >= t0:
                    return c[1]
            for _ in range(3):
                with self._lock:
                    g = self._gen
                res = self._build()
                with self._lock:
                    if g == self._gen:
                        self._cache = (time.time(), res)
                        return res
            return res

    def _refresh(self):
        try:
            self._rebuild()
        except Exception:
            pass
        finally:
            with self._lock:
                self._building = False

    def _events(self):
        """{(kind, event folder): event dict (ts, reason, cam)} from the lists of events kept for every drive; an entry that names a reason wins over one that does not."""
        out = {}
        import app_sentry
        roles = DR.read_roles()
        for kind, files in (("sentry", ["sentry_index.json"] + ["teslacam_index_%s_sentry.json" % r for r in roles]),
                            ("saved", ["teslacam_index_%s_saved.json" % r for r in roles])):
            for fn in files:
                try:
                    with open(app_sentry.index_path(self.dir, fn), encoding="utf-8") as fh:
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
        have = self.shots.have(kind, cid) | self.shots.tried(kind, cid)
        return mom[0], mom[1], [c for c in want if c not in have], want

    def _hi(self, kind, cid, mins, ev):
        """(camera, [(clip stamp, first second, last second)]) of the pictures wanted around an event whose video is on a drive; None when it needs none."""
        if not ev or kind == "recent" or self.evst.info(kind, cid).get("tried"):
            return None
        cam = EV.camera(kind, ev)
        win = EV.window(ev, {s: n["cams"] for s, n in mins.items()}, cam)
        return (cam, win) if win else None

    def _build(self):
        events = self._events()
        store = self.tc._tl("A")
        stills = self.fg.stills.stamps()
        nostill = {k[6:] for k in getattr(self.fg.stills, "unreadable", dict)() if k.startswith("stamp|")}      # minutes whose video gave no picture when tried: not counted as missing
        stl = set(stills) | nostill
        rv = self.tc._rv()
        scanned = rv.scanned()
        unref = getattr(self.tc._rv(), "unrefined", lambda: set())()      # minutes with signs that have not had the second look
        windows = TD.drive_windows(self.dir)
        starts = [w[0] for w in windows]
        synced_until = TD.synced_to(self.dir, windows)
        led = TLG.Ledger(self.dir)
        found = {}                                           # (kind, event) -> {stamp: dict(cams, bytes)}
        for day in led.days():
            for key, rec in led.day(day).items():
                kind, ev, rest = key.split("|", 2)
                m = _FILE.match(rest)
                if not m or kind not in KINDS or (kind == "recent" and PG.has(m.group(1))) or (kind != "recent" and PG.has_event(kind, ev)):
                    continue
                node = found.setdefault((kind, ev), {}).setdefault(m.group(1), dict(cams=set(), bytes={}, files={}, arch={}))
                node["cams"].add(m.group(2))
                a_has = bool(rec["drives"].get("A") and not rec["drives"]["A"].get("gone"))
                for r, x in rec["drives"].items():
                    if not x.get("gone"):
                        node["bytes"][r] = node["bytes"].get(r, 0) + rec.get("size", 0)
                        node["files"][r] = node["files"].get(r, 0) + 1
                        if r != "A" and a_has:
                            node["arch"][r] = node["arch"].get(r, 0) + 1
        groups, out = {}, {k: {} for k in KINDS}
        for (kind, ev), mins in found.items():
            if kind == "recent":
                for run in _runs(mins):
                    groups[("recent", run[0])] = {st: mins[st] for st in run}
            else:
                groups[(kind, ev)] = mins
        self._groups = {k: sorted(v) for k, v in groups.items()}
        ignored = set()                                      # the minutes of what is parked at an ignored place (Home): left alone
        husks = []                                           # clips with nothing left: no video on any drive, nothing kept
        for (kind, cid), mins in groups.items():
            if not any(node["bytes"] for node in mins.values()) and not any(st in stills or st in scanned or store.summary(st) is not None for st in mins) \
                    and not (kind != "recent" and (self.shots.have(kind, cid) or self.evst.info(kind, cid).get("n"))):
                husks.append((kind, cid, mins))
                continue
            meta, st_, sg, sh = [0, 0], [0, 0], [0, 0], [0, 0]
            waiting, wait_n, snap_n, refine_n, nbytes, nfiles, narch = "", 0, 0, 0, {}, {}, {}
            parked = kind == "recent" and RC.kind(min(mins, key=CC.utc)) == "park"        # a parked section lies in no drive: its stills never wait for Tessie's drive or its snapshots
            ign = False
            if kind == "recent" and parked:
                p0 = RC.pos(min(mins, key=CC.utc))
                ign = bool(p0) and IGN.at(p0[0], p0[1])
            elif kind == "sentry":
                ev0 = events.get((kind, cid)) or {}
                ign = IGN.at(ev0.get("lat"), ev0.get("lon"))           # Sentry only records parked
            if ign:
                ignored.update(mins)
            for st, node in mins.items():
                for r, c in node["files"].items():
                    nfiles[r] = nfiles.get(r, 0) + c
                for r, c in node["arch"].items():
                    narch[r] = narch.get(r, 0) + c
                for r, b in node["bytes"].items():
                    nbytes[r] = nbytes.get(r, 0) + b
                sm = store.summary(st)
                vid = bool(node["bytes"])                                  # video of this minute is on a drive: a minute whose video is gone everywhere cannot be read any more, so it is not missing anything
                if kind != "sentry" and node["cams"] and (vid or sm is not None):      # a parked Sentry clip has no telemetry to read
                    meta[1] += 1
                    meta[0] += sm is not None
                if "front" in node["cams"] and (vid or st in stl):
                    st_[1] += 1
                    t = CC.utc(st)
                    if st in stl and (parked or (TD._snap_ok(windows, starts, t) and t <= synced_until)):
                        st_[0] += 1
                    elif st in stl and t > synced_until:
                        waiting = "waiting for the drive from Tessie"
                        wait_n += 1
                    elif st in stl:
                        waiting = "drive snapshots not built yet"
                        snap_n += 1
                if SS.wanted(st, sm) and (vid or st in scanned):
                    sg[1] += 1
                    sg[0] += st in scanned
                if st in unref and vid:
                    refine_n += 1
            if ign:                                          # nothing is kept from it and nothing is missing: it can only be deleted
                meta, st_, sg, waiting, wait_n, snap_n, refine_n = [0, 0], [0, 0], [0, 0], "", 0, 0, 0
            read = [st for st in mins if scanned.get(st)]
            sign_n = len(rv.signs(read)) if read else 0                          # the signs read in the clip (one sign seen in several stills is one)
            have = self.shots.have(kind, cid) if kind != "recent" else set()
            w = self._wanted(kind, cid, mins, events.get((kind, cid))) if nbytes and not ign else None
            if w:
                sh = [len(w[3]) - len(w[2]), len(w[3])]
            elif have:
                sh = [len(have), len(have)]
            hi = [0, 0]                                                          # the pictures around the event: one set, made or not
            if kind != "recent":
                got = self.evst.info(kind, cid).get("n")
                hw = self._hi(kind, cid, mins, events.get((kind, cid))) if nbytes and not ign else None
                hi = [1, 1] if got else ([0, 1] if hw else [0, 0])
            out[kind][cid] = dict(n=len(mins), first=min(mins), bytes=nbytes, files=nfiles, in_archive=narch, size=max(nbytes.values()) if nbytes else 0, meta=meta, stills=st_, signs=sg, shots=sh, window=hi, tier=self.evst.info(kind, cid).get("tier", "") if kind != "recent" else "",
                                  shot_cams=sorted(have), ready=all(x[0] >= x[1] for x in (meta, st_, sg, sh, hi)) and refine_n == 0, waiting=waiting, wait_n=wait_n, snap_n=snap_n, refine_n=refine_n,
                                  sign_n=sign_n, todo=any(x[0] < x[1] for x in (meta, sg, sh, hi)) or st_[0] + wait_n < st_[1] or refine_n > 0, ignored=ign)
        IGN.set_stamps(ignored)
        if husks:                                            # what is left of a clip deleted piece by piece: its record goes too (the footage log keeps its lines)
            PG.add([st for k, _c, mins in husks if k == "recent" for st in mins], [(k, c) for k, c, _m in husks if k != "recent"])
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
        if not writable(role):
            return None, "That drive is not one the app handles."
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
        clips = [dict(kind=k, id=c) for k, c, _ in plan]
        read = None
        if action == "move":                      # a move reads what its clips still lack first, so the Archive's clip is never ahead of what is kept from it
            st = self.status(force=True)["clips"]
            lack = [c for c in clips if st[c["kind"]][c["id"]].get("todo")]
            if lack:
                kind_of = {}
                for it in lack:
                    for stamp in self._groups.get((it["kind"], it["id"]), []):
                        kind_of[stamp] = it["kind"]
                paths = self._paths(TLG.Ledger(self.dir), set(kind_of), roots)
                if paths and not self.tc._rv().status().get("running"):
                    read = (kind_of, paths, [(it["kind"], it["id"]) for it in lack if it["kind"] != "recent"])
        self._stop = False
        self._roots = roots
        self._job = dict(running=True, action=action, role=role, files=len(files), done=0, bytes=sum(sz for _, sz, _ in files), bytes_done=0, freed=0, errors=[], current="",
                         clips=len(plan), t0=time.time(), reading=bool(read), step="", step_done=0, step_total=0, steps=[])
        if read:
            self._thread = threading.Thread(target=self._read_then_run, args=(read, action, role, files, roots, clips), daemon=True)
        else:
            self._thread = threading.Thread(target=self._run, args=(action, role, files, roots, clips), daemon=True)
        self._thread.start()
        return self.job()

    # ---- delete by kind: the video, the metadata, the stills, the event pictures, the signs ----------------------------------
    TYPES = ("video", "metadata", "stills", "pictures", "signs")

    def purge(self, items, types, roots, confirmed=False, force=False):
        """Deletes the chosen kinds of what is kept for the clips (needs `confirmed`, as every delete): "video" from every connected drive, "metadata" (speed, position, steering),
        "stills", the event "pictures", and the "signs" read. Video of a clip that is not all kept yet (and has no copy on the Archive) needs `force`, which the page sends only
        after saying what is missing; what is parked at an ignored place (Home) needs neither. Returns the job or dict(error=...)."""
        if confirmed is not True:
            return dict(error="Not confirmed: nothing was removed.")
        types = [t for t in self.TYPES if t in (types or [])]
        if not types:
            return dict(error="Nothing was chosen to delete.")
        if not items:
            return dict(error="Nothing is selected.")
        if self._thread and self._thread.is_alive():
            return dict(error="Something is already being done to the drives.")
        if "signs" in types and self.tc._rv().status().get("running"):
            return dict(error="Signs are being read for a drive: wait for it or stop it.")
        st = self.status(force=True)["clips"]
        plan = []
        for it in items:
            rec = (st.get(it.get("kind")) or {}).get(it.get("id"))
            if rec is None:
                return dict(error="A selected clip is not in the footage log.")
            plan.append((it["kind"], it["id"], rec))
        led = TLG.Ledger(self.dir)
        vfiles, unkept = {}, []
        if "video" in types:
            for kind, cid, rec in plan:
                for role in sorted(roots):
                    if not roots[role] or not writable(role):
                        continue
                    fs = self.files(kind, cid, role, led)
                    if not fs:
                        continue
                    on_archive = role != "A" and all(self._archive_has(led, k, sz) for k, sz, _ in fs)
                    if not (rec["ready"] or rec.get("ignored") or on_archive):
                        unkept.append((kind, cid))
                    vfiles.setdefault(role, []).extend(fs)
        if unkept and not force:
            return dict(error="The video of %d clip(s) is not all kept yet (metadata, stills, signs) and the Archive holds no copy." % len({u for u in unkept}), needs_force=True)
        files = [x for fs in vfiles.values() for x in fs]
        clips = [dict(kind=k, id=c) for k, c, _ in plan]
        self._stop = False
        self._roots = roots
        self._job = dict(running=True, action="purge", types=types, files=len(files), done=0, bytes=sum(sz for _, sz, _ in files), bytes_done=0, freed=0, data_freed=0, errors=[], current="",
                         clips=len(plan), t0=time.time(), step="", step_done=0, step_total=0, steps=[])
        self._thread = threading.Thread(target=self._purge, args=(plan, types, vfiles, roots, clips), daemon=True)
        self._thread.start()
        return self.job()

    def _purge(self, plan, types, vfiles, roots, clips):
        j = self._job
        removed = []
        try:
            stamps = sorted({s for k, c, _r in plan for s in self._groups.get((k, c), [])})
            if "signs" in types:
                j["current"] = "signs"
                self.tc._rv().forget(stamps)
            if "metadata" in types:
                j["current"] = "metadata"
                self.tc._tl("A").forget(stamps)
            if "stills" in types:
                j["current"] = "stills"
                _n, b = self.fg.stills.remove(stamps)
                j["data_freed"] += b
            if "pictures" in types:
                j["current"] = "pictures"
                for k, c, _r in plan:
                    if k != "recent":
                        j["data_freed"] += self.shots.remove(k, c) + self.evst.remove(k, c)
            self._log("purge:" + ",".join(t for t in types if t != "video"), "", clips, removed, dict(j, freed=j["data_freed"]))
            for role in sorted(vfiles):
                if self._stop:
                    break
                self._run("delete", role, vfiles[role], roots, clips)
            if not self._stop and not j["errors"] and (("video" in types and all(rec.get("ignored") for _k, _c, rec in plan)) or all(t in types for t in self.TYPES)):     # everything deleted (or the video of what is parked at Home, nothing else is kept from it): the clip leaves the list
                PG.add([s for k, c, _r in plan if k == "recent" for s in self._groups.get((k, c), [])], [(k, c) for k, c, _r in plan if k != "recent"])
        finally:
            self._changed()
            self._settle()

    def _read_then_run(self, read, action, role, files, roots, clips):
        """Reads what is missing (the same steps as `process`), then moves. Anything that could not be read stops the move: nothing has been copied or removed yet."""
        j = self._job
        self._process(*read)
        j["reading"] = False
        j["step"] = ""
        if self._stop or j["errors"]:
            return
        self._run(action, role, files, roots, clips)

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
                for r in DR.read_roles():
                    x = rec["drives"].get(r)
                    if x is not None and not x.get("gone") and roots.get(r):
                        out.setdefault(m.group(1), {}).setdefault(m.group(2), TF.front_path(roots[r], dict(src=kind, ev=ev, stamp=m.group(1), cam=m.group(2))))
                        break
        return out

    STEPS = ("meta", "stills", "snapshots", "pictures", "signs")

    def start_process(self, items, roots, steps=None):
        """Reads what the clips still lack from their video on the connected drives: the metadata of each minute, then its stills, then the snapshots of the drives it lies in, then its event pictures, then its signs
        (or only the steps in `steps`: "meta", "stills", "snapshots", "pictures", "signs"). A clip whose video is on no connected drive is left as it is."""
        want = set(steps) & set(self.STEPS) if steps else set(self.STEPS)
        if not want:
            return dict(error="Nothing was asked for.")
        if self._thread and self._thread.is_alive():
            return dict(error="Something is already being done to the drives.")
        rv = self.tc._rv()
        if "signs" in want and rv.status().get("running"):
            return dict(error="Signs are being read for a drive: wait for it or stop it.")
        st = self.status(force=True)["clips"]
        kind_of = {}
        for it in items:
            if it.get("id") not in (st.get(it.get("kind")) or {}):
                return dict(error="A selected clip is not in the footage log.")
            if st[it["kind"]][it["id"]].get("ignored"):          # parked at Home: nothing is made from it
                continue
            for stamp in self._groups.get((it["kind"], it["id"]), []):
                kind_of[stamp] = it["kind"]
        items = [it for it in items if not st[it["kind"]][it["id"]].get("ignored")]
        if not items:
            return dict(error="These are parked at Home: nothing is read from them.")
        paths = self._paths(TLG.Ledger(self.dir), set(kind_of), roots)
        evs = [(it["kind"], it["id"]) for it in items if it["kind"] != "recent"]
        if not paths:
            return dict(error="The video of these clips is on no connected drive.")
        self._stop = False
        self._roots = roots
        self._job = dict(running=True, action="process", step="", step_done=0, step_total=0, steps=[], files=0, done=0, bytes=0, bytes_done=0, freed=0, errors=[], current="",
                         clips=len(items), t0=time.time())
        self._thread = threading.Thread(target=self._process, args=(kind_of, paths, evs, want), daemon=True)
        self._thread.start()
        return self.job()

    # ---- the full-size pictures of events, on their own: the Keeper's step after the stills ------------------------
    def pictures_plan(self, roots):
        """[(kind, event, stamp, second, {camera: video path})] of the Saved and Sentry events whose full-size pictures are missing and whose video is on a connected drive."""
        st = self.status()["clips"]
        events = self._events()
        wants = []
        for kind in ("saved", "sentry"):
            for cid, r in st[kind].items():
                if r["shots"][0] >= r["shots"][1] or (kind, cid) not in events:
                    continue
                mins = {s: dict(cams=set(c)) for s, c in self._cams_of(kind, cid).items()}
                w = self._wanted(kind, cid, mins, events[(kind, cid)])
                if w:
                    wants.append((kind, cid, w))
        if not wants:
            return []
        paths = self._paths(TLG.Ledger(self.dir), {w[0] for _k, _c, w in wants}, roots)
        out = []
        for kind, cid, w in wants:
            got = {c: paths[w[0]][c] for c in w[2] if w[0] in paths and c in paths[w[0]]}
            if got:
                out.append((kind, events[(kind, cid)], w[0], w[1], got))
        return out

    def windows_plan(self, roots, only=None):
        """[(kind, event, window, {clip stamp: video path of the camera})] of the Saved and Sentry events whose pictures around the moment are missing and whose video is on a connected
        drive (only those in `only`, [(kind, id)], when given)."""
        st = self.status()["clips"]
        events = self._events()
        wants = []
        for kind in ("saved", "sentry"):
            for cid, r in st[kind].items():
                if r["window"][0] >= r["window"][1] or (kind, cid) not in events or (only is not None and (kind, cid) not in only):
                    continue
                mins = {s: dict(cams=set(c)) for s, c in self._cams_of(kind, cid).items()}
                h = self._hi(kind, cid, mins, events[(kind, cid)])
                if h:
                    wants.append((kind, events[(kind, cid)], h[0], h[1]))
        if not wants:
            return []
        paths = self._paths(TLG.Ledger(self.dir), {w[0] for _k, _e, _c, win in wants for w in win}, roots)
        out = []
        for kind, ev, cam, win in wants:
            got = {w[0]: paths[w[0]][cam] for w in win if w[0] in paths and cam in paths[w[0]]}
            if got:
                out.append((kind, ev, [w for w in win if w[0] in got], got))
        return out

    def pictures_start(self, roots):
        """Saves those pictures in the background. True when a job was started (False: nothing to save, or something else is being done to the drives)."""
        if self._thread and self._thread.is_alive():
            return False
        plan = self.pictures_plan(roots)
        wplan = self.windows_plan(roots)
        if not plan and not wplan:
            return False
        total = sum(len(x[4]) for x in plan) + len(wplan)
        self._stop = False
        self._job = dict(running=True, action="pictures", step="Saving event pictures", step_done=0, step_total=total, steps=[dict(step="Saving event pictures", total=total)], files=0, done=0,
                         bytes=0, bytes_done=0, freed=0, errors=[], current="", clips=len(plan) + len(wplan), t0=time.time())
        self._thread = threading.Thread(target=self._pictures, args=(plan, wplan), daemon=True)
        self._thread.start()
        return True

    def _pictures(self, plan, wplan=()):
        j = self._job
        n = 0
        try:
            for kind, ev, stamp, sec, got in plan:
                if self._stop:
                    break
                j["current"] = ev["id"]
                n += len(self.shots.make(kind, ev, stamp, sec, got, stop=lambda: self._stop))
                j["step_done"] = n
            for kind, ev, win, got in wplan:
                if self._stop:
                    break
                j["current"] = ev["id"]
                self.evst.make(kind, ev, win, got, stop=lambda: self._stop)
                n += 1
                j["step_done"] = n
        except Exception as e:
            j["errors"].append("%s: %s" % (type(e).__name__, e))
        finally:
            self._changed()
            self._settle()

    def _changed(self):
        """A job changed what is on the drives (files moved or deleted) or what is kept: whoever caches a look at the drives is told."""
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass

    def pictures_running(self):
        return bool(self._thread and self._thread.is_alive())

    def _step(self, name, total):
        self._job.update(step=name, step_done=0, step_total=total, current="", step_t0=time.time())
        self._job["steps"].append(dict(step=name, total=total))

    def _process(self, kind_of, paths, evs=(), want=None):
        want = set(want) if want else set(self.STEPS)
        j = self._job
        store = self.tc._tl("A")
        stills = self.fg.stills
        stills.width, stills.quality = app_config.get("stills.width", 400), app_config.get("stills.quality", 6)
        first = lambda s: paths[s].get("front") or next(iter(paths[s].values()))      # telemetry is read from another camera when there is no front clip

        def prog(n, stamp):
            j["step_done"], j["current"] = n, stamp
        try:
            if "meta" in want:
                todo = [dict(stamp=s, path=first(s)) for s in sorted(paths, reverse=True) if kind_of[s] != "sentry" and store.summary(s) is None]
                self._step("Reading metadata", len(todo))
                if todo and not self._stop:
                    TS.read(store, todo, self.dir, stop=lambda: self._stop, progress=prog)
            if "stills" in want:
                bad = getattr(stills, "unreadable", dict)()
                todo = [dict(stamp=s, role="", path=paths[s]["front"], mark_stamp=True) for s in sorted(paths, reverse=True) if "front" in paths[s] and not stills.has(s) and "stamp|" + s not in bad]
                self._step("Making stills", len(todo))
                if todo and not self._stop:
                    ST.make_many(stills, todo, int(app_config.get("stills.workers", 3)), stop=lambda: self._stop, progress=prog, by="clips")
            if "snapshots" in want:
                import drive_snapshots as SN
                import teslacam_sync as SY
                drives = SY.snapshots_for(self.dir, set(paths))
                self._step("Building drive snapshots", len(drives))
                if drives and not self._stop:
                    root = (getattr(self, "_roots", None) or {}).get("A")
                    front = SN.front_clips(self.tc, root) if root else {}
                    SY.run_snapshots(self.dir, drives, front, stop=lambda: self._stop, progress=lambda n, tot, st: prog(n, st), note=lambda t: None, error=j["errors"].append)
            if "pictures" in want:
                events = self._events()
                shots = []
                for kind, cid in evs:
                    mins = {st: dict(cams=set(c)) for st, c in self._cams_of(kind, cid).items()}
                    w = self._wanted(kind, cid, mins, events.get((kind, cid))) if mins else None
                    got = {c: paths[w[0]][c] for c in (w[2] if w else ()) if w[0] in paths and c in paths[w[0]]}
                    if got:
                        shots.append((kind, events[(kind, cid)], w[0], w[1], got))
                wins = [x for x in self.windows_plan(getattr(self, "_roots", None) or {}, only=set(evs))]
                self._step("Saving event pictures", sum(len(x[4]) for x in shots) + len(wins))
                n = 0
                for kind, ev, stamp, sec, got in shots:
                    if self._stop:
                        break
                    j["current"] = ev["id"]
                    n += len(self.shots.make(kind, ev, stamp, sec, got, stop=lambda: self._stop))
                    j["step_done"] = n
                for kind, ev, win, got in wins:
                    if self._stop:
                        break
                    j["current"] = ev["id"]
                    self.evst.make(kind, ev, win, got, stop=lambda: self._stop)
                    n += 1
                    j["step_done"] = n
            if "signs" in want:
                rv = self.tc._rv()
                done = rv.scanned()
                items = []
                for s in sorted(paths):
                    sm = store.summary(s)
                    if "front" in paths[s] and SS.wanted(s, sm) and s not in done:
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
                if not self._stop and hasattr(rv, "refine"):          # the second look at the signs just read (and those read before it existed)
                    again = rv.unrefined()
                    items = [(s, paths[s]["front"]) for s in sorted(paths) if s in again and "front" in paths[s]]
                    self._step("Checking signs", len(items))
                    if items:
                        rv.refine(items)
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
            self._changed()
            self._settle()

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
            self._changed()
            self._settle()

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
