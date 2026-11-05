"""park_scene.py - did the car stay where it was between two stops? The dashcam answers it from what it saw.

A park the dashcam recorded in pieces (the car woke for a few minutes, slept, woke again) is one park when the car did not move between the pieces. The first piece after a drive
is where the drive ended and the last piece before one is where it began; the pieces between have no GPS. Their pictures tell: a small grey copy of each camera's view (32 x 18) of
one minute is kept, and two minutes are the same scene when most of the cameras they have in common look alike (correlation of the two copies).

    Scenes(path)                           the copies kept so far, {stamp: {camera: hex}} (a JSON file on this computer)
    Scenes.get(stamp)                      -> {camera: bytes} or None (not looked at yet)
    same(sa, sb)                           -> True (the same place), False (it moved), None (nothing to compare)
    probe_bg(scenes, stamps, path_of)      looks at the minutes in the background (ffmpeg, one frame per camera), keeping the answers as it goes
"""
import json
import os
import shutil
import subprocess
import threading

W, H = 32, 18
SEEK_S = 20                 # the second of the minute that is looked at
LIKE = 0.6                  # two views are alike at this correlation or more (light changes, a door open or shut: the rest of the picture still fits)
NEED = 2                    # ... and the scene is the same when this many of the cameras they have in common are alike (or all of them when only one is common)
_BG = {"on": False}


class Scenes:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.d = {}
        try:
            with open(path, encoding="utf-8") as f:
                self.d = {k: v for k, v in json.load(f).items() if isinstance(v, dict)}
        except (OSError, ValueError):
            pass

    def get(self, stamp):
        with self.lock:
            v = self.d.get(stamp)
        if v is None:
            return None
        return {c: bytes.fromhex(h) for c, h in v.items()}

    def put(self, stamp, cams):
        with self.lock:
            self.d[stamp] = {c: b.hex() for c, b in cams.items()}

    def save(self):
        with self.lock:
            snap = dict(self.d)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(snap, f)
            os.replace(self.path + ".tmp", self.path)
        except OSError:
            pass


def have_ffmpeg():
    return shutil.which("ffmpeg") is not None


def frame(path, seek=SEEK_S):
    """The grey 32 x 18 copy (576 bytes) of one picture of a clip, or None."""
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%g" % seek, "-i", path, "-frames:v", "1", "-vf", "scale=%d:%d:flags=area,format=gray" % (W, H), "-f", "rawvideo", "-"],
                           capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 and len(r.stdout) == W * H else None


def corr(a, b):
    """Correlation of two equally long byte strings (-1 .. 1); 0 when one of them is flat."""
    n = len(a)
    if n == 0 or n != len(b):
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb) ** 0.5


def same(sa, sb):
    """sa, sb = {camera: bytes} of two minutes. True when most of the cameras they have in common look alike, False when they do not, None when there is nothing to compare."""
    if not sa or not sb:
        return None
    cams = [c for c in sa if c in sb]
    if not cams:
        return None
    alike = sum(1 for c in cams if corr(sa[c], sb[c]) >= LIKE)
    return alike >= min(NEED, len(cams))


def probe_bg(scenes, stamps, cams_of, path_of, workers=3, save_every=12):
    """Looks, in the background, at every minute of `stamps` that has no copy yet. cams_of(stamp) -> its cameras, path_of(stamp, camera) -> a file or None. One job at a time. True when started."""
    todo = [s for s in sorted(set(stamps), reverse=True) if scenes.get(s) is None and any(path_of(s, c) for c in cams_of(s))]
    if not todo or _BG["on"] or not have_ffmpeg():
        return False
    _BG["on"] = True

    def one(st):
        out = {}
        for c in cams_of(st):
            p = path_of(st, c)
            f = frame(p) if p else None
            if f:
                out[c] = f
        return st, out

    def job():
        from concurrent.futures import ThreadPoolExecutor
        try:
            n = 0
            with ThreadPoolExecutor(workers) as ex:
                for st, got in ex.map(one, todo):
                    if got:
                        scenes.put(st, got)
                        n += 1
                        if n % save_every == 0:
                            scenes.save()
            if n % save_every:
                scenes.save()
        finally:
            _BG["on"] = False
    threading.Thread(target=job, daemon=True).start()
    return True
