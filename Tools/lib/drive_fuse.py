"""drive_fuse.py - one finer track for a Tessie drive, filled in from the dashcam telemetry where it exists.

Tessie logs a point about every 30 s: at 100 km/h that is a chord of ~800 m across every bend. The car writes its own speed and GPS into
every video frame, and once a clip has been read (teslacam_timeline.py) one reading per second is kept in the shared store
<data>/teslacam_timeline/<day>.json, whether or not the footage is still on a disk. A drive is matched to those readings by TIME
(never by name: a Tessie drive and a run of clips rarely start in the same second), so:

  * camera seconds inside the drive (plus MARGIN_S either side) are used as they are: ~30x finer, and on the road
  * a Tessie point is kept only where the camera has nothing within JOIN_S seconds (the start and end, which the clips usually
    miss by about a minute, and any hole in the footage)
  * the camera has no elevation, so each camera second takes the elevation interpolated from Tessie's points

Times are compared in UTC: Tessie's local clock and the dashcam's file names are turned into UTC with the configured time zone (app.timezone,
daylight saving included). The car's clock sometimes stayed on standard time for days after the clocks changed (drive_summary.json foot_off 3600),
so "local" or "standard" is chosen per drive, checked against the route by GPS; without that a drive is joined to another hour's driving and the
track jumps between the two.

Nothing is written and no Tessie record is changed (Tessie stays the source of truth); the result is worked out when asked.
fuse_drive() returns {info, points}: info says how much the camera covered, points are parallel lists
t (epoch s, local clock), la, lo, v (km/h), s (0 = Tessie, 1 = camera), el (m).
"""
import bisect
import calendar
import csv
import datetime
import glob
import os
import math

import app_config as CFG

JOIN_S = 20.0       # a Tessie point this close to a camera second is left out
MARGIN_S = 30.0     # camera seconds this far outside the Tessie drive still belong to it
CLOCKS = ("local", "standard")   # the car's clock was right / stayed on standard time (see utc())
MAX_ERR_KM = 3.0    # the camera must be this close to Tessie's route, on average, for an offset to be believed
MAX_POINTS = 6000   # a page is sent at most this many points (a 360 km drive is ~13 000 camera seconds)


def _t(s, fmt):
    """A local clock time as seconds: the clock read as if it were UTC, which is how the drive pages and the camera store keep time
    (it never depends on this computer's time zone)."""
    return calendar.timegm(datetime.datetime.strptime(s, fmt).timetuple())


def secs(stamp):
    return _t(stamp, "%Y-%m-%d_%H-%M-%S")


def _f(r, i):
    try:
        return float(r[i])
    except (TypeError, ValueError, IndexError):
        return None


def tessie_points(folder):
    """The points of a Tessie drive (its drive_deepdive_*.csv): [{t, la, lo, v, el}] by time."""
    fs = sorted(glob.glob(os.path.join(folder, "drive_deepdive_*.csv")))
    out = []
    if not fs:
        return out
    with open(fs[0], newline="", encoding="utf-8-sig") as fh:
        rd = csv.reader(fh)
        ix = {n: i for i, n in enumerate(next(rd, []))}
        iL, iO, iE, iS = ix.get("Latitude"), ix.get("Longitude"), ix.get("Elevation (m)"), ix.get("Speed (km/h)")
        if iL is None or iO is None:
            return out
        for r in rd:
            try:
                t = _t(r[0], "%Y-%m-%d %H:%M:%S")
            except (ValueError, IndexError):
                continue
            la, lo = _f(r, iL), _f(r, iO)
            if la is None or lo is None or (abs(la) < 1 and abs(lo) < 1):
                continue
            out.append(dict(t=t, la=la, lo=lo, v=_f(r, iS), el=_f(r, iE)))
    out.sort(key=lambda p: p["t"])
    return out


def _tz():
    import zoneinfo
    try:
        return zoneinfo.ZoneInfo(CFG.get("timezone") or "Australia/Sydney")
    except Exception:
        return datetime.timezone.utc


def utc(local, clock="local"):
    """UTC epoch of a local clock reading (a naive time read as UTC, the convention of every drive page - local seconds).
    clock="local": the clock is right, daylight saving included. clock="standard": the clock stayed on standard time (the car's did for days
    after the clocks changed), so it is behind by the daylight-saving hour while that is in force."""
    tz = _tz()
    dt = datetime.datetime.fromtimestamp(local, datetime.timezone.utc).replace(tzinfo=None).replace(tzinfo=tz)
    off = dt.utcoffset() - (dt.dst() if clock == "standard" else datetime.timedelta(0))
    return local - off.total_seconds()


