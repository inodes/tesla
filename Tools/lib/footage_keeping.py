"""The older way of keeping footage: the per-drive catalog, deletion tombstones, making room on the Archive, copying and purging clips, home-parked purge. To be replaced by the camera libraries (sync_camera); until then it is used by the drive browser, DriveAnalyzer and drive_actions. Moved unchanged out of analyse_drives.py."""

from datetime import datetime
from collections import defaultdict
from lib.table_formatter import display_len
import fnmatch
from lib.table_formatter import format_box_line
from lib.table_formatter import format_row
from lib.sei_inspector import format_stream_banner
from lib.table_formatter import format_title_line
import glob
from lib.sei_inspector import inspect_mp4_clip
import json
import os
from lib.table_formatter import pad_display
import re
import shutil
import subprocess
import sys
import time
from datetime import timedelta
from lib.table_formatter import truncate_display
from lib.drives.helpers import clean_event_reason, haversine_distance_m

def find_mounted_tesla_volumes(subdir=None):
    """
    Dynamically discovers all mounted volumes matching TESLADRIVE* under
    /Volumes. If subdir is provided (e.g. 'TeslaCam'), returns existing
    subdirectories within those volumes.

    TESLADRIVE* volumes are reserved exclusively for dashcam/TeslaCam media
    (see AGENTS.md) - this is what locates real footage on the drive for
    the Saved/Sentry/Recent columns below. Tessie CSV/registry data is
    never read from or written to TESLADRIVE (that tooling runs from the
    repository and iCloud only, see sync_dashcam.sh) - callers must not pass
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

 # Pass 2: Capacity > 1600 GB (2TB Master Archive SSD), never a drive
 # carrying a JOWUA/USB identity marker.
    for d in candidates:
        if not d:
            continue
        root = get_drive_root(d)
        if not root or not os.path.isdir(root):
            continue
        if _has_secondary_marker(root):
            continue
        try:
            total_gb = shutil.disk_usage(root).total / (1024**3)
            if total_gb > 1600:
                tc_dir = os.path.join(root, "TeslaCam")
                return tc_dir if os.path.isdir(tc_dir) else root
        except OSError:
            pass

 # Pass 3: a --localsync archive folder on this computer (never a
 # removable /Volumes drive, never a drive carrying a JOWUA/USB marker).
    for d in candidates:
        if not d:
            continue
        root = get_drive_root(d)
        if not root or not os.path.isdir(root):
            continue
        if os.path.realpath(root).startswith("/Volumes/") or _has_secondary_marker(root):
            continue
        tc_dir = os.path.join(root, "TeslaCam")
        if os.path.isdir(tc_dir):
            return tc_dir

 # SAFETY (2026-09-30, user: "Nothing should be deleted (unless forced)
 # unless it's on archive"): there used to be two more passes here - a
 # name match on "/Volumes/TESLADRIVE" and a last-resort "first drive
 # given" fallback - so with the Archive unplugged the Tesla USB became
 # the "Master Archive". Reconcile then treated any Saved/Sentry folder
 # the car removed from the USB as a Finder deletion on the Archive and
 # scrubbed it from the JOWUA (possibly the only copy). No real Archive
 # connected now means NO master, and every tombstone/purge path below
 # refuses to delete anything.
    return None


def _has_secondary_marker(root):
    """True if this drive root carries a JOWUA or Tesla USB identity marker."""
    return any(os.path.isdir(os.path.join(root, m)) for m in
               ("JOWUA_1TB", "JOWUA", "TESLA_USB_128GB", "TESLA_USB_256GB", "TESLA_USB"))


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
    if not m_dir or not os.path.isdir(m_dir):
        return 0, 0, 0  # no real Archive connected - never delete from a secondary drive
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


# ---------------------------------------------------------------------------
# Archive make-room (2026-09-30). User: "need a solution for archive full -
# such as working through recent clips", choosing "oldest RecentClips, just
# enough": when a sync leg into the Archive won't fit, offer the OLDEST
# Archive RecentClips needed to cover the shortfall. Saved/Sentry footage is
# never touched, and neither is any RecentClip within
# ARCHIVE_ROOM_EVENT_GUARD of a Saved/Sentry event. Deleted minutes are
# tombstoned so rsync never copies them back from the JOWUA/USB (which get
# the normal "pending cleanup" scrub prompt later).
# ---------------------------------------------------------------------------
ARCHIVE_ROOM_EVENT_GUARD = timedelta(minutes=10)


def _parse_catalog_dt(value):
    if not value:
        return None
    v = str(value).replace("T", "_")[:19]
    try:
        return datetime.strptime(v, "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        try:
            return datetime.strptime(str(value)[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None


def plan_archive_recent_room(master_dir, bytes_needed):
    """Oldest-first Archive RecentClips minutes whose deletion frees at least
    bytes_needed. Returns (plan, planned_bytes, skipped_near_events) where plan
    is a list of (minute_dt, [full paths], bytes)."""
    if not master_dir or not os.path.isdir(master_dir) or bytes_needed <= 0:
        return [], 0, 0
    cat = scan_or_update_drive_catalog(master_dir, master_dir)
    pkgs = cat.get("packages", {})
    tombs = load_tombstones(master_dir)
    lookup = get_tombstone_lookup_sets(tombs, include_flagged=True)

    guards = []
    for key, p in pkgs.items():
        if p.get("cat") in ("Saved", "Sentry"):
            s_dt = _parse_catalog_dt(p.get("start_dt"))
            e_dt = _parse_catalog_dt(p.get("end_dt")) or s_dt
            if s_dt:
                guards.append((s_dt - ARCHIVE_ROOM_EVENT_GUARD, e_dt + ARCHIVE_ROOM_EVENT_GUARD + timedelta(minutes=1)))

    minutes = defaultdict(list)
    rc_dir = os.path.join(master_dir, "RecentClips")
    for key, p in pkgs.items():
        if not key.startswith("RecentClips"):
            continue
        for fname in p.get("files", []):
            if not fname.endswith(".mp4") or fname.startswith("._"):
                continue
            dt = _parse_catalog_dt(fname[:19])
            if dt is None:
                continue
            if is_clip_tombstoned(f"RecentClips/{fname}", tombs, lookup_sets=lookup, include_flagged=True):
                continue
            minutes[dt].append(os.path.join(rc_dir, fname))

    plan, planned, skipped = [], 0, 0
    for dt in sorted(minutes):
        if any(g_s <= dt <= g_e for g_s, g_e in guards):
            skipped += 1
            continue
        size = 0
        paths = []
        for fp in minutes[dt]:
            try:
                size += os.path.getsize(fp)
                paths.append(fp)
            except OSError:
                continue  # catalog entry no longer on disk
        if not paths:
            continue
        plan.append((dt, paths, size))
        planned += size
        if planned >= bytes_needed:
            break
    return plan, planned, skipped


def make_room_on_archive(master_dir, bytes_needed, secondary_dirs=None, exclude_file=None):
    """Interactive: preview the plan, delete only after an explicit 'y'.
    Returns bytes actually freed (0 if declined or nothing possible)."""
    if not master_dir or not os.path.isdir(master_dir):
        print("    Master Archive not identified - nothing deleted.")
        return 0
    if not sys.stdin.isatty():
        print("    No terminal to confirm on - nothing deleted.")
        return 0
    plan, planned, skipped = plan_archive_recent_room(master_dir, bytes_needed)
    gb = lambda b: f"{b / 1024**3:.2f} GB"
    if not plan:
        print("    No Archive RecentClips available to remove (Saved/Sentry and clips near events are protected).")
        return 0
    first, last = plan[0][0], plan[-1][0]
    n_files = sum(len(p) for _, p, _ in plan)
    by_day = defaultdict(lambda: [0, 0])
    for dt, paths, size in plan:
        by_day[dt.date()][0] += 1
        by_day[dt.date()][1] += size
    print(f"\n    Oldest Archive RecentClips that would make room ({gb(planned)} of {gb(bytes_needed)} needed):")
    for day in sorted(by_day):
        mins, size = by_day[day]
        print(f"      {day}  {mins:4d} min  {gb(size):>10}")
    print(f"      = {len(plan)} minutes, {n_files} files, {first:%Y-%m-%d %H:%M} -> {last:%Y-%m-%d %H:%M}")
    if skipped:
        print(f"      ({skipped} older minutes kept because they are within "
              f"{int(ARCHIVE_ROOM_EVENT_GUARD.total_seconds() // 60)} min of a Saved/Sentry event)")
    if planned < bytes_needed:
        print(f"      Note: this is all that can be removed - still {gb(bytes_needed - planned)} short.")
    try:
        ans = input("    Permanently delete these from the Archive (and later from the JOWUA/USB)? [y/N]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        ans = ""
    if ans not in ("y", "yes"):
        print("    Nothing deleted.")
        return 0

    free_before = shutil.disk_usage(master_dir).free
    known = list(tombs_known_drives(master_dir, secondary_dirs))
    data = load_tombstones(master_dir)
    tdict = data.setdefault("tombstones", {})
    now_iso = datetime.now().isoformat()
    freed = 0
    removed_by_day = defaultdict(list)
    bytes_by_day = defaultdict(int)
    for dt, paths, _size in plan:
        for fp in paths:
            try:
                sz = os.path.getsize(fp)
                os.remove(fp)
                freed += sz
                bytes_by_day[dt.date()] += sz
                removed_by_day[dt.date()].append(os.path.basename(fp))
            except OSError as e:
                print(f"      could not delete {os.path.basename(fp)}: {e}")
    for day, files in removed_by_day.items():
        key = f"RecentClips/{day.isoformat()}_archive-room"
        entry = tdict.get(key) or {
            "status": "tombstone", "deleted_at": now_iso, "cat": "Recent",
            "reason": "Archive make-room (oldest RecentClips)", "folder": "RecentClips",
            "files": [], "est_bytes": 0, "pending_drives": known,
        }
        entry["files"] = sorted(set(entry.get("files", [])) | set(files))
        entry["files_count"] = len(entry["files"])
        # Real size, so the "Active Tombstones (N packages, N files, ~X)"
        # summary doesn't show ~0 MB (2026-09-30).
        entry["est_bytes"] = entry.get("est_bytes", 0) + bytes_by_day[day]
        tdict[key] = entry
    save_tombstones(data, master_dir)
    scan_or_update_drive_catalog(master_dir, master_dir, force_rescan=False)
    if exclude_file:
        export_tombstone_rsync_excludes(master_dir, exclude_file)
    n_removed = sum(len(v) for v in removed_by_day.values())
    # 2026-09-30: deleting ~248 GB once left the drive's free space unchanged
    # (the files were gone but the space wasn't released), the sync then
    # filled the drive and failed. Measure what the drive actually gained.
    gained = 0
    for _ in range(10):
        gained = shutil.disk_usage(master_dir).free - free_before
        if freed <= 0 or gained >= freed * 0.9:
            break
        time.sleep(1)
    if freed > 0 and gained < freed * 0.5:
        print(f"    ⚠ Deleted {gb(freed)} ({n_removed} files), but the drive's free space only went up by {gb(max(gained, 0))}.")
        print("      Something is still holding the deleted files, so their space isn't released yet -")
        print("      usually a program that still has them open (or, on APFS only, a Time Machine snapshot).")
        print(f"      On the Mac:  sudo lsof -a +L1 '{get_drive_root(master_dir)}'   (programs holding deleted files)")
        print("      Quitting that program, or ejecting and reconnecting the drive, releases the space.")
        return -freed  # deleted, but no usable space - callers must not continue into a full drive
    print(f"    ✔ Freed {gb(freed)} from the Archive ({n_removed} files); drive free space up {gb(gained)}.")
    return freed


def tombs_known_drives(master_dir, secondary_dirs=None):
    """Secondary drive ids that should get the pending-cleanup scrub."""
    data = load_tombstones(master_dir)
    known = list(data.get("known_drives", ["VOL_JOWUA", "VOL_TESLA_USB"]))
    for d in secondary_dirs or []:
        drv_id = get_drive_identifier(d, master_dir)
        if drv_id and drv_id != "MASTER" and drv_id not in known:
            known.append(drv_id)
    return known


def flag_footage_packages_for_deletion(packages, analyzer):
    """
    Stages packages for deletion in .TeslaCam_tombstones.json with status='flagged'.
    Files REMAIN physically on disk until explicitly purged via sync_dashcam.sh or [P]urge.
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
    if not master_dir or not os.path.isdir(master_dir):
        return 0, 0, 0  # no real Archive connected - never delete anything
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


