#!/usr/bin/env python3
"""
sync_api_evnex.py - Automated, idempotent sync that turns newly-
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

REQ-153: a completed session that delivered ~zero energy (0 kWh) is a
connection BLIP, not a charge - Evnex records every cable plug-in/unplug
event as its own "session", so plugging in and then deciding not to
charge (and unplugging) produces exactly this shape. These are
evaluated (via is_real_charging_session()) and permanently ignored -
NOT written as a charge_deepdive_*_Evnex_Home.json file at all, since
that file format represents a real charging session - and their ids are
instead recorded in a tiny separate .evnex_blip_ids.json file (see
load_ignored_blip_ids()/save_ignored_blip_ids()) purely so the same blip
is never re-evaluated on a future run.

Credentials come from evnex_common.py (config.json or
EVNEX_CLIENT_USERNAME/EVNEX_CLIENT_PASSWORD env vars) - nothing this
script prints or writes includes the Evnex username/password.

Usage:
    ./sync_api_evnex.py                # write any new completed sessions
    ./sync_api_evnex.py --dry-run       # show what would be written, write nothing
    ./sync_api_evnex.py --limit 5       # cap how many new files get written this run
    ./sync_api_evnex.py --quiet         # summary only
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.evnex_common import (  # noqa: E402
    get_authenticated_client,
    is_real_charging_session,
    to_jsonable,
    EvnexCredentialsError,
)
from lib.tessie_api_common import resolve_tessie_data_home  # noqa: E402
import lib.charge_paths as CP  # noqa: E402
import lib.tessie_timezone as tztools  # noqa: E402


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
    """Same completeness rule already used by analysis_home_charge_deep_dive.py's
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
    for fname, fpath in CP.list_files(ddir):
        if not (fname.startswith("charge_deepdive_") and fname.endswith("_Evnex_Home.json")):
            continue
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            sid = data.get("evnex_session_id")
            if sid:
                seen.add(sid)
        except Exception:
            continue
    return seen


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BLIP_IGNORE_PATH_NAME = ".evnex_blip_ids.json"


def load_ignored_blip_ids(ddir):
    """REQ-153: session ids already evaluated as a connection blip (0 kWh -
    a plug-in the user decided not to charge from, then unplugged - see
    is_real_charging_session()'s own docstring) and deliberately NOT
    written as a real charge_deepdive_*_Evnex_Home.json file (per the
    user's explicit correction: "Evnex records cable plugins and
    unplugs - not sessions. If I plugged in and decided not to charge
    then remove - this is that."). Tracked in this tiny separate file
    (never a full deep-dive JSON per blip, which would be a real
    charging-session artifact for something that was never a charge)
    purely so the SAME blip isn't re-evaluated and re-logged on every
    future run - fetch_all_sessions() re-fetches this account's entire
    session history every run with no incremental cursor, so without
    this a blip would show up as "new" forever."""
    path = os.path.join(ddir, BLIP_IGNORE_PATH_NAME)
    if not os.path.isfile(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("ids") or [])
    except Exception:
        return set()


