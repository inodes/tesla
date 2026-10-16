#!/usr/bin/env python3
"""app_config.py - the settings of the app, the drive report pages and the places lookups, read from the "app" block of config.json.

Nothing here is a secret. Every setting has a default below, so config.json only needs the values you want to change (see config.example.json for
the block). A change takes effect the next time the app or script reads it (the app re-reads config.json when its modified time changes).

  app.timezone                                     the car's home time zone (IANA name). Times are matched in UTC; this is only used to turn the local clock times that
                                                   Tessie's files and the dashcam's file names carry into UTC (and back for display), daylight saving included
  app.map.tiles_base / style_light / style_dark   OpenFreeMap style URL base and the style used in light / dark mode (liberty = 3D, bright, positron, dark, fiord)
  app.map.places_max_lon                           places east of this longitude are left off the Places map (160 = no New Zealand)
  app.map.speed_colors / speed_over_color / autopilot_colors  the speed gradient (km/h -> colour stops, the last stop is the top of the scale; faster than that is speed_over_color, black) and the FSD / Autosteer / TACC / Manual colours of every route map
  app.map.network_colors                           colour of each charger network on the Places map (a network not listed gets a colour of its own)
  app.teslacam.moving_kmh / run_gap_s / gap_min_s / same_place_m / same_place_est_m / same_minute_s / read_workers / days_newest_first / rows_newest_first
                                                   the TeslaCam timeline: a minute is "moving" above this speed; clips this close (s) are one recording; a hole shorter than gap_min_s is not shown;
                                                   a gap is "parked" when the car's last and next position are within same_place_m (same_place_est_m when one is a Sentry estimate);
                                                   clips read at once (CPU work, one process each); days_newest_first / rows_newest_first = the order of the day blocks and of the rows inside a day
  app.review.step_s / min_kmh / min_conf / workers / merge_s   reading speed-limit signs from a drive's video (Review tab); candidate_km / candidate_avg_kmh = the "Review candidates" button on Drives
  app.review.accel_min_pct / speed_from / speed_to / speed_tol_kmh / speed_hold_s / speed_window_s / steer_deg
                                                   the manual drive review (Review tab): an accelerator at or above accel_min_pct % is "pressed"; the speed-change
                                                   finder starts with speed_from -> speed_to km/h (changeable in the tab), a speed within speed_tol_kmh of one is
                                                   "at" it and must hold for speed_hold_s s, the second level must start within speed_window_s s; the wheel moving
                                                   steer_deg degrees around an FSD switch-off counts as steering
  app.review.event_before_s / event_after_s / max_event_stills / sheet_cols / sheet_tile_px
                                                   front-camera stills of one review event: one a second from event_before_s before to event_after_s after (thinned
                                                   to max_event_stills), laid out in a contact sheet of sheet_cols columns of sheet_tile_px-wide tiles
  app.drives.repark_max_km / repark_max_minutes    a drive shorter than both is a "re-park" (the car was shuffled), not a drive
  app.drives.long_highway_*                        a "long highway" drive (the Drives list's Long highway filter)
  app.drives.charge_window_minutes                 a charge within this long after / before a drive makes it "to charge" / "from charge"
  app.drives.low_battery_pct                       the "finished at N% or less" filter
  app.places.stay_min_seconds                      a gap between real drives shorter than this is not a parked period
  app.places.save_radius_*                         radius suggested when saving an OpenStreetMap place: at least min_m, else far enough to reach every stop the car
                                                   recorded within cluster_m of the stop, plus margin_m
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
    "timezone": "Australia/Sydney",
    "map": {"tiles_base": "https://tiles.openfreemap.org/styles/", "style_light": "positron", "style_dark": "dark", "places_max_lon": 160,
            "speed_colors": [[0, "#0000FF"], [10, "#008080"], [20, "#90EE90"], [30, "#008000"], [40, "#32CD32"], [50, "#ADFF2F"], [60, "#FFFF00"], [70, "#FFA500"], [80, "#FF8C00"], [90, "#FF4500"], [100, "#FF0000"], [110, "#8B0000"], [120, "#800080"]],
            "speed_over_color": "#000000",
            "autopilot_colors": {"FSD": "#3b6cf6", "Autosteer": "#8b5cf6", "TACC": "#14b8a6", "Manual": "#94a3b8"},
            "network_colors": {"Tesla": "#e0201b", "Tesla Destination": "#f97316", "Chargefox": "#16a34a", "Evie": "#7c3aed", "EVX": "#eab308", "Exploren": "#db2777",
                              "NRMA": "#2563eb", "AmpCharge": "#84cc16", "Jolt": "#14b8a6", "BP Pulse": "#0891b2", "3rd-Party": "#6b7280", "WA EV Network": "#9333ea"}},
    "teslacam": {"moving_kmh": 3.0, "run_gap_s": 75, "gap_min_s": 120, "same_place_m": 300, "same_place_est_m": 800, "same_minute_s": 30, "read_workers": 4, "days_newest_first": True, "rows_newest_first": True},
    "review": {"step_s": 1.0, "min_kmh": 30.0, "min_conf": 50.0, "workers": 3, "merge_s": 12.0, "candidate_km": 20, "candidate_avg_kmh": 60,
               "accel_min_pct": 5, "speed_from": 100, "speed_to": 110, "speed_tol_kmh": 3, "speed_hold_s": 8, "speed_window_s": 90,
               "event_before_s": 12, "event_after_s": 15, "max_event_stills": 40, "steer_deg": 15, "sheet_cols": 7, "sheet_tile_px": 240},
    "drives": {"repark_max_km": 0.05, "repark_max_minutes": 10,
               "long_highway_km": 30, "long_highway_fast_kmh": 90, "long_highway_fast_minutes": 10,
               "charge_window_minutes": 30, "low_battery_pct": 20},
    "places": {"stay_min_seconds": 120, "save_radius_min_m": 100, "save_radius_margin_m": 25, "save_radius_cluster_m": 250},
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
