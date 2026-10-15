#!/usr/bin/env python3
"""app_config.py - the settings of the app, the drive report pages and the places lookups, read from the "app" block of config.json.

Nothing here is a secret. Every setting has a default below, so config.json only needs the values you want to change (see config.example.json for
the block). A change takes effect the next time the app or script reads it (the app re-reads config.json when its modified time changes).

  app.map.tiles_base / style_light / style_dark   OpenFreeMap style URL base and the style used in light / dark mode (liberty = 3D, bright, positron, dark, fiord)
  app.map.places_max_lon                           places east of this longitude are left off the Places map (160 = no New Zealand)
  app.drives.repark_max_km / repark_max_minutes    a drive shorter than both is a "re-park" (the car was shuffled), not a drive
  app.drives.long_highway_*                        a "long highway" drive (the only kind that shows 110/100 speed-change markers)
  app.drives.charge_window_minutes                 a charge within this long after / before a drive makes it "to charge" / "from charge"
  app.drives.low_battery_pct                       the "finished at N% or less" filter
  app.places.stay_min_seconds                      a gap between real drives shorter than this is not a parked period
  app.nearby.*                                     OpenStreetMap lookups for "what is around here" (Overpass servers, time limits, Photon fallback)
  app.window.*                                     the native window size and the local web port
"""
import json
import os
import threading

try:
    import tessie_api_common as _T
except ImportError:                      # imported as lib.app_config
    from lib import tessie_api_common as _T  # type: ignore

DEFAULTS = {
    "map": {"tiles_base": "https://tiles.openfreemap.org/styles/", "style_light": "positron", "style_dark": "dark", "places_max_lon": 160},
    "drives": {"repark_max_km": 0.05, "repark_max_minutes": 10,
               "long_highway_km": 30, "long_highway_fast_kmh": 90, "long_highway_fast_minutes": 10,
               "charge_window_minutes": 30, "low_battery_pct": 20},
    "places": {"stay_min_seconds": 120},
    "nearby": {"overpass_endpoints": ["https://overpass.kumi.systems/api/interpreter", "https://overpass.private.coffee/api/interpreter",
                                      "https://overpass.openstreetmap.fr/api/interpreter", "https://lz4.overpass-api.de/api/interpreter",
                                      "https://overpass-api.de/api/interpreter"],
               "overpass_total_timeout_s": 20, "overpass_server_timeout_s": 8,
               "photon_url": "https://photon.komoot.io/reverse", "photon_limit": 50},
    "window": {"width": 1440, "height": 940, "port": 8765},
}
_lock = threading.Lock()
_cache = {"mtime": None, "path": None, "val": None}


def path():
    return _T.resolve_config_path()


def _merge(base, over):
    out = {}
    for k, v in base.items():
        o = over.get(k) if isinstance(over, dict) else None
        out[k] = _merge(v, o) if isinstance(v, dict) and isinstance(o, dict) else (o if o is not None and not isinstance(v, dict) else v)
    return out


def settings():
    """All settings, defaults filled in. Re-read when config.json changes."""
    p = path()
    try:
        mt = os.stat(p).st_mtime
    except OSError:
        mt = None
    with _lock:
        if _cache["val"] is not None and _cache["mtime"] == mt and _cache["path"] == p:
            return _cache["val"]
        raw = {}
        if mt is not None:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    raw = (json.load(f) or {}).get("app") or {}
            except Exception:
                raw = {}
        _cache.update(mtime=mt, path=p, val=_merge(DEFAULTS, raw if isinstance(raw, dict) else {}))
        return _cache["val"]


def get(dotted, default=None):
    """get("drives.repark_max_km")"""
    v = settings()
    for k in dotted.split("."):
        if not isinstance(v, dict) or k not in v:
            return default
        v = v[k]
    return v
