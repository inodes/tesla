#!/usr/bin/env python3
"""
Tessie CSV File Classifier & Renamer
===================================
Automatically differentiates:
1. Multi-Drive Summaries vs. Individual Drive Deep Dives
2. Multi-Charge Summaries vs. Individual Charge Deep Dives
3. Multi-Day Continuous Telemetry Streams
4. Parking Idles, Battery Health, Tire Pressures & Firmware Alerts
5. Ingestion pipeline: Landing Inbox (Downloads/Inbox) ➔ Standardized iCloud/SSD
"""

import os
import sys
import csv
import json
import glob
import shutil
import argparse
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
CONFIG_JSON_PATH = os.path.join(REPO_ROOT, "Tessie", "config.json")

def load_config():
    """Loads configuration dictionary from config.json."""
    default_config = {
        "landing_directory": "~/Downloads",
        "inbox_directory": "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie/Inbox",
        "tessie_directory": "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie",
        "invoices_directory": "~/iCloud/PDF/Tesla/Supercharging"
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

def find_mounted_tesla_volumes(subdir=None):
    """
    Dynamically discovers all mounted volumes matching TESLADRIVE* under /Volumes.
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

def analyze_file(filepath):
    filename = os.path.basename(filepath)
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return {
                "type": "empty",
                "category": "Empty",
                "desc": "Empty File (0 bytes)",
                "proposed": filename,
                "rows": 0
            }

        header_set = set(h.strip() for h in header)
        
        # 1. DRIVES SUMMARY (Format A: Multi-Trip Catalog)
        if "Starting Location" in header_set and "Distance (km)" in header_set:
            dates = []
            for row in reader:
                if row and row[0]:
                    try:
                        dt = datetime.strptime(row[0].strip()[:16], "%Y-%m-%d %H:%M")
                        dates.append(dt)
                    except ValueError:
                        pass
            rows = len(dates)
            if dates:
                min_d = min(dates).strftime("%Y-%m-%d")
                max_d = max(dates).strftime("%Y-%m-%d")
                name = f"drives_summary_{min_d}_to_{max_d}.csv"
                desc = f"Multi-Trip Summary Catalog ({rows} trips: {min_d} ➔ {max_d})"
            else:
                name = "drives_summary.csv"
                desc = f"Multi-Trip Summary Catalog ({rows} trips)"
            return {
                "type": "drives_summary",
                "category": "Drives Summary",
                "desc": desc,
                "proposed": name,
                "rows": rows
            }

        # 2. CHARGES SUMMARY (Format A: Multi-Charge Catalog)
        elif "Supercharger" in header_set and ("Energy Added (kWh)" in header_set or "Location" in header_set):
            dates = []
            for row in reader:
                if row and row[0]:
                    try:
                        dt = datetime.strptime(row[0].strip()[:16], "%Y-%m-%d %H:%M")
                        dates.append(dt)
                    except ValueError:
                        pass
            rows = len(dates)
            if dates:
                min_d = min(dates).strftime("%Y-%m-%d")
                max_d = max(dates).strftime("%Y-%m-%d")
                name = f"charges_summary_{min_d}_to_{max_d}.csv"
                desc = f"Multi-Charge Summary Catalog ({rows} charges: {min_d} ➔ {max_d})"
            else:
                name = "charges_summary.csv"
                desc = f"Multi-Charge Summary Catalog ({rows} charges)"
            return {
                "type": "charges_summary",
                "category": "Charges Summary",
                "desc": desc,
                "proposed": name,
                "rows": rows
            }

        # 3. PARKING & IDLES SUMMARY
        elif "Location" in header_set and "Starting Battery (%)" in header_set:
            dates = []
            for row in reader:
                if row and row[0]:
                    try:
                        dt = datetime.strptime(row[0].strip()[:16], "%Y-%m-%d %H:%M")
                        dates.append(dt)
                    except ValueError:
                        pass
            rows = len(dates)
            min_d = min(dates).strftime("%Y-%m-%d") if dates else ""
            max_d = max(dates).strftime("%Y-%m-%d") if dates else ""
            name = f"idles_summary_{min_d}_to_{max_d}.csv" if dates else "idles_summary.csv"
            return {
                "type": "idles_summary",
                "category": "Idles / Parking",
                "desc": f"Parking & Idle Periods ({rows} sessions: {min_d} ➔ {max_d})",
                "proposed": name,
                "rows": rows
            }

        # 4. BATTERY HEALTH & CAPACITY
        elif "Max Range (km)" in header_set and "Usable Capacity (kWh)" in header_set:
            dates = []
            for row in reader:
                if row and row[0]:
                    try:
                        dt = datetime.strptime(row[0].strip()[:10], "%Y-%m-%d")
                        dates.append(dt)
                    except ValueError:
                        pass
            rows = len(dates)
            min_d = min(dates).strftime("%Y-%m-%d") if dates else ""
            max_d = max(dates).strftime("%Y-%m-%d") if dates else ""
            name = f"battery_health_{min_d}_to_{max_d}.csv" if dates else "battery_health_history.csv"
            return {
                "type": "battery",
                "category": "Battery Health",
                "desc": f"Battery Capacity & Max Range ({rows} readings: {min_d} ➔ {max_d})",
                "proposed": name,
                "rows": rows
            }

        # 5. TIRE PRESSURE TELEMETRY
        elif "Tire" in header_set and "Pressure (psi)" in header_set:
            rows = sum(1 for _ in reader)
            return {
                "type": "tires",
                "category": "Tire Pressure",
                "desc": f"Tire Pressure Telemetry ({rows} PSI readings)",
                "proposed": "tire_pressure_history.csv",
                "rows": rows
            }

        # 6. FIRMWARE ALERTS / DIAGNOSTICS
        elif "Customer Facing Message 1" in header_set or "Clear Condition" in header_set:
            rows = sum(1 for _ in reader)
            return {
                "type": "alerts",
                "category": "Firmware Alerts",
                "desc": f"Vehicle Diagnostics & Trouble Codes ({rows} DTC alerts)",
                "proposed": "firmware_alerts_history.csv",
                "rows": rows
            }

        # 7. HIGH-FREQUENCY TELEMETRY (Deep Dives vs Continuous Stream)
        elif "Timestamp (AEST)" in header_set or "Timestamp" in header_set:
            with open(filepath, "r", encoding="utf-8-sig") as fh_dict:
                d_reader = csv.DictReader(fh_dict)
                rows_list = list(d_reader)

            timestamps = []
            is_charging = False
            is_driving = False

            for r in rows_list:
                ts_str = r.get("Timestamp (AEST)") or r.get("Timestamp")
                if ts_str:
                    try:
                        dt = datetime.strptime(ts_str.strip()[:19], "%Y-%m-%d %H:%M:%S")
                        timestamps.append(dt)
                    except ValueError:
                        pass

                c_state = (r.get("Charging State") or "").lower()
                s_state = (r.get("Shift State") or "").upper()
                spd = float(r.get("Speed (km/h)") or 0)
                chg_pwr = float(r.get("Charger Power (kW)") or 0)

                if c_state in ["charging", "complete"] or chg_pwr > 0:
                    is_charging = True
                if s_state in ["D", "R"] or spd > 0:
                    is_driving = True

            rows = len(timestamps)
            if timestamps:
                min_ts = min(timestamps)
                max_ts = max(timestamps)
                span_sec = (max_ts - min_ts).total_seconds()

                if span_sec > 7200:  # > 2 hours -> Continuous Telemetry Stream
                    name = f"telemetry_stream_{min_ts.strftime('%Y-%m-%d')}_to_{max_ts.strftime('%Y-%m-%d')}.csv"
                    desc = f"Continuous Telemetry Stream ({rows} samples: {min_ts.strftime('%Y-%m-%d')} ➔ {max_ts.strftime('%Y-%m-%d')})"
                    cat = "Telemetry Stream"
                    typ = "telemetry_stream"
                elif is_driving or not is_charging:  # Single Drive Deep Dive
                    name = f"drive_deepdive_{min_ts.strftime('%Y-%m-%d_%H-%M')}.csv"
                    desc = f"Single Drive Deep Dive ({rows} GPS/speed/power samples on {min_ts.strftime('%Y-%m-%d %H:%M')})"
                    cat = "Drive Deep Dive"
                    typ = "drive_deepdive"
                else:  # Single Charge Deep Dive
                    name = f"charge_deepdive_{min_ts.strftime('%Y-%m-%d_%H-%M')}.csv"
                    desc = f"Single Charge Deep Dive ({rows} telemetry samples on {min_ts.strftime('%Y-%m-%d %H:%M')})"
                    cat = "Charge Deep Dive"
                    typ = "charge_deepdive"
            else:
                name = "telemetry.csv"
                desc = "Telemetry Trace"
                cat = "Telemetry"
                typ = "telemetry"

            return {
                "type": typ,
                "category": cat,
                "desc": desc,
                "proposed": name,
                "rows": rows
            }

        return {
            "type": "unknown",
            "category": "Unknown CSV",
            "desc": "Generic CSV Dataset",
            "proposed": filename,
            "rows": sum(1 for _ in reader)
        }

def find_inbox_tessie_files(inbox_dirs):
    """Discovers raw Tessie CSV files in landing / inbox directories."""
    candidates = []
    for d in inbox_dirs:
        exp_d = os.path.abspath(os.path.expanduser(d))
        if os.path.isdir(exp_d):
            for f in os.listdir(exp_d):
                if f.endswith(".csv"):
                    fp = os.path.join(exp_d, f)
                    try:
                        info = analyze_file(fp)
                        if info["type"] != "unknown":
                            candidates.append((fp, info))
                    except Exception:
                        pass
    return candidates

def main():
    parser = argparse.ArgumentParser(description="Tessie CSV Classifier, Renamer & Ingestion Engine")
    parser.add_argument("--source", help="Source directory containing raw Tessie CSV files")
    parser.add_argument("--inbox", "--landing", action="store_true", help="Auto-ingest new downloads from landing inbox directories (~/Downloads, Inbox)")
    parser.add_argument("--copy-to", help="Copy and rename files to target directory (leaves source intact)")
    parser.add_argument("--move-to", help="Move and rename files to target directory (removes from landing inbox)")
    parser.add_argument("--in-place", action="store_true", help="Rename files directly in place in source directory")
    parser.add_argument("--dry-run", action="store_true", help="Preview proposed names without renaming or copying")
    
    args = parser.parse_args()
    config = load_config()

    target_tessie_dir = os.path.abspath(os.path.expanduser(config.get("tessie_directory", "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie")))
    inbox_dir = os.path.abspath(os.path.expanduser(config.get("inbox_directory", "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie/Inbox")))
    landing_dir = os.path.abspath(os.path.expanduser(config.get("landing_directory", "~/Downloads")))

    is_inbox_mode = getattr(args, "inbox", False)

    # 1. Ingestion / Inbox Mode
    if is_inbox_mode:
        inbox_dirs = [inbox_dir, landing_dir]
        found = find_inbox_tessie_files(inbox_dirs)
        if not found:
            print(f"ℹ️  No new Tessie CSV downloads found in landing directories:")
            print(f"   • {inbox_dir}")
            print(f"   • {landing_dir}")
            sys.exit(0)

        print("==========================================================================")
        print("          📥 Tessie Download Ingestion & Renaming Engine                  ")
        print("==========================================================================")
        print(f"Found {len(found)} new download(s) in landing area:\n")

        plan = []
        for fp, info in found:
            sz_kb = os.path.getsize(fp) / 1024.0
            plan.append((os.path.basename(fp), fp, info, sz_kb))
            print(f"📁 {os.path.basename(fp)} ({sz_kb:.1f} KB)")
            print(f"   Source   : {os.path.dirname(fp)}")
            print(f"   Category : [{info['category']}] - {info['desc']}")
            print(f"   ➔ Move To: {info['proposed']}\n")

        print("==========================================================================")
        if args.dry_run:
            print("Dry run mode: No changes made.")
            return

        dest_dir = args.copy_to or args.move_to or target_tessie_dir
        print(f"Target Directory: {dest_dir}")

        if sys.stdin.isatty():
            try:
                confirm = input("Move and standardize into Tessie data directory? [y/N]: ").strip().lower()
                if confirm != "y":
                    print("Cancelled.")
                    return
            except (KeyboardInterrupt, EOFError):
                print("\nCancelled.")
                return

        os.makedirs(dest_dir, exist_ok=True)
        for orig_name, src_path, info, sz in plan:
            dst_path = os.path.join(dest_dir, info["proposed"])
            shutil.move(src_path, dst_path)
            print(f"✔ Moved & Standardized: {info['proposed']}")

        # Multi-drive sync
        ext_volumes = find_mounted_tesla_volumes("Tessie")
        if ext_volumes:
            for v in ext_volumes:
                for orig_name, src_path, info, sz in plan:
                    v_dst = os.path.join(v, info["proposed"])
                    try:
                        shutil.copyfile(os.path.join(dest_dir, info["proposed"]), v_dst)
                        print(f"✔ Synced to external SSD: {v_dst}")
                    except Exception as e:
                        print(f"⚠️ Failed to sync to {v_dst}: {e}")

        print(f"\n🎉 Successfully ingested {len(plan)} file(s) into {dest_dir}!")
        return

    # 2. Standard Directory Processing Mode
    src_dir = args.source or target_tessie_dir
    if not os.path.isdir(src_dir):
        print(f"Error: Source directory not found: {src_dir}")
        sys.exit(1)

    csv_files = [f for f in sorted(os.listdir(src_dir)) if f.endswith(".csv")]
    if not csv_files:
        print(f"No CSV files found in {src_dir}")
        sys.exit(0)

    print("==========================================================================")
    print("             📋 Tessie CSV Review & Renaming Engine                       ")
    print("==========================================================================")
    print(f"Source Directory: {src_dir}")
    print(f"Found {len(csv_files)} CSV file(s):\n")

    plan = []
    for f in csv_files:
        fp = os.path.join(src_dir, f)
        info = analyze_file(fp)
        sz_kb = os.path.getsize(fp) / 1024.0
        plan.append((f, fp, info, sz_kb))
        
        print(f"📁 {f} ({sz_kb:.1f} KB)")
        print(f"   Category : [{info['category']}] - {info['desc']}")
        print(f"   ➔ Rename : {info['proposed']}\n")

    print("==========================================================================")

    if args.dry_run:
        print("Dry run mode: No changes made.")
        return

    dest_dir = args.copy_to or args.move_to
    if not args.in_place and not dest_dir:
        dest_dir = target_tessie_dir

    if args.in_place:
        mode_label = "Renaming files directly in-place in source directory"
    elif args.move_to:
        mode_label = f"Moving & standardizing files to: {dest_dir}"
    else:
        mode_label = f"Copying & standardizing files to: {dest_dir}"

    print(f"Action: {mode_label}")

    if sys.stdin.isatty():
        try:
            confirm = input("Proceed? [y/N]: ").strip().lower()
            if confirm != "y":
                print("Cancelled.")
                return
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return

    if args.in_place:
        for orig_name, src_path, info, sz in plan:
            dst_path = os.path.join(src_dir, info["proposed"])
            if src_path != dst_path:
                os.rename(src_path, dst_path)
                print(f"✔ Renamed: {orig_name} ➔ {info['proposed']}")
        print(f"\nSuccessfully renamed {len(plan)} file(s) in place.")
    elif args.move_to:
        os.makedirs(dest_dir, exist_ok=True)
        for orig_name, src_path, info, sz in plan:
            dst_path = os.path.join(dest_dir, info["proposed"])
            shutil.move(src_path, dst_path)
            print(f"✔ Moved: {info['proposed']}")
        print(f"\nSuccessfully moved {len(plan)} file(s) into: {dest_dir}")
    else:
        os.makedirs(dest_dir, exist_ok=True)
        for orig_name, src_path, info, sz in plan:
            dst_path = os.path.join(dest_dir, info["proposed"])
            with open(src_path, "rb") as f_in, open(dst_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            print(f"✔ Copied: {info['proposed']}")
        print(f"\nSuccessfully organized {len(plan)} file(s) into: {dest_dir}")

if __name__ == "__main__":
    main()
