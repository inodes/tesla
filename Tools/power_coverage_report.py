#!/usr/bin/env python3
"""
power_coverage_report.py - REQ-116: a year/month drill-down of how well
this repo's own power-billing data actually covers real history, so a
gap doesn't silently produce a wrong tariff-period boundary or a wrong
reconstructed bill.

Why this exists: REQ-115's database migration made every reader's OWN
usage figures reliable, and REQ-113's `sync_agl_rates.py` can derive real
rate periods from bill PDFs - but neither one shows whether the
UNDERLYING evidence actually has gaps. User: "there is potential for
incorrect calculations if there are gaps. Maybe there is a way to script
a summary of years and months that we can drill down to listing which
periods have csv's which periods have invoices covered (partially and
fully) and which are missing." Prompted directly by re-running
`sync_agl_rates.py` against all 25 real bill PDFs and finding TWO
previously-unnoticed invoice gaps (Apr 2022-Aug 2023, and Nov 2023-Dec
2024) on top of the one already flagged in REQ-113's own notes (Jan-Nov
2025) - exactly the kind of thing this report is meant to surface
without having to re-derive it by hand each time.

Two independent coverage dimensions, each scored per calendar month:

  USAGE  - half-hourly meter readings in the usage database
           (lib/power_usage_db.py, REQ-115). "full" = the month's own
           row count (role="import") is within 2% of days_in_month*48;
           "partial" = some rows but short of that; "none" = zero rows.
           The half-hour grid is what every kWh figure in this repo is
           actually built from - a gap here means reconstruct_bill()/
           power_demand_calculator.py have nothing to sum for that
           stretch, not a wrong number, a MISSING one (see AGENTS.md
           REQ-031's own known 2022-05-31 to 2024-09-07 export gap).

  INVOICE - real bill PDFs (scanned the same way `sync_agl_rates.py`
            does - by content, not just filename, across both the
            invoices folder and the landing/Downloads folder), using
            each bill's own extracted `period_start`/`period_end` (this
            works across every bill layout this repo has seen, 2020
            through today - unlike rate-table extraction, which only
            parses the newer 2023+ layout). "full" = every day of the
            month falls inside the UNION of all bill periods; "partial"
            = some days do; "none" = zero days do. AGL's billing cycle
            almost never aligns to calendar-month boundaries (a ~25th-
            to-24th cycle), so "full" here means the union of adjacent
            bills covers the whole month, not that one single bill does.

A month with good USAGE coverage but missing INVOICE coverage is exactly
the situation that made REQ-113's rate work risky: we can compute a real
kWh figure for that month but have no bill to confirm what rate applied.

Usage:
    ./power_coverage_report.py                 # one line per year, Jan-Dec coverage grid for both dimensions
    ./power_coverage_report.py --year 2023      # day-level detail for every month in that year
    ./power_coverage_report.py --gaps           # list every month that ISN'T "full" on both dimensions, across all years
"""

import argparse
import calendar
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from providers_power import get_power_provider  # noqa: E402
from lib.power_usage_db import get_usage_db_connection  # noqa: E402
from lib.invoice_common import PDFTextExtractor, AGLInvoiceExtractor  # noqa: E402
from sync_agl_rates import find_agl_bill_pdfs, resolve_landing_dir  # noqa: E402

FULL_THRESHOLD = 0.98  # >=98% of the expected half-hour count counts as "full" (allows a handful of dropped/duplicate intervals without flagging a real day as a gap)

SYMBOLS = {"full": "F", "partial": "~", "none": "."}


def month_range(start_ym, end_ym):
    """['YYYYMM', ...] inclusive, in order."""
    y, m = int(start_ym[:4]), int(start_ym[4:])
    ey, em = int(end_ym[:4]), int(end_ym[4:])
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def usage_row_counts(provider, data_home=None):
    """{'YYYYMM': row_count} for role="import" - the half-hour grid every
    kWh figure in this repo is built from."""
    conn = get_usage_db_connection(provider, data_home)
    try:
        rows = conn.execute(
            "SELECT year_month, COUNT(*) FROM usage_readings WHERE role = 'import' AND year_month IS NOT NULL GROUP BY year_month"
        ).fetchall()
        return {ym: c for ym, c in rows}
    finally:
        conn.close()


def collect_bill_periods(provider, log=print):
    """[(date, date), ...] - every real bill's own (period_start,
    period_end), across the invoices folder AND the landing/Downloads
    folder, using the SAME content-detection scan `sync_agl_rates.py`
    uses (never just a filename guess). Bills this repo can't parse a
    period for (a layout with no "Bill period:" line at all) are skipped
    with a warning rather than silently treated as a gap they aren't
    necessarily part of."""
    ext = AGLInvoiceExtractor()
    invoices_dir = provider.resolve_invoices_dir()
    landing_dir = resolve_landing_dir()
    bills = find_agl_bill_pdfs([invoices_dir, landing_dir], ext, log=log)
    periods = []
    for path, txt in bills:
        meta = ext.extract(txt, path)
        ps, pe = meta.get("period_start"), meta.get("period_end")
        if not ps or not pe:
            log(f"  ⚠ {os.path.basename(path)}: no extractable bill period - excluded from invoice coverage")
            continue
        periods.append((
            datetime.strptime(ps, "%Y-%m-%d").date(),
            datetime.strptime(pe, "%Y-%m-%d").date(),
        ))
    periods.sort()
    return periods


def days_covered_in_month(year, month, bill_periods):
    """How many of this calendar month's own days fall inside ANY real
    bill period (a plain per-day membership count, not a fraction of a
    bill - correct even though AGL's cycle never aligns to calendar
    months)."""
    days_in_month = calendar.monthrange(year, month)[1]
    month_start = date(year, month, 1)
    month_end = date(year, month, days_in_month)
    covered = 0
    d = month_start
    while d <= month_end:
        if any(ps <= d <= pe for ps, pe in bill_periods):
            covered += 1
        d += timedelta(days=1)
    return covered, days_in_month


