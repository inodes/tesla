#!/usr/bin/env python3
"""
power_invoice_report.py - REQ-120 (2026-09-24): given a REAL AGL electricity
invoice (a PDF this repo already knows how to find and parse - see
lib/invoice_common.py's AGLInvoiceExtractor and sync_agl_rates.py's
find_agl_bill_pdfs()), print AGL's own charge breakdown for that exact
billing cycle alongside the solar ROI for the same period: how much
self-consuming solar saved (what those kWh would otherwise have cost from
the grid) and how much exporting the excess earned (the feed-in credit -
already a real line on the bill, called out here rather than buried in
the total).

User: "Is there a way to print out a similar report to any give AGL
invoice showing how the charges were done - but also insight into self
consumption also included. [...] 1. Self consuming is the saving of what
would have been paid to grid without solar. 2. Excess - the amount
earned for feed in tariffs. This is an effective ROI on Solar."

This is deliberately a thin wrapper around REQ-118/119's
power_solar_report.py rather than a new calculation engine: the only new
piece is resolving a period from a REAL invoice PDF instead of a
calendar month/year/date range, and printing that invoice's own
extracted total as a ground-truth cross-check against
power_cost_calculator.py's reconstruct_bill() (already validated to
within cents-level rounding across REAL_INVOICE_CYCLES - see
power_cost_calculator.py's self_test()). Everything else - the PV
provider correlation, the self-consumption savings math, the HTML/PNG
charts - is power_solar_report.py's existing, already-tested code,
called with the invoice's own period_start/period_end.

Usage:
    ./power_invoice_report.py --list                  # show every invoice this repo can find
    ./power_invoice_report.py                          # most recent electricity invoice
    ./power_invoice_report.py --invoice 494808436       # match by invoice number
    ./power_invoice_report.py --invoice 20260625        # match by filename fragment
    ./power_invoice_report.py --invoice 494808436 --no-fetch
"""

import argparse
import os
import sys
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from providers_power import get_power_provider  # noqa: E402
from lib.tessie_api_common import load_config, resolve_tessie_data_home  # noqa: E402
from lib.power_usage_db import get_usage_db_connection  # noqa: E402
from providers_pv import get_pv_provider  # noqa: E402
from lib.pv_history_db import get_pv_db_connection, sync_daily_range, query_daily_range  # noqa: E402
from lib.invoice_common import AGLInvoiceExtractor  # noqa: E402
from power_cost_calculator import reconstruct_bill  # noqa: E402
from power_demand_calculator import load_rate_periods  # noqa: E402
from sync_agl_rates import find_agl_bill_pdfs, resolve_landing_dir  # noqa: E402
import power_solar_report as psr  # noqa: E402

CROSS_CHECK_TOLERANCE = 0.60  # same per-line rounding tolerance as power_cost_calculator.py's self_test()


def collect_electricity_bills(provider, log=print):
    """[(path, meta), ...] sorted oldest-to-newest - every real AGL
    ELECTRICITY bill this repo can find and extract a period for, scanned
    the same content-detection way sync_agl_rates.py/power_coverage_report.py
    do (invoices folder + landing/Downloads, by content not filename)."""
    ext = AGLInvoiceExtractor()
    invoices_dir = provider.resolve_invoices_dir()
    landing_dir = resolve_landing_dir()
    found = find_agl_bill_pdfs([invoices_dir, landing_dir], ext, log=log)
    bills = []
    for path, txt in found:
        meta = ext.extract(txt, path)
        if meta.get("service_type") != "electricity":
            continue
        if not meta.get("period_start") or not meta.get("period_end"):
            log(f"  ⚠ {os.path.basename(path)}: no extractable bill period - can't report on this one")
            continue
        bills.append((path, meta))
    bills.sort(key=lambda pm: pm[1]["period_end"])
    return bills


def print_invoice_list(bills, log=print):
    log(f"{len(bills)} electricity invoice(s) found:\n")
    for path, meta in bills:
        total = meta.get("total_amount")
        total_str = f"${total:.2f}" if total is not None else "$?"
        log(f"  {meta.get('period_start')} to {meta.get('period_end')}  "
            f"invoice {meta.get('invoice_number') or '?':<14} {total_str:>9}  {os.path.basename(path)}")


def select_invoice(bills, invoice_arg):
    """Most recent bill if invoice_arg is falsy; otherwise a case-insensitive
    substring match against either the filename or the invoice number,
    erroring out (with the candidates listed) on zero or multiple matches
    rather than silently guessing."""
    if not invoice_arg:
        return bills[-1]
    needle = invoice_arg.lower()
    matches = [
        (p, m) for p, m in bills
        if needle in os.path.basename(p).lower()
        or (m.get("invoice_number") and needle in m["invoice_number"].lower())
    ]
    if not matches:
        raise SystemExit(f"No invoice matches '{invoice_arg}'. Run with --list to see what's available.")
    if len(matches) > 1:
        lines = "\n".join(
            f"  {os.path.basename(p)}  (invoice {m.get('invoice_number')}, {m.get('period_start')} to {m.get('period_end')})"
            for p, m in matches
        )
        raise SystemExit(f"'{invoice_arg}' matches {len(matches)} invoices - be more specific:\n{lines}")
    return matches[0]


