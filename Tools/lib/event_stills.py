"""event_stills.py - the pictures around a Saved or Sentry event, kept after the video is gone, at a higher resolution than the one-second stills of ordinary driving.

    <data>/teslacam_event_stills/<saved|sentry>/<event folder>/<camera>.zip     <clip stamp>_<NN>.jpg: second NN of that minute's clip, one a second
    <data>/teslacam_event_stills/<saved|sentry>/<event folder>/info.json        dict(cam, tier, width, quality, trigger=[clip, sec], t, first, last, n)

Three levels of picture (the level is `tier` in info.json):

    high     app.stills.event_width (1280 px) wide: HALF_S seconds either side of the moment the event happened (the Sentry trigger, the moment Saved was pressed)
    normal   app.stills.width (400 px), the size of the stills of every other minute
    shrunk   app.stills.thin_width (240 px), the size ordinary driving is shrunk to (stills_thin.py)

An event starts at "high" and can be shrunk to "normal", and from there to "shrunk" (shrink()). The rest of the event's minutes are the ordinary front stills (teslacam_stills.py).

Which camera: a Sentry event the camera that saw it (event.json "camera": 0/1 front, 5 left repeater, 6 right repeater, 7 back; front when not known), a Saved clip the front camera.
The moment is the event's own time (event_shots.moment) on the car's clock, like the clip names. Needs ffmpeg.

    camera(kind, ev)                              -> the camera pictured
    window(ev, cams_by_stamp, camera)             -> [(clip stamp, first second, last second)]: the part of each minute with a clip of that camera inside the window
    EventStills(data).info(kind, eid)             -> info.json or {}
    EventStills(data).get(kind, eid, stamp, sec)  -> JPEG bytes or None
    EventStills(data).seconds(kind, eid)          -> {clip stamp: [seconds kept]}
    EventStills(data).make(kind, ev, window, paths, stop=None)   paths = {clip stamp: video file of that camera}; -> pictures made
    EventStills(data).shrink(kind, eid, tier)     -> (bytes before, bytes after) or None when there is nothing to do
"""
import json
import os
import tempfile
import zipfile

import app_config
import clip_clock as CC
import event_shots as ES
import stills_thin as STH
from lib import teslacam_stills as ST

DIRNAME = "teslacam_event_stills"
HALF_S = 30
CLIP_S = 60
DENSE_S = 10        # seconds either side of the moment that get a picture every half second (front camera only)
CAMS = ("front", "left_pillar", "right_pillar", "left_repeater", "back", "right_repeater")


class Paths(dict):
    """{clip stamp: video file of the main camera}, with `all` = {clip stamp: {camera: video file}} when the other cameras are on the drive too."""
    all = None
TIERS = ("high", "normal", "shrunk")


def camera(kind, ev):
    if kind == "sentry":
        return ES.EVCAM.get(str(ev.get("cam") if ev.get("cam") is not None else ""), "front")
    return "front"


def window(ev, cams_by_stamp, cam, half=HALF_S):
    """[(clip stamp, first second, last second)] for the minutes (with `cam` among their cameras) that the window of `half` seconds either side of the event touches."""
    mom = ES.moment(ev, sorted(cams_by_stamp))
    if mom is None:
        return []
    t = CC.utc(mom[0]) + mom[1]
    out = []
    for st in sorted(cams_by_stamp):
        if cam not in cams_by_stamp[st]:
            continue
        s0 = CC.utc(st)
        a, b = max(0, int(t - half - s0)), min(CLIP_S - 1, int(t + half - s0))
        if a <= b and t - half < s0 + CLIP_S and t + half >= s0:
            out.append((st, a, b))
    return out


def _widths():
    return {"high": int(app_config.get("stills.event_width", 1280)), "normal": int(app_config.get("stills.width", 400)), "shrunk": int(app_config.get("stills.thin_width", 240)), "side": int(app_config.get("stills.event_side_width", 800))}


