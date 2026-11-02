"""teslacam_ingest.py - read what a TeslaCam minute holds ONCE, keep it on this computer, and never ask the drive for it again.

For every Recent / Saved minute the front clip gives two small things, both read in the same pass over the file:

    telemetry   speed, GPS, steering, pedals, FSD ... (teslacam_timeline.Store: <data>/teslacam_timeline/)
    thumbnail   one small picture from the start of the clip, ~10 KB   (<data>/teslacam_thumbs/<day>.zip, one zip per day)

After that the Timeline and the Drives pages work from what is kept, with the drive away. The drive is only needed for what was not read yet
and for playing the full video. The one-second stills (teslacam_stills.py) are a separate, heavier job and are not made here.

This module knows the timeline, not drives: it is given the minutes and events that exist and a `locate` function that says where the
footage of one can be read right now (a path, or None). Which drive holds what, and how to reach it, is the drive's side (teslacam_locate.py),
so another source of footage only has to supply its own `locate`.

    day_status(store, thumbs, minutes, locate)          what is read, what is missing, what can be read now
    pull(store, thumbs, minutes, locate, ...)           read the missing ones (telemetry + thumbnail in one pass), keep them
    pull_events(images, kind, events, locate, ...)      keep the picture of what triggered each event
"""
import collections
import concurrent.futures as cf
import io
import os
import threading
import time
import zipfile

from lib import teslacam_stills as ST
import teslacam_timeline as TL

DIRNAME = "teslacam_thumbs"
WIDTH = 320
QUALITY = 6                     # ffmpeg -q:v
_LOCK = threading.RLock()


class Thumbs:
    """One small JPEG per minute, kept in one zip per day. A day's zip is rewritten as a whole (a temporary file, then renamed), so it is
    never half written."""

    def __init__(self, data_dir):
        self.base = os.path.join(data_dir, DIRNAME)
        self._names = {}

    def path(self, day):
        return os.path.join(self.base, day + ".zip")

    def _have(self, day):
        p = self.path(day)
        try:
            mt = os.path.getmtime(p)
        except OSError:
            return set()
        c = self._names.get(day)
        if c and c[0] == mt:
            return c[1]
        try:
            with zipfile.ZipFile(p) as z:
                names = {n[:-4] for n in z.namelist() if n.endswith(".jpg")}
        except (OSError, zipfile.BadZipFile):
            names = set()
        self._names[day] = (mt, names)
        return names

    def has(self, stamp):
        with _LOCK:
            return stamp in self._have(stamp[:10])

    def get(self, stamp):
        with _LOCK:
            try:
                with zipfile.ZipFile(self.path(stamp[:10])) as z:
                    return z.read(stamp + ".jpg")
            except (OSError, KeyError, zipfile.BadZipFile):
                return None

    def put_many(self, items):
        """items = {stamp: jpeg bytes}; written to each day's zip, keeping what it already holds."""
        with _LOCK:
            byday = collections.defaultdict(dict)
            for s, b in items.items():
                if b:
                    byday[s[:10]][s] = b
            for day, new in byday.items():
                old = {}
                try:
                    with zipfile.ZipFile(self.path(day)) as z:
                        old = {n[:-4]: z.read(n) for n in z.namelist() if n.endswith(".jpg")}
                except (OSError, zipfile.BadZipFile):
                    pass
                old.update(new)
                os.makedirs(self.base, exist_ok=True)
                part = self.path(day) + ".part"
                with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED) as z:
                    for s in sorted(old):
                        z.writestr(s + ".jpg", old[s])
                os.replace(part, self.path(day))
                self._names.pop(day, None)

    def count(self, day):
        with _LOCK:
            return len(self._have(day))


