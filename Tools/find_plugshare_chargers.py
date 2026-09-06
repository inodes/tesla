#!/usr/bin/env python3
"""
🔌 PlugShare Non-Tesla Charger Discovery, Exploration & Registry Engine 🔌
===========================================================================
Discovers, searches, inspects, and registers non-Tesla public EV chargers
(Chargefox, Evie Networks, BP Pulse, Exploren, JOLT, NRMA, AmpCharge, etc.)
into the dedicated registry Tessie/plugshare_chargers.json using PlugShare's native v3 API.

- 🇦🇺 Country & Regional Exploration: Discover public chargers across Australia & states
- 📍 Nearby / Address Search: Discover chargers near any address, suburb, or GPS coordinates
- 🔎 Text Search: Search stations by name, network, or keyword
- ⚡ Deep Hardware & Gross Pricing: Extracts kW power, connector types, stalls, and gross $/kWh tariffs
- 📊 History Auto-Discovery: Scan charges_master.csv for unregistered charging sessions
- 💾 Registry Integration: Populates Tessie/plugshare_chargers.json (mirrored schema style)
"""

import os
import sys
import re
import csv
import time
import json
import math
import shutil
import random
import signal
import argparse
import unicodedata
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import urllib.request
import urllib.parse
import urllib.error

# Handle SIGPIPE gracefully when piping to head / grep
try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except Exception:
    pass

# -----------------------------------------------------------------------------
# ANSI Color Codes & Unicode Helpers (matching find_tesla_chargers.py)
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

_tools_dir = os.path.dirname(os.path.abspath(__file__))
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

from table_formatter import (
    char_width,
    display_len,
    truncate_display,
    pad_display,
    format_row,
    format_title_line,
    format_box_line,
    clean_station_name,
)

# =============================================================================
# Geodesic & Location Utilities
# =============================================================================

