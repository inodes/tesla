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
import os as _os, sys as _sys; _sys.path[:0] = [p for p in (_os.path.dirname(_os.path.abspath(__file__)), _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))) if p not in _sys.path]      # the commands beside it, and Tools/ (lib, providers)

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.power_bill import REAL_INVOICE_CYCLES, reconstruct_bill  # noqa: E402


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
