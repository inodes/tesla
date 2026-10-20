#!/usr/bin/env python3
"""
sync_api_tessie.py - Fetch Tessie drives & charges via the Developer API
==========================================================================
Replaces the manual "download CSV from Tessie, drop it in Downloads,
run --consolidate" workflow with one command. This tool fetches new/changed
records via the API for every data type it knows about (drives, charges -
more can be added later without touching the analyzer scripts), writes a
CSV in the landing directory in the exact shape a manual Tessie export
already has, and then - by default, with a confirmation prompt - runs the
existing analyzer scripts' own `--consolidate` flag as a subprocess for
each type that got new data. It never imports or reimplements
drives_master.csv/charges_master.csv merging, invoice reconciliation, or
archiving - analyse_drives.py and analyse_charging.py don't
need to change at all; this tool just runs the exact command you'd type
yourself. Fetching logic stays out of those two scripts either way.

Consolidation is skipped (with a clear note) when: nothing new was fetched,
--dry-run was passed, --no-consolidate was passed, or this is running
non-interactively (e.g. cron) without --yes - unattended automation must
opt in explicitly with --yes rather than silently merging into your master
files.

Why JSON *and* CSV, not just CSV:
  - A local JSON cache (Tessie/api_cache/<type>.json), keyed by the API's
    own record `id`, is what makes re-running this idempotent and (from the
    second run on) incremental - only the window since the last successful
    sync is re-fetched. The CSV export has no `id`/`updated_at` columns at
    all, so this bookkeeping isn't possible from CSV alone.
  - The CSV written to the landing directory comes straight from Tessie's
    own `format=csv` output, not hand-derived from the JSON fields - some
    of its columns (e.g. Cost Per kWh) are computed server-side from
    full-precision values before rounding, and re-deriving them from the
    already-rounded JSON fields was found to disagree slightly during
    testing. Using Tessie's own CSV formatting avoids that class of issue
    entirely. See Tessie/TESSIE_API.md.

No pagination cursor is documented for either endpoint, so a JSON fetch
that might be truncated by `limit` is split in half and re-fetched
recursively until every leaf request comes back under its own limit - this
avoids silently missing records on an account with a lot of history.

Cache durability: the on-disk sync cache (Tessie/api_cache/<type>.json)
only advances its "last synced to" watermark once the corresponding data
is actually safe - either because there was nothing new in this window,
or because --consolidate has just successfully merged the freshly-written
CSV into the master file. A landing-directory CSV that gets deleted or
lost before it's ever consolidated is NOT lost from this tool's point of
view: because the watermark was never advanced for it, the next sync
simply re-fetches and re-surfaces that same window again. (An earlier
version of this script advanced the watermark on fetch success alone,
which meant a CSV lost before --consolidate was gone for good and
silently excluded from every future sync - see in
Tessie/TESSIE_API.md.)

Usage:
    export TESSIE_ACCESS_TOKEN=your_token_here
    ./sync_api_tessie.py                       # fetch + prompt to consolidate, both types
    ./sync_api_tessie.py --yes                  # fetch + consolidate without prompting (cron)
    ./sync_api_tessie.py --no-consolidate        # fetch only, leave CSV(s) for you to merge later
    ./sync_api_tessie.py --drives                # just drives
    ./sync_api_tessie.py --since-days 180        # first-run backfill window
    ./sync_api_tessie.py --dry-run               # fetch and report, write/consolidate nothing
    ./sync_api_tessie.py --reset-cache --charges  # forget prior sync point, re-backfill

See Tessie/TESSIE_API.md for endpoint/field notes and known limitations.
"""

import argparse
import csv
import io
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.tessie_api_common import (
    DEFAULT_UNIT_PARAMS, REPO_ROOT, TOOLS_DIR, api_get, extract_json_records,
    get_token, get_vin, load_config, load_vehicle_spec, resolve_tessie_data_home,
)
import lib.timezones as tztools

CACHE_DIR = os.path.join(REPO_ROOT, "resources", "api_cache")

# Which existing analyzer script owns --consolidate for each data type. This
# tool only ever shells out to these as a subprocess (same as typing the
# command yourself) - never imports their classes, so they stay fully
# decoupled from how fetching works.
CONSOLIDATE_SCRIPTS = {
 # REQ-072 fix (2026-09-15): analyse_drives.py/analyse_charging.py
 # live in Tools/, not Tools/lib/ - REQ-067 moved tessie_api_common.py (and its
 # SCRIPT_DIR) one directory deeper into lib/ without updating these two paths,
 # so --consolidate silently broke ("can't open file .../lib/tessie_charging_analyzer.py").
 # TOOLS_DIR (the same two-hop constant REQ-067 introduced for REPO_ROOT) is the
 # correct base here.
    "drives": os.path.join(TOOLS_DIR, "analyse_drives.py"),
    "charges": os.path.join(TOOLS_DIR, "analyse_charging.py"),
}

# One entry per data type this tool knows how to fetch. Adding a new Tessie
# endpoint later (e.g. "idles") means adding one entry here, not a new
# script and not touching analyse_drives.py/analyse_charging.py.
DATA_TYPES = {
    "drives": {
        "endpoint": "drives",
        "cache_file": os.path.join(CACHE_DIR, "drives.json"),
        "csv_prefix": "tessie_api_sync_drives",
        "default_since_days": 90,
    },
    "charges": {
        "endpoint": "charges",
        "cache_file": os.path.join(CACHE_DIR, "charges.json"),
        "csv_prefix": "tessie_api_sync_charges",
        "default_since_days": 90,
    },
}

# TODO-011: idles/tire_pressure/firmware_alerts - simple "single CSV +
# archive" persistence, deliberately NOT the fuller <type>/ folder +
# master CSV + deepdive/archive pattern drives/charges use (there's no
# per-record deep-dive detail to fetch for any of these three, and no
# dedicated analyzer script to hand a --consolidate step to either) -
# the user's own explicit choice for this TODO's format: "simple...
# single csv with archive". Each gets its own <type>_master.csv under
# <tessie data home>/<type>/, with a pre-change backup copied to
# <type>/archive/ before every overwrite (BUG-025's own convention).
IDLES_CACHE_FILE = os.path.join(CACHE_DIR, "idles.json")
FIRMWARE_ALERTS_CACHE_FILE = os.path.join(CACHE_DIR, "firmware_alerts.json")
TIRE_PRESSURE_CACHE_FILE = os.path.join(CACHE_DIR, "tire_pressure.json")

IDLES_FIELDNAMES = [
    "Idle ID", "Started At (UTC)", "Started At TZ", "Started At (Local)",
    "Ended At (UTC)", "Ended At TZ", "Ended At (Local)", "Duration (min)",
    "Location", "Saved Location", "Latitude", "Longitude",
    "Starting Battery (%)", "Ending Battery (%)",
    "Rated Range Used (km)", "Ideal Range Used (km)",
    "Climate Fraction", "Sentry Fraction",
    "Energy Used (kWh)", "Charger Energy Used (kWh)", "Import ID",
]

# Confirmed live (2026-09-26, corrected same day): tire_pressure DOES
# support real history via from/to - an earlier pass here tested only the
# no-args current-snapshot call and wrongly concluded there was no
# history at all. With from/to given, the response shape changes
# entirely: a flat "melted" list of {timestamp, tire, pressure} entries
# (one per tire per reading, no `results` wrapper, no `id`, and no
# per-reading status - status only comes from the current-snapshot
# call), pivoted here into one wide row per timestamp by
# _pivot_tire_pressure_history(). Also confirmed: Tessie's own backend
# only retains ~30-35 days of this history regardless of how far back
# `from` asks (a 10-year window and a 30-day window returned within
# ~100 rows of each other), and `limit` has no truncating effect on this
# endpoint at all (unlike idles) - so this is fetched as one direct call
# per window, not via fetch_json_chunked()'s id-based recursive split.
TIRE_PRESSURE_FIELDNAMES = [
    "Timestamp (UTC)", "Timestamp TZ", "Timestamp (Local)",
    "Front Left (kPa)", "Front Left Status",
    "Front Right (kPa)", "Front Right Status",
    "Rear Left (kPa)", "Rear Left Status",
    "Rear Right (kPa)", "Rear Right Status",
]

# Confirmed live (2026-09-26): firmware_alerts records have NO `id` field
# at all (unlike drives/charges/idles) - "Alert Key" (timestamp+name) is
# this repo's own synthesized dedup key, not anything Tessie returns.
FIRMWARE_ALERTS_FIELDNAMES = [
    "Alert Key", "Timestamp (UTC)", "Timestamp TZ", "Timestamp (Local)",
    "Name", "Description", "Impact", "Recent Fleet Count", "Incidence Rate",
]

MIN_CHUNK_SECONDS = 3600  # don't split a date range smaller than 1 hour
OVERLAP_SECONDS = 86400   # re-check the last day every run in case of late server-side edits


def load_cache(path):
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "records" in data:
                    return data
        except Exception:
            pass
    return {"_meta": {"last_synced_to": None}, "records": {}}


def save_cache(path, cache):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def commit_cache(spec, cache, to_ts):
    """Advance the sync watermark and persist the cache to disk. Only call
    this once the data fetched for this window is actually safe: either
    there was nothing new to lose (new_or_changed == 0, so there's no
    pending CSV at all), or --consolidate has just successfully merged the
    freshly-written CSV into the master file. Committing on fetch success
    alone was the original issue: a CSV deleted before
    consolidation was permanently unrecoverable, because the cache already
    considered those records 'seen'."""
    cache["_meta"]["last_synced_to"] = to_ts
    cache["_meta"]["last_synced_at_wallclock"] = datetime.now().isoformat(timespec="seconds")
    save_cache(spec["cache_file"], cache)


