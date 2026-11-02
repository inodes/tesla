"""stills_thin.py - make the one-second stills of ordinary driving smaller, and keep the good pictures for what matters.

The stills of a minute (teslacam_stills.py: <data>/teslacam_stills/<day>/<clip>.zip) are 400 px wide. That is plenty to browse a drive and far more than most of
driving deserves, so a minute that nothing was taken from is re-encoded smaller (app.stills.thin_width / thin_quality). A minute keeps its stills as they are when

    it belongs to a Saved or Sentry event          (and the event has its full-size pictures: event_shots.py)
    a sign was found in it                         (and the sign has its own crop: app_review.py)
    it has not been looked at yet                  (no telemetry read, or driven fast enough for signs but not read for them yet): it may still turn out to matter

so the pictures worth reading text from are the full-size ones kept for events and signs, not these.

    StillsThin(data_dir, stills, tc).status()   -> dict(minutes, bytes, running, done, total, saved, errors, last)   what could be thinned, and the job
    StillsThin.start() / .stop()

The pictures kept around a Saved or Sentry event (event_stills.py) start at the highest level; shrinking steps them down to the size of the other stills ("normal") and,
when asked for, to the size ordinary driving is shrunk to ("shrunk"), where the event's own minute stills are shrunk as well.

Which minutes were thinned is remembered in <data>/teslacam_stills/thinned.json {clip: [width, quality]}, so none is done twice.
"""
import json
import os
import tempfile
import threading
import time
import zipfile

import app_config
import teslacam_ledger as TLG
import teslacam_stills as ST
import sign_speed as SS

MARK = "thinned.json"
TTL = 60.0


