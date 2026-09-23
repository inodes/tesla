"""
Route Planner & Highway Corridor Projection Engine
===================================================
Fetches high-resolution driving route geometry via Open Source Routing Machine (OSRM)
and calculates exact highway progress (km from departure) and lateral detour offsets (km off highway)
for EV charging stations along a driving corridor.

Zero external native dependencies required; pure Python implementation with great-circle
fallback for offline or unroutable coordinates.
"""

import math
import json
import urllib.request
import urllib.parse
from typing import List, Tuple, Dict, Any, Optional
from datetime import datetime, timezone


EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates great-circle distance between two points in kilometers."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2.0) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_KM * c


def point_to_segment_distance_km(
    plat: float, plon: float,
    alat: float, alon: float,
    blat: float, blon: float
) -> Tuple[float, float]:
    """
    Computes distance from point P to line segment AB, and the interpolation ratio t (0.0 to 1.0).
    Uses local Cartesian equirectangular projection for fast sub-meter accuracy.
    Returns (distance_km, t).
    """
    # Mid-latitude scaling for equirectangular approximation
    mid_lat_rad = math.radians((alat + blat + plat) / 3.0)
    cos_lat = math.cos(mid_lat_rad)

    # Convert coordinates to km offsets relative to A
    ax, ay = 0.0, 0.0
    bx = (blon - alon) * (math.pi / 180.0) * EARTH_RADIUS_KM * cos_lat
    by = (blat - alat) * (math.pi / 180.0) * EARTH_RADIUS_KM
    px = (plon - alon) * (math.pi / 180.0) * EARTH_RADIUS_KM * cos_lat
    py = (plat - alat) * (math.pi / 180.0) * EARTH_RADIUS_KM

    dx = bx - ax
    dy = by - ay
    seg_len_sq = dx * dx + dy * dy

    if seg_len_sq < 1e-9:
        dist = math.sqrt(px * px + py * py)
        return dist, 0.0

    # Projection factor t
    t = (px * dx + py * dy) / seg_len_sq
    t_clamped = max(0.0, min(1.0, t))

    closest_x = ax + t_clamped * dx
    closest_y = ay + t_clamped * dy

    dist = math.sqrt((px - closest_x) ** 2 + (py - closest_y) ** 2)
    return dist, t_clamped


def fetch_driving_route(
    waypoints: List[Tuple[float, float]],
    timeout: int = 10,
    fallback: bool = True
) -> Dict[str, Any]:
    """
    Queries public OSRM routing API for driving directions across a sequence of (lat, lon) waypoints.
    Returns dictionary with:
      - distance_km (float)
      - duration_mins (float)
      - polyline: List[Tuple[float, float]] (high-resolution (lat, lon) vertices)
      - summary (str)
    """
    if len(waypoints) < 2:
        raise ValueError("At least 2 waypoints (origin and destination) are required for route generation.")

    # Format OSRM query: {lon1},{lat1};{lon2},{lat2}...
    coords_str = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in waypoints)
    url = f"https://router.project-osrm.org/route/v1/driving/{coords_str}?overview=full&geometries=geojson"

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "TeslaTools/1.0 (https://github.com/inodes/tesla)"}
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
            routes = data.get("routes", [])
            if routes:
                r = routes[0]
                dist_km = round(r.get("distance", 0.0) / 1000.0, 1)
                dur_mins = round(r.get("duration", 0.0) / 60.0, 1)
                geo_coords = r.get("geometry", {}).get("coordinates", [])
                polyline = [(lat, lon) for lon, lat in geo_coords]
                legs = r.get("legs", [])
                summary_names = [leg.get("summary", "") for leg in legs if leg.get("summary")]
                summary = " / ".join(filter(bool, summary_names)) or "Driving Route"
                return {
                    "success": True,
                    "distance_km": dist_km,
                    "duration_mins": dur_mins,
                    "polyline": polyline,
                    "summary": summary,
                    "engine": "OSRM"
                }
    except Exception as e:
        if not fallback:
            raise RuntimeError(f"OSRM Routing failed: {e}")

    # Fallback: Great-circle geometric interpolation between waypoints
    polyline = []
    total_dist = 0.0
    for i in range(len(waypoints) - 1):
        lat1, lon1 = waypoints[i]
        lat2, lon2 = waypoints[i + 1]
        leg_d = haversine_km(lat1, lon1, lat2, lon2)
        total_dist += leg_d
        num_steps = max(5, int(leg_d / 5.0))  # 5km resolution
        for s in range(num_steps):
            frac = s / float(num_steps)
            interp_lat = lat1 + (lat2 - lat1) * frac
            interp_lon = lon1 + (lon2 - lon1) * frac
            polyline.append((interp_lat, interp_lon))
    polyline.append(waypoints[-1])

    # Estimate duration based on 90 km/h average
    est_dur_mins = round((total_dist / 90.0) * 60.0, 1)

    return {
        "success": True,
        "distance_km": round(total_dist, 1),
        "duration_mins": est_dur_mins,
        "polyline": polyline,
        "summary": "Great-Circle Direct Corridor",
        "engine": "GeometricFallback"
    }


