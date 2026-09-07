#!/usr/bin/env python3
"""
agl_cost_calculator.py - Full AGL Solar VIP bill reconstruction from the
usage master (agl_usage_sync.py) and dated tariff rates
(agl_tariff_rates.json): usage charge, supply charge, tiered solar
credit, and the Ausgrid EA025 demand charge (agl_demand_calculator.py),
all in one place so the true cost of a charging session (or any other
load) can eventually be weighed against real, verified AGL pricing
rather than a guess.

Solar credit tiering (see AGENTS.md REQ-031 - found by reading the FULL
invoice text, not just the headline "Standard feed-in tariff*" line):
AGL pools total solar export across the whole rate sub-period and splits
it into "Standard feed-in tariff*" (up to 10 kWh/day x days-in-period,
at the period's tier-1 rate) and "Next*" (everything beyond that, at a
flat $0.04/kWh that hasn't been observed to change). This is a POOLED
period-total cap, not a per-day cap summed afterward - confirmed because
every observed tier-1 amount is an exact multiple of 10 x the sub-period's
day count (300, 310, 250, ...), which would be a remarkable coincidence
under day-by-day capping.

Demand-charge arrears timing (confirmed against 2 real invoices): the
invoice for cycle [start, end] bills the demand peak for the calendar
month immediately before `end`'s month - i.e. the most recently FULLY
COMPLETED calendar month as of the cycle's end date.

Self-test (run this file directly) reconstructs 4 real invoices (Apr,
May, Jun, Jul 2026 cycles) end-to-end and compares the computed total
against each bill's actual "Direct Debit amount due" - within a small
cents-level tolerance for AGL's own per-line rounding.
"""

import argparse
import csv
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agl_common import resolve_agl_data_home  # noqa: E402
from agl_demand_calculator import load_rate_periods, rate_for_date, monthly_latched_kw, demand_charge  # noqa: E402

TIER2_RATE = 0.04  # flat $0.04/kWh beyond the daily cap - unchanged across every period observed so far
TIER1_CAP_KWH_PER_DAY = 10


def parse_agl_datetime(s):
    return datetime.strptime(s.strip(), "%d/%m/%Y %I:%M:%S %p")


def daterange_days(start, end):
    """Inclusive day count between two date() objects."""
    return (end - start).days + 1


def split_by_rate_period(start, end, periods=None):
    """[(sub_start, sub_end, period), ...] - splits [start, end]
    (inclusive dates) at any tariff effective_from boundary that falls
    within it, so a cycle straddling a rate change is billed correctly
    at each rate for the days it actually applied."""
    periods = periods or load_rate_periods()
    boundaries = sorted({p["_effective_from_date"] for p in periods
                          if start <= p["_effective_from_date"] <= end})
    cuts = [start] + boundaries + [end + timedelta(days=1)]
    out = []
    for i in range(len(cuts) - 1):
        sub_start = cuts[i]
        sub_end = cuts[i + 1] - timedelta(days=1)
        if sub_start > sub_end:
            continue
        out.append((sub_start, sub_end, rate_for_date(sub_start, periods)))
    return out


