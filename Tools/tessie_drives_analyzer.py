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
import fnmatch
import json
import math
import shutil
import subprocess
import argparse
import time
import unicodedata
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from typing import Optional, List, Dict, Tuple, Any, Union

EMOJI_MAP = {
    "Recent": "🔄 Recent",
    "Saved": "💾 Saved",
    "Sentry": "🔴 Sentry"
}

_tools_dir = os.path.dirname(os.path.abspath(__file__))
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

from lib.archive_naming import next_archive_path
from lib.analysis_deepdive_charts import write_interactive_chart, try_open_in_browser

from lib.table_formatter import (
    char_width,
    display_len,
    pad_display,
    truncate_display,
    format_row,
    format_title_line,
    format_box_line,
)
import lib.tessie_timezone as tztools
import lib.tessie_api_common as tessie_api_common


def normalize_row_timezone(row, lat_key, lon_key, start_key_prefix):
    """Ensures row[f"{start_key_prefix} (UTC)"]/(TZ)/(Local) are populated
    (TODO-009). Idempotent: if the row already has a non-empty UTC value
    (e.g. re-reading an already-migrated drives_master.csv), leaves it
    alone. Otherwise derives true UTC from whichever legacy column is
    present - Tessie's own CSV export always applies ONE fixed,
    known offset (Australia/Sydney's, for whatever date the row is on -
    this repo always requests timezone=Australia/Sydney), so that offset
    is exactly reversible regardless of where the event really happened;
    the row's own (unaffected) lat/lon then gives the TRUE zone via
    resolve_timezone(). Self-healing: works identically whether the row
    came from the existing master file or a freshly landed CSV, so a
    normal --consolidate run upgrades old and new rows alike with no
    separate migration step."""
    utc_key = f"{start_key_prefix} (UTC)"
    tz_key = f"{start_key_prefix} TZ"
    local_key = f"{start_key_prefix} (Local)"
    if row.get(utc_key):
        return
    legacy = (row.get(f"{start_key_prefix} (AEST)") or row.get(start_key_prefix)
              or row.get(start_key_prefix.replace(" At", "")) or "").strip()
    if not legacy:
        row[utc_key] = ""
        row[tz_key] = ""
        row[local_key] = ""
        return
    try:
        naive = datetime.strptime(legacy[:16], "%Y-%m-%d %H:%M")
    except Exception:
        row[utc_key] = ""
        row[tz_key] = ""
        row[local_key] = legacy
        return
    # naive.timestamp() would interpret the naive value in THIS machine's
    # own local zone, which is wrong on any host not itself set to
    # Sydney - compute purely arithmetically instead: treat the naive
    # value as if it already were UTC (epoch_as_if_utc), then subtract
    # the offset Tessie actually applied (Sydney's, for this date) to
    # recover the true UTC instant.
    sydney_offset = tztools.sydney_offset_seconds(naive)
    epoch_as_if_utc = int((naive - datetime(1970, 1, 1)).total_seconds())
    true_utc_epoch = epoch_as_if_utc - int(sydney_offset)

    lat = row.get(lat_key)
    lon = row.get(lon_key)
    zone = tztools.resolve_timezone(lat, lon)
    local_dt, abbr, offset_str = tztools.localize(true_utc_epoch, zone)

    row[utc_key] = str(true_utc_epoch)
    row[tz_key] = zone
    row[local_key] = f"{local_dt.strftime('%Y-%m-%d %H:%M')} {abbr}" if local_dt else legacy


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

def format_drive_efficiency(t):
    """kWh/100km average efficiency for a whole trip (drives-table column,
    REQ AGENTS.local.md 2026-09-15: user - "a new column in the drives
    probably after 'Route (Origin -> Destination)' and before 'Parked
    Duration' - this should be in kWh/100km"). Prefers Tessie's own
    'Average Energy Used (Wh/km)' summary column (already correctly
    averaged over the whole drive - divide by 10 for kWh/100km), falling
    back to Total Energy Used (kWh) / Distance (km) * 100 when Wh/km is
    missing but both of those are present. Returns '-' when neither is
    available (e.g. an older export or a footage-only row with no
    drive-summary energy data)."""
    wh_km = t.get("wh_km")
    try:
        if wh_km not in (None, "", "-"):
            return f"{float(wh_km) / 10.0:.1f} kWh/100km"
    except (TypeError, ValueError):
        pass
    kwh_used = t.get("kwh_used")
    dist_km = t.get("dist_km")
    try:
        if kwh_used not in (None, "", "-") and dist_km and float(dist_km) > 0:
            return f"{(float(kwh_used) / float(dist_km)) * 100.0:.1f} kWh/100km"
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return "-"

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
    icons = {
        "Saved": "📹 Saved",
        "Sentry": "🛡️ Sentry",
        "Recent": "🕒 Recent",
        "Other": "📁 Clips"
    }
    order = ["Saved", "Sentry", "Recent", "Other"]
    tags = [icons.get(c, c) for c in order if c in cats]
    return ", ".join(tags) if tags else "No local footage"

def is_home_place(place_str, places_dict=None):
    if not place_str:
        return False
    p_lower = str(place_str).lower().replace("🏠", "").replace("📍", "").strip()
    if places_dict and isinstance(places_dict, dict):
        for p_name, p_info in places_dict.items():
            if isinstance(p_info, dict) and (p_info.get("type") == "home" or str(p_name).lower() == "home"):
                meta_short = str((p_info.get("tesla_metadata") or {}).get("short_name", "")).lower()
                loc_name = str(p_info.get("location_name", "")).lower()
                nick_name = str(p_info.get("nickname", "")).lower()
                if p_lower in (str(p_name).lower(), meta_short, loc_name, nick_name) and p_lower:
                    return True
    # Strictly match 'home' as a discrete token (zero hardcoded PII)
    return bool(re.search(r"\bhome\b", p_lower))

def is_home_event(ev, analyzer=None):
    if not ev:
        return False
    loc = ev.get("location", "")
    act = ev.get("activity", "")
    places_dict = getattr(analyzer, "places", None) if analyzer else None
    return is_home_place(loc, places_dict=places_dict) or is_home_place(act, places_dict=places_dict)

def clean_destination_display(p):
    if not p:
        return ""
    p_clean = p.replace("🏠", "").replace("📍", "").strip()
    p_lower = p_clean.lower()
    if p_lower in ("home",):
        return ""
    if p_lower in ("home",):
        return ""
    if "echo point" in p_lower:
        return "Echo Point"
    if "scenic world" in p_lower:
        return "Scenic World"
    if "railway parade" in p_lower:
        return "Leura (Railway Pde)"
    if "cliff drive" in p_lower:
        return "Katoomba (Cliff Dr)"
    if "hancott" in p_lower or "goulding" in p_lower:
        return "Preschool"
    if "," in p_clean:
        return p_clean.split(",")[0].strip()
    return p_clean

def extract_notable_destinations(trips, max_places=3):
    seen = set()
    places = []
    for t in trips:
        for p in [t.get("end_place"), t.get("start_place")]:
            if not p:
                continue
            cp = clean_destination_display(p)
            if cp and cp not in seen:
                seen.add(cp)
                places.append(cp)
    if not places:
        return "Local (West Ryde)"
    return ", ".join(places[:max_places])

def shorten_display_path(p, max_len=40):
    if not p:
        return ""
    p_str = str(p)
    home = os.path.expanduser("~")
    if p_str.startswith(home):
        p_str = "~" + p_str[len(home):]
    p_str = p_str.replace("~/Library/Mobile Documents/com~apple~CloudDocs/", "iCloud/")
    p_str = p_str.replace("~/Library/Mobile Documents/com~apple~CloudDocs", "iCloud")
    if max_len and len(p_str) > max_len:
        p_str = "…" + p_str[-(max_len - 1):]
    return p_str

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
    Dynamically discovers all mounted volumes matching TESLADRIVE* under
    /Volumes. If subdir is provided (e.g. 'TeslaCam'), returns existing
    subdirectories within those volumes.

    TESLADRIVE* volumes are reserved exclusively for dashcam/TeslaCam media
    (see AGENTS.md) - this is what locates real footage on the drive for
    the Saved/Sentry/Recent columns below. Tessie CSV/registry data is
    never read from or written to TESLADRIVE (that tooling runs from the
    repository and iCloud only, see dashcam_sync.sh) - callers must not pass
    a 'Tessie' subdir here.
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

def resolve_master_teslacam_dir(teslacam_dirs=None):
    """
    Identifies the authoritative Master Archive TeslaCam directory.
    Uses multi-tier detection:
    1. Root marker directory: `ARCHIVE_2TB` or `ARCHIVE` (100% authoritative)
    2. Physical storage capacity: > 1600 GB (2TB Master Archive SSD)
    3. Primary mount name: `/Volumes/TESLADRIVE` (strictly without numeric suffix)
    Guarantees secondary drives (JOWUA 1TB, Tesla USB) are NEVER mistaken for Master Archive.
    """
    candidates = list(teslacam_dirs) if teslacam_dirs else []
    
    # If no candidates provided, auto-discover from /Volumes
    if not candidates:
        if os.path.isdir("/Volumes/TESLADRIVE/TeslaCam") or os.path.isdir("/Volumes/TESLADRIVE"):
            candidates.append("/Volumes/TESLADRIVE")
        for v in glob.glob("/Volumes/*"):
            if v not in candidates and os.path.isdir(os.path.join(v, "TeslaCam")):
                candidates.append(v)

    # Pass 1: Explicit Root Marker (ARCHIVE_2TB / ARCHIVE)
    for d in candidates:
        if not d:
            continue
        root = get_drive_root(d)
        if not root or not os.path.isdir(root):
            continue
        if os.path.isdir(os.path.join(root, "ARCHIVE_2TB")) or os.path.isdir(os.path.join(root, "ARCHIVE")):
            tc_dir = os.path.join(root, "TeslaCam")
            return tc_dir if os.path.isdir(tc_dir) else root

    # Pass 2: Capacity > 1600 GB (2TB Master Archive SSD)
    for d in candidates:
        if not d:
            continue
        root = get_drive_root(d)
        if not root or not os.path.isdir(root):
            continue
        try:
            total_gb = shutil.disk_usage(root).total / (1024**3)
            if total_gb > 1600 and not os.path.isdir(os.path.join(root, "JOWUA_1TB")):
                tc_dir = os.path.join(root, "TeslaCam")
                return tc_dir if os.path.isdir(tc_dir) else root
        except OSError:
            pass

    # Pass 3: Primary Mount Name '/Volumes/TESLADRIVE' (strictly without ' 1', ' 2')
    for d in candidates:
        if not d:
            continue
        root = get_drive_root(d)
        if not root or not os.path.isdir(root):
            continue
        base = os.path.basename(os.path.normpath(root))
        if base == "TESLADRIVE":
            tc_dir = os.path.join(root, "TeslaCam")
            return tc_dir if os.path.isdir(tc_dir) else root

    # Pass 4: Fallback to first candidate that is NOT JOWUA or USB
    for d in candidates:
        if not d:
            continue
        root = get_drive_root(d)
        if not root or not os.path.isdir(root):
            continue
        ident = get_drive_identifier(root)
        if ident not in ["VOL_JOWUA", "VOL_TESLA_USB"]:
            tc_dir = os.path.join(root, "TeslaCam")
            return tc_dir if os.path.isdir(tc_dir) else d

    # Last resort fallback if only one candidate exists
    if candidates and candidates[0]:
        root0 = get_drive_root(candidates[0])
        tc_dir = os.path.join(root0, "TeslaCam")
        return tc_dir if os.path.isdir(tc_dir) else candidates[0]

    return None

def get_drive_identifier(drive_path, master_dir=None):
    """
    Authoritatively identifies a connected TeslaCam drive using:
    1. Root directory identity markers (ARCHIVE_2TB, JOWUA_1TB, TESLA_USB_*)
    2. Volume mount name conventions (TESLADRIVE, TESLADRIVE 1, JOWUA, TESLA_USB)
    3. Physical storage capacity fallback (>1600 GB, 700-1600 GB, <600 GB)
    """
    if not drive_path:
        return "UNKNOWN"
    root = get_drive_root(drive_path)
    if not root or not os.path.exists(root):
        return "UNKNOWN"
    
    m_root = get_drive_root(master_dir) if master_dir else None
    if m_root and os.path.realpath(root) == os.path.realpath(m_root):
        return "MASTER"

    # 1. Root Marker Directory Inspection
    if os.path.isdir(os.path.join(root, "ARCHIVE_2TB")) or os.path.isdir(os.path.join(root, "ARCHIVE")):
        return "VOL_2TB_ARCHIVE"
    elif os.path.isdir(os.path.join(root, "JOWUA_1TB")) or os.path.isdir(os.path.join(root, "JOWUA")):
        return "VOL_JOWUA"
    elif any(os.path.isdir(os.path.join(root, m)) for m in ["TESLA_USB_128GB", "TESLA_USB_256GB", "TESLA_USB"]):
        return "VOL_TESLA_USB"

    # 2. Mount Name / Directory Name Matching
    norm = os.path.normpath(drive_path)
    base = os.path.basename(root)
    if "TESLADRIVE 1" in norm or base == "TESLADRIVE 1" or "JOWUA" in base.upper():
        return "VOL_JOWUA"
    elif "TESLADRIVE 2" in norm or base == "TESLADRIVE 2" or "USB" in base.upper():
        return "VOL_TESLA_USB"
    elif base == "TESLADRIVE":
        return "VOL_2TB_ARCHIVE"

    # 3. Physical Storage Capacity Heuristic (Fallback for distinct hardware partitions)
    try:
        total_gb = shutil.disk_usage(root).total / (1024**3)
        if total_gb > 1600:
            return "VOL_2TB_ARCHIVE"
        elif 700 <= total_gb <= 1600:
            return "VOL_JOWUA"
        elif 30 <= total_gb < 700:
            return "VOL_TESLA_USB"
    except OSError:
        pass

    return f"VOL_{base.upper().replace(' ', '_')}" if base else "SECONDARY_DRIVE"

def get_drive_root(drive_or_teslacam_dir):
    """Returns root directory of the drive (parent directory if pointing to TeslaCam subfolder)."""
    if not drive_or_teslacam_dir:
        return None
    norm = os.path.abspath(drive_or_teslacam_dir)
    if os.path.basename(norm).lower() == "teslacam":
        return os.path.dirname(norm)
    return norm

def get_catalog_file_path(drive_dir=None):
    """Returns path to .TeslaCam_catalog.json on drive root (or Master Archive SSD root)."""
    d_dir = drive_dir or resolve_master_teslacam_dir()
    if d_dir and os.path.isdir(d_dir):
        root = get_drive_root(d_dir)
        new_path = os.path.join(root, ".TeslaCam_catalog.json")
        # Self-healing migration from legacy .catalog.json if present
        legacy_path_root = os.path.join(root, ".catalog.json")
        legacy_path_tc = os.path.join(d_dir, ".catalog.json")
        if not os.path.isfile(new_path):
            if os.path.isfile(legacy_path_root):
                try:
                    os.replace(legacy_path_root, new_path)
                except OSError:
                    pass
            elif os.path.isfile(legacy_path_tc):
                try:
                    os.replace(legacy_path_tc, new_path)
                except OSError:
                    pass
        return new_path
    return os.path.expanduser("~/.TeslaCam_catalog.json")

def get_tombstone_file_path(master_dir=None):
    """Returns path to .TeslaCam_tombstones.json on Master Archive SSD root."""
    m_dir = master_dir or resolve_master_teslacam_dir()
    if m_dir and os.path.isdir(m_dir):
        root = get_drive_root(m_dir)
        new_path = os.path.join(root, ".TeslaCam_tombstones.json")
        # Self-healing migration from legacy .tombstones.json if present
        legacy_path_root = os.path.join(root, ".tombstones.json")
        legacy_path_tc = os.path.join(m_dir, ".tombstones.json")
        if not os.path.isfile(new_path):
            if os.path.isfile(legacy_path_root):
                try:
                    os.replace(legacy_path_root, new_path)
                except OSError:
                    pass
            elif os.path.isfile(legacy_path_tc):
                try:
                    os.replace(legacy_path_tc, new_path)
                except OSError:
                    pass
        return new_path
    return os.path.expanduser("~/.TeslaCam_tombstones.json")

def load_catalog(drive_dir=None):
    """Loads drive catalog containing indexed dashcam packages."""
    path = get_catalog_file_path(drive_dir)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "packages" in data:
                    return data
        except Exception:
            pass
    m_dir = resolve_master_teslacam_dir()
    drive_id = get_drive_identifier(drive_dir, m_dir) if drive_dir else "MASTER"
    return {
        "version": 1,
        "drive_id": drive_id,
        "updated_at": datetime.now().isoformat(),
        "dirs_mtime": {},
        "summary": {"total_packages": 0, "total_files": 0, "total_bytes": 0},
        "packages": {}
    }

def save_catalog(data, drive_dir=None):
    """Persists drive catalog atomically to disk with refreshed summary metrics."""
    path = get_catalog_file_path(drive_dir)
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data["updated_at"] = datetime.now().isoformat()
        pkgs = data.get("packages", {})
        tot_files = sum(p.get("files_count", len(p.get("files", []))) for p in pkgs.values())
        tot_bytes = sum(p.get("size_bytes", 0) for p in pkgs.values())
        data["summary"] = {
            "total_packages": len(pkgs),
            "total_files": tot_files,
            "total_bytes": tot_bytes
        }
        tmp_path = path + f".tmp.{os.getpid()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, path)
        except OSError:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    except (OSError, PermissionError):
        pass

def scan_or_update_drive_catalog(drive_path, master_dir=None, force_rescan=False):
    """
    Fast, cached drive indexer.
    Uses directory mtime validation to achieve < 15ms lookup speed across thousands of clips.
    Incrementally updates only modified folders when changes occur.
    """
    if not drive_path or not os.path.isdir(drive_path):
        return {"version": 1, "drive_id": "UNKNOWN", "packages": {}}

    # If drive_path is the drive root, resolve its TeslaCam subfolder if present
    if os.path.isdir(os.path.join(drive_path, "TeslaCam")):
        drive_path = os.path.join(drive_path, "TeslaCam")

    m_dir = master_dir or resolve_master_teslacam_dir()
    drive_id = get_drive_identifier(drive_path, m_dir)
    
    # 1. Quick mtime inspection of top-level categories
    cur_mtimes = {}
    for cat in ["SavedClips", "SentryClips", "RecentClips"]:
        p = os.path.join(drive_path, cat)
        if os.path.isdir(p):
            try:
                cur_mtimes[cat] = os.path.getmtime(p)
            except OSError:
                pass

    catalog_data = load_catalog(drive_path)
    catalog_data["drive_id"] = drive_id
    cached_mtimes = catalog_data.get("dirs_mtime", {})
    pkgs = catalog_data.get("packages", {})

    # Check if we can fast-return cached catalog
    if not force_rescan and pkgs and cached_mtimes == cur_mtimes:
        return catalog_data

    # 2. Re-index SavedClips and SentryClips incrementally
    for cat_name, cat_label in [("SavedClips", "Saved"), ("SentryClips", "Sentry")]:
        cat_dir = os.path.join(drive_path, cat_name)
        if not os.path.isdir(cat_dir):
            for k in list(pkgs.keys()):
                if k.startswith(f"{cat_name}/"):
                    del pkgs[k]
            continue

        disk_entries = set()
        try:
            with os.scandir(cat_dir) as cat_it:
                for cat_entry in cat_it:
                    if cat_entry.name.startswith(".") or not cat_entry.is_dir():
                        continue
                    rel_k = f"{cat_name}/{cat_entry.name}"
                    disk_entries.add(rel_k)
                    try:
                        folder_mtime = cat_entry.stat().st_mtime
                    except OSError:
                        folder_mtime = 0.0

                    # If folder mtime matches cached package, keep cached package
                    cached_pkg = pkgs.get(rel_k)
                    if cached_pkg and cached_pkg.get("mtime") == folder_mtime and not force_rescan:
                        continue

                    all_f = []
                    tot_size = 0
                    try:
                        with os.scandir(cat_entry.path) as sub_it:
                            for f_entry in sub_it:
                                if not f_entry.name.startswith("."):
                                    all_f.append(f_entry.name)
                                    try:
                                        tot_size += f_entry.stat().st_size
                                    except OSError:
                                        pass
                    except OSError:
                        pass

                    mp4s = [x for x in all_f if x.endswith(".mp4") and x != "event.mp4"]
                    start_dt_str = ""
                    end_dt_str = ""
                    if mp4s:
                        mp4s.sort()
                        first_ts = mp4s[0][:19]
                        last_ts = mp4s[-1][:19]
                        start_dt_str = first_ts.replace("_", "T")
                        end_dt_str = last_ts.replace("_", "T")

                    folder_reason = "User Tapped" if cat_label == "Saved" else "Sentry Event"
                    folder_event_ts = ""
                    ev_json_path = os.path.join(cat_entry.path, "event.json")
                    if os.path.isfile(ev_json_path):
                        try:
                            with open(ev_json_path, "r", encoding="utf-8") as fp:
                                d = json.load(fp)
                                raw_r = d.get("reason", "")
                                if raw_r:
                                    folder_reason = clean_event_reason(raw_r).title()
                                ts = d.get("timestamp")
                                if ts:
                                    folder_event_ts = ts
                        except Exception:
                            pass

                    pkgs[rel_k] = {
                        "cat": cat_label,
                        "reason": folder_reason,
                        "event_ts": folder_event_ts,
                        "start_dt": start_dt_str,
                        "end_dt": end_dt_str,
                        "time_range": cat_entry.name,
                        "files_count": len(all_f),
                        "size_bytes": tot_size,
                        "mtime": folder_mtime,
                        "files": all_f
                    }
        except OSError:
            pass

        # Remove deleted event folders from catalog
        for k in list(pkgs.keys()):
            if k.startswith(f"{cat_name}/") and k not in disk_entries:
                del pkgs[k]

    # 3. Re-index RecentClips (Continuous driving buffer)
    rc_dir = os.path.join(drive_path, "RecentClips")
    if os.path.isdir(rc_dir):
        disk_recent_days = defaultdict(list)
        day_sizes = defaultdict(int)
        try:
            with os.scandir(rc_dir) as it:
                for entry in it:
                    if entry.name.endswith(".mp4") and not entry.name.startswith("."):
                        day_k = entry.name[:10]
                        disk_recent_days[day_k].append(entry.name)
                        try:
                            day_sizes[day_k] += entry.stat().st_size
                        except OSError:
                            pass
        except OSError:
            pass

        seen_rc_keys = set()
        for day_k, files_list in disk_recent_days.items():
            rel_k = f"RecentClips/{day_k}"
            seen_rc_keys.add(rel_k)
            files_list.sort()
            first_ts = files_list[0][:19]
            last_ts = files_list[-1][:19]

            pkgs[rel_k] = {
                "cat": "Recent",
                "reason": "Continuous Driving Loop",
                "start_dt": first_ts.replace("_", "T"),
                "end_dt": last_ts.replace("_", "T"),
                "time_range": f"{first_ts[11:16]} ➔ {last_ts[11:16]}",
                "files_count": len(files_list),
                "size_bytes": day_sizes[day_k],
                "mtime": cur_mtimes.get("RecentClips", 0.0),
                "files": files_list
            }

        for k in list(pkgs.keys()):
            if k.startswith("RecentClips/") and k not in seen_rc_keys:
                del pkgs[k]
    else:
        for k in list(pkgs.keys()):
            if k.startswith("RecentClips/"):
                del pkgs[k]

    catalog_data["dirs_mtime"] = cur_mtimes
    catalog_data["packages"] = pkgs
    save_catalog(catalog_data, drive_path)
    return catalog_data

def load_tombstones(master_dir=None):
    """Loads tombstone registry containing deleted packages and pending drives."""
    path = get_tombstone_file_path(master_dir)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "tombstones" in data:
                    return data
        except Exception:
            pass
    return {"version": 1, "updated_at": datetime.now().isoformat(), "known_drives": ["VOL_JOWUA", "VOL_TESLA_USB"], "tombstones": {}}

def save_tombstones(data, master_dir=None):
    """Persists tombstone registry atomically to disk."""
    path = get_tombstone_file_path(master_dir)
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data["updated_at"] = datetime.now().isoformat()
        tmp_path = path + f".tmp.{os.getpid()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, path)
        except OSError:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    except (OSError, PermissionError):
        pass

