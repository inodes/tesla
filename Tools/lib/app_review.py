"""app_review.py - read the round speed-limit signs along a drive from its front-camera video, for the Review tab of tesla_app.py.

One front clip is one minute. For each minute the car was moving in, a still is taken every `step_s` seconds, round red-ringed
signs are looked for (sign_finder.py: left of the road, plus a second look on the right for ramps) and the number is read with
tesseract; a reading that is not a plausible limit (40..130 in tens) is dropped. What was found is kept per minute under
<data>/drive_review/ (<day>.json, plus a small crop of every sign in crops/), keyed by the minute's name like the telemetry cache,
so the same clip copied to another disk is never read twice. Nothing on a drive is changed.

Readings from motion-blurred or backlit signs are sometimes wrong: every sign is kept with its crop, how many frames saw it and
how sure the reading was, so it can be judged by eye in the page.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import clip_clock as CC

for _d in ("/opt/homebrew/bin", "/usr/local/bin"):      # an app started from the Dock does not see Homebrew's folder
    if os.path.isdir(_d) and _d not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + _d

OPTS = dict(step_s=1.0,        # a still every this many seconds
            min_kmh=30.0,      # only look while the car goes at least this fast (signs matter on the road, not in a car park)
            min_conf=50.0,     # tesseract confidence (0-100) below which a reading is dropped
            workers=3,         # stills looked at in parallel
            merge_s=12.0,      # the same number seen again within this many seconds is the same sign
            refine_step_s=0.2, # the second look at a sign: a still this often ...
            refine_pad_s=2.0,  # ... from this long before the first still it was seen in until this long after the last
            refine_conf=85.0,  # a sign read this surely, in more than one still, with one number, is left as it is
            crop_px=320)       # the longest side of the picture kept of each sign: big enough to read the number from again once the video is gone
_LOCK = threading.RLock()
_DAYFILE = re.compile(r"^\d{4}-\d\d-\d\d\.json$")      # a day's file: not the copy iCloud makes when two Macs save at once ("2026-09-16 2.json"), whose old minutes would count as never read


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
    """UTC epoch of a clip's name (the car's clock turned into UTC, see clip_clock.py)."""
    return CC.utc(stamp)


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
                if _DAYFILE.match(fn):
                    for st, r in self._day(fn[:-5]).items():
                        out[st] = len(r.get("d", []))
        return out

    def unrefined(self):
        """The minutes with signs that have not had the second look yet (refine)."""
        out = set()
        with _LOCK:
            try:
                names = os.listdir(self.base)
            except OSError:
                return out
            for fn in names:
                if _DAYFILE.match(fn):
                    for st, r in self._day(fn[:-5]).items():
                        if not r.get("rf") and any(x.get("kind") != "school" or not x.get("tm") for x in r.get("d", [])):
                            out.add(st)
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

    def signs(self, stamps, lo=None):
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
                out.append(dict(text=d["text"], side=d["side"], t0=d["t"], t1=d["t"], n=0, conf=-1, crop="", t=d["t"], kind=d.get("kind", ""), _sc=-1, _tm={}))
                g = out[-1]
            g["n"] += 1
            g["t1"] = d["t"]
            sc = 1000 + d.get("tc", 0) if d.get("tm") else d["c"] + 1.5 * min(d.get("r", 0), 120)     # a school zone sign is shown from the still its plate was read best in; any other from the biggest (closest) still it was read surely in
            if d.get("tm"):
                g["_tm"][tuple(d["tm"])] = g["_tm"].get(tuple(d["tm"]), 0) + max(1, d.get("tc", 0))
            if sc > g["_sc"]:
                g.update(conf=d["c"], crop=d.get("f", ""), t=d["t"], _sc=sc)
        for g in out:
            g["times"] = list(max(g["_tm"], key=lambda k: g["_tm"][k])) if g["_tm"] else []     # the hours most of the stills agree on
            del g["_sc"], g["_tm"]
            g["u"], g["u0"], g["u1"] = g["t"], g["t0"], g["t1"]                    # UTC epoch seconds: what the page compares and seeks with
            for k in ("t", "t0", "t1"):
                g[k] = CC.local_iso(g[k], lo)                                       # local clock text (lo = seconds ahead of UTC, else app.timezone), display only
            g["conf"] = round(g["conf"])
        return out

    def minute_signs(self, stamps):
        """{clip stamp: [sign]} for the minutes read that held signs; a sign is one reading seen in several stills (same text and side within merge_s):
        dict(text, side, kind, s0, s1 (seconds into the minute), n, conf, f (the picture of it, /review/<f>))."""
        out = {}
        with _LOCK:
            for st in stamps:
                rows = sorted(((self._day(st[:10]).get(st) or {}).get("d", [])), key=lambda d: d["s"])
                groups = []
                for d in rows:
                    g = next((o for o in reversed(groups) if o["text"] == d["text"] and o["side"] == d["side"] and d["s"] - o["s1"] <= OPTS["merge_s"]), None)
                    if g is None:
                        g = dict(text=d["text"], side=d["side"], kind=d.get("kind", ""), s0=d["s"], s1=d["s"], n=0, conf=-1, f="")
                        groups.append(g)
                    g["n"] += 1
                    g["s1"] = d["s"]
                    sc = d["c"] + 1.5 * min(d.get("r", 0), 120)          # the picture shown is the biggest (closest to the sign) of those read surely: the most pixels to read it from
                    if sc > g.get("_sc", -1):
                        g.update(conf=round(d["c"]), f=d.get("f", ""), _sc=sc)
                for g in groups:
                    g.pop("_sc", None)
                if groups:
                    out[st] = groups
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

    def refine(self, items):
        """The second look: items = [(stamp, front clip path)] of minutes with signs. Around every sign that was seen once, or read unsurely, or read as two different numbers, the video is
        taken again every refine_step_s seconds, the number is voted on over all the stills, and the clearest pictures are kept. Done once per minute."""
        if self.t and self.t.is_alive():
            return self.status()
        need = requirements()
        if need["py"] or need["cmd"]:
            self.st = dict(running=False, done=0, total=0, current="", found=0, error="Missing: " + ", ".join(need["py"] + need["cmd"]), t0=0)
            return self.status()
        with _LOCK:
            un = self.unrefined()
            todo = [i for i in items if i[0] in un]
        self.stop_flag = False
        self.st = dict(running=True, done=0, total=len(todo), current="", found=0, error="", t0=time.time())
        self.t = threading.Thread(target=self._run_refine, args=(todo,), daemon=True)
        self.t.start()
        return self.status()

    def _run_refine(self, todo):
        try:
            for n, (stamp, path) in enumerate(todo):
                if self.stop_flag:
                    break
                self.st["current"] = stamp
                with _LOCK:
                    rec = json.loads(json.dumps(self._day(stamp[:10]).get(stamp) or {}))
                new = refine_clip(path, stamp, rec, self.base)
                with _LOCK:
                    self._day(stamp[:10])[stamp] = new
                    self._save(stamp[:10])
                self.st["done"] = n + 1
                self.st["found"] += len(new.get("d", []))
        except Exception as e:
            self.st["error"] = "%s: %s" % (type(e).__name__, e)

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
    """Stills of a clip every `step` seconds into tmp, full size; returns [(second, file)]. Needs ffmpeg. Only the clip's key frames are decoded (the car writes four a second):
    a still is then at most a quarter second off its second, and the work is a fifth of decoding every frame. A clip with fewer key frames than that (the same picture
    repeated for several stills) is taken again frame by frame."""
    def run(extra):
        for f in os.listdir(tmp):
            if f.startswith("f") and f.endswith(".jpg"):
                os.remove(os.path.join(tmp, f))
        subprocess.run(["ffmpeg", "-v", "error"] + extra + ["-i", path, "-vf", "fps=%g" % (1.0 / step), "-q:v", "2", os.path.join(tmp, "f%04d.jpg")],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=180)
        return sorted(f for f in os.listdir(tmp) if f.startswith("f") and f.endswith(".jpg"))
    fs = run(["-skip_frame", "nokey"])
    same = sum(1 for a, b in zip(fs, fs[1:]) if os.path.getsize(os.path.join(tmp, a)) == os.path.getsize(os.path.join(tmp, b)))
    if len(fs) < 2 or same > 0.25 * len(fs):
        fs = run([])
    return [(k * step, os.path.join(tmp, f)) for k, f in enumerate(fs)]


def scan_clip(path, stamp, v, base):
    """Look through one minute. Returns {n: stills looked at, d: [{s, text, c, x, y, r, side, f}]}."""
    import cv2
    import sign_classes as SC
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
            for c in SC.find_school(im):    # a school zone board, and the hours on its plate when they could be read
                out.append(dict(s=s, text=c["text"], kind="school", c=c["conf"], x=c["x"], y=c["y"], r=c["r"], side=c["side"], tm=c["times"], tc=c["tconf"], crop=c["crop"]))
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
                cpx = float(OPTS["crop_px"])
                if max(h, w) > cpx:
                    z = cpx / max(h, w)
                    crop = cv2.resize(crop, (max(1, int(w * z)), max(1, int(h * z))))
                cv2.imwrite(os.path.join(base, "crops", name), crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
                d["f"] = name
            keep.append(d)
    return dict(n=len(fr), d=keep)


def _spans(d, pad):
    """[[side, from, to, [indexes into d]]]: the stretches of a minute to look at again, one per sign seen (overlapping ones joined), on the side it was seen on."""
    out = []
    for side in ("L", "R"):
        for i in sorted((i for i, x in enumerate(d) if x["side"] == side and x.get("kind") != "school"), key=lambda i: d[i]["s"]):
            a, b = d[i]["s"] - pad, d[i]["s"] + pad
            if out and out[-1][0] == side and a <= out[-1][2]:
                out[-1][2] = max(out[-1][2], b)
                out[-1][3].append(i)
            else:
                out.append([side, a, b, [i]])
    return out


def _weak(d, ix):
    """A sign worth a second look: seen in one still only, read unsurely, or read as different numbers."""
    xs = [d[i] for i in ix]
    return len(xs) == 1 or max(x["c"] for x in xs) < OPTS["refine_conf"] or len({x["text"] for x in xs}) > 1


def _vote(old, new):
    """The number the stills agree on (each reading counts as sure as it was read), the earlier readings of it, and the (at most three) clearest new ones: the biggest, surest rings."""
    score = {}
    for x in list(old) + list(new):
        score[x["text"]] = score.get(x["text"], 0) + x["c"]
    win = max(sorted(score), key=lambda t: score[t])
    best = sorted((x for x in new if x["text"] == win), key=lambda x: -x["c"] * max(1, x.get("r", 1)))[:3]
    return win, [x for x in old if x["text"] == win], best


def _frames_at(path, start, dur, step, tmp):
    """Stills from `start` for `dur` seconds of a clip, every `step` seconds, full size; [(second, file)]."""
    os.makedirs(tmp, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.2f" % max(0.0, start), "-t", "%.2f" % dur, "-i", path, "-vf", "fps=%g" % (1.0 / step), "-q:v", "2", os.path.join(tmp, "r%04d.jpg")],
                   stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
    fs = sorted(f for f in os.listdir(tmp) if f.startswith("r") and f.endswith(".jpg"))
    return [(max(0.0, start) + k * step, os.path.join(tmp, f)) for k, f in enumerate(fs)]


def _look(im, side):
    """The best reading of a round limit sign on one side of a still, or None."""
    import sign_finder as SF
    for c in SF.find_signs(im, roi=SF.ROI_LEFT if side == "L" else SF.ROI_RIGHT):
        if c.get("ok") and c["conf"] >= OPTS["min_conf"] and c["text"]:
            return c
    return None


def refine_clip(path, stamp, rec, base):
    """The second look at one minute that already has signs (see Review.refine). Returns the new record, marked done (rf). A sign read in the first pass that the finer stills
    outvote is dropped with its picture."""
    import cv2
    import sign_classes as SC
    d = list(rec.get("d", []))
    spans = [sp for sp in _spans(d, float(OPTS["refine_pad_s"])) if _weak(d, sp[3])]
    drop, add, k = set(), [], 0
    step = max(0.05, float(OPTS["refine_step_s"]))
    with tempfile.TemporaryDirectory() as tmp:
        for n, (side, a, b, ix) in enumerate(spans):
            a, b = max(0.0, a), min(60.0, b)
            new = []
            for s, f in _frames_at(path, a, b - a, step, os.path.join(tmp, str(n))):
                im = cv2.imread(f)
                c = _look(im, side) if im is not None else None
                if c:
                    new.append(dict(s=round(s, 2), text=c["text"], c=round(c["conf"]), x=c["x"], y=c["y"], r=c["r"], side=side, crop=c["crop"]))
            if not new:
                continue
            win, _keep, best = _vote([d[i] for i in ix], new)
            drop |= {i for i in ix if d[i]["text"] != win}
            os.makedirs(os.path.join(base, "crops"), exist_ok=True)
            for x in best:
                crop = x.pop("crop")
                h, w = crop.shape[:2]
                if h > 0 and w > 0:
                    cpx = float(OPTS["crop_px"])
                    if max(h, w) > cpx:
                        z = cpx / max(h, w)
                        crop = cv2.resize(crop, (max(1, int(w * z)), max(1, int(h * z))))
                    x["f"] = "%s_r%02d.jpg" % (stamp, k)
                    k += 1
                    cv2.imwrite(os.path.join(base, "crops", x["f"]), crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
                add.append(x)
    # school zone signs seen without their hours: the plate is only readable close up, so the video is taken again finely around them
    sz = sorted(x["s"] for x in d if x.get("kind") == "school" and not x.get("tm"))
    wins = []
    for t in sz:
        if wins and t - wins[-1][1] <= 2 * float(OPTS["refine_pad_s"]):
            wins[-1][1] = t
        else:
            wins.append([t, t])
    kz = 0
    with tempfile.TemporaryDirectory() as tmp:
        for n, (a, b) in enumerate(wins):
            a, b = max(0.0, a - float(OPTS["refine_pad_s"])), min(60.0, b + float(OPTS["refine_pad_s"]))
            got = []
            for s_, f in _frames_at(path, a, b - a, step, os.path.join(tmp, "z%d" % n)):
                im = cv2.imread(f)
                for c in (SC.find_school(im) if im is not None else []):
                    if c["times"]:
                        got.append(dict(s=round(s_, 2), text=c["text"], kind="school", c=c["conf"], x=c["x"], y=c["y"], r=c["r"], side=c["side"], tm=c["times"], tc=c["tconf"], crop=c["crop"]))
            for x in sorted(got, key=lambda x: -x["tc"])[:2]:
                crop = x.pop("crop")
                h, w = crop.shape[:2]
                if h > 0 and w > 0:
                    cpx = float(OPTS["crop_px"])
                    if max(h, w) > cpx:
                        z = cpx / max(h, w)
                        crop = cv2.resize(crop, (max(1, int(w * z)), max(1, int(h * z))))
                    x["f"] = "%s_z%02d.jpg" % (stamp, kz)
                    kz += 1
                    os.makedirs(os.path.join(base, "crops"), exist_ok=True)
                    cv2.imwrite(os.path.join(base, "crops", x["f"]), crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
                add.append(x)
    for i in drop:
        if d[i].get("f"):
            try:
                os.remove(os.path.join(base, "crops", d[i]["f"]))
            except OSError:
                pass
    kept = [x for i, x in enumerate(d) if i not in drop] + add
    return dict(rec, d=sorted(kept, key=lambda x: x["s"]), rf=1)
