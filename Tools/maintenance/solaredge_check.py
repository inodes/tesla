#!/usr/bin/env python3
"""solaredge_check.py - is the SolarEdge V2 connection working? Shows how SolarEdge is connected (Site Access approval or Fleet key), the site, and one day of 15-minute
meter readings as the PV cache will keep them (lib/solaredge_v2.py).

  ./solaredge_check.py                 status, the site, yesterday's readings
  ./solaredge_check.py --day 2026-10-28
  ./solaredge_check.py --url           the address to open to approve the site (or http://127.0.0.1:8765/solaredge/connect while the app runs)
  ./solaredge_check.py --raw           also the first part of SolarEdge's own answer for the day
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import solaredge_v2 as SE2  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", help="YYYY-MM-DD (default yesterday)")
    ap.add_argument("--url", action="store_true")
    ap.add_argument("--raw", action="store_true")
    a = ap.parse_args()
    st = SE2.status()
    print("mode: %(mode)s   site: %(site_id)s   approved site: %(granted_site)s   error: %(error)s" % st)
    if a.url:
        print(SE2.authorize_url())
        return 0
    if not st["connected"]:
        print("Not connected. %s" % ("Open http://127.0.0.1:8765/solaredge/connect in the app's browser and approve the site." if st["configured"] else "Put solaredge.client_id and client_secret in config.json."))
        return 1
    site = st["site_id"]
    s, parsed, raw = SE2.get("/sites/%s" % site)
    print("site: HTTP %s %s" % (s, (parsed or {}).get("name") if isinstance(parsed, dict) else raw[:120]))
    day = datetime.strptime(a.day, "%Y-%m-%d").date() if a.day else date.today() - timedelta(days=1)
    if a.raw:
        s, parsed, raw = SE2.get("/sites/%s/meters/telemetry" % site, dict(resolution="QUARTER_HOUR", **{"from": SE2._stamp(datetime.combine(day, datetime.min.time()), SE2.site_tz(site)), "to": SE2._stamp(datetime.combine(day + timedelta(days=1), datetime.min.time()), SE2.site_tz(site))}))
        print("raw: HTTP %s\n%s" % (s, json.dumps(parsed, indent=1)[:2500] if parsed else raw[:600]))
    rows, err = SE2.meter_rows(site, day, day, "QUARTER_HOUR")
    print("%s: %d quarter-hours%s" % (day, len(rows), "  ERROR: " + err if err else ""))
    tot = {k: sum(r[k] or 0 for r in rows) for k in ("production_kwh", "consumption_kwh", "feedin_kwh", "purchased_kwh", "self_consumption_kwh")}
    print("  " + "  ".join("%s %.2f" % (k.replace("_kwh", ""), v) for k, v in tot.items()) + "  kWh")
    for r in rows[len(rows) // 2: len(rows) // 2 + 3]:
        print("  ", r["ts"], {k: (round(v, 3) if v is not None else None) for k, v in r.items() if k != "ts"})
    return 0 if rows and not err else 1


if __name__ == "__main__":
    sys.exit(main())
