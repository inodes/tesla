"""home_ignore.py - footage parked at Home is not worth keeping, so the app leaves it alone.

A Recent recording parked at Home, and a Sentry event at Home, are the car sitting in its own garage: nothing in them is worth keeping stills of, reading signs
in, or counting as "needs updating". The saved places named in app.teslacam.ignore_places (default "Home") are those places.

    set_places(fn)              fn() -> [dict(name, lat, lon, radius)]: the saved places
    at(lat, lon)  -> bool       True when the point is at one of the ignored places (the smallest saved place whose radius holds it, as the pages name a place)
    set_stamps(stamps)          the minutes of the Recent sections and Sentry events found to be at such a place (ClipKeep keeps this up to date)
    has(stamp)    -> bool       True for one of those minutes: the planners that make stills leave it out
"""
import json
import math
import os

import app_config

_PLACES = lambda: []            # noqa: E731
_STAMPS = frozenset()
_PATH = None
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
    """The name of the saved place the point is at ('' when none): of the places whose radius holds it, the smallest (the most specific), then the nearest."""
    if lat is None or lon is None:
        return ""
    best, br, bd = "", 1e18, 1e18
    try:
        places = _PLACES() or []
    except Exception:
        places = []
    for p in places:
        try:
            d = _hav(lat, lon, p["lat"], p["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        r = max(p.get("radius") or 100, MIN_RADIUS)
        if d <= r and (r < br or (r == br and d < bd)):
            best, br, bd = p.get("name") or "", r, d
    return best


def at(lat, lon):
    n = place_at(lat, lon)
    return bool(n) and n.strip().lower() in names()


def set_file(path):
    """Where the minutes found to be at an ignored place are kept between runs of the app, so the first look at what needs doing after a start already leaves them out; loads them."""
    global _PATH, _STAMPS
    _PATH = path
    try:
        with open(path, encoding="utf-8") as f:
            _STAMPS = frozenset(json.load(f))
    except (OSError, ValueError, TypeError):
        pass


def set_stamps(stamps):
    global _STAMPS
    new = frozenset(stamps)
    if new == _STAMPS:
        return
    _STAMPS = new
    if _PATH:
        try:
            with open(_PATH + ".tmp", "w", encoding="utf-8") as f:
                json.dump(sorted(new), f, separators=(",", ":"))
            os.replace(_PATH + ".tmp", _PATH)
        except OSError:
            pass


def version():
    """Changes when the set of ignored minutes does (a cached answer worked out with another set is looked at again)."""
    return len(_STAMPS)


def has(stamp):
    return stamp in _STAMPS
