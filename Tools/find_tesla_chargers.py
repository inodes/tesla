#!/usr/bin/env python3
"""
⚡ Tesla Charger Explorer & Scraper (Find Us Engine) ⚡
======================================================
Hierarchical explorer and live scraper for Tesla charging infrastructure:
- 🗺️  Systematic Drill-Down: Region ➔ Country ➔ Type ➔ State ➔ Station
- 🔴 Superchargers (--sc) & 🔌 Destination Charging (--dc)
- 🇦🇺 Comprehensive Australian state normalization (NSW, VIC, QLD, WA, SA, TAS, ACT, NT)
- 📊 Instantaneous extraction via SSR __NEXT_DATA__ & WebKit API interception
- 💾 Automatic registry integration (superchargers.json & charging.json)
- 🔄 Dynamic multi-drive synchronization across all mounted TESLADRIVE* volumes
"""

import os
import sys

# Auto re-exec inside local direnv/pyenv virtual environment if not already active
_script_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_script_dir)
for _py_candidate in [
    os.path.join(_repo_root, ".direnv", "python-3.11", "bin", "python3"),
    os.path.join(_repo_root, ".direnv", "python-3.11", "bin", "python"),
    os.path.join(_repo_root, ".venv", "bin", "python3"),
    os.path.join(_repo_root, ".venv", "bin", "python")
]:
    if os.path.isfile(_py_candidate) and os.path.abspath(sys.executable) != os.path.abspath(_py_candidate):
        try:
            import playwright
        except ImportError:
            os.execv(_py_candidate, [_py_candidate] + sys.argv)

import re
import time
import json
import math
import random
import shutil
import argparse
import unicodedata
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, parse_qs, quote, unquote
from datetime import datetime, timezone

