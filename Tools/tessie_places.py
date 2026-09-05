#!/usr/bin/env python3
"""
Tesla / Tessie Location Management & POI Lookup Engine
======================================================
- Dedicated place manager for Tessie/places.json (leaves chargers alone)
- Inspect, filter, and search stored locations in a formatted table
- Interactive POI lookup by street address or GPS coordinates via OpenStreetMap / Overpass
- Drive stop clustering and interactive discovery from drives_master.csv (ignores charging stops)
- Automatic POI center coordinate resolution and custom coordinate/address override
- Multi-drive auto-synchronization across all mounted TESLADRIVE* volumes
- Strict Zero-PII compliance across all documentation and examples
"""

import os
import sys
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
from datetime import datetime
from collections import defaultdict

# ---------------------------------------------------------------------------
# Path & Environment Setup
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
TESSIE_DIR = os.path.join(REPO_ROOT, "Tessie")
PLACES_JSON_PATH = os.path.join(TESSIE_DIR, "places.json")
CONFIG_JSON_PATH = os.path.join(TESSIE_DIR, "config.json")
DRIVES_MASTER_PATH = os.path.join(TESSIE_DIR, "drives_master.csv")
CHARGING_JSON_PATH = os.path.join(TESSIE_DIR, "tesla_chargers.json") if os.path.isfile(os.path.join(TESSIE_DIR, "tesla_chargers.json")) else os.path.join(TESSIE_DIR, "charging.json")
SUPERCHARGERS_JSON_PATH = os.path.join(TESSIE_DIR, "tesla_superchargers.json") if os.path.isfile(os.path.join(TESSIE_DIR, "tesla_superchargers.json")) else os.path.join(TESSIE_DIR, "superchargers.json")

# ---------------------------------------------------------------------------
# Unicode & Terminal Display Utilities
# ---------------------------------------------------------------------------
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
        if c in ('🔄', '💾', '🔴', '🚗', '📹', '📂', '🚪', '⚠️', '✔', '❌', '🕒', '📅', '📍', '🛑', '⚡', '🏢', '🏷', '📫', '🎯', '🅿'):
            return 2
        w = unicodedata.east_asian_width(c)
        if w in ('W', 'F'):
            return 2
        return 1

def display_len(s):
    clean = re.sub(r'\x1b\[[0-9;]*m', '', s)
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
    else:
        return s + " " * pad_len

# ---------------------------------------------------------------------------
# Geodesic Math
# ---------------------------------------------------------------------------
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
# Drive Synchronization
# ---------------------------------------------------------------------------
def sync_places_file(source_path=PLACES_JSON_PATH):
    """Synchronizes places.json to all mounted TESLADRIVE* volumes."""
    synced = []
    if not os.path.exists(source_path):
        return synced
    
    volumes_dir = "/Volumes"
    if os.path.exists(volumes_dir):
        try:
            for entry in os.listdir(volumes_dir):
                if entry.startswith("TESLADRIVE"):
                    vol_path = os.path.join(volumes_dir, entry)
                    dest_tessie = os.path.join(vol_path, "Tessie")
                    if os.path.isdir(dest_tessie):
                        dest_file = os.path.join(dest_tessie, os.path.basename(source_path))
                        try:
                            shutil.copyfile(source_path, dest_file)
                            synced.append(dest_file)
                        except Exception as e:
                            print(f"⚠️  Failed to sync to {dest_file}: {e}")
        except Exception:
            pass
    return synced

# ---------------------------------------------------------------------------
# Places JSON Storage Manager (Only touches places.json, leaves chargers alone)
# ---------------------------------------------------------------------------
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
    """Sorts keys and atomically saves places dictionary to places.json, then syncs to drives."""
    sorted_places = dict(sorted(places.items(), key=lambda x: x[0].lower()))
    
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

    synced = sync_places_file(path)
    return synced

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
                    return p_name

    return None

# ---------------------------------------------------------------------------
# POI Lookup Engine (Nominatim + Overpass)
# ---------------------------------------------------------------------------
USER_AGENT = "TeslaPlacesManager/1.0 (https://github.com/inodes/tesla)"

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter"
]