def make_thumb(video, width=WIDTH, quality=QUALITY):
    """JPEG bytes of the first key frame of a clip (about 0.1 s; the video is not decoded beyond that frame), or None. Raises ST.DriveStalled."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "t.jpg")
        ST.run_ffmpeg(["ffmpeg", "-v", "error", "-skip_frame", "nokey", "-i", video, "-frames:v", "1", "-vf", "scale=%d:-2" % width, "-q:v", str(quality), out])
        try:
            with open(out, "rb") as f:
                return f.read() or None
        except OSError:
            return None


def _job(path, want_tel, want_thumb, width, quality):
    """Runs in a worker process: (series or None, jpeg or None, stalled message or '', error message or ''). The file is read once for both."""
    import dashcam_series as DS
    import struct
    series = jpg = None
    stall = err = ""
    try:
        if want_tel:
            series = DS.read_series(path)
    except (OSError, struct.error) as e:
        err = "%s: %s" % (type(e).__name__, e)
    try:
        if want_thumb:
            jpg = make_thumb(path, width, quality)
    except ST.DriveStalled as e:
        stall = str(e)
    return series, jpg, stall, err


def day_status(store, thumbs, minutes, locate):
    """What a day has and lacks. `minutes` = [dict(stamp, roles?)]; `locate(minute)` = the clip's path now, or None.
    dict(minutes, telemetry, thumbs, missing=[stamp], pullable=[stamp] (missing and reachable now), away=[source names] (hold something missing, not reachable))"""
    missing, pullable, away = [], [], set()
    n_tel = n_th = 0
    for m in minutes:
        tel = store.summary(m["stamp"]) is not None
        th = thumbs.has(m["stamp"])
        n_tel += tel
        n_th += th
        if tel and th:
            continue
        missing.append(m["stamp"])
        if locate(m):
            pullable.append(m["stamp"])
        else:
            away.update(m.get("roles") or [])
    return dict(minutes=len(minutes), telemetry=n_tel, thumbs=n_th, missing=missing, pullable=pullable, away=sorted(away))


def pull(store, thumbs, items, locate, stop=None, progress=None, deadline=None, workers=None):
    """Reads the telemetry and the thumbnail that `items` (minutes: dict(stamp, ...)) still lack, from wherever `locate(minute)` says the clip can be read, in
    worker processes, and keeps them. Newest first. Stops at `stop()` / `deadline`, or when a drive stalls (the rest is left for later).
    Returns dict(read, thumbs, skipped (not on a connected drive), failed, stalled (message or ''))."""
    w = max(1, int(workers or TL.OPTS["read_workers"]))
    todo, skipped = [], 0
    for m in sorted(items, key=lambda x: x["stamp"], reverse=True):
        want_tel, want_th = store.summary(m["stamp"]) is None, not thumbs.has(m["stamp"])
        if not (want_tel or want_th):
            continue
        path = locate(m)
        if not path:
            skipped += 1
            continue
        todo.append((m["stamp"], path, want_tel, want_th))
    res = dict(read=0, thumbs=0, skipped=skipped, failed=0, stalled="")
    if not todo:
        return res
    pend = {}
    n = 0
    with cf.ProcessPoolExecutor(w) as ex:
        win, it = collections.deque(), iter(todo)

        def fill():
            while len(win) < w * 2:
                nxt = next(it, None)
                if nxt is None:
                    return
                win.append((nxt, ex.submit(_job, nxt[1], nxt[2], nxt[3], WIDTH, QUALITY)))

        def keep():
            if pend:
                thumbs.put_many(dict(pend))
                pend.clear()
        fill()
        while win:
            (stamp, path, want_tel, want_th), fut = win.popleft()
            if (stop and stop()) or (deadline and time.time() > deadline) or res["stalled"]:
                fut.cancel()
                for _, f in win:
                    f.cancel()
                break
            series, jpg, stall, err = fut.result()
            if stall:
                res["stalled"] = stall                      # leave this one unread; the next pull tries again
                continue
            if want_tel:
                if err and series is None:
                    res["failed"] += 1                      # not written: a clip that could not be read is tried again, not recorded as empty
                else:
                    store.put(stamp, series)
                    res["read"] += 1
            if want_th and jpg:
                pend[stamp] = jpg
                res["thumbs"] += 1
            n += 1
            if len(pend) >= 20:
                keep()
            if progress:
                progress(n, len(todo), stamp)
            fill()
        keep()
    store.flush()
    return res


# ------------------------------------------------------------------------------------------------ the picture of an event
EVENT_W = 640                   # the picture of what triggered a Sentry / Saved event: big enough for a card and a map popup
EVENT_Q = 3
EVCAM = {"0": "front", "1": "front", "5": "left_repeater", "6": "right_repeater", "7": "back"}      # event.json "camera" -> clip name (the same table app.js uses)


def trigger_point(ev):
    """(clip minute stamp, seconds into that clip) of the moment an event was detected: the last minute that starts at or before it (an event begins
    minutes earlier), 1 to 58 s in. `ev` = a kept event (ts, cam, clips) or the app's shown one (cts). The event time and the clip names are both the
    CAR's clock, which is not always the local clock and can change inside one event (clip_clock.py), so every comparison is made on UTC."""
    import clip_clock as CC
    ks = sorted((ev.get("clips") or {}), key=CC.utc)
    if not ks:
        return None, 2
    try:
        tu = CC.utc(ev.get("cts") or ev.get("ts") or "")             # `ts` of a shown event is local time for display: `cts` is the car's reading
    except ValueError:
        return ks[0], 2
    before = [k for k in ks if CC.utc(k) <= tu]
    k0 = before[-1] if before else ks[0]
    return k0, int(max(1, min(58, round(tu - CC.utc(k0)))))


