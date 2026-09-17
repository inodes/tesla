#!/usr/bin/env python3
"""
analyse_car_efficiency.py - AGENTS.md TODO-031

Builds a speed-vs-efficiency / speed-vs-range "fact sheet" for this
vehicle from REAL deep-dive drive telemetry (drive_deepdive_*.csv files
- AGENTS.md TODO-026), meant to be handed to an LLM (or combined with an
ABRP trip estimate) alongside a question like "given I'll be driving
mostly at 100km/h, what efficiency and range should I expect".

Design (per the user's own TODO-031 writeup):
  1. For a given speed, what efficiency should we expect?
  2. For a given speed, what range should we expect?
  3. This is a PROFILE, not a one-off report - re-running it as more
     drives accumulate deep-dive telemetry (see --backfill-deepdive in
     sync_api_tessie.py) makes it more accurate over time. Every run
     fully regenerates the output from whatever real data currently
     exists - there is no incremental/manual state to merge, so it can
     never drift from what the data actually shows.

Method
------
Reuses TessieAnalyzer (analyse_drives.py) for all the real-data
plumbing already proven by REQ-036/037 and TODO-026:
  - load_drives() / load_detailed_drives() to find every drive and every
    local drive_deepdive_*.csv.
  - compute_drive_segments() to break each drive's raw telemetry into
    contiguous climbing/level/descending/stationary segments, with
    per-segment efficiency computed from the REAL Odometer/Energy
    Remaining delta (not a Power(kW) integration - REQ-037), already
    guarded against quantization noise (0.15km minimum distance).

Flat filter and grade correction (per the user's efficiency-legitimacy
feedback on the first version of this tool - "I'd question the
legitimacy of efficiency above 120 and the outlier in 90-100 / I think
you have to look at the inclines these were calculated on ... These
speeds should be calculated on the flat / However an incline up and an
incline down can be also factored in"):

  - compute_drive_segments()'s own +/-2% grade threshold decides
    climbing/level/descending PER INTERVAL before merging into segments;
    that's loose enough that a merged "level" segment's own NET grade
    (elevation_change_m / distance_km) can still be a non-trivial slope,
    especially for the short, thin segments that make up the highest
    speed buckets. STRICT_FLAT_GRADE_PCT below is a second, stricter
    check applied HERE, on each merged segment's own net grade, purely
    for the speed-vs-efficiency buckets - segments that don't pass it are
    excluded from the "flat-only" columns (speed_vs_efficiency_and_range)
    exactly as the user asked ("these speeds should be calculated on the
    flat"). How much this tightening excluded is reported in
    source_data so it's visible when a bucket goes thin/disappears
    because of it, rather than silently.
  - Separately, EVERY non-stationary segment (flat, climbing, AND
    descending) also gets a physics-based grade-corrected efficiency:
    the segment's real measured energy_kwh has the potential-energy
    component (mass * 9.81 * elevation_change_m, converted to kWh)
    removed, isolating the speed/aero/rolling-resistance component so a
    steep climb or descent segment can still contribute a usable,
    flat-equivalent estimate to a speed bucket - this is the "an incline
    up and incline down can be also factored in" ask. It is reported as
    an ADDITIONAL set of columns (speed_vs_efficiency_grade_corrected),
    never merged into or replacing the strict flat-only figures, and is
    explicitly caveated on the assumed mass used (Tessie/vehicle_spec.json's
    mass.assumed_driving_mass_kg when configured - see
    vehicle_mass_kg_for_profile() below - else a generic fallback).

Usable battery capacity: prefers a LIVE reading over a historical
estimate. sync_api_tessie.py --battery-health (AGENTS.md TODO-031)
polls Tessie's own battery_health endpoint on a regular basis and logs
every reading to battery_health_log.json in the Tessie data home; the
MOST RECENT logged capacity_kwh is used here whenever that file exists,
since it reflects the car's actual current degradation, not a snapshot
from whenever historical drives happened to occur. If that log doesn't
exist yet (battery-health sync has never been run), this falls back to
the original empirical estimate: for every drive in drives_master.csv
with a Battery % delta >= 5 points and a real Total Energy Used (kWh)
figure, capacity_kwh = kwh_used / (pct_delta / 100), median of all such
per-drive estimates. Both figures are always reported in the output
(usable_battery_capacity_kwh) even when only one is used for the actual
range column, so the two can be sanity-checked against each other.

Output
------
Writes BOTH a machine-readable JSON (the actual "fact sheet" meant for
an LLM/ABRP combination) and a flat CSV (the same buckets, for a quick
look in a spreadsheet) into the resolved Tessie data home - same
"PII never lives in the repo working tree" rule as drives_master.csv
itself (AGENTS.md Section 1): this is an AGGREGATE, no addresses/
coordinates/per-trip rows, but it's still real personal driving data
and stays out of the git tree on principle, resolved via
TessieAnalyzer's own tessie_data_home (never hardcoded beside this
script).
"""
import os
import sys
import json
import csv
import glob
import statistics
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyse_drives import TessieAnalyzer  # noqa: E402
from lib.tessie_api_common import load_vehicle_spec, load_config, get_vin  # noqa: E402

