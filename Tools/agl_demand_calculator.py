#!/usr/bin/env python3
"""
agl_demand_calculator.py - Reproduce AGL's Ausgrid EA025 Residential
Demand charge from the consolidated usage master (agl_usage_sync.py)
and the dated tariff rates (agl_tariff_rates.json).

Method (see agl_common.py / AGENTS.md REQ-031 for how this was derived
and verified): for each calendar month, take every half-hour "import"
(E1) reading that falls in the season-appropriate demand window
(weekdays, NSW public holidays excluded, summer 2pm-8pm Nov-Mar, winter
5pm-9pm Jun-Aug, zero charge in shoulder months) and find its maximum.
kWh * 2 = kW (a half-hour average). That single latched kW value is
billed - in ARREARS, on the invoice cycle that closes after the
calendar month ends - at demand_rate_per_kw_day * kW * the INVOICING
cycle's day count, GST-inclusive at the end (x1.10).

Self-test (run this file directly): reproduces June and July 2026's
already-invoiced demand charges exactly from the real usage data, and
projects August 2026 (not yet invoiced at time of writing). This is the
whole point of the self-test - if AGL's actual billed figure ever stops
matching what this file computes, that's a sign either the tariff rate
changed again or the window logic needs revisiting, not something to
silently trust.

Usage:
    ./agl_demand_calculator.py --self-test
    ./agl_demand_calculator.py --month 2026-08 --days 31
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agl_common import resolve_agl_data_home, in_demand_window  # noqa: E402

def _rates_path():
    """Where agl_tariff_rates.json actually lives - the AGL data home
    (resolve_agl_data_home(), same folder as the usage master), NEVER
    beside this script. Real tariff figures tied to this account are
    PII per AGENTS.md's zero-PII rule and must never sit in the repo -
    see AGENTS.md REQ-034 for the incident this fixes (the file was
    briefly created directly in Tools/ during REQ-031, caught and moved
    before ever being committed)."""
    return os.path.join(resolve_agl_data_home(), "agl_tariff_rates.json")


def load_rate_periods():
    rates_path = _rates_path()
    if not os.path.isfile(rates_path):
        raise FileNotFoundError(
            f"agl_tariff_rates.json not found at {rates_path}. This file holds "
            f"real account tariff data and is never committed to the repo (see "
            f"AGENTS.md REQ-034) - copy your own rates file there, or start from "
            f"Tools/agl_tariff_rates.example.json's schema."
        )
    with open(rates_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    periods = sorted(data["periods"], key=lambda p: p["effective_from"])
    for p in periods:
        p["_effective_from_date"] = datetime.strptime(p["effective_from"], "%Y-%m-%d").date()
    return periods


def rate_for_date(d, periods=None):
    """The tariff period in effect on date `d` (a date object) - the
    last period whose effective_from is <= d."""
    periods = periods or load_rate_periods()
    applicable = [p for p in periods if p["_effective_from_date"] <= d]
    if not applicable:
        raise ValueError(f"No tariff period covers {d} - earliest known period starts "
                          f"{periods[0]['effective_from']}")
    return applicable[-1]


def parse_agl_datetime(s):
    return datetime.strptime(s.strip(), "%d/%m/%Y %I:%M:%S %p")


def load_import_readings(master_path, year, month):
    """[(datetime, kWh), ...] for every E1 (import) row in `master_path`
    whose StartDate falls in the given calendar year/month."""
    out = []
    with open(master_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row["Role"] != "import":
                continue
            dt = parse_agl_datetime(row["StartDate"])
            if dt.year == year and dt.month == month:
                out.append((dt, float(row["kWh"])))
    return out


def monthly_latched_kw(master_path, year, month):
    """(kw, at_datetime) for the latched demand peak in this calendar
    month, or (0.0, None) if no interval in the demand window this
    month (e.g. a shoulder month)."""
    readings = load_import_readings(master_path, year, month)
    candidates = [(dt, kwh) for dt, kwh in readings if in_demand_window(dt)]
    if not candidates:
        return 0.0, None
    dt, kwh = max(candidates, key=lambda x: x[1])
    return round(kwh * 2, 2), dt


def demand_charge(kw_latched, billing_days, rate_per_kw_day):
    ex_gst = kw_latched * rate_per_kw_day * billing_days
    incl_gst = ex_gst * 1.10
    return round(ex_gst, 2), round(incl_gst, 2)


def self_test():
    data_home = resolve_agl_data_home()
    master_path = os.path.join(data_home, "usage", "agl_usage_master.csv")
    periods = load_rate_periods()

    cases = [
        # (year, month, billing_days, expected_kw, expected_ex_gst, invoice_label)
        (2026, 6, 30, 9.84, 113.62, "494808436 (25 Jun-24 Jul 2026)"),
        (2026, 7, 31, 8.74, 104.28, "498179654 (25 Jul-24 Aug 2026)"),
    ]
    all_passed = True
    for year, month, days, expected_kw, expected_ex_gst, label in cases:
        kw, at = monthly_latched_kw(master_path, year, month)
        rate = rate_for_date(datetime(year, month, 1).date(), periods)["demand_rate_per_kw_day"]
        ex_gst, incl_gst = demand_charge(kw, days, rate)
        ok = abs(kw - expected_kw) < 0.005 and abs(ex_gst - expected_ex_gst) < 0.005
        all_passed &= ok
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {year}-{month:02d}: latched {kw} kW at {at} -> "
              f"${ex_gst} ex GST (${incl_gst} incl) | invoice {label} says {expected_kw} kW / ${expected_ex_gst}")

    # August 2026: not yet invoiced at the time this was written - a
    # projection, not a reconciliation. Printed separately so it's
    # never mistaken for a verified self-test result.
    kw, at = monthly_latched_kw(master_path, 2026, 8)
    rate = rate_for_date(datetime(2026, 8, 1).date(), periods)["demand_rate_per_kw_day"]
    ex_gst, incl_gst = demand_charge(kw, 31, rate)
    print(f"[PROJECTED, not yet invoiced] 2026-08: latched {kw} kW at {at} -> "
          f"${ex_gst} ex GST (${incl_gst} incl), assuming a 31-day cycle")

    print()
    print("ALL SELF-TESTS PASSED" if all_passed else "SELF-TEST FAILURE - see FAIL lines above")
    return all_passed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--month", help="YYYY-MM to compute the latched peak for")
    parser.add_argument("--days", type=int, help="Billing cycle day count to apply the rate over")
    args = parser.parse_args()

    if args.self_test:
        ok = self_test()
        sys.exit(0 if ok else 1)

    if args.month:
        year, month = (int(x) for x in args.month.split("-"))
        data_home = resolve_agl_data_home()
        master_path = os.path.join(data_home, "usage", "agl_usage_master.csv")
        kw, at = monthly_latched_kw(master_path, year, month)
        print(f"{args.month}: latched {kw} kW" + (f" at {at}" if at else " (no demand-window interval this month)"))
        if args.days and kw:
            rate = rate_for_date(datetime(year, month, 1).date())["demand_rate_per_kw_day"]
            ex_gst, incl_gst = demand_charge(kw, args.days, rate)
            print(f"  -> ${ex_gst} ex GST (${incl_gst} incl GST) over a {args.days}-day cycle")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
