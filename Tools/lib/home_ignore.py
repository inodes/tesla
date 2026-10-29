"""home_ignore.py - footage parked at Home is not worth keeping, so the app leaves it alone.

A Recent recording parked at Home, and a Sentry event at Home, are the car sitting in its own garage: nothing in them is worth keeping stills of, reading signs
in, or counting as "needs updating". The saved places named in app.teslacam.ignore_places (default "Home") are those places.

    set_places(fn)              fn() -> [dict(name, lat, lon, radius)]: the saved places
    at(lat, lon)  -> bool       True when the point is at one of the ignored places (the nearest saved place within its radius, as the pages name a place)
    set_stamps(stamps)          the minutes of the Recent sections and Sentry events found to be at such a place (ClipKeep keeps this up to date)
    has(stamp)    -> bool       True for one of those minutes: the planners that make stills leave it out
"""
import math

import app_config

_PLACES = lambda: []            # noqa: E731
_STAMPS = frozenset()
MIN_RADIUS = 60                 # metres: as the pages name a place (nameAt in app.js)


def set_places(fn):
    global _PLACES
    _PLACES = fn


def names():
    v = app_config.get("teslacam.ignore_places", ["Home"])
    return {str(x).strip().lower() for x in (v if isinstance(v, (list, tuple)) else [v]) if str(x).strip()}


def _hav(a, b, c, d):
    p1, p2 = math.radians(a), math.radians(c)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(h))


def place_at(lat, lon):
    """The name of the saved place the point is at ('' when none): the nearest one within its radius."""
    if lat is None or lon is None:
        return ""
    best, bd = "", 1e18
    try:
        places = _PLACES() or []
    except Exception:
        places = []
    for p in places:
        try:
            d = _hav(lat, lon, p["lat"], p["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if d <= max(p.get("radius") or 100, MIN_RADIUS) and d < bd:
            best, bd = p.get("name") or "", d
    return best


def at(lat, lon):
    n = place_at(lat, lon)
    return bool(n) and n.strip().lower() in names()


def set_stamps(stamps):
    global _STAMPS
    _STAMPS = frozenset(stamps)


def has(stamp):
    return stamp in _STAMPS