def preserve_file_metadata(src_path, dst_path):
    """Preserves POSIX mode, modification & access times, and macOS birthtime (creation date)."""
    try:
        shutil.copystat(src_path, dst_path)
    except Exception:
        pass
    if sys.platform == "darwin":
        try:
            st = os.stat(src_path)
            ts = getattr(st, "st_birthtime", None) or st.st_mtime
            dt_obj = datetime.fromtimestamp(ts)
            ts_str = dt_obj.strftime("%m/%d/%Y %H:%M:%S")
            subprocess.run(["SetFile", "-d", ts_str, "-m", dt_obj.strftime("%m/%d/%Y %H:%M:%S"), dst_path],
                           capture_output=True, check=False)
        except Exception:
            pass


def copy_clips_locally(clips_or_files, analyzer, date_or_str=None, default_dest=None, ask_preference=True, prompt_prefix="📥 Copy clips locally for faster viewing?"):
    """
    Copies video clips (from clip dicts or file paths) to local disk (default: <landing_directory>/TeslaCam/<YYYY-MM-DD>/).
    Preserves all timestamps (creation birthtime & modified time) and companion event metadata (event.json & thumb.png).
    Prompts user for destination or viewing in place on drive.
    Returns: (copied_count, dest_dir)
    """
    if not clips_or_files:
        print("⚠️  No files provided to copy.")
        return 0, None

    # Collect distinct valid source file paths
    src_paths = []
    for item in clips_or_files:
        if isinstance(item, str):
            if os.path.isfile(item):
                src_paths.append(item)
        elif isinstance(item, dict):
            p = item.get("path")
            if p and os.path.isfile(p):
                src_paths.append(p)
            for f in item.get("files", []):
                if os.path.isfile(f):
                    src_paths.append(f)

    src_paths = sorted(list(set(src_paths)))
    if not src_paths:
        print("⚠️  No valid video files found to copy.")
        return 0, None

    # Determine default destination directory (<landing_directory>/TeslaCam/<YYYY-MM-DD>/)
    landing_base = getattr(analyzer, "landing_dir", None) or os.path.expanduser("~/Downloads")
    if not date_or_str and src_paths:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(src_paths[0]))
        date_or_str = m.group(1) if m else datetime.now().strftime("%Y-%m-%d")

    target_sub = date_or_str or datetime.now().strftime("%Y-%m-%d")
    if default_dest is None:
        if os.path.basename(landing_base).lower() == "teslacam":
            default_dest = os.path.join(landing_base, target_sub)
        else:
            default_dest = os.path.join(landing_base, "TeslaCam", target_sub)

    # Inspect video stream & SEI telemetry prior to copy
    primary_sample = src_paths[0] if src_paths else None
    stream_info = inspect_mp4_clip(primary_sample) if primary_sample else None
    stream_banner = format_stream_banner(stream_info) if stream_info else None

    first_folder = os.path.dirname(src_paths[0])

    if ask_preference:
        print(f"\n{prompt_prefix}")
        if stream_banner:
            print(f"   {stream_banner}")
        if stream_info and stream_info.get("is_encrypted"):
            print(f"   ⚠️  Stream is encrypted ({stream_info.get('encryption_scheme', 'CENC')}) — requires vehicle decryption tools to view.")
        print(f"   Default destination: {default_dest}")
        print("   [Enter] Copy to default")
        print("   [v]     View in place on drive")
        print("   Or enter a custom folder path")
        try:
            c_input = input("   > ").strip()
        except (KeyboardInterrupt, EOFError):
            c_input = "v"

        if c_input.lower() in ("v", "view", "n", "no", "place", "p"):
            if sys.platform == "darwin" and os.path.exists(first_folder):
                subprocess.run(["open", first_folder])
                print(f"📂 Opened in Finder: {first_folder}\n")
            return 0, first_folder

        if c_input.lower() in ("", "c", "copy", "y", "yes"):
            dest_dir = default_dest
        else:
            dest_dir = os.path.abspath(os.path.expanduser(c_input))
    else:
        dest_dir = default_dest

    os.makedirs(dest_dir, exist_ok=True)

    # Collect any companion event metadata files (event.json, thumb.png) from event folders
    files_to_copy = [(src, os.path.basename(src)) for src in src_paths]
    seen_event_dirs = set()
    for src in src_paths:
        src_dir = os.path.dirname(src)
        if src_dir not in seen_event_dirs:
            seen_event_dirs.add(src_dir)
            folder_name = os.path.basename(src_dir)
            for meta_name in ("event.json", "thumb.png"):
                meta_path = os.path.join(src_dir, meta_name)
                if os.path.isfile(meta_path):
                    dest_meta_name = f"{folder_name}_{meta_name}" if "Clips" in os.path.dirname(src_dir) else meta_name
                    files_to_copy.append((meta_path, dest_meta_name))

    copied_count = 0
    copied_bytes = 0
    total_files = len(files_to_copy)
    total_bytes = sum(os.path.getsize(f) for f, _ in files_to_copy if os.path.exists(f))
    start_time = time.time()
    bar_width = 26

    def _render_progress(cur_bytes, cur_file_idx, cur_fname=""):
        if not sys.stdout.isatty():
            return
        elapsed = time.time() - start_time
        speed = (cur_bytes / elapsed) if elapsed > 0.3 else 0
        pct = (cur_bytes / total_bytes) if total_bytes > 0 else 1.0
        filled = int(bar_width * pct)
        bar = "█" * filled + "░" * (bar_width - filled)

        speed_str = f"{speed / (1024 * 1024):.1f} MB/s" if speed >= 1024 * 1024 else f"{speed / 1024:.0f} KB/s"
        c_mb = cur_bytes / (1024 * 1024)
        t_mb = total_bytes / (1024 * 1024)
        c_sz_str = f"{c_mb/1024:.1f} GB" if t_mb >= 1024 else f"{c_mb:.0f} MB"
        t_sz_str = f"{t_mb/1024:.1f} GB" if t_mb >= 1024 else f"{t_mb:.0f} MB"

        fname_short = (cur_fname[:26] + "…") if len(cur_fname) > 27 else cur_fname
        line = f"\r ⏳ [{bar}] {pct*100:>3.0f}% ({cur_file_idx}/{total_files} files, {c_sz_str}/{t_sz_str}) [{speed_str}] {fname_short:<28}"
        sys.stdout.write(line)
        sys.stdout.flush()

    for idx, (src, dst_name) in enumerate(files_to_copy, 1):
        dst_file = os.path.join(dest_dir, dst_name)
        try:
            src_sz = os.path.getsize(src)
            if os.path.exists(dst_file) and os.path.getsize(dst_file) == src_sz:
                preserve_file_metadata(src, dst_file)
                copied_bytes += src_sz
                copied_count += 1
                _render_progress(copied_bytes, idx, dst_name)
            else:
                chunk_size = 2 * 1024 * 1024
                with open(src, "rb") as fsrc, open(dst_file, "wb") as fdst:
                    while True:
                        buf = fsrc.read(chunk_size)
                        if not buf:
                            break
                        fdst.write(buf)
                        copied_bytes += len(buf)
                        _render_progress(copied_bytes, idx, dst_name)
                preserve_file_metadata(src, dst_file)
                copied_count += 1
        except Exception as e:
            if sys.stdout.isatty():
                sys.stdout.write("\n")
            print(f"   ⚠️ Error copying {dst_name}: {e}")

    if sys.stdout.isatty():
        sys.stdout.write("\r" + " " * 95 + "\r")
        sys.stdout.flush()

    mb_done = copied_bytes / (1024 * 1024)
    done_str = f"{mb_done/1024:.1f} GB" if mb_done >= 1024 else f"{mb_done:.0f} MB"
    print(f"\n✔ Successfully copied {copied_count} clip & metadata file(s) ({done_str}) to:\n   {dest_dir}\n")
    if sys.platform == "darwin" and os.path.exists(dest_dir):
        subprocess.run(["open", dest_dir])
        print(f"📂 Opened in Finder: {dest_dir}\n")

    return copied_count, dest_dir


