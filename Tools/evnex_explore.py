#!/usr/bin/env python3
"""
evnex_explore.py - Read-only dump of everything the `evnex` library can
see about your Evnex home charger, for manual inspection.
==========================================================================
Purpose (per the user's own framing): (a) see what info is actually
available from Evnex, (b) compare how much detail that is against what
Tessie can source from a charging session, ahead of deciding how (or
whether) to fold Evnex data into charges_master.csv alongside
Superchargers/Chargefox/Evie/etc.

This script is deliberately READ-ONLY - it never calls any of the
`evnex` library's control endpoints (stop_charge_point, enable_charger,
disable_charger, unlock_charger, set_charge_point_override,
set_charger_load_profile, set_charge_point_schedule). It only queries
status/history.

Credentials come from evnex_common.py (Tessie/config.json or
EVNEX_CLIENT_USERNAME/EVNEX_CLIENT_PASSWORD env vars) - see that module's
docstring for why there's no --username/--password CLI flag. Nothing
this script prints or writes includes your Evnex password.

Usage:
    ./evnex_explore.py                  # dump everything to a JSON file + summary to stdout
    ./evnex_explore.py --days 30        # wider org-insight window (default 7)
    ./evnex_explore.py --output PATH    # where to write the full JSON dump
    ./evnex_explore.py --quiet          # summary only, suppress per-call progress lines
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evnex_common import get_authenticated_client, EvnexCredentialsError  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUTPUT_DIR = os.path.join(REPO_ROOT, "Tessie")


def to_jsonable(obj):
    """Recursively convert pydantic models (and containers of them) into
    plain JSON-safe data. Falls back to str() for anything else exotic
    (e.g. datetime objects some fields might carry)."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        try:
            return to_jsonable(model_dump(mode="json"))
        except TypeError:
            return to_jsonable(model_dump())
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


async def safe_call(log, label, coro):
    """Runs one API call, logging success/failure, and returning
    (result_or_None, error_or_None) - never raises, so one offline
    charger or unsupported call doesn't abort the whole exploration."""
    try:
        result = await coro
        log(f"  ok: {label}")
        return to_jsonable(result), None
    except Exception as e:
        log(f"  FAILED: {label}: {e}")
        return None, str(e)


async def explore(days, log):
    evnex = await get_authenticated_client()

    report = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "organisations": []}

    log("Fetching user detail...")
    user_detail, err = await safe_call(log, "get_user_detail", evnex.get_user_detail())
    report["user_detail"] = user_detail
    report["user_detail_error"] = err

    orgs = (user_detail or {}).get("organisations") or []
    if not orgs:
        log("No organisations found on this account - nothing more to explore.")
        return report

    for org in orgs:
        org_id = org.get("id")
        org_name = org.get("name", org_id)
        log(f"\n=== Organisation: {org_name} ({org_id}) ===")
        org_report = {"org": org}

        org_report["locations"], org_report["locations_error"] = await safe_call(
            log, "get_org_locations", evnex.get_org_locations(org_id=org_id)
        )
        org_report["summary_status"], org_report["summary_status_error"] = await safe_call(
            log, "get_org_summary_status", evnex.get_org_summary_status(org_id=org_id)
        )
        org_report["connector_summary"], org_report["connector_summary_error"] = await safe_call(
            log, "get_org_connector_summary", evnex.get_org_connector_summary(org_id=org_id)
        )
        org_report["insight"], org_report["insight_error"] = await safe_call(
            log, f"get_org_insight(days={days})", evnex.get_org_insight(days=days, org_id=org_id)
        )

        charge_points, cp_err = await safe_call(
            log, "get_org_charge_points", evnex.get_org_charge_points(org_id=org_id)
        )
        org_report["charge_points_error"] = cp_err
        org_report["charge_points"] = []

        for cp in charge_points or []:
            cp_id = cp.get("id")
            cp_name = cp.get("name", cp_id)
            log(f"  --- Charge point: {cp_name} ({cp_id}) ---")
            cp_report = {"summary": cp}

            cp_report["detail_v3"], cp_report["detail_v3_error"] = await safe_call(
                log, "get_charge_point_detail_v3", evnex.get_charge_point_detail_v3(cp_id)
            )
            cp_report["status"], cp_report["status_error"] = await safe_call(
                log, "get_charge_point_status", evnex.get_charge_point_status(cp_id)
            )
            cp_report["energy_meter_reading"], cp_report["energy_meter_reading_error"] = await safe_call(
                log, "get_charge_point_energy_meter_reading",
                evnex.get_charge_point_energy_meter_reading(cp_id),
            )
            cp_report["solar_config"], cp_report["solar_config_error"] = await safe_call(
                log, "get_charge_point_solar_config", evnex.get_charge_point_solar_config(cp_id)
            )
            cp_report["sessions"], cp_report["sessions_error"] = await safe_call(
                log, "get_charge_point_sessions", evnex.get_charge_point_sessions(cp_id)
            )

            sessions = cp_report["sessions"] or []
            log(f"      -> {len(sessions)} session(s) returned")

            org_report["charge_points"].append(cp_report)

        report["organisations"].append(org_report)

    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=7, help="Org-insight window in days (default: 7)")
    parser.add_argument("--output", default=None, help="Path for the full JSON dump (default: Tessie/evnex_explore_<timestamp>.json)")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-call progress lines, print only the final summary")
    args = parser.parse_args()

    def log(msg):
        if not args.quiet:
            print(msg)

    try:
        report = asyncio.run(explore(args.days, log))
    except EvnexCredentialsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        # get_authenticated_client() already scrubs any credential value
        # out of this message before it reaches here.
        print(f"Error: {e}", file=sys.stderr)
        return 1

    output_path = args.output
    if not output_path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(DEFAULT_OUTPUT_DIR, f"evnex_explore_{timestamp}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    org_count = len(report.get("organisations", []))
    cp_count = sum(len(o.get("charge_points", [])) for o in report.get("organisations", []))
    session_count = sum(
        len(cp.get("sessions") or [])
        for o in report.get("organisations", [])
        for cp in o.get("charge_points", [])
    )
    print(f"\nDone: {org_count} organisation(s), {cp_count} charge point(s), {session_count} session record(s) total.")
    print(f"Full detail written to: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
