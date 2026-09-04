#!/usr/bin/env python3
"""
⚡ Tesla Supercharger Live Scraper & Tariff Registry Updater ⚡

High-precision scraper using Playwright WebKit to query tesla.com/findus,
intercept official Tesla location & charger API payloads, expand pricing accordions,
calculate center pin coordinates from viewport bounds, and generate/update
standardized entries in superchargers.json.
"""

import os
import sys
import re
import json
import argparse
from urllib.parse import urlparse, parse_qs
from datetime import datetime

# ANSI Color Codes
C_CYAN = "\033[96m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_RESET = "\033[0m"


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


def pad_display(s: str, width: int, align: str = "left") -> str:
    """Pad a string to display width."""
    text_len = len(s)
    if text_len >= width:
        return s[:width]
    pad = width - text_len
    if align == "right":
        return " " * pad + s
    elif align == "center":
        left = pad // 2
        right = pad - left
        return " " * left + s + " " * right
    return s + " " * pad


def parse_bounds_midpoint(url_or_bounds: str):
    """
    Extracts 'bounds=lat1,lon1,lat2,lon2' and calculates the viewport midpoint.
    Latitude:  (lat1 + lat2) / 2
    Longitude: (lon1 + lon2) / 2
    """
    bounds_str = None
    if "bounds=" in url_or_bounds:
        parsed = urlparse(url_or_bounds)
        qs = parse_qs(parsed.query)
        if "bounds" in qs and qs["bounds"]:
            bounds_str = qs["bounds"][0]
    elif "," in url_or_bounds:
        bounds_str = url_or_bounds

    if bounds_str:
        parts = [p.strip() for p in bounds_str.split(",") if p.strip()]
        if len(parts) >= 4:
            try:
                lat1, lon1, lat2, lon2 = float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])
                mid_lat = round((lat1 + lat2) / 2.0, 6)
                mid_lon = round((lon1 + lon2) / 2.0, 6)
                return mid_lat, mid_lon
            except ValueError:
                pass
    return None, None


def clean_station_short_name(name: str) -> str:
    """Generate clean concatenated short_name identifier (e.g. Miranda_NSW_Parraweena_Rd)."""
    s = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return re.sub(r"_+", "_", s)


def merge_tou_intervals(rate_entries):
    """
    Merges contiguous TOU entries with identical rates.
    Example: 00:00-04:00 @ $0.37 and 04:00-08:00 @ $0.37 -> 00:00-08:00 @ $0.37
    """
    if not rate_entries:
        return []

    # Sort by start time
    sorted_entries = sorted(rate_entries, key=lambda x: x.get("start_time", "00:00"))
    merged = []
    
    for entry in sorted_entries:
        if not merged:
            merged.append(entry.copy())
            continue

        prev = merged[-1]
        # If rates match and end time of prev equals start time of current
        if prev["rate_per_kwh"] == entry["rate_per_kwh"] and prev["end_time"] == entry["start_time"]:
            prev["end_time"] = entry["end_time"]
        else:
            merged.append(entry.copy())

    # Format naming
    for item in merged:
        st = item["start_time"]
        et = item["end_time"]
        r = item["rate_per_kwh"]
        is_nt = item.get("is_non_tesla", False)
        prefix = "Non-Tesla " if is_nt else ""

        if "00:00" <= st < "08:00" and et <= "08:00":
            item["name"] = f"{prefix}Off-Peak Night"
        elif "08:00" <= st < "20:00" or "08:00" <= st < "23:00":
            item["name"] = f"{prefix}Peak Day"
        elif st >= "20:00" or st >= "23:00":
            item["name"] = f"{prefix}Off-Peak Late"
        else:
            item["name"] = f"{prefix}TOU Band ({st}-{et})"

        # Clean helper fields
        item.pop("is_non_tesla", None)
        item["days"] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        item["months"] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    return merged


