#!/usr/bin/env python3
"""
sync_fuel_history.py - Synchronize & Manage NSW Historical Fuel Price Data
==========================================================================
Discovers and downloads monthly historical fuel price archives from Data.NSW CKAN
(August 2016 – present), parses station records, and maintains an indexed SQLite
database with daily and monthly regional averages for EV vs ICE cost reconciliation.

Usage:
  ./sync_fuel_history.py --status
  ./sync_fuel_history.py --sync-all
  ./sync_fuel_history.py --sync-month 2026-08
  ./sync_fuel_history.py --query --date 2026-08-15 --fuel-type U91
  ./sync_fuel_history.py --recompute-aggregates
"""

import argparse
import datetime
import os
import sys
import time
import urllib.request
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = SCRIPT_DIR
REPO_ROOT = os.path.dirname(TOOLS_DIR)

if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from lib.fuel_common import (
        DEFAULT_USER_AGENT,
        COMPARISON_VEHICLES,
        fetch_ckan_resources,
        get_db_connection,
        get_fuel_price,
        get_sync_status_summary,
        ingest_month_data,
        parse_xlsx_stream,
        parse_csv_stream,
        resolve_fuel_dir,
        resolve_fuel_db_path,
        calculate_ice_comparison_costs,
    )
    from lib.tessie_api_common import resolve_tessie_data_home
    from lib.table_formatter import pad_display, display_len
except ImportError:
    from Tools.lib.fuel_common import (
        DEFAULT_USER_AGENT,
        COMPARISON_VEHICLES,
        fetch_ckan_resources,
        get_db_connection,
        get_fuel_price,
        get_sync_status_summary,
        ingest_month_data,
        parse_xlsx_stream,
        parse_csv_stream,
        resolve_fuel_dir,
        resolve_fuel_db_path,
        calculate_ice_comparison_costs,
    )
    from Tools.lib.tessie_api_common import resolve_tessie_data_home
    from Tools.lib.table_formatter import pad_display, display_len


def print_banner(title: str, min_width: int = 80):
    content_w = display_len(title)
    inner_w = max(min_width - 4, content_w)
    total_w = inner_w + 4
    print("┌" + "─" * (total_w - 2) + "┐")
    print("│ " + pad_display(title, inner_w) + " │")
    print("└" + "─" * (total_w - 2) + "┘")


def print_status(tessie_dir: Optional[str] = None):
    summary = get_sync_status_summary(tessie_dir)
    fuel_dir = resolve_fuel_dir(tessie_dir)
    raw_dir = os.path.join(fuel_dir, "raw")
    raw_files = [f for f in os.listdir(raw_dir) if f.endswith((".xlsx", ".csv"))] if os.path.isdir(raw_dir) else []

 # Sample current prices
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    u91_p = get_fuel_price(today_str, "U91", "all", tessie_dir)
    p98_p = get_fuel_price(today_str, "P98", "all", tessie_dir)
    dl_p = get_fuel_price(today_str, "DL", "all", tessie_dir)

    title = "⛽ NSW FUELCHECK HISTORICAL PRICING STATUS"
    section1 = [
        f"📁 Fuel Data Home:    {fuel_dir}",
        f"💾 SQLite Database:   {summary['db_path']} ({summary['db_size_mb']} MB)",
        f"📦 Synced Months:     {summary['synced_months_count']} months archived ({len(raw_files)} raw files saved)",
        f"🏪 Station Registry:  {summary['station_count']:,} unique service stations indexed",
        f"📅 Historical Range:  {summary['date_range']} ({summary['daily_records']:,} daily averages)",
    ]
    section2 = [
        f"📊 Latest Reference Prices:  U91: ${u91_p:.2f}/L  |  P98: ${p98_p:.2f}/L  |  Diesel: ${dl_p:.2f}/L"
    ]

    all_lines = [title] + section1 + section2
    max_w = max(display_len(line) for line in all_lines)
    inner_w = max(80, max_w)
    total_w = inner_w + 4

    print("┌" + "─" * (total_w - 2) + "┐")
    print("│ " + pad_display(title, inner_w) + " │")
    print("├" + "─" * (total_w - 2) + "┤")
    for line in section1:
        print("│ " + pad_display(line, inner_w) + " │")
    print("├" + "─" * (total_w - 2) + "┤")
    for line in section2:
        print("│ " + pad_display(line, inner_w) + " │")
    print("└" + "─" * (total_w - 2) + "┘")



