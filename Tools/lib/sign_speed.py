"""sign_speed.py - how fast the car must go for a minute (or a second) to be read for speed-limit signs.

On the road signs are read from review.min_kmh (30). In the last review.end_s seconds (180) of a drive they are read from review.end_kmh (10):
the car slows to turn in, and the sign just before home or the car park is the one that matters there. A minute with no drive worked out
for it (run_cuts not built yet) uses min_kmh.

    floor(stamp)          -> the slowest speed read in this minute (the lower one when any of the minute is near the drive's end)
    floor_at(stamp, s)    -> the same for second s of the minute
    wanted(stamp, sm)     -> True when the minute's summary (vmax) says it is to be read
"""
import app_config
import clip_clock as CC
import run_cuts as RC


def _opts():
    o = app_config.get("review") or {}
    return float(o.get("min_kmh", 30.0)), float(o.get("end_kmh", 10.0)), float(o.get("end_s", 180.0))


def _drive_end(stamp):
    """UTC end of the drive the minute lies in, or None (not in a drive, or not known)."""
    try:
        s = RC.section(stamp)
        if not s or s[0] != "drive":
            return None
        last = RC.section_last(stamp)
        return None if last is None else last + 60
    except Exception:
        return None


def floor(stamp):
    lo, end_kmh, end_s = _opts()
    e = _drive_end(stamp)
    if e is not None and CC.utc(stamp) + 60 > e - end_s:
        return min(lo, end_kmh)
    return lo


def floor_at(stamp, s, _e=()):
    lo, end_kmh, end_s = _opts()
    e = _drive_end(stamp) if _e == () else _e
    if e is not None and CC.utc(stamp) + s >= e - end_s:
        return min(lo, end_kmh)
    return lo


def wanted(stamp, sm):
    return isinstance(sm, dict) and sm.get("vmax", 0) >= floor(stamp)