def print_as_billed(meta, bill, log=print):
    """The ground-truth cross-check: AGL's own printed total (extracted
    straight from the PDF) next to power_cost_calculator.py's reconstructed
    line-by-line breakdown - the same breakdown reconstruct_bill() has
    already been validated to produce within cents of real invoices."""
    real_total = meta.get("total_amount")
    account = meta.get("account_number")
    log(f"=== AGL invoice {meta.get('invoice_number') or '(no number found)'} "
        f"({meta.get('period_start')} to {meta.get('period_end')}) ===")
    if account:
        log(f"Account {account}" + (f" · issued {meta['issue_date']}" if meta.get("issue_date") else ""))
    log("")
    log("Reconstructed breakdown (power_cost_calculator.py's reconstruct_bill(), from the real half-hourly AGL meter data):")
    log(f"  Usage charge:                 ${bill['usage_ex_gst']:8.2f}  ex GST")
    log(f"  Supply charge:                ${bill['supply_ex_gst']:8.2f}  ex GST")
    if bill["demand_ex_gst"]:
        log(f"  Demand charge:                ${bill['demand_ex_gst']:8.2f}  ex GST"
            f"  (peak {bill['demand_kw_latched']:.2f} kW on {bill['demand_peak_at']}, {bill['demand_month']})")
    log(f"  Export credit (feed-in):     -${bill['solar_credit_ex_gst']:8.2f}  ex GST")
    log(f"  {'-' * 44}")
    log(f"  Subtotal:                     ${bill['total_ex_gst']:8.2f}  ex GST")
    log(f"  Total incl. GST:              ${bill['total_incl_gst']:8.2f}")
    if real_total is not None:
        diff = abs(bill["total_incl_gst"] - real_total)
        status = "✓ matches" if diff <= CROSS_CHECK_TOLERANCE else "⚠ MISMATCH -"
        log(f"\n  AGL's own printed total:      ${real_total:8.2f}   [{status} diff ${diff:.2f}]")
    else:
        log("\n  (Could not extract AGL's own printed total from this PDF to cross-check against)")
    log("")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--invoice", help="Match a specific invoice by number or filename fragment (default: most recent)")
    parser.add_argument("--list", action="store_true", help="List every invoice this repo can find, then exit")
    parser.add_argument("--no-fetch", action="store_true", help="Use only whatever PV provider data is already cached - no API calls")
    parser.add_argument("--out-dir", help="Where to write the report files (default: <Tesla data home>/energy_reports)")
    args = parser.parse_args()

    provider = get_power_provider()
    bills = collect_electricity_bills(provider)
    if not bills:
        raise SystemExit("No electricity invoices with an extractable bill period were found.")

    if args.list:
        print_invoice_list(bills)
        return

    path, meta = select_invoice(bills, args.invoice)
    start = datetime.strptime(meta["period_start"], "%Y-%m-%d").date()
    end = datetime.strptime(meta["period_end"], "%Y-%m-%d").date()
    if end > date.today():
        end = date.today()
        print(f"(period's end clipped to today, {end} - this invoice's cycle isn't finished yet)")
    label = meta.get("invoice_number") or f"{start}_to_{end}"

    periods = load_rate_periods()
    cfg = load_config()
    pv_provider = get_pv_provider()
    pv_api_key, pv_account_id = pv_provider.resolve_credentials(cfg)

    out_dir = args.out_dir or os.path.join(resolve_tessie_data_home(), "energy_reports")
    os.makedirs(out_dir, exist_ok=True)

    agl_conn = get_usage_db_connection(provider)
    pv_conn = get_pv_db_connection()
    try:
        bill = reconstruct_bill(start, end, periods=periods, log=None)
        print_as_billed(meta, bill)

        if not pv_api_key or not pv_account_id:
            print(f"({pv_provider.display_name} not configured - self-consumption savings will be $0.00 for this run)")
        elif not args.no_fetch:
            fetched = sync_daily_range(pv_conn, start, end, pv_provider, pv_api_key, pv_account_id, log=print)
            if fetched:
                print(f"  Fetched {fetched} new {pv_provider.display_name} day(s)")

        pv_by_day = query_daily_range(pv_conn, start, end)
        daily = psr.build_daily_table(agl_conn, pv_by_day, start, end)
        conflicts = psr.export_conflicts(daily)
        self_cons_savings, have_days, missing_days = psr.self_consumption_savings(daily, periods, provider)

        print("--- Solar ROI for this invoice's billing cycle ---\n")
        psr.print_summary(start, end, label, daily, bill, self_cons_savings, have_days, missing_days, conflicts)

        html_path = os.path.join(out_dir, f"invoice_report_{label}.html")
        psr.render_html_chart(daily, start, end, f"AGL invoice {label}", bill, self_cons_savings, html_path)
        print(f"\nWrote {html_path}")

        month_rows = []
        for m_start, m_end, m_label in psr.month_bounds_within(start, end):
            m_bill = reconstruct_bill(m_start, m_end, periods=periods, log=None)
            m_daily = [r for r in daily if m_start <= r["date"] <= m_end]
            m_savings, _, _ = psr.self_consumption_savings(m_daily, periods, provider)
            no_solar_ex_gst = m_bill["usage_ex_gst"] + m_savings + m_bill["supply_ex_gst"] + m_bill["demand_ex_gst"]
            month_rows.append({
                "label": m_label,
                "actual_incl_gst": m_bill["total_incl_gst"],
                "no_solar_incl_gst": round(no_solar_ex_gst * 1.10, 2),
            })

        png_path = os.path.join(out_dir, f"invoice_report_{label}.png")
        if psr.render_png_chart(month_rows, label, png_path):
            print(f"Wrote {png_path}")
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
