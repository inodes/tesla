"""places_common.py - places.json (load, save, name checks, keywords), reverse and forward geocoding, distances and the OpenStreetMap (Overpass) look-up of what is near a place. Used by places_manager, charger_find, places_osm_editor, the charger providers, the drive and charge analysers and the app."""

import os
import sys
import time
import re
import json
import math
import tempfile
import urllib.request
import urllib.parse
from typing import List, Tuple

TESSIE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "resources")   # the repo's resources/ (the fallback below)

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