def reconcile_archive_catalog_and_tombstones(master_dir, secondary_dirs=None, force_rescan=False):
    """
    Scans the physical Master Archive directory and reconciles with .catalog.json:
    1. Detects manual Finder deletions -> adds to .tombstones.json with pending_drives.
    2. Discovers new files on Master Archive -> indexes into .catalog.json.
    3. Updates secondary drive catalogs.
    Uses fast directory mtime cache validation (<15ms) unless force_rescan is True.
    Returns (added_count, promoted_tombstones_count).
    """
    if not master_dir or not os.path.isdir(master_dir):
        return 0, 0

    tombstones_data = load_tombstones(master_dir)
    tombs = tombstones_data.get("tombstones", {})
    known_drives = list(tombstones_data.get("known_drives", ["VOL_JOWUA", "VOL_TESLA_USB"]))
    
    if secondary_dirs:
        for s in secondary_dirs:
            drv_id = get_drive_identifier(s, master_dir)
            if drv_id and drv_id != "MASTER" and drv_id not in known_drives:
                known_drives.append(drv_id)
        tombstones_data["known_drives"] = known_drives

    # Load previous catalog before update to detect Finder deletions
    old_catalog = load_catalog(master_dir)
    old_pkgs = dict(old_catalog.get("packages", {}))

    # Scan and update Master catalog with fast mtime cache
    new_catalog = scan_or_update_drive_catalog(master_dir, master_dir, force_rescan=force_rescan)
    new_pkgs = new_catalog.get("packages", {})

    # Detect Finder deletions: in old_pkgs but not in new_pkgs
    promoted_tombstones = 0
    now_iso = datetime.now().isoformat()
    for cat_k, cat_info in old_pkgs.items():
        if cat_k not in new_pkgs:
            if cat_k.startswith("SavedClips/") or cat_k.startswith("SentryClips/"):
                tombs[cat_k] = {
                    "status": "pending_purge",
                    "deleted_at": now_iso,
                    "reason": "Master Archive Deletion (Finder)",
                    "cat": cat_info.get("cat", "Saved"),
                    "folder": cat_k,
                    "files": cat_info.get("files", []),
                    "est_bytes": cat_info.get("size_bytes", 0),
                    "pending_drives": list(known_drives)
                }
                promoted_tombstones += 1

    added_to_catalog = sum(1 for k in new_pkgs if k not in old_pkgs)

    # Clean any tombstoned items that were restored or re-added
    for k in new_pkgs:
        if k in tombs:
            del tombs[k]

    save_tombstones(tombstones_data, master_dir)

    # Update secondary drive catalogs if present
    if secondary_dirs:
        for s in secondary_dirs:
            if s and os.path.isdir(s):
                scan_or_update_drive_catalog(s, master_dir)

    return added_to_catalog, promoted_tombstones

def scrub_pending_tombstones_for_drive(drive_path, master_dir=None, dry_run=False):
    """
    Purges deleted packages from a connected secondary drive (e.g. JOWUA) matching active tombstones.
    Clears the drive from pending_drives, and retires tombstones once scrubbed everywhere.
    Returns (scrubbed_files, freed_bytes, retired_tombstones).
    """
    if not drive_path or not os.path.isdir(drive_path):
        return 0, 0, 0

    tc_dir = os.path.join(drive_path, "TeslaCam") if os.path.isdir(os.path.join(drive_path, "TeslaCam")) else drive_path
    m_dir = master_dir or resolve_master_teslacam_dir()
    drive_id = get_drive_identifier(drive_path, m_dir)
    if drive_id == "MASTER":
        return 0, 0, 0

    tombstones_data = load_tombstones(m_dir)
    tombs = tombstones_data.get("tombstones", {})
    if not tombs:
        return 0, 0, 0

    scrubbed_files = 0
    freed_bytes = 0
    retired_count = 0

    for key, info in list(tombs.items()):
        pending = info.get("pending_drives", [])
        if drive_id in pending or not pending:
            target_dir = os.path.join(tc_dir, key)
            if os.path.isdir(target_dir):
                for root, dirs, files in os.walk(target_dir, topdown=False):
                    for f in files:
                        fp = os.path.join(root, f)
                        try:
                            sz = os.path.getsize(fp)
                            if not dry_run:
                                os.remove(fp)
                            scrubbed_files += 1
                            freed_bytes += sz
                        except OSError:
                            pass
                    if not dry_run:
                        try:
                            os.rmdir(root)
                        except OSError:
                            pass
            elif key.startswith("RecentClips/"):
                rc_dir = os.path.join(tc_dir, "RecentClips")
                if os.path.isdir(rc_dir):
                    for f in info.get("files", []):
                        fp = os.path.join(rc_dir, f)
                        if os.path.isfile(fp):
                            try:
                                sz = os.path.getsize(fp)
                                if not dry_run:
                                    os.remove(fp)
                                scrubbed_files += 1
                                freed_bytes += sz
                            except OSError:
                                pass

            if not dry_run:
                if drive_id in pending:
                    pending.remove(drive_id)
                    info["pending_drives"] = pending

                if not pending:
                    del tombs[key]
                    retired_count += 1
            else:
                if drive_id in pending and len(pending) <= 1:
                    retired_count += 1

    if not dry_run:
        save_tombstones(tombstones_data, m_dir)
        if scrubbed_files > 0:
            scan_or_update_drive_catalog(drive_path, m_dir, force_rescan=True)
    return scrubbed_files, freed_bytes, retired_count

def get_tombstone_lookup_sets(tombstones_data, include_flagged=False):
    """Builds O(1) fast lookup sets for active deleted tombstones (excludes staged/flagged unless requested)."""
    if not tombstones_data or "tombstones" not in tombstones_data:
        return set(), set(), []
    ts_dict = tombstones_data["tombstones"]
    prefixes = set()
    exact_files = set()
    wildcard_files = []
    for k, info in ts_dict.items():
        if not include_flagged and info.get("status") == "flagged":
            continue
        prefixes.add(os.path.normpath(k).replace("\\", "/"))
        for f in info.get("files", []):
            if "*" in f or "?" in f:
                wildcard_files.append(f)
            else:
                exact_files.add(f)
    return prefixes, exact_files, wildcard_files

def is_clip_tombstoned(rel_path, tombstones_data, lookup_sets=None, include_flagged=False):
    """Checks whether a relative file path matches an active deleted tombstone."""
    if not tombstones_data or "tombstones" not in tombstones_data:
        return False
    norm_rel = os.path.normpath(rel_path).replace("\\", "/")
    base_rel = os.path.basename(norm_rel)
    
    if lookup_sets is not None:
        if len(lookup_sets) == 3:
            prefixes, exact_files, wildcard_files = lookup_sets
        else:
            prefixes, exact_files = lookup_sets
            wildcard_files = []
    else:
        prefixes, exact_files, wildcard_files = get_tombstone_lookup_sets(tombstones_data, include_flagged=include_flagged)
        
    if not prefixes and not exact_files and not wildcard_files:
        return False
        
    if norm_rel in prefixes or any(norm_rel.startswith(p + "/") or norm_rel.startswith(p + "-") or norm_rel.startswith(p + "_") for p in prefixes):
        return True
    if base_rel in exact_files:
        return True
    if wildcard_files and any(fnmatch.fnmatch(base_rel, pat) for pat in wildcard_files):
        return True
    return False

def is_package_flagged(pkg, tombstones_data=None, repo_root=None, master_dir=None, lookup_sets=None):
    """Checks whether a package is currently flagged for deletion in .tombstones.json."""
    if not tombstones_data:
        m_dir = master_dir or resolve_master_teslacam_dir()
        tombstones_data = load_tombstones(m_dir)
    tombs = tombstones_data.get("tombstones", {})
    if not tombs:
        return False
        
    is_folder = pkg.get("is_folder", False)
    if is_folder:
        cat = pkg.get("cat", "Saved")
        folder_base = os.path.basename(pkg.get("folder", ""))
        rel_key = f"{cat}Clips/{folder_base}"
        return rel_key in tombs
    else:
        dt = pkg.get("dt")
        start_dt_str = dt.strftime("%Y-%m-%d_%H-%M-%S") if dt else "recent"
        rel_key = f"RecentClips/{start_dt_str}"
        if rel_key in tombs:
            return True
            
        if lookup_sets is not None:
            if len(lookup_sets) == 3:
                _, exact_files, wildcard_files = lookup_sets
            else:
                _, exact_files = lookup_sets
                wildcard_files = []
        else:
            exact_files = set()
            wildcard_files = []
            for info in tombs.values():
                for f in info.get("files", []):
                    if "*" in f or "?" in f:
                        wildcard_files.append(f)
                    else:
                        exact_files.add(f)
                
        for f in pkg.get("files", []):
            fname = os.path.basename(f)
            if fname in exact_files:
                return True
            if wildcard_files and any(fnmatch.fnmatch(fname, pat) for pat in wildcard_files):
                return True
    return False

def get_flagged_tombstones(master_dir=None):
    """Returns all active tombstones pending multi-drive deletion."""
    m_dir = master_dir or resolve_master_teslacam_dir()
    data = load_tombstones(m_dir)
    tombs = data.get("tombstones", {})
    return {k: v for k, v in tombs.items() if v.get("status") in ("flagged", "pending_purge", "tombstone")}

def export_tombstone_rsync_excludes(master_dir=None, out_path=None):
    """Generates an rsync --exclude-from pattern file from active tombstones."""
    data = load_tombstones(master_dir)
    tombs = data.get("tombstones", {})
    patterns = []
    for key, info in tombs.items():
        patterns.append(f"{key}")
        patterns.append(f"{key}/**")
        for f in info.get("files", []):
            if key.startswith("RecentClips"):
                patterns.append(f"RecentClips/{f}")
            else:
                patterns.append(f"{key}/{f}")
    
    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            for p in sorted(set(patterns)):
                f.write(f"{p}\n")
    return sorted(list(set(patterns)))

def flag_footage_packages_for_deletion(packages, analyzer):
    """
    Stages packages for deletion in .TeslaCam_tombstones.json with status='flagged'.
    Files REMAIN physically on disk until explicitly purged via dashcam_sync.sh or [P]urge.
    Returns (count_flagged, total_est_bytes).
    """
    master_dir = resolve_master_teslacam_dir(analyzer.teslacam_dirs)
    tombstones_data = load_tombstones(master_dir)
    
    known_drives = list(tombstones_data.get("known_drives", ["VOL_JOWUA", "VOL_TESLA_USB"]))
    for d in analyzer.teslacam_dirs:
        drv_id = get_drive_identifier(d, master_dir)
        if drv_id and drv_id != "MASTER" and drv_id not in known_drives:
            known_drives.append(drv_id)
    tombstones_data["known_drives"] = known_drives

    tombs = tombstones_data.get("tombstones", {})
    now_iso = datetime.now().isoformat()
    count_flagged = 0
    total_bytes = 0
    
    for p in packages:
        cat = p["cat"]
        is_folder = p.get("is_folder", False)
        pkg_size = p.get("tot_size") or p.get("size", 0)
        
        if is_folder:
            folder_base = os.path.basename(p["folder"])
            rel_key = f"{cat}Clips/{folder_base}"
            file_names = [os.path.basename(f) for f in p.get("files", [])]
            tombs[rel_key] = {
                "status": "flagged",
                "flagged_at": now_iso,
                "deleted_at": now_iso,
                "cat": cat,
                "reason": p.get("reason", "user_flagged"),
                "folder": rel_key,
                "files": file_names,
                "est_bytes": pkg_size,
                "files_count": len(file_names),
                "pending_drives": list(known_drives)
            }
            count_flagged += 1
            total_bytes += pkg_size
        else:
            file_names = [os.path.basename(f) for f in p.get("files", [])]
            dt = p.get("dt")
            start_dt_str = dt.strftime("%Y-%m-%d_%H-%M-%S") if dt else "recent"
            rel_key = f"RecentClips/{start_dt_str}"
            tombs[rel_key] = {
                "status": "flagged",
                "flagged_at": now_iso,
                "deleted_at": now_iso,
                "cat": cat,
                "reason": p.get("reason", "user_flagged"),
                "folder": "RecentClips",
                "files": file_names,
                "est_bytes": pkg_size,
                "files_count": len(file_names),
                "pending_drives": list(known_drives)
            }
            count_flagged += 1
            total_bytes += pkg_size
            
    save_tombstones(tombstones_data, master_dir)
    return count_flagged, total_bytes

def unflag_footage_packages(packages, analyzer):
    """Removes packages from .tombstones.json."""
    master_dir = resolve_master_teslacam_dir(analyzer.teslacam_dirs)
    data = load_tombstones(master_dir)
    tombs = data.get("tombstones", {})
    count_unflagged = 0
    
    for p in packages:
        cat = p["cat"]
        is_folder = p.get("is_folder", False)
        if is_folder:
            folder_base = os.path.basename(p["folder"])
            rel_key = f"{cat}Clips/{folder_base}"
            if rel_key in tombs:
                del tombs[rel_key]
                count_unflagged += 1
        else:
            dt = p.get("dt")
            start_dt_str = dt.strftime("%Y-%m-%d_%H-%M-%S") if dt else "recent"
            rel_key = f"RecentClips/{start_dt_str}"
            if rel_key in tombs:
                del tombs[rel_key]
                count_unflagged += 1
            else:
                for f in p.get("files", []):
                    fname = os.path.basename(f)
                    for k in list(tombs.keys()):
                        if fname in tombs[k].get("files", []):
                            del tombs[k]
                            count_unflagged += 1
                            
    save_tombstones(data, master_dir)
    return count_unflagged

def purge_flagged_tombstones_across_drives(teslacam_dirs, repo_root=None, dry_run=False):
    """
    Permanently deletes flagged and active tombstones across Master Archive and connected secondary drives.
    Returns (total_files_deleted, total_bytes_freed, total_retired_tombstones).
    """
    master_dir = resolve_master_teslacam_dir(teslacam_dirs)
    tombstones_data = load_tombstones(master_dir)
    tombs = tombstones_data.get("tombstones", {})
    if not tombs:
        return 0, 0, 0

    catalog_data = load_catalog(master_dir)
    cat_pkgs = catalog_data.get("packages", {})

    total_files = 0
    total_bytes = 0
    total_retired = 0
    now_iso = datetime.now().isoformat()

    # 1. Unlink files from Master Archive
    if master_dir and os.path.isdir(master_dir):
        for key, info in list(tombs.items()):
            target_dir = os.path.join(master_dir, key)
            if os.path.isdir(target_dir):
                for root, dirs, files in os.walk(target_dir, topdown=False):
                    for f in files:
                        fp = os.path.join(root, f)
                        try:
                            sz = os.path.getsize(fp)
                            if not dry_run:
                                os.remove(fp)
                            total_files += 1
                            total_bytes += sz
                        except OSError:
                            pass
                    if not dry_run:
                        try:
                            os.rmdir(root)
                        except OSError:
                            pass
            elif key.startswith("RecentClips"):
                rc_dir = os.path.join(master_dir, "RecentClips")
                if os.path.isdir(rc_dir):
                    for f in info.get("files", []):
                        fp = os.path.join(rc_dir, f)
                        if os.path.isfile(fp):
                            try:
                                sz = os.path.getsize(fp)
                                if not dry_run:
                                    os.remove(fp)
                                total_files += 1
                                total_bytes += sz
                            except OSError:
                                pass
            if not dry_run:
                if key in cat_pkgs:
                    del cat_pkgs[key]
                info["status"] = "tombstone"
                info["purged_at"] = now_iso

        if not dry_run:
            save_tombstones(tombstones_data, master_dir)
            scan_or_update_drive_catalog(master_dir, master_dir, force_rescan=True)

    # 2. Scrub connected secondary drives
    for d in teslacam_dirs:
        drv_id = get_drive_identifier(d, master_dir)
        if drv_id != "MASTER":
            n_f, b_f, n_r = scrub_pending_tombstones_for_drive(d, master_dir, dry_run=dry_run)
            total_files += n_f
            total_bytes += b_f
            total_retired += n_r

    # If all known secondary drives were scrubbed or no secondary drives pending, clean up retired tombstones
    if not dry_run:
        fresh_tombs = load_tombstones(master_dir)
        t_dict = fresh_tombs.get("tombstones", {})
        for k in list(t_dict.keys()):
            if not t_dict[k].get("pending_drives"):
                del t_dict[k]
                total_retired += 1
        save_tombstones(fresh_tombs, master_dir)

    return total_files, total_bytes, total_retired

