#!/usr/bin/env python3
"""lib/car_figures.py - the car's own pack size, full range and driving consumption, read from its data. Never a typed-in number.

    latest_battery(data_dir)              -> {"capacity_kwh", "max_range_km", "synced_at"} from the newest battery health reading, or None
    usable_kwh(data_dir)                  -> the pack's usable kWh now (newest battery health reading), or None
    driving_wh_per_km(data_dir, t0, t1)   -> Wh/km over the drives in drives_master.csv (sum of energy used / sum of distance), or None

Every function returns None when the data is not there, so a caller shows "unknown" instead of a guess.
"""
import csv
import os


def _f(x):
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def latest_battery(data_dir):
    """The newest battery health reading (battery_health_log.json and the Tessie battery exports), or None."""
    if not data_dir:
        return None
    try:
        from lib.battery_health import load_battery_health_history
        hist = load_battery_health_history(data_dir) or []
    except Exception:
        return None
    hist = [h for h in hist if _f(h.get("capacity_kwh"))]
    if not hist:
        return None
    h = max(hist, key=lambda e: str(e.get("synced_at") or ""))
    return {"capacity_kwh": _f(h.get("capacity_kwh")), "max_range_km": _f(h.get("max_range_km")), "synced_at": h.get("synced_at")}


def usable_kwh(data_dir):
    b = latest_battery(data_dir)
    return b["capacity_kwh"] if b else None


def drives_master_path(data_dir):
    for p in (os.path.join(data_dir or "", "drives", "drives_master.csv"), os.path.join(data_dir or "", "drives_master.csv")):
        if data_dir and os.path.isfile(p):
            return p
    return None


def driving_wh_per_km(data_dir, start_epoch=None, end_epoch=None):
    """Wh per km the car used over its logged drives (optionally only those starting in [start_epoch, end_epoch], UTC seconds).
    Only drives with both a distance and an energy figure count. None with no such drive."""
    path = drives_master_path(data_dir)
    if not path:
        return None
    km = kwh = 0.0
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                d, e = _f(r.get("Distance (km)")), _f(r.get("Total Energy Used (kWh)"))
                if not d or d <= 0 or e is None or e <= 0:
                    continue
                if start_epoch is not None or end_epoch is not None:
                    t = _f(r.get("Started At (UTC)"))
                    if t is None or (start_epoch is not None and t < start_epoch) or (end_epoch is not None and t > end_epoch):
                        continue
                km += d
                kwh += e
    except OSError:
        return None
    return kwh * 1000.0 / km if km > 0 else None