def get_utc_now_iso() -> str:
    """Returns current UTC timestamp in ISO-8601 format (YYYY-MM-DDTHH:MM:SSZ)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def resolve_location_timezone(state: str = None, country: str = None, lat: float = None, lon: float = None) -> str:
    """
    Deterministically resolves standard IANA timezone identifier for a station or place.
    Supports Australian states (NSW, VIC, QLD, WA, SA, TAS, ACT, NT), international, and coordinates.
    """
    c_clean = (country or "Australia").lower().replace("+", " ").strip()
    st_clean = (state or "").upper().strip()

    if c_clean in ["australia", "au"]:
        if st_clean in ["NSW", "ACT"]:
            return "Australia/Sydney"
        elif st_clean == "VIC":
            return "Australia/Melbourne"
        elif st_clean == "QLD":
            return "Australia/Brisbane"
        elif st_clean == "SA":
            return "Australia/Adelaide"
        elif st_clean == "WA":
            return "Australia/Perth"
        elif st_clean == "TAS":
            return "Australia/Hobart"
        elif st_clean == "NT":
            return "Australia/Darwin"
        
        # Coordinate-based fallback for Australia
        if lon is not None:
            if lon < 129.0:
                return "Australia/Perth"
            elif lon < 141.0:
                return "Australia/Adelaide"
            elif lat is not None and lat > -28.0:
                return "Australia/Brisbane"
            else:
                return "Australia/Sydney"
        return "Australia/Sydney"

    elif c_clean in ["new zealand", "nz"]:
        return "Pacific/Auckland"
    elif c_clean in ["japan", "jp"]:
        return "Asia/Tokyo"
    elif c_clean in ["hong kong", "hk"]:
        return "Asia/Hong_Kong"
    elif c_clean in ["singapore", "sg"]:
        return "Asia/Singapore"
    elif c_clean in ["united kingdom", "uk", "great britain"]:
        return "Europe/London"
    elif c_clean in ["united states", "usa", "us"]:
        us_tz_map = {
            "CA": "America/Los_Angeles", "WA": "America/Los_Angeles", "OR": "America/Los_Angeles", "NV": "America/Los_Angeles",
            "NY": "America/New_York", "NJ": "America/New_York", "MA": "America/New_York", "FL": "America/New_York",
            "TX": "America/Chicago", "IL": "America/Chicago", "CO": "America/Denver", "AZ": "America/Phoenix", "HI": "Pacific/Honolulu"
        }
        return us_tz_map.get(st_clean, "America/New_York")
    elif c_clean in ["germany", "de"]:
        return "Europe/Berlin"
    elif c_clean in ["france", "fr"]:
        return "Europe/Paris"
    
    return "UTC"

# -----------------------------------------------------------------------------
# ANSI Color Codes & Unicode Helpers
# -----------------------------------------------------------------------------

C_CYAN = "\033[96m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_BLUE = "\033[38;5;39m"
C_ORANGE = "\033[38;5;214m"
C_RED = "\033[91m"
C_MAGENTA = "\033[95m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_RESET = "\033[0m"

try:
    import ctypes
    libc = ctypes.CDLL("libc.dylib" if sys.platform == "darwin" else "libc.so.6")
    _libc_wcwidth = libc.wcwidth
    _libc_wcwidth.argtypes = [ctypes.c_wchar]
    _libc_wcwidth.restype = ctypes.c_int

    def char_width(c):
        if c in ('\ufe0f', '\ufe0e'):
            return 0
        w = _libc_wcwidth(c)
        return max(0, w) if w >= 0 else 1
except Exception:
    def char_width(c):
        if c in ('\ufe0f', '\ufe0e'):
            return 0
        if c in ('🔴', '⚡', '🔌', '🏠', '🅿️', '✅', '⚠️', '❌', '❓', '📄', '💾', '📊', '🚗', '🕒', '📍', '💰', '⚙️', '🗺️', '🇦🇺'):
            return 2
        w = unicodedata.east_asian_width(c)
        if w in ('W', 'F'):
            return 2
        return 1

def display_len(s):
    clean = re.sub(r"\033\[[0-9;]*m", "", s)
    return sum(char_width(c) for c in clean)

def pad_display(s, target_width, align="left"):
    d_len = display_len(s)
    pad_len = max(0, target_width - d_len)
    if align == "right":
        return " " * pad_len + s
    elif align == "center":
        left = pad_len // 2
        right = pad_len - left
        return " " * left + s + " " * right
    return s + " " * pad_len

# -----------------------------------------------------------------------------
# Dynamic TESLADRIVE Volume Discovery
# -----------------------------------------------------------------------------

def find_mounted_tesla_volumes(subdir=None):
    """
    Dynamically discovers all mounted volumes matching TESLADRIVE* under /Volumes.
    If subdir is provided (e.g., 'TeslaCam', 'Tessie', 'Tools', 'invoices'),
    returns existing subdirectories within those volumes.
    """
    volumes_root = "/Volumes"
    if not os.path.isdir(volumes_root):
        return []
    discovered = []
    seen = set()
    try:
        entries = sorted(os.listdir(volumes_root))
    except Exception:
        entries = []
    for entry in entries:
        if entry.upper().startswith("TESLADRIVE"):
            vol_path = os.path.join(volumes_root, entry)
            if os.path.isdir(vol_path):
                target = os.path.join(vol_path, subdir) if subdir else vol_path
                if os.path.isdir(target):
                    real_p = os.path.abspath(os.path.realpath(target))
                    if real_p not in seen:
                        seen.add(real_p)
                        discovered.append(real_p)
    return discovered

# -----------------------------------------------------------------------------
# Regional & Geographic Constants
# -----------------------------------------------------------------------------

REGIONS_MAP = {
    "asia_pacific": {
        "name": "Asia/Pacific",
        "aliases": ["asia/pacific", "asia_pacific", "apac", "asia pacific", "asia"],
        "countries": [
            "Australia", "China Mainland", "Hong Kong", "India", "Japan",
            "South Korea", "Macau", "Malaysia", "New Zealand", "Philippines",
            "Singapore", "Thailand", "Taiwan"
        ]
    },
    "north_america": {
        "name": "North America",
        "aliases": ["north america", "north_america", "na", "usa", "us"],
        "countries": ["Canada", "Mexico", "Puerto Rico", "United States"]
    },
    "europe": {
        "name": "Europe",
        "aliases": ["europe", "eu"],
        "countries": [
            "Austria", "Belgium", "Switzerland", "Czech Republic", "Germany",
            "Denmark", "Estonia", "Spain", "Finland", "France", "United Kingdom",
            "Greece", "Croatia", "Hungary", "Ireland", "Iceland", "Italy",
            "Kazakhstan", "Liechtenstein", "Lithuania", "Luxembourg", "Latvia",
            "Morocco", "Netherlands", "Norway", "Poland", "Portugal", "Romania",
            "Serbia", "Sweden", "Slovenia", "Slovakia", "Turkey"
        ]
    },
    "middle_east": {
        "name": "Middle East",
        "aliases": ["middle east", "middle_east", "me"],
        "countries": ["United Arab Emirates", "Israel", "Jordan", "Qatar", "Saudi Arabia"]
    },
    "south_america": {
        "name": "South America",
        "aliases": ["south america", "south_america", "sa", "latam"],
        "countries": ["Chile", "Colombia"]
    }
}

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

# -----------------------------------------------------------------------------
# Normalization Helpers
# -----------------------------------------------------------------------------

def clean_station_short_name(name: str) -> str:
    """Replaces all non-alphanumeric characters with underscores and condenses multiple underscores."""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", name)
    return s.strip("_")

def normalize_country_slug(country_name: str) -> str:
    """Normalizes country name to URL slug (e.g. 'Hong Kong' -> 'Hong+Kong', 'Australia' -> 'Australia')."""
    clean = country_name.replace("_", " ").strip()
    # Normalize common abbreviations
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
    
    # Capitalize words
    words = clean.split()
    cap_words = [w.capitalize() for w in words]
    return "+".join(cap_words)

def extract_au_state_from_text(title: str, address_str: str = "") -> str:
    """Extracts standard 2-3 letter Australian state code from title or address string."""
    text = f"{title} {address_str}"
    
    # 1. Match trailing or embedded ", NSW" / ", VIC" / ", QLD" etc.
    m = re.search(r",\s*([A-Za-z]{2,3})(?:\s*-\s*|\s*$|\s*,|\s*\d{4})", title)
    if m:
        st = m.group(1).upper()
        if st in AU_STATE_MAP:
            return st
            
    # 2. Match embedded state code with word boundaries
    for st_code in ["NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"]:
        if re.search(rf"\b{st_code}\b", title):
            return st_code
            
    # 3. Match full state names
    text_lower = text.lower()
    for full_name, code in AU_STATE_REVERSE_MAP.items():
        if full_name in text_lower:
            return code
            
    # 4. Match postcode ranges in address
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

def merge_tou_intervals(intervals: list) -> list:
    """Merges consecutive TOU rate periods with identical pricing into clean 24h intervals."""
    if not intervals:
        return []
    
    intervals = sorted(intervals, key=lambda x: x["start_time"])
    merged = []
    
    curr = dict(intervals[0])
    for nxt in intervals[1:]:
        if curr["end_time"] == nxt["start_time"] and abs(curr["rate_per_kwh"] - nxt["rate_per_kwh"]) < 0.001:
            curr["end_time"] = nxt["end_time"]
        else:
            merged.append(curr)
            curr = dict(nxt)
    merged.append(curr)

    # Assign descriptive label based on time range
    for entry in merged:
        st, et = entry["start_time"], entry["end_time"]
        is_nt = entry.get("is_non_tesla", False)
        prefix = "Non-Tesla " if is_nt else ""
        
        st_h = int(st.split(":")[0]) if ":" in st else 0
        et_h = int(et.split(":")[0]) if ":" in et else 24
        
        if st_h >= 22 or et_h <= 8:
            entry["label"] = f"{prefix}Off-Peak Night"
        elif st_h >= 8 and et_h <= 20:
            entry["label"] = f"{prefix}Peak Day"
        elif st_h >= 20 and et_h <= 24:
            entry["label"] = f"{prefix}Off-Peak Evening"
        elif st_h >= 8 and et_h >= 22:
            entry["label"] = f"{prefix}Peak Day"
        else:
            entry["label"] = f"{prefix}Standard Rate"
            
    return merged

# -----------------------------------------------------------------------------
# GPS Distance, Auto-Discovery & Filter Helpers
# -----------------------------------------------------------------------------

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

def get_current_gps_location(repo_root: str = None) -> tuple:
    """
    Deterministically determines current GPS coordinates (lat, lon, label):
    1. Highest fidelity: Latest Tesla vehicle parked position from Tessie/drives_master.csv
    2. Primary reference: Home location from Tessie/places.json
    3. Fallback: Fast IP-based geolocation
    """
    if not repo_root:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 1. Latest Vehicle Telemetry / Drives GPS
    drives_p = os.path.join(repo_root, "Tessie", "drives_master.csv")
    if os.path.isfile(drives_p):
        try:
            import csv
            with open(drives_p, "r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
                if rows:
                    latest = rows[-1]
                    e_lat = latest.get("Ending Latitude")
                    e_lon = latest.get("Ending Longitude")
                    e_loc = latest.get("Ending Saved Location") or latest.get("Ending Location") or "Current Vehicle Position"
                    if e_lat and e_lon:
                        return float(e_lat), float(e_lon), f"Vehicle Position ({e_loc})"
        except Exception:
            pass

    # 2. Places.json 'Home' Location
    places_p = os.path.join(repo_root, "Tessie", "places.json")
    if os.path.isfile(places_p):
        try:
            with open(places_p, "r", encoding="utf-8") as f:
                places = json.load(f)
                if "Home" in places:
                    h = places["Home"]
                    if h.get("lat") is not None and h.get("lon") is not None:
                        return float(h["lat"]), float(h["lon"]), "Home (places.json)"
        except Exception:
            pass

    # 3. IP-based Geolocation Fallback
    try:
        import urllib.request
        req = urllib.request.Request("http://ip-api.com/json/", headers={"User-Agent": "curl/7.88.1"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "success" and data.get("lat") is not None:
                city = data.get("city", "Local IP")
                return float(data["lat"]), float(data["lon"]), f"IP Location ({city})"
    except Exception:
        pass

    # Default Sydney CBD fallback
    return -33.8688, 151.2093, "Sydney CBD (Default Fallback)"

_GEOCODE_CACHE = {}

def geocode_address(address_str: str, country_hint: str = "Australia") -> tuple:
    """
    Geocodes an arbitrary address, suburb, or landmark via Nominatim / Photon / Open-Meteo with caching.
    Returns: (lat: float, lon: float, display_name: str) or (None, None, None).
    """
    if not address_str:
        return None, None, None
    clean = address_str.strip()
    if clean in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[clean]

    import urllib.request
    import urllib.parse

    # 1. Try OpenStreetMap Nominatim with contact email
    try:
        q_str = clean if country_hint.lower() in clean.lower() else f"{clean}, {country_hint}"
        params = urllib.parse.urlencode({
            "q": q_str,
            "format": "json",
            "limit": 1,
            "email": "glenn@inodes.org"
        })
        url = f"https://nominatim.openstreetmap.org/search?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "TeslaChargerExplorer/1.0 (glenn@inodes.org)"})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data and len(data) > 0:
                first = data[0]
                lat = float(first["lat"])
                lon = float(first["lon"])
                display = first.get("display_name", clean)
                parts = display.split(", ")
                short_display = ", ".join(parts[:4]) if len(parts) > 4 else display
                res = (lat, lon, short_display)
                _GEOCODE_CACHE[clean] = res
                return res
    except Exception:
        pass

    # 2. Try Photon Komoot API
    try:
        params = urllib.parse.urlencode({"q": clean, "limit": 1})
        url = f"https://photon.komoot.io/api/?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "TeslaChargerExplorer/1.0"})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            feats = data.get("features", [])
            if feats:
                feat = feats[0]
                coords = feat["geometry"]["coordinates"]
                props = feat.get("properties", {})
                name = props.get("name") or props.get("street") or clean
                city = props.get("city") or props.get("state") or ""
                display = f"{name}, {city}".strip(", ")
                res = (float(coords[1]), float(coords[0]), display)
                _GEOCODE_CACHE[clean] = res
                return res
    except Exception:
        pass

    # 3. Try Open-Meteo Geocoding API
    try:
        params = urllib.parse.urlencode({"name": clean, "count": 1, "format": "json"})
        url = f"https://geocoding-api.open-meteo.com/v1/search?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "TeslaChargerExplorer/1.0"})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results", [])
            if results:
                r = results[0]
                display = f"{r.get('name')}, {r.get('admin1', '')}, {r.get('country', '')}".strip(", ")
                res = (float(r["latitude"]), float(r["longitude"]), display)
                _GEOCODE_CACHE[clean] = res
                return res
    except Exception:
        pass

    return None, None, None

def resolve_reference_coordinates(ref_str: str, repo_root: str = None) -> tuple:
    """
    Resolves reference GPS coordinates (lat, lon, label) from:
    1. 'me', 'current', 'gps' -> get_current_gps_location()
    2. Direct coordinate string: '-33.806, 151.079'
    3. Known place name in Tessie/places.json (e.g. 'Home', 'West Ryde Coles')
    4. Registered Supercharger name in Tessie/superchargers.json
    5. Arbitrary street address / suburb via geocode_address()
    """
    if not ref_str:
        return None, None, None

    clean = ref_str.strip()
    if clean.lower() in ["me", "current", "gps", "auto", "here"]:
        return get_current_gps_location(repo_root)

    # Direct coordinate match: lat,lon
    coord_m = re.match(r"^([-\d\.]+)\s*,\s*([-\d\.]+)$", clean)
    if coord_m:
        try:
            return float(coord_m.group(1)), float(coord_m.group(2)), f"({coord_m.group(1)}, {coord_m.group(2)})"
        except Exception:
            pass

    if not repo_root:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Search Tessie/places.json
    places_p = os.path.join(repo_root, "Tessie", "places.json")
    if os.path.isfile(places_p):
        try:
            with open(places_p, "r", encoding="utf-8") as f:
                places = json.load(f)
            # Exact match
            for p_name, p_info in places.items():
                if p_name.lower() == clean.lower():
                    return float(p_info.get("lat")), float(p_info.get("lon")), f"{p_name} (places.json)"
            # Partial match
            for p_name, p_info in places.items():
                if clean.lower() in p_name.lower():
                    return float(p_info.get("lat")), float(p_info.get("lon")), f"{p_name} (places.json)"
                for kw in p_info.get("keywords", []):
                    if clean.lower() in kw.lower():
                        return float(p_info.get("lat")), float(p_info.get("lon")), f"{p_name} ({kw})"
        except Exception:
            pass

    # Search Tessie/superchargers.json
    sc_p = os.path.join(repo_root, "Tessie", "superchargers.json")
    if os.path.isfile(sc_p):
        try:
            with open(sc_p, "r", encoding="utf-8") as f:
                sc_data = json.load(f)
            for sc_name, sc_info in sc_data.items():
                if clean.lower() in sc_name.lower() or clean.lower() in sc_info.get("location", {}).get("suburb", "").lower():
                    loc = sc_info.get("location", {})
                    if loc.get("lat") is not None and loc.get("lon") is not None:
                        return float(loc.get("lat")), float(loc.get("lon")), sc_name
        except Exception:
            pass

    # Fallback to Online Geocoding
    geo_lat, geo_lon, geo_label = geocode_address(clean)
    if geo_lat is not None and geo_lon is not None:
        return geo_lat, geo_lon, geo_label

    return None, None, None

def parse_eval_time(time_str: str = None, tz_name: str = "Australia/Sydney") -> datetime:
    """Parses a time string (e.g. '14:30', '2:30pm', 'now', or ISO) into a timezone-aware datetime."""
    try:
        tz = ZoneInfo(tz_name) if tz_name else ZoneInfo("Australia/Sydney")
    except Exception:
        tz = ZoneInfo("UTC")

    now = datetime.now(tz)
    if not time_str or time_str.lower() in ["now", "current"]:
        return now

    clean = time_str.strip()
    m_12 = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", clean, re.IGNORECASE)
    if m_12:
        h = int(m_12.group(1))
        m = int(m_12.group(2) or 0)
        ap = m_12.group(3).upper()
        if ap == "PM" and h != 12:
            h += 12
        elif ap == "AM" and h == 12:
            h = 0
        return now.replace(hour=h, minute=m, second=0, microsecond=0)

    m_24 = re.match(r"^(\d{1,2}):(\d{2})$", clean)
    if m_24:
        h = int(m_24.group(1))
        m = int(m_24.group(2))
        return now.replace(hour=h, minute=m, second=0, microsecond=0)

    try:
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=tz)
        return dt.astimezone(tz)
    except Exception:
        pass

    return now

def get_effective_rate_at_time(station_data: dict, target_time_input = None, is_non_tesla: bool = False) -> tuple:
    """
    Calculates the exact applicable rate, label, and time window for a station at a specific time.
    Returns: (rate_per_kwh: float, label: str, time_window: str, local_time_display: str)
    """
    loc = station_data.get("location", {})
    tz_name = resolve_location_timezone(
        state=loc.get("state"),
        country=loc.get("country"),
        lat=loc.get("lat"),
        lon=loc.get("lon")
    )

    if isinstance(target_time_input, datetime):
        eval_dt = target_time_input.astimezone(ZoneInfo(tz_name))
    else:
        eval_dt = parse_eval_time(target_time_input, tz_name=tz_name)

    time_24 = eval_dt.strftime("%H:%M")
    day_abbr = eval_dt.strftime("%a")
    month_abbr = eval_dt.strftime("%b")
    local_time_display = eval_dt.strftime("%I:%M %p").lstrip("0")

    tariffs = station_data.get("tariffs", {})
    if is_non_tesla:
        scheds = tariffs.get("non_tesla", {}).get("rate_schedules", [])
    else:
        scheds = tariffs.get("tesla_members", {}).get("rate_schedules", [])
        if not scheds and "tessie_cost_config" in station_data:
            scheds = station_data.get("tessie_cost_config", {}).get("rate_schedules", [])

    if not scheds:
        flat = tariffs.get("per_kwh_flat")
        if flat is not None and float(flat) > 0:
            return float(flat), "Flat Rate", "24/7", local_time_display
        return None, "Standard", "24/7", local_time_display

    for s in scheds:
        months = s.get("months")
        if months and month_abbr not in months:
            continue
        days = s.get("days")
        if days and day_abbr not in days:
            continue

        st = s.get("start_time", "00:00")
        et = s.get("end_time", "24:00")
        et_cmp = "24:00" if et in ["00:00", "24:00"] else et

        if st <= time_24 < et_cmp:
            rate = float(s.get("rate_per_kwh", 0))
            lbl = s.get("label") or s.get("name") or "Rate"
            window = f"{st}–{et}"
            return rate, lbl, window, local_time_display

    fallback = scheds[0]
    return float(fallback.get("rate_per_kwh", 0)), fallback.get("label", "Rate"), f"{fallback.get('start_time', '')}–{fallback.get('end_time', '')}", local_time_display

def evaluate_station_filter(station_data: dict, filter_expr: str, target_time: str = None) -> bool:
    """
    Evaluates a user filter expression or criteria against a station record.
    Supported context variables:
      - tier ('V2', 'V3', 'V4', 'AC')
      - stalls (int)
      - max_power_kw / power (float)
      - open_to_non_tesla / non_tesla (bool)
      - tesla_only (bool)
      - rate / price / rate_now (float, effective at target time)
      - min_rate / max_rate (float)
      - status ('UP_TO_DATE', 'STALE', 'NOT_IN_JSON')
      - state (str, e.g. 'NSW')
      - name / title (str)
      - address (str)
      - suburb (str)
      - dist / distance_km (float)
    """
    if not filter_expr:
        return True

    clean_expr = filter_expr.strip()

    meta = station_data.get("tesla_metadata", {})
    loc = station_data.get("location", {})
    hw = station_data.get("hardware", {})
    comp = station_data.get("compatibility", {})
    tariffs = station_data.get("tariffs", {})

    tier = str(hw.get("tier", "")).upper()
    stalls = int(hw.get("stalls", 0) or 0)
    max_power_kw = float(hw.get("max_power_kw", 0) or 0)
    open_to_non_tesla = bool(comp.get("open_to_non_tesla", False))
    tesla_only = bool(comp.get("tesla_only", not open_to_non_tesla))

    scheds = tariffs.get("tesla_members", {}).get("rate_schedules", [])
    rates = [float(s.get("rate_per_kwh", 0)) for s in scheds if s.get("rate_per_kwh") is not None]
    min_rate = min(rates) if rates else float("inf")
    max_rate = max(rates) if rates else float("inf")

    eff_rate, eff_label, eff_window, _ = get_effective_rate_at_time(station_data, target_time)
    current_rate = eff_rate if eff_rate is not None else min_rate

    status = str(station_data.get("_status", "")).upper()
    state = str(loc.get("state") or station_data.get("state", "")).upper()
    name = str(meta.get("name") or station_data.get("title", ""))
    title = name
    suburb = str(loc.get("suburb", ""))
    address = str(loc.get("address", ""))
    dist = float(station_data.get("_distance_km", float("inf")))

    ctx = {
        "tier": tier,
        "stalls": stalls,
        "max_power_kw": max_power_kw,
        "power": max_power_kw,
        "open_to_non_tesla": open_to_non_tesla,
        "non_tesla": open_to_non_tesla,
        "tesla_only": tesla_only,
        "rate": current_rate,
        "price": current_rate,
        "rate_now": current_rate,
        "min_rate": min_rate,
        "max_rate": max_rate,
        "status": status,
        "state": state,
        "name": name,
        "title": title,
        "suburb": suburb,
        "address": address,
        "dist": dist,
        "distance_km": dist
    }

    try:
        return bool(eval(clean_expr, {"__builtins__": {}}, ctx))
    except Exception:
        # Fallback to case-insensitive text match
        return clean_expr.lower() in json.dumps(ctx).lower()

# -----------------------------------------------------------------------------
# Tesla Charger Explorer & Scraper Engine
# -----------------------------------------------------------------------------

class TeslaChargerExplorer:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.repo_root = os.path.dirname(self.script_dir)
        self.superchargers_path = os.path.join(self.repo_root, "Tessie", "superchargers.json")
        self.superchargers_archived_path = os.path.join(self.repo_root, "Tessie", "superchargers_archived.json")
        self.destination_chargers_path = os.path.join(self.repo_root, "Tessie", "destination_chargers.json")
        self.destination_chargers_archived_path = os.path.join(self.repo_root, "Tessie", "destination_chargers_archived.json")

    def fetch_station_list(self, country: str = "Australia", charger_type: str = "superchargers") -> list:
        """
        Fetches the complete catalog of charging stations for a country.
        charger_type: 'superchargers' or 'chargers' (Destination Charging)
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print(f"{C_RED}❌ Playwright is not installed in the active virtual environment.{C_RESET}")
            print(f"{C_YELLOW}Please ensure your direnv python environment is active.{C_RESET}")
            sys.exit(1)

        country_slug = normalize_country_slug(country)
        target_url = f"https://www.tesla.com/en_AU/findus/list/{charger_type}/{country_slug}"
        
        type_label = "Superchargers" if charger_type == "superchargers" else "Destination Chargers"
        print(f"\n{C_CYAN}🌐 Fetching {type_label} list for {country_slug.replace('+', ' ')}...{C_RESET}")
        print(f"   {C_DIM}{target_url}{C_RESET}")

        stations = []
        with sync_playwright() as p:
            browser = p.webkit.launch(headless=self.headless)
            context = browser.new_context(
                locale="en-AU",
                timezone_id="Australia/Sydney",
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
            )
            page = context.new_page()
            try:
                page.goto(target_url, wait_until="networkidle", timeout=35000)
            except Exception as e:
                print(f"{C_YELLOW}⚠ Network timeout waiting for idle, parsing current DOM...{C_RESET}")

            # Extract location links from page
            links = page.eval_on_selector_all(
                'a[href*="/findus/location/"]',
                'elements => elements.map(e => ({title: e.textContent.trim(), href: e.href}))'
            )
            browser.close()

        seen_hrefs = set()
        for idx, item in enumerate(links, 1):
            title = item.get("title", "").strip()
            href = item.get("href", "").strip()
            if not title or not href or href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            
            # Extract Location ID / Slug from URL
            slug = href.split("/")[-1].split("?")[0]
            state = extract_au_state_from_text(title)
            short_name = clean_station_short_name(title)
            
            stations.append({
                "index": len(stations) + 1,
                "title": title,
                "short_name": short_name,
                "state": state,
                "country": country_slug.replace("+", " "),
                "type": "supercharger" if charger_type == "superchargers" else "destination_charger",
                "slug": slug,
                "url": href
            })

        print(f"{C_GREEN}✔ Discovered {len(stations)} {type_label} in {country_slug.replace('+', ' ')}.{C_RESET}\n")
        return stations

    def _scrape_page_payload(self, page, target_url: str, timeout_ms: int = 35000, max_retries: int = 3, base_retry_delay: float = 2.0) -> tuple:
        """
        Navigates page to target_url and captures XHR/Fetch API responses + SSR __NEXT_DATA__.
        Includes exponential backoff with jitter on timeouts or transient network errors.
        """
        for attempt in range(1, max_retries + 1):
            captured_api_data = {}
            next_data_payload = None

            def handle_response(resp):
                if "get-charger-details" in resp.url or "get-location-details" in resp.url:
                    try:
                        captured_api_data[resp.url] = resp.json()
                    except Exception:
                        pass

            page.on("response", handle_response)
            err_msg = None
            try:
                page.goto(target_url, wait_until="networkidle", timeout=timeout_ms)
                page.wait_for_timeout(800)
            except Exception as e:
                err_msg = str(e)

            # Expand accordions if present
            for accordion_label in ["Pricing for Tesla & Members", "Pricing for Non-Tesla"]:
                try:
                    btn = page.locator(f"button:has-text('{accordion_label}'), [role='button']:has-text('{accordion_label}')").first
                    if btn.count() > 0 and btn.is_visible():
                        btn.click()
                        page.wait_for_timeout(250)
                except Exception:
                    pass

            # Extract __NEXT_DATA__ SSR props
            try:
                raw_next = page.eval_on_selector("#__NEXT_DATA__", "e => e.textContent")
                if raw_next:
                    next_data_payload = json.loads(raw_next)
            except Exception:
                pass

            try:
                page.remove_listener("response", handle_response)
            except Exception:
                pass

            # If valid data captured, return immediately
            if next_data_payload or captured_api_data:
                return captured_api_data, next_data_payload

            # Exponential backoff with jitter on transient error / timeout
            if attempt < max_retries:
                backoff = base_retry_delay * (2 ** (attempt - 1)) + random.uniform(0.3, 1.0)
                reason = f" ({err_msg[:60]}...)" if err_msg else ""
                print(f"  {C_YELLOW}⚠ Attempt {attempt}/{max_retries} failed{reason}. Backing off {backoff:.1f}s before retry...{C_RESET}")
                time.sleep(backoff)

        return {}, None

    def _parse_scraped_data(self, target_url: str, captured_api_data: dict, next_data_payload: dict, charger_type: str = "supercharger") -> tuple:
        """Parses raw intercepted payloads and SSR props into structured station dictionary."""
        charger_payload = None
        location_payload = None
        for req_url, res in (captured_api_data or {}).items():
            if "get-charger-details" in req_url:
                charger_payload = res.get("data", {}).get("data", {})
            elif "get-location-details" in req_url:
                location_payload = res.get("data", {})

        page_props = (next_data_payload or {}).get("props", {}).get("pageProps", {})
        fmt_data = page_props.get("formattedData", {})
        loc_data = page_props.get("locationData", {})

        if not charger_payload and not fmt_data and not loc_data:
            return None, None

        # Extract Core Metadata
        station_name = (
            charger_payload.get("name") if charger_payload
            else fmt_data.get("chargerName") or fmt_data.get("chargerBrandName") or "Tesla Charger"
        )

        # Address & Suburb Parsing
        addr_info = charger_payload.get("address", {}) if charger_payload else {}
        street_num = addr_info.get("streetNumber", "").strip()
        street_name = addr_info.get("street", "").strip()
        street_full = f"{street_num} {street_name}".strip() if street_num else street_name
        suburb = addr_info.get("city", "").strip()
        raw_state = addr_info.get("state", "").strip()
        postcode = addr_info.get("postalCode", "").strip()
        country = addr_info.get("country", "Australia").strip()
        country_code = addr_info.get("countryCode", "AU").strip()

        # Fallback from formattedData.chargerAddress
        if not street_full and fmt_data.get("chargerAddress"):
            addr_lines = fmt_data.get("chargerAddress")
            if len(addr_lines) >= 1:
                street_full = addr_lines[0].strip()
            if len(addr_lines) >= 2:
                parts = addr_lines[1].split(",")
                suburb = parts[0].strip()
                if len(parts) > 1:
                    raw_state = parts[1].strip()

        # Resolve state code
        state_code = extract_au_state_from_text(station_name, f"{street_full} {suburb} {raw_state} {postcode}")
        formatted_address = f"{street_full}, {suburb}, {AU_STATE_MAP.get(state_code, state_code)} {postcode}".strip(", ")

        # Geolocation Coordinates
        lat, lon = None, None
        directions_link = fmt_data.get("chargerAddressDetails", {}).get("directionsLink", "")
        if "daddr=" in directions_link:
            coord_match = re.search(r"daddr=([-\d\.]+),([-\d\.]+)", directions_link)
            if coord_match:
                lat = float(coord_match.group(1))
                lon = float(coord_match.group(2))

        if lat is None and charger_payload:
            centroid = charger_payload.get("centroid", {})
            lat = centroid.get("latitude")
            lon = centroid.get("longitude")

        if lat is None:
            m_lat = loc_data.get("marketing", {}).get("gmaps_override_latitude")
            m_lon = loc_data.get("marketing", {}).get("gmaps_override_longitude")
            if m_lat is not None and m_lon is not None:
                lat = float(m_lat)
                lon = float(m_lon)

        # Hardware Specs
        stalls = (
            charger_payload.get("publicStallCount") if charger_payload
            else fmt_data.get("chargerQuantity") or 8
        )
        max_kw = (
            charger_payload.get("maxPowerKw") if charger_payload
            else fmt_data.get("chargerMaxPower") or 250
        )
        tier = "V4" if max_kw >= 300 else ("V3" if max_kw >= 250 else ("V2" if max_kw >= 120 else "AC"))

        # Non-Tesla Compatibility
        notice = str(fmt_data.get("additionalNotice") or "")
        open_to_non_tesla = (
            bool(charger_payload.get("openToNonTeslas")) if charger_payload
            else ("Open to Tesla and Other EVs" in notice or "CCS compatibility" in notice)
        )

        # General Location / Center Name
        common_name = (
            charger_payload.get("commonSiteName") if charger_payload
            else fmt_data.get("commonSiteName") or loc_data.get("marketing", {}).get("display_name")
        )
        general_location = suburb
        if common_name and common_name != station_name:
            general_location = common_name.split("-")[0].strip()
        elif "Parraweena" in street_full:
            general_location = "Tesla Center"
        elif "Centre" in station_name or "Mall" in station_name:
            general_location = station_name

        short_name = clean_station_short_name(station_name)

        # Access Hours
        hours_str = "Available 24/7"
        raw_hours = charger_payload.get("accessHours") if charger_payload else None
        if isinstance(raw_hours, dict) and raw_hours.get("twentyFourSeven"):
            hours_str = "Available 24/7"
        elif isinstance(raw_hours, str) and raw_hours.strip():
            hours_str = raw_hours.strip()

        # Pricebooks & TOU Rate Schedules
        pricebooks = charger_payload.get("effectivePricebooks", []) if charger_payload else []
        tesla_raw_tou = []
        non_tesla_raw_tou = []
        idle_fee = 1.00
        congestion_fee = 0.50

        for pb in pricebooks:
            fee_type = pb.get("feeType", "").upper()
            v_type = pb.get("vehicleMakeType", "").upper()
            rate = pb.get("rateBase")
            is_tou = pb.get("isTou", False)
            st = pb.get("startTime", "")
            et = pb.get("endTime", "")

            if fee_type == "CONGESTION" and rate is not None:
                congestion_fee = float(rate)
            elif fee_type == "IDLE" and rate is not None:
                idle_fee = float(rate)
            elif fee_type == "CHARGING" and rate is not None:
                if is_tou and st and et:
                    et_clean = "24:00" if et in ["00:00", "24:00"] else et
                    entry = {
                        "start_time": st,
                        "end_time": et_clean,
                        "rate_per_kwh": float(rate)
                    }
                    if v_type == "TSLA":
                        entry["is_non_tesla"] = False
                        tesla_raw_tou.append(entry)
                    elif v_type == "NTSLA":
                        entry["is_non_tesla"] = True
                        non_tesla_raw_tou.append(entry)

        # Fallback pricing parsing from fmt_data.chargerPricing
        if not tesla_raw_tou and fmt_data.get("chargerPricing"):
            for p_group in fmt_data.get("chargerPricing", []):
                grp_label = str(p_group.get("chargingLabel", "")).lower()
                is_nt = "non-tesla" in grp_label or "other" in grp_label
                for p_det in p_group.get("pricingDetails", []):
                    lbl = p_det.get("label", "")
                    rate_str = p_det.get("rate", "")
                    r_val_m = re.search(r"\$?([\d\.]+)", rate_str)
                    rate_val = float(r_val_m.group(1)) if r_val_m else 0.0
                    
                    t_match = re.findall(r"(\d{1,2}):(\d{2})\s*(AM|PM)", lbl, re.IGNORECASE)
                    if len(t_match) == 2:
                        def to_24h(h, m, ap):
                            ih = int(h)
                            if ap.upper() == "PM" and ih != 12:
                                ih += 12
                            elif ap.upper() == "AM" and ih == 12:
                                ih = 0
                            return f"{ih:02d}:{m}"
                        st_24 = to_24h(*t_match[0])
                        et_24 = to_24h(*t_match[1])
                        if et_24 == "00:00":
                            et_24 = "24:00"
                        entry = {
                            "start_time": st_24,
                            "end_time": et_24,
                            "rate_per_kwh": rate_val,
                            "is_non_tesla": is_nt
                        }
                        if is_nt:
                            non_tesla_raw_tou.append(entry)
                        else:
                            tesla_raw_tou.append(entry)

        tesla_rate_schedules = merge_tou_intervals(tesla_raw_tou)
        non_tesla_rate_schedules = merge_tou_intervals(non_tesla_raw_tou)

        # Build backward-compatible tessie_cost_config for Tesla vehicles
        tessie_cost_schedules = []
        for s in tesla_rate_schedules:
            tessie_cost_schedules.append({
                "name": s.get("label", "Rate"),
                "rate_per_kwh": s.get("rate_per_kwh"),
                "start_time": s.get("start_time"),
                "end_time": s.get("end_time"),
                "days": s.get("days", ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]),
                "months": s.get("months", ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
            })

        tz_name = resolve_location_timezone(state=state_code, country=country, lat=lat, lon=lon)
        now_utc = get_utc_now_iso()

        # Construct Unified Station Record
        record = {
            "tesla_metadata": {
                "name": f"Tesla Supercharger - {station_name}" if charger_type == "supercharger" and not station_name.startswith("Tesla") else station_name,
                "general_location": general_location,
                "location_name": general_location,
                "short_name": short_name,
                "type": "supercharger" if charger_type == "supercharger" else "destination_charger",
                "findus_url": target_url,
                "keywords": [
                    street_full,
                    street_name,
                    general_location,
                    f"{suburb}, {state_code}",
                    suburb
                ]
            },
            "location": {
                "address": formatted_address,
                "street": street_full,
                "suburb": suburb,
                "state": state_code,
                "postcode": postcode,
                "country": country,
                "country_code": country_code,
                "lat": lat,
                "lon": lon,
                "radius_m": 250
            },
            "hardware": {
                "stalls": stalls,
                "max_power_kw": max_kw,
                "tier": tier
            },
            "compatibility": {
                "open_to_non_tesla": open_to_non_tesla,
                "tesla_only": not open_to_non_tesla,
                "connector_types": ["CCS2"] if open_to_non_tesla else ["CCS2 (Tesla Only)"]
            },
            "access": {
                "hours": hours_str
            },
            "tariffs": {
                "currency": "AUD" if country_code == "AU" else "USD",
                "idle_fee_per_min": idle_fee,
                "congestion_fee_per_min": congestion_fee,
                "has_tou_pricing": bool(tesla_rate_schedules),
                "tesla_members": {
                    "pricing_model": "time_of_use" if tesla_rate_schedules else "flat",
                    "rate_schedules": tesla_rate_schedules
                },
                "non_tesla": {
                    "supported": open_to_non_tesla,
                    "pricing_model": "time_of_use" if non_tesla_rate_schedules else ("flat" if open_to_non_tesla else "none"),
                    "rate_schedules": non_tesla_rate_schedules
                }
            },
            "tessie_cost_config": {
                "currency": "AUD" if country_code == "AU" else "USD",
                "pricing_model": "time_of_use" if tessie_cost_schedules else "flat",
                "per_kwh_flat": 0.0,
                "per_minute": 0.0,
                "per_session": 0.0,
                "idle_fee_per_min": idle_fee,
                "congestion_fee_per_min": congestion_fee,
                "rate_schedules": tessie_cost_schedules
            },
            "first_seen": now_utc,
            "last_updated": now_utc,
            "last_verified": now_utc,
            "valid_from": now_utc
        }

        station_key = station_name
        return station_key, record

    def scrape_station_details(self, url_or_id: str, charger_type: str = "supercharger", page=None) -> tuple:
        """Scrapes full technical hardware, Time-of-Use pricebooks, and GPS coordinates for a station."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print(f"{C_RED}❌ Playwright is not installed in the active virtual environment.{C_RESET}")
            sys.exit(1)

        # Resolve target URL
        if url_or_id.startswith("http"):
            target_url = url_or_id
        else:
            type_slug = "supercharger" if charger_type == "supercharger" else "charger"
            target_url = f"https://www.tesla.com/en_AU/findus/location/{type_slug}/{url_or_id}"

        print(f"\n{C_CYAN}⚡ Deep-dive scraping station details with Playwright WebKit...{C_RESET}")
        print(f"   {C_DIM}{target_url}{C_RESET}\n")

        if page is not None:
            captured_api_data, next_data_payload = self._scrape_page_payload(page, target_url)
            return self._parse_scraped_data(target_url, captured_api_data, next_data_payload, charger_type=charger_type)

        with sync_playwright() as p:
            browser = p.webkit.launch(headless=self.headless)
            context = browser.new_context(
                locale="en-AU",
                timezone_id="Australia/Sydney",
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
            )
            page_inst = context.new_page()
            captured_api_data, next_data_payload = self._scrape_page_payload(page_inst, target_url)
            browser.close()

        return self._parse_scraped_data(target_url, captured_api_data, next_data_payload, charger_type=charger_type)

    def load_active_registries(self) -> tuple:
        """Loads in-memory dictionaries of superchargers.json and destination_chargers.json."""
        sc_reg = {}
        if os.path.isfile(self.superchargers_path):
            try:
                with open(self.superchargers_path, "r", encoding="utf-8") as f:
                    sc_reg = json.load(f)
            except Exception:
                sc_reg = {}
        dc_reg = {}
        if os.path.isfile(self.destination_chargers_path):
            try:
                with open(self.destination_chargers_path, "r", encoding="utf-8") as f:
                    dc_reg = json.load(f)
            except Exception:
                dc_reg = {}
        return sc_reg, dc_reg

    def get_station_record(self, station_or_query, sc_reg: dict = None, dc_reg: dict = None) -> tuple:
        """
        Finds a station in the local JSON registry (superchargers.json or destination_chargers.json).
        Returns: (station_key, station_data, charger_type) if found, else (None, None, None).
        Accepts a station dict (from fetch_station_list), station name, URL, short_name, slug, or ID.
        """
        if sc_reg is None or dc_reg is None:
            sc_reg, dc_reg = self.load_active_registries()

        if isinstance(station_or_query, dict):
            title = station_or_query.get("title", "")
            short_name = station_or_query.get("short_name", "")
            slug = str(station_or_query.get("slug", "")).lower()
            url = station_or_query.get("url", "")
            st_type = station_or_query.get("type", "supercharger")
            reg = sc_reg if st_type == "supercharger" else dc_reg
            other_reg = dc_reg if st_type == "supercharger" else sc_reg
            other_type = "destination_charger" if st_type == "supercharger" else "supercharger"

            for cur_reg, cur_type in [(reg, st_type), (other_reg, other_type)]:
                # 1. Exact title key match
                if title in cur_reg:
                    return title, cur_reg[title], cur_type
                if f"Tesla Supercharger - {title}" in cur_reg:
                    return f"Tesla Supercharger - {title}", cur_reg[f"Tesla Supercharger - {title}"], cur_type
                # 2. Match by short_name / slug / url / keywords
                title_lower = title.lower()
                clean_title_norm = clean_station_short_name(title).lower()
                for k, entry in cur_reg.items():
                    meta = entry.get("tesla_metadata", {})
                    if short_name and meta.get("short_name") == short_name:
                        return k, entry, cur_type
                    if clean_title_norm and clean_station_short_name(meta.get("short_name", "")).lower() == clean_title_norm:
                        return k, entry, cur_type
                    findus_url = meta.get("findus_url", "")
                    if url and findus_url == url:
                        return k, entry, cur_type
                    if slug and slug in findus_url.lower():
                        return k, entry, cur_type
                    # Keywords match
                    keywords = [kw.lower() for kw in meta.get("keywords", [])]
                    if title_lower in keywords:
                        return k, entry, cur_type
            return None, None, None

        clean_q = str(station_or_query).strip()
        if not clean_q:
            return None, None, None

        # Check superchargers first, then destination chargers
        for cur_reg, cur_type in [(sc_reg, "supercharger"), (dc_reg, "destination_charger")]:
            # 1. Exact key match
            if clean_q in cur_reg:
                return clean_q, cur_reg[clean_q], cur_type
            if f"Tesla Supercharger - {clean_q}" in cur_reg:
                return f"Tesla Supercharger - {clean_q}", cur_reg[f"Tesla Supercharger - {clean_q}"], cur_type

            # 2. Normalized short_name match
            q_slug = clean_station_short_name(clean_q).lower()
            for k, entry in cur_reg.items():
                meta = entry.get("tesla_metadata", {})
                if meta.get("short_name", "").lower() == q_slug:
                    return k, entry, cur_type
                findus_url = meta.get("findus_url", "").lower()
                if clean_q.lower() in findus_url:
                    return k, entry, cur_type

            # 3. Substring match across key, name, location, or suburb
            clean_lower = clean_q.lower()
            for k, entry in cur_reg.items():
                meta = entry.get("tesla_metadata", {})
                loc = entry.get("location", {})
                name = meta.get("name", "").lower()
                gen_loc = meta.get("general_location", "").lower()
                suburb = loc.get("suburb", "").lower()
                if (clean_lower in k.lower() or 
                    clean_lower in name or 
                    clean_lower in gen_loc or 
                    clean_lower in suburb):
                    return k, entry, cur_type

        return None, None, None

    def get_station_status(self, station: dict, sc_reg: dict = None, dc_reg: dict = None, threshold_days: int = 90) -> str:
        """
        Determines the registry status of a station:
        - 'UP_TO_DATE' (Green): Present in JSON registry and verified within threshold_days (<= 90 days / 3 months).
        - 'STALE' (Blue): Present in JSON registry but last verification is older than threshold_days (> 90 days).
        - 'NOT_IN_JSON' (Orange): Not present in JSON registry.
        """
        if sc_reg is None or dc_reg is None:
            sc_reg, dc_reg = self.load_active_registries()

        _, matched_entry, _ = self.get_station_record(station, sc_reg=sc_reg, dc_reg=dc_reg)

        if not matched_entry:
            return "NOT_IN_JSON"

        # Check last_verified timestamp
        ver_str = matched_entry.get("last_verified") or matched_entry.get("last_updated") or matched_entry.get("first_seen")
        if not ver_str:
            return "STALE"

        try:
            dt = datetime.fromisoformat(ver_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            diff_days = (now - dt).days
            if diff_days <= threshold_days:
                return "UP_TO_DATE"
            else:
                return "STALE"
        except Exception:
            return "STALE"

    def scrape_all_stations(self, stations: list, sync_external: bool = False, force: bool = False, pacing_delay: float = 0.5, timeout_sec: int = 35, max_retries: int = 3, subset_filter: str = None):
        """Batch scrapes and updates stations with adaptive pacing, backoffs, retries, and granular change reporting."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print(f"{C_RED}❌ Playwright is not installed in the active virtual environment.{C_RESET}")
            sys.exit(1)

        sc_reg, dc_reg = self.load_active_registries()

        # Apply subset filter if requested ('new' or 'stale')
        target_stations = list(stations)
        if subset_filter == "new":
            target_stations = [s for s in target_stations if self.get_station_status(s, sc_reg=sc_reg, dc_reg=dc_reg) == "NOT_IN_JSON"]
        elif subset_filter == "stale":
            target_stations = [s for s in target_stations if self.get_station_status(s, sc_reg=sc_reg, dc_reg=dc_reg) == "STALE"]

        total = len(target_stations)
        if total == 0:
            print(f"{C_YELLOW}No stations match the selected batch criteria.{C_RESET}")
            return

        filter_label = f" ({subset_filter.upper()} ONLY)" if subset_filter else ""
        print(f"\n{C_BOLD}{'='*80}{C_RESET}")
        print(f"  ⚡ {C_CYAN}{C_BOLD}STARTING BATCH SCRAPER FOR {total} STATIONS{filter_label}{C_RESET}")
        print(f"  {C_DIM}Pacing: {pacing_delay:.1f}s | Timeout: {timeout_sec}s | Max Retries: {max_retries}{C_RESET}")
        print(f"{C_BOLD}{'='*80}{C_RESET}\n")

        stats = {
            "CREATED": 0,
            "ARCHIVED": 0,
            "VERIFIED": 0,
            "ERROR": 0
        }
        failed_stations = []
        consecutive_failures = 0
        t0 = time.time()

        with sync_playwright() as p:
            browser = p.webkit.launch(headless=self.headless)
            context = browser.new_context(
                locale="en-AU",
                timezone_id="Australia/Sydney",
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
            )
            page = context.new_page()

            for idx, st in enumerate(target_stations, 1):
                st_title = st.get("title", "")
                st_type = st.get("type", "supercharger")
                st_url = st.get("url", "")
                
                status = self.get_station_status(st, sc_reg=sc_reg, dc_reg=dc_reg)
                status_color = C_GREEN if status == "UP_TO_DATE" else (C_BLUE if status == "STALE" else C_ORANGE)
                status_tag = f"[{status_color}{status}{C_RESET}]"

                print(f"[{idx:3d}/{total:3d}] ⚡ Scraping {st_title} {status_tag}...")

                try:
                    captured_api_data, next_data_payload = self._scrape_page_payload(
                        page, st_url, timeout_ms=timeout_sec * 1000, max_retries=max_retries
                    )
                    station_key, record = self._parse_scraped_data(st_url, captured_api_data, next_data_payload, charger_type=st_type)
                    if record:
                        res_status = self.update_registry(station_key, record, sync_external=False)
                        stats[res_status] = stats.get(res_status, 0) + 1
                        if st_type == "supercharger":
                            sc_reg[station_key] = record
                        else:
                            dc_reg[station_key] = record
                        consecutive_failures = 0
                    else:
                        print(f"  {C_RED}❌ Failed parsing record for {st_title}{C_RESET}")
                        failed_stations.append(st_title)
                        stats["ERROR"] = stats.get("ERROR", 0) + 1
                        consecutive_failures += 1
                except Exception as e:
                    print(f"  {C_RED}❌ Error scraping {st_title}: {e}{C_RESET}")
                    failed_stations.append(st_title)
                    stats["ERROR"] = stats.get("ERROR", 0) + 1
                    consecutive_failures += 1

                # Circuit breaker cooldown if 3 consecutive failures occur
                if consecutive_failures >= 3:
                    print(f"\n  {C_YELLOW}⚠ 3 consecutive failures encountered. Pausing 10s cooldown before continuing...{C_RESET}\n")
                    time.sleep(10)
                    consecutive_failures = 0

                # Adaptive pacing delay between requests (with minor jitter)
                if idx < total and pacing_delay > 0:
                    time.sleep(pacing_delay + random.uniform(0.1, 0.3))

            browser.close()

        elapsed = time.time() - t0
        processed_ok = stats["CREATED"] + stats["ARCHIVED"] + stats["VERIFIED"]
        print(f"\n{C_BOLD}{'='*80}{C_RESET}")
        print(f"  ⚡ {C_BOLD}BATCH SCRAPE & RE-VERIFICATION SUMMARY{C_RESET}")
        print(f"{C_BOLD}{'='*80}{C_RESET}")
        print(f"  ⏱️  Total Duration:      {elapsed:.1f}s ({total} stations evaluated)")
        print(f"  🟢 Newly Created:       {stats['CREATED']} (added fresh to JSON registry)")
        print(f"  🔵 Updated & Archived:  {stats['ARCHIVED']} (pricing/hardware changed, old rate card archived)")
        print(f"  ⚪ Verified Unchanged:  {stats['VERIFIED']} (re-verified without changes)")
        if stats["ERROR"] > 0:
            print(f"  🔴 Failed / Errors:     {stats['ERROR']}")
            print(f"     Failed stations:     {', '.join(failed_stations[:10])}{' ...' if len(failed_stations) > 10 else ''}")
        print(f"{C_BOLD}{'='*80}{C_RESET}\n")

        if sync_external:
            ext_drives = find_mounted_tesla_volumes()
            if ext_drives:
                print(f"{C_CYAN}🔄 Syncing updated registries across {len(ext_drives)} mounted TESLADRIVE volume(s)...{C_RESET}")
                for ext_drive in ext_drives:
                    try:
                        for reg_file in ["superchargers.json", "superchargers_archived.json", "charging.json", "charging_archived.json"]:
                            src = os.path.join(self.repo_root, "Tessie", reg_file)
                            if os.path.isfile(src):
                                dst = os.path.join(ext_drive, "Tessie", reg_file)
                                os.makedirs(os.path.dirname(dst), exist_ok=True)
                                shutil.copy2(src, dst)
                        print(f"  {C_GREEN}✔ Synced to:{C_RESET} {ext_drive}")
                    except Exception as e:
                        print(f"  {C_RED}❌ Failed syncing to {ext_drive}:{C_RESET} {e}")
            else:
                print(f"{C_YELLOW}⚠ No mounted TESLADRIVE volumes detected under /Volumes. Registry saved locally.{C_RESET}")

    def _detect_record_changes(self, existing: dict, new: dict) -> bool:
        """Detects whether hardware, accessibility, or pricing/tariffs differ between existing and new records."""
        if not existing or not new:
            return False

        # Compare hardware
        ex_hw = existing.get("hardware", {})
        new_hw = new.get("hardware", {})
        if (ex_hw.get("stalls") != new_hw.get("stalls") or 
            ex_hw.get("max_power_kw") != new_hw.get("max_power_kw") or
            ex_hw.get("tier") != new_hw.get("tier")):
            return True

        # Compare compatibility & access
        ex_comp = existing.get("compatibility", {})
        new_comp = new.get("compatibility", {})
        if ex_comp.get("open_to_non_tesla") != new_comp.get("open_to_non_tesla"):
            return True

        ex_acc = existing.get("access", {})
        new_acc = new.get("access", {})
        if ex_acc.get("hours") != new_acc.get("hours"):
            return True

        # Extract normalized tariffs from existing
        ex_tariffs = existing.get("tariffs", {})
        ex_t_scheds = ex_tariffs.get("tesla_members", {}).get("rate_schedules", [])
        if not ex_t_scheds and "tessie_cost_config" in existing:
            ex_t_scheds = existing.get("tessie_cost_config", {}).get("rate_schedules", [])

        # Extract normalized tariffs from new
        new_tariffs = new.get("tariffs", {})
        new_t_scheds = new_tariffs.get("tesla_members", {}).get("rate_schedules", [])

        def simplify_scheds(scheds):
            return [(s.get("start_time"), s.get("end_time"), round(float(s.get("rate_per_kwh", 0)), 4)) for s in scheds]

        if simplify_scheds(ex_t_scheds) != simplify_scheds(new_t_scheds):
            return True

        # Compare fees
        ex_idle = ex_tariffs.get("idle_fee_per_min") or existing.get("tessie_cost_config", {}).get("idle_fee_per_min", 0)
        new_idle = new_tariffs.get("idle_fee_per_min", 0)
        if round(float(ex_idle or 0), 2) != round(float(new_idle or 0), 2):
            return True

        ex_cong = ex_tariffs.get("congestion_fee_per_min") or existing.get("tessie_cost_config", {}).get("congestion_fee_per_min", 0)
        new_cong = new_tariffs.get("congestion_fee_per_min", 0)
        if round(float(ex_cong or 0), 2) != round(float(new_cong or 0), 2):
            return True

        # Compare non-tesla schedules
        ex_nt_scheds = ex_tariffs.get("non_tesla", {}).get("rate_schedules", [])
        if not ex_nt_scheds and "non_tesla_pricing" in existing:
            ex_nt_scheds = existing.get("non_tesla_pricing", {}).get("rate_schedules", [])
        new_nt_scheds = new_tariffs.get("non_tesla", {}).get("rate_schedules", [])
        if simplify_scheds(ex_nt_scheds) != simplify_scheds(new_nt_scheds):
            return True

        return False

    def display_preview(self, station_key: str, data: dict, from_cache: bool = False):
        """Renders an attractive terminal summary card for a station (cached or live scraped)."""
        meta = data.get("tesla_metadata", {})
        loc = data.get("location", {})
        hw = data.get("hardware", {})
        comp = data.get("compatibility", {})
        tariffs = data.get("tariffs", {})
        tz_name = resolve_location_timezone(state=loc.get("state"), country=loc.get("country"), lat=loc.get("lat"), lon=loc.get("lon"))

        box_width = 86
        title_prefix = "LOCAL JSON REGISTRY" if from_cache else "LIVE SCRAPED CHARGER"
        title_content = f"{title_prefix}: {station_key}"
        if len(title_content) > box_width - 4:
            title_content = title_content[:box_width - 7] + "..."
        pad_total = box_width - 2 - len(title_content)
        pad_l = pad_total // 2
        pad_r = pad_total - pad_l
        header_line = f"║{' ' * pad_l}{title_content}{' ' * pad_r}║"

        print(f"\n╔{'═' * (box_width - 2)}╗")
        print(header_line)
        print(f"╚{'═' * (box_width - 2)}╝\n")

        source_label = f"{C_GREEN}Local JSON Registry (Tessie/superchargers.json){C_RESET}" if from_cache else f"{C_CYAN}Live Scraped (Tesla Find Us WebKit API){C_RESET}"
        print(f"  📂 {C_BOLD}Data Source:{C_RESET}       {source_label}")
        print(f"  📍 {C_BOLD}Station Key:{C_RESET}       {station_key}")
        print(f"  🏷️  {C_BOLD}Short Identifier:{C_RESET} {meta.get('short_name')}")
        print(f"  🏢 {C_BOLD}General Location:{C_RESET} {meta.get('general_location')}")
        print(f"  📮 {C_BOLD}Address:{C_RESET}          {loc.get('address')} ({loc.get('lat')}, {loc.get('lon')})")
        print(f"  🌐 {C_BOLD}Timezone:{C_RESET}         {tz_name}")
        print(f"  🔗 {C_BOLD}Find Us URL:{C_RESET}      {meta.get('findus_url')}")
        print(f"  🔌 {C_BOLD}Hardware:{C_RESET}         {hw.get('stalls')} Stalls | Up to {hw.get('max_power_kw')} kW ({hw.get('tier')})")
        
        non_t_str = f"{C_GREEN}YES (Open to CCS2 EVs){C_RESET}" if comp.get("open_to_non_tesla") else f"{C_RED}NO (Tesla Only){C_RESET}"
        print(f"  🚗 {C_BOLD}Non-Tesla Access:{C_RESET} {non_t_str}")
        print(f"  ⏱️  {C_BOLD}Idle / Congestion:{C_RESET} ${tariffs.get('idle_fee_per_min', 0):.2f}/min Idle | ${tariffs.get('congestion_fee_per_min', 0):.2f}/min Congestion")
        if data.get("valid_from"):
            print(f"  🕒 {C_BOLD}Effective Date:{C_RESET}   {data.get('valid_from')} (Verified: {data.get('last_verified', 'N/A')})")

        curr = tariffs.get("currency", "AUD")
        t_scheds = tariffs.get("tesla_members", {}).get("rate_schedules", [])
        if t_scheds:
            print(f"\n  💰 {C_BOLD}Tesla & Members Time-of-Use Rates ({curr}):{C_RESET}")
            for sch in t_scheds:
                lbl = sch.get("label", "Rate")
                st, et = sch.get("start_time"), sch.get("end_time")
                rate = sch.get("rate_per_kwh")
                print(f"    • {pad_display(lbl, 24)} {st} – {et}: ${rate:.2f}/kWh")

        nt_scheds = tariffs.get("non_tesla", {}).get("rate_schedules", [])
        if nt_scheds:
            print(f"\n  🔌 {C_BOLD}Non-Tesla Time-of-Use Rates ({curr}):{C_RESET}")
            for sch in nt_scheds:
                lbl = sch.get("label", "Non-Tesla Rate")
                st, et = sch.get("start_time"), sch.get("end_time")
                rate = sch.get("rate_per_kwh")
                print(f"    • {pad_display(lbl, 24)} {st} – {et}: ${rate:.2f}/kWh")

        print()

    def update_registry(self, station_key: str, data: dict, sync_external: bool = False) -> str:
        """
        Saves scraped station data into superchargers.json or destination_chargers.json, archives older versions if changed.
        Returns: 'CREATED', 'ARCHIVED', 'VERIFIED', or 'ERROR'.
        """
        is_sc = data.get("tesla_metadata", {}).get("type") == "supercharger"
        target_fpath = self.superchargers_path if is_sc else self.destination_chargers_path
        archive_fpath = self.superchargers_archived_path if is_sc else self.destination_chargers_archived_path
        reg_name = "superchargers.json" if is_sc else "destination_chargers.json"
        arch_name = "superchargers_archived.json" if is_sc else "destination_chargers_archived.json"
        now_utc = get_utc_now_iso()

        # Load active registry
        active_reg = {}
        if os.path.isfile(target_fpath):
            try:
                with open(target_fpath, "r", encoding="utf-8") as f:
                    active_reg = json.load(f)
            except Exception:
                active_reg = {}

        # Load archive registry
        archive_reg = {}
        if os.path.isfile(archive_fpath):
            try:
                with open(archive_fpath, "r", encoding="utf-8") as f:
                    archive_reg = json.load(f)
            except Exception:
                archive_reg = {}

        existing_entry = active_reg.get(station_key)
        new_entry = dict(data)
        changes_detected = False
        result_status = "CREATED"

        if existing_entry:
            orig_first_seen = existing_entry.get("first_seen") or existing_entry.get("created_at") or now_utc
            new_entry["first_seen"] = orig_first_seen

            if self._detect_record_changes(existing_entry, new_entry):
                changes_detected = True
                result_status = "ARCHIVED"
                print(f"  {C_YELLOW}⚡ Detected changes in pricing/hardware for '{station_key}'. Archiving previous version...{C_RESET}")
                archived_record = dict(existing_entry)
                archived_record["archived_at"] = now_utc
                archived_record["valid_to"] = now_utc
                if "valid_from" not in archived_record:
                    archived_record["valid_from"] = orig_first_seen

                if station_key not in archive_reg:
                    archive_reg[station_key] = []
                elif isinstance(archive_reg[station_key], dict):
                    archive_reg[station_key] = [archive_reg[station_key]]

                archive_reg[station_key].append(archived_record)

                new_entry["last_updated"] = now_utc
                new_entry["last_verified"] = now_utc
                new_entry["valid_from"] = now_utc
            else:
                result_status = "VERIFIED"
                new_entry["last_updated"] = existing_entry.get("last_updated", orig_first_seen)
                new_entry["valid_from"] = existing_entry.get("valid_from", orig_first_seen)
                new_entry["last_verified"] = now_utc
        else:
            result_status = "CREATED"
            new_entry["first_seen"] = now_utc
            new_entry["last_updated"] = now_utc
            new_entry["last_verified"] = now_utc
            new_entry["valid_from"] = now_utc

        active_reg[station_key] = new_entry

        # Write active registry
        try:
            with open(target_fpath, "w", encoding="utf-8") as f:
                json.dump(active_reg, f, indent=2, ensure_ascii=False)
            print(f"  {C_GREEN}✔ Saved active registry entry in:{C_RESET} {target_fpath}")
        except Exception as e:
            print(f"  {C_RED}❌ Failed writing {target_fpath}:{C_RESET} {e}")
            return "ERROR"

        # Write archive registry if changes occurred
        if changes_detected:
            try:
                with open(archive_fpath, "w", encoding="utf-8") as f:
                    json.dump(archive_reg, f, indent=2, ensure_ascii=False)
                print(f"  {C_GREEN}✔ Updated historical archive in:{C_RESET} {archive_fpath}")
            except Exception as e:
                print(f"  {C_RED}❌ Failed writing archive {archive_fpath}:{C_RESET} {e}")
                return "ERROR"

        if sync_external:
            ext_drives = find_mounted_tesla_volumes()
            if not ext_drives:
                print(f"  {C_YELLOW}⚠ No mounted TESLADRIVE volumes detected under /Volumes. Skipping external sync.{C_RESET}")
            for ext_drive in ext_drives:
                ext_sc = os.path.join(ext_drive, "Tessie", reg_name)
                ext_arch = os.path.join(ext_drive, "Tessie", arch_name)
                try:
                    os.makedirs(os.path.dirname(ext_sc), exist_ok=True)
                    with open(ext_sc, "w", encoding="utf-8") as f:
                        json.dump(active_reg, f, indent=2, ensure_ascii=False)
                    if changes_detected:
                        with open(ext_arch, "w", encoding="utf-8") as f:
                            json.dump(archive_reg, f, indent=2, ensure_ascii=False)
                    print(f"  {C_GREEN}✔ Synced updated registry & archives to:{C_RESET} {ext_drive}")
                except Exception as e:
                    print(f"  {C_RED}❌ Failed to sync to external drive {ext_drive}:{C_RESET} {e}")

        return result_status

