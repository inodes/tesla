"""DriveAnalyzer: loads, consolidates and matches Tessie drives with the dashcam deep-dive files, computes segments and rolling efficiency, and fetches deep-dive telemetry. Moved unchanged out of analyse_drives.py."""


import lib.drive_paths as _drive_paths
from lib.timezones import legacy_get as _legacy_get
import csv
from datetime import datetime
from collections import defaultdict
from lib.fuel_common import format_drive_fuel_comparison
import json
from lib.archive_naming import next_archive_path
import os
from lib.table_formatter import pad_display
import shutil
import subprocess
import sys
import lib.tessie_api_common as tessie_api_common
import time
from datetime import timedelta
from lib.analysis_deepdive_charts import try_open_in_browser
import lib.timezones as tztools
from lib.analysis_deepdive_charts import write_interactive_chart
from lib.footage_keeping import _minute_bucket, find_mounted_tesla_volumes, get_tombstone_lookup_sets, is_clip_tombstoned, load_tombstones, resolve_master_teslacam_dir, scan_or_update_drive_catalog
from lib.drives.helpers import compute_adaptive_rolling_window, format_drive_efficiency, format_duration_short, format_footage_tag, haversine_distance_m, normalize_row_timezone, parse_relative_date, shorten_display_path

class DriveAnalyzer:
    def __init__(self, tessie_dir=None, teslacam_dirs=None):
        self.script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))      # the Tools folder (this file is in lib/drives/)
        parent_dir = os.path.dirname(self.script_dir)
        self.icloud_dir = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla")
        self.repo_root = parent_dir
        self.landing_dir = os.path.expanduser("~/Downloads")
        self._synced_this_session = False
        self._sync_disabled = False

 # Read config.json directly (same file/convention as
 # ChargingAnalyzer and tessie_api_common.load_config()) just
 # for its "tessie_directory" setting - see self.tessie_data_home
 # below for why.
        _cfg_path = os.path.join(parent_dir, "config.json")  # TODO-034: root is primary now
        if not os.path.isfile(_cfg_path):
            _cfg_path = os.path.join(parent_dir, "resources", "config.json")  # legacy fallback
        cfg_tessie_dir = None
        cfg_landing_dir = None
        if os.path.isfile(_cfg_path):
            try:
                with open(_cfg_path, "r", encoding="utf-8") as _f:
                    _cdata = json.load(_f)
                    cfg_tessie_dir = _cdata.get("directories", {}).get("data")
                    cfg_landing_dir = _cdata.get("directories", {}).get("landing")
            except Exception:
                cfg_tessie_dir = None
        if cfg_tessie_dir:
            cfg_tessie_dir = os.path.expanduser(cfg_tessie_dir)
        if cfg_landing_dir:
            self.landing_dir = os.path.expanduser(cfg_landing_dir)

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
 # machine does - see 's identical lesson, which is
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
            self.tessie_data_home,  # BUG-133: the ONE authoritative config-resolved
 # data home (see above) was never actually in this list, so once
 # drives_master.csv/deepdive stopped being duplicated into the repo
 # folder (BUG-131 follow-up's de-duplication), load_drives()/
 # load_detailed_drives() - which both scan self.tessie_dirs, not
 # self.tessie_data_home directly - silently found 0 drives even
 # though the real data was sitting right there in tessie_data_home.
 # Listed first: it's the authoritative source, everything after it
 # is a legacy/registry-only fallback.
            os.path.join(parent_dir, "resources"),
            os.path.join(self.script_dir, "resources"),
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
        self._places_source_mtimes = {}
        for td in self.tessie_dirs:
            for fname, target_dict in [
                ("places.json", self.places),
                ("chargers/personal_chargers.json", self.places),
                ("chargers/tesla_superchargers.json", self.supercharger_places),
                ("superchargers.json", self.supercharger_places),
                ("chargers/tesla_chargers.json", self.charging_places),
                ("destination_chargers.json", self.charging_places),
                ("charging.json", self.charging_places),
                ("chargers/plugshare_chargers.json", self.charging_places),
            ]:
                try:
                    pf = os.path.join(td, fname)
                    if os.path.isfile(pf):
 # Record this file's mtime at load time (TODO-001) - checked by
 # _places_cache_is_stale() before every resolve_place() lookup so an
 # edit to places.json (or any of these sibling registries) made
 # mid-session, e.g. in another terminal/editor while a long-running
 # interactive menu loop is still open, is picked up on the next
 # lookup rather than silently ignored until the process restarts.
                        self._places_source_mtimes[pf] = os.path.getmtime(pf)
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

    def _places_cache_is_stale(self):
 # TODO-001: true if any source file's mtime has changed since the
 # last load_places() call, OR a previously-missing file now exists
 # (e.g. personal_chargers.json created mid-session), OR a
 # previously-present file has been deleted. Cheap (a handful of
 # os.path.getmtime() calls) so it is fine to run before every
 # resolve_place() lookup rather than on a timer.
        known = getattr(self, "_places_source_mtimes", None)
        if known is None:
            return True
        for td in self.tessie_dirs:
            for fname in (
                "places.json", "chargers/personal_chargers.json",
                "chargers/tesla_superchargers.json", "superchargers.json",
                "chargers/tesla_chargers.json", "destination_chargers.json",
                "charging.json", "chargers/plugshare_chargers.json",
            ):
                pf = os.path.join(td, fname)
                exists = os.path.isfile(pf)
                was_known = pf in known
                if exists != was_known:
                    return True
                if exists:
                    try:
                        if os.path.getmtime(pf) != known[pf]:
                            return True
                    except OSError:
                        return True
        return False

    def display_place(self, name):
        """Full, human-readable name for a resolved place. resolve_place()
        returns a charger's file-safe short_name ("Coffs_Harbour_NSW_Pacific_Hwy",
        "Taree_NSW") because that is what the stored drive rows and charger
        pooling key on; anything shown to the user maps it back to the
        registry's full name ("Coffs Harbour, NSW - Pacific Hwy"). Names that
        are not a registry short_name (Home, saved places, street names)
        pass through unchanged."""
        if not name or not isinstance(name, str) or "_" not in name:
            return name
        if self._places_cache_is_stale():
            self.load_places()
        cache = getattr(self, "_short_to_full", None)
        if cache is None or getattr(self, "_short_to_full_src", None) is not (self.places, self.charging_places, self.supercharger_places):
            cache = {}
            for reg in (self.supercharger_places, self.charging_places, self.places):
                for key, info in (reg or {}).items():
                    if not isinstance(info, dict):
                        continue
                    meta = info.get("tesla_metadata") or {}
                    short = meta.get("short_name")
                    if not short:
                        continue
                    full = meta.get("findus_location_name") or meta.get("findus_list_name") or str(key)
                    cache.setdefault(short, full)
            self._short_to_full = cache
            self._short_to_full_src = (self.places, self.charging_places, self.supercharger_places)
        return cache.get(name, name)

    def resolve_place(self, address, saved_loc="", lat=None, lon=None):
        if self._places_cache_is_stale():
            self.load_places()
        addr_clean = address.lower() if address else ""

 # 1. GPS Geofence matching on known places (Highest Accuracy! - checked
 # before Tessie's own Saved Location, which is manually entered in the
 # Tessie app and can be stale, generic, or missing charger-specific
 # detail our own registries already have - see AGENTS.md .)
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
 # field - fallback only, since it's user-editable in the Tessie app and
 # not guaranteed to reflect our own registries or stay in sync with them)
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
            loc = p_info.get("location", {})
            p_lat = p_info.get("lat") if p_info.get("lat") is not None else loc.get("lat")
            p_lon = p_info.get("lon") if p_info.get("lon") is not None else loc.get("lon")
            kws = p_info.get("keywords") or p_info.get("tesla_metadata", {}).get("keywords") or []
            target_name = p_info.get("tesla_metadata", {}).get("short_name") or p_info.get("location_name") or p_info.get("nickname") or place_name
            for kw in kws:
                if kw and isinstance(kw, str) and kw.strip() and len(kw.strip()) >= 3:
                    all_kws.append((len(kw.strip()), kw.strip().lower(), target_name, p_lat, p_lon))
        all_kws.sort(key=lambda x: x[0], reverse=True)
        for _, kw_clean, target_name, p_lat, p_lon in all_kws:
            if kw_clean in addr_clean:
 # If GPS is known, do not match keyword if the place is located > 1000m away
                if lat is not None and lon is not None and p_lat is not None and p_lon is not None:
                    dist_to_place = haversine_distance_m(lat, lon, p_lat, p_lon)
                    if dist_to_place > 1000:
                        continue
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
            print("\033[31mNo data directory configured - set \"directories.data\" in config.json "
                  "(see config.example.json). Refusing to write drives_master.csv into the repo folder.\033[0m")
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
                                start_time = r.get("Started At (UTC)") or _legacy_get(r, "Started At") or r.get("Started At") or r.get("Started")
                                end_time = r.get("Ended At (UTC)") or _legacy_get(r, "Ended At") or r.get("Ended At") or r.get("Ended")
                                dist = r.get("Distance (km)", "0")
                                s_loc = r.get("Starting Location", "")
                                if not start_time or not end_time:
                                    continue
                                key = (_minute_bucket(start_time), _minute_bucket(end_time), dist.strip(), s_loc.strip())
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
                                    ts_raw = _legacy_get(first_row, "Timestamp") or first_row.get("Timestamp") or ""
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
 # REQ-193: one folder per drive
                                        if dt_epoch is not None:
                                            target_dd_path = _drive_paths.csv_path(deepdive_dir, stamp, create=True)
                                        else:
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
                                    archive_dir = os.path.join(dest_dirs[0], "archive") if dest_dirs else os.path.join(self.repo_root, "resources", "drives", "archive")
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
                        start_time = r.get("Started At (UTC)") or _legacy_get(r, "Started At") or r.get("Started At") or r.get("Started")
                        end_time = r.get("Ended At (UTC)") or _legacy_get(r, "Ended At") or r.get("Ended At") or r.get("Ended")
                        dist = r.get("Distance (km)", "0")
                        s_loc = r.get("Starting Location", "")
                        if not start_time or not end_time:
                            continue
                        key = (_minute_bucket(start_time), _minute_bucket(end_time), dist.strip(), s_loc.strip())
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
            _legacy_get(x, "Started At") or x.get("Started At") or x.get("Started") or ""))

 # Safety net (/024/025 history): back up the master file's
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
        archive_dir = os.path.join(dest_dirs[0], "archive") if dest_dirs else os.path.join(self.repo_root, "resources", "drives", "archive")
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
        analyse_charging.py's parse_flexible_date() (a different
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
                raw_current, raw_voltage = [], []
                timestamps = []

                def to_float(v):
                    try:
                        return float(v) if v not in (None, "") else None
                    except Exception:
                        return None

                for row in reader:
                    ts_str = _legacy_get(row, "Timestamp") or row.get("Timestamp")
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
                    raw_current.append(to_float(row.get("Pack Current (A)")))
                    raw_voltage.append(to_float(row.get("Pack Voltage (V)")))

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
                        "pack_current_a": raw_current,
                        "pack_voltage_v": raw_voltage,
                    },
                }
        except Exception:
            return None

    def load_detailed_drives(self):
        """Mirrors load_detailed_charges() (analyse_charging.py) -
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
 # REQ-193: per-drive folders (deepdive/<stamp>/drive_deepdive_<stamp>.csv)
                if os.path.basename(os.path.normpath(s_dir)) == "deepdive":
                    for fpath in _drive_paths.list_csvs(s_dir):
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

    def _drive_start_collision_epochs(self):
        """TODO-003: the set of Started At (UTC) epoch values shared by
        MORE than one drives_master.csv row - a real, recurring pattern
        (~1.8% of drives) where a 0-distance "blip" drive (a shift-state
        rolling/parking micro-adjustment) is recorded starting at the
        exact same second as the real drive that follows it. Cached on
        first use since self.drives doesn't change within a run."""
        if getattr(self, "_collision_epochs_cache", None) is not None:
            return self._collision_epochs_cache
        if not self.drives:
            self.load_drives()
        counts = {}
        for d in self.drives:
            key = d.get("start_utc_epoch")
            if key is not None:
                counts[key] = counts.get(key, 0) + 1
        self._collision_epochs_cache = {k for k, v in counts.items() if v > 1}
        return self._collision_epochs_cache

    def match_detailed_drive(self, drive_row):
        """The detailed_drives entry (if any) whose telemetry window
        overlaps this drives_master.csv row's start/end - same optional,
        may-or-may-not-exist lookup pattern already used for charges'
        dt_rec (self.detailed_charges), just keyed on drive start/end
        instead of a fuzzy 15-minute window (a drive's own start/end is
        already precise, no need to guess).

        TODO-003 found a real data-integrity edge case this loose overlap
        test alone gets wrong: when a 0-distance "blip" drive and the real
        drive after it share the identical start timestamp (see
        _drive_start_collision_epochs()), BOTH rows' padded windows overlap
        the SAME short deep-dive record - the blip's own tiny telemetry -
        and the real (often much longer) drive would silently be shown
        that unrelated, mostly-stationary snippet as if it were its own.
        Verified against this repo's real drive history: 12 real drives
        (8-54 min, matched to a 2.6-13.3 min record covering as little as
        6% of their own declared duration) were affected. The fix is
        deliberately narrow - the duration-sanity check below only ever
        applies to a drive_row whose start IS a genuine collision, and
        only rejects a candidate covering under 40% of the drive's own
        declared duration - because plenty of ordinary, non-colliding
        drives legitimately have a deep-dive record shorter than their
        full duration (partial telemetry capture is a normal, accepted
        characteristic of this data source, confirmed across the wider
        drive history and deliberately left untouched here)."""
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
        is_collision = drive_row.get("start_utc_epoch") in self._drive_start_collision_epochs()
        drive_duration_min = (d_end - d_start).total_seconds() / 60.0
        # REQ-179: overlap test in UTC - deep-dive timestamps are on Sydney's
        # clock, a drive's start_dt/end_dt are local to where the car was
        # (and a drive can cross a timezone), so naive clocks mismatch.
        _ds, _de = drive_row.get("start_utc_epoch"), drive_row.get("end_utc_epoch")
        _use_utc = _ds is not None and _de is not None
        _pad_s = pad.total_seconds()
        # 2026-10-14: the +/-2 min padding alone let a drive that starts ~1-2 min after the PREVIOUS drive's
        # telemetry ended (a quick stop, then the drive back) count as "covered" by that earlier record, so its own
        # telemetry was never fetched. A padded match now also needs a REAL overlap, or a start within the padding of
        # the drive's own start; and the record with the largest real overlap wins instead of the first one found.
        best, best_ov = None, None
        for rec in self.detailed_drives:
            if _use_utc:
                _rs = tztools.epoch_for_naive_local(rec["start_datetime"])
                _re = tztools.epoch_for_naive_local(rec["end_datetime"])
                _overlap = _rs <= _de + _pad_s and _re >= _ds - _pad_s
                _real = min(_re, _de) - max(_rs, _ds)
                _near = abs(_rs - _ds) <= _pad_s
            else:
                _overlap = rec["start_datetime"] <= d_end + pad and rec["end_datetime"] >= d_start - pad
                _real = (min(rec["end_datetime"], d_end) - max(rec["start_datetime"], d_start)).total_seconds()
                _near = abs((rec["start_datetime"] - d_start).total_seconds()) <= _pad_s
            if _overlap and (_real >= 0 or _near):
                if is_collision and drive_duration_min > 5:
                    rec_duration_min = (rec["end_datetime"] - rec["start_datetime"]).total_seconds() / 60.0
                    if rec_duration_min < 0.4 * drive_duration_min:
                        continue
                if best is None or _real > best_ov:
                    best, best_ov = rec, _real
        return best

    def compute_drive_segments(self, raw_samples, grade_threshold_pct=2.0, smoothing_window_km=0.45):
        """Break a drive's raw telemetry into contiguous climbing/level/
        descending segments using rolling distance windows for both grade
        classification and segment efficiency, matching the smoothed
        profile chart (eliminating micro-sample discretization artifacts
        like spurious 50 kWh/100km on a descent)."""
        ts = raw_samples.get("timestamp") or []
        elev = raw_samples.get("elevation_m") or []
        odo = raw_samples.get("odometer_km") or []
        n = len(ts)
        if n < 2:
            return []

        dts = [self._deepdive_ts(t) if t else None for t in ts]
        rolling_eff = self.compute_rolling_efficiency(raw_samples)

        n_iv = n - 1
        iv_dist = [max(0.0, (odo[k + 1] - odo[k])) if (odo[k] is not None and odo[k + 1] is not None) else 0.0 for k in range(n_iv)]
        iv_elev = [(elev[k + 1] - elev[k]) if (elev[k] is not None and elev[k + 1] is not None) else 0.0 for k in range(n_iv)]

        intervals = []
        for i in range(n_iv):
            dt0, dt1 = dts[i], dts[i + 1]
            dist_k = iv_dist[i]
            delev_k = iv_elev[i]
            if not (dt0 and dt1 and dt1 > dt0):
                continue

            win = self._rolling_distance_window(iv_dist, i, smoothing_window_km)
            if win is not None and win[2] >= 0.15:
                lo, hi, w_dist = win
                w_delev = (elev[hi + 1] - elev[lo]) if (lo < len(elev) and hi + 1 < len(elev) and elev[lo] is not None and elev[hi + 1] is not None) else delev_k
                grade_pct = (w_delev / (w_dist * 1000.0)) * 100.0
            else:
                grade_pct = (delev_k / (dist_k * 1000.0) * 100.0) if dist_k > 0.01 else 0.0

            if dist_k <= 0.01:
                cls = "stationary"
            elif grade_pct >= grade_threshold_pct:
                cls = "climbing"
            elif grade_pct <= -grade_threshold_pct:
                cls = "descending"
            else:
                cls = "level"

            intervals.append({
                "start": dt0, "end": dt1, "distance_km": dist_k,
                "elevation_change_m": delev_k, "class": cls, "sample_idx": i
            })

 # Merge contiguous same-class intervals
        segments = []
        for iv in intervals:
            if segments and segments[-1]["class"] == iv["class"]:
                seg = segments[-1]
                seg["end"] = iv["end"]
                seg["distance_km"] += iv["distance_km"]
                seg["elevation_change_m"] += iv["elevation_change_m"]
                seg["sample_indices"].append(iv["sample_idx"])
            else:
                segments.append({
                    "class": iv["class"], "start": iv["start"], "end": iv["end"],
                    "distance_km": iv["distance_km"], "elevation_change_m": iv["elevation_change_m"],
                    "sample_indices": [iv["sample_idx"]]
                })

 # Calculate duration, speed, and smoothed rolling efficiency
        final_segments = []
        for seg in segments:
            seg["duration_mins"] = (seg["end"] - seg["start"]).total_seconds() / 60.0
            seg["avg_speed_kmh"] = (seg["distance_km"] / (seg["duration_mins"] / 60.0)
                                     if seg["duration_mins"] > 0 else None)
            s_effs = [rolling_eff[k] for k in seg["sample_indices"] if rolling_eff[k] is not None]
            if s_effs and seg["distance_km"] >= 0.10:
                seg["efficiency_kwh_per_100km"] = sum(s_effs) / len(s_effs)
            else:
                seg["efficiency_kwh_per_100km"] = None
            del seg["sample_indices"]

            if seg["class"] == "stationary" and seg["duration_mins"] < 0.2 and seg["distance_km"] <= 0.001:
                continue
            final_segments.append(seg)

        return final_segments

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
        cur = raw_samples.get("pack_current_a") or []
        volt = raw_samples.get("pack_voltage_v") or []
        pwr = raw_samples.get("power_kw") or []
        n = len(ts)
        if n < 2:
            return [None] * n

        dts = [self._deepdive_ts(t) if t else None for t in ts]

 # Real per-interval deltas - interval k sits between sample k and
 # k+1 (same source data compute_drive_segments() already derives).
        n_iv = n - 1
        iv_dist = [0.0] * n_iv
        iv_energy = [None] * n_iv
        for k in range(n_iv):
            o0 = odo[k] if k < len(odo) else None
            o1 = odo[k + 1] if k + 1 < len(odo) else None
            if o0 is not None and o1 is not None:
                iv_dist[k] = max(0.0, o1 - o0)

            dt0 = dts[k] if k < len(dts) else None
            dt1 = dts[k + 1] if k + 1 < len(dts) else None
            dt_hr = ((dt1 - dt0).total_seconds() / 3600.0) if (dt0 and dt1 and dt1 > dt0) else None

 # High-precision power integration:
 # If pack current (A) and pack voltage (V) are available:
 # Negative current = discharging (+power consumed).
 # Positive current = regen into pack (-power consumed / energy returned).
            if dt_hr and cur and volt and k + 1 < len(cur) and k + 1 < len(volt):
                c0, v0 = cur[k], volt[k]
                c1, v1 = cur[k + 1], volt[k + 1]
                if c0 is not None and v0 is not None and c1 is not None and v1 is not None:
                    p0 = - (c0 * v0) / 1000.0
                    p1 = - (c1 * v1) / 1000.0
                    iv_energy[k] = 0.5 * (p0 + p1) * dt_hr
            elif dt_hr and pwr and k + 1 < len(pwr):
                pw0, pw1 = pwr[k], pwr[k + 1]
                if pw0 is not None and pw1 is not None:
                    iv_energy[k] = 0.5 * (pw0 + pw1) * dt_hr

 # Fallback to Energy Remaining (kWh) delta
            if iv_energy[k] is None:
                r0 = erem[k] if k < len(erem) else None
                r1 = erem[k + 1] if k + 1 < len(erem) else None
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

        start_place = self.display_place(start_place)
        end_place = self.display_place(end_place)
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
        geocodes via places_manager.reverse_geocode() (Nominatim) - but only
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
            from places_manager import reverse_geocode, haversine_distance
        except ImportError:
            return None

        geo_cache = {}
        try:
            from lib.geo_common import load_geo_cache, save_geo_cache
            geo_cache = load_geo_cache(self.repo_root) or {}
        except Exception:
            pass

        # The sampling step grows with the trip so a long drive is still
        # labelled along its whole length (a fixed 500 m step with a tiny
        # lookup budget used up the budget in the first few km and every
        # later sample was nearest-filled from the start - "Home" for a
        # 300 km drive to Taree). Lookups are cached by rounded lat/lon, so
        # the one-off cost (~1 s each, Nominatim's limit) is paid once.
        _pts = [(a, b) for a, b in zip(lats, lons) if a is not None and b is not None]
        _trip_m = sum(haversine_distance(_pts[k][0], _pts[k][1], _pts[k + 1][0], _pts[k + 1][1])
                      for k in range(len(_pts) - 1))
        MAX_LIVE_LOOKUPS = 50
        MIN_MOVE_M = max(500.0, _trip_m / 45.0)
        live_lookups_done = 0
        _last_live = 0.0
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
                label = self.display_place(matched_place)
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
                        import time as _time
                        _wait = 1.1 - (_time.time() - _last_live)
                        if _wait > 0:
                            _time.sleep(_wait)
                        info = reverse_geocode(lat, lon)
                        _last_live = _time.time()
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
                from lib.geo_common import save_geo_cache
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
        """Short 'Road, Suburb'-style label from a places_manager.reverse_geocode()
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

    @staticmethod
    def _drive_stamp(target_drive, start_epoch):
        """The stamp of a drive's folder: local start minute plus the zone abbreviation (AGENTS.md TODO-033)."""
        start_dt = target_drive.get("start_dt") or datetime.fromtimestamp(start_epoch)
        stamp = start_dt.strftime("%Y-%m-%d_%H-%M")
        abbr = target_drive.get("start_tz_abbr")      # the zone the drive STARTED in (Queensland is AEST all year; Sydney is AEDT in summer)
        if not abbr:
            try:
                _, abbr, _ = tztools.localize(start_epoch)
            except Exception:
                abbr = None
        if abbr:
            stamp = f"{stamp}_{abbr}"
        return stamp

    def has_local_deepdive(self, target_drive):
        """True when the drive's folder already holds its telemetry file (found by the drive's start minute, as fetch_drive_deepdive_from_api does),
        even if match_detailed_drive() does not accept it (a partial capture, a collision). Such a drive needs no fetch."""
        start_epoch = target_drive.get("start_utc_epoch")
        if start_epoch is None or not self.drives_dirs:
            return False
        return bool(_drive_paths.find_csv(os.path.join(self.drives_dirs[0], "deepdive"), self._drive_stamp(target_drive, start_epoch)))

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
        # A drive is identified by the minute it started: when its folder already has telemetry there is nothing to fetch, and there is
        # never a second copy (no "_01" folders).
        have = _drive_paths.find_csv(os.path.join(self.drives_dirs[0], "deepdive"), self._drive_stamp(target_drive, start_epoch))
        if have:
            return have
        pad = 120
        params = dict(DEFAULT_UNIT_PARAMS)
        params.update({
            "from": int(start_epoch) - pad,
            "to": int(end_epoch) + pad,
            "interval": 1,
            "format": "csv",
        })

        if not getattr(self, "quiet_fetch", False):                 # the backfill shows one progress bar instead
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

        stamp = self._drive_stamp(target_drive, start_epoch)
        dest_path = _drive_paths.csv_path(dest_dir, stamp, create=True)       # one folder per drive, named by its start minute

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

        if not getattr(self, "quiet_fetch", False):
            print(f"   ✅ Fetched and saved: {os.path.basename(dest_path)} ({parsed.get('samples_count', 0)} samples)")
        return dest_path

    def get_drive_deepdive(self, target_drive):
        """Looks up deep-dive telemetry for one drive, computes its segment
        breakdown, and generates (but no longer auto-opens - user report,
        2026-09-15: opening a browser tab unconditionally on every single
        day/trip selection was surprising and unwanted, unlike
        analyse_charging.py's own charging curve, which only opens
        via an explicit "[o]pen chart in browser" action - callers now
        offer that same explicit action instead, see display_footage_details()
        and inspect_drive()) its elevation/efficiency profile chart (if one
        exists) - the shared data logic behind BOTH inspect_drive() (the
        standalone --inspect-drive flag) and display_footage_details()
        (AGENTS.md TODO-019: folded into the ordinary trip drill-down so
        this shows up automatically alongside footage, with no separate
        footage-vs-deep-dive prompt - mirrors how analyse_charging.py's
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

    def inspect_drive(self, target, vehicle_key=None):
        """Deep-dive inspect a specific drive by 1-based index (in
        start-time order, matching how drives are listed elsewhere) or by
        date/datetime string (see AGENTS.md REQ-036) - prints the
        climbing/level/descending segment breakdown (compute_drive_segments)
        when a matching drive_deepdive_*.csv exists for it, and opens the
        interactive elevation/power profile chart. Mirrors
        analyse_charging.py's inspect_session(), just keyed on a
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
        dist_km = float(target_drive.get("dist_km", 0))
        soc_str = f"   \033[1mBattery:\033[0m {target_drive.get('start_soc', '')}% ➔ {target_drive.get('end_soc', '')}%" if target_drive.get("start_soc") else ""
        eff_str = format_drive_efficiency(target_drive)
        eff_disp = f"   \033[1mEfficiency:\033[0m {eff_str}" if eff_str and eff_str != "-" else ""
        l1 = f"  \033[1mStarted:\033[0m {target_drive['start_dt'].strftime('%Y-%m-%d %H:%M:%S')}   \033[1mDuration:\033[0m {dur_mins:.0f} mins   \033[1mDistance:\033[0m {dist_km:.1f} km{soc_str}{eff_disp}"
        print(f"│{pad_display(l1, box_w - 2, truncate=True)}│")

        result = self.get_drive_deepdive(target_drive)
        dt_rec, segments, chart_path = result if result else (None, None, None)
        raw_samples = dt_rec.get("raw_samples") if dt_rec else None

 # Comparative ICE running costs on drive date
        try:
            kwh = float(target_drive.get("kwh_used")) if target_drive.get("kwh_used") else None
        except (ValueError, TypeError):
            kwh = None

        td_home = self.tessie_data_home or (self.tessie_dirs[0] if self.tessie_dirs else None)
        fuel_lines = format_drive_fuel_comparison(
            dist_km, target_drive["start_dt"], kwh_used=kwh,
            duration_mins=dur_mins, raw_samples=raw_samples,
            vehicle_key=vehicle_key, tessie_dir=td_home, box_width=box_w, indent="  "
        )
        if fuel_lines:
            print(f"├{'─' * (box_w - 2)}┤")
            for fl in fuel_lines:
                print(f"│{pad_display(fl, box_w - 2, truncate=True)}│")

        if not result:
            print(f"├{'─' * (box_w - 2)}┤")
            l2 = "  \033[38;5;214mNo deep-dive telemetry (drive_deepdive_*.csv) found for this drive.\033[0m"
            print(f"│{pad_display(l2, box_w - 2, truncate=True)}│")
            print(f"└{'─' * (box_w - 2)}┘")
            return

        print(f"├{'─' * (box_w - 2)}┤")
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
 # explicit action instead, matching analyse_charging.py's
 # own "[o]pen chart in browser" for the charging curve.
        if chart_path and sys.stdin.isatty():
            try:
                print("  Action:")
                print("    [o]pen chart in browser")
                print("    [Enter] to return")
                act = input("  > ").strip().lower()
                if act in ('o', 'open', 'chart', 'c'):
                    try_open_in_browser(chart_path)
            except (KeyboardInterrupt, EOFError):
                pass
        print()

    def sync_drives_if_stale(self, target_date=None, force=False):
        """
        Determines if local drives_master.csv is stale compared to requested date or current time,
        and automatically triggers sync_api_tessie.py --drives --yes to fetch fresh telemetry.
        """
        if getattr(self, "_sync_disabled", False):
            return False
        if getattr(self, "_synced_this_session", False) and not force:
            return False

 # 1. Check if Tessie API token is available
        cfg = tessie_api_common.load_config()
        token = os.environ.get("TESSIE_ACCESS_TOKEN") or cfg.get("tessie", {}).get("access_token")
        if not token:
            return False

        now = datetime.now()
        is_stale = False
        stale_reason = ""

        if not self.drives:
            self.load_drives(check_stale=False)

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

        sync_script = os.path.join(self.script_dir, "sync_api_tessie.py")
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
                        start_str = _legacy_get(r, "Started At") or r.get("Started At") or r.get("Started")
                        dt_start = datetime.strptime(start_str.strip()[:16], "%Y-%m-%d %H:%M")
                    if end_utc_epoch is not None:
                        local_dt, end_abbr, _ = tztools.localize(end_utc_epoch, end_tz)
                        dt_end = local_dt.replace(tzinfo=None)
                    else:
                        end_abbr = None
                        end_str = _legacy_get(r, "Ended At") or r.get("Ended At") or r.get("Ended")
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
 # BUG (found 2026-09-29, user report: a low-disk-space sync warning
 # led to investigating why the auto Home+Recent purge found nothing
 # to reclaim): scan_or_update_drive_catalog() resolves a drive-ROOT
 # path to its own "<root>/TeslaCam" subfolder internally for
 # scanning (its own comment: "If drive_path is the drive root,
 # resolve its TeslaCam subfolder if present") - but that resolution
 # is local to that function's own `drive_path` variable and was
 # never propagated back here. Every real caller of DriveAnalyzer
 # (sync_dashcam.sh's $VOL_TESLA_USB/$VOL_JOWUA/$VOL_2TB, all set to
 # the drive ROOT, e.g. /Volumes/TESLADRIVE 2, never .../TeslaCam)
 # passes drive roots, so `tc` below stayed the unresolved root while
 # `pkgs`' rel_k keys were computed against the CORRECTLY resolved
 # ".../TeslaCam" folder - `folder_path = os.path.join(tc, ...)` then
 # silently dropped the "TeslaCam" path segment for EVERY clip, on
 # EVERY drive, always. Confirmed live against the real connected
 # drives: 0 of 63,430 indexed clips had a path that actually existed
 # on disk before this fix - any feature that only reads counts/dates
 # from the cached catalog (e.g. --timeline's day summary) never
 # noticed, but anything needing the real file (purge/delete, preview,
 # size-on-disk, copy) would have silently failed to find every single
 # clip. Resolve once here, exactly mirroring
 # scan_or_update_drive_catalog()'s own check, so both that function's
 # scan and this function's path reconstruction agree.
            if os.path.isdir(os.path.join(tc, "TeslaCam")):
                tc = os.path.join(tc, "TeslaCam")
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

    def find_footage_range(self, start_dt, end_dt, prior_seconds=300, after_seconds=300):
        """Find all footage (Recent, Saved, Sentry) within a continuous time range,
        including a configurable window prior to start and after end (default: 5 mins prior, 5 mins after)."""
        self.index_footage()
        earliest = start_dt - timedelta(seconds=prior_seconds)
        latest = end_dt + timedelta(seconds=after_seconds)
        matches = []
        for item in self.footage_db:
            if earliest <= item["dt"] <= latest:
                matches.append(item)
        matches.sort(key=lambda x: (x["dt"], x["cam"]))
        return matches

    def get_trip_footage_summary(self, trip):
        """Returns formatted string tag and set of categories covering the full trip and prior/after windows."""
        s_dt = trip["start_dt"]
        e_dt = trip.get("end_dt") or s_dt
        all_clips = self.find_footage_range(s_dt, e_dt, prior_seconds=300, after_seconds=300)
        if not all_clips:
            return "No local footage", set()
        
        cats = set(c["cat"] for c in all_clips)
        return format_footage_tag(cats), cats

    def get_timeline_data(self, target_date):
        """Build event-driven timeline for target_date alternating seamlessly between parked and driving states."""
        if not self.drives:
            self.load_drives()
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
            sp = d['start_place']
            ep = d['end_place']
            if sp == ep:
                act_msg = f"🅿️ {sp} (Reposition {dist_str}, {dur_str})"
            else:
                act_msg = f"🚗 {sp} ➔ {ep} ({dist_str}, {dur_str})"

            events.append({
                "event_idx": event_idx,
                "type": "drive",
                "start_dt": d_start,
                "end_dt": d_end,
                "time_str": f"{s_str} - {e_str}",
                "location": d["end_place"],
                "activity": act_msg,
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
            
 # Attach footage to each event (filter for target day once, including 5m prior/after buffer for Recent context)
        day_clips = [c for c in self.footage_db if day_start - timedelta(minutes=10) <= c["dt"] < day_end + timedelta(minutes=10)]
        for ev in events:
            s_dt = ev["start_dt"]
            e_dt = ev["end_dt"]
            is_drive = (ev.get("type") == "drive")
            s_buf = s_dt - timedelta(minutes=5)
            e_buf = e_dt + timedelta(minutes=5)
            if is_drive:
                clips = [
                    c for c in day_clips
                    if s_buf <= c["dt"] <= e_buf
                ]
            else:
                clips = [
                    c for c in day_clips
                    if s_dt <= c["dt"] < e_dt
                ]
            ev["recent_mins"] = len(set(c["dt"] for c in clips if c["cat"] == "Recent"))
            ev["saved_mins"] = len(set(c["dt"] for c in clips if c["cat"] == "Saved"))
            ev["sentry_mins"] = len(set(c["dt"] for c in clips if c["cat"] == "Sentry"))
            ev["clips"] = clips

        return events, day_start, None


def export_all_deepdives(analyzer, dest_dir, dry_run=False):
    """
    TODO-024: bundles every real deep-dive artifact (driving AND charging)
    into one destination folder, so grabbing them doesn't mean reaching
    into the nested iCloud folder structure by hand. Plain flattened copy
    into <dest_dir>/drives/ and <dest_dir>/charges/ (not a zip) - the
    backlog note for this TODO explicitly deferred the zip-vs-plain-copy
    choice pending the user's confirmation, so this ships the option that
    needed no decision at all.

    Copies whatever real files already exist for each session - CSV/JSON
    always, HTML/PNG only where they already exist. As of 2026-09-25:
    driving deep-dives (drives/deepdive/) have NO PNG yet, only the
    dependency-free HTML canvas charts (deepdive_charts.py's own design
    choice, not an oversight - see its module docstring); charging
    deep-dives (charges/deepdive/) already include real PNGs from earlier
    session work. This function does not generate anything new - it only
    copies what is already on disk, so that gap is inherited here rather
    than solved by it.
    """
    home = analyzer.tessie_data_home
    if not home:
        print("No Tessie data home resolved - nothing to export.")
        return 0, 0

    sources = [
        (os.path.join(home, "drives", "deepdive"), os.path.join(dest_dir, "drives")),
        (os.path.join(home, "charges", "deepdive"), os.path.join(dest_dir, "charges")),
    ]
    total_files = 0
    total_bytes = 0
    by_ext = defaultdict(int)

    for src_dir, dst_dir in sources:
        if not os.path.isdir(src_dir):
            continue
        if not dry_run:
            os.makedirs(dst_dir, exist_ok=True)
        # REQ-193: drives/deepdive holds one folder per drive - walk into them (flat files too)
        rels = []
        for dp_, _dn, fns in os.walk(src_dir):
            for fn_ in fns:
                rels.append(os.path.relpath(os.path.join(dp_, fn_), src_dir))
        for rel_name in sorted(rels):
            fname = os.path.basename(rel_name)
            if fname.startswith("."):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in (".csv", ".json", ".html", ".png"):
                continue
            src_path = os.path.join(src_dir, rel_name)
            if not os.path.isfile(src_path):
                continue
            sz = os.path.getsize(src_path)
            if not dry_run:
                os.makedirs(os.path.dirname(os.path.join(dst_dir, rel_name)), exist_ok=True)
                shutil.copy2(src_path, os.path.join(dst_dir, rel_name))
            total_files += 1
            total_bytes += sz
            by_ext[ext] += 1

    return total_files, total_bytes, by_ext