# (lo, hi) km/h, half-open [lo, hi) except the last bucket.
SPEED_BUCKETS_KMH = [
    (0, 10), (10, 20), (20, 30), (30, 40), (40, 50), (50, 60),
    (60, 70), (70, 80), (80, 90), (90, 100), (100, 110),
    (110, 120), (120, 130), (130, 999),
]

# Below this total bucket distance, flag confidence as "low" - a
# handful of short segments at a given speed is not enough to trust as
# a stable efficiency figure yet (this is exactly what re-running the
# profile after more --backfill-deepdive data is meant to fix).
CONFIDENCE_LOW_KM = 5.0
CONFIDENCE_MEDIUM_KM = 20.0

# Stricter than compute_drive_segments()'s own +/-2% per-interval
# threshold - applied to each MERGED "level" segment's own net grade
# (elevation_change_m over the whole segment, not per-interval) before
# it's allowed into the flat-only speed buckets. See module docstring.
STRICT_FLAT_GRADE_PCT = 0.5

# Fallback vehicle mass for the physics-based grade correction (mass *
# 9.81 * elevation_change_m, converted J -> kWh via /3.6e6) - used ONLY
# if Tessie/vehicle_spec.json doesn't exist yet or has no mass.
# assumed_driving_mass_kg set (see load_vehicle_spec()/
# vehicle_mass_kg_for_profile() below). NOT a confirmed as-tested kerb
# weight for any specific VIN - a generic round Model Y estimate.
# Override with --vehicle-mass-kg, or (preferred - no code/CLI needed)
# by editing Tessie/vehicle_spec.json's mass.assumed_driving_mass_kg.
FALLBACK_VEHICLE_MASS_KG = 2200


def vehicle_mass_kg_for_profile(spec, cli_override_kg=None):
    """Resolution order: an explicit --vehicle-mass-kg CLI value wins
    outright; otherwise Tessie/vehicle_spec.json's own
    mass.assumed_driving_mass_kg (the user's own real curb weight plus
    an assumed typical occupant/cargo load - see vehicle_spec.example.json
    for the field's own note); otherwise FALLBACK_VEHICLE_MASS_KG."""
    if cli_override_kg is not None:
        return cli_override_kg
    spec_mass = (spec or {}).get("mass", {}).get("assumed_driving_mass_kg")
    if spec_mass:
        return spec_mass
    return FALLBACK_VEHICLE_MASS_KG


