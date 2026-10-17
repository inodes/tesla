"""app_drive_review.py - a review of one drive, started by hand from the Review tab of that drive (tesla_app.py).

Three kinds of event are found from the dashcam telemetry (one reading a second per minute of footage, kept in <data>/teslacam_timeline, see
teslacam_timeline.py - nothing is read from the video for this part):

  intervention   FSD switched off (to manual, Autosteer or TACC): what the driver was doing at that moment (brake / accelerator / steering),
                 how long FSD was off, and whether it was the end of the trip (stopped)
  accelerator    the accelerator pressed while FSD was on (a run of seconds at or above accel_min_pct): length, peak, speed
  speed change   every lasting change of speed_min_kmh or more (the speed a few seconds before against a few seconds after) while FSD stayed on; the
                 page filters them afterwards (for example 100 -> 110 km/h)

Then, for each event only, the FRONT camera is looked at: a still every second from event_before_s before to event_after_s after (the usual
28 stills; a longer event is thinned to max_event_stills), every round red-ringed speed-limit sign in them is read (sign_finder.py + tesseract,
as app_review.py does for a whole drive), and the stills are kept as one contact sheet per event. No other camera is touched, and a drive's
other minutes are not looked at. The events and the sheets are kept in <data>/drive_review/drives/<stamp>.json and .../events/<stamp>/ so
a finished review opens instantly; running it again replaces them. Nothing on a drive or in Tessie is changed.

Time: UTC is the clock. The dashcam's file names are the car's clock, which clip_clock.py turns into UTC minute by minute (it can sit on standard time for
days, and change when the car crosses a time-zone border, even inside this drive), and Tessie's local times are turned into UTC with app.timezone, so a drive and its footage are always compared as the same instant. Each event keeps its UTC instant ("utc"); "t" is the drive page's local
clock, which is only what is shown.
"""
import bisect
import datetime
import json
import re
import math
import os
import statistics
import subprocess
import tempfile
import threading
import time

import app_config
import app_review
import clip_clock as CC
import drive_paths as DP
import drive_fuse as DF
import teslacam_stills as ST
import teslacam_timeline as TL

OPTS = dict(accel_min_pct=5,        # accelerator at or above this (%) counts as pressed
            merge_s=3,              # presses / levels with a gap shorter than this are one
            speed_min_kmh=5,        # a lasting change of speed at least this big, with FSD on, is a speed-change event (a Hurry -> Standard switch from 100 to 110 is about 105 -> 112)
            speed_tol_kmh=6,        # the page's speed filter: a speed this close to the one asked for matches (the car rarely sits exactly on the set speed)
            event_before_s=12,      # stills from this long before an event ...
            event_after_s=15,       # ... to this long after it (28 stills with the one a second)
            max_event_stills=40,    # a longer event is thinned to this many
            hi_px=1000,             # the big picture of each still of an event (cut from the full-size frame while the video is there): width in px, 0 = none
            exit_car_kmh=90,        # a sign read while the car goes at least this fast ...
            exit_gap_kmh=20,        # ... and this much lower than the car is marked as probably an exit or side road
            hi_quality=88,          # and its JPEG quality
            sign_min_stills=2,      # a coloured sign (not a speed sign) must be seen in this many stills, or it is taken as noise
            works_gap_s=40,         # a speed sign that differs from the map this close in time to a road-work sign is marked as a temporary limit
            osm_fetch=True,         # fetch a missing OpenStreetMap tile (a few seconds) to compare a speed sign with the map's limit
            steer_deg=15)           # the wheel moved this much around a switch-off = "steering"
MODES = {0: "Manual", 1: "FSD", 2: "Autosteer", 3: "TACC"}
SIGN_VER = 2                        # bump when the sign finders change: the kept readings (signcache) are then made again
MAXGAP = 3                          # seconds between two readings that are still consecutive
_LOCK = threading.RLock()


def _mode(n):
    return MODES.get(n, "mode %s" % n)