def fetch_json_chunked(endpoint, vin, token, from_ts, to_ts, chunk_limit=500, api_get_fn=api_get, key_fn=None):
    """Fetch every JSON record in [from_ts, to_ts), splitting the window in
    half and recursing whenever a chunk comes back exactly at chunk_limit
    (a sign there could be more we didn't see - no cursor is documented for
    either endpoint, so this is the safe way to avoid silent truncation).

    key_fn (TODO-011): the recursive merge below dedupes overlapping-window
    records by a per-record key - originally always the API's own numeric
    `id` (drives/charges/idles all have one). firmware_alerts has no `id`
    at all (confirmed live), so it passes its own composite key function
    instead; every other caller keeps the default."""
    if to_ts <= from_ts:
        return []
    key_fn = key_fn or (lambda r: r["id"])
    params = dict(DEFAULT_UNIT_PARAMS)
    params.update({"from": from_ts, "to": to_ts, "limit": chunk_limit})
    status, body = api_get_fn(endpoint, vin, token, params=params)
    if status != 200:
        raise RuntimeError(f"{endpoint} fetch failed for [{from_ts}, {to_ts}): HTTP {status} - {body[:200]!r}")
    records = extract_json_records(body)
    if len(records) < chunk_limit:
        return records
    if (to_ts - from_ts) <= MIN_CHUNK_SECONDS:
 # Hit the minimum split size while still at the limit ceiling - for
 # real drive/charge data this should never happen (it would mean
 # chunk_limit-or-more events inside one hour), so fail loudly rather
 # than silently returning a truncated, incomplete result.
        raise RuntimeError(
            f"{endpoint}: got {len(records)} record(s) (>= limit {chunk_limit}) in a window that "
            f"can't be split further ({from_ts}-{to_ts}, {to_ts - from_ts}s) - refusing to silently "
            f"drop records. Re-run with a larger --chunk-limit if this is genuinely expected."
        )
    mid = (from_ts + to_ts) // 2
    left = fetch_json_chunked(endpoint, vin, token, from_ts, mid, chunk_limit, api_get_fn, key_fn)
    right = fetch_json_chunked(endpoint, vin, token, mid, to_ts, chunk_limit, api_get_fn, key_fn)
    combined = {key_fn(r): r for r in left}
    combined.update({key_fn(r): r for r in right})
    return list(combined.values())


def csv_row_count(csv_bytes):
    reader = csv.reader(io.StringIO(csv_bytes.decode("utf-8-sig")))
    rows = list(reader)
    return max(0, len(rows) - 1)  # minus header


def latest_entry_timestamp(records):
    """The most recent point in time this batch's data actually covers -
    used to name the CSV after when the data is FROM, not merely when the
    fetch happened to run (a run at 5pm covering a drive from 8am should be
    named after 8am, not 5pm). Prefers each record's `ended_at` (the point
    its data became final); falls back to `started_at` for the rare record
    with no end yet. Returns None if no record has a usable timestamp."""
    ts_values = [r.get("ended_at") or r.get("started_at") for r in records]
    ts_values = [t for t in ts_values if t is not None]
    return max(ts_values) if ts_values else None


def unique_csv_path(landing_dir, prefix, stamp):
    """<prefix>_<stamp>.csv in landing_dir, unless that name is already
    taken, in which case a zero-padded numeric suffix (_01, _02, ...) is
    added until a free name is found. Two fetches can land on the exact
    same "latest entry" stamp even though they're genuinely different
    files: a field-level edit to an already-seen record doesn't move the
    max timestamp, and a re-run before --consolidate was ever answered
    re-fetches the same pending window (by design, since ) and so
    can compute the same latest-entry stamp again."""
    base = f"{prefix}_{stamp}"
    path = os.path.join(landing_dir, base + ".csv")
    if not os.path.exists(path):
        return path
    n = 1
    while True:
        candidate = os.path.join(landing_dir, f"{base}_{n:02d}.csv")
        if not os.path.exists(candidate):
            return candidate
        n += 1


