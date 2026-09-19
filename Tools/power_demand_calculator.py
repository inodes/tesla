#!/usr/bin/env python3
"""
power_demand_calculator.py - Reproduce the active power provider's demand
charge (e.g. AGL's Ausgrid EA025 Residential Demand charge) from the
consolidated usage master (sync_power_usage.py) and the active
provider's own dated tariff rates.

REQ-070 (2026-09-15): generalized from agl_demand_calculator.py - this
script no longer knows one retailer's own rules (the season-aware
demand window, the NSW holiday calendar, the GST multiplier). Those now
live in Tools/providers_power/power_agl.py's AGLProvider, the first and
so far only BasePowerProvider implementation - see
providers_power/__init__.py's get_power_provider(), which resolves
config.json's "power_provider" key (default "agl", this household's
real current retailer). Every function below delegates to whichever
provider that resolves to; nothing here hardcodes AGL by name.

Method (unchanged from the original AGL-only version - see
providers_power/power_agl.py's own docstring for how this was derived
and verified): for each calendar month, take every half-hour "import"
reading that falls in the provider's own season-appropriate demand
window and find its maximum. kWh * 2 = kW (a half-hour average). That
single latched kW value is billed - in ARREARS, on the invoice cycle
that closes after the calendar month ends - at
demand_rate_per_kw_day * kW * the INVOICING cycle's day count,
GST-inclusive at the end (provider.demand_charge()).

Self-test (run this file directly): reproduces June and July 2026's
already-invoiced AGL demand charges exactly from the real usage data,
and projects August 2026 (not yet invoiced at time of writing). This is
the whole point of the self-test - if the actual billed figure ever
stops matching what this file computes, that's a sign either the
tariff rate changed again or the provider's window logic needs
revisiting, not something to silently trust.

Usage:
    ./power_demand_calculator.py --self-test
    ./power_demand_calculator.py --month 2026-08 --days 31
"""

import argparse
import csv
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from providers_power import get_power_provider  # noqa: E402


def load_rate_periods():
    """Sorted dated tariff periods for the active power provider."""
    return get_power_provider().load_rate_periods()


def rate_for_date(d, periods=None):
    """The active provider's tariff period in effect on date `d` (a
    date object) - the last period whose effective_from is <= d."""
    return get_power_provider().rate_for_date(d, periods)


def load_import_readings(master_path, year, month):
    """[(datetime, kWh), ...] for every "import"-role row in
    `master_path` whose StartDate falls in the given calendar
    year/month - datetime parsing follows the active provider's own
    usage-export timestamp format."""
    provider = get_power_provider()
    out = []
    with open(master_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row["Role"] != "import":
                continue
            dt = provider.parse_datetime(row["StartDate"])
            if dt.year == year and dt.month == month:
                out.append((dt, float(row["kWh"])))
    return out


def monthly_latched_kw(master_path, year, month):
    """(kw, at_datetime) for the latched demand peak in this calendar
    month under the active provider's own demand window, or (0.0, None)
    if no interval in the demand window this month (e.g. a shoulder
    month)."""
    provider = get_power_provider()
    readings = load_import_readings(master_path, year, month)
    candidates = [(dt, kwh) for dt, kwh in readings if provider.in_demand_window(dt)]
    if not candidates:
        return 0.0, None
    dt, kwh = max(candidates, key=lambda x: x[1])
    return round(kwh * 2, 2), dt


def demand_charge(kw_latched, billing_days, rate_per_kw_day):
    return get_power_provider().demand_charge(kw_latched, billing_days, rate_per_kw_day)


def self_test():
    provider = get_power_provider()
    data_home = provider.resolve_data_home()
    master_path = os.path.join(data_home, "usage", provider.usage_master_filename())
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
    parser = argparse.ArgumentParser(
        usage="""power_demand_calculator.py --month YYYY-MM [--days N]
   or:  power_demand_calculator.py --self-test
""",
        description="""
NAME
    power_demand_calculator.py - reproduce the active power provider's residential demand charge

DESCRIPTION
    Reproduces the active power provider's demand charge (e.g. AGL's Ausgrid
    EA025 Residential Demand charge - see Tools/providers_power/power_agl.py)
    from the consolidated usage master (sync_power_usage.py) and the
    provider's own dated tariff rates: finds the provider's own
    season-appropriate demand window's maximum half-hourly import for the
    month and bills that single latched kW value in arrears, at
    demand_rate_per_kw_day * kW * the invoicing cycle's day count. The
    active provider is config.json's "power_provider" key (default "agl").
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES
  power_demand_calculator.py --month 2026-08 --days 31
  power_demand_calculator.py --self-test
"""
    )
    parser.add_argument("--self-test", action="store_true", help="Reproduce already-invoiced months and project the current one")
    parser.add_argument("--month", help="YYYY-MM to compute the latched peak for")
    parser.add_argument("--days", type=int, help="Billing cycle day count to apply the rate over")
    args = parser.parse_args()

    if args.self_test:
        ok = self_test()
        sys.exit(0 if ok else 1)

    if args.month:
        year, month = (int(x) for x in args.month.split("-"))
        provider = get_power_provider()
        data_home = provider.resolve_data_home()
        master_path = os.path.join(data_home, "usage", provider.usage_master_filename())
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