def sum_kwh(master_path, role, start, end):
    total = 0.0
    with open(master_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row["Role"] != role:
                continue
            dt = parse_agl_datetime(row["StartDate"])
            if start <= dt.date() <= end:
                total += float(row["kWh"])
    return total


def demand_month_for_cycle_end(end):
    """The calendar month billed on an invoice ending on `end` - the
    most recently fully completed calendar month as of that date."""
    first_of_end_month = end.replace(day=1)
    last_month_end = first_of_end_month - timedelta(days=1)
    return last_month_end.year, last_month_end.month


def solar_credit(total_kwh, days, tier1_rate):
    cap = TIER1_CAP_KWH_PER_DAY * days
    tier1_kwh = min(total_kwh, cap)
    tier2_kwh = max(0.0, total_kwh - cap)
    return round(tier1_kwh * tier1_rate, 2), round(tier2_kwh * TIER2_RATE, 2)


def reconstruct_bill(start, end, master_path=None, periods=None, log=None):
    """Full ex-GST/incl-GST reconstruction for invoice cycle [start, end]
    (inclusive date() objects). Returns a dict of every component plus
    the total, so a caller can inspect where the money went, not just
    the bottom line."""
    data_home = resolve_agl_data_home()
    master_path = master_path or os.path.join(data_home, "usage", "agl_usage_master.csv")
    periods = periods or load_rate_periods()
    days = daterange_days(start, end)

    usage_ex_gst = 0.0
    supply_ex_gst = 0.0
    solar_credit_total = 0.0
    for sub_start, sub_end, period in split_by_rate_period(start, end, periods):
        sub_days = daterange_days(sub_start, sub_end)
        import_kwh = sum_kwh(master_path, "import", sub_start, sub_end)
        solar_kwh = sum_kwh(master_path, "solar_export", sub_start, sub_end)
        usage_ex_gst += import_kwh * period["usage_rate_per_kwh"]
        supply_ex_gst += sub_days * period["supply_charge_per_day"]
        t1, t2 = solar_credit(solar_kwh, sub_days, period["solar_feed_in_tier1_rate_per_kwh"])
        solar_credit_total += t1 + t2
        if log:
            log(f"  [{sub_start} to {sub_end}, {sub_days}d, rate from {period['effective_from']}] "
                f"import {import_kwh:.3f} kWh x ${period['usage_rate_per_kwh']}, "
                f"solar {solar_kwh:.3f} kWh -> credit ${t1 + t2:.2f}")

    demand_year, demand_month = demand_month_for_cycle_end(end)
    kw_latched, peak_at = monthly_latched_kw(master_path, demand_year, demand_month)
    demand_rate = rate_for_date(end, periods)["demand_rate_per_kw_day"]
    demand_ex_gst, _ = demand_charge(kw_latched, days, demand_rate) if kw_latched else (0.0, 0.0)

    # Confirmed against real invoices (see AGENTS.md REQ-031): GST applies
    # to CHARGES ONLY (usage + supply + demand), never netted against the
    # solar export credit - "Total GST" on every sampled bill is exactly
    # 10% of "Total charges", not 10% of the net after credits. Solar
    # credits are GST-free and simply subtracted at the end.
    charges_ex_gst = usage_ex_gst + supply_ex_gst + demand_ex_gst
    total_ex_gst = charges_ex_gst - solar_credit_total
    total_incl_gst = round(charges_ex_gst * 1.10 - solar_credit_total, 2)

    return {
        "days": days,
        "usage_ex_gst": round(usage_ex_gst, 2),
        "supply_ex_gst": round(supply_ex_gst, 2),
        "solar_credit_ex_gst": round(solar_credit_total, 2),
        "demand_ex_gst": demand_ex_gst,
        "demand_kw_latched": kw_latched,
        "demand_peak_at": peak_at,
        "demand_month": f"{demand_year}-{demand_month:02d}",
        "total_ex_gst": round(total_ex_gst, 2),
        "total_incl_gst": total_incl_gst,
    }


def self_test():
    cases = [
        (date(2026, 4, 25), date(2026, 5, 24), 49.89, "487506779-1 (Apr)"),
        (date(2026, 5, 25), date(2026, 6, 24), 80.31, "491187907 (May)"),
        (date(2026, 6, 25), date(2026, 7, 24), 269.98, "494808436 (Jun)"),
        (date(2026, 7, 25), date(2026, 8, 24), 202.44, "498179654 (Jul)"),
    ]
    all_ok = True
    for start, end, expected_total, label in cases:
        print(f"=== {label}: {start} to {end} ===")
        result = reconstruct_bill(start, end, log=print)
        diff = abs(result["total_incl_gst"] - expected_total)
        ok = diff <= 0.60  # cents-level per-line rounding across several components
        all_ok &= ok
        status = "PASS" if ok else "FAIL"
        print(f"  usage ${result['usage_ex_gst']} + supply ${result['supply_ex_gst']} "
              f"- solar credit ${result['solar_credit_ex_gst']} + demand ${result['demand_ex_gst']} "
              f"(latched {result['demand_kw_latched']} kW for {result['demand_month']})")
        print(f"  = ${result['total_ex_gst']} ex GST -> ${result['total_incl_gst']} incl GST  "
              f"[{status}, invoice says ${expected_total}, diff ${diff:.2f}]")
        print()
    print("ALL SELF-TESTS PASSED (within rounding tolerance)" if all_ok else "SELF-TEST FAILURE - see FAIL lines above")
    return all_ok


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--start", help="YYYY-MM-DD cycle start")
    parser.add_argument("--end", help="YYYY-MM-DD cycle end")
    args = parser.parse_args()

    if args.self_test:
        sys.exit(0 if self_test() else 1)

    if args.start and args.end:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.strptime(args.end, "%Y-%m-%d").date()
        result = reconstruct_bill(start, end, log=print)
        print(f"\nTotal: ${result['total_ex_gst']} ex GST -> ${result['total_incl_gst']} incl GST")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
