"""car_efficiency.py - what the efficiency figures are built from: the car's mass, a drive segment's grade and the energy it took to climb, grade-corrected kWh/100 km and how much a distance can be trusted. Used by analyse_car_efficiency and lib/ev_efficiency."""

# Below this total bucket distance, flag confidence as "low" - a
# handful of short segments at a given speed is not enough to trust as
# a stable efficiency figure yet (this is exactly what re-running the
# profile after more --backfill-deepdive data is meant to fix).
CONFIDENCE_LOW_KM = 5.0
CONFIDENCE_MEDIUM_KM = 20.0


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


def confidence_for_distance(dist_km):
    if dist_km < CONFIDENCE_LOW_KM:
        return "low"
    if dist_km < CONFIDENCE_MEDIUM_KM:
        return "medium"
    return "high"


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
