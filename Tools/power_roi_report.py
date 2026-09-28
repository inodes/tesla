#!/usr/bin/env python3
"""
power_roi_report.py - REQ-121 (2026-09-24): payback period and lifetime
ROI for the solar SYSTEM ITSELF (not one invoice, not one calendar
period) - Solar/solar_system.json (net upfront cost after the
STC rebate, install/commissioning date, assumed lifetime) plus the REAL
annualized savings rate this repo already computes from the active PV
provider (SolarEdge via providers_pv, REQ-140 - was PVOutput before it)
and AGL data (power_solar_report.py, REQ-118/119; power_invoice_report.py,
REQ-120).

User pasted the standard Australian solar-ROI formulas and asked for a
proper payback/ROI breakdown, then supplied the actual signed install
contract (Soltek Energy, quote Stewart-1019): 30x LG Neon2 350W panels
(10.5kW), SolarEdge SE10000H inverter w/ MODBUS, $14,200.00 fully
installed after the STC rebate (GST inclusive) - and corrected the
system's first day of operation to 2019-11-28 (the contract was signed
2019-11-04, four days into a lead time before actual switch-on).

Formula (unchanged from the user's own paste, config.json's own AGL
electricity tariffs standing in for R_import/R_fit):
    Payback (years) = Net Upfront Cost / Annual Savings
    Annual Savings   = (E_self x R_import) + (E_export x R_fit) - Opex
    Lifetime ROI (%) = ((Annual Savings x Lifetime - Net Upfront Cost) / Net Upfront Cost) x 100

The one substitution versus a generic online calculator: Annual Savings
is not an industry-average guess - it's this system's own real
self-consumption savings and export credit (power_cost_calculator.py's
reconstruct_bill() + power_solar_report.py's PV-provider correlation),
annualized separately because the two components have different real
coverage - self-consumption needs the PV provider (which has real gaps
this system's history), export credit only needs AGL's own meter data
(which doesn't) - rather than annualizing a single blended total.

Usage:
    ./power_roi_report.py                       # trailing 365 days ending today (clamped to install date)
    ./power_roi_report.py --year 2026
    ./power_roi_report.py --from 2026-01-01 --to 2026-09-24
    ./power_roi_report.py --no-fetch
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from providers_power import get_power_provider  # noqa: E402
from lib.tessie_api_common import load_config  # noqa: E402
from lib.solar_system_config import load_solar_system_config  # noqa: E402
from lib.power_usage_db import get_usage_db_connection  # noqa: E402
from providers_pv import get_pv_provider  # noqa: E402
from lib.pv_history_db import get_pv_db_connection, sync_daily_range, query_daily_range  # noqa: E402
from power_cost_calculator import reconstruct_bill  # noqa: E402
from power_demand_calculator import load_rate_periods  # noqa: E402
import power_solar_report as psr  # noqa: E402


def resolve_solar_config():
    """REQ-150: solar_system.json (Solar/, a sibling of AGL/PVOutput/
    SolarEdge under the data home) replaces config.json's old 'solar'
    block - see lib/solar_system_config.py."""
    try:
        solar = load_solar_system_config()
    except FileNotFoundError as e:
        raise SystemExit(str(e))
    install_date = datetime.strptime(solar["install_date"], "%Y-%m-%d").date()
    return install_date, float(solar["net_upfront_cost_aud"]), int(solar.get("lifetime_years", 25)), float(solar.get("system_size_kw") or 0)


def resolve_roi_period(args, install_date):
    """Trailing 365 days ending today by default (clamped so it never
    starts before the system existed); --year/--from/--to override it
    the same way power_solar_report.py's resolve_period() does."""
    today = date.today()
    if args.year:
        y = int(args.year)
        start, end = date(y, 1, 1), min(date(y, 12, 31), today)
        return start, end
    if args.date_from or args.date_to:
        if not (args.date_from and args.date_to):
            raise SystemExit("--from and --to must be given together")
        return datetime.strptime(args.date_from, "%Y-%m-%d").date(), datetime.strptime(args.date_to, "%Y-%m-%d").date()
    start = max(install_date, today - timedelta(days=365))
    return start, today


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", help="YYYY - use this calendar year's real savings as the annualization basis")
    parser.add_argument("--from", dest="date_from", help="YYYY-MM-DD (use with --to)")
    parser.add_argument("--to", dest="date_to", help="YYYY-MM-DD (use with --from)")
    parser.add_argument("--no-fetch", action="store_true", help="Use only whatever PV provider data is already cached - no API calls")
    args = parser.parse_args()

    cfg = load_config()
    install_date, net_cost, lifetime_years, system_size_kw = resolve_solar_config()

    provider = get_power_provider()
    periods = load_rate_periods()
    pv_provider = get_pv_provider()
    pv_api_key, pv_account_id = pv_provider.resolve_credentials(cfg)

    start, end = resolve_roi_period(args, install_date)
    if end > date.today():
        end = date.today()

    agl_conn = get_usage_db_connection(provider)
    pv_conn = get_pv_db_connection()
    try:
        if pv_api_key and pv_account_id and not args.no_fetch:
            fetched = sync_daily_range(pv_conn, start, end, pv_provider, pv_api_key, pv_account_id, log=print)
            if fetched:
                print(f"  Fetched {fetched} new {pv_provider.display_name} day(s)")

        pv_by_day = query_daily_range(pv_conn, start, end)
        daily = psr.build_daily_table(agl_conn, pv_by_day, start, end)
        self_cons_savings, have_days, missing_days = psr.self_consumption_savings(daily, periods, provider)
        bill = reconstruct_bill(start, end, periods=periods, log=None)

        total_days = (end - start).days + 1
        scale_self = 365.0 / have_days if have_days else 0.0
        scale_export = 365.0 / total_days if total_days else 0.0
        annual_self_cons = self_cons_savings * scale_self
        annual_export_credit = bill["solar_credit_ex_gst"] * scale_export
        annual_savings = annual_self_cons + annual_export_credit

        years_elapsed = (date.today() - install_date).days / 365.25

        print(f"\n=== Solar ROI - {system_size_kw:g}kW system, installed {install_date} ({years_elapsed:.1f} years ago) ===\n")
        print(f"Net upfront cost (after STC rebate, GST incl.): ${net_cost:,.2f}")
        print(f"\nAnnualization basis: {start} to {end} ({total_days} day(s); "
              f"{have_days} of them with real {pv_provider.display_name} self-consumption data" +
              (f", {missing_days} missing" if missing_days else "") + ")")
        if have_days < total_days * 0.5:
            print("  ⚠ Less than half this window has real self-consumption data - the annualized "
                  "figure below is a real but thin extrapolation, not a full year of actual readings.")
        print(f"\n  Self-consumption savings: ${self_cons_savings:.2f} over {have_days} day(s) "
              f"-> annualized ${annual_self_cons:,.2f}/yr")
        print(f"  Export credit (feed-in):  ${bill['solar_credit_ex_gst']:.2f} over {total_days} day(s) "
              f"-> annualized ${annual_export_credit:,.2f}/yr")
        print(f"  {'-' * 60}")
        print(f"  Estimated annual savings run-rate:            ${annual_savings:,.2f}/yr")

        if annual_savings <= 0:
            print("\nAnnual savings run-rate is $0 or negative for this window - can't compute a payback period from it.")
            return

        payback_years = net_cost / annual_savings
        lifetime_roi_pct = ((annual_savings * lifetime_years - net_cost) / net_cost) * 100
        years_remaining = payback_years - years_elapsed

        print(f"\nSimple payback period:   {payback_years:.1f} years  (net cost / annual savings run-rate)")
        if years_remaining <= 0:
            print(f"Status:                  Already paid back - {-years_remaining:.1f} years ago, "
                  f"at this savings rate ({years_elapsed:.1f} years in vs. a {payback_years:.1f}-year payback)")
        else:
            print(f"Status:                  {years_elapsed:.1f} years in, ~{years_remaining:.1f} years to go to full payback")
        print(f"Lifetime ROI ({lifetime_years} years): {lifetime_roi_pct:,.0f}%  "
              f"((annual savings x {lifetime_years} - net cost) / net cost)")
        print("\n(Not financial advice - a real-data estimate of this system's own payback, not a "
              "forecast: future tariffs, feed-in rates and household usage will all move the actual number.)")
    finally:
        agl_conn.close()
        pv_conn.close()


if __name__ == "__main__":
 # REQ-140: reconstruct_bill() (power_cost_calculator.py) now raises a
 # clear ValueError, instead of a bare TypeError, when a requested
 # period spans an agl_tariff_rates.json rate period that's still
 # missing its real usage/supply/solar-credit rate (auto-proposed from
 # a bill but not yet reconciled against the matching real electricity
 # invoice - a genuine, pre-existing data gap found live while testing
 # this migration, e.g. this account's own file currently has one
 # spanning 2025-08-27 to 2026-06-29). Caught here so a caller hitting
 # it gets that one-line explanation and a clean exit, not a stack
 # trace - this file's own PV-provider logic isn't what's incomplete.
    try:
        main()
    except ValueError as e:
        print(f"\nError: {e}")
        sys.exit(1)
