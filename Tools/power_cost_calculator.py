#!/usr/bin/env python3
"""
power_cost_calculator.py - Full electricity-bill reconstruction for the
active power provider (e.g. AGL's Solar VIP plan) from the usage master
(sync_power_usage.py) and the provider's own dated tariff rates: usage
charge, supply charge, tiered solar credit, and the distributor's
residential demand charge (power_demand_calculator.py), all in one
place so the true cost of a charging session (or any other load) can
eventually be weighed against real, verified pricing rather than a
guess.

REQ-070 (2026-09-15): generalized from agl_cost_calculator.py - the
retailer-specific rules this file used to hardcode (AGL's pooled
tiered solar credit, the Ausgrid EA025 demand charge, the GST handling)
now live in Tools/providers_power/power_agl.py's AGLProvider, the first
and so far only BasePowerProvider implementation (get_power_provider(),
default "agl" - see providers_power/__init__.py). This file itself
contains no AGL-specific numbers; every provider-specific rule is
delegated. The design notes below describe AGL's own real billing
behaviour (still true, still how the self-test below is verified), not
something this file computes directly any more.

Solar credit tiering (see AGENTS.md REQ-031 - found by reading the FULL
invoice text, not just the headline "Standard feed-in tariff*" line):
AGL pools total solar export across the whole rate sub-period and splits
it into "Standard feed-in tariff*" (up to 10 kWh/day x days-in-period,
at the period's tier-1 rate) and "Next*" (everything beyond that, at a
flat $0.04/kWh that hasn't been observed to change). This is a POOLED
period-total cap, not a per-day cap summed afterward - confirmed because
every observed tier-1 amount is an exact multiple of 10 x the sub-period's
day count (300, 310, 250, ...), which would be a remarkable coincidence
under day-by-day capping. (See AGLProvider.solar_credit() for the actual
implementation.)

Demand-charge arrears timing (confirmed against 2 real invoices): the
invoice for cycle [start, end] bills the demand peak for the calendar
month immediately before `end`'s month - i.e. the most recently FULLY
COMPLETED calendar month as of the cycle's end date. This timing rule
is provider-agnostic and stays in this file (demand_month_for_cycle_end()
below) rather than in the provider class.

Self-test (run this file directly) reconstructs 4 real AGL invoices
(Apr, May, Jun, Jul 2026 cycles) end-to-end and compares the computed
total against each bill's actual "Direct Debit amount due" - within a
small cents-level tolerance for AGL's own per-line rounding.
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from providers_power import get_power_provider  # noqa: E402
from power_demand_calculator import load_rate_periods, rate_for_date, monthly_latched_kw, demand_charge  # noqa: E402
from lib.power_usage_db import get_usage_db_connection, iter_rows  # noqa: E402


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


def sum_kwh(data_home, role, start, end, provider=None):
    provider = provider or get_power_provider()
    conn = get_usage_db_connection(provider, data_home)
    try:
        total = 0.0
        for row in iter_rows(conn, role=role, start_date=start, end_date=end):
            total += float(row["kWh"])
        return total
    finally:
        conn.close()


def usage_cost_for_range(data_home, start, end, period, provider):
    """(total_cost_ex_gst, total_import_kwh) for every real "import"
    usage reading in [start, end] (inclusive dates), pricing EACH real
    interval (AGL: half-hourly) at its OWN rate via provider.
    usage_rate_for_window() - REQ-152. This is deliberately NOT the
    same shortcut analyse_session_cost.py's evaluate_tessie_charge()
    uses for a single EV-charging session (one wall-clock-time-weighted
    blended rate for the whole session) - that shortcut relies on a
    session's power draw being roughly constant, which is a reasonable
    assumption for a car charging at a fixed rate but NOT for a whole
    household's consumption across possibly many days of a rate
    period, so this instead prices every real interval individually
    and sums the cost, the only correct way to bill a genuine TOU
    period when consumption varies by time of day. For a flat (non-TOU)
    period this is mathematically identical to the old `import_kwh *
    period["usage_rate_per_kwh"]` (usage_rate_for_window() returns that
    same flat rate for every interval) - confirmed to the cent against
    every real invoiced cycle's self-test."""
    conn = get_usage_db_connection(provider, data_home)
    try:
        total_cost = 0.0
        total_kwh = 0.0
        for row in iter_rows(conn, role="import", start_date=start, end_date=end):
            row_start = provider.parse_datetime(row["StartDate"])
            row_end = provider.parse_datetime(row["EndDate"])
            kwh = float(row["kWh"])
            rate, _breakdown = provider.usage_rate_for_window(period, row_start, row_end)
            total_cost += kwh * rate
            total_kwh += kwh
        return total_cost, total_kwh
    finally:
        conn.close()


