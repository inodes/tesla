#!/usr/bin/env python3
"""
Tessie Drive Log Analyzer, Importer & Known Places Matcher
=========================================================
- Automatically imports & cleans Tessie exports from iCloud or local directories
- Categorizes multi-drive summaries vs high-frequency telemetry traces
- Matches GPS coordinates and address variations to Known Place nicknames
- Links passenger entry/exit timestamps to TeslaCam video clips
"""

import os
import sys
import csv
import glob
import json
import math
import shutil
import argparse
from datetime import datetime, timedelta
from collections import defaultdict, Counter

def haversine_distance_m(lat1, lon1, lat2, lon2):
    R = 6371000.0  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0)**2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c

class TessieAnalyzer:
    def __init__(self, tessie_dir=None, teslacam_dirs=None):
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.icloud_dir = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie")
        
        self.tessie_dirs = [
            tessie_dir,
            os.path.join(self.script_dir, "Tessie"),
            "/Volumes/TESLADRIVE 1/Tessie",
            "/Volumes/TESLADRIVE/Tessie",
            self.icloud_dir
        ]
        self.tessie_dirs = [d for d in self.tessie_dirs if d and os.path.isdir(d)]
        
        self.teslacam_dirs = teslacam_dirs or [
            "/Volumes/TESLADRIVE 1/TeslaCam",
            "/Volumes/TESLADRIVE/TeslaCam",
            "/Volumes/TESLADRIVE 2/TeslaCam"
        ]
        self.teslacam_dirs = [d for d in self.teslacam_dirs if os.path.isdir(d)]
        
        self.places = self.load_places()
        self.drives = []
        self.video_index = {}

    def load_places(self):
        for td in self.tessie_dirs:
            pf = os.path.join(td, "places.json")
            if os.path.isfile(pf):
                try:
                    with open(pf, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception as e:
                    print(f"Warning: Failed to load {pf}: {e}")
        return {}

    def resolve_place(self, address, saved_loc="", lat=None, lon=None):
        if saved_loc and saved_loc.strip():
            s_clean = saved_loc.strip()
            if s_clean in self.places:
                return s_clean

        addr_clean = address.lower()
        
        # 1. Match by address keywords
        for place_name, p_info in self.places.items():
            for kw in p_info.get("keywords", []):
                if kw.lower() in addr_clean:
                    return place_name

        # 2. Match by GPS radius
        if lat is not None and lon is not None:
            for place_name, p_info in self.places.items():
                p_lat = p_info.get("lat")
                p_lon = p_info.get("lon")
                p_rad = p_info.get("radius_m", 250)
                if p_lat is not None and p_lon is not None:
                    dist = haversine_distance_m(lat, lon, p_lat, p_lon)
                    if dist <= p_rad:
                        return place_name

        # Fallback to street name
        parts = address.split(",")
        return parts[0].strip() if parts else address

    def categorize_csv(self, filepath):
        with open(filepath, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                return "empty", os.path.basename(filepath)
                
            header_set = set(header)
            
            # 1. Multi-Drive Summary (Format A)
            if "Started At (AEST)" in header_set and "Starting Location" in header_set:
                dates = []
                for row in reader:
                    if row and row[0]:
                        try:
                            dt = datetime.strptime(row[0].strip(), "%Y-%m-%d %H:%M")
                            dates.append(dt)
                        except ValueError:
                            pass
                if dates:
                    min_d = min(dates).strftime("%Y-%m-%d")
                    max_d = max(dates).strftime("%Y-%m-%d")
                    return "drives_summary", f"drives_summary_{min_d}_to_{max_d}.csv"
                return "drives_summary", "drives_summary.csv"
                
            # 2. Point-by-point Telemetry Trace (Format B)
            elif "Timestamp (AEST)" in header_set and "Speed (km/h)" in header_set and "Power (kW)" in header_set:
                timestamps = []
                for row in reader:
                    if row and row[0]:
                        try:
                            dt = datetime.strptime(row[0].strip()[:19], "%Y-%m-%d %H:%M:%S")
                            timestamps.append(dt)
                        except ValueError:
                            pass
                if timestamps:
                    min_ts = min(timestamps).strftime("%Y-%m-%d_%H-%M")
                    max_ts = max(timestamps).strftime("%Y-%m-%d_%H-%M")
                    if (max(timestamps) - min(timestamps)).total_seconds() <= 7200:
                        return "single_drive_telemetry", f"drive_trace_{min_ts}.csv"
                    else:
                        return "telemetry_stream", f"telemetry_stream_{min_ts}_to_{max_ts}.csv"
                return "telemetry_stream", "telemetry_stream.csv"
                
            # 3. Charges
            elif "Supercharging (kWh)" in header_set or ("Started At (AEST)" in header_set and "Energy Added (kWh)" in header_set):
                return "charges", "charges_history.csv"
                
            # 4. Idles / Parked
            elif "Started At (AEST)" in header_set and "Location" in header_set and "Duration (Minutes)" in header_set and "Starting Battery (%)" in header_set:
                return "idles", "idles_parking_history.csv"
                
            # 5. Battery
            elif "Max Range (km)" in header_set and "Usable Capacity (kWh)" in header_set:
                return "battery", "battery_health_history.csv"
                
            # 6. Tire Pressure
            elif "Tire" in header_set and "Pressure (psi)" in header_set:
                return "tires", "tire_pressure_history.csv"
                
            # 7. Firmware Alerts
            elif "Customer Facing Message 1" in header_set or "Clear Condition" in header_set:
                return "alerts", "firmware_alerts_history.csv"
                
            return "unknown", os.path.basename(filepath)

    def import_and_organize(self, target_dir=None):
        dest = target_dir or (
            "/Volumes/TESLADRIVE 1/Tessie" if os.path.isdir("/Volumes/TESLADRIVE 1")
            else os.path.join(self.script_dir, "Tessie")
        )
        os.makedirs(dest, exist_ok=True)
        
        if not os.path.isdir(self.icloud_dir):
            print(f"Error: iCloud Tessie folder not found at: {self.icloud_dir}")
            return

        print(f"==========================================================================")
        print(f"         📥 Importing & Standardizing Tessie Files from iCloud             ")
        print(f"==========================================================================")
        print(f"  • Source : {self.icloud_dir}")
        print(f"  • Target : {dest}\n")

        files = [f for f in os.listdir(self.icloud_dir) if f.endswith(".csv")]
        if not files:
            print("  No CSV files found in iCloud Tessie directory.")
            return

        for f in sorted(files):
            src_fp = os.path.join(self.icloud_dir, f)
            cat, std_name = self.categorize_csv(src_fp)
            dst_fp = os.path.join(dest, std_name)
            
            # Copy with standardized name
            with open(src_fp, 'rb') as f_in, open(dst_fp, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
            print(f"  ✔ [{cat:<22}] {f}")
            print(f"     ➔ {std_name}")

        print(f"\n==========================================================================")
        print(f"Successfully organized {len(files)} Tessie file(s) in {dest}")
        print(f"==========================================================================")

    def cluster_places(self, radius_m=250.0):
        if not self.drives:
            self.load_drives()
            
        locations = []
        for d in self.drives:
            if d["start_lat"] and d["start_lon"]:
                locations.append((d["start_addr"], d["raw"].get("Starting Saved Location", ""), d["start_lat"], d["start_lon"]))
            if d["end_lat"] and d["end_lon"]:
                locations.append((d["end_addr"], d["raw"].get("Ending Saved Location", ""), d["end_lat"], d["end_lon"]))

        clusters = []
        for addr, saved, lat, lon in locations:
            matched = None
            for c in clusters:
                if haversine_distance_m(lat, lon, c["lat"], c["lon"]) <= radius_m:
                    matched = c
                    break
            if matched:
                matched["count"] += 1
                matched["addresses"][addr.split(",")[0].strip()] += 1
                if saved:
                    matched["saved"][saved.strip()] += 1
            else:
                clusters.append({
                    "lat": lat,
                    "lon": lon,
                    "count": 1,
                    "addresses": Counter([addr.split(",")[0].strip()]),
                    "saved": Counter([saved.strip()] if saved else [])
                })

        clusters.sort(key=lambda x: x["count"], reverse=True)
        return clusters

    def load_drives(self):
        all_csvs = []
        for td in self.tessie_dirs:
            all_csvs.extend(glob.glob(os.path.join(td, "*.csv")))
            
        if not all_csvs:
            return []

        raw_rows = []
        seen = set()

        for csv_path in all_csvs:
            try:
                with open(csv_path, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    if "Starting Location" not in reader.fieldnames:
                        continue
                    for r in reader:
                        start_time = r.get("Started At (AEST)") or r.get("Started At") or r.get("Started")
                        end_time = r.get("Ended At (AEST)") or r.get("Ended At") or r.get("Ended")
                        if not start_time or not end_time:
                            continue
                        key = (start_time, end_time, r.get("Distance (km)", "0"))
                        if key not in seen:
                            seen.add(key)
                            raw_rows.append(r)
            except Exception as e:
                pass

        parsed = []
        for r in raw_rows:
            try:
                start_str = r.get("Started At (AEST)") or r.get("Started At") or r.get("Started")
                end_str = r.get("Ended At (AEST)") or r.get("Ended At") or r.get("Ended")
                dt_start = datetime.strptime(start_str.strip(), "%Y-%m-%d %H:%M")
                dt_end = datetime.strptime(end_str.strip(), "%Y-%m-%d %H:%M")
                
                dist_km = float(r.get("Distance (km)", 0))
                dur_min = int(float(r.get("Duration (Minutes)", 0)))
                
                s_addr = r.get("Starting Location", "")
                e_addr = r.get("Ending Location", "")
                s_saved = r.get("Starting Saved Location", "")
                e_saved = r.get("Ending Saved Location", "")
                
                s_lat = float(r.get("Starting Latitude", 0)) if r.get("Starting Latitude") else None
                s_lon = float(r.get("Starting Longitude", 0)) if r.get("Starting Longitude") else None
                e_lat = float(r.get("Ending Latitude", 0)) if r.get("Ending Latitude") else None
                e_lon = float(r.get("Ending Longitude", 0)) if r.get("Ending Longitude") else None
                
                s_place = self.resolve_place(s_addr, s_saved, s_lat, s_lon)
                e_place = self.resolve_place(e_addr, e_saved, e_lat, e_lon)
                
                parsed.append({
                    "start_dt": dt_start,
                    "end_dt": dt_end,
                    "dur_min": dur_min,
                    "dist_km": dist_km,
                    "start_addr": s_addr,
                    "end_addr": e_addr,
                    "start_place": s_place,
                    "end_place": e_place,
                    "start_lat": s_lat,
                    "start_lon": s_lon,
                    "end_lat": e_lat,
                    "end_lon": e_lon,
                    "energy_kwh": float(r.get("Total Energy Used (kWh)", 0)) if r.get("Total Energy Used (kWh)") else 0,
                    "raw": r
                })
            except Exception:
                continue

        parsed.sort(key=lambda x: x["start_dt"])
        self.drives = parsed
        return self.drives

    def index_videos(self):
        for tc in self.teslacam_dirs:
            for root, _, files in os.walk(tc):
                for f in files:
                    if not f.startswith("._") and f.endswith(".mp4"):
                        base = f[:19]
                        try:
                            dt = datetime.strptime(base, "%Y-%m-%d_%H-%M-%S")
                            if dt not in self.video_index:
                                self.video_index[dt] = os.path.join(root, f)
                        except ValueError:
                            pass

    def find_footage(self, target_dt, window_seconds=120):
        matches = []
        for dt, fpath in self.video_index.items():
            if abs((dt - target_dt).total_seconds()) <= window_seconds:
                matches.append((dt, fpath))
        matches.sort(key=lambda x: x[0])
        return matches

def main():
    parser = argparse.ArgumentParser(description="Tessie Drive Log Analyzer & TeslaCam Matcher")
    parser.add_argument("--import-icloud", action="store_true", help="Import & standardize CSVs from iCloud Tessie folder")
    parser.add_argument("--cluster", action="store_true", help="Discover and display frequent location clusters & nicknames")
    parser.add_argument("--since", help="Filter drives since date (YYYY-MM-DD), e.g. 2026-09-02")
    parser.add_argument("--days", type=int, help="Filter drives from past N days")
    parser.add_argument("--place", help="Filter drives involving a specific place name (e.g. 'School', 'Home', 'Bunnings')")
    parser.add_argument("--entry-exit", action="store_true", help="Focus on door entry & exit timestamps with camera video file paths")
    parser.add_argument("--tessie-dir", help="Custom path to directory containing Tessie CSV exports")
    
    args = parser.parse_args()

    analyzer = TessieAnalyzer(tessie_dir=args.tessie_dir)

    if args.import_icloud:
        analyzer.import_and_organize()
        return

    if args.cluster:
        clusters = analyzer.cluster_places()
        print("==========================================================================")
        print(f"         📍 Frequent Location Clusters ({len(clusters)} Areas Discovered) ")
        print("==========================================================================")
        for i, c in enumerate(clusters[:20]):
            top_addr = c["addresses"].most_common(1)[0][0]
            top_saved = c["saved"].most_common(1)[0][0] if c["saved"] else ""
            aliases = [f"{a} ({cnt})" for a, cnt in c["addresses"].most_common(3)[1:]]
            name = top_saved or top_addr
            print(f"\n[{i+1}] {name} (Visited {c['count']} times)")
            print(f"    GPS Center : {c['lat']:.5f}, {c['lon']:.5f} (Radius ~250m)")
            print(f"    Primary Addr: {top_addr}")
            if aliases:
                print(f"    Aliases     : {', '.join(aliases)}")
        return

    drives = analyzer.load_drives()
    if not drives:
        print("==========================================================================")
        print("               📁 No Tessie CSV Export Files Found                        ")
        print("==========================================================================")
        print("Run with --import-icloud to automatically import your Tessie exports from iCloud:")
        print("  ./tessie_analyzer.py --import-icloud")
        sys.exit(0)

    cutoff_dt = None
    if args.since:
        try:
            cutoff_dt = datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            print(f"Error: Invalid date format for --since: {args.since}")
            sys.exit(1)
    elif args.days:
        cutoff_dt = datetime.now() - timedelta(days=args.days)

    filtered_drives = []
    for d in drives:
        if cutoff_dt and d["start_dt"] < cutoff_dt:
            continue
        if args.place:
            p_query = args.place.lower()
            if p_query not in d["start_place"].lower() and p_query not in d["end_place"].lower():
                continue
        filtered_drives.append(d)

    print("==========================================================================")
    print(f"       🚗 Tessie Drive & Location Analyzer ({len(filtered_drives)} Trips Found)        ")
    print("==========================================================================")
    if cutoff_dt:
        print(f"Filter: Since {cutoff_dt.strftime('%Y-%m-%d')}")
    if args.place:
        print(f"Place Filter: '{args.place}'")
    print("--------------------------------------------------------------------------")

    analyzer.index_videos()

    for i, trip in enumerate(filtered_drives):
        t_start = trip["start_dt"]
        t_end = trip["end_dt"]
        dur = trip["dur_min"]
        dist = trip["dist_km"]
        s_place = trip["start_place"]
        e_place = trip["end_place"]
        
        dwell_str = ""
        if i < len(filtered_drives) - 1:
            next_start = filtered_drives[i+1]["start_dt"]
            dwell_mins = int((next_start - t_end).total_seconds() / 60)
            if dwell_mins >= 0:
                hours, mins = divmod(dwell_mins, 60)
                if hours > 0:
                    dwell_str = f" [Parked for {hours}h {mins}m until {next_start.strftime('%H:%M')}]"
                else:
                    dwell_str = f" [Parked for {mins}m until {next_start.strftime('%H:%M')}]"

        start_clips = analyzer.find_footage(t_start, window_seconds=120)
        end_clips = analyzer.find_footage(t_end, window_seconds=180)

        print(f"\n🚗 TRIP #{i+1} | {t_start.strftime('%a %d %b')}: {t_start.strftime('%H:%M')} ➔ {t_end.strftime('%H:%M')} ({dur}m, {dist:.1f} km)")
        print(f"   🛫 Origin      : {s_place} ({trip['start_addr'].split(',')[0]})")
        print(f"   🛬 Destination : {e_place} ({trip['end_addr'].split(',')[0]}){dwell_str}")

        print(f"   🚪 Entry Window: ~{t_start.strftime('%H:%M:%S')} (Getting into car)")
        if start_clips:
            c_dir = os.path.dirname(start_clips[0][1])
            c_base = os.path.basename(start_clips[0][1])[:19]
            print(f"      ✔ Footage: {c_base}* ({c_dir})")
        else:
            print(f"      ℹ Footage not yet synced (present on car's in-car drive)")

        print(f"   🚪 Exit Window : ~{t_end.strftime('%H:%M:%S')} (Arriving & getting out)")
        if end_clips:
            c_dir = os.path.dirname(end_clips[0][1])
            c_base = os.path.basename(end_clips[0][1])[:19]
            print(f"      ✔ Footage: {c_base}* ({c_dir})")
        else:
            print(f"      ℹ Footage not yet synced (present on car's in-car drive)")

if __name__ == "__main__":
    main()
