#!/usr/bin/env python3
"""
Tessie CSV File Classifier & Renamer
===================================
Inspects Tessie CSV exports, detects data schemas, extracts actual date spans,
and renames files into clear, standardized names for review and archiving.
"""

import os
import sys
import csv
import shutil
import argparse
from datetime import datetime

def analyze_file(filepath):
    filename = os.path.basename(filepath)
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return {
                "type": "empty",
                "desc": "Empty File",
                "proposed": filename,
                "rows": 0
            }

        header_set = set(h.strip() for h in header)
        
        # 1. Drives Summary (Format A)
        if "Started At (AEST)" in header_set and "Starting Location" in header_set:
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
                desc = f"Trip Summaries ({rows} drives: {min_d} ➔ {max_d})"
            else:
                name = "drives_summary.csv"
                desc = f"Trip Summaries ({rows} drives)"
            return {
                "type": "drives_summary",
                "desc": desc,
                "proposed": name,
                "rows": rows
            }

        # 2. Telemetry Traces (Format B)
        elif "Timestamp (AEST)" in header_set and "Speed (km/h)" in header_set and "Power (kW)" in header_set:
            timestamps = []
            for row in reader:
                if row and row[0]:
                    try:
                        dt = datetime.strptime(row[0].strip()[:19], "%Y-%m-%d %H:%M:%S")
                        timestamps.append(dt)
                    except ValueError:
                        pass
            rows = len(timestamps)
            if timestamps:
                min_ts = min(timestamps)
                max_ts = max(timestamps)
                if (max_ts - min_ts).total_seconds() <= 7200:
                    name = f"drive_telemetry_{min_ts.strftime('%Y-%m-%d_%H-%M')}.csv"
                    desc = f"Single Drive Telemetry ({rows} pts on {min_ts.strftime('%Y-%m-%d %H:%M')})"
                else:
                    name = f"telemetry_stream_{min_ts.strftime('%Y-%m-%d')}_to_{max_ts.strftime('%Y-%m-%d')}.csv"
                    desc = f"Continuous Telemetry Stream ({rows} pts: {min_ts.strftime('%Y-%m-%d')} ➔ {max_ts.strftime('%Y-%m-%d')})"
            else:
                name = "drive_telemetry.csv"
                desc = "Drive Telemetry Trace"
            return {
                "type": "telemetry",
                "desc": desc,
                "proposed": name,
                "rows": rows
            }

        # 3. Charging Sessions
        elif "Supercharging (kWh)" in header_set or ("Started At (AEST)" in header_set and "Energy Added (kWh)" in header_set):
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
            name = f"charges_{min_d}_to_{max_d}.csv" if dates else "charges_history.csv"
            return {
                "type": "charges",
                "desc": f"Charging Sessions ({rows} charges: {min_d} ➔ {max_d})",
                "proposed": name,
                "rows": rows
            }

        # 4. Parking & Idles
        elif "Started At (AEST)" in header_set and "Location" in header_set and "Duration (Minutes)" in header_set and "Starting Battery (%)" in header_set:
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
            name = f"idles_parking_{min_d}_to_{max_d}.csv" if dates else "idles_parking_history.csv"
            return {
                "type": "idles",
                "desc": f"Parking & Idle Periods ({rows} sessions: {min_d} ➔ {max_d})",
                "proposed": name,
                "rows": rows
            }

        # 5. Battery Health
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
                "desc": f"Battery Health & Capacity ({rows} readings: {min_d} ➔ {max_d})",
                "proposed": name,
                "rows": rows
            }

        # 6. Tire Pressure
        elif "Tire" in header_set and "Pressure (psi)" in header_set:
            rows = sum(1 for _ in reader)
            return {
                "type": "tires",
                "desc": f"Tire Pressure Telemetry ({rows} PSI readings)",
                "proposed": "tire_pressure_history.csv",
                "rows": rows
            }

        # 7. Firmware Alerts
        elif "Customer Facing Message 1" in header_set or "Clear Condition" in header_set:
            rows = sum(1 for _ in reader)
            return {
                "type": "alerts",
                "desc": f"Vehicle Firmware Diagnostics ({rows} DTC alerts)",
                "proposed": "firmware_alerts_history.csv",
                "rows": rows
            }

        return {
            "type": "unknown",
            "desc": "Generic CSV",
            "proposed": filename,
            "rows": sum(1 for _ in reader)
        }

def main():
    parser = argparse.ArgumentParser(description="Tessie CSV File Classifier & Renamer")
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
        print(f"   Category : [{info['type']}] - {info['desc']}")
        print(f"   ➔ Rename : {info['proposed']}\n")

    print("==========================================================================")

    if args.dry_run:
        print("Dry run mode: No changes made.")
        return

    # Determine default destination if neither --in-place nor --copy-to is set
    dest_dir = args.copy_to
    if not args.in_place and not dest_dir:
        dest_dir = (
            "/Volumes/TESLADRIVE 1/Tessie" if os.path.isdir("/Volumes/TESLADRIVE 1/Tessie")
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

    # Execute Copy or In-place rename
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
