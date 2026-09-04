#!/usr/bin/env python3
"""
Tessie Drive Log Analyzer & Master Consolidator
===============================================
- Multi-level drill-down: Place ➔ Days ➔ Trips ➔ Camera Footage
- Differentiates footage sources:
    1. Recent (Driving Loop)
    2. Sentry (Sentry Alert Events)
    3. Saved (Honks & Dashcam Taps)
    4. No footage / In-Car only
- Consolidates & deduplicates raw Tessie CSVs into drives_master.csv
- PII-safe Known Place nicknames
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
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0)**2
    return R * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

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
            days_ago = 7
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
        self.footage_db = []
        self._indexed = False

    def load_places(self):
        for td in self.tessie_dirs:
            pf = os.path.join(td, "places.json")
            if os.path.isfile(pf):
                try:
                    with open(pf, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
        return {}

    def resolve_place(self, address, saved_loc="", lat=None, lon=None):
        if saved_loc and saved_loc.strip():
            s_clean = saved_loc.strip()
            if s_clean in self.places:
                return s_clean

        addr_clean = address.lower()
        
        for place_name, p_info in self.places.items():
            for kw in p_info.get("keywords", []):
                if kw.lower() in addr_clean:
                    return place_name

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

        raw_rows.sort(key=lambda x: (x.get("Started At (AEST)") or x.get("Started At") or x.get("Started") or ""))

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

    def index_footage(self):
        if self._indexed:
            return
        self.footage_db = []
        for tc in self.teslacam_dirs:
            if not os.path.isdir(tc):
                continue
            for root, _, files in os.walk(tc):
                rel = os.path.relpath(root, tc)
                if "RecentClips" in rel:
                    cat = "Recent"
                elif "SentryClips" in rel:
                    cat = "Sentry"
                elif "SavedClips" in rel:
                    cat = "Saved"
                else:
                    cat = "Other"

                for f in files:
                    if not f.startswith("._") and f.endswith(".mp4") and f != "event.mp4":
                        base = f[:19]
                        try:
                            dt = datetime.strptime(base, "%Y-%m-%d_%H-%M-%S")
                            cam = f[20:-4] if len(f) > 24 else "front"
                            self.footage_db.append({
                                "dt": dt,
                                "cat": cat,
                                "path": os.path.join(root, f),
                                "folder": root,
                                "file": f,
                                "cam": cam
                            })
                        except ValueError:
                            pass
        self._indexed = True

    def find_footage(self, target_dt, window_seconds=180):
        self.index_footage()
        matches = []
        for item in self.footage_db:
            if abs((item["dt"] - target_dt).total_seconds()) <= window_seconds:
                matches.append(item)
        matches.sort(key=lambda x: (x["dt"], x["cam"]))
        return matches

    def get_trip_footage_summary(self, trip):
        """Returns a string tag describing footage availability (Recent, Sentry, Saved, or No footage)."""
        start_clips = self.find_footage(trip["start_dt"], 120)
        end_clips = self.find_footage(trip["end_dt"], 180)
        all_clips = start_clips + end_clips
        if not all_clips:
            return "No local footage", set()
        
        cats = sorted(list(set(c["cat"] for c in all_clips)))
        cat_str = " + ".join(cats)
        return cat_str, set(cats)

def display_footage_details(trip, analyzer):
    """Level 3: Deep listing of exact video files by camera angle for a single drive."""
    start_clips = analyzer.find_footage(trip["start_dt"], 120)
    end_clips = analyzer.find_footage(trip["end_dt"], 180)
    
    t_start = trip["start_dt"]
    t_end = trip["end_dt"]
    
    print(f"\n==========================================================================")
    print(f" 📹 CAMERA FOOTAGE LISTING: {t_start.strftime('%a %d %b %Y')} ({t_start.strftime('%H:%M')} ➔ {t_end.strftime('%H:%M')})")
    print(f"    Origin      : {trip['start_place']} ({trip['start_addr'].split(',')[0]})")
    print(f"    Destination : {trip['end_place']} ({trip['end_addr'].split(',')[0]})")
    print(f"==========================================================================")
    
    # 1. Entry footage (getting into car)
    print(f"\n🚪 1. ENTRY WINDOW (Departure ~{t_start.strftime('%H:%M:%S')}):")
    if start_clips:
        by_folder = defaultdict(list)
        for c in start_clips:
            by_folder[(c['cat'], c['folder'])].append(c)
            
        for (cat, folder), clips in by_folder.items():
            print(f"   [{cat}Clips] 📂 {folder}")
            # Group by timestamp
            by_ts = defaultdict(dict)
            for c in clips:
                ts_str = c['dt'].strftime("%H:%M:%S")
                by_ts[ts_str][c['cam']] = c['file']
            for ts_str, cam_dict in sorted(by_ts.items()):
                cams = ", ".join(sorted(cam_dict.keys()))
                sample = cam_dict.get('left_repeater') or cam_dict.get('front') or list(cam_dict.values())[0]
                print(f"     • {ts_str} ({cams}) ➔ {sample}")
    else:
        print("   ⚠️ No local footage found on archive drives.")

    # 2. Exit footage (arriving & unloading)
    print(f"\n🚪 2. EXIT WINDOW (Arrival ~{t_end.strftime('%H:%M:%S')}):")
    if end_clips:
        by_folder = defaultdict(list)
        for c in end_clips:
            by_folder[(c['cat'], c['folder'])].append(c)
            
        for (cat, folder), clips in by_folder.items():
            print(f"   [{cat}Clips] 📂 {folder}")
            by_ts = defaultdict(dict)
            for c in clips:
                ts_str = c['dt'].strftime("%H:%M:%S")
                by_ts[ts_str][c['cam']] = c['file']
            for ts_str, cam_dict in sorted(by_ts.items()):
                cams = ", ".join(sorted(cam_dict.keys()))
                sample = cam_dict.get('left_repeater') or cam_dict.get('front') or list(cam_dict.values())[0]
                print(f"     • {ts_str} ({cams}) ➔ {sample}")
    else:
        print("   ⚠️ No local footage found on archive drives.")
        
    print(f"\n💡 Quick Tips:")
    print(f"   • To view side doors (getting in/out): check *-left_repeater.mp4 or *-right_repeater.mp4")
    print(f"   • To view trunk/rear walking: check *-back.mp4")
    if start_clips or end_clips:
        first_folder = (start_clips[0]['folder'] if start_clips else end_clips[0]['folder'])
        print(f"   • Open folder in Finder: open \"{first_folder}\"")
    print(f"--------------------------------------------------------------------------")

def drill_down_day(day_str, day_trips, analyzer):
    """Level 2: Display drives for a selected day and allow picking a trip for footage listing."""
    while True:
        dt_obj = datetime.strptime(day_str, "%Y-%m-%d")
        total_km = sum(t["dist_km"] for t in day_trips)
        total_mins = sum(t["dur_min"] for t in day_trips)
        hours, mins = divmod(total_mins, 60)
        time_str = f"{hours}h {mins:02d}m" if hours else f"{mins}m"
        
        print(f"\n==========================================================================")
        print(f" 📅 {dt_obj.strftime('%A, %d %B %Y')} — {len(day_trips)} Drives ({time_str}, {total_km:.1f} km)")
        print(f"==========================================================================")
        
        for i, t in enumerate(day_trips):
            t_start = t["start_dt"].strftime("%H:%M")
            t_end = t["end_dt"].strftime("%H:%M")
            dur = t["dur_min"]
            dist = t["dist_km"]
            s_place = t["start_place"]
            e_place = t["end_place"]
            
            dwell_str = ""
            if i < len(day_trips) - 1:
                next_start = day_trips[i+1]["start_dt"]
                d_mins = int((next_start - t["end_dt"]).total_seconds() / 60)
                if d_mins >= 0:
                    dh, dm = divmod(d_mins, 60)
                    dwell_str = f" [Parked for {f'{dh}h ' if dh else ''}{dm}m until {next_start.strftime('%H:%M')}]"
                    
            f_tag, _ = analyzer.get_trip_footage_summary(t)
            
            print(f" [{i+1}] {t_start} ➔ {t_end} ({dur}m, {dist:.1f} km): {s_place} ➔ {e_place}{dwell_str}")
            print(f"     └─ Footage: {f_tag}")

        print(f"--------------------------------------------------------------------------")
        if not sys.stdin.isatty():
            # Non-interactive mode: print footage for all trips
            for t in day_trips:
                display_footage_details(t, analyzer)
            break
            
        try:
            choice = input(f"Select Trip [1-{len(day_trips)}] for exact footage, [a]ll, [b]ack, [q]uit: ").strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["b", "back"]:
                break
            elif choice in ["a", "all"]:
                for t in day_trips:
                    display_footage_details(t, analyzer)
            elif choice.isdigit() and 1 <= int(choice) <= len(day_trips):
                display_footage_details(day_trips[int(choice)-1], analyzer)
            else:
                print("Invalid choice.")
        except (KeyboardInterrupt, EOFError):
            break

def display_days_menu(days_dict, title, analyzer):
    """Level 1: Display days summary table and allow drilling down to any day."""
    sorted_days = sorted(days_dict.keys(), reverse=True)
    
    while True:
        total_trips = sum(len(v) for v in days_dict.values())
        print(f"\n==========================================================================")
        print(f" 📍 {title} ({total_trips} Trips Across {len(sorted_days)} Days)")
        print(f"==========================================================================")
        
        for idx, d_str in enumerate(sorted_days):
            day_trips = days_dict[d_str]
            dt_obj = datetime.strptime(d_str, "%Y-%m-%d")
            total_km = sum(t["dist_km"] for t in day_trips)
            total_mins = sum(t["dur_min"] for t in day_trips)
            hours, mins = divmod(total_mins, 60)
            time_str = f"{hours}h {mins:02d}m" if hours else f"{mins}m"
            
            # Aggregate footage types across day
            day_cats = set()
            for t in day_trips:
                _, cats = analyzer.get_trip_footage_summary(t)
                day_cats.update(cats)
                
            if day_cats:
                f_summary = "✔ " + " + ".join(sorted(list(day_cats))) + " footage"
            else:
                f_summary = "No local footage"
                
            print(f" [{idx+1:>2}] {dt_obj.strftime('%a %d %b %Y')}: {len(day_trips):>2} trip(s) ({time_str:>6}, {total_km:>5.1f} km) | {f_summary}")

        print(f"--------------------------------------------------------------------------")
        if not sys.stdin.isatty():
            # In non-interactive mode, expand the most recent day or all
            drill_down_day(sorted_days[0], days_dict[sorted_days[0]], analyzer)
            break
            
        try:
            choice = input(f"Select Day [1-{len(sorted_days)}] to drill down, [a]ll, [q]uit: ").strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["a", "all"]:
                for d_str in sorted_days:
                    drill_down_day(d_str, days_dict[d_str], analyzer)
            elif choice.isdigit() and 1 <= int(choice) <= len(sorted_days):
                selected_day = sorted_days[int(choice)-1]
                drill_down_day(selected_day, days_dict[selected_day], analyzer)
            else:
                print("Invalid choice.")
        except (KeyboardInterrupt, EOFError):
            break

def main():
    parser = argparse.ArgumentParser(description="Tessie Drive Log Analyzer & Master Consolidator")
    parser.add_argument("--drives", action="store_true", help="Analyze and inspect drives")
    parser.add_argument("--today", action="store_true", help="Inspect only today's drives")
    parser.add_argument("--yesterday", action="store_true", help="Inspect only yesterday's drives")
    parser.add_argument("--since", help="Filter drives since date or day name (e.g. 'wednesday', '2026-09-02')")
    parser.add_argument("--days", type=int, help="Filter drives from past N days")
    parser.add_argument("--place", help="Filter drives by place nickname (e.g. 'School', 'Swimming', 'Activity')")
    parser.add_argument("--tessie-dir", help="Custom path to directory containing Tessie CSV exports")
    
    args = parser.parse_args()

    analyzer = TessieAnalyzer(tessie_dir=args.tessie_dir)
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

    if not filtered:
        print("No drives matched your filter criteria.")
        return

    # Group by day
    days_dict = defaultdict(list)
    for d in filtered:
        d_key = d["start_dt"].strftime("%Y-%m-%d")
        days_dict[d_key].append(d)

    title = args.place if args.place else ("Drives Since " + cutoff_dt.strftime('%Y-%m-%d') if cutoff_dt else "All Drive History")
    display_days_menu(days_dict, title, analyzer)

if __name__ == "__main__":
    main()
