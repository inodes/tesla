"""app_sentry.py - saved Sentry events on the Archive drive, for the Parked pages of tesla_app.py (REQ-200).

TeslaCam/SentryClips/<event folder>/ holds one-minute clips per camera (<stamp>-<camera>.mp4), event.json (when it
triggered, the reason, an approximate place) and thumb.png. This reads those, remembers the answer in
<data>/sentry_index.json (only folders not seen before are read again), and never changes anything on the drive.
"""
import json
import os
import re
import threading

CAMS = ("front", "back", "left_repeater", "right_repeater", "left_pillar", "right_pillar")
_CLIP = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-(%s)\.mp4$" % "|".join(CAMS))
_LOCK = threading.Lock()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class Sentry:
    def __init__(self, data_dir):
        self.f = os.path.join(data_dir, "sentry_index.json")
        self.mem = None

    def _load(self):
        if self.mem is None:
            try:
                with open(self.f, encoding="utf-8") as fh:
                    self.mem = json.load(fh)
            except (OSError, ValueError):
                self.mem = {}
        return self.mem

    def events(self, teslacam):
        """Every saved Sentry event (newest first). teslacam = the Archive's TeslaCam folder, or None when it is not connected."""
        with _LOCK:
            idx = self._load()
            root = os.path.join(teslacam, "SentryClips") if teslacam else None
            if root and os.path.isdir(root):
                try:
                    names = sorted(n for n in os.listdir(root) if re.match(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$", n))
                except OSError:
                    names = []
                new = [n for n in names if n not in idx]
                for n in new:
                    r = self._read(os.path.join(root, n), n)
                    if r:
                        idx[n] = r
                for n in [k for k in idx if k not in set(names)]:
                    del idx[n]                      # the folder was deleted from the Archive
                if new or len(idx) != len(names):
                    try:
                        tmp = self.f + ".tmp"
                        with open(tmp, "w", encoding="utf-8") as fh:
                            json.dump(idx, fh, ensure_ascii=False)
                        os.replace(tmp, self.f)
                    except OSError:
                        pass
            return sorted(idx.values(), key=lambda e: e["ts"], reverse=True), bool(root and os.path.isdir(root))

    def _read(self, path, name):
        try:
            files = os.listdir(path)
        except OSError:
            return None
        clips = {}
        for fn in files:
            m = _CLIP.match(fn)
            if m:
                clips.setdefault(m.group(1), []).append(m.group(2))
        ev = {}
        try:
            with open(os.path.join(path, "event.json"), encoding="utf-8") as fh:
                ev = json.load(fh)
        except (OSError, ValueError):
            pass
        ts = ev.get("timestamp") or name.replace("_", "T", 1)[:11] + name[11:].replace("-", ":")
        return dict(id=name, ts=ts, city=ev.get("city") or "", street=ev.get("street") or "", lat=_num(ev.get("est_lat")), lon=_num(ev.get("est_lon")),
                    reason=ev.get("reason") or "", cam=ev.get("camera") or "", clips={k: sorted(v, key=CAMS.index) for k, v in sorted(clips.items())},
                    thumb="thumb.png" in files, whole="event.mp4" in files)