def sync_resources(
    target_month: Optional[str] = None,
    limit: Optional[int] = None,
    force: bool = False,
    pace: float = 0.5,
    tessie_dir: Optional[str] = None
):
    print("🔍 Discovering monthly datasets on Data.NSW CKAN...")
    try:
        all_resources = fetch_ckan_resources()
    except Exception as e:
        print(f"❌ Error querying Data.NSW CKAN: {e}")
        return

    print(f"📋 Found {len(all_resources)} total monthly historical packages on Data.NSW.")
    conn = get_db_connection(tessie_dir)
    fuel_dir = resolve_fuel_dir(tessie_dir)
    raw_dir = os.path.join(fuel_dir, "raw")

 # Check synced resources in DB
    cur = conn.cursor()
    cur.execute("SELECT month_year FROM fuel_sync_log WHERE status = 'synced'")
    synced_set = {row[0] for row in cur.fetchall()}

    to_process = []
    for r in all_resources:
        ym = r["month_year"]
        if target_month and ym != target_month:
            continue
        if ym in synced_set and not force:
            continue
        to_process.append(r)

    if not to_process:
        print("✅ All historical months are already up to date!")
        print_status(tessie_dir)
        return

    if limit and len(to_process) > limit:
        to_process = to_process[:limit]

    print(f"📥 Processing {len(to_process)} monthly datasets...")

    for idx, res in enumerate(to_process, 1):
        ym = res["month_year"]
        fmt = res["format"].lower()
        filename = f"fuelcheck_{ym}.{fmt}"
        raw_filepath = os.path.join(raw_dir, filename)

        print(f"[{idx}/{len(to_process)}] ⏳ Syncing {ym} ({res['title']})...", end="", flush=True)

        data_bytes = None
 # Check if already cached on disk
        if os.path.isfile(raw_filepath) and os.path.getsize(raw_filepath) > 1024 and not force:
            with open(raw_filepath, "rb") as f:
                data_bytes = f.read()
            src = "disk cache"
        else:
            url = res["url"]
            try:
                req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
                with urllib.request.urlopen(req, timeout=45) as resp:
                    data_bytes = resp.read()
 # Save to disk
                with open(raw_filepath, "wb") as f:
                    f.write(data_bytes)
                src = f"downloaded ({len(data_bytes)/(1024*1024):.1f} MB)"
            except Exception as e:
                print(f" ❌ Download failed: {e}")
                continue

        t0 = time.time()
        try:
            row_count = ingest_month_data(res, data_bytes, conn, save_raw_path=raw_filepath)
            dur = time.time() - t0
            print(f" ✅ Ingested {row_count:,} records in {dur:.2f}s [{src}]")
        except Exception as e:
            print(f" ❌ Ingestion error: {e}")

        if pace > 0:
            time.sleep(pace)

    print("\n🎉 Synchronization complete!")
    print_status(tessie_dir)


def query_price(date_str: str, fuel_type: str = "U91", region: str = "all", tessie_dir: Optional[str] = None):
    price = get_fuel_price(date_str, fuel_type, region, tessie_dir)
    print(f"\n⛽ Reference Fuel Price for {date_str} [{fuel_type.upper()}] ({region}): ${price:.3f}/L")

 # Also show multi-vehicle ICE comparison for standard 100km trip
    print("\n🚗 Benchmark Vehicle 100km Fuel Cost Comparison on this date:")
    comps = calculate_ice_comparison_costs(100.0, date_str, tessie_dir=tessie_dir)
    for k, v in comps.items():
        name = v.get("name") or v.get("label") or k
        f_type = v.get("fuel_type", "U91")
        l_100 = v.get("l_per_100km", 8.5)
        f_price = v.get("fuel_price_per_l", 1.95)
        f_cost = v.get("estimated_cost", (l_100 * f_price))
        print(f"  • {name:<46}: {l_100:>4.1f} L/100km [{f_type:<4} @ ${f_price:.2f}/L] = ${f_cost:.2f}")