def _opts(extra=None):
    o = dict(OPTS)
    o.update({k: v for k, v in (app_config.get("review") or {}).items() if k in OPTS})
    for k, v in (extra or {}).items():
        if k in OPTS and v is not None:
            try:
                o[k] = type(OPTS[k])(v)
            except (TypeError, ValueError):
                pass
    return o


# ---------------------------------------------------------------------------------------------------------------- events
def to_rows(cam):
    """camera_points tuples -> dicts."""
    return [dict(t=c[0], la=c[1], lo=c[2], v=c[3], ap=c[4], ac=c[5], st=c[6], blk=c[7], br=c[8], stamp=c[9], i=c[10]) for c in cam]


def interventions(rows, o):
    out = []
    for k in range(1, len(rows)):
        p, c = rows[k - 1], rows[k]
        if c["t"] - p["t"] > MAXGAP or p["ap"] != 1 or c["ap"] in (1, None):
            continue
        back = next((r["t"] for r in rows[k + 1:k + 241] if r["ap"] == 1), None)
        j = k
        while j > 0 and rows[j - 1]["ap"] == 1 and rows[j]["t"] - rows[j - 1]["t"] <= MAXGAP:
            j -= 1
        win = rows[max(0, k - 4):k + 2]
        flags = []
        if any(r["br"] for r in win):
            flags.append("brake")
        if any((r["ac"] or 0) >= o["accel_min_pct"] for r in win):
            flags.append("accelerator")
        sv = [r["st"] for r in win if r["st"] is not None]
        if sv and max(sv) - min(sv) >= o["steer_deg"]:
            flags.append("steering")
        out.append(dict(kind="intervention", t=c["t"], t1=back if back is not None else c["t"], v=c["v"], la=c["la"], lo=c["lo"],
                        from_mode=_mode(p["ap"]), to_mode=_mode(c["ap"]), flags=flags,
                        off_s=round(back - c["t"]) if back is not None else None, fsd_before_s=round(c["t"] - rows[j]["t"]),
                        at_stop=(c["v"] or 0) < 3))
    return out


def accel_presses(rows, o):
    out, cur = [], None

    def close():
        if not cur:
            return
        vs = cur["vs"]
        after = [r for r in rows if cur["t1"] < r["t"] <= cur["t1"] + 5]
        out.append(dict(kind="accelerator", t=cur["t"], t1=cur["t1"], dur_s=round(cur["t1"] - cur["t"]) + 1, peak=max(cur["ac"]), mean=round(statistics.mean(cur["ac"])),
                        v=cur["v0"], v1=cur["v1"], la=cur["la"], lo=cur["lo"], ended_in_off=any(r["ap"] not in (1, None) for r in after)))
    for r in rows:
        on = r["ap"] == 1 and (r["ac"] or 0) >= o["accel_min_pct"]
        if not on:
            continue
        if cur and r["t"] - cur["t1"] <= o["merge_s"] + 1:
            cur["t1"] = r["t"]
            cur["ac"].append(r["ac"])
            cur["vs"].append(r["v"])
            cur["v1"] = r["v"]
        else:
            close()
            cur = dict(t=r["t"], t1=r["t"], ac=[r["ac"]], vs=[r["v"]], v0=r["v"], v1=r["v"], la=r["la"], lo=r["lo"])
    close()
    return out


