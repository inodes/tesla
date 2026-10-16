#!/usr/bin/env python3
"""
geo_common.py - Shared Geographic, State Boundary, and Coordinate Utilities
=============================================================================
Provides Australian state coordinate resolution, boundary lookup, geo caching,
and OSM Nominatim reverse geocoding for charging and telemetry analysis.
"""

import os
import sys
import math
import time
import json
import re
import urllib.request
from typing import Dict, List, Optional, Tuple, Any

_lib_dir = os.path.dirname(os.path.abspath(__file__))
_tools_dir = os.path.dirname(_lib_dir)
_repo_root = os.path.dirname(_tools_dir)

AU_STATE_MAP = {
    "NSW": "New South Wales",
    "VIC": "Victoria",
    "QLD": "Queensland",
    "WA": "Western Australia",
    "SA": "South Australia",
    "TAS": "Tasmania",
    "ACT": "Australian Capital Territory",
    "NT": "Northern Territory"
}

AU_STATE_REVERSE_MAP = {v.lower(): k for k, v in AU_STATE_MAP.items()}

AU_STATE_BOUNDS = {
    "ACT": {"lat": (-35.92, -35.12), "lon": (148.76, 149.40)},
    "NSW": {"lat": (-37.51, -28.16), "lon": (140.99, 153.64)},
    "VIC": {"lat": (-39.16, -33.98), "lon": (140.96, 149.98)},
    "QLD": {"lat": (-29.18, -10.05), "lon": (137.99, 153.55)},
    "SA":  {"lat": (-38.06, -25.99), "lon": (128.99, 141.01)},
    "WA":  {"lat": (-35.13, -13.69), "lon": (112.92, 129.01)},
    "TAS": {"lat": (-43.64, -39.57), "lon": (143.83, 148.51)},
    "NT":  {"lat": (-26.01, -10.97), "lon": (128.99, 138.01)},
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Computes the great-circle distance between two points in kilometers."""
    r = 6371.0  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2.0) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates great-circle distance in kilometers between two GPS coordinates using Haversine formula."""
    if None in (lat1, lon1, lat2, lon2):
        return float("inf")
    try:
        f_lat1, f_lon1 = float(lat1), float(lon1)
        f_lat2, f_lon2 = float(lat2), float(lon2)
    except (ValueError, TypeError):
        return float("inf")

    R = 6371.0  # Earth's radius in km
    dlat = math.radians(f_lat2 - f_lat1)
    dlon = math.radians(f_lon2 - f_lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(f_lat1)) * math.cos(math.radians(f_lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c



def clean_station_short_name(name: str) -> str:
    """Replaces all non-alphanumeric characters with underscores and condenses multiple underscores."""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", name)
    return s.strip("_")


def normalize_country_slug(country_name: str) -> str:
    """Normalizes country name to URL slug (e.g. 'Hong Kong' -> 'Hong+Kong', 'Australia' -> 'Australia')."""
    clean = country_name.replace("_", " ").strip()
    c_lower = clean.lower()
    if c_lower in ["au", "australia", "aus"]:
        return "Australia"
    if c_lower in ["us", "usa", "united states", "united_states"]:
        return "United+States"
    if c_lower in ["hk", "hong kong", "hong_kong", "hongkong"]:
        return "Hong+Kong"
    if c_lower in ["nz", "new zealand", "new_zealand"]:
        return "New+Zealand"
    if c_lower in ["uk", "united kingdom", "united_kingdom", "great britain"]:
        return "United+Kingdom"
    if c_lower in ["jp", "japan"]:
        return "Japan"
    words = clean.split()
    cap_words = [w.capitalize() for w in words]
    return "+".join(cap_words)


def extract_au_state_from_text(title: str, address_str: str = "") -> str:
    """Extracts standard 2-3 letter Australian state code from title or address string."""
    text = f"{title} {address_str}"
    m = re.search(r",\s*([A-Za-z]{2,3})(?:\s*-\s*|\s*$|\s*,|\s*\d{4})", title)
    if m:
        st = m.group(1).upper()
        if st in AU_STATE_MAP:
            return st
    for st_code in ["NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"]:
        if re.search(rf"\b{st_code}\b", title):
            return st_code
    text_lower = text.lower()
    for full_name, code in AU_STATE_REVERSE_MAP.items():
        if full_name in text_lower:
            return code
    postcode_match = re.search(r"\b(\d{4})\b", text)
    if postcode_match:
        pc = int(postcode_match.group(1))
        if 2000 <= pc <= 2599 or 2619 <= pc <= 2899 or 2921 <= pc <= 2999:
            return "NSW"
        elif 3000 <= pc <= 3999 or 8000 <= pc <= 8999:
            return "VIC"
        elif 4000 <= pc <= 4999 or 9000 <= pc <= 9999:
            return "QLD"
        elif 5000 <= pc <= 5799 or 5800 <= pc <= 5999:
            return "SA"
        elif 6000 <= pc <= 6797 or 6800 <= pc <= 6999:
            return "WA"
        elif 7000 <= pc <= 7799 or 7800 <= pc <= 7999:
            return "TAS"
        elif 2600 <= pc <= 2618 or 2900 <= pc <= 2920:
            return "ACT"
        elif 800 <= pc <= 899 or 900 <= pc <= 999:
            return "NT"
    return "Other"


def load_state_boundaries(repo_root: Optional[str] = None) -> dict:
    """Loads state_boundaries.json containing corner polygons, safe bounding boxes, and border lines."""
    if not repo_root:
        repo_root = _repo_root
    b_path = os.path.join(repo_root, "resources", "state_boundaries.json")
    if os.path.isfile(b_path):
        try:
            with open(b_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def load_geo_cache(repo_root: Optional[str] = None) -> dict:
    """Loads persistent geocoding cache from Tessie/geo_cache.json or external Tessie data home."""
    tessie_home = None
    try:
        from lib.tessie_api_common import resolve_tessie_data_home
        tessie_home = resolve_tessie_data_home()
    except Exception:
        pass
    if not tessie_home:
        if not repo_root:
            repo_root = _repo_root
        tessie_home = os.path.join(repo_root, "resources")
    c_path = os.path.join(tessie_home, "geo_cache.json")
    if os.path.isfile(c_path):
        try:
            with open(c_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_geo_cache(cache: dict, repo_root: Optional[str] = None):
    """Saves persistent geocoding cache to Tessie/geo_cache.json or external Tessie data home."""
    tessie_home = None
    try:
        from lib.tessie_api_common import resolve_tessie_data_home
        tessie_home = resolve_tessie_data_home()
    except Exception:
        pass
    if not tessie_home:
        if not repo_root:
            repo_root = _repo_root
        tessie_home = os.path.join(repo_root, "resources")
    c_path = os.path.join(tessie_home, "geo_cache.json")
    try:
        os.makedirs(os.path.dirname(c_path), exist_ok=True)
        with open(c_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass


def reverse_geocode_osm(lat: float, lon: float) -> Optional[dict]:
    """Reverse geocodes a coordinate via OpenStreetMap Nominatim, respecting rate limits."""
    url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&addressdetails=1"
    req = urllib.request.Request(url, headers={"User-Agent": "TeslaChargersEngine/1.0 (Australia-GeoResolver)"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            addr = data.get("address", {})
            st_full = (addr.get("state") or "").lower().strip()
            state_code = AU_STATE_REVERSE_MAP.get(st_full, "Other")
            suburb = addr.get("suburb") or addr.get("town") or addr.get("city") or addr.get("village") or ""
            postcode = addr.get("postcode") or ""
            return {
                "state": state_code,
                "state_full": addr.get("state", ""),
                "suburb": suburb,
                "postcode": postcode,
                "source": "osm_nominatim"
            }
    except Exception:
        return None


def _dist_to_segment_km(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    """Computes minimum distance in kilometers from point (px=lon, py=lat) to line segment (x1,y1)-(x2,y2)."""
    dx = (x2 - x1) * 111.0 * math.cos(math.radians(py))
    dy = (y2 - y1) * 111.0
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        return math.hypot((px - x1) * 111.0 * math.cos(math.radians(py)), (py - y1) * 111.0)
    t = max(0.0, min(1.0, ((px - x1) * 111.0 * math.cos(math.radians(py)) * dx + (py - y1) * 111.0 * dy) / seg_len_sq))
    proj_x = x1 + t * (x2 - x1)
    proj_y = y1 + t * (y2 - y1)
    return math.hypot((px - proj_x) * 111.0 * math.cos(math.radians(py)), (py - proj_y) * 111.0)

def get_country_boundary_config(boundaries: dict, country: str = "Australia") -> dict:
    """Extracts country boundary definition, searching under regions hierarchy (APAC, NA, EU, ME, SA) or top-level."""
    if not boundaries:
        return {}
    c_clean = (country or "Australia").replace("+", " ").strip().lower()

 # 1. Search under regions hierarchy
    for r_key, r_data in boundaries.get("regions", {}).items():
        for c_name, c_data in r_data.get("countries", {}).items():
            if c_name.lower() == c_clean or str(c_data.get("country_code", "")).lower() == c_clean:
                return c_data

 # 2. Fallbacks
    if country in boundaries:
        return boundaries[country]
    if "states" in boundaries:
        return boundaries
    return {}

def state_from_coords(lat: float, lon: float, country: str = "Australia", repo_root: str = None) -> tuple:
    """
    Determines state/territory from GPS coordinates using a hybrid guide approach:
    1. Instant rejection if coordinates are outside the country's bounding box.
    2. Instant check against safe_bounds in state_boundaries.json (0 network calls).
    3. If within border_threshold_km of a state boundary, checks persistent geo_cache.json
       or performs an OSM Nominatim reverse lookup and caches the result.
    4. Otherwise interpolates geometric state boundary lines (e.g. Murray River).
    Returns: (state_code: str or None, suburb: str or None)
    """
    if lat is None or lon is None:
        return None, None
    try:
        lat, lon = float(lat), float(lon)
    except (ValueError, TypeError):
        return None, None

    boundaries = load_state_boundaries(repo_root=repo_root)
    country_config = get_country_boundary_config(boundaries, country=country)

 # Country bounding box rejection (e.g. Australia: lat [-44.5, -10.0], lon [112.0, 154.5])
    c_bbox = country_config.get("bbox", [-44.5, 112.0, -10.0, 154.5])
    if c_bbox and len(c_bbox) == 4:
        min_lat, min_lon, max_lat, max_lon = c_bbox
        if lat < min_lat or lat > max_lat or lon < min_lon or lon > max_lon:
            return None, None

    threshold_km = country_config.get("border_threshold_km", 15.0)

 # 1. Fast path: check safe interior bounds (definitively inside state, far from any border)
    for st, data in country_config.get("states", {}).items():
        sb = data.get("safe_bounds")
        if sb and len(sb) == 4:
            if sb[0] <= lat <= sb[2] and sb[1] <= lon <= sb[3]:
                return st, None

 # 2. Border proximity detection
    near_border = False
    for b_name, b_pts in country_config.get("borders", {}).items():
        min_b_lon = min(p[0] for p in b_pts) - 0.5
        max_b_lon = max(p[0] for p in b_pts) + 0.5
        min_b_lat = min(p[1] for p in b_pts) - 0.5
        max_b_lat = max(p[1] for p in b_pts) + 0.5
        if min_b_lon <= lon <= max_b_lon and min_b_lat <= lat <= max_b_lat:
            for i in range(len(b_pts) - 1):
                d = _dist_to_segment_km(lon, lat, b_pts[i][0], b_pts[i][1], b_pts[i+1][0], b_pts[i+1][1])
                if d <= threshold_km:
                    near_border = True
                    break
        if near_border:
            break

 # 3. If near border, check local cache or reverse geocode
    if near_border:
        cache = load_geo_cache(repo_root=repo_root)
        ckey = f"{lat:.4f},{lon:.4f}"
        if ckey in cache:
            entry = cache[ckey]
            return entry.get("state", "Other"), entry.get("suburb")

 # Network lookup with cache write
        geo = reverse_geocode_osm(lat, lon)
        if geo:
            cache[ckey] = geo
            save_geo_cache(cache, repo_root=repo_root)
            time.sleep(1.0)
            return geo.get("state", "Other"), geo.get("suburb")

 # 4. Geometric boundary interpolation (for interior areas not covered by safe boxes)
    act_data = country_config.get("states", {}).get("ACT", {})
    act_bb = act_data.get("bbox", [-35.92, 148.76, -35.12, 149.40])
    if act_bb[0] <= lat <= act_bb[2] and act_bb[1] <= lon <= act_bb[3]:
        return "ACT", None

 # WA
    if lon < 129.0:
        return "WA", None
 # NT vs SA
    if 129.0 <= lon < 138.0:
        return "NT" if lat > -26.0 else "SA", None
 # SA vs QLD/NSW
    if 138.0 <= lon < 141.0:
        if lat > -26.0: return "QLD", None
        return "SA", None

 # NSW vs VIC (Murray River & Black-Allan Line)
    nsw_vic = country_config.get("borders", {}).get("NSW_VIC", [])
    if nsw_vic:
        for i in range(len(nsw_vic) - 1):
            x1, y1 = nsw_vic[i]
            x2, y2 = nsw_vic[i+1]
            if min(x1, x2) <= lon <= max(x1, x2):
                border_lat = y1 + (y2 - y1) * (lon - x1) / (x2 - x1)
                if lat < border_lat:
                    return "VIC", None
                break

 # QLD vs NSW
    qld_nsw = country_config.get("borders", {}).get("QLD_NSW", [])
    if qld_nsw:
        for i in range(len(qld_nsw) - 1):
            x1, y1 = qld_nsw[i]
            x2, y2 = qld_nsw[i+1]
            if min(x1, x2) <= lon <= max(x1, x2):
                border_lat = y1 + (y2 - y1) * (lon - x1) / (x2 - x1)
                if lat > border_lat:
                    return "QLD", None
                break

    if lat < -37.5:
        return "TAS" if lat < -39.5 else "VIC", None
    return "NSW", None