# -----------------------------------------------------------------------------
# Rich Unicode Table Renderer
# -----------------------------------------------------------------------------

def print_charging_stations_table(stations: list, ref_lat: float = None, ref_lon: float = None, ref_label: str = None, 
                                 active_radius: float = None, eval_time_label: str = None, sort_mode: str = None):
    """
    Renders charging stations in a clean Unicode box-drawing table with exact column alignment.
    """
    if not stations:
        print(f"{C_YELLOW}No matching charging stations found.{C_RESET}")
        return

    has_dist = ref_lat is not None and ref_lon is not None
    
    # Calculate column widths dynamically
    max_title_len = max((display_len(s.get("title", "")) for s in stations), default=20)
    title_col_w = max(max_title_len + 2, 26)
    
    max_suburb_len = max((display_len(s.get("location", {}).get("suburb") or s.get("short_name", "")) for s in stations), default=12)
    suburb_col_w = max(max_suburb_len + 2, 19)

    headers = ["#", "Type", "State", "Station Name", "Tier", "Stalls", "Access", "Rate (Now)", "Period / Window"]
    widths = [6, 8, 7, title_col_w, 6, 9, 13, 12, 30]

    if has_dist:
        headers.append("Dist (km)")
        widths.append(11)

    headers.append("Location / Suburb")
    widths.append(suburb_col_w)

    total_inner_w = sum(widths) + len(widths) - 1

    # Header Box Banner
    print(f"\n┌{'─' * total_inner_w}┐")
    title_line = f" ⚡ {C_BOLD}MATCHING CHARGING STATIONS ({len(stations)} found){C_RESET}"
    print(f"│{pad_display(title_line, total_inner_w, 'left')}│")
    
    if ref_label:
        radius_note = f" [within {active_radius:.0f} km]" if (active_radius and active_radius > 0) else ""
        sort_str = f" [Sorted by: {sort_mode}]" if sort_mode else ""
        orig_line = f" 📍 {C_CYAN}Proximity Origin:{C_RESET} {ref_label} ({ref_lat:.4f}, {ref_lon:.4f}){radius_note}{sort_str}"
        print(f"│{pad_display(orig_line, total_inner_w, 'left')}│")
    elif sort_mode:
        sort_line = f" 📊 {C_BOLD}Sort Order:{C_RESET} {sort_mode}"
        print(f"│{pad_display(sort_line, total_inner_w, 'left')}│")

    eval_label = eval_time_label or "Current Local Time"
    time_line = f" ⏰ {C_BOLD}Pricing Evaluation:{C_RESET} {eval_label}"
    print(f"│{pad_display(time_line, total_inner_w, 'left')}│")
    
    legend_line = f" 📊 {C_BOLD}Status Legend:{C_RESET} [{C_GREEN} 1 {C_RESET}] Up to Date (<=3mo)  |  [{C_BLUE} 2 {C_RESET}] Stale (>3mo)  |  [{C_ORANGE} 3 {C_RESET}] Not in JSON"
    print(f"│{pad_display(legend_line, total_inner_w, 'left')}│")
    
    # Table Top Line & Header
    top_b = "├" + "┬".join("─" * w for w in widths) + "┤"
    print(top_b)
    
    h_cells = [pad_display(f"{C_BOLD}{h}{C_RESET}", w, "center") for h, w in zip(headers, widths)]
    print("│" + "│".join(h_cells) + "│")
    
    mid_b = "├" + "┼".join("─" * w for w in widths) + "┤"
    print(mid_b)

    for idx, s in enumerate(stations, 1):
        status = s.get("_status", "NOT_IN_JSON")
        color = C_GREEN if status == "UP_TO_DATE" else (C_BLUE if status == "STALE" else C_ORANGE)
        num_str = f"[{color}{idx:2d}{C_RESET}]"
        t_icon = "🔴 SC" if s.get("type") == "supercharger" else "🔌 DC"

        hw = s.get("hardware", {})
        tier_str = hw.get("tier", "-") or "-"
        stalls_str = f"{hw.get('stalls')} bays" if hw.get("stalls") else "-"
        
        comp = s.get("compatibility", {})
        if comp.get("open_to_non_tesla"):
            access_str = f"{C_GREEN}CCS2 All{C_RESET}"
        elif comp.get("tesla_only") is not None:
            access_str = f"{C_CYAN}Tesla Only{C_RESET}"
        else:
            access_str = "-"

        eff_rate = s.get("_eff_rate")
        eff_lbl = s.get("_eff_label", "Rate")
        eff_win = s.get("_eff_window", "")
        
        if eff_rate is not None:
            rate_str = f"${eff_rate:.2f}/kWh"
            period_str = f"{eff_lbl} ({eff_win})" if eff_win else eff_lbl
        else:
            tariffs = s.get("tariffs", {})
            scheds = tariffs.get("tesla_members", {}).get("rate_schedules", [])
            rates = [float(sc.get("rate_per_kwh", 0)) for sc in scheds if sc.get("rate_per_kwh") is not None]
            if rates:
                min_r, max_r = min(rates), max(rates)
                rate_str = f"${min_r:.2f}" if min_r == max_r else f"${min_r:.2f}-${max_r:.2f}"
                period_str = "Time-of-Use"
            else:
                rate_str = "-"
                period_str = "-"

        suburb_str = s.get("location", {}).get("suburb") or s.get("short_name", "")

        row_cells = [
            pad_display(num_str, widths[0], "center"),
            pad_display(t_icon, widths[1], "center"),
            pad_display(s.get("state", ""), widths[2], "center"),
            pad_display(" " + s.get("title", ""), widths[3], "left"),
            pad_display(tier_str, widths[4], "center"),
            pad_display(stalls_str, widths[5], "center"),
            pad_display(access_str, widths[6], "center"),
            pad_display(rate_str + " ", widths[7], "right"),
            pad_display(" " + period_str, widths[8], "left"),
        ]
        
        if has_dist:
            dist_val = s.get("_distance_km", float("inf"))
            dist_text = f"{dist_val:.1f} km " if dist_val != float("inf") else "-- "
            row_cells.append(pad_display(dist_text, widths[9], "right"))
            row_cells.append(pad_display(f" {C_DIM}{suburb_str}{C_RESET}", widths[10], "left"))
        else:
            row_cells.append(pad_display(f" {C_DIM}{suburb_str}{C_RESET}", widths[9], "left"))

        print("│" + "│".join(row_cells) + "│")

    bot_b = "└" + "┴".join("─" * w for w in widths) + "┘"
    print(bot_b)