def geocode_address(address_str):
    """Geocodes a street address or place name string to lat, lon via Nominatim."""
    if not address_str or not address_str.strip():
        return None
    
    clean_addr = address_str.strip()
    encoded = urllib.parse.quote(clean_addr)
    url = f"https://nominatim.openstreetmap.org/search?q={encoded}&format=json&addressdetails=1&extratags=1&limit=5"
    
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data and isinstance(data, list) and len(data) > 0:
                first = data[0]
                return {
                    "lat": float(first.get("lat")),
                    "lon": float(first.get("lon")),
                    "display_name": first.get("display_name", ""),
                    "type": first.get("type", ""),
                    "class": first.get("class", ""),
                    "address": first.get("address", {}),
                    "raw": first
                }
    except Exception:
        pass
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

def query_overpass_pois(lat, lon, radius_m=250):
    """Queries Overpass API for all named POIs / venues within radius_m of lat, lon."""
    query = f"""
    [out:json][timeout:10];
    (
      node(around:{radius_m},{lat},{lon})["name"];
      way(around:{radius_m},{lat},{lon})["name"];
    );
    out center tags;
    """
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            req = urllib.request.Request(endpoint, data=data, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=8) as resp:
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
                    building = tags.get("building")
                    brand = tags.get("brand")

                    category = amenity or shop or leisure or tourism or office or healthcare
                    if not category and building and building != "yes":
                        category = f"building ({building})"
                    
                    if not category and not brand:
                        continue

                    if name.lower() in seen_names:
                        continue
                    seen_names.add(name.lower())

                    p_lat = el.get("lat") or el.get("center", {}).get("lat", lat)
                    p_lon = el.get("lon") or el.get("center", {}).get("lon", lon)
                    dist = haversine_distance(lat, lon, p_lat, p_lon)

                    cat_label = (category or "business").replace("_", " ").title()
                    if brand and brand.lower() != name.lower():
                        cat_label += f" ({brand})"

                    results.append({
                        "name": name,
                        "category": cat_label,
                        "lat": p_lat,
                        "lon": p_lon,
                        "dist_m": round(dist, 1)
                    })

                results.sort(key=lambda x: x["dist_m"])
                return results
        except Exception:
            continue
    return []

def suggest_keywords(address_str, poi_name=""):
    """Extracts clean street and address keywords from a raw address string."""
    keywords = []
    if not address_str:
        return keywords
    
    parts = [p.strip() for p in re.split(r'[,;]', address_str) if p.strip()]
    if parts:
        street_part = parts[0]
        keywords.append(street_part)
        
        m = re.match(r'^\d+[\w\-/]*\s+(.+)$', street_part)
        if m:
            street_only = m.group(1).strip()
            if street_only and street_only not in keywords:
                keywords.append(street_only)
        
        for p in list(keywords):
            abbr = p.replace(" Road", " Rd").replace(" Street", " St").replace(" Avenue", " Ave").replace(" Highway", " Hwy")
            if abbr != p and abbr not in keywords:
                keywords.append(abbr)
    
    if poi_name and len(poi_name) > 3 and poi_name not in keywords:
        keywords.append(poi_name)
        
    return keywords

