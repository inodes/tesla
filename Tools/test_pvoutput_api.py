#!/usr/bin/env python3
"""
test_pvoutput_api.py - PVOutput API Connectivity & Data-Shape Test
========================================================================
One-shot sanity check for https://pvoutput.org/service/r2/ before writing
any real sync tool: confirms an API key + system ID pair works, pulls the
system's own metadata (getsystem.jsp) and one historical day of intraday
readings (getstatus.jsp?h=1), and prints the raw field layout so it can be
compared against Tessie/PVOUTPUT_API.md's still-unconfirmed notes on field
order, rate-limit header names, and account tier.

This is a TEST/exploration script, not a production sync tool - read-only,
makes no writes to disk or to PVOutput (never touches addstatus.jsp/
addoutput.jsp), same pattern as test_tessie_api.py and evnex_explore.py.

Auth - reads the API key + system ID from, in priority order:
  1. --api-key / --system-id CLI args
  2. PVOUTPUT_API_KEY / PVOUTPUT_SYSTEM_ID environment variables
  3. "pvoutput_api_key" / "pvoutput_system_id" keys in Tessie/config.json
     (gitignored - never committed)
Never hardcode a key in this file, in git, or paste one into chat.

Generate a key at: https://pvoutput.org/account.jsp (Settings -> API)

Usage:
    export PVOUTPUT_API_KEY=your_key_here
    export PVOUTPUT_SYSTEM_ID=your_system_id_here
    ./test_pvoutput_api.py
    ./test_pvoutput_api.py --date 20260901
    ./test_pvoutput_api.py --show-values

See Tessie/PVOUTPUT_API.md for endpoint/field notes and known limitations -
several items there ("What still needs verifying") can only be settled by
this script's real output.
"""

import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pvoutput_common import (  # noqa: F401 - shared with any later real sync script, see Tessie/PVOUTPUT_API.md
    api_get,
    get_api_key,
    get_system_id,
    load_config,
    parse_status_rows,
    print_rate_limit,
)


def show_raw_fields(label, body, show_values=False, max_fields=40):
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        print(f"  ⚠️  {label}: empty response body")
        return None
    if text.startswith("Access denied") or text.startswith("Invalid") or text.startswith("Unauthorised") or text.startswith("Unauthorized"):
        print(f"  ❌ {label}: PVOutput returned an error body: {text[:200]}")
        return None

    fields = text.split(",")
    print(f"  ✔ {label}: {len(fields)} comma-separated field(s) in the response")
    if show_values:
        shown = fields[:max_fields]
        for i, val in enumerate(shown):
            print(f"     [{i}] {val}")
        if len(fields) > max_fields:
            print(f"     ... ({len(fields) - max_fields} more field(s) not shown)")
    else:
        print(f"     (raw, first 200 chars): {text[:200]}")
    return fields


def main():
    parser = argparse.ArgumentParser(
        description="Test connectivity to the PVOutput API and inspect the getsystem/getstatus data shape"
    )
    parser.add_argument("--api-key", help="PVOutput API key (overrides env var / config.json)")
    parser.add_argument("--system-id", help="PVOutput system ID (overrides env var / config.json)")
    parser.add_argument("--date", help="Date (YYYYMMDD) for the historical getstatus.jsp?h=1 pull (default: yesterday, system-local)")
    parser.add_argument("--show-values", action="store_true", help="Print indexed field values too (off by default - this is real generation/location data)")
    args = parser.parse_args()

    config = load_config()
    api_key = get_api_key(args.api_key, config)
    system_id = get_system_id(args.system_id, config)

    if not api_key or not system_id:
        print("❌ No API key / system ID found. Provide via --api-key/--system-id, the")
        print("   PVOUTPUT_API_KEY/PVOUTPUT_SYSTEM_ID environment variables, or the")
        print('   "pvoutput_api_key"/"pvoutput_system_id" keys in Tessie/config.json.')
        print("   Generate a key at: https://pvoutput.org/account.jsp (Settings -> API)")
        sys.exit(1)

    date_arg = args.date or (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y%m%d")

    print(f"\U0001f50e Testing PVOutput API for system ...{str(system_id)[-4:]} (key not shown)\n")

    any_ok = False

    # --- getsystem.jsp: one-off system metadata (name, size, panels, lat/lon, status interval) ---
    print("getsystem.jsp (system metadata):")
    status, body, headers = api_get("getsystem", api_key, system_id, params={"donations": 1})
    if status == 200:
        show_raw_fields("System metadata", body, show_values=args.show_values)
        print_rate_limit(headers)
        any_ok = True
    elif status in (401, 403):
        print(f"  ❌ HTTP {status} - API key/system ID rejected")
    elif status is None:
        print(f"  ❌ request failed - {body.decode('utf-8', errors='replace')}")
        print("     (if this looks like a network/proxy/connection error rather than an HTTP")
        print("      status, you may be running this from a sandboxed environment with")
        print("      restricted egress rather than your own machine)")
    else:
        print(f"  ❌ unexpected HTTP {status} - {body[:200].decode('utf-8', errors='replace')}")
    print()

    # --- getstatus.jsp?h=1: one historical day of intraday readings ---
    print(f"getstatus.jsp?d={date_arg}&h=1 (historical intraday readings):")
    status, body, headers = api_get("getstatus", api_key, system_id, params={"d": date_arg, "h": 1, "limit": 288})
    if status == 200:
        rows = parse_status_rows(body)
        print(f"  ✔ {len(rows)} reading row(s) returned for {date_arg}")
        if rows:
            print(f"     Fields in first row: {len(rows[0])}")
            if args.show_values:
                print(f"     First row : {rows[0]}")
                print(f"     Last row  : {rows[-1]}")
            else:
                print(f"     (raw first row, first 200 chars): {','.join(rows[0])[:200]}")
        else:
            print("     (no readings - either no data for that date, or the account/date is empty)")
        print_rate_limit(headers)
        any_ok = True
    elif status in (401, 403):
        print(f"  ❌ HTTP {status} - API key/system ID rejected")
    elif status == 400:
        print(f"  ❌ HTTP 400 - bad request: {body[:200].decode('utf-8', errors='replace')}")
    elif status is None:
        print(f"  ❌ request failed - {body.decode('utf-8', errors='replace')}")
    else:
        print(f"  ❌ unexpected HTTP {status} - {body[:200].decode('utf-8', errors='replace')}")
    print()

    sys.exit(0 if any_ok else 1)


if __name__ == "__main__":
    main()