# -----------------------------------------------------------------------------
# Interactive Drill-Down Navigation Menu
# -----------------------------------------------------------------------------

def interactive_drilldown(explorer: TeslaChargerExplorer):
    """Provides interactive terminal navigation: Region ➔ Country ➔ Type ➔ State ➔ Station ➔ Scrape."""
    print(f"\n{C_BOLD}{'='*80}{C_RESET}")
    print(f"{C_CYAN}{C_BOLD}               ⚡ TESLA CHARGER HIERARCHICAL EXPLORER ⚡{C_RESET}")
    print(f"{C_BOLD}{'='*80}{C_RESET}\n")

    # Step 1: Select Region
    region_keys = list(REGIONS_MAP.keys())
    print(f"{C_BOLD}Select Geographic Region:{C_RESET}")
    for idx, r_key in enumerate(region_keys, 1):
        r_info = REGIONS_MAP[r_key]
        print(f"  [{C_GREEN}{idx}{C_RESET}] {r_info['name']} ({len(r_info['countries'])} countries)")
    print()

    try:
        r_choice = input(f"Enter region [1-{len(region_keys)}, default: 1 (Asia/Pacific)]: ").strip()
        r_idx = int(r_choice) - 1 if r_choice else 0
        if r_idx < 0 or r_idx >= len(region_keys):
            r_idx = 0
    except (ValueError, KeyboardInterrupt, EOFError):
        print("\nExiting.")
        return

    selected_region = REGIONS_MAP[region_keys[r_idx]]
    countries = selected_region["countries"]

    # Step 2: Select Country
    print(f"\n{C_BOLD}Select Country in {selected_region['name']}:{C_RESET}")
    for idx, c_name in enumerate(countries, 1):
        print(f"  [{C_GREEN}{idx:2d}{C_RESET}] {c_name}")
    print()

    try:
        c_choice = input(f"Enter country [1-{len(countries)}, default: 1 ({countries[0]})]: ").strip()
        c_idx = int(c_choice) - 1 if c_choice else 0
        if c_idx < 0 or c_idx >= len(countries):
            c_idx = 0
    except (ValueError, KeyboardInterrupt, EOFError):
        print("\nExiting.")
        return

    selected_country = countries[c_idx]

    # Step 3: Select Charger Type
    print(f"\n{C_BOLD}Select Infrastructure Type:{C_RESET}")
    print(f"  [{C_GREEN}1{C_RESET}] 🔴 Superchargers (V2/V3/V4 DC Fast Charging)")
    print(f"  [{C_GREEN}2{C_RESET}] 🔌 Destination Charging (Hotels, Resorts, Malls AC)")
    print(f"  [{C_GREEN}3{C_RESET}] ⚡ All Charging Stations")
    print()

    try:
        t_choice = input("Enter option [1-3, default: 1]: ").strip() or "1"
    except (KeyboardInterrupt, EOFError):
        print("\nExiting.")
        return

    types_to_fetch = []
    if t_choice == "2":
        types_to_fetch = ["chargers"]
    elif t_choice == "3":
        types_to_fetch = ["superchargers", "chargers"]
    else:
        types_to_fetch = ["superchargers"]

    # Fetch Station Lists
    all_stations = []
    for c_type in types_to_fetch:
        stations = explorer.fetch_station_list(country=selected_country, charger_type=c_type)
        all_stations.extend(stations)

    if not all_stations:
        print(f"{C_YELLOW}No stations found for {selected_country}.{C_RESET}")
        return

    # Group Stations by State / Region
    grouped = {}
    for st in all_stations:
        state_key = st["state"]
        if state_key not in grouped:
            grouped[state_key] = []
        grouped[state_key].append(st)

    # Load registries for status checking
    sc_reg, dc_reg = explorer.load_active_registries()

    # Include any custom/local registry stations not present in the web list
    matched_registry_keys = set()
    for s in all_stations:
        k, rec, _ = explorer.get_station_record(s, sc_reg=sc_reg, dc_reg=dc_reg)
        if k:
            matched_registry_keys.add(k)

    for reg_dict, reg_type in [(sc_reg, "supercharger"), (dc_reg, "destination_charger")]:
        if (reg_type == "supercharger" and "superchargers" not in types_to_fetch) or \
           (reg_type == "destination_charger" and "chargers" not in types_to_fetch):
            continue
        for k, entry in reg_dict.items():
            if k not in matched_registry_keys:
                meta = entry.get("tesla_metadata", {})
                loc = entry.get("location", {})
                st_entry = {
                    "title": k,
                    "short_name": meta.get("short_name", clean_station_short_name(k)),
                    "state": loc.get("state") or extract_au_state_from_text(k),
                    "country": loc.get("country", selected_country),
                    "type": reg_type,
                    "slug": "",
                    "url": meta.get("findus_url", "")
                }
                all_stations.append(st_entry)

    # Re-group Stations by State / Region
    grouped = {}
    for st in all_stations:
        state_key = st["state"]
        if state_key not in grouped:
            grouped[state_key] = []
        grouped[state_key].append(st)

    # Calculate status counts
    new_count = 0
    stale_count = 0
    uptodate_count = 0
    for st in all_stations:
        st_status = explorer.get_station_status(st, sc_reg=sc_reg, dc_reg=dc_reg)
        if st_status == "NOT_IN_JSON":
            new_count += 1
        elif st_status == "STALE":
            stale_count += 1
        else:
            uptodate_count += 1

    # Display Stations Grouped by State
    print(f"\n{C_BOLD}{'='*80}{C_RESET}")
    print(f"  ⚡ {C_BOLD}{selected_country.upper()} CHARGING STATIONS ({len(all_stations)} total){C_RESET}")
    print(f"  {C_BOLD}📊 Status Breakdown:{C_RESET} [{C_GREEN} Up to Date: {uptodate_count} {C_RESET}] | [{C_BLUE} Stale (>3mo): {stale_count} {C_RESET}] | [{C_ORANGE} Not in JSON: {new_count} {C_RESET}]")
    print(f"{C_BOLD}{'='*80}{C_RESET}\n")

    station_lookup = {}
    st_counter = 1

    max_title_len = max((display_len(s["title"]) for s in all_stations), default=30)
    col_width = max(38, max_title_len + 12)

    sorted_states = sorted(grouped.keys())
    for st_name in sorted_states:
        st_list = grouped[st_name]
        state_header = f"{AU_STATE_MAP.get(st_name, st_name)} ({st_name})" if st_name in AU_STATE_MAP else st_name
        print(f"{C_CYAN}{C_BOLD}📍 {state_header} [{len(st_list)} stations]:{C_RESET}")
        
        for i in range(0, len(st_list), 2):
            left = st_list[i]
            left_status = explorer.get_station_status(left, sc_reg=sc_reg, dc_reg=dc_reg)
            left_color = C_GREEN if left_status == "UP_TO_DATE" else (C_BLUE if left_status == "STALE" else C_ORANGE)
            left_icon = "🔴" if left["type"] == "supercharger" else "🔌"
            left_text = f" [{left_color}{st_counter:3d}{C_RESET}] {left_icon} {left['title']}"
            station_lookup[st_counter] = left
            st_counter += 1

            if i + 1 < len(st_list):
                right = st_list[i+1]
                right_status = explorer.get_station_status(right, sc_reg=sc_reg, dc_reg=dc_reg)
                right_color = C_GREEN if right_status == "UP_TO_DATE" else (C_BLUE if right_status == "STALE" else C_ORANGE)
                right_icon = "🔴" if right["type"] == "supercharger" else "🔌"
                right_text = f" [{right_color}{st_counter:3d}{C_RESET}] {right_icon} {right['title']}"
                station_lookup[st_counter] = right
                st_counter += 1
                print(f"{pad_display(left_text, col_width)} {right_text}")
            else:
                print(f"{left_text}")
        print()

    # Step 5: Select Station to Scrape / Inspect
    print(f"{C_BOLD}Options:{C_RESET}")
    print(f"  • Enter a station number [1-{len(all_stations)}] to inspect details")
    print(f"  • Type {C_GREEN}'all'{C_RESET} to batch scrape and update all {len(all_stations)} stations")
    print(f"  • Type {C_ORANGE}'new'{C_RESET} to scrape only {new_count} stations not in JSON registry")
    print(f"  • Type {C_BLUE}'stale'{C_RESET} to re-verify {stale_count} stale stations (> 90 days)")
    print(f"  • Type {C_CYAN}'near <address>'{C_RESET} (e.g. 'near Ryde', 'near 100 George St') to find closest stations & live rates")
    print(f"  • Press {C_YELLOW}Enter{C_RESET} or Ctrl+C to exit")
    print()

    try:
        pick = input("Select option (number, 'all', 'new', 'stale', or 'near <address>'): ").strip()
        if not pick:
            return

        # Proximity search in interactive drilldown
        if pick.lower().startswith("near ") or pick.lower().startswith("address "):
            addr_q = pick.split(" ", 1)[1].strip()
            geo_lat, geo_lon, geo_label = resolve_reference_coordinates(addr_q, explorer.repo_root)
            if geo_lat is None or geo_lon is None:
                print(f"{C_RED}❌ Could not resolve coordinates for '{addr_q}'.{C_RESET}")
                return

            print(f"\n{C_CYAN}📍 Proximity Origin:{C_RESET} {geo_label} ({geo_lat:.4f}, {geo_lon:.4f})")
            now_dt = datetime.now(ZoneInfo("Australia/Sydney"))
            print(f"⏰ {C_BOLD}Pricing Evaluated At:{C_RESET} {now_dt.strftime('%I:%M %p').lstrip('0')} (Current Local Time)\n")

            # Attach distance and effective rates
            near_list = []
            for st in all_stations:
                st_c = dict(st)
                st_c["_status"] = explorer.get_station_status(st, sc_reg=sc_reg, dc_reg=dc_reg)
                _, cached_data, _ = explorer.get_station_record(st, sc_reg=sc_reg, dc_reg=dc_reg)
                if cached_data:
                    st_c["hardware"] = cached_data.get("hardware", {})
                    st_c["compatibility"] = cached_data.get("compatibility", {})
                    st_c["tariffs"] = cached_data.get("tariffs", {})
                    st_c["location"] = cached_data.get("location", {})
                    c_lat = cached_data.get("location", {}).get("lat")
                    if c_lat is None:
                        c_lat = cached_data.get("lat")
                    c_lon = cached_data.get("location", {}).get("lon")
                    if c_lon is None:
                        c_lon = cached_data.get("lon")
                    if c_lat is not None and c_lon is not None:
                        st_c["_distance_km"] = haversine_distance_km(geo_lat, geo_lon, c_lat, c_lon)
                    else:
                        st_c["_distance_km"] = float("inf")
                else:
                    st_c["_distance_km"] = float("inf")
                near_list.append(st_c)

            near_list = [s for s in near_list if s.get("_distance_km", float("inf")) <= 50.0]
            near_list.sort(key=lambda s: s.get("_distance_km", float("inf")))
            top_near = near_list[:20]

            for s in top_near:
                eff_rate, eff_lbl, eff_win, _ = get_effective_rate_at_time(s, now_dt)
                s["_eff_rate"] = eff_rate
                s["_eff_label"] = eff_lbl
                s["_eff_window"] = eff_win

            print_charging_stations_table(
                top_near,
                ref_lat=geo_lat,
                ref_lon=geo_lon,
                ref_label=geo_label,
                active_radius=50.0,
                eval_time_label="Current Local Time",
                sort_mode="Distance (Closest First)"
            )
            return

        if pick.lower() in ["all", "a"]:
            save_prompt = input(f"Scrape and save all {len(all_stations)} stations into registry? [y/N]: ").strip().lower()
            if save_prompt == "y":
                explorer.scrape_all_stations(all_stations, sync_external=True)
            return
        elif pick.lower() in ["new", "n"]:
            if new_count == 0:
                print(f"{C_GREEN}All stations are already present in JSON registry.{C_RESET}")
                return
            save_prompt = input(f"Scrape and save {new_count} new stations into registry? [y/N]: ").strip().lower()
            if save_prompt == "y":
                explorer.scrape_all_stations(all_stations, sync_external=True, subset_filter="new")
            return
        elif pick.lower() in ["stale", "s"]:
            if stale_count == 0:
                print(f"{C_GREEN}All registered stations are currently up to date (<= 90 days).{C_RESET}")
                return
            save_prompt = input(f"Re-verify and update {stale_count} stale stations in registry? [y/N]: ").strip().lower()
            if save_prompt == "y":
                explorer.scrape_all_stations(all_stations, sync_external=True, subset_filter="stale")
            return

        if pick.isdigit() and int(pick) in station_lookup:
            target_st = station_lookup[int(pick)]
            cached_key, cached_record, _ = explorer.get_station_record(target_st, sc_reg=sc_reg, dc_reg=dc_reg)
            if cached_record:
                explorer.display_preview(cached_key, cached_record, from_cache=True)
                rescrape_prompt = input("Fetch fresh live pricing from Tesla Find Us? [y/N]: ").strip().lower()
                if rescrape_prompt != "y":
                    return

            key, record = explorer.scrape_station_details(target_st["url"], charger_type=target_st["type"])
            if record:
                explorer.display_preview(key, record, from_cache=False)
                save_prompt = input("Save & sync this station to local registry? [y/N]: ").strip().lower()
                if save_prompt == "y":
                    explorer.update_registry(key, record, sync_external=True)
        else:
            print(f"{C_YELLOW}Invalid selection.{C_RESET}")
    except (KeyboardInterrupt, EOFError):
        print("\nExiting.")