class StillsThin:
    def __init__(self, data_dir, stills, tc):
        self.dir, self.stills, self.tc = data_dir, stills, tc
        self._lock = threading.Lock()
        self._cache = None
        self._thread = None
        self._stop = False
        self._job = dict(done=0, total=0, saved=0, errors=[], current="", last=None)

    # ---- which minutes -------------------------------------------------------------------------------------------
    def _marks(self):
        try:
            with open(os.path.join(self.stills.root, MARK), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save_marks(self, marks):
        os.makedirs(self.stills.root, exist_ok=True)
        p = os.path.join(self.stills.root, MARK)
        with open(p + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(marks, fh, separators=(",", ":"))
        os.replace(p + ".tmp", p)

    def _event_stamps(self):
        out = set()
        led = TLG.Ledger(self.dir)
        for day in led.days():
            for key in led.day(day):
                kind, _ev, rest = key.split("|", 2)
                if kind in ("saved", "sentry"):
                    out.add(rest[:19])
        return out

    def candidates(self):
        """The minutes whose stills can be made smaller, newest first."""
        store = self.tc._tl("A")
        scanned = self.tc._rv().scanned()
        keep, marks = self._event_stamps(), self._marks()
        out = []
        for st in sorted(self.stills.stamps(), reverse=True):
            if st in keep or st in marks:
                continue
            sm = store.summary(st)
            if not isinstance(sm, dict):
                continue
            if SS.wanted(st, sm) and (st not in scanned or scanned[st] > 0):
                continue                                                  # driven fast enough for signs: not read yet, or a sign was found
            out.append(st)
        return out

    def event_levels(self):
        """{tier: number of events} and the bytes the pictures around events take, for Saved and Sentry events with pictures kept."""
        import event_stills as EV
        n, size = {t: 0 for t in EV.TIERS}, 0
        for kind, eid, info in EV.EventStills(self.dir).events():
            n[info.get("tier") if info.get("tier") in n else "high"] += 1
            try:
                size += os.path.getsize(os.path.join(EV.EventStills(self.dir).folder(kind, eid), info.get("cam", "") + ".zip"))
            except OSError:
                pass
        return dict(n, bytes=size)

    def _event_minutes(self):
        """{(kind, event folder): {clip stamps}} from the footage log."""
        out = {}
        led = TLG.Ledger(self.dir)
        for day in led.days():
            for key in led.day(day):
                kind, ev, rest = key.split("|", 2)
                if kind in ("saved", "sentry"):
                    out.setdefault((kind, ev), set()).add(rest[:19])
        return out

    def scope_status(self, clips, stamps):
        """What shrinking would do for these clips only ([(kind, id)]; `stamps` = their minutes): the stills of ordinary driving among them, and the pictures kept around their events,
        each with how many pictures, what they take now and about what they would take after (worked out by shrinking a few into a scratch copy)."""
        import event_stills as EV
        es = EV.EventStills(self.dir)
        cand = [st for st in self.candidates() if st in stamps]
        pics = sum(self.stills.count(st) for st in cand)
        size = sum(self._size(self.stills.path(st)) for st in cand)
        after = self._sample([self.stills.path(st) for st in cand], size, int(app_config.get("stills.thin_width", 240)), int(app_config.get("stills.thin_quality", 10)))
        marks = self._marks()
        mins = self._event_minutes()
        ev = {t: 0 for t in EV.TIERS}
        epics = ebytes = 0
        paths = []
        for kind, eid in clips:
            i = es.info(kind, eid)
            if not i.get("n"):
                continue
            ev[i.get("tier") if i.get("tier") in ev else "high"] += 1
            epics += i["n"]
            z = os.path.join(es.folder(kind, eid), i["cam"] + ".zip")
            ebytes += self._size(z)
            paths.append((z, i.get("tier") or "high"))
        wd = EV._widths(), EV._quality()
        out = dict(scoped=True, clips=len(clips), driving=dict(minutes=len(cand), pictures=pics, bytes=size, after=after),
                   events=dict(ev, pictures=epics, bytes=ebytes))
        for lvl in ("normal", "shrunk"):
            todo = [z for z, t in paths if EV.TIERS.index(t) < EV.TIERS.index(lvl)]
            tot = sum(self._size(z) for z in todo)
            out["events"][lvl + "_after"] = self._sample(todo, tot, wd[0][lvl], wd[1][lvl]) if todo else 0
            out["events"][lvl + "_n"] = len(todo)
        mstamps = {st for k in clips for st in mins.get(tuple(k), ())}
        mc = [st for st in mstamps if st not in marks and self.stills.has(st)]
        out["events"]["minute_stills"] = sum(self.stills.count(st) for st in mc)
        out["events"]["minute_bytes"] = sum(self._size(self.stills.path(st)) for st in mc)
        return dict(out, running=self.running(), **self._job)

    @staticmethod
    def _size(p):
        try:
            return os.path.getsize(p)
        except OSError:
            return 0

    def _sample(self, paths, total, width, quality, n=3):
        """About what `total` bytes of those zips take once re-encoded: the ratio found on a few of them, applied to the whole."""
        if not paths or not total:
            return 0
        have = [p for p in paths if self._size(p)][:n]
        old = new = 0
        for p in have:
            try:
                o, nw = thin_zip(p, width, quality, dry=True)
            except (OSError, zipfile.BadZipFile, ST.DriveStalled):
                continue
            old, new = old + o, new + nw
        return int(total * (new / old)) if old else total

    def status(self, force=False):
        with self._lock:
            if not force and self._cache and time.time() - self._cache[0] < TTL and not self.running():
                base = self._cache[1]
            else:
                base = None
        if base is None:
            c = self.candidates()
            size = 0
            for st in c:
                try:
                    size += os.path.getsize(self.stills.path(st))
                except OSError:
                    pass
            base = dict(minutes=len(c), bytes=size, events=self.event_levels())
            with self._lock:
                self._cache = (time.time(), base)
        return dict(base, running=self.running(), **self._job)

    def running(self):
        return bool(self._thread and self._thread.is_alive())

    # ---- the job ---------------------------------------------------------------------------------------------------
    def start(self, confirmed=False, events=None, clips=None, stamps=None):
        """Re-encoding replaces the stills and cannot be undone: it needs `confirmed`. With events="normal" or "shrunk" it is the pictures around Saved and Sentry events
        that are shrunk to that level instead of the stills of ordinary driving."""
        if confirmed is not True:
            return dict(self.status(), error="Not confirmed: nothing was changed.")
        if self.running():
            return self.status()
        if events not in (None, "normal", "shrunk"):
            return dict(self.status(), error="Unknown level.")
        self._stop = False
        if events:
            import event_stills as EV
            tiers = EV.TIERS
            todo = [(k, e) for k, e, i in EV.EventStills(self.dir).events() if tiers.index(i.get("tier") or "high") < tiers.index(events) and (clips is None or (k, e) in clips)]
            self._job = dict(done=0, total=len(todo), saved=0, errors=[], current="", last=None, what="events")
            self._thread = threading.Thread(target=self._run_events, args=(todo, events), daemon=True)
        else:
            todo = [st for st in self.candidates() if stamps is None or st in stamps]
            self._job = dict(done=0, total=len(todo), saved=0, errors=[], current="", last=None, what="driving")
            self._thread = threading.Thread(target=self._run, args=(todo,), daemon=True)
        self._thread.start()
        return self.status()

    def stop(self):
        self._stop = True
        return self.status()

    def _run(self, todo):
        w = int(app_config.get("stills.thin_width", 240))
        q = int(app_config.get("stills.thin_quality", 10))
        marks, j = self._marks(), self._job
        try:
            for st in todo:
                if self._stop:
                    break
                j["current"] = st
                try:
                    old, new = thin_zip(self.stills.path(st), w, q)
                    j["saved"] += max(0, old - new)
                    marks[st] = [w, q]
                except FileNotFoundError:
                    pass
                except (OSError, zipfile.BadZipFile, ST.DriveStalled) as e:
                    j["errors"].append("%s: %s" % (st, e))
                    if len(j["errors"]) >= 20:
                        break
                j["done"] += 1
                if j["done"] % 50 == 0:
                    self._save_marks(marks)
        finally:
            self._save_marks(marks)
            j["last"] = time.time()
            with self._lock:
                self._cache = None


    def _run_events(self, todo, level):
        import event_stills as EV
        es, j = EV.EventStills(self.dir), self._job
        w = int(app_config.get("stills.thin_width", 240))
        q = int(app_config.get("stills.thin_quality", 10))
        mins = self._event_minutes() if level == "shrunk" else {}
        marks = self._marks() if level == "shrunk" else {}
        try:
            for kind, eid in todo:
                if self._stop:
                    break
                j["current"] = eid
                try:
                    r = es.shrink(kind, eid, level)
                    if r:
                        j["saved"] += max(0, r[0] - r[1])
                    if level == "shrunk":                                      # the event's own minutes are shrunk with it
                        for st in sorted(mins.get((kind, eid), ())):
                            if st in marks or not self.stills.has(st):
                                continue
                            old, new = thin_zip(self.stills.path(st), w, q)
                            j["saved"] += max(0, old - new)
                            marks[st] = [w, q]
                except FileNotFoundError:
                    pass
                except (OSError, zipfile.BadZipFile, ST.DriveStalled) as e:
                    j["errors"].append("%s: %s" % (eid, e))
                    if len(j["errors"]) >= 20:
                        break
                j["done"] += 1
        finally:
            if level == "shrunk":
                self._save_marks(marks)
            j["last"] = time.time()
            with self._lock:
                self._cache = None


def thin_zip(path, width, quality, dry=False):
    """Re-encodes every picture of one clip's zip at most `width` px wide (never larger than it is); returns (bytes before, bytes after).
    The zip is left alone when the result would not be clearly smaller. With dry=True nothing is replaced: only what the result would take is returned."""
    old = os.path.getsize(path)
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(path) as z:
            names = sorted(z.namelist())
            for i, n in enumerate(names):
                with open(os.path.join(tmp, "i%03d.jpg" % i), "wb") as f:
                    f.write(z.read(n))
        if not names:
            return old, old
        ST.run_ffmpeg(["ffmpeg", "-v", "error", "-start_number", "0", "-i", os.path.join(tmp, "i%03d.jpg"), "-vf", "scale='min(%d,iw)':-2" % width,
                       "-q:v", str(quality), "-start_number", "0", os.path.join(tmp, "o%03d.jpg")])
        outs = ["o%03d.jpg" % i for i in range(len(names))]
        if not all(os.path.isfile(os.path.join(tmp, o)) for o in outs):
            raise OSError("ffmpeg did not re-encode every picture")
        part = path + ".thin"
        try:
            with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED) as z:
                for n, o in zip(names, outs):
                    z.write(os.path.join(tmp, o), n)
            new = os.path.getsize(part)
            if dry:
                pass
            elif new >= old * 0.9:
                new = old
            else:
                os.replace(part, path)
        finally:
            if os.path.exists(part):
                os.remove(part)
    return old, new