def local(u):
    """The page's clock (local seconds, a naive time read as UTC) of a UTC epoch."""
    dt = datetime.datetime.fromtimestamp(u, _tz())
    return calendar.timegm(dt.replace(tzinfo=None).timetuple())


def camera_points(store, t0, t1, clock="local"):
    """The per-second readings in [t0, t1] (page clock) from the shared store: [(t, la, lo, speed, autopilot mode, accelerator %, steering deg,
    indicator, brake, clip stamp, second in the clip)] by time. A clip is named by the car's clock; that is turned into UTC (clock: see utc()) and from there to the page's clock,
    so a drive and its footage are always compared as the same instant."""
    out = []
    for st in sorted(store.stamps()):
        try:
            m = local(utc(secs(st), clock))
        except ValueError:
            continue
        if m + 60 < t0 or m > t1:
            continue
        ser = store.series(st)
        if not ser:
            continue
        for i, (la, lo) in enumerate(zip(ser["lat"], ser["lon"])):
            if la is None or lo is None or (abs(la) < 1 and abs(lo) < 1):
                continue
            if t0 <= m + i <= t1:
                out.append((m + i, la, lo, ser["v"][i], ser["ap"][i], ser["accel"][i], ser["steer"][i], ser["blk"][i], ser["br"][i], st, i))
    return out


def _interp(ts, vs, t):
    """vs at time t from the points with a value (clamped at the ends); None when there is none."""
    k = bisect.bisect_left(ts, t)
    if not ts:
        return None
    if k <= 0:
        return vs[0]
    if k >= len(ts):
        return vs[-1]
    a, b = ts[k - 1], ts[k]
    return vs[k - 1] + (vs[k] - vs[k - 1]) * ((t - a) / (b - a) if b > a else 0)


