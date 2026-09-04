#!/usr/bin/env python3
"""
Tessie Drive Log Analyzer & Deduplication Pipeline
=================================================
- Consolidates & deduplicates multiple Tessie CSV exports into drives_master.csv
- Grouped time periods & interactive period selector
- Resolves GPS coordinates & addresses to custom Known Place nicknames (PII-safe)
- Matches trip departure/arrival windows directly to TeslaCam dashcam footage
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

def parse_relative_date(date_str):
    if not date_str:
        return None
    d_clean = date_str.strip().lower()
    now = datetime.now()
    
    if d_clean == "today":
        return datetime(now.year, now.month, now.day)
    elif d_clean == "yesterday":
        y = now - timedelta(days=1)
        return datetime(y.year, y.month, y.day)
    
    days_of_week = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if d_clean in days_of_week:
        target_weekday = days_of_week.index(d_clean)
        curr_weekday = now.weekday()
        days_ago = (curr_weekday - target_weekday) % 7
        if days_ago == 0:
            days_ago = 7  # previous week's day
        target_date = now - timedelta(days=days_ago)
        return datetime(target_date.year, target_date.month, target_date.day)

    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M"]:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            pass
    return None

class TessieAnalyzer:
    def __init__(self, tessie_dir=None, teslacam_dirs=None):
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(self.script_dir)
        self.icloud_dir = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie")
        
        self.tessie_dirs = [
            tessie_dir,
            "/Volumes/TESLADRIVE 1/Tessie",
            "/Volumes/TESLADRIVE/Tessie",
            os.path.join(parent_dir, "Tessie"),
            os.path.join(self.script_dir, "Tessie"),
            os.path.expanduser("~/iCloud/repos/tesla/Tessie"),
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
                    pass
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

        parts = address.split(",")
        return parts[0].strip() if parts else address

    def consolidate_drives(self, master_dir=None):
        """Merges all found trip summary CSVs into drives_master.csv and purges duplicate rows."""
        dest_dir = master_dir or (
            "/Volumes/TESLADRIVE 1/Tessie" if os.path.isdir("/Volumes/TESLADRIVE 1/Tessie")
            else os.path.expanduser("~/iCloud/repos/tesla/Tessie")
        )
        os.makedirs(dest_dir, exist_ok=True)
        master_file = os.path.join(dest_dir, "drives_master.csv")

        raw_rows = []
        seen_keys = set()
        fieldnames = None

        all_csvs = []
        for td in self.tessie_dirs:
            for f in os.listdir(td):
                if f.endswith(".csv"):
                    all_csvs.append(os.path.join(td, f))

        for csv_path in all_csvs:
            try:
                with open(csv_path, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    if not reader.fieldnames or "Starting Location" not in reader.fieldnames:
                        continue
                    if not fieldnames:
                        fieldnames = reader.fieldnames
                    for r in reader:
                        start_time = r.get("Started At (AEST)") or r.get("Started At") or r.get("Started")
                        end_time = r.get("Ended At (AEST)") or r.get("Ended At") or r.get("Ended")
                        dist = r.get("Distance (km)", "0")
                        s_loc = r.get("Starting Location", "")
                        if not start_time or not end_time:
                            continue
                        key = (start_time.strip(), end_time.strip(), dist.strip(), s_loc.strip())
                        if key not in seen_keys:
                            seen_keys.add(key)
                            raw_rows.append(r)
            except Exception:
                pass

        if not raw_rows:
            return 0

        # Sort chronologically
        raw_rows.sort(key=lambda x: (x.get("Started At (AEST)") or x.get("Started At") or x.get("Started") or ""))

        # Write consolidated master file
        with open(master_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(raw_rows)

        return len(raw_rows)

    def load_drives(self):
        self.consolidate_drives()
        master_file = None
        for td in self.tessie_dirs:
            mf = os.path.join(td, "drives_master.csv")
            if os.path.isfile(mf):
                master_file = mf
                break

        if not master_file:
            return []

        parsed = []
        with open(master_file, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for r in reader:
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

def display_trips(trips, analyzer):
    analyzer.index_videos()
    print(f"\n==========================================================================")
    print(f"       🚗 Detailed Trip Log & Video Matches ({len(trips)} Trips)          ")
    print(f"==========================================================================")

    for i, trip in enumerate(trips):
        t_start = trip["start_dt"]
        t_end = trip["end_dt"]
        dur = trip["dur_min"]
        dist = trip["dist_km"]
        s_place = trip["start_place"]
        e_place = trip["end_place"]
        
        dwell_str = ""
        if i < len(trips) - 1:
            next_start = trips[i+1]["start_dt"]
            dwell_mins = int((next_start - t_end).total_seconds() / 60)
            if dwell_mins >= 0:
                hours, mins = divmod(dwell_mins, 60)
                dwell_str = f" [Parked for {f'{hours}h ' if hours else ''}{mins}m until {next_start.strftime('%H:%M')}]"

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
            print(f"      ℹ Footage not yet synced (present on in-car drive)")

        print(f"   🚪 Exit Window : ~{t_end.strftime('%H:%M:%S')} (Arriving & getting out)")
        if end_clips:
            c_dir = os.path.dirname(end_clips[0][1])
            c_base = os.path.basename(end_clips[0][1])[:19]
            print(f"      ✔ Footage: {c_base}* ({c_dir})")
        else:
            print(f"      ℹ Footage not yet synced (present on in-car drive)")

def main():
    parser = argparse.ArgumentParser(description="Tessie Drive Log Analyzer & Master Consolidator")
    parser.add_argument("--drives", action="store_true", help="Analyze and inspect drives")
    parser.add_argument("--today", action="store_true", help="Inspect only today's drives")
    parser.add_argument("--yesterday", action="store_true", help="Inspect only yesterday's drives")
    parser.add_argument("--since", help="Filter drives since date or day name (e.g. 'wednesday', '2026-09-02')")
    parser.add_argument("--days", type=int, help="Filter drives from past N days")
    parser.add_argument("--place", help="Filter drives by place nickname (e.g. 'School', 'Home', 'Activity', 'Friend')")
    parser.add_argument("--interactive", action="store_true", help="Prompt to select a time period interactively")
    parser.add_argument("--consolidate", action="store_true", help="Consolidate all raw CSVs into drives_master.csv")
    
    args = parser.parse_args()

    analyzer = TessieAnalyzer()
    drives = analyzer.load_drives()

    if not drives:
        print("No Tessie drive records found. Please ensure CSVs exist in iCloud or Tessie/.")
        sys.exit(0)

    # Date filter calculation
    cutoff_dt = None
    end_dt = None

    if args.today:
        cutoff_dt = parse_relative_date("today")
    elif args.yesterday:
        cutoff_dt = parse_relative_date("yesterday")
        end_dt = parse_relative_date("today")
    elif args.since:
        cutoff_dt = parse_relative_date(args.since)
    elif args.days:
        cutoff_dt = datetime.now() - timedelta(days=args.days)

    # Group drives by day for overview table
    by_day = defaultdict(list)
    for d in drives:
        d_key = d["start_dt"].strftime("%Y-%m-%d")
        by_day[d_key].append(d)

    sorted_days = sorted(by_day.keys())

    # If specific filters were provided, display filtered results directly
    if cutoff_dt or args.place:
        filtered = []
        for d in drives:
            if cutoff_dt and d["start_dt"] < cutoff_dt:
                continue
            if end_dt and d["start_dt"] >= end_dt:
                continue
            if args.place:
                q = args.place.lower()
                if q not in d["start_place"].lower() and q not in d["end_place"].lower():
                    continue
            filtered.append(d)

        print("==========================================================================")
        print(f"  🚗 Filtered Drives: {len(filtered)} Trips Found")
        if cutoff_dt:
            print(f"     Since: {cutoff_dt.strftime('%Y-%m-%d %H:%M')}")
        if args.place:
            print(f"     Place: '{args.place}'")
        print("==========================================================================")
        
        display_trips(filtered, analyzer)
        return

    # No filter passed: Print Clean High-Level Overview Summary
    print("==========================================================================")
    print(f"          🚗 Tessie Drives Master Catalog ({len(drives)} Total Trips)       ")
    print("==========================================================================")
    print(f"  • Date Range   : {drives[0]['start_dt'].strftime('%Y-%m-%d')}  ➔  {drives[-1]['start_dt'].strftime('%Y-%m-%d')}")
    print(f"  • Total Distance: {sum(d['dist_km'] for d in drives):.1f} km across {len(drives)} drives")
    print(f"  • Master File  : /Volumes/TESLADRIVE 1/Tessie/drives_master.csv")
    print("--------------------------------------------------------------------------")
    print("  📅 RECENT ACTIVITY SUMMARY (Past 7 Active Days):")

    recent_days = sorted_days[-7:]
    for d_str in recent_days:
        day_trips = by_day[d_str]
        dt_obj = datetime.strptime(d_str, "%Y-%m-%d")
        day_km = sum(t["dist_km"] for t in day_trips)
        day_mins = sum(t["dur_min"] for t in day_trips)
        hours, mins = divmod(day_mins, 60)
        time_str = f"{hours}h {mins:02d}m" if hours else f"{mins}m"
        places_visited = list(dict.fromkeys([t["end_place"] for t in day_trips if t["end_place"] != "Home"]))
        p_str = ", ".join(places_visited[:3])
        print(f"    • {dt_obj.strftime('%a %d %b')}: {len(day_trips):>2} drives | {day_km:>5.1f} km ({time_str:>6}) ➔ {p_str or 'Local'}")

    print("--------------------------------------------------------------------------")
    print("  💡 Select a Time Period to Inspect:")
    print("     [1] Today (Friday 04 Sep)")
    print("     [2] Yesterday (Thursday 03 Sep)")
    print("     [3] Wednesday (02 Sep)")
    print("     [4] Past 7 Days")
    print("     [5] Search by Place (e.g. 'School', 'Activity', 'Friend', 'Swimming')")
    print("     [q] Quit")
    print("--------------------------------------------------------------------------")

    # If running in interactive terminal, prompt user
    if sys.stdin.isatty():
        try:
            choice = input("Enter choice [1-5]: ").strip().lower()
            if choice == "1":
                display_trips(by_day.get(drives[-1]["start_dt"].strftime("%Y-%m-%d"), []), analyzer)
            elif choice == "2":
                if len(sorted_days) >= 2:
                    display_trips(by_day[sorted_days[-2]], analyzer)
            elif choice == "3":
                w_dt = parse_relative_date("wednesday")
                w_key = w_dt.strftime("%Y-%m-%d")
                display_trips(by_day.get(w_key, []), analyzer)
            elif choice == "4":
                p7_trips = [d for d in drives if d["start_dt"] >= datetime.now() - timedelta(days=7)]
                display_trips(p7_trips, analyzer)
            elif choice == "5":
                p_query = input("Enter place nickname or keyword: ").strip()
                p_trips = [d for d in drives if p_query.lower() in d["start_place"].lower() or p_query.lower() in d["end_place"].lower()]
                display_trips(p_trips, analyzer)
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
    else:
        print("Tip: Run with flags: --today, --yesterday, --since wednesday, or --place <NAME>")

if __name__ == "__main__":
    main()
