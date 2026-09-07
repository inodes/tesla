#!/usr/bin/env python3
"""
test_tessie_api.py - Tessie Developer API Connectivity & Data-Shape Test
==========================================================================
One-shot sanity check for https://developer.tessie.com before building a
real sync tool to replace manual CSV downloads: confirms an access token
works, pulls a small sample of /drives and /charges, and shows what
fields come back so they can be compared against the existing Tessie CSV
export columns this repo's tools already parse.

This is a TEST/exploration script, not a production sync tool - it makes
no changes to any registry or master CSV, and (unless --save-csv is
passed) writes nothing to disk at all.

Auth - reads the access token from, in priority order:
  1. --token CLI arg
  2. TESSIE_ACCESS_TOKEN environment variable
  3. "tessie_access_token" key in Tessie/config.json (gitignored - never
     committed, same as the "vin" key already stored there)
Never hardcode a token in this file, in git, or paste one into chat.

Generate a token at: https://dash.tessie.com/settings/api

Note: the API defaults to miles/Celsius/UTC unless told otherwise via
distance_format/temperature_format/timezone params - this script requests
km/C/Australia-Sydney by default (--distance-format/--temperature-format/
--timezone to override) to match this repo's existing CSV convention.

Usage:
    export TESSIE_ACCESS_TOKEN=your_token_here
    ./test_tessie_api.py
    ./test_tessie_api.py --limit 3 --since-days 7 --save-csv
    ./test_tessie_api.py --show-values     # prints real field values too

See Tessie/TESSIE_API.md for endpoint/field notes and known limitations.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tessie_api_common import api_get, get_token, get_vin, load_config  # noqa: F401 - shared with tessie_api_sync.py, see Tessie/TESSIE_API.md


def summarize_json_records(label, body, show_values=False):
    try:
        data = json.loads(body)
    except Exception:
        print(f"  ⚠️  {label}: response was not valid JSON ({len(body)} bytes) - first 200 chars:")
        print("     " + body[:200].decode("utf-8", errors="replace"))
        return None

    # Tessie's list endpoints typically wrap results as {"results": [...]}
    records = data.get("results") if isinstance(data, dict) else data
    if not isinstance(records, list):
        keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
        print(f"  ⚠️  {label}: unexpected response shape - top-level: {keys}")
        return None

    print(f"  ✔ {label}: {len(records)} record(s) returned")
    if records:
        print(f"     Fields in first record: {sorted(records[0].keys())}")
        if show_values:
            print(f"     First record (full):\n{json.dumps(records[0], indent=6)}")
    return records


def main():
    parser = argparse.ArgumentParser(
        description="Test connectivity to the Tessie API and inspect the /drives and /charges data shape"
    )
    parser.add_argument("--token", help="Tessie API access token (overrides env var / config.json)")
    parser.add_argument("--vin", help="Vehicle VIN (overrides Tessie/config.json's 'vin')")
    parser.add_argument("--limit", type=int, default=5, help="Records to request per endpoint (default: 5)")
    parser.add_argument("--since-days", type=int, default=30, help="Only request records from the last N days (default: 30)")
    parser.add_argument("--save-csv", action="store_true", help="Also request format=csv and save a small sample file per endpoint")
    parser.add_argument("--show-values", action="store_true", help="Print full field values for the first record too (off by default - this is real location/battery data)")
    parser.add_argument("--distance-format", default="km", choices=["mi", "km"], help="Distance unit for API responses (default: km, matching this repo's CSVs - API itself defaults to mi)")
    parser.add_argument("--temperature-format", default="c", choices=["c", "f"], help="Temperature unit for API responses (default: c)")
    parser.add_argument("--timezone", default="Australia/Sydney", help="IANA timezone for API timestamps (default: Australia/Sydney, matching the '(AEST)' CSV headers - API itself defaults to UTC)")
    args = parser.parse_args()

    config = load_config()
    token = get_token(args.token, config)
    vin = get_vin(args.vin, config)

    if not token:
        print("❌ No access token found. Provide one via --token, the TESSIE_ACCESS_TOKEN")
        print('   environment variable, or a "tessie_access_token" key in Tessie/config.json.')
        print("   Generate one at: https://dash.tessie.com/settings/api")
        sys.exit(1)
    if not vin:
        print("❌ No VIN found. Provide one via --vin or ensure Tessie/config.json has a \"vin\" key.")
        sys.exit(1)

    print(f"\U0001f50e Testing Tessie API for VIN ...{vin[-6:]} (token not shown)\n")

    since_ts = int(time.time()) - args.since_days * 86400
    any_ok = False

    unit_params = {
        "distance_format": args.distance_format,
        "temperature_format": args.temperature_format,
        "timezone": args.timezone,
    }

    for endpoint, label in [("drives", "Drives"), ("charges", "Charges")]:
        status, body = api_get(endpoint, vin, token, params={"limit": args.limit, "from": since_ts, **unit_params})
        if status == 200:
            summarize_json_records(label, body, show_values=args.show_values)
            any_ok = True
        elif status in (401, 403):
            print(f"  ❌ {label}: HTTP {status} - token rejected or lacks permission for this vehicle")
        elif status == 404:
            print(f"  ❌ {label}: HTTP 404 - check the VIN is correct and registered to this Tessie account")
        elif status is None:
            print(f"  ❌ {label}: request failed - {body.decode('utf-8', errors='replace')}")
            print("     (if this looks like a network/proxy/connection error rather than an HTTP")
            print("      status, you may be running this from a sandboxed environment with")
            print("      restricted egress rather than your own machine)")
        else:
            print(f"  ❌ {label}: unexpected HTTP {status} - {body[:200].decode('utf-8', errors='replace')}")
        print()

    if args.save_csv:
        out_dir = os.path.join(REPO_ROOT, "Tessie", "api_test_samples")
        os.makedirs(out_dir, exist_ok=True)
        for endpoint, label in [("drives", "drives"), ("charges", "charges")]:
            status, body = api_get(endpoint, vin, token, params={"limit": args.limit, "from": since_ts, "format": "csv", **unit_params})
            if status == 200:
                out_path = os.path.join(out_dir, f"sample_{label}.csv")
                with open(out_path, "wb") as f:
                    f.write(body)
                print(f"  \U0001f4be Saved {label} CSV sample to {out_path} ({len(body)} bytes)")
            else:
                print(f"  ❌ {label} CSV sample: HTTP {status}")

    sys.exit(0 if any_ok else 1)


if __name__ == "__main__":
    main()