def recompute_all(tessie_dir: Optional[str] = None):
    fuel_dir = resolve_fuel_dir(tessie_dir)
    raw_dir = os.path.join(fuel_dir, "raw")
    if not os.path.isdir(raw_dir):
        print(f"❌ No raw directory found at {raw_dir}")
        return

    files = sorted([f for f in os.listdir(raw_dir) if f.endswith((".xlsx", ".csv"))])
    if not files:
        print(f"❌ No cached raw files found in {raw_dir}")
        return

    print(f"🔄 Recomputing aggregates from {len(files)} cached raw archives...")
    conn = get_db_connection(tessie_dir)

    for idx, fname in enumerate(files, 1):
        fpath = os.path.join(raw_dir, fname)
 # Extract month_year from filename
        m = re.search(r"(\d{4})[-_](0[1-9]|1[0-2])", fname)
        ym = f"{m.group(1)}-{m.group(2)}" if m else "unknown"
        res = {
            "resource_id": f"local_{fname}",
            "title": fname,
            "month_year": ym,
            "format": "XLSX" if fname.endswith(".xlsx") else "CSV",
            "url": fpath,
        }
        print(f"[{idx}/{len(files)}] Processing {fname} ({ym})...", end="", flush=True)
        t0 = time.time()
        with open(fpath, "rb") as f:
            data_bytes = f.read()
        row_count = ingest_month_data(res, data_bytes, conn, save_raw_path=fpath)
        print(f" ✅ {row_count:,} rows in {time.time()-t0:.2f}s")

    print("\n🎉 Recomputation complete!")
    print_status(tessie_dir)


def compare_drive(target: str, vehicle_key: Optional[str] = None, tessie_dir: Optional[str] = None):
    try:
        from analyse_drives import TessieAnalyzer
    except ImportError:
        from Tools.analyse_drives import TessieAnalyzer

    analyzer = TessieAnalyzer(tessie_dir=tessie_dir)
    analyzer.inspect_drive(target, vehicle_key=vehicle_key)


def list_vehicles(tessie_dir: Optional[str] = None):
    from lib.fuel_common import load_comparison_vehicles, get_default_comparison_vehicle_key
    reg = load_comparison_vehicles(tessie_dir)
    default_key = get_default_comparison_vehicle_key(tessie_dir)
    vehicles = reg.get("vehicles", {})

    print("\n🚗 BENCHMARK & COMPARISON VEHICLES REGISTRY (Green Vehicle Guide & Real-World):")
    print(f"   Active default comparison vehicle: \033[1m\033[92m{default_key}\033[0m\n")

    hdr = f"  {'Key':<24} | {'Name':<44} | {'Fuel':<6} | {'Urban':<9} | {'Highway':<9} | {'Combined':<9}"
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))

    for k, v in vehicles.items():
        name = v.get("name", k)
        ft = v.get("fuel_type", "U91")
        cons = v.get("consumption", {})
        u_l = f"{cons.get('urban_l_per_100km', 0.0):.1f} L"
        h_l = f"{cons.get('highway_l_per_100km', 0.0):.1f} L"
        c_l = f"{cons.get('combined_l_per_100km', 0.0):.1f} L"
        marker = "⭐ " if k == default_key else "  "
        print(f"{marker}{k:<22} | {name:<44} | {ft:<6} | {u_l:<9} | {h_l:<9} | {c_l:<9}")
    print()