def open_footage_package(pkg_or_items, analyzer, date_or_str=None, chosen_file=None, reveal_clip=True, ask_preference=True, force_copy=False):
    """
    Unified entry point for opening, revealing, and previewing TeslaCam footage.
    - For RecentClips or when force_copy=True: Prompts/copies to organized local folder (<landing_directory>/TeslaCam/<YYYY-MM-DD>/)
      preserving all file permissions and timestamps before revealing or opening.
    - For SavedClips / SentryClips: Opens the self-contained event directory directly in macOS Finder
      or reveals the primary clip.
    Returns: (dest_dir, primary_clip_path)
    """
    if not pkg_or_items:
        print("⚠️  No footage provided to open.")
        return None, None

    files = []
    cat = None
    folder = None

    if isinstance(pkg_or_items, dict) and "files" in pkg_or_items:
        pkg = pkg_or_items
        files = list(pkg.get("files", []))
        cat = pkg.get("cat")
        folder = pkg.get("folder")
        if not date_or_str:
            date_val = pkg.get("date_str")
            if not date_val and pkg.get("start_dt"):
                date_val = pkg["start_dt"].strftime("%Y-%m-%d")
            date_or_str = date_val
    elif isinstance(pkg_or_items, list):
        if len(pkg_or_items) == 1 and isinstance(pkg_or_items[0], dict) and "files" in pkg_or_items[0]:
            return open_footage_package(pkg_or_items[0], analyzer, date_or_str=date_or_str, chosen_file=chosen_file, reveal_clip=reveal_clip, ask_preference=ask_preference, force_copy=force_copy)
        for it in pkg_or_items:
            if isinstance(it, str):
                files.append(it)
                if not cat:
                    if "RecentClips" in it:
                        cat = "Recent"
                    elif "SentryClips" in it:
                        cat = "Sentry"
                    elif "SavedClips" in it:
                        cat = "Saved"
            elif isinstance(it, dict):
                p = it.get("path")
                if p:
                    files.append(p)
                for f in it.get("files", []):
                    files.append(f)
                if not cat:
                    cat = it.get("cat")
                if not folder:
                    folder = it.get("folder")
                if not date_or_str and it.get("dt"):
                    date_or_str = it["dt"].strftime("%Y-%m-%d")
    else:
        return None, None

    files = [f for f in files if os.path.isfile(f)]
    if not files and folder and os.path.exists(folder):
        if sys.platform == "darwin":
            subprocess.run(["open", folder])
            print(f"📂 Opened in Finder: {folder}\n")
        return folder, None

    if not files:
        print("⚠️  No valid video files found in package.")
        return None, None

    is_recent = (cat == "Recent") or any("RecentClips" in f for f in files)
    should_copy = force_copy or is_recent

    if should_copy:
        copied_cnt, dest_dir = copy_clips_locally(files, analyzer, date_or_str=date_or_str, ask_preference=ask_preference)
        target_f = chosen_file or (get_primary_preview_clip({"files": files}) if reveal_clip else None)
        local_target = None
        if target_f and dest_dir and os.path.exists(dest_dir):
            candidate = os.path.join(dest_dir, os.path.basename(target_f))
            if os.path.exists(candidate):
                local_target = candidate
            elif os.path.exists(target_f):
                local_target = target_f

        if local_target and reveal_clip:
            preview_footage_file(local_target, method="quicklook")
        return dest_dir, local_target
    else:
        target_f = chosen_file or (get_primary_preview_clip({"files": files}) if reveal_clip else None)
        target_folder = folder or (os.path.dirname(files[0]) if files else None)
        if reveal_clip and target_f and os.path.exists(target_f):
            preview_footage_file(target_f, method="quicklook")
            return os.path.dirname(target_f), target_f
        elif target_folder and os.path.exists(target_folder):
            if sys.platform == "darwin":
                subprocess.run(["open", target_folder])
            print(f"📂 Opened in Finder: {target_folder}\n")
            return target_folder, target_f
        elif target_f and os.path.exists(target_f):
            preview_footage_file(target_f, method="quicklook")
            return os.path.dirname(target_f), target_f
        else:
            print("⚠️ Folder not accessible.\n")
            return None, None


