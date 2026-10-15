"""
lib/ev_efficiency.py - Empirical EV urban/highway efficiency split.

Real-world kWh/100km for this vehicle, split at the same 70km/h boundary
lib.fuel_common.compute_drive_urban_highway_split() already uses for the
comparison-vehicle side of a running-cost comparison ("urban" = avg speed
<= urban_speed_threshold_kmh, "highway" = above it), so a fuel car's
"highway" figure and this EV's "highway" figure mean the same real-world
speed range on both sides of any comparison built from both.

Reuses, rather than re-implements, analyse_car_efficiency.py's own
already-live-verified machinery:
- analyse_drives.DriveAnalyzer for loading real drive telemetry and
  turning it into merged speed/grade segments (compute_drive_segments()).
- grade_corrected_efficiency_kwh_per_100km() / potential_energy_kwh() -
  the same physics-based correction that removes a hill's potential-
  energy contribution from a segment's measured efficiency, so a urban
  loop with a lot of climbing doesn't look artificially inefficient (and
  a descent doesn't look artificially efficient) relative to a flatter
  one. Applied here to EVERY non-stationary segment regardless of class
  (climbing/level/descending), exactly as build_profile()'s own
  corrected_buckets pool does - this is a broader, more usable sample
  than the strict-flat-only pool build_profile() also keeps for its
  by-speed-bucket table.
- vehicle_mass_kg_for_profile() - same mass resolution order (CLI
  override > Tessie/vehicle_spec.json's assumed_driving_mass_kg >
  FALLBACK_VEHICLE_MASS_KG).
- confidence_for_distance() - same low/medium/high distance-based
  confidence label used throughout analyse_car_efficiency.py's output.

REQ-159 (2026-09-28): added for task #56 - deriving the EV side of the planned
cost-per-100km-over-time comparison chart (task #58) from the user's OWN
real driving instead of an assumed WLTP/GVG figure, split the same way
the fuel-car comparison side already is.
"""

import os
import sys

_TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from analyse_drives import DriveAnalyzer  # noqa: E402
from analyse_car_efficiency import (  # noqa: E402
    grade_corrected_efficiency_kwh_per_100km,
    potential_energy_kwh,
    vehicle_mass_kg_for_profile,
    confidence_for_distance,
)
from lib.tessie_api_common import load_vehicle_spec  # noqa: E402

DEFAULT_URBAN_SPEED_THRESHOLD_KMH = 70.0


def _empty_group():
    return {
        "segment_count": 0,
        "total_distance_km": 0.0,
        "total_duration_mins": 0.0,
        "avg_efficiency_kwh_per_100km": None,
        "confidence": "none",
    }


def _finalize_group(segs):
    if not segs:
        return _empty_group()
    total_dist = sum(s["distance_km"] for s in segs)
    total_dur = sum(s["duration_mins"] for s in segs)
    total_energy = sum(s["energy_kwh"] for s in segs if s.get("energy_kwh") is not None)
    weighted_eff = (total_energy / total_dist) * 100.0 if total_dist > 0 else None
    return {
        "segment_count": len(segs),
        "total_distance_km": round(total_dist, 2),
        "total_duration_mins": round(total_dur, 1),
        "avg_efficiency_kwh_per_100km": round(weighted_eff, 1) if weighted_eff is not None else None,
        "confidence": confidence_for_distance(total_dist),
    }


def compute_ev_urban_highway_efficiency(
    tessie_dir=None,
    vehicle_mass_kg=None,
    urban_speed_threshold_kmh=DEFAULT_URBAN_SPEED_THRESHOLD_KMH,
):
    """Real, empirically-measured urban vs highway kWh/100km for this
    vehicle, aggregated from every drive with high-resolution telemetry
    (same DriveAnalyzer.match_detailed_drive() / compute_drive_segments()
    pipeline analyse_car_efficiency.py's build_profile() already uses),
    split at urban_speed_threshold_kmh (default 70km/h, matching
    lib.fuel_common.compute_drive_urban_highway_split()'s own default -
    "urban" is avg segment speed <= threshold, "highway" is above it).

    Each non-stationary segment's efficiency is grade-corrected (its
    potential-energy component from elevation change removed) before
    being assigned to a group, so hilly urban driving and flat highway
    driving (or vice versa) stay comparable.

    Returns {"urban": {...}, "highway": {...}, "vehicle_mass_kg": ...,
    "urban_speed_threshold_kmh": ...}, where each group dict has
    segment_count, total_distance_km, total_duration_mins,
    avg_efficiency_kwh_per_100km (None if no segments), and confidence
    ("none"/"low"/"medium"/"high", via the same distance thresholds as
    analyse_car_efficiency.py's per-speed-bucket table)."""
    analyzer = DriveAnalyzer(tessie_dir=tessie_dir)
    drives = analyzer.load_drives()
    analyzer.load_detailed_drives()

    vehicle_spec = load_vehicle_spec()
    mass_kg = vehicle_mass_kg_for_profile(vehicle_spec, cli_override_kg=vehicle_mass_kg)

    urban_segs = []
    highway_segs = []

    for d in drives:
        dt_rec = analyzer.match_detailed_drive(d)
        if not dt_rec:
            continue
        segments = analyzer.compute_drive_segments(dt_rec["raw_samples"])
        for seg in segments:
            if seg["class"] == "stationary":
                continue
            if seg["distance_km"] <= 0 or seg.get("avg_speed_kmh") is None:
                continue

            # Same derivation build_profile() uses (2026-09-21 fix):
            # compute_drive_segments() only carries the smoothed
            # efficiency ratio through the interval merge, not a raw
            # energy_kwh figure - derive one from that ratio so
            # grade_corrected_efficiency_kwh_per_100km() below has
            # something to correct.
            if seg.get("energy_kwh") is None:
                seg["energy_kwh"] = (
                    (seg["efficiency_kwh_per_100km"] * seg["distance_km"]) / 100.0
                    if seg.get("efficiency_kwh_per_100km") is not None else None
                )

            corrected_eff = grade_corrected_efficiency_kwh_per_100km(seg, mass_kg)
            if corrected_eff is None:
                continue
            pe_kwh = potential_energy_kwh(mass_kg, seg.get("elevation_change_m", 0.0))
            entry = {
                "distance_km": seg["distance_km"],
                "duration_mins": seg["duration_mins"],
                "energy_kwh": (seg["energy_kwh"] or 0.0) - pe_kwh,
            }
            if seg["avg_speed_kmh"] <= urban_speed_threshold_kmh:
                urban_segs.append(entry)
            else:
                highway_segs.append(entry)

    return {
        "urban": _finalize_group(urban_segs),
        "highway": _finalize_group(highway_segs),
        "vehicle_mass_kg": mass_kg,
        "urban_speed_threshold_kmh": urban_speed_threshold_kmh,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(compute_ev_urban_highway_efficiency(), indent=2))
