"""stills_thin.py - make the one-second stills of ordinary driving smaller, and keep the good pictures for what matters.

The stills of a minute (teslacam_stills.py: <data>/teslacam_stills/<day>/<clip>.zip) are 400 px wide. That is plenty to browse a drive and far more than most of
driving deserves, so a minute that nothing was taken from is re-encoded smaller (app.stills.thin_width / thin_quality). A minute keeps its stills as they are when

    it belongs to a Saved or Sentry event          (and the event has its full-size pictures: event_shots.py)
    a sign was found in it                         (and the sign has its own crop: app_review.py)
    it has not been looked at yet                  (no telemetry read, or driven fast enough for signs but not read for them yet): it may still turn out to matter

so the pictures worth reading text from are the full-size ones kept for events and signs, not these.

    StillsThin(data_dir, stills, tc).status()   -> dict(minutes, bytes, running, done, total, saved, errors, last)   what could be thinned, and the job
    StillsThin.start() / .stop()

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
        min_kmh = float((app_config.get("review") or {}).get("min_kmh", 30.0))
        keep, marks = self._event_stamps(), self._marks()
        out = []
        for st in sorted(self.stills.stamps(), reverse=True):
            if st in keep or st in marks:
                continue
            sm = store.summary(st)
            if not isinstance(sm, dict):
                continue
            if sm.get("vmax", 0) >= min_kmh and (st not in scanned or scanned[st] > 0):
                continue                                                  # driven fast enough for signs: not read yet, or a sign was found
            out.append(st)
        return out

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
            base = dict(minutes=len(c), bytes=size)
            with self._lock:
                self._cache = (time.time(), base)
        return dict(base, running=self.running(), **self._job)

    def running(self):
        return bool(self._thread and self._thread.is_alive())

    # ---- the job ---------------------------------------------------------------------------------------------------
    def start(self):
        if self.running():
            return self.status()
        todo = self.candidates()
        self._stop = False
        self._job = dict(done=0, total=len(todo), saved=0, errors=[], current="", last=None)
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


def thin_zip(path, width, quality):
    """Re-encodes every picture of one clip's zip at most `width` px wide (never larger than it is); returns (bytes before, bytes after).
    The zip is left alone when the result would not be clearly smaller."""
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
            if new >= old * 0.9:
                new = old
            else:
                os.replace(part, path)
        finally:
            if os.path.exists(part):
                os.remove(part)
    return old, new
