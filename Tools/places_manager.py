#!/usr/bin/env python3
"""
Tesla / Tessie Location Management & POI Lookup Engine
======================================================
- Dedicated place manager for Tessie/places.json (leaves chargers alone)
- Inspect, filter, and search stored locations in a formatted table
- Interactive POI lookup by street address or GPS coordinates via OpenStreetMap / Overpass
- Drive stop clustering and interactive discovery from drives_master.csv (ignores charging stops)
- Automatic POI center coordinate resolution and custom coordinate/address override
- Strict Zero-PII compliance across all documentation and examples
"""

import os
from lib.timezones import legacy_get as _legacy_get  # BUG-176
import sys
import time
import re
import csv
import json
import math
import glob
import shutil
import tempfile
import argparse
import unicodedata
import urllib.request
import urllib.parse
import webbrowser
from datetime import datetime, timedelta
from collections import defaultdict
import textwrap
from typing import List, Tuple, Dict, Any, Optional, Set

# ---------------------------------------------------------------------------
# Path & Environment Setup
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
TESSIE_DIR = os.path.join(REPO_ROOT, "resources")
CONFIG_JSON_PATH = os.path.join(TESSIE_DIR, "config.json")
DRIVES_MASTER_PATH = os.path.join(TESSIE_DIR, "drives", "drives_master.csv")
CHARGING_JSON_PATH = os.path.join(TESSIE_DIR, "tesla_chargers.json") if os.path.isfile(os.path.join(TESSIE_DIR, "tesla_chargers.json")) else os.path.join(TESSIE_DIR, "charging.json")
SUPERCHARGERS_JSON_PATH = os.path.join(TESSIE_DIR, "tesla_superchargers.json") if os.path.isfile(os.path.join(TESSIE_DIR, "tesla_superchargers.json")) else os.path.join(TESSIE_DIR, "superchargers.json")

# places.json/ignored_places.json are real personal data (named places,
# home/work coordinates) - per AGENTS.md's Zero-PII / "PII Never Lives in
# the Repo Working Tree At All" rule (REQ-034), they resolve through the
# same external tessie_directory home every other real-data script uses,
# not a path hardcoded beside this script. sys.path[0] already has this
# directory (Tools/) when run directly, matching charger_find.py's own
# use of this same import (added 2026-09-14, real-data-in-tree audit).
try:
    from lib.tessie_api_common import resolve_tessie_data_home as _resolve_tessie_data_home
    _REAL_TESSIE_DIR = _resolve_tessie_data_home()
except Exception:
    _REAL_TESSIE_DIR = TESSIE_DIR
PLACES_JSON_PATH = os.path.join(_REAL_TESSIE_DIR, "places.json")
IGNORED_JSON_PATH = os.path.join(_REAL_TESSIE_DIR, "ignored_places.json")

# ---------------------------------------------------------------------------
# Unicode & Terminal Display Utilities
# ---------------------------------------------------------------------------
_tools_dir = os.path.dirname(os.path.abspath(__file__))
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

from lib.table_formatter import (
    char_width,
    display_len,
    truncate_display,
    pad_display,
    format_row,
    format_title_line,
    format_box_line,
    hyperlink,
)

def gmaps_url(lat, lon):
    """Google Maps URL for a coordinate pair, opening its pin directly."""
    return f"https://www.google.com/maps/search/?api=1&query={lat:.5f},{lon:.5f}"

# ---------------------------------------------------------------------------
# Geodesic Math
# ---------------------------------------------------------------------------
def format_dist_human(dist_m):
    """Formats distance in m (if < 1000m) or km (if >= 1000m)."""
    if dist_m is None:
        return ""
    if dist_m >= 1000:
        return f"{dist_m / 1000.0:.1f} km"
    return f"{int(round(dist_m))}m"


def haversine_distance(lat1, lon1, lat2, lon2):
    """Returns distance in meters between two lat/lon coordinates."""
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Places JSON Storage Manager (Only touches places.json, leaves chargers alone)
# ---------------------------------------------------------------------------
def validate_place_name(name: str) -> Tuple[bool, str]:
    """
    Validates a proposed place name.
    Rejects names that:
    - Are empty or whitespace
    - Start with './', '../', or '/' (file paths / CLI commands)
    - Contain script filenames (e.g. places_manager.py, analyse_drives.py, .sh, .py)
    - Contain CLI action verbs combined with script invocations
    - Consist purely of punctuation, box-drawing characters, or separator lines (===, ---, ───)
    Returns (is_valid, error_message).
    """
    if not name or not isinstance(name, str) or not name.strip():
        return False, "Place name cannot be empty."

    clean = name.strip()

    if len(clean) < 2:
        return False, "Place name is too short (minimum 2 characters)."

    # Check for CLI commands or script paths accidentally entered
    if clean.startswith(("./", "../", "/")):
        return False, f"Invalid place name \"{clean}\": looks like a file path or shell command."

    if re.search(r'\b(places_manager|analyse_drives|analyse_charging|sync_api|charger_find)\.(py|sh)\b', clean, re.I):
        return False, f"Invalid place name \"{clean}\": contains script filename."

    if re.search(r'^(python|python3|bash|zsh|sh|\./)\s+', clean, re.I):
        return False, f"Invalid place name \"{clean}\": looks like a shell command invocation."

    # Check for box-drawing / separator lines
    if re.match(r'^[=\-─_#*~+│├┤┬┴┼┌┐└┘]+$', clean):
        return False, f"Invalid place name \"{clean}\": contains only separator/box characters."

    return True, ""


def clean_place_keywords(keywords: List[str]) -> List[str]:
    """Filters out coordinate strings, separator lines, empty entries, and CLI artifacts."""
    cleaned = []
    for kw in keywords:
        if not kw or not isinstance(kw, str):
            continue
        k = kw.strip()
        if not k:
            continue
        if is_coord_string(k):
            continue
        if re.match(r'^[=\-─_#*~+│├┤┬┴┼┌┐└┘]+$', k):
            continue
        if k.startswith(("./", "../")) or re.search(r'\.(py|sh)\b', k, re.I):
            continue
        if k not in cleaned:
            cleaned.append(k)
    return cleaned


def load_places(path=PLACES_JSON_PATH):
    """Loads and returns places dictionary from places.json."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Error loading {path}: {e}")
        return {}


def save_places(places, path=PLACES_JSON_PATH):
    """Sorts keys, cleans keywords, and atomically saves places dictionary to places.json."""
    cleaned_places = {}
    for k, v in places.items():
        is_val, _ = validate_place_name(k)
        if not is_val:
            continue
        if isinstance(v, dict):
            entry = dict(v)
            if "keywords" in entry and isinstance(entry["keywords"], list):
                entry["keywords"] = clean_place_keywords(entry["keywords"])
            cleaned_places[k] = entry
        else:
            cleaned_places[k] = v

    sorted_places = dict(sorted(cleaned_places.items(), key=lambda x: x[0].lower()))
    
    dir_name = os.path.dirname(os.path.abspath(path))
    os.makedirs(dir_name, exist_ok=True)
    
    temp_fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix="places_", suffix=".json")
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(sorted_places, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(temp_path, path)
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise e

    return True

# ---------------------------------------------------------------------------
# Ignored Stop Clusters (clusters the user has decided aren't worth naming)
# ---------------------------------------------------------------------------
def load_ignored_places(path=IGNORED_JSON_PATH):
    """Loads the list of previously-ignored stop clusters from ignored_places.json."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"❌ Error loading {path}: {e}")
        return []

def save_ignored_places(entries, path=IGNORED_JSON_PATH):
    """Atomically saves the list of ignored stop clusters to ignored_places.json."""
    dir_name = os.path.dirname(os.path.abspath(path))
    os.makedirs(dir_name, exist_ok=True)

    temp_fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix="ignored_", suffix=".json")
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(temp_path, path)
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise e

def is_cluster_ignored(cl, ignored_entries):
    """True if cl's center falls within any ignored entry's radius (same distance-match rule as places.json)."""
    for ig in ignored_entries:
        ig_lat, ig_lon = ig.get("center_lat"), ig.get("center_lon")
        ig_rad = ig.get("radius_m", 100)
        if ig_lat is not None and ig_lon is not None:
            if haversine_distance(cl["center_lat"], cl["center_lon"], ig_lat, ig_lon) <= ig_rad:
                return True
    return False

# ---------------------------------------------------------------------------
# Resolution Engine (Matches places, chargers; detects unlabelled stops)
# ---------------------------------------------------------------------------
def resolve_stop(addr, saved_loc, lat, lon, places, chargers):
    """
    Returns matched place name, or None if unlabelled.
    Gives GPS geofence priority, followed by charging stations and keywords.
    """
    if saved_loc and saved_loc in places:
        return saved_loc
    
    if lat is not None and lon is not None:
 # 1. GPS geofence in places.json
        best_place, best_dist = None, 999999
        for p_name, p_data in places.items():
            p_lat = p_data.get("lat")
            p_lon = p_data.get("lon")
            p_rad = p_data.get("radius_m", 150)
            if p_lat is not None and p_lon is not None:
                d = haversine_distance(lat, lon, p_lat, p_lon)
                if d <= p_rad and d < best_dist:
                    best_place = p_name
                    best_dist = d
        if best_place:
            return best_place

 # 2. GPS geofence in chargers (superchargers / destination)
        for c_name, c_data in chargers.items():
            c_lat = c_data.get("lat")
            c_lon = c_data.get("lon")
            if c_lat is not None and c_lon is not None:
                d = haversine_distance(lat, lon, c_lat, c_lon)
                if d <= 250:
                    return c_name

 # 3. Keyword matching against places.json
    if addr:
        addr_clean = addr.lower()
        for p_name, p_data in places.items():
            for kw in p_data.get("keywords", []):
                if kw and len(kw) >= 3 and kw.lower() in addr_clean:
                    if lat is not None and lon is not None:
                        p_lat = p_data.get("lat")
                        p_lon = p_data.get("lon")
                        if p_lat is not None and p_lon is not None:
                            d = haversine_distance(lat, lon, p_lat, p_lon)
                            if d > 1000:
                                continue
                    return p_name

    return None

# ---------------------------------------------------------------------------
# POI Lookup Engine (Nominatim + Overpass)
# ---------------------------------------------------------------------------
USER_AGENT = "TeslaPlacesManager/1.0 (https://github.com/inodes/tesla)"

AU_STATE_MAP = {
    "New South Wales": "NSW",
    "Victoria": "VIC",
    "Queensland": "QLD",
    "South Australia": "SA",
    "Western Australia": "WA",
    "Tasmania": "TAS",
    "Australian Capital Territory": "ACT",
    "Northern Territory": "NT"
}

OVERPASS_ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]


def _nearby_setting(key, default):
    """config.json "app.nearby.<key>" (see lib/app_config.py), else the default."""
    try:
        from lib import app_config
        return app_config.get("nearby." + key, default)
    except Exception:
        return default


OVERPASS_ERROR = ""     # why the last query_overpass_pois() got no answer ("" = it was answered, even if there was nothing there)