# -----------------------------------------------------------------------------
# CLI Entrypoint & Argument Handling
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="""
⚡ Tesla Charger Discovery, Exploration, Query & Scraper Engine ⚡
================================================================
Explore, list, search, filter, and scrape technical hardware specs, Time-of-Use
(TOU) rate cards, and accessibility across Tesla Superchargers and Destination Chargers.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Query & Proximity Examples:
  # 1. List chargers ordered by distance from an arbitrary street address with live pricing:
  ./Tools/find_tesla_chargers.py --address "100 George St, Sydney" --limit 10 --list
  ./Tools/find_tesla_chargers.py --near "14 Parraweena Rd, Miranda" --limit 10 --list
  ./Tools/find_tesla_chargers.py --near "Ryde" --radius-km 25 --list

  # 2. Evaluate pricing at a specific time of day (e.g. 2:00 PM Peak or 11:30 PM Night):
  ./Tools/find_tesla_chargers.py --near "Home" --time "14:00" --list
  ./Tools/find_tesla_chargers.py --near "Home" --time "23:30" --list

  # 3. Sort chargers by lowest price near your location (defaults to 50km radius):
  ./Tools/find_tesla_chargers.py --near "Home" --sort price --limit 10 --list
  ./Tools/find_tesla_chargers.py --near "Home" --sort price --radius-km 100 --list

  # 4. Filter all V3 Superchargers:
  ./Tools/find_tesla_chargers.py --sc --tier V3 --list
  ./Tools/find_tesla_chargers.py --sc --filter "tier == 'V3' and rate <= 0.50" --list

  # 5. List stations open to Non-Tesla CCS2 vehicles:
  ./Tools/find_tesla_chargers.py --sc --non-tesla --list

  # 6. Auto-discover location from vehicle GPS telemetry / IP:
  ./Tools/find_tesla_chargers.py --sc --gps --list

  # 7. Fast offline inspection from local JSON cache:
  ./Tools/find_tesla_chargers.py --inspect "Macquarie"
  ./Tools/find_tesla_chargers.py --inspect "Miranda" --live

  # 8. Batch scrape new or stale stations:
  ./Tools/find_tesla_chargers.py --sc --new --all --sync
  ./Tools/find_tesla_chargers.py --sc --stale --all --sync
"""
    )
    
    # Discovery & Geographic Flags
    parser.add_argument("--region", help="Geographic Region (e.g. 'Asia/Pacific', 'North America', 'Europe')")
    parser.add_argument("--country", default="Australia", help="Country name (default: 'Australia')")
    parser.add_argument("--state", help="State / Territory code (e.g. 'NSW', 'VIC', 'QLD', 'WA', 'SA', 'TAS', 'ACT', 'NT')")
    parser.add_argument("--suburb", help="Filter by suburb name")
    parser.add_argument("-q", "--search", "--query", help="Search term across station names, locations, and addresses")
    parser.add_argument("--sc", "--superchargers", action="store_true", help="Filter Superchargers only")
    parser.add_argument("--dc", "--destination-chargers", action="store_true", help="Filter Destination Chargers only")
    parser.add_argument("--list", action="store_true", help="List matching charging stations in rich table format")
    
    # Address, GPS & Proximity Flags
    parser.add_argument("-a", "--address", "--near", dest="near_address", help="Reference address, suburb, place name, or shortcut (e.g. 'Home', '100 George St, Sydney', 'Ryde')")
    parser.add_argument("--gps", action="store_true", help="Auto-discover current GPS location from vehicle telemetry / IP / Home")
    parser.add_argument("--coords", help="Direct reference coordinates in 'lat,lon' format (e.g. '-33.806,151.079')")
    parser.add_argument("--radius-km", type=float, help="Filter stations within specified radius in kilometers (default: 50km when sorting by price near a location; 0 for nationwide)")
    parser.add_argument("-t", "--time", help="Evaluation time for Time-of-Use pricing (e.g. '14:30', '2:00 PM', 'now'). Defaults to current local time.")
    parser.add_argument("-n", "--limit", type=int, help="Maximum number of stations to display")
    parser.add_argument("--sort", choices=["dist", "price", "power", "stalls"], help="Sort results by distance, price at time, max power, or stall count")

    # Hardware, Accessibility & Tariff Filter Flags
    parser.add_argument("--filter", help="Python filter expression (e.g. \"tier == 'V3' and price <= 0.50 and stalls >= 8\")")
    parser.add_argument("--tier", help="Filter by Supercharger tier (e.g. 'V2', 'V3', 'V4', 'AC')")
    parser.add_argument("--tesla-only", action="store_true", help="Filter Tesla-only charging stations")
    parser.add_argument("--non-tesla", "--open-to-all", action="store_true", help="Filter stations open to Non-Tesla CCS2 vehicles")
    parser.add_argument("--max-price", type=float, help="Filter stations with member rate <= specified price/kWh at target time")
    parser.add_argument("--min-stalls", type=int, help="Filter stations with stall count >= specified count")
    parser.add_argument("--status", choices=["UP_TO_DATE", "STALE", "NOT_IN_JSON", "up_to_date", "stale", "not_in_json"], help="Filter by registry status")
    parser.add_argument("--new", action="store_true", help="Filter only stations not present in JSON registry")
    parser.add_argument("--stale", action="store_true", help="Filter only stale stations (> 90 days since verification)")

    # Inspection, Scraping & Persistence Flags
    parser.add_argument("--inspect", "--scrape", help="Inspect station details (defaults to fast offline JSON; use --live for web scrape)")
    parser.add_argument("--url", help="Direct Tesla Find Us location URL to inspect or scrape")
    parser.add_argument("--live", "--force", action="store_true", help="Force live WebKit scrape from Tesla Find Us instead of cached JSON")
    parser.add_argument("--all", action="store_true", help="Batch scrape and update all matching stations")
    parser.add_argument("--delay", type=float, default=0.5, help="Pacing delay in seconds between station requests in batch mode (default: 0.5)")
    parser.add_argument("--timeout", type=int, default=35, help="Network timeout in seconds per page load (default: 35)")
    parser.add_argument("--retries", type=int, default=3, help="Max retry attempts per station with exponential backoff (default: 3)")
    parser.add_argument("--all-types", action="store_true", help="Include both Superchargers and Destination Chargers")
    parser.add_argument("--save", "--update", action="store_true", help="Save / update scraped entry in superchargers.json or destination_chargers.json")
    parser.add_argument("--sync", action="store_true", help="Sync updated registry across all mounted TESLADRIVE external volumes")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--headful", "--visible", action="store_true", help="Run browser in visible mode (default is headless)")

    args = parser.parse_args()
    explorer = TeslaChargerExplorer(headless=not args.headful)

    # 1. Inspection / Direct Scrape by Query, Name, URL, or ID
    target_inspect = args.inspect or args.url
    if target_inspect:
        target_type = "destination_charger" if args.dc else "supercharger"
        sc_reg, dc_reg = explorer.load_active_registries()

        if not args.live:
            cached_key, cached_record, cached_type = explorer.get_station_record(target_inspect, sc_reg=sc_reg, dc_reg=dc_reg)
            if cached_record:
                if args.json:
                    print(json.dumps({cached_key: cached_record}, indent=2, ensure_ascii=False))
                    return
                explorer.display_preview(cached_key, cached_record, from_cache=True)
                print(f"{C_DIM}To re-scrape live pricing: ./Tools/find_tesla_chargers.py --inspect '{target_inspect}' --live [--save] [--sync]{C_RESET}\n")
                return

        station_key, data = explorer.scrape_station_details(target_inspect, charger_type=target_type)
        if not data:
            sys.exit(1)
        if args.json:
            print(json.dumps({station_key: data}, indent=2, ensure_ascii=False))
            return
        explorer.display_preview(station_key, data, from_cache=False)
        if args.save:
            explorer.update_registry(station_key, data, sync_external=args.sync)
        else:
            print(f"{C_DIM}Run with '--save' or '--update' to write changes into registry.{C_RESET}\n")
        return

    # 2. Location / GPS Reference Resolution
    ref_lat, ref_lon, ref_label = None, None, None
    if args.gps:
        ref_lat, ref_lon, ref_label = get_current_gps_location(explorer.repo_root)
    elif args.coords:
        ref_lat, ref_lon, ref_label = resolve_reference_coordinates(args.coords, explorer.repo_root)
    elif args.near_address:
        ref_lat, ref_lon, ref_label = resolve_reference_coordinates(args.near_address, explorer.repo_root)

    # 3. Command-Line Listing / Search / Batch Scrape / Filtering
    is_list_or_filter = (
        args.list or args.search or args.state or args.suburb or 
        args.sc or args.dc or args.all_types or args.all or args.filter or args.tier or 
        args.tesla_only or args.non_tesla or args.max_price is not None or 
        args.min_stalls is not None or args.status or args.new or args.stale or
        args.gps or args.near_address or args.coords or args.radius_km is not None or
        args.time is not None or args.limit is not None or args.sort is not None
    )

    if is_list_or_filter:
        charger_types = []
        if args.sc and not args.dc:
            charger_types = ["superchargers"]
        elif args.dc and not args.sc:
            charger_types = ["chargers"]
        elif args.all_types or (args.sc and args.dc):
            charger_types = ["superchargers", "chargers"]
        else:
            # Default to Superchargers only
            charger_types = ["superchargers"]

        all_stations = []
        for c_type in charger_types:
            st_list = explorer.fetch_station_list(country=args.country, charger_type=c_type)
            all_stations.extend(st_list)

        sc_reg, dc_reg = explorer.load_active_registries()

        # Include any custom/local registry stations not present in the web list
        matched_registry_keys = set()
        for s in all_stations:
            k, rec, _ = explorer.get_station_record(s, sc_reg=sc_reg, dc_reg=dc_reg)
            if k:
                matched_registry_keys.add(k)

        for reg_dict, reg_type in [(sc_reg, "supercharger"), (dc_reg, "destination_charger")]:
            if (reg_type == "supercharger" and "superchargers" not in charger_types) or \
               (reg_type == "destination_charger" and "chargers" not in charger_types):
                continue
            for k, entry in reg_dict.items():
                if k not in matched_registry_keys:
                    meta = entry.get("tesla_metadata", {})
                    loc = entry.get("location", {})
                    st_entry = {
                        "title": k,
                        "short_name": meta.get("short_name", clean_station_short_name(k)),
                        "state": loc.get("state") or extract_au_state_from_text(k),
                        "country": loc.get("country", "Australia"),
                        "type": reg_type,
                        "slug": "",
                        "url": meta.get("findus_url", "")
                    }
                    all_stations.append(st_entry)

        # Attach registry status, cached hardware, proximity data, and effective rates
        enriched_stations = []
        for s in all_stations:
            st_copy = dict(s)
            st_status = explorer.get_station_status(s, sc_reg=sc_reg, dc_reg=dc_reg)
            st_copy["_status"] = st_status

            _, cached_data, _ = explorer.get_station_record(s, sc_reg=sc_reg, dc_reg=dc_reg)
            if cached_data:
                st_copy["hardware"] = cached_data.get("hardware", {})
                st_copy["compatibility"] = cached_data.get("compatibility", {})
                st_copy["tariffs"] = cached_data.get("tariffs", {})
                st_copy["location"] = cached_data.get("location", {})
                st_copy["tesla_metadata"] = cached_data.get("tesla_metadata", {})

                # Compute distance if reference coordinates available
                c_lat = cached_data.get("location", {}).get("lat")
                if c_lat is None:
                    c_lat = cached_data.get("lat")
                c_lon = cached_data.get("location", {}).get("lon")
                if c_lon is None:
                    c_lon = cached_data.get("lon")

                if ref_lat is not None and ref_lon is not None and c_lat is not None and c_lon is not None:
                    st_copy["_distance_km"] = haversine_distance_km(ref_lat, ref_lon, c_lat, c_lon)
                else:
                    st_copy["_distance_km"] = float("inf")
            else:
                st_copy["_distance_km"] = float("inf")

            # Calculate effective rate at target time
            eff_rate, eff_lbl, eff_win, loc_time = get_effective_rate_at_time(
                st_copy, target_time_input=args.time, is_non_tesla=args.non_tesla
            )
            st_copy["_eff_rate"] = eff_rate
            st_copy["_eff_label"] = eff_lbl
            st_copy["_eff_window"] = eff_win
            st_copy["_eval_time_display"] = loc_time

            enriched_stations.append(st_copy)

        # Apply Filters
        filtered = enriched_stations

        if args.state:
            target_st = args.state.upper()
            filtered = [s for s in filtered if s["state"] == target_st]

        if args.suburb:
            sub_lower = args.suburb.lower()
            filtered = [s for s in filtered if sub_lower in s["title"].lower() or sub_lower in s.get("location", {}).get("suburb", "").lower()]

        if args.search:
            q_lower = args.search.lower()
            filtered = [s for s in filtered if (
                q_lower in s["title"].lower() or 
                q_lower in s["short_name"].lower() or
                q_lower in s.get("location", {}).get("address", "").lower()
            )]

        if args.status:
            stat_upper = args.status.upper()
            filtered = [s for s in filtered if s.get("_status") == stat_upper]

        if args.new:
            filtered = [s for s in filtered if s.get("_status") == "NOT_IN_JSON"]

        if args.stale:
            filtered = [s for s in filtered if s.get("_status") == "STALE"]

        if args.tier:
            t_clean = args.tier.upper()
            filtered = [s for s in filtered if s.get("hardware", {}).get("tier", "").upper() == t_clean]

        if args.tesla_only:
            filtered = [s for s in filtered if s.get("compatibility", {}).get("tesla_only", False) or not s.get("compatibility", {}).get("open_to_non_tesla", False)]

        if args.non_tesla:
            filtered = [s for s in filtered if s.get("compatibility", {}).get("open_to_non_tesla", False)]

        if args.max_price is not None:
            filtered = [s for s in filtered if s.get("_eff_rate") is not None and s.get("_eff_rate") <= args.max_price]

        if args.min_stalls is not None:
            filtered = [s for s in filtered if int(s.get("hardware", {}).get("stalls", 0) or 0) >= args.min_stalls]

        # Determine proximity radius filter (default to 50km when searching near an address/place unless explicitly overridden)
        active_radius = args.radius_km
        if active_radius is None and ref_lat is not None and ref_lon is not None:
            active_radius = 50.0

        if active_radius is not None and active_radius > 0 and ref_lat is not None and ref_lon is not None:
            filtered = [s for s in filtered if s.get("_distance_km", float("inf")) <= active_radius]

        if args.filter:
            filtered = [s for s in filtered if evaluate_station_filter(s, args.filter, target_time=args.time)]

        # Sorting Logic
        if args.sort == "dist" or (args.sort is None and ref_lat is not None and ref_lon is not None):
            filtered.sort(key=lambda s: s.get("_distance_km", float("inf")))
        elif args.sort == "price":
            # Sort by lowest price first, tiebreak with closest distance
            filtered.sort(key=lambda s: (
                s.get("_eff_rate") if s.get("_eff_rate") is not None else float("inf"),
                s.get("_distance_km", float("inf"))
            ))
        elif args.sort == "power":
            filtered.sort(key=lambda s: (float(s.get("hardware", {}).get("max_power_kw", 0) or 0), -s.get("_distance_km", float("inf"))), reverse=True)
        elif args.sort == "stalls":
            filtered.sort(key=lambda s: (int(s.get("hardware", {}).get("stalls", 0) or 0), -s.get("_distance_km", float("inf"))), reverse=True)

        # Apply limit if requested
        if args.limit and args.limit > 0:
            filtered = filtered[:args.limit]

        # Batch scrape mode
        if args.all:
            subset_mode = "new" if args.new else ("stale" if args.stale else None)
            explorer.scrape_all_stations(
                filtered,
                sync_external=args.sync,
                pacing_delay=args.delay,
                timeout_sec=args.timeout,
                max_retries=args.retries,
                subset_filter=subset_mode
            )
            return

        # Output JSON if requested
        if args.json:
            print(json.dumps(filtered, indent=2, ensure_ascii=False))
            return

        # Determine human-friendly sort mode description
        sort_mode_desc = None
        if args.sort == "dist" or (args.sort is None and ref_lat is not None and ref_lon is not None):
            sort_mode_desc = "Distance (Closest First)"
        elif args.sort == "price":
            sort_mode_desc = "Price (Lowest First, Closest Distance Tie-Break)"
        elif args.sort == "power":
            sort_mode_desc = "Max Power (kW, Highest First)"
        elif args.sort == "stalls":
            sort_mode_desc = "Stall Count (Highest First)"

        eval_time_label = f"Target Time: {args.time}" if args.time else "Current Local Time"

        print_charging_stations_table(
            filtered,
            ref_lat=ref_lat,
            ref_lon=ref_lon,
            ref_label=ref_label,
            active_radius=active_radius,
            eval_time_label=eval_time_label,
            sort_mode=sort_mode_desc
        )

        print(f"\n{C_DIM}To inspect details:     ./Tools/find_tesla_chargers.py --inspect <ID_or_Name_or_URL>{C_RESET}")
        print(f"{C_DIM}To batch update:        ./Tools/find_tesla_chargers.py --sc --new --all --sync{C_RESET}\n")
        return

    # 4. Interactive Mode Fallback
    if sys.stdin.isatty():
        interactive_drilldown(explorer)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()