def _quality():
    return {"high": int(app_config.get("stills.event_quality", 3)), "normal": int(app_config.get("stills.quality", 6)), "shrunk": int(app_config.get("stills.thin_quality", 10)), "side": int(app_config.get("stills.event_side_quality", 4))}


class EventStills:
    def __init__(self, data_dir):
        self.root = os.path.join(data_dir, DIRNAME)

    def folder(self, kind, eid):
        return os.path.join(self.root, kind, eid)

    def remove(self, kind, eid):
        """Deletes everything kept for the event; returns the bytes freed."""
        d = self.folder(kind, eid)
        freed = 0
        try:
            for fn in os.listdir(d):
                fp = os.path.join(d, fn)
                try:
                    freed += os.path.getsize(fp)
                    os.remove(fp)
                except OSError:
                    pass
            os.rmdir(d)
        except OSError:
            pass
        return freed

    def info(self, kind, eid):
        try:
            with open(os.path.join(self.folder(kind, eid), "info.json"), encoding="utf-8") as fh:
                j = json.load(fh)
                return j if isinstance(j, dict) else {}
        except (OSError, ValueError):
            return {}

    def _zip(self, kind, eid, cam=None, half=False):
        cam = cam or self.info(kind, eid).get("cam") or ""
        return os.path.join(self.folder(kind, eid), cam + (".h" if half else "") + ".zip") if cam else ""

    def cams(self, kind, eid):
        """The cameras with pictures kept for the event (the main one first)."""
        i = self.info(kind, eid)
        got = [c for c in (i.get("cams") or [i.get("cam")]) if c and os.path.isfile(self._zip(kind, eid, c))]
        return got

    def halves(self, kind, eid):
        """The camera whose half-second pictures are kept, or ""."""
        c = self.info(kind, eid).get("cam") or ""
        return c if c and os.path.isfile(self._zip(kind, eid, c, True)) else ""

    def size(self, kind, eid):
        """Bytes the event's pictures take."""
        d, n = self.folder(kind, eid), 0
        try:
            for fn in os.listdir(d):
                if fn.endswith(".zip"):
                    n += os.path.getsize(os.path.join(d, fn))
        except OSError:
            pass
        return n

    def zips(self, kind, eid):
        d = self.folder(kind, eid)
        try:
            return [os.path.join(d, fn) for fn in sorted(os.listdir(d)) if fn.endswith(".zip")]
        except OSError:
            return []

    def seconds(self, kind, eid, cam=None, half=False):
        z = self._zip(kind, eid, cam, half)
        out = {}
        try:
            with zipfile.ZipFile(z) as zf:
                for n in zf.namelist():
                    if n.endswith(".jpg") and len(n) == 26:                       # <19 char stamp>_<NN>.jpg
                        out.setdefault(n[:19], []).append(int(n[20:22]))
        except (OSError, zipfile.BadZipFile):
            return {}
        return {k: sorted(v) for k, v in out.items()}

    def get(self, kind, eid, stamp, sec, cam=None, half=False):
        z = self._zip(kind, eid, cam, half)
        try:
            with zipfile.ZipFile(z) as zf:
                return zf.read("%s_%02d.jpg" % (stamp, sec))
        except (OSError, KeyError, zipfile.BadZipFile):
            return None

    def wants_more(self, kind, eid):
        """True when the event's pictures could still get the other cameras and the half-second pictures (a "high" event that was not reduced to the front camera)."""
        i = self.info(kind, eid)
        return bool(i.get("n")) and (i.get("tier") or "high") == "high" and not i.get("reduced") and not (i.get("more") or i.get("more_tried"))

    def make(self, kind, ev, win, paths, stop=None):
        """Cuts the pictures of the window from the clips in `paths` ({clip stamp: video file}); a window already made is left alone. Returns how many pictures were made.
        A drive that stops answering raises teslacam_stills.DriveStalled."""
        eid = ev["id"]
        got = getattr(paths, "all", None)
        n = 0
        if not self.info(kind, eid).get("n"):
            n = self._make_main(kind, ev, win, paths, stop)
        if got and not (stop and stop()) and self.wants_more(kind, eid):
            n += self.make_more(kind, ev, got, stop)
        return n

    def _make_main(self, kind, ev, win, paths, stop=None):
        eid = ev["id"]
        cam = camera(kind, ev)
        wd, q = _widths()["high"], _quality()["high"]
        out = self.folder(kind, eid)
        os.makedirs(out, exist_ok=True)
        names, n = [], 0
        with tempfile.TemporaryDirectory() as tmp:
            for stamp, a, b in win:
                if (stop and stop()) or stamp not in paths:
                    continue
                pre = os.path.join(tmp, stamp + "_")
                ST.run_ffmpeg(["ffmpeg", "-v", "error", "-ss", str(a), "-t", str(b - a + 1), "-i", paths[stamp], "-vf", "fps=1,scale=%d:-2" % wd, "-q:v", str(q), "-start_number", "0", pre + "%03d.jpg"])
                for i in range(b - a + 1):
                    f = "%s%03d.jpg" % (pre, i)
                    if os.path.isfile(f) and os.path.getsize(f) > 0:
                        names.append((f, "%s_%02d.jpg" % (stamp, a + i)))
            if not names and not (stop and stop()) and win:
                with open(os.path.join(out, "info.json"), "w", encoding="utf-8") as fh:      # nothing came out of the video: not asked for again
                    json.dump(dict(cam=cam, n=0, tried=1), fh)
            if not names or (stop and stop()):
                return 0
            part = os.path.join(out, cam + ".zip.part")
            with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED) as z:
                for f, nm in names:
                    z.write(f, nm)
            os.replace(part, os.path.join(out, cam + ".zip"))
            n = len(names)
        mom = ES.moment(ev, [w[0] for w in win]) or ("", 0)
        info = dict(cam=cam, cams=[cam], tier="high", width=wd, quality=q, trigger=list(mom), t=ev.get("ts") or "", first=names[0][1][:-4], last=names[-1][1][:-4], n=n)
        self._save_info(kind, eid, info)
        return n

    def _save_info(self, kind, eid, info):
        p = os.path.join(self.folder(kind, eid), "info.json")
        with open(p + ".part", "w", encoding="utf-8") as fh:
            json.dump(info, fh)
        os.replace(p + ".part", p)

    def _cut(self, tmp, tag, path, a, n, wd, q, fps, off=0.0):
        """Cuts `n` pictures from second `a`+`off` of the video; returns the files made."""
        pre = os.path.join(tmp, tag + "_")
        if n < 1:
            return []
        ST.run_ffmpeg(["ffmpeg", "-v", "error", "-ss", "%g" % (a + off), "-t", str(n), "-i", path, "-vf", "fps=%s,scale=%d:-2" % (fps, wd), "-q:v", str(q), "-start_number", "0", pre + "%03d.jpg"])
        fs = ["%s%03d.jpg" % (pre, i) for i in range(n)]
        return [f if os.path.isfile(f) and os.path.getsize(f) > 0 else None for f in fs]

    def _write_zip(self, path, files):
        part = path + ".part"
        with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED) as z:
            for f, nm in files:
                z.write(f, nm)
        os.replace(part, path)

    def make_more(self, kind, ev, allp, stop=None):
        """The other five cameras at app.stills.event_side_width, one a second over the same window as the main camera, and the half-second pictures of the main camera within DENSE_S of the moment.
        `allp` = {clip stamp: {camera: video file}}. A part whose video is not there is left out; nothing more is asked for once this has run (info "more"). Returns pictures made."""
        eid = ev["id"]
        info = self.info(kind, eid)
        cam = info.get("cam") or camera(kind, ev)
        cams = {s: set(c) for s, c in allp.items()}
        win = window(ev, cams, cam)
        if not win:
            return 0
        out, made, n = self.folder(kind, eid), list(info.get("cams") or [cam]), 0
        wd, q = _widths(), _quality()
        with tempfile.TemporaryDirectory() as tmp:
            for c in CAMS:
                if c == cam or (stop and stop()):
                    continue
                files = []
                for stamp, a, b in window(ev, cams, c):
                    if stop and stop():
                        break
                    if c not in allp.get(stamp, {}):
                        continue
                    got = self._cut(tmp, "%s_%s" % (c, stamp), allp[stamp][c], a, b - a + 1, wd["side"], q["side"], 1)
                    files += [(f, "%s_%02d.jpg" % (stamp, a + i)) for i, f in enumerate(got) if f]
                if files and not (stop and stop()):
                    self._write_zip(os.path.join(out, c + ".zip"), files)
                    made.append(c)
                    n += len(files)
            half = []
            for stamp, a, b in window(ev, cams, cam, DENSE_S):
                if (stop and stop()) or cam not in allp.get(stamp, {}):
                    continue
                got = self._cut(tmp, "h_" + stamp, allp[stamp][cam], a, b - a, wd["high"], q["high"], 1, 0.5)
                half += [(f, "%s_%02d.jpg" % (stamp, a + i)) for i, f in enumerate(got)]
            if half and not (stop and stop()):
                self._write_zip(os.path.join(out, cam + ".h.zip"), half)
                n += len(half)
        if stop and stop():
            return n
        info = self.info(kind, eid)
        info.update(cams=made, side_width=wd["side"], dense=DENSE_S if half else 0)
        info["more" if len(made) > 1 or half else "more_tried"] = 1
        self._save_info(kind, eid, info)
        return n

    def reduce(self, kind, eid):
        """Keeps only the main camera's one-a-second pictures of the event: deletes the other cameras and the half-second pictures. Returns the bytes freed (None when there was nothing to reduce)."""
        info = self.info(kind, eid)
        keep = self._zip(kind, eid)
        freed = 0
        for z in self.zips(kind, eid):
            if z != keep:
                try:
                    freed += os.path.getsize(z)
                    os.remove(z)
                except OSError:
                    pass
        if not freed and info.get("reduced"):
            return None
        info.update(cams=[info.get("cam")], dense=0, reduced=1)
        info.pop("more", None)
        self._save_info(kind, eid, info)
        return freed

    def shrink(self, kind, eid, tier):
        """Re-encodes the pictures of the event at the width of `tier` ("normal" or "shrunk"); a picture is never made larger. Only ever one step down: high -> normal -> shrunk
        (asking for "shrunk" on a "high" event goes straight to it). Returns (bytes before, bytes after), or None when the event is already at that level or below."""
        info = self.info(kind, eid)
        if tier not in TIERS[1:] or not info.get("n") or TIERS.index(info.get("tier") or "high") >= TIERS.index(tier):
            return None
        old = new = self.reduce(kind, eid) or 0         # shrinking keeps the main camera only
        old += new
        new = 0
        for z in self.zips(kind, eid):
            o, nw = STH.thin_zip(z, _widths()[tier], _quality()[tier])
            old, new = old + o, new + nw
        info.update(tier=tier, width=min(_widths()[tier], info.get("width") or 9999), quality=_quality()[tier])
        p = os.path.join(self.folder(kind, eid), "info.json")
        with open(p + ".part", "w", encoding="utf-8") as fh:
            json.dump(info, fh)
        os.replace(p + ".part", p)
        return old, new

    def events(self):
        """[(kind, event folder, info)] of every event with pictures kept."""
        out = []
        for kind in ("saved", "sentry"):
            try:
                for eid in sorted(os.listdir(os.path.join(self.root, kind))):
                    i = self.info(kind, eid)
                    if i.get("n"):
                        out.append((kind, eid, i))
            except OSError:
                pass
        return out