def prompt_radius_input(default_radius=150, label="Radius in meters"):
    """
    Prompts the user for a radius in meters with validation and friendly error re-prompting.
    Accepts raw numbers (e.g. 50) or values with units (e.g. 50m, 50 meters).
    """
    while True:
        try:
            r_input = input(f"{label} [default {default_radius}m]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return default_radius
        if not r_input:
            return default_radius
        clean_r = re.sub(r'(?i)\s*(m|meters|meter|metres|metre)$', '', r_input).strip()
        if clean_r.isdigit() and int(clean_r) > 0:
            return int(clean_r)
        print(f"⚠️  Invalid radius \"{r_input}\". Please enter a positive number of meters (e.g. 50 or 50m).")

_POI_MEMORY_CACHE = {}


def geocode_candidates(query, country_hint="Australia", anchor_coords=None, limit=10):
    """
    Finds geographic location candidates in Australia via Nominatim / Photon.
    Returns a list of structured candidate dicts:
    [{'lat': float, 'lon': float, 'label': str, 'state': str, 'name': str, 'dist_km': float, 'display_name': str, 'raw': dict}, ...]
    Deduplicated by vicinity and sorted by distance to anchor_coords (e.g. Home).
    """
    if not query or not query.strip():
        return []

    clean = query.strip()
    q_str = clean if country_hint.lower() in clean.lower() else f"{clean}, {country_hint}"
    
    candidates = []
    
 # 1. Nominatim (OpenStreetMap)
    try:
        params = urllib.parse.urlencode({
            "q": q_str,
            "format": "json",
            "addressdetails": 1,
            "extratags": 1,
            "countrycodes": "au",
            "limit": limit
        })
        url = f"https://nominatim.openstreetmap.org/search?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list):
                for item in data:
                    lat = float(item.get("lat", 0))
                    lon = float(item.get("lon", 0))
                    addr = item.get("address", {})
                    
                    iso_state = addr.get("ISO3166-2-lvl4", "")
                    state_code = iso_state.replace("AU-", "") if iso_state.startswith("AU-") else AU_STATE_MAP.get(addr.get("state"), addr.get("state") or "")
                    
                    raw_name = item.get("name") or ""
                    name = addr.get("suburb") or addr.get("neighbourhood") or addr.get("political") or addr.get("quarter") or addr.get("town") or raw_name or addr.get("city") or clean
                    name = name.replace(" District", "").replace(" Shire", "").strip()
                    
                    disp = item.get("display_name", "")
                    q_lower = clean.lower()
                    if "," not in clean and q_lower not in name.lower():
                        continue
                        
                    label = f"{name}, {state_code}" if state_code else name
                    dist_km = haversine_distance(anchor_coords[0], anchor_coords[1], lat, lon) / 1000.0 if anchor_coords else None

                    candidates.append({
                        "lat": lat,
                        "lon": lon,
                        "label": label,
                        "state": state_code,
                        "name": name,
                        "dist_km": dist_km,
                        "display_name": disp,
                        "raw": item
                    })
    except Exception:
        pass

 # 2. Photon (Komoot) fallback if Nominatim returned 0 candidates
    if not candidates:
        try:
            params = urllib.parse.urlencode({"q": q_str, "limit": limit})
            url = f"https://photon.komoot.io/api/?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                pdata = json.loads(resp.read().decode("utf-8"))
                for feat in pdata.get("features", []):
                    coords = feat.get("geometry", {}).get("coordinates", [])
                    if len(coords) < 2:
                        continue
                    lon = float(coords[0])
                    lat = float(coords[1])
                    props = feat.get("properties", {})
                    state = props.get("state") or ""
                    state_code = AU_STATE_MAP.get(state, state)
                    raw_name = props.get("name") or props.get("street") or props.get("city") or clean
                    name = raw_name.replace(" District", "").replace(" Shire", "").strip()
                    label = f"{name}, {state_code}" if state_code else name
                    dist_km = haversine_distance(anchor_coords[0], anchor_coords[1], lat, lon) / 1000.0 if anchor_coords else None
                    candidates.append({
                        "lat": lat,
                        "lon": lon,
                        "label": label,
                        "state": state_code,
                        "name": name,
                        "dist_km": dist_km,
                        "display_name": f"{label}, Australia",
                        "raw": feat
                    })
        except Exception:
            pass

 # Deduplicate candidates within 25km of each other or with identical label
    deduped = []
    for c in candidates:
        is_dup = False
        for d in deduped:
            if c["label"] == d["label"] or (haversine_distance(c["lat"], c["lon"], d["lat"], d["lon"]) < 25000.0):
                is_dup = True
                break
        if not is_dup:
            deduped.append(c)

 # Proximity sort if anchor coords given
    if anchor_coords:
        deduped.sort(key=lambda x: x["dist_km"] if x["dist_km"] is not None else 999999)

    return deduped


def resolve_location_disambiguated(query, anchor_coords=None, interactive=True, country_hint="Australia"):
    """
    Resolves an address/suburb query with interactive disambiguation if multiple distinct locations match.
    Returns (lat: float, lon: float, label: str) or (None, None, "").
    """
    if not query or not query.strip():
        return None, None, ""

    clean = query.strip()
    candidates = geocode_candidates(clean, country_hint=country_hint, anchor_coords=anchor_coords)
    
    if not candidates:
        return None, None, ""

 # Check for direct label match (e.g. user specified "Richmond, NSW" or "West Ryde, NSW")
    for c in candidates:
        if c["label"].lower() == clean.lower():
            return c["lat"], c["lon"], c["label"]

 # If only 1 candidate exists, return it directly
    if len(candidates) == 1:
        return candidates[0]["lat"], candidates[0]["lon"], candidates[0]["label"]

 # Multiple candidates found
 # Interactive prompt if running in interactive terminal
    if interactive and sys.stdin and sys.stdin.isatty():
        print(f"\n📍 Multiple locations found for '{clean}':")
        for i, c in enumerate(candidates[:5]):
            dist_str = f" [{c['dist_km']:.0f} km away]" if c.get("dist_km") is not None else ""
            print(f"   [ {i+1} ] {c['label']} ({c['lat']:.4f}, {c['lon']:.4f}){dist_str}")
        cancel_idx = min(len(candidates), 5) + 1
        print(f"   [ {cancel_idx} ] None of the above - please be more specific (e.g. --near \"{clean}, NSW\")\n")
        
        try:
            choice = input(f"Select location [1-{cancel_idx-1}, default 1]: ").strip()
            if not choice:
                selected = candidates[0]
                return selected["lat"], selected["lon"], selected["label"]
            if choice.isdigit():
                idx = int(choice)
                if 1 <= idx <= min(len(candidates), 5):
                    selected = candidates[idx - 1]
                    return selected["lat"], selected["lon"], selected["label"]
                else:
                    return None, None, ""
            else:
                return None, None, ""
        except (KeyboardInterrupt, EOFError):
            print()
            return None, None, ""

 # Non-interactive fallback: pick closest candidate and log informative message
    top = candidates[0]
    dist_str = f" ({top['dist_km']:.0f} km away)" if top.get("dist_km") is not None else ""
    print(f"ℹ️  Multiple locations found for '{clean}'; defaulted to closest: {top['label']}{dist_str}. (Specify '{clean}, <State>' to override).")
    return top["lat"], top["lon"], top["label"]


def geocode_address(address_str):
    """Geocodes a street address or place name string to structured dict via geocode_candidates."""
    if not address_str or not address_str.strip():
        return None
    cands = geocode_candidates(address_str, limit=5)
    if cands:
        first = cands[0]
        return {
            "lat": first["lat"],
            "lon": first["lon"],
            "display_name": first["display_name"],
            "label": first["label"],
            "state": first["state"],
            "name": first["name"],
            "type": first["raw"].get("type", "") if isinstance(first.get("raw"), dict) else "",
            "class": first["raw"].get("class", "") if isinstance(first.get("raw"), dict) else "",
            "address": first["raw"].get("address", {}) if isinstance(first.get("raw"), dict) else {},
            "raw": first["raw"]
        }
    return None

def reverse_geocode(lat, lon):
    """Reverse geocodes lat, lon to structured address and place name via Nominatim."""
    url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&addressdetails=1&extratags=1&namedetails=1"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data:
                return {
                    "name": data.get("namedetails", {}).get("name") or data.get("name") or "",
                    "display_name": data.get("display_name", ""),
                    "category": data.get("category") or data.get("class", ""),
                    "type": data.get("type", ""),
                    "address": data.get("address", {}),
                    "raw": data
                }
    except Exception:
        pass
    return None

def prompt_poi_naming(base_name, category):
    """
    Prompts the user on whether to keep the pure name or include the category in brackets.
    E.g. [ 1 ] Ron Routley Oval (default)
         [ 2 ] Ron Routley Oval (Pitch)
         [ c ] Enter custom name
    """
    clean_base = (base_name or "").strip()
    clean_cat = (category or "").strip()

    if not clean_cat or clean_cat.lower() in clean_base.lower():
        return clean_base

    name_with_cat = f"{clean_base} ({clean_cat})"
    print(f"\n🏷️  Select place name format:")
    print(f"  [ 1 ] {clean_base} (default)")
    print(f"  [ 2 ] {name_with_cat}")
    print(f"  [ c ] Enter custom name")

    try:
        n_choice = input("Choice [1]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return clean_base

    if n_choice == "2":
        return name_with_cat
    elif n_choice in ("c", "custom"):
        try:
            custom_val = input(f"Enter custom name [default: {clean_base}]: ").strip()
            return custom_val if custom_val else clean_base
        except (EOFError, KeyboardInterrupt):
            return clean_base
    return clean_base


def classify_poi_group(tags):
    """Classifies OSM tags into user-friendly top-level facility groups.

    REQ-099 (2026-09-22): rewritten after BUG-145 exposed that this
    function - like query_overpass_pois() right below it - had drifted
    across 4 separate module-level copies of itself (this file's own
    duplication debt, see query_overpass_pois()'s docstring), and even
    the one copy that's actually reachable had silently regressed
    relative to its dead siblings (missing the boating group and the
    landuse retail check that 3 of the 4 copies had). Also found: `office`
    was read into a local variable in every single copy but never once
    checked by any branch - so every real office=* venue (lawyer,
    accountant, real estate agent, ...) has always fallen through to
    "Other Facilities" here, the same silent-drop shape as BUG-145's
    craft=* gap, just never previously noticed. `craft` (beyond the
    winery/brewery case) and `historic` had no group of their own either.
    This is a single, deliberately broader rewrite - not a patch - so
    the recognized-category set here matches query_overpass_pois()'s own
    expanded set below, rather than the two silently drifting apart again.
    """
    amenity = (tags.get("amenity") or "").lower()
    shop = (tags.get("shop") or "").lower()
    cuisine = (tags.get("cuisine") or "").lower()
    leisure = (tags.get("leisure") or "").lower()
    tourism = (tags.get("tourism") or "").lower()
    healthcare = (tags.get("healthcare") or "").lower()
    office = (tags.get("office") or "").lower()
    craft = (tags.get("craft") or "").lower()
    historic = (tags.get("historic") or "").lower()
    sport = (tags.get("sport") or "").lower()
    man_made = (tags.get("man_made") or "").lower()
    waterway = (tags.get("waterway") or "").lower()
    landuse = (tags.get("landuse") or "").lower()
    building = (tags.get("building") or "").lower()

    name = (tags.get("name") or "").lower()
    brand = (tags.get("brand") or "").lower()

    supermarket_brands = ("woolworths", "coles", "aldi", "iga", "harris farm", "costco", "foodland", "drakes", "spudshed", "kmart", "target", "big w")
    if any(b in name or b in brand for b in supermarket_brands):
        return "Supermarkets & Groceries", "🛒"

    # Wineries, breweries & distilleries - the exact case that triggered this
    # rewrite (craft=winery had nowhere of its own to go and fell to
    # "Other Facilities"). Checked early so it wins over the more generic
    # dining/retail buckets below for a venue tagged both ways (e.g. a
    # cellar-door restaurant with amenity=restaurant + craft=winery).
    if craft in ("winery", "brewery", "distillery", "beverage_production") or tourism == "wine_cellar" or amenity == "brewery":
        return "Wineries & Breweries", "🍷"

    if leisure in ("slipway", "marina") or man_made in ("pier", "wharf", "jetty", "pontoon") or waterway in ("boat_ramp", "dock", "wharf") or "boat" in amenity or sport in ("sailing", "rowing", "kayak", "canoe", "boating"):
        return "Boating, Wharves & Ramps", "⛵"

    if amenity in ("cafe", "coffee_shop") or shop in ("coffee", "tea") or "coffee" in cuisine:
        return "Cafes & Coffee", "☕"

    if amenity in ("restaurant", "fast_food", "food_court", "pub", "bar", "biergarten", "ice_cream"):
        return "Dining & Fast Food", "🍔"

    if shop in ("supermarket", "convenience", "bakery", "butcher", "greengrocer", "deli", "grocery", "general", "department_store", "mall", "farm", "health_food", "seafood", "cheese", "confectionery", "chocolate", "frozen_food", "pastry", "nuts", "spices"):
        return "Supermarkets & Groceries", "🛒"

    # BUG-172 (2026-09-30): landuse=retail/commercial and building=retail/
    # commercial only describe the land or the building, not what the venue
    # is. They used to win over the venue's own tags, so at the Big Banana
    # (Coffs Harbour) a theme park, a motel and a laser-tag centre all
    # counted as "shops". Only fall back to them when nothing more specific
    # is tagged.
    has_specific = bool(amenity or tourism or leisure or healthcare or office or craft or historic or sport)

    if (landuse in ("retail", "commercial") and not has_specific) or shop == "mall" or any(s in name for s in ("town centre", "town center", "shopping centre", "shopping center", "shopping plaza", "shopping mall", "homemaker centre", "marketplace")):
        return "Retail & Shopping", "🛍️"

    if shop in ("hairdresser", "beauty", "massage", "tattoo", "piercing", "optician", "cosmetics", "chemist", "hearing_aids", "perfumery"):
        return "Health & Beauty", "💇"

    if shop or (building in ("retail", "commercial") and not has_specific) or amenity == "marketplace":
        return "Retail & Shopping", "🛍️"

    if amenity in ("toilets", "restroom", "drinking_water", "atm", "bank", "bureau_de_change", "pharmacy", "post_box", "post_office", "library", "community_centre", "place_of_worship", "social_facility", "townhall", "courthouse", "police", "fire_station"):
        return "Amenities & Services", "🚻"

    if tourism in ("hotel", "motel", "guest_house", "hostel", "apartment", "camp_site", "caravan_site", "chalet", "alpine_hut", "wilderness_hut"):
        return "Lodging & Hotels", "🏨"

    if amenity in ("fuel", "car_wash", "parking", "charging_station", "car_rental", "car_sharing", "vehicle_inspection", "compressed_air") or shop in ("car", "car_repair", "car_parts", "tyres", "motorcycle"):
        return "Automotive & Parking", "🚗"

    if healthcare or amenity in ("hospital", "clinic", "doctors", "doctor", "dentist", "veterinary"):
        return "Healthcare", "🏥"

    if office:
        return "Offices & Professional Services", "💼"

    if craft:
        return "Trades & Craft Services", "🔨"

    if tourism in ("attraction", "museum", "gallery", "viewpoint", "zoo", "aquarium", "artwork", "theme_park", "picnic_site"):
        return "Attractions & Culture", "📸"

    if historic:
        return "Historic Sites", "🏛️"

    if leisure in ("fitness_centre", "sports_centre", "sports_hall", "golf_course", "swimming_pool", "bowling_alley", "ice_rink", "stadium", "miniature_golf", "trampoline_park", "water_park") or sport:
        return "Sports & Fitness", "🏋️"

    if leisure in ("park", "playground", "nature_reserve", "garden", "dog_park", "pitch", "track", "picnic_table"):
        return "Parks & Recreation", "🌳"

    if leisure or amenity in ("cinema", "theatre", "arts_centre"):
        return "Leisure & Entertainment", "🎯"

    return "Other Facilities", "📍"


def query_overpass_pois(lat, lon, radius_m=500):
    """Queries Overpass API for all named POIs / venues within radius_m of lat, lon.

    Duplication note (BUG-140/145, still true): this file has FOUR
    separate module-level `def query_overpass_pois` definitions (lines
    ~642, ~1075, ~1501, and this one) and FOUR of `classify_poi_group`
    right above each - only the last pair (this one) is ever reachable;
    the other three are dead code, each having independently drifted out
    of sync with this one over time (that drift is exactly how BUG-140's
    missing radius guard and REQ-099's missing boating/office/craft
    groups happened - a fix landing in one copy silently never reached
    the others). Not consolidated here - that's a bigger, separate
    cleanup touching several unrelated duplicated functions in this same
    file (geocode_candidates, resolve_location_disambiguated,
    geocode_address, reverse_geocode, prompt_poi_naming are ALSO each
    duplicated 3x alongside this pair, discovered while scoping this fix -
    flagged, not touched, deliberately out of scope for a POI-
    categorization fix).

    REQ-099 (2026-09-22): category detection used to only recognize a
    hand-picked handful of top-level keys (amenity/shop/leisure/tourism/
    office/healthcare, then craft after BUG-145). Any real, named,
    in-range OSM feature tagged with something outside that list was
    silently dropped, at any radius, cache or no cache - discovered from
    Cassegrain Winery (craft=winery only) having been invisible to this
    tool since a 2021 retag. Now recognizes every top-level key that
    denotes a distinct kind of named place a driver might reasonably
    want to know is nearby (amenity, shop, tourism, leisure, office,
    healthcare, craft, historic, sport), plus a small curated set of
    specific man_made/waterway values (pier, wharf, jetty, pontoon,
    boat_ramp, dock) - deliberately NOT the bare man_made/waterway keys,
    which are mostly noisy infrastructure (water towers, masts, drains)
    that isn't a "facility" in this sense. classify_poi_group() above was
    rewritten in step so nothing newly-detected here dead-ends in an
    uninformative "Other Facilities" bucket.
    """
    query = f"""
    [out:json][timeout:20];
    (
      node(around:{radius_m},{lat},{lon})["name"];
      way(around:{radius_m},{lat},{lon})["name"];
      relation(around:{radius_m},{lat},{lon})["name"];
    );
    out center tags;
    """
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, */*",
    }
    
    global OVERPASS_ERROR
    OVERPASS_ERROR = "no OpenStreetMap server answered"
    total = float(_nearby_setting("overpass_total_timeout_s", 20))      # give up after this long in total: the servers are either up or not
    per = float(_nearby_setting("overpass_server_timeout_s", 8))
    errs, t_end = [], time.time() + total
    for endpoint in (_nearby_setting("overpass_endpoints", None) or OVERPASS_ENDPOINTS):
        if time.time() > t_end:
            errs.append("gave up after %d s" % total)
            break
        try:
            req = urllib.request.Request(endpoint, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=max(3, min(per, t_end - time.time()))) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                elements = res_data.get("elements", [])
                
                results = []
                seen_names = set()
                for el in elements:
                    tags = el.get("tags", {})
                    name = tags.get("name", "").strip()
                    if not name:
                        continue
                    
                    amenity = tags.get("amenity")
                    shop = tags.get("shop")
                    leisure = tags.get("leisure")
                    tourism = tags.get("tourism")
                    office = tags.get("office")
                    healthcare = tags.get("healthcare")
                    craft = tags.get("craft")
                    historic = tags.get("historic")
                    sport = tags.get("sport")
                    man_made = tags.get("man_made")
                    waterway = tags.get("waterway")
                    building = tags.get("building")
                    brand = tags.get("brand")

 # Filter out charging station self-matches
                    if amenity == "charging_station" or "supercharger" in name.lower() or name.lower().startswith("tesla super"):
                        continue

                    category = (
                        amenity or shop or leisure or tourism or office or
                        healthcare or craft or historic or sport
                    )
                    if not category and man_made in ("pier", "wharf", "jetty", "pontoon"):
                        category = man_made
                    if not category and waterway in ("boat_ramp", "dock", "wharf"):
                        category = waterway
                    if not category and building:
                        category = f"building ({building})" if building != "yes" else "building"
                    if not category and tags.get("landuse") in ("industrial", "commercial", "retail"):
                        category = f"{tags.get('landuse')} area"
                    
                    if not category and not brand:
                        continue

                    if name.lower() in seen_names:
                        continue
                    seen_names.add(name.lower())

                    p_lat = el.get("lat") or el.get("center", {}).get("lat", lat)
                    p_lon = el.get("lon") or el.get("center", {}).get("lon", lon)
                    dist = haversine_distance(lat, lon, p_lat, p_lon)

 # BUG-140: a way/relation's centroid can sit outside radius_m even
 # though Overpass's own around: filter matched some part of its
 # geometry - see this function's docstring. Restored the guard the
 # other (dead, unreachable) copies of this function each had.
                    if dist > radius_m:
                        continue

                    cat_label = (category or "business").replace("_", " ").title()
                    if brand and brand.lower() != name.lower():
                        cat_label += f" ({brand})"

                    group_name, group_emoji = classify_poi_group(tags)

 # BUG-151: cache only ever stored the COMPUTED name/category/group/emoji,
 # never the raw tags classify_poi_group() actually read - so when its rules
 # changed (REQ-099), every cached record became permanently un-recomputable
 # without a live Overpass refetch (BUG-146's CATEGORY_SCHEMA_CUTOFF forces
 # exactly that, at real cost - see BUG-149/BUG-150). Saving just the handful
 # of tag values classify_poi_group() actually consults means a FUTURE
 # classification change can be applied to every cached record instantly and
 # offline (see station_facilities_common.py's reclassify_cached_facilities()) -
 # this is the last time a rule change has to cost a live refetch.
                    raw_tags = {
                        k: v for k, v in {
                            "amenity": amenity, "shop": shop, "cuisine": tags.get("cuisine"),
                            "leisure": leisure, "tourism": tourism, "office": office,
                            "healthcare": healthcare, "craft": craft, "historic": historic,
                            "sport": sport, "man_made": man_made, "waterway": waterway,
                            "landuse": tags.get("landuse"), "building": building,
                            "brand": brand, "name": name,
                        }.items() if v
                    }

                    results.append({
                        "name": name,
                        "category": cat_label,
                        "group": group_name,
                        "emoji": group_emoji,
                        "lat": p_lat,
                        "lon": p_lon,
                        "dist_m": round(dist, 1),
                        "raw_tags": raw_tags
                    })

                results.sort(key=lambda x: x["dist_m"])
                OVERPASS_ERROR = ""
                return results
        except Exception as ex:
            errs.append("%s: %s" % (endpoint.split("/")[2], str(ex)[:70]))
            OVERPASS_ERROR = "OpenStreetMap (Overpass) did not answer. " + "; ".join(errs)
            continue
    return []


def search_places_by_name(query, near_lat=None, near_lon=None, limit=7, max_radius_m=350):
    """
    Proximity-first name/text search for places strictly within the immediate cluster vicinity:
    1. First queries local Overpass POIs around near_lat, near_lon (within max_radius_m).
    2. Falls back to Nominatim with a tight localized bounding box, discarding anything beyond max_radius_m.
    Returns a list of dicts sorted strictly by distance (closest first):
    [{'name': str, 'category': str, 'lat': float, 'lon': float, 'dist_m': float, 'display_name': str}, ...]
    """
    if not query or not query.strip():
        return []

    q_clean = query.strip().lower()
    results = []
    seen_keys = set()

 # 1. Local Overpass search (within max_radius_m of cluster)
    if near_lat is not None and near_lon is not None:
        try:
            local_pois = query_overpass_pois(near_lat, near_lon, radius_m=max_radius_m)
            for poi in local_pois:
                name = poi.get("name", "")
                cat = poi.get("category", "")
                tags_str = " ".join(str(v) for v in poi.get("tags", {}).values()).lower()
                if q_clean in name.lower() or q_clean in cat.lower() or q_clean in tags_str:
                    p_lat = poi["lat"]
                    p_lon = poi["lon"]
                    dist_m = poi["dist_m"]
                    key = f"{name.lower()}_{round(p_lat, 4)}_{round(p_lon, 4)}"
                    if key not in seen_keys:
                        seen_keys.add(key)
                        results.append({
                            "name": name,
                            "category": cat or "POI",
                            "lat": p_lat,
                            "lon": p_lon,
                            "dist_m": dist_m,
                            "display_name": f"{name} ({cat})",
                        })
        except Exception:
            pass

 # 2. Localized Nominatim Search with tight proximity bounding box (max ~max_radius_m)
    if near_lat is not None and near_lon is not None:
        r_km = max(0.4, max_radius_m / 1000.0)
        d = r_km / 111.0  # ~111km per degree of latitude
        params = {
            "q": query.strip(),
            "format": "json",
            "addressdetails": 1,
            "limit": limit,
            "viewbox": f"{near_lon - d},{near_lat + d},{near_lon + d},{near_lat - d}",
            "bounded": 1
        }
        url = f"https://nominatim.openstreetmap.org/search?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for item in data or []:
                    try:
                        p_lat = float(item.get("lat"))
                        p_lon = float(item.get("lon"))
                    except (TypeError, ValueError):
                        continue
                    dist_m = round(haversine_distance(near_lat, near_lon, p_lat, p_lon), 1)
 # Hard cap: ignore anything outside max_radius_m
                    if dist_m > max_radius_m:
                        continue
                    display_name = item.get("display_name", "")
                    name = display_name.split(",")[0].strip() if display_name else query.strip()
                    category = (item.get("type") or item.get("class") or "").replace("_", " ").title()
                    key = f"{name.lower()}_{round(p_lat, 4)}_{round(p_lon, 4)}"
                    if key not in seen_keys:
                        seen_keys.add(key)
                        results.append({
                            "name": name,
                            "category": category or "Place",
                            "lat": p_lat,
                            "lon": p_lon,
                            "dist_m": dist_m,
                            "display_name": display_name,
                        })
        except Exception:
            pass

    if any(r["dist_m"] is not None for r in results):
        results.sort(key=lambda r: r["dist_m"] if r["dist_m"] is not None else float("inf"))
    return results[:limit]

def is_coord_string(s):
    """Returns True if string represents coordinates, coordinate pairs, or coordinate URLs rather than a street address."""
    if not s:
        return False
    c = str(s).strip("()[]{}'\" \t\r\n")
    if c.lower().startswith("geo:"):
        c = c[4:].strip()
    return bool(re.match(r'^[+-]?\d+(?:\.\d+)?(?:[,\s]+[+-]?\d+(?:\.\d+)?)?$', c)) or bool(re.search(r'[@?&q=][+-]?\d+\.\d+[,/][+-]?\d+\.\d+', c))

def suggest_keywords(address_str, poi_name=""):
    """Extracts clean street and address keywords from a raw address string, rejecting coordinates."""
    keywords = []
    if address_str and not is_coord_string(address_str):
        parts = [p.strip() for p in re.split(r'[,;]', address_str) if p.strip()]
        if parts:
            street_part = parts[0]
            if not is_coord_string(street_part):
                keywords.append(street_part)
                m = re.match(r'^\d+[\w\-/]*\s+(.+)$', street_part)
                if m:
                    street_only = m.group(1).strip()
                    if street_only and not is_coord_string(street_only) and street_only not in keywords:
                        keywords.append(street_only)
                for p in list(keywords):
                    abbr = p.replace(" Road", " Rd").replace(" Street", " St").replace(" Avenue", " Ave").replace(" Highway", " Hwy")
                    if abbr != p and abbr not in keywords:
                        keywords.append(abbr)

    if poi_name and len(poi_name) > 3 and not is_coord_string(poi_name) and poi_name not in keywords:
        is_val, _ = validate_place_name(poi_name)
        if is_val:
            keywords.append(poi_name)

    return clean_place_keywords(keywords)

def parse_coords_or_address(user_input):
    """
    Parses 'lat, lon' coordinate string or geocodes an address/name string.
    Supports raw floats, parentheses, brackets, Google Maps URLs, and geo: URIs.
    Returns (lat, lon, display_name) or (None, None, None).
    """
    if not user_input or not user_input.strip():
        return None, None, None
    
    clean = user_input.strip()

 # 1. URL pattern (e.g. google maps links or geo: URIs)
    url_match = re.search(r'[@?&q=]([+-]?\d+\.\d+)[,/]([+-]?\d+\.\d+)', clean)
    if url_match:
        lat = float(url_match.group(1))
        lon = float(url_match.group(2))
        rev = reverse_geocode(lat, lon)
        addr = rev.get("display_name", "") if rev else ""
        return lat, lon, addr

 # 2. Stripped of enclosing brackets, parentheses, or quotes
    cleaned_input = clean.strip("()[]{}'\" \t\r\n")
    if cleaned_input.lower().startswith("geo:"):
        cleaned_input = cleaned_input[4:].strip()

    coord_match = re.match(r'^\s*([+-]?\d+(?:\.\d+)?)[,\s]+([+-]?\d+(?:\.\d+)?)\s*$', cleaned_input)
    if coord_match:
        lat = float(coord_match.group(1))
        lon = float(coord_match.group(2))
        rev = reverse_geocode(lat, lon)
        addr = rev.get("display_name", "") if rev else ""
        return lat, lon, addr
    
    geo = geocode_address(clean)
    if geo:
        return geo["lat"], geo["lon"], geo["display_name"]
    return None, None, None

# ---------------------------------------------------------------------------
# Interactive POI Lookup & Confirmation Workflow
# ---------------------------------------------------------------------------
def interactive_lookup_and_add(query_or_addr=None, lat=None, lon=None, default_radius=150):
    """
    Interactively resolves an address or GPS coordinate, displays associated drive history immediately,
    and delays OSM / Places API lookup until explicitly requested via [s].
    """
    places = load_places()

    resolved_lat = lat
    resolved_lon = lon
    resolved_addr = query_or_addr or ""
    existing_name = None
    existing_place_data = None
    initial_radius = max(150, default_radius)

    # 1. Prioritize direct lookup against existing places in places.json
    if query_or_addr and places and (resolved_lat is None or resolved_lon is None):
        q_clean = query_or_addr.strip()
        matched_entry = None
        if q_clean in places:
            matched_entry = (q_clean, places[q_clean])
        else:
            q_lower = q_clean.lower()
            for p_name, p_data in places.items():
                if p_name.lower() == q_lower:
                    matched_entry = (p_name, p_data)
                    break
                kws = [k.lower() for k in p_data.get("keywords", []) if isinstance(k, str)]
                if q_lower in kws:
                    matched_entry = (p_name, p_data)
                    break

        if matched_entry:
            existing_name, existing_place_data = matched_entry
            p_lat = existing_place_data.get("lat")
            p_lon = existing_place_data.get("lon")
            if p_lat is not None and p_lon is not None:
                resolved_lat = p_lat
                resolved_lon = p_lon
                resolved_addr = existing_place_data.get("address") or existing_place_data.get("display_address") or ""
                if not resolved_addr:
                    rev = reverse_geocode(resolved_lat, resolved_lon)
                    resolved_addr = rev.get("display_name", "") if rev else existing_name
                initial_radius = existing_place_data.get("radius_m", default_radius)

    if resolved_lat is None or resolved_lon is None:
        if not query_or_addr:
            try:
                c_in = input("Enter central GPS (lat, lon) or address to lookup (or Enter to cancel): ").strip()
                if not c_in:
                    return None
                query_or_addr = c_in
                if query_or_addr in places:
                    existing_name = query_or_addr
                    existing_place_data = places[query_or_addr]
                    resolved_lat = existing_place_data.get("lat")
                    resolved_lon = existing_place_data.get("lon")
                    resolved_addr = existing_place_data.get("address") or existing_place_data.get("display_address") or ""
                    if not resolved_addr and resolved_lat is not None and resolved_lon is not None:
                        rev = reverse_geocode(resolved_lat, resolved_lon)
                        resolved_addr = rev.get("display_name", "") if rev else existing_name
                    initial_radius = existing_place_data.get("radius_m", default_radius)
            except (EOFError, KeyboardInterrupt):
                return None

        if resolved_lat is None or resolved_lon is None:
            p_lat, p_lon, p_addr = parse_coords_or_address(query_or_addr)
            if p_lat is not None and p_lon is not None:
                resolved_lat = p_lat
                resolved_lon = p_lon
                resolved_addr = p_addr or query_or_addr
            else:
                print(f"⚠️  Could not geocode address: \"{query_or_addr}\"")
                try:
                    c_in = input("Enter central GPS (lat, lon) or address to re-center (or Enter to cancel): ").strip()
                    if not c_in:
                        return None
                    resolved_lat, resolved_lon, resolved_addr = parse_coords_or_address(c_in)
                except Exception:
                    return None

    if resolved_lat is None or resolved_lon is None:
        print("❌ Invalid coordinates.")
        return None

    def _is_coord_str(s):
        if not s:
            return True
        c = s.strip("()[]{}'\" \t\r\n")
        if c.lower().startswith("geo:"):
            c = c[4:].strip()
        return bool(re.match(r'^[+-]?\d+(?:\.\d+)?([,\s]+[+-]?\d+(?:\.\d+)?)?$', c)) or bool(re.search(r'[@?&q=][+-]?\d+\.\d+[,/][+-]?\d+\.\d+', c))

    if places and resolved_lat is not None and resolved_lon is not None and not existing_name:
        for p_name, p_data in places.items():
            p_lat = p_data.get("lat")
            p_lon = p_data.get("lon")
            p_rad = p_data.get("radius_m", 150)
            if p_lat is not None and p_lon is not None:
                d = haversine_distance(resolved_lat, resolved_lon, p_lat, p_lon)
                if d <= p_rad:
                    existing_name = p_name
                    existing_place_data = p_data
                    break

    if existing_name:
        target_label = existing_name
    elif query_or_addr and not _is_coord_str(query_or_addr):
        target_label = query_or_addr.split(",")[0].strip()
    elif resolved_addr and not _is_coord_str(resolved_addr):
        target_label = resolved_addr.split(",")[0].strip()
    else:
        target_label = "Target"

    visits = get_coords_trip_history(resolved_lat, resolved_lon, radius_m=initial_radius, places=places, label=target_label)
    pass_bys = get_coords_pass_by_history(resolved_lat, resolved_lon, radius_m=initial_radius, places=places, label=target_label, visits=visits)

    # If no recorded drive visits or pass-through trips are found, expand radius in steps up to 1km
    if not visits and not pass_bys:
        nearest_stop = find_nearest_drive_stop(resolved_lat, resolved_lon, max_radius_m=1000)
        if nearest_stop:
            orig_lat, orig_lon = resolved_lat, resolved_lon
            resolved_lat = nearest_stop["lat"]
            resolved_lon = nearest_stop["lon"]
            if nearest_stop.get("addr"):
                resolved_addr = nearest_stop["addr"]
            if not _is_coord_str(resolved_addr):
                target_label = resolved_addr.split(",")[0].strip()

            existing_name = None
            existing_place_data = None
            if places:
                for p_name, p_data in places.items():
                    p_lat = p_data.get("lat")
                    p_lon = p_data.get("lon")
                    p_rad = p_data.get("radius_m", 150)
                    if p_lat is not None and p_lon is not None:
                        if haversine_distance(resolved_lat, resolved_lon, p_lat, p_lon) <= p_rad:
                            existing_name = p_name
                            existing_place_data = p_data
                            target_label = p_name
                            break

            print(f"\n🔍 No drive stops found within {initial_radius}m of input ({orig_lat:.5f}, {orig_lon:.5f}).")
            print(f"✔ Expanded search radius up to 1km and matched nearest recorded stop (~{nearest_stop['dist_m']:.1f}m away):")
            print(f"   📍 Replacement GPS Coords : {resolved_lat:.5f}, {resolved_lon:.5f}")
            if resolved_addr:
                print(f"   📍 Location Address       : {resolved_addr[:65]}")

            visits = get_coords_trip_history(resolved_lat, resolved_lon, radius_m=initial_radius, places=places, label=target_label)
            pass_bys = get_coords_pass_by_history(resolved_lat, resolved_lon, radius_m=initial_radius, places=places, label=target_label, visits=visits)

    print("\n" + "=" * 78)
    if existing_name:
        kws_list = existing_place_data.get("keywords", []) if existing_place_data else []
        kws_str = ", ".join(kws_list) if kws_list else "(None)"
        p_rad = existing_place_data.get("radius_m", 150) if existing_place_data else 150
        print(f"📍 Place Name:   {existing_name} (✔ Defined in places.json)")
        if resolved_addr:
            print(f"📍 Location:     {resolved_addr[:65]}")
        print(f"🎯 GPS Coords:   {resolved_lat:.5f}, {resolved_lon:.5f} (Radius: {p_rad}m)")
        print(f"🏷️  Keywords:     {kws_str}")
    else:
        print(f"📍 Location:     {resolved_addr[:65] if resolved_addr else 'Unknown Address'}")
        print(f"🎯 GPS Coords:   {resolved_lat:.5f}, {resolved_lon:.5f}")
    print("=" * 78)

    if visits or pass_bys:
        col_widths = [6, 18, 56, 10, 12]
        headers = [" # ", " Date & Time", " Trip Flow (Origin ➔ Target ➔ Next)", " Stay ", " Total Dist "]
        aligns = ["right", "left", "left", "right", "right"]
        total_inner = sum(col_widths) + len(col_widths) - 1

        print("\n" + format_box_line("┌", "─", "┐", [total_inner]))
        title_summary = []
        if visits:
            title_summary.append(f"{len(visits)} visit{'s' if len(visits) != 1 else ''}")
        if pass_bys:
            title_summary.append(f"{len(pass_bys)} pass-by drive{'s' if len(pass_bys) != 1 else ''}")
        summary_txt = " | ".join(title_summary)
        print(format_title_line(f" 🚗 ASSOCIATED DRIVES ({summary_txt})", total_inner))

        if visits:
            print(format_box_line("├", "─", "┤", [total_inner]))
            print(format_title_line("  🚗 RECORDED VISITS & STOPS (Newest First)", total_inner))
            print(format_box_line("├", "┬", "┤", col_widths))
            print(format_row(headers, col_widths, aligns=aligns))
            print(format_box_line("├", "┼", "┤", col_widths))

            recent_visits = list(reversed(visits[-15:]))
            for idx, v in enumerate(recent_visits, 1):
                orig = v["origin"]
                dest = v["destination"]
                if orig != "-" and dest != "-":
                    route_str = f"{orig} ➔ {target_label} ➔ {dest}"
                elif orig != "-":
                    route_str = f"{orig} ➔ {target_label}"
                else:
                    route_str = f"{target_label} ➔ {dest}"

                ts_short = v["date_ts"]
                dist_str = f"{v['dist_km']:.1f} km"
                stay_str = v["stay"]

                r_cells = [
                    f" [{idx:2d}] ",
                    f" {ts_short}",
                    f" {route_str}",
                    f" {stay_str} ",
                    f" {dist_str} "
                ]
                print(format_row(r_cells, col_widths, aligns=aligns))

        if pass_bys:
            print(format_box_line("├", "─", "┤", [total_inner]))
            print(format_title_line("  🚗 PASSING BY (Newest First)", total_inner))
            print(format_box_line("├", "┬", "┤", col_widths))
            print(format_row(headers, col_widths, aligns=aligns))
            print(format_box_line("├", "┼", "┤", col_widths))

            recent_pass_bys = list(reversed(pass_bys[-15:]))
            for idx, pb in enumerate(recent_pass_bys, 1):
                orig = pb["origin"]
                dest = pb["destination"]
                route_str = f"{orig} ➔ {target_label} ➔ {dest}"
                ts_short = pb["date_ts"]
                dist_str = f"{pb['dist_km']:.1f} km"
                stay_str = pb["stay"]

                r_cells = [
                    f" [{idx:2d}] ",
                    f" {ts_short}",
                    f" {route_str}",
                    f" {stay_str} ",
                    f" {dist_str} "
                ]
                print(format_row(r_cells, col_widths, aligns=aligns))

        print(format_box_line("└", "┴", "┘", col_widths))
    else:
        print("\n🚗 Associated Drives: No recorded drive visits or pass-through trips found within 1km of these coordinates.")

    candidates = []

    def print_actions():
        print("\nAction:")
        if candidates:
            print(f"  [1-{len(candidates)}] Accept suggested POI (uses POI center & auto-adjusted radius)")
        if existing_name:
            print(f"  [e]   Edit / Update \"{existing_name}\" details (name, radius, keywords, coords)")
            print("  [c]   Enter custom name (override current place)")
        else:
            print("  [c]   Enter custom name (uses parking stop coordinates)")
        print("  [g]   Provide custom GPS coordinates or address (set a precise center)")
        print("  [s]   Search for a place by name (e.g. a shopping centre or landmark)")
        print("  [m]   Open this location in your browser (Google Maps)")
        if existing_name:
            print(f"  [d]   Delete \"{existing_name}\" from places.json")
        print("  [b]   Back to lookup list")
        print("  [q]   Quit review session")

    print_actions()

    selected_name = None
    final_lat = resolved_lat
    final_lon = resolved_lon
    recommended_radius = default_radius

    while True:
        try:
            choice = input("\nChoice: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None

        if choice in ("b", "back", "k", "skip", ""):
            print("⏩ Skipped (kept as raw drive address).")
            return None
        elif choice in ("q", "quit"):
            return None

        if choice in ("e", "edit", "u", "update") and existing_name:
            ret, places = interactive_edit_place(existing_name, places)
            if ret == "quit":
                return None
            return existing_name

        if choice in ("d", "del", "delete") and existing_name:
            try:
                confirm = input(f"Are you sure you want to delete \"{existing_name}\" from places.json? [y/N]: ").strip().lower()
                if confirm in ("y", "yes"):
                    del places[existing_name]
                    save_places(places)
                    print(f"✔ Successfully deleted \"{existing_name}\" from places.json.")
                    return None
                else:
                    print("Deletion cancelled.")
                    print_actions()
                    continue
            except (EOFError, KeyboardInterrupt):
                return None

        if choice in ("m", "map"):
            map_url = gmaps_url(resolved_lat, resolved_lon)
            opened = False
            try:
                opened = webbrowser.open(map_url)
            except Exception:
                opened = False
            if opened:
                print(f"🌐 Opened in your default browser: {map_url}")
            else:
                print(f"⚠️  Couldn't open browser: {map_url}")
            print_actions()
            continue

        if choice in ("s", "search"):
            while True:
                try:
                    print("\nSearch for a place:")
                    print("  Type a name or landmark, then Enter")
                    print("  [Enter] alone to query nearby OSM POIs")
                    print("  [b]     Back")
                    s_in = input("  > ").strip()
                except (EOFError, KeyboardInterrupt):
                    return None
                if s_in.lower() in ("b", "back", "cancel"):
                    print_actions()
                    break
                if not s_in:
                    print(f"\n🌐 Querying OpenStreetMap POIs near {resolved_lat:.5f}, {resolved_lon:.5f}...")
                    pois = query_overpass_pois(resolved_lat, resolved_lon, radius_m=350)
                    if not pois:
                        print("\nℹ️  No specific commercial POI found in OpenStreetMap within 350m.")
                        continue
                    candidates = pois[:7]
                    print(f"✔ Discovered {len(candidates)} nearby POI{'s' if len(candidates) != 1 else ''}.\n")
                    print("🏢 Nearby Point-of-Interest (POI) Candidates:")
                    for p_idx, poi in enumerate(candidates, 1):
                        dist_disp = format_dist_human(poi['dist_m'])
                        print(f"  [{p_idx}] {poi['name']} ({poi['category']})  [~{dist_disp} away | Center: {poi['lat']:.5f}, {poi['lon']:.5f}]")
                    try:
                        print(f"\nSelect a POI:")
                        print(f"  [1-{len(candidates)}] Choose a result")
                        print(f"  [s] Search again")
                        print(f"  [b] Back")
                        p_choice = input("  > ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        return None
                    if p_choice in ("b", "back", "cancel"):
                        print_actions()
                        break
                    if p_choice.isdigit() and 1 <= int(p_choice) <= len(candidates):
                        poi = candidates[int(p_choice) - 1]
                        selected_name = prompt_poi_naming(poi["name"], poi.get("category", ""))
                        final_lat = poi["lat"]
                        final_lon = poi["lon"]
                        recommended_radius = max(150, int(poi["dist_m"] + 60))
                        print(f"\n📍 Using POI Center: {final_lat:.5f}, {final_lon:.5f} [{selected_name}]")
                        print(f"   Parking offset: {poi['dist_m']}m ➔ Recommended Geofence Radius: {recommended_radius}m")
                        break
                    continue
                else:
                    s_results = search_places_by_name(s_in, near_lat=resolved_lat, near_lon=resolved_lon, max_radius_m=500)
                    if not s_results:
                        print(f"ℹ️  No results found within 500m for \"{s_in}\".")
                        continue
                    candidates = s_results[:7]
                    print(f"\n🔎 Search results for \"{s_in}\":")
                    for s_idx, r in enumerate(candidates, 1):
                        dist_part = f" [~{format_dist_human(r['dist_m'])} away]" if r.get("dist_m") is not None else ""
                        print(f"  [{s_idx}] {r['name']} ({r.get('category', '')}){dist_part}")
                        print(f"        {r.get('display_name', '')}")
                    try:
                        print(f"\nSelect a result:")
                        print(f"  [1-{len(candidates)}] Choose a result")
                        print(f"  [s] Search again")
                        print(f"  [b] Back")
                        s_choice = input("  > ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        return None
                    if s_choice in ("b", "back", "cancel"):
                        print_actions()
                        break
                    if s_choice.isdigit() and 1 <= int(s_choice) <= len(candidates):
                        poi = candidates[int(s_choice) - 1]
                        selected_name = prompt_poi_naming(poi["name"], poi.get("category", ""))
                        final_lat = poi["lat"]
                        final_lon = poi["lon"]
                        offset_d = poi.get("dist_m") or haversine_distance(resolved_lat, resolved_lon, final_lat, final_lon)
                        recommended_radius = max(150, int(offset_d + 60))
                        print(f"\n📍 Using POI Center: {final_lat:.5f}, {final_lon:.5f} [{selected_name}]")
                        print(f"   Offset from parking: {offset_d:.1f}m ➔ Recommended Geofence Radius: {recommended_radius}m")
                        break

            if selected_name:
                break
            continue

        if choice.isdigit() and candidates and 1 <= int(choice) <= len(candidates):
            poi = candidates[int(choice) - 1]
            selected_name = prompt_poi_naming(poi["name"], poi.get("category", ""))
            final_lat = poi["lat"]
            final_lon = poi["lon"]
            offset_d = poi.get("dist_m") or haversine_distance(resolved_lat, resolved_lon, final_lat, final_lon)
            recommended_radius = max(150, int(offset_d + 60))
            print(f"\n📍 Using POI Center: {final_lat:.5f}, {final_lon:.5f} [{selected_name}]")
            print(f"   Offset: {offset_d:.1f}m ➔ Recommended Geofence Radius: {recommended_radius}m")
            break

        elif choice == "g":
            try:
                g_in = input("Enter central GPS (lat, lon) or exact address to re-center: ").strip()
                g_lat, g_lon, g_addr = parse_coords_or_address(g_in)
                if g_lat is None or g_lon is None:
                    print("❌ Could not parse coordinates or address.")
                    print_actions()
                    continue
                final_lat = g_lat
                final_lon = g_lon
                resolved_addr = g_addr or resolved_addr
                offset_d = haversine_distance(resolved_lat, resolved_lon, final_lat, final_lon)
                recommended_radius = max(150, int(offset_d + 60))
                print(f"✔ Centered at: {final_lat:.5f}, {final_lon:.5f} (Offset from parking: {offset_d:.1f}m)")
                
                def_name = g_addr.split(",")[0].strip() if g_addr else ""
                n_prompt = f"Enter Place Name [default: {def_name}]: " if def_name else "Enter Place Name: "
                n_in = input(n_prompt).strip()
                selected_name = n_in if n_in else def_name
                break
            except (EOFError, KeyboardInterrupt):
                return None

        elif choice == "c":
            try:
                selected_name = input("Enter Place Name: ").strip()
                c_coord = input(f"Center Coordinates [default: {final_lat:.5f}, {final_lon:.5f} (Enter to keep, or provide new)]: ").strip()
                if c_coord:
                    c_lat, c_lon, _ = parse_coords_or_address(c_coord)
                    if c_lat is not None and c_lon is not None:
                        final_lat, final_lon = c_lat, c_lon
                        offset_d = haversine_distance(resolved_lat, resolved_lon, final_lat, final_lon)
                        recommended_radius = max(150, int(offset_d + 60))
                        print(f"✔ Set center: {final_lat:.5f}, {final_lon:.5f} (Offset: {offset_d:.1f}m)")
                break
            except (EOFError, KeyboardInterrupt):
                return None
        else:
            selected_name = choice
            break

    if not selected_name:
        print("❌ Place name cannot be empty.")
        return None

    is_valid, err = validate_place_name(selected_name)
    if not is_valid:
        print(f"❌ {err}")
        return None

    radius_m = prompt_radius_input(recommended_radius, label="Geofence Radius in meters")

    kw_candidates = suggest_keywords(resolved_addr, selected_name)
    kw_str = ", ".join(kw_candidates) if kw_candidates else ""
    try:
        k_input = input(f"Keywords / Aliases [default: {kw_str}]: ").strip()
        if k_input:
            keywords = [k.strip() for k in k_input.split(",") if k.strip() and not is_coord_string(k.strip())]
        else:
            keywords = kw_candidates
    except Exception:
        keywords = kw_candidates

    addr_label = resolved_addr.split(',')[0].strip() if (resolved_addr and not is_coord_string(resolved_addr)) else ""
    notes = f"{selected_name} ({addr_label})" if addr_label else selected_name

    places[selected_name] = {
        "lat": round(final_lat, 5),
        "lon": round(final_lon, 5),
        "radius_m": radius_m,
        "keywords": keywords,
        "notes": notes
    }

    save_places(places)
    print(f"\n✔ Successfully saved \"{selected_name}\" to places.json (Center: {final_lat:.5f}, {final_lon:.5f}, Radius: {radius_m}m)!")
    return selected_name

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Drive Log Discovery & Visit Analytics Helpers
# ---------------------------------------------------------------------------
def load_config():
    """Loads configuration dictionary from config.json."""
    default_config = {
        "landing_directory": "~/Downloads",
        "data_directory": "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla",
        "invoices_directory": "~/iCloud/PDF/Tesla/charging_invoices"
    }
    for cfg_p in [os.path.join(REPO_ROOT, "config.json"), CONFIG_JSON_PATH]:  # TODO-034: root is primary, Tessie/ legacy fallback
        if os.path.exists(cfg_p):
            try:
                with open(cfg_p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        default_config.update(data)
                        break
            except Exception:
                pass
    return default_config

def find_candidate_drive_logs():
    """Finds drives_master.csv or drives_summary_*.csv from configured directories and external SSDs."""
    config = load_config()
    tessie_dir = os.path.abspath(os.path.expanduser(config.get("directories", {}).get("data", "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla")))
    
    search_dirs = [tessie_dir]
    if TESSIE_DIR not in search_dirs:
        search_dirs.append(TESSIE_DIR)

    for d in search_dirs:
        for mf in (os.path.join(d, "drives", "drives_master.csv"), os.path.join(d, "drives_master.csv")):
            if os.path.isfile(mf):
                return [mf]

    summaries = []
    for d in search_dirs:
        for sub in ("drives", ""):
            for f in glob.glob(os.path.join(d, sub, "drives_summary_*.csv")):
                if f not in summaries:
                    summaries.append(f)
    return summaries


def find_deepdive_files():
    """Finds all drive_deepdive_*.csv files from configured directories and subdirectories."""
    config = load_config()
    tessie_dir = os.path.abspath(os.path.expanduser(config.get("directories", {}).get("data", "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla")))
    
    search_dirs = [tessie_dir]
    if TESSIE_DIR not in search_dirs and os.path.exists(TESSIE_DIR):
        search_dirs.append(TESSIE_DIR)

    files = []
    seen = set()
    for d in search_dirs:
        for sub in ["", "drives", "drives/deepdive", "deepdive", "archive", "drives/archive"]:
            p = os.path.join(d, sub)
            if os.path.isdir(p):
                for f in (glob.glob(os.path.join(p, "drive_deepdive_*.csv"))
                          + glob.glob(os.path.join(p, "*", "drive_deepdive_*.csv"))             # REQ-193 per-drive folders
                          + glob.glob(os.path.join(p, "*", "*", "drive_deepdive_*.csv"))):      # day folders
                    real = os.path.realpath(f)
                    if real not in seen:
                        seen.add(real)
                        files.append(real)
    return files

def _parse_drive_ts(ts_str):
    if not ts_str or ts_str == "-":
        return None
    clean = re.sub(r"\s+[A-Z]{3,4}$", "", ts_str.strip())
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(clean, fmt)
        except ValueError:
            pass
    return None

def _format_stay_duration(minutes):
    if minutes is None or minutes < 0:
        return "-"
    if minutes < 60:
        return f"{int(round(minutes))}m"
    h = int(minutes // 60)
    m = int(round(minutes % 60))
    if m >= 60:
        h += 1
        m = 0
    return f"{h}h {m}m" if m > 0 else f"{h}h"

def load_chargers():
    """
    Loads all known charging stations (Superchargers, destination chargers, third-party)
    from configured tessie_directory so they are accurately recognized and excluded from place review.
    """
    chargers = {}
    config = load_config()
    tessie_dir = os.path.abspath(os.path.expanduser(config.get("directories", {}).get("data", "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla")))
    
    search_dirs = [tessie_dir, os.path.join(tessie_dir, "chargers")]
    if TESSIE_DIR not in search_dirs and os.path.exists(TESSIE_DIR):
        search_dirs.append(TESSIE_DIR)

    filenames = [
        "tesla_superchargers.json",
        "superchargers.json",
        "tesla_chargers.json",
        "charging.json",
        "charging_stations.json",
        "chargefox_chargers.json",
        "evie_chargers.json",
        "bppulse_chargers.json",
        "nrma_chargers.json",
        "jolt_chargers.json",
        "ampcharge_chargers.json",
        "evx_chargers.json",
        "exploren_chargers.json"
    ]

    for d in search_dirs:
        for fname in filenames:
            c_path = os.path.join(d, fname)
            if os.path.isfile(c_path):
                try:
                    with open(c_path, "r", encoding="utf-8") as f:
                        c_data = json.load(f)
                        if isinstance(c_data, dict):
                            for k, v in c_data.items():
                                if isinstance(v, dict):
                                    lat = v.get("lat") or v.get("location", {}).get("lat")
                                    lon = v.get("lon") or v.get("location", {}).get("lon")
                                    if lat is not None and lon is not None:
                                        rad = v.get("radius_m") or v.get("location", {}).get("radius_m", 250)
                                        chargers[k] = {
                                            "lat": float(lat),
                                            "lon": float(lon),
                                            "radius_m": rad,
                                            "name": v.get("name") or k
                                        }
                except Exception:
                    pass
    return chargers


def get_place_visit_counts(places, drive_logs=None):
    """
    Computes visit frequency (unique visits / stays) for each place in places.json by scanning drive logs.
    An arrival followed by a departure at the same location is paired into 1 single visit.
    Returns: dict {place_name: visit_count}
    """
    visit_counts = {p: 0 for p in places}
    if drive_logs is None:
        drive_logs = find_candidate_drive_logs()
    if not drive_logs:
        return visit_counts

    chargers = load_chargers()

    for csv_file in drive_logs:
        try:
            with open(csv_file, "r", encoding="utf-8-sig") as fp:
                reader = csv.DictReader(fp)
                for row in reader:
                    e_addr = (row.get("Ending Location") or row.get("ending_location") or row.get("Ending Address") or row.get("ending_address") or "").strip()
                    e_saved = (row.get("Ending Saved Location") or row.get("ending_saved_location") or "").strip()
                    e_lat = float(row.get("Ending Latitude") or row.get("ending_latitude") or row.get("Ending Lat") or 0) if (row.get("Ending Latitude") or row.get("ending_latitude") or row.get("Ending Lat")) else None
                    e_lon = float(row.get("Ending Longitude") or row.get("ending_longitude") or row.get("Ending Lon") or 0) if (row.get("Ending Longitude") or row.get("ending_longitude") or row.get("Ending Lon")) else None

                    dest = resolve_stop(e_addr, e_saved, e_lat, e_lon, places, chargers) or e_saved or e_addr.split(",")[0] or "Unknown"
                    if dest in visit_counts:
                        visit_counts[dest] += 1
        except Exception:
            pass

    return visit_counts

def get_place_trip_history(target_place_name, places=None, drive_logs=None):
    """
    Extracts all chronological visits to a given place from drive logs.
    Pairs arrival and subsequent departure legs into single visit records:
    Origin ➔ target_place_name ➔ Destination.
    Returns: list of dicts with visit details.
    """
    if places is None:
        places = load_places()
    if drive_logs is None:
        drive_logs = find_candidate_drive_logs()
    if not drive_logs:
        return []

    chargers = load_chargers()

    raw_drives = []
    for csv_file in drive_logs:
        try:
            with open(csv_file, "r", encoding="utf-8-sig") as fp:
                reader = csv.DictReader(fp)
                for row in reader:
                    s_addr = (row.get("Starting Location") or row.get("starting_location") or row.get("Starting Address") or row.get("starting_address") or "").strip()
                    s_saved = (row.get("Starting Saved Location") or row.get("starting_saved_location") or "").strip()
                    s_lat = float(row.get("Starting Latitude") or row.get("starting_latitude") or row.get("Starting Lat") or 0) if (row.get("Starting Latitude") or row.get("starting_latitude") or row.get("Starting Lat")) else None
                    s_lon = float(row.get("Starting Longitude") or row.get("starting_longitude") or row.get("Starting Lon") or 0) if (row.get("Starting Longitude") or row.get("starting_longitude") or row.get("Starting Lon")) else None

                    e_addr = (row.get("Ending Location") or row.get("ending_location") or row.get("Ending Address") or row.get("ending_address") or "").strip()
                    e_saved = (row.get("Ending Saved Location") or row.get("ending_saved_location") or "").strip()
                    e_lat = float(row.get("Ending Latitude") or row.get("ending_latitude") or row.get("Ending Lat") or 0) if (row.get("Ending Latitude") or row.get("ending_latitude") or row.get("Ending Lat")) else None
                    e_lon = float(row.get("Ending Longitude") or row.get("ending_longitude") or row.get("Ending Lon") or 0) if (row.get("Ending Longitude") or row.get("ending_longitude") or row.get("Ending Lon")) else None

                    origin = resolve_stop(s_addr, s_saved, s_lat, s_lon, places, chargers) or s_saved or s_addr.split(",")[0] or "Unknown"
                    dest = resolve_stop(e_addr, e_saved, e_lat, e_lon, places, chargers) or e_saved or e_addr.split(",")[0] or "Unknown"

                    start_ts = (row.get("Started At (Local)") or _legacy_get(row, "Started At") or row.get("Started At") or "").strip()
                    end_ts = (row.get("Ended At (Local)") or _legacy_get(row, "Ended At") or row.get("Ended At") or "").strip()
                    try:
                        dist_km = float(row.get("Distance (km)") or row.get("distance_km") or 0)
                    except ValueError:
                        dist_km = 0.0
                    try:
                        dur_min = float(row.get("Duration (Minutes)") or row.get("duration_minutes") or row.get("Duration") or 0)
                    except ValueError:
                        dur_min = 0.0
                    try:
                        energy_kwh = float(row.get("Total Energy Used (kWh)") or row.get("energy_used_kwh") or 0)
                    except ValueError:
                        energy_kwh = 0.0

                    raw_drives.append({
                        "origin": origin,
                        "dest": dest,
                        "start_ts": start_ts,
                        "end_ts": end_ts,
                        "dist_km": dist_km,
                        "dur_min": dur_min,
                        "energy_kwh": energy_kwh
                    })
        except Exception:
            pass

 # Reconstruct paired visits
    visits = []
    i = 0
    total_drives = len(raw_drives)
    while i < total_drives:
        d = raw_drives[i]
        if d["dest"] == target_place_name:
            arrival = d
            departure = None
            if i + 1 < total_drives and raw_drives[i + 1]["origin"] == target_place_name:
                departure = raw_drives[i + 1]
                i += 1

            arr_dt = _parse_drive_ts(arrival["end_ts"])
            dep_dt = _parse_drive_ts(departure["start_ts"]) if departure else None
            stay_mins = (dep_dt - arr_dt).total_seconds() / 60.0 if (arr_dt and dep_dt and dep_dt >= arr_dt) else None

            visits.append({
                "date_ts": arrival["end_ts"][:16] if arrival["end_ts"] else (arrival["start_ts"][:16] if arrival["start_ts"] else "-"),
                "origin": arrival["origin"],
                "place": target_place_name,
                "destination": departure["dest"] if departure else "-",
                "stay": _format_stay_duration(stay_mins) if stay_mins is not None else "-",
                "dist_km": arrival["dist_km"] + (departure["dist_km"] if departure else 0.0),
                "energy_kwh": arrival["energy_kwh"] + (departure["energy_kwh"] if departure else 0.0),
                "paired": departure is not None,
                "is_arrival": True,
                "is_departure": departure is not None
            })
        elif d["origin"] == target_place_name:
            visits.append({
                "date_ts": d["start_ts"][:16] if d["start_ts"] else "-",
                "origin": "-",
                "place": target_place_name,
                "destination": d["dest"],
                "stay": "-",
                "dist_km": d["dist_km"],
                "energy_kwh": d["energy_kwh"],
                "paired": False,
                "is_arrival": False,
                "is_departure": True
            })
        i += 1

 # BUG-128 (2026-09-21): this function built `visits` correctly but never
 # returned it - every call fell through to Python's implicit `return None`,
 # so `inspect_place()` crashed with "TypeError: 'NoneType' object is not
 # iterable" when selecting ANY place from `places_manager.py list` (not
 # just "Home" - that was simply the first entry tested).
    return visits

def find_nearest_drive_stop(lat, lon, max_radius_m=1000, drive_logs=None):
    """
    Finds the closest recorded drive stop (start or end point of a recorded drive)
    within max_radius_m of (lat, lon).
    Returns dict with (dist_m, lat, lon, addr, timestamp, type) or None.
    """
    if drive_logs is None:
        drive_logs = find_candidate_drive_logs()
    if not drive_logs:
        return None

    closest = None
    min_dist = float("inf")

    for csv_file in drive_logs:
        try:
            with open(csv_file, "r", encoding="utf-8-sig") as fp:
                reader = csv.DictReader(fp)
                for row in reader:
                    s_lat = float(row.get("Starting Latitude") or row.get("starting_latitude") or row.get("Starting Lat") or 0) if (row.get("Starting Latitude") or row.get("starting_latitude") or row.get("Starting Lat")) else None
                    s_lon = float(row.get("Starting Longitude") or row.get("starting_longitude") or row.get("Starting Lon") or 0) if (row.get("Starting Longitude") or row.get("starting_longitude") or row.get("Starting Lon")) else None
                    s_addr = (row.get("Starting Location") or row.get("starting_location") or row.get("Starting Address") or row.get("starting_address") or "").strip()
                    s_ts = (row.get("Started At (Local)") or _legacy_get(row, "Started At") or row.get("Started At") or "").strip()

                    e_lat = float(row.get("Ending Latitude") or row.get("ending_latitude") or row.get("Ending Lat") or 0) if (row.get("Ending Latitude") or row.get("ending_latitude") or row.get("Ending Lat")) else None
                    e_lon = float(row.get("Ending Longitude") or row.get("ending_longitude") or row.get("Ending Lon") or 0) if (row.get("Ending Longitude") or row.get("ending_longitude") or row.get("Ending Lon")) else None
                    e_addr = (row.get("Ending Location") or row.get("ending_location") or row.get("Ending Address") or row.get("ending_address") or "").strip()
                    e_ts = (row.get("Ended At (Local)") or _legacy_get(row, "Ended At") or row.get("Ended At") or "").strip()

                    if s_lat is not None and s_lon is not None:
                        d = haversine_distance(lat, lon, s_lat, s_lon)
                        if d <= max_radius_m and d < min_dist:
                            min_dist = d
                            closest = {
                                "dist_m": d,
                                "lat": s_lat,
                                "lon": s_lon,
                                "addr": s_addr,
                                "timestamp": s_ts,
                                "type": "Start"
                            }

                    if e_lat is not None and e_lon is not None:
                        d = haversine_distance(lat, lon, e_lat, e_lon)
                        if d <= max_radius_m and d < min_dist:
                            min_dist = d
                            closest = {
                                "dist_m": d,
                                "lat": e_lat,
                                "lon": e_lon,
                                "addr": e_addr,
                                "timestamp": e_ts,
                                "type": "End"
                            }
        except Exception:
            pass

    return closest


def get_coords_trip_history(lat, lon, radius_m=150, places=None, drive_logs=None, label="Target"):
    """
    Extracts all chronological visits matching a coordinate radius from drive logs.
    Pairs arrival and subsequent departure legs into single visit records:
    Origin ➔ label ➔ Destination.
    Returns: list of dicts with visit details.
    """
    if places is None:
        places = load_places()
    if drive_logs is None:
        drive_logs = find_candidate_drive_logs()
    if not drive_logs:
        return []

    chargers = load_chargers()

    raw_drives = []
    for csv_file in drive_logs:
        try:
            with open(csv_file, "r", encoding="utf-8-sig") as fp:
                reader = csv.DictReader(fp)
                for row in reader:
                    s_addr = (row.get("Starting Location") or row.get("starting_location") or row.get("Starting Address") or row.get("starting_address") or "").strip()
                    s_saved = (row.get("Starting Saved Location") or row.get("starting_saved_location") or "").strip()
                    s_lat = float(row.get("Starting Latitude") or row.get("starting_latitude") or row.get("Starting Lat") or 0) if (row.get("Starting Latitude") or row.get("starting_latitude") or row.get("Starting Lat")) else None
                    s_lon = float(row.get("Starting Longitude") or row.get("starting_longitude") or row.get("Starting Lon") or 0) if (row.get("Starting Longitude") or row.get("starting_longitude") or row.get("Starting Lon")) else None

                    e_addr = (row.get("Ending Location") or row.get("ending_location") or row.get("Ending Address") or row.get("ending_address") or "").strip()
                    e_saved = (row.get("Ending Saved Location") or row.get("ending_saved_location") or "").strip()
                    e_lat = float(row.get("Ending Latitude") or row.get("ending_latitude") or row.get("Ending Lat") or 0) if (row.get("Ending Latitude") or row.get("ending_latitude") or row.get("Ending Lat")) else None
                    e_lon = float(row.get("Ending Longitude") or row.get("ending_longitude") or row.get("Ending Lon") or 0) if (row.get("Ending Longitude") or row.get("ending_longitude") or row.get("Ending Lon")) else None

                    s_match = False
                    if s_lat is not None and s_lon is not None:
                        s_match = (haversine_distance(lat, lon, s_lat, s_lon) <= radius_m)

                    e_match = False
                    if e_lat is not None and e_lon is not None:
                        e_match = (haversine_distance(lat, lon, e_lat, e_lon) <= radius_m)

                    if not s_match and not e_match:
                        continue

                    origin = label if s_match else (resolve_stop(s_addr, s_saved, s_lat, s_lon, places, chargers) or s_saved or s_addr.split(",")[0] or "Unknown")
                    dest = label if e_match else (resolve_stop(e_addr, e_saved, e_lat, e_lon, places, chargers) or e_saved or e_addr.split(",")[0] or "Unknown")

                    start_ts = (row.get("Started At (Local)") or _legacy_get(row, "Started At") or row.get("Started At") or "").strip()
                    end_ts = (row.get("Ended At (Local)") or _legacy_get(row, "Ended At") or row.get("Ended At") or "").strip()
                    try:
                        dist_km = float(row.get("Distance (km)") or row.get("distance_km") or 0)
                    except ValueError:
                        dist_km = 0.0
                    try:
                        dur_min = float(row.get("Duration (Minutes)") or row.get("duration_minutes") or row.get("Duration") or 0)
                    except ValueError:
                        dur_min = 0.0
                    try:
                        energy_kwh = float(row.get("Total Energy Used (kWh)") or row.get("energy_used_kwh") or 0)
                    except ValueError:
                        energy_kwh = 0.0

                    raw_drives.append({
                        "origin": origin,
                        "dest": dest,
                        "start_ts": start_ts,
                        "end_ts": end_ts,
                        "dist_km": dist_km,
                        "dur_min": dur_min,
                        "energy_kwh": energy_kwh,
                        "s_match": s_match,
                        "e_match": e_match
                    })
        except Exception:
            pass

 # Reconstruct paired visits
    visits = []
    i = 0
    total_drives = len(raw_drives)
    while i < total_drives:
        d = raw_drives[i]
        if d["e_match"]:
            arrival = d
            departure = None
            if i + 1 < total_drives and raw_drives[i + 1]["s_match"]:
                departure = raw_drives[i + 1]
                i += 1

            arr_dt = _parse_drive_ts(arrival["end_ts"])
            dep_dt = _parse_drive_ts(departure["start_ts"]) if departure else None
            stay_mins = (dep_dt - arr_dt).total_seconds() / 60.0 if (arr_dt and dep_dt and dep_dt >= arr_dt) else None

            visits.append({
                "date_ts": arrival["end_ts"][:16] if arrival["end_ts"] else (arrival["start_ts"][:16] if arrival["start_ts"] else "-"),
                "origin": arrival["origin"],
                "place": label,
                "destination": departure["dest"] if departure else "-",
                "stay": _format_stay_duration(stay_mins) if stay_mins is not None else "-",
                "dist_km": arrival["dist_km"] + (departure["dist_km"] if departure else 0.0),
                "energy_kwh": arrival["energy_kwh"] + (departure["energy_kwh"] if departure else 0.0),
                "paired": departure is not None,
                "is_arrival": True,
                "is_departure": departure is not None
            })
        elif d["s_match"]:
            visits.append({
                "date_ts": d["start_ts"][:16] if d["start_ts"] else "-",
                "origin": "-",
                "place": label,
                "destination": d["dest"],
                "stay": "-",
                "dist_km": d["dist_km"],
                "energy_kwh": d["energy_kwh"],
                "paired": False,
                "is_arrival": False,
                "is_departure": True
            })
        i += 1

    return visits


def get_coords_pass_by_history(target_lat, target_lon, radius_m=150, places=None, drive_logs=None, deepdive_files=None, label="Target", visits=None):
    """
    Finds drives that passed within radius_m of (target_lat, target_lon) without stopping there.
    Excludes drives that start or end at the target, and drives that occurred within 10 minutes
    of a recorded visit (stop) at the target location.
    Returns: list of dicts with pass-by details (sorted chronologically).
    """
    if places is None:
        places = load_places()
    if drive_logs is None:
        drive_logs = find_candidate_drive_logs()
    if not drive_logs:
        return []
    if deepdive_files is None:
        deepdive_files = find_deepdive_files()
    if not deepdive_files:
        return []

    chargers = load_chargers()

 # Collect all visit timestamps to exclude pass-bys within 10 minutes of stopping there
    stop_dts = []
    if visits is None:
        visits = get_coords_trip_history(target_lat, target_lon, radius_m=radius_m, places=places, drive_logs=drive_logs, label=label)
    
    for v in visits:
        v_dt = _parse_drive_ts(v.get("date_ts"))
        if v_dt:
            stop_dts.append(v_dt)

    drives_master = []
    for csv_file in drive_logs:
        try:
            with open(csv_file, "r", encoding="utf-8-sig") as fp:
                reader = csv.DictReader(fp)
                for row in reader:
                    s_addr = (row.get("Starting Location") or row.get("starting_location") or row.get("Starting Address") or row.get("starting_address") or "").strip()
                    s_saved = (row.get("Starting Saved Location") or row.get("starting_saved_location") or "").strip()
                    s_lat = float(row.get("Starting Latitude") or row.get("starting_latitude") or row.get("Starting Lat") or 0) if (row.get("Starting Latitude") or row.get("starting_latitude") or row.get("Starting Lat")) else None
                    s_lon = float(row.get("Starting Longitude") or row.get("starting_longitude") or row.get("Starting Lon") or 0) if (row.get("Starting Longitude") or row.get("starting_longitude") or row.get("Starting Lon")) else None

                    e_addr = (row.get("Ending Location") or row.get("ending_location") or row.get("Ending Address") or row.get("ending_address") or "").strip()
                    e_saved = (row.get("Ending Saved Location") or row.get("ending_saved_location") or "").strip()
                    e_lat = float(row.get("Ending Latitude") or row.get("ending_latitude") or row.get("Ending Lat") or 0) if (row.get("Ending Latitude") or row.get("ending_latitude") or row.get("Ending Lat")) else None
                    e_lon = float(row.get("Ending Longitude") or row.get("ending_longitude") or row.get("Ending Lon") or 0) if (row.get("Ending Longitude") or row.get("ending_longitude") or row.get("Ending Lon")) else None

                    start_ts = (row.get("Started At (Local)") or _legacy_get(row, "Started At") or row.get("Started At") or "").strip()
                    end_ts = (row.get("Ended At (Local)") or _legacy_get(row, "Ended At") or row.get("Ended At") or "").strip()

                    try:
                        dist_km = float(row.get("Distance (km)") or row.get("distance_km") or 0)
                    except ValueError:
                        dist_km = 0.0

                    s_dist = haversine_distance(target_lat, target_lon, s_lat, s_lon) if s_lat is not None and s_lon is not None else float("inf")
                    e_dist = haversine_distance(target_lat, target_lon, e_lat, e_lon) if e_lat is not None and e_lon is not None else float("inf")

                    d_start_dt = _parse_drive_ts(start_ts)
                    d_end_dt = _parse_drive_ts(end_ts)

 # Also add start/end dt to stop_dts if it was near target
                    if s_dist <= radius_m and d_start_dt:
                        stop_dts.append(d_start_dt)
                    if e_dist <= radius_m and d_end_dt:
                        stop_dts.append(d_end_dt)

                    drives_master.append({
                        "s_addr": s_addr, "s_saved": s_saved, "s_lat": s_lat, "s_lon": s_lon, "s_dist": s_dist,
                        "e_addr": e_addr, "e_saved": e_saved, "e_lat": e_lat, "e_lon": e_lon, "e_dist": e_dist,
                        "start_ts": start_ts, "end_ts": end_ts, "dist_km": dist_km,
                        "start_dt": d_start_dt, "end_dt": d_end_dt
                    })
        except Exception:
            pass

    deg_lat_pad = (radius_m + 100) / 111000.0
    deg_lon_pad = (radius_m + 100) / 85000.0
    min_lat, max_lat = target_lat - deg_lat_pad, target_lat + deg_lat_pad
    min_lon, max_lon = target_lon - deg_lon_pad, target_lon + deg_lon_pad

    pass_bys = []

    for fpath in deepdive_files:
        try:
            with open(fpath, "r", encoding="utf-8-sig") as fp:
                reader = csv.DictReader(fp)
                fieldnames = reader.fieldnames or []
                if "Latitude" not in fieldnames or "Longitude" not in fieldnames:
                    continue

                rows = list(reader)
                if len(rows) < 10:
                    continue

                first_row = rows[0]
                last_row = rows[-1]

                try:
                    f_lat, f_lon = float(first_row.get("Latitude") or 0), float(first_row.get("Longitude") or 0)
                    l_lat, l_lon = float(last_row.get("Latitude") or 0), float(last_row.get("Longitude") or 0)
                except ValueError:
                    continue

                f_dist = haversine_distance(target_lat, target_lon, f_lat, f_lon)
                l_dist = haversine_distance(target_lat, target_lon, l_lat, l_lon)

 # Exclude if drive started or stopped within radius
                if f_dist <= radius_m or l_dist <= radius_m:
                    continue

                matched_row = None
                min_pass_dist = float("inf")

 # Skip the first 5 and last 5 rows to ensure we only capture genuine en-route passage
                for idx, r in enumerate(rows[5:-5], 5):
                    try:
                        r_lat = float(r.get("Latitude") or 0)
                        r_lon = float(r.get("Longitude") or 0)
                    except ValueError:
                        continue

                    if min_lat <= r_lat <= max_lat and min_lon <= r_lon <= max_lon:
                        d = haversine_distance(target_lat, target_lon, r_lat, r_lon)
                        if d <= radius_m and d < min_pass_dist:
                            min_pass_dist = d
                            matched_row = r

                if matched_row:
                    pass_ts = _legacy_get(matched_row, "Timestamp") or matched_row.get("Timestamp (Local)") or matched_row.get("Timestamp")
                    pass_dt = _parse_drive_ts(pass_ts)

 # Exclude if within 10 minutes of any recorded stop/visit at this target
                    if pass_dt and any(abs((pass_dt - s_dt).total_seconds()) < 600 for s_dt in stop_dts):
                        continue

                    matched_master = None
                    if pass_dt:
                        for dm in drives_master:
                            if dm["start_dt"] and dm["end_dt"]:
                                if dm["start_dt"] - timedelta(minutes=3) <= pass_dt <= dm["end_dt"] + timedelta(minutes=3):
                                    matched_master = dm
                                    break

                    if matched_master:
                        orig = resolve_stop(matched_master["s_addr"], matched_master["s_saved"], matched_master["s_lat"], matched_master["s_lon"], places, chargers) or matched_master["s_saved"] or matched_master["s_addr"].split(",")[0] or "Unknown"
                        dest = resolve_stop(matched_master["e_addr"], matched_master["e_saved"], matched_master["e_lat"], matched_master["e_lon"], places, chargers) or matched_master["e_saved"] or matched_master["e_addr"].split(",")[0] or "Unknown"
                        dist_km = matched_master["dist_km"]
                        date_str = matched_master["start_ts"][:16] if matched_master["start_ts"] else (pass_ts[:16] if pass_ts else "-")
                    else:
                        orig = resolve_stop(first_row.get("Starting Location", ""), "", f_lat, f_lon, places, chargers) or f"Start ({f_lat:.2f}, {f_lon:.2f})"
                        dest = resolve_stop(last_row.get("Ending Location", ""), "", l_lat, l_lon, places, chargers) or f"End ({l_lat:.2f}, {l_lon:.2f})"
                        dist_km = 0.0
                        date_str = pass_ts[:16] if pass_ts else "-"

                    pass_bys.append({
                        "date_ts": date_str,
                        "pass_ts": pass_ts,
                        "origin": orig,
                        "place": label,
                        "destination": dest,
                        "stay": "Pass by",
                        "closest_dist_m": min_pass_dist,
                        "dist_km": dist_km
                    })
        except Exception:
            pass

    pass_bys.sort(key=lambda x: _parse_drive_ts(x["date_ts"]) or datetime.min)
    return pass_bys


def inspect_place(place_name_or_query, places=None):
    """
    Renders a detailed inspection card for a specific place with recent trip history and statistics.
    Uses table_formatter with exact ┬, ┼, ┴ column junction borders.
    """
    if places is None:
        places = load_places()
    if not places:
        print("ℹ️  No places defined in places.json.")
        return

 # Find matching place
    target_name = None
    q = place_name_or_query.strip().lower() if isinstance(place_name_or_query, str) else str(place_name_or_query)

 # 1. Exact match
    for p_name in places:
        if p_name.lower() == q:
            target_name = p_name
            break

 # 2. Substring match
    if not target_name:
        for p_name, p_data in places.items():
            kws = " ".join(p_data.get("keywords", [])).lower()
            if q in p_name.lower() or q in kws:
                target_name = p_name
                break

    if not target_name:
        print(f"❌ Place \"{place_name_or_query}\" not found in places.json.")
        return

    p_data = places[target_name]
    lat, lon = p_data.get("lat", 0.0), p_data.get("lon", 0.0)
    rad = p_data.get("radius_m", 150)
    kws = ", ".join(p_data.get("keywords", [])) or "None"
    notes = p_data.get("notes", "") or "None"

    visits = get_place_trip_history(target_name, places=places)
    pass_bys = get_coords_pass_by_history(lat, lon, radius_m=rad, places=places, label=target_name, visits=visits)
    total_legs = sum(2 if v.get("paired") else 1 for v in visits)

    col_widths = [6, 18, 56, 10, 12]
    headers = [" # ", " Date & Time", " Trip Flow (Origin ➔ Target ➔ Next)", " Stay ", " Total Dist "]
    aligns = ["right", "left", "left", "right", "right"]
    total_inner = sum(col_widths) + len(col_widths) - 1

    print("\n" + format_box_line("┌", "─", "┐", [total_inner]))
    print(format_title_line(f" 📍 LOCATION DETAILS: {target_name}", total_inner))
    print(format_box_line("├", "─", "┤", [total_inner]))
    print(format_title_line(f"  Coordinates:   {lat:.5f}, {lon:.5f} ({rad}m geofence radius)", total_inner))
    map_link = gmaps_url(lat, lon)
    print(format_title_line(f"  Google Maps:   {hyperlink(f'{lat:.5f}, {lon:.5f}', map_link)}", total_inner))
    if kws != "None":
        kw_prefix = "  Keywords:      "
        kw_indent = " " * len(kw_prefix)
        wrapped_kws = textwrap.wrap(kws, width=total_inner - len(kw_prefix) - 2)
        for idx, wl in enumerate(wrapped_kws):
            line = f"{kw_prefix}{wl}" if idx == 0 else f"{kw_indent}{wl}"
            print(format_title_line(line, total_inner))
    if notes != "None":
        n_prefix = "  Notes:         "
        n_indent = " " * len(n_prefix)
        wrapped_notes = textwrap.wrap(notes, width=total_inner - len(n_prefix) - 2)
        for idx, wl in enumerate(wrapped_notes):
            line = f"{n_prefix}{wl}" if idx == 0 else f"{n_indent}{wl}"
            print(format_title_line(line, total_inner))
    print(format_box_line("├", "─", "┤", [total_inner]))
    print(format_title_line("  📊 DRIVE & VISIT HISTORY SUMMARY", total_inner))
    print(format_title_line(f"  Total Recorded Visits:     {len(visits)} visits ({total_legs} drive legs)", total_inner))
    if pass_bys:
        print(format_title_line(f"  Total Passed By Trips:     {len(pass_bys)} drives passing through", total_inner))
    if visits:
        first_ts = visits[0]["date_ts"]
        latest_ts = visits[-1]["date_ts"]
        print(format_title_line(f"  First Recorded Visit:      {first_ts}", total_inner))
        print(format_title_line(f"  Latest Recorded Visit:     {latest_ts}", total_inner))
    print(format_box_line("├", "─", "┤", [total_inner]))
    print(format_title_line("  🚗 RECENT VISITS & TRIP FLOW (Newest First)", total_inner))
    print(format_box_line("├", "┬", "┤", col_widths))
    print(format_row(headers, col_widths, aligns=aligns))
    print(format_box_line("├", "┼", "┤", col_widths))

    if not visits:
        no_trips_msg = "No recorded drive visits found for this location."
        print(f"│{pad_display(f'  {no_trips_msg}', total_inner, 'left')}│")
    else:
        recent_visits = list(reversed(visits[-15:]))
        for idx, v in enumerate(recent_visits, 1):
            orig = v["origin"]
            dest = v["destination"]
            if orig != "-" and dest != "-":
                route_str = f"{orig} ➔ {target_name} ➔ {dest}"
            elif orig != "-":
                route_str = f"{orig} ➔ {target_name}"
            else:
                route_str = f"{target_name} ➔ {dest}"

            ts_short = v["date_ts"]
            dist_str = f"{v['dist_km']:.1f} km"
            stay_str = v["stay"]

            r_cells = [
                f" [{idx:2d}] ",
                f" {ts_short}",
                f" {route_str}",
                f" {stay_str} ",
                f" {dist_str} "
            ]
            print(format_row(r_cells, col_widths, aligns=aligns))

    if pass_bys:
        print(format_box_line("├", "─", "┤", [total_inner]))
        print(format_title_line("  🚗 PASSING BY (Newest First)", total_inner))
        print(format_box_line("├", "┬", "┤", col_widths))
        print(format_row(headers, col_widths, aligns=aligns))
        print(format_box_line("├", "┼", "┤", col_widths))

        recent_pass_bys = list(reversed(pass_bys[-15:]))
        for idx, pb in enumerate(recent_pass_bys, 1):
            orig = pb["origin"]
            dest = pb["destination"]
            route_str = f"{orig} ➔ {target_name} ➔ {dest}"
            ts_short = pb["date_ts"]
            dist_str = f"{pb['dist_km']:.1f} km"
            stay_str = pb["stay"]

            r_cells = [
                f" [{idx:2d}] ",
                f" {ts_short}",
                f" {route_str}",
                f" {stay_str} ",
                f" {dist_str} "
            ]
            print(format_row(r_cells, col_widths, aligns=aligns))

    print(format_box_line("└", "┴", "┘", col_widths) + "\n")

    if sys.stdin and sys.stdin.isatty():
        while True:
            print("Action:")
            print("  [d] Delete this place")
            print("  [r] Rename place")
            print("  [g] Change geofence radius")
            print("  [k] Edit keywords / aliases")
            print("  [n] Edit notes")
            print("  [b] Back to list (or press Enter)")
            try:
                act = input("\nChoice [b]: ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print()
                break
            if not act or act in ("b", "back", "q", "quit"):
                break
            elif act in ("d", "delete"):
                try:
                    conf = input(f"⚠️ Are you sure you want to permanently delete \"{target_name}\"? [y/N]: ").strip().lower()
                except (KeyboardInterrupt, EOFError):
                    break
                if conf in ("y", "yes"):
                    places = load_places()
                    if target_name in places:
                        del places[target_name]
                        save_places(places)
                        print(f"✔ Permanently deleted \"{target_name}\" from places.json.")
                        return "deleted"
                else:
                    print("Deletion cancelled.")
            elif act in ("r", "rename"):
                try:
                    new_n = input(f"Enter new name [current: \"{target_name}\"]: ").strip()
                except (KeyboardInterrupt, EOFError):
                    break
                if new_n and new_n != target_name:
                    is_valid, err = validate_place_name(new_n)
                    if not is_valid:
                        print(f"❌ {err}")
                    else:
                        places = load_places()
                        if new_n in places:
                            print(f"❌ Place \"{new_n}\" already exists in places.json.")
                        else:
                            places[new_n] = places.pop(target_name)
                            save_places(places)
                            print(f"✔ Renamed \"{target_name}\" ➔ \"{new_n}\".")
                            return "renamed"
            elif act in ("g", "radius"):
                new_rad = prompt_radius_input(rad, label="Enter new radius in meters")
                if new_rad != rad:
                    places = load_places()
                    if target_name in places:
                        places[target_name]["radius_m"] = new_rad
                        save_places(places)
                        print(f"✔ Geofence radius updated: {rad}m ➔ {new_rad}m.")
                        rad = new_rad
            elif act in ("k", "keywords", "alias"):
                curr_kws = p_data.get("keywords", [])
                curr_kws_str = ", ".join(curr_kws)
                try:
                    new_k_str = input(f"Enter keywords / aliases (comma-separated) [current: {curr_kws_str}]: ").strip()
                except (KeyboardInterrupt, EOFError):
                    break
                if new_k_str:
                    new_kws = [k.strip() for k in new_k_str.split(",") if k.strip()]
                    places = load_places()
                    if target_name in places:
                        places[target_name]["keywords"] = new_kws
                        save_places(places)
                        print(f"✔ Keywords updated: {', '.join(new_kws)}")
            elif act in ("n", "notes"):
                curr_notes = p_data.get("notes", "")
                try:
                    new_n_str = input(f"Enter notes [current: {curr_notes}]: ").strip()
                except (KeyboardInterrupt, EOFError):
                    break
                if new_n_str:
                    places = load_places()
                    if target_name in places:
                        places[target_name]["notes"] = new_n_str
                        save_places(places)
                        print(f"✔ Notes updated: \"{new_n_str}\"")


# ---------------------------------------------------------------------------
# CLI Command: List Stored Places
# ---------------------------------------------------------------------------
def cmd_list(args):
    while True:
        places = load_places()
        if not places:
            print("ℹ️  No places defined in places.json.")
            return

        if getattr(args, "search", None):
            q = args.search.lower()
            filtered = {}
            for name, data in places.items():
                kws = " ".join(data.get("keywords", [])).lower()
                notes = data.get("notes", "").lower()
                if q in name.lower() or q in kws or q in notes:
                    filtered[name] = data
            places = filtered

        if not places:
            print(f"ℹ️  No places matched search query: \"{args.search}\"")
            return

        visit_counts = get_place_visit_counts(places)

        ref_lat, ref_lon = None, None
        if getattr(args, "near", None):
            all_places = load_places()
            if args.near in all_places:
                ref_lat = all_places[args.near]["lat"]
                ref_lon = all_places[args.near]["lon"]
            else:
                geo = geocode_address(args.near)
                if geo:
                    ref_lat, ref_lon = geo["lat"], geo["lon"]
                else:
                    print(f"⚠️  Reference place \"{args.near}\" not found.")

        items = list(places.items())
        sort_choice = getattr(args, "sort", "visits")
        if ref_lat is not None and ref_lon is not None:
            items.sort(key=lambda x: haversine_distance(ref_lat, ref_lon, x[1]["lat"], x[1]["lon"]))
        elif sort_choice == "name":
            items.sort(key=lambda x: x[0].lower())
        elif sort_choice == "radius":
            items.sort(key=lambda x: x[1].get("radius_m", 0), reverse=True)
        elif sort_choice == "visits":
            items.sort(key=lambda x: (visit_counts.get(x[0], 0), x[0].lower()), reverse=True)

        col_widths = [6, 30, 8, 24, 8]
        headers = [" # ", " Place Name", " Visits ", " Coordinates (Lat, Lon) ", " Radius "]
        aligns = ["right", "left", "right", "center", "right"]

        if ref_lat is not None:
            col_widths.append(11)
            headers.append(" Dist (km) ")
            aligns.append("right")

        col_widths.append(38)
        headers.append(" Keywords / Aliases")
        aligns.append("left")

        total_inner = sum(col_widths) + len(col_widths) - 1

        title = f" 📍 STORED LOCATIONS ({len(items)} places)"
        if getattr(args, "near", None):
            title += f" [Sorted by distance from {args.near}]"
        elif sort_choice == "visits":
            title += " [Sorted by visits (highest frequency first)]"

        print("\n" + format_box_line("┌", "─", "┐", [total_inner]))
        print(format_title_line(title, total_inner))
        print(format_box_line("├", "┬", "┤", col_widths))
        print(format_row(headers, col_widths, aligns=aligns))
        print(format_box_line("├", "┼", "┤", col_widths))

        for idx, (name, data) in enumerate(items, 1):
            lat = data.get("lat", 0.0)
            lon = data.get("lon", 0.0)
            radius = f"{data.get('radius_m', 0)}m"
            kws = ", ".join(data.get("keywords", []))
            coords_str = f"{lat:.5f}, {lon:.5f}"
            v_str = str(visit_counts.get(name, 0))

            row_cells = [
                f" [{idx:2d}] ",
                f" {name}",
                f" {v_str} ",
                f" {coords_str} ",
                f" {radius} "
            ]
            if ref_lat is not None:
                dist_km = haversine_distance(ref_lat, ref_lon, lat, lon) / 1000.0
                row_cells.append(f" {dist_km:.1f} km ")
            row_cells.append(f" {kws}")

            print(format_row(row_cells, col_widths, aligns=aligns))

        print(format_box_line("└", "┴", "┘", col_widths) + "\n")

        if not (sys.stdin and sys.stdin.isatty()):
            break

        try:
            print("  [#] Inspect location details & visit history")
            print("  [q]uit")
            pick = input("  > ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break
        if not pick or pick.lower() in ("q", "quit", "exit"):
            break
        if pick.isdigit():
            p_idx = int(pick)
            if 1 <= p_idx <= len(items):
                sel_name = items[p_idx - 1][0]
                inspect_place(sel_name, places=places)
            else:
                print(f"⚠️ Invalid selection. Please enter 1 to {len(items)}.")
        else:
            inspect_place(pick, places=places)

def review_single_cluster(cl, places, total_clusters=1, cluster_num=1, allow_ignore=True):
    clean_addr = cl["address"] or f"{cl['center_lat']:.5f}, {cl['center_lon']:.5f}"
    print(f"\n==============================================================================")
    print(f"📍 Cluster [{cluster_num}/{total_clusters}]: {len(cl['stops'])} visits")
    print(f"Address: {clean_addr}")
    gps_str = f"{cl['center_lat']:.5f}, {cl['center_lon']:.5f}"
    print(f"GPS:     {hyperlink(gps_str, gmaps_url(cl['center_lat'], cl['center_lon']))}")
    print(f"==============================================================================")

    candidates = []

    def print_actions():
        print("\nAction:")
        if candidates:
            print(f"  [1-{len(candidates)}] Accept suggested POI (uses POI center & auto-adjusted radius)")
        print("  [c]   Enter custom name (uses parking stop coordinates)")
        print("  [g]   Provide custom GPS coordinates or address (set a precise center)")
        print("  [s]   Search for a place by name (e.g. a shopping centre or landmark)")
        print("  [m]   Open this location in your browser (Google Maps)")
        if allow_ignore:
            print("  [i]   Ignore permanently (address is fine as-is, not worth naming - won't be asked again)")
        print("  [b]   Back to cluster list (leave unlabelled for now)")
        print("  [q]   Quit review session")

    print_actions()

    search_pick = None
    while True:
        try:
            choice = input("\nChoice: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "quit"

        if choice in ("m", "map"):
            map_url = gmaps_url(cl["center_lat"], cl["center_lon"])
            opened = False
            try:
                opened = webbrowser.open(map_url)
            except Exception:
                opened = False
            if opened:
                print(f"🌐 Opened in your default browser: {map_url}")
            else:
                print(f"⚠️  Couldn't open a browser automatically - copy this URL instead:\n   {map_url}")
            print_actions()
            continue

        if choice in ("s", "search"):
            while True:
                try:
                    print("\nSearch for a place:")
                    print("  Type a name or landmark, then Enter")
                    print("  [Enter] alone to query nearby OSM POIs")
                    print("  [b]     Back")
                    s_in = input("  > ").strip()
                except (EOFError, KeyboardInterrupt):
                    return "quit"
                if s_in.lower() in ("b", "back", "cancel"):
                    print_actions()
                    break
                if not s_in:
                    print(f"\n🌐 Querying OpenStreetMap POIs near {cl['center_lat']:.5f}, {cl['center_lon']:.5f}...")
                    pois = query_overpass_pois(cl["center_lat"], cl["center_lon"], radius_m=350)
                    if not pois:
                        print("\nℹ️  No specific commercial POI found in OpenStreetMap within 350m.")
                        continue
                    candidates = pois[:5]
                    print(f"✔ Discovered {len(candidates)} nearby POI{'s' if len(candidates) != 1 else ''}.\n")
                    print("🏢 Suggested POIs from API:")
                    for p_idx, poi in enumerate(candidates, 1):
                        dist_disp = format_dist_human(poi['dist_m'])
                        print(f"  [{p_idx}] {poi['name']} ({poi['category']}) [~{dist_disp} | Center: {poi['lat']:.5f}, {poi['lon']:.5f}]")
                    try:
                        print(f"\nSelect a POI:")
                        print(f"  [1-{len(candidates)}] Choose a result")
                        print(f"  [s] Search again")
                        print(f"  [b] Back")
                        p_choice = input("  > ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        return "quit"
                    if p_choice in ("b", "back", "cancel"):
                        print_actions()
                        break
                    if p_choice.isdigit() and 1 <= int(p_choice) <= len(candidates):
                        poi = candidates[int(p_choice) - 1]
                        search_pick = {
                            "name": prompt_poi_naming(poi["name"], poi.get("category", "")),
                            "lat": poi["lat"],
                            "lon": poi["lon"],
                            "dist_m": poi["dist_m"],
                            "category": poi.get("category", "")
                        }
                        choice = "s_pick"
                        break
                    continue
                else:
                    s_results = search_places_by_name(s_in, near_lat=cl["center_lat"], near_lon=cl["center_lon"], max_radius_m=350)
                    if not s_results:
                        print(f"ℹ️  No results found within 350m for \"{s_in}\".")
                        continue
                    print(f"\n🔎 Search results for \"{s_in}\":")
                    for s_idx, r in enumerate(s_results, 1):
                        dist_part = f" [~{format_dist_human(r['dist_m'])} away]" if r["dist_m"] is not None else ""
                        print(f"  [{s_idx}] {r['name']} ({r['category']}){dist_part}")
                        print(f"        {r['display_name']}")
                    try:
                        print(f"\nSelect a result:")
                        print(f"  [1-{len(s_results)}] Choose a result")
                        print(f"  [s] Search again")
                        print(f"  [b] Back")
                        s_choice = input("  > ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        return "quit"
                    if s_choice in ("b", "back", "cancel"):
                        print_actions()
                        break
                    if s_choice.isdigit() and 1 <= int(s_choice) <= len(s_results):
                        search_pick = s_results[int(s_choice) - 1]
                        choice = "s_pick"
                        break

            if choice == "s_pick":
                break
            continue

        if choice in ("q", "quit", "b", "back", "s_pick", "c", "custom", "g", "gps", "i", "ignore") or (choice.isdigit() and 1 <= int(choice) <= len(candidates)):
            break

        print(f"⚠️  Unrecognised choice \"{choice}\". Please select an option from the menu.")
        print_actions()

    if choice in ("q", "quit"):
        return "quit"
    elif choice in ("b", "back", "skip", ""):
        return False
    elif allow_ignore and choice in ("i", "ignore"):
        ignored_entries = load_ignored_places()
        entry = {
            "address": cl.get("address", ""),
            "center_lat": cl["center_lat"],
            "center_lon": cl["center_lon"],
            "radius_m": 100,
            "stops": cl.get("stops", []),
            "ignored_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        ignored_entries.append(entry)
        save_ignored_places(ignored_entries)
        print(f"\n🙈 Ignored cluster at {clean_addr} (won't be asked again).")
        return True

    selected_name = None
    final_lat = cl["center_lat"]
    final_lon = cl["center_lon"]
    recommended_radius = 150

    if choice.isdigit() and 1 <= int(choice) <= len(candidates):
        poi = candidates[int(choice) - 1]
        selected_name = prompt_poi_naming(poi["name"], poi.get("category", ""))
        final_lat = poi["lat"]
        final_lon = poi["lon"]
        recommended_radius = max(150, int(poi["dist_m"] + 60))
        print(f"\n📍 Using POI Center: {final_lat:.5f}, {final_lon:.5f} [{selected_name}]")
        print(f"   Parking offset: {poi['dist_m']}m ➔ Recommended Geofence Radius: {recommended_radius}m")

    elif choice == "s_pick" and search_pick:
        selected_name = search_pick["name"]
        final_lat = search_pick["lat"]
        final_lon = search_pick["lon"]
        offset_d = search_pick["dist_m"] if search_pick["dist_m"] is not None else haversine_distance(cl["center_lat"], cl["center_lon"], final_lat, final_lon)
        recommended_radius = max(150, int(offset_d + 60))
        print(f"\n📍 Using Search Result: {final_lat:.5f}, {final_lon:.5f} [{selected_name}]")
        print(f"   Parking offset: {offset_d:.0f}m ➔ Recommended Geofence Radius: {recommended_radius}m")

    elif choice in ("g", "gps"):
        try:
            g_in = input("Enter central GPS (lat, lon) or exact address to re-center: ").strip()
            g_lat, g_lon, g_addr = parse_coords_or_address(g_in)
            if g_lat is None or g_lon is None:
                print("❌ Could not parse coordinates or address.")
                return False
            final_lat = g_lat
            final_lon = g_lon
            offset_d = haversine_distance(cl["center_lat"], cl["center_lon"], final_lat, final_lon)
            recommended_radius = max(150, int(offset_d + 60))
            print(f"✔ Centered at: {final_lat:.5f}, {final_lon:.5f} (Offset from parking: {offset_d:.1f}m)")

            def_name = g_addr.split(",")[0].strip() if g_addr else ""
            n_prompt = f"Enter Place Name [default: {def_name}]: " if def_name else "Enter Place Name: "
            n_in = input(n_prompt).strip()
            selected_name = n_in if n_in else def_name
        except (EOFError, KeyboardInterrupt):
            return "quit"

    elif choice in ("c", "custom"):
        try:
            selected_name = input("Enter Place Name: ").strip()
            c_coord = input(f"Center Coordinates [default: {final_lat:.5f}, {final_lon:.5f} (Enter to keep, or provide new)]: ").strip()
            if c_coord:
                c_lat, c_lon, _ = parse_coords_or_address(c_coord)
                if c_lat is not None and c_lon is not None:
                    final_lat, final_lon = c_lat, c_lon
                    offset_d = haversine_distance(cl["center_lat"], cl["center_lon"], final_lat, final_lon)
                    recommended_radius = max(150, int(offset_d + 60))
                    print(f"✔ Set center: {final_lat:.5f}, {final_lon:.5f} (Offset: {offset_d:.1f}m)")
        except (EOFError, KeyboardInterrupt):
            return "quit"
    else:
        selected_name = choice.strip()

    if not selected_name:
        print("⏩ Skipped (empty name).")
        return False

    is_valid, err = validate_place_name(selected_name)
    if not is_valid:
        print(f"❌ {err}")
        return False

    radius_m = prompt_radius_input(recommended_radius, label="Radius in meters")

    keywords = suggest_keywords(clean_addr, selected_name)
    notes = f"{selected_name} ({clean_addr.split(',')[0].strip()})"

    places[selected_name] = {
        "lat": round(final_lat, 5),
        "lon": round(final_lon, 5),
        "radius_m": radius_m,
        "keywords": keywords,
        "notes": notes
    }
    save_places(places)
    print(f"✔ Saved \"{selected_name}\" to places.json (Center: {final_lat:.5f}, {final_lon:.5f}, Radius: {radius_m}m)!")
    return True

# ---------------------------------------------------------------------------
# CLI Command: Review Drive Stops & Unlabelled Clusters (Ignores Chargers)
# ---------------------------------------------------------------------------
def cmd_review_drives(args):
    """
    Scans drives_master.csv (and summary CSVs) for unlabelled stops, clusters them
    by proximity, ignores charging stations, and runs an interactive POI review loop.
    """
    csv_candidates = find_candidate_drive_logs()

    if not csv_candidates:
        print(f"❌ No drive logs found in configured Tessie directories.")
        return

    places = load_places()
    
 # Read chargers to strictly exclude them from review (chargers left alone)
    chargers = load_chargers()
    
    unlabelled_stops = []
    for csv_file in csv_candidates:
        try:
            with open(csv_file, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    e_addr = (
                        row.get("Ending Location") or row.get("ending_location") or
                        row.get("Ending Address") or row.get("ending_address") or ""
                    ).strip()
                    
                    e_saved = (
                        row.get("Ending Saved Location") or row.get("ending_saved_location") or ""
                    ).strip()

                    e_lat_s = (
                        row.get("Ending Latitude") or row.get("ending_latitude") or
                        row.get("Ending Lat") or ""
                    ).strip()

                    e_lon_s = (
                        row.get("Ending Longitude") or row.get("ending_longitude") or
                        row.get("Ending Lon") or ""
                    ).strip()

                    ts_s = (
                        row.get("Ended At (Local)") or _legacy_get(row, "Ended At") or
                        row.get("Ended At") or row.get("Ended") or row.get("end_time") or ""
                    ).strip()
                    
                    if not e_lat_s or not e_lon_s:
                        continue
                    try:
                        lat = float(e_lat_s)
                        lon = float(e_lon_s)
                    except ValueError:
                        continue

                    matched = resolve_stop(e_addr, e_saved, lat, lon, places, chargers)
                    if not matched:
                        unlabelled_stops.append({
                            "address": e_addr,
                            "lat": lat,
                            "lon": lon,
                            "timestamp": ts_s
                        })
        except Exception as e:
            print(f"⚠️ Error reading {os.path.basename(csv_file)}: {e}")

    if not unlabelled_stops:
        print("✔ All drive stops match existing places in places.json or known charging stations!")
        return

 # Cluster unlabelled stops within 75 meters of each other
    clusters = []
    for stop in unlabelled_stops:
        found_cluster = None
        for cl in clusters:
            dist = haversine_distance(stop["lat"], stop["lon"], cl["center_lat"], cl["center_lon"])
            if dist <= 75.0:
                found_cluster = cl
                break
        if found_cluster:
            found_cluster["stops"].append(stop)
            n = len(found_cluster["stops"])
            found_cluster["center_lat"] = (found_cluster["center_lat"] * (n - 1) + stop["lat"]) / n
            found_cluster["center_lon"] = (found_cluster["center_lon"] * (n - 1) + stop["lon"]) / n
        else:
            clusters.append({
                "center_lat": stop["lat"],
                "center_lon": stop["lon"],
                "address": stop["address"],
                "stops": [stop]
            })

    min_stops = getattr(args, "min_stops", 2)
    valid_clusters = [cl for cl in clusters if len(cl["stops"]) >= min_stops]
    valid_clusters.sort(key=lambda x: len(x["stops"]), reverse=True)

    if not valid_clusters:
        print(f"✔ No unlabelled stop clusters with ≥ {min_stops} visits found.")
        return

 # Interactive Cluster Selection Menu Loop
    while True:
        places = load_places()
        ignored_entries = load_ignored_places()
        remaining_clusters = []
        for cl in valid_clusters:
            matched = False
            for p_name, p_data in places.items():
                p_lat, p_lon, p_rad = p_data.get("lat"), p_data.get("lon"), p_data.get("radius_m", 150)
                if p_lat is not None and p_lon is not None:
                    if haversine_distance(cl["center_lat"], cl["center_lon"], p_lat, p_lon) <= p_rad:
                        matched = True
                        break
            if not matched and is_cluster_ignored(cl, ignored_entries):
                matched = True
            if not matched:
                remaining_clusters.append(cl)

        if not remaining_clusters:
            print("\n🎉 All drive stop clusters are now tagged or resolved!")
            if ignored_entries:
                print(f"ℹ️  {len(ignored_entries)} previously-ignored cluster(s) - run `./places_manager.py ignored` to review them.")
            break

 # Render cluster table
        w_num = 4
        w_visits = 10
        w_addr = 46
        w_coords = 23
        w_date = 12

        header_cols = [
            pad_display("#", w_num, "right"),
            pad_display("Visits", w_visits, "right"),
            pad_display("Approximate Address / Suburb", w_addr),
            pad_display("Coordinates (Lat, Lon)", w_coords),
            pad_display("Last Visited", w_date)
        ]
        header = "  " + "  ".join(header_cols)
        total_w = display_len(header) + 2
        border = "─" * total_w

        title = f"🔍 UNLABELLED STOP CLUSTERS ({len(remaining_clusters)} clusters with ≥ {min_stops} visits)"
        print("\n┌" + border[2:] + "┐")
        print(f"│ {title}" + " " * max(0, total_w - display_len(title) - 4) + " │")
        print("├" + border[2:] + "┤")
        print("│" + header + " " * max(0, total_w - display_len(header) - 2) + "│")
        print("├" + border[2:] + "┤")

        for idx, cl in enumerate(remaining_clusters, 1):
            clean_addr = ", ".join(p.strip() for p in cl["address"].split(",")[:2]) if cl["address"] else "Unknown Address"
            if len(clean_addr) > w_addr:
                clean_addr = clean_addr[:w_addr-3] + "..."
            last_date = ""
            if cl["stops"]:
                last_date = cl["stops"][-1].get("timestamp", "")[:10]
            coords_str = f"{cl['center_lat']:.5f}, {cl['center_lon']:.5f}"
            coords_link = hyperlink(coords_str, gmaps_url(cl['center_lat'], cl['center_lon']))

            row_cols = [
                pad_display(f"[{idx:2d}]", w_num, "right"),
                pad_display(f"{len(cl['stops']):2d} visits", w_visits, "right"),
                pad_display(clean_addr, w_addr),
                pad_display(coords_link, w_coords),
                pad_display(last_date, w_date)
            ]
            row_str = "  " + "  ".join(row_cols)
            print("│" + row_str + " " * max(0, total_w - display_len(row_str) - 2) + "│")

        print("└" + border[2:] + "┘\n")

        print(f"  [1-{len(remaining_clusters)}] Select a cluster to tag/edit")
        print(f"  [a]ll (step through sequentially)")
        print(f"  [q]uit")
        try:
            choice = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if choice in ("q", "quit"):
            break
        elif choice in ("a", "all"):
            for idx, cl in enumerate(remaining_clusters, 1):
                res = review_single_cluster(cl, places, len(remaining_clusters), idx)
                if res == "quit":
                    return
            continue
        elif choice.isdigit():
            val = int(choice)
            if 1 <= val <= len(remaining_clusters):
                cl = remaining_clusters[val - 1]
                res = review_single_cluster(cl, places, len(remaining_clusters), val)
                if res == "quit":
                    return
            else:
                print("Invalid cluster number.")
        else:
            print("Invalid selection.")

# ---------------------------------------------------------------------------
# CLI Command: Review Previously-Ignored Stop Clusters
# ---------------------------------------------------------------------------
def cmd_review_ignored(args):
    """
    Lists stop clusters previously marked [i]gnore during `review`, and lets
    the user either promote one to a real named place (same POI/naming flow
    as review) or un-ignore it outright so it reappears in `review` again.
    """
    while True:
        ignored_entries = load_ignored_places()
        if not ignored_entries:
            print("✔ No ignored places - nothing to review.")
            return

        w_num = 4
        w_visits = 10
        w_addr = 46
        w_coords = 23
        w_date = 12

        header_cols = [
            pad_display("#", w_num, "right"),
            pad_display("Visits", w_visits, "right"),
            pad_display("Approximate Address / Suburb", w_addr),
            pad_display("Coordinates (Lat, Lon)", w_coords),
            pad_display("Ignored On", w_date)
        ]
        header = "  " + "  ".join(header_cols)
        total_w = display_len(header) + 2
        border = "─" * total_w

        title = f"🙈 IGNORED STOP CLUSTERS ({len(ignored_entries)})"
        print("\n┌" + border[2:] + "┐")
        print(f"│ {title}" + " " * max(0, total_w - display_len(title) - 4) + " │")
        print("├" + border[2:] + "┤")
        print("│" + header + " " * max(0, total_w - display_len(header) - 2) + "│")
        print("├" + border[2:] + "┤")

        for idx, ig in enumerate(ignored_entries, 1):
            clean_addr = ", ".join(p.strip() for p in ig.get("address", "").split(",")[:2]) if ig.get("address") else "Unknown Address"
            if len(clean_addr) > w_addr:
                clean_addr = clean_addr[:w_addr-3] + "..."
            ignored_date = (ig.get("ignored_at") or "")[:10]
            coords_str = f"{ig['center_lat']:.5f}, {ig['center_lon']:.5f}"
            coords_link = hyperlink(coords_str, gmaps_url(ig['center_lat'], ig['center_lon']))

            row_cols = [
                pad_display(f"[{idx:2d}]", w_num, "right"),
                pad_display(f"{len(ig.get('stops', [])):2d} visits", w_visits, "right"),
                pad_display(clean_addr, w_addr),
                pad_display(coords_link, w_coords),
                pad_display(ignored_date, w_date)
            ]
            row_str = "  " + "  ".join(row_cols)
            print("│" + row_str + " " * max(0, total_w - display_len(row_str) - 2) + "│")

        print("└" + border[2:] + "┘\n")

        print(f"  [1-{len(ignored_entries)}] Name it")
        print(f"  [u]nignore <N> (e.g. 'u 2') to let it reappear in review")
        print(f"  [q]uit")
        try:
            choice = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if choice in ("q", "quit"):
            break
        elif choice.startswith("u"):
            rest = choice[1:].strip()
            if not rest.isdigit():
                print("Usage: u <N>, e.g. 'u 2'")
                continue
            val = int(rest)
            if 1 <= val <= len(ignored_entries):
                removed = ignored_entries.pop(val - 1)
                save_ignored_places(ignored_entries)
                addr = removed.get("address", "that cluster")
                print(f"↩️  Un-ignored \"{addr}\" - it'll show up in `review` again.")
            else:
                print("Invalid cluster number.")
        elif choice.isdigit():
            val = int(choice)
            if 1 <= val <= len(ignored_entries):
                places = load_places()
                entry = ignored_entries[val - 1]
                res = review_single_cluster(entry, places, len(ignored_entries), val, allow_ignore=False)
                if res == "quit":
                    return
                if res is True:
 # Successfully named & saved to places.json - no longer needed here.
                    ignored_entries.pop(val - 1)
                    save_ignored_places(ignored_entries)
            else:
                print("Invalid cluster number.")
        else:
            print("Invalid selection.")

# ---------------------------------------------------------------------------
# CLI Command: Add / Update / Remove
# ---------------------------------------------------------------------------
def cmd_add(args):
    places = load_places()
    name = args.name.strip()
    
    is_valid, err = validate_place_name(name)
    if not is_valid:
        print(f"❌ {err}")
        return

    lat = args.lat
    lon = args.lon
    
    if args.address and (lat is None or lon is None):
        print(f"🌐 Geocoding \"{args.address}\"...")
        geo = geocode_address(args.address)
        if geo:
            lat = geo["lat"]
            lon = geo["lon"]
            print(f"✔ Coordinates resolved: {lat:.5f}, {lon:.5f}")
        else:
            print(f"❌ Failed to geocode address: \"{args.address}\"")
            return

    if lat is None or lon is None:
        print("❌ Both latitude and longitude (or --address) are required.")
        return

    keywords = args.keywords or []
    if not keywords and args.address:
        keywords = suggest_keywords(args.address, name)

    notes = args.notes or (f"{name} ({args.address})" if args.address else name)

    places[name] = {
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "radius_m": args.radius,
        "keywords": clean_place_keywords(keywords),
        "notes": notes
    }

    save_places(places)
    print(f"✔ Successfully added \"{name}\" to places.json!")

def cmd_remove(args):
    places = load_places()
    name = args.name.strip()
    if name not in places:
        print(f"❌ Place \"{name}\" not found in places.json.")
        return
    del places[name]
    save_places(places)
    print(f"✔ Removed \"{name}\" from places.json.")

def cmd_rename(args):
    places = load_places()
    old_name = args.old_name.strip()
    new_name = args.new_name.strip()
    if old_name not in places:
        print(f"❌ Place \"{old_name}\" not found in places.json.")
        return
    is_valid, err = validate_place_name(new_name)
    if not is_valid:
        print(f"❌ {err}")
        return
    if new_name in places and new_name != old_name:
        print(f"❌ Place \"{new_name}\" already exists in places.json.")
        return
    data = places.pop(old_name)
    places[new_name] = data
    save_places(places)
    print(f"✔ Renamed \"{old_name}\" to \"{new_name}\".")

def cmd_update(args):
    places = load_places()
    name = args.name.strip()
    target_key = None
    for k in places:
        if k.lower() == name.lower():
            target_key = k
            break
    if not target_key:
        print(f"❌ Place \"{name}\" not found in places.json.")
        return

    data = places[target_key]
    changed = False

    if args.radius is not None:
        old_rad = data.get("radius_m", 150)
        data["radius_m"] = args.radius
        print(f"✔ Radius updated: {old_rad}m ➔ {args.radius}m")
        changed = True

    if args.address:
        print(f"🌐 Geocoding \"{args.address}\"...")
        geo = geocode_address(args.address)
        if geo:
            data["lat"] = round(geo["lat"], 5)
            data["lon"] = round(geo["lon"], 5)
            print(f"✔ Coordinates updated to: {data['lat']}, {data['lon']}")
            changed = True
        else:
            print(f"⚠️ Failed to geocode address: \"{args.address}\"")

    if args.lat is not None and args.lon is not None:
        data["lat"] = round(args.lat, 5)
        data["lon"] = round(args.lon, 5)
        print(f"✔ Coordinates updated to: {data['lat']}, {data['lon']}")
        changed = True

    if args.add_keyword:
        current_kws = data.get("keywords", [])
        for kw in args.add_keyword:
            kw = kw.strip()
            if kw and kw not in current_kws:
                current_kws.append(kw)
                print(f"✔ Added keyword: \"{kw}\"")
                changed = True
        data["keywords"] = current_kws

    if args.remove_keyword:
        current_kws = data.get("keywords", [])
        for kw in args.remove_keyword:
            kw = kw.strip()
            if kw in current_kws:
                current_kws.remove(kw)
                print(f"✔ Removed keyword: \"{kw}\"")
                changed = True
        data["keywords"] = current_kws

    if args.notes is not None:
        data["notes"] = args.notes
        print(f"✔ Notes updated: \"{args.notes}\"")
        changed = True

    if changed:
        places[target_key] = data
        save_places(places)
        print(f"✔ Successfully updated \"{target_key}\" in places.json.")
    else:
        print("ℹ️ No changes specified.")

def cmd_alias(args):
    places = load_places()
    name = args.name.strip()
    target_key = None
    for k in places:
        if k.lower() == name.lower():
            target_key = k
            break
    if not target_key:
        print(f"❌ Place \"{name}\" not found in places.json.")
        return

    data = places[target_key]
    current_kws = data.get("keywords", [])
    added_kws = []

    if args.address:
        suggested = suggest_keywords(args.address, target_key)
        for kw in suggested:
            if kw not in current_kws:
                current_kws.append(kw)
                added_kws.append(kw)

        if args.expand_radius:
            geo = geocode_address(args.address)
            if geo and data.get("lat") and data.get("lon"):
                dist = haversine_distance(data["lat"], data["lon"], geo["lat"], geo["lon"])
                needed_rad = int(dist + 50)
                if needed_rad > data.get("radius_m", 150):
                    old_rad = data.get("radius_m", 150)
                    data["radius_m"] = needed_rad
                    print(f"✔ Auto-expanded radius: {old_rad}m ➔ {needed_rad}m (offset ~{dist:.1f}m)")

    if args.keywords:
        for kw in args.keywords:
            kw = kw.strip()
            if kw and kw not in current_kws:
                current_kws.append(kw)
                added_kws.append(kw)

    data["keywords"] = current_kws
    places[target_key] = data
    save_places(places)
    print(f"✔ Aliased to \"{target_key}\": added {added_kws}")

# ---------------------------------------------------------------------------
# CLI Command: Interactive Place Editor
# ---------------------------------------------------------------------------
def interactive_edit_place(name, places):
    """
    Interactive editing session for a single place in places.json.
    Returns ('back', places), ('deleted', places), or ('quit', places).
    """
    curr_name = name
    while True:
        if curr_name not in places:
            print(f"❌ Place \"{curr_name}\" not found in places.json.")
            return "back", places

        data = places[curr_name]
        lat = data.get("lat")
        lon = data.get("lon")
        rad = data.get("radius_m", 150)
        kws = data.get("keywords", [])
        notes = data.get("notes", "")

        coords_str = f"{lat:.5f}, {lon:.5f}" if (lat is not None and lon is not None) else "Not set"
        kws_str = ", ".join(kws) if kws else "(None)"

        print("\n" + "=" * 78)
        print(f"📍 Editing Place: {curr_name}")
        print("=" * 78)
        print(f" • Name:     {curr_name}")
        print(f" • GPS:      {coords_str}")
        print(f" • Radius:   {rad}m")
        print(f" • Keywords: {kws_str}")
        print(f" • Notes:    {notes if notes else '(None)'}")
        print("-" * 78)
        print("Select field to edit:")
        print(f"  [1] Rename / Edit Name      (current: \"{curr_name}\")")
        print(f"  [2] Edit Radius             (current: {rad}m)")
        print(f"  [3] Edit Coordinates (GPS)  (current: {coords_str})")
        print(f"  [4] Add Keyword(s) / Alias")
        print(f"  [5] Remove Keyword(s)")
        print(f"  [6] Edit All Keywords       (replace list)")
        print(f"  [7] Edit Notes              (current: \"{notes}\")")
        print(f"  [8] Search Nearby POIs      (via OpenStreetMap / Overpass API)")
        print(f"  [d] Delete Place permanently")
        print(f"  [b] Back to Place List")
        print(f"  [q] Quit")
        print("-" * 78)

        try:
            choice = input("Choice [1-8, d, b, q]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "back", places

        if choice in ("b", "back", ""):
            return "back", places
        elif choice in ("q", "quit"):
            return "quit", places
        elif choice == "1":
            try:
                new_n = input(f"Enter new name [default: \"{curr_name}\"]: ").strip()
                if new_n and new_n != curr_name:
                    is_valid, err = validate_place_name(new_n)
                    if not is_valid:
                        print(f"❌ {err}")
                    elif new_n in places:
                        print(f"❌ Place \"{new_n}\" already exists.")
                    else:
                        places[new_n] = places.pop(curr_name)
                        curr_name = new_n
                        save_places(places)
                        print(f"✔ Renamed to \"{curr_name}\"")
            except (EOFError, KeyboardInterrupt):
                pass
        elif choice == "2":
            try:
                r_in = input(f"Enter new radius in meters [current: {rad}m]: ").strip()
                if r_in:
                    try:
                        new_r = int(r_in)
                        if new_r > 0:
                            data["radius_m"] = new_r
                            places[curr_name] = data
                            save_places(places)
                            print(f"✔ Radius updated to {new_r}m")
                    except ValueError:
                        print("❌ Invalid radius number.")
            except (EOFError, KeyboardInterrupt):
                pass
        elif choice == "3":
            try:
                c_in = input(f"Enter new coordinates (lat, lon) or street address to geocode: ").strip()
                if c_in:
                    n_lat, n_lon, n_addr = parse_coords_or_address(c_in)
                    if n_lat is not None and n_lon is not None:
                        data["lat"] = round(n_lat, 5)
                        data["lon"] = round(n_lon, 5)
                        places[curr_name] = data
                        save_places(places)
                        print(f"✔ Coordinates updated to: {data['lat']}, {data['lon']}")
                        if n_addr:
                            print(f"   Address: {n_addr}")
                    else:
                        print("❌ Failed to parse coordinates or geocode address.")
            except (EOFError, KeyboardInterrupt):
                pass
        elif choice == "4":
            try:
                k_in = input("Enter keyword(s) or street name to add (comma-separated): ").strip()
                if k_in:
                    new_kws = [k.strip() for k in k_in.split(",") if k.strip()]
                    added = []
                    for k in new_kws:
                        if k not in data.get("keywords", []):
                            data.setdefault("keywords", []).append(k)
                            added.append(k)
                    if added:
                        places[curr_name] = data
                        save_places(places)
                        print(f"✔ Added keyword(s): {', '.join(added)}")
                    else:
                        print("ℹ️ All keywords already present.")
            except (EOFError, KeyboardInterrupt):
                pass
        elif choice == "5":
            if not kws:
                print("ℹ️ No keywords to remove.")
                continue
            print("\nCurrent Keywords:")
            for k_idx, kw in enumerate(kws, 1):
                print(f"  [{k_idx}] {kw}")
            try:
                print("  Enter keyword number(s) to remove (e.g. 1, 3)")
                print("  [b]ack")
                rem_in = input("  > ").strip().lower()
                if rem_in and rem_in not in ("b", "back"):
                    indices = []
                    for part in rem_in.split(","):
                        part = part.strip()
                        if part.isdigit() and 1 <= int(part) <= len(kws):
                            indices.append(int(part) - 1)
                    if indices:
                        removed = [kws[i] for i in sorted(indices, reverse=True)]
                        new_kws = [kw for i, kw in enumerate(kws) if i not in indices]
                        data["keywords"] = new_kws
                        places[curr_name] = data
                        save_places(places)
                        print(f"✔ Removed keyword(s): {', '.join(removed)}")
            except (EOFError, KeyboardInterrupt):
                pass
        elif choice == "6":
            try:
                all_k_in = input(f"Enter all keywords (comma-separated) [current: {kws_str}]: ").strip()
                if all_k_in != "":
                    new_kws = [k.strip() for k in all_k_in.split(",") if k.strip()]
                    data["keywords"] = new_kws
                    places[curr_name] = data
                    save_places(places)
                    print(f"✔ Keywords updated: {', '.join(new_kws)}")
            except (EOFError, KeyboardInterrupt):
                pass
        elif choice == "7":
            try:
                note_in = input(f"Enter new notes [current: \"{notes}\"]: ").strip()
                if note_in:
                    data["notes"] = note_in
                    places[curr_name] = data
                    save_places(places)
                    print(f"✔ Notes updated to: \"{note_in}\"")
            except (EOFError, KeyboardInterrupt):
                pass
        elif choice == "8":
            if lat is None or lon is None:
                print("❌ Place coordinates are missing.")
                continue
            print(f"\n🌐 Querying nearby POIs around ({lat:.5f}, {lon:.5f})...")
            pois = query_overpass_pois(lat, lon, radius_m=max(250, rad + 50))
            if not pois:
                print("ℹ️ No POIs found nearby.")
                continue
            print(f"\n🏢 Nearby POIs:")
            for p_idx, p in enumerate(pois[:7], 1):
                print(f"  [{p_idx}] {p['name']} ({p['category']}) [~{p['dist_m']}m | Center: {p['lat']:.5f}, {p['lon']:.5f}]")
            print(f"  [b] Back")
            try:
                poi_c = input("  > ").strip().lower()
                if poi_c.isdigit() and 1 <= int(poi_c) <= min(len(pois), 7):
                    selected_poi = pois[int(poi_c) - 1]
                    data["lat"] = round(selected_poi["lat"], 5)
                    data["lon"] = round(selected_poi["lon"], 5)
                    adopt_name = input(f"Adopt POI name \"{selected_poi['name']}\"? [y/N]: ").strip().lower()
                    if adopt_name == "y":
                        if selected_poi["name"] != curr_name and selected_poi["name"] in places:
                            print(f"⚠️ Place \"{selected_poi['name']}\" already exists.")
                        elif selected_poi["name"] != curr_name:
                            places[selected_poi["name"]] = places.pop(curr_name)
                            curr_name = selected_poi["name"]
                    places[curr_name] = data
                    save_places(places)
                    print(f"✔ Updated coordinates to ({data['lat']}, {data['lon']})")
            except (EOFError, KeyboardInterrupt):
                pass
        elif choice == "d":
            try:
                confirm = input(f"⚠️ Are you sure you want to permanently delete \"{curr_name}\"? [y/N]: ").strip().lower()
                if confirm == "y":
                    del places[curr_name]
                    save_places(places)
                    print(f"✔ Deleted \"{curr_name}\" from places.json.")
                    return "deleted", places
            except (EOFError, KeyboardInterrupt):
                pass
        else:
            print("Invalid choice.")

def cmd_edit(args):
    """Interactive place editor with table selection, search filtering, and live field editing."""
    places = load_places()
    if not places:
        print("ℹ️ No places found in places.json. Use 'add' or 'lookup' to create one.")
        return

 # Direct name argument provided
    if getattr(args, "name", None):
        target_name = None
        q = args.name.strip().lower()
        for k in places:
            if k.lower() == q:
                target_name = k
                break
        if not target_name:
            for k in places:
                if q in k.lower():
                    target_name = k
                    break
        if target_name:
            res, places = interactive_edit_place(target_name, places)
            return
        else:
            print(f"❌ Place matching \"{args.name}\" not found.")

    search_filter = getattr(args, "search", "") or ""
    ref_near = getattr(args, "near", None)

    while True:
        places = load_places()
        if not places:
            print("ℹ️ No places remaining.")
            break

        ref_lat, ref_lon = None, None
        if ref_near:
            if ref_near in places:
                ref_lat = places[ref_near].get("lat")
                ref_lon = places[ref_near].get("lon")
            else:
                geo = geocode_address(ref_near)
                if geo:
                    ref_lat, ref_lon = geo["lat"], geo["lon"]

        items = list(places.items())
        if ref_lat is not None and ref_lon is not None:
            items.sort(key=lambda x: haversine_distance(ref_lat, ref_lon, x[1].get("lat", 0), x[1].get("lon", 0)))
        else:
            items.sort(key=lambda x: x[0].lower())

        if search_filter:
            q = search_filter.lower()
            filtered = []
            for name, data in items:
                kws = " ".join(data.get("keywords", [])).lower()
                notes = data.get("notes", "").lower()
                if q in name.lower() or q in kws or q in notes:
                    filtered.append((name, data))
            items = filtered

        if not items:
            print(f"\nℹ️ No places matched search query: \"{search_filter}\"")
            try:
                print("  [Enter] Clear filter")
                print("  [a]dd new")
                print("  [q]uit")
                reset = input("  > ").strip().lower()
                if reset in ("q", "quit"):
                    break
                elif reset in ("a", "add"):
                    interactive_lookup_and_add()
                    continue
                else:
                    search_filter = ""
                    continue
            except (EOFError, KeyboardInterrupt):
                break

 # Render places table
        w_num = 4
        w_name = 30
        w_coords = 23
        w_rad = 8
        w_kws = 36

        header_cols = [
            pad_display("#", w_num, "right"),
            pad_display("Place Name", w_name),
            pad_display("Coordinates (Lat, Lon)", w_coords),
            pad_display("Radius", w_rad, "right"),
            pad_display("Keywords / Aliases", w_kws)
        ]
        header = "  " + "  ".join(header_cols)
        total_w = display_len(header) + 2
        border = "─" * total_w

        filter_label = f" [Filter: \"{search_filter}\"]" if search_filter else ""
        title = f"📍 SAVED PLACES ({len(items)} places){filter_label}"

        print("\n┌" + border[2:] + "┐")
        print(f"│ {title}" + " " * max(0, total_w - display_len(title) - 4) + " │")
        print("├" + border[2:] + "┤")
        print("│" + header + " " * max(0, total_w - display_len(header) - 2) + "│")
        print("├" + border[2:] + "┤")

        for idx, (p_name, p_data) in enumerate(items, 1):
            lat = p_data.get("lat")
            lon = p_data.get("lon")
            coords_str = f"{lat:.5f}, {lon:.5f}" if (lat is not None and lon is not None) else "N/A"
            rad_str = f"{p_data.get('radius_m', 150)}m"
            kws = ", ".join(p_data.get("keywords", []))
            if len(kws) > w_kws:
                kws = kws[:w_kws - 3] + "..."

            row_cols = [
                pad_display(f"[{idx:2d}]", w_num, "right"),
                pad_display(p_name[:w_name], w_name),
                pad_display(coords_str, w_coords),
                pad_display(rad_str, w_rad, "right"),
                pad_display(kws, w_kws)
            ]
            row_str = "  " + "  ".join(row_cols)
            print("│" + row_str + " " * max(0, total_w - display_len(row_str) - 2) + "│")

        print("└" + border[2:] + "┘\n")

        print(f"  [1-{len(items)}] Select a place to edit")
        print(f"  [s]earch <query>")
        print(f"  [a]dd new")
        print(f"  [q]uit")
        try:
            choice = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        c_lower = choice.lower()
        if c_lower in ("q", "quit"):
            break
        elif c_lower in ("a", "add"):
            interactive_lookup_and_add()
            continue
        elif c_lower.startswith("s ") or c_lower.startswith("/"):
            query_part = choice.split(" ", 1)[1] if " " in choice else choice.lstrip("/")
            search_filter = query_part.strip()
            continue
        elif c_lower == "s":
            try:
                s_in = input("Enter search keyword (or Enter to clear): ").strip()
                search_filter = s_in
                continue
            except (EOFError, KeyboardInterrupt):
                continue
        elif choice.isdigit():
            val = int(choice)
            if 1 <= val <= len(items):
                selected_place = items[val - 1][0]
                action, places = interactive_edit_place(selected_place, places)
                if action == "quit":
                    break
            else:
                print("Invalid place number.")
        else:
            print("Invalid selection.")

# ---------------------------------------------------------------------------
# Main CLI Router
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="""
NAME
    places_manager.py - Tesla / Tessie Location Management & POI Lookup Engine

SYNOPSIS
    places_manager.py list   [--near PLACE] [--search TEXT] [--sort name|radius|dist]
    places_manager.py edit   [NAME] [--search TEXT] [--near PLACE]
    places_manager.py lookup [QUERY ...] [--radius METERS]
    places_manager.py review [--min-stops N]
    places_manager.py ignored
    places_manager.py add    NAME [--address ADDR | --lat LAT --lon LON] [--radius M]
                     [--keywords K ...] [--notes TEXT]
    places_manager.py update NAME [--radius M] [--address ADDR | --lat LAT --lon LON]
                     [--add-keyword K ...] [--remove-keyword K ...] [--notes TEXT]
    places_manager.py alias  NAME --address ADDR [--keywords K ...] [--expand-radius]
    places_manager.py remove NAME
    places_manager.py rename OLD_NAME NEW_NAME

DESCRIPTION
    Manage the named-place registry (places.json) used across the Tesla/Tessie
    tooling for geofencing, proximity search, and POI lookups. Each subcommand
    below covers one place-management workflow; run 'places_manager.py COMMAND -h'
    for that subcommand's own options.
""",
        epilog="""
EXAMPLES
  places_manager.py list --near Home --sort dist
  places_manager.py lookup "100 George St, Sydney"
  places_manager.py add "Work" --address "1 Martin Pl, Sydney" --radius 100
  places_manager.py update "Work" --add-keyword "Martin Place"
  places_manager.py review --min-stops 3
""",
        formatter_class=argparse.RawTextHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

 # Subcommand: list
    p_list = subparsers.add_parser("list", help="List all stored places in a clean formatted table")
    p_list.add_argument("--near", type=str, help="Reference place name or coordinates to sort by proximity")
    p_list.add_argument("--search", type=str, help="Search query filter for name, keywords, or notes")
    p_list.add_argument("--sort", choices=["visits", "name", "radius", "dist"], default="visits", help="Sort order (default: visits)")

 # Subcommand: inspect / info / query
    p_inspect = subparsers.add_parser("inspect", aliases=["info", "query", "show"], help="Inspect a specific stored location's details, coordinates, and recent visit/trip history")
    p_inspect.add_argument("name", type=str, help="Place name or search query to inspect")

 # Subcommand: edit
    p_edit = subparsers.add_parser("edit", help="Interactively select and edit stored places")
    p_edit.add_argument("name", nargs="?", default=None, help="Optional place name to jump directly to editing")
    p_edit.add_argument("--search", type=str, help="Initial search filter")
    p_edit.add_argument("--near", type=str, help="Reference place name or coordinates to sort by proximity")

 # Subcommand: lookup
    p_lookup = subparsers.add_parser("lookup", help="Interactively look up an address or coordinates using Places/POI API")
    p_lookup.add_argument("query", nargs="*", help="Address string or 'lat lon' coordinates")
    p_lookup.add_argument("--radius", type=int, default=150, help="Default geofence radius in meters")

 # Subcommand: review
    p_review = subparsers.add_parser("review", help="Scan drives_master.csv and interactively review unlabelled stop clusters (ignores chargers)")
    p_review.add_argument("--min-stops", type=int, default=2, help="Minimum visits required to flag a stop cluster (default: 2)")

 # Subcommand: ignored
    p_ignored = subparsers.add_parser("ignored", help="Review stop clusters previously marked [i]gnore during 'review' - promote one to a named place, or un-ignore it")

 # Subcommand: add
    p_add = subparsers.add_parser("add", help="Add a new place directly via CLI")
    p_add.add_argument("name", type=str, help="Place name / nickname")
    p_add.add_argument("--address", type=str, help="Street address to geocode automatically")
    p_add.add_argument("--lat", type=float, help="Latitude")
    p_add.add_argument("--lon", type=float, help="Longitude")
    p_add.add_argument("--radius", type=int, default=150, help="Geofence radius in meters (default: 150)")
    p_add.add_argument("--keywords", nargs="+", help="Keywords/aliases for address matching")
    p_add.add_argument("--notes", type=str, help="Optional descriptive notes")

 # Subcommand: update
    p_update = subparsers.add_parser("update", help="Update properties of an existing place")
    p_update.add_argument("name", type=str, help="Existing place name")
    p_update.add_argument("--radius", type=int, help="New geofence radius in meters")
    p_update.add_argument("--address", type=str, help="New street address to geocode coordinates")
    p_update.add_argument("--lat", type=float, help="New latitude")
    p_update.add_argument("--lon", type=float, help="New longitude")
    p_update.add_argument("--add-keyword", nargs="+", help="One or more keywords/street names to append")
    p_update.add_argument("--remove-keyword", nargs="+", help="One or more keywords to remove")
    p_update.add_argument("--notes", type=str, help="New notes string")

 # Subcommand: alias
    p_alias = subparsers.add_parser("alias", help="Alias a secondary street address to an existing place")
    p_alias.add_argument("name", type=str, help="Existing place name")
    p_alias.add_argument("--address", type=str, help="Secondary entrance/street address to alias")
    p_alias.add_argument("--keywords", nargs="+", help="Additional keywords to alias")
    p_alias.add_argument("--expand-radius", action="store_true", help="Auto-expand radius if new address offset exceeds current radius")

 # Subcommand: remove
    p_remove = subparsers.add_parser("remove", help="Remove a place from places.json")
    p_remove.add_argument("name", type=str, help="Place name to remove")

 # Subcommand: rename
    p_rename = subparsers.add_parser("rename", help="Rename an existing place in places.json")
    p_rename.add_argument("old_name", type=str, help="Existing place name")
    p_rename.add_argument("new_name", type=str, help="New place name")

    args = parser.parse_args()

    if not args.command:
 # If user passed a bare place name or search string, inspect directly
        raw_args = [a for a in sys.argv[1:] if not a.startswith("-")]
        if raw_args:
            inspect_place(" ".join(raw_args))
            return
        args.near = None
        args.search = None
        args.sort = "visits"
        cmd_list(args)
        return

    if args.command == "list":
        cmd_list(args)
    elif args.command in ("inspect", "info", "query", "show"):
        inspect_place(args.name)
    elif args.command == "edit":
        cmd_edit(args)
    elif args.command == "lookup":
        q = " ".join(args.query) if args.query else None
        interactive_lookup_and_add(query_or_addr=q, default_radius=args.radius)
    elif args.command == "review":
        cmd_review_drives(args)
    elif args.command == "ignored":
        cmd_review_ignored(args)
    elif args.command == "add":
        cmd_add(args)
    elif args.command == "update":
        cmd_update(args)
    elif args.command == "alias":
        cmd_alias(args)
    elif args.command == "remove":
        cmd_remove(args)
    elif args.command == "rename":
        cmd_rename(args)

if __name__ == "__main__":
    main()