def save_ignored_blip_ids(ddir, ids):
    """Overwrites the blip-ignore file with the full current set of
    ignored ids - small (a handful of UUIDs), so a full rewrite each run
    is simpler and safer than an append-only log."""
    os.makedirs(ddir, exist_ok=True)
    path = os.path.join(ddir, BLIP_IGNORE_PATH_NAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"ids": sorted(ids)}, f, indent=2)


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
    real_seen = load_seen_session_ids(ddir)
    blip_ids = load_ignored_blip_ids(ddir)
    seen = real_seen | blip_ids
    log(f"Found {len(real_seen)} already-downloaded Evnex session(s) and "
        f"{len(blip_ids)} already-ignored connection blip(s) in {ddir}")

 # REQ-153: resolve each real (non-blip) session's own Tessie numeric
 # charge id up front, from the raw un-patched Tessie API cache
 # (resources/api_cache/charges.json) - the only place this id lives at
 # all (charges_master.csv carries no id column, see
 # lib/charge_cost_model.py's own docstring) - and store it directly in
 # this session's own deep-dive JSON so a future reader never has to
 # redo this epoch-matching against a cache that can be refreshed/
 # pruned later. None when no match is found (e.g. this session predates
 # the raw cache), never guessed.
    from lib.charge_cost_model import load_raw_tessie_cache, find_cache_record_by_epoch
    _cache_records, cache_by_epoch = load_raw_tessie_cache(REPO_ROOT)

    pairs = await fetch_all_sessions(log)
    log(f"Evnex reports {len(pairs)} session(s) across all organisations/charge points")

    written, skipped_seen, skipped_active, skipped_bad, skipped_blips = 0, 0, 0, 0, 0
    new_blip_ids = set()
    for session, location in pairs:
        sid = session.get("id")
        attrs = session.get("attributes") or {}

        if not sid or sid in seen:
            skipped_seen += 1
            continue
        if not is_session_complete(attrs):
            skipped_active += 1
            continue

 # REQ-153: a connection blip (0 kWh - a plug-in/unplug with no real
 # charge, per the user's own correction: "Evnex records cable
 # plugins and unplugs - not sessions") is evaluated and permanently
 # ignored HERE, before any of the real-session work below - it never
 # gets a charge_deepdive_*_Evnex_Home.json file at all, since that
 # file format represents a real charging session.
        if not is_real_charging_session(attrs):
            skipped_blips += 1
            new_blip_ids.add(sid)
            started_iso = attrs.get("chargingStarted") or attrs.get("startDate") or "?"
            if dry_run:
                log(f"  [dry-run] would skip session {sid} ({started_iso}) - connection blip (0 kWh, not a real charge)")
            else:
                log(f"  skipped session {sid} ({started_iso}) - connection blip (0 kWh, not a real charge) - not saved")
            continue

        start_epoch = iso_to_epoch(attrs.get("chargingStarted") or attrs.get("startDate"))
        if start_epoch is None:
            skipped_bad += 1
            continue

        tessie_cache_rec = find_cache_record_by_epoch(cache_by_epoch, start_epoch)
        tessie_id = tessie_cache_rec.get("id") if tessie_cache_rec else None

        zone = resolve_home_zone(location)
        local_dt, abbr, offset_str = tztools.localize(start_epoch, zone)
        if local_dt:
 # AGENTS.md TODO-033: fold the real timezone abbreviation into
 # the filename's time portion itself, not just the human-
 # readable local_start_str below - every session recorded so
 # far is AEST/AEDT (Australia/Sydney), but a bare "1327" gives
 # no way to tell which without opening the file.
            local_label = f"{local_dt.strftime('%Y-%m-%d_%H%M')}_{abbr}" if abbr else local_dt.strftime("%Y-%m-%d_%H%M")
            local_start_str = f"{local_dt.strftime('%Y-%m-%d %H:%M')} {abbr}"
        else:
            local_label = datetime.fromtimestamp(start_epoch, tz=timezone.utc).strftime("%Y-%m-%d_%H%M")
            local_start_str = None

 # Always-present, zero-padded "_NN" position suffix (starting at
 # "00") rather than a bare name for the first file and an ad-hoc
 # "_2", "_3", ... suffix only from the second collision onward -
 # see unique_stamped_path()'s docstring / AGENTS.md REQ-030.
        out_path = CP.unique_path(ddir, local_label, suffix="_Evnex_Home", ext=".json")
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
            # REQ-153: this session's own Tessie numeric charge id
            # (resolved above from the raw API cache by epoch), or None
            # when no match exists (e.g. this session predates the raw
            # cache) - never guessed. See lib/charge_cost_model.py's own
            # docstring for why this id matters (it's the only thing
            # that can target Tessie's set_cost endpoint).
            "tessie_id": tessie_id,
            "session": session,
        }

        if dry_run:
            log(f"  [dry-run] would write {base_name}")
        else:
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2, default=str)
 # AGENTS.md TODO-033: set this file's mtime to the real
 # charge-start event, not whenever this sync happened to run,
 # so `ls -alt` sorts deep-dive files in true chronological
 # order regardless of when they were fetched.
            try:
                os.utime(out_path, (start_epoch, start_epoch))
            except Exception:
                pass
            log(f"  wrote {base_name}")
        written += 1
        seen.add(sid)

    if new_blip_ids and not dry_run:
        save_ignored_blip_ids(ddir, blip_ids | new_blip_ids)

    log(
        f"\nDone: {written} new session(s) {'would be ' if dry_run else ''}written"
        + (f", {skipped_blips} connection blip(s) {'would be ' if dry_run else ''}skipped (0 kWh, not real charges - not saved)" if skipped_blips else "")
        + f", {skipped_seen} already had a file, {skipped_active} still in progress, "
        f"{skipped_bad} missing a usable start time."
    )
    return written


def main():
    parser = argparse.ArgumentParser(
        usage="sync_api_evnex.py [--dry-run] [--limit N] [--quiet]\n",
        description="""
NAME
    sync_api_evnex.py - sync completed Evnex home-charging sessions into deep-dive files

DESCRIPTION
    Automated, idempotent sync that turns newly-completed Evnex home-charging
    sessions into per-session deep-dive files under charges/deepdive/ - keyed
    on Evnex's own session UUID, never a filename. Only ever writes new files;
    never modifies or deletes an existing one.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES
  sync_api_evnex.py
  sync_api_evnex.py --dry-run
  sync_api_evnex.py --limit 5 --quiet
"""
    )
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