def speed_changes(rows, o):
    """Every lasting change of speed while FSD was on: the median speed 8..3 s before a second against the median 3..9 s after it, at least
    speed_min_kmh apart and both at 20 km/h or more; the biggest change wins within 20 s."""
    out, cand = [], []
    tt = [r["t"] for r in rows]
    n = len(rows)

    def win(t, a, b):
        return [r for r in rows[max(0, bisect.bisect_left(tt, t + a)):bisect.bisect_right(tt, t + b)]]

    def med(w):
        v = sorted(r["v"] for r in w if r["v"] is not None)
        return v[len(v) >> 1] if len(v) >= 3 else None
    for k in range(n):
        t = tt[k]
        pre, post = win(t, -8, -3), win(t, 3, 9)
        a, b = med(pre), med(post)
        if a is None or b is None or a < 20 or b < 20 or abs(b - a) < o["speed_min_kmh"]:
            continue
        mid = win(t, -8, 9)
        if sum(1 for r in mid if r["ap"] == 1) < 0.9 * len(mid) or rows[k]["ap"] != 1:
            continue
        cand.append((abs(b - a), k, a, b))
    cand.sort(reverse=True)
    kept = []
    for d, k, a, b in cand:
        if all(abs(tt[k] - tt[x[1]]) >= 20 for x in kept):
            kept.append((d, k, a, b))
    for d, k, a, b in sorted(kept, key=lambda x: x[1]):
        t = tt[k]
        mid = win(t, -2, 3)
        out.append(dict(kind="speed", t=t - 2, t1=t + 3, v=round(a), v1=round(b), la=rows[k]["la"], lo=rows[k]["lo"], dur_s=5,
                        pressed=any((r["ac"] or 0) >= o["accel_min_pct"] for r in mid)))
    return out


def find_events(rows, o):
    ev = interventions(rows, o) + accel_presses(rows, o) + speed_changes(rows, o)
    ev.sort(key=lambda e: e["t"])
    n = {"intervention": 0, "accelerator": 0, "speed": 0}
    for e in ev:
        n[e["kind"]] += 1
        e["id"] = {"intervention": "I", "accelerator": "A", "speed": "S"}[e["kind"]] + str(n[e["kind"]])
    return ev