def usage_status(year, month, row_count, is_current_month):
    days_in_month = calendar.monthrange(year, month)[1]
    if is_current_month:
        days_in_month = datetime.now().day  # still accumulating - don't call a partial month a "gap"
    expected = days_in_month * 48
    if row_count <= 0:
        return "none"
    if row_count >= expected * FULL_THRESHOLD:
        return "full"
    return "partial"


def invoice_status(covered_days, days_in_month, is_current_month):
    if is_current_month:
        days_in_month = min(days_in_month, datetime.now().day)
        covered_days = min(covered_days, days_in_month)
    if covered_days <= 0:
        return "none"
    if covered_days >= days_in_month:
        return "full"
    return "partial"


def build_month_table(provider, log=print):
    usage_counts = usage_row_counts(provider)
    bill_periods = collect_bill_periods(provider, log=log)

    if not usage_counts and not bill_periods:
        return [], bill_periods

    earliest_ym = min(usage_counts) if usage_counts else None
    latest_ym = max(usage_counts) if usage_counts else None
    if bill_periods:
        b_start_ym = f"{bill_periods[0][0].year:04d}{bill_periods[0][0].month:02d}"
        b_end_ym = f"{bill_periods[-1][1].year:04d}{bill_periods[-1][1].month:02d}"
        earliest_ym = min(earliest_ym, b_start_ym) if earliest_ym else b_start_ym
        latest_ym = max(latest_ym, b_end_ym) if latest_ym else b_end_ym

    today_ym = datetime.now().strftime("%Y%m")
    latest_ym = max(latest_ym, today_ym)

    rows = []
    now = datetime.now()
    for ym in month_range(earliest_ym, latest_ym):
        year, month = int(ym[:4]), int(ym[4:])
        is_current = (year == now.year and month == now.month)
        row_count = usage_counts.get(ym, 0)
        u_status = usage_status(year, month, row_count, is_current)
        covered, days_in_month = days_covered_in_month(year, month, bill_periods)
        i_status = invoice_status(covered, days_in_month, is_current)
        rows.append({
            "year_month": ym, "year": year, "month": month,
            "usage_rows": row_count, "usage_status": u_status,
            "invoice_days_covered": covered, "days_in_month": days_in_month,
            "invoice_status": i_status, "is_current": is_current,
        })
    return rows, bill_periods


def print_year_grid(rows):
    by_year = {}
    for r in rows:
        by_year.setdefault(r["year"], {})[r["month"]] = r

    print(f"{'Year':<6}{'Usage (Jan..Dec)':<28}{'Invoices (Jan..Dec)':<28}")
    for year in sorted(by_year):
        months = by_year[year]
        u = "".join(SYMBOLS[months[m]["usage_status"]] if m in months else " " for m in range(1, 13))
        i = "".join(SYMBOLS[months[m]["invoice_status"]] if m in months else " " for m in range(1, 13))
        u_spaced = " ".join(u)
        i_spaced = " ".join(i)
        print(f"{year:<6}{u_spaced:<28}{i_spaced:<28}")
    print("\nF = full coverage   ~ = partial   . = none   (blank = outside the observed range)")
    print("Current month is still accumulating - its own partial reading is expected, not a gap.")


def print_month_detail(rows, year):
    year_rows = [r for r in rows if r["year"] == year]
    if not year_rows:
        print(f"No data at all for {year}.")
        return
    print(f"{'Month':<10}{'Usage rows':<14}{'Usage':<10}{'Invoice days':<16}{'Invoice':<10}")
    for r in year_rows:
        mname = calendar.month_abbr[r["month"]]
        cur = " (in progress)" if r["is_current"] else ""
        inv_frac = f"{r['invoice_days_covered']}/{r['days_in_month']}"
        print(f"{mname:<10}{r['usage_rows']:<14}{r['usage_status']:<10}"
              f"{inv_frac:<16}{r['invoice_status']}{cur}")


def print_gaps(rows):
    gap_rows = [r for r in rows if not r["is_current"] and (r["usage_status"] != "full" or r["invoice_status"] != "full")]
    if not gap_rows:
        print("No gaps - every month has full usage AND full invoice coverage.")
        return
    print(f"{len(gap_rows)} month(s) with less than full coverage on at least one dimension:\n")
    for r in gap_rows:
        mname = f"{calendar.month_abbr[r['month']]} {r['year']}"
        print(f"  {mname:<10} usage={r['usage_status']:<8} ({r['usage_rows']} rows)   "
              f"invoice={r['invoice_status']:<8} ({r['invoice_days_covered']}/{r['days_in_month']} days)")


def main():
    parser = argparse.ArgumentParser(
        description="Year/month coverage report: which months have usage-database data, "
                    "which have real bill-PDF coverage (partial/full/missing), and where the gaps are.",
    )
    parser.add_argument("--year", type=int, help="Show day-level month detail for one calendar year")
    parser.add_argument("--gaps", action="store_true", help="List every month that isn't fully covered on both dimensions")
    args = parser.parse_args()

    provider = get_power_provider()
    rows, bill_periods = build_month_table(provider)
    if not rows:
        print("No usage data and no bill PDFs found - nothing to report.")
        return

    print(f"{len(bill_periods)} real bill period(s) found; {rows[0]['year_month']} to {rows[-1]['year_month']} observed.\n")

    if args.year:
        print_month_detail(rows, args.year)
    elif args.gaps:
        print_gaps(rows)
    else:
        print_year_grid(rows)


if __name__ == "__main__":
    main()
