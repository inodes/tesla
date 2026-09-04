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
# Tesla Charger Explorer & Scraper Engine
# -----------------------------------------------------------------------------

class TeslaChargerExplorer:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.repo_root = os.path.dirname(self.script_dir)
        self.superchargers_path = os.path.join(self.repo_root, "Tessie", "superchargers.json")
        self.superchargers_archived_path = os.path.join(self.repo_root, "Tessie", "superchargers_archived.json")
        self.charging_path = os.path.join(self.repo_root, "Tessie", "charging.json")
        self.charging_archived_path = os.path.join(self.repo_root, "Tessie", "charging_archived.json")
        self.example_sc_path = os.path.join(self.repo_root, "Tessie", "superchargers.example.json")
        self.example_sc_archived_path = os.path.join(self.repo_root, "Tessie", "superchargers_archived.example.json")
        self.example_dc_path = os.path.join(self.repo_root, "Tessie", "charging.example.json")
        self.example_dc_archived_path = os.path.join(self.repo_root, "Tessie", "charging_archived.example.json")

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
        """Loads in-memory dictionaries of superchargers.json and charging.json."""
        sc_reg = {}
        if os.path.isfile(self.superchargers_path):
            try:
                with open(self.superchargers_path, "r", encoding="utf-8") as f:
                    sc_reg = json.load(f)
            except Exception:
                sc_reg = {}
        dc_reg = {}
        if os.path.isfile(self.charging_path):
            try:
                with open(self.charging_path, "r", encoding="utf-8") as f:
                    dc_reg = json.load(f)
            except Exception:
                dc_reg = {}
        return sc_reg, dc_reg

    def get_station_status(self, station: dict, sc_reg: dict = None, dc_reg: dict = None, threshold_days: int = 90) -> str:
        """
        Determines the registry status of a station:
        - 'UP_TO_DATE' (Green): Present in JSON registry and verified within threshold_days (<= 90 days / 3 months).
        - 'STALE' (Blue): Present in JSON registry but last verification is older than threshold_days (> 90 days).
        - 'NOT_IN_JSON' (Orange): Not present in JSON registry.
        """
        if sc_reg is None or dc_reg is None:
            sc_reg, dc_reg = self.load_active_registries()

        reg = sc_reg if station.get("type") == "supercharger" else dc_reg
        title = station.get("title", "")
        slug = str(station.get("slug", "")).lower()
        url = station.get("url", "")
        short_name = station.get("short_name", "")

        matched_entry = None
        # 1. Exact title key match
        if title in reg:
            matched_entry = reg[title]
        elif f"Tesla Supercharger - {title}" in reg:
            matched_entry = reg[f"Tesla Supercharger - {title}"]
        else:
            # 2. Match by slug / url / short_name
            for k, entry in reg.items():
                meta = entry.get("tesla_metadata", {})
                if short_name and meta.get("short_name") == short_name:
                    matched_entry = entry
                    break
                findus_url = meta.get("findus_url", "")
                if url and findus_url == url:
                    matched_entry = entry
                    break
                if slug and slug in findus_url.lower():
                    matched_entry = entry
                    break

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

    def scrape_all_stations(self, stations: list, sync_external: bool = False, force: bool = False, pacing_delay: float = 0.5, timeout_sec: int = 35, max_retries: int = 3):
        """Batch scrapes and updates all stations in the list with adaptive pacing, backoffs, and retries."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print(f"{C_RED}❌ Playwright is not installed in the active virtual environment.{C_RESET}")
            sys.exit(1)

        total = len(stations)
        if total == 0:
            print(f"{C_YELLOW}No stations to scrape.{C_RESET}")
            return

        print(f"\n{C_BOLD}{'='*80}{C_RESET}")
        print(f"  ⚡ {C_CYAN}{C_BOLD}STARTING BATCH SCRAPER FOR {total} STATIONS{C_RESET}")
        print(f"  {C_DIM}Pacing: {pacing_delay:.1f}s | Timeout: {timeout_sec}s | Max Retries: {max_retries}{C_RESET}")
        print(f"{C_BOLD}{'='*80}{C_RESET}\n")

        sc_reg, dc_reg = self.load_active_registries()
        success_count = 0
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

            for idx, st in enumerate(stations, 1):
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
                        self.update_registry(station_key, record, sync_external=False)
                        if st_type == "supercharger":
                            sc_reg[station_key] = record
                        else:
                            dc_reg[station_key] = record
                        success_count += 1
                        consecutive_failures = 0
                    else:
                        print(f"  {C_RED}❌ Failed parsing record for {st_title}{C_RESET}")
                        failed_stations.append(st_title)
                        consecutive_failures += 1
                except Exception as e:
                    print(f"  {C_RED}❌ Error scraping {st_title}: {e}{C_RESET}")
                    failed_stations.append(st_title)
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
        print(f"\n{C_BOLD}{'='*80}{C_RESET}")
        print(f"  {C_GREEN}✔ Batch scraping completed in {elapsed:.1f}s:{C_RESET} {success_count}/{total} processed ({len(failed_stations)} failed).")
        if failed_stations:
            print(f"  {C_RED}Failed stations:{C_RESET} {', '.join(failed_stations[:10])}{' ...' if len(failed_stations) > 10 else ''}")
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

    def display_preview(self, station_key: str, data: dict):
        """Renders an attractive terminal summary card for a scraped station."""
        meta = data.get("tesla_metadata", {})
        loc = data.get("location", {})
        hw = data.get("hardware", {})
        comp = data.get("compatibility", {})
        tariffs = data.get("tariffs", {})
        tz_name = resolve_location_timezone(state=loc.get("state"), country=loc.get("country"), lat=loc.get("lat"), lon=loc.get("lon"))

        box_width = 86
        title_content = f"LIVE SCRAPED CHARGER: {station_key}"
        if len(title_content) > box_width - 4:
            title_content = title_content[:box_width - 7] + "..."
        pad_total = box_width - 2 - len(title_content)
        pad_l = pad_total // 2
        pad_r = pad_total - pad_l
        header_line = f"║{' ' * pad_l}{title_content}{' ' * pad_r}║"

        print(f"\n╔{'═' * (box_width - 2)}╗")
        print(header_line)
        print(f"╚{'═' * (box_width - 2)}╝\n")

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

    def update_registry(self, station_key: str, data: dict, sync_external: bool = False):
        """Saves scraped station data into superchargers.json or charging.json, archives older versions if changed, and syncs to external drives."""
        is_sc = data.get("tesla_metadata", {}).get("type") == "supercharger"
        target_fpath = self.superchargers_path if is_sc else self.charging_path
        archive_fpath = self.superchargers_archived_path if is_sc else self.charging_archived_path
        example_fpath = self.example_sc_path if is_sc else self.example_dc_path
        example_arch_fpath = self.example_sc_archived_path if is_sc else self.example_dc_archived_path
        reg_name = "superchargers.json" if is_sc else "charging.json"
        arch_name = "superchargers_archived.json" if is_sc else "charging_archived.json"
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

        if existing_entry:
            orig_first_seen = existing_entry.get("first_seen") or existing_entry.get("created_at") or now_utc
            new_entry["first_seen"] = orig_first_seen

            if self._detect_record_changes(existing_entry, new_entry):
                changes_detected = True
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
                new_entry["last_updated"] = existing_entry.get("last_updated", orig_first_seen)
                new_entry["valid_from"] = existing_entry.get("valid_from", orig_first_seen)
                new_entry["last_verified"] = now_utc
        else:
            new_entry["first_seen"] = now_utc
            new_entry["last_updated"] = now_utc
            new_entry["last_verified"] = now_utc
            new_entry["valid_from"] = now_utc

        active_reg[station_key] = new_entry

        # Write active registry
        for fpath in [target_fpath, example_fpath]:
            if os.path.isfile(fpath) or fpath == target_fpath:
                try:
                    with open(fpath, "w", encoding="utf-8") as f:
                        json.dump(active_reg, f, indent=2, ensure_ascii=False)
                    print(f"  {C_GREEN}✔ Saved active registry entry in:{C_RESET} {fpath}")
                except Exception as e:
                    print(f"  {C_RED}❌ Failed writing {fpath}:{C_RESET} {e}")

        # Write archive registry if changes occurred
        if changes_detected:
            for fpath in [archive_fpath, example_arch_fpath]:
                try:
                    with open(fpath, "w", encoding="utf-8") as f:
                        json.dump(archive_reg, f, indent=2, ensure_ascii=False)
                    print(f"  {C_GREEN}✔ Updated historical archive in:{C_RESET} {fpath}")
                except Exception as e:
                    print(f"  {C_RED}❌ Failed writing archive {fpath}:{C_RESET} {e}")

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

    # Display Stations Grouped by State
    print(f"\n{C_BOLD}{'='*80}{C_RESET}")
    print(f"  ⚡ {C_BOLD}{selected_country.upper()} CHARGING STATIONS ({len(all_stations)} total){C_RESET}")
    print(f"  {C_BOLD}📊 Status Legend:{C_RESET} [{C_GREEN} 1 {C_RESET}] Up to Date (<=3mo)  |  [{C_BLUE} 2 {C_RESET}] Stale (>3mo)  |  [{C_ORANGE} 3 {C_RESET}] Not in JSON")
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
    print(f"  • Enter a station number [1-{len(all_stations)}] to inspect and scrape live pricing")
    print(f"  • Type {C_GREEN}'all'{C_RESET} to batch scrape and update all {len(all_stations)} stations")
    print(f"  • Press {C_YELLOW}Enter{C_RESET} or Ctrl+C to exit")
    print()

    try:
        pick = input("Select station number or 'all': ").strip()
        if not pick:
            return
        if pick.lower() in ["all", "a"]:
            save_prompt = input(f"Scrape and save all {len(all_stations)} stations into registry? [y/N]: ").strip().lower()
            if save_prompt == "y":
                explorer.scrape_all_stations(all_stations, sync_external=True)
            return
        if pick.isdigit() and int(pick) in station_lookup:
            target_st = station_lookup[int(pick)]
            key, record = explorer.scrape_station_details(target_st["url"], charger_type=target_st["type"])
            if record:
                explorer.display_preview(key, record)
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
⚡ Tesla Charger Discovery, Exploration & Live Scraper Engine ⚡
================================================================
Explore, list, search, and live-scrape technical hardware specs, Time-of-Use (TOU)
pricing, and accessibility across Tesla Superchargers and Destination Chargers.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 1. Interactive terminal drill-down (Region ➔ Country ➔ State ➔ Station):
  ./Tools/find_tesla_chargers.py

  # 2. List all Superchargers in Australia grouped by state:
  ./Tools/find_tesla_chargers.py --country Australia --sc --list

  # 3. Filter NSW Superchargers:
  ./Tools/find_tesla_chargers.py --country Australia --state NSW --sc --list

  # 4. Search by keyword/suburb:
  ./Tools/find_tesla_chargers.py --country Australia --search "Miranda"

  # 5. Scrape specific Supercharger by Location ID or Find Us URL:
  ./Tools/find_tesla_chargers.py --scrape 19258
  ./Tools/find_tesla_chargers.py --url 'https://www.tesla.com/en_AU/findus/location/supercharger/19258'

  # 6. Scrape, save to superchargers.json, and sync across external TESLADRIVE volumes:
  ./Tools/find_tesla_chargers.py --scrape 19258 --update --sync

  # 7. Batch scrape and populate all Australian Superchargers:
  ./Tools/find_tesla_chargers.py --country Australia --sc --all --sync
"""
    )
    
    # Discovery & Filtering Flags
    parser.add_argument("--region", help="Geographic Region (e.g. 'Asia/Pacific', 'North America', 'Europe')")
    parser.add_argument("--country", default="Australia", help="Country name (e.g. 'Australia', 'Hong Kong', 'Japan', 'United States')")
    parser.add_argument("--state", help="State / Territory code (e.g. 'NSW', 'VIC', 'QLD', 'WA', 'SA', 'TAS', 'ACT')")
    parser.add_argument("--suburb", help="Filter by suburb / city name")
    parser.add_argument("-q", "--search", "--query", help="Search term across station names and addresses")
    parser.add_argument("--sc", "--superchargers", action="store_true", help="Filter Superchargers only")
    parser.add_argument("--dc", "--destination-chargers", action="store_true", help="Filter Destination Chargers only")
    parser.add_argument("--list", action="store_true", help="List matching charging stations")
    
    # Scraping & Persistence Flags
    parser.add_argument("--scrape", "--inspect", help="Scrape live station details by Location ID, Slug, or URL")
    parser.add_argument("--url", help="Direct Tesla Find Us location URL to scrape")
    parser.add_argument("--all", action="store_true", help="Batch scrape and update all matching stations")
    parser.add_argument("--delay", type=float, default=0.5, help="Pacing delay in seconds between station requests in batch mode (default: 0.5)")
    parser.add_argument("--timeout", type=int, default=35, help="Network timeout in seconds per page load (default: 35)")
    parser.add_argument("--retries", type=int, default=3, help="Max retry attempts per station with exponential backoff (default: 3)")
    parser.add_argument("--save", "--update", action="store_true", help="Save / update scraped entry in superchargers.json or charging.json")
    parser.add_argument("--sync", action="store_true", help="Sync updated registry across all mounted TESLADRIVE external volumes")
    parser.add_argument("--json", action="store_true", help="Output raw JSON payload to stdout")
    parser.add_argument("--headful", "--visible", action="store_true", help="Run browser in visible mode (default is headless)")

    args = parser.parse_args()
    explorer = TeslaChargerExplorer(headless=not args.headful)

    # 1. Direct Scrape by URL or ID
    target_scrape = args.scrape or args.url
    if target_scrape:
        target_type = "destination_charger" if args.dc else "supercharger"
        station_key, data = explorer.scrape_station_details(target_scrape, charger_type=target_type)
        if not data:
            sys.exit(1)
        if args.json:
            print(json.dumps({station_key: data}, indent=2, ensure_ascii=False))
            return
        explorer.display_preview(station_key, data)
        if args.save:
            explorer.update_registry(station_key, data, sync_external=args.sync)
        else:
            print(f"{C_DIM}Run with '--save' or '--update' to write changes into registry.{C_RESET}\n")
        return

    # 2. Command-Line Listing / Search / Batch Scrape
    if args.list or args.search or args.state or args.suburb or args.sc or args.dc or args.all:
        charger_types = []
        if args.sc and not args.dc:
            charger_types = ["superchargers"]
        elif args.dc and not args.sc:
            charger_types = ["chargers"]
        else:
            charger_types = ["superchargers", "chargers"]

        all_stations = []
        for c_type in charger_types:
            st_list = explorer.fetch_station_list(country=args.country, charger_type=c_type)
            all_stations.extend(st_list)

        # Apply Filters
        filtered = all_stations
        if args.state:
            target_st = args.state.upper()
            filtered = [s for s in filtered if s["state"] == target_st]

        if args.suburb:
            sub_lower = args.suburb.lower()
            filtered = [s for s in filtered if sub_lower in s["title"].lower()]

        if args.search:
            q_lower = args.search.lower()
            filtered = [s for s in filtered if q_lower in s["title"].lower() or q_lower in s["short_name"].lower()]

        if args.all:
            explorer.scrape_all_stations(
                filtered,
                sync_external=args.sync,
                pacing_delay=args.delay,
                timeout_sec=args.timeout,
                max_retries=args.retries
            )
            return

        sc_reg, dc_reg = explorer.load_active_registries()
        max_title_len = max((display_len(s["title"]) for s in filtered), default=28)
        name_col_width = max(max_title_len + 2, 30)

        print(f"\n{C_BOLD}{'='*90}{C_RESET}")
        print(f"  ⚡ {C_BOLD}MATCHING CHARGING STATIONS ({len(filtered)} found){C_RESET}")
        print(f"  {C_BOLD}📊 Status Legend:{C_RESET} [{C_GREEN} 1 {C_RESET}] Up to Date (<=3mo)  |  [{C_BLUE} 2 {C_RESET}] Stale (>3mo)  |  [{C_ORANGE} 3 {C_RESET}] Not in JSON")
        print(f"{C_BOLD}{'='*90}{C_RESET}\n")

        print(f"  {'#':>5}  {'Type':<12} {'State':<5} {pad_display('Station Name', name_col_width)} {'Location ID / URL'}")
        print(f"  {'-'*5}  {'-'*12} {'-'*5} {'-'*name_col_width} {'-'*24}")

        for idx, s in enumerate(filtered, 1):
            status = explorer.get_station_status(s, sc_reg=sc_reg, dc_reg=dc_reg)
            color = C_GREEN if status == "UP_TO_DATE" else (C_BLUE if status == "STALE" else C_ORANGE)
            num_str = f"[{color}{idx:3d}{C_RESET}]"
            t_label = "Supercharger" if s["type"] == "supercharger" else "Destination"
            t_icon = "🔴" if s["type"] == "supercharger" else "🔌"
            print(f"  {num_str}  {t_icon} {t_label:<9} {s['state']:<5} {pad_display(s['title'], name_col_width)} {C_DIM}{s['url']}{C_RESET}")

        print(f"\n{C_DIM}To scrape details: ./Tools/find_tesla_chargers.py --scrape <ID_or_URL> [--save] [--sync]{C_RESET}")
        print(f"{C_DIM}To batch scrape all: ./Tools/find_tesla_chargers.py {'--sc ' if args.sc else ''}{f'--state {args.state} ' if args.state else ''}--all [--sync]{C_RESET}\n")
        return

    # 3. Interactive Mode Fallback
    if sys.stdin.isatty():
        interactive_drilldown(explorer)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
