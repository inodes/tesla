"""app_review.py - read the round speed-limit signs along a drive from its front-camera video, for the Review tab of tesla_app.py.

One front clip is one minute. For each minute the car was moving in, a still is taken every `step_s` seconds, round red-ringed
signs are looked for (sign_finder.py: left of the road, plus a second look on the right for ramps) and the number is read with
tesseract; a reading that is not a plausible limit (40..130 in tens) is dropped. What was found is kept per minute under
<data>/drive_review/ (<day>.json, plus a small crop of every sign in crops/), keyed by the minute's name like the telemetry cache,
so the same clip copied to another disk is never read twice. Nothing on a drive is changed.

Readings from motion-blurred or backlit signs are sometimes wrong: every sign is kept with its crop, how many frames saw it and
how sure the reading was, so it can be judged by eye in the page.
"""
import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

for _d in ("/opt/homebrew/bin", "/usr/local/bin"):      # an app started from the Dock does not see Homebrew's folder
    if os.path.isdir(_d) and _d not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + _d

OPTS = dict(step_s=1.0,        # a still every this many seconds
            min_kmh=30.0,      # only look while the car goes at least this fast (signs matter on the road, not in a car park)
            min_conf=50.0,     # tesseract confidence (0-100) below which a reading is dropped
            workers=3,         # stills looked at in parallel
            merge_s=12.0)      # the same number seen again within this many seconds is the same sign
_LOCK = threading.RLock()


def requirements():
    """What reading signs needs and this machine lacks: Python modules, commands, and the lines that install them."""
    py = [m for m in ("cv2", "numpy") if importlib.util.find_spec(m) is None]
    cmd = [c for c in ("ffmpeg", "tesseract") if shutil.which(c) is None]
    hint = []
    if py:
        hint.append("%s -m pip install opencv-python-headless numpy" % sys.executable)
    if cmd:
        hint.append("brew install " + " ".join(cmd))
    return dict(py=py, cmd=cmd, hint=hint)


def secs(stamp):
    return datetime.datetime.strptime(stamp, "%Y-%m-%d_%H-%M-%S").timestamp()


