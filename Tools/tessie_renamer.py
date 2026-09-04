#!/usr/bin/env python3
"""
Tessie CSV File Classifier & Renamer
===================================
Automatically differentiates:
1. Multi-Drive Summaries vs. Individual Drive Deep Dives
2. Multi-Charge Summaries vs. Individual Charge Deep Dives
3. Multi-Day Continuous Telemetry Streams
4. Parking Idles, Battery Health, Tire Pressures & Firmware Alerts
"""

import os
import sys
import csv
import shutil
import argparse
from datetime import datetime

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
            # Re-read rows with DictReader to inspect values
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

                if span_sec > 7200:  # > 2 hours -> Multi-day Continuous Telemetry Stream
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

def main():
    parser = argparse.ArgumentParser(description="Tessie CSV Classifier & Renamer")
    parser.add_argument("--source", help="Source directory containing raw Tessie CSV files")
    parser.add_argument("--copy-to", help="Copy and rename files to target directory (leaves source intact)")
    parser.add_argument("--in-place", action="store_true", help="Rename files directly in place in source directory")
    parser.add_argument("--dry-run", action="store_true", help="Preview proposed names without renaming or copying")
    
    args = parser.parse_args()

    default_icloud = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie")
    src_dir = args.source or (
        default_icloud if os.path.isdir(default_icloud)
        else os.path.expanduser("~/iCloud/repos/tesla/Tessie")
    )

    if not os.path.isdir(src_dir):
        print(f"Error: Source directory not found: {src_dir}")
        sys.exit(1)

    csv_files = [f for f in sorted(os.listdir(src_dir)) if f.endswith(".csv")]
    if not csv_files:
        print(f"No CSV files found in {src_dir}")
        sys.exit(0)

    print("==========================================================================")
    print(f"             📋 Tessie CSV Review & Renaming Engine                       ")
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

    dest_dir = args.copy_to
    if not args.in_place and not dest_dir:
        external_tessie = find_mounted_tesla_volumes("Tessie")
        dest_dir = (
            external_tessie[0] if external_tessie
            else os.path.expanduser("~/iCloud/repos/tesla/Tessie")
        )

    mode_label = f"Copying & standardizing files to: {dest_dir}" if dest_dir else "Renaming files directly in-place in source directory"
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

    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)
        for orig_name, src_path, info, sz in plan:
            dst_path = os.path.join(dest_dir, info["proposed"])
            with open(src_path, "rb") as f_in, open(dst_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            print(f"✔ Copied: {info['proposed']}")
        print(f"\nSuccessfully organized {len(plan)} file(s) into: {dest_dir}")
    else:
        for orig_name, src_path, info, sz in plan:
            dst_path = os.path.join(src_dir, info["proposed"])
            if src_path != dst_path:
                os.rename(src_path, dst_path)
                print(f"✔ Renamed: {orig_name} ➔ {info['proposed']}")
        print(f"\nSuccessfully renamed {len(plan)} file(s) in place.")

if __name__ == "__main__":
    main()