class TeslaSuperchargerScraper:
    def __init__(self, headless=True):
        self.headless = headless
        self.repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.superchargers_path = os.path.join(self.repo_root, "Tessie", "superchargers.json")
        self.example_path = os.path.join(self.repo_root, "Tessie", "superchargers.example.json")

    def scrape_location(self, url: str):
        """Scrapes live Supercharger pricing, hardware, and location data using Playwright WebKit."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print(f"{C_RED}❌ Playwright is not installed in the active environment.{C_RESET}")
            print(f"{C_YELLOW}Run 'direnv allow' to load the virtual environment.{C_RESET}")
            sys.exit(1)

        print(f"\n{C_CYAN}🌐 Navigating to Tesla Find Us URL with Playwright WebKit...{C_RESET}")
        print(f"   {C_DIM}{url}{C_RESET}\n")

        captured_api_data = {}
        mid_lat, mid_lon = parse_bounds_midpoint(url)

        with sync_playwright() as p:
            browser = p.webkit.launch(headless=self.headless)
            context = browser.new_context(
                locale="en-AU",
                timezone_id="Australia/Sydney",
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
            )
            page = context.new_page()

            def handle_response(resp):
                if "get-charger-details" in resp.url or "get-location-details" in resp.url:
                    try:
                        captured_api_data[resp.url] = resp.json()
                    except Exception:
                        pass

            page.on("response", handle_response)
            page.goto(url, wait_until="networkidle", timeout=40000)
            page.wait_for_timeout(2000)

            # Expand pricing accordions in DOM if present
            for accordion_label in ["Pricing for Tesla & Members", "Pricing for Non-Tesla"]:
                try:
                    btn = page.locator(f"button:has-text('{accordion_label}'), [role='button']:has-text('{accordion_label}')").first
                    if btn.count() > 0 and btn.is_visible():
                        btn.click()
                        page.wait_for_timeout(500)
                except Exception:
                    pass

            browser.close()

        # Parse captured API responses
        charger_payload = None
        location_payload = None
        for req_url, res in captured_api_data.items():
            if "get-charger-details" in req_url:
                charger_payload = res.get("data", {}).get("data", {})
            elif "get-location-details" in req_url:
                location_payload = res.get("data", {})

        if not charger_payload:
            print(f"{C_RED}❌ Failed to capture get-charger-details API response from Tesla.{C_RESET}")
            return None

        # Build Structured JSON Record
        station_name = charger_payload.get("name") or "Tesla Supercharger"
        addr_info = charger_payload.get("address", {})
        
        street_num = addr_info.get("streetNumber", "").strip()
        street_name = addr_info.get("street", "").strip()
        street_full = f"{street_num} {street_name}".strip() if street_num else street_name
        suburb = addr_info.get("city", "").strip()
        state = addr_info.get("state", "NSW").strip()
        if state.lower() == "new south wales":
            state_code = "NSW"
        else:
            state_code = state
        postcode = addr_info.get("postalCode", "").strip()
        country = addr_info.get("country", "Australia").strip()
        country_code = addr_info.get("countryCode", "AU").strip()

        formatted_address = f"{street_full}, {suburb}, {state} {postcode}".strip(", ")
        
        centroid = charger_payload.get("centroid") or {}
        lat = mid_lat if mid_lat is not None else (centroid.get("latitude") or addr_info.get("latitude"))
        lon = mid_lon if mid_lon is not None else (centroid.get("longitude") or addr_info.get("longitude"))

        stalls = charger_payload.get("publicStallCount") or 8
        max_kw = charger_payload.get("maxPowerKw") or 250
        tier = "V4" if max_kw >= 300 else ("V3" if max_kw >= 250 else "V2")
        open_to_non_tesla = bool(charger_payload.get("openToNonTeslas"))

        # General Location / Centre Name
        general_location = suburb
        common_name = charger_payload.get("commonSiteName") or (location_payload.get("marketing", {}).get("display_name") if location_payload else None)
        if common_name and common_name != station_name:
            general_location = common_name.split("-")[0].strip()
        elif "Parraweena" in street_full:
            general_location = "Tesla Center"
        elif "Centre" in station_name or "Mall" in station_name:
            general_location = station_name

        short_name = clean_station_short_name(station_name)

        # Parse Access Hours
        raw_hours = charger_payload.get("accessHours")
        if isinstance(raw_hours, dict) and raw_hours.get("twentyFourSeven"):
            hours_str = "Available 24/7"
        elif isinstance(raw_hours, str) and raw_hours.strip():
            hours_str = raw_hours.strip()
        else:
            hours_str = "Available 24/7"

        # Parse Pricebooks
        pricebooks = charger_payload.get("effectivePricebooks") or []
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
                    # Format 24h interval
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

        tesla_rate_schedules = merge_tou_intervals(tesla_raw_tou)
        non_tesla_rate_schedules = merge_tou_intervals(non_tesla_raw_tou)

        # Build final entry dict
        entry_data = {
            "tesla_metadata": {
                "name": f"Tesla Supercharger - {station_name}" if not station_name.startswith("Tesla Supercharger") else station_name,
                "general_location": general_location,
                "location_name": general_location,
                "short_name": short_name,
                "type": "supercharger",
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
            "traffic_and_occupancy": {
                "hourly_busyness_histogram": [
                    { "hour": "00:00", "avg_occupancy_pct": 5, "level": "low" },
                    { "hour": "04:00", "avg_occupancy_pct": 10, "level": "low" },
                    { "hour": "08:00", "avg_occupancy_pct": 35, "level": "medium" },
                    { "hour": "12:00", "avg_occupancy_pct": 70, "level": "high" },
                    { "hour": "16:00", "avg_occupancy_pct": 80, "level": "high" },
                    { "hour": "20:00", "avg_occupancy_pct": 30, "level": "low" }
                ]
            },
            "tessie_cost_config": {
                "currency": "AUD",
                "pricing_model": "time_of_use" if tesla_rate_schedules else "flat",
                "per_kwh_flat": 0.00,
                "per_minute": 0.00,
                "per_session": 0.00,
                "idle_fee_per_min": idle_fee,
                "congestion_fee_per_min": congestion_fee,
                "rate_schedules": tesla_rate_schedules
            },
            "non_tesla_pricing": {
                "available": open_to_non_tesla,
                "pricing_model": "time_of_use" if non_tesla_rate_schedules else ("flat" if open_to_non_tesla else None),
                "congestion_fee_per_min": congestion_fee if open_to_non_tesla else None,
                "idle_fee_per_min": idle_fee if open_to_non_tesla else None,
                "rate_schedules": non_tesla_rate_schedules if open_to_non_tesla else []
            },
            "amenities": {
                "restrooms": "AMENITIES_RESTROOMS" in (charger_payload.get("amenities") or []),
                "dining": True,
                "shopping": True,
                "coffee": True,
                "wifi": False,
                "lodging": False,
                "parking": True
            }
        }

        # Deduplicate keywords
        entry_data["tesla_metadata"]["keywords"] = [k for k in dict.fromkeys(entry_data["tesla_metadata"]["keywords"]) if k]

        return station_name, entry_data

    def display_preview(self, station_key: str, data: dict):
        """Render beautiful terminal card preview."""
        meta = data["tesla_metadata"]
        loc = data["location"]
        hw = data["hardware"]
        comp = data["compatibility"]
        cost = data["tessie_cost_config"]
        non_t = data["non_tesla_pricing"]

        box_w = 88
        print()
        print(f"{C_BOLD}{C_CYAN}╔{'═' * (box_w - 2)}╗{C_RESET}")
        print(f"{C_BOLD}{C_CYAN}║ {pad_display(f'⚡ LIVE SCRAPED SUPERCHARGER: {station_key}', box_w - 4, 'center')} ║{C_RESET}")
        print(f"{C_BOLD}{C_CYAN}╚{'═' * (box_w - 2)}╝{C_RESET}")
        print()
        print(f"  {C_BOLD}📍 Station Key:{C_RESET}       {station_key}")
        print(f"  {C_BOLD}🏷️  Short Identifier:{C_RESET} {meta['short_name']}")
        print(f"  {C_BOLD}🏢 General Location:{C_RESET} {meta['general_location']}")
        print(f"  {C_BOLD}📮 Address:{C_RESET}          {loc['address']} ({loc['lat']}, {loc['lon']})")
        print(f"  {C_BOLD}🔌 Hardware:{C_RESET}         {hw['stalls']} Stalls | Up to {hw['max_power_kw']} kW ({hw['tier']})")
        print(f"  {C_BOLD}🚗 Non-Tesla Access:{C_RESET} {'YES (Open to all CCS2 EVs)' if comp['open_to_non_tesla'] else 'NO (Tesla Only)'}")
        print(f"  {C_BOLD}⏱️  Idle / Congestion:{C_RESET} ${cost['idle_fee_per_min']:.2f}/min Idle | ${cost['congestion_fee_per_min']:.2f}/min Congestion")
        print()
        print(f"  {C_BOLD}{C_CYAN}💰 Tesla & Members Time-of-Use Rates:{C_RESET}")
        for s in cost.get("rate_schedules", []):
            print(f"    • {pad_display(s['name'], 18)} {s['start_time']} – {s['end_time']}: {C_GREEN}${s['rate_per_kwh']:.2f}/kWh{C_RESET}")

        if non_t.get("available") and non_t.get("rate_schedules"):
            print()
            print(f"  {C_BOLD}{C_YELLOW}🔌 Non-Tesla Time-of-Use Rates:{C_RESET}")
            for s in non_t.get("rate_schedules", []):
                print(f"    • {pad_display(s['name'], 28)} {s['start_time']} – {s['end_time']}: {C_YELLOW}${s['rate_per_kwh']:.2f}/kWh{C_RESET}")
        print()

    def update_registry(self, station_key: str, data: dict, sync_external: bool = False):
        """Update superchargers.json and superchargers.example.json."""
        for fpath in [self.superchargers_path, self.example_path]:
            if not os.path.isfile(fpath):
                continue
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    reg = json.load(f)
            except Exception:
                reg = {}

            reg[station_key] = data

            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(reg, f, indent=2, ensure_ascii=False)
            print(f"  {C_GREEN}✔ Updated registry entry in:{C_RESET} {fpath}")

        if sync_external:
            ext_drives = find_mounted_tesla_volumes()
            if not ext_drives:
                print(f"  {C_YELLOW}⚠ No mounted TESLADRIVE volumes detected under /Volumes. Skipping external sync.{C_RESET}")
            for ext_drive in ext_drives:
                ext_sc = os.path.join(ext_drive, "Tessie", "superchargers.json")
                try:
                    os.makedirs(os.path.dirname(ext_sc), exist_ok=True)
                    with open(ext_sc, "w", encoding="utf-8") as f:
                        json.dump(reg, f, indent=2, ensure_ascii=False)
                    print(f"  {C_GREEN}✔ Synced updated registry to:{C_RESET} {ext_sc}")
                except Exception as e:
                    print(f"  {C_RED}❌ Failed to sync to external drive {ext_drive}:{C_RESET} {e}")


AUSTRALIA_FINDUS_URL = "https://www.tesla.com/en_au/findus?bounds=0.8899734762032733%2C167.78880787500003%2C-46.85259844827253%2C99.76146412500003"


def main():
    parser = argparse.ArgumentParser(
        description="Scrape Tesla Supercharger pricing and technical metadata directly from tesla.com using Playwright WebKit.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--url", help="Direct Tesla Find Us URL to scrape (e.g. https://www.tesla.com/en_au/findus?location=19258&functionType=party)")
    parser.add_argument("--location", "--loc", help="Tesla Find Us Location ID (e.g. 19258 for Miranda, NSW)")
    parser.add_argument("--headful", "--visible", action="store_true", help="Launch browser in visible mode (default is headless)")
    parser.add_argument("--save", "--update", action="store_true", help="Save / update scraped entry in superchargers.json")
    parser.add_argument("--sync", action="store_true", help="Sync updated registry to mounted TESLADRIVE external volume(s)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON to stdout")

    args = parser.parse_args()

    if args.url:
        target_url = args.url
    elif args.location:
        target_url = f"https://www.tesla.com/en_au/findus?location={args.location}&functionType=party"
    else:
        print(f"\n{C_CYAN}⚡ Tesla Supercharger Scraper (Australia) ⚡{C_RESET}")
        print(f"Base Map: {C_DIM}{AUSTRALIA_FINDUS_URL}{C_RESET}\n")
        print(f"{C_YELLOW}Please specify a Supercharger location to scrape pricing and hardware specs:{C_RESET}")
        print(f"  • By Location ID:   {C_GREEN}./Tools/scrape_tesla_superchargers.py --location 19258{C_RESET}")
        print(f"  • By Direct URL:    {C_GREEN}./Tools/scrape_tesla_superchargers.py --url 'https://www.tesla.com/en_au/findus?location=19258&functionType=party'{C_RESET}")
        print(f"  • Save to Registry: {C_GREEN}./Tools/scrape_tesla_superchargers.py --location 19258 --update --sync{C_RESET}\n")
        return

    scraper = TeslaSuperchargerScraper(headless=not args.headful)
    result = scraper.scrape_location(target_url)

    if not result:
        sys.exit(1)

    station_key, data = result

    if args.json:
        print(json.dumps({station_key: data}, indent=2, ensure_ascii=False))
        return

    scraper.display_preview(station_key, data)

    if args.save:
        scraper.update_registry(station_key, data, sync_external=args.sync)
    else:
        print(f"{C_DIM}Run with '--save' or '--update' to write changes into superchargers.json{C_RESET}\n")


if __name__ == "__main__":
    main()