class Review:
    def __init__(self, data_dir):
        self.base = os.path.join(data_dir, "drive_review")
        self.days = {}
        self.t = None
        self.stop_flag = False
        self.st = dict(running=False, done=0, total=0, current="", found=0, error="", t0=0)

    # ------------------------------------------------------------------------------------------------------ store
    def _day(self, day):
        if day not in self.days:
            try:
                with open(os.path.join(self.base, day + ".json"), encoding="utf-8") as fh:
                    self.days[day] = json.load(fh)
            except (OSError, ValueError):
                self.days[day] = {}
        return self.days[day]

    def _save(self, day):
        os.makedirs(self.base, exist_ok=True)
        p = os.path.join(self.base, day + ".json")
        with open(p + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(self.days[day], fh, separators=(",", ":"))
        os.replace(p + ".tmp", p)

    def scanned(self):
        """Every minute that has been looked at, with how many signs it held: {stamp: n} (for the Drives list)."""
        out = {}
        try:
            names = os.listdir(self.base)
        except OSError:
            return out
        with _LOCK:
            for fn in names:
                if fn.endswith(".json"):
                    for st, r in self._day(fn[:-5]).items():
                        out[st] = len(r.get("d", []))
        return out

    def forget(self, stamps):
        with _LOCK:
            days = set()
            for st in stamps:
                if self._day(st[:10]).pop(st, None) is not None:
                    days.add(st[:10])
                    try:                                        # and the pictures of its signs
                        for fn in os.listdir(os.path.join(self.base, "crops")):
                            if fn.startswith(st + "_"):
                                os.remove(os.path.join(self.base, "crops", fn))
                    except OSError:
                        pass
            for d in days:
                self._save(d)

    def signs(self, stamps):
        """The signs seen in these minutes, one entry per sign (the same number seen in several stills within merge_s is one sign)."""
        raw = []
        with _LOCK:
            for st in stamps:
                r = self._day(st[:10]).get(st)
                for d in (r or {}).get("d", []):
                    raw.append(dict(d, t=secs(st) + d["s"], stamp=st))
        raw.sort(key=lambda d: d["t"])
        out = []
        for d in raw:
            g = next((o for o in reversed(out) if o["text"] == d["text"] and o["side"] == d["side"] and d["t"] - o["t1"] <= OPTS["merge_s"]), None)
            if g is None:
                out.append(dict(text=d["text"], side=d["side"], t0=d["t"], t1=d["t"], n=0, conf=-1, crop="", t=d["t"]))
                g = out[-1]
            g["n"] += 1
            g["t1"] = d["t"]
            if d["c"] > g["conf"]:
                g.update(conf=d["c"], crop=d.get("f", ""), t=d["t"])
        for g in out:
            for k in ("t", "t0", "t1"):
                g[k] = datetime.datetime.fromtimestamp(g[k]).strftime("%Y-%m-%dT%H:%M:%S")
            g["conf"] = round(g["conf"])
        return out

    # -------------------------------------------------------------------------------------------------------- job
    def status(self):
        s = dict(self.st)
        s["running"] = bool(self.t and self.t.is_alive())
        return s

    def start(self, items, force=False):
        """items = [(stamp, front clip path, speed per second of that minute or None)], oldest first."""
        if self.t and self.t.is_alive():
            return self.status()
        need = requirements()
        if need["py"] or need["cmd"]:
            self.st = dict(running=False, done=0, total=0, current="", found=0, error="Missing: " + ", ".join(need["py"] + need["cmd"]), t0=0)
            return self.status()
        if force:
            self.forget([i[0] for i in items])
        with _LOCK:
            todo = [i for i in items if self._day(i[0][:10]).get(i[0]) is None]
        self.stop_flag = False
        self.st = dict(running=True, done=0, total=len(todo), current="", found=0, error="", t0=time.time())
        self.t = threading.Thread(target=self._run, args=(todo,), daemon=True)
        self.t.start()
        return self.status()

    def stop(self):
        self.stop_flag = True
        return self.status()

    def _run(self, todo):
        try:
            for n, (stamp, path, v) in enumerate(todo):
                if self.stop_flag:
                    break
                self.st["current"] = stamp
                rec = scan_clip(path, stamp, v, self.base)
                with _LOCK:
                    self._day(stamp[:10])[stamp] = rec
                    self._save(stamp[:10])
                self.st["done"] = n + 1
                self.st["found"] += len(rec["d"])
        except Exception as e:                           # a drive pulled out, a bad file, OpenCV or tesseract missing: say so
            self.st["error"] = "%s: %s" % (type(e).__name__, e)


def _frames(path, step, tmp):
    """Stills of a clip every `step` seconds into tmp, full size; returns [(second, file)]. Needs ffmpeg."""
    subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", "fps=%g" % (1.0 / step), "-q:v", "2", os.path.join(tmp, "f%04d.jpg")],
                   stdin=subprocess.DEVNULL, capture_output=True, timeout=180)
    fs = sorted(f for f in os.listdir(tmp) if f.startswith("f") and f.endswith(".jpg"))
    return [(k * step, os.path.join(tmp, f)) for k, f in enumerate(fs)]


def scan_clip(path, stamp, v, base):
    """Look through one minute. Returns {n: stills looked at, d: [{s, text, c, x, y, r, side, f}]}."""
    import cv2
    import sign_finder as SF
    from concurrent.futures import ThreadPoolExecutor
    step = max(0.25, float(OPTS["step_s"]))
    found = []
    with tempfile.TemporaryDirectory() as tmp:
        fr = _frames(path, step, tmp)
        if v:   # only while moving fast enough
            fr = [(s, f) for s, f in fr if int(s) < len(v) and v[int(s)] is not None and v[int(s)] >= OPTS["min_kmh"]]

        def one(a):
            s, f = a
            im = cv2.imread(f)
            if im is None:
                return []
            out = []
            for side, roi in (("L", SF.ROI_LEFT), ("R", SF.ROI_RIGHT)):
                for c in SF.find_signs(im, roi=roi):
                    if c.get("ok") and c["conf"] >= OPTS["min_conf"] and c["text"]:
                        out.append(dict(s=s, text=c["text"], c=round(c["conf"]), x=c["x"], y=c["y"], r=c["r"], side=side, crop=c["crop"]))
                        break               # the best reading per side and still
            return out
        with ThreadPoolExecutor(max(1, int(OPTS["workers"]))) as ex:
            for lst in ex.map(one, fr):
                found += lst
        os.makedirs(os.path.join(base, "crops"), exist_ok=True)
        keep = []
        for k, d in enumerate(found):
            crop = d.pop("crop")
            name = "%s_%02d.jpg" % (stamp, k)
            h, w = crop.shape[:2]
            if h > 0 and w > 0:
                if max(h, w) > 160:
                    z = 160.0 / max(h, w)
                    crop = cv2.resize(crop, (max(1, int(w * z)), max(1, int(h * z))))
                cv2.imwrite(os.path.join(base, "crops", name), crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                d["f"] = name
            keep.append(d)
    return dict(n=len(fr), d=keep)