def vehicle_reference_from_spec(spec):
    """Reference/context data for sanity-checking the measured buckets
    against (WLTP/GVG figures, battery pack info) - NOT something this
    tool measured itself. Sourced entirely from Tessie/vehicle_spec.json
    (see load_vehicle_spec() in tessie_api_common.py and that file's own
    .example.json template) rather than hardcoded here, so it can be
    corrected/updated without a code change. Returns a clearly-flagged
    "not configured" placeholder if that file doesn't exist yet."""
    if not spec:
        return {
            "configured": False,
            "note": "Tessie/vehicle_spec.json not found - copy "
                    "Tessie/vehicle_spec.example.json and fill in this "
                    "car's real spec (see tessie_api_common.load_vehicle_spec()) "
                    "for a reference/sanity-check section here.",
        }
    vehicle = spec.get("vehicle", {})
    battery = spec.get("battery", {})
    mass = spec.get("mass", {})
    aero = spec.get("aero", {})
    wltp = spec.get("wltp", {})
    supercharging = spec.get("supercharging", {})
    gvg = spec.get("gvg_reference", {})
    return {
        "configured": True,
        "model": vehicle.get("model"),
        "trim": vehicle.get("trim"),
        "model_code": vehicle.get("model_code"),
        "battery_chemistry": battery.get("chemistry"),
        "battery_gross_kwh": battery.get("gross_kwh"),
        "battery_usable_kwh": battery.get("usable_kwh"),
        "battery_original_capacity_kwh": battery.get("original_capacity_kwh"),
        "curb_weight_kg": mass.get("curb_weight_kg"),
        "gvwr_kg": mass.get("gvwr_kg"),
        "drag_coefficient_cd": aero.get("drag_coefficient_cd"),
        "wltp_range_km": wltp.get("range_km"),
        "wltp_efficiency_kwh_per_100km": wltp.get("efficiency_kwh_per_100km"),
        "supercharge_15min_added_km": supercharging.get("added_km_15min"),
        "supercharge_15min_efficiency_kwh_per_100km": supercharging.get("efficiency_kwh_per_100km_15min"),
        "gvg_real_world_reference_kwh_per_100km": gvg.get("real_world_efficiency_kwh_per_100km"),
        "source": spec.get("source"),
        "note": "Reference figures only (WLTP/GVG cycles average many speeds "
                "and conditions) - not directly comparable to any single "
                "speed bucket below, just a plausibility check on the "
                "measured range as a whole.",
    }


def bucket_for_speed(speed_kmh):
    for lo, hi in SPEED_BUCKETS_KMH:
        if lo <= speed_kmh < hi:
            return (lo, hi)
    return None


def confidence_for_distance(dist_km):
    if dist_km < CONFIDENCE_LOW_KM:
        return "low"
    if dist_km < CONFIDENCE_MEDIUM_KM:
        return "medium"
    return "high"


def segment_net_grade_pct(seg):
    """The MERGED segment's own overall grade (not the per-interval
    grade compute_drive_segments() used to decide climbing/level/
    descending in the first place - that field doesn't survive the
    interval merge). 0.0 for a zero-distance segment."""
    dist_m = seg.get("distance_km", 0.0) * 1000.0
    if dist_m <= 0:
        return 0.0
    return (seg.get("elevation_change_m", 0.0) / dist_m) * 100.0


def potential_energy_kwh(mass_kg, elevation_change_m):
    """PE = m * g * h (Joules), converted to kWh (/3.6e6)."""
    return (mass_kg * 9.81 * elevation_change_m) / 3_600_000.0


def grade_corrected_efficiency_kwh_per_100km(seg, mass_kg):
    """The segment's real measured efficiency with the potential-energy
    component of its own elevation change removed - an estimate of what
    the efficiency would have been on the flat. Returns None if the
    segment has no usable energy/distance figure."""
    energy_kwh = seg.get("energy_kwh")
    dist_km = seg.get("distance_km", 0.0)
    if energy_kwh is None or dist_km <= 0.15:
        return None
    pe_kwh = potential_energy_kwh(mass_kg, seg.get("elevation_change_m", 0.0))
    corrected_energy_kwh = energy_kwh - pe_kwh
    return (corrected_energy_kwh / dist_km) * 100.0


def estimate_usable_capacity_kwh(drives, min_pct_delta=5):
    """Empirical usable-battery-capacity estimate from real drives_master.csv
    rows (see module docstring) - returns (median_kwh, sample_count,
    low_kwh, high_kwh) or (None, 0, None, None) if too little data."""
    estimates = []
    for d in drives:
        try:
            start_soc = float(d.get("start_soc") or "")
            end_soc = float(d.get("end_soc") or "")
            kwh_used = float(d.get("kwh_used") or "")
        except (ValueError, TypeError):
            continue
        pct_delta = start_soc - end_soc
        if pct_delta < min_pct_delta or kwh_used <= 0:
            continue
        capacity = kwh_used / (pct_delta / 100.0)
        # Sanity bounds - a real pack here is not going to estimate
        # under 20kWh or over 150kWh from one drive; anything outside
        # that is almost certainly a bad/rounded CSV row, not a real
        # capacity reading.
        if 20.0 <= capacity <= 150.0:
            estimates.append(capacity)
    if not estimates:
        return None, 0, None, None
    estimates.sort()
    median_kwh = statistics.median(estimates)
    return median_kwh, len(estimates), estimates[0], estimates[-1]


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
            if not _BATTERY_CSV_REQUIRED_COLUMNS.issubset(set(reader.fieldnames or [])):
                return []
            entries = []
            for r in reader:
                ts_raw = (r.get("Timestamp (AEST)") or "").strip()
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


