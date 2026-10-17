#!/usr/bin/env python3
"""
tessie_api_sync.py - Fetch Tessie drives & charges via the Developer API
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
archiving - tessie_drives_analyzer.py and tessie_charging_analyzer.py don't
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
    testing. Using Tessie's own CSV formatting avoids that class of bug
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
silently excluded from every future sync - see BUG-019 in
Tessie/TESSIE_API.md.)

Usage:
    export TESSIE_ACCESS_TOKEN=your_token_here
    ./tessie_api_sync.py                       # fetch + prompt to consolidate, both types
    ./tessie_api_sync.py --yes                  # fetch + consolidate without prompting (cron)
    ./tessie_api_sync.py --no-consolidate        # fetch only, leave CSV(s) for you to merge later
    ./tessie_api_sync.py --drives                # just drives
    ./tessie_api_sync.py --since-days 180        # first-run backfill window
    ./tessie_api_sync.py --dry-run               # fetch and report, write/consolidate nothing
    ./tessie_api_sync.py --reset-cache --charges  # forget prior sync point, re-backfill

See Tessie/TESSIE_API.md for endpoint/field notes and known limitations.
"""

import argparse
import csv
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tessie_api_common import (
    DEFAULT_UNIT_PARAMS, REPO_ROOT, SCRIPT_DIR, api_get, extract_json_records,
    get_token, get_vin, load_config,
)

CACHE_DIR = os.path.join(REPO_ROOT, "Tessie", "api_cache")

# Which existing analyzer script owns --consolidate for each data type. This
# tool only ever shells out to these as a subprocess (same as typing the
# command yourself) - never imports their classes, so they stay fully
# decoupled from how fetching works.
CONSOLIDATE_SCRIPTS = {
    "drives": os.path.join(SCRIPT_DIR, "tessie_drives_analyzer.py"),
    "charges": os.path.join(SCRIPT_DIR, "tessie_charging_analyzer.py"),
}

# One entry per data type this tool knows how to fetch. Adding a new Tessie
# endpoint later (e.g. "idles") means adding one entry here, not a new
# script and not touching tessie_drives_analyzer.py/tessie_charging_analyzer.py.
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
    alone was the original bug (BUG-019): a CSV deleted before
    consolidation was permanently unrecoverable, because the cache already
    considered those records 'seen'."""
    cache["_meta"]["last_synced_to"] = to_ts
    cache["_meta"]["last_synced_at_wallclock"] = datetime.now().isoformat(timespec="seconds")
    save_cache(spec["cache_file"], cache)


def fetch_json_chunked(endpoint, vin, token, from_ts, to_ts, chunk_limit=500, api_get_fn=api_get):
    """Fetch every JSON record in [from_ts, to_ts), splitting the window in
    half and recursing whenever a chunk comes back exactly at chunk_limit
    (a sign there could be more we didn't see - no cursor is documented for
    either endpoint, so this is the safe way to avoid silent truncation)."""
    if to_ts <= from_ts:
        return []
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
    left = fetch_json_chunked(endpoint, vin, token, from_ts, mid, chunk_limit, api_get_fn)
    right = fetch_json_chunked(endpoint, vin, token, mid, to_ts, chunk_limit, api_get_fn)
    combined = {r["id"]: r for r in left}
    combined.update({r["id"]: r for r in right})
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
    re-fetches the same pending window (by design, since BUG-019) and so
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
        csv_params.update({"from": from_ts, "to": to_ts, "format": "csv", "limit": len(records) + 10})
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
        with open(csv_path, "wb") as f:
            f.write(csv_body)

    # Only advance the watermark immediately when there's nothing pending
    # to lose (new_or_changed == 0 - no CSV was written this run either).
    # When new_or_changed > 0, a CSV now exists in the landing directory
    # that hasn't been merged into the master file yet; committing here
    # would let a deleted/lost CSV silently vanish from all future syncs
    # (BUG-019). Instead, hand the in-memory cache back via
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
        description="Fetch Tessie drives/charges via the Developer API, then run the existing --consolidate flows for you (with a confirmation prompt)"
    )
    parser.add_argument("--drives", action="store_true", help="Sync drives only")
    parser.add_argument("--charges", action="store_true", help="Sync charges only")
    parser.add_argument("--token", help="Tessie API access token (overrides env var / config.json)")
    parser.add_argument("--vin", help="Vehicle VIN (overrides Tessie/config.json's 'vin')")
    parser.add_argument("--since-days", type=int, default=None,
                         help="First-run backfill window in days (default: 90; ignored once a type has a prior sync point unless --reset-cache is also passed)")
    parser.add_argument("--landing-dir", help="Where to write the CSV (default: Tessie/config.json's 'landing_directory')")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and report only - write nothing to disk")
    parser.add_argument("--reset-cache", action="store_true",
                         help="Forget the prior sync point/cache for the selected type(s) and do a fresh --since-days backfill")
    parser.add_argument("--yes", "-y", action="store_true",
                         help="Consolidate automatically without prompting (needed for unattended/cron use)")
    parser.add_argument("--no-consolidate", action="store_true",
                         help="Fetch only - never run --consolidate, even interactively")
    parser.add_argument("--consolidate", action="store_true",
                         help="Skip fetching entirely and just run --consolidate directly on each "
                              "selected analyzer script (drives and/or charges), merging whatever "
                              "CSV(s) are already sitting in the landing directory - e.g. to finish "
                              "consolidating from a previous sync without doing a new fetch. No "
                              "token/VIN needed for this mode.")
    args = parser.parse_args()

    if args.consolidate and args.no_consolidate:
        print("❌ --consolidate and --no-consolidate are mutually exclusive.")
        sys.exit(2)
    if args.consolidate and args.dry_run:
        print("❌ --consolidate runs the real --consolidate subprocess directly - it can't be combined with --dry-run.")
        sys.exit(2)

    config = load_config()

    selected = []
    if args.drives:
        selected.append("drives")
    if args.charges:
        selected.append("charges")
    if not selected:
        selected = list(DATA_TYPES.keys())  # "all things are fetched" when neither flag is given

    if args.consolidate:
        # Direct mode: no API call at all, so no token/VIN needed - just run
        # each selected analyzer's own --consolidate, exactly as if you'd
        # typed e.g. `tessie_drives_analyzer.py --consolidate` yourself.
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
    landing_dir = args.landing_dir or os.path.expanduser(config.get("landing_directory") or "~/Downloads")

    if not token:
        print("❌ No access token found. Provide one via --token, the TESSIE_ACCESS_TOKEN")
        print('   environment variable, or a "tessie_access_token" key in Tessie/config.json.')
        print("   Generate one at: https://dash.tessie.com/settings/api")
        sys.exit(1)
    if not vin:
        print("❌ No VIN found. Provide one via --vin or ensure Tessie/config.json has a \"vin\" key.")
        sys.exit(1)

    print(f"\U0001f50e Syncing Tessie API data for VIN ...{vin[-6:]} (token not shown)")
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
