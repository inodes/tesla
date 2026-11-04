"""app_route.py - a drive's route from Tessie's deep-dive file, to fill what the dashcam's telemetry does not cover.

Tessie is the base of the Drives list, so a drive's line on the map must run from its start to its end even where the dashcam footage (and so its telemetry, a point a second)
is gone or was never kept. Tessie's own file has a point about every 25 s (time, position, speed): between them the car is placed in a straight line. Where the dashcam's
telemetry exists it is used as it is.

    points(csv_path)                      -> [(UTC epoch, lat, lon, km/h)] oldest first
    fill(series, t0, t1, pts)             -> a per-second series over the whole drive [t0, t1]: the dashcam's seconds, Tessie's (moved onto the dashcam's at the joins) in between
"""
import time

import drive_points as DPT

KEYS = ("v", "lat", "lon", "hd", "ap", "steer", "accel", "br", "blk", "gear")
MAX_GAP_S = 180            # Tessie points further apart than this are not joined (the car was not reporting)


def points(path, lo=None):
    """[(UTC epoch, lat, lon, km/h)] of a drive's telemetry file, by time. Read through lib/drive_points.py: Tessie's JSON carries the epoch itself; an older CSV's
    wall-clock text is converted once with the zone's rules on its date. The zone is only a conversion, the data is UTC."""
    out = []
    try:
        for t, r in DPT.points_epoch(path):
            try:
                la, lon = float(r["Latitude"]), float(r["Longitude"])
            except (KeyError, ValueError, TypeError):
                continue
            if abs(la) < 1 and abs(lon) < 1:
                continue
            try:
                v = float(r.get("Speed (km/h)") or 0)
            except ValueError:
                v = 0.0
            out.append((t, la, lon, v))
    except OSError:
        return []
    return sorted(out)


def describe(pts, t0, t1):
    """What Tessie's points say about the drive [t0, t1] (UTC): how many fall inside it, from when to when, how often, and how much of the drive they follow (a gap longer than MAX_GAP_S is not joined)."""
    inw = [p for p in pts if t0 - 30 <= p[0] <= t1 + 30]
    if not inw:
        return dict(points=0)
    gaps = sorted(b[0] - a[0] for a, b in zip(inw, inw[1:]))
    cov = sum(g for g in gaps if g <= MAX_GAP_S)
    return dict(points=len(inw), first=inw[0][0], last=inw[-1][0], every_s=gaps[len(gaps) // 2] if gaps else None, covered_s=cov, longest_gap_s=gaps[-1] if gaps else 0)


FADE_S = 30                # Tessie's route is moved onto the dashcam's at a join, and the move fades out over this many seconds
HOLE_S = 3                 # a hole this short in the dashcam's seconds (the second between two one-minute clips) is bridged from the dashcam's own points


def _tess(pts, times, t):
    """Tessie's place at UTC second t (a straight line between its two points either side), or None where it cannot say."""
    import bisect
    j = bisect.bisect_right(times, t) - 1
    if j < 0 or j + 1 >= len(pts) and t > pts[j][0]:
        return None
    a = pts[j]
    b = pts[j + 1] if j + 1 < len(pts) else None
    if b is None or t > b[0] or b[0] - a[0] > MAX_GAP_S:
        return None
    f = (t - a[0]) / float(b[0] - a[0]) if b[0] > a[0] else 0.0
    return a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f, a[3] + (b[3] - a[3]) * f


def rebase_minutes(minutes, old_t0, new_t0):
    """The footage minutes with their offsets counted from `new_t0` instead of `old_t0` (UTC seconds): when the series is stretched back to Tessie's start, before the first clip, every
    minute is that much later from the start. Without it the video blocks sit at the start of the bar and the black ("no video") at its end, the wrong way round."""
    shift = int(round(old_t0 - new_t0))
    return [dict(m, off=m["off"] + shift) for m in minutes or []] if shift else list(minutes or [])


def fill(series, t0, t1, pts):
    """The dashcam's seconds where it has them. A hole of a few seconds between two of them (the car's clips are a minute and a second apart) is drawn between the
    dashcam's own points. A longer hole, and the ends before and after the footage, are Tessie's route, moved onto the dashcam's at the join so the line is not broken
    there (Tessie's clock and GPS are not the dashcam's: left alone they leave a jump of tens of metres at every join)."""
    t0, t1 = int(t0), int(t1)
    s0 = int(series["t0"]) if series else t0
    n_cam = len(series["v"]) if series else 0
    base = min(s0, t0) if series else t0
    end = max(t1, s0 + n_cam)
    n = max(0, end - base)
    out = {k: [None] * n for k in KEYS}
    if series:
        for k in KEYS:
            src = series.get(k) or []
            for i, x in enumerate(src[:n_cam]):
                out[k][s0 - base + i] = x
    known = lambda i: 0 <= i < n and out["lat"][i] is not None       # noqa: E731
    times = [p[0] for p in pts]
    lo, hi = max(0, t0 - base), min(n, t1 - base)
    i = lo
    while i < hi:
        if known(i):
            i += 1
            continue
        j = i
        while j < hi and not known(j):
            j += 1
        left = i - 1 if known(i - 1) else None
        right = j if known(j) else None
        if left is not None and right is not None and j - i <= HOLE_S:
            for k in range(i, j):
                f = (k - left) / float(right - left)
                for key in ("lat", "lon", "v"):
                    a, b = out[key][left], out[key][right]
                    out[key][k] = round(a + (b - a) * f, 6 if key != "v" else 1) if a is not None and b is not None else a
                for key in KEYS:
                    if key not in ("lat", "lon", "v"):
                        out[key][k] = out[key][left]
        elif pts:
            def shift(idx):
                """Tessie's place minus the dashcam's at second idx (to move Tessie's route onto the dashcam's), or None."""
                if idx is None:
                    return None
                q = _tess(pts, times, base + idx)
                return (out["lat"][idx] - q[0], out["lon"][idx] - q[1]) if q else None
            dl, dr = shift(left), shift(right)
            for k in range(i, j):
                q = _tess(pts, times, base + k)
                if q is None:
                    continue
                dla = dlo = 0.0
                if dl and dr:
                    f = (k - left) / float(right - left)
                    dla, dlo = dl[0] * (1 - f) + dr[0] * f, dl[1] * (1 - f) + dr[1] * f
                elif dl:
                    w = max(0.0, 1 - (k - left) / float(FADE_S))
                    dla, dlo = dl[0] * w, dl[1] * w
                elif dr:
                    w = max(0.0, 1 - (right - k) / float(FADE_S))
                    dla, dlo = dr[0] * w, dr[1] * w
                out["lat"][k] = round(q[0] + dla, 6)
                out["lon"][k] = round(q[1] + dlo, 6)
                out["v"][k] = round(q[2], 1)
        i = j
    out["t0"] = base
    out["pending"] = (series or {}).get("pending", 0)
    return out