def parse_coords_or_address(user_input):
    """
    Parses 'lat, lon' coordinate string or geocodes an address/name string.
    Returns (lat, lon, display_name) or (None, None, None).
    """
    if not user_input or not user_input.strip():
        return None, None, None
    
    clean = user_input.strip()
    coord_match = re.match(r'^\s*([+-]?\d+(?:\.\d+)?)[,\s]+([+-]?\d+(?:\.\d+)?)\s*$', clean)
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
    Interactively resolves an address or GPS coordinate, fetches POI candidates,
    prompts the user to pick, customize, recenter, or skip, and saves to places.json.
    """
    places = load_places()
    
    resolved_lat = lat
    resolved_lon = lon
    resolved_addr = query_or_addr or ""

    print(f"\n🌐 Querying Places API...")
    
    if resolved_lat is not None and resolved_lon is not None:
        rev = reverse_geocode(resolved_lat, resolved_lon)
        if rev:
            resolved_addr = resolved_addr or rev.get("display_name", "")
    elif query_or_addr:
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

    pois = query_overpass_pois(resolved_lat, resolved_lon, radius_m=250)

    print("\n" + "=" * 78)
    print(f"📍 Location:   {resolved_addr[:65] if resolved_addr else 'Unknown Address'}")
    print(f"🎯 GPS Coords: {resolved_lat:.5f}, {resolved_lon:.5f}")
    print("=" * 78)

    if pois:
        print("\n🏢 Nearby Point-of-Interest (POI) Candidates:")
        for idx, poi in enumerate(pois[:7], 1):
            print(f"  [{idx}] {poi['name']} ({poi['category']})  [~{poi['dist_m']}m away | Center: {poi['lat']:.5f}, {poi['lon']:.5f}]")
    else:
        print("\nℹ️  No specific commercial POI found in OpenStreetMap within 250m.")

    print("\nOptions:")
    if pois:
        print(f"  [1-{len(pois[:7])}] Accept suggested POI (uses POI center & recommended geofence radius)")
    print("  [c]   Enter custom name (uses current coordinates)")
    print("  [g]   Provide custom GPS coordinates or address (set a precise center)")
    print("  [s]   Skip / Keep address as-is (Leave drive unchanged)")
    print("  [q]   Quit")

    try:
        choice = input("\nSelect choice: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None

    if choice in ("s", "skip", ""):
        print("⏩ Skipped (kept as raw drive address).")
        return None
    elif choice in ("q", "quit"):
        return None

    selected_name = None
    final_lat = resolved_lat
    final_lon = resolved_lon
    recommended_radius = default_radius

    if choice.isdigit() and 1 <= int(choice) <= len(pois[:7]):
        poi = pois[int(choice) - 1]
        selected_name = poi["name"]
        final_lat = poi["lat"]
        final_lon = poi["lon"]
        recommended_radius = max(150, int(poi["dist_m"] + 60))
        print(f"\n📍 Using POI Center: {final_lat:.5f}, {final_lon:.5f} [{selected_name}]")
        print(f"   Parking offset: {poi['dist_m']}m ➔ Recommended Geofence Radius: {recommended_radius}m")

    elif choice == "g":
        try:
            g_in = input("Enter central GPS (lat, lon) or exact address to re-center: ").strip()
            g_lat, g_lon, g_addr = parse_coords_or_address(g_in)
            if g_lat is None or g_lon is None:
                print("❌ Could not parse coordinates or address.")
                return None
            final_lat = g_lat
            final_lon = g_lon
            resolved_addr = g_addr or resolved_addr
            offset_d = haversine_distance(resolved_lat, resolved_lon, final_lat, final_lon)
            recommended_radius = max(150, int(offset_d + 60))
            print(f"✔ Centered at: {final_lat:.5f}, {final_lon:.5f} (Offset from parking: {offset_d:.1f}m)")
            
            # Prompt name
            def_name = g_addr.split(",")[0].strip() if g_addr else ""
            n_prompt = f"Enter Place Name [default: {def_name}]: " if def_name else "Enter Place Name: "
            n_in = input(n_prompt).strip()
            selected_name = n_in if n_in else def_name
        except (EOFError, KeyboardInterrupt):
            return None

    elif choice == "c":
        try:
            selected_name = input("Enter Place Name: ").strip()
            # Optional coordinate override
            c_coord = input(f"Center Coordinates [default: {final_lat:.5f}, {final_lon:.5f} (Enter to keep, or provide new)]: ").strip()
            if c_coord:
                c_lat, c_lon, _ = parse_coords_or_address(c_coord)
                if c_lat is not None and c_lon is not None:
                    final_lat, final_lon = c_lat, c_lon
                    offset_d = haversine_distance(resolved_lat, resolved_lon, final_lat, final_lon)
                    recommended_radius = max(150, int(offset_d + 60))
                    print(f"✔ Set center: {final_lat:.5f}, {final_lon:.5f} (Offset: {offset_d:.1f}m)")
        except (EOFError, KeyboardInterrupt):
            return None
    else:
        selected_name = choice

    if not selected_name:
        print("❌ Place name cannot be empty.")
        return None

    try:
        r_input = input(f"Geofence Radius in meters [default {recommended_radius}m]: ").strip()
        radius_m = int(r_input) if r_input else recommended_radius
    except Exception:
        radius_m = recommended_radius

    kw_candidates = suggest_keywords(resolved_addr, selected_name)
    kw_str = ", ".join(kw_candidates) if kw_candidates else ""
    try:
        k_input = input(f"Keywords / Aliases [default: {kw_str}]: ").strip()
        if k_input:
            keywords = [k.strip() for k in k_input.split(",") if k.strip()]
        else:
            keywords = kw_candidates
    except Exception:
        keywords = kw_candidates

    notes = f"{selected_name} ({resolved_addr.split(',')[0].strip() if resolved_addr else ''})"

    places[selected_name] = {
        "lat": round(final_lat, 5),
        "lon": round(final_lon, 5),
        "radius_m": radius_m,
        "keywords": keywords,
        "notes": notes
    }

    synced = save_places(places)
    print(f"\n✔ Successfully saved \"{selected_name}\" to places.json (Center: {final_lat:.5f}, {final_lon:.5f}, Radius: {radius_m}m)!")
    if synced:
        for s in synced:
            print(f"✔ Synced to {s}")
    
    return selected_name

# ---------------------------------------------------------------------------
# CLI Command: List Stored Places
# ---------------------------------------------------------------------------
def cmd_list(args):
    places = load_places()
    if not places:
        print("ℹ️  No places defined in Tessie/places.json.")
        return

    if args.search:
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

    ref_lat, ref_lon = None, None
    if args.near:
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
    if ref_lat is not None and ref_lon is not None:
        items.sort(key=lambda x: haversine_distance(ref_lat, ref_lon, x[1]["lat"], x[1]["lon"]))
    elif args.sort == "name":
        items.sort(key=lambda x: x[0].lower())
    elif args.sort == "radius":
        items.sort(key=lambda x: x[1].get("radius_m", 0), reverse=True)

    w_num = 4
    w_name = 28
    w_coords = 22
    w_radius = 8
    w_dist = 11 if ref_lat is not None else 0
    w_kws = 36

    header_cols = [
        pad_display("#", w_num, "right"),
        pad_display("Place Name", w_name),
        pad_display("Coordinates (Lat, Lon)", w_coords),
        pad_display("Radius", w_radius, "right"),
    ]
    if ref_lat is not None:
        header_cols.append(pad_display("Dist (km)", w_dist, "right"))
    header_cols.append(pad_display("Keywords / Aliases", w_kws))

    header = "  " + "  ".join(header_cols)
    total_w = display_len(header) + 2
    border = "─" * total_w

    title = f"📍 STORED LOCATIONS ({len(items)} places)"
    if args.near:
        title += f" [Sorted by distance from {args.near}]"

    print("\n┌" + border[2:] + "┐")
    print(f"│ {title}" + " " * max(0, total_w - display_len(title) - 4) + " │")
    print("├" + border[2:] + "┤")
    print("│" + header + " " * max(0, total_w - display_len(header) - 2) + "│")
    print("├" + border[2:] + "┤")

    for idx, (name, data) in enumerate(items, 1):
        lat = data.get("lat", 0.0)
        lon = data.get("lon", 0.0)
        radius = f"{data.get('radius_m', 0)}m"
        kws = ", ".join(data.get("keywords", []))
        if len(kws) > w_kws:
            kws = kws[:w_kws - 3] + "..."
        coords_str = f"{lat:.5f}, {lon:.5f}"

        row_cols = [
            pad_display(f"[{idx:2d}]", w_num, "right"),
            pad_display(name[:w_name], w_name),
            pad_display(coords_str, w_coords),
            pad_display(radius, w_radius, "right"),
        ]
        if ref_lat is not None:
            dist_km = haversine_distance(ref_lat, ref_lon, lat, lon) / 1000.0
            row_cols.append(pad_display(f"{dist_km:.1f} km", w_dist, "right"))
        row_cols.append(pad_display(kws, w_kws))

        row_str = "  " + "  ".join(row_cols)
        print("│" + row_str + " " * max(0, total_w - display_len(row_str) - 2) + "│")

    print("└" + border[2:] + "┘\n")

# ---------------------------------------------------------------------------
# Drive Log Discovery Helper
# ---------------------------------------------------------------------------
def load_config():
    """Loads configuration dictionary from config.json."""
    default_config = {
        "landing_directory": "~/Downloads",
        "inbox_directory": "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie/Inbox",
        "tessie_directory": "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie",
        "invoices_directory": "~/iCloud/PDF/Tesla/charging_invoices"
    }
    for cfg_p in [CONFIG_JSON_PATH, os.path.join(REPO_ROOT, "config.json")]:
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
    tessie_dir = os.path.abspath(os.path.expanduser(config.get("tessie_directory", "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie")))
    
    search_dirs = [tessie_dir]
    volumes_root = "/Volumes"
    if os.path.isdir(volumes_root):
        try:
            for entry in os.listdir(volumes_root):
                if entry.startswith("TESLADRIVE"):
                    vol_tessie = os.path.join(volumes_root, entry, "Tessie")
                    if os.path.isdir(vol_tessie) and vol_tessie not in search_dirs:
                        search_dirs.append(vol_tessie)
        except Exception:
            pass
    if TESSIE_DIR not in search_dirs:
        search_dirs.append(TESSIE_DIR)

    # 1. Check for drives_master.csv first
    for d in search_dirs:
        mf = os.path.join(d, "drives_master.csv")
        if os.path.isfile(mf):
            return [mf]

    # 2. Check for summary CSVs
    summaries = []
    for d in search_dirs:
        for f in glob.glob(os.path.join(d, "drives_summary_*.csv")):
            if f not in summaries:
                summaries.append(f)
    return summaries

def review_single_cluster(cl, places, total_clusters=1, cluster_num=1):
    clean_addr = cl["address"] or f"{cl['center_lat']:.5f}, {cl['center_lon']:.5f}"
    print(f"\n==============================================================================")
    print(f"📍 Cluster [{cluster_num}/{total_clusters}]: {len(cl['stops'])} visits")
    print(f"Address: {clean_addr}")
    print(f"GPS:     {cl['center_lat']:.5f}, {cl['center_lon']:.5f}")
    print(f"==============================================================================")

    pois = query_overpass_pois(cl["center_lat"], cl["center_lon"], radius_m=250)
    candidates = pois[:5]

    if candidates:
        print("\n🏢 Suggested POIs from API:")
        for p_idx, poi in enumerate(candidates, 1):
            print(f"  [{p_idx}] {poi['name']} ({poi['category']}) [~{poi['dist_m']}m | Center: {poi['lat']:.5f}, {poi['lon']:.5f}]")
    else:
        print("\nℹ️  No specific commercial POI found in OpenStreetMap within 250m.")

    print("\nAction:")
    if candidates:
        print(f"  [1-{len(candidates)}] Accept suggested POI (uses POI center & auto-adjusted radius)")
    print("  [c]   Enter custom name (uses parking stop coordinates)")
    print("  [g]   Provide custom GPS coordinates or address (set a precise center)")
    print("  [b]   Back to cluster list (leave unlabelled for now)")
    print("  [q]   Quit review session")

    try:
        choice = input("\nChoice: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "quit"

    if choice in ("q", "quit"):
        return "quit"
    elif choice in ("b", "back", "s", "skip", ""):
        return False

    selected_name = None
    final_lat = cl["center_lat"]
    final_lon = cl["center_lon"]
    recommended_radius = 150

    if choice.isdigit() and 1 <= int(choice) <= len(candidates):
        poi = candidates[int(choice) - 1]
        selected_name = poi["name"]
        final_lat = poi["lat"]
        final_lon = poi["lon"]
        recommended_radius = max(150, int(poi["dist_m"] + 60))
        print(f"\n📍 Using POI Center: {final_lat:.5f}, {final_lon:.5f} [{selected_name}]")
        print(f"   Parking offset: {poi['dist_m']}m ➔ Recommended Geofence Radius: {recommended_radius}m")

    elif choice == "g":
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

    elif choice == "c":
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
        selected_name = choice

    if not selected_name:
        print("⏩ Skipped (empty name).")
        return False

    try:
        r_input = input(f"Radius in meters [default {recommended_radius}m]: ").strip()
        radius_m = int(r_input) if r_input else recommended_radius
    except Exception:
        radius_m = recommended_radius

    keywords = suggest_keywords(clean_addr, selected_name)
    notes = f"{selected_name} ({clean_addr.split(',')[0].strip()})"

    places[selected_name] = {
        "lat": round(final_lat, 5),
        "lon": round(final_lon, 5),
        "radius_m": radius_m,
        "keywords": keywords,
        "notes": notes
    }
    synced = save_places(places)
    print(f"✔ Saved \"{selected_name}\" to places.json (Center: {final_lat:.5f}, {final_lon:.5f}, Radius: {radius_m}m)!")
    if synced:
        for s in synced:
            print(f"✔ Synced to {s}")
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
    chargers = {}
    for c_path in [CHARGING_JSON_PATH, SUPERCHARGERS_JSON_PATH]:
        if os.path.exists(c_path):
            try:
                with open(c_path, "r", encoding="utf-8") as f:
                    c_data = json.load(f)
                    if isinstance(c_data, dict):
                        for k, v in c_data.items():
                            if isinstance(v, dict) and "lat" in v and "lon" in v:
                                chargers[k] = v
            except Exception:
                pass

    print(f"🔍 Analyzing drive history from {len(csv_candidates)} file(s) (excluding chargers)...")
    
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
                        row.get("Ended At (AEST)") or row.get("Ended At") or
                        row.get("Ended") or row.get("end_time") or ""
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
        remaining_clusters = []
        for cl in valid_clusters:
            matched = False
            for p_name, p_data in places.items():
                p_lat, p_lon, p_rad = p_data.get("lat"), p_data.get("lon"), p_data.get("radius_m", 150)
                if p_lat is not None and p_lon is not None:
                    if haversine_distance(cl["center_lat"], cl["center_lon"], p_lat, p_lon) <= p_rad:
                        matched = True
                        break
            if not matched:
                remaining_clusters.append(cl)

        if not remaining_clusters:
            print("\n🎉 All drive stop clusters are now tagged or resolved!")
            break

        # Render cluster table
        w_num = 4
        w_visits = 10
        w_addr = 36
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
            clean_addr = cl["address"].split(",")[0].strip() if cl["address"] else "Unknown Address"
            if len(clean_addr) > w_addr:
                clean_addr = clean_addr[:w_addr-3] + "..."
            last_date = ""
            if cl["stops"]:
                last_date = cl["stops"][-1].get("timestamp", "")[:10]
            coords_str = f"{cl['center_lat']:.5f}, {cl['center_lon']:.5f}"

            row_cols = [
                pad_display(f"[{idx:2d}]", w_num, "right"),
                pad_display(f"{len(cl['stops']):2d} visits", w_visits, "right"),
                pad_display(clean_addr, w_addr),
                pad_display(coords_str, w_coords),
                pad_display(last_date, w_date)
            ]
            row_str = "  " + "  ".join(row_cols)
            print("│" + row_str + " " * max(0, total_w - display_len(row_str) - 2) + "│")

        print("└" + border[2:] + "┘\n")

        prompt_msg = f"Select Cluster to tag/edit [1-{len(remaining_clusters)}], [a]ll (step through sequentially), [q]uit: "
        try:
            choice = input(prompt_msg).strip().lower()
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
# CLI Command: Add / Update / Remove
# ---------------------------------------------------------------------------
def cmd_add(args):
    places = load_places()
    name = args.name.strip()
    
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
        "keywords": keywords,
        "notes": notes
    }

    synced = save_places(places)
    print(f"✔ Successfully added \"{name}\" to places.json!")
    if synced:
        for s in synced:
            print(f"✔ Synced to {s}")

def cmd_remove(args):
    places = load_places()
    name = args.name.strip()
    if name not in places:
        print(f"❌ Place \"{name}\" not found in places.json.")
        return
    del places[name]
    synced = save_places(places)
    print(f"✔ Removed \"{name}\" from places.json.")
    if synced:
        for s in synced:
            print(f"✔ Synced to {s}")

def cmd_rename(args):
    places = load_places()
    old_name = args.old_name.strip()
    new_name = args.new_name.strip()
    if old_name not in places:
        print(f"❌ Place \"{old_name}\" not found in places.json.")
        return
    if new_name in places and new_name != old_name:
        print(f"❌ Place \"{new_name}\" already exists in places.json.")
        return
    data = places.pop(old_name)
    places[new_name] = data
    synced = save_places(places)
    print(f"✔ Renamed \"{old_name}\" to \"{new_name}\".")
    if synced:
        for s in synced:
            print(f"✔ Synced to {s}")

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
        synced = save_places(places)
        print(f"✔ Successfully updated \"{target_key}\" in places.json.")
        if synced:
            for s in synced:
                print(f"✔ Synced to {s}")
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
    synced = save_places(places)
    print(f"✔ Aliased to \"{target_key}\": added {added_kws}")
    if synced:
        for s in synced:
            print(f"✔ Synced to {s}")

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
                    if new_n in places:
                        print(f"❌ Place \"{new_n}\" already exists.")
                    else:
                        places[new_n] = places.pop(curr_name)
                        curr_name = new_n
                        synced = save_places(places)
                        print(f"✔ Renamed to \"{curr_name}\"")
                        if synced:
                            for s in synced:
                                print(f"✔ Synced to {s}")
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
                            synced = save_places(places)
                            print(f"✔ Radius updated to {new_r}m")
                            if synced:
                                for s in synced:
                                    print(f"✔ Synced to {s}")
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
                        synced = save_places(places)
                        print(f"✔ Coordinates updated to: {data['lat']}, {data['lon']}")
                        if n_addr:
                            print(f"   Address: {n_addr}")
                        if synced:
                            for s in synced:
                                print(f"✔ Synced to {s}")
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
                        synced = save_places(places)
                        print(f"✔ Added keyword(s): {', '.join(added)}")
                        if synced:
                            for s in synced:
                                print(f"✔ Synced to {s}")
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
                rem_in = input(f"Select keyword number(s) to remove (e.g. 1, 3) or [b]ack: ").strip().lower()
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
                        synced = save_places(places)
                        print(f"✔ Removed keyword(s): {', '.join(removed)}")
                        if synced:
                            for s in synced:
                                print(f"✔ Synced to {s}")
            except (EOFError, KeyboardInterrupt):
                pass
        elif choice == "6":
            try:
                all_k_in = input(f"Enter all keywords (comma-separated) [current: {kws_str}]: ").strip()
                if all_k_in != "":
                    new_kws = [k.strip() for k in all_k_in.split(",") if k.strip()]
                    data["keywords"] = new_kws
                    places[curr_name] = data
                    synced = save_places(places)
                    print(f"✔ Keywords updated: {', '.join(new_kws)}")
                    if synced:
                        for s in synced:
                            print(f"✔ Synced to {s}")
            except (EOFError, KeyboardInterrupt):
                pass
        elif choice == "7":
            try:
                note_in = input(f"Enter new notes [current: \"{notes}\"]: ").strip()
                if note_in:
                    data["notes"] = note_in
                    places[curr_name] = data
                    synced = save_places(places)
                    print(f"✔ Notes updated to: \"{note_in}\"")
                    if synced:
                        for s in synced:
                            print(f"✔ Synced to {s}")
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
                poi_c = input(f"Select POI [1-{min(len(pois), 7)}] to adopt center & name, or [b]ack: ").strip().lower()
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
                    synced = save_places(places)
                    print(f"✔ Updated coordinates to ({data['lat']}, {data['lon']})")
                    if synced:
                        for s in synced:
                            print(f"✔ Synced to {s}")
            except (EOFError, KeyboardInterrupt):
                pass
        elif choice == "d":
            try:
                confirm = input(f"⚠️ Are you sure you want to permanently delete \"{curr_name}\"? [y/N]: ").strip().lower()
                if confirm == "y":
                    del places[curr_name]
                    synced = save_places(places)
                    print(f"✔ Deleted \"{curr_name}\" from places.json.")
                    if synced:
                        for s in synced:
                            print(f"✔ Synced to {s}")
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
                reset = input("Press [Enter] to clear filter, [a]dd new, [q]uit: ").strip().lower()
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

        prompt_str = f"Select Place to edit [1-{len(items)}], [s]earch <query>, [a]dd new, [q]uit: "
        try:
            choice = input(prompt_str).strip()
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
        description="Tesla / Tessie Location Management & POI Lookup Engine",
        formatter_class=argparse.RawTextHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Subcommand: list
    p_list = subparsers.add_parser("list", help="List all stored places in a clean formatted table")
    p_list.add_argument("--near", type=str, help="Reference place name or coordinates to sort by proximity")
    p_list.add_argument("--search", type=str, help="Search query filter for name, keywords, or notes")
    p_list.add_argument("--sort", choices=["name", "radius", "dist"], default="name", help="Sort order")

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
        args.near = None
        args.search = None
        args.sort = "name"
        cmd_list(args)
        return

    if args.command == "list":
        cmd_list(args)
    elif args.command == "edit":
        cmd_edit(args)
    elif args.command == "lookup":
        q = " ".join(args.query) if args.query else None
        interactive_lookup_and_add(query_or_addr=q, default_radius=args.radius)
    elif args.command == "review":
        cmd_review_drives(args)
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