def set_default_vehicle(vehicle_key: str, tessie_dir: Optional[str] = None):
    from lib.fuel_common import load_comparison_vehicles, set_db_default_comparison_vehicle
    reg = load_comparison_vehicles(tessie_dir)
    vehicles = reg.get("vehicles", {})
    if vehicle_key not in vehicles:
        print(f"❌ Vehicle key '{vehicle_key}' not found in comparison vehicle registry.")
        print(f"   Available keys: {', '.join(vehicles.keys())}")
        return

    set_db_default_comparison_vehicle(vehicle_key, tessie_dir=tessie_dir)
    cfg_path = os.path.join(REPO_ROOT, "config.json")
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["default_comparison_vehicle"] = vehicle_key
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    name = vehicles[vehicle_key].get("name", vehicle_key)
    print(f"✅ Set default comparison vehicle to: \033[1m{name}\033[0m ({vehicle_key})")


def main():
    parser = argparse.ArgumentParser(
        description="Synchronize and manage NSW FuelCheck historical pricing datasets for EV cost comparisons."
    )
    parser.add_argument("--sync-all", action="store_true", help="Download and ingest all missing historical months from Data.NSW CKAN.")
    parser.add_argument("--sync-month", type=str, metavar="YYYY-MM", help="Download and ingest a specific month (e.g. 2026-08).")
    parser.add_argument("--limit", type=int, metavar="N", help="Limit number of months to download in this run.")
    parser.add_argument("--force", action="store_true", help="Re-download and re-ingest even if already marked synced.")
    parser.add_argument("--pace", type=float, default=0.3, help="Seconds to wait between downloads (default: 0.3s).")
    parser.add_argument("--status", "--stats", action="store_true", help="Show database health, storage size, and historical range.")
    parser.add_argument("--query", action="store_true", help="Query fuel price on a specific date.")
    parser.add_argument("--date", type=str, default=datetime.date.today().strftime("%Y-%m-%d"), help="Date for price query (YYYY-MM-DD).")
    parser.add_argument("--fuel-type", type=str, default="U91", help="Fuel type for query (U91, P95, P98, DL, E10, LPG).")
    parser.add_argument("--region", type=str, default="all", choices=["all", "metro", "regional"], help="Region for query.")
    parser.add_argument("--compare-drive", type=str, metavar="TARGET", help="Compare a specific drive by index or date/datetime against historical fuel prices.")
    parser.add_argument("--vehicle", "--compare-vehicle", dest="vehicle", type=str, help="Specific comparison vehicle key (e.g. mazda_cx8_diesel, ford_ranger_diesel, rav4_hybrid, petrol_standard).")
    parser.add_argument("--list-vehicles", "--vehicles", action="store_true", help="List all comparison vehicles in database with urban, highway, and combined consumption.")
    parser.add_argument("--set-default-vehicle", type=str, metavar="KEY", help="Set the default comparison vehicle in database and configuration.")
    parser.add_argument("--recompute-aggregates", action="store_true", help="Recompute all database averages from local raw files.")
    parser.add_argument("--tessie-dir", type=str, help="Override path to Tessie data home.")

    args = parser.parse_args()

    if args.list_vehicles:
        list_vehicles(args.tessie_dir)
    elif args.set_default_vehicle:
        set_default_vehicle(args.set_default_vehicle, args.tessie_dir)
    elif args.compare_drive:
        compare_drive(args.compare_drive, vehicle_key=args.vehicle, tessie_dir=args.tessie_dir)
    elif args.query:
        query_price(args.date, args.fuel_type, args.region, args.tessie_dir)
    elif args.sync_month:
        sync_resources(target_month=args.sync_month, force=args.force, pace=args.pace, tessie_dir=args.tessie_dir)
    elif args.sync_all:
        sync_resources(limit=args.limit, force=args.force, pace=args.pace, tessie_dir=args.tessie_dir)
    elif args.recompute_aggregates:
        recompute_all(args.tessie_dir)
    else:
        print_status(args.tessie_dir)


if __name__ == "__main__":
    main()