def _missing_rate_fields(period):
    """Fields required to price this period's usage/supply/solar
    charges, accounting for a genuine TOU/tou3 period's usage_rate_per_kwh
    being intentionally None (REQ-149/151) - replaced by its own real
    peak/(shoulder/)off-peak rate fields instead of one flat number."""
    common = ["supply_charge_per_day", "solar_feed_in_tier1_rate_per_kwh"]
    ttype = period.get("tariff_type")
    if ttype == "tou":
        usage_fields = ["peak_rate_per_kwh", "offpeak_rate_per_kwh"]
    elif ttype == "tou3":
        usage_fields = ["peak_rate_per_kwh", "shoulder_rate_per_kwh", "offpeak_rate_per_kwh"]
    else:
        usage_fields = ["usage_rate_per_kwh"]
    return [f for f in common + usage_fields if period.get(f) is None]


def demand_month_for_cycle_end(end):
    """The calendar month billed on an invoice ending on `end` - the
    most recently fully completed calendar month as of that date. This
    arrears-timing rule is the same for every provider observed so far,
    so it stays here rather than in providers_power/."""
    first_of_end_month = end.replace(day=1)
    last_month_end = first_of_end_month - timedelta(days=1)
    return last_month_end.year, last_month_end.month


def solar_credit(total_kwh, days, tier1_rate, provider=None):
    provider = provider or get_power_provider()
    return provider.solar_credit(total_kwh, days, tier1_rate)


def reconstruct_bill(start, end, data_home=None, periods=None, log=None):
    """Full ex-GST/incl-GST reconstruction for invoice cycle [start, end]
    (inclusive date() objects), under the active power provider. Returns
    a dict of every component plus the total, so a caller can inspect
    where the money went, not just the bottom line."""
    provider = get_power_provider()
    data_home = data_home or provider.resolve_data_home()
    periods = periods or load_rate_periods()
    days = daterange_days(start, end)

    usage_ex_gst = 0.0
    supply_ex_gst = 0.0
    solar_credit_total = 0.0
    for sub_start, sub_end, period in split_by_rate_period(start, end, periods):
        sub_days = daterange_days(sub_start, sub_end)
 # A rate period can legitimately have some fields still unfilled -
 # sync_agl_rates.py (REQ-112) auto-proposes a new period's own
 # effective_from/demand_rate the moment it notices a new bill's date
 # range, but usage_rate_per_kwh/supply_charge_per_day/
 # solar_feed_in_tier1_rate_per_kwh only get filled in once that
 # period's own ELECTRICITY invoice text is actually parsed (a period
 # auto-proposed from a GAS bill, for instance, never gets them at
 # all). A real gap here (caught live testing REQ-140's PV-provider
 # migration: this account's own agl_tariff_rates.json has exactly
 # this shape for 2025-08-27 through 2026-06-29) used to crash this
 # whole reconstruction with a bare TypeError on the multiplication
 # below - raised explicitly here instead, so a caller spanning an
 # incomplete period gets a clear, actionable message pointing at
 # which real period needs its rates filled in, not a stack trace.
        missing_fields = _missing_rate_fields(period)
        if missing_fields:
            raise ValueError(
                f"agl_tariff_rates.json's period starting {period['effective_from']} (covers "
                f"{sub_start} to {sub_end} of this request) is missing {', '.join(missing_fields)} - "
                f"fill it in from that period's real electricity invoice before reconstructing a bill "
                f"that spans it."
            )
        usage_cost_this_period, import_kwh = usage_cost_for_range(data_home, sub_start, sub_end, period, provider)
        solar_kwh = sum_kwh(data_home, "solar_export", sub_start, sub_end, provider)
        usage_ex_gst += usage_cost_this_period
        supply_ex_gst += sub_days * period["supply_charge_per_day"]
        t1, t2 = solar_credit(solar_kwh, sub_days, period["solar_feed_in_tier1_rate_per_kwh"], provider)
        solar_credit_total += t1 + t2
        if log:
            rate_note = (f"${period['usage_rate_per_kwh']}" if period.get("usage_rate_per_kwh") is not None
                         else f"real per-interval {period.get('tariff_type')} rate")
            log(f"  [{sub_start} to {sub_end}, {sub_days}d, rate from {period['effective_from']}] "
                f"import {import_kwh:.3f} kWh x {rate_note} -> usage ${usage_cost_this_period:.2f}, "
                f"solar {solar_kwh:.3f} kWh -> credit ${t1 + t2:.2f}")

    demand_year, demand_month = demand_month_for_cycle_end(end)
    kw_latched, peak_at = monthly_latched_kw(data_home, demand_year, demand_month)
    demand_rate = rate_for_date(end, periods)["demand_rate_per_kw_day"]
    demand_ex_gst, _ = demand_charge(kw_latched, days, demand_rate) if kw_latched else (0.0, 0.0)

 # Confirmed against real invoices (see AGENTS.md REQ-031): GST applies
 # to CHARGES ONLY (usage + supply + demand), never netted against the
 # solar export credit - "Total GST" on every sampled bill is exactly
 # 10% of "Total charges", not 10% of the net after credits. Solar
 # credits are GST-free and simply subtracted at the end. (This GST
 # treatment is currently the same for every provider observed - if a
 # future provider does it differently, this is the place to make it
 # provider-specific rather than assuming it stays universal.)
    charges_ex_gst = usage_ex_gst + supply_ex_gst + demand_ex_gst
    total_ex_gst = charges_ex_gst - solar_credit_total
    total_incl_gst = round(charges_ex_gst * provider.gst_multiplier() - solar_credit_total, 2)

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