def battery_health_trend_summary(history):
    """A short first-vs-latest degradation summary once there's enough
    history to say anything - None (not an empty dict) with fewer than
    2 real readings, since a "trend" from one point isn't one."""
    if not history or len(history) < 2:
        return None
    first, latest = history[0], history[-1]
    try:
        first_kwh = float(first["capacity_kwh"])
        latest_kwh = float(latest["capacity_kwh"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "reading_count": len(history),
        "first_synced_at": first.get("synced_at"),
        "first_capacity_kwh": round(first_kwh, 2),
        "latest_synced_at": latest.get("synced_at"),
        "latest_capacity_kwh": round(latest_kwh, 2),
        "change_kwh": round(latest_kwh - first_kwh, 2),
    }


def _bucket_row_from_segments(segs, capacity_kwh, efficiency_key):
    """Shared aggregation for both the strict-flat and grade-corrected
    bucket tables - segs is a list of dicts each carrying at least
    "distance_km", "duration_mins", "energy_kwh" (already
    grade-corrected for the corrected table, raw for the strict-flat
    one) and efficiency_key (the precomputed per-segment efficiency, for
    min/max reporting)."""
    if not segs:
        return {
            "segment_count": 0, "total_distance_km": 0.0,
            "total_duration_mins": 0.0, "avg_efficiency_kwh_per_100km": None,
            "min_efficiency_kwh_per_100km": None, "max_efficiency_kwh_per_100km": None,
            "estimated_range_km": None, "confidence": "none",
        }
    total_dist = sum(s["distance_km"] for s in segs)
    total_dur = sum(s["duration_mins"] for s in segs)
    total_energy = sum(s["energy_kwh"] for s in segs if s.get("energy_kwh") is not None)
    weighted_eff = (total_energy / total_dist) * 100.0 if total_dist > 0 else None
    effs = [s[efficiency_key] for s in segs if s.get(efficiency_key) is not None]
    est_range = (capacity_kwh / weighted_eff) * 100.0 if (capacity_kwh and weighted_eff) else None
    return {
        "segment_count": len(segs),
        "total_distance_km": round(total_dist, 2),
        "total_duration_mins": round(total_dur, 1),
        "avg_efficiency_kwh_per_100km": round(weighted_eff, 1) if weighted_eff is not None else None,
        "min_efficiency_kwh_per_100km": round(min(effs), 1) if effs else None,
        "max_efficiency_kwh_per_100km": round(max(effs), 1) if effs else None,
        "estimated_range_km": round(est_range, 0) if est_range is not None else None,
        "confidence": confidence_for_distance(total_dist),
    }


def build_profile(tessie_dir=None, vehicle_mass_kg=None):
    analyzer = TessieAnalyzer(tessie_dir=tessie_dir)
    drives = analyzer.load_drives()
    analyzer.load_detailed_drives()

    vehicle_spec = load_vehicle_spec()
    vehicle_mass_kg = vehicle_mass_kg_for_profile(vehicle_spec, cli_override_kg=vehicle_mass_kg)

    capacity_empirical_kwh, capacity_n, capacity_lo, capacity_hi = estimate_usable_capacity_kwh(drives)
    battery_history = load_battery_health_history(analyzer.tessie_data_home)
    live_battery = battery_history[-1] if battery_history else None
    battery_trend = battery_health_trend_summary(battery_history)

    if live_battery and live_battery.get("capacity_kwh"):
        capacity_kwh = live_battery["capacity_kwh"]
        capacity_source = "live_battery_health"
    elif capacity_empirical_kwh:
        capacity_kwh = capacity_empirical_kwh
        capacity_source = "empirical_historical"
    else:
        capacity_kwh = None
        capacity_source = "none"

    strict_buckets = {b: [] for b in SPEED_BUCKETS_KMH}
    corrected_buckets = {b: [] for b in SPEED_BUCKETS_KMH}
    climb_segs, descend_segs, stationary_km = [], [], 0.0
    drives_with_telemetry = 0
    total_level_distance_km = 0.0
    strict_flat_distance_km = 0.0
    excluded_by_stricter_filter_count = 0
    excluded_by_stricter_filter_km = 0.0

    for d in drives:
        dt_rec = analyzer.match_detailed_drive(d)
        if not dt_rec:
            continue
        drives_with_telemetry += 1
        segments = analyzer.compute_drive_segments(dt_rec["raw_samples"])
        for seg in segments:
            if seg["class"] == "stationary":
                stationary_km += seg["distance_km"]
                continue
            if seg["distance_km"] <= 0 or seg.get("avg_speed_kmh") is None:
                continue

            b = bucket_for_speed(seg["avg_speed_kmh"])

            # Grade-corrected pool: every non-stationary segment,
            # regardless of class, contributes here (the whole point of
            # factoring in inclines rather than throwing them away).
            corrected_eff = grade_corrected_efficiency_kwh_per_100km(seg, vehicle_mass_kg)
            if b is not None and corrected_eff is not None:
                pe_kwh = potential_energy_kwh(vehicle_mass_kg, seg.get("elevation_change_m", 0.0))
                corrected_buckets[b].append({
                    "distance_km": seg["distance_km"],
                    "duration_mins": seg["duration_mins"],
                    "energy_kwh": (seg["energy_kwh"] or 0.0) - pe_kwh,
                    "grade_corrected_efficiency_kwh_per_100km": corrected_eff,
                })

            if seg["class"] == "climbing":
                climb_segs.append(seg)
                continue
            if seg["class"] == "descending":
                descend_segs.append(seg)
                continue

            # class == "level" - eligible for the strict flat-only pool,
            # subject to the tighter net-grade check this tool applies
            # on top of compute_drive_segments()'s own +/-2%.
            if seg.get("efficiency_kwh_per_100km") is None:
                continue
            total_level_distance_km += seg["distance_km"]
            if b is None:
                continue
            net_grade = segment_net_grade_pct(seg)
            if abs(net_grade) > STRICT_FLAT_GRADE_PCT:
                excluded_by_stricter_filter_count += 1
                excluded_by_stricter_filter_km += seg["distance_km"]
                continue
            strict_buckets[b].append(seg)
            strict_flat_distance_km += seg["distance_km"]

    bucket_rows = []
    corrected_bucket_rows = []
    for b in SPEED_BUCKETS_KMH:
        lo, hi = b
        speed_range = [lo, hi if hi != 999 else None]

        strict_row = _bucket_row_from_segments(strict_buckets[b], capacity_kwh, "efficiency_kwh_per_100km")
        strict_row["speed_range_kmh"] = speed_range
        bucket_rows.append(strict_row)

        corrected_row = _bucket_row_from_segments(corrected_buckets[b], capacity_kwh, "grade_corrected_efficiency_kwh_per_100km")
        corrected_row["speed_range_kmh"] = speed_range
        corrected_bucket_rows.append(corrected_row)

    def grade_summary(segs):
        if not segs:
            return None
        total_dist = sum(s["distance_km"] for s in segs)
        total_energy = sum(s["energy_kwh"] for s in segs if s.get("energy_kwh") is not None)
        weighted_eff = (total_energy / total_dist) * 100.0 if total_dist > 0 else None
        return {
            "segment_count": len(segs),
            "total_distance_km": round(total_dist, 2),
            "avg_efficiency_kwh_per_100km": round(weighted_eff, 1) if weighted_eff is not None else None,
        }

    profile = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "notes": [
            "Vehicle identity/spec (vehicle_reference below) comes from "
            "Tessie/vehicle_spec.json, not hardcoded here - edit that "
            "file directly to correct/update it, no code change needed "
            "(see vehicle_spec.example.json for the schema). "
            "vehicle_reference.configured is False if that file doesn't "
            "exist yet.",
            "usable_battery_capacity_kwh.kwh_used_for_range prefers the "
            "LATEST entry in live_reading_history (sync_api_tessie.py "
            "--battery-health, polled on a regular basis - see "
            "live_reading_trend for how it's moved since the first "
            "logged reading) since it reflects the car's actual current "
            "degradation; the empirical historical estimate (median of "
            "kWh-used/%-used across real drives) is kept alongside for "
            "comparison and used only as a fallback if no live reading "
            "has been synced yet.",
            f"speed_vs_efficiency_and_range is FLAT-ONLY: a 'level' "
            f"segment (compute_drive_segments' own +/-2% per-interval "
            f"threshold) is included here only if the MERGED segment's "
            f"own net grade is also within +/-{STRICT_FLAT_GRADE_PCT}% "
            f"- stricter than the +/-2% used to classify it in the first "
            f"place, because the speed-efficiency question this table "
            f"answers is specifically corrupted by grade. See "
            f"source_data.excluded_by_stricter_flat_filter for how much "
            f"this excluded - a bucket that goes thin/disappears here is "
            f"the tightening doing its job, not a bug.",
            "speed_vs_efficiency_grade_corrected is the ADDITIONAL "
            "figure the flat-only table doesn't give up: EVERY "
            "non-stationary segment (flat, climbing, or descending) "
            "gets its measured energy adjusted by a physics-based "
            "potential-energy correction (mass * 9.81 * "
            "elevation_change_m, J->kWh) before being bucketed by speed "
            "- an estimate of what the efficiency would have been on "
            "the flat. This is NOT a replacement for the flat-only "
            "table - it assumes {vehicle_mass_kg:.0f}kg for this car "
            "(source_data.vehicle_mass_kg_assumed_for_grade_correction - "
            "from Tessie/vehicle_spec.json's mass.assumed_driving_mass_kg "
            "when configured, else a generic fallback; override per-run "
            "with --vehicle-mass-kg) and roughly treats regen on a "
            "descent as 100% efficient, so treat it as a rough "
            "supplementary estimate, most useful for filling in a "
            "high-speed bucket the flat-only table has too little data "
            "for.",
            "estimated_range_km assumes the FULL usable capacity with no "
            "reserve/buffer - treat it as an optimistic ceiling, not a "
            "number to plan a trip against on its own.",
            "confidence is 'low' under 5km of real driving in that speed "
            "bucket, 'medium' under 20km, 'high' above - re-run this "
            "script after more drives get deep-dive telemetry "
            "(sync_api_tessie.py --backfill-deepdive --drives) to firm "
            "up any 'low'/'medium' bucket.",
            "live_reading_history is the FULL merged history from every "
            "source this repo has for it: battery_health_log.json (every "
            "--battery-health sync to date) plus any manually-exported "
            "battery-health-history CSV sitting loose at the Tessie data "
            "home root (TODO-011), deduplicated by timestamp - so a real "
            "degradation trend becomes visible across everything Tessie "
            "has recorded, not just new syncs (see live_reading_trend for "
            "a first-vs-latest summary once there are 2+ readings).",
        ],
        "vehicle_reference": vehicle_reference_from_spec(vehicle_spec),
        "source_data": {
            "total_drives_in_master_csv": len(drives),
            "drives_with_deepdive_telemetry": drives_with_telemetry,
            "total_level_distance_km_before_stricter_filter": round(total_level_distance_km, 1),
            "total_strict_flat_distance_km_analyzed": round(strict_flat_distance_km, 1),
            "stationary_distance_km": round(stationary_km, 2),
            "strict_flat_grade_threshold_pct": STRICT_FLAT_GRADE_PCT,
            "excluded_by_stricter_flat_filter": {
                "segment_count": excluded_by_stricter_filter_count,
                "distance_km": round(excluded_by_stricter_filter_km, 1),
            },
            "vehicle_mass_kg_assumed_for_grade_correction": vehicle_mass_kg,
        },
        "usable_battery_capacity_kwh": {
            "kwh_used_for_range": round(capacity_kwh, 1) if capacity_kwh else None,
            "source_used_for_range": capacity_source,
            "live_reading": {
                "capacity_kwh": round(live_battery["capacity_kwh"], 2),
                "synced_at": live_battery.get("synced_at"),
                "max_range_km": live_battery.get("max_range_km"),
                "max_ideal_range_km": live_battery.get("max_ideal_range_km"),
            } if live_battery else None,
            "live_reading_history": battery_history,
            "live_reading_trend": battery_trend,
            "empirical_historical_estimate": {
                "median_estimate_kwh": round(capacity_empirical_kwh, 1) if capacity_empirical_kwh else None,
                "sample_count": capacity_n,
                "range_observed_kwh": [round(capacity_lo, 1), round(capacity_hi, 1)] if capacity_empirical_kwh else None,
            },
        },
        "speed_vs_efficiency_and_range": bucket_rows,
        "speed_vs_efficiency_grade_corrected": corrected_bucket_rows,
        "climbing_summary": grade_summary(climb_segs),
        "descending_summary": grade_summary(descend_segs),
    }
    return profile