def calculate_corridor_stations(
    stations: List[Any],
    polyline: List[Tuple[float, float]],
    corridor_radius_km: float = 5.0
) -> List[Tuple[Any, float, float]]:
    """
    Projects stations onto the route polyline within corridor_radius_km.
    Computes:
      - progress_km: Cumulative distance along the highway from route origin to station's closest access point.
      - detour_km: Perpendicular distance from highway to station (minimum lateral offset).

    Returns a list of (station_record, progress_km, detour_km) tuples sorted by progress_km.
    """
    if len(polyline) < 2:
        return []

    # 1. Precompute cumulative distance along polyline segments
    cum_dists = [0.0]
    seg_lengths = []
    for i in range(len(polyline) - 1):
        lat1, lon1 = polyline[i]
        lat2, lon2 = polyline[i + 1]
        d = haversine_km(lat1, lon1, lat2, lon2)
        seg_lengths.append(d)
        cum_dists.append(cum_dists[-1] + d)

    # 2. Compute route bounding box with corridor buffer for ultra-fast filtering
    min_lat = min(p[0] for p in polyline) - (corridor_radius_km / 110.0)
    max_lat = max(p[0] for p in polyline) + (corridor_radius_km / 110.0)
    min_lon = min(p[1] for p in polyline) - (corridor_radius_km / (111.0 * math.cos(math.radians((min_lat + max_lat) / 2.0))))
    max_lon = max(p[1] for p in polyline) + (corridor_radius_km / (111.0 * math.cos(math.radians((min_lat + max_lat) / 2.0))))

    # 3. Downsample polyline for fast candidate vertex lookup (approx 500m to 1km spacing)
    sample_stride = max(1, len(polyline) // 1000)
    sampled_indices = list(range(0, len(polyline), sample_stride))
    if (len(polyline) - 1) not in sampled_indices:
        sampled_indices.append(len(polyline) - 1)

    matching_stations = []

    for st in stations:
        slat = getattr(st, "lat", None)
        slon = getattr(st, "lon", None)
        if slat is None or slon is None:
            continue

        # Spatial bounding box rejection (<1 microsecond per station)
        if not (min_lat <= slat <= max_lat and min_lon <= slon <= max_lon):
            continue

        # Find closest sampled vertex
        best_sample_idx = 0
        min_sample_dist = float("inf")
        for s_idx in sampled_indices:
            vlat, vlon = polyline[s_idx]
            # Quick equirectangular approx
            dy = (vlat - slat) * 111.0
            dx = (vlon - slon) * 111.0 * math.cos(math.radians(slat))
            d_approx = math.sqrt(dx * dx + dy * dy)
            if d_approx < min_sample_dist:
                min_sample_dist = d_approx
                best_sample_idx = s_idx

        # If even the closest vertex is way too far (> 3x corridor radius), skip
        if min_sample_dist > (corridor_radius_km * 3.5 + 5.0):
            continue

        # Narrow search to segments surrounding best_sample_idx
        search_radius_pts = max(sample_stride * 2, 25)
        start_seg = max(0, best_sample_idx - search_radius_pts)
        end_seg = min(len(polyline) - 1, best_sample_idx + search_radius_pts)

        best_detour = float("inf")
        best_progress = 0.0

        for seg_idx in range(start_seg, end_seg):
            alat, alon = polyline[seg_idx]
            blat, blon = polyline[seg_idx + 1]
            detour, t = point_to_segment_distance_km(slat, slon, alat, alon, blat, blon)
            if detour < best_detour:
                best_detour = detour
                best_progress = cum_dists[seg_idx] + (t * seg_lengths[seg_idx])

        if best_detour <= corridor_radius_km:
            rounded_prog = round(best_progress, 1)
            rounded_detour = round(best_detour, 1)
            # Annotate record
            st.distance_km = rounded_prog  # Used for standard table distance formatting
            st.detour_km = rounded_detour
            st.route_progress_km = rounded_prog
            st.stop_score_data = compute_route_stop_score(st, rounded_detour, corridor_radius_km)
            st.stop_score = st.stop_score_data["score"]
            matching_stations.append((st, rounded_prog, rounded_detour))

    # Sort in geographical route progress order (start to finish)
    matching_stations.sort(key=lambda x: x[1])
    return matching_stations


def compute_route_stop_score(
    station: Any,
    detour_km: Optional[float] = None,
    max_detour_km: float = 5.0
) -> Dict[str, Any]:
    """
    Computes a composite route stop quality score (0.0 to 10.0) based on:
      a) Nearby facilities quality (0.0 - 10.0 pts)
      b) Highway detour offset (0.0 km = 10.0 pts, decaying linearly to 0.0 pts at max_detour_km)
      c) Speed & Capacity (Power kW up to 300kW + Stall count up to 12 bays = 0.0 - 10.0 pts)

    Returns a dict with:
      - score: float (0.0 to 10.0)
      - badge: str (e.g. '🟢 9.2')
      - emoji: str ('🟢', '🟡', '🟠', '🔴')
      - facilities_score: float
      - facilities_summary: str
      - detour_score: float
      - speed_score: float
      - power_pts: float
      - stalls_pts: float
    """
    # a) Facilities score (0.0 - 10.0)
    fac_score = 5.0
    fac_summary = "Neutral / Unverified"
    try:
        try:
            from lib.station_facilities import get_facilities_registry, resolve_station_physical_coords
        except ImportError:
            from station_facilities import get_facilities_registry, resolve_station_physical_coords
        reg = get_facilities_registry()
        if reg:
            phys_lat, phys_lon, _ = resolve_station_physical_coords(station)
            fac_info = reg.get_station_facilities(
                station_key=getattr(station, "key", None),
                station_name=getattr(station, "name", None),
                lat=phys_lat,
                lon=phys_lon
            )
            if fac_info and "score" in fac_info:
                fac_score = float(fac_info["score"])
                fac_summary = fac_info.get("badge", f"{fac_score:.1f}/10")
            elif getattr(station, "amenities", None):
                # BUG-148: a dict/list can be truthy (non-empty) while every value
                # inside it is empty (e.g. {"items": []}) - that carries zero real
                # amenity signal and must NOT floor to a fake "found something"
                # score. Only trust a genuinely non-zero count; otherwise fall
                # through and keep the honest 5.0 "Neutral / Unverified" default
                # rather than a manufactured 3.0 that looks like real OSM data.
                am = getattr(station, "amenities", None)
                if isinstance(am, list) and am:
                    fac_score = min(10.0, max(3.0, len(am) * 1.5))
                    fac_summary = ", ".join(am[:3])
                elif isinstance(am, dict) and am:
                    cnt = sum(len(v) if isinstance(v, list) else 1 for v in am.values())
                    if cnt > 0:
                        fac_score = min(10.0, max(3.0, cnt * 1.2))
                        fac_summary = f"{cnt} amenities listed"
    except Exception:
        pass

    # b) Detour score (0.0 - 10.0)
    d_km = detour_km if detour_km is not None else getattr(station, "detour_km", 0.0)
    if d_km is None or d_km <= 0.0:
        detour_score = 10.0
    else:
        detour_score = max(0.0, min(10.0, (1.0 - (d_km / max(0.1, max_detour_km))) * 10.0))
    detour_score = round(detour_score, 1)

    # c) Speed & Capacity (0.0 - 10.0)
    pwr = getattr(station, "max_power_kw", None) or 0.0
    power_pts = min(5.0, (pwr / 300.0) * 5.0)

    stalls = getattr(station, "stalls_total", None) or 0
    stalls_pts = min(5.0, (stalls / 12.0) * 5.0)

    speed_score = round(power_pts + stalls_pts, 1)

    overall_score = round((fac_score + detour_score + speed_score) / 3.0, 1)

    if overall_score >= 8.0:
        emoji = "🟢"
    elif overall_score >= 6.0:
        emoji = "🟡"
    elif overall_score >= 4.0:
        emoji = "🟠"
    else:
        emoji = "🔴"

    return {
        "score": overall_score,
        "badge": f"{emoji} {overall_score:.1f}",
        "emoji": emoji,
        "facilities_score": round(fac_score, 1),
        "facilities_summary": fac_summary,
        "detour_score": detour_score,
        "speed_score": speed_score,
        "power_pts": round(power_pts, 1),
        "stalls_pts": round(stalls_pts, 1)
    }