# ----------------------------------------------------------------------------------------------------------------- stills
def _extract(path, lo, count, step, tmp, tag):
    """Stills of a front clip: `count` frames from second `lo`, one every `step` seconds -> [file]."""
    pat = os.path.join(tmp, "%s_%%03d.jpg" % tag)
    subprocess.run(["ffmpeg", "-v", "error", "-ss", "%g" % lo, "-i", path, "-t", "%g" % (count * step), "-vf", "fps=%g" % (1.0 / step), "-q:v", "2", pat],
                   stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
    return sorted(os.path.join(tmp, f) for f in os.listdir(tmp) if f.startswith(tag + "_") and f.endswith(".jpg"))


def _end(ev):
    """Where an event ends for the stills: a switch-off is one moment (how long FSD stayed off is a fact about it, not part of it)."""
    return ev["t"] if ev["kind"] == "intervention" else ev["t1"]


def _side(x, w):
    return "L" if x < 0.4 * w else "R" if x > 0.6 * w else "C"


def _read_signs(im, o_rv):
    """Every sign in one full-size frame -> [dict(cat, text, side, conf, crop)]. cat: speed (red ring, white face, read by sign_finder), then by colour
    (sign_classes): regulatory, warning, roadwork, info, directional, safety."""
    import sign_finder as SF
    import sign_classes as SC
    out = []
    W = im.shape[1]
    for side, roi in (("L", SF.ROI_LEFT), ("R", SF.ROI_RIGHT)):
        for c in SF.find_signs(im, roi=roi):
            if c.get("ok") and c["conf"] >= o_rv["min_conf"] and c["text"]:
                out.append(dict(cat="speed", text=c["text"], side=side, conf=round(c["conf"]), crop=c["crop"]))
                break
    try:
        found = SC.find_all(im)
    except Exception:                                        # an odd frame must not stop the review
        found = []
    for d in found:
        if d["cat"] == "regulatory" and d["col"] == "red" and any(x["cat"] == "speed" for x in out):
            continue                                         # the red ring of a speed sign
        txt = re.sub(r"[^A-Za-z0-9 /.\-]", "", d.get("text") or "").strip() if d.get("conf", 0) >= 50 else ""
        out.append(dict(cat=d["cat"], text=txt, side=_side(d["x"] + d["w"] / 2.0, W), conf=int(d.get("conf", 0)), crop=d["crop"]))
    return out


def _where(by_t, T):
    """(lat, lon, heading) of the car at second T: the position then and the direction it moved over the seconds around it (None when it did not move)."""
    import osm_roads
    r = by_t.get(T)
    if not r or r.get("la") is None or r.get("lo") is None:
        return None
    a = next((by_t[x] for x in (T - 3, T - 2, T - 1) if x in by_t and by_t[x].get("la") is not None), r)
    b = next((by_t[x] for x in (T + 3, T + 2, T + 1) if x in by_t and by_t[x].get("la") is not None), r)
    if a is b:
        return None
    dy, dx = (b["la"] - a["la"]) * 110540.0, (b["lo"] - a["lo"]) * 111320.0 * math.cos(math.radians(r["la"]))
    if math.hypot(dx, dy) < 5:
        return None
    return r["la"], r["lo"], osm_roads._bearing(a["la"], a["lo"], b["la"], b["lo"])


_ROADS = {}


def _roads(base, o):
    """The OpenStreetMap roads (osm_roads.py) from the tile cache the speed analysis keeps next to the drive data."""
    import osm_roads
    cache = os.path.join(os.path.dirname(base), "drives", "analysis", "osm_cache")
    key = (cache, bool(o.get("osm_fetch")))
    if key not in _ROADS:
        _ROADS[key] = osm_roads.Roads(cache, fetch=bool(o.get("osm_fetch")))
    return _ROADS[key]


class _SignCache:
    """What was read in each full-size frame, kept so that a later review does not read the same frame again: <base>/signcache/<clip stamp>.json
    holds {second in the clip: [sign]} (kind, text, side, sureness, name of its small picture in signcache/<clip stamp>/). A reading made with
    other finders or another min_conf (key) is not used."""

    def __init__(self, base, key):
        self.dir = os.path.join(base, "signcache")
        self.key = key
        self.c = {}
        self.dirty = set()

    def _load(self, clip):
        if clip not in self.c:
            try:
                with open(os.path.join(self.dir, clip + ".json"), encoding="utf-8") as fh:
                    d = json.load(fh)
            except (OSError, ValueError):
                d = {}
            self.c[clip] = d if d.get("key") == self.key else {"key": self.key, "s": {}}
        return self.c[clip]

    def has(self, clip, sec):
        return str(sec) in self._load(clip)["s"]

    def get(self, clip, sec):
        out = []
        for d in self._load(clip)["s"].get(str(sec), []):
            out.append(dict(cat=d["cat"], text=d["text"], side=d["side"], conf=d["conf"], crop=os.path.join(self.dir, clip, d["f"]) if d.get("f") else None))
        return out

    def put(self, clip, sec, dets):
        import cv2
        rows = []
        for n, d in enumerate(dets):
            name = ""
            crop = d.get("crop")
            if crop is not None and crop.shape[0] > 0 and crop.shape[1] > 0:
                if max(crop.shape[:2]) > 160:
                    z = 160.0 / max(crop.shape[:2])
                    crop = cv2.resize(crop, (max(1, int(crop.shape[1] * z)), max(1, int(crop.shape[0] * z))))
                os.makedirs(os.path.join(self.dir, clip), exist_ok=True)
                name = "%03d_%d.jpg" % (sec, n)
                cv2.imwrite(os.path.join(self.dir, clip, name), crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
            rows.append(dict(cat=d["cat"], text=d["text"], side=d["side"], conf=d["conf"], f=name))
        self._load(clip)["s"][str(sec)] = rows
        self.dirty.add(clip)

    def save(self):
        for clip in sorted(self.dirty):
            os.makedirs(self.dir, exist_ok=True)
            tmp = os.path.join(self.dir, clip + ".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.c[clip], fh, separators=(",", ":"))
            os.replace(tmp, os.path.join(self.dir, clip + ".json"))
        self.dirty.clear()


def look(ev, rows, front, base, stamp, o, stop, stills=None, signs=True):
    """The front-camera stills of one event. Sets ev["pics"] ([clip stamp, second in the clip] of each still, in time order), ev["shots"] (seconds from the
    event), ev["tile"] (shape of a still), ev["n_stills"] and, when the video is there and `signs`, ev["signs"] read from the full-size frames.
    The displayed stills come from the kept one-second stills (teslacam_stills): made from the video here if not yet, and still there after
    the video is deleted."""
    import cv2
    import numpy as np
    from concurrent.futures import ThreadPoolExecutor
    o_rv = dict(app_review.OPTS)
    t_a, t_b = ev["t"] - o["event_before_s"], _end(ev) + o["event_after_s"]
    span = t_b - t_a
    step = max(1, int(math.ceil(span / float(o["max_event_stills"]))))
    by_t = {int(r["t"]): r for r in rows}
    want = []                                               # (T, clip stamp, second in the clip)
    for T in range(int(math.ceil(t_a)), int(t_b) + 1, step):
        r = by_t.get(T) or by_t.get(T - 1) or by_t.get(T + 1)
        if r and (r["stamp"] in front or (stills and stills.has(r["stamp"]))):
            want.append((T, r["stamp"], r["i"]))
    if stills:
        for st in sorted({w[1] for w in want}):             # the stills of every clip involved, from the video while it is there
            if st in front and not stills.has(st):
                stills.make(st, front[st])
    pics = [w for w in want if stills and stills.has(w[1])]
    ev["pics"] = [[st, i] for _T, st, i in pics]
    ev["shots"] = [round(T - ev["t"], 1) for T, _st, _i in pics]
    ev["n_stills"] = len(pics)
    ev["sheet"] = ""
    ev["tile"] = None
    if pics:
        im = cv2.imdecode(np.frombuffer(stills.get(pics[0][1], pics[0][2]) or b"", np.uint8), cv2.IMREAD_COLOR)
        if im is not None:
            ev["tile"] = dict(w=int(im.shape[1]), h=int(im.shape[0]))
    ev["signs"] = []
    ev["read"] = 0
    folder = os.path.join(base, "events", stamp)
    os.makedirs(folder, exist_ok=True)
    where = {w[0]: k for k, w in enumerate(pics)}
    hi_name = lambda clip, sec: "%s_%03d_%d.jpg" % (clip, sec, int(o["hi_px"]))
    ev["hi"] = [""] * len(pics)
    cache = _SignCache(base, "%s:%s" % (SIGN_VER, o_rv["min_conf"]))
    # the video is opened only for the frames that still need it: no big picture kept yet, or (when signs are read) no reading kept yet
    need = []
    for w in want:
        if w[1] not in front:
            continue
        k = where.get(w[0])
        hi_ok = not o["hi_px"] or k is None or os.path.exists(os.path.join(folder, hi_name(w[1], w[2])))
        if not (hi_ok and (not signs or cache.has(w[1], w[2]))):
            need.append(w)
    shots = []                                              # (T, clip, second, image)
    with tempfile.TemporaryDirectory() as tmp:
        k = 0
        while k < len(need):                                # a run in one clip, in steps of `step` seconds
            j = k
            while j + 1 < len(need) and need[j + 1][1] == need[k][1] and need[j + 1][2] - need[j][2] == step:
                j += 1
            files = _extract(front[need[k][1]], need[k][2], j - k + 1, step, tmp, "r%d" % k)
            for n, f in enumerate(files[:j - k + 1]):
                im = cv2.imread(f)
                if im is not None:
                    shots.append((need[k + n][0], need[k + n][1], need[k + n][2], im))
            k = j + 1
        if stop():
            return
        # the big pictures of this event: the full-size frames just read, kept at hi_px wide (the one-second stills of the store are small, for browsing)
        if o["hi_px"]:
            for T, clip, sec, im in shots:
                if where.get(T) is None:
                    continue
                if im.shape[1] > o["hi_px"]:
                    im = cv2.resize(im, (int(o["hi_px"]), max(1, int(im.shape[0] * o["hi_px"] / float(im.shape[1])))), interpolation=cv2.INTER_AREA)
                cv2.imwrite(os.path.join(folder, hi_name(clip, sec)), im, [cv2.IMWRITE_JPEG_QUALITY, int(o["hi_quality"])])
        for k, (T, clip, sec) in enumerate(pics):
            if os.path.exists(os.path.join(folder, hi_name(clip, sec))):
                ev["hi"][k] = hi_name(clip, sec)
        if signs:
            todo = [x for x in shots if not cache.has(x[1], x[2])]
            with ThreadPoolExecutor(max(1, int(o_rv["workers"]))) as ex:
                for x, lst in zip(todo, ex.map(lambda x: _read_signs(x[3], o_rv), todo)):
                    cache.put(x[1], x[2], lst)
        cache.save()
        ev["read"] = sum(1 for _T, clip, sec in pics if cache.has(clip, sec))
        found = []                                          # every reading of these frames: just made, or kept from an earlier review
        for T, clip, sec in want:
            if cache.has(clip, sec):
                for d in cache.get(clip, sec):
                    found.append(dict(d, t=T))
        sgs = []
        for d in found:                                     # the same sign in following stills is one sign (a speed sign by its number, the others by kind and side)
            g = next((x for x in reversed(sgs) if x["cat"] == d["cat"] and x["side"] == d["side"] and (d["cat"] != "speed" or x["text"] == d["text"])
                      and d["t"] - x["t1"] <= o_rv["merge_s"]), None)
            if g is None:
                sgs.append(dict(cat=d["cat"], text=d["text"], side=d["side"], t=d["t"], t0=d["t"], t1=d["t"], n=0, conf=-1, crop=None, f=""))
                g = sgs[-1]
            g["n"] += 1
            g["t1"] = d["t"]
            if d["conf"] > g["conf"] or (d["cat"] != "speed" and not g["text"] and d["text"]):
                g.update(conf=d["conf"], crop=d["crop"], t=d["t"])
                if d["cat"] != "speed" or not g["text"]:
                    g["text"] = d["text"]
        sgs = [g for g in sgs if g["cat"] == "speed" or g["n"] >= o["sign_min_stills"]]       # a colour blob in one still only is mostly noise
        os.makedirs(os.path.join(base, "crops"), exist_ok=True)
        for n, g in enumerate(sgs):
            crop = g.pop("crop")
            if isinstance(crop, str):
                crop = cv2.imread(crop)
            name = "%s_%s_%02d.jpg" % (stamp, ev["id"], n)
            if crop is not None and crop.shape[0] > 0 and crop.shape[1] > 0:
                if max(crop.shape[:2]) > 160:
                    z = 160.0 / max(crop.shape[:2])
                    crop = cv2.resize(crop, (max(1, int(crop.shape[1] * z)), max(1, int(crop.shape[0] * z))))
                cv2.imwrite(os.path.join(base, "crops", name), crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                g["f"] = name
        roads = _roads(base, o) if sgs else None
        for g in sgs:
            near = [r["v"] for r in rows if g["t0"] - 1 <= r["t"] <= g["t1"] + 1 and r["v"] is not None]
            g["car"] = round(statistics.median(near)) if near else None
            m = re.match(r"\d+", str(g["text"]))
            lim = int(m.group(0)) if (g["cat"] == "speed" and m) else None
            # at highway speed a much lower limit is usually an exit or a side road, not this road
            g["anom"] = bool(lim is not None and g["car"] is not None and g["car"] >= o["exit_car_kmh"] and lim <= g["car"] - o["exit_gap_kmh"])
            # the map: what OpenStreetMap says the limit is where the sign was seen
            g["osm"] = None
            g["map"] = ""
            pos = _where(by_t, int(g["t"])) if roads is not None else None
            if pos:
                hit = roads.at(*pos)
                if hit:
                    g["osm"] = hit["limit"]
                    g["map"] = (hit["name"] or hit["ref"] or hit["hw"]) + (" (variable)" if hit["var"] else "")
            g["conflict"] = bool(lim is not None and g["osm"] is not None and lim != g["osm"])
        # a speed sign that differs from the map with road-work signs about is most likely a temporary limit
        works = [g for g in sgs if g["cat"] == "roadwork"]
        for g in sgs:
            g["temp"] = bool(g["cat"] == "roadwork" or (g["conflict"] and any(abs(w["t"] - g["t"]) <= o["works_gap_s"] for w in works)))
        ev["signs"] = sgs


# -------------------------------------------------------------------------------------------------------------------- job
class DriveReview:
    def __init__(self, data_dir, tc):
        self.dir = data_dir
        self.base = os.path.join(data_dir, "drive_review")
        self.tc = tc
        self.t = None
        self.stop_flag = False
        self._rv = {}
        self.st = dict(running=False, stamp="", stage="", done=0, total=0, error="", t0=0)

    # ---- stored results
    def _path(self, stamp):
        return os.path.join(self.base, "drives", stamp + ".json")

    def result(self, stamp):
        try:
            with open(self._path(stamp), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def reviewed(self):
        """{drive stamp: percent} of the stills of the events of each reviewed drive that were looked at for signs (100 for a drive with no events)."""
        out = {}
        try:
            names = os.listdir(os.path.join(self.base, "drives"))
        except OSError:
            return out
        for n in names:
            if not n.endswith(".json"):
                continue
            path = os.path.join(self.base, "drives", n)
            try:
                mt = os.path.getmtime(path)
            except OSError:
                continue
            hit = self._rv.get(path)
            if not hit or hit[0] != mt:
                res = self.result(n[:-5])
                pct = None
                if res and res.get("clock") is not None:
                    evs = res.get("events") or []
                    tot = sum(e.get("n_stills") or 0 for e in evs)
                    pct = 100 if not evs else (round(100.0 * sum(e["read"] if "read" in e else (e.get("n_stills") or 0 if not res.get("note") else 0) for e in evs) / tot) if tot else 0)
                hit = self._rv[path] = (mt, pct)
            if hit[1] is not None:
                out[n[:-5]] = hit[1]
        return out

    def _save(self, stamp, res):
        os.makedirs(os.path.dirname(self._path(stamp)), exist_ok=True)
        with open(self._path(stamp) + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(res, fh, separators=(",", ":"))
        os.replace(self._path(stamp) + ".tmp", self._path(stamp))

    def status(self):
        s = dict(self.st)
        s["running"] = bool(self.t and self.t.is_alive())
        return s

    def defaults(self):
        o = _opts()
        return dict(speed_from=100, speed_to=110, speed_tol_kmh=o["speed_tol_kmh"])     # the page's speed filter starts here

    def start(self, stamp, teslacam, params):
        if self.t and self.t.is_alive():
            return self.status()
        self.stop_flag = False
        self.st = dict(running=True, stamp=stamp, stage="Starting", done=0, total=0, error="", t0=time.time())
        self.t = threading.Thread(target=self._run, args=(stamp, teslacam, params or {}), daemon=True)
        self.t.start()
        return self.status()

    def stop(self):
        self.stop_flag = True
        return self.status()

    def _stage(self, s, done=0, total=0):
        self.st.update(stage=s, done=done, total=total)

    # ---- the job
    def _run(self, stamp, teslacam, params):
        try:
            self._review(stamp, teslacam, params)
        except Exception as e:                                  # a missing tool, a drive pulled out, a bad file: say so
            self.st["error"] = "%s: %s" % (type(e).__name__, e)

    def _review(self, stamp, teslacam, params):
        o = _opts(params)
        folder = DP.folder_for(os.path.join(self.dir, "drives", "deepdive"), stamp)
        self._stage("Reading the drive")
        with open(os.path.join(folder, "drive_summary.json"), encoding="utf-8") as fh:
            sm = json.load(fh)
        t0, t1 = DF._t(sm["start"], "%Y-%m-%d %H:%M:%S"), DF._t(sm["end"], "%Y-%m-%d %H:%M:%S")
        tp = DF.tessie_points(folder)
        if len(tp) < 2:
            raise ValueError("this drive has no sample file")
        store, _ = self.tc._tl("A")
        front, mins = {}, []
        if teslacam:
            recent, saved, _s = self.tc._sources("A", teslacam)
            mins = TL.minutes(recent, saved)
            front = {m["stamp"]: self.tc._front(teslacam, m) for m in mins if "front" in m["cams"]}
        ts, las, los = [p["t"] for p in tp], [p["la"] for p in tp], [p["lo"] for p in tp]
        self._stage("Matching the dashcam telemetry")
        clock, cam = DF.clock_offset(ts, las, los, store, t0, t1, None, self.dir)
        if clock is None and front:                             # nothing read for this drive yet: read the minutes that cover it (front camera only)
            u0, u1 = DF.utc(t0), DF.utc(t1)
            todo = [(m["stamp"], front[m["stamp"]]) for m in mins if m["stamp"] in front and store.summary(m["stamp"]) is None and CC.maybe_in(m["stamp"], u0 - 60, u1 + 60)]
            if todo:                                            # under any offset the car's clock may have had: reading them is what teaches the clock
                self._stage("Reading the telemetry of %d minute(s) of footage" % len(todo), 0, len(todo))
                TL.read_many(store, todo, stop=lambda: self.stop_flag)
            if self.stop_flag:
                return
            CC.refresh(self.dir, store, wait=True)
            clock, cam = DF.clock_offset(ts, las, los, store, t0, t1, None, self.dir)
        if clock is None:
            res = dict(stamp=stamp, made=_now(), params=dict(o), clock=None, events=[], stills=False,
                       note="No dashcam telemetry lies on this drive's route (the footage is not on a connected drive, or was never read).")
            self._save(stamp, res)
            return
        rows = to_rows(cam)
        events = find_events(rows, o)
        req = app_review.requirements()
        stills = ST.Stills(self.dir, app_config.get("stills.width", 400), app_config.get("stills.quality", 6))
        can_sign = bool(front) and not (req["py"] or req["cmd"])
        can_look = bool(front) and ST.have_ffmpeg() or bool(stills.stamps())      # stills come from the video or from the ones kept
        note = ""
        if not front and not stills.stamps():
            note = "The Archive is not connected and no stills are kept, so the events are listed without stills."
        elif not front:
            note = "The Archive is not connected: the stills are the kept ones, and no signs were read."
        elif not can_sign:
            note = "Sign reading needs: %s" % "; ".join(req["hint"])
        done_ev = 0
        if can_look:
            for ev in events:
                if self.stop_flag:
                    return
                self._stage("Looking at the front camera: %s" % ev["id"], done_ev, len(events))
                try:
                    look(ev, rows, front, self.base, stamp, o, lambda: self.stop_flag, stills, can_sign)
                except Exception as e:
                    ev["look_error"] = "%s: %s" % (type(e).__name__, e)
                done_ev += 1
        for ev in events:
            ev["utc"] = DF.utc(ev["t"])
            ev["clip"] = _clip_of(rows, ev["t"])
        secs_n = len(rows)
        res = dict(stamp=stamp, made=_now(), params=dict(o), clock=clock, t0=t0, t1=t1, poff=CC.local_offset(DF.utc(t0)), events=events, stills=can_look, note=note,
                   coverage=dict(seconds=secs_n, pct=round(100.0 * min(1.0, secs_n / max(1.0, t1 - t0)))),
                   counts={k: sum(1 for e in events if e["kind"] == k) for k in ("intervention", "accelerator", "speed")})
        self._save(stamp, res)


def _clip_of(rows, t):
    r = min(rows, key=lambda x: abs(x["t"] - t)) if rows else None
    return dict(stamp=r["stamp"], s=r["i"]) if r else None


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