def trigger_camera(ev, k0):
    """The camera that saw a Sentry event, or the front camera when it is not one of the clips of that minute."""
    have = (ev.get("clips") or {}).get(k0) or []
    cam = EVCAM.get(str(ev.get("cam")), "front")
    return cam if cam in have else ("front" if "front" in have else (have[0] if have else None))


class EventImages:
    """One picture per event, from the camera that saw it at the moment it was detected, kept in one zip per kind (events_sentry.zip, events_saved.zip)
    next to the minute thumbnails, so a card keeps its picture when the drive is away."""

    def __init__(self, data_dir):
        self.base = os.path.join(data_dir, DIRNAME)

    def path(self, kind):
        return os.path.join(self.base, "events_%s.zip" % kind)

    def _names(self, kind):
        try:
            with zipfile.ZipFile(self.path(kind)) as z:
                return {n[:-4] for n in z.namelist() if n.endswith(".jpg")}
        except (OSError, zipfile.BadZipFile):
            return set()

    def has(self, kind, eid):
        with _LOCK:
            return eid in self._names(kind)

    def get(self, kind, eid):
        with _LOCK:
            try:
                with zipfile.ZipFile(self.path(kind)) as z:
                    return z.read(eid + ".jpg")
            except (OSError, KeyError, zipfile.BadZipFile):
                return None

    def put_many(self, kind, items):
        with _LOCK:
            old = {}
            try:
                with zipfile.ZipFile(self.path(kind)) as z:
                    old = {n[:-4]: z.read(n) for n in z.namelist() if n.endswith(".jpg")}
            except (OSError, zipfile.BadZipFile):
                pass
            old.update({k: v for k, v in items.items() if v})
            os.makedirs(self.base, exist_ok=True)
            part = self.path(kind) + ".part"
            with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED) as z:
                for k in sorted(old):
                    z.writestr(k + ".jpg", old[k])
            os.replace(part, self.path(kind))


def make_event_image(video, seconds, width=EVENT_W, quality=EVENT_Q):
    """JPEG bytes of one frame of a clip `seconds` in (about 0.15 s), or None. The last clip of an event is often cut short (a few seconds), so when
    `seconds` is past its end the last frame is taken. Raises ST.DriveStalled."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "e.jpg")
        for seek in (["-ss", str(seconds)], ["-sseof", "-0.3"]):
            ST.run_ffmpeg(["ffmpeg", "-v", "error", "-y"] + seek + ["-i", video, "-frames:v", "1", "-vf", "scale=%d:-2" % width, "-q:v", str(quality), out])
            try:
                with open(out, "rb") as f:
                    b = f.read()
                if b:
                    return b
            except OSError:
                pass
        return None


def pull_events(images, kind, events, locate, stop=None, progress=None):
    """Keeps the picture of every event in `events` (kept or shown events of one kind) that lacks one, from the clip of the triggering camera that
    `locate(kind, event, minute stamp, camera)` returns (a path, or None). Returns dict(made, skipped (not reachable now, or no clips), failed, stalled)."""
    res = dict(made=0, skipped=0, failed=0, stalled="")
    todo = []
    for e in events:
        if images.has(kind, e["id"]):
            continue
        k0, sec = trigger_point(e)
        cam = trigger_camera(e, k0) if k0 else None
        if not cam:
            res["skipped"] += 1
            continue
        path = locate(kind, e, k0, cam)
        if not path:
            res["skipped"] += 1
            continue
        todo.append((e["id"], path, sec))
    got = {}
    for n, (eid, path, sec) in enumerate(todo, 1):
        if (stop and stop()) or res["stalled"]:
            break
        try:
            b = make_event_image(path, sec)
        except ST.DriveStalled as ex:
            res["stalled"] = str(ex)
            break
        if b:
            got[eid] = b
            res["made"] += 1
        else:
            res["failed"] += 1
        if len(got) >= 10:
            images.put_many(kind, got)
            got = {}
        if progress:
            progress(n, len(todo), eid)
    if got:
        images.put_many(kind, got)
    return res