def write_outputs(profile, out_dir, vin=None):
    """Writes car_profile.json (renamed from analysis_car_efficiency.json,
    2026-09-08, per the user's own suggestion once vehicle_spec.json/this
    file's own vehicle_reference block made clear how much per-vehicle
    reference data was accumulating here: "given it has so much
    information - maybe call this car_profile.json instead and tie it to
    VIN - so that car_profile.json can have multiple entries") and
    car_profile.csv (same rename, unchanged shape).

    car_profile.json's top level is now {"<vin>": {...this same profile
    dict as before...}, ...} - keyed by VIN so more than one vehicle can
    have its own entry in the same file without clobbering another car's
    data. Any OTHER vin's entry already in the file is read back and
    preserved before writing (this vin's own entry is always fully
    replaced/regenerated, since it's a computed profile, not hand-edited
    data) - if vin is None (couldn't be resolved from config.json),
    falls back to the literal key "unknown_vin" rather than silently
    clobbering the whole file structure."""
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "car_profile.json")
    csv_path = os.path.join(out_dir, "car_profile.csv")
    vin_key = vin or "unknown_vin"

    all_profiles = {}
    if os.path.isfile(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if isinstance(existing, dict):
                all_profiles = existing
        except Exception:
            pass
    all_profiles[vin_key] = profile

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_profiles, f, indent=2)

    corrected_by_range = {
        tuple(row["speed_range_kmh"]): row
        for row in profile["speed_vs_efficiency_grade_corrected"]
    }

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "speed_lo_kmh", "speed_hi_kmh",
            "flat_segment_count", "flat_distance_km", "flat_duration_mins",
            "flat_avg_efficiency_kwh_per_100km", "flat_min_efficiency_kwh_per_100km",
            "flat_max_efficiency_kwh_per_100km", "flat_estimated_range_km", "flat_confidence",
            "grade_corrected_segment_count", "grade_corrected_distance_km",
            "grade_corrected_avg_efficiency_kwh_per_100km", "grade_corrected_estimated_range_km",
            "grade_corrected_confidence",
        ])
        for row in profile["speed_vs_efficiency_and_range"]:
            lo, hi = row["speed_range_kmh"]
            crow = corrected_by_range.get((lo, hi)) or {}
            w.writerow([
                lo, hi if hi is not None else "130+", row["segment_count"],
                row["total_distance_km"], row["total_duration_mins"],
                row["avg_efficiency_kwh_per_100km"], row["min_efficiency_kwh_per_100km"],
                row["max_efficiency_kwh_per_100km"], row["estimated_range_km"], row["confidence"],
                crow.get("segment_count"), crow.get("total_distance_km"),
                crow.get("avg_efficiency_kwh_per_100km"), crow.get("estimated_range_km"),
                crow.get("confidence"),
            ])
    return json_path, csv_path


