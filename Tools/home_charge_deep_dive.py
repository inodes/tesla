#!/usr/bin/env python3
"""
home_charge_deep_dive.py - Sample home-charging sessions and pull the full
Tessie history + matching Evnex session, for manual cross-referencing
against Solaredge/AGL data while building a real AGL Solar VIP cost model.
=============================================================================
Context: `charges_master.csv`'s summary row for a home charge is one line
(energy added, duration, cost as Tessie/the car estimates it). Neither
Tessie nor Evnex alone can compute the TRUE cost of a session under AGL's
"Solar VIP" plan (flat usage rate + daily supply + tiered solar export
credit + a monthly demand/capacity charge keyed off the single highest
weekday-5-9pm 30-min import in the whole month) - that needs real grid
import/export data too. This script doesn't compute cost - it gathers
the two sides we CAN already pull automatically (Tessie's own per-second
vehicle-state history for the session, via `GET /{vin}/states`, and the
matching Evnex charging-session record) into one file per session, with
clearly-marked blank spots for Solaredge/AGL figures to be added by hand
for now (both APIs are a later step - see AGENTS.md REQ-026/onwards).

Home-session detection: this dataset's non-Supercharger, non-fast-charger
charges are ALL at one address (two text variants of the same home street -
verified: 193/193 such rows share just those two Location strings) - so
"not a Supercharger and not a 3rd-party fast charger" is used as the home
filter rather than a GPS/`resolve_place()` check. Simple and correct for
this data today; would need revisiting if a non-home AC destination
charger session ever gets recorded.

Usage:
    ./home_charge_deep_dive.py --list                  # show the sample pool, most recent first
    ./home_charge_deep_dive.py --sample 3               # deep-dive the 3 most recent home sessions
    ./home_charge_deep_dive.py --rows 1,5,12            # deep-dive specific pool rows (see --list numbering)
    ./home_charge_deep_dive.py --sample 3 --pad-minutes 15
"""

import argparse
import calendar
import csv
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tessie_api_common import (  # noqa: E402
    DEFAULT_UNIT_PARAMS,
    api_get,
    extract_json_records,
    get_token,
    get_vin,
    load_config,
    resolve_tessie_data_home,
)
import tessie_timezone as tztools  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


TESSIE_DATA_HOME = resolve_tessie_data_home()
CHARGES_MASTER = os.path.join(TESSIE_DATA_HOME, "charges", "charges_master.csv")
DEFAULT_OUTPUT_DIR = os.path.join(TESSIE_DATA_HOME, "home_charge_deep_dive")