# REQ-071 (2026-09-15): real invoice cycles pulled out of self_test()
# into this module-level constant so analysis_ev_session_cost.py's own
# --reconcile-invoices (the user's own "AGL invoices can be used to
# cross reference the data as we've determined it") can reuse the SAME
# already-verified (start, end, real total) cases as ground truth for
# reconstruct_bill(), instead of a second hand-copied list that could
# drift from this one. Real dollar totals and invoice numbers, not
# rates or usage data - this repo's own established precedent already
# commits these (they were here, inline in self_test(), before this
# refactor) rather than treating them as PII the way agl_tariff_rates.json
# and agl_usage_master.csv are.
REAL_INVOICE_CYCLES = [
    (date(2026, 4, 25), date(2026, 5, 24), 49.89, "487506779-1 (Apr)"),
    (date(2026, 5, 25), date(2026, 6, 24), 80.31, "491187907 (May)"),
    (date(2026, 6, 25), date(2026, 7, 24), 269.98, "494808436 (Jun)"),
    (date(2026, 7, 25), date(2026, 8, 24), 202.44, "498179654 (Jul)"),
]


def self_test():
    cases = REAL_INVOICE_CYCLES
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
    parser = argparse.ArgumentParser(
        usage="""power_cost_calculator.py --start YYYY-MM-DD --end YYYY-MM-DD
   or:  power_cost_calculator.py --self-test
""",
        description="""
NAME
    power_cost_calculator.py - full electricity-bill reconstruction for the active power provider

DESCRIPTION
    Reconstructs the active power provider's full bill for a cycle (e.g.
    AGL's Solar VIP plan) from the usage master (sync_power_usage.py) and
    the provider's own dated tariff rates: usage charge, supply charge,
    tiered solar credit, and the distributor's residential demand charge
    (power_demand_calculator.py) - all in one place, so the true cost of a
    charging session (or any other load) can be weighed against real,
    verified pricing. The active provider is config.json's "power_provider"
    key (default "agl").
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES
  power_cost_calculator.py --start 2026-08-01 --end 2026-08-31
  power_cost_calculator.py --self-test
"""
    )
    parser.add_argument("--self-test", action="store_true", help="Run the built-in self-test against already-invoiced AGL cycles")
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