def main():
    import argparse
    parser = argparse.ArgumentParser(
        usage="analyse_car_efficiency.py [--tessie-dir DIR] [--out-dir DIR] [--vehicle-mass-kg KG]\n",
        description="""
NAME
    analyse_car_efficiency.py - build the speed-vs-efficiency/range car profile

DESCRIPTION
    Builds the speed-vs-efficiency/range car profile (car_profile.json/.csv)
    from real deep-dive drive telemetry.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES
  analyse_car_efficiency.py
  analyse_car_efficiency.py --vehicle-mass-kg 2100
"""
    )
    parser.add_argument("--tessie-dir", help="Custom path to directory containing Tessie CSV exports")
    parser.add_argument("--out-dir", help="Where to write car_profile.json/.csv (default: alongside drives_master.csv, i.e. the resolved Tessie data home - never the repo)")
    parser.add_argument("--vehicle-mass-kg", type=float, default=None,
                         help="Vehicle mass used for the grade-corrected efficiency estimate - "
                              "overrides Tessie/vehicle_spec.json's mass.assumed_driving_mass_kg "
                              f"for this run only (falls back to {FALLBACK_VEHICLE_MASS_KG}kg if "
                              "neither is set)")
    args = parser.parse_args()

    analyzer_probe = TessieAnalyzer(tessie_dir=args.tessie_dir)
    out_dir = args.out_dir or analyzer_probe.tessie_data_home or "."

    profile = build_profile(tessie_dir=args.tessie_dir, vehicle_mass_kg=args.vehicle_mass_kg)
    vin = get_vin(None, load_config())
    json_path, csv_path = write_outputs(profile, out_dir, vin=vin)

    src = profile["source_data"]
    print(f"Analyzed {src['drives_with_deepdive_telemetry']} of {src['total_drives_in_master_csv']} drives with deep-dive telemetry.")
    print(f"Flat-only distance analyzed: {src['total_strict_flat_distance_km_analyzed']} km "
          f"(+/-{src['strict_flat_grade_threshold_pct']}% net grade) - "
          f"{src['excluded_by_stricter_flat_filter']['distance_km']} km across "
          f"{src['excluded_by_stricter_flat_filter']['segment_count']} segment(s) excluded by this "
          f"tighter filter vs. compute_drive_segments()'s own +/-2%.")

    cap = profile["usable_battery_capacity_kwh"]
    if cap["live_reading"]:
        n_readings = len(cap["live_reading_history"]) if cap.get("live_reading_history") else 1
        print(f"Usable battery capacity (LIVE, used for range below): {cap['live_reading']['capacity_kwh']} kWh "
              f"(synced {cap['live_reading']['synced_at']}, {n_readings} reading(s) logged to date)")
        trend = cap.get("live_reading_trend")
        if trend:
            print(f"  Trend: {trend['first_capacity_kwh']} kWh ({trend['first_synced_at']}) -> "
                  f"{trend['latest_capacity_kwh']} kWh ({trend['latest_synced_at']}), "
                  f"{trend['change_kwh']:+.2f} kWh across {trend['reading_count']} readings")
    emp = cap["empirical_historical_estimate"]
    if emp["median_estimate_kwh"]:
        used_tag = " [used for range - no live reading yet]" if not cap["live_reading"] else " [comparison only]"
        print(f"Usable battery capacity (empirical/historical): {emp['median_estimate_kwh']} kWh "
              f"(from {emp['sample_count']} drives, observed range {emp['range_observed_kwh'][0]}-{emp['range_observed_kwh'][1]} kWh){used_tag}")

    print()
    print("== Flat-only (strict grade filter) ==")
    print(f"{'Speed (km/h)':<14}{'Segments':>9}{'Dist (km)':>11}{'Eff (kWh/100km)':>17}{'Range (km)':>12}{'Confidence':>12}")
    for row in profile["speed_vs_efficiency_and_range"]:
        lo, hi = row["speed_range_kmh"]
        speed_label = f"{lo}-{hi}" if hi is not None else f"{lo}+"
        eff = row["avg_efficiency_kwh_per_100km"]
        rng = row["estimated_range_km"]
        print(f"{speed_label:<14}{row['segment_count']:>9}{row['total_distance_km']:>11}"
              f"{(eff if eff is not None else '-'):>17}{(rng if rng is not None else '-'):>12}{row['confidence']:>12}")

    resolved_mass_kg = profile["source_data"]["vehicle_mass_kg_assumed_for_grade_correction"]
    print()
    print(f"== Grade-corrected (ALL segments, assumed mass {resolved_mass_kg:.0f}kg) ==")
    print(f"{'Speed (km/h)':<14}{'Segments':>9}{'Dist (km)':>11}{'Eff (kWh/100km)':>17}{'Range (km)':>12}{'Confidence':>12}")
    for row in profile["speed_vs_efficiency_grade_corrected"]:
        lo, hi = row["speed_range_kmh"]
        speed_label = f"{lo}-{hi}" if hi is not None else f"{lo}+"
        eff = row["avg_efficiency_kwh_per_100km"]
        rng = row["estimated_range_km"]
        print(f"{speed_label:<14}{row['segment_count']:>9}{row['total_distance_km']:>11}"
              f"{(eff if eff is not None else '-'):>17}{(rng if rng is not None else '-'):>12}{row['confidence']:>12}")

    vr = profile["vehicle_reference"]
    if vr.get("configured") and vr.get("wltp_efficiency_kwh_per_100km"):
        print(f"\nFor reference - {vr.get('trim') or vr.get('model')} WLTP: {vr['wltp_efficiency_kwh_per_100km']} kWh/100km, "
              f"{vr['wltp_range_km']} km (mixed-cycle average, not directly comparable to a single speed bucket).")
    else:
        print(f"\n{vr.get('note', 'Tessie/vehicle_spec.json not configured - no WLTP reference to show.')}")
    print(f"\nWrote {json_path}\nWrote {csv_path}")


if __name__ == "__main__":
    main()