def load_home_charge_pool():
    """Every charges_master.csv row that's neither a Supercharger nor a
    3rd-party fast charger, sorted most-recent-first. See module docstring
    for why that's a clean home-session filter for this dataset.

    Returns (complete, in_progress) - a charging session can still be
    actively running at the moment this tool happens to run (or have been
    still running when the last --consolidate/sync captured it), and its
    energy/duration/cost figures aren't final yet. `in_progress` rows are
    kept out of the default pool numbering entirely so they can never be
    silently sampled - see is_session_complete()."""
    if not os.path.isfile(CHARGES_MASTER):
        raise FileNotFoundError(f"Not found: {CHARGES_MASTER} - run --consolidate first.")
    with open(CHARGES_MASTER, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    def is_home(r):
        sc = (r.get("Supercharger") or "").strip().lower()
        fc = (r.get("Fast Charger") or "").strip().lower()
        return sc != "true" and fc != "true"

    pool = [r for r in rows if is_home(r)]

    def start_key(r):
        raw = r.get("Started At (UTC)") or r.get("Started At (AEST)") or r.get("Started At") or ""
        try:
            return int(raw)
        except (TypeError, ValueError):
            try:
                return datetime.strptime(raw[:16], "%Y-%m-%d %H:%M").timestamp()
            except Exception:
                return 0

    pool.sort(key=start_key, reverse=True)

    complete = [r for r in pool if is_session_complete(r)]
    in_progress = [r for r in pool if not is_session_complete(r)]
    return complete, in_progress


def sydney_local_to_utc_epoch(local_str):
    """'YYYY-MM-DD HH:MM' Sydney wall-clock text -> true UTC epoch seconds,
    using the same reversal math as the drives UTC migration (TODO-009):
    Tessie's manual/CSV export always converts to Australia/Sydney's real
    offset for that date, so subtracting that offset recovers UTC exactly."""
    naive = datetime.strptime(local_str[:16], "%Y-%m-%d %H:%M")
    epoch_as_if_utc = calendar.timegm(naive.timetuple())
    offset = int(tztools.sydney_offset_seconds(naive))
    return epoch_as_if_utc - offset


def is_session_complete(row, now=None):
    """A row only counts as a finished session if it has a real end time
    that isn't in the future. Guards against sampling a charge that's
    still actively going right now (or was still going when the CSV was
    last synced) - its Energy Added/Duration/Cost are a snapshot, not a
    final figure, and feeding that into a tariff/cost comparison would be
    comparing a complete Evnex/Solaredge/AGL window against an incomplete
    Tessie one. See ACTIVE_EVNEX_STATUSES below for the equivalent check
    on the Evnex side - that source is explicit about it (a `sessionStatus`
    field), Tessie's own summary export isn't, so this checks the data
    itself rather than trusting a status flag that may not exist."""
    end_raw = row.get("Ended At (UTC)") or row.get("Ended At (AEST)") or row.get("Ended At")
    if not end_raw or not str(end_raw).strip():
        return False
    try:
        _, end_epoch = row_utc_window(row)
    except Exception:
        return True  # unparseable - don't block on this, just let it through
    now_epoch = int((now or datetime.now(timezone.utc)).timestamp())
    return end_epoch <= now_epoch


def row_utc_window(row):
    """(start_epoch, end_epoch) for a charges_master.csv row - prefers the
    already-migrated UTC columns (drives has these; charges doesn't yet),
    falls back to converting the legacy AEST text."""
    start_raw = row.get("Started At (UTC)")
    end_raw = row.get("Ended At (UTC)")
    if start_raw and end_raw:
        return int(start_raw), int(end_raw)
    start_txt = row.get("Started At (AEST)") or row.get("Started At")
    end_txt = row.get("Ended At (AEST)") or row.get("Ended At")
    return sydney_local_to_utc_epoch(start_txt), sydney_local_to_utc_epoch(end_txt)


def iso_to_epoch(iso_str):
    if not iso_str:
        return None
    return int(datetime.fromisoformat(iso_str.replace("Z", "+00:00")).timestamp())


def find_latest_evnex_dump():
    tessie_dir = os.path.join(REPO_ROOT, "Tessie")
    candidates = [
        f for f in os.listdir(tessie_dir)
        if f.startswith("evnex_explore_") and f.endswith(".json")
    ]
    if not candidates:
        return None
    candidates.sort()
    return os.path.join(tessie_dir, candidates[-1])


def load_evnex_sessions(evnex_dump_path):
    """Flat list of every session across every org/charge-point in an
    evnex_explore.py dump file."""
    if not evnex_dump_path or not os.path.isfile(evnex_dump_path):
        return []
    with open(evnex_dump_path, "r", encoding="utf-8") as f:
        dump = json.load(f)
    sessions = []
    for org in dump.get("organisations", []):
        for cp in org.get("charge_points", []):
            for s in (cp.get("sessions") or []):
                sessions.append(s)
    return sessions


def match_evnex_sessions(evnex_sessions, window_start, window_end):
    """Evnex sessions whose own charging window overlaps [window_start,
    window_end] (both true UTC epoch seconds). Returns (completed,
    still_active) - Evnex is explicit about this via `sessionStatus`
    ("Active" sessions have `chargingStopped: null` - their energy/cost
    figures are still climbing), unlike Tessie's own summary export (see
    is_session_complete() for that side's equivalent check). A still-active
    match is kept out of `completed` so it's never silently treated as
    finished data."""
    completed, still_active = [], []
    for s in evnex_sessions:
        a = s.get("attributes", {})
        s_start = iso_to_epoch(a.get("chargingStarted") or a.get("startDate"))
        s_end = iso_to_epoch(a.get("chargingStopped") or a.get("endDate")) or s_start
        if s_start is None:
            continue
        if s_start <= window_end and s_end >= window_start:
            if a.get("sessionStatus") == "Active" or not a.get("chargingStopped"):
                still_active.append(s)
            else:
                completed.append(s)
    return completed, still_active


def fetch_tessie_states(vin, token, from_ts, to_ts, interval):
    params = dict(DEFAULT_UNIT_PARAMS)
    params.update({"from": from_ts, "to": to_ts, "interval": interval, "condense": "false"})
    status, body = api_get("states", vin, token, params)
    if status is None:
        return None, f"connection error: {body.decode('utf-8', 'replace')}"
    if status != 200:
        return None, f"HTTP {status}: {body[:500].decode('utf-8', 'replace')}"
    try:
        return extract_json_records(body), None
    except ValueError as e:
        return None, str(e)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--token", help="Tessie API access token (overrides env var / config.json)")
    parser.add_argument("--vin", help="Vehicle VIN (overrides Tessie/config.json's 'vin')")
    parser.add_argument("--list", action="store_true", help="List the home-session pool (most recent first) and exit")
    parser.add_argument("--sample", type=int, default=3, help="Deep-dive the N most recent home sessions (default: 3, ignored if --rows given)")
    parser.add_argument("--rows", help="Comma-separated 1-based pool indices to deep-dive instead of --sample (see --list)")
    parser.add_argument("--pad-minutes", type=int, default=10, help="Minutes of padding before/after the session window for the Tessie states pull (default: 10)")
    parser.add_argument("--interval", type=int, default=1, help="Tessie /states sampling interval in seconds, 1 = finest available (default: 1)")
    parser.add_argument("--evnex-file", help="Path to a specific evnex_explore.py JSON dump (default: the most recent Tessie/evnex_explore_*.json)")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help=f"Where to write per-session JSON bundles (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--include-in-progress", action="store_true", help="Also list/sample sessions with no confirmed end time yet (excluded by default - see module docstring)")
    args = parser.parse_args()

    pool, in_progress_pool = load_home_charge_pool()
    if args.include_in_progress:
        pool = in_progress_pool + pool  # newest-first still holds since in_progress is always the newest
        in_progress_pool = []

    if args.list:
        if in_progress_pool:
            print(f"({len(in_progress_pool)} session(s) still in progress / no confirmed end time - excluded below, re-run with --include-in-progress to see them)\n")
        print(f"{'#':>3}  {'Started (local text)':<20} {'Duration':>9} {'Energy Added':>13}")
        for i, r in enumerate(pool, start=1):
            started = r.get("Started At (Local)") or r.get("Started At (AEST)") or r.get("Started At") or "?"
            dur = r.get("Duration (Minutes)", "?")
            energy = r.get("Energy Added (kWh)", "?")
            print(f"{i:>3}  {started:<20} {dur:>7}min {energy:>11}kWh")
        return 0

    if in_progress_pool:
        print(f"Note: {len(in_progress_pool)} session(s) excluded as still in progress / no confirmed end time (--include-in-progress to override).")

    config = load_config()
    token = get_token(args.token, config)
    vin = get_vin(args.vin, config)
    if not token or not vin:
        print("Error: missing Tessie access token and/or VIN. Set in Tessie/config.json or pass --token/--vin.", file=sys.stderr)
        return 1

    if args.rows:
        try:
            indices = [int(x.strip()) for x in args.rows.split(",") if x.strip()]
        except ValueError:
            print("Error: --rows must be comma-separated integers, e.g. --rows 1,5,12", file=sys.stderr)
            return 1
        selected = []
        for i in indices:
            if i < 1 or i > len(pool):
                print(f"Warning: row {i} out of range (pool has {len(pool)} sessions) - skipped")
                continue
            selected.append((i, pool[i - 1]))
    else:
        n = max(0, min(args.sample, len(pool)))
        selected = list(enumerate(pool[:n], start=1))

    if not selected:
        print("No sessions selected - nothing to do.")
        return 0

    evnex_dump_path = args.evnex_file or find_latest_evnex_dump()
    evnex_sessions = load_evnex_sessions(evnex_dump_path)
    if evnex_dump_path:
        print(f"Using Evnex dump: {evnex_dump_path} ({len(evnex_sessions)} session(s) loaded)")
    else:
        print("No evnex_explore.py dump found - run ./Tools/evnex_explore.py first for Evnex cross-referencing. Continuing with Tessie-only data.")

    os.makedirs(args.output_dir, exist_ok=True)
    pad = args.pad_minutes * 60

    for idx, row in selected:
        try:
            start_epoch, end_epoch = row_utc_window(row)
        except Exception as e:
            print(f"[{idx}] skipped - couldn't determine session window: {e}")
            continue

        label = row.get("Started At (Local)") or row.get("Started At (AEST)") or row.get("Started At") or f"row{idx}"
        print(f"\n[{idx}] {label} - fetching Tessie states + matching Evnex...")

        states, err = fetch_tessie_states(vin, token, start_epoch - pad, end_epoch + pad, args.interval)
        if err:
            print(f"    Tessie /states FAILED: {err}")
            states = []
        else:
            print(f"    Tessie /states: {len(states)} sample(s)")

        evnex_matches, evnex_still_active = match_evnex_sessions(evnex_sessions, start_epoch, end_epoch)
        print(f"    Evnex matches: {len(evnex_matches)} completed" + (f", {len(evnex_still_active)} STILL ACTIVE (excluded from evnex_matches, see evnex_matches_in_progress)" if evnex_still_active else ""))

        bundle = {
            "pool_index": idx,
            "tessie_summary_row": row,
            "window_utc": {
                "start_epoch": start_epoch,
                "end_epoch": end_epoch,
                "start_iso": datetime.fromtimestamp(start_epoch, tz=timezone.utc).isoformat(),
                "end_iso": datetime.fromtimestamp(end_epoch, tz=timezone.utc).isoformat(),
                "pad_minutes": args.pad_minutes,
            },
            "tessie_states": states,
            "evnex_matches": evnex_matches,
            "evnex_matches_in_progress": evnex_still_active,  # still charging - energy/cost not final, don't use for tariff comparison
            "solaredge": None,  # TODO(manual): paste Solaredge generation/export data for this window here
            "agl": None,        # TODO(manual): paste AGL interval/meter data for this window here
        }

        safe_name = label.replace(" ", "_").replace(":", "-")
        out_path = os.path.join(args.output_dir, f"session_{idx:02d}_{safe_name}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(bundle, f, indent=2, default=str)
        print(f"    Written: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