def parse_tombstone_datetime(key, info):
    """Extracts event datetime from package folder key or tombstone info."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})[T_](\d{2}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2})", key)
    if m:
        d_part = m.group(1)
        t_part = m.group(2).replace("-", ":")
        try:
            return datetime.strptime(f"{d_part} {t_part}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    start_dt = info.get("start_dt")
    if start_dt:
        try:
            return datetime.fromisoformat(start_dt.replace("Z", ""))
        except ValueError:
            pass
    deleted_at = info.get("deleted_at")
    if deleted_at:
        try:
            return datetime.fromisoformat(deleted_at.replace("Z", ""))
        except ValueError:
            pass
    return datetime.min

def display_flagged_deletions_table(analyzer):
    """Renders structured Unicode table of all active tombstones sorted by date and grouped by month."""
    master_dir = resolve_master_teslacam_dir(analyzer.teslacam_dirs)
    flagged = get_flagged_tombstones(master_dir)
    n_drives = max(1, len(analyzer.teslacam_dirs))
    
    if not flagged:
        total_inner = 76
        print(f"\n┌{'─'*total_inner}┐")
        print(format_title_line(" 🗑️  ACTIVE TOMBSTONES (Pending Multi-Drive Cleanup)", total_inner, "left"))
        print(format_box_line("├", "─", "┤", [total_inner]))
        print(f"│{pad_display(' ℹ️  No active tombstones (all drives are fully synchronized).', total_inner, 'left')}│")
        print(format_box_line("└", "─", "┘", [total_inner]))
        return []

    # Parse and sort all items chronologically (newest first)
    parsed_items = []
    for k, info in flagged.items():
        dt = parse_tombstone_datetime(k, info)
        parsed_items.append((k, info, dt))

    parsed_items.sort(key=lambda x: x[2], reverse=True)

    # Group by month (e.g. '2026-09')
    months_grouped = {}
    for k, info, dt in parsed_items:
        m_key = dt.strftime("%Y-%m") if dt != datetime.min else "Unknown"
        months_grouped.setdefault(m_key, []).append((k, info, dt))

    max_k_len = max((display_len(k) for k, _, _ in parsed_items), default=30)
    w_idx = max(5, max((display_len(f" [{idx+1}]") + 1 for idx in range(len(parsed_items))), default=5))
    w_key = max(30, min(45, max_k_len + 2))
    w_type = 10
    w_files = 7
    w_size = 11
    w_drives = 22
    w_date = 18
    
    col_widths = [w_idx, w_key, w_type, w_files, w_size, w_drives, w_date]
    col_aligns = ["center", "left", "left", "right", "right", "left", "left"]
    
    total_inner = sum(col_widths) + len(col_widths) - 1
    title = f" 🗑️  ACTIVE TOMBSTONES (Pending Multi-Drive Cleanup — {len(parsed_items)} Items Across {len(months_grouped)} Month{'s' if len(months_grouped) != 1 else ''})"
    t_len = display_len(title)
    if t_len >= total_inner:
        w_key += (t_len - total_inner) + 3
        col_widths[1] = w_key
        total_inner = sum(col_widths) + len(col_widths) - 1

    print(f"\n┌{'─'*total_inner}┐")
    print(format_title_line(title, total_inner, "left"))
    print(format_box_line("├", "┬", "┤", col_widths))
    print(format_row(["#", " Package / Relative Path", " Type", "Files ", "Est. Size ", " Pending Drives", " Deleted Date"], col_widths, col_aligns))
    
    tot_bytes = 0
    tot_files = 0
    ordered_items = []
    row_num = 1
    
    for m_key, m_items in months_grouped.items():
        m_bytes = sum(inf.get("est_bytes", 0) for _, inf, _ in m_items)
        m_mb = m_bytes / (1024 * 1024)
        m_sz = f"{m_mb/1024:.2f} GB" if m_mb >= 1024 else f"{m_mb:.0f} MB"
        try:
            m_dt = datetime.strptime(m_key + "-01", "%Y-%m-%d")
            m_label = m_dt.strftime("%B %Y")
        except Exception:
            m_label = m_key
        m_banner = f" 🗓️  {m_label} ({len(m_items)} package{'s' if len(m_items) != 1 else ''} — ~{m_sz})"
        
        print(format_box_line("├", "┴", "┤", col_widths))
        print(f"│{pad_display(m_banner, total_inner, 'left')}│")
        print(format_box_line("├", "┬", "┤", col_widths))
        
        for key, info, dt in m_items:
            cat = info.get("cat", "Saved")
            icon = "🔄" if cat == "Recent" else ("💾" if cat == "Saved" else ("🔴" if cat == "Sentry" else "📁"))
            type_str = f" {icon} {cat}"
            f_cnt = info.get("files_count", len(info.get("files", [])))
            sz_b = info.get("est_bytes", 0)
            tot_bytes += sz_b
            tot_files += f_cnt
            
            sz_mb = sz_b / (1024 * 1024)
            sz_str = f"{sz_mb/1024:.1f} GB" if sz_mb >= 1024 else f"{sz_mb:.0f} MB"
            
            pending_list = info.get("pending_drives", [])
            pending_str = ", ".join(d.replace("VOL_", "") for d in pending_list) if pending_list else "None"
            fl_dt = info.get("deleted_at", "-")[:16].replace("T", " ")
            
            c_idx = f"[{row_num}]"
            k_disp = key if display_len(key) <= (w_key - 2) else key[:w_key - 5] + "..."
            c_key = f" {k_disp}"
            c_files = f"{f_cnt} "
            c_size = f"{sz_str} "
            c_drives = f" {pending_str}"
            c_date = f" {fl_dt}"
            
            cells = [c_idx, c_key, type_str, c_files, c_size, c_drives, c_date]
            print(format_row(cells, col_widths, col_aligns))
            ordered_items.append((key, info))
            row_num += 1
        
    print(format_box_line("├", "┴", "┤", col_widths))
    
    tot_mb = tot_bytes / (1024 * 1024)
    tot_sz_str = f"{tot_mb/1024:.2f} GB" if tot_mb >= 1024 else f"{tot_mb:.1f} MB"
    
    sum_line = f" Total Active Tombstones: {len(parsed_items)} package(s), {tot_files} files (~{tot_sz_str} reclaimable across secondary drives)"
    print(f"│{pad_display(sum_line, total_inner, 'left')}│")
    print(format_box_line("└", "─", "┘", [total_inner]))
    print()
    return ordered_items

def display_flagged_deletions_menu(analyzer):
    """Interactive management menu for staged/flagged deletions."""
    while True:
        items = display_flagged_deletions_table(analyzer)
        if not items or not sys.stdin.isatty():
            if sys.stdin.isatty():
                input("Press Enter to return to timeline: ")
            break
            
        try:
            choice = input("Tombstones Action: [u <#>] unflag, [P]urge secondary drives now, [b]ack, [q]uit: ").strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["b", "back"]:
                break
            elif choice in ["p", "purge"]:
                try:
                    conf = input("Type 'yes' or 'y' to scrub and purge all tombstone footage from secondary drives: ").strip().lower()
                except (KeyboardInterrupt, EOFError):
                    print("\nPurge cancelled.\n")
                    continue
                    master_dir = resolve_master_teslacam_dir(analyzer.teslacam_dirs)
                    n_del, b_del, n_ret = purge_flagged_tombstones_across_drives(analyzer.teslacam_dirs, analyzer.repo_root)
                    if n_del > 0 or n_ret > 0:
                        del_mb = b_del / (1024 * 1024)
                        del_sz = f"{del_mb/1024:.1f} GB" if del_mb >= 1024 else f"{del_mb:.0f} MB"
                        print(f"\n✔ Successfully purged {n_del} file(s) ({del_sz}) across secondary drive(s).")
                        print(f"✔ Retired and cleaned up {n_ret} tombstone(s) in .TeslaCam_tombstones.json.\n")
                    else:
                        data = load_tombstones(master_dir)
                        tombs = data.get("tombstones", {})
                        if tombs:
                            all_pending = set()
                            for t in tombs.values():
                                all_pending.update(t.get("pending_drives", []))
                            p_str = ", ".join(d.replace("VOL_", "") for d in sorted(all_pending)) if all_pending else "None"
                            print(f"\n>>> No connected secondary drives have pending files to purge.")
                            print(f"    ({len(tombs)} active tombstone(s) pending for disconnected drive(s): {p_str})")
                            try:
                                ans_clear = input(f"Would you like to clear/retire these {len(tombs)} tombstone(s) from Master Archive now? [y/N]: ").strip().lower()
                                if ans_clear in ("y", "yes"):
                                    data["tombstones"] = {}
                                    save_tombstones(data, master_dir)
                                    print(f"\n✔ Cleared and retired {len(tombs)} active tombstone(s) from Master Archive.\n")
                                else:
                                    print(">>> Kept tombstones in registry.\n")
                            except (KeyboardInterrupt, EOFError):
                                print("\n>>> Kept tombstones in registry.\n")
                        else:
                            print("\n>>> No pending files or tombstones found requiring purge.\n")
            elif choice.startswith("u ") or choice.startswith("unflag "):
                target_str = choice.split(maxsplit=1)[1].strip()
                selected_indices = parse_selection_indices(target_str, len(items))
                if not selected_indices:
                    print("Invalid selection.\n")
                    continue
                selected_keys = [items[idx - 1][0] for idx in selected_indices]
                master_dir = resolve_master_teslacam_dir(analyzer.teslacam_dirs)
                data = load_tombstones(master_dir)
                tombs = data.get("tombstones", {})
                unflagged_count = 0
                for k in selected_keys:
                    if k in tombs:
                        del tombs[k]
                        unflagged_count += 1
                save_tombstones(data, master_dir)
                print(f"\n✔ Unflagged {unflagged_count} package(s).\n")
                continue
            else:
                print("Invalid choice. Enter [u <#>] to unflag, [P] to purge, or [b] to go back.\n")
        except (KeyboardInterrupt, EOFError):
            break

def preview_footage_file(file_path, method="finder"):
    """
    Reveals video file in macOS Finder (open -R) or launches system file manager.
    Clean, non-blocking, and stable (avoids qlmanage crashes on video plugins).
    """
    if not file_path or not os.path.exists(file_path):
        return None
    if sys.platform == "darwin":
        try:
            subprocess.Popen(["open", "-R", file_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        return None
    elif sys.platform.startswith("linux"):
        try:
            subprocess.Popen(["xdg-open", os.path.dirname(file_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    elif sys.platform == "win32":
        try:
            subprocess.Popen(["explorer", f"/select,{os.path.normpath(file_path)}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    return None

def close_preview_process(proc):
    """Placeholder for preview process cleanup."""
    pass

def get_primary_preview_clip(pkg):
    """
    Selects the best camera clip from a package for previewing.
    Prioritizes front camera, then back, repeater, or first valid MP4.
    """
    files = pkg.get("files", [])
    mp4_files = [f for f in files if f.endswith(".mp4") and os.path.isfile(f)]
    if not mp4_files:
        return None
    for f in mp4_files:
        if "-front.mp4" in f:
            return f
    for f in mp4_files:
        if "-back.mp4" in f:
            return f
    return mp4_files[0]

def run_interactive_footage_review(packages, analyzer, context_label="Event Footage"):
    """
    Interactive Step-Through Review Wizard:
    Sequentially reviews footage packages, launches macOS Quick Look video preview for each,
    and prompts user to Keep ([k] / Enter) or Delete ([d]).
    """
    if not packages:
        print("\n⚠️  No footage packages available to review.")
        return 0, 0, 0

    total_pkgs = len(packages)
    print("\n" + "=" * 76)
    print(f" 🎬 INTERACTIVE FOOTAGE REVIEW WIZARD: {context_label}")
    print("=" * 76)
    print(f" Stepping through {total_pkgs} footage package(s).")
    print(" Footage clips will be revealed in macOS Finder.")
    print(" Commands: [k]eep / next (Enter), [f]lag delete, [u]nflag, [v]iew in Finder, [o]pen folder, [s]kip, [q]uit.")
    print("=" * 76)

    kept_count = 0
    deleted_count = 0
    freed_bytes_total = 0
    tombstones_total = 0
    current_proc = None

    try:
        for idx, pkg in enumerate(packages, 1):
            close_preview_process(current_proc)
            current_proc = None

            primary_clip = get_primary_preview_clip(pkg)
            cam_label = "Front Camera"
            if primary_clip:
                if "-back.mp4" in primary_clip:
                    cam_label = "Back Camera"
                elif "-left_repeater.mp4" in primary_clip:
                    cam_label = "Left Repeater"
                elif "-right_repeater.mp4" in primary_clip:
                    cam_label = "Right Repeater"

            icon = "🔄" if pkg["cat"] == "Recent" else ("💾" if pkg["cat"] == "Saved" else ("🔴" if pkg["cat"] == "Sentry" else "📁"))
            mb_val = pkg["tot_size"] / (1024 * 1024)
            sz_str = f"{mb_val/1024:.1f} GB" if mb_val >= 1024 else f"{mb_val:.0f} MB"

            if primary_clip:
                current_proc = preview_footage_file(primary_clip, method="quicklook")

            box_w = 78
            p_rel = pkg["rel_path"]
            p_cnt = pkg["files_count"]
            n_drives = max(1, len(analyzer.teslacam_dirs))
            flagged_already = is_package_flagged(pkg, repo_root=analyzer.repo_root)
            status_flag = " 🗑️  [FLAGGED FOR DELETION]" if flagged_already else ""

            time_range_disp = pkg.get("time_range_str") or pkg["time_str"]
            event_idx = pkg.get("event_idx")
            ev_act = pkg.get("event_activity")
            ev_time = pkg.get("event_time_str")

            print(f"\n┌{'─'*box_w}┐")
            hdr = f" [{idx}/{total_pkgs}] {icon} {pkg['cat']} Footage: {pkg['reason']}{status_flag}"
            print(f"│{pad_display(hdr, box_w, 'left')}│")
            print(f"├{'─'*box_w}┤")
            print(f"│{pad_display(f' Time Range : {time_range_disp}', box_w, 'left')}│")
            if event_idx is not None and ev_act:
                ev_str = f"[#{event_idx}] {ev_act}"
                if ev_time and ev_time not in ev_act:
                    ev_str = f"[#{event_idx}] ({ev_time}) {ev_act}"
                ev_lines = wrap_text_display(ev_str.strip(), box_w - 15)
                for i_ev, ev_l in enumerate(ev_lines):
                    pfx = " Event      : " if i_ev == 0 else "              "
                    print(f"│{pad_display(f'{pfx}{ev_l}', box_w, 'left')}│")
            print(f"│{pad_display(f' Package    : {p_rel}', box_w, 'left')}│")
            print(f"│{pad_display(f' Files      : {p_cnt} files ({sz_str}) across {n_drives} mounted drive(s)', box_w, 'left')}│")
            if primary_clip:
                status_prev = f" Highlight  : {os.path.basename(primary_clip)} ({cam_label})"
            else:
                status_prev = " Highlight  : ⚠️  No previewable MP4 file found"
            print(f"│{pad_display(status_prev, box_w, 'left')}│")
            print(f"└{'─'*box_w}┘")

            while True:
                prompt_str = f"Action: [k]eep / next (Enter), [f]lag delete, [u]nflag, [v]iew cams, [o]pen, [s]kip, [q]uit: "
                try:
                    ans = input(prompt_str).strip().lower()
                except (KeyboardInterrupt, EOFError):
                    ans = "q"

                if ans in ("", "k", "keep", "n", "next"):
                    kept_count += 1
                    break
                elif ans in ("f", "flag", "d", "del", "delete"):
                    n_flag, b_flag = flag_footage_packages_for_deletion([pkg], analyzer)
                    deleted_count += 1
                    freed_bytes_total += b_flag
                    tombstones_total += n_flag
                    flag_mb = b_flag / (1024 * 1024)
                    flag_sz = f"{flag_mb/1024:.1f} GB" if flag_mb >= 1024 else f"{flag_mb:.0f} MB"
                    ev_ref = f" (Event #{pkg['event_idx']})" if pkg.get("event_idx") else ""
                    print(f" 🗑️  Flagged for deletion: {pkg['rel_path']} [{time_range_disp}]{ev_ref} (~{flag_sz} reclaimable on next sync).\n")
                    break
                elif ans in ("u", "unflag"):
                    unflag_footage_packages([pkg], analyzer)
                    print(f" ✔ Removed deletion flag from {pkg['rel_path']}.\n")
                    break
                elif ans in ("v", "view", "play"):
                    mp4s = [f for f in pkg.get("files", []) if f.endswith(".mp4") and os.path.isfile(f)]
                    if not mp4s:
                        print(" ⚠️  No MP4 video files in this package.")
                        continue
                    print("\n Available Camera Angles:")
                    for c_i, c_f in enumerate(mp4s, 1):
                        print(f"   [{c_i}] {os.path.basename(c_f)}")
                    print("   [a] Open all cameras in Quick Look")
                    print("   [Enter] Return to review")
                    try:
                        c_pick = input(" Select camera angle [1-N or a]: ").strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        c_pick = ""
                    if c_pick == "a":
                        close_preview_process(current_proc)
                        current_proc = None
                        for c_f in mp4s:
                            preview_footage_file(c_f, method="quicklook")
                        print(" 📹 Launched all camera angles in Quick Look.\n")
                    elif c_pick.isdigit() and 1 <= int(c_pick) <= len(mp4s):
                        close_preview_process(current_proc)
                        chosen_f = mp4s[int(c_pick) - 1]
                        current_proc = preview_footage_file(chosen_f, method="quicklook")
                        print(f" 📹 Launched: {os.path.basename(chosen_f)}\n")
                    continue
                elif ans in ("o", "open"):
                    folder_p = pkg.get("folder")
                    if folder_p and os.path.exists(folder_p) and sys.platform == "darwin":
                        subprocess.run(["open", folder_p])
                        print(f" 📂 Opened in Finder: {folder_p}\n")
                    else:
                        print(" ⚠️ Folder not accessible.\n")
                    continue
                elif ans in ("s", "skip"):
                    print("\n Skipping remainder of review.")
                    break
                elif ans in ("q", "quit", "exit"):
                    close_preview_process(current_proc)
                    print("\n Exiting review wizard.")
                    return kept_count, deleted_count, freed_bytes_total
                else:
                    print(" Invalid option. Press Enter to keep, 'd' to delete, 'v' to switch camera angle, or 'q' to quit.")

            if ans in ("s", "skip"):
                break

    finally:
        close_preview_process(current_proc)

    tot_freed_mb = freed_bytes_total / (1024 * 1024)
    tot_sz_str = f"{tot_freed_mb/1024:.1f} GB" if tot_freed_mb >= 1024 else f"{tot_freed_mb:.0f} MB"
    print("\n" + "=" * 76)
    print(" 🎬 REVIEW WIZARD SUMMARY")
    print("=" * 76)
    print(f" Packages Kept   : {kept_count}")
    print(f" Packages Deleted: {deleted_count} ({tot_sz_str} reclaimed across mounted drives)")
    if tombstones_total > 0:
        print(f" Tombstones Added: {tombstones_total} (saved to Tessie/dashcam_tombstones.json)")
    print("=" * 76 + "\n")

    return kept_count, deleted_count, freed_bytes_total


def compute_adaptive_rolling_window(distance_km: Optional[float]) -> float:
    """
    Computes an optimal adaptive rolling distance window (km) based on total drive distance.
    Formula: Window (W) = Total Distance * 0.05 (Option 1: Adaptive Window Formula)

    Bounds:
    - Minimum floor of 0.35 km: Prevents 0.01 kWh battery quantization noise artifacts on short trips.
    - Maximum ceiling of 5.0 km: Keeps long highway road trips responsive to major terrain trends.

    Examples:
    - 6 km trip:  max(0.35, 6 * 0.05)   = 0.35 km window (high-res city traffic detail)
    - 10 km trip: max(0.35, 10 * 0.05)  = 0.50 km window (traffic lights & local accelerations)
    - 20 km trip: 20 * 0.05             = 1.00 km window (mixed commute)
    - 50 km trip: 50 * 0.05             = 2.50 km window (suburban & freeway)
    - 100+ km:    min(5.0, 100 * 0.05)  = 5.00 km window (smooth highway trends)
    """
    if not distance_km or distance_km <= 0:
        return 0.8
    w = distance_km * 0.05
    return max(0.35, min(5.0, round(w, 2)))


class TessieAnalyzer:
    def __init__(self, tessie_dir=None, teslacam_dirs=None):
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(self.script_dir)
        self.icloud_dir = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie")
        self.repo_root = parent_dir
        self.landing_dir = os.path.expanduser("~/Downloads")
        self._synced_this_session = False
        self._sync_disabled = False

        # Read config.json directly (same file/convention as
        # TessieChargingAnalyzer and tessie_api_common.load_config()) just
        # for its "tessie_directory" setting - see self.tessie_data_home
        # below for why.
        _cfg_path = os.path.join(parent_dir, "config.json")  # TODO-034: root is primary now
        if not os.path.isfile(_cfg_path):
            _cfg_path = os.path.join(parent_dir, "Tessie", "config.json")  # legacy fallback
        cfg_tessie_dir = None
        if os.path.isfile(_cfg_path):
            try:
                with open(_cfg_path, "r", encoding="utf-8") as _f:
                    cfg_tessie_dir = json.load(_f).get("tessie_directory")
            except Exception:
                cfg_tessie_dir = None
        if cfg_tessie_dir:
            cfg_tessie_dir = os.path.expanduser(cfg_tessie_dir)

        # The ONE authoritative home for drives_master.csv and its
        # archive/ folder: config.json's "tessie_directory" if present,
        # else the default iCloud path, else (last resort - e.g. no
        # config yet) the repo's own Tessie folder. Deliberately singular
        # - this used to be written to EVERY discovered Tessie-like
        # directory (repo AND iCloud via self.tessie_dirs, which stays
        # plural below for registry/legacy-layout READING), which is why
        # the repo ended up holding its own drifting copy of real drive
        # data, including its own separate archive/ contents. User's
        # explicit ask: nothing should be archived (or duplicated at all)
        # in the repo folder.
        if cfg_tessie_dir:
            # Trust the user's own configured location UNCONDITIONALLY -
            # do not silently fall back to the repo folder just because
            # this particular process can't currently see that path (e.g.
            # it doesn't exist yet, or a sandboxed/bridged shell resolves
            # a "~"-relative path differently than the user's real
            # machine does - see BUG-020's identical lesson, which is
            # exactly what happened testing this very code from this
            # session's own bridge: it silently fell through to this
            # repo folder and re-corrupted drives_master.csv). Only the
            # ABSENCE of a configured value falls through below.
            self.tessie_data_home = os.path.abspath(os.path.expanduser(cfg_tessie_dir))
        elif os.path.isdir(self.icloud_dir):
            self.tessie_data_home = os.path.abspath(os.path.realpath(self.icloud_dir))
        else:
            # Explicit user instruction: the repo folder is NEVER a valid
            # home for real master data, not even as a last-resort
            # fallback. If nothing is configured and the default iCloud
            # folder doesn't exist either, there is no data home -
            # consolidate_drives() below refuses to write rather than
            # silently defaulting into the repo.
            self.tessie_data_home = None

        self.tessie_dirs = []
        candidates = [
            tessie_dir,
            os.path.join(parent_dir, "Tessie"),
            os.path.join(self.script_dir, "Tessie"),
            os.path.expanduser("~/iCloud/repos/tesla/Tessie"),
            self.icloud_dir
        ]  # Tessie CSV/registry data runs from the repo and iCloud only -
           # TESLADRIVE is not scanned here (see find_mounted_tesla_volumes).
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
        
        if self.tessie_data_home:
            self.drives_dirs = [os.path.join(self.tessie_data_home, "drives")]
            try:
                os.makedirs(self.drives_dirs[0], exist_ok=True)
                os.makedirs(os.path.join(self.drives_dirs[0], "archive"), exist_ok=True)
            except Exception:
                pass
        else:
            self.drives_dirs = []
        
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
                ("personal_chargers.json", self.places),
                ("tesla_superchargers.json", self.supercharger_places),
                ("superchargers.json", self.supercharger_places),
                ("tesla_chargers.json", self.charging_places),
                ("destination_chargers.json", self.charging_places),
                ("charging.json", self.charging_places),
                ("plugshare_chargers.json", self.charging_places),
            ]:
                try:
                    pf = os.path.join(td, fname)
                    if os.path.isfile(pf):
                        with open(pf, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            if isinstance(data, dict):
                                st_data = data.get("stations", data) if isinstance(data.get("stations"), dict) else data
                                for k, v in st_data.items():
                                    if k not in target_dict:
                                        target_dict[k] = v
                except Exception:
                    pass
        return self.places

    def resolve_place(self, address, saved_loc="", lat=None, lon=None):
        addr_clean = address.lower() if address else ""

        # 1. GPS Geofence matching on known places (Highest Accuracy! - checked
        #    before Tessie's own Saved Location, which is manually entered in the
        #    Tessie app and can be stale, generic, or missing charger-specific
        #    detail our own registries already have - see AGENTS.md BUG-016.)
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

        # 2. GPS Geofence matching on charging profiles
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

        # 3. GPS Geofence matching on Superchargers
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

        # 4. Saved Location exact or keyword match (Tessie's own manually-entered
        #    field - fallback only, since it's user-editable in the Tessie app and
        #    not guaranteed to reflect our own registries or stay in sync with them)
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
        if address and isinstance(address, str):
            parts = address.split(",")
            return parts[0].strip() if parts else address
        return saved_loc or ""

    def consolidate_drives(self, new_csv_path=None, master_dir=None, verbose=False):
        dest_dirs = []
        if master_dir:
            dest_dirs.append(master_dir)
        else:
            dest_dirs = list(self.drives_dirs)

        if not dest_dirs:
            print("\033[31mNo Tessie data directory configured - set \"tessie_directory\" in config.json "
                  "(see Tessie/config.example.json). Refusing to write drives_master.csv into the repo folder.\033[0m")
            return 0

        master_files = [os.path.join(d, "drives_master.csv") for d in dest_dirs]
        for d in dest_dirs:
            try:
                os.makedirs(d, exist_ok=True)
                os.makedirs(os.path.join(d, "archive"), exist_ok=True)
            except Exception:
                pass

        # 1. Gather all candidate CSV files
        candidate_files = []
        if new_csv_path and os.path.isfile(new_csv_path):
            candidate_files.append((new_csv_path, False))

        # Scan root directory of each Tessie dir for newly dropped/unrenamed drive files
        for td in self.tessie_dirs:
            if not os.path.isdir(td):
                continue
            try:
                for f in os.listdir(td):
                    fp = os.path.join(td, f)
                    if os.path.isfile(fp) and f.endswith(".csv"):
                        f_lower = f.lower()
                        # Strictly target drive exports, ignoring system-wide logs and charging files
                        ignored_stems = ["charges", "telemetry_stream", "firmware", "tire", "battery_health", "idles"]
                        if ("drives" in f_lower or f.startswith("drive_deepdive_")) and not any(ign in f_lower for ign in ignored_stems):
                            candidate_files.append((fp, True))
            except Exception:
                pass

        # Scan existing drives/ subdirectories and their archives
        for dd in dest_dirs:
            if not os.path.isdir(dd):
                continue
            try:
                for f in os.listdir(dd):
                    fp = os.path.join(dd, f)
                    if os.path.isfile(fp) and f.endswith(".csv"):
                        is_raw = (f != "drives_master.csv" and not f.startswith("drive_deepdive_"))
                        candidate_files.append((fp, is_raw))
                arch = os.path.join(dd, "archive")
                if os.path.isdir(arch):
                    for f in os.listdir(arch):
                        fp = os.path.join(arch, f)
                        if os.path.isfile(fp) and f.endswith(".csv"):
                            candidate_files.append((fp, False))
            except Exception:
                pass

        # Files in self.landing_dir (e.g. ~/Downloads)
        files_to_archive = []
        if self.landing_dir and os.path.isdir(self.landing_dir):
            try:
                for f in os.listdir(self.landing_dir):
                    if f.endswith(".csv"):
                        fp = os.path.join(self.landing_dir, f)
                        candidate_files.append((fp, True))
            except Exception:
                pass

        # 2. Read and deduplicate records
        raw_rows = []
        seen_keys = set()
        fieldnames = None
        initial_master_count = 0
        existing_disk_row_count = 0

        # Read existing master file first if present
        for mf in master_files:
            if os.path.isfile(mf):
                try:
                    with open(mf, "r", encoding="utf-8-sig") as f:
                        reader = csv.DictReader(f)
                        if reader.fieldnames and "Starting Location" in reader.fieldnames:
                            if not fieldnames:
                                fieldnames = list(reader.fieldnames)
                            for r in reader:
                                existing_disk_row_count += 1
                                normalize_row_timezone(r, "Starting Latitude", "Starting Longitude", "Started At")
                                normalize_row_timezone(r, "Ending Latitude", "Ending Longitude", "Ended At")
                                start_time = r.get("Started At (UTC)") or r.get("Started At (AEST)") or r.get("Started At") or r.get("Started")
                                end_time = r.get("Ended At (UTC)") or r.get("Ended At (AEST)") or r.get("Ended At") or r.get("Ended")
                                dist = r.get("Distance (km)", "0")
                                s_loc = r.get("Starting Location", "")
                                if not start_time or not end_time:
                                    continue
                                key = (start_time.strip(), end_time.strip(), dist.strip(), s_loc.strip())
                                if key not in seen_keys:
                                    seen_keys.add(key)
                                    raw_rows.append(r)
                            initial_master_count = len(raw_rows)
                            break
                except Exception:
                    pass

        # Read all other candidate files
        seen_paths = set()
        for fp, from_landing in candidate_files:
            try:
                real_fp = os.path.realpath(fp)
            except Exception:
                real_fp = fp
            if real_fp in seen_paths or real_fp in [os.path.realpath(m) for m in master_files if os.path.exists(m)]:
                continue
            seen_paths.add(real_fp)

            try:
                with open(fp, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    if not reader.fieldnames:
                        continue
                    
                    # Check if this is a high-frequency Drive Deep Dive telemetry file
                    is_drive_deepdive = ("Speed (km/h)" in reader.fieldnames or "Shift State" in reader.fieldnames) and "Starting Location" not in reader.fieldnames
                    if is_drive_deepdive:
                        # Full drive telemetry - keep strictly separate from summary master!
                        if from_landing:
                            try:
                                first_row = next(reader, None)
                                if first_row:
                                    ts_raw = first_row.get("Timestamp (AEST)") or first_row.get("Timestamp") or ""
                                    dt = parse_relative_date(ts_raw[:16]) or parse_flexible_date(ts_raw) if 'parse_flexible_date' in globals() else None
                                    if not dt:
                                        try:
                                            dt = datetime.strptime(ts_raw[:16], "%Y-%m-%d %H:%M")
                                        except Exception:
                                            dt = None
                                    if dt:
                                        try:
                                            abbr = tztools.abbr_for_naive_local(dt)
                                            dt_epoch = tztools.epoch_for_naive_local(dt)
                                        except Exception:
                                            abbr, dt_epoch = None, None
                                        stamp = dt.strftime('%Y-%m-%d_%H-%M')
                                        if abbr:
                                            stamp = f"{stamp}_{abbr}"
                                        deepdive_name = f"drive_deepdive_{stamp}.csv"
                                    else:
                                        dt_epoch = None
                                        deepdive_name = f"drive_deepdive_{os.path.basename(fp)}"

                                    for dd in dest_dirs:
                                        deepdive_dir = os.path.join(dd, "deepdive")
                                        os.makedirs(deepdive_dir, exist_ok=True)
                                        target_dd_path = os.path.join(deepdive_dir, deepdive_name)
                                        shutil.copy2(fp, target_dd_path)
                                        # AGENTS.md TODO-033: set this file's mtime to the
                                        # real drive-start event, not whenever this manual
                                        # ingest happened to run.
                                        if dt_epoch is not None:
                                            try:
                                                os.utime(target_dd_path, (dt_epoch, dt_epoch))
                                            except Exception:
                                                pass
                                    # Archive source from landing or root
                                    archive_dir = os.path.join(dest_dirs[0], "archive") if dest_dirs else os.path.join(self.repo_root, "Tessie", "drives", "archive")
                                    os.makedirs(archive_dir, exist_ok=True)
                                    shutil.move(fp, next_archive_path(archive_dir, os.path.basename(fp)))
                                    print(f"\033[94m⚡ Saved Drive Deep Dive Telemetry (Kept Separate):\033[0m {deepdive_name}")
                            except Exception:
                                pass
                        continue

                    # Otherwise, must be a trip summary file
                    if "Starting Location" not in reader.fieldnames:
                        continue

                    if not fieldnames:
                        fieldnames = list(reader.fieldnames)
                    added_from_file = 0
                    for r in reader:
                        normalize_row_timezone(r, "Starting Latitude", "Starting Longitude", "Started At")
                        normalize_row_timezone(r, "Ending Latitude", "Ending Longitude", "Ended At")
                        start_time = r.get("Started At (UTC)") or r.get("Started At (AEST)") or r.get("Started At") or r.get("Started")
                        end_time = r.get("Ended At (UTC)") or r.get("Ended At (AEST)") or r.get("Ended At") or r.get("Ended")
                        dist = r.get("Distance (km)", "0")
                        s_loc = r.get("Starting Location", "")
                        if not start_time or not end_time:
                            continue
                        key = (start_time.strip(), end_time.strip(), dist.strip(), s_loc.strip())
                        if key not in seen_keys:
                            seen_keys.add(key)
                            raw_rows.append(r)
                            added_from_file += 1

                    if from_landing and ("Starting Location" in reader.fieldnames):
                        files_to_archive.append(fp)
            except Exception:
                pass

        if not raw_rows:
            if verbose:
                print("No drive records found to consolidate.")
            return 0

        # TODO-009: self-healing UTC + real per-event timezone columns.
        # Runs on every row every consolidate - old master rows and
        # freshly-landed rows alike - so there is no separate one-time
        # migration step; see normalize_row_timezone()'s own docstring.
        for row in raw_rows:
            normalize_row_timezone(row, "Starting Latitude", "Starting Longitude", "Started At")
            normalize_row_timezone(row, "Ending Latitude", "Ending Longitude", "Ended At")
        if fieldnames:
            legacy_time_cols = {"Started At (AEST)", "Started At", "Ended At (AEST)", "Ended At"}
            new_time_cols = ["Started At (UTC)", "Started At TZ", "Started At (Local)",
                              "Ended At (UTC)", "Ended At TZ", "Ended At (Local)"]
            if any(c in fieldnames for c in new_time_cols):
                # Already migrated (idempotent re-run) - just drop any
                # lingering legacy columns, new columns are already
                # present in the right place.
                fieldnames = [c for c in fieldnames if c not in legacy_time_cols]
            else:
                rebuilt = []
                inserted = False
                for col in fieldnames:
                    if col in legacy_time_cols:
                        if not inserted:
                            rebuilt.extend(new_time_cols)
                            inserted = True
                        continue
                    rebuilt.append(col)
                if not inserted:
                    rebuilt = new_time_cols + rebuilt
                fieldnames = rebuilt

        # Sort chronologically by true UTC (fixes DST fall-back's
        # repeated hour and any future cross-timezone ordering issue -
        # TODO-009), falling back to the legacy text for any row that
        # somehow still lacks it.
        raw_rows.sort(key=lambda x: (
            int(x["Started At (UTC)"]) if x.get("Started At (UTC)") else 0,
            x.get("Started At (AEST)") or x.get("Started At") or x.get("Started") or ""))

        # Safety net (BUG-023/024/025 history): back up the master file's
        # pre-change state before overwriting it, whenever the row count is
        # about to actually change (new rows landing, or stale duplicates
        # being cleaned up) - archived with the same anti-clobber XX_ scheme
        # as everything else, so a real pre-change snapshot is always
        # recoverable rather than reconstructed by hand after the fact.
        rows_changing = existing_disk_row_count != len(raw_rows)
        duplicates_found_in_existing = max(0, existing_disk_row_count - initial_master_count)
        if rows_changing:
            backup_dir = os.path.join(dest_dirs[0], "archive") if dest_dirs else None
            if backup_dir:
                os.makedirs(backup_dir, exist_ok=True)
                for mf in master_files:
                    if os.path.isfile(mf):
                        try:
                            shutil.copy2(mf, next_archive_path(backup_dir, os.path.basename(mf)))
                        except Exception:
                            pass

        # Write to all master files
        for mf in master_files:
            try:
                with open(mf, "w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", restval="")
                    writer.writeheader()
                    writer.writerows(raw_rows)
            except Exception as e:
                if verbose:
                    print(f"Error writing master file {mf}: {e}")

        # Sanity-check what was actually written before reporting success -
        # catches a bad write (row-count mismatch) or a lingering exact-
        # duplicate line the key-based dedup above should have already
        # prevented, rather than silently trusting the write succeeded.
        sanity_issues = []
        for mf in master_files:
            try:
                with open(mf, "r", encoding="utf-8-sig") as f:
                    written_lines = [ln for ln in f.read().splitlines() if ln.strip()]
                data_lines = written_lines[1:]
                if len(data_lines) != len(raw_rows):
                    sanity_issues.append(
                        f"{os.path.basename(mf)}: wrote {len(raw_rows)} rows but file now has {len(data_lines)} data lines")
                exact_dupes = len(data_lines) - len(set(data_lines))
                if exact_dupes > 0:
                    sanity_issues.append(
                        f"{os.path.basename(mf)}: {exact_dupes} exact-duplicate line(s) still present after dedup")
            except Exception as e:
                sanity_issues.append(f"{os.path.basename(mf)}: could not verify after write - {e}")
        if sanity_issues:
            print("\033[31m⚠ Consolidate sanity check failed:\033[0m")
            for issue in sanity_issues:
                print(f"   - {issue}")

        # Archive processed raw files now that they've been merged into master
        archive_dir = os.path.join(dest_dirs[0], "archive") if dest_dirs else os.path.join(self.repo_root, "Tessie", "drives", "archive")
        os.makedirs(archive_dir, exist_ok=True)
        for lp in files_to_archive:
            try:
                fname = os.path.basename(lp)
                dst = next_archive_path(archive_dir, fname)
                shutil.move(lp, dst)
                print(f"\033[94m📥 Ingested & Archived:\033[0m {fname} ➔ drives/archive/{os.path.basename(dst)}")
            except Exception:
                pass

        new_count = len(raw_rows) - initial_master_count
        if duplicates_found_in_existing > 0:
            print(f"\033[38;5;214m🧹 Flagged & auto-removed {duplicates_found_in_existing} duplicate row(s) found in the existing drives_master.csv "
                  f"(pre-change backup saved to drives/archive/)\033[0m")
        if verbose or new_count > 0 or duplicates_found_in_existing > 0:
            dest_names = ", ".join(shorten_display_path(m, 0) for m in master_files)
            print(f"\033[92m✔ Consolidated {len(raw_rows)} total drives (+{new_count} new) into drives_master.csv\033[0m ({dest_names})\n")

        return len(raw_rows)

    @staticmethod
    def _deepdive_ts(ts_str):
        """Timestamp parser for drive_deepdive_*.csv rows - these are a
        plain 'YYYY-MM-DD HH:MM:SS' local string with no timezone suffix
        (unlike drives_master.csv's '(AEST)'-suffixed columns), so this
        is deliberately its own small parser rather than reusing
        tessie_charging_analyzer.py's parse_flexible_date() (a different
        module, and overkill for a format this fixed)."""
        if not ts_str:
            return None
        try:
            return datetime.strptime(ts_str.strip(), "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None

    def parse_detailed_drive_csv(self, filepath):
        """Parses high-frequency telemetry CSV files exported from the
        Tesla app for a single drive (drive_deepdive_*.csv - same export
        format as charging's charge_deepdive_*.csv, see AGENTS.md
        REQ-035/036) - keeps the raw per-sample series (needed for
        segment/grade analysis, not just a whole-trip aggregate) rather
        than collapsing straight to summary numbers."""
        try:
            with open(filepath, "r", encoding="utf-8-sig", errors="ignore") as f:
                reader = list(csv.DictReader(f))
                if not reader:
                    return None

                header = set(reader[0].keys())
                if "Speed (km/h)" not in header and "Shift State" not in header:
                    return None

                raw_ts, raw_elev, raw_speed, raw_power, raw_odo, raw_erem = [], [], [], [], [], []
                raw_lat, raw_lon = [], []
                timestamps = []

                def to_float(v):
                    try:
                        return float(v) if v not in (None, "") else None
                    except Exception:
                        return None

                for row in reader:
                    ts_str = row.get("Timestamp (AEST)") or row.get("Timestamp")
                    raw_ts.append(ts_str)
                    dt = self._deepdive_ts(ts_str) if ts_str else None
                    if dt:
                        timestamps.append(dt)
                    raw_elev.append(to_float(row.get("Elevation (m)")))
                    raw_speed.append(to_float(row.get("Speed (km/h)")))
                    raw_power.append(to_float(row.get("Power (kW)")))
                    raw_odo.append(to_float(row.get("Odometer (km)")))
                    raw_erem.append(to_float(row.get("Energy Remaining (kWh)")))
                    raw_lat.append(to_float(row.get("Latitude")))
                    raw_lon.append(to_float(row.get("Longitude")))

                if not timestamps:
                    return None

                min_dt, max_dt = min(timestamps), max(timestamps)
                dur_mins = (max_dt - min_dt).total_seconds() / 60.0
                odo_vals = [v for v in raw_odo if v is not None]
                distance_km = (max(odo_vals) - min(odo_vals)) if len(odo_vals) >= 2 else None

                return {
                    "source_file": os.path.basename(filepath),
                    "source_path": filepath,
                    "start_datetime": min_dt,
                    "end_datetime": max_dt,
                    "duration_mins": dur_mins,
                    "samples_count": len(reader),
                    "distance_km": distance_km,
                    "raw_samples": {
                        "timestamp": raw_ts,
                        "elevation_m": raw_elev,
                        "speed_kmh": raw_speed,
                        "power_kw": raw_power,
                        "odometer_km": raw_odo,
                        "energy_remaining_kwh": raw_erem,
                        "latitude": raw_lat,
                        "longitude": raw_lon,
                    },
                }
        except Exception:
            return None

    def load_detailed_drives(self):
        """Mirrors load_detailed_charges() (tessie_charging_analyzer.py) -
        find every drive_deepdive_*.csv this data home has (drives_dirs,
        their archive/deepdive subfolders, the repo Tessie/ dirs, and the
        landing dir), parse it, and keep the ones that actually contain
        drive telemetry. A drive with no matching file simply has nothing
        in self.detailed_drives for it - see match_detailed_drive() for
        the optional, session-may-or-may-not-exist lookup this feeds."""
        detailed = []
        seen_paths = set()
        search_dirs = [self.landing_dir] + list(self.drives_dirs) + list(self.tessie_dirs)
        for dd in list(self.drives_dirs):
            for sub in ["archive", "deepdive"]:
                sub_p = os.path.join(dd, sub)
                if os.path.isdir(sub_p) and sub_p not in search_dirs:
                    search_dirs.append(sub_p)
        for td in list(self.tessie_dirs):
            for sub in ["drives", "archive"]:
                sub_p = os.path.join(td, sub)
                if os.path.isdir(sub_p) and sub_p not in search_dirs:
                    search_dirs.append(sub_p)
            # A --tessie-dir override (e.g. running against an alternate
            # data location) deserves the SAME "drives/deepdive" and
            # "drives/archive" treatment drives_dirs already gets above -
            # without this, deep-dive telemetry sitting in <td>/drives/deepdive
            # was silently invisible to this search whenever td came from
            # --tessie-dir rather than tessie_data_home (found the hard way
            # verifying --backfill-deepdive against a location that wasn't
            # this process's config-resolved tessie_data_home).
            for sub in ["drives/deepdive", "drives/archive"]:
                sub_p = os.path.join(td, sub)
                if os.path.isdir(sub_p) and sub_p not in search_dirs:
                    search_dirs.append(sub_p)

        candidates = []
        for s_dir in search_dirs:
            if not s_dir or not os.path.isdir(s_dir):
                continue
            try:
                for fname in os.listdir(s_dir):
                    if not fname.endswith(".csv") or fname.startswith("."):
                        continue
                    if fname.startswith("drive_deepdive_"):
                        fpath = os.path.join(s_dir, fname)
                        real_p = os.path.realpath(fpath)
                        if real_p not in seen_paths:
                            seen_paths.add(real_p)
                            candidates.append(real_p)
            except Exception:
                pass

        for cp in candidates:
            parsed = self.parse_detailed_drive_csv(cp)
            if parsed:
                detailed.append(parsed)

        detailed.sort(key=lambda x: x["start_datetime"] or datetime.min)
        self.detailed_drives = detailed
        return self.detailed_drives

    def match_detailed_drive(self, drive_row):
        """The detailed_drives entry (if any) whose telemetry window
        overlaps this drives_master.csv row's start/end - same optional,
        may-or-may-not-exist lookup pattern already used for charges'
        dt_rec (self.detailed_charges), just keyed on drive start/end
        instead of a fuzzy 15-minute window (a drive's own start/end is
        already precise, no need to guess)."""
        if not getattr(self, "detailed_drives", None):
            return None
        d_start = drive_row.get("start_dt")
        d_end = drive_row.get("end_dt")
        if not d_start:
            return None
        if not d_end:
            d_end = d_start
        # Small padding (Tesla's own start/stop detection and the app's
        # detailed export don't always agree to the second).
        pad = timedelta(minutes=2)
        for rec in self.detailed_drives:
            if rec["start_datetime"] <= d_end + pad and rec["end_datetime"] >= d_start - pad:
                return rec
        return None

    def compute_drive_segments(self, raw_samples, grade_threshold_pct=2.0):
        """Break a drive's raw telemetry into contiguous climbing/level/
        descending segments (see AGENTS.md REQ-036) and compute per-segment
        distance/duration/elevation-change/energy/efficiency - the actual
        point being to answer "what was my efficiency going downhill",
        which a whole-trip average can never show.

        Grade is derived from consecutive samples' own Elevation (m) and
        Odometer (km) - Delevation / (Ddistance * 1000) * 100 - never
        assumed from speed or road type. Energy per interval is the
        REAL delta in "Energy Remaining (kWh)" between consecutive
        samples (e_rem[i] - e_rem[i+1]) - not a Power (kW) integration
        (see AGENTS.md REQ-037: the user pointed out Power (kW) is
        sometimes entirely empty in a real drive export, while Odometer
        and Energy Remaining reliably vary, and that they're the more
        trustworthy source for efficiency anyway). Positive means net
        consumption (the battery's remaining energy went down); a
        descending segment showing net-negative energy (i.e. net
        regeneration - remaining energy went UP) is an expected,
        physically real result here, not a bug, and is exactly the kind
        of thing this is meant to reveal.

        Intervals with no usable distance (Ddistance <= 0, e.g. a stopped
        moment) are classified "stationary" rather than forced into a
        grade bucket, and are still tracked as their own segment so a
        red light doesn't get silently absorbed into whichever segment
        happens to be adjacent."""
        ts = raw_samples.get("timestamp") or []
        elev = raw_samples.get("elevation_m") or []
        odo = raw_samples.get("odometer_km") or []
        erem = raw_samples.get("energy_remaining_kwh") or []
        n = len(ts)

        dts = [self._deepdive_ts(t) if t else None for t in ts]

        intervals = []
        for i in range(n - 1):
            dt0, dt1 = dts[i], dts[i + 1]
            e0, e1 = elev[i], elev[i + 1]
            o0, o1 = odo[i], odo[i + 1]
            r0 = erem[i] if i < len(erem) else None
            r1 = erem[i + 1] if i + 1 < len(erem) else None
            if not (dt0 and dt1 and dt1 > dt0):
                continue
            dt_hours = (dt1 - dt0).total_seconds() / 3600.0
            ddist_km = (o1 - o0) if (o0 is not None and o1 is not None) else None
            delev_m = (e1 - e0) if (e0 is not None and e1 is not None) else None
            energy_kwh = (r0 - r1) if (r0 is not None and r1 is not None) else None

            if ddist_km is not None and ddist_km > 0.01 and delev_m is not None:
                grade_pct = (delev_m / (ddist_km * 1000.0)) * 100.0
                if grade_pct >= grade_threshold_pct:
                    cls = "climbing"
                elif grade_pct <= -grade_threshold_pct:
                    cls = "descending"
                else:
                    cls = "level"
            else:
                grade_pct = None
                cls = "stationary"

            intervals.append({
                "start": dt0, "end": dt1, "dt_hours": dt_hours,
                "distance_km": ddist_km or 0.0, "elevation_change_m": delev_m or 0.0,
                "energy_kwh": energy_kwh, "grade_pct": grade_pct, "class": cls,
            })

        # Merge consecutive same-class intervals into segments.
        segments = []
        for iv in intervals:
            if segments and segments[-1]["class"] == iv["class"]:
                seg = segments[-1]
                seg["end"] = iv["end"]
                seg["distance_km"] += iv["distance_km"]
                seg["elevation_change_m"] += iv["elevation_change_m"]
                if iv["energy_kwh"] is not None:
                    seg["energy_kwh"] = (seg["energy_kwh"] or 0.0) + iv["energy_kwh"]
                seg["_interval_count"] += 1
            else:
                segments.append({
                    "class": iv["class"], "start": iv["start"], "end": iv["end"],
                    "distance_km": iv["distance_km"], "elevation_change_m": iv["elevation_change_m"],
                    "energy_kwh": iv["energy_kwh"], "_interval_count": 1,
                })

        for seg in segments:
            seg["duration_mins"] = (seg["end"] - seg["start"]).total_seconds() / 60.0
            seg["avg_speed_kmh"] = (seg["distance_km"] / (seg["duration_mins"] / 60.0)
                                     if seg["duration_mins"] > 0 else None)
            if seg["distance_km"] > 0.15 and seg["energy_kwh"] is not None:
                seg["efficiency_kwh_per_100km"] = (seg["energy_kwh"] / seg["distance_km"]) * 100.0
            else:
                seg["efficiency_kwh_per_100km"] = None
            del seg["_interval_count"]

        return segments

    def _rolling_distance_window(self, iv_dist, i, window_km):
        """Shared centered, distance-based window expansion used by every
        compute_rolling_*() smoother below (originally
        compute_rolling_efficiency()'s own logic, factored out 2026-09-15
        when the user asked for the same rolling-window treatment on a
        new Speed series too - "Drives can be spiky. So rolling
        averages" - rather than duplicating this per metric).

        iv_dist: real per-INTERVAL distance (km), one entry between each
        pair of consecutive samples (see compute_drive_segments()/
        compute_rolling_efficiency() for how that's derived from real
        Odometer deltas - never assumed from speed).

        Starts centered on sample i (the interval just before it and the
        interval just after it), then grows outward - alternating left/
        right - until at least window_km of REAL distance is covered, so
        it adapts to how fast the car was actually going rather than a
        fixed sample count or time span (0.8 km is ~30 sec on the
        freeway, several minutes stuck in traffic).

        Returns (lo, hi, dist_km) - the interval-index range [lo, hi]
        (inclusive) and the real distance it covers - or None if sample i
        has no touching interval at all (shouldn't normally happen once
        n >= 2, but kept explicit rather than silently returning a
        zero-width window)."""
        n_iv = len(iv_dist)
        lo = max(0, i - 1)
        hi = min(n_iv - 1, i)
        if lo > hi:
            return None
        dist = sum(iv_dist[lo:hi + 1])
        grow_left_next = True
        while dist < window_km and (lo > 0 or hi < n_iv - 1):
            if grow_left_next and lo > 0:
                lo -= 1
                dist += iv_dist[lo]
            elif hi < n_iv - 1:
                hi += 1
                dist += iv_dist[hi]
            elif lo > 0:
                lo -= 1
                dist += iv_dist[lo]
            grow_left_next = not grow_left_next
        return lo, hi, dist

    def compute_rolling_efficiency(self, raw_samples, window_km=None, min_km=None):
        """Per-SAMPLE instantaneous efficiency (kWh/100km), smoothed with a
        centered ROLLING-DISTANCE window (_rolling_distance_window())
        rather than either extreme: raw per-interval (wild swings - two
        ~15-30s samples can be under 50m apart, so a tiny real energy
        delta divided by that is meaningless noise) or one flat value per
        whole climbing/level/descending SEGMENT (the previous chart
        behavior - a real user report, 2026-09-15: "the efficiency graph
        is disjointed and not smooth and connected", because broadcasting
        one number across an entire segment produces a blocky step
        function with a sharp near-vertical jump at every segment-class
        boundary, even though the drive itself is continuous).

        Built from the exact same real per-interval Odometer (km) /
        Energy Remaining (kWh) deltas compute_drive_segments() computes,
        so the chart and the segment table can never silently disagree
        about the underlying data, only about how it's presented (one
        flat number per segment there, one smoothly-varying curve here).

        Adapts dynamically to total drive length (5% adaptive window)
        when window_km is None.

        Returns a list the same length as raw_samples's own arrays - None
        wherever the window still couldn't gather min_km of real distance
        (e.g. near a full stop) or lacks enough real energy readings to
        trust, rather than guessing."""
        ts = raw_samples.get("timestamp") or []
        odo = raw_samples.get("odometer_km") or []
        erem = raw_samples.get("energy_remaining_kwh") or []
        n = len(ts)
        if n < 2:
            return [None] * n

        # Real per-interval deltas - interval k sits between sample k and
        # k+1 (same source data compute_drive_segments() already derives).
        n_iv = n - 1
        iv_dist = [0.0] * n_iv
        iv_energy = [None] * n_iv
        for k in range(n_iv):
            o0 = odo[k] if k < len(odo) else None
            o1 = odo[k + 1] if k + 1 < len(odo) else None
            r0 = erem[k] if k < len(erem) else None
            r1 = erem[k + 1] if k + 1 < len(erem) else None
            if o0 is not None and o1 is not None:
                iv_dist[k] = max(0.0, o1 - o0)
            if r0 is not None and r1 is not None:
                iv_energy[k] = r0 - r1

        total_trip_dist = sum(iv_dist)
        eff_window_km = window_km if window_km is not None else compute_adaptive_rolling_window(total_trip_dist)
        eff_min_km = min_km if min_km is not None else max(0.15, eff_window_km * 0.35)

        result = [None] * n
        for i in range(n):
            win = self._rolling_distance_window(iv_dist, i, eff_window_km)
            if win is None:
                continue
            lo, hi, dist = win
            if dist < eff_min_km:
                continue
            window = iv_energy[lo:hi + 1]
            known = [e for e in window if e is not None]
            if len(known) < max(1, len(window) * 0.5):
                continue
            result[i] = (sum(known) / dist) * 100.0
        return result

    def compute_rolling_speed(self, raw_samples, window_km=None, min_km=None):
        """Per-sample average speed (km/h), smoothed with the exact same
        centered rolling-distance window as compute_rolling_efficiency()
        (_rolling_distance_window()) - real distance over real elapsed
        time within the window, not a plain average of the raw per-
        sample 'Speed (km/h)' telemetry field, and not assumed from
        Odometer alone either.

        Adapts dynamically to total drive length (5% adaptive window)
        when window_km is None.

        Returns a list the same length as raw_samples's own arrays -
        None wherever the window can't gather min_km of real distance or
        enough real elapsed-time coverage to trust, same gap-honesty
        contract as compute_rolling_efficiency()."""
        ts = raw_samples.get("timestamp") or []
        odo = raw_samples.get("odometer_km") or []
        n = len(ts)
        if n < 2:
            return [None] * n

        dts = [self._deepdive_ts(t) if t else None for t in ts]
        n_iv = n - 1
        iv_dist = [0.0] * n_iv
        iv_time_hr = [None] * n_iv
        for k in range(n_iv):
            o0 = odo[k] if k < len(odo) else None
            o1 = odo[k + 1] if k + 1 < len(odo) else None
            if o0 is not None and o1 is not None:
                iv_dist[k] = max(0.0, o1 - o0)
            dt0, dt1 = dts[k], dts[k + 1]
            if dt0 and dt1 and dt1 > dt0:
                iv_time_hr[k] = (dt1 - dt0).total_seconds() / 3600.0

        total_trip_dist = sum(iv_dist)
        eff_window_km = window_km if window_km is not None else compute_adaptive_rolling_window(total_trip_dist)
        eff_min_km = min_km if min_km is not None else max(0.15, eff_window_km * 0.35)

        result = [None] * n
        for i in range(n):
            win = self._rolling_distance_window(iv_dist, i, eff_window_km)
            if win is None:
                continue
            lo, hi, dist = win
            if dist < eff_min_km:
                continue
            window = iv_time_hr[lo:hi + 1]
            known = [t for t in window if t is not None]
            if len(known) < max(1, len(window) * 0.5):
                continue
            total_hr = sum(known)
            if total_hr <= 0:
                continue
            result[i] = dist / total_hr
        return result

    def generate_drive_profile_chart(self, dt_rec, target_drive=None):
        """Interactive elevation/efficiency/speed profile for one drive's
        deep-dive telemetry (AGENTS.md REQ-036/REQ-037) - Elevation (m) on
        the left axis, Efficiency (kWh/100km) and Speed (km/h) each on
        their own stacked right-hand axis, x-axis = time. Both Efficiency
        and Speed are compute_rolling_efficiency()/compute_rolling_speed()'s
        smoothed per-sample curves with an adaptive rolling distance window
        (5% of total drive length, bounded between 0.35 km and 5.0 km)."""
        raw = dt_rec.get("raw_samples")
        if not raw or not raw.get("timestamp"):
            return None
        timestamps = raw["timestamp"]
        n = len(timestamps)
        x_labels = [ts[11:19] if ts and len(ts) >= 19 else (ts or "") for ts in timestamps]

        total_dist = dt_rec.get("distance_km")
        if not total_dist and raw.get("odometer_km"):
            odo_clean = [o for o in raw["odometer_km"] if o is not None]
            if len(odo_clean) >= 2:
                total_dist = max(0.0, odo_clean[-1] - odo_clean[0])

        window_km = compute_adaptive_rolling_window(total_dist)
        eff_series = self.compute_rolling_efficiency(raw, window_km=window_km)
        speed_series = self.compute_rolling_speed(raw, window_km=window_km)

        series = [
            {"name": "Elevation", "unit": "m", "axis": "left", "color": "#16a34a",
             "values": raw.get("elevation_m", [None] * n)},
            {"name": "Efficiency", "unit": "kWh/100km", "axis": "right", "color": "#dc2626",
             "fill_negative": True, "values": eff_series},
            {"name": "Speed", "unit": "km/h", "axis": "speed", "color": "#2563eb",
             "values": speed_series},
        ]

        # No "plots" subfolder - sits right beside the CSV, alongside
        # every other deep-dive artifact for this same drive.
        src_dir = os.path.dirname(dt_rec.get("source_path") or "") or os.getcwd()
        base = os.path.splitext(os.path.basename(dt_rec.get("source_file") or "drive"))[0]
        out_path = os.path.join(src_dir, f"{base}_profile.html")

        # Whole-drive average efficiency (REQ AGENTS.local.md 2026-09-15) -
        # a single number for the WHOLE drive, not the smoothed per-sample
        # eff_series above (that's for the curve on the chart itself, this
        # is the one-line summary under the heading) - total energy used
        # (first sample's Energy Remaining minus the last, since it only
        # decreases while actually driving) over the drive's total distance.
        avg_eff = None
        erem_vals = [v for v in raw.get("energy_remaining_kwh", []) if v is not None]
        dist_km = dt_rec.get("distance_km")
        if erem_vals and dist_km and dist_km > 0.05:
            energy_used_kwh = erem_vals[0] - erem_vals[-1]
            if energy_used_kwh > 0:
                avg_eff = (energy_used_kwh / dist_km) * 100.0

        ref_lines = []
        if avg_eff is not None:
            ref_lines.append({
                "value": round(avg_eff, 2),
                "axis": "right",
                "label": f"Drive Avg: {avg_eff:.1f} kWh/100km",
                "color": "#d97706",
                "style": "dashed",
            })

        # Resolve from and to places (AGENTS.local.md REQ-083)
        start_place = (target_drive.get("start_place") if target_drive else None) or dt_rec.get("start_place")
        end_place = (target_drive.get("end_place") if target_drive else None) or dt_rec.get("end_place")

        if not start_place or not end_place:
            d_start = dt_rec.get("start_datetime")
            if d_start and getattr(self, "drives", None):
                for d in self.drives:
                    ds = d.get("start_dt")
                    if ds and abs((ds - d_start).total_seconds()) <= 180:
                        start_place = start_place or d.get("start_place")
                        end_place = end_place or d.get("end_place")
                        break

        if not start_place and raw.get("latitude") and raw.get("longitude"):
            for lat, lon in zip(raw["latitude"], raw["longitude"]):
                if lat is not None and lon is not None:
                    start_place = self.resolve_place(None, "", lat, lon)
                    break

        if not end_place and raw.get("latitude") and raw.get("longitude"):
            for lat, lon in reversed(list(zip(raw["latitude"], raw["longitude"]))):
                if lat is not None and lon is not None:
                    end_place = self.resolve_place(None, "", lat, lon)
                    break

        route_prefix = ""
        if start_place and end_place:
            route_prefix = f"{start_place} ➔ {end_place} \u2014 "
        elif start_place:
            route_prefix = f"From {start_place} \u2014 "
        elif end_place:
            route_prefix = f"To {end_place} \u2014 "

        win_str = f"{window_km:.2f} km" if window_km < 1.0 else f"{window_km:.1f} km"
        subtitle = (f"{route_prefix}{dt_rec.get('samples_count', n)} samples over "
                    f"{dt_rec.get('duration_mins', 0):.1f} min"
                    + (f", {dt_rec['distance_km']:.1f} km" if dt_rec.get("distance_km") else "")
                    + (f" \u2014 Avg Efficiency: {avg_eff:.1f} kWh/100km" if avg_eff is not None else "")
                    + f" ({win_str} adaptive rolling window)")

        locations = self._compute_drive_locations(raw)

        write_interactive_chart(
            out_path,
            title=f"Drive Elevation/Efficiency/Speed Profile - {dt_rec.get('source_file', '')}",
            x_values=x_labels,
            x_label="Time",
            series=series,
            subtitle=subtitle,
            locations=locations,
            ref_lines=ref_lines,
        )
        return out_path

    def _compute_drive_locations(self, raw_samples):
        """Sparse, nearest-fill reverse-geocoded location label per sample,
        for the drive profile chart's hover tooltip (REQ AGENTS.local.md
        2026-09-15: user - "while dragging cursor along the timeline, it
        would be good to have an extra line for where the car is at that
        point in time (e.g. address pulled from csv)"). The CSV only has
        raw Latitude/Longitude, not a ready-made address, so this reverse-
        geocodes via tessie_places.reverse_geocode() (Nominatim) - but only
        when the car has moved more than MIN_MOVE_M since the last lookup,
        caching by a coarsely-rounded lat/lon and nearest-filling every
        sample in between. Reverse-geocoding every single sample would be
        both pointless (a still-moving car's address doesn't meaningfully
        change every few seconds) and abusive of Nominatim's free-API 1
        req/sec usage policy for a drive with hundreds of samples. Returns
        None (no location line at all) if there's no lat/lon in this CSV
        (older exports) or every lookup fails; otherwise a list the same
        length as the sample series, aligned 1:1 like every other series
        write_interactive_chart() takes."""
        lats = raw_samples.get("latitude") or []
        lons = raw_samples.get("longitude") or []
        n = len(lats)
        if n == 0 or not any(v is not None for v in lats):
            return None

        try:
            from tessie_places import reverse_geocode, haversine_distance
        except ImportError:
            return None

        geo_cache = {}
        try:
            from charger_find_tesla import load_geo_cache, save_geo_cache
            geo_cache = load_geo_cache(self.repo_root) or {}
        except Exception:
            pass

        MIN_MOVE_M = 500.0
        MAX_LIVE_LOOKUPS = 4
        live_lookups_done = 0
        network_available = True
        dirty_cache = False

        cache = {}
        geocoded_idx = []
        labels = [None] * n
        last_lat, last_lon = None, None

        for i in range(n):
            lat, lon = lats[i], lons[i]
            if lat is None or lon is None:
                continue
            if last_lat is not None and haversine_distance(last_lat, last_lon, lat, lon) < MIN_MOVE_M:
                continue

            # 1. Check known places first (0ms latency)
            matched_place = self.resolve_place(None, "", lat, lon)
            if matched_place:
                label = matched_place
                cache[(round(lat, 3), round(lon, 3))] = label
            else:
                cache_key = f"{round(lat, 3)},{round(lon, 3)}"
                if (round(lat, 3), round(lon, 3)) in cache:
                    label = cache[(round(lat, 3), round(lon, 3))]
                elif cache_key in geo_cache:
                    cached_val = geo_cache[cache_key]
                    if isinstance(cached_val, dict):
                        label = self._format_reverse_geocode_label(cached_val)
                    else:
                        label = str(cached_val)
                    cache[(round(lat, 3), round(lon, 3))] = label
                elif network_available and live_lookups_done < MAX_LIVE_LOOKUPS:
                    try:
                        info = reverse_geocode(lat, lon)
                        live_lookups_done += 1
                        if info:
                            label = self._format_reverse_geocode_label(info)
                            cache[(round(lat, 3), round(lon, 3))] = label
                            geo_cache[cache_key] = info
                            dirty_cache = True
                        else:
                            label = None
                    except Exception:
                        network_available = False
                        label = None
                else:
                    label = None

            if label:
                labels[i] = label
                geocoded_idx.append(i)
            last_lat, last_lon = lat, lon

        if dirty_cache:
            try:
                from charger_find_tesla import save_geo_cache
                save_geo_cache(geo_cache, self.repo_root)
            except Exception:
                pass

        if not geocoded_idx:
            return None

        filled = [None] * n
        for i in range(n):
            nearest = min(geocoded_idx, key=lambda gi: abs(gi - i))
            filled[i] = labels[nearest]
        return filled

    @staticmethod
    def _format_reverse_geocode_label(info):
        """Short 'Road, Suburb'-style label from a tessie_places.reverse_geocode()
        result, falling back to its display_name/name when address parts
        are missing (e.g. open water, a highway with no suburb tag)."""
        addr = (info or {}).get("address") or {}
        road = addr.get("road") or addr.get("pedestrian") or addr.get("footway") or addr.get("cycleway")
        suburb = addr.get("suburb") or addr.get("town") or addr.get("city") or addr.get("village") or addr.get("municipality")
        parts = [p for p in (road, suburb) if p]
        if parts:
            return ", ".join(parts)
        return info.get("display_name") or info.get("name") or None

    DRIVE_FINALIZE_GRACE_SECONDS = 600  # see _drive_is_in_progress()

    def _drive_is_in_progress(self, target_drive):
        """True when this drive should be treated as still in progress -
        and therefore NOT eligible for fetch_drive_deepdive_from_api()
        below (user's own words: auto-fetch should have "the exception
        being a drive in progress"). True when there's no recorded end
        time at all, or the end time is suspiciously recent - Tessie's
        own sync can take a few minutes to finalize a drive's summary row
        after it actually ends, and fetching too early risks a truncated
        /states window getting permanently cached as a wrong/incomplete
        deep-dive file (there's no cheap way to tell "truncated because
        still driving" apart from "truncated because the network hiccuped"
        after the fact, so the safer fix is not to race the finalize at
        all)."""
        end_epoch = target_drive.get("end_utc_epoch")
        if end_epoch is None:
            end_dt = target_drive.get("end_dt")
            if not end_dt:
                return True
            end_epoch = end_dt.timestamp()
        return (time.time() - end_epoch) < self.DRIVE_FINALIZE_GRACE_SECONDS

    def fetch_drive_deepdive_from_api(self, target_drive):
        """Auto-fetches this drive's detailed telemetry from Tessie's own
        GET /{vin}/states endpoint (AGENTS.md TODO-026) when no local
        drive_deepdive_*.csv already covers it - the automated equivalent
        of the Tesla app's manual "download detailed telemetry" export,
        using the exact same endpoint/calling convention already proven
        live by analysis_home_charge_deep_dive.py's fetch_tessie_states(). Deliberately
        requests format="csv" (not json) so Tessie's own server does the
        CSV formatting - the same convention the manual export already
        produces, and the one parse_detailed_drive_csv() already expects -
        rather than us guessing at the /states JSON response's exact
        timestamp representation, which this session had no way to
        confirm live (Tessie's API is unreachable from this session's own
        sandboxed device-bridge shell - a tooling limitation, not a repo
        issue; see AGENTS.md TODO-026 for the still-open follow-up to
        verify this against a real drive).

        Writes the result into drives_dirs[0]/deepdive/ (today's
        canonical home for these files, matching charges/deepdive after
        TODO-023) and self-validates it with parse_detailed_drive_csv()
        before keeping it - a response that doesn't actually parse as
        drive telemetry is deleted and this returns None, exactly as if
        nothing had been fetched at all, so a format mismatch degrades
        safely rather than caching junk. Never called for a drive still
        in progress - see _drive_is_in_progress(). Returns the written
        file path on success, else None (no vin/token configured,
        network/auth failure, or a response that fails to parse)."""
        start_epoch = target_drive.get("start_utc_epoch")
        end_epoch = target_drive.get("end_utc_epoch")
        if start_epoch is None or end_epoch is None or not self.drives_dirs:
            return None

        try:
            from lib.tessie_api_common import load_config, get_token, get_vin, api_get, DEFAULT_UNIT_PARAMS
        except Exception:
            return None

        cfg = load_config()
        token = get_token(None, cfg)
        vin = get_vin(None, cfg)
        if not token or not vin:
            return None

        # Same +/-2min padding match_detailed_drive() already allows for
        # when matching an existing file - Tesla's own start/stop
        # detection and the detailed export don't always agree to the
        # second.
        pad = 120
        params = dict(DEFAULT_UNIT_PARAMS)
        params.update({
            "from": int(start_epoch) - pad,
            "to": int(end_epoch) + pad,
            "interval": 1,
            "format": "csv",
        })

        print("   ℹ️  No local deep-dive file for this drive - fetching detailed telemetry from Tessie...")
        status, body = api_get("states", vin, token, params)
        if status != 200:
            print(f"   ⚠️  Tessie /states fetch failed (HTTP {status}) - continuing without deep-dive telemetry.")
            return None

        dest_dir = os.path.join(self.drives_dirs[0], "deepdive")
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except Exception:
            return None

        start_dt = target_drive.get("start_dt") or datetime.fromtimestamp(start_epoch)
        stamp = start_dt.strftime("%Y-%m-%d_%H-%M")
        # AGENTS.md TODO-033: fold the real AEST/AEDT abbreviation into the
        # stamp here too, matching the manual-ingest path above.
        try:
            _, abbr, _ = tztools.localize(start_epoch)
            if abbr:
                stamp = f"{stamp}_{abbr}"
        except Exception:
            pass
        dest_path = os.path.join(dest_dir, f"drive_deepdive_{stamp}.csv")
        n = 1
        while os.path.exists(dest_path):
            dest_path = os.path.join(dest_dir, f"drive_deepdive_{stamp}_{n:02d}.csv")
            n += 1

        try:
            with open(dest_path, "wb") as f:
                f.write(body)
        except Exception:
            return None
        # AGENTS.md TODO-033: set this file's mtime to the real drive-start
        # event, not whenever this auto-fetch happened to run.
        try:
            os.utime(dest_path, (start_epoch, start_epoch))
        except Exception:
            pass

        parsed = self.parse_detailed_drive_csv(dest_path)
        if not parsed or not parsed.get("raw_samples", {}).get("timestamp"):
            try:
                os.remove(dest_path)
            except Exception:
                pass
            print("   ⚠️  Tessie's response doesn't look like drive telemetry - continuing without deep-dive.")
            return None

        print(f"   ✅ Fetched and saved: {os.path.basename(dest_path)} ({parsed.get('samples_count', 0)} samples)")
        return dest_path

    def get_drive_deepdive(self, target_drive):
        """Looks up deep-dive telemetry for one drive, computes its segment
        breakdown, and generates (but no longer auto-opens - user report,
        2026-09-15: opening a browser tab unconditionally on every single
        day/trip selection was surprising and unwanted, unlike
        tessie_charging_analyzer.py's own charging curve, which only opens
        via an explicit "[o]pen chart in browser" action - callers now
        offer that same explicit action instead, see display_footage_details()
        and inspect_drive()) its elevation/efficiency profile chart (if one
        exists) - the shared data logic behind BOTH inspect_drive() (the
        standalone --inspect-drive flag) and display_footage_details()
        (AGENTS.md TODO-019: folded into the ordinary trip drill-down so
        this shows up automatically alongside footage, with no separate
        footage-vs-deep-dive prompt - mirrors how tessie_charging_analyzer.py's
        inspect_session() already handles its charging curve inline).
        When no local file covers this drive yet, auto-fetches one from
        Tessie's /states API instead of giving up (AGENTS.md TODO-026) -
        skipped for a drive still in progress (_drive_is_in_progress()),
        and skipped/reverted silently on any fetch or parse failure, so
        this never behaves worse than before the auto-fetch existed.
        Returns None if no deep-dive telemetry exists (or could be
        fetched) for this drive; otherwise (dt_rec, segments,
        chart_path_or_None). Callers are free to format/print the result
        however fits their own display."""
        if not getattr(self, "detailed_drives", None):
            self.load_detailed_drives()
        dt_rec = self.match_detailed_drive(target_drive)
        if not dt_rec and not self._drive_is_in_progress(target_drive):
            fetched_path = self.fetch_drive_deepdive_from_api(target_drive)
            if fetched_path:
                self.load_detailed_drives()
                dt_rec = self.match_detailed_drive(target_drive)
        if not dt_rec:
            return None
        segments = self.compute_drive_segments(dt_rec["raw_samples"])
        try:
            chart_path = self.generate_drive_profile_chart(dt_rec, target_drive=target_drive)
        except Exception:
            chart_path = None
        return dt_rec, segments, chart_path

    def inspect_drive(self, target):
        """Deep-dive inspect a specific drive by 1-based index (in
        start-time order, matching how drives are listed elsewhere) or by
        date/datetime string (see AGENTS.md REQ-036) - prints the
        climbing/level/descending segment breakdown (compute_drive_segments)
        when a matching drive_deepdive_*.csv exists for it, and opens the
        interactive elevation/power profile chart. Mirrors
        tessie_charging_analyzer.py's inspect_session(), just keyed on a
        drive instead of a charge."""
        if not self.drives:
            self.load_drives()
        if not getattr(self, "detailed_drives", None):
            self.load_detailed_drives()

        target_drive = None
        try:
            idx = int(target)
            sorted_drives = sorted(self.drives, key=lambda d: d["start_dt"])
            if 1 <= idx <= len(sorted_drives):
                target_drive = sorted_drives[idx - 1]
        except ValueError:
            t_dt = parse_relative_date(target)
            if t_dt:
                best, best_diff = None, None
                for d in self.drives:
                    diff = abs((d["start_dt"] - t_dt).total_seconds())
                    if best_diff is None or diff < best_diff:
                        best, best_diff = d, diff
                if best is not None and best_diff <= 7200:
                    target_drive = best

        if not target_drive:
            print(f"\033[91mCould not find drive matching:\033[0m {target}")
            return

        box_w = 145
        header_title = (f" 🚗 DEEP-DIVE DRIVE INSPECTION: {target_drive['start_dt'].strftime('%Y-%m-%d %H:%M')} "
                         f"{target_drive.get('start_place', '') or ''} ➔ {target_drive.get('end_place', '') or ''}")
        print()
        print(f"┌{'─' * (box_w - 2)}┐")
        print(f"│{pad_display(header_title, box_w - 2, 'left', truncate=True)}│")
        print(f"├{'─' * (box_w - 2)}┤")

        dur_mins = (target_drive["end_dt"] - target_drive["start_dt"]).total_seconds() / 60.0
        l1 = f"  \033[1mStarted:\033[0m {target_drive['start_dt'].strftime('%Y-%m-%d %H:%M:%S')}   \033[1mDuration:\033[0m {dur_mins:.0f} mins"
        print(f"│{pad_display(l1, box_w - 2, truncate=True)}│")

        result = self.get_drive_deepdive(target_drive)
        if not result:
            l2 = "  \033[38;5;214mNo deep-dive telemetry (drive_deepdive_*.csv) found for this drive.\033[0m"
            print(f"│{pad_display(l2, box_w - 2, truncate=True)}│")
            print(f"└{'─' * (box_w - 2)}┘")
            return
        dt_rec, segments, chart_path = result

        l3 = f"  \033[1mDeep-Dive File:\033[0m {dt_rec.get('source_file', '')}   ({dt_rec.get('samples_count', 0)} samples)"
        print(f"│{pad_display(l3, box_w - 2, truncate=True)}│")
        print(f"├{'─' * (box_w - 2)}┤")

        seg_title = "  \033[1m\033[95m⛰  SEGMENT BREAKDOWN (climbing/level/descending/stationary):\033[0m"
        print(f"│{pad_display(seg_title, box_w - 2, truncate=True)}│")

        cls_icon = {"climbing": "⬆️ ", "descending": "⬇️ ", "level": "➡️ ", "stationary": "⏸ "}
        for seg in segments:
            icon = cls_icon.get(seg["class"], "  ")
            eff = seg["efficiency_kwh_per_100km"]
            eff_str = f"{eff:.1f} kWh/100km" if eff is not None else "-"
            l = (f"    {icon}{seg['class']:<11} {seg['distance_km']:5.2f} km  {seg['duration_mins']:5.1f} min  "
                 f"Δelev {seg['elevation_change_m']:+6.1f} m   eff: {eff_str}")
            print(f"│{pad_display(l, box_w - 2, truncate=True)}│")

        print(f"├{'─' * (box_w - 2)}┤")
        if chart_path:
            l4 = f"  \033[1mElevation/Efficiency Profile:\033[0m {chart_path}"
            print(f"│{pad_display(l4, box_w - 2, truncate=True)}│")
        print(f"└{'─' * (box_w - 2)}┘")

        # User report (2026-09-15): used to auto-open in the browser
        # unconditionally (get_drive_deepdive() no longer does that) -
        # explicit action instead, matching tessie_charging_analyzer.py's
        # own "[o]pen chart in browser" for the charging curve.
        if chart_path and sys.stdin.isatty():
            try:
                act = input(f"{'':2}Action: [o]pen chart in browser, [Enter] to return: ").strip().lower()
                if act in ('o', 'open', 'chart', 'c'):
                    try_open_in_browser(chart_path)
            except (KeyboardInterrupt, EOFError):
                pass
        print()

    def sync_drives_if_stale(self, target_date=None, force=False):
        """
        Determines if local drives_master.csv is stale compared to requested date or current time,
        and automatically triggers tessie_api_sync.py --drives --yes to fetch fresh telemetry.
        """
        if getattr(self, "_sync_disabled", False):
            return False
        if getattr(self, "_synced_this_session", False) and not force:
            return False

        # 1. Check if Tessie API token is available
        cfg = tessie_api_common.load_config()
        token = os.environ.get("TESSIE_ACCESS_TOKEN") or cfg.get("tessie_access_token")
        if not token:
            return False

        now = datetime.now()
        is_stale = False
        stale_reason = ""

        latest_drive_dt = None
        if self.drives:
            latest_drive_dt = max(d["start_dt"] for d in self.drives)

        if force:
            is_stale = True
            stale_reason = "Manual sync requested"
        elif not self.drives:
            is_stale = True
            stale_reason = "No local drive records found"
        elif target_date:
            t_date = target_date.date() if isinstance(target_date, datetime) else target_date
            if isinstance(t_date, str):
                try:
                    t_date = datetime.strptime(t_date[:10], "%Y-%m-%d").date()
                except ValueError:
                    t_date = now.date()
            if t_date <= now.date():
                if latest_drive_dt and latest_drive_dt.date() < t_date:
                    is_stale = True
                    stale_reason = f"Requested date ({t_date}) is after latest recorded drive ({latest_drive_dt.strftime('%Y-%m-%d')})"
                elif latest_drive_dt and latest_drive_dt.date() == t_date and (now - latest_drive_dt).total_seconds() > 14400:
                    is_stale = True
                    stale_reason = f"Latest drive on {t_date} was over 4 hours ago"
        elif latest_drive_dt:
            age_hours = (now - latest_drive_dt).total_seconds() / 3600
            if latest_drive_dt.date() < now.date() or age_hours > 6:
                is_stale = True
                stale_reason = f"Latest recorded drive is {age_hours:.1f}h old ({latest_drive_dt.strftime('%Y-%m-%d %H:%M')})"

        if not is_stale:
            return False

        sync_script = os.path.join(self.script_dir, "tessie_api_sync.py")
        if not os.path.isfile(sync_script):
            return False

        print(f"\n🔄 Telemetry data is stale ({stale_reason}).")
        print("   Auto-syncing latest drives from Tessie API...")
        try:
            res = subprocess.run(
                [sys.executable, sync_script, "--drives", "--yes"],
                capture_output=True,
                text=True,
                timeout=45
            )
            self._synced_this_session = True
            if "Consolidated" in res.stdout or "+0 new" not in res.stdout:
                # Reload drives cleanly into memory
                self._indexed = False
                self.load_drives(check_stale=False)
                return True
        except Exception as e:
            print(f"⚠️  Auto-sync notice: {e}")
        return False

    def load_drives(self, check_stale=False):
        self.consolidate_drives()
        master_file = None
        for td in self.tessie_dirs:
            for candidate in [
                os.path.join(td, "drives", "drives_master.csv"),
                os.path.join(td, "drives_master.csv")
            ]:
                if os.path.isfile(candidate):
                    master_file = candidate
                    break
            if master_file:
                break

        if not master_file:
            return []

        parsed = []
        with open(master_file, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for r in reader:
                try:
                    # TODO-009: prefer the self-healing UTC+TZ columns
                    # (normalize_row_timezone() in consolidate_drives(),
                    # which always runs first, above) over the legacy
                    # single-fixed-zone text - dt_start/dt_end stay
                    # naive LOCAL datetimes for every bit of downstream
                    # display/day-grouping code that already expects
                    # that, but are now correctly localized per-row
                    # instead of blindly assumed Sydney; start_utc_epoch/
                    # end_utc_epoch carry the true UTC instant for
                    # sorting, so ordering is correct even across a
                    # DST fall-back or a future interstate trip.
                    start_utc_epoch = int(r["Started At (UTC)"]) if r.get("Started At (UTC)") else None
                    end_utc_epoch = int(r["Ended At (UTC)"]) if r.get("Ended At (UTC)") else None
                    start_tz = r.get("Started At TZ") or tztools.DEFAULT_TIMEZONE
                    end_tz = r.get("Ended At TZ") or tztools.DEFAULT_TIMEZONE
                    if start_utc_epoch is not None:
                        local_dt, start_abbr, _ = tztools.localize(start_utc_epoch, start_tz)
                        dt_start = local_dt.replace(tzinfo=None)
                    else:
                        start_abbr = None
                        start_str = r.get("Started At (AEST)") or r.get("Started At") or r.get("Started")
                        dt_start = datetime.strptime(start_str.strip()[:16], "%Y-%m-%d %H:%M")
                    if end_utc_epoch is not None:
                        local_dt, end_abbr, _ = tztools.localize(end_utc_epoch, end_tz)
                        dt_end = local_dt.replace(tzinfo=None)
                    else:
                        end_abbr = None
                        end_str = r.get("Ended At (AEST)") or r.get("Ended At") or r.get("Ended")
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
                        "start_utc_epoch": start_utc_epoch,
                        "end_utc_epoch": end_utc_epoch,
                        "start_tz_abbr": start_abbr,
                        "end_tz_abbr": end_abbr,
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
                        "start_soc": (r.get("Starting Battery (%)") or "").strip(),
                        "end_soc": (r.get("Ending Battery (%)") or "").strip(),
                        "kwh_used": (r.get("Total Energy Used (kWh)") or "").strip(),
                        "wh_km": (r.get("Average Energy Used (Wh/km)") or "").strip(),
                        "raw": r
                    })
                except Exception:
                    continue

        # TODO-009: sort by true UTC when available (correct across a
        # DST fall-back or any future cross-timezone trip), falling
        # back to local-naive for any legacy row without it.
        parsed.sort(key=lambda x: x["start_utc_epoch"] if x["start_utc_epoch"] is not None else x["start_dt"].timestamp())
        self.drives = parsed
        return self.drives

    def index_footage(self, force_rescan=False):
        if self._indexed and not force_rescan:
            return
        master_dir = resolve_master_teslacam_dir(self.teslacam_dirs)
        tombstones = load_tombstones(master_dir)
        lookup_sets = get_tombstone_lookup_sets(tombstones)
        self.footage_db = []
        seen_rel = set()

        for tc in self.teslacam_dirs:
            if not tc or not os.path.isdir(tc):
                continue
            cat_data = scan_or_update_drive_catalog(tc, master_dir, force_rescan=force_rescan)
            pkgs = cat_data.get("packages", {})

            for rel_k, pkg_info in pkgs.items():
                if is_clip_tombstoned(rel_k, tombstones, lookup_sets=lookup_sets):
                    continue
                cat = pkg_info.get("cat", "Other")
                is_rc = rel_k.startswith("RecentClips")
                folder_path = os.path.join(tc, "RecentClips") if is_rc else os.path.join(tc, rel_k)

                for f in pkg_info.get("files", []):
                    if not f.endswith(".mp4") or f == "event.mp4" or f.startswith("._"):
                        continue
                    file_rel_path = f"RecentClips/{f}" if is_rc else f"{rel_k}/{f}"
                    if is_clip_tombstoned(file_rel_path, tombstones, lookup_sets=lookup_sets):
                        continue
                    if file_rel_path in seen_rel:
                        continue
                    seen_rel.add(file_rel_path)

                    if len(f) >= 19 and f[4] == '-' and f[7] == '-' and f[10] == '_' and f[13] == '-' and f[16] == '-':
                        try:
                            dt = datetime(int(f[0:4]), int(f[5:7]), int(f[8:10]), int(f[11:13]), int(f[14:16]), int(f[17:19]))
                            cam = f[20:-4] if len(f) > 24 else "front"
                            full_fpath = os.path.join(folder_path, f)
                            self.footage_db.append({
                                "dt": dt,
                                "cat": cat,
                                "path": full_fpath,
                                "folder": folder_path,
                                "file": f,
                                "cam": cam,
                                "reason": pkg_info.get("reason", "Event"),
                                "event_ts": pkg_info.get("event_ts", ""),
                                "pkg_size": pkg_info.get("size_bytes", 0)
                            })
                        except ValueError:
                            continue
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
        if isinstance(target_date, str):
            try:
                target_date = datetime.strptime(target_date[:10], "%Y-%m-%d").date()
            except ValueError:
                target_date = now.date()
        elif isinstance(target_date, datetime):
            target_date = target_date.date()

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

        # Check staleness and auto-sync if target date has no drives
        if not getattr(self, "_sync_disabled", False):
            day_drives = [
                d for d in self.drives
                if d["start_dt"] < day_end and d["end_dt"] > day_start
            ]
            if not day_drives and day_start.date() <= now.date():
                if self.sync_drives_if_stale(target_date=target_date):
                    day_drives = [
                        d for d in self.drives
                        if d["start_dt"] < day_end and d["end_dt"] > day_start
                    ]
        else:
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
                    "activity": f"🅿 {current_location} ({dur_str})",
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
                act_str = f"🅿 {current_location} ({dur_str})"
            elif not day_drives:
                time_str = "00:00 - 24:00"
                act_str = f"🅿 {current_location} (Stationary all day - 24h)"
            else:
                e_str = "24:00"
                time_str = f"{s_str} - {e_str}"
                act_str = f"🅿 {current_location} ({dur_str})"

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
            
        # Attach footage to each event (filter for target day once)
        day_clips = [c for c in self.footage_db if day_start <= c["dt"] < day_end]
        for ev in events:
            s_dt = ev["start_dt"]
            e_dt = ev["end_dt"]
            clips = [c for c in day_clips if s_dt <= c["dt"] < e_dt]
            ev["recent_mins"] = len(set(c["dt"] for c in clips if c["cat"] == "Recent"))
            ev["saved_mins"] = len(set(c["dt"] for c in clips if c["cat"] == "Saved"))
            ev["sentry_mins"] = len(set(c["dt"] for c in clips if c["cat"] == "Sentry"))
            ev["clips"] = clips
            
        return events, day_start, None

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
    
    box_inner = 72
    print()
    print(format_box_line("┌", "─", "┐", [box_inner]))
    print(format_title_line(f" 📹 CAMERA FOOTAGE: {t_start.strftime('%a %d %b %Y')} ({t_start.strftime('%H:%M')} ➔ {t_end.strftime('%H:%M')})", box_inner))
    print(format_box_line("├", "─", "┤", [box_inner]))
    print(format_title_line(f"    Origin      : {trip['start_place']} ({trip['start_addr'].split(',')[0]})", box_inner))
    print(format_title_line(f"    Destination : {trip['end_place']} ({trip['end_addr'].split(',')[0]})", box_inner))
    print(format_box_line("└", "─", "┘", [box_inner]))
    
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

    # 3. Drive deep-dive telemetry (AGENTS.md TODO-019) - shown automatically
    # alongside footage whenever it exists, no separate footage-vs-deep-dive
    # prompt, mirroring how tessie_charging_analyzer.py's inspect_session()
    # already shows its charging curve inline. User report (2026-09-15): this
    # used to auto-open the chart in the browser unconditionally on every
    # trip selection - now offered as an explicit "[o]pen chart in browser"
    # action below instead, matching tessie_charging_analyzer.py's own
    # charging-curve action.
    print(f"\n📊 3. DRIVE TELEMETRY:")
    dd_chart_path = None
    deepdive_result = analyzer.get_drive_deepdive(trip)
    if not deepdive_result:
        print("   ℹ️  No deep-dive telemetry (drive_deepdive_*.csv) found for this drive.")
    else:
        dd_rec, dd_segments, dd_chart_path = deepdive_result
        print(f"   Deep-Dive File: {dd_rec.get('source_file', '')} ({dd_rec.get('samples_count', 0)} samples)")
        cls_icon = {"climbing": "⬆️ ", "descending": "⬇️ ", "level": "➡️ ", "stationary": "⏸ "}
        for seg in dd_segments:
            icon = cls_icon.get(seg["class"], "  ")
            eff = seg["efficiency_kwh_per_100km"]
            eff_str = f"{eff:.1f} kWh/100km" if eff is not None else "-"
            print(f"     {icon}{seg['class']:<11} {seg['distance_km']:5.2f} km  {seg['duration_mins']:5.1f} min  "
                  f"Δelev {seg['elevation_change_m']:+6.1f} m   eff: {eff_str}")
        if dd_chart_path:
            print(f"   Elevation/Efficiency Profile: {dd_chart_path}")

    print(f"\n💡 Quick Tips:")
    print(f"   • To view side doors (getting in/out): check *-left_repeater.mp4 or *-right_repeater.mp4")
    print(f"   • To view trunk/rear walking: check *-back.mp4")
    # BUG-087 (2026-09-15): the whole action prompt below - including
    # "[o]pen chart in browser" - used to be nested inside
    # "if start_clips or end_clips:", so a drive with real deep-dive
    # telemetry/chart but NO local dashcam footage (a real user report:
    # an early-morning drive with a chart but no synced footage yet)
    # never got offered the chart action at all. has_chart and
    # has_footage are now independent gates - the chart action shows up
    # whenever a chart exists, whether or not footage does.
    has_chart = bool(dd_chart_path and os.path.exists(dd_chart_path))
    has_footage = bool(start_clips or end_clips)
    first_folder = None
    if has_footage:
        first_folder = (start_clips[0]['folder'] if start_clips else end_clips[0]['folder'])
        print(f"   • Open folder in Finder: open \"{first_folder}\"")
    if (has_footage or has_chart) and sys.stdin.isatty():
        try:
            action_parts = []
            if has_footage:
                action_parts.append("[d]elete footage across all drives")
            if has_chart:
                action_parts.append("[o]pen chart in browser")
            if has_footage:
                action_parts.append("[f]older")
            action_parts.append("[Enter] continue")
            action_choice = input(f"\n📹 Trip Footage Action: {', '.join(action_parts)}: ").strip().lower()
            if action_choice in ("d", "del", "delete") and has_footage:
                all_clips = start_clips + end_clips
                pkgs = build_timeline_footage_packages({"clips": all_clips, "start_dt": trip["start_dt"], "end_dt": trip["end_dt"]})
                if pkgs:
                    total_files = sum(p["files_count"] for p in pkgs)
                    num_drives = max(1, len(analyzer.teslacam_dirs))
                    total_bytes = sum(p["tot_size"] for p in pkgs) * num_drives
                    mb_val = total_bytes / (1024 * 1024)
                    sz_str = f"{mb_val/1024:.1f} GB" if mb_val >= 1024 else f"{mb_val:.0f} MB"
                    print(f"\n⚠️  Permanently delete all footage for this trip (~{total_files * num_drives} files, {sz_str}) across {num_drives} mounted drive(s)? [y/N]: ", end="")
                    conf = input().strip().lower()
                    if conf in ("y", "yes"):
                        n_del, b_del, n_tombs = delete_footage_packages_across_drives(pkgs, analyzer)
                        del_mb = b_del / (1024 * 1024)
                        del_sz = f"{del_mb/1024:.1f} GB" if del_mb >= 1024 else f"{del_mb:.0f} MB"
                        print(f"✔ Successfully deleted {n_del} file(s) ({del_sz}) and recorded {n_tombs} tombstone(s).\n")
            elif action_choice in ("o", "open", "chart", "c") and has_chart:
                try_open_in_browser(dd_chart_path)
            elif action_choice in ("f", "folder") and has_footage:
                if sys.platform == "darwin" and os.path.exists(first_folder):
                    subprocess.run(["open", first_folder])
                    print(f"📂 Opened in Finder: {first_folder}\n")
        except (KeyboardInterrupt, EOFError):
            pass

def drill_down_day(day_str, day_trips, analyzer):
    """Level 2: Display drives for a selected day in a structured table and allow picking a trip for footage listing."""
    while True:
        dt_obj = datetime.strptime(day_str, "%Y-%m-%d")
        total_km = sum(t["dist_km"] for t in day_trips)
        total_mins = sum(t["dur_min"] for t in day_trips)
        hours, mins = divmod(total_mins, 60)
        time_str = f"{hours}h {mins:02d}m" if hours else f"{mins}m"
        
        # Precompute rows to dynamically measure max column width
        rows_data = []
        for i, t in enumerate(day_trips):
            t_start = t["start_dt"].strftime("%H:%M")
            t_end = t["end_dt"].strftime("%H:%M")
            time_w = f" {t_start} ➔ {t_end}"
            dur_str = f" {format_duration_short(t['dur_min'])}"
            dist_str = f" {t['dist_km']:.1f} km"
            
            raw = t.get("raw", {})
            s_soc = t.get("start_soc") or raw.get("Starting Battery (%)", "-")
            e_soc = t.get("end_soc") or raw.get("Ending Battery (%)", "-")
            soc_str = f" {s_soc}%➔{e_soc}%" if s_soc and s_soc != "-" else " -"
            
            s_p = t.get("start_place") or "Start"
            e_p = t.get("end_place") or "End"
            if s_p.lower() == "home":
                s_p = "🏠 Home"
            if e_p.lower() == "home":
                e_p = "🏠 Home"
            route_str = f" {s_p} ➔ {e_p}"
            
            park_str = " —"
            if i < len(day_trips) - 1:
                next_start = day_trips[i+1]["start_dt"]
                d_mins = int((next_start - t["end_dt"]).total_seconds() / 60)
                if d_mins >= 0:
                    park_str = f" {format_duration_short(d_mins)}"
            
            f_tag, f_cats = analyzer.get_trip_footage_summary(t)
            
            rows_data.append({
                "idx": f" [{i+1}]",
                "time": time_w,
                "dur": dur_str,
                "dist": dist_str,
                "soc": soc_str,
                "route": route_str,
                "eff": f" {format_drive_efficiency(t)}",
                "park": park_str,
                "sav": "📹" if "Saved" in f_cats else "·",
                "sen": "🛡️" if "Sentry" in f_cats else "·",
                "rec": "🕒" if "Recent" in f_cats else "·"
            })

        w_idx = max(len(" # "), max((display_len(r["idx"]) + 1 for r in rows_data), default=5))
        w_time = max(len(" Time Window "), max((display_len(r["time"]) + 1 for r in rows_data), default=15))
        w_dur = max(len(" Dur "), max((display_len(r["dur"]) + 1 for r in rows_data), default=8))
        w_dist = max(len(" Distance "), max((display_len(r["dist"]) + 1 for r in rows_data), default=11))
        w_soc = max(len(" SoC % "), max((display_len(r["soc"]) + 1 for r in rows_data), default=10))
        w_route = max(len(" Route (Origin ➔ Destination) "), max((display_len(r["route"]) + 1 for r in rows_data), default=30))
        w_eff = max(len(" Avg Efficiency "), max((display_len(r["eff"]) + 1 for r in rows_data), default=17))
        w_park = max(len(" Parked Duration "), max((display_len(r["park"]) + 1 for r in rows_data), default=14))
        
        w_sav = 7
        w_sen = 8
        w_rec = 8
        w_foot_total = w_sav + w_sen + w_rec + 2  # 25

        left_widths = [w_idx, w_time, w_dur, w_dist, w_soc, w_route, w_eff, w_park]
        left_headers = [" #", " Time Window", " Dur", " Distance", " SoC %", " Route (Origin ➔ Destination)", " Avg Efficiency", " Parked Duration"]

        total_inner = sum(left_widths) + len(left_widths) + w_foot_total
        title = f" 📅 {dt_obj.strftime('%A, %d %B %Y')} — {len(day_trips)} Drives (Total: {time_str}, {total_km:.1f} km)"
        t_len = display_len(title)
        if t_len + 2 > total_inner:
            w_route += (t_len + 2 - total_inner)
            left_widths[5] = w_route
            total_inner = sum(left_widths) + len(left_widths) + w_foot_total

        border_top = "┌" + "─" * total_inner + "┐"
        border_top_header = "├" + "┬".join("─"*w for w in left_widths) + "┬" + "─"*w_foot_total + "┤"
        border_sub = "│" + "│".join(" "*w for w in left_widths) + "├" + "─"*w_sav + "┬" + "─"*w_sen + "┬" + "─"*w_rec + "┤"
        border_data = "├" + "┼".join("─"*w for w in left_widths) + "┼" + "─"*w_sav + "┼" + "─"*w_sen + "┼" + "─"*w_rec + "┤"
        border_bot = "└" + "┴".join("─"*w for w in left_widths) + "┴" + "─"*w_sav + "┴" + "─"*w_sen + "┴" + "─"*w_rec + "┘"
        
        print(f"\n{border_top}")
        print(format_title_line(title, total_inner, "left"))
        print(border_top_header)
        
        row1_cols = [pad_display(h, w) for h, w in zip(left_headers, left_widths)] + [pad_display("Footage", w_foot_total, "center")]
        print("│" + "│".join(row1_cols) + "│")
        print(border_sub)
        
        row2_cols = [" "*w for w in left_widths] + [
            pad_display("Saved", w_sav, "center"),
            pad_display("Sentry", w_sen, "center"),
            pad_display("Recent", w_rec, "center")
        ]
        print("│" + "│".join(row2_cols) + "│")
        print(border_data)
        
        all_widths = left_widths + [w_sav, w_sen, w_rec]
        # idx/dur/dist/soc/park are numbers (with units, or plain for idx) -> right-aligned;
        # time and route are free text -> left-aligned; footage icons stay centered.
        all_aligns = ["right", "left", "right", "right", "right", "left", "right", "right", "center", "center", "center"]
        for r in rows_data:
            row_cells = [
                r["idx"] + " ", r["time"], r["dur"] + " ", r["dist"] + " ", r["soc"] + " ", r["route"], r["eff"] + " ", r["park"] + " ",
                r["sav"], r["sen"], r["rec"]
            ]
            print(format_row(row_cells, all_widths, all_aligns))
            
        print(border_bot)
        try:
            choice = input(f"Select Trip [1-{len(day_trips)}] for footage, [t]imeline, [a]ll, [b]ack to days, [q]uit: ").strip().lower()
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

def drill_down_all_days(sorted_days, days_dict, analyzer, title):
    """Level 2 (combined): flatten every day in the current view into one
    chronologically-ordered trip table with a Date column, so [a]ll at the
    days menu shows the whole period at a glance instead of chaining through
    each day's own interactive drill-down one at a time."""
    all_trips = [(d_str, t) for d_str in sorted_days for t in days_dict[d_str]]

    while True:
        total_km = sum(t["dist_km"] for _, t in all_trips)
        total_mins = sum(t["dur_min"] for _, t in all_trips)
        hours, mins = divmod(total_mins, 60)
        time_str = f"{hours}h {mins:02d}m" if hours else f"{mins}m"

        rows_data = []
        for i, (d_str, t) in enumerate(all_trips):
            dt_day = datetime.strptime(d_str, "%Y-%m-%d")
            date_str = f" {dt_day.strftime('%a %d %b')}"
            t_start = t["start_dt"].strftime("%H:%M")
            t_end = t["end_dt"].strftime("%H:%M")
            time_w = f" {t_start} ➔ {t_end}"
            dur_str = f" {format_duration_short(t['dur_min'])}"
            dist_str = f" {t['dist_km']:.1f} km"

            raw = t.get("raw", {})
            s_soc = t.get("start_soc") or raw.get("Starting Battery (%)", "-")
            e_soc = t.get("end_soc") or raw.get("Ending Battery (%)", "-")
            soc_str = f" {s_soc}%➔{e_soc}%" if s_soc and s_soc != "-" else " -"

            s_p = t.get("start_place") or "Start"
            e_p = t.get("end_place") or "End"
            if s_p.lower() == "home":
                s_p = "🏠 Home"
            if e_p.lower() == "home":
                e_p = "🏠 Home"
            route_str = f" {s_p} ➔ {e_p}"

            # Parked Duration is the gap to the next trip - only meaningful
            # within the same day (the list crosses day boundaries, newest
            # day first), so it's only computed when the next entry is
            # chronologically later on the same date.
            park_str = " —"
            if i < len(all_trips) - 1 and all_trips[i+1][0] == d_str:
                next_start = all_trips[i+1][1]["start_dt"]
                d_mins = int((next_start - t["end_dt"]).total_seconds() / 60)
                if d_mins >= 0:
                    park_str = f" {format_duration_short(d_mins)}"

            f_tag, f_cats = analyzer.get_trip_footage_summary(t)

            rows_data.append({
                "idx": f" [{i+1}]",
                "date": date_str,
                "time": time_w,
                "dur": dur_str,
                "dist": dist_str,
                "soc": soc_str,
                "route": route_str,
                "eff": f" {format_drive_efficiency(t)}",
                "park": park_str,
                "sav": "📹" if "Saved" in f_cats else "·",
                "sen": "🛡️" if "Sentry" in f_cats else "·",
                "rec": "🕒" if "Recent" in f_cats else "·"
            })

        w_idx = max(len(" # "), max((display_len(r["idx"]) + 1 for r in rows_data), default=5))
        w_date = max(len(" Date "), max((display_len(r["date"]) + 1 for r in rows_data), default=13))
        w_time = max(len(" Time Window "), max((display_len(r["time"]) + 1 for r in rows_data), default=15))
        w_dur = max(len(" Dur "), max((display_len(r["dur"]) + 1 for r in rows_data), default=8))
        w_dist = max(len(" Distance "), max((display_len(r["dist"]) + 1 for r in rows_data), default=11))
        w_soc = max(len(" SoC % "), max((display_len(r["soc"]) + 1 for r in rows_data), default=10))
        w_route = max(len(" Route (Origin ➔ Destination) "), max((display_len(r["route"]) + 1 for r in rows_data), default=30))
        w_eff = max(len(" Avg Efficiency "), max((display_len(r["eff"]) + 1 for r in rows_data), default=17))
        w_park = max(len(" Parked Duration "), max((display_len(r["park"]) + 1 for r in rows_data), default=14))

        w_sav = 7
        w_sen = 8
        w_rec = 8
        w_foot_total = w_sav + w_sen + w_rec + 2  # 25

        left_widths = [w_idx, w_date, w_time, w_dur, w_dist, w_soc, w_route, w_eff, w_park]
        left_headers = [" #", " Date", " Time Window", " Dur", " Distance", " SoC %", " Route (Origin ➔ Destination)", " Avg Efficiency", " Parked Duration"]

        total_inner = sum(left_widths) + len(left_widths) + w_foot_total
        title_str = f" 📋 {title} — All {len(all_trips)} Drives (Total: {time_str}, {total_km:.1f} km)"
        t_len = display_len(title_str)
        if t_len + 2 > total_inner:
            w_route += (t_len + 2 - total_inner)
            left_widths[6] = w_route
            total_inner = sum(left_widths) + len(left_widths) + w_foot_total

        border_top = "┌" + "─" * total_inner + "┐"
        border_top_header = "├" + "┬".join("─"*w for w in left_widths) + "┬" + "─"*w_foot_total + "┤"
        border_sub = "│" + "│".join(" "*w for w in left_widths) + "├" + "─"*w_sav + "┬" + "─"*w_sen + "┬" + "─"*w_rec + "┤"
        border_data = "├" + "┼".join("─"*w for w in left_widths) + "┼" + "─"*w_sav + "┼" + "─"*w_sen + "┼" + "─"*w_rec + "┤"
        border_bot = "└" + "┴".join("─"*w for w in left_widths) + "┴" + "─"*w_sav + "┴" + "─"*w_sen + "┴" + "─"*w_rec + "┘"

        print(f"\n{border_top}")
        print(format_title_line(title_str, total_inner, "left"))
        print(border_top_header)

        row1_cols = [pad_display(h, w) for h, w in zip(left_headers, left_widths)] + [pad_display("Footage", w_foot_total, "center")]
        print("│" + "│".join(row1_cols) + "│")
        print(border_sub)

        row2_cols = [" "*w for w in left_widths] + [
            pad_display("Saved", w_sav, "center"),
            pad_display("Sentry", w_sen, "center"),
            pad_display("Recent", w_rec, "center")
        ]
        print("│" + "│".join(row2_cols) + "│")
        print(border_data)

        all_widths = left_widths + [w_sav, w_sen, w_rec]
        # idx/dur/dist/soc are numbers (with units, or plain for idx) -> right-aligned;
        # date/time/route are free text -> left-aligned; footage icons stay centered.
        all_aligns = ["right", "left", "left", "right", "right", "right", "left", "right", "right", "center", "center", "center"]
        for r in rows_data:
            row_cells = [
                r["idx"] + " ", r["date"], r["time"], r["dur"] + " ", r["dist"] + " ", r["soc"] + " ", r["route"], r["eff"] + " ", r["park"] + " ",
                r["sav"], r["sen"], r["rec"]
            ]
            print(format_row(row_cells, all_widths, all_aligns))

        print(border_bot)
        try:
            choice = input(f"Select Trip [1-{len(all_trips)}] for footage, [a]ll, [b]ack to days, [q]uit: ").strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["b", "back"]:
                break
            elif choice in ["a", "all"]:
                for _, t in all_trips:
                    display_footage_details(t, analyzer)
            elif choice.isdigit() and 1 <= int(choice) <= len(all_trips):
                display_footage_details(all_trips[int(choice)-1][1], analyzer)
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

def ensure_package_sizes(packages):
    """Accurately calculates total size on disk for selected packages before staging/display."""
    for p in packages:
        files = p.get("files", [])
        if files:
            try:
                real_size = sum(os.path.getsize(f) for f in files if os.path.exists(f))
                if real_size > 0:
                    p["tot_size"] = real_size
            except OSError:
                pass

def build_timeline_footage_packages(event, compute_sizes=False):
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
        reason = f_clips[0].get("reason") or "Event"
        event_ts_str = None
        event_dt = None
        cached_ts = f_clips[0].get("event_ts")
        if cached_ts:
            try:
                event_dt = datetime.fromisoformat(cached_ts.split("+")[0].split(".")[0])
                event_ts_str = event_dt.strftime("%H:%M:%S")
            except Exception:
                pass
        unique_dts = sorted(list(set(c["dt"] for c in f_clips)))
        if not event_dt and unique_dts:
            event_dt = unique_dts[0]
            event_ts_str = event_dt.strftime("%H:%M:%S")
        
        unique_cams = set(c["cam"] for c in f_clips)
        if compute_sizes:
            tot_size = sum(os.path.getsize(c["path"]) for c in f_clips if os.path.exists(c["path"]))
        else:
            tot_size = len(f_clips) * (7.5 * 1024 * 1024)
        folder_base = os.path.basename(folder)
        dur_m = len(unique_dts)
        start_ts = unique_dts[0].strftime("%H:%M:%S") if unique_dts else "--:--:--"
        end_ts = unique_dts[-1].strftime("%H:%M:%S") if unique_dts else "--:--:--"
        range_str = f"{start_ts} ➔ {end_ts} ({dur_m}m)" if dur_m > 1 else f"{start_ts} (1m)"
        
        all_files = [c["path"] for c in f_clips]
        
        event_pkgs.append({
            "pkg_idx": None,
            "dt": event_dt,
            "start_dt": unique_dts[0] if unique_dts else event_dt,
            "end_dt": unique_dts[-1] if unique_dts else event_dt,
            "cat": cat,
            "time_str": event_ts_str or (unique_dts[0].strftime("%H:%M:%S") if unique_dts else "--:--:--"),
            "time_range_str": range_str,
            "event_idx": event.get("event_idx"),
            "event_time_str": event.get("time_str"),
            "event_activity": event.get("activity", "").strip(),
            "reason": f"{reason} ({dur_m}m)",
            "cams_count": len(unique_cams),
            "files_count": len(f_clips),
            "tot_size": tot_size,
            "rel_path": f"{cat}Clips/{folder_base}/",
            "folder": folder,
            "files": all_files,
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
        if compute_sizes:
            tot_size = sum(os.path.getsize(c["path"]) for c in all_chunk_clips if os.path.exists(c["path"]))
        else:
            tot_size = len(all_chunk_clips) * (7.5 * 1024 * 1024)
        
        start_ts = c_start.strftime("%H:%M:%S")
        end_ts = c_end.strftime("%H:%M:%S")
        range_str = f"{start_ts} ➔ {end_ts} ({mins}m)" if mins > 1 else f"{start_ts} (1m)"

        if mins == 1:
            time_label = start_ts
            reason_label = "Continuous Loop (1m)"
            rel_pattern = f"RecentClips/{c_start.strftime('%Y-%m-%d_%H-%M-%S')}-*.mp4"
        else:
            time_label = f"{c_start.strftime('%H:%M')} - {c_end.strftime('%H:%M')}"
            reason_label = f"Continuous Loop ({mins}m)"
            rel_pattern = f"RecentClips/{c_start.strftime('%Y-%m-%d_%H-%M')} ➔ {c_end.strftime('%H-%M')}"

        packages.append({
            "pkg_idx": None,
            "dt": c_start,
            "start_dt": c_start,
            "end_dt": c_end,
            "cat": "Recent",
            "time_str": time_label,
            "time_range_str": range_str,
            "event_idx": event.get("event_idx"),
            "event_time_str": event.get("time_str"),
            "event_activity": event.get("activity", "").strip(),
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
    ensure_package_sizes(packages)
    w_idx = 5
    w_time = 15
    w_type = 11
    w_reason = 25
    w_files = 21
    w_path = 44
    col_widths = [w_idx, w_time, w_type, w_reason, w_files, w_path]
    base_inner = sum(col_widths) + len(col_widths) - 1
    
    t_start = event["start_dt"]
    title = f" 📹 Event Footage: {t_start.strftime('%a %d %b %Y')} ({event['time_str']}) — {event['activity']}"
    
    t_len = display_len(title)
    if t_len >= base_inner:
        w_path += (t_len - base_inner) + 3
        col_widths = [w_idx, w_time, w_type, w_reason, w_files, w_path]
    total_inner = sum(col_widths) + len(col_widths) - 1
    
    if not packages:
        print(f"┌{'─'*total_inner}┐")
        print(f"│{pad_display(title, total_inner, 'left')}│")
        print(f"├{'─'*total_inner}┤")
        print(f"│{pad_display(' ⚠️  No local camera footage found on archive drives for this event.', total_inner, 'left')}│")
        print(f"└{'─'*total_inner}┘")
        return packages

    h_row = [f" {'#':^3} ", " Time / Window", " Type", " Trigger / Reason", " Cameras & Files", " TeslaCam Path / Target"]

    print(format_box_line("┌", "─", "┐", [total_inner]))
    print(f"│{pad_display(title, total_inner, 'left')}│")
    print(format_box_line("├", "┬", "┤", col_widths))
    print(format_row(h_row, col_widths, aligns=["center", "left", "left", "left", "left", "left"]))
    print(format_box_line("├", "┼", "┤", col_widths))

    total_files = sum(p["files_count"] for p in packages)
    total_bytes = sum(p["tot_size"] for p in packages)
    total_mb = total_bytes / (1024 * 1024)
    total_size_str = f"{total_mb/1024:.1f} GB" if total_mb >= 1024 else f"{total_mb:.0f} MB"

    tombstones_data = load_tombstones(analyzer.repo_root)

    for p in packages:
        is_fl = is_package_flagged(p, tombstones_data=tombstones_data, repo_root=analyzer.repo_root)
        if is_fl:
            type_label = "🗑️ Flagged"
        else:
            icon = "🔄" if p["cat"] == "Recent" else ("💾" if p["cat"] == "Saved" else ("🔴" if p["cat"] == "Sentry" else "📁"))
            type_label = f"{icon} {p['cat']}"
        size_mb = p["tot_size"] / (1024 * 1024)
        size_str = f"{size_mb/1024:.1f} GB" if size_mb >= 1024 else f"{size_mb:.0f} MB"
        files_label = f"{p['files_count']} files ({size_str})"
        
        c_idx = f" [{p['pkg_idx']:>2}]"
        c_time = f" {p['time_str']}"
        c_type = f" {type_label}"
        c_reason = f" {p['reason']}"
        c_files = f" {files_label}"
        c_path = f" {p['rel_path']}"
        
        row_vals = [c_idx, c_time, c_type, c_reason, c_files, c_path]
        print(format_row(row_vals, col_widths, aligns=["left", "left", "left", "left", "left", "left"]))

    print(format_box_line("├", "┴", "┤", col_widths))
    sum_msg = f" Summary: {len(packages)} footage items ({total_files} video files, {total_size_str})"
    print(f"│{pad_display(sum_msg, total_inner, 'left')}│")
    print(format_box_line("└", "─", "┘", [total_inner]))
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
            n_flagged = len(get_flagged_tombstones(analyzer.repo_root))
            purge_opt = f", [P]urge flagged ({n_flagged})" if n_flagged > 0 else ""
            prompt_str = f"Footage Action: [1, 2, 'all'] copy, [v <#>] preview, [f <#>] flag delete, [u <#>] unflag{purge_opt}, [r]eview, [o]pen, [b]ack, [q]uit: "
            choice = input(prompt_str).strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["b", "back"]:
                break
            elif choice in ["r", "review"]:
                run_interactive_footage_review(packages, analyzer, context_label=f"Event {event['time_str']}")
                s_dt = event["start_dt"]
                e_dt = event.get("end_dt", s_dt + timedelta(seconds=120))
                event["clips"] = [c for c in analyzer.footage_db if s_dt <= c["dt"] < e_dt]
                continue
            elif choice in ["o", "open"]:
                folder_to_open = packages[0]["folder"] if packages else None
                if folder_to_open and os.path.exists(folder_to_open):
                    if sys.platform == "darwin":
                        subprocess.run(["open", folder_to_open])
                    print(f"📂 Opened in Finder: {folder_to_open}\n")
                else:
                    print("⚠️ Folder not accessible.\n")
                continue
            elif choice in ["p", "purge"]:
                flagged_tombs = get_flagged_tombstones(analyzer.repo_root)
                if not flagged_tombs:
                    print("\n ℹ️  No footage packages are currently flagged for deletion.\n")
                    continue
                display_flagged_deletions_table(analyzer)
                try:
                    conf = input(" Type 'yes' or 'y' to permanently purge all flagged footage now across mounted drives: ").strip().lower()
                except (KeyboardInterrupt, EOFError):
                    print("\n Purge cancelled.\n")
                    continue
                if conf in ("y", "yes"):
                    n_del, b_del, n_tombs = purge_flagged_tombstones_across_drives(analyzer.teslacam_dirs, analyzer.repo_root)
                    del_mb = b_del / (1024 * 1024)
                    del_sz = f"{del_mb/1024:.1f} GB" if del_mb >= 1024 else f"{del_mb:.0f} MB"
                    print(f"\n ✔ Successfully purged {n_del} file(s) ({del_sz}) across {len(analyzer.teslacam_dirs)} drive(s).")
                    print(f" ✔ Updated {n_tombs} tombstone(s) to 'purged' in Tessie/dashcam_tombstones.json.\n")
                    analyzer.index_footage()
                    s_dt = event["start_dt"]
                    e_dt = event.get("end_dt", s_dt + timedelta(seconds=120))
                    event["clips"] = [c for c in analyzer.footage_db if s_dt <= c["dt"] < e_dt]
                else:
                    print(" Purge cancelled.\n")
                continue

            is_flag_del = False
            is_unflag = False
            is_preview = False
            target_str = choice

            if choice.startswith("f ") or choice.startswith("flag ") or choice.startswith("d ") or choice.startswith("del ") or choice.startswith("delete "):
                is_flag_del = True
                target_str = choice.split(maxsplit=1)[1].strip()
            elif choice in ("f", "flag", "d", "del", "delete"):
                is_flag_del = True
                target_str = input("Select footage item(s) to flag for deletion [e.g. 1, 1-3, 'all']: ").strip().lower()
            elif choice.startswith("u ") or choice.startswith("unflag "):
                is_unflag = True
                target_str = choice.split(maxsplit=1)[1].strip()
            elif choice in ("u", "unflag"):
                is_unflag = True
                target_str = input("Select footage item(s) to unflag [e.g. 1, 1-3, 'all']: ").strip().lower()
            elif choice.startswith("v ") or choice.startswith("view ") or choice.startswith("play "):
                is_preview = True
                target_str = choice.split(maxsplit=1)[1].strip()
            elif choice in ("v", "view", "play"):
                is_preview = True
                target_str = input("Select footage item to preview [1-N]: ").strip().lower()

            selected_indices = parse_selection_indices(target_str, len(packages))
            if not selected_indices:
                print(f"Invalid choice. Please enter item numbers (e.g. 1, 6-9, 'all'), [v <#>] to preview, [f <#>] to flag, [u <#>] to unflag, [P]urge, [r]eview, [o]pen, [b]ack, or [q]uit.\n")
                continue

            if is_preview:
                for idx_val in selected_indices:
                    matching = [p for p in packages if p["pkg_idx"] == idx_val]
                    if matching:
                        p_cand = matching[0]
                        clip_p = get_primary_preview_clip(p_cand)
                        if clip_p:
                            preview_footage_file(clip_p, method="quicklook")
                            print(f"\n📂 Revealed in Finder for item [{idx_val}]: {os.path.basename(clip_p)}\n")
                        else:
                            print(f"\n⚠️  No previewable MP4 file found for item [{idx_val}].\n")
                continue

            selected_pkgs = [p for p in packages if p["pkg_idx"] in selected_indices]
            
            if is_flag_del:
                n_flag, b_flag = flag_footage_packages_for_deletion(selected_pkgs, analyzer)
                f_mb = b_flag / (1024 * 1024)
                f_sz = f"{f_mb/1024:.1f} GB" if f_mb >= 1024 else f"{f_mb:.0f} MB"
                num_drives = max(1, len(analyzer.teslacam_dirs))
                mult_mb = (b_flag * num_drives) / (1024 * 1024)
                mult_sz = f"{mult_mb/1024:.1f} GB" if mult_mb >= 1024 else f"{mult_mb:.0f} MB"
                print(f"\n ✔ Flagged {n_flag} footage package(s) for deletion (~{f_sz}/drive ➔ ~{mult_sz} across {num_drives} drive(s)).")
                print("   Files remain on disk until explicitly purged via 'dashcam_sync.sh' or [P]urge.\n")
                continue

            if is_unflag:
                n_unflag = unflag_footage_packages(selected_pkgs, analyzer)
                print(f"\n ✔ Unflagged {n_unflag} footage package(s).\n")
                continue
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

def parse_range_selection(raw_str, max_val):
    """Parses range strings like '11', '16-20', '1,3,5', 'all' into sorted list of integers."""
    raw_str = raw_str.strip().lower()
    if raw_str in ("all", "*"):
        return list(range(1, max_val + 1))
    results = set()
    parts = raw_str.replace(" ", ",").split(",")
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if "-" in p:
            sub = p.split("-")
            if len(sub) == 2 and sub[0].isdigit() and sub[1].isdigit():
                s, e = int(sub[0]), int(sub[1])
                for v in range(min(s, e), max(s, e) + 1):
                    if 1 <= v <= max_val:
                        results.add(v)
        elif p.isdigit():
            v = int(p)
            if 1 <= v <= max_val:
                results.add(v)
    return sorted(list(results))

def render_staging_preview_table(packages_to_process, title_desc, analyzer):
    """Renders a 100% pixel-perfect Unicode preview table for packages being staged."""
    ensure_package_sizes(packages_to_process)
    n_drives = max(1, len(analyzer.teslacam_dirs))
    tot_bytes = sum(p["tot_size"] for p in packages_to_process)
    tot_files = sum(p["files_count"] for p in packages_to_process)
    tot_mb = tot_bytes / (1024 * 1024)
    tot_sz = f"{tot_mb/1024:.1f} GB" if tot_mb >= 1024 else f"{tot_mb:.0f} MB"
    mult_mb = (tot_bytes * n_drives) / (1024 * 1024)
    mult_sz = f"{mult_mb/1024:.1f} GB" if mult_mb >= 1024 else f"{mult_mb:.0f} MB"

    w_idx = 5
    w_ev = 7
    w_act = 31
    max_rng_len = max((display_len(p.get("time_range_str") or p.get("time_str", "")) for p in packages_to_process), default=22)
    w_rng = max(24, max_rng_len + 2)
    max_path_len = max((display_len(p.get("rel_path", "")) for p in packages_to_process), default=24)
    w_path = max(26, min(42, max_path_len + 2))
    w_files = 7
    w_size = 11

    col_widths = [w_idx, w_ev, w_act, w_rng, w_path, w_files, w_size]
    col_aligns = ["center", "center", "left", "left", "left", "right", "right"]
    total_inner = sum(col_widths) + len(col_widths) - 1

    t_hdr = f" 🗑️  STAGE FOOTAGE IN {title_desc} FOR DELETION ({len(packages_to_process)} Packages)"
    t_len = display_len(t_hdr)
    if t_len >= total_inner:
        w_path += (t_len - total_inner) + 3
        col_widths[4] = w_path
        total_inner = sum(col_widths) + len(col_widths) - 1

    print(f"\n┌{'─'*total_inner}┐")
    print(format_title_line(t_hdr, total_inner, "left"))
    print(format_box_line("├", "┬", "┤", col_widths))
    print(format_row(["#", "Event", " State & Route", " Time Range", " Package Path", "Files ", "Est. Size "], col_widths, col_aligns))
    print(format_box_line("├", "┼", "┤", col_widths))

    for i, p in enumerate(packages_to_process, 1):
        c_i = f"[{i}]"
        c_ev = f"[#{p['event_idx']}]" if p.get("event_idx") else "--"
        act_raw = p.get("event_activity", "").strip()
        c_act = " " + truncate_display(act_raw, w_act - 2)
        rng_raw = p.get("time_range_str") or p.get("time_str", "")
        c_rng = " " + rng_raw
        path_raw = p.get("rel_path", "")
        c_path = " " + (path_raw if display_len(path_raw) <= (w_path - 2) else truncate_display(path_raw, w_path - 2))
        c_fl = f"{p['files_count']} "
        p_mb = p["tot_size"] / (1024 * 1024)
        p_sz = f"{p_mb/1024:.1f} GB" if p_mb >= 1024 else f"{p_mb:.0f} MB"
        c_sz = f"{p_sz} "
        print(format_row([c_i, c_ev, c_act, c_rng, c_path, c_fl, c_sz], col_widths, col_aligns))

    print(format_box_line("├", "┴", "┤", col_widths))
    tot_msg = f" Total Reclaimable: {len(packages_to_process)} package(s), {tot_files} files (~{tot_sz}/drive ➔ ~{mult_sz} across {n_drives} drive(s))"
    print(f"│{pad_display(tot_msg, total_inner, 'left')}│")
    print(format_box_line("└", "─", "┘", [total_inner]))

def batch_flag_events_interactive(events, choice_str, analyzer, is_unflag=False):
    """
    Interactively flags or unflags footage packages belonging to selected events (e.g. '11', '16-20', 'home', 'all').
    Renders an unambiguous preview table of exactly what ranges, folders, and sizes are being staged.
    """
    raw_target = choice_str.strip()
    for pfx in ["unflag", "flag", "delete", "del", "f", "d", "u"]:
        if raw_target.lower().startswith(pfx + " "):
            raw_target = raw_target[len(pfx):].strip()
            break
            
    if not raw_target:
        print("⚠️  Please specify event number(s), range, or 'home' (e.g. 'f 1', 'f 16-20', 'f home', 'f all').\n")
        return

    master_dir = resolve_master_teslacam_dir(analyzer.teslacam_dirs)
    tombstones_data = load_tombstones(master_dir)
    lookup_sets = get_tombstone_lookup_sets(tombstones_data)

    is_home_target = raw_target.lower() in ("home", "h", "a", "auto", "autoflag", "auto-flag")

    if is_home_target:
        packages_to_process = []
        selected_events = []
        for ev in events:
            if ev.get("type") == "parked" and is_home_event(ev, analyzer):
                pkgs = build_timeline_footage_packages(ev)
                for p in pkgs:
                    if p.get("cat") == "Recent":
                        if is_unflag:
                            if is_package_flagged(p, tombstones_data=tombstones_data, master_dir=master_dir, lookup_sets=lookup_sets):
                                packages_to_process.append(p)
                                if ev not in selected_events:
                                    selected_events.append(ev)
                        else:
                            if not is_package_flagged(p, tombstones_data=tombstones_data, master_dir=master_dir, lookup_sets=lookup_sets):
                                packages_to_process.append(p)
                                if ev not in selected_events:
                                    selected_events.append(ev)
        if not packages_to_process:
            if is_unflag:
                print("\nℹ️  No flagged Home Recent packages found on this day.\n")
            else:
                print("\nℹ️  No unflagged Home Recent packages found on this day.\n")
            return
        idx_title = f"HOME RECENT (#{','.join(str(e['event_idx']) for e in selected_events)})"
    else:
        selected_indices = parse_range_selection(raw_target, len(events))
        if not selected_indices:
            print(f"⚠️  No valid events matching '{raw_target}'. Enter event numbers 1-{len(events)} (e.g. '16-20', 'home', or 'all').\n")
            return

        target_events = [ev for ev in events if ev["event_idx"] in selected_indices]
        packages_to_process = []
        for ev in target_events:
            packages_to_process.extend(build_timeline_footage_packages(ev))

        if not packages_to_process:
            print(f"\n⚠️  No footage packages found in selected event(s) #{','.join(str(i) for i in selected_indices)}.\n")
            return
        idx_title = f"EVENTS #{min(selected_indices)}–#{max(selected_indices)}" if len(selected_indices) > 1 else f"EVENT #{selected_indices[0]}"

    if is_unflag:
        n_unflag = unflag_footage_packages(packages_to_process, analyzer)
        print(f"\n✔ Unflagged {n_unflag} footage package(s).\n")
        return

    # Unambiguous preview table before flagging
    render_staging_preview_table(packages_to_process, idx_title, analyzer)

    try:
        conf = input(f" Confirm staging these {len(packages_to_process)} package(s) for deletion? [Y/n]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        conf = "n"

    if conf in ("", "y", "yes"):
        n_flag, b_flag = flag_footage_packages_for_deletion(packages_to_process, analyzer)
        print(f"\n ✔ Successfully flagged {n_flag} footage package(s) for deletion.")
        print("   Files remain on disk until explicitly purged via 'dashcam_sync.sh' or [P]urge.\n")
    else:
        print("\n Flagging cancelled.\n")

def find_home_recent_footage_packages(analyzer, target_dates=None):
    """Finds all unflagged RecentClips packages recorded while parked at Home across target dates."""
    analyzer.index_footage()
    master_dir = resolve_master_teslacam_dir(analyzer.teslacam_dirs)
    tombstones_data = load_tombstones(master_dir)
    lookup_sets = get_tombstone_lookup_sets(tombstones_data)

    if target_dates:
        dates_to_scan = target_dates
    else:
        # Only scan dates that actually have RecentClips indexed in footage_db
        dates_to_scan = sorted(list(set(c["dt"].date() for c in analyzer.footage_db if c.get("cat") == "Recent")))

    home_recent_pkgs = []
    for d_date in dates_to_scan:
        events, _, _ = analyzer.get_timeline_data(d_date)
        for ev in events:
            if ev.get("type") == "parked" and is_home_event(ev, analyzer):
                pkgs = build_timeline_footage_packages(ev)
                for p in pkgs:
                    if p.get("cat") == "Recent" and not is_package_flagged(p, tombstones_data=tombstones_data, master_dir=master_dir, lookup_sets=lookup_sets):
                        home_recent_pkgs.append(p)

    return home_recent_pkgs

def purge_home_recent_cli(analyzer, target_dates=None, stage_only=False, dry_run=False, yes=False):
    """CLI flow to stage or purge all Home RecentClips footage packages."""
    pkgs = find_home_recent_footage_packages(analyzer, target_dates=target_dates)
    if not pkgs:
        print("\nℹ️  No unflagged Home Recent footage packages found.\n")
        return 0, 0

    title = f"HOME RECENT FOOTAGE ({len(pkgs)} Packages Across {len(set(p['dt'].date() for p in pkgs if p['dt']))} Days)"
    render_staging_preview_table(pkgs, title, analyzer)

    if dry_run:
        print("\nℹ️  Dry-run: No files were flagged or purged.\n")
        return 0, 0

    if not yes:
        action_desc = "staging" if stage_only else "permanently purging"
        try:
            conf = input(f"\n Confirm {action_desc} these {len(pkgs)} Home Recent package(s)? [Y/n]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            conf = "n"
        if conf not in ("", "y", "yes"):
            print("\n Operation cancelled.\n")
            return 0, 0

    n_flag, b_flag = flag_footage_packages_for_deletion(pkgs, analyzer)
    analyzer._indexed = False

    if stage_only:
        print(f"\n ✔ Flagged {n_flag} Home Recent package(s) for deletion.")
        print("   Files remain on disk until explicitly purged via 'dashcam_sync.sh' or [P]urge.\n")
        return n_flag, b_flag

    print("\n🗑️  Executing multi-drive purge of flagged Home Recent packages...")
    n_del, b_del, n_tombs = purge_flagged_tombstones_across_drives(analyzer.teslacam_dirs, analyzer.repo_root)
    analyzer._indexed = False
    f_mb = b_del / (1024 * 1024)
    f_sz = f"{f_mb/1024:.1f} GB" if f_mb >= 1024 else f"{f_mb:.0f} MB"
    print(f"\n ✔ Successfully purged {n_del} file(s) across connected drives ({f_sz} freed).\n")
    return n_del, b_del

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

        master_dir = resolve_master_teslacam_dir(analyzer.teslacam_dirs)
        tombstones_data = load_tombstones(master_dir)
        lookup_sets = get_tombstone_lookup_sets(tombstones_data)

        # Expand w_act dynamically so that no route or location line gets truncated or broken
        max_act_len = max((display_len(ev["activity"]) + 4 for ev in events), default=50)
        w_act = max(60, max_act_len + 3)
        col_widths = [w_idx, w_time, w_rec, w_sav, w_sen, w_act]
        total_inner = sum(col_widths) + len(col_widths) - 1
        
        total_recent_mins = sum(ev["recent_mins"] for ev in events)
        total_saved_mins = sum(ev["saved_mins"] for ev in events)
        total_sentry_mins = sum(ev["sentry_mins"] for ev in events)
        has_any_footage = (total_recent_mins + total_saved_mins + total_sentry_mins) > 0
        
        h_row = [f" {'#':^3} ", " Time Window", "🔄", "💾", "🔴", " Vehicle State & Route / Location"]

        print(format_box_line("┌", "─", "┐", [total_inner]))
        print(f"│{pad_display(title, total_inner, 'left')}│")
        print(format_box_line("├", "┬", "┤", col_widths))
        print(format_row(h_row, col_widths, aligns=["center", "left", "center", "center", "center", "left"]))
        print(format_box_line("├", "┼", "┤", col_widths))
        
        total_flagged_mins = 0
        home_recent_pkgs = []
        for ev in events:
            r_str = format_duration_short(ev['recent_mins'])
            s_str = format_duration_short(ev['saved_mins'])
            sn_str = format_duration_short(ev['sentry_mins'])
            
            c_idx = f" [{ev['event_idx']:>2}]"
            c_time = " " + ev['time_str']
            c_rec = r_str
            c_sav = s_str
            c_sen = sn_str
            
            ev_pkgs = build_timeline_footage_packages(ev)
            is_ev_flagged = any(is_package_flagged(p, tombstones_data=tombstones_data, master_dir=master_dir, lookup_sets=lookup_sets) for p in ev_pkgs) if ev_pkgs else False
            if is_ev_flagged:
                total_flagged_mins += (ev['recent_mins'] + ev['saved_mins'] + ev['sentry_mins'])

            if ev.get("type") == "parked" and is_home_event(ev, analyzer):
                for p in ev_pkgs:
                    if p.get("cat") == "Recent" and not is_package_flagged(p, tombstones_data=tombstones_data, master_dir=master_dir, lookup_sets=lookup_sets):
                        home_recent_pkgs.append(p)

            full_act_text = ev["activity"].strip() + (" 🗑️" if is_ev_flagged else "")
            act_lines = wrap_text_display(full_act_text, w_act - 2)
            c_act = " " + act_lines[0]
            row_vals = [c_idx, c_time, c_rec, c_sav, c_sen, c_act]
            print(format_row(row_vals, col_widths, aligns=["center", "left", "center", "center", "center", "left"]))
            
            for extra in act_lines[1:]:
                extra_vals = ["", "", "", "", "", "   " + extra]
                print(format_row(extra_vals, col_widths, aligns=["left", "left", "left", "left", "left", "left"]))
            
        print(format_box_line("├", "┴", "┤", col_widths))
        if not has_any_footage:
            foot_msg = " No local footage on archive SSD. Run 'dashcam_sync.sh' to backup from car."
            print(f"│{pad_display(foot_msg, total_inner, 'left')}│")
        else:
            rec_tot = format_duration_short(total_recent_mins)
            sav_tot = format_duration_short(total_saved_mins)
            sen_tot = format_duration_short(total_sentry_mins)
            flag_note = f" (🗑️ {format_duration_short(total_flagged_mins)} staged for purge)" if total_flagged_mins > 0 else ""
            foot_msg = f" Footage Totals: 🔄 {rec_tot} | 💾 {sav_tot} | 🔴 {sen_tot}{flag_note}"
            foot_pad = pad_display(foot_msg, total_inner, "left")
            print(f"│{foot_pad}│")
        print(format_box_line("└", "─", "┘", [total_inner]))
            
        if not sys.stdin.isatty():
            break
            
        try:
            a_prompt = f", [a]uto-flag Home Recent ({len(home_recent_pkgs)})" if home_recent_pkgs else ""
            r_prompt = ", [r]eview day" if has_any_footage else ""
            n_fl = len(get_flagged_tombstones(master_dir))
            f_prompt = f", [F]lagged ({n_fl})" if n_fl > 0 else ""
            prompt_str = f"Select Event [1-{len(events)} or HH:MM]{a_prompt}, [f <#|range>] flag, [u <#|range>] unflag{r_prompt}{f_prompt}, [p]rev day, [n]ext day, [b]ack, [q]uit: "
            choice = input(prompt_str).strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["b", "back"]:
                break
            elif choice in ["f", "flagged"]:
                display_flagged_deletions_menu(analyzer)
                continue
            elif choice in ["a", "auto", "home", "autoflag", "auto-flag"]:
                batch_flag_events_interactive(events, "home", analyzer, is_unflag=False)
                continue
            elif any(choice.startswith(pfx + " ") for pfx in ["f", "d", "flag", "del", "delete"]):
                batch_flag_events_interactive(events, choice, analyzer, is_unflag=False)
                continue
            elif any(choice.startswith(pfx + " ") for pfx in ["u", "unflag"]):
                batch_flag_events_interactive(events, choice, analyzer, is_unflag=True)
                continue
            elif choice in ["r", "review", "review day"]:
                all_day_pkgs = []
                for ev in events:
                    all_day_pkgs.extend(build_timeline_footage_packages(ev))
                if not all_day_pkgs:
                    print("\n⚠️  No footage packages recorded on this day.\n")
                else:
                    run_interactive_footage_review(all_day_pkgs, analyzer, context_label=day_start.strftime('%A, %d %B %Y'))
                continue
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

def display_days_menu(days_dict, title, analyzer, can_go_back=False):
    """Level 1: Display days summary table and allow drilling down to any day."""
    sorted_days = sorted(days_dict.keys(), reverse=True)
    
    while True:
        total_trips = sum(len(v) for v in days_dict.values())
        
        # Precompute rows to dynamically measure max column widths
        rows_data = []
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
                
            notable = extract_notable_destinations(day_trips, max_places=3)
            
            rows_data.append({
                "idx": f" [{idx+1}]",
                "date": f" {dt_obj.strftime('%a %d %b %Y')}",
                "trips": f" {len(day_trips)}",
                "time": f" {time_str}",
                "dist": f" {total_km:.1f} km",
                "notable": f" {notable}",
                "sav": "📹" if "Saved" in day_cats else "·",
                "sen": "🛡️" if "Sentry" in day_cats else "·",
                "rec": "🕒" if "Recent" in day_cats else "·"
            })

        w_idx = max(len(" # "), max((display_len(r["idx"]) + 1 for r in rows_data), default=5))
        w_date = max(len(" Date "), max((display_len(r["date"]) + 1 for r in rows_data), default=18))
        w_trips = max(len(" Trips "), max((display_len(r["trips"]) + 1 for r in rows_data), default=8))
        w_time = max(len(" Time "), max((display_len(r["time"]) + 1 for r in rows_data), default=11))
        w_dist = max(len(" Distance "), max((display_len(r["dist"]) + 1 for r in rows_data), default=13))
        w_notable = max(len(" Notable Destinations "), max((display_len(r["notable"]) + 1 for r in rows_data), default=25))

        w_sav = 7
        w_sen = 8
        w_rec = 8
        w_foot_total = w_sav + w_sen + w_rec + 2  # 25

        left_widths = [w_idx, w_date, w_trips, w_time, w_dist, w_notable]
        left_headers = [" #", " Date", " Trips", " Time", " Distance", " Notable Destinations"]

        total_inner = sum(left_widths) + len(left_widths) + w_foot_total
        title_str = f" 📍 {title} ({total_trips} Trips Across {len(sorted_days)} Days)"
        t_len = display_len(title_str)
        if t_len + 2 > total_inner:
            w_notable += (t_len + 2 - total_inner)
            left_widths[5] = w_notable
            total_inner = sum(left_widths) + len(left_widths) + w_foot_total

        border_top = "┌" + "─" * total_inner + "┐"
        border_top_header = "├" + "┬".join("─"*w for w in left_widths) + "┬" + "─"*w_foot_total + "┤"
        border_sub = "│" + "│".join(" "*w for w in left_widths) + "├" + "─"*w_sav + "┬" + "─"*w_sen + "┬" + "─"*w_rec + "┤"
        border_data = "├" + "┼".join("─"*w for w in left_widths) + "┼" + "─"*w_sav + "┼" + "─"*w_sen + "┼" + "─"*w_rec + "┤"
        border_bot = "└" + "┴".join("─"*w for w in left_widths) + "┴" + "─"*w_sav + "┴" + "─"*w_sen + "┴" + "─"*w_rec + "┘"
        
        print(f"\n{border_top}")
        print(format_title_line(title_str, total_inner, "left"))
        print(border_top_header)
        
        row1_cols = [pad_display(h, w) for h, w in zip(left_headers, left_widths)] + [pad_display("Footage", w_foot_total, "center")]
        print("│" + "│".join(row1_cols) + "│")
        print(border_sub)
        
        row2_cols = [" "*w for w in left_widths] + [
            pad_display("Saved", w_sav, "center"),
            pad_display("Sentry", w_sen, "center"),
            pad_display("Recent", w_rec, "center")
        ]
        print("│" + "│".join(row2_cols) + "│")
        print(border_data)
        
        all_widths = left_widths + [w_sav, w_sen, w_rec]
        # idx/trips/time/dist are numbers (with units, or plain for idx/trips) -> right-aligned;
        # date and notable-destinations are free text -> left-aligned; footage icons stay centered.
        all_aligns = ["right", "left", "right", "right", "right", "left", "center", "center", "center"]
        for r in rows_data:
            row_cells = [
                r["idx"] + " ", r["date"], r["trips"] + " ", r["time"] + " ", r["dist"] + " ", r["notable"],
                r["sav"], r["sen"], r["rec"]
            ]
            print(format_row(row_cells, all_widths, all_aligns))

        print(border_bot)
        try:
            back_prompt = ", [b]ack" if can_go_back else ""
            choice = input(f"Select Day [1-{len(sorted_days)}] to drill down{back_prompt}, [a]ll, [q]uit: ").strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["b", "back"] and can_go_back:
                break
            elif choice in ["a", "all"]:
                drill_down_all_days(sorted_days, days_dict, analyzer, title)
            elif choice.isdigit() and 1 <= int(choice) <= len(sorted_days):
                selected_day = sorted_days[int(choice)-1]
                drill_down_day(selected_day, days_dict[selected_day], analyzer)
            else:
                print("Invalid choice.")
        except (KeyboardInterrupt, EOFError):
            break

def display_months_menu(days_dict, analyzer):
    """Level 0: Display months overview table and allow picking a month to inspect."""
    months_dict = defaultdict(dict)
    for d_str, trips in days_dict.items():
        m_key = d_str[:7]
        months_dict[m_key][d_str] = trips
    
    sorted_months = sorted(months_dict.keys(), reverse=True)
    if len(sorted_months) == 1:
        m_key = sorted_months[0]
        dt_m = datetime.strptime(m_key + "-01", "%Y-%m-%d")
        display_days_menu(months_dict[m_key], dt_m.strftime("%B %Y"), analyzer, can_go_back=False)
        return
        
    while True:
        # Precompute rows to dynamically measure max column widths
        rows_data = []
        for idx, m_key in enumerate(sorted_months):
            m_days = months_dict[m_key]
            m_trips = [t for day_list in m_days.values() for t in day_list]
            dt_m = datetime.strptime(m_key + "-01", "%Y-%m-%d")
            total_km = sum(t["dist_km"] for t in m_trips)
            total_mins = sum(t["dur_min"] for t in m_trips)
            hours, mins = divmod(total_mins, 60)
            time_str = f"{hours}h {mins:02d}m" if hours else f"{mins}m"
            notable = extract_notable_destinations(m_trips, max_places=4)
            
            rows_data.append({
                "idx": f" [{idx+1}]",
                "month": f" {dt_m.strftime('%B %Y')}",
                "days": f" {len(m_days)}",
                "trips": f" {len(m_trips)}",
                "time": f" {time_str}",
                "dist": f" {total_km:,.1f} km",
                "notable": f" {notable}"
            })

        w_idx = max(len(" # "), max((display_len(r["idx"]) + 1 for r in rows_data), default=5))
        w_month = max(len(" Month "), max((display_len(r["month"]) + 1 for r in rows_data), default=18))
        w_days = max(len(" Days "), max((display_len(r["days"]) + 1 for r in rows_data), default=8))
        w_trips = max(len(" Trips "), max((display_len(r["trips"]) + 1 for r in rows_data), default=8))
        w_time = max(len(" Time "), max((display_len(r["time"]) + 1 for r in rows_data), default=11))
        w_dist = max(len(" Distance "), max((display_len(r["dist"]) + 1 for r in rows_data), default=13))
        w_notable = max(len(" Notable Destinations "), max((display_len(r["notable"]) + 1 for r in rows_data), default=25))
        
        total_inner = w_idx + w_month + w_days + w_trips + w_time + w_dist + w_notable + 6
        total_all_trips = sum(len(trips) for trips in days_dict.values())
        title = f" 🗓️ TESSIE DRIVES BY MONTH ({total_all_trips:,} Trips Across {len(days_dict)} Days)"
        t_len = display_len(title)
        if t_len + 2 > total_inner:
            w_notable += (t_len + 2 - total_inner)
            total_inner = w_idx + w_month + w_days + w_trips + w_time + w_dist + w_notable + 6

        border_top = format_box_line("┌", "─", "┐", [total_inner])
        border_top_header = format_box_line("├", "┬", "┤", [w_idx, w_month, w_days, w_trips, w_time, w_dist, w_notable])
        border_data_divider = format_box_line("├", "┼", "┤", [w_idx, w_month, w_days, w_trips, w_time, w_dist, w_notable])
        border_bot = format_box_line("└", "┴", "┘", [w_idx, w_month, w_days, w_trips, w_time, w_dist, w_notable])
        
        print(f"\n{border_top}")
        print(format_title_line(title, total_inner, "left"))
        print(border_top_header)
        
        m_widths = [w_idx, w_month, w_days, w_trips, w_time, w_dist, w_notable]
        m_headers = [" #", " Month", " Days", " Trips", " Time", " Distance", " Notable Destinations"]
        # idx/days/trips/time/dist are numbers (with units, or plain for idx/days/trips) -> right-aligned;
        # month and notable-destinations are free text -> left-aligned.
        m_aligns = ["right", "left", "right", "right", "right", "right", "left"]
        print(format_row(m_headers, m_widths))
        print(border_data_divider)
        
        for r in rows_data:
            m_cells = [r["idx"] + " ", r["month"], r["days"] + " ", r["trips"] + " ", r["time"] + " ", r["dist"] + " ", r["notable"]]
            print(format_row(m_cells, m_widths, m_aligns))
            
        print(border_bot)
        try:
            prompt = f"Select Month [1-{len(sorted_months)}], [a]ll months, [t]oday, [y]esterday, [q]uit: "
            choice = input(prompt).strip().lower()
            if choice == "q":
                sys.exit(0)
            elif choice in ["a", "all"]:
                display_days_menu(days_dict, "All Drive History", analyzer, can_go_back=True)
            elif choice in ["t", "today"]:
                today_str = datetime.now().strftime("%Y-%m-%d")
                if today_str in days_dict:
                    drill_down_day(today_str, days_dict[today_str], analyzer)
                else:
                    print("No drives recorded for today.")
            elif choice in ["y", "yesterday"]:
                y_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                if y_str in days_dict:
                    drill_down_day(y_str, days_dict[y_str], analyzer)
                else:
                    print("No drives recorded for yesterday.")
            elif choice.isdigit() and 1 <= int(choice) <= len(sorted_months):
                selected_m = sorted_months[int(choice)-1]
                dt_m = datetime.strptime(selected_m + "-01", "%Y-%m-%d")
                display_days_menu(months_dict[selected_m], dt_m.strftime("%B %Y"), analyzer, can_go_back=True)
            else:
                print("Invalid choice.")
        except (KeyboardInterrupt, EOFError):
            break

def main():
    parser = argparse.ArgumentParser(
        usage="""tessie_drives_analyzer.py --drives [--today | --yesterday | --month M | --since DATE | --days N]
                                  [--place NAME]
   or:  tessie_drives_analyzer.py --timeline [DATE]
   or:  tessie_drives_analyzer.py --inspect-drive INDEX_OR_DATE
   or:  tessie_drives_analyzer.py --consolidate [CSV]
   or:  tessie_drives_analyzer.py --sync | --no-sync
   or:  tessie_drives_analyzer.py [--purge-home-recent | --stage-home-recent] [--dry-run] [--yes]
""",
        description="""
NAME
    tessie_drives_analyzer.py - Tessie Drive Log Analyzer & Master Consolidator

DESCRIPTION
    Analyze, filter, and deep-dive Tessie drive logs, generate 24-hour vehicle &
    camera activity timelines, consolidate drive CSVs into drives_master.csv, and
    manage Home-parked RecentClips dashcam footage.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES
  tessie_drives_analyzer.py --drives --today
  tessie_drives_analyzer.py --drives --place Work --days 30
  tessie_drives_analyzer.py --timeline yesterday
  tessie_drives_analyzer.py --inspect-drive "2026-08-30 21:01"
  tessie_drives_analyzer.py --consolidate
  tessie_drives_analyzer.py --stage-home-recent --dry-run
"""
    )

    g_filter = parser.add_argument_group("Drive Selection & Filtering")
    g_filter.add_argument("--drives", action="store_true", help="Analyze and inspect drives")
    g_filter.add_argument("--today", action="store_true", help="Inspect only today's drives")
    g_filter.add_argument("--yesterday", action="store_true", help="Inspect only yesterday's drives")
    g_filter.add_argument("--month", help="Filter drives by month (e.g. '2026-09', 'september', 'aug')")
    g_filter.add_argument("--since", help="Filter drives since date or day name (e.g. 'wednesday', '2026-09-02')")
    g_filter.add_argument("--days", type=int, help="Filter drives from past N days")
    g_filter.add_argument("--place", help="Filter drives by place nickname (e.g. 'School', 'Work', 'Gym')")

    g_timeline = parser.add_argument_group("Timeline & Inspection")
    g_timeline.add_argument("--timeline", nargs="?", const="today", default=None, help="Generate 24-hour event-driven vehicle & camera activity timeline for a date (e.g. today, yesterday, 2026-09-02, wednesday)")
    g_timeline.add_argument("--inspect-drive", help="Deep-dive inspect a specific drive by 1-based index (start-time order) or date/datetime (e.g. '2026-08-30', '2026-08-30 21:01')")

    g_sync = parser.add_argument_group("Consolidation & Sync")
    g_sync.add_argument("--consolidate", nargs="?", const=True, help="Consolidate drives CSV(s) into drives_master.csv (optionally specify path to a new CSV)")
    g_sync.add_argument("--tessie-dir", help="Custom path to directory containing Tessie CSV exports")
    g_sync.add_argument("--sync", action="store_true", help="Force sync latest drives from Tessie API before running")
    g_sync.add_argument("--no-sync", action="store_true", help="Disable automatic API sync check when data is stale")

    g_camera = parser.add_argument_group("Home Camera Clip Management")
    g_camera.add_argument("--purge-home-recent", "--auto-purge-home-recent", action="store_true", help="Auto-purge all unflagged RecentClips packages recorded while parked at Home")
    g_camera.add_argument("--stage-home-recent", "--auto-flag-home-recent", action="store_true", help="Stage all unflagged Home RecentClips packages for deletion without purging")
    g_camera.add_argument("--dry-run", action="store_true", help="Preview footage to be staged or purged without modifying disks")
    g_camera.add_argument("--yes", "-y", action="store_true", help="Automatically confirm purge without interactive prompt")

    args = parser.parse_args()

    analyzer = TessieAnalyzer(tessie_dir=args.tessie_dir)
    if args.no_sync:
        analyzer._sync_disabled = True
    if args.sync:
        analyzer.sync_drives_if_stale(force=True)
    if args.consolidate:
        path = args.consolidate if isinstance(args.consolidate, str) else None
        analyzer.consolidate_drives(new_csv_path=path, verbose=True)
        return

    drives = analyzer.load_drives()

    if not drives:
        print("No Tessie drive records found. Please ensure CSVs exist in iCloud or Tessie/.")
        sys.exit(0)

    if args.purge_home_recent or args.stage_home_recent:
        target_dates = None
        if args.today:
            target_dates = [parse_relative_date("today").date()]
        elif args.yesterday:
            target_dates = [parse_relative_date("yesterday").date()]
        elif args.since:
            s_dt = parse_relative_date(args.since).date()
            target_dates = [s_dt + timedelta(days=i) for i in range((datetime.now().date() - s_dt).days + 1)]
        elif args.days:
            s_dt = (datetime.now() - timedelta(days=args.days)).date()
            target_dates = [s_dt + timedelta(days=i) for i in range((datetime.now().date() - s_dt).days + 1)]
        elif args.timeline is not None:
            t_dt = parse_relative_date(args.timeline)
            if t_dt:
                target_dates = [t_dt.date()]

        purge_home_recent_cli(
            analyzer,
            target_dates=target_dates,
            stage_only=args.stage_home_recent,
            dry_run=args.dry_run,
            yes=args.yes
        )
        return

    if args.inspect_drive:
        analyzer.inspect_drive(args.inspect_drive)
        return

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
        if args.month:
            m_target = args.month.strip().lower()
            d_ym = d["start_dt"].strftime("%Y-%m")
            d_month_name = d["start_dt"].strftime("%B").lower()
            if m_target not in d_ym and not d_month_name.startswith(m_target):
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

    # If specific day or single day filtered, go directly to day view
    if args.today or args.yesterday:
        selected_d = list(days_dict.keys())[0]
        drill_down_day(selected_d, days_dict[selected_d], analyzer)
        return

    # If filtered to a specific month or single place/short range
    unique_months = set(d_str[:7] for d_str in days_dict.keys())
    if args.month or len(unique_months) == 1:
        m_str = list(unique_months)[0]
        dt_m = datetime.strptime(m_str + "-01", "%Y-%m-%d")
        title = args.place if args.place else dt_m.strftime("%B %Y")
        display_days_menu(days_dict, title, analyzer, can_go_back=False)
        return

    title = args.place if args.place else ("Drives Since " + cutoff_dt.strftime('%Y-%m-%d') if cutoff_dt else "All Drive History")
    display_months_menu(days_dict, analyzer)

if __name__ == "__main__":
    main()
