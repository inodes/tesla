#!/usr/bin/env python3
"""
tessie_api_common.py - Shared Tessie Developer API plumbing
==============================================================
Config/auth loading and the low-level HTTP call, factored out so
test_tessie_api.py and sync_api_tessie.py (and anything else that talks to
the API later) share one implementation instead of drifting apart.

See Tessie/TESSIE_API.md for endpoint/field notes, confirmed parameters,
and known limitations (this module is not a full API client - just enough
plumbing for this repo's own scripts).
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# REQ-067 (2026-09-15, AGENTS.local.md): this file moved from Tools/ into
# Tools/lib/ (mirroring the Tools/providers_chargers/ package), one directory
# deeper than when SCRIPT_DIR->REPO_ROOT was a single dirname() hop - now needs
# the same two-hop pattern providers_chargers/*.py already uses (_script_dir -> _tools_dir
# -> _repo_root) so REPO_ROOT still resolves to the actual repo root, not Tools/.
TOOLS_DIR = os.path.dirname(SCRIPT_DIR)
REPO_ROOT = os.path.dirname(TOOLS_DIR)

# TODO-034 (2026-09-15, AGENTS.local.md): config.json moved from Tessie/ to
# the repo root - the project outgrew being "a Tessie connector" a long time
# ago (AGL/PVOutput/dashcam/charger-discovery all live here too), so its one
# config file shouldn't sit inside the one subdirectory named after the
# original, narrower scope. ROOT_CONFIG_PATH is the new primary location;
# _LEGACY_TESSIE_CONFIG_PATH is checked only as a fallback (an older clone
# that hasn't moved its real file yet) - resolve_config_path() is the ONE
# place this order is decided, so every caller (this module's own
# load_config(), pvoutput_common.py, and anything else) stays in sync
# automatically instead of each hardcoding its own copy of "config.json".
ROOT_CONFIG_PATH = os.path.join(REPO_ROOT, "config.json")
_LEGACY_TESSIE_CONFIG_PATH = os.path.join(REPO_ROOT, "Tessie", "config.json")
API_BASE = "https://api.tessie.com"


def resolve_config_path():
    """The real config.json to read: the new root location if it exists,
    else the pre-TODO-034 Tessie/ location (so an old checkout/backup that
    hasn't moved its real file yet still works), else the root path (so a
    fresh install's error messages point at the CURRENT convention, not the
    retired one)."""
    if os.path.isfile(ROOT_CONFIG_PATH):
        return ROOT_CONFIG_PATH
    if os.path.isfile(_LEGACY_TESSIE_CONFIG_PATH):
        return _LEGACY_TESSIE_CONFIG_PATH
    return ROOT_CONFIG_PATH


# Kept for any external reference to the OLD name - now resolved dynamically
# rather than fixed at import time, so it reflects whichever location
# actually has the file.
CONFIG_PATH = resolve_config_path()

# Matches this repo's CSV convention (see Tessie/TESSIE_API.md) - the API
# itself defaults to mi/UTC.
DEFAULT_UNIT_PARAMS = {
    "distance_format": "km",
    "temperature_format": "c",
    "timezone": "Australia/Sydney",
}


# config.json restructure (2026-09-21, user request): directories/tessie/
# evnex/pvoutput/power got their own nested blocks, matching how abrp/osm
# were already grouped, instead of a long flat run of top-level keys. This
# maps each new nested path back to the old flat key name so load_config()
# can inject both - existing call sites that haven't been migrated yet
# (grep the repo for these old names before deleting this) keep working
# unchanged during the transition; new/updated call sites should read the
# nested path directly and this shim removed once nothing reads the old
# flat names any more.
_FLAT_KEY_COMPAT_MAP = {
    "landing_directory": ("directories", "landing"),
    "inbox_directory": ("directories", "inbox"),
    "tessie_directory": ("directories", "data"),
    "archive_directory": ("directories", "archive"),
    "invoices_directory": ("directories", "invoices"),
    "external_drive_path": ("directories", "external_drive"),
    "vin": ("tessie", "vin"),
    "tessie_access_token": ("tessie", "access_token"),
    "evnex_username": ("evnex", "username"),
    "evnex_password": ("evnex", "password"),
    "pvoutput_api_key": ("pvoutput", "api_key"),
    "pvoutput_system_id": ("pvoutput", "system_id"),
    "power_provider": ("power", "provider"),
    "power_usage_staleness_days": ("power", "usage_staleness_days"),
}

# BUG-131 TODO (2026-09-21): temporary instrumentation, delete this whole
# block (down to and including _CompatAccessDict) plus the
# `_CompatAccessDict(cfg, ...)` wrapping below, once we're confident every
# real call site was migrated off the old flat key names above. While it's
# in place, any code that still does cfg.get("evnex_username") etc. (instead
# of cfg.get("evnex", {}).get("username")) gets one line appended to
# config_compat_access.log (repo root, already *.log-gitignored) recording
# which flat key and which file/line read it. Watch that log stay empty
# across a normal week of real usage (syncs, places_manager, charger_find,
# etc.) before removing the shim in _FLAT_KEY_COMPAT_MAP entirely - a
# non-empty log after that means a real caller was missed by the mechanical
# refactor and still needs updating, not that the shim should stay forever.
_COMPAT_ACCESS_LOG_PATH = os.path.join(REPO_ROOT, "config_compat_access.log")


def _log_compat_access(flat_key):
    import inspect
    import time
    try:
        caller = "?"
        for fr in inspect.stack()[2:]:
            if fr.filename != __file__:
                caller = f"{fr.filename}:{fr.lineno}"
                break
        with open(_COMPAT_ACCESS_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} key={flat_key} caller={caller}\n")
    except Exception:
        pass  # instrumentation must never break a real config read


class _CompatAccessDict(dict):
    """dict subclass that logs a read of any still-live legacy flat config
    key (see _log_compat_access docstring above) without changing behavior
    at all - every read still returns exactly what a plain dict would."""

    def __init__(self, *args, _watch_keys=(), **kwargs):
        super().__init__(*args, **kwargs)
        self._watch_keys = frozenset(_watch_keys)

    def get(self, key, default=None):
        if key in self._watch_keys:
            _log_compat_access(key)
        return super().get(key, default)

    def __getitem__(self, key):
        if key in self._watch_keys:
            _log_compat_access(key)
        return super().__getitem__(key)


def load_config():
    cfg_path = resolve_config_path()
    cfg = {}
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            return {}
    if not isinstance(cfg, dict):
        return cfg
    for flat_key, (group, nested_key) in _FLAT_KEY_COMPAT_MAP.items():
        if flat_key in cfg:
            continue  # an old-style flat config.json still wins, unchanged
        group_val = cfg.get(group)
        if isinstance(group_val, dict) and nested_key in group_val:
            cfg[flat_key] = group_val[nested_key]
    return _CompatAccessDict(cfg, _watch_keys=_FLAT_KEY_COMPAT_MAP.keys())


def load_vehicle_spec():
    """This car's own real specs (curb weight, battery gross/usable
    capacity, drag coefficient, WLTP/GVG reference efficiency figures).
    Lives in the external tessie_directory home alongside every other
    real-data file (see vehicle_spec.example.json for the schema/
    placeholder values), resolved via resolve_tessie_data_home() - not
    hardcoded beside this script - per AGENTS.md's Zero-PII / "PII Never
    Lives in the Repo Working Tree At All" rule (REQ-034). An earlier
    version of this function hardcoded the path beside config.json in
    the repo tree; fixed 2026-09-14 during the real-data-in-tree audit.

    Kept here rather than as hardcoded constants in any one script (its
    first consumer was analysis_car_efficiency.py's grade-corrected
    efficiency estimate, which needs a real vehicle mass) specifically
    so any future tool that wants this car's own real spec - a
    different efficiency calculation, a range-vs-spec sanity check,
    whatever - reads the same one file instead of a second drifting
    copy of the same numbers. Returns {} if the file doesn't exist yet
    or fails to parse, exactly like load_config()."""
    path = os.path.join(resolve_tessie_data_home(), "vehicle_spec.json")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def resolve_tessie_data_home():
    """The ONE authoritative home for real Tessie/Evnex data - config.json's
    own "tessie_directory" (trusted unconditionally - see AGENTS.md
    REQ-028/029: real data does not belong inside the repo folder), falling
    back to the default iCloud path only if nothing is configured. Never
    falls back to REPO_ROOT. Shared by every script that reads or writes
    real data outside the repo (analyse_drives.py/
    analyse_charging.py have their own copy of this same logic
    baked into their classes; this is the standalone-script equivalent)."""
    cfg_dir = load_config().get("directories", {}).get("data")
    if cfg_dir:
        return os.path.realpath(os.path.abspath(os.path.expanduser(cfg_dir)))
    return os.path.realpath(os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla"))


def unique_stamped_path(directory, prefix, stamp, suffix="", ext=""):
    """<prefix>_<stamp>_<NN><suffix><ext> inside `directory`, where NN is a
    zero-padded 2-digit sequence number that is ALWAYS present - never
    omitted just because this happens to be the only file for that
    minute - and always starts at "00", incrementing only as far as
    needed to avoid clobbering an existing file.

    Replaces an earlier, inconsistent scheme where the first file for a
    given stamp used a bare name and only the second one onward got an
    ad-hoc "_2", "_3", ... suffix on an actual collision (see AGENTS.md
    REQ-030 - two Evnex sessions landing in the same display minute
    produced charge_deepdive_..._Evnex_Home.json and
    charge_deepdive_..._Evnex_Home_2.json, which doesn't sort/pair
    predictably and looks like an error). Every file sharing a `stamp`
    now always carries its position - "00", "01", ... - so the name
    alone tells you there could be siblings, instead of only the second
    file onward looking unusual.

    Distinct from archive_naming.next_archive_path() (an "XX_filename"
    PREFIX scheme for archived raw CSVs, kept exactly as-is - that one
    exists so Spotlight/Finder still recognise the original filename and
    extension, a different problem than this one) and from
    sync_api_tessie.py's unique_csv_path() (a different stamp format -
    YYYYMMDD_HHMMSS with seconds - guarding against a documented,
    unrelated re-fetch scenario, not a human-facing display name)."""
    n = 0
    while True:
        name = f"{prefix}_{stamp}_{n:02d}{suffix}{ext}"
        path = os.path.join(directory, name)
        if not os.path.exists(path):
            return path
        n += 1


def get_token(cli_token, config):
    return cli_token or os.environ.get("TESSIE_ACCESS_TOKEN") or config.get("tessie", {}).get("access_token")


def get_vin(cli_vin, config):
    return cli_vin or config.get("tessie", {}).get("vin")


def api_get(path, vin, token, params=None, timeout=30):
    """GET https://api.tessie.com/{vin}/{path}?access_token=...&...
    Returns (status_code_or_None, raw_bytes). status is None only for a
    connection-level failure (DNS/timeout/blocked egress), not an HTTP error.

    Sends an Accept header matching the requested format - a fixed
    "Accept: application/json" on a format=csv request was seen causing a
    server-side HTTP 500 (content-negotiation clash) during testing."""
    params = dict(params or {})
    params["access_token"] = token
    url = f"{API_BASE}/{vin}/{path}?{urllib.parse.urlencode(params)}"
    accept = "text/csv, */*;q=0.1" if params.get("format") == "csv" else "application/json"
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "tessie-tools/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return None, str(e).encode("utf-8")


def extract_json_records(body):
    """Parse a /drives or /charges JSON response body into a list of record
    dicts, or raise ValueError with a clear message if the shape is wrong."""
    try:
        data = json.loads(body)
    except Exception as e:
        raise ValueError(f"response was not valid JSON: {e}") from e
    records = data.get("results") if isinstance(data, dict) else data
    if not isinstance(records, list):
        keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
        raise ValueError(f"unexpected response shape - top-level: {keys}")
    return records


def save_audited_osm_to_tesla_registry(
    station_key: str,
    osm_lat: float,
    osm_lon: float,
    osm_node_id: int = None,
    is_destination: Optional[bool] = None,
) -> bool:
    """Saves verified OSM GPS coordinates and node ID to local Tesla registry.

    BUG-153 (2026-09-22): a Supercharger and its co-located Tesla Destination
    Charger commonly share the exact same display name/key (Balgowlah, NSW
    has both) - this used to loop over BOTH tesla_superchargers.json and
    tesla_chargers.json unconditionally and write to EVERY file where the
    name matched, with no idea which type was actually being audited. That
    meant auditing either one of a co-located pair could silently overwrite
    the OTHER's OSM data too - worse than just picking the wrong file, this
    wrote the wrong coordinates into BOTH records in one call. Callers that
    know the type (every real one - it's always the caller's own station
    object) now pass it, restricting the search to only that type's file;
    an unspecified type (is_destination=None) that resolves to the same key
    in both files is an unresolvable ambiguity and is refused rather than
    guessed, matching the same fix applied to places_osm_editor.py's
    (separate, historically-diverged) copy of this same function.
    """
    from datetime import datetime, timezone
    candidates = []
    tessie_dir = resolve_tessie_data_home()
    if tessie_dir:
        candidates.append(tessie_dir)
    candidates.append(os.path.join(REPO_ROOT, "resources"))

    if is_destination is True:
        filenames = ("tesla_chargers.json",)
    elif is_destination is False:
        filenames = ("tesla_superchargers.json",)
    else:
        filenames = ("tesla_superchargers.json", "tesla_chargers.json")

    if is_destination is None:
        hits = []
        for c_dir in candidates:
            for filename in filenames:
                fpath = os.path.join(c_dir, filename)
                if not os.path.isfile(fpath):
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict) and station_key in data:
                        hits.append((c_dir, filename))
                except Exception:
                    continue
        if len({fn for _, fn in hits}) > 1:
            print(f"⚠ Refusing to save audited OSM data for '{station_key}': this name exists in "
                  f"more than one registry file and the caller didn't say which one it means - "
                  f"pass is_destination explicitly instead of guessing.")
            return False

    saved_any = False
    for c_dir in candidates:
        for filename in filenames:
            fpath = os.path.join(c_dir, filename)
            if not os.path.isfile(fpath):
                continue
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)

                target_key = None
                if isinstance(data, dict):
                    if station_key in data:
                        target_key = station_key
                    else:
                        st_norm = station_key.lower().replace(" ", "").replace("_", "")
                        for k in data.keys():
                            k_norm = k.lower().replace(" ", "").replace("_", "")
                            if st_norm == k_norm or st_norm in k_norm or k_norm in st_norm:
                                target_key = k
                                break

                if target_key:
                    entry = data[target_key]
                    if "location" not in entry or not isinstance(entry["location"], dict):
                        entry["location"] = {}
                    entry["location"]["osm_lat"] = round(float(osm_lat), 6)
                    entry["location"]["osm_lon"] = round(float(osm_lon), 6)
                    if osm_node_id:
                        entry["location"]["osm_node_id"] = int(osm_node_id)
                    entry["location"]["osm_audited_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                    with open(fpath, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    saved_any = True
            except Exception:
                continue
    return saved_any