def retag_csv_utc_tz_local(csv_body, records):
    """REQ-179: turn Tessie's server-formatted charges/drives CSV into the
    same UTC / TZ / Local schema the master files use, so no row depends on
    a zone label in a header ("Started At (AEST)" vs "(AEDT)" broke the
    import on the first day of daylight saving) or on one fixed display zone.

    The CSV is requested with timezone=UTC, so its naive time text IS UTC -
    no offset guessing, no ambiguous repeated hour at daylight-saving
    fall-back. Each time column is replaced by three:
      "<X> At (UTC)"   exact Unix epoch seconds (taken from the JSON
                        record with the same minute, else from the text)
      "<X> At TZ"      IANA zone of WHERE the car was at that moment
      "<X> At (Local)" "YYYY-MM-DD HH:MM ABBR" in that zone
    Zone comes from the row's own coordinates: a charge has one point; a
    drive has a Starting and an Ending point, resolved separately, so a drive
    that crosses a timezone border keeps a correct local time at each end
    while both ends stay comparable through the UTC epoch."""
    import calendar as _cal
    import io as _io
    import csv as _csv
    text = csv_body.decode("utf-8-sig") if isinstance(csv_body, (bytes, bytearray)) else csv_body
    reader = _csv.DictReader(_io.StringIO(text))
    fields = list(reader.fieldnames or [])
    start_key = next((f for f in fields if f.startswith("Started At")), None)
    end_key = next((f for f in fields if f.startswith("Ended At")), None)
    if not start_key or not end_key or start_key.endswith("(UTC)") is False:
        return csv_body  # not the UTC-requested shape - leave untouched
    by_minute = {"Started At": {}, "Ended At": {}}
    for r in records or []:
        for label, k in (("Started At", "started_at"), ("Ended At", "ended_at")):
            v = r.get(k)
            if isinstance(v, (int, float)):
                by_minute[label][int(v) // 60] = int(v)

    def epoch_of(label, txt):
        txt = (txt or "").strip()
        if not txt:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                e = _cal.timegm(datetime.strptime(txt, fmt).timetuple())
                return by_minute[label].get(e // 60, e)
            except ValueError:
                continue
        return None

    def point(row, label):
        if "Starting Latitude" in row:  # drive: start / end have their own point
            pre = "Starting " if label == "Started At" else "Ending "
            return row.get(pre + "Latitude"), row.get(pre + "Longitude")
        return row.get("Latitude"), row.get("Longitude")

    def cols(label, row, epoch):
        if epoch is None:
            return {f"{label} (UTC)": "", f"{label} TZ": "", f"{label} (Local)": ""}
        lat, lon = point(row, label)
        zone = tztools.resolve_timezone(lat if lat not in ("", None) else None,
                                        lon if lon not in ("", None) else None)
        local_dt, abbr, _ = tztools.localize(epoch, zone)
        return {f"{label} (UTC)": str(int(epoch)), f"{label} TZ": zone,
                f"{label} (Local)": f"{local_dt.strftime('%Y-%m-%d %H:%M')} {abbr}" if local_dt else ""}

    new_fields = ["Started At (UTC)", "Started At TZ", "Started At (Local)",
                  "Ended At (UTC)", "Ended At TZ", "Ended At (Local)"]
    out_fields = new_fields + [f for f in fields if f not in (start_key, end_key)]
    out = _io.StringIO()
    w = _csv.DictWriter(out, fieldnames=out_fields, quoting=_csv.QUOTE_ALL, lineterminator="\n")
    w.writeheader()
    for row in reader:
        new = {}
        new.update(cols("Started At", row, epoch_of("Started At", row.get(start_key))))
        new.update(cols("Ended At", row, epoch_of("Ended At", row.get(end_key))))
        for f in fields:
            if f not in (start_key, end_key):
                new[f] = row.get(f, "")
        w.writerow(new)
    return out.getvalue().encode("utf-8")


def sync_one_type(type_name, spec, token, vin, since_days, dry_run, landing_dir,
                   reset_cache=False, api_get_fn=api_get, now_ts=None):
    """Returns a summary dict; raises on hard failure."""
    now_ts = now_ts if now_ts is not None else int(time.time())
    cache = {"_meta": {"last_synced_to": None}, "records": {}} if reset_cache else load_cache(spec["cache_file"])
    last_synced_to = cache["_meta"].get("last_synced_to")

    if last_synced_to:
        from_ts = max(0, last_synced_to - OVERLAP_SECONDS)
    else:
        from_ts = now_ts - since_days * 86400
    to_ts = now_ts

    records = fetch_json_chunked(spec["endpoint"], vin, token, from_ts, to_ts, api_get_fn=api_get_fn)

    before_count = len(cache["records"])
    new_or_changed = 0
    for r in records:
        rid = str(r.get("id")) if r.get("id") is not None else None
        if not rid:
            continue
        if cache["records"].get(rid) != r:
            new_or_changed += 1
        cache["records"][rid] = r

    csv_path = None
    csv_warning = None
    if new_or_changed > 0 and not dry_run:
        csv_params = dict(DEFAULT_UNIT_PARAMS)
        # REQ-179: ask for UTC text (exact, no daylight-saving ambiguity) and
        # re-tag it below with per-point UTC epoch + real timezone + local time.
        csv_params.update({"from": from_ts, "to": to_ts, "format": "csv", "limit": len(records) + 10,
                           "timezone": "UTC"})
        status, csv_body = api_get_fn(spec["endpoint"], vin, token, params=csv_params)
        if status != 200:
            raise RuntimeError(f"{spec['endpoint']} CSV fetch failed: HTTP {status} - {csv_body[:200]!r}")
        actual_rows = csv_row_count(csv_body)
        if actual_rows != len(records):
            csv_warning = (f"CSV returned {actual_rows} row(s) but the JSON fetch for the same "
                            f"window found {len(records)} - saving it anyway, but verify manually.")
        os.makedirs(landing_dir, exist_ok=True)
        latest_ts = latest_entry_timestamp(records)
        stamp_dt = datetime.fromtimestamp(latest_ts) if latest_ts is not None else datetime.now()
        stamp = stamp_dt.strftime("%Y%m%d_%H%M%S")
        csv_path = unique_csv_path(landing_dir, spec["csv_prefix"], stamp)
        csv_body = retag_csv_utc_tz_local(csv_body, records)
        with open(csv_path, "wb") as f:
            f.write(csv_body)

 # Only advance the watermark immediately when there's nothing pending
 # to lose (new_or_changed == 0 - no CSV was written this run either).
 # When new_or_changed > 0, a CSV now exists in the landing directory
 # that hasn't been merged into the master file yet; committing here
 # would let a deleted/lost CSV silently vanish from all future syncs
 # (). Instead, hand the in-memory cache back via
 # "pending_commit" so the caller can commit it once --consolidate
 # actually succeeds - or never, if the CSV never gets merged, in which
 # case the next sync safely re-fetches and re-surfaces the same window.
    committed = False
    if not dry_run and new_or_changed == 0:
        commit_cache(spec, cache, to_ts)
        committed = True

    return {
        "type": type_name,
        "window": (from_ts, to_ts),
        "fetched": len(records),
        "new_or_changed": new_or_changed,
        "cache_total_before": before_count,
        "cache_total_after": len(cache["records"]),
        "csv_path": csv_path,
        "csv_warning": csv_warning,
        "committed": committed,
        "pending_commit": None if (committed or dry_run) else {"spec": spec, "cache": cache, "to_ts": to_ts},
    }


def _parse_battery_health_timestamp(ts):
    """Best-effort parse of one battery_health reading's own "timestamp"
    field into a naive LOCAL-time datetime (matching this repo's own
    convention elsewhere - drives/charges CSV timestamps are always
    local, e.g. analyse_drives.py's own tztools.localize() calls
    - so this needs to actually convert, not just relabel).

    Note (2026-09-08, live on the user's real Mac): this was originally
    assumed to always be Unix seconds (an int/float) per a docs summary -
    the real API returned a STRING instead (`TypeError: 'str' object
    cannot be interpreted as an integer`), so every shape actually seen
    or plausible is handled now: a numeric string (still epoch seconds,
    stringified), a real Unix int/float, or an ISO-8601 string.

    Note (found while building a scatterplot from this same data,
    2026-09-08): the real API's ISO strings carry an explicit UTC "Z"
    suffix (e.g. "2026-06-05T05:55:34.000Z") - the first fix's
    `.replace(tzinfo=None)` kept the UTC clock numbers but silently
    dropped the "this was UTC" fact, mislabeling them as if already
    local. Confirmed live: that produced "synced_at": "2026-06-05T05:55:34"
    for the exact same real reading (odometer 105, max_range 566.1) the
    user's own CSV export recorded as local "2026-06-05 15:55:34" - a
    10-hour (AEST) discrepancy that would have shown up as duplicate,
    near-identical points 10 hours apart once merged/deduplicated by
    timestamp string, e.g. on a scatterplot. Now explicit UTC info
    (a "Z" suffix or a numeric +HH:MM/-HH:MM offset) is actually
    converted to local time via timezones.localize() (this
    endpoint carries no lat/lon of its own to resolve an exact zone, so
    DEFAULT_TIMEZONE is used, same fallback convention as elsewhere in
    this repo) rather than just having its offset stripped; a NAIVE ISO
    string (no offset info at all) is trusted as already-local, since
    there's no basis to assume otherwise. Returns None if every attempt
    fails, rather than raising - callers fall back to now() so a single
    unparseable reading never crashes the whole sync."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        local_dt, _abbr, _off = tztools.localize(ts, tztools.DEFAULT_TIMEZONE)
        return local_dt.replace(tzinfo=None) if local_dt else None
    if isinstance(ts, str):
        s = ts.strip()
        try:
            epoch = float(s)
            local_dt, _abbr, _off = tztools.localize(epoch, tztools.DEFAULT_TIMEZONE)
            return local_dt.replace(tzinfo=None) if local_dt else None
        except (ValueError, OSError, OverflowError):
            pass
        try:
            parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            local_dt, _abbr, _off = tztools.localize(parsed.timestamp(), tztools.DEFAULT_TIMEZONE)
            return local_dt.replace(tzinfo=None) if local_dt else None
        return parsed  # naive string, no offset info - trust it as already-local
    return None


def _battery_health_reading_from_api(r):
    """Normalizes one raw battery_health API reading (from either the
    single "result" snapshot or one item of the historical "results"
    array - same field shape either way) into this repo's log entry
    format. The live "result" snapshot has no timestamp of its own, so
    callers get now() for that one; a "results" array item's own
    timestamp is parsed via _parse_battery_health_timestamp() (its own
    docstring covers why this can't just assume Unix seconds)."""
    parsed = _parse_battery_health_timestamp(r.get("timestamp"))
    synced_at = (parsed or datetime.now()).isoformat(timespec="seconds")
    return {
        "synced_at": synced_at,
        "capacity_kwh": r.get("capacity"),
        "max_range_km": r.get("max_range"),
        "max_ideal_range_km": r.get("max_ideal_range"),
        "odometer_km": r.get("odometer"),
        "original_capacity_kwh": r.get("original_capacity"),
        "degradation_percent": r.get("degradation_percent"),
        "health_percent": r.get("health_percent"),
        "source": "api:battery_health",
        "raw": r,
    }


def fetch_battery_health_chunked(vin, token, from_ts, to_ts, chunk_limit=500, api_get_fn=api_get):
    """Fetch every historical battery_health reading in [from_ts, to_ts)
    from Tessie's own API - the SAME GET /{vin}/battery_health endpoint
    sync_battery_health() polls for a live snapshot, but Tessie returns a
    "results" array of every real historical reading in range whenever
    from/to are supplied (confirmed via Tessie's own developer docs, "Get
    Battery Health Measurements" - a documented variant of this same
    endpoint, not a separate path). This is the actual fix for "pull the
    full history from Tessie yourself" - manually-exported CSVs are no
    longer needed as the primary source once this runs.

    Mirrors fetch_json_chunked()'s own recursive bisection-on-limit
    pattern (no documented pagination cursor for this endpoint either),
    but keyed by each reading's own "timestamp" instead of an "id" field
    - battery_health readings don't have one."""
    if to_ts <= from_ts:
        return []
    params = dict(DEFAULT_UNIT_PARAMS)
    params.update({"from": from_ts, "to": to_ts})
    status, body = api_get_fn("battery_health", vin, token, params=params)
    if status != 200:
        raise RuntimeError(f"battery_health history fetch failed for [{from_ts}, {to_ts}): HTTP {status} - {body[:200]!r}")
    try:
        data = json.loads(body)
    except Exception as e:
        raise RuntimeError(f"battery_health history response was not valid JSON: {e}") from e
    records = data.get("results") if isinstance(data, dict) else None
    if not isinstance(records, list):
        raise RuntimeError(f"unexpected battery_health history response shape: {list(data.keys()) if isinstance(data, dict) else type(data).__name__}")
    if len(records) < chunk_limit:
        return records
    if (to_ts - from_ts) <= MIN_CHUNK_SECONDS:
        raise RuntimeError(
            f"battery_health: got {len(records)} record(s) (>= limit {chunk_limit}) in a window that "
            f"can't be split further ({from_ts}-{to_ts}, {to_ts - from_ts}s) - refusing to silently drop records."
        )
    mid = (from_ts + to_ts) // 2
    left = fetch_battery_health_chunked(vin, token, from_ts, mid, chunk_limit, api_get_fn)
    right = fetch_battery_health_chunked(vin, token, mid, to_ts, chunk_limit, api_get_fn)
    combined = {r.get("timestamp"): r for r in left}
    combined.update({r.get("timestamp"): r for r in right})
    return list(combined.values())


def detect_battery_health_outliers(records, z_threshold=4.0):
    """Two layers of outlier detection, persisting 'is_outlier': True and
    'outlier_reason' on matching records ('is_outlier': False on valid ones):

    1. Physical-ceiling check (needs no history at all): usable capacity can
       never exceed this pack's gross capacity (vehicle_spec.json). Tessie's
       BMS can momentarily report above that ceiling right after a high-power
       DC fast-charge burst, before cell voltage has settled - a known
       artifact, confirmed 2026-09-16 by cross-referencing one such reading
       against the charge log (it landed seconds after a 120+kW Supercharger
       session ended). These points are excluded from the trend fit below,
       not just flagged after the fact, so a spike doesn't also tilt the
       trendline it gets measured against.
    2. Trendline-residual check: evaluates deviation from the vehicle's own
       dynamic degradation slope vs odometer, rather than a static threshold.
       Adapts automatically as the battery ages. Needs >= 4 real readings.
    """
    if not isinstance(records, list):
        return records

    spec = load_vehicle_spec()
    gross_kwh = (spec.get("battery") or {}).get("gross_kwh")

    def physical_ceiling_reason(r):
        cap = r.get("capacity_kwh")
        if gross_kwh and cap is not None and cap > gross_kwh:
            return (f"Capacity {cap:.2f} kWh exceeds this pack's {gross_kwh:.2f} kWh gross "
                    f"capacity - physically impossible as a usable-capacity reading, "
                    f"almost certainly a BMS estimation artifact right after a high-power DC charge")
        return None

    physically_impossible = {}
    for r in records:
        if not isinstance(r, dict):
            continue
        reason = physical_ceiling_reason(r)
        if reason:
            physically_impossible[id(r)] = reason

    if len(records) < 4:
        for r in records:
            if not isinstance(r, dict):
                continue
            reason = physically_impossible.get(id(r))
            if reason:
                r["is_outlier"] = True
                r["outlier_reason"] = reason
            else:
                r.setdefault("is_outlier", False)
        return records

    def linear_fit(xs, ys):
        n = len(xs)
        if n < 2:
            return 0.0, statistics.mean(ys) if ys else 0.0
        mx, my = statistics.mean(xs), statistics.mean(ys)
        denom = sum((x - mx) ** 2 for x in xs)
        if denom == 0:
            return 0.0, my
        m = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
        b = my - m * mx
        return m, b

 # Separate readings with and without odometer - excluding any reading
 # already flagged as physically impossible, so a BMS spike doesn't also
 # bias the trend line it (and every other reading) gets measured against.
    odo_records = [
        r for r in records
        if isinstance(r, dict) and r.get("odometer_km") is not None and id(r) not in physically_impossible
    ]

 # Model Capacity vs Odometer degradation trend
    cap_pts = [(r["odometer_km"], r["capacity_kwh"], r) for r in odo_records if r.get("capacity_kwh") is not None]
    if len(cap_pts) >= 4:
        xs = [p[0] for p in cap_pts]
        ys = [p[1] for p in cap_pts]
        m, b = linear_fit(xs, ys)
        res = [y - (m * x + b) for x, y in zip(xs, ys)]
        med_res = statistics.median(res)
        mad = statistics.median([abs(r_val - med_res) for r_val in res])
        sigma = 1.4826 * mad if mad > 0 else (statistics.stdev(res) if len(res) > 1 else 1.0)
        for p, r_val in zip(cap_pts, res):
            z = r_val / (sigma if sigma > 0 else 1.0)
            p[2]["_z_cap"] = z
            p[2]["_exp_cap"] = m * p[0] + b

 # Model Range vs Odometer degradation trend
    rng_pts = [(r["odometer_km"], r["max_range_km"], r) for r in odo_records if r.get("max_range_km") is not None]
    if len(rng_pts) >= 4:
        xs = [p[0] for p in rng_pts]
        ys = [p[1] for p in rng_pts]
        m, b = linear_fit(xs, ys)
        res = [y - (m * x + b) for x, y in zip(xs, ys)]
        med_res = statistics.median(res)
        mad = statistics.median([abs(r_val - med_res) for r_val in res])
        sigma = 1.4826 * mad if mad > 0 else (statistics.stdev(res) if len(res) > 1 else 1.0)
        for p, r_val in zip(rng_pts, res):
            z = r_val / (sigma if sigma > 0 else 1.0)
            p[2]["_z_rng"] = z
            p[2]["_exp_rng"] = m * p[0] + b

 # Chronological median fallback for records with no odometer - also
 # excluding physically-impossible readings from the reference set.
    all_ranges = [
        r.get("max_range_km") for r in records
        if isinstance(r, dict) and r.get("max_range_km") is not None and id(r) not in physically_impossible
    ]
    med_all_rng = statistics.median(all_ranges) if all_ranges else 0.0
    mad_all_rng = statistics.median([abs(x - med_all_rng) for x in all_ranges]) if all_ranges else 1.0
    sigma_all_rng = 1.4826 * mad_all_rng if mad_all_rng > 0 else 1.0

    for r in records:
        if not isinstance(r, dict):
            continue
        reasons = []
        z_cap = r.pop("_z_cap", None)
        z_rng = r.pop("_z_rng", None)
        exp_cap = r.pop("_exp_cap", None)
        exp_rng = r.pop("_exp_rng", None)

        physical_reason = physically_impossible.get(id(r))
        if physical_reason:
            reasons.append(physical_reason)

        if z_cap is not None and abs(z_cap) > z_threshold:
            reasons.append(f"Capacity {r['capacity_kwh']:.2f} kWh vs trend {exp_cap:.2f} kWh "
                            f"(exceeds {z_threshold}σ trend residual, Z={z_cap:+.2f})")
        if z_rng is not None and abs(z_rng) > z_threshold:
            reasons.append(f"Range {r['max_range_km']:.2f} km vs trend {exp_rng:.2f} km "
                            f"(exceeds {z_threshold}σ trend residual, Z={z_rng:+.2f})")

 # Fallback for no-odometer records
        if r.get("odometer_km") is None and r.get("max_range_km") is not None:
            z_no_odo = (r["max_range_km"] - med_all_rng) / sigma_all_rng
            if abs(z_no_odo) > z_threshold:
                reasons.append(f"Range {r['max_range_km']:.2f} km vs median {med_all_rng:.2f} km "
                                f"(exceeds {z_threshold}σ trend residual, Z={z_no_odo:+.2f})")

        if reasons:
            r["is_outlier"] = True
            r["outlier_reason"] = "; ".join(reasons)
        else:
            r["is_outlier"] = False
            r.pop("outlier_reason", None)

    return records


def _utc_tz_local_cols(prefix, epoch, lat=None, lon=None):
    """{prefix (UTC)/(TZ)/(Local)} for one Unix epoch second read straight
    from Tessie's JSON API. Deliberately NOT normalize_row_timezone()
    (TODO-009) - that helper reverses Tessie's own CSV EXPORT convention
    (a naive local string, always in the one fixed Australia/Sydney offset
    it was requested in, that has to be un-reversed via the true lat/lon
    to recover UTC). The JSON API's own epoch fields are already true UTC
    with no such reversal needed - this just localizes them for display.
    Uses resolve_timezone(lat, lon) for the real zone when a per-record
    GPS point exists (idles); falls back to tztools.localize()'s own
    default (Australia/Sydney) when it doesn't (tire_pressure/
    firmware_alerts carry no location at all from the API)."""
    if epoch is None or epoch == "":
        return {f"{prefix} (UTC)": "", f"{prefix} TZ": "", f"{prefix} (Local)": ""}
    zone = tztools.resolve_timezone(lat, lon) if (lat is not None and lon is not None) else None
    local_dt, abbr, _ = tztools.localize(epoch, zone)
    return {
        f"{prefix} (UTC)": int(epoch),
        f"{prefix} TZ": abbr or "",
        f"{prefix} (Local)": local_dt.strftime("%Y-%m-%d %H:%M") if local_dt else "",
    }


def _idle_row_from_api(r):
    started_at = r.get("started_at")
    ended_at = r.get("ended_at")
    lat = r.get("latitude")
    lon = r.get("longitude")
    duration_min = (round((ended_at - started_at) / 60.0, 1)
                     if (started_at is not None and ended_at is not None) else "")
    row = {
        "Idle ID": r.get("id"),
        "Duration (min)": duration_min,
        "Location": r.get("location") or "",
        "Saved Location": r.get("saved_location") or "",
        "Latitude": lat,
        "Longitude": lon,
        "Starting Battery (%)": r.get("starting_battery"),
        "Ending Battery (%)": r.get("ending_battery"),
        "Rated Range Used (km)": r.get("rated_range_used"),
        "Ideal Range Used (km)": r.get("ideal_range_used"),
        "Climate Fraction": r.get("climate_fraction"),
        "Sentry Fraction": r.get("sentry_fraction"),
        "Energy Used (kWh)": r.get("energy_used"),
        "Charger Energy Used (kWh)": r.get("charger_energy_used"),
        "Import ID": r.get("import_id") or "",
    }
    row.update(_utc_tz_local_cols("Started At", started_at, lat, lon))
    row.update(_utc_tz_local_cols("Ended At", ended_at, lat, lon))
    return row


def _tire_pressure_row_from_api(r):
    """Builds one wide CSV row from either shape tire_pressure's API can
    hand us: the current-snapshot call (has *_status fields) or one
    pivoted history entry from _pivot_tire_pressure_history() (pressures
    only, no status - Tessie's history mode doesn't report it). Missing
    status keys simply come out blank either way."""
    row = {
        "Front Left (kPa)": r.get("front_left"),
        "Front Left Status": r.get("front_left_status") or "",
        "Front Right (kPa)": r.get("front_right"),
        "Front Right Status": r.get("front_right_status") or "",
        "Rear Left (kPa)": r.get("rear_left"),
        "Rear Left Status": r.get("rear_left_status") or "",
        "Rear Right (kPa)": r.get("rear_right"),
        "Rear Right Status": r.get("rear_right_status") or "",
    }
    row.update(_utc_tz_local_cols("Timestamp", r.get("timestamp")))
    return row


def fetch_tire_pressure_history(vin, token, from_ts, to_ts, api_get_fn=api_get):
    """Fetch every historical tire_pressure reading in [from_ts, to_ts).
    Confirmed live (2026-09-26): passing from/to switches this endpoint
    into a genuinely different response shape from its current-snapshot
    mode - a flat list of {timestamp, tire, pressure} entries (one per
    tire per reading), no `results` wrapper, no `id`. `limit` has no
    truncating effect here (tested), so - unlike fetch_json_chunked() -
    this is one direct, unchunked call per window."""
    if to_ts <= from_ts:
        return []
    params = {"pressure_format": "kpa", "from": from_ts, "to": to_ts}
    status, body = api_get_fn("tire_pressure", vin, token, params=params)
    if status != 200:
        raise RuntimeError(f"tire_pressure history fetch failed for [{from_ts}, {to_ts}): HTTP {status} - {body[:200]!r}")
    try:
        data = json.loads(body)
    except Exception as e:
        raise RuntimeError(f"tire_pressure history response was not valid JSON: {e}") from e
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected tire_pressure history response shape: {type(data).__name__}")
    return data


def _pivot_tire_pressure_history(entries):
    """Groups the melted {timestamp, tire, pressure} list from
    fetch_tire_pressure_history() into one dict per timestamp (all 4
    tires) - the same wide shape _tire_pressure_row_from_api() already
    expects for a current-snapshot reading, just without any *_status
    keys (the history shape doesn't carry them)."""
    by_ts = {}
    for e in entries:
        ts = e.get("timestamp")
        tire = e.get("tire")
        if ts is None or tire not in ("front_left", "front_right", "rear_left", "rear_right"):
            continue
        by_ts.setdefault(ts, {"timestamp": ts})[tire] = e.get("pressure")
    return list(by_ts.values())


def _firmware_alert_key(r):
    """No native id on this endpoint (confirmed live) - timestamp+name is
    unique enough for one vehicle (the same named alert firing twice in
    the exact same second isn't a real-world case)."""
    return f"{r.get('timestamp')}:{r.get('name')}"


def _firmware_alert_row_from_api(r):
    row = {
        "Alert Key": _firmware_alert_key(r),
        "Name": r.get("name") or "",
        "Description": r.get("description") or "",
        "Impact": r.get("impact") or "",
        "Recent Fleet Count": r.get("recent_fleet_count"),
        "Incidence Rate": r.get("incidence_rate"),
    }
    row.update(_utc_tz_local_cols("Timestamp", r.get("timestamp")))
    return row


def merge_into_master_csv(type_name, home, new_rows, fieldnames, key_field, sort_key_fn):
    """Simple "single CSV + archive" persistence for a Tessie data type
    with no dedicated analyzer script of its own (idles/tire_pressure/
    firmware_alerts - TODO-011): unlike drives/charges there's no
    separate --consolidate step to hand off to, so this writes directly
    to <home>/<type_name>/<type_name>_master.csv. Deliberately the
    simpler of the two shapes this repo now has for a new Tessie data
    type (vs. the fuller <type>/ folder + master CSV + deepdive/archive
    pattern drives/charges use, and vs. battery_health's single JSON
    log) - the user's own explicit choice for this TODO: "simple...
    single csv with archive."

    Merges new_rows into whatever master CSV already exists, keyed by
    key_field (a re-fetched/updated record overwrites its old row rather
    than duplicating), and - matching drives/charges' own "pre-change
    backup saved to archive/" convention (BUG-025) - copies the master
    to <type_name>/archive/ with a UTC timestamp in its name before
    overwriting it, whenever it already existed. Returns (added, changed,
    total)."""
    type_dir = os.path.join(home, type_name)
    archive_dir = os.path.join(type_dir, "archive")
    master_path = os.path.join(type_dir, f"{type_name}_master.csv")
    os.makedirs(type_dir, exist_ok=True)

    existing = {}
    if os.path.isfile(master_path):
        with open(master_path, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                k = row.get(key_field)
                if k:
                    existing[k] = row

    added = 0
    changed = 0
    for raw_row in new_rows:
        row = {k: ("" if v is None else v) for k, v in raw_row.items()}
        k = str(row.get(key_field))
        prev = existing.get(k)
        if prev is None:
            added += 1
        elif {kk: str(vv) for kk, vv in prev.items()} != {kk: str(vv) for kk, vv in row.items()}:
            changed += 1
        existing[k] = row

    if added == 0 and changed == 0:
        return 0, 0, len(existing)

    all_rows = sorted(existing.values(), key=sort_key_fn)

    if os.path.isfile(master_path):
        os.makedirs(archive_dir, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%SZ")
        shutil.copy2(master_path, os.path.join(archive_dir, f"{type_name}_master_backup_{stamp}.csv"))

    tmp = master_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", restval="")
        writer.writeheader()
        writer.writerows(all_rows)
    os.replace(tmp, master_path)

    return added, changed, len(all_rows)


def sync_idles(tessie_dir=None, since_days=90, dry_run=False, reset_cache=False):
    """idles: a real fetchable date-range history (has `id`, `started_at`,
    `ended_at`, real per-record lat/lon) - same chunked incremental-fetch
    shape as drives/charges, just written straight to its own simple
    master CSV instead of a landing-dir CSV + --consolidate subprocess
    (TODO-011 - no dedicated analyzer script exists for this type)."""
    home = tessie_dir or resolve_tessie_data_home()
    if not home:
        print("❌ No Tessie data home configured - nothing to sync to.")
        return None
    cfg = load_config()
    token = get_token(None, cfg)
    vin = get_vin(None, cfg)
    if not token or not vin:
        print("❌ No access token/VIN configured - see config.json or TESSIE_ACCESS_TOKEN.")
        return None

    cache = {"_meta": {"last_synced_to": None}, "records": {}} if reset_cache else load_cache(IDLES_CACHE_FILE)
    last_synced_to = cache["_meta"].get("last_synced_to")
    to_ts = int(time.time())
    from_ts = max(0, last_synced_to - OVERLAP_SECONDS) if last_synced_to else (to_ts - since_days * 86400)

    try:
        records = fetch_json_chunked("idles", vin, token, from_ts, to_ts)
    except RuntimeError as e:
        print(f"❌ idles fetch failed: {e}")
        return None

    new_or_changed = 0
    for r in records:
        rid = str(r.get("id")) if r.get("id") is not None else None
        if not rid:
            continue
        if cache["records"].get(rid) != r:
            new_or_changed += 1
        cache["records"][rid] = r

    print(f"🔎 idles: window {datetime.fromtimestamp(from_ts)} -> {datetime.fromtimestamp(to_ts)}")
    print(f"   Fetched {len(records)} record(s); {new_or_changed} new/changed")

    if dry_run:
        print("   (--dry-run: not written, sync point not advanced)")
        return {"fetched": len(records), "new_or_changed": new_or_changed}

    if new_or_changed == 0:
        commit_cache({"cache_file": IDLES_CACHE_FILE}, cache, to_ts)
        print("   Nothing new or changed - idles_master.csv unchanged.")
        return {"fetched": len(records), "new_or_changed": 0}

    rows = [_idle_row_from_api(r) for r in cache["records"].values()]
    added, changed, total = merge_into_master_csv(
        "idles", home, rows, IDLES_FIELDNAMES, "Idle ID",
        sort_key_fn=lambda row: int(row.get("Started At (UTC)") or 0),
    )
    commit_cache({"cache_file": IDLES_CACHE_FILE}, cache, to_ts)
    print(f"   💾 idles_master.csv: {added} added, {changed} changed, {total} total")
    return {"fetched": len(records), "new_or_changed": new_or_changed, "added": added, "changed": changed, "total": total}


def sync_tire_pressure(tessie_dir=None, since_days=35, dry_run=False, reset_cache=False):
    """tire_pressure DOES have real history (corrected 2026-09-26 - see
    fetch_tire_pressure_history()'s own comment for how an earlier pass
    here got this wrong by only testing the no-args call). Backfills
    from Tessie's own real retention window (~30-35 days - default
    since_days matches that, since asking further back doesn't return
    anything more) using the same watermark/cache incremental-sync shape
    as idles/firmware_alerts, keyed by each reading's own Timestamp
    (there's no `id` on this endpoint at all, in either response shape).
    ALSO does the current-snapshot poll every run and merges it in on
    top - that's the only way to get the *_status (low/normal) fields at
    all, since the history shape never reports them."""
    home = tessie_dir or resolve_tessie_data_home()
    if not home:
        print("❌ No Tessie data home configured - nothing to sync to.")
        return None
    cfg = load_config()
    token = get_token(None, cfg)
    vin = get_vin(None, cfg)
    if not token or not vin:
        print("❌ No access token/VIN configured - see config.json or TESSIE_ACCESS_TOKEN.")
        return None

    cache = {"_meta": {"last_synced_to": None}, "records": {}} if reset_cache else load_cache(TIRE_PRESSURE_CACHE_FILE)
    last_synced_to = cache["_meta"].get("last_synced_to")
    to_ts = int(time.time())
    from_ts = max(0, last_synced_to - OVERLAP_SECONDS) if last_synced_to else (to_ts - since_days * 86400)

    try:
        history_entries = fetch_tire_pressure_history(vin, token, from_ts, to_ts)
    except RuntimeError as e:
        print(f"❌ tire_pressure history fetch failed: {e}")
        history_entries = []
    history_rows = _pivot_tire_pressure_history(history_entries)

    current_reading = None
    status, body = api_get("tire_pressure", vin, token, params={"pressure_format": "kpa"})
    if status == 200:
        try:
            current_reading = json.loads(body)
        except Exception as e:
            print(f"❌ Could not parse current tire_pressure snapshot: {e}")
    else:
        print(f"❌ Current tire_pressure snapshot fetch failed: HTTP {status}")

    new_or_changed = 0
    for hr in history_rows:
        key = str(hr["timestamp"])
        if cache["records"].get(key) != hr:
            new_or_changed += 1
        cache["records"][key] = hr
    if current_reading:
        key = str(current_reading.get("timestamp"))
        if cache["records"].get(key) != current_reading:
            new_or_changed += 1
        cache["records"][key] = current_reading

    print(f"🔎 tire_pressure: window {datetime.fromtimestamp(from_ts)} -> {datetime.fromtimestamp(to_ts)} "
          f"(+ current snapshot)")
    print(f"   Fetched {len(history_rows)} historical reading(s); {new_or_changed} new/changed")
    if current_reading:
        print(f"   Current: FL {current_reading.get('front_left')}kPa ({current_reading.get('front_left_status')})  "
              f"FR {current_reading.get('front_right')}kPa ({current_reading.get('front_right_status')})")
        print(f"            RL {current_reading.get('rear_left')}kPa ({current_reading.get('rear_left_status')})  "
              f"RR {current_reading.get('rear_right')}kPa ({current_reading.get('rear_right_status')})")

    if dry_run:
        print("   (--dry-run: not written, sync point not advanced)")
        return {"fetched": len(history_rows), "new_or_changed": new_or_changed}

    if new_or_changed == 0:
        commit_cache({"cache_file": TIRE_PRESSURE_CACHE_FILE}, cache, to_ts)
        print("   Nothing new or changed - tire_pressure_master.csv unchanged.")
        return {"fetched": len(history_rows), "new_or_changed": 0}

    rows = [_tire_pressure_row_from_api(r) for r in cache["records"].values()]
    added, changed, total = merge_into_master_csv(
        "tire_pressure", home, rows, TIRE_PRESSURE_FIELDNAMES, "Timestamp (UTC)",
        sort_key_fn=lambda r: int(r.get("Timestamp (UTC)") or 0),
    )
    commit_cache({"cache_file": TIRE_PRESSURE_CACHE_FILE}, cache, to_ts)
    print(f"   💾 tire_pressure_master.csv: {added} added, {changed} changed, {total} total")
    return {"fetched": len(history_rows), "new_or_changed": new_or_changed, "added": added, "changed": changed, "total": total}


def sync_firmware_alerts(tessie_dir=None, since_days=180, dry_run=False, reset_cache=False):
    """firmware_alerts: a real fetchable date-range history (from/to
    supported) but with NO native `id` (confirmed live) - dedup keyed on
    the synthesized "timestamp:name" instead. Same simple single-CSV-
    plus-archive persistence as idles (TODO-011)."""
    home = tessie_dir or resolve_tessie_data_home()
    if not home:
        print("❌ No Tessie data home configured - nothing to sync to.")
        return None
    cfg = load_config()
    token = get_token(None, cfg)
    vin = get_vin(None, cfg)
    if not token or not vin:
        print("❌ No access token/VIN configured - see config.json or TESSIE_ACCESS_TOKEN.")
        return None

    cache = {"_meta": {"last_synced_to": None}, "records": {}} if reset_cache else load_cache(FIRMWARE_ALERTS_CACHE_FILE)
    last_synced_to = cache["_meta"].get("last_synced_to")
    to_ts = int(time.time())
    from_ts = max(0, last_synced_to - OVERLAP_SECONDS) if last_synced_to else (to_ts - since_days * 86400)

    try:
        records = fetch_json_chunked("firmware_alerts", vin, token, from_ts, to_ts, key_fn=_firmware_alert_key)
    except RuntimeError as e:
        print(f"❌ firmware_alerts fetch failed: {e}")
        return None

    new_or_changed = 0
    for r in records:
        key = _firmware_alert_key(r)
        if cache["records"].get(key) != r:
            new_or_changed += 1
        cache["records"][key] = r

    print(f"🔎 firmware_alerts: window {datetime.fromtimestamp(from_ts)} -> {datetime.fromtimestamp(to_ts)}")
    print(f"   Fetched {len(records)} record(s); {new_or_changed} new/changed")

    if dry_run:
        print("   (--dry-run: not written, sync point not advanced)")
        return {"fetched": len(records), "new_or_changed": new_or_changed}

    if new_or_changed == 0:
        commit_cache({"cache_file": FIRMWARE_ALERTS_CACHE_FILE}, cache, to_ts)
        print("   Nothing new or changed - firmware_alerts_master.csv unchanged.")
        return {"fetched": len(records), "new_or_changed": 0}

    rows = [_firmware_alert_row_from_api(r) for r in cache["records"].values()]
    added, changed, total = merge_into_master_csv(
        "firmware_alerts", home, rows, FIRMWARE_ALERTS_FIELDNAMES, "Alert Key",
        sort_key_fn=lambda row: int(row.get("Timestamp (UTC)") or 0),
    )
    commit_cache({"cache_file": FIRMWARE_ALERTS_CACHE_FILE}, cache, to_ts)
    print(f"   💾 firmware_alerts_master.csv: {added} added, {changed} changed, {total} total")
    return {"fetched": len(records), "new_or_changed": new_or_changed, "added": added, "changed": changed, "total": total}


def sync_battery_health(tessie_dir=None, since_days=1095):
    """Pulls the vehicle's FULL real battery_health history straight from
    Tessie's API (fetch_battery_health_chunked(), from `since_days` ago
    to now - default 1095/~3 years, comfortably covering "since delivery"
    for any car without needing an exact date) PLUS one current-moment
    snapshot (the plain, no-from/to GET this function used to be limited
    to), and merges everything into
    <tessie data home>/battery_health_log.json, deduplicated by
    timestamp. This replaces relying on manually-exported CSVs
    (analysis_car_efficiency.py's load_battery_health_history() still
    merges any of those it finds too, but only as a fallback/supplement -
    the API pull here is the real, primary source per the user's own
    request: "I wanted you to pull down full battery_health from tessie
    yourself... tessie api scripts are meant to pull down the full
    version" (2026-09-08), after an earlier version of this function only
    ever logged one live snapshot per run).

    Note (2026-09-08): every earlier call here omitted
    DEFAULT_UNIT_PARAMS entirely (unlike the drives/charges sync
    functions, which always include it) - Tessie's API defaults to MILES
    when distance_format isn't specified, so max_range_km/max_ideal_range_km
    were silently being logged in miles under a "_km" name. Confirmed by
    the numbers themselves: the first live sync recorded max_range 350.22
    (labeled km) - 350.22 mi = 563.6 km, matching the ~564.67km "Max
    Range" the user's own Tessie battery-health CSV export/chart showed
    for the same car at essentially the same time almost exactly. Every
    reading logged from now on explicitly requests distance_format=km."""
    home = tessie_dir or resolve_tessie_data_home()
    if not home:
        print("❌ No Tessie data home configured - nothing to log to.")
        return None

    cfg = load_config()
    token = get_token(None, cfg)
    vin = get_vin(None, cfg)
    if not token or not vin:
        print("❌ No access token/VIN configured - see config.json or TESSIE_ACCESS_TOKEN.")
        return None

    to_ts = int(time.time())
    from_ts = to_ts - since_days * 86400

    try:
        history_records = fetch_battery_health_chunked(vin, token, from_ts, to_ts)
    except RuntimeError as e:
        print(f"❌ battery_health history fetch failed: {e}")
        history_records = []

    params = dict(DEFAULT_UNIT_PARAMS)
    status, body = api_get("battery_health", vin, token, params)
    current_reading = None
    if status == 200:
        try:
            current_reading = json.loads(body).get("result", {})
        except Exception as e:
            print(f"❌ Could not parse current battery_health snapshot: {e}")
    else:
        print(f"❌ Current battery_health snapshot fetch failed: HTTP {status}")

    log_path = os.path.join(home, "battery_health_log.json")
    log = []
    if os.path.isfile(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                log = json.load(f)
            if not isinstance(log, list):
                log = []
        except Exception:
            log = []

    merged = {e["synced_at"]: e for e in log if isinstance(e, dict) and e.get("synced_at")}
    for r in history_records:
        entry = _battery_health_reading_from_api(r)
        merged[entry["synced_at"]] = entry
    if current_reading:
        entry = _battery_health_reading_from_api(current_reading)
        merged[entry["synced_at"]] = entry

    if not merged:
        print("❌ Nothing fetched (both the history pull and the current snapshot failed) - nothing logged.")
        return None

    log = sorted(merged.values(), key=lambda e: e["synced_at"])
    log = detect_battery_health_outliers(log)

    tmp = log_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)
    os.replace(tmp, log_path)

    latest_valid = next((e for e in reversed(log) if not e.get("is_outlier")), log[-1])
    outlier_count = sum(1 for e in log if e.get("is_outlier"))
    print(f"✅ Battery health: {len(history_records)} historical reading(s) pulled from Tessie's API "
          f"(since {datetime.fromtimestamp(from_ts).date()}), plus current snapshot.")
    print(f"   Latest: {latest_valid['capacity_kwh']} kWh usable capacity, {latest_valid['max_range_km']} km max range "
          f"(at current charge - NOT a rated figure), {latest_valid['max_ideal_range_km']} km max ideal range.")
    if latest_valid.get("health_percent") is not None or latest_valid.get("degradation_percent") is not None:
        print(f"   Health: {latest_valid['health_percent']}% / Degradation: {latest_valid['degradation_percent']}%")
    if outlier_count > 0:
        print(f"   Outliers flagged: {outlier_count} reading(s) excluded from degradation baselines.")
    print(f"   Logged to {log_path} ({len(log)} reading(s) total, deduplicated by timestamp).")
    return latest_valid


def backfill_deepdive_telemetry(kinds, limit=None, pace_seconds=1.5, since_days=None, tessie_dir=None):
    """Bulk-fetch deep-dive telemetry for every past drive and/or charging
    session that doesn't already have a local drive_deepdive_*.csv /
    charge_deepdive_*.csv, until the local store is caught up with what
    Tessie's own history actually has. `kinds` is a list containing
    "drives", "charges", or both.

    This lives HERE, not as a flag on analyse_drives.py's or
    analyse_charging.py's own CLI, on purpose: pulling data from
    Tessie's API in bulk is exactly the "sync" shape of work this whole
    module exists for (drives/charges fetch, above), not analysis or
    display. Each analyzer script keeps exactly one narrow, pre-existing
    exception to that split - a single-record, ON-DEMAND fetch behind an
    interactive view (drives: get_drive_deepdive(); charges:
    inspect_session() does not yet auto-fetch, only drives has this today)
    - a fundamentally different shape of operation (tied to what's on
    screen right now, one record at a time) from a bulk catch-up job over
    the whole history.

    This function is the one deliberate exception to this module's own
    "never import the analyzer classes, only shell out to them for
    --consolidate" rule stated in the module docstring above: it needs
    each analyzer's own record-matching logic (match_detailed_drive()/
    match_detailed_charge(), _drive_is_in_progress()/_charge_is_in_progress())
    to know what's actually missing, and its already-shipped
    fetch_drive_deepdive_from_api()/fetch_charge_deepdive_from_api() to do
    the real pull - reimplementing either here would just be a second,
    drifting copy of the same logic. This is a READ/MATCH-only
    dependency; the --consolidate path above is completely unchanged and
    still always shells out as a subprocess, never importing anything.

    Returns {kind: (fetched, skipped_in_progress, already_had, failed)}.
    """
    sys.path.insert(0, TOOLS_DIR)  # REQ-072 fix: these analyzer modules live in Tools/, not Tools/lib/
    results = {}
    if "drives" in kinds:
        results["drives"] = _backfill_drive_deepdive(limit, pace_seconds, since_days, tessie_dir)
    if "charges" in kinds:
        results["charges"] = _backfill_charge_deepdive(limit, pace_seconds, since_days, tessie_dir)
    return results


def _since_cutoff(since_days):
    if since_days is None:
        return None
    from datetime import timedelta
    return datetime.now() - timedelta(days=since_days)


def _backfill_drive_deepdive(limit, pace_seconds, since_days, tessie_dir):
    from analyse_drives import DriveAnalyzer  # noqa: E402 (deliberate - see backfill_deepdive_telemetry docstring)

    print("   Looking for drives without deep-dive telemetry...")
    analyzer = DriveAnalyzer(tessie_dir=tessie_dir)
    drives = analyzer.load_drives()
    if not drives:
        print("   No local drives_master.csv found - nothing to backfill (run a normal --drives sync first).")
        return 0, 0, 0, 0
    analyzer.load_detailed_drives()
    since_dt = _since_cutoff(since_days)

    fetched, skipped_in_progress, already_had, failed = 0, 0, 0, 0
    total_candidates = 0
    todo = []
    for d in drives:
        if since_dt and d["start_dt"] < since_dt:
            continue
        if analyzer.match_detailed_drive(d) or analyzer.has_local_deepdive(d):
            already_had += 1
            continue
        total_candidates += 1
        if analyzer._drive_is_in_progress(d):
            skipped_in_progress += 1
            continue
        if limit is not None and len(todo) >= limit:
            break
        todo.append(d)

    if todo:
        from lib.progress import Bar
        bar = Bar(len(todo), "   Drive telemetry")
        analyzer.quiet_fetch = True
        for i, d in enumerate(todo, 1):
            label = f"{d['start_dt'].strftime('%Y-%m-%d %H:%M')} -> {d['end_dt'].strftime('%H:%M')} ({d['dist_km']:.1f} km)"
            bar.update(i - 1, label)
            path = analyzer.fetch_drive_deepdive_from_api(d)
            if path:
                fetched += 1
                # Keep detailed_drives in sync so match_detailed_drive() doesn't re-fetch the same drive later in this run - parse just
                # the ONE new file and append it rather than re-running the full load_detailed_drives() rescan after every fetch
                parsed = analyzer.parse_detailed_drive_csv(path)
                if parsed:
                    analyzer.detailed_drives.append(parsed)
            else:
                failed += 1
                bar.note(f"   could not fetch the telemetry of {label}")
            if pace_seconds:
                time.sleep(pace_seconds)
        bar.update(len(todo), "")
        bar.done()

    since_note = f" starting {since_dt.date()}" if since_dt else ""
    print(f"   Checked {len(drives)} total drive(s) in drives_master.csv{since_note}.")
    print(f"   Drives: {fetched} fetched, {failed} failed, "
          f"{skipped_in_progress} in-progress (skipped), {already_had} already had local telemetry.")
    remaining = total_candidates - fetched - skipped_in_progress - failed
    if limit is not None and remaining > 0:
        print(f"   ({remaining} more drive(s) still missing telemetry - re-run to continue, or raise --limit.)")
    return fetched, skipped_in_progress, already_had, failed


def _backfill_charge_deepdive(limit, pace_seconds, since_days, tessie_dir):
    from analyse_charging import ChargingAnalyzer  # noqa: E402 (deliberate - see backfill_deepdive_telemetry docstring)

    print("   Looking for charges without deep-dive telemetry...")
    analyzer = ChargingAnalyzer(tessie_dir=tessie_dir)
    charges = analyzer.load_charges()
    if not charges:
        print("   No local charges_master.csv found - nothing to backfill (run a normal --charges sync first).")
        return 0, 0, 0, 0
    analyzer.load_detailed_charges()
    since_dt = _since_cutoff(since_days)

    fetched, skipped_in_progress, already_had, failed = 0, 0, 0, 0
    skipped_invoice_only = 0
    total_candidates = 0

    for s in charges:
        if since_dt and s.get("started_at") and s["started_at"] < since_dt:
            continue
        if analyzer.match_detailed_charge(s):
            already_had += 1
            continue
        if getattr(analyzer, "is_invoice_only", lambda c: c.get("duration_mins", 0) <= 0 and c.get("odometer_km", 0) <= 0)(s):
            skipped_invoice_only += 1
            continue
 # AGENTS.md REQ-050: home Evnex sessions are no longer skipped here
 # - fetch_charge_deepdive_from_api() now covers them too (Tessie
 # polls the car during ANY plugged-in session, home included).
        total_candidates += 1
        if analyzer._charge_is_in_progress(s):
            skipped_in_progress += 1
            continue
        if limit is not None and fetched >= limit:
            break

        place = s.get("place_name") or s.get("location_raw") or "?"
        start_label = s["started_at"].strftime("%Y-%m-%d %H:%M") if s.get("started_at") else "?"
        label = f"{start_label} - {place} ({s.get('network', '?')})"
        print(f"   [{fetched + failed + 1}] {label}")
        path = analyzer.fetch_charge_deepdive_from_api(s)
        if path:
            fetched += 1
            parsed = analyzer.parse_detailed_charge_csv(path)
            if parsed:
                analyzer.detailed_charges.append(parsed)
        else:
            failed += 1
        if pace_seconds:
            time.sleep(pace_seconds)

    since_note = f" starting {since_dt.date()}" if since_dt else ""
    print(f"   Checked {len(charges)} total charging session(s) in charges_master.csv{since_note}.")
    inv_note = f", {skipped_invoice_only} invoice-only (no telemetry)" if skipped_invoice_only else ""
    print(f"   Charges: {fetched} fetched, {failed} failed, {skipped_in_progress} in-progress (skipped), "
          f"{already_had} already had local telemetry{inv_note}.")
    remaining = total_candidates - fetched - skipped_in_progress - failed
    if limit is not None and remaining > 0:
        print(f"   ({remaining} more session(s) still missing telemetry - re-run to continue, or raise --limit.)")
    return fetched, skipped_in_progress, already_had, failed


def run_consolidate(type_name, runner=subprocess.run):
    """Shell out to the existing analyzer script's own --consolidate flag -
    the exact same command a user would type by hand. Returns True on a
    clean exit, False otherwise (stdout/stderr are inherited, not captured,
    so the analyzer script's own output prints normally)."""
    script = CONSOLIDATE_SCRIPTS[type_name]
    cmd = [sys.executable, script, "--consolidate"]
    print(f"  \u25b6 Running: {' '.join(cmd)}")
    result = runner(cmd)
    return result.returncode == 0


def should_consolidate(pending_types, auto_yes, is_tty, input_fn=input):
    """Decide whether to proceed with consolidation for the types that got
    new data. Returns (proceed: bool, reason: str) - reason explains a
    False decision so it can be printed. Automation (non-interactive, no
    --yes) never consolidates on its own - it must opt in explicitly."""
    if not pending_types:
        return False, None
    if auto_yes:
        return True, None
    if not is_tty:
        return False, "non-interactive session and --yes not given - skipping consolidate; run it manually or pass --yes"
    prompt = f"Run --consolidate now for {', '.join(pending_types)}? [y/N] "
    answer = input_fn(prompt).strip().lower()
    if answer in ("y", "yes"):
        return True, None
    return False, "skipped - CSV(s) written but not merged. Re-run with --yes, or run each analyzer's own --consolidate manually, whenever you're ready."


def main():
    parser = argparse.ArgumentParser(
        usage="""sync_api_tessie.py [--drives | --charges] [--since-days N] [--yes] [--dry-run]
                       [--reset-cache] [--token TOKEN] [--vin VIN] [--landing-dir DIR]
   or:  sync_api_tessie.py --consolidate [--drives | --charges]
   or:  sync_api_tessie.py --backfill-deepdive [--drives | --charges] [--limit N] [--pace SECONDS]
   or:  sync_api_tessie.py --battery-health [--battery-health-since-days N]
   or:  sync_api_tessie.py [--idles] [--tire-pressure] [--firmware-alerts] [--dry-run] [--reset-cache]
""",
        description="""
NAME
    sync_api_tessie.py - Tessie Developer API Drive/Charge/Battery-Health Sync

DESCRIPTION
    Fetch Tessie drives/charges via the Developer API, then run the existing
    --consolidate flows for you (with a confirmation prompt unless --yes is given).
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES
  sync_api_tessie.py --drives --since-days 30
  sync_api_tessie.py --charges --yes
  sync_api_tessie.py --consolidate --drives
  sync_api_tessie.py --backfill-deepdive --limit 50 --pace 2
  sync_api_tessie.py --battery-health
  sync_api_tessie.py --idles --tire-pressure --firmware-alerts
"""
    )

    g_select = parser.add_argument_group("Selection & Auth")
    g_select.add_argument("--drives", action="store_true", help="Sync drives only")
    g_select.add_argument("--charges", action="store_true", help="Sync charges only")
    g_select.add_argument("--token", help="Tessie API access token (overrides env var / config.json)")
    g_select.add_argument("--vin", help="Vehicle VIN (overrides config.json's 'vin')")

    g_sync = parser.add_argument_group("Sync Behavior")
    g_sync.add_argument("--since-days", type=int, default=None,
                         help="First-run backfill window in days (default: 90; ignored once a type has a prior sync point unless --reset-cache is also passed)")
    g_sync.add_argument("--landing-dir", help="Where to write the CSV (default: config.json's 'landing_directory')")
    g_sync.add_argument("--dry-run", action="store_true", help="Fetch and report only - write nothing to disk")
    g_sync.add_argument("--reset-cache", action="store_true",
                         help="Forget the prior sync point/cache for the selected type(s) and do a fresh --since-days backfill")
    g_sync.add_argument("--yes", "-y", action="store_true",
                         help="Consolidate automatically without prompting (needed for unattended/cron use)")
    g_sync.add_argument("--no-consolidate", action="store_true",
                         help="Fetch only - never run --consolidate, even interactively")
    g_sync.add_argument("--consolidate", action="store_true",
                         help="Skip fetching entirely and just run --consolidate directly on each "
                              "selected analyzer script (drives and/or charges), merging whatever "
                              "CSV(s) are already sitting in the landing directory - e.g. to finish "
                              "consolidating from a previous sync without doing a new fetch. No "
                              "token/VIN needed for this mode.")

    g_deepdive = parser.add_argument_group("Deep-Dive Telemetry Backfill")
    g_deepdive.add_argument("--backfill-deepdive", action="store_true",
                         help="Bulk-fetch deep-dive telemetry from Tessie's /states API for every past "
                              "drive and/or charging session that doesn't have a local "
                              "drive_deepdive_*.csv / charge_deepdive_*.csv yet (uses the same "
                              "--drives/--charges selectors as the sync above - both, if neither is "
                              "given). Separate from the regular sync - re-run as many times as needed, "
                              "it only ever fetches what's still missing.")
    g_deepdive.add_argument("--limit", type=int, default=25,
                         help="Max NEW deep-dive fetches per domain for --backfill-deepdive in this run (default 25 - re-run to continue). Use 0 for no cap.")
    g_deepdive.add_argument("--pace", type=float, default=1.5,
                         help="Seconds to wait between --backfill-deepdive API calls (default 1.5)")
    g_deepdive.add_argument("--tessie-dir", help="Custom path to directory containing Tessie CSV exports (--backfill-deepdive only)")

    g_battery = parser.add_argument_group("Battery Health")
    g_battery.add_argument("--battery-health", action="store_true",
                         help="Pull the vehicle's full battery health history from Tessie's API (default "
                              "3 years back) plus a current snapshot, and merge everything into "
                              "battery_health_log.json, deduplicated by timestamp.")
    g_battery.add_argument("--battery-health-since-days", type=int, default=1095,
                         help="How far back to pull real battery_health history on --battery-health "
                              "(default: 1095 / ~3 years).")

    g_simple = parser.add_argument_group("Idles / Tire Pressure / Firmware Alerts (TODO-011)")
    g_simple.add_argument("--idles", action="store_true",
                         help="Sync idle events (parked, not charging) into idles_master.csv "
                              "(simple single-CSV-plus-archive format, incremental like drives/charges).")
    g_simple.add_argument("--idles-since-days", type=int, default=90,
                         help="First-run backfill window in days for --idles (default: 90).")
    g_simple.add_argument("--tire-pressure", action="store_true",
                         help="Sync tire pressure history (Tessie retains ~30-35 days of it, "
                              "regardless of how far back is asked) plus the current live snapshot "
                              "(the only source of the low/normal status fields) into "
                              "tire_pressure_master.csv.")
    g_simple.add_argument("--tire-pressure-since-days", type=int, default=35,
                         help="First-run backfill window in days for --tire-pressure (default: 35 - "
                              "beyond Tessie's own real retention window for this endpoint, so a "
                              "larger value won't return more).")
    g_simple.add_argument("--firmware-alerts", action="store_true",
                         help="Sync vehicle firmware alert history into firmware_alerts_master.csv "
                              "(simple single-CSV-plus-archive format, incremental like --idles).")
    g_simple.add_argument("--firmware-alerts-since-days", type=int, default=180,
                         help="First-run backfill window in days for --firmware-alerts (default: 180).")
    args = parser.parse_args()

    if args.battery_health:
        sync_battery_health(tessie_dir=args.tessie_dir, since_days=args.battery_health_since_days)
        return

    if args.idles or args.tire_pressure or args.firmware_alerts:
 # Same isolated-flag shape as --battery-health above (TODO-011): a
 # lightweight fetch+merge with no --consolidate subprocess/prompt to
 # coordinate, so these can run standalone or combined with each
 # other, but not mixed into the same invocation as --drives/--charges.
        any_failed = False
        if args.idles:
            result = sync_idles(tessie_dir=args.tessie_dir, since_days=args.idles_since_days,
                                 dry_run=args.dry_run, reset_cache=args.reset_cache)
            if result is None:
                any_failed = True
            print()
        if args.tire_pressure:
            result = sync_tire_pressure(tessie_dir=args.tessie_dir, since_days=args.tire_pressure_since_days,
                                         dry_run=args.dry_run, reset_cache=args.reset_cache)
            if result is None:
                any_failed = True
            print()
        if args.firmware_alerts:
            result = sync_firmware_alerts(tessie_dir=args.tessie_dir, since_days=args.firmware_alerts_since_days,
                                           dry_run=args.dry_run, reset_cache=args.reset_cache)
            if result is None:
                any_failed = True
            print()
        sys.exit(1 if any_failed else 0)

    if args.consolidate and args.no_consolidate:
        print("❌ --consolidate and --no-consolidate are mutually exclusive.")
        sys.exit(2)
    if args.consolidate and args.dry_run:
        print("❌ --consolidate runs the real --consolidate subprocess directly - it can't be combined with --dry-run.")
        sys.exit(2)

    selected = []
    if args.drives:
        selected.append("drives")
    if args.charges:
        selected.append("charges")
    if not selected:
        selected = list(DATA_TYPES.keys())  # "all things are fetched" when neither flag is given

    if args.backfill_deepdive:
 # Reuses the same --drives/--charges selectors as the regular sync
 # above (both, when neither is given) - "which drives and which
 # charges were pulled" should be exactly as explicit here as it is
 # for a normal sync run.
        limit = None if args.limit == 0 else args.limit
        backfill_deepdive_telemetry(selected, limit=limit, pace_seconds=args.pace, since_days=args.since_days, tessie_dir=args.tessie_dir)
        return

    config = load_config()

    if args.consolidate:
 # Direct mode: no API call at all, so no token/VIN needed - just run
 # each selected analyzer's own --consolidate, exactly as if you'd
 # typed e.g. `analyse_drives.py --consolidate` yourself.
        print(f"\u25b6 --consolidate: skipping fetch, running --consolidate directly for: {', '.join(selected)}\n")
        any_failed = False
        for type_name in selected:
            ok = run_consolidate(type_name)
            if not ok:
                print(f"  ❌ {type_name} --consolidate failed - see output above")
                any_failed = True
        sys.exit(1 if any_failed else 0)

    token = get_token(args.token, config)
    vin = get_vin(args.vin, config)
    landing_dir = args.landing_dir or os.path.expanduser(config.get("directories", {}).get("landing") or "~/Downloads")

    if not token:
        print("❌ No access token found. Provide one via --token, the TESSIE_ACCESS_TOKEN")
        print('   environment variable, or a "tessie_access_token" key in config.json.')
        print("   Generate one at: https://dash.tessie.com/settings/api")
        sys.exit(1)
    if not vin:
        print("❌ No VIN found. Provide one via --vin or ensure config.json has a \"vin\" key.")
        sys.exit(1)

    print(f"\U0001f50e Syncing Tessie API data for VIN {'*' * max(len(vin) - 6, 0)}{vin[-6:]}, token {'*' * max(len(token) - 3, 0)}{token[-3:]}")
    if args.dry_run:
        print("   (--dry-run: fetching and reporting only, writing nothing)")
    print()

    any_failed = False
    results = {}
    for type_name in selected:
        spec = DATA_TYPES[type_name]
        since_days = args.since_days if args.since_days is not None else spec["default_since_days"]
        try:
            result = sync_one_type(type_name, spec, token, vin, since_days, args.dry_run, landing_dir,
                                    reset_cache=args.reset_cache)
        except Exception as e:
            print(f"  ❌ {type_name}: {e}")
            any_failed = True
            continue
        results[type_name] = result

        from_ts, to_ts = result["window"]
        print(f"  ✔ {type_name}: window {datetime.fromtimestamp(from_ts)} -> {datetime.fromtimestamp(to_ts)}")
        print(f"     Fetched {result['fetched']} record(s); {result['new_or_changed']} new/changed")
        print(f"     Cache: {result['cache_total_before']} -> {result['cache_total_after']} total record(s)")
        if result["csv_warning"]:
            print(f"     ⚠️  {result['csv_warning']}")
        if result["csv_path"]:
            print(f"     \U0001f4be Wrote {result['csv_path']}")
        elif result["fetched"] == 0:
            print(f"     Nothing in this window - no CSV written.")
        elif result["new_or_changed"] == 0:
            print(f"     Nothing new or changed since last sync - no CSV written.")
        elif args.dry_run:
            print(f"     (--dry-run: CSV not written)")
        print()

    pending = [t for t in selected if results.get(t) and results[t]["csv_path"] and not args.no_consolidate]
    if args.no_consolidate and any(results.get(t) and results[t]["csv_path"] for t in selected):
        print("--no-consolidate passed - CSV(s) written above but not merged. Run each analyzer's own --consolidate when ready.")
        print("(Sync point not advanced for these - re-running will safely re-fetch the same window until you do.)\n")
    elif pending:
        proceed, reason = should_consolidate(pending, args.yes, sys.stdin.isatty())
        if proceed:
            print()
            for type_name in pending:
                ok = run_consolidate(type_name)
                if ok:
                    pc = results[type_name]["pending_commit"]
                    if pc:
                        commit_cache(pc["spec"], pc["cache"], pc["to_ts"])
                else:
                    print(f"  ❌ {type_name} --consolidate failed - see output above")
                    print(f"     Sync point not advanced for {type_name} - nothing is lost; fix the issue and "
                          f"re-run --consolidate (or this sync) whenever you're ready.")
                    any_failed = True
            print()
        elif reason:
            print(reason + "\n")
            print("(Sync point not advanced for these - re-running will safely re-fetch the same window until you consolidate.)\n")

    sys.exit(1 if any_failed else 0)


if __name__ == "__main__":
    main()