def fuse(tessie, cam, t0, t1):
    """Tessie points + camera seconds -> {info, points}; see the module text."""
    ct = [c[0] for c in cam]
    rows = [(c[0], c[1], c[2], c[3], 1) for c in cam]
    for p in tessie:
        k = bisect.bisect_left(ct, p["t"])
        near = min([abs(ct[j] - p["t"]) for j in (k - 1, k) if 0 <= j < len(ct)] or [1e9])
        if near > JOIN_S:
            rows.append((p["t"], p["la"], p["lo"], p["v"], 0))
    rows.sort(key=lambda r: r[0])
    el = [(p["t"], p["el"]) for p in tessie if p["el"] is not None]
    et, ev = [e[0] for e in el], [e[1] for e in el]
    n = len(rows)
    step = max(1, -(-n // MAX_POINTS))
    keep = rows[::step] + ([rows[-1]] if rows and (n - 1) % step else [])
    pts = dict(t=[int(r[0]) for r in keep], la=[round(r[1], 5) for r in keep], lo=[round(r[2], 5) for r in keep],
               v=[round(r[3] or 0, 1) for r in keep], s=[r[4] for r in keep],
               el=[None if (e := _interp(et, ev, r[0])) is None else round(e, 1) for r in keep])
    dts = sorted(tessie[i + 1]["t"] - tessie[i]["t"] for i in range(len(tessie) - 1))
    dur = max(1.0, t1 - t0)
    info = dict(t0=t0, t1=t1, tessie_n=len(tessie), tessie_every_s=dts[len(dts) // 2] if dts else None, cam_n=len(cam),
                cam_pct=round(100.0 * min(1.0, len(cam) / dur)),
                cam_first=cam[0][0] if cam else None, cam_last=cam[-1][0] if cam else None,
                start_gap_s=max(0, round(cam[0][0] - t0)) if cam else None, end_gap_s=max(0, round(t1 - cam[-1][0])) if cam else None,
                points=len(keep), thinned=step > 1)
    return dict(info=info, points=pts)


def fuse_drive(data_dir, stamp, store):
    """The fused track of the Tessie drive in <data>/drives/deepdive/<stamp>/ ; None when that drive has no sample file."""
    import json
    folder = os.path.join(data_dir, "drives", "deepdive", stamp)
    try:
        with open(os.path.join(folder, "drive_summary.json"), encoding="utf-8") as fh:
            sm = json.load(fh)
        t0, t1 = _t(sm["start"], "%Y-%m-%d %H:%M:%S"), _t(sm["end"], "%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError, KeyError):
        return None
    tp = tessie_points(folder)
    if not tp:
        return None
    off, cam = clock_offset([p["t"] for p in tp], [p["la"] for p in tp], [p["lo"] for p in tp], store, t0, t1, "standard" if sm.get("foot_off") == 3600 else None)
    return fuse(tp, cam, t0, t1)


MAX_PAGE_ROWS = 9000    # a drive page is given at most this many rows


def _lerp(ts, rows, col, t):
    """Column `col` of the Tessie rows at time t (straight line between the two rows around it; None when there is no value)."""
    if not rows:
        return None
    k = bisect.bisect_left(ts, t)
    if k <= 0:
        return rows[0][col]
    if k >= len(ts):
        return rows[-1][col]
    va, vb = rows[k - 1][col], rows[k][col]
    if va is None or vb is None:
        return vb if va is None else va
    f = (t - ts[k - 1]) / (ts[k] - ts[k - 1]) if ts[k] > ts[k - 1] else 0.0
    return va + (vb - va) * f


def _km(la1, lo1, la2, lo2):
    x = math.radians(lo2 - lo1) * math.cos(math.radians((la1 + la2) / 2))
    return 6371.0 * math.hypot(x, math.radians(la2 - la1))


def clock_offset(ts, las, los, store, t0, t1, hint=None):
    """(clock, camera points) - the camera clock ("local" or "standard", see utc()) under which the dashcam's GPS lies on Tessie's route
    (mean distance <= MAX_ERR_KM), trying the one the drive was found with first; (None, []) when neither puts the footage on the route."""
    best = None
    for off in ([hint] if hint else []) + [o for o in CLOCKS if o != hint]:
        cam = camera_points(store, t0 - MARGIN_S, t1 + MARGIN_S, off)
        if len(cam) < 10:
            continue
        errs = []
        for c in cam[::max(1, len(cam) // 60)]:
            k = bisect.bisect_left(ts, c[0])
            if k <= 0 or k >= len(ts):
                continue
            f = (c[0] - ts[k - 1]) / (ts[k] - ts[k - 1]) if ts[k] > ts[k - 1] else 0.0
            errs.append(_km(c[1], c[2], las[k - 1] + (las[k] - las[k - 1]) * f, los[k - 1] + (los[k] - los[k - 1]) * f))
        if len(errs) >= 5:
            e = sum(errs) / len(errs)
            if e <= MAX_ERR_KM:
                return off, cam
            best = e if best is None else min(best, e)
    return None, []


def enrich(rows, store, hint=None):
    """The rows of a drive page ([t, lat, lon, speed, power, elevation, battery, range, inside, outside, odometer], Tessie's, about one every 30 s)
    with the dashcam's seconds put in: speed, position, and five new columns [autopilot mode, accelerator %, steering deg, indicator, brake].
    Power, elevation, battery, range, temperatures and odometer have no camera reading, so they follow Tessie's points in a straight line
    between them (the same line the Tessie chart already draws). Returns {rows, info} or None when the camera has nothing for this drive."""
    if not rows:
        return None
    rows = [list(r) + [None] * (11 - len(r)) for r in rows]
    ts = [r[0] for r in rows]
    ok = [r for r in rows if r[1] is not None and r[2] is not None]
    if len(ok) < 2:
        return None
    off, cam = clock_offset([r[0] for r in ok], [r[1] for r in ok], [r[2] for r in ok], store, rows[0][0], rows[-1][0], hint)
    if off is None:
        return None
    ct = [c[0] for c in cam]
    out = []
    for c in cam:
        out.append([c[0], c[1], c[2], round(c[3] or 0, 1)] + [None if (x := _lerp(ts, rows, i, c[0])) is None else round(x, 3) for i in range(4, 11)] + [c[4], c[5], c[6], c[7], c[8]])
    for r in rows:
        k = bisect.bisect_left(ct, r[0])
        near = min([abs(ct[j] - r[0]) for j in (k - 1, k) if 0 <= j < len(ct)] or [1e9])
        if near > JOIN_S:
            out.append(r + [None] * 5)
    out.sort(key=lambda r: r[0])
    step = max(1, -(-len(out) // MAX_PAGE_ROWS))
    if step > 1:
        out = out[::step] + ([out[-1]] if (len(out) - 1) % step else [])
    return dict(rows=out, info=dict(tessie=len(rows), camera=len(cam), rows=len(out), thinned=step > 1, clock=off))
