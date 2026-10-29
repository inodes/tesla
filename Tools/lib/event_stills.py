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
import teslacam_stills as ST

DIRNAME = "teslacam_event_stills"
HALF_S = 30
CLIP_S = 60
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
    return {"high": int(app_config.get("stills.event_width", 1280)), "normal": int(app_config.get("stills.width", 400)), "shrunk": int(app_config.get("stills.thin_width", 240))}


def _quality():
    return {"high": int(app_config.get("stills.event_quality", 3)), "normal": int(app_config.get("stills.quality", 6)), "shrunk": int(app_config.get("stills.thin_quality", 10))}


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

    def _zip(self, kind, eid, cam=None):
        cam = cam or self.info(kind, eid).get("cam") or ""
        return os.path.join(self.folder(kind, eid), cam + ".zip") if cam else ""

    def seconds(self, kind, eid):
        z = self._zip(kind, eid)
        out = {}
        try:
            with zipfile.ZipFile(z) as zf:
                for n in zf.namelist():
                    if n.endswith(".jpg") and len(n) == 26:                       # <19 char stamp>_<NN>.jpg
                        out.setdefault(n[:19], []).append(int(n[20:22]))
        except (OSError, zipfile.BadZipFile):
            return {}
        return {k: sorted(v) for k, v in out.items()}

    def get(self, kind, eid, stamp, sec):
        z = self._zip(kind, eid)
        try:
            with zipfile.ZipFile(z) as zf:
                return zf.read("%s_%02d.jpg" % (stamp, sec))
        except (OSError, KeyError, zipfile.BadZipFile):
            return None

    def make(self, kind, ev, win, paths, stop=None):
        """Cuts the pictures of the window from the clips in `paths` ({clip stamp: video file}); a window already made is left alone. Returns how many pictures were made.
        A drive that stops answering raises teslacam_stills.DriveStalled."""
        eid = ev["id"]
        if self.info(kind, eid).get("n"):
            return 0
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
            if not names or (stop and stop()):
                return 0
            part = os.path.join(out, cam + ".zip.part")
            with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED) as z:
                for f, nm in names:
                    z.write(f, nm)
            os.replace(part, os.path.join(out, cam + ".zip"))
            n = len(names)
        mom = ES.moment(ev, [w[0] for w in win]) or ("", 0)
        info = dict(cam=cam, tier="high", width=wd, quality=q, trigger=list(mom), t=ev.get("ts") or "", first=names[0][1][:-4], last=names[-1][1][:-4], n=n)
        with open(os.path.join(out, "info.json.part"), "w", encoding="utf-8") as fh:
            json.dump(info, fh)
        os.replace(os.path.join(out, "info.json.part"), os.path.join(out, "info.json"))
        return n

    def shrink(self, kind, eid, tier):
        """Re-encodes the pictures of the event at the width of `tier` ("normal" or "shrunk"); a picture is never made larger. Only ever one step down: high -> normal -> shrunk
        (asking for "shrunk" on a "high" event goes straight to it). Returns (bytes before, bytes after), or None when the event is already at that level or below."""
        info = self.info(kind, eid)
        if tier not in TIERS[1:] or not info.get("n") or TIERS.index(info.get("tier") or "high") >= TIERS.index(tier):
            return None
        z = self._zip(kind, eid)
        old, new = STH.thin_zip(z, _widths()[tier], _quality()[tier])
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
