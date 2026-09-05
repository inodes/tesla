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
import re
import csv
import glob
import json
import math
import shutil
import tempfile
import subprocess
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
    return r.replace("_", " ")

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

class TessieAnalyzer:
    def __init__(self, tessie_dir=None, teslacam_dirs=None):
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(self.script_dir)
        self.icloud_dir = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie")
        self.repo_root = parent_dir
        self.config = {}
        for cfg_p in [
            os.path.join(parent_dir, "config.json"),
            os.path.join(parent_dir, "Tessie", "config.json"),
            os.path.join(self.script_dir, "config.json"),
            os.path.join(self.script_dir, "Tessie", "config.json")
        ]:
            if os.path.exists(cfg_p):
                try:
                    with open(cfg_p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            self.config = data
                            break
                except Exception:
                    pass

        cfg_landing = self.config.get("landing_directory") or self.config.get("landing_dir")
        self.landing_dir = os.path.expanduser(cfg_landing) if cfg_landing else os.path.expanduser("~/Downloads")
        
        self.tessie_dirs = []
        candidates = [
            tessie_dir,
            os.path.join(parent_dir, "Tessie"),
            os.path.join(self.script_dir, "Tessie"),
            os.path.expanduser("~/iCloud/repos/tesla/Tessie"),
            self.icloud_dir
        ] + find_mounted_tesla_volumes("Tessie")
        seen_tessie = set()
        for d in candidates:
            try:
                if d and os.path.isdir(d):
                    real_d = os.path.abspath(os.path.realpath(d))
                    if real_d not in seen_tessie:
                        seen_tessie.add(real_d)
                        self.tessie_dirs.append(real_d)
            except Exception:
                pass

        cfg_archive = self.config.get("archive_directory") or self.config.get("archive_dir")
        if cfg_archive:
            self.archive_dir = os.path.abspath(os.path.expanduser(cfg_archive))
        elif self.tessie_dirs:
            self.archive_dir = os.path.join(self.tessie_dirs[0], "archive")
        else:
            self.archive_dir = os.path.join(self.repo_root, "Tessie", "archive")
        try:
            os.makedirs(self.archive_dir, exist_ok=True)
        except Exception:
            pass
        
        self.teslacam_dirs = []
        if teslacam_dirs:
            candidates_tc = teslacam_dirs if isinstance(teslacam_dirs, list) else [teslacam_dirs]
        else:
            candidates_tc = find_mounted_tesla_volumes("TeslaCam")
            
        seen_tc = set()
        for d in candidates_tc:
            try:
                if d and os.path.isdir(d):
                    real_d = os.path.abspath(os.path.realpath(d))
                    if real_d not in seen_tc:
                        seen_tc.add(real_d)
                        self.teslacam_dirs.append(real_d)
            except Exception:
                pass
        
        self.places = {}
        self.charging_places = {}
        self.supercharger_places = {}
        self.load_places()
        self.drives = []
        self.footage_db = []
        self._indexed = False

    def load_places(self):
        self.places = {}
        self.charging_places = {}
        self.supercharger_places = {}
        for td in self.tessie_dirs:
            for fname, target_dict in [
                ("places.json", self.places),
                ("charging.json", self.charging_places),
                ("superchargers.json", self.supercharger_places)
            ]:
                try:
                    pf = os.path.join(td, fname)
                    if os.path.isfile(pf):
                        with open(pf, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            if isinstance(data, dict):
                                target_dict.update(data)
                except Exception:
                    pass
        return self.places

    def resolve_place(self, address, saved_loc="", lat=None, lon=None):
        # 1. Saved Location exact or keyword match
        if saved_loc and saved_loc.strip():
            s_clean = saved_loc.strip()
            if s_clean in self.places:
                return s_clean
            for place_name, p_info in self.places.items():
                if not isinstance(p_info, dict):
                    continue
                kws = p_info.get("keywords") or []
                if s_clean.lower() == place_name.lower():
                    return place_name
                for kw in kws:
                    if kw and isinstance(kw, str) and kw.strip() and kw.strip().lower() in s_clean.lower():
                        return place_name
            return s_clean

        addr_clean = address.lower() if address else ""

        # 2. GPS Geofence matching on known places (Highest Accuracy!)
        if lat is not None and lon is not None:
            best_place = None
            min_dist = float("inf")
            for place_name, p_info in self.places.items():
                if not isinstance(p_info, dict):
                    continue
                loc = p_info.get("location", {})
                p_lat = p_info.get("lat") if p_info.get("lat") is not None else loc.get("lat")
                p_lon = p_info.get("lon") if p_info.get("lon") is not None else loc.get("lon")
                p_rad = p_info.get("radius_m") if p_info.get("radius_m") is not None else loc.get("radius_m", 250)
                if p_lat is not None and p_lon is not None:
                    dist = haversine_distance_m(lat, lon, p_lat, p_lon)
                    if dist <= p_rad and dist < min_dist:
                        min_dist = dist
                        best_place = p_info.get("tesla_metadata", {}).get("short_name") or p_info.get("location_name") or p_info.get("nickname") or place_name
            if best_place:
                return best_place

        # 3. GPS Geofence matching on charging profiles
        if lat is not None and lon is not None:
            best_charge = None
            min_dist = float("inf")
            for c_name, c_info in self.charging_places.items():
                if not isinstance(c_info, dict):
                    continue
                loc = c_info.get("location", {})
                p_lat = c_info.get("lat") if c_info.get("lat") is not None else loc.get("lat")
                p_lon = c_info.get("lon") if c_info.get("lon") is not None else loc.get("lon")
                p_rad = c_info.get("radius_m") if c_info.get("radius_m") is not None else loc.get("radius_m", 250)
                if p_lat is not None and p_lon is not None:
                    dist = haversine_distance_m(lat, lon, p_lat, p_lon)
                    if dist <= p_rad and dist < min_dist:
                        min_dist = dist
                        best_charge = c_info.get("tesla_metadata", {}).get("short_name") or c_info.get("location_name") or c_name
            if best_charge:
                return best_charge

        # 4. GPS Geofence matching on Superchargers
        if lat is not None and lon is not None:
            best_sc = None
            min_dist = float("inf")
            for sc_name, sc_info in self.supercharger_places.items():
                if not isinstance(sc_info, dict):
                    continue
                loc = sc_info.get("location", {})
                p_lat = sc_info.get("lat") if sc_info.get("lat") is not None else loc.get("lat")
                p_lon = sc_info.get("lon") if sc_info.get("lon") is not None else loc.get("lon")
                p_rad = sc_info.get("radius_m") if sc_info.get("radius_m") is not None else loc.get("radius_m", 250)
                if p_lat is not None and p_lon is not None:
                    dist = haversine_distance_m(lat, lon, p_lat, p_lon)
                    if dist <= p_rad and dist < min_dist:
                        min_dist = dist
                        best_sc = sc_info.get("tesla_metadata", {}).get("short_name") or sc_info.get("location_name") or sc_name
            if best_sc:
                return best_sc

        # 5. Strict Keyword matching on known places (sorted by length descending)
        all_kws = []
        for place_name, p_info in self.places.items():
            if not isinstance(p_info, dict):
                continue
            kws = p_info.get("keywords") or p_info.get("tesla_metadata", {}).get("keywords") or []
            target_name = p_info.get("tesla_metadata", {}).get("short_name") or p_info.get("location_name") or p_info.get("nickname") or place_name
            for kw in kws:
                if kw and isinstance(kw, str) and kw.strip() and len(kw.strip()) >= 3:
                    all_kws.append((len(kw.strip()), kw.strip().lower(), target_name))
        all_kws.sort(key=lambda x: x[0], reverse=True)
        for _, kw_clean, target_name in all_kws:
            if kw_clean in addr_clean:
                return target_name

        # 6. Fallback: first part of street address
        parts = address.split(",")
        return parts[0].strip() if parts else address

    def ingest_and_archive_drives(self):
        """
        Loads drives_master.csv first as the definitive source of truth.
        Scans landing_dir and tessie_dirs for incoming drive CSVs:
          - Genuinely new drive sessions (not present in master) are appended to master.
          - Existing/overlapping sessions are ignored so master's verified records are preserved.
          - Ingested candidate CSV files are moved to the archive directory.
        """
        master_file = None
        for td in self.tessie_dirs:
            p = os.path.join(td, "drives_master.csv")
            if os.path.isfile(p):
                master_file = p
                break
        if not master_file:
            target_dir = self.tessie_dirs[0] if self.tessie_dirs else os.path.join(self.repo_root, "Tessie")
            master_file = os.path.join(target_dir, "drives_master.csv")

        master_rows = []
        master_keys = set()
        master_fieldnames = None

        if os.path.isfile(master_file):
            try:
                with open(master_file, "r", encoding="utf-8-sig", errors="ignore") as f:
                    reader = csv.DictReader(f)
                    master_fieldnames = list(reader.fieldnames or [])
                    for row in reader:
                        s_at = (row.get("Started At (AEST)") or row.get("Started At (AEDT)") or row.get("Started At") or row.get("Started") or "").strip()
                        e_at = (row.get("Ended At (AEST)") or row.get("Ended At (AEDT)") or row.get("Ended At") or row.get("Ended") or "").strip()
                        s_loc = (row.get("Starting Location") or "").strip()
                        if s_at and e_at:
                            master_keys.add((s_at, e_at, s_loc))
                        master_rows.append(row)
            except Exception:
                pass

        search_dirs = []
        if self.landing_dir and os.path.isdir(self.landing_dir):
            search_dirs.append(self.landing_dir)
        for td in self.tessie_dirs:
            if td and os.path.isdir(td) and td not in search_dirs:
                search_dirs.append(td)

        candidate_files = []
        for s_dir in search_dirs:
            try:
                fnames = sorted(os.listdir(s_dir))
            except Exception:
                continue
            for fn in fnames:
                if not fn.endswith(".csv") or fn.startswith(".") or fn == "charges_master.csv" or fn == "drives_master.csv":
                    continue
                if any(x in fn for x in ["telemetry_stream", "charge_deepdive", "drive_deepdive", "battery_health", "tire_pressure", "firmware_alerts", "idles_summary"]):
                    continue
                fp = os.path.join(s_dir, fn)
                try:
                    with open(fp, "r", encoding="utf-8-sig", errors="ignore") as test_f:
                        first_line = test_f.readline()
                        if "Starting Location" in first_line and "Distance (km)" in first_line:
                            candidate_files.append(fp)
                except Exception:
                    pass

        os.makedirs(self.archive_dir, exist_ok=True)
        master_modified = False

        for c_fp in candidate_files:
            c_name = os.path.basename(c_fp)
            try:
                new_in_file = 0
                ignored_in_file = 0
                with open(c_fp, "r", encoding="utf-8-sig", errors="ignore") as in_f:
                    reader = csv.DictReader(in_f)
                    if not master_fieldnames and reader.fieldnames:
                        master_fieldnames = list(reader.fieldnames)
                    for r in reader:
                        s_at = (r.get("Started At (AEST)") or r.get("Started At (AEDT)") or r.get("Started At") or r.get("Started") or "").strip()
                        e_at = (r.get("Ended At (AEST)") or r.get("Ended At (AEDT)") or r.get("Ended At") or r.get("Ended") or "").strip()
                        s_loc = (r.get("Starting Location") or "").strip()
                        if not s_at or not e_at:
                            continue
                        key = (s_at, e_at, s_loc)
                        if key in master_keys:
                            ignored_in_file += 1
                        else:
                            master_keys.add(key)
                            master_rows.append(r)
                            new_in_file += 1
                            master_modified = True

                if new_in_file > 0:
                    print(f"\033[94m📥 Ingested:\033[0m Added {new_in_file} new drive(s) from '{c_name}' into drives_master.csv (ignored {ignored_in_file} existing master records)")
                else:
                    print(f"\033[90mℹ️  [Ingest] '{c_name}' has {ignored_in_file} drives (all already present in drives_master.csv)\033[0m")

                ts = datetime.now().strftime("%Y%m%d%H%M")
                dst_name = f"{c_name}.{ts}"
                dst_path = os.path.join(self.archive_dir, dst_name)
                shutil.move(c_fp, dst_path)
                print(f"\033[94m📦 Archived:\033[0m {c_name} ➔ archive/{dst_name}")

            except Exception:
                pass

        if master_rows and (master_modified or not os.path.isfile(master_file)):
            try:
                master_rows.sort(key=lambda r: (r.get("Started At (AEST)") or r.get("Started At (AEDT)") or r.get("Started At") or r.get("Started") or ""))
                temp_fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(master_file), text=True)
                with os.fdopen(temp_fd, "w", encoding="utf-8", newline="") as out_f:
                    writer = csv.DictWriter(out_f, fieldnames=master_fieldnames or master_rows[0].keys())
                    writer.writeheader()
                    writer.writerows(master_rows)
                os.replace(temp_path, master_file)
                print(f"\033[92m✔ Updated {os.path.basename(master_file)} ({len(master_rows)} total records)\033[0m")
            except Exception:
                pass

        return master_rows, master_file

    def consolidate_drives(self, master_dir=None):
        master_rows, _ = self.ingest_and_archive_drives()
        return len(master_rows)

    def load_drives(self):
        master_rows, master_file = self.ingest_and_archive_drives()
        parsed = []
        for r in master_rows:
            try:
                start_str = r.get("Started At (AEST)") or r.get("Started At (AEDT)") or r.get("Started At") or r.get("Started")
                end_str = r.get("Ended At (AEST)") or r.get("Ended At (AEDT)") or r.get("Ended At") or r.get("Ended")
                if not start_str or not end_str:
                    continue
                dt_start = datetime.strptime(start_str.strip()[:16], "%Y-%m-%d %H:%M")
                dt_end = datetime.strptime(end_str.strip()[:16], "%Y-%m-%d %H:%M")
                
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
        seen_rel = set()
        for tc in self.teslacam_dirs:
            try:
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
                            rel_key = os.path.normpath(os.path.join(rel, f))
                            if rel_key in seen_rel:
                                continue
                            seen_rel.add(rel_key)
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
            except Exception:
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
    
    print(f"\n┌────────────────────────────────────────────────────────────────────────┐")
    print(f"│ 📹 CAMERA FOOTAGE: {t_start.strftime('%a %d %b %Y')} ({t_start.strftime('%H:%M')} ➔ {t_end.strftime('%H:%M')})".ljust(73) + "│")
    print(f"├────────────────────────────────────────────────────────────────────────┤")
    print(f"│    Origin      : {trip['start_place']} ({trip['start_addr'].split(',')[0]})".ljust(73) + "│")
    print(f"│    Destination : {trip['end_place']} ({trip['end_addr'].split(',')[0]})".ljust(73) + "│")
    print(f"└────────────────────────────────────────────────────────────────────────┘")
    
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
    
    print(f"\n┌────────────────────────────────────────────────────────────────────────┐")
    print(f"│ 📹 CAMERA FOOTAGE: {t_start.strftime('%a %d %b %Y')} ({t_start.strftime('%H:%M')} ➔ {t_end.strftime('%H:%M')})".ljust(73) + "│")
    print(f"├────────────────────────────────────────────────────────────────────────┤")
    print(f"│    Origin      : {trip['start_place']} ({trip['start_addr'].split(',')[0]})".ljust(73) + "│")
    print(f"│    Destination : {trip['end_place']} ({trip['end_addr'].split(',')[0]})".ljust(73) + "│")
    print(f"└────────────────────────────────────────────────────────────────────────┘")
    
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
    print(f"└────────────────────────────────────────────────────────────────────────────┘")

def drill_down_day(day_str, day_trips, analyzer):
    """Level 2: Display drives for a selected day and allow picking a trip for footage listing."""
    while True:
        dt_obj = datetime.strptime(day_str, "%Y-%m-%d")
        total_km = sum(t["dist_km"] for t in day_trips)
        total_mins = sum(t["dur_min"] for t in day_trips)
        hours, mins = divmod(total_mins, 60)
        time_str = f"{hours}h {mins:02d}m" if hours else f"{mins}m"
        
        print(f"\n┌────────────────────────────────────────────────────────────────────────────┐")
        print(f"│ 📅 {dt_obj.strftime('%A, %d %B %Y')} — {len(day_trips)} Drives ({time_str}, {total_km:.1f} km)".ljust(77) + "│")
        print(f"├────────────────────────────────────────────────────────────────────────────┤")
        
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
            
            # Add borders
            from tessie_drives_analyzer import pad_display
            
            line1 = f" [{i+1}] {t_start} ➔ {t_end} ({dur}m, {dist:.1f} km): {s_place} ➔ {e_place}{dwell_str}"
            line2 = f"     └─ Footage: {f_tag}"
            
            print(f"│{pad_display(line1, 76, 'left')}│")
            print(f"│{pad_display(line2, 76, 'left')}│")

        print(f"└────────────────────────────────────────────────────────────────────────────┘")
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

def parse_selection_indices(input_str, max_idx):
    """Parses selections like '1,5,8', '6-9', '3', 'all', 'a' into a sorted list of unique 1-based indices."""
    if not input_str:
        return []
    s = input_str.strip().lower()
    if s in ("all", "a", "*"):
        return list(range(1, max_idx + 1))
    
    selected = set()
    parts = s.split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            sub = part.split("-")
            if len(sub) == 2 and sub[0].strip().isdigit() and sub[1].strip().isdigit():
                start_i = int(sub[0].strip())
                end_i = int(sub[1].strip())
                if start_i > end_i:
                    start_i, end_i = end_i, start_i
                for idx in range(start_i, end_i + 1):
                    if 1 <= idx <= max_idx:
                        selected.add(idx)
        elif part.isdigit():
            idx = int(part)
            if 1 <= idx <= max_idx:
                selected.add(idx)
    return sorted(list(selected))

def make_event_slug(ev):
    """Generates a clean, filesystem-safe folder name for exporting an event's footage."""
    dt_str = ev["start_dt"].strftime("%Y-%m-%d_%H-%M")
    if ev.get("type") == "drive" and ev.get("drive"):
        d = ev["drive"]
        raw = f"{dt_str}_Drive_{d['start_place']}_to_{d['end_place']}"
    else:
        loc = ev.get("location", "Parked")
        raw = f"{dt_str}_Parked_{loc}"
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", raw)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug

def build_timeline_footage_packages(event):
    """
    Groups raw video clips in an event into distinct selectable packages:
    - Saved / Sentry event folders (with event.json metadata, reason, trigger time)
    - Contiguous Recent clips merged into continuous time chunks separated by event triggers or gaps
    """
    clips = event.get("clips", [])
    if not clips:
        return []
        
    seen_rel = set()
    unique_clips = []
    for c in clips:
        tc_root = c["folder"].split("TeslaCam")[0] + "TeslaCam" if "TeslaCam" in c["folder"] else c["folder"]
        rel_tc = os.path.relpath(c["path"], tc_root)
        if rel_tc not in seen_rel:
            seen_rel.add(rel_tc)
            unique_clips.append(c)

    # 1. Saved and Sentry folders
    by_folder = defaultdict(list)
    for c in unique_clips:
        if c["cat"] in ("Saved", "Sentry"):
            by_folder[(c["cat"], c["folder"])].append(c)

    event_pkgs = []
    for (cat, folder), f_clips in sorted(by_folder.items()):
        ev_json = os.path.join(folder, "event.json")
        reason = "Event"
        event_ts_str = None
        event_dt = None
        if os.path.exists(ev_json):
            try:
                with open(ev_json) as fp:
                    d = json.load(fp)
                    raw_reason = d.get("reason", "event")
                    reason = clean_event_reason(raw_reason).title()
                    ts = d.get("timestamp")
                    if ts:
                        event_dt = datetime.fromisoformat(ts.split("+")[0].split(".")[0])
                        event_ts_str = event_dt.strftime("%H:%M:%S")
            except Exception:
                pass
        unique_dts = sorted(list(set(c["dt"] for c in f_clips)))
        if not event_dt and unique_dts:
            event_dt = unique_dts[0]
            event_ts_str = event_dt.strftime("%H:%M:%S")
        
        unique_cams = set(c["cam"] for c in f_clips)
        tot_size = sum(os.path.getsize(c["path"]) for c in f_clips if os.path.exists(c["path"]))
        folder_base = os.path.basename(folder)
        dur_m = len(unique_dts)
        
        all_files = []
        if os.path.exists(folder):
            for fname in os.listdir(folder):
                if not fname.startswith("."):
                    all_files.append(os.path.join(folder, fname))
        
        event_pkgs.append({
            "pkg_idx": None,
            "dt": event_dt,
            "cat": cat,
            "time_str": event_ts_str or (unique_dts[0].strftime("%H:%M:%S") if unique_dts else "--:--:--"),
            "reason": f"{reason} ({dur_m}m)",
            "cams_count": len(unique_cams),
            "files_count": len(f_clips),
            "tot_size": tot_size,
            "rel_path": f"{cat}Clips/{folder_base}/",
            "folder": folder,
            "files": all_files or [c["path"] for c in f_clips],
            "is_folder": True
        })

    # 2. Recent clips (grouped into continuous chunks)
    recent_clips = [c for c in unique_clips if c["cat"] == "Recent"]
    recent_by_dt = defaultdict(list)
    for c in recent_clips:
        recent_by_dt[c["dt"]].append(c)

    sorted_recent_dts = sorted(recent_by_dt.keys())
    recent_chunks = []
    current_chunk = []

    for dt in sorted_recent_dts:
        if not current_chunk:
            current_chunk.append(dt)
        else:
            prev_dt = current_chunk[-1]
            delta_sec = (dt - prev_dt).total_seconds()
            intervening_ev = any(prev_dt < ep["dt"] <= dt for ep in event_pkgs if ep["dt"])
            if delta_sec <= 90 and not intervening_ev:
                current_chunk.append(dt)
            else:
                recent_chunks.append(current_chunk)
                current_chunk = [dt]

    if current_chunk:
        recent_chunks.append(current_chunk)

    packages = list(event_pkgs)
    for chunk in recent_chunks:
        c_start = chunk[0]
        c_end = chunk[-1]
        mins = len(chunk)
        all_chunk_clips = []
        for dt in chunk:
            all_chunk_clips.extend(recent_by_dt[dt])
        unique_cams = set(c["cam"] for c in all_chunk_clips)
        tot_size = sum(os.path.getsize(c["path"]) for c in all_chunk_clips if os.path.exists(c["path"]))
        
        if mins == 1:
            time_label = c_start.strftime("%H:%M:%S")
            reason_label = "Continuous Loop (1m)"
            rel_pattern = f"RecentClips/{c_start.strftime('%Y-%m-%d_%H-%M-%S')}-*.mp4"
        else:
            time_label = f"{c_start.strftime('%H:%M')} - {c_end.strftime('%H:%M')}"
            reason_label = f"Continuous Loop ({mins}m)"
            rel_pattern = f"RecentClips/{c_start.strftime('%Y-%m-%d_%H-%M')} ➔ {c_end.strftime('%H-%M')}"

        packages.append({
            "pkg_idx": None,
            "dt": c_start,
            "cat": "Recent",
            "time_str": time_label,
            "reason": reason_label,
            "cams_count": len(unique_cams),
            "files_count": len(all_chunk_clips),
            "tot_size": tot_size,
            "rel_path": rel_pattern,
            "folder": all_chunk_clips[0]["folder"],
            "files": [c["path"] for c in all_chunk_clips],
            "is_folder": False
        })

    packages.sort(key=lambda x: (x["dt"] if x["dt"] else datetime.min))
    for i, p in enumerate(packages, 1):
        p["pkg_idx"] = i
    return packages

def render_timeline_footage_table(event, analyzer):
    """Renders a structured Unicode box table of footage packages for an event."""
    packages = build_timeline_footage_packages(event)
    w_idx = 5
    w_time = 15
    w_type = 11
    w_reason = 25
    w_files = 21
    w_path = 44
    base_inner = w_idx + w_time + w_type + w_reason + w_files + w_path + 5
    
    t_start = event["start_dt"]
    title = f" 📹 Event Footage: {t_start.strftime('%a %d %b %Y')} ({event['time_str']}) — {event['activity']}"
    
    t_len = display_len(title)
    if t_len >= base_inner:
        w_path += (t_len - base_inner) + 3
    total_inner = w_idx + w_time + w_type + w_reason + w_files + w_path + 5
    
    if not packages:
        print(f"┌{'─'*total_inner}┐")
        print(f"│{pad_display(title, total_inner, 'left')}│")
        print(f"├{'─'*total_inner}┤")
        print(f"│{pad_display(' ⚠️  No local camera footage found on archive drives for this event.', total_inner, 'left')}│")
        print(f"└{'─'*total_inner}┘")
        return packages

    h_idx = f" {'#':^3} "
    h_time = pad_display(" Time / Window", w_time, "left")
    h_type = pad_display(" Type", w_type, "left")
    h_reason = pad_display(" Trigger / Reason", w_reason, "left")
    h_files = pad_display(" Cameras & Files", w_files, "left")
    h_path = pad_display(" TeslaCam Path / Target", w_path, "left")

    print(f"┌{'─'*total_inner}┐")
    print(f"│{pad_display(title, total_inner, 'left')}│")
    print(f"├{'─'*w_idx}┬{'─'*w_time}┬{'─'*w_type}┬{'─'*w_reason}┬{'─'*w_files}┬{'─'*w_path}┤")
    print(f"│{h_idx}│{h_time}│{h_type}│{h_reason}│{h_files}│{h_path}│")
    print(f"├{'─'*w_idx}┼{'─'*w_time}┼{'─'*w_type}┼{'─'*w_reason}┼{'─'*w_files}┼{'─'*w_path}┤")

    total_files = sum(p["files_count"] for p in packages)
    total_bytes = sum(p["tot_size"] for p in packages)
    total_mb = total_bytes / (1024 * 1024)
    total_size_str = f"{total_mb/1024:.1f} GB" if total_mb >= 1024 else f"{total_mb:.0f} MB"

    for p in packages:
        icon = "🔄" if p["cat"] == "Recent" else ("💾" if p["cat"] == "Saved" else ("🔴" if p["cat"] == "Sentry" else "📁"))
        type_label = f" {icon} {p['cat']}"
        size_mb = p["tot_size"] / (1024 * 1024)
        size_str = f"{size_mb/1024:.1f} GB" if size_mb >= 1024 else f"{size_mb:.0f} MB"
        files_label = f" {p['files_count']} files ({size_str})"
        
        c_idx = f" [{p['pkg_idx']:>2}]"
        c_time = pad_display(" " + p["time_str"], w_time, "left")
        c_type = pad_display(type_label, w_type, "left")
        c_reason = pad_display(" " + p["reason"], w_reason, "left")
        c_files = pad_display(files_label, w_files, "left")
        c_path = pad_display(" " + p["rel_path"], w_path, "left")
        
        print(f"│{c_idx}│{c_time}│{c_type}│{c_reason}│{c_files}│{c_path}│")

    print(f"├{'─'*w_idx}┴{'─'*w_time}┴{'─'*w_type}┴{'─'*w_reason}┴{'─'*w_files}┴{'─'*w_path}┤")
    sum_msg = f" Summary: {len(packages)} footage items ({total_files} video files, {total_size_str})"
    print(f"│{pad_display(sum_msg, total_inner, 'left')}│")
    print(f"└{'─'*total_inner}┘")
    return packages

def display_timeline_event_details(event, analyzer):
    """Level 3 for Timeline: Detailed listing & export menu for footage within a specific event."""
    while True:
        packages = render_timeline_footage_table(event, analyzer)
        if not packages:
            if not sys.stdin.isatty():
                break
            try:
                c = input("Navigation: [b]ack, [q]uit: ").strip().lower()
                if c == "q":
                    sys.exit(0)
                break
            except (KeyboardInterrupt, EOFError):
                break

        if not sys.stdin.isatty():
            break

        try:
            prompt_str = f"Select Footage [e.g. 1,5,8 or 6-9, 'all'] to copy, [o]pen folder, [b]ack, [q]uit: "
            choice = input(prompt_str).strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["b", "back"]:
                break
            elif choice in ["o", "open"]:
                folder_to_open = packages[0]["folder"] if packages else None
                if folder_to_open and os.path.exists(folder_to_open):
                    if sys.platform == "darwin":
                        subprocess.run(["open", folder_to_open])
                    print(f"📂 Opened in Finder: {folder_to_open}\n")
                else:
                    print("⚠️ Folder not accessible.\n")
                continue

            selected_indices = parse_selection_indices(choice, len(packages))
            if not selected_indices:
                print(f"Invalid choice. Please enter item numbers (e.g. 1, 6-9, 'all'), [o]pen, [b]ack, or [q]uit.\n")
                continue

            selected_pkgs = [p for p in packages if p["pkg_idx"] in selected_indices]
            total_files = sum(p["files_count"] for p in selected_pkgs)
            total_bytes = sum(p["tot_size"] for p in selected_pkgs)
            total_mb = total_bytes / (1024 * 1024)
            size_str = f"{total_mb/1024:.1f} GB" if total_mb >= 1024 else f"{total_mb:.0f} MB"

            slug = make_event_slug(event)
            default_dest = os.path.expanduser(f"~/Downloads/TeslaCam/{slug}")
            
            dest_input = input(f"📁 Export destination [Enter for '{default_dest}']: ").strip()
            dest_dir = os.path.abspath(os.path.expanduser(dest_input)) if dest_input else default_dest
            
            os.makedirs(dest_dir, exist_ok=True)
            print(f"\n📦 Exporting {len(selected_pkgs)} items ({total_files} files, {size_str}) to:\n   {dest_dir}")
            
            copied_count = 0
            copied_bytes = 0
            for p in selected_pkgs:
                if p.get("is_folder"):
                    folder_base = os.path.basename(p["folder"])
                    sub_dest = os.path.join(dest_dir, f"{p['cat']}Clips", folder_base)
                    os.makedirs(sub_dest, exist_ok=True)
                    for f_src in p["files"]:
                        if os.path.isfile(f_src):
                            f_name = os.path.basename(f_src)
                            dst_file = os.path.join(sub_dest, f_name)
                            try:
                                shutil.copyfile(f_src, dst_file)
                                copied_count += 1
                                copied_bytes += os.path.getsize(dst_file)
                            except Exception as e:
                                print(f"   ⚠️ Error copying {f_name}: {e}")
                else:
                    sub_dest = os.path.join(dest_dir, "RecentClips")
                    os.makedirs(sub_dest, exist_ok=True)
                    for f_src in p["files"]:
                        if os.path.isfile(f_src):
                            f_name = os.path.basename(f_src)
                            dst_file = os.path.join(sub_dest, f_name)
                            try:
                                shutil.copyfile(f_src, dst_file)
                                copied_count += 1
                                copied_bytes += os.path.getsize(dst_file)
                            except Exception as e:
                                print(f"   ⚠️ Error copying {f_name}: {e}")

            mb_done = copied_bytes / (1024 * 1024)
            done_str = f"{mb_done/1024:.1f} GB" if mb_done >= 1024 else f"{mb_done:.0f} MB"
            print(f"\n✔ Successfully exported {copied_count} files ({done_str}) to:\n   {dest_dir}\n")

            if sys.platform == "darwin":
                try:
                    open_resp = input("🔍 Open exported folder in Finder? [Y/n]: ").strip().lower()
                    if open_resp in ("", "y", "yes"):
                        subprocess.run(["open", dest_dir])
                        print(f"📂 Opened in Finder: {dest_dir}\n")
                except (KeyboardInterrupt, EOFError):
                    pass

        except (KeyboardInterrupt, EOFError):
            break

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
            prompt_str = f"Select Event [1-{len(events)} or HH:MM] for footage, [t]ag <#>, [p]rev day, [n]ext day, [b]ack, [q]uit: "
            choice = input(prompt_str).strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["b", "back"]:
                break
            elif choice in ["p", "prev"]:
                curr_date = curr_date - timedelta(days=1)
            elif choice in ["n", "next"]:
                curr_date = curr_date + timedelta(days=1)
            elif choice.startswith("t ") or choice.startswith("tag ") or choice == "t":
                parts = choice.split()
                tag_idx = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
                if tag_idx is None:
                    try:
                        t_in = input(f"Enter Event # to tag [1-{len(events)}]: ").strip()
                        tag_idx = int(t_in) if t_in.isdigit() else None
                    except (KeyboardInterrupt, EOFError):
                        continue
                if tag_idx:
                    matched_ev = next((ev for ev in events if ev["event_idx"] == tag_idx), None)
                    if matched_ev:
                        addr = ""
                        lat, lon = None, None
                        if matched_ev.get("drive"):
                            d = matched_ev["drive"]
                            addr = d.get("ending_address") or d.get("starting_address") or ""
                            lat = d.get("ending_latitude") or d.get("starting_latitude")
                            lon = d.get("ending_longitude") or d.get("starting_longitude")
                        else:
                            ev_idx = events.index(matched_ev)
                            for prev_i in range(ev_idx - 1, -1, -1):
                                if events[prev_i].get("drive"):
                                    d = events[prev_i]["drive"]
                                    addr = d.get("ending_address") or ""
                                    lat = d.get("ending_latitude")
                                    lon = d.get("ending_longitude")
                                    break
                            if lat is None:
                                addr = matched_ev.get("location", "")
                        try:
                            from tessie_places import interactive_lookup_and_add
                            saved = interactive_lookup_and_add(query_or_addr=addr, lat=lat, lon=lon)
                            if saved:
                                analyzer.load_places()
                                for d in analyzer.drives:
                                    d["start_place"] = analyzer.resolve_place(d.get("starting_address",""), d.get("saved_location",""), d.get("starting_latitude"), d.get("starting_longitude"))
                                    d["end_place"] = analyzer.resolve_place(d.get("ending_address",""), d.get("saved_location",""), d.get("ending_latitude"), d.get("ending_longitude"))
                                continue
                        except Exception as e:
                            print(f"⚠️ Error during tagging: {e}")
                    else:
                        print("Invalid event number.")
                else:
                    print("Invalid event number.")
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
        
        w_idx = 5
        w_date = 18
        w_trips = 9
        w_time = 12
        w_dist = 14
        w_footage = 42
        
        total_inner = w_idx + w_date + w_trips + w_time + w_dist + w_footage + 5
        border_top = "┌" + "─" * total_inner + "┐"
        border_mid = "├" + "─" * w_idx + "┬" + "─" * w_date + "┬" + "─" * w_trips + "┬" + "─" * w_time + "┬" + "─" * w_dist + "┬" + "─" * w_footage + "┤"
        border_bot = "└" + "─" * w_idx + "┴" + "─" * w_date + "┴" + "─" * w_trips + "┴" + "─" * w_time + "┴" + "─" * w_dist + "┴" + "─" * w_footage + "┘"
        
        print(f"\n{border_top}")
        
        title_str = f" 📍 {title} ({total_trips} Trips Across {len(sorted_days)} Days)"
        print(f"│{pad_display(title_str, total_inner, 'left')}│")
        print(border_mid)
        
        header = f"│{pad_display(' #', w_idx)}│{pad_display(' Date', w_date)}│{pad_display(' Trips', w_trips)}│{pad_display(' Time', w_time)}│{pad_display(' Distance', w_dist)}│{pad_display(' Footage', w_footage)}│"
        print(header)
        print(border_mid)
        
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
            
            c_idx = pad_display(f" [{idx+1}]", w_idx)
            c_date = pad_display(f" {dt_obj.strftime('%a %d %b %Y')}", w_date)
            c_trips = pad_display(f" {len(day_trips)}", w_trips)
            c_time = pad_display(f" {time_str}", w_time)
            c_dist = pad_display(f" {total_km:.1f} km", w_dist)
            c_foot = pad_display(f" {f_summary}", w_footage)
            
            print(f"│{c_idx}│{c_date}│{c_trips}│{c_time}│{c_dist}│{c_foot}│")

        print(border_bot)
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