def _minute_bucket(v):
    """REQ-179: identity key for a drive's start/end time. UTC epochs are
    compared to the MINUTE, because older master rows carry epochs derived
    from Tessie's minute-resolution CSV text while rows synced since REQ-179
    carry the exact second - the same drive must not be added twice."""
    v = str(v or "").strip()
    return str(int(v) // 60) if v.isdigit() else v


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


# ---------------------------------------------------------------------------
# Home + Recent deletion safety (2026-09-30). User: "A drive sync should
# always be checked prior - so it needs to know literally Home + Recent -
# before deleting" and "Change radius to be sure".
#
# The old check trusted the day timeline: it extended wherever the last
# drive in drives_master.csv ended right up to "Now", only refreshed Tessie
# when a day had NO drives, and decided "Home" by substring-matching place
# keywords (one of which is "Hay Street") against the location text. If a
# later drive hadn't reached drives_master.csv yet, its RecentClips were
# "parked at Home" and deleted from every drive, Archive included.
#
# Now a RecentClips clip is only eligible when ALL of these hold:
#   1. Tessie drives were re-synced successfully in this run (else: nothing).
#   2. It sits in a parked gap between two real drives where the earlier
#      drive ENDED and the next drive STARTED inside the Home GPS radius
#      (places.json type=home, radius_m) - no keyword/name matching - and
#      the odometer didn't move across the gap (no missing drive).
#   3. For the gap after the newest drive (no next drive yet), only up to
#      the moment of this run's sync minus HOME_PURGE_OPEN_END_GRACE, so a
#      drive Tessie hasn't finalised yet can't be mistaken for parking.
#   4. The whole ~1-minute clip is inside that gap, with
#      HOME_PURGE_EDGE_MARGIN kept clear at both ends.
# ---------------------------------------------------------------------------
HOME_PURGE_EDGE_MARGIN = timedelta(minutes=2)


HOME_PURGE_OPEN_END_GRACE = timedelta(minutes=15)


HOME_PURGE_ODOMETER_TOL_KM = 0.5


HOME_PURGE_CLIP_SECONDS = 60


def _home_geofences(analyzer):
    """(lat, lon, radius_m) for every place marked type=home."""
    fences = []
    for _name, info in (getattr(analyzer, "places", None) or {}).items():
        if not isinstance(info, dict) or info.get("type") != "home":
            continue
        try:
            lat = float(info.get("lat"))
            lon = float(info.get("lon"))
            radius = float(info.get("radius_m") or 100)
        except (TypeError, ValueError):
            continue
        fences.append((lat, lon, radius))
    return fences


def _point_at_home(lat, lon, fences):
    if lat is None or lon is None or not fences:
        return False
    return any(haversine_distance_m(lat, lon, f_lat, f_lon) <= radius for f_lat, f_lon, radius in fences)


def _drive_odometer(drive, column):
    try:
        v = (drive.get("raw") or {}).get(column)
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def refresh_drives_for_home_purge(analyzer):
    """Pull the latest drives from Tessie before any Home + Recent deletion.
    Returns the (local) time the sync started, or None if it could not be
    confirmed - callers must then delete nothing."""
    cached = getattr(analyzer, "_home_purge_synced_at", None)
    if cached is not None:
        return cached
    sync_script = os.path.join(analyzer.script_dir, "sync_api_tessie.py")
    if not os.path.isfile(sync_script):
        print("⚠️  Home + Recent: can't confirm the latest drives (sync_api_tessie.py missing) - nothing will be deleted.")
        return None
    print(">>> Checking Tessie for the latest drives before any Home + Recent deletion...")
    started = datetime.now()
    try:
        res = subprocess.run([sys.executable, sync_script, "--drives", "--yes"],
                             capture_output=True, text=True, timeout=180)
    except Exception as e:
        print(f"⚠️  Home + Recent: drive sync failed ({e}) - nothing will be deleted.")
        return None
    if res.returncode != 0:
        last = [ln for ln in (res.stderr or res.stdout or "").strip().splitlines() if ln.strip()][-1:]
        detail = f" - {last[0].strip()}" if last else ""
        print(f"⚠️  Home + Recent: drive sync exited with code {res.returncode}{detail} - nothing will be deleted.")
        return None
    analyzer.load_drives(check_stale=False)
    analyzer._synced_this_session = True
    analyzer._home_purge_synced_at = started
    return started


def verified_home_parked_windows(analyzer, synced_at):
    """[(start, end)] local-time windows where the car is proven parked at Home."""
    fences = _home_geofences(analyzer)
    if not fences:
        print("⚠️  Home + Recent: no place with type=home and GPS coordinates in places.json - nothing will be deleted.")
        return []
    drives = sorted((d for d in (analyzer.drives or []) if d.get("start_dt") and d.get("end_dt")),
                    key=lambda d: d["start_dt"])
    windows = []
    for i, a in enumerate(drives):
        if not _point_at_home(a.get("end_lat"), a.get("end_lon"), fences):
            continue
        start = a["end_dt"] + HOME_PURGE_EDGE_MARGIN
        if i + 1 < len(drives):
            b = drives[i + 1]
            if not _point_at_home(b.get("start_lat"), b.get("start_lon"), fences):
                continue  # next known drive starts elsewhere -> a drive is missing in between
            o_a = _drive_odometer(a, "Ending Odometer (km)")
            o_b = _drive_odometer(b, "Starting Odometer (km)")
            if o_a is not None and o_b is not None and abs(o_b - o_a) > HOME_PURGE_ODOMETER_TOL_KM:
                continue  # the car moved between these drives
            end = b["start_dt"] - HOME_PURGE_EDGE_MARGIN
        else:
            if synced_at is None:
                continue
            end = synced_at - HOME_PURGE_OPEN_END_GRACE
        if end > start:
            windows.append((start, end))
    return windows


def _clip_in_home_windows(clip_dt, windows):
    clip_end = clip_dt + timedelta(seconds=HOME_PURGE_CLIP_SECONDS)
    return any(w_start <= clip_dt and clip_end <= w_end for w_start, w_end in windows)


def find_home_recent_footage_packages(analyzer, target_dates=None):
    """Finds unflagged RecentClips packages recorded while the car was PROVEN
    parked at Home (see the safety notes above). Returns [] - so nothing is
    deleted - whenever that can't be proven."""
    synced_at = refresh_drives_for_home_purge(analyzer)
    if synced_at is None:
        return []
    windows = verified_home_parked_windows(analyzer, synced_at)
    if not windows:
        return []

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
            if ev.get("type") != "parked":
                continue
            home_clips = [c for c in ev.get("clips", [])
                          if c.get("cat") == "Recent" and _clip_in_home_windows(c["dt"], windows)]
            if not home_clips:
                continue
            ev_home = dict(ev)
            ev_home["clips"] = home_clips
            for p in build_timeline_footage_packages(ev_home):
                if p.get("cat") == "Recent" and not is_package_flagged(p, tombstones_data=tombstones_data, master_dir=master_dir, lookup_sets=lookup_sets):
                    home_recent_pkgs.append(p)

    return home_recent_pkgs


def purge_home_recent_cli(analyzer, target_dates=None, stage_only=False, dry_run=False, yes=False):
    """CLI flow to stage or purge all Home RecentClips footage packages."""
    if not resolve_master_teslacam_dir(analyzer.teslacam_dirs):
        print("\nℹ️  Master Archive not connected - Home + Recent footage is never deleted without it.\n")
        return 0, 0
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
        print("   Files remain on disk until explicitly purged via 'sync_dashcam.sh' or [P]urge.\n")
        return n_flag, b_flag

    print("\n🗑️  Executing multi-drive purge of flagged Home Recent packages...")
    n_del, b_del, n_tombs = purge_flagged_tombstones_across_drives(analyzer.teslacam_dirs, analyzer.repo_root)
    analyzer._indexed = False
    f_mb = b_del / (1024 * 1024)
    f_sz = f"{f_mb/1024:.1f} GB" if f_mb >= 1024 else f"{f_mb:.0f} MB"
    print(f"\n ✔ Successfully purged {n_del} file(s) across connected drives ({f_sz} freed).\n")
    return n_del, b_del
