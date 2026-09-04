#!/usr/bin/env python3
"""
Tessie Drive Log Analyzer & Master Consolidator
===============================================
- Multi-level drill-down: Place ➔ Days ➔ Trips ➔ Camera Footage
- Visual footage indicators:
    🔄 Recent (Continuous Driving Loop)
    💾 Saved (Honks & Dashcam Taps)
    🔴 Sentry (Sentry Alert Events)
    No local footage (Missing / not archived)
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
import unicodedata
from datetime import datetime, timedelta
from collections import defaultdict, Counter

EMOJI_MAP = {
    "Recent": "🔄 Recent",
    "Saved": "💾 Saved",
    "Sentry": "🔴 Sentry"
}

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
        if c in ('🔄', '💾', '🔴', '🚗', '📹', '📂', '🚪', '⚠️', '✔', '❌', '🕒', '📅', '📍', '🛑'):
            return 2
        if c in ('🅿',):
            return 1
        w = unicodedata.east_asian_width(c)
        if w in ('W', 'F'):
            return 2
        return 1

def display_len(s):
    return sum(char_width(c) for c in s)

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

def wrap_text_display(s, max_width):
    if display_len(s) <= max_width:
        return [s]
    if "; " in s:
        parts = s.split("; ")
        lines = []
        for p in parts:
            lines.extend(wrap_text_display(p, max_width))
        return lines
    words = s.split(" ")
    lines = []
    current = ""
    for w in words:
        candidate = f"{current} {w}".strip() if current else w
        if display_len(candidate) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines or [s]

def format_duration_short(mins):
    if not mins or mins == 0:
        return "-"
    if mins < 60:
        return f"{mins}m"
    h, m = divmod(mins, 60)
    if m == 0:
        return f"{h}h"
    return f"{h}h {m}m"

def clean_event_reason(reason):
    if not reason:
        return "event"
    r = reason.strip()
    prefixes = [
        "sentry_aware_",
        "user_interaction_dashcam_launcher_",
        "user_interaction_dashcam_",
        "user_interaction_",
        "vehicle_"
    ]
    for p in prefixes:
        if r.startswith(p):
            r = r[len(p):]
    return r

def format_footage_tag(cats):
    if not cats:
        return "No local footage"
    # Order: Recent, Saved, Sentry
    ordered_cats = [c for c in ["Recent", "Saved", "Sentry"] if c in cats]
    parts = [EMOJI_MAP.get(c, c) for c in ordered_cats]
    return " + ".join(parts) + " footage"

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

    for fmt in ["%Y%m%d", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%d %H:%M"]:
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
            try:
                pf = os.path.join(td, "places.json")
                if os.path.isfile(pf):
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
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except Exception:
            pass
        master_file = os.path.join(dest_dir, "drives_master.csv")

        raw_rows = []
        seen_keys = set()
        fieldnames = None

        all_csvs = []
        for td in self.tessie_dirs:
            try:
                if os.path.isdir(td):
                    for f in os.listdir(td):
                        if f.endswith(".csv"):
                            all_csvs.append(os.path.join(td, f))
            except Exception:
                pass

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

        try:
            with open(master_file, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(raw_rows)
        except Exception:
            pass

        return len(raw_rows)

    def load_drives(self):
        self.consolidate_drives()
        master_file = None
        for td in self.tessie_dirs:
            try:
                mf = os.path.join(td, "drives_master.csv")
                if os.path.isfile(mf):
                    master_file = mf
                    break
            except Exception:
                pass

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
        """Returns formatted string tag and set of categories."""
        start_clips = self.find_footage(trip["start_dt"], 120)
        end_clips = self.find_footage(trip["end_dt"], 180)
        all_clips = start_clips + end_clips
        if not all_clips:
            return "No local footage", set()
        
        cats = set(c["cat"] for c in all_clips)
        return format_footage_tag(cats), cats

    def get_timeline_data(self, target_date):
        """Build event-driven timeline for target_date alternating seamlessly between parked and driving states."""
        self.index_footage()
        now = datetime.now()
        day_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
        day_end = day_start + timedelta(days=1)
        
        # 1. Check if date is in the future
        if day_start.date() > now.date():
            return [], day_start, "Future date — no telemetry or drive data recorded."
            
        # 2. Check if date is before earliest recorded drive
        if self.drives and day_end <= self.drives[0]["start_dt"]:
            first_drive_str = self.drives[0]["start_dt"].strftime("%Y-%m-%d")
            return [], day_start, f"No drive data recorded prior to earliest record ({first_drive_str})."

        is_today = (day_start.date() == now.date())
        effective_end = min(day_end, now) if is_today else day_end

        # Drives on target date (including any overlapping start/end)
        day_drives = [
            d for d in self.drives
            if d["start_dt"] < day_end and d["end_dt"] > day_start
        ]
        day_drives.sort(key=lambda x: x["start_dt"])
        
        # Determine initial location at 00:00
        prev_drives = [d for d in self.drives if d["end_dt"] <= day_start]
        current_location = prev_drives[-1]["end_place"] if prev_drives else (self.drives[0]["start_place"] if self.drives else "Unknown")
        
        events = []
        cursor_time = day_start
        event_idx = 1
        
        for d in day_drives:
            d_start = max(d["start_dt"], day_start)
            d_end = min(d["end_dt"], day_end)
            
            # 1. Parked period before this drive (if cursor_time < d_start)
            if d_start > cursor_time:
                park_dur_mins = int((d_start - cursor_time).total_seconds() / 60)
                dur_str = format_duration_short(park_dur_mins) if park_dur_mins > 0 else "<1m"
                s_str = cursor_time.strftime("%H:%M")
                e_str = d_start.strftime("%H:%M")
                events.append({
                    "event_idx": event_idx,
                    "type": "parked",
                    "start_dt": cursor_time,
                    "end_dt": d_start,
                    "time_str": f"{s_str} - {e_str}",
                    "location": current_location,
                    "activity": f"🅿  {current_location} ({dur_str})",
                    "drive": None
                })
                event_idx += 1
                
            # 2. Driving period
            drive_dur_mins = int((d_end - d_start).total_seconds() / 60)
            dur_str = format_duration_short(drive_dur_mins) if drive_dur_mins > 0 else "<1m"
            dist_str = f"{d['dist_km']:.1f} km"
            s_str = d_start.strftime("%H:%M")
            e_str = "24:00" if d_end == day_end else d_end.strftime("%H:%M")
            events.append({
                "event_idx": event_idx,
                "type": "drive",
                "start_dt": d_start,
                "end_dt": d_end,
                "time_str": f"{s_str} - {e_str}",
                "location": d["end_place"],
                "activity": f"🚗 {d['start_place']} ➔ {d['end_place']} ({dist_str}, {dur_str})",
                "drive": d
            })
            event_idx += 1
            current_location = d["end_place"]
            cursor_time = d_end
            
        # 3. Final parked period (or entire day if no drives)
        if cursor_time < effective_end:
            park_dur_mins = int((effective_end - cursor_time).total_seconds() / 60)
            dur_str = format_duration_short(park_dur_mins) if park_dur_mins > 0 else "<1m"
            s_str = cursor_time.strftime("%H:%M")
            
            if is_today:
                e_str = "Now"
                time_str = f"{s_str} - {e_str}"
                act_str = f"🅿  {current_location} ({dur_str})"
            elif not day_drives:
                time_str = "00:00 - 24:00"
                act_str = f"🅿  {current_location} (Stationary all day - 24h)"
            else:
                e_str = "24:00"
                time_str = f"{s_str} - {e_str}"
                act_str = f"🅿  {current_location} ({dur_str})"

            events.append({
                "event_idx": event_idx,
                "type": "parked",
                "start_dt": cursor_time,
                "end_dt": effective_end,
                "time_str": time_str,
                "location": current_location,
                "activity": act_str,
                "drive": None
            })
            event_idx += 1
            
        # Attach footage to each event
        for ev in events:
            s_dt = ev["start_dt"]
            e_dt = ev["end_dt"]
            clips = [c for c in self.footage_db if s_dt <= c["dt"] < e_dt]
            ev["recent_mins"] = len(set(c["dt"] for c in clips if c["cat"] == "Recent"))
            ev["saved_mins"] = len(set(c["dt"] for c in clips if c["cat"] == "Saved"))
            ev["sentry_mins"] = len(set(c["dt"] for c in clips if c["cat"] == "Sentry"))
            ev["clips"] = clips
            
        return events, day_start, None

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
    
def render_footage_listing(clips, indent=""):
    by_cat = defaultdict(list)
    for c in clips:
        by_cat[c['cat']].append(c)
        
    for cat in ['Recent', 'Saved', 'Sentry', 'Other']:
        if cat not in by_cat:
            continue
        cat_clips = by_cat[cat]
        icon = '🔄' if cat == 'Recent' else ('💾' if cat == 'Saved' else ('🔴' if cat == 'Sentry' else '📁'))
        
        if cat == 'Recent':
            folder_path = os.path.dirname(cat_clips[0]['path'])
            print(f"\n{indent}📂 [{icon} RecentClips] {folder_path}")
            by_ts = sorted(list(set(c['dt'] for c in cat_clips)))
            for dt in by_ts:
                ts_str = dt.strftime('%H:%M:%S')
                f_pattern = dt.strftime('%Y-%m-%d_%H-%M-%S-*.mp4')
                print(f"{indent}   • {ts_str} ➔ {f_pattern}")
        else:
            base_dir = os.path.dirname(os.path.dirname(cat_clips[0]['path']))
            folder_cat_name = f"{cat}Clips"
            print(f"\n{indent}📂 [{icon} {folder_cat_name}] {base_dir}")
            
            by_folder = defaultdict(list)
            for c in cat_clips:
                by_folder[c['folder']].append(c)
                
            for folder, f_clips in sorted(by_folder.items()):
                folder_name = os.path.basename(folder)
                ev_json = os.path.join(folder, 'event.json')
                reason = 'event'
                event_ts_str = None
                if os.path.exists(ev_json):
                    try:
                        with open(ev_json) as fp:
                            data = json.load(fp)
                            reason = clean_event_reason(data.get('reason', 'event'))
                            if 'timestamp' in data:
                                event_ts_str = data['timestamp'].split('T')[-1]
                    except Exception:
                        pass
                
                unique_dts = sorted(list(set(c['dt'] for c in f_clips)))
                num_clips = len(unique_dts)
                dur_m = num_clips
                
                if not event_ts_str:
                    event_ts_str = unique_dts[0].strftime('%H:%M:%S') if unique_dts else folder_name.split('_')[-1].replace('-', ':')
                
                date_prefix = unique_dts[0].strftime('%Y-%m-%d') if unique_dts else folder_name.split('_')[0]
                events_word = "event" if num_clips == 1 else "events"
                print(f"{indent}   • {event_ts_str} ({reason}) ➔ {folder_name}/{date_prefix}_*.mp4 ({num_clips} {events_word}, {dur_m}m)")

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
        render_footage_listing(start_clips, indent="  ")
    else:
        print("   ⚠️ No local footage found on archive drives.")

    # 2. Exit footage (arriving & unloading)
    print(f"\n🚪 2. EXIT WINDOW (Arrival ~{t_end.strftime('%H:%M:%S')}):")
    if end_clips:
        render_footage_listing(end_clips, indent="  ")
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
            for t in day_trips:
                display_footage_details(t, analyzer)
            break
            
        try:
            choice = input(f"Select Trip [1-{len(day_trips)}] for footage, [t]imeline, [a]ll, [b]ack, [q]uit: ").strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["b", "back"]:
                break
            elif choice in ["t", "timeline"]:
                display_timeline(dt_obj, analyzer)
            elif choice in ["a", "all"]:
                for t in day_trips:
                    display_footage_details(t, analyzer)
            elif choice.isdigit() and 1 <= int(choice) <= len(day_trips):
                display_footage_details(day_trips[int(choice)-1], analyzer)
            else:
                print("Invalid choice.")
        except (KeyboardInterrupt, EOFError):
            break

def display_timeline_event_details(event, analyzer):
    """Level 3 for Timeline: Detailed listing of all footage within a specific event."""
    t_start = event["start_dt"]
    clips = event["clips"]
    
    print(f"\n==========================================================================")
    print(f" 📹 TIMELINE FOOTAGE LISTING: {t_start.strftime('%a %d %b %Y')} ({event['time_str']})")
    print(f"    Activity : {event['activity']}")
    print(f"    Clips    : Recent: {event['recent_mins']}m | Saved: {event['saved_mins']}m | Sentry: {event['sentry_mins']}m")
    print(f"==========================================================================")
    
    if not clips:
        print("\n⚠️ No local camera footage found for this event window.")
        print("--------------------------------------------------------------------------")
        return
        
    render_footage_listing(clips)
            
    print(f"\n💡 Quick Tips:")
    print(f"   • Open first folder in Finder: open \"{clips[0]['folder']}\"")
    print(f"--------------------------------------------------------------------------")

def display_timeline(target_date, analyzer, compact=False):
    """24-Hour Event Timeline: Vehicle state & footage breakdown across parked/driving events."""
    curr_date = target_date
    w_idx = 5
    w_time = 15
    w_rec = 6
    w_sav = 6
    w_sen = 6

    while True:
        events, day_start, status_msg = analyzer.get_timeline_data(curr_date)
        title = f" 24-Hour Event Timeline: {day_start.strftime('%A, %d %B %Y')}"
        
        if not events:
            total_inner = 70
            msg = status_msg or "No telemetry or footage records found for this date."
            print(f"┌{'─'*total_inner}┐")
            print(f"│{title:<{total_inner}}│")
            print(f"├{'─'*total_inner}┤")
            print(f"│ {msg:<{total_inner-1}}│")
            print(f"└{'─'*total_inner}┘")
            
            if not sys.stdin.isatty():
                break
                
            try:
                choice = input("Navigation: [p]rev day, [n]ext day, [b]ack, [q]uit: ").strip().lower()
                if choice == "q":
                    sys.exit(0)
                elif choice in ["b", "back"]:
                    break
                elif choice in ["p", "prev"]:
                    curr_date = curr_date - timedelta(days=1)
                elif choice in ["n", "next"]:
                    curr_date = curr_date + timedelta(days=1)
                else:
                    print("Invalid choice.")
                continue
            except (KeyboardInterrupt, EOFError):
                break

        # Expand w_act dynamically so that no route or location line gets truncated or broken
        max_act_len = max((display_len(ev["activity"]) for ev in events), default=50)
        w_act = max(60, max_act_len + 3)
        total_inner = w_idx + w_time + w_rec + w_sav + w_sen + w_act + 5
        
        total_recent_mins = sum(ev["recent_mins"] for ev in events)
        total_saved_mins = sum(ev["saved_mins"] for ev in events)
        total_sentry_mins = sum(ev["sentry_mins"] for ev in events)
        has_any_footage = (total_recent_mins + total_saved_mins + total_sentry_mins) > 0
        
        h_idx = f" {'#':^3} "
        h_time = f" {'Time Window':<13} "
        h_rec = pad_display("🔄", w_rec, "center")
        h_sav = pad_display("💾", w_sav, "center")
        h_sen = pad_display("🔴", w_sen, "center")
        h_act = f" {'Vehicle State & Route / Location':<{w_act-2}} "

        print(f"┌{'─'*total_inner}┐")
        print(f"│{title:<{total_inner}}│")
        print(f"├{'─'*w_idx}┬{'─'*w_time}┬{'─'*w_rec}┬{'─'*w_sav}┬{'─'*w_sen}┬{'─'*w_act}┤")
        print(f"│{h_idx}│{h_time}│{h_rec}│{h_sav}│{h_sen}│{h_act}│")
        print(f"├{'─'*w_idx}┼{'─'*w_time}┼{'─'*w_rec}┼{'─'*w_sav}┼{'─'*w_sen}┼{'─'*w_act}┤")
        
        for ev in events:
            r_str = format_duration_short(ev['recent_mins'])
            s_str = format_duration_short(ev['saved_mins'])
            sn_str = format_duration_short(ev['sentry_mins'])
            
            c_idx = f" [{ev['event_idx']:>2}]"
            c_time = f" {ev['time_str']:<13} "
            c_rec = f"{r_str:^6}"
            c_sav = f"{s_str:^6}"
            c_sen = f"{sn_str:^6}"
            
            act_lines = wrap_text_display(ev["activity"].strip(), w_act - 2)
            first_act = pad_display(" " + act_lines[0], w_act, "left")
            print(f"│{c_idx}│{c_time}│{c_rec}│{c_sav}│{c_sen}│{first_act}│")
            
            for extra in act_lines[1:]:
                e_idx = " " * w_idx
                e_time = " " * w_time
                e_rec = " " * w_rec
                e_sav = " " * w_sav
                e_sen = " " * w_sen
                e_act = pad_display("   " + extra, w_act, "left")
                print(f"│{e_idx}│{e_time}│{e_rec}│{e_sav}│{e_sen}│{e_act}│")
            
        print(f"├{'─'*w_idx}┴{'─'*w_time}┴{'─'*w_rec}┴{'─'*w_sav}┴{'─'*w_sen}┴{'─'*w_act}┤")
        if not has_any_footage:
            foot_msg = " No local footage on archive SSD. Run 'tesla_sync.sh' to backup from car."
            print(f"│{foot_msg:<{total_inner}}│")
        else:
            rec_tot = format_duration_short(total_recent_mins)
            sav_tot = format_duration_short(total_saved_mins)
            sen_tot = format_duration_short(total_sentry_mins)
            foot_msg = f" Footage Totals: 🔄 {rec_tot} | 💾 {sav_tot} | 🔴 {sen_tot}"
            foot_pad = pad_display(foot_msg, total_inner, "left")
            print(f"│{foot_pad}│")
        print(f"└{'─'*total_inner}┘")
            
        if not sys.stdin.isatty():
            break
            
        try:
            prompt_str = f"Select Event [1-{len(events)} or HH:MM] for footage, [p]rev day, [n]ext day, [b]ack, [q]uit: "
            choice = input(prompt_str).strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["b", "back"]:
                break
            elif choice in ["p", "prev"]:
                curr_date = curr_date - timedelta(days=1)
            elif choice in ["n", "next"]:
                curr_date = curr_date + timedelta(days=1)
            else:
                matched_ev = None
                if choice.isdigit():
                    val = int(choice)
                    for ev in events:
                        if ev["event_idx"] == val:
                            matched_ev = ev
                            break
                elif ":" in choice:
                    time_part = choice.replace(".", ":")
                    for ev in events:
                        if ev["start_dt"].strftime("%H:%M") == time_part or time_part in ev["time_str"]:
                            matched_ev = ev
                            break
                if matched_ev:
                    display_timeline_event_details(matched_ev, analyzer)
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
            
            day_cats = set()
            for t in day_trips:
                _, cats = analyzer.get_trip_footage_summary(t)
                day_cats.update(cats)
                
            f_summary = format_footage_tag(day_cats)
                
            print(f" [{idx+1:>2}] {dt_obj.strftime('%a %d %b %Y')}: {len(day_trips):>2} trip(s) ({time_str:>6}, {total_km:>5.1f} km) | {f_summary}")

        print(f"--------------------------------------------------------------------------")
        if not sys.stdin.isatty():
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
    parser.add_argument("--timeline", nargs="?", const="today", default=None, help="Generate 24-hour event-driven vehicle & camera activity timeline for a date (e.g. today, yesterday, 2026-09-02, wednesday)")
    parser.add_argument("--today", action="store_true", help="Inspect only today's drives")
    parser.add_argument("--yesterday", action="store_true", help="Inspect only yesterday's drives")
    parser.add_argument("--since", help="Filter drives since date or day name (e.g. 'wednesday', '2026-09-02')")
    parser.add_argument("--days", type=int, help="Filter drives from past N days")
    parser.add_argument("--place", help="Filter drives by place nickname (e.g. 'School', 'Work', 'Gym')")
    parser.add_argument("--tessie-dir", help="Custom path to directory containing Tessie CSV exports")
    
    args = parser.parse_args()

    analyzer = TessieAnalyzer(tessie_dir=args.tessie_dir)
    drives = analyzer.load_drives()

    if not drives:
        print("No Tessie drive records found. Please ensure CSVs exist in iCloud or Tessie/.")
        sys.exit(0)

    if args.timeline is not None:
        target_date = parse_relative_date(args.timeline)
        if not target_date:
            print(f"Error: Could not parse date '{args.timeline}'. Supported formats: YYYYMMDD, YYYY-MM-DD, today, yesterday, weekday name.")
            sys.exit(1)
        display_timeline(target_date, analyzer)
        return

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

    days_dict = defaultdict(list)
    for d in filtered:
        d_key = d["start_dt"].strftime("%Y-%m-%d")
        days_dict[d_key].append(d)

    title = args.place if args.place else ("Drives Since " + cutoff_dt.strftime('%Y-%m-%d') if cutoff_dt else "All Drive History")
    display_days_menu(days_dict, title, analyzer)

if __name__ == "__main__":
    main()
