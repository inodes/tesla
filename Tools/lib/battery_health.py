"""battery_health.py - the car's battery health history (the Tessie battery CSVs), for analyse_car_efficiency and analyse_battery_health."""

import os
import json
import csv
import glob
from datetime import datetime


def _safe_float(v):
    """Best-effort float parse - returns None (never raises) for
    blank/missing/non-numeric CSV cells."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_BATTERY_CSV_REQUIRED_COLUMNS = {
    "Timestamp (AEST)",
    "Max Range (km)",
    "Max Ideal Range (km)",
    "Usable Capacity (kWh)",
}


def _parse_battery_health_csv(path):
    """Normalizes one manually-exported battery-health-history CSV -
    Tessie's own /battery export, seen under two different filenames
    in the wild so far (battery_health_<start>_to_<end>.csv from an
    older export, <VIN>-battery.csv from a newer one - AGENTS.md
    TODO-011, still a manual download today, no automated
    fetch+consolidate for this endpoint yet) - into the same shape
    battery_health_log.json entries use, so every source merges into
    one real trend regardless of which export naming produced it.
    Returns a list of dicts (possibly empty) - a row that fails to
    parse is just skipped, and a file that isn't actually a
    battery-health export (wrong columns) is skipped entirely rather
    than guessed at."""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            _cols = set(reader.fieldnames or [])
            # BUG-176: the exporter's zone label flips AEST<->AEDT with daylight saving
            _ts_col = next((c for c in (reader.fieldnames or []) if c and c.startswith("Timestamp (A")), None)
            if _ts_col:
                _cols.add("Timestamp (AEST)")
            if not _BATTERY_CSV_REQUIRED_COLUMNS.issubset(_cols):
                return []
            entries = []
            for r in reader:
                ts_raw = (r.get(_ts_col) or r.get("Timestamp (AEST)") or "").strip()
                if not ts_raw:
                    continue
                try:
                    dt = datetime.strptime(ts_raw[:19], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                capacity = _safe_float(r.get("Usable Capacity (kWh)"))
                if capacity is None:
                    continue
                entries.append({
                    "synced_at": dt.isoformat(timespec="seconds"),
                    "capacity_kwh": capacity,
                    "max_range_km": _safe_float(r.get("Max Range (km)")),
                    "max_ideal_range_km": _safe_float(r.get("Max Ideal Range (km)")),
                    "odometer_km": _safe_float(r.get("Odometer (km)")),
                    "source": f"csv:{os.path.basename(path)}",
                })
            return entries
    except Exception:
        return []


def load_battery_health_history(tessie_dir_home, include_outliers=False):
    """The FULL history of readings, merged from every source this repo
    has for it: battery_health_log.json (written by sync_api_tessie.py
    --battery-health, AGENTS.md TODO-031/REQ-045 - it appends one entry
    per sync, on whatever regular cadence sync_drives_all.sh runs at,
    never overwrites) PLUS any manually-exported battery-health-history
    CSV sitting loose at the Tessie data home root (TODO-011 - naming
    has already changed once in the wild, from
    battery_health_<start>_to_<end>.csv to <VIN>-battery.csv, so every
    *.csv found there is sniffed by its header rather than matched by
    filename). The whole point is to be able to see a real degradation
    TREND over time, not just the latest single reading, per the
    user's own request ("I would prefer a full list of all the
    entries... because we want to see battery health trend") - and
    per the user's own correction after an earlier version of this
    function only read battery_health_log.json ("This is not true.
    Tessie has all entries."), it now merges in the real historical
    exports too. Entries are deduplicated by exact synced_at timestamp
    (a live JSON reading wins over a CSV row at the same instant, since
    it's the more precise/authoritative source) and returned
    oldest-to-newest.

    When include_outliers is False (default), records flagged with
    'is_outlier': True are filtered out so downstream degradation
    profiles and live capacity selection are not distorted by transient
    charging artifacts.

    Returns None if nothing at all was found/readable - callers needing
    "the current capacity" should use history[-1] (the most recent real
    reading) and fall back to the empirical estimate if this is None."""
    if not tessie_dir_home:
        return None

    merged = {}

    log_path = os.path.join(tessie_dir_home, "battery_health_log.json")
    if os.path.isfile(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                log = json.load(f)
            if isinstance(log, list):
                for e in log:
                    if isinstance(e, dict) and e.get("capacity_kwh") is not None and e.get("synced_at"):
                        if not include_outliers and e.get("is_outlier") is True:
                            continue
                        e = dict(e)
                        e.setdefault("source", "battery_health_log.json")
                        merged[e["synced_at"]] = e
        except Exception:
            pass

    try:
        csv_paths = sorted(glob.glob(os.path.join(tessie_dir_home, "*.csv")))
    except Exception:
        csv_paths = []
    for csv_path in csv_paths:
        for e in _parse_battery_health_csv(csv_path):
            if not include_outliers and e.get("is_outlier") is True:
                continue
            merged.setdefault(e["synced_at"], e)  # a live JSON reading at the exact same timestamp wins

    if not merged:
        return None

    def _sort_key(e):
        try:
            return datetime.fromisoformat(e["synced_at"])
        except (ValueError, TypeError):
            return datetime.min

    return sorted(merged.values(), key=_sort_key)
