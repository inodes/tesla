#!/usr/bin/env python3
"""
evnex_api_sync.py - Automated, idempotent sync that turns newly-
COMPLETED Evnex home-charging sessions into per-session deep-dive files
under charges/deepdive/ (see AGENTS.md REQ-030/REQ-032). Renamed from
evnex_api_downloader.py so every source-consolidation script in this
repo uses the same verb - "sync" covers both an API pull (this one,
Tessie's) and a manually-dropped-in export (AGL's) equally well.

Distinct from evnex_explore.py (a read-only, human-driven diagnostic dump
of everything Evnex can see, written into the repo's own Tessie/ folder
for manual inspection) - this script calls the Evnex API directly itself
rather than depending on a possibly-stale evnex_explore.py dump file, is
meant to run unattended, and only ever WRITES new files - it never
modifies or deletes an existing one.

Idempotency / dedup: keyed on Evnex's own session `id` (a UUID) - not on
a timestamp or filename. A filename is for humans to browse (Spotlight,
Finder); code must never trust it as the source of truth for "is this
the same session" - see AGENTS.md REQ-030 for the full reasoning (the
same principle TODO-009 already established for CSV columns, extended
to filenames). Every existing charge_deepdive_*_Evnex_Home.json file
already on disk is scanned once at startup and its own
"evnex_session_id" field collected into a seen-set before deciding
what's new.

In-progress sessions are always skipped (Evnex's own sessionStatus ==
"Active", or a still-null chargingStopped/endDate) - their energy/cost
figures aren't final yet. Re-run this again later and a since-completed
session is picked up normally, no special handling needed.

Credentials come from evnex_common.py (Tessie/config.json or
EVNEX_CLIENT_USERNAME/EVNEX_CLIENT_PASSWORD env vars) - nothing this
script prints or writes includes the Evnex username/password.

Usage:
    ./evnex_api_sync.py                # write any new completed sessions
    ./evnex_api_sync.py --dry-run       # show what would be written, write nothing
    ./evnex_api_sync.py --limit 5       # cap how many new files get written this run
    ./evnex_api_sync.py --quiet         # summary only
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evnex_common import get_authenticated_client, EvnexCredentialsError  # noqa: E402
from evnex_explore import to_jsonable  # noqa: E402
from tessie_api_common import resolve_tessie_data_home, unique_stamped_path  # noqa: E402
import tessie_timezone as tztools  # noqa: E402


def deepdive_dir():
    return os.path.join(resolve_tessie_data_home(), "charges", "deepdive")


def iso_to_epoch(iso_str):
    if not iso_str:
        return None
    try:
        return int(datetime.fromisoformat(iso_str.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def is_session_complete(attrs):
    """Same completeness rule already used by home_charge_deep_dive.py's
    match_evnex_sessions() - Evnex is explicit about an in-progress session
    via sessionStatus/chargingStopped, so trust that rather than guessing
    from timestamps."""
    return attrs.get("sessionStatus") != "Active" and bool(attrs.get("chargingStopped"))


def load_seen_session_ids(ddir):
    """Every Evnex session id already saved as a deep-dive file, read from
    the files' OWN content - never from filenames (see module docstring).
    A file that's unreadable/not-yet-ours is skipped, not treated as an
    error - this is a discovery scan, not a validation pass."""
    seen = set()
    if not os.path.isdir(ddir):
        return seen
    for fname in os.listdir(ddir):
        if not (fname.startswith("charge_deepdive_") and fname.endswith("_Evnex_Home.json")):
            continue
        try:
            with open(os.path.join(ddir, fname), "r", encoding="utf-8") as f:
                data = json.load(f)
            sid = data.get("evnex_session_id")
            if sid:
                seen.add(sid)
        except Exception:
            continue
    return seen


def resolve_home_zone(location):
    """GPS-resolved IANA zone from the Evnex location's own coordinates -
    never trust Evnex's own "timeZone" attribute (observed value: the
    literal placeholder string "Factory", not a real zone). Same
    GPS-first-not-assumed principle as resolve_place()/TODO-009."""
    lat = lon = None
    if location:
        attrs = location.get("attributes") or {}
        coords = attrs.get("coordinates") or {}
        try:
            lat = float(coords.get("latitude"))
            lon = float(coords.get("longitude"))
        except (TypeError, ValueError):
            lat = lon = None
    # resolve_timezone() already falls back to DEFAULT_TIMEZONE
    # (Australia/Sydney) when lat/lon are missing - no need to duplicate
    # that fallback here.
    return tztools.resolve_timezone(lat, lon)


async def fetch_all_sessions(log):
    """(session_dict, location_dict_or_None) for every session across every
    org/charge-point on the account - calls the Evnex API directly, does
    not depend on any evnex_explore.py dump file being present or fresh."""
    evnex = await get_authenticated_client()
    user_detail = to_jsonable(await evnex.get_user_detail())
    orgs = (user_detail or {}).get("organisations") or []
    pairs = []
    for org in orgs:
        org_id = org.get("id")
        try:
            locations = to_jsonable(await evnex.get_org_locations(org_id=org_id))
        except Exception as e:
            log(f"  FAILED get_org_locations({org_id}): {e}")
            locations = []
        loc_by_cp = {}
        for loc in locations or []:
            cp_refs = ((loc.get("relationships") or {}).get("chargePoints") or {}).get("data", [])
            for ref in cp_refs:
                loc_by_cp[ref.get("id")] = loc

        try:
            charge_points = to_jsonable(await evnex.get_org_charge_points(org_id=org_id))
        except Exception as e:
            log(f"  FAILED get_org_charge_points({org_id}): {e}")
            continue

        for cp in charge_points or []:
            cp_id = cp.get("id")
            try:
                cp_sessions = to_jsonable(await evnex.get_charge_point_sessions(cp_id))
            except Exception as e:
                log(f"  FAILED get_charge_point_sessions({cp_id}): {e}")
                continue
            for s in cp_sessions or []:
                pairs.append((s, loc_by_cp.get(cp_id)))
    return pairs


async def run(dry_run, limit, log):
    ddir = deepdive_dir()
    seen = load_seen_session_ids(ddir)
    log(f"Found {len(seen)} already-downloaded Evnex session(s) in {ddir}")

    pairs = await fetch_all_sessions(log)
    log(f"Evnex reports {len(pairs)} session(s) across all organisations/charge points")

    written, skipped_seen, skipped_active, skipped_bad = 0, 0, 0, 0
    for session, location in pairs:
        sid = session.get("id")
        attrs = session.get("attributes") or {}

        if not sid or sid in seen:
            skipped_seen += 1
            continue
        if not is_session_complete(attrs):
            skipped_active += 1
            continue

        start_epoch = iso_to_epoch(attrs.get("chargingStarted") or attrs.get("startDate"))
        if start_epoch is None:
            skipped_bad += 1
            continue

        zone = resolve_home_zone(location)
        local_dt, abbr, offset_str = tztools.localize(start_epoch, zone)
        if local_dt:
            local_label = local_dt.strftime("%Y-%m-%d_%H%M")
            local_start_str = f"{local_dt.strftime('%Y-%m-%d %H:%M')} {abbr}"
        else:
            local_label = datetime.fromtimestamp(start_epoch, tz=timezone.utc).strftime("%Y-%m-%d_%H%M")
            local_start_str = None

        # Always-present, zero-padded "_NN" position suffix (starting at
        # "00") rather than a bare name for the first file and an ad-hoc
        # "_2", "_3", ... suffix only from the second collision onward -
        # see unique_stamped_path()'s docstring / AGENTS.md REQ-030.
        out_path = unique_stamped_path(ddir, "charge_deepdive", local_label, suffix="_Evnex_Home", ext=".json")
        base_name = os.path.basename(out_path)

        if limit is not None and written >= limit:
            log(f"  --limit {limit} reached, {base_name} left for next run")
            break

        record = {
            "evnex_session_id": sid,
            "source": "evnex",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "charging_started_utc": attrs.get("chargingStarted") or attrs.get("startDate"),
            "charging_stopped_utc": attrs.get("chargingStopped") or attrs.get("endDate"),
            "timezone": zone,
            "local_start": local_start_str,
            "session": session,
        }

        if dry_run:
            log(f"  [dry-run] would write {base_name}")
        else:
            os.makedirs(ddir, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2, default=str)
            log(f"  wrote {base_name}")
        written += 1
        seen.add(sid)

    log(
        f"\nDone: {written} new session(s) {'would be ' if dry_run else ''}written, "
        f"{skipped_seen} already had a file, {skipped_active} still in progress, "
        f"{skipped_bad} missing a usable start time."
    )
    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Show what would be written, write nothing")
    parser.add_argument("--limit", type=int, default=None, help="Cap how many new files get written this run")
    parser.add_argument("--quiet", action="store_true", help="Summary only, suppress per-session progress lines")
    args = parser.parse_args()

    def log(msg):
        if not args.quiet:
            print(msg)

    try:
        asyncio.run(run(args.dry_run, args.limit, log))
    except EvnexCredentialsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        # get_authenticated_client() already scrubs any credential value
        # out of this message before it reaches here.
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
