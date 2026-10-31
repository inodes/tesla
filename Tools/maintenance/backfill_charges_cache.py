#!/usr/bin/env python3
"""backfill_charges_cache.py - add the Tessie charges from before the raw cache starts to resources/api_cache/charges.json.

The cache is filled from the first sync forward, so a charge older than that has no Tessie id and its cost cannot be written back (analyse_charging.py --sync-tessie-costs
skips it: "predates raw API cache"). This asks Tessie for the charges between two dates and adds the ones the cache does not hold; nothing already in the cache changes and
nothing is written to Tessie.

    ./backfill_charges_cache.py --from 2026-05-01 [--to 2026-06-10] [--apply]

Without --apply it only lists what would be added.
"""
import argparse
import calendar
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.tessie_api_common import api_get, get_token, get_vin, load_config  # noqa: E402

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "resources", "api_cache", "charges.json")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--to", dest="end", help="YYYY-MM-DD (UTC); default the day the cache starts")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    with open(CACHE, encoding="utf-8") as f:
        cache = json.load(f)
    recs = cache["records"]
    first = min(r["started_at"] for r in recs.values())
    t0 = calendar.timegm(datetime.strptime(a.start, "%Y-%m-%d").timetuple())
    t1 = calendar.timegm(datetime.strptime(a.end, "%Y-%m-%d").timetuple()) if a.end else first
    cfg = load_config()
    tok, vin = get_token(None, cfg), get_vin(None, cfg)
    if not tok or not vin:
        print("No Tessie access token / VIN in config.json.")
        return 1
    st, body = api_get("charges", vin, tok, params={"from": t0, "to": t1})
    if st != 200:
        print("Tessie answered HTTP %s: %s" % (st, body[:200]))
        return 1
    new = [r for r in json.loads(body).get("results", []) if str(r["id"]) not in recs]
    for r in sorted(new, key=lambda r: r["started_at"]):
        print("%s  id %s  %6.2f kWh  $%s  %s" % (datetime.fromtimestamp(r["started_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), r["id"], r.get("energy_used") or 0, r.get("cost"), (r.get("location") or "")[:40]))
    print("%d charge(s) not in the cache%s" % (len(new), "" if a.apply else " (dry run: nothing written)"))
    if a.apply and new:
        for r in new:
            recs[str(r["id"])] = r
        with open(CACHE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(cache, f)
        os.replace(CACHE + ".tmp", CACHE)
        print("Added to %s" % CACHE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
