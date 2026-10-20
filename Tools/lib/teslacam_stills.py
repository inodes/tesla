"""teslacam_stills.py - one still for every second of every front-camera clip, kept after the video is gone.

    <data>/teslacam_stills/<day>/<clip stamp>.zip       00.jpg ... 59.jpg  (second N of the clip), 400 px wide

One file per minute-clip rather than 60 loose files: the same images, individually readable (the app shows each one on its own), but
~10 thousand files for all the footage instead of ~650 thousand that iCloud would have to sync one by one. The zip is not compressed
(a JPEG does not shrink) so reading one image is a seek, not an unpack.

make(): decodes a clip once with ffmpeg (one frame a second, scaled) and writes the zip, nothing else. The same clip stamp on two drives
(a copy) is made once, from the first drive in ORDER (Archive, JOWUA, Tesla USB). A clip that already has its zip is skipped, so the
stills are never taken twice and stay when the video is deleted.
"""
import os
import shutil
import subprocess
import tempfile
import zipfile

import teslacam_files as TF

DIRNAME = "teslacam_stills"
WIDTH = 400
QUALITY = 6                 # ffmpeg -q:v (2 best ... 31 worst)
ORDER = "AJU?"


class Stills:
    def __init__(self, data_dir, width=WIDTH, quality=QUALITY):
        self.root = os.path.join(data_dir, DIRNAME)
        self.width = width
        self.quality = quality

    def path(self, stamp):
        return os.path.join(self.root, stamp[:10], stamp + ".zip")

    def has(self, stamp):
        return os.path.isfile(self.path(stamp))

    def count(self, stamp):
        try:
            with zipfile.ZipFile(self.path(stamp)) as z:
                return len(z.namelist())
        except (OSError, zipfile.BadZipFile):
            return 0

    def get(self, stamp, n):
        """JPEG bytes of second n of the clip, or None."""
        try:
            with zipfile.ZipFile(self.path(stamp)) as z:
                return z.read("%02d.jpg" % n)
        except (OSError, KeyError, zipfile.BadZipFile):
            return None

    def stamps(self):
        out = set()
        try:
            for d in os.listdir(self.root):
                p = os.path.join(self.root, d)
                if os.path.isdir(p):
                    out.update(n[:-4] for n in os.listdir(p) if n.endswith(".zip"))
        except OSError:
            pass
        return out

    def make(self, stamp, video):
        """Take the stills of one clip; returns how many (0 = nothing written)."""
        if self.has(stamp):
            return self.count(stamp)
        with tempfile.TemporaryDirectory() as tmp:
            frames = []
            for keyframes in (True, False):                 # the car writes a key frame every ~0.25 s: decoding only those is 3x faster; a clip with fewer is decoded whole
                for f in os.listdir(tmp):
                    os.remove(os.path.join(tmp, f))
                subprocess.run(["ffmpeg", "-v", "error"] + (["-skip_frame", "nokey"] if keyframes else []) + ["-i", video, "-vf", "fps=1,scale=%d:-2" % self.width,
                                "-q:v", str(self.quality), os.path.join(tmp, "f%03d.jpg")], stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
                frames = sorted(f for f in os.listdir(tmp) if f.startswith("f") and f.endswith(".jpg"))
                if len(frames) >= 55:
                    break
            if not frames:
                return 0
            dest = self.path(stamp)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            part = dest + ".part"
            with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED) as z:
                for i, f in enumerate(frames):
                    z.write(os.path.join(tmp, f), "%02d.jpg" % i)
            os.replace(part, dest)
            return len(frames)


def plan(stills, roots):
    """roots {role: TeslaCam folder}. Returns dict(drives=[per drive counts], todo=[dict(stamp, role, path)] newest first).
    Per drive: clips (front, distinct stamps), done (stills kept), to_make, elsewhere (an earlier drive makes it), no_front is not needed:
    only front clips are listed."""
    drives, todo, taken = [], [], set()
    have = stills.stamps()
    for role in sorted(roots, key=lambda r: ORDER.index(r) if r in ORDER else 9):
        files, ok = TF.walk(roots[role])
        best = {}
        for f in files:
            if f["cam"] != "front":
                continue
            cur = best.get(f["stamp"])
            if cur is None or TF.ORDER[f["src"]] < TF.ORDER[cur["src"]]:
                best[f["stamp"]] = f
        d = dict(role=role, clips=len(best), done=0, to_make=0, elsewhere=0, ok=ok,
                 first=min(best)[:10] if best else None, last=max(best)[:10] if best else None)
        for st, f in best.items():
            if st in have:
                d["done"] += 1
            elif st in taken:
                d["elsewhere"] += 1
            else:
                taken.add(st)
                d["to_make"] += 1
                todo.append(dict(stamp=st, role=role, path=TF.front_path(roots[role], f)))
        drives.append(d)
    todo.sort(key=lambda x: x["stamp"], reverse=True)
    return dict(drives=drives, todo=todo, kept=len(have))


def make_many(stills, todo, workers=3, stop=None, progress=None, limit=None):
    """Takes the stills of the planned clips (newest first), `workers` ffmpegs at a time; returns how many clips were made."""
    from concurrent.futures import ThreadPoolExecutor
    jobs = todo[:limit] if limit else todo
    done = [0]

    def one(x):
        if stop and stop():
            return 0
        try:
            n = stills.make(x["stamp"], x["path"])
        except (subprocess.SubprocessError, OSError):
            n = 0
        done[0] += 1
        if progress:
            progress(done[0], x["stamp"])
        return 1 if n else 0
    with ThreadPoolExecutor(max(1, workers)) as ex:
        return sum(ex.map(one, jobs))


def have_ffmpeg():
    return shutil.which("ffmpeg") is not None