def haversine_distance_km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return float("inf")
    try:
        f_lat1, f_lon1 = float(lat1), float(lon1)
        f_lat2, f_lon2 = float(lat2), float(lon2)
    except (ValueError, TypeError):
        return float("inf")
    R = 6371.0
    dlat = math.radians(f_lat2 - f_lat1)
    dlon = math.radians(f_lon2 - f_lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(f_lat1)) * math.cos(math.radians(f_lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def get_utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def get_current_gps_location(repo_root=None):
    if not repo_root:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    drives_p = os.path.join(repo_root, "Tessie", "drives_master.csv")
    if os.path.isfile(drives_p):
        try:
            with open(drives_p, "r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
                if rows:
                    latest = rows[-1]
                    e_lat, e_lon = latest.get("Ending Latitude"), latest.get("Ending Longitude")
                    e_loc = latest.get("Ending Saved Location") or latest.get("Ending Location") or "Current Vehicle Position"
                    if e_lat and e_lon:
                        return float(e_lat), float(e_lon), f"Vehicle Position ({e_loc})"
        except Exception:
            pass
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
    try:
        req = urllib.request.Request("http://ip-api.com/json/", headers={"User-Agent": "curl/7.88.1"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "success" and data.get("lat") is not None:
                return float(data["lat"]), float(data["lon"]), f"IP Location ({data.get('city', 'Local IP')})"
    except Exception:
        pass
    return -33.8688, 151.2093, "Sydney CBD (Default Fallback)"

_GEOCODE_CACHE = {}

def geocode_address(address_str, country_hint="Australia"):
    if not address_str:
        return None, None, None
    clean = address_str.strip()
    if clean in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[clean]
    try:
        q_str = clean if country_hint.lower() in clean.lower() else f"{clean}, {country_hint}"
        params = urllib.parse.urlencode({"q": q_str, "format": "json", "limit": 1, "email": "glenn@inodes.org"})
        url = f"https://nominatim.openstreetmap.org/search?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "PlugShareChargerExplorer/1.0 (glenn@inodes.org)"})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data:
                first = data[0]
                lat, lon = float(first["lat"]), float(first["lon"])
                display = first.get("display_name", clean)
                parts = display.split(", ")
                short_display = ", ".join(parts[:4]) if len(parts) > 4 else display
                res = (lat, lon, short_display)
                _GEOCODE_CACHE[clean] = res
                return res
    except Exception:
        pass
    try:
        params = urllib.parse.urlencode({"q": clean, "limit": 1})
        url = f"https://photon.komoot.io/api/?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "PlugShareChargerExplorer/1.0"})
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
    return None, None, None

def resolve_reference_coordinates(ref_str, repo_root=None):
    if not ref_str:
        return None, None, None
    clean = ref_str.strip()
    if clean.lower() in ["me", "current", "gps", "auto", "here"]:
        return get_current_gps_location(repo_root)
    coord_m = re.match(r"^([-\d\.]+)\s*,\s*([-\d\.]+)$", clean)
    if coord_m:
        try:
            return float(coord_m.group(1)), float(coord_m.group(2)), f"({coord_m.group(1)}, {coord_m.group(2)})"
        except Exception:
            pass
    if not repo_root:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    places_p = os.path.join(repo_root, "Tessie", "places.json")
    if os.path.isfile(places_p):
        try:
            with open(places_p, "r", encoding="utf-8") as f:
                places = json.load(f)
            for p_name, p_info in places.items():
                if p_name.lower() == clean.lower():
                    return float(p_info.get("lat")), float(p_info.get("lon")), f"{p_name} (places.json)"
            for p_name, p_info in places.items():
                if clean.lower() in p_name.lower():
                    return float(p_info.get("lat")), float(p_info.get("lon")), f"{p_name} (places.json)"
        except Exception:
            pass
    geo_lat, geo_lon, geo_label = geocode_address(clean)
    if geo_lat is not None:
        return geo_lat, geo_lon, geo_label
    return None, None, None

def find_mounted_tesla_volumes(subdir=None):
    """
    No-op retained for call-site compatibility. TESLADRIVE* volumes are
    reserved exclusively for dashcam/TeslaCam media - the PlugShare
    registry is never synced there. Tessie tooling runs directly from
    the repository and iCloud only.
    """
    return []

# =============================================================================
# PlugShare API Client & Network Mapping
# =============================================================================

PLUGSHARE_NETWORKS = {
    48: "Chargefox",
    60: "Evie Networks",
    63: "NRMA",
    69: "JOLT",
    70: "Exploren",
    81: "BP Pulse",
    1292: "AmpCharge",
    1: "ChargePoint",
    57: "EVUp",
    1041: "EV Range",
    35: "Tesla Destination",
    8: "Tesla Supercharger",
    24: "Tesla",
}

PLUGSHARE_CONNECTORS = {
    2: "CHAdeMO",
    3: "CHAdeMO",
    7: "Type 2",
    11: "Tesla (Roadster)",
    12: "Wall (AU/NZ)",
    13: "CHAdeMO",
    14: "Three Phase (5-pin)",
    15: "Caravan Mains (15A)",
    20: "CCS2"
}

AUSTRALIAN_STATES_BOUNDS = {
    "NSW": {"name": "New South Wales", "lat": -32.5, "lon": 149.0, "spanLat": 6.0, "spanLng": 8.0},
    "VIC": {"name": "Victoria", "lat": -37.0, "lon": 144.5, "spanLat": 4.0, "spanLng": 6.0},
    "QLD": {"name": "Queensland", "lat": -22.5, "lon": 144.5, "spanLat": 12.0, "spanLng": 10.0},
    "WA":  {"name": "Western Australia", "lat": -26.0, "lon": 121.0, "spanLat": 14.0, "spanLng": 12.0},
    "SA":  {"name": "South Australia", "lat": -32.0, "lon": 136.0, "spanLat": 8.0, "spanLng": 8.0},
    "TAS": {"name": "Tasmania", "lat": -42.0, "lon": 146.5, "spanLat": 3.0, "spanLng": 3.0},
    "ACT": {"name": "Australian Capital Territory", "lat": -35.3, "lon": 149.1, "spanLat": 0.8, "spanLng": 0.8},
    "NT":  {"name": "Northern Territory", "lat": -19.5, "lon": 133.5, "spanLat": 10.0, "spanLng": 8.0},
}

AUSTRALIAN_SEARCH_REGIONS = [
    ("Sydney Metro & Central Coast", -33.75, 151.10, 1.8, 1.8),
    ("Melbourne Metro & Port Phillip", -37.85, 145.00, 1.8, 1.8),
    ("Brisbane Metro & South East QLD", -27.47, 153.05, 1.8, 1.8),
    ("Gold Coast & Tweed", -28.05, 153.40, 1.0, 1.0),
    ("Sunshine Coast", -26.65, 153.05, 1.0, 1.0),
    ("Perth Metro & Peel", -31.95, 115.85, 1.8, 1.8),
    ("Adelaide Metro & Hills", -34.93, 138.60, 1.5, 1.5),
    ("Canberra & ACT", -35.30, 149.13, 0.8, 0.8),
    ("Hobart & Southern TAS", -42.88, 147.32, 1.0, 1.0),
    ("Launceston & Northern TAS", -41.44, 147.14, 1.0, 1.0),
    ("Newcastle & Hunter Valley", -32.85, 151.60, 1.5, 1.5),
    ("Wollongong & Illawarra", -34.43, 150.88, 1.2, 1.2),
    ("Geelong & Bellarine", -38.15, 144.36, 1.0, 1.0),
    ("Cairns & Far North QLD", -16.92, 145.77, 1.5, 1.5),
    ("Townsville & North QLD", -19.26, 146.81, 1.5, 1.5),
    ("Darwin & Top End", -12.46, 130.84, 1.5, 1.5),
    ("Alice Springs & Red Centre", -23.70, 133.88, 1.5, 1.5),
    ("Albury / Wodonga / Hume", -36.08, 146.92, 1.5, 1.5),
    ("Dubbo & Central West NSW", -32.25, 148.60, 2.0, 2.0),
    ("Ballarat & Western VIC", -37.56, 143.85, 1.5, 1.5),
    ("Bendigo & Central VIC", -36.76, 144.28, 1.5, 1.5),
    ("Toowoomba & Darling Downs", -27.56, 151.95, 1.5, 1.5),
]

class PlugShareClient:
    BASE_URL = "https://api.plugshare.com/v3"
    AUTH_HEADER = "Basic d2ViX3YyOkVOanNuUE54NHhXeHVkODU="

    def __init__(self, headers=None):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Authorization": self.AUTH_HEADER,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.plugshare.com",
            "Referer": "https://www.plugshare.com/map/australia"
        }
        if headers:
            self.headers.update(headers)
        # Set by _get() whenever it returns None, so callers that need to
        # know *why* a request failed (rate-limited vs. genuine 404 vs.
        # network error) can surface it instead of a bare "not found".
        self.last_error = None

    def _get(self, endpoint, params=None, timeout=15, retries=1, backoff=1.5):
        url = f"{self.BASE_URL}{endpoint}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        self.last_error = None
        attempt = 0
        while True:
            req = urllib.request.Request(url, headers=self.headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    self.last_error = "HTTP 404 (not found)"
                    return None
                self.last_error = f"HTTP {e.code}" + (" (likely rate-limited/blocked)" if e.code in (403, 429) else "")
            except urllib.error.URLError as e:
                self.last_error = f"network error: {e.reason}"
            except TimeoutError:
                self.last_error = "request timed out"
            except json.JSONDecodeError:
                self.last_error = "non-JSON response (likely a rate-limit / bot-challenge page)"
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
            attempt += 1
            if attempt > retries:
                return None
            time.sleep(backoff * attempt + random.uniform(0, 0.5))

    def get_location_details(self, location_id):
        """Fetches complete station record including tariffs, hardware, hours, and amenities."""
        return self._get(f"/locations/{location_id}")

    def search_region(self, lat, lon, span_lat=0.1, span_lng=0.1, count=250, access="1,2"):
        """Fetches station clusters across a bounding box region."""
        params = {
            "latitude": lat,
            "longitude": lon,
            "spanLat": span_lat,
            "spanLng": span_lng,
            "count": count,
            "access": access,
            "minimal": 0
        }
        data = self._get("/locations/region", params=params)
        return data if isinstance(data, list) else []

    def search_by_text(self, query):
        """Uses PlugShare search API to search by name/query."""
        params = {"query": query}
        data = self._get("/locations/search", params=params)
        return data if isinstance(data, list) else []

    def search_nearby(self, lat, lon, distance_km=15, limit=50):
        """Translates radius in km into spanLat/spanLng and queries PlugShare."""
        deg_lat = (distance_km * 2.0) / 111.0
        deg_lon = (distance_km * 2.0) / 92.0
        span_lat = max(0.04, min(deg_lat, 8.0))
        span_lng = max(0.04, min(deg_lon, 8.0))
        results = self.search_region(lat, lon, span_lat=span_lat, span_lng=span_lng, count=min(250, max(limit * 2, 60)))
        filtered = []
        for loc in results:
            l_lat, l_lon = loc.get("latitude"), loc.get("longitude")
            if l_lat is not None and l_lon is not None:
                d_km = haversine_distance_km(lat, lon, l_lat, l_lon)
                if d_km <= distance_km:
                    filtered.append((d_km, loc))
        filtered.sort(key=lambda x: x[0])
        return [loc for _, loc in filtered[:limit]]

    def search_australia(self):
        """Fast concurrent discovery across Australian metro and regional centers."""
        def _fetch_region(item):
            name, lat, lon, slat, slon = item
            return self.search_region(lat, lon, span_lat=slat, span_lng=slon, count=250)

        with ThreadPoolExecutor(max_workers=10) as ex:
            results = list(ex.map(_fetch_region, AUSTRALIAN_SEARCH_REGIONS))

        seen = set()
        stations = []
        for res in results:
            for s in res:
                s_id = s.get("id")
                if s_id and s_id not in seen:
                    seen.add(s_id)
                    stations.append(s)
        return stations

    def search_state(self, state_code):
        """Discovers stations across a given Australian state."""
        st = state_code.upper()
        if st not in AUSTRALIAN_STATES_BOUNDS:
            return []
        cfg = AUSTRALIAN_STATES_BOUNDS[st]
        matching_regions = [r for r in AUSTRALIAN_SEARCH_REGIONS if st in r[0] or cfg["name"].split()[0] in r[0]]
        if not matching_regions:
            matching_regions = [(cfg["name"], cfg["lat"], cfg["lon"], cfg["spanLat"], cfg["spanLng"])]

        def _fetch_reg(item):
            name, lat, lon, slat, slon = item
            return self.search_region(lat, lon, span_lat=slat, span_lng=slon, count=250)

        with ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(_fetch_reg, matching_regions))

        seen = set()
        stations = []
        for res in results:
            for s in res:
                s_id = s.get("id")
                if s_id and s_id not in seen:
                    seen.add(s_id)
                    stations.append(s)
        return stations

# =============================================================================
# Schema Converter & Helpers
# =============================================================================

def extract_au_state_and_suburb(addr: str):
    if not addr:
        return "-", ""
    st_m = re.search(r"\b(NSW|VIC|QLD|WA|SA|TAS|ACT|NT)\b", addr, re.IGNORECASE)
    st = st_m.group(1).upper() if st_m else "-"
    sub_m = re.search(r",\s*([^,]+?)\s+(?:NSW|VIC|QLD|WA|SA|TAS|ACT|NT)\b", addr, re.IGNORECASE)
    sub = sub_m.group(1).strip() if sub_m else ""
    if not sub:
        parts = [p.strip() for p in addr.split(",")]
        if len(parts) >= 2:
            sub = parts[1]
    return st, sub

def extract_gross_rate_per_kwh(details_json):
    """Extracts consumer-facing gross rate per kWh ($/kWh) following Gross-First architecture."""
    cd = (details_json.get("cost_description") or "").strip()
    m = re.search(r"Price per kWh:\s*\$?([0-9.]+)", cd, re.I)
    if not m:
        m = re.search(r"([0-9.]+)\s*/\s*kWh", cd, re.I)
    if m:
        try:
            return round(float(m.group(1)), 2)
        except ValueError:
            pass

    for st in details_json.get("stations", []):
        st_cd = (st.get("cost_description") or "").strip()
        m = re.search(r"([0-9.]+)\s*/\s*kWh", st_cd, re.I)
        if m:
            try:
                return round(float(m.group(1)), 2)
            except ValueError:
                pass
        for o in st.get("outlets", []):
            for pr in o.get("prices", []):
                for el in pr.get("elements", []):
                    for pc in el.get("price_components", []):
                        if pc.get("type") == "ENERGY" and pc.get("price") is not None:
                            try:
                                return round(float(pc["price"]), 2)
                            except (ValueError, TypeError):
                                pass
    return 0.0

def plugshare_to_registry_record(basic_or_detail, full_detail=None):
    """Converts a PlugShare station payload into the dedicated plugshare_chargers.json schema."""
    d = full_detail if full_detail else basic_or_detail
    loc_id = d.get("id")
    name = (d.get("name") or f"PlugShare Station {loc_id}").strip()

    network = "3rd-Party"
    maj_net_id = d.get("majority_network_id")
    if maj_net_id and maj_net_id in PLUGSHARE_NETWORKS:
        network = PLUGSHARE_NETWORKS[maj_net_id]
    else:
        for st in d.get("stations", []):
            st_net = st.get("network_id")
            if st_net and st_net in PLUGSHARE_NETWORKS:
                network = PLUGSHARE_NETWORKS[st_net]
                break
            if isinstance(st.get("network"), dict) and st["network"].get("name"):
                network = st["network"]["name"]
                break

    name_lower = name.lower()
    if "exploren" in name_lower: network = "Exploren"
    elif "bp pulse" in name_lower or "bp " in name_lower: network = "BP Pulse"
    elif "evie" in name_lower: network = "Evie Networks"
    elif "chargefox" in name_lower: network = "Chargefox"
    elif "nrma" in name_lower: network = "NRMA"
    elif "jolt" in name_lower: network = "JOLT"
    elif "ampcharge" in name_lower or "ampol" in name_lower: network = "AmpCharge"
    elif "tesla supercharger" in name_lower: network = "Tesla Supercharger"
    elif "tesla destination" in name_lower: network = "Tesla Destination"
    elif "tesla" in name_lower: network = "Tesla"

    lat = d.get("latitude")
    lon = d.get("longitude")
    addr_str = d.get("address") or ""

    connectors = set()
    max_kw = 0.0
    stalls_count = d.get("station_count") or len(d.get("stations", [])) or 1
    is_fast = bool(d.get("is_fast_charger"))

    for st in d.get("stations", []):
        for o in st.get("outlets", []):
            c_code = o.get("connector")
            c_name = PLUGSHARE_CONNECTORS.get(c_code, o.get("connector_name"))
            if c_name:
                connectors.add(c_name)
            kw = o.get("kilowatts")
            if kw and kw > max_kw:
                max_kw = float(kw)
            if o.get("is_dc"):
                is_fast = True

    if not connectors and d.get("connector_types"):
        connectors = set(d["connector_types"])

    if not connectors:
        connectors.add("CCS2")

    gross_rate = extract_gross_rate_per_kwh(d)
    idle_fee = 0.0
    cd = d.get("cost_description") or ""
    m_idle = re.search(r"Idle fee per minute:\s*\$?([0-9.]+)", cd, re.I)
    if m_idle:
        try:
            idle_fee = round(float(m_idle.group(1)), 2)
        except ValueError:
            pass

    keywords = set()
    keywords.add(name)
    for part in name.split():
        if len(part) >= 3 and not part.isdigit():
            keywords.add(part)
    if addr_str:
        for part in addr_str.split(","):
            part_clean = part.strip()
            if len(part_clean) >= 4 and not part_clean.isdigit():
                keywords.add(part_clean)
    if network and network != "3rd-Party":
        keywords.add(network)

    clean_title = re.sub(r"\s*-\s*\d+\s*$", "", name)
    station_key = clean_title

    acc_code = d.get("access")
    access_str = "Public" if acc_code == 1 else ("Customer / Patron" if acc_code == 2 else "Private")
    if d.get("open247"):
        access_str += " 24/7"

    now_iso = get_utc_now_iso()
    state, suburb = extract_au_state_and_suburb(addr_str)

    record = {
        "plugshare_metadata": {
            "id": loc_id,
            "name": name,
            "network": network,
            "score": d.get("score"),
            "url": f"https://www.plugshare.com/location/{loc_id}",
            "keywords": sorted(list(keywords))
        },
        "location": {
            "address": addr_str,
            "street": addr_str.split(",")[0].strip() if addr_str else "",
            "suburb": suburb,
            "state": state,
            "postcode": "",
            "country": "Australia",
            "country_code": "AU",
            "lat": lat,
            "lon": lon,
            "radius_m": 150
        },
        "hardware": {
            "stalls": stalls_count,
            "max_power_kw": round(max_kw, 1) if max_kw > 0 else (150.0 if is_fast else 22.0),
            "connector_types": sorted(list(connectors))
        },
        "compatibility": {
            "open_to_non_tesla": True,
            "tesla_only": False,
            "access": access_str
        },
        "access": {
            "hours": d.get("hours") or ("Available 24/7" if d.get("open247") else None),
            "description": d.get("description") or d.get("cost_description")
        },
        "tariffs": {
            "currency": "AUD",
            "per_kwh_flat": gross_rate,
            "idle_fee_per_min": idle_fee,
            "has_tou_pricing": False
        },
        "amenities": {
            "parking": True,
            "restrooms": True,
            "shopping": True,
            "dining": True
        },
        "first_seen": now_iso,
        "last_updated": now_iso,
        "last_verified": now_iso,
        "valid_from": now_iso
    }
    return station_key, record

# =============================================================================
# PlugShare Dedicated Registry Management
# =============================================================================

class PlugShareRegistry:
    def __init__(self, repo_root=None):
        self.repo_root = repo_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.registry_path = os.path.join(self.repo_root, "Tessie", "plugshare_chargers.json")

    def load(self):
        if os.path.isfile(self.registry_path):
            try:
                with open(self.registry_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save(self, data):
        try:
            os.makedirs(os.path.dirname(self.registry_path), exist_ok=True)
            with open(self.registry_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"  {C_RED}❌ Failed writing {self.registry_path}: {e}{C_RESET}")
            return False

    def find_existing_match(self, station_key, record, registry=None):
        """
        Decide whether `record` (freshly scraped, keyed by `station_key`) is the
        same physical station as something already in the registry.

        PlugShare IDs are the only fully reliable identity signal - two entirely
        different real stations can (and do) share an identical display name,
        e.g. two separate "Bunnings Gladesville" listings ~70m apart on PlugShare
        (one a public Exploren DC unit, one a patron-only AC wall charger). Name
        matching must never override an explicit PlugShare-ID mismatch, or the
        second station silently overwrites the first under the shared key.
        """
        if registry is None:
            registry = self.load()
        new_id = record.get("plugshare_metadata", {}).get("id")

        # 1. PlugShare ID match is authoritative regardless of key/name -
        #    catches the case where a station's display name/key has changed
        #    since it was last saved.
        if new_id:
            for k, v in registry.items():
                if v.get("plugshare_metadata", {}).get("id") == new_id:
                    return k, v

        def _id_conflicts(v):
            existing_id = v.get("plugshare_metadata", {}).get("id")
            return bool(existing_id) and bool(new_id) and existing_id != new_id

        # 2. Exact key match - but only treat it as the same station if it
        #    doesn't carry a *different*, known PlugShare ID. A name collision
        #    with a conflicting ID means these are two distinct real stations
        #    that happen to share a name; the caller must file the new one
        #    under a disambiguated key instead of merging/overwriting here.
        if station_key in registry and not _id_conflicts(registry[station_key]):
            return station_key, registry[station_key]
        for k, v in registry.items():
            if k.lower() == station_key.lower() and not _id_conflicts(v):
                return k, v

        # 3. Proximity + name-token overlap fallback, for legacy/manual entries
        #    that have no PlugShare ID recorded at all to compare against.
        #    Skipped entirely for any entry with a conflicting ID - being
        #    physically close (as the two Bunnings Gladesville stations are)
        #    does not make them the same station.
        new_lat = record.get("location", {}).get("lat")
        new_lon = record.get("location", {}).get("lon")
        if new_lat is not None and new_lon is not None:
            new_name_tokens = set(re.findall(r"\w+", station_key.lower()))
            for k, v in registry.items():
                if _id_conflicts(v):
                    continue
                ex_lat = v.get("location", {}).get("lat")
                ex_lon = v.get("location", {}).get("lon")
                if ex_lat is not None and ex_lon is not None:
                    dist = haversine_distance_km(new_lat, new_lon, ex_lat, ex_lon)
                    if dist < 0.20:
                        ex_tokens = set(re.findall(r"\w+", k.lower()))
                        meaningful = {t for t in (new_name_tokens & ex_tokens) if len(t) >= 3}
                        if meaningful:
                            return k, v
        return None, None

    def add_or_update(self, station_key, record, sync_external=False):
        # Defense-in-depth: never persist a record with no id, no name,
        # and no location data at all - this is the shape of the broken
        # "PlugShare Station None" stubs the /locations/{id} detail
        # endpoint can return for a station it won't fully disclose.
        meta = record.get("plugshare_metadata", {}) or {}
        loc = record.get("location", {}) or {}
        has_identity = bool(meta.get("id")) or bool(loc.get("address"))
        has_coords = loc.get("lat") is not None or loc.get("lon") is not None
        if not (has_identity or has_coords):
            print(f"  {C_RED}❌ Refusing to save '{station_key}': no id, address, or coordinates were returned for this station.{C_RESET}")
            return "REJECTED_EMPTY"
        registry = self.load()
        now_utc = get_utc_now_iso()
        existing_key, existing = self.find_existing_match(station_key, record, registry)
        if existing:
            ex_rate = existing.get("tariffs", {}).get("per_kwh_flat", 0.0)
            if ex_rate > 0 and record.get("tariffs", {}).get("per_kwh_flat", 0.0) == 0:
                record["tariffs"] = existing["tariffs"]
            ex_keywords = set(existing.get("plugshare_metadata", {}).get("keywords", []))
            new_keywords = set(record.get("plugshare_metadata", {}).get("keywords", []))
            record["plugshare_metadata"]["keywords"] = sorted(list(ex_keywords | new_keywords))
            record["first_seen"] = existing.get("first_seen", now_utc)
            record["last_updated"] = now_utc
            record["last_verified"] = now_utc
            registry[existing_key] = record
            result = "UPDATED"
            print(f"  {C_CYAN}🔄 Updated existing entry '{existing_key}' in:{C_RESET} {self.registry_path}")
        else:
            final_key = station_key
            if final_key in registry:
                # A different, unrelated station already occupies this exact
                # key (same display name, non-matching/unknown PlugShare ID -
                # find_existing_match() already ruled out a real match above).
                # Never overwrite it silently; file this one under a
                # disambiguated key instead.
                new_id = record.get("plugshare_metadata", {}).get("id")
                network_hint = record.get("plugshare_metadata", {}).get("network") or ""
                if network_hint and network_hint != "3rd-Party":
                    candidate = f"{station_key} ({network_hint})"
                elif new_id:
                    candidate = f"{station_key} (PlugShare #{new_id})"
                else:
                    candidate = f"{station_key} (2)"
                n = 2
                while candidate in registry:
                    candidate = f"{station_key} ({n})"
                    n += 1
                print(f"  {C_YELLOW}⚠️  '{station_key}' already exists as a different station (different PlugShare ID) - saving this one as '{candidate}' instead of overwriting it.{C_RESET}")
                final_key = candidate
            record["first_seen"] = now_utc
            record["last_updated"] = now_utc
            record["last_verified"] = now_utc
            registry[final_key] = record
            result = "CREATED"
            print(f"  {C_GREEN}✅ Created new entry '{final_key}' in:{C_RESET} {self.registry_path}")
        if self.save(registry):
            if sync_external:
                print(f"  {C_YELLOW}ℹ️  Tessie data and charging tooling run directly from the repository and iCloud. Mounted TESLADRIVE volumes are reserved exclusively for dashcam/TeslaCam media.{C_RESET}")
            return result
        return "ERROR"

# =============================================================================
# Rich Unicode Table Renderer (Matching find_tesla_chargers.py style)
# =============================================================================

def print_plugshare_chargers_table(
    stations: list,
    ref_lat: float = None,
    ref_lon: float = None,
    ref_label: str = None,
    active_radius: float = None,
    eval_time_label: str = None,
    sort_mode: str = None,
    existing_registry: dict = None
):
    """
    Renders charging stations in an executive Unicode box table.
    Mirrors find_tesla_chargers.py exact styling, alignment, headers, and color indicators.
    """
    if not stations:
        print(f"{C_YELLOW}No matching charging stations found.{C_RESET}")
        return

    has_dist = ref_lat is not None and ref_lon is not None

    max_title_len = max((display_len(s.get("title", "")) for s in stations), default=20)
    title_col_w = min(max(max_title_len + 2, 24), 32)

    max_suburb_len = max((display_len(s.get("location", {}).get("suburb") or s.get("short_name", "")) for s in stations), default=12)
    suburb_col_w = max(min(max_suburb_len + 2, 24), 19)

    headers = ["#", "Type", "State", "Station Name", "Network", "Stalls", "Access", "Rate (Now)", "Max Power"]
    widths = [6, 8, 7, title_col_w, 16, 9, 13, 12, 12]

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
        coords_str = f" ({ref_lat:.4f}, {ref_lon:.4f})" if (ref_lat is not None and ref_lon is not None) else ""
        orig_line = f" 📍 {C_CYAN}Proximity Origin:{C_RESET} {ref_label}{coords_str}{radius_note}{sort_str}"
        print(f"│{pad_display(orig_line, total_inner_w, 'left')}│")
    elif sort_mode:
        sort_line = f" 📊 {C_BOLD}Sort Order:{C_RESET} {sort_mode}"
        print(f"│{pad_display(sort_line, total_inner_w, 'left')}│")

    eval_label = eval_time_label or "Current Local Time"
    time_line = f" ⏰ {C_BOLD}Pricing Evaluation:{C_RESET} {eval_label}"
    print(f"│{pad_display(time_line, total_inner_w, 'left')}│")

    legend_line = f" 📊 {C_BOLD}Status Legend:{C_RESET} [{C_GREEN} 1 {C_RESET}] In plugshare_chargers.json  |  [{C_ORANGE} 2 {C_RESET}] Not in JSON"
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
        color = C_GREEN if status == "IN_JSON" else C_ORANGE
        num_str = f"[{color}{idx:2d}{C_RESET}]"
        
        is_dc = s.get("is_dc", True)
        t_icon = "🔴 DC" if is_dc else "🔌 AC"

        hw = s.get("hardware", {})
        stalls_val = hw.get("stalls")
        stalls_str = f"{stalls_val} bays" if stalls_val else "-"
        
        power_val = hw.get("max_power_kw", 0)
        power_str = f"{power_val:.0f} kW" if power_val > 0 else "-"

        comp = s.get("compatibility", {})
        access_str = comp.get("access", "Public")

        tariffs = s.get("tariffs", {})
        rate_val = tariffs.get("per_kwh_flat", 0)
        if rate_val > 0:
            rate_str = f"${rate_val:.2f}/kWh"
        else:
            rate_str = "-"

        net_val = s.get("network", "3rd-Party")
        suburb_raw = s.get("location", {}).get("suburb") or s.get("short_name", "")

        title_disp = truncate_display(s.get("title", ""), widths[3] - 2)
        net_disp = truncate_display(net_val, widths[4] - 2)
        access_disp = truncate_display(access_str, widths[6] - 2)
        suburb_disp = truncate_display(suburb_raw, suburb_col_w - 2)

        row_cells = [
            pad_display(num_str, widths[0], "center"),
            pad_display(t_icon, widths[1], "center"),
            pad_display(s.get("state", "-"), widths[2], "center"),
            pad_display(" " + title_disp, widths[3], "left"),
            pad_display(" " + net_disp, widths[4], "left"),
            pad_display(stalls_str + " ", widths[5], "right"),
            pad_display(" " + access_disp, widths[6], "left"),
            pad_display(rate_str + " ", widths[7], "right"),
            pad_display(power_str + " ", widths[8], "right"),
        ]

        if has_dist:
            dist_val = s.get("_distance_km", float("inf"))
            dist_text = f"{dist_val:.1f} km " if dist_val != float("inf") else "-- "
            row_cells.append(pad_display(dist_text, widths[9], "right"))
            row_cells.append(pad_display(f" {C_DIM}{suburb_disp}{C_RESET}", widths[10], "left"))
        else:
            row_cells.append(pad_display(f" {C_DIM}{suburb_disp}{C_RESET}", widths[9], "left"))

        print("│" + "│".join(row_cells) + "│")

    bot_b = "└" + "┴".join("─" * w for w in widths) + "┘"
    print(bot_b)

# =============================================================================
# Station Detail Preview (Matching find_tesla_chargers.py 86-char box)
# =============================================================================

def display_station_preview(station_key: str, data: dict, from_cache: bool = False):
    meta = data.get("plugshare_metadata", {})
    loc = data.get("location", {})
    hw = data.get("hardware", {})
    comp = data.get("compatibility", {})
    tariffs = data.get("tariffs", {})
    acc = data.get("access", {})

    box_width = 86
    title_prefix = "LOCAL JSON REGISTRY" if from_cache else "LIVE SCRAPED (PLUGSHARE)"
    title_content = f"{title_prefix}: {station_key}"
    if len(title_content) > box_width - 4:
        title_content = title_content[:box_width - 7] + "..."
    pad_total = box_width - 2 - len(title_content)
    pad_l = max(0, pad_total // 2)
    pad_r = max(0, pad_total - pad_l)
    header_line = f"║{' ' * pad_l}{title_content}{' ' * pad_r}║"

    print(f"\n╔{'═' * (box_width - 2)}╗")
    print(header_line)
    print(f"╚{'═' * (box_width - 2)}╝\n")

    source_label = f"{C_GREEN}Local JSON Registry (Tessie/plugshare_chargers.json){C_RESET}" if from_cache else f"{C_CYAN}Live Scraped (PlugShare v3 API){C_RESET}"

    lw = 22
    def _field(icon, label, value):
        styled_label = f"{C_BOLD}{label}:{C_RESET}"
        print(f"  {icon} {pad_display(styled_label, lw)} {value}")

    _field("📂", "Data Source", source_label)
    _field("📍", "Station Key", station_key)
    _field("🏢", "Network", meta.get("network", "-"))
    if meta.get("id"):
        _field("🆔", "PlugShare ID", str(meta.get("id")))
    _field("📮", "Address", f"{loc.get('address')} ({loc.get('lat')}, {loc.get('lon')})")
    if meta.get("url"):
        _field("🔗", "PlugShare URL", meta.get("url"))

    connectors_str = ", ".join(hw.get("connector_types", ["-"]))
    stalls_val = hw.get("stalls", "-")
    power_val = hw.get("max_power_kw", "-")
    _field("🔌", "Hardware", f"{stalls_val} Bays | Up to {power_val} kW ({connectors_str})")

    access_val = comp.get("access", "Public")
    _field("🚗", "EV Access", f"{C_GREEN}{access_val}{C_RESET}")

    idle_rate = tariffs.get("idle_fee_per_min", 0)
    _field("⏱️ ", "Idle Fee", f"${idle_rate:.2f}/min" if idle_rate > 0 else "None")

    if data.get("valid_from"):
        _field("🕒", "Effective Date", f"{data.get('valid_from')} (Verified: {data.get('last_verified', 'N/A')})")
    if acc.get("hours"):
        _field("⏰", "Operating Hours", str(acc.get("hours")).replace("\n", " | "))
    if meta.get("score") is not None:
        _field("★ ", "PlugScore", f"{meta.get('score'):.1f} / 10.0")

    curr = tariffs.get("currency", "AUD")
    rate = tariffs.get("per_kwh_flat", 0)
    print(f"\n  💰 {C_BOLD}Gross Tariff ({curr} inc-GST):{C_RESET}")
    if rate > 0:
        print(f"    • {C_GREEN}${rate:.2f}/kWh{C_RESET} (Flat Gross Rate)")
    else:
        print(f"    • {C_YELLOW}Price unlisted / Check station on PlugShare{C_RESET}")
    print()

# =============================================================================
# Interactive Station Selection Loop (Table Menu Selection)
# =============================================================================

def interactive_station_selector_loop(
    stations: list,
    registry_obj: PlugShareRegistry,
    existing_registry: dict,
    ps_client: PlugShareClient,
    sync_external: bool = False,
    re_render_cb = None
):
    """
    Interactive selection loop following a station listing table.
    Allows the user to select any station by number [1-N] to inspect details,
    re-scrape live pricing, open in web browser / Google Maps, or re-render the list.
    """
    if not stations:
        return

    while True:
        print(f"\n{C_BOLD}Options:{C_RESET} Enter station number [{C_GREEN}1-{len(stations)}{C_RESET}] to inspect, {C_CYAN}'a <#>' {C_RESET}to add, or {C_YELLOW}Enter{C_RESET}/'q' to quit.")
        try:
            choice = input(f"Select Station [1-{len(stations)}], [q]uit: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not choice or choice in ("q", "quit", "exit"):
            break

        if choice in ("r", "list", "table") and re_render_cb:
            re_render_cb()
            continue

        is_direct_add = False
        target_num = None
        if choice.startswith("a ") or choice.startswith("add "):
            parts = choice.split()
            if len(parts) > 1 and parts[1].isdigit():
                target_num = int(parts[1])
                is_direct_add = True
        elif choice.isdigit():
            target_num = int(choice)

        if target_num is None or target_num < 1 or target_num > len(stations):
            print(f"{C_RED}❌ Invalid selection. Please enter a number between 1 and {len(stations)}.{C_RESET}\n")
            continue

        selected_st = stations[target_num - 1]
        station_title = selected_st.get("title", "")
        loc_id = selected_st.get("id")

        ex_key, cached_record = registry_obj.find_existing_match(station_title, selected_st.get("_record", {}), existing_registry)

        if is_direct_add or not cached_record:
            print(f"\n{C_CYAN}⚡ Fetching live details for [{target_num}] {station_title}...{C_RESET}")
            full_data = ps_client.get_location_details(loc_id) if loc_id else None
            key, record = plugshare_to_registry_record(selected_st.get("_raw", selected_st), full_detail=full_data)
            display_station_preview(key, record, from_cache=False)
            try:
                save_in = input("Save / update this station into JSON registry? [Y/n]: ").strip().lower()
                if save_in != "n":
                    registry_obj.add_or_update(key, record, sync_external=sync_external)
                    existing_registry[key] = record
                    selected_st["_status"] = "IN_JSON"
            except (EOFError, KeyboardInterrupt):
                pass
        else:
            display_station_preview(ex_key, cached_record, from_cache=True)
            print(f"{C_BOLD}Actions for {station_title}:{C_RESET}")
            print(f"  [{C_GREEN}1{C_RESET}] Re-scrape live details from PlugShare (v3 API)")
            print(f"  [{C_GREEN}2{C_RESET}] Open PlugShare URL in default web browser")
            print(f"  [{C_GREEN}3{C_RESET}] Open in Google Maps (navigation / coordinates)")
            print(f"  [{C_GREEN}b{C_RESET}] Back to station list")
            print(f"  [{C_GREEN}q{C_RESET}] Quit")
            print()

            try:
                act = input(f"Action [1-3, b, q, default: b]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                break

            if act == "1":
                print(f"\n{C_CYAN}⚡ Re-fetching live details from PlugShare...{C_RESET}")
                full_data = ps_client.get_location_details(loc_id) if loc_id else None
                key, record = plugshare_to_registry_record(selected_st.get("_raw", selected_st), full_detail=full_data)
                display_station_preview(key, record, from_cache=False)
                try:
                    save_in = input("Update JSON registry with refreshed details? [Y/n]: ").strip().lower()
                    if save_in != "n":
                        registry_obj.add_or_update(key, record, sync_external=sync_external)
                        existing_registry[key] = record
                except (EOFError, KeyboardInterrupt):
                    pass
            elif act == "2":
                url_to_open = selected_st.get("url") or cached_record.get("plugshare_metadata", {}).get("url")
                if url_to_open:
                    print(f"🌐 Opening {url_to_open}...")
                    import subprocess
                    subprocess.run(["open", url_to_open] if sys.platform == "darwin" else ["xdg-open", url_to_open], check=False)
                else:
                    print(f"{C_RED}❌ No PlugShare URL found for this station.{C_RESET}")
            elif act == "3":
                loc_data = cached_record.get("location", {})
                lat_val = loc_data.get("lat")
                lon_val = loc_data.get("lon")
                if lat_val is not None and lon_val is not None:
                    maps_url = f"https://www.google.com/maps/search/?api=1&query={lat_val},{lon_val}"
                    print(f"🗺️ Opening Google Maps: {maps_url}...")
                    import subprocess
                    subprocess.run(["open", maps_url] if sys.platform == "darwin" else ["xdg-open", maps_url], check=False)
                else:
                    print(f"{C_RED}❌ GPS coordinates missing for this station.{C_RESET}")
            elif act in ("b", "back", ""):
                pass
            print()

# =============================================================================
# Auto-Discovery from Driving / Charging History
# =============================================================================

def discover_from_history(registry_obj, ps_client, repo_root=None):
    repo_root = repo_root or registry_obj.repo_root
    registry = registry_obj.load()
    master_path = None
    for p in [
        os.path.join(repo_root, "Tessie", "charges_master.csv"),
        os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie/charges_master.csv"),
        os.path.expanduser("~/iCloud/Tesla/Tessie/charges_master.csv"),
    ]:
        if os.path.isfile(p):
            master_path = p
            break
    if not master_path:
        print(f"{C_RED}❌ No charges_master.csv found.{C_RESET}")
        return []
    print(f"  📂 Scanning: {master_path}")
    unmatched = {}
    try:
        with open(master_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                loc = row.get("Location") or row.get("Saved Location") or ""
                if not loc or "home" in loc.lower() or "tesla" in loc.lower():
                    continue
                is_super = str(row.get("Supercharger", "")).strip().lower() == "true"
                if is_super:
                    continue
                already_known = False
                for k in registry:
                    if loc.lower() in k.lower() or k.lower() in loc.lower():
                        already_known = True
                        break
                    for kw in registry[k].get("plugshare_metadata", {}).get("keywords", []):
                        if loc.lower() in kw.lower() or kw.lower() in loc.lower():
                            already_known = True
                            break
                    if already_known:
                        break
                if not already_known and loc not in unmatched:
                    lat = row.get("Latitude") or row.get("Starting Latitude")
                    lon = row.get("Longitude") or row.get("Starting Longitude")
                    if lat and lon:
                        try:
                            unmatched[loc] = (float(lat), float(lon))
                        except (ValueError, TypeError):
                            pass
    except Exception as e:
        print(f"{C_RED}❌ Error reading CSV: {e}{C_RESET}")
        return []
    if not unmatched:
        print(f"{C_GREEN}✅ All non-Tesla charging locations in history are already registered!{C_RESET}")
        return []
    print(f"\n{C_YELLOW}🔍 Found {len(unmatched)} unregistered charging location(s) in history:{C_RESET}")
    for loc_name, (lat, lon) in unmatched.items():
        print(f"  • {loc_name} ({lat:.4f}, {lon:.4f})")
    discovered = []
    for loc_name, (lat, lon) in unmatched.items():
        print(f"\n  🔎 Querying PlugShare for '{loc_name}'...")
        results = ps_client.search_nearby(lat, lon, distance_km=0.5, limit=3)
        if results:
            best = results[0]
            d = ps_client.get_location_details(best["id"]) or best
            key, rec = plugshare_to_registry_record(d)
            st_entry = {
                "title": key,
                "short_name": key,
                "state": rec.get("location", {}).get("state", "-"),
                "country": "Australia",
                "type": "dc_fast" if rec.get("hardware", {}).get("max_power_kw", 0) >= 25 else "ac_destination",
                "is_dc": rec.get("hardware", {}).get("max_power_kw", 0) >= 25,
                "network": rec.get("plugshare_metadata", {}).get("network", "3rd-Party"),
                "url": rec.get("plugshare_metadata", {}).get("url", ""),
                "id": rec.get("plugshare_metadata", {}).get("id"),
                "location": rec.get("location", {}),
                "hardware": rec.get("hardware", {}),
                "compatibility": rec.get("compatibility", {}),
                "tariffs": rec.get("tariffs", {}),
                "_status": "NOT_IN_JSON",
                "_record": rec,
                "_raw": d,
                "_distance_km": float("inf")
            }
            discovered.append(st_entry)
    return discovered

# =============================================================================
# Main CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="🔌 PlugShare Non-Tesla Charger Discovery & Registry Populator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Query & Exploration Examples:
  # 1. Discover chargers nationwide across Australia:
  ./Tools/find_plugshare_chargers.py --country Australia
  ./Tools/find_plugshare_chargers.py --country Australia --dc
  ./Tools/find_plugshare_chargers.py --country Australia --dc --limit 10

  # 2. Nearby search from vehicle position or Home:
  ./Tools/find_plugshare_chargers.py --near Home --dc --radius 15
  ./Tools/find_plugshare_chargers.py --near "Ryde" --radius 25 --limit 10

  # 3. State / Regional search:
  ./Tools/find_plugshare_chargers.py --state NSW --dc --limit 20
  ./Tools/find_plugshare_chargers.py --state VIC --dc

  # 4. Text / Station / Network search:
  ./Tools/find_plugshare_chargers.py --search "BP Pulse"
  ./Tools/find_plugshare_chargers.py --search "Evie" --state NSW

  # 5. Offline Inspection & Live Scrape:
  ./Tools/find_plugshare_chargers.py --inspect "Bunnings Gladesville"
  ./Tools/find_plugshare_chargers.py --plugshare 801149 --save

  # 6. Auto-discovery from charging history:
  ./Tools/find_plugshare_chargers.py --from-history --sync
"""
    )
    # Discovery & Geographic Flags
    parser.add_argument("--country", default="Australia", help="Country name (default: 'Australia')")
    parser.add_argument("--state", choices=["NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT", "nsw", "vic", "qld", "wa", "sa", "tas", "act", "nt"], help="State / Territory code (e.g. 'NSW', 'VIC', 'QLD')")
    parser.add_argument("--suburb", help="Filter by suburb name")
    parser.add_argument("-q", "--search", "--query", help="Search term across station names, locations, and keywords")
    parser.add_argument("--dc", "--dc-only", action="store_true", help="Filter DC Fast Chargers only (>=25kW)")
    parser.add_argument("--ac", "--ac-only", action="store_true", help="Filter AC Destination Chargers only (<25kW)")
    parser.add_argument("--all-types", action="store_true", help="Include both DC Fast and AC Destination Chargers")
    parser.add_argument("--list", action="store_true", help="List matching charging stations in rich table format")

    # Address, GPS & Proximity Flags
    parser.add_argument("-a", "--near", "--address", dest="near", help="Reference address, suburb, place name, or shortcut (e.g. 'Home', 'Ryde')")
    parser.add_argument("--gps", action="store_true", help="Auto-discover current GPS location from vehicle telemetry / IP / Home")
    parser.add_argument("--coords", help="Direct reference coordinates in 'lat,lon' format")
    parser.add_argument("--radius", "--radius-km", dest="radius", type=float, default=15, help="Search radius in km (default: 15)")
    parser.add_argument("-n", "--limit", type=int, help="Maximum number of stations to display")
    parser.add_argument("--sort", choices=["dist", "price", "rate", "power", "stalls", "name"], help="Sort results by distance, price, max power, or stall count")

    # Hardware & Network Filters
    parser.add_argument("--min-power", type=float, default=0, help="Minimum power in kW")
    parser.add_argument("--network", help="Filter by network name (e.g. Chargefox, Evie, BP Pulse, Exploren, NRMA)")

    # Inspection, Persistence & History Flags
    parser.add_argument("--inspect", "--scrape", help="Inspect station details (from local JSON registry or PlugShare)")
    parser.add_argument("--plugshare", help="PlugShare location ID(s) to inspect/scrape (comma-separated)")
    parser.add_argument("--save", "--update", action="store_true", help="Save / update inspected station into JSON registry")
    parser.add_argument("--add-all", action="store_true", help="Auto-add all matching stations to registry")
    parser.add_argument("--refresh-prices", action="store_true", help="Fetch live PlugShare detail for every listed station with no known rate and save the pricing found (makes one API call per station missing a price)")
    parser.add_argument("--sync", action="store_true", help="Deprecated no-op: TESLADRIVE volumes are reserved for dashcam media only, Tessie data never syncs there")
    parser.add_argument("--from-history", action="store_true", help="Auto-discover from charges_master.csv")
    parser.add_argument("--json", action="store_true", help="Output results as raw JSON")

    args = parser.parse_args()

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ps_client = PlugShareClient()
    registry_obj = PlugShareRegistry(repo_root=repo_root)
    existing_registry = registry_obj.load()

    # 1. Direct Inspection by Name, Query, or PlugShare ID
    target_inspect = args.inspect or args.plugshare
    if target_inspect:
        ids_or_names = [i.strip() for i in target_inspect.split(",") if i.strip()]
        for item in ids_or_names:
            if item.isdigit():
                print(f"  🔎 Fetching PlugShare location {item}...")
                full_d = ps_client.get_location_details(item)
                if not full_d:
                    print(f"  {C_RED}❌ Station {item} not found on PlugShare.{C_RESET}")
                    continue
                # Guard: the /locations/{id} detail endpoint can return a
                # structurally valid but empty/near-empty payload (no id, no
                # name, no coordinates) rather than a clean 404 - e.g. when
                # the station requires auth PlugShare doesn't grant here, or
                # the id is stale/invalid. Never build a registry record from
                # that; it silently produced null "PlugShare Station None"
                # stubs in the past.
                has_identity = bool(full_d.get("id") or full_d.get("name"))
                has_location = full_d.get("latitude") is not None or full_d.get("longitude") is not None or full_d.get("address")
                if not (has_identity or has_location):
                    print(f"  {C_RED}❌ Station {item} returned no usable data from PlugShare (empty/near-empty response).{C_RESET}")
                    print(f"  {C_DIM}   Raw response keys: {sorted(full_d.keys()) if isinstance(full_d, dict) else type(full_d)}{C_RESET}")
                    print(f"  {C_DIM}   Try locating it via --search \"<name>\" or --near instead - the bulk search/region API returns full records even when the direct detail endpoint doesn't.{C_RESET}")
                    continue
                k, rec = plugshare_to_registry_record(full_d)
                display_station_preview(k, rec, from_cache=False)
                if args.save or args.add_all:
                    registry_obj.add_or_update(k, rec, sync_external=args.sync)
                elif sys.stdin.isatty():
                    try:
                        save_in = input("Save / update this station into JSON registry? [Y/n]: ").strip().lower()
                        if save_in != "n":
                            registry_obj.add_or_update(k, rec, sync_external=args.sync)
                    except (EOFError, KeyboardInterrupt):
                        pass
            else:
                # Search local registry first
                matches = []
                for k, v in existing_registry.items():
                    if item.lower() in k.lower() or item.lower() in v.get("location", {}).get("suburb", "").lower():
                        matches.append((k, v))
                if len(matches) == 1:
                    k, v = matches[0]
                    display_station_preview(k, v, from_cache=True)
                elif len(matches) > 1:
                    st_list = []
                    for k, v in matches:
                        is_dc = v.get("hardware", {}).get("max_power_kw", 0) >= 25.0
                        st_list.append({
                            "title": k,
                            "short_name": k,
                            "state": v.get("location", {}).get("state", "-"),
                            "country": "Australia",
                            "type": "dc_fast" if is_dc else "ac_destination",
                            "is_dc": is_dc,
                            "network": v.get("plugshare_metadata", {}).get("network", "3rd-Party"),
                            "url": v.get("plugshare_metadata", {}).get("url", ""),
                            "id": v.get("plugshare_metadata", {}).get("id"),
                            "location": v.get("location", {}),
                            "hardware": v.get("hardware", {}),
                            "compatibility": v.get("compatibility", {}),
                            "tariffs": v.get("tariffs", {}),
                            "_status": "IN_JSON",
                            "_record": v,
                            "_distance_km": float("inf")
                        })
                    print_plugshare_chargers_table(st_list, ref_label=f"Query: \"{item}\"")
                    if sys.stdin.isatty():
                        interactive_station_selector_loop(st_list, registry_obj, existing_registry, ps_client, sync_external=args.sync)
                else:
                    # Search online via text search
                    print(f"  🔎 Searching PlugShare for '{item}'...")
                    online_results = ps_client.search_by_text(item)
                    if online_results:
                        st_list = []
                        for raw in online_results:
                            k, rec = plugshare_to_registry_record(raw)
                            is_in_json = registry_obj.find_existing_match(k, rec, existing_registry)[0] is not None
                            is_dc = rec.get("hardware", {}).get("max_power_kw", 0) >= 25.0
                            st_list.append({
                                "title": k,
                                "short_name": k,
                                "state": rec.get("location", {}).get("state", "-"),
                                "country": "Australia",
                                "type": "dc_fast" if is_dc else "ac_destination",
                                "is_dc": is_dc,
                                "network": rec.get("plugshare_metadata", {}).get("network", "3rd-Party"),
                                "url": rec.get("plugshare_metadata", {}).get("url", ""),
                                "id": rec.get("plugshare_metadata", {}).get("id"),
                                "location": rec.get("location", {}),
                                "hardware": rec.get("hardware", {}),
                                "compatibility": rec.get("compatibility", {}),
                                "tariffs": rec.get("tariffs", {}),
                                "_status": "IN_JSON" if is_in_json else "NOT_IN_JSON",
                                "_record": rec,
                                "_raw": raw,
                                "_distance_km": float("inf")
                            })
                        print_plugshare_chargers_table(st_list, ref_label=f"Query: \"{item}\"")
                        if sys.stdin.isatty():
                            interactive_station_selector_loop(st_list, registry_obj, existing_registry, ps_client, sync_external=args.sync)
                    else:
                        print(f"  {C_RED}❌ No matching station found for '{item}'.{C_RESET}")
        return

    # 2. Auto-Discovery from History
    if args.from_history:
        print(f"\n{C_BOLD}{'=' * 80}{C_RESET}")
        print(f"{C_CYAN}{C_BOLD}               🔍 AUTO-DISCOVERY FROM CHARGING HISTORY{C_RESET}")
        print(f"{C_BOLD}{'=' * 80}{C_RESET}")
        discovered = discover_from_history(registry_obj, ps_client, repo_root=repo_root)
        if discovered:
            print_plugshare_chargers_table(discovered, existing_registry=existing_registry)
            if args.add_all:
                added = 0
                for st in discovered:
                    k = st["title"]
                    rec = st["_record"]
                    ex_k, _ = registry_obj.find_existing_match(k, rec, existing_registry)
                    if not ex_k:
                        res = registry_obj.add_or_update(k, rec, sync_external=args.sync)
                        if res in ("CREATED", "UPDATED"):
                            added += 1
                            existing_registry[k] = rec
                print(f"\n{C_GREEN}✅ Added {added} new stations.{C_RESET}")
            elif sys.stdin.isatty():
                interactive_station_selector_loop(discovered, registry_obj, existing_registry, ps_client, sync_external=args.sync)
        return

    # 3. Resolve Reference Coordinates (for proximity searches)
    ref_lat, ref_lon, ref_label = None, None, None
    active_radius = args.radius
    if args.gps:
        ref_lat, ref_lon, ref_label = get_current_gps_location(repo_root)
    elif args.coords:
        ref_lat, ref_lon, ref_label = resolve_reference_coordinates(args.coords, repo_root)
    elif args.near:
        ref_lat, ref_lon, ref_label = resolve_reference_coordinates(args.near, repo_root)

    # 4. Fetch Stations Based on Mode
    raw_stations = []
    has_explicit_proximity = ref_lat is not None and ref_lon is not None

    if has_explicit_proximity:
        print(f"\n🌐 Fetching PlugShare Chargers near {ref_label} (within {active_radius:.0f} km)...")
        print(f"   https://api.plugshare.com/v3/locations/region")
        raw_stations = ps_client.search_nearby(ref_lat, ref_lon, distance_km=active_radius, limit=min(250, args.limit or 50))
        print(f"✔ Discovered {len(raw_stations)} PlugShare Chargers in target area.")
    elif args.state:
        st_code = args.state.upper()
        cfg = AUSTRALIAN_STATES_BOUNDS.get(st_code, {})
        print(f"\n🌐 Fetching PlugShare Chargers for {cfg.get('name', st_code)} ({st_code})...")
        print(f"   https://api.plugshare.com/v3/locations/region")
        raw_stations = ps_client.search_state(st_code)
        print(f"✔ Discovered {len(raw_stations)} PlugShare Chargers in {st_code}.")
    elif args.search:
        print(f"\n🌐 Searching PlugShare for \"{args.search}\"...")
        print(f"   https://api.plugshare.com/v3/locations/search")
        raw_stations = ps_client.search_by_text(args.search)
        print(f"✔ Discovered {len(raw_stations)} matching PlugShare stations.")
    else:
        # Default: Discover chargers across Australia
        print(f"\n🌐 Fetching PlugShare Chargers for Australia...")
        print(f"   https://api.plugshare.com/v3/locations/region")
        raw_stations = ps_client.search_australia()
        print(f"✔ Discovered {len(raw_stations)} PlugShare Chargers in Australia.")

    # Convert to unified station structures
    all_stations = []
    seen_ids = set()

    for raw in raw_stations:
        s_id = raw.get("id")
        if s_id in seen_ids:
            continue
        seen_ids.add(s_id)
        k, rec = plugshare_to_registry_record(raw)
        ex_k, ex_rec = registry_obj.find_existing_match(k, rec, existing_registry)
        
        # Merge existing registry pricing/details if present
        target_rec = ex_rec if ex_rec else rec
        target_key = ex_k if ex_k else k
        is_in_json = ex_k is not None

        power_kw = float(target_rec.get("hardware", {}).get("max_power_kw", 0) or 0)
        is_dc = power_kw >= 25.0 or bool(raw.get("is_fast_charger"))

        st_entry = {
            "title": target_key,
            "short_name": target_key,
            "state": target_rec.get("location", {}).get("state", "-"),
            "country": "Australia",
            "type": "dc_fast" if is_dc else "ac_destination",
            "is_dc": is_dc,
            "network": target_rec.get("plugshare_metadata", {}).get("network", "3rd-Party"),
            "url": target_rec.get("plugshare_metadata", {}).get("url", f"https://www.plugshare.com/location/{s_id}"),
            "id": s_id,
            "location": target_rec.get("location", {}),
            "hardware": target_rec.get("hardware", {}),
            "compatibility": target_rec.get("compatibility", {}),
            "tariffs": target_rec.get("tariffs", {}),
            "_status": "IN_JSON" if is_in_json else "NOT_IN_JSON",
            "_record": target_rec,
            "_raw": raw,
            "_distance_km": float("inf")
        }

        # Calculate proximity distance if reference coords available
        c_lat = target_rec.get("location", {}).get("lat")
        c_lon = target_rec.get("location", {}).get("lon")
        if ref_lat is not None and ref_lon is not None and c_lat is not None and c_lon is not None:
            st_entry["_distance_km"] = haversine_distance_km(ref_lat, ref_lon, c_lat, c_lon)

        all_stations.append(st_entry)

    # Include any local registry stations that match search filters and weren't discovered
    for k, v in existing_registry.items():
        s_id = v.get("plugshare_metadata", {}).get("id")
        if s_id and s_id in seen_ids:
            continue
        power_kw = float(v.get("hardware", {}).get("max_power_kw", 0) or 0)
        is_dc = power_kw >= 25.0
        st_entry = {
            "title": k,
            "short_name": k,
            "state": v.get("location", {}).get("state", "-"),
            "country": "Australia",
            "type": "dc_fast" if is_dc else "ac_destination",
            "is_dc": is_dc,
            "network": v.get("plugshare_metadata", {}).get("network", "3rd-Party"),
            "url": v.get("plugshare_metadata", {}).get("url", ""),
            "id": s_id,
            "location": v.get("location", {}),
            "hardware": v.get("hardware", {}),
            "compatibility": v.get("compatibility", {}),
            "tariffs": v.get("tariffs", {}),
            "_status": "IN_JSON",
            "_record": v,
            "_raw": {},
            "_distance_km": float("inf")
        }
        c_lat = v.get("location", {}).get("lat")
        c_lon = v.get("location", {}).get("lon")
        if ref_lat is not None and ref_lon is not None and c_lat is not None and c_lon is not None:
            st_entry["_distance_km"] = haversine_distance_km(ref_lat, ref_lon, c_lat, c_lon)

        all_stations.append(st_entry)

    # 5. Apply Filters
    filtered = all_stations

    # DC / AC type filtering
    if args.dc and not args.ac:
        filtered = [s for s in filtered if s["is_dc"]]
    elif args.ac and not args.dc:
        filtered = [s for s in filtered if not s["is_dc"]]

    if args.state:
        target_st = args.state.upper()
        filtered = [s for s in filtered if s.get("state") == target_st]

    if args.suburb:
        sub_lower = args.suburb.lower()
        filtered = [s for s in filtered if sub_lower in s["title"].lower() or sub_lower in s.get("location", {}).get("suburb", "").lower()]

    if args.search:
        q_lower = args.search.lower()
        filtered = [
            s for s in filtered
            if q_lower in s["title"].lower()
            or q_lower in s.get("network", "").lower()
            or q_lower in s.get("location", {}).get("address", "").lower()
            or any(q_lower in kw.lower() for kw in s.get("_record", {}).get("plugshare_metadata", {}).get("keywords", []))
        ]

    if args.min_power > 0:
        filtered = [s for s in filtered if float(s.get("hardware", {}).get("max_power_kw", 0) or 0) >= args.min_power]

    if args.network:
        net_lower = args.network.lower()
        filtered = [s for s in filtered if net_lower in s.get("network", "").lower()]

    if has_explicit_proximity and active_radius and active_radius > 0:
        filtered = [s for s in filtered if s.get("_distance_km", float("inf")) <= active_radius]

    # 6. Sorting
    sort_mode_desc = None
    if args.sort == "dist" or (args.sort is None and has_explicit_proximity):
        filtered.sort(key=lambda s: s.get("_distance_km", float("inf")))
        sort_mode_desc = "Distance (Closest First)"
    elif args.sort in ("price", "rate"):
        filtered.sort(key=lambda s: (
            s.get("tariffs", {}).get("per_kwh_flat") if s.get("tariffs", {}).get("per_kwh_flat", 0) > 0 else float("inf"),
            s.get("_distance_km", float("inf"))
        ))
        sort_mode_desc = "Price (Lowest First, Closest Tie-Break)"
    elif args.sort == "power":
        filtered.sort(key=lambda s: (float(s.get("hardware", {}).get("max_power_kw", 0) or 0), -s.get("_distance_km", float("inf"))), reverse=True)
        sort_mode_desc = "Max Power (kW, Highest First)"
    elif args.sort == "stalls":
        filtered.sort(key=lambda s: (int(s.get("hardware", {}).get("stalls", 0) or 0), -s.get("_distance_km", float("inf"))), reverse=True)
        sort_mode_desc = "Stall Count (Highest First)"
    elif args.sort == "name":
        filtered.sort(key=lambda s: s.get("title", "").lower())
        sort_mode_desc = "Station Name (Alphabetical)"
    else:
        # No explicit --sort and no proximity reference to sort by distance:
        # default to State, then Station Name (alphabetical) instead of
        # leaving results in arbitrary API/registry order.
        filtered.sort(key=lambda s: (s.get("state", "-") or "-", s.get("title", "").lower()))
        sort_mode_desc = "State, then Station Name (Alphabetical)"

    # 7. Limit
    if args.limit and args.limit > 0:
        filtered = filtered[:args.limit]

    # 7b. Refresh Missing Prices
    # The bulk/region discovery endpoint (used by --near/--state/--search) does
    # not include pricing - only the single-station detail endpoint
    # (get_location_details) does. Stations already saved in the registry with
    # a known rate carry it forward automatically (see the merge in
    # add_or_update()), but anything not yet individually scraped shows up
    # with no rate ("-") until someone runs --plugshare/--inspect on it. This
    # flag does that for every currently-listed station missing a rate, in
    # one pass, and persists whatever it finds back into the registry.
    if args.refresh_prices:
        missing = [
            st for st in filtered
            if not (st.get("tariffs", {}).get("per_kwh_flat") or 0) > 0 and st.get("id")
        ]
        if not missing:
            print(f"\n{C_GREEN}✅ Every listed station already has a known rate - nothing to refresh.{C_RESET}")
        else:
            print(f"\n{C_CYAN}💲 Refreshing live pricing for {len(missing)} station(s) with no known rate (one PlugShare request each, ~{len(missing) * 1.5:.0f}s+)...{C_RESET}")
            refreshed = 0
            failed = 0
            for i, st in enumerate(missing, 1):
                loc_id = st["id"]
                print(f"  [{i}/{len(missing)}] {st.get('title', '?')}...", end=" ", flush=True)
                full_d = ps_client.get_location_details(loc_id)
                has_identity = isinstance(full_d, dict) and (full_d.get("id") or full_d.get("name") or full_d.get("latitude") is not None)
                if not has_identity:
                    reason = ps_client.last_error or "empty/unusable response"
                    print(f"{C_RED}failed - {reason}{C_RESET}")
                    failed += 1
                    # A rate-limit/block response tends to persist for the rest of
                    # the burst too - back off harder so the remaining requests in
                    # this run have a real chance instead of failing in lock-step.
                    if ps_client.last_error and ("rate-limited" in ps_client.last_error or "HTTP 429" in ps_client.last_error):
                        time.sleep(5.0 + random.uniform(0, 2.0))
                    else:
                        time.sleep(1.5 + random.uniform(0, 1.0))
                    continue
                k, rec = plugshare_to_registry_record(st.get("_raw") or {}, full_detail=full_d)
                new_rate = rec.get("tariffs", {}).get("per_kwh_flat", 0.0)
                print(f"{C_GREEN}${new_rate:.2f}/kWh{C_RESET}" if new_rate > 0 else f"{C_DIM}still unlisted on PlugShare.{C_RESET}")
                # Reflect it immediately in the table we're about to render...
                st["tariffs"] = rec.get("tariffs", {})
                st["_record"] = rec
                # ...and persist it so future runs don't need to re-fetch it.
                ex_k, _ = registry_obj.find_existing_match(k, rec, existing_registry)
                res = registry_obj.add_or_update(ex_k or k, rec, sync_external=args.sync)
                if res in ("CREATED", "UPDATED"):
                    existing_registry[ex_k or k] = rec
                    refreshed += 1
                time.sleep(1.5 + random.uniform(0, 1.0))  # be polite to PlugShare's API - a tight loop of these looks like scraping and can get rate-limited
            print(f"{C_GREEN}✅ Refreshed pricing for {refreshed}/{len(missing)} station(s) with a rate found.{C_RESET}")
            if failed:
                print(f"{C_YELLOW}⚠️  {failed}/{len(missing)} request(s) failed outright (see reasons above) - if most/all of them say the same thing, that's PlugShare rate-limiting the burst, not a real \"not found\". Re-run --refresh-prices again in a few minutes, or fall back to --plugshare <id> one at a time for the ones that still matter.{C_RESET}")

    # 8. Batch Add All Mode
    if args.add_all:
        added = 0
        for st in filtered:
            k = st["title"]
            rec = st["_record"]
            ex_k, _ = registry_obj.find_existing_match(k, rec, existing_registry)
            if not ex_k:
                res = registry_obj.add_or_update(k, rec, sync_external=args.sync)
                if res in ("CREATED", "UPDATED"):
                    added += 1
                    existing_registry[k] = rec
        print(f"\n{C_GREEN}✅ Added {added} new stations to registry.{C_RESET}")
        return

    # 9. Output JSON
    if args.json:
        out = [{
            "title": s["title"],
            "network": s["network"],
            "state": s["state"],
            "power_kw": s.get("hardware", {}).get("max_power_kw"),
            "stalls": s.get("hardware", {}).get("stalls"),
            "rate_per_kwh": s.get("tariffs", {}).get("per_kwh_flat"),
            "distance_km": s.get("_distance_km") if s.get("_distance_km") != float("inf") else None,
            "url": s["url"]
        } for s in filtered]
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return

    # 10. Render Table
    def render_table_cb():
        print_plugshare_chargers_table(
            filtered,
            ref_lat=ref_lat,
            ref_lon=ref_lon,
            ref_label=ref_label,
            active_radius=active_radius if has_explicit_proximity else None,
            eval_time_label="Current Local Time",
            sort_mode=sort_mode_desc,
            existing_registry=existing_registry
        )

    render_table_cb()

    # 11. Interactive or Non-Interactive Exit
    if sys.stdin.isatty():
        interactive_station_selector_loop(
            filtered,
            registry_obj,
            existing_registry,
            ps_client,
            sync_external=args.sync,
            re_render_cb=render_table_cb
        )
    else:
        print(f"\n{C_DIM}To inspect details:     ./Tools/find_plugshare_chargers.py --inspect <ID_or_Name_or_URL>{C_RESET}")
        print(f"{C_DIM}To batch add to JSON:   ./Tools/find_plugshare_chargers.py --country Australia --add-all{C_RESET}\n")

if __name__ == "__main__":
    main()
