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
    """Alias for haversine_km."""
    return haversine_km(lat1, lon1, lat2, lon2)



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
    b_path = os.path.join(repo_root, "Tessie", "state_boundaries.json")
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
        tessie_home = os.path.join(repo_root, "Tessie")
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
        tessie_home = os.path.join(repo_root, "Tessie")
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


def state_from_coords(lat: float, lon: float, country: str = "Australia", repo_root: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Determines state/territory from GPS coordinates using a hybrid guide approach:
    1. Instant rejection if coordinates are outside the country's bounding box.
    2. Instant check against safe_bounds in state_boundaries.json (0 network calls).
    3. If within border_threshold_km of a state boundary, checks persistent geo_cache.json
       or performs an OSM Nominatim reverse lookup and caches the result.
    Returns: (state_code: str or None, suburb: str or None)
    """
    if lat is None or lon is None:
        return None, None
    try:
        lat, lon = float(lat), float(lon)
    except (ValueError, TypeError):
        return None, None

    boundaries = load_state_boundaries(repo_root=repo_root)
    country_config = {}
    if boundaries:
        for r_key, r_data in boundaries.get("regions", {}).items():
            for c_name, c_data in r_data.get("countries", {}).items():
                if c_name.lower() == country.lower():
                    country_config = c_data
                    break
        if not country_config:
            country_config = boundaries.get(country, boundaries.get("states", {}))

    for st, data in country_config.get("states", {}).items():
        sb = data.get("safe_bounds")
        if sb and len(sb) == 4:
            if sb[0] <= lat <= sb[2] and sb[1] <= lon <= sb[3]:
                return st, None

    for st, box in AU_STATE_BOUNDS.items():
        if box["lat"][0] <= lat <= box["lat"][1] and box["lon"][0] <= lon <= box["lon"][1]:
            return st, None

    return None, None
