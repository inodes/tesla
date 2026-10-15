#!/usr/bin/env python3
"""
power_solar_report.py - REQ-118 (2026-09-24): a holistic energy report
correlating AGL's real billed import/export (lib/power_usage_db.py,
REQ-115) with the active PV provider's real generation/consumption
(lib/pv_history_db.py; SolarEdge via providers_pv as of REQ-140, was
PVOutput's lib/pvoutput_history_db.py before it - PVOutput on this
account turned out to be a pure relay of this SAME SolarEdge account's
own API, see AGENTS.local.md's REQ-140 entry) over any period of choice.

User: "How difficult would it be to have a script that could correlate
the PVOutput and AGL data and report and graph periods of choice -
including calculating the amounts earned by solar export, amount saved
by self consumption and costing. Basically a holistic grapher, auditor
and financial calculator."

Why both sources are needed, not just one: AGL only sees what crosses
its meter - real import and real export - which is why
power_cost_calculator.py's reconstruct_bill() can already compute usage
cost, supply charge and solar EXPORT credit from AGL data alone (reused
here unchanged). But AGL has no idea how much solar you actually USED
yourselves, because self-consumed power never crosses the meter at
all - that's only visible to the PV provider's own generation and
consumption meters (a real self_consumption_kwh field on SolarEdge -
REQ-140 - not derived here from two CT clamps the way the original
PVOutput-based version had to). This script adds that missing half:
self-consumption savings = self-consumed kWh valued at the AGL usage
rate in effect on the day it happened (providers_power's own
rate_for_date()) - the grid cost solar avoided by being used directly
rather than exported and bought back.

"Auditor": AGL's and the PV provider's export figures are two
independent real meters measuring almost the same physical export - a
material daily disagreement between them is a data-quality flag worth
surfacing (same spirit as pvoutput_common.py's own
pv_agl_import_conflict(), generalized here to export).

Usage:
    ./power_solar_report.py                        # last full calendar month (default)
    ./power_solar_report.py --month 2026-08
    ./power_solar_report.py --year 2026
    ./power_solar_report.py --from 2026-01-01 --to 2026-08-31
    ./power_solar_report.py --month 2026-08 --no-fetch   # cache-only, no PV provider API calls
"""

import argparse
import calendar
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from providers_power import get_power_provider  # noqa: E402
from lib.tessie_api_common import load_config, resolve_tessie_data_home  # noqa: E402
from lib.power_usage_db import get_usage_db_connection  # noqa: E402
from providers_pv import get_pv_provider  # noqa: E402
from lib.pv_history_db import get_pv_db_connection, sync_daily_range, query_daily_range  # noqa: E402
from lib.analysis_deepdive_charts import write_interactive_chart  # noqa: E402
from power_cost_calculator import reconstruct_bill  # noqa: E402
from power_demand_calculator import load_rate_periods  # noqa: E402

# --- dataviz reference palette (light mode) - see AGENTS.md / dataviz skill ---
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e1e0d9"
S1_BLUE = "#2a78d6"
S2_ORANGE = "#eb6834"
S3_AQUA = "#1baf7a"
S4_YELLOW = "#eda100"

EXPORT_CONFLICT_MIN_KWH = 0.5
EXPORT_CONFLICT_RATIO = 0.3


def resolve_period(args):
    """(start, end, label) date() objects, inclusive, from whichever of
    --month/--year/--from+--to was given. Default (nothing given): the
    last full calendar month - the same "what happened recently"
    framing check_staleness()/power_coverage_report.py already use."""
    if args.month:
        y, m = int(args.month[:4]), int(args.month[5:7])
        last_day = calendar.monthrange(y, m)[1]
        return date(y, m, 1), date(y, m, last_day), args.month
    if args.year:
        y = int(args.year)
        return date(y, 1, 1), date(y, 12, 31), str(y)
    if args.date_from or args.date_to:
        if not (args.date_from and args.date_to):
            raise SystemExit("--from and --to must be given together")
        start = datetime.strptime(args.date_from, "%Y-%m-%d").date()
        end = datetime.strptime(args.date_to, "%Y-%m-%d").date()
        if end < start:
            raise SystemExit(f"--to ({end}) is before --from ({start})")
        return start, end, f"{start}_to_{end}"
    today = date.today()
    last_month_end = today.replace(day=1) - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    return last_month_start, last_month_end, last_month_start.strftime("%Y-%m")


def daily_role_kwh(conn, role, start, end):
    """{date_obj: kwh} for one AGL register role over [start, end]."""
    rows = conn.execute(
        "SELECT substr(start_date_iso, 1, 10) AS day, SUM(kwh) AS total "
        "FROM usage_readings WHERE role = ? AND start_date_iso >= ? AND start_date_iso < ? "
        "GROUP BY day",
        (role, start.isoformat(), (end + timedelta(days=1)).isoformat()),
    )
    return {datetime.strptime(day, "%Y-%m-%d").date(): total for day, total in rows}


def build_daily_table(agl_conn, pv_by_day, start, end):
    """[{date, agl_import, agl_export, pv_generation, pv_export,
    pv_used, pv_self_consumed}, ...] for every day in [start, end] - for
    the chart and the export cross-check. The financial TOTAL never
    comes from summing this table - it stays keyed off
    reconstruct_bill()'s own rate-period-aware sums (see main()) so the
    two paths can never silently disagree with each other."""
    agl_import = daily_role_kwh(agl_conn, "import", start, end)
    agl_export = daily_role_kwh(agl_conn, "solar_export", start, end)

    out = []
    d = start
    while d <= end:
        pv = pv_by_day.get(d)
        pv_gen = pv["production_kwh"] if pv else None
        pv_exp = pv["feedin_kwh"] if pv else None
        pv_used = pv["consumption_kwh"] if pv else None
        # Prefer the provider's own directly-measured self-consumption
        # field (SolarEdge: a real SelfConsumption meter, not inferred)
        # over deriving it from generation-minus-export - falls back to
        # the derived figure only if a provider can't report it.
        pv_self = pv.get("self_consumption_kwh") if pv else None
        if pv_self is None and pv_gen is not None and pv_exp is not None:
            pv_self = max(0.0, pv_gen - pv_exp)
        out.append({
            "date": d,
            "agl_import": agl_import.get(d, 0.0),
            "agl_export": agl_export.get(d, 0.0),
            # AGL is a manual-sync source (REQ-070/071) - a day this
            # recent legitimately having NO rows yet (not synced, not
            # "measured zero") is common, not a data-quality conflict -
            # export_conflicts() only flags a day where AGL actually
            # has SOME reading to disagree with the PV provider about.
            "agl_has_data": (d in agl_import) or (d in agl_export),
            "pv_generation": pv_gen,
            "pv_export": pv_exp,
            "pv_used": pv_used,
            "pv_self_consumed": pv_self,
        })
        d += timedelta(days=1)
    return out


def export_conflicts(daily):
    """Days where AGL's and the PV provider's independent export figures
    disagree by >= EXPORT_CONFLICT_RATIO - a data-quality flag, not a
    claim about which source is wrong (same spirit as
    pvoutput_common.py's own pv_agl_import_conflict(), generalized here
    to export)."""
    flagged = []
    for row in daily:
        if row["pv_export"] is None or not row["agl_has_data"]:
            continue
        larger = max(row["agl_export"], row["pv_export"])
        if larger < EXPORT_CONFLICT_MIN_KWH:
            continue
        if abs(row["agl_export"] - row["pv_export"]) / larger >= EXPORT_CONFLICT_RATIO:
            flagged.append(row)
    return flagged


def self_consumption_savings(daily, periods, provider):
    """(savings_ex_gst, days_with_pv_data, days_missing_pv_data) - the
    grid cost avoided by using solar directly rather than exporting it
    and buying the equivalent back, valued at whatever AGL usage rate
    was actually in effect on each day (never a single blended rate
    across the whole period, so a rate change mid-period is still
    handled correctly).

    REQ-152: for a genuine TOU/tou3 period (peak/shoulder/off-peak
    rates that actually differ - or, for this account's own real
    history so far, that happen not to - see REQ-149/151) this prices
    each day via provider.usage_rate_for_window() over that whole
    calendar day, a wall-clock-time-weighted blend - a real
    approximation, not the per-real-interval precision
    power_cost_calculator.py's usage_cost_for_range() now has for the
    whole-household usage charge, since self-consumption here is only
    available as a DAILY total, not per-interval. Stated plainly since
    solar self-consumption skews toward daylight hours, not evenly
    across the day, so this blend is directionally reasonable but not
    exact for a day whose Peak window sits inside daylight hours.

    A day whose matching rate period has NO rate information at all -
    no usage_rate_per_kwh AND no tariff_type (real, live case caught
    while testing REQ-140: one auto-proposed period in
    agl_tariff_rates.json was sourced from a GAS bill, not an
    electricity one, and never got any rate filled in) - is still
    counted as "missing" here, same as a day with no PV data - there's
    truly no rate to value that day's self-consumption at, not a
    reason to crash the whole report."""
    savings = 0.0
    have, missing = 0, 0
    for row in daily:
        if row["pv_self_consumed"] is None:
            missing += 1
            continue
        period = provider.rate_for_date(row["date"], periods) or {}
        if period.get("tariff_type") is None and period.get("usage_rate_per_kwh") is None:
            missing += 1
            continue
        day_start = datetime.combine(row["date"], datetime.min.time())
        day_end = day_start + timedelta(days=1)
        rate, _breakdown = provider.usage_rate_for_window(period, day_start, day_end)
        have += 1
        savings += row["pv_self_consumed"] * rate
    return savings, have, missing


def month_bounds_within(start, end):
    """[(month_start, month_end, label), ...] - every calendar month
    overlapping [start, end], each clipped to the requested range - the
    grouping the $ chart bars by, so a custom --from/--to range still
    gets a readable multi-bar breakdown instead of one undifferentiated
    total."""
    out = []
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        m_start = max(start, date(y, m, 1))
        m_end = min(end, date(y, m, calendar.monthrange(y, m)[1]))
        out.append((m_start, m_end, f"{y}-{m:02d}"))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def render_html_chart(daily, start, end, label, bill, self_cons_savings, out_path):
    x_values = [row["date"].strftime("%Y-%m-%d") for row in daily]
    series = [
        {"name": "Generation", "unit": "kWh", "axis": "left", "color": S1_BLUE,
         "values": [row["pv_generation"] for row in daily]},
        {"name": "Grid import", "unit": "kWh", "axis": "left", "color": S2_ORANGE,
         "values": [row["agl_import"] for row in daily]},
        {"name": "Grid export", "unit": "kWh", "axis": "left", "color": S3_AQUA,
         "values": [row["agl_export"] for row in daily]},
        {"name": "Household use", "unit": "kWh", "axis": "left", "color": S4_YELLOW,
         "values": [row["pv_used"] for row in daily]},
    ]
    total_solar_value = self_cons_savings + bill["solar_credit_ex_gst"]
    subtitle = (
        f"{start} to {end} ({len(daily)} day(s))<br>"
        f"Net bill: ${bill['total_incl_gst']:.2f} incl GST &middot; "
        f"Total solar value: ${total_solar_value:.2f} "
        f"(${self_cons_savings:.2f} self-consumption + ${bill['solar_credit_ex_gst']:.2f} export credit)"
    )
    write_interactive_chart(
        out_path,
        title=f"Energy flows - {label}",
        x_values=x_values,
        x_label="Date",
        series=series,
        subtitle=subtitle,
        zoomable=True,
    )


def render_png_chart(month_rows, label, out_path):
    """Grouped bar chart, one pair per calendar month in the report
    period: the actual bill vs. what it would have cost with no solar
    at all - the gap between the two bars IS the total solar value for
    that month, at a glance. Same matplotlib styling (SURFACE/
    TEXT_PRIMARY/TEXT_SECONDARY/GRID, dpi=220, savefig pattern) as
    analyse_battery_health.py's render_png()."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return False

    labels = [r["label"] for r in month_rows]
    actual = [r["actual_incl_gst"] for r in month_rows]
    no_solar = [r["no_solar_incl_gst"] for r in month_rows]

    x = np.arange(len(labels))
    width = 0.36

    fig, ax = plt.subplots(figsize=(max(8, len(labels) * 1.1), 6), dpi=220)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    ax.bar(x - width / 2, actual, width, color=S1_BLUE, label="Actual bill (with solar)", zorder=3)
    ax.bar(x + width / 2, no_solar, width, color=S2_ORANGE, label="Without solar (hypothetical)", zorder=3)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, color=TEXT_SECONDARY)
    ax.set_ylabel("$ incl GST", color=TEXT_SECONDARY)
    ax.tick_params(axis="y", labelcolor=TEXT_SECONDARY)
    ax.grid(True, axis="y", color=GRID, linewidth=0.8, zorder=1)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=TEXT_SECONDARY)
    ax.set_title(f"Cost with vs. without solar - {label}", color=TEXT_PRIMARY,
                 fontsize=15, fontweight="bold", loc="left", pad=20)

    fig.savefig(out_path, facecolor=SURFACE, bbox_inches="tight", dpi=220)
    plt.close(fig)
    return True


def print_summary(start, end, label, daily, bill, self_cons_savings, have_days, missing_days, conflicts, log=print):
    total_days = (end - start).days + 1
    unsynced_recent = sum(1 for r in daily if not r["agl_has_data"])
    total_gen = sum(r["pv_generation"] for r in daily if r["pv_generation"] is not None)
    total_agl_import = sum(r["agl_import"] for r in daily)
    total_agl_export = sum(r["agl_export"] for r in daily)
    total_self_consumed = sum(r["pv_self_consumed"] for r in daily if r["pv_self_consumed"] is not None)

    no_solar_usage_ex_gst = bill["usage_ex_gst"] + self_cons_savings
    no_solar_total_ex_gst = no_solar_usage_ex_gst + bill["supply_ex_gst"] + bill["demand_ex_gst"]
    no_solar_total_incl_gst = round(no_solar_total_ex_gst * 1.10, 2)
    total_solar_value = self_cons_savings + bill["solar_credit_ex_gst"]

    log(f"=== Energy report: {label} ({start} to {end}, {total_days} day(s)) ===\n")
    log(f"PV provider coverage: {have_days}/{have_days + missing_days} day(s) have real generation data"
        + (f" ({missing_days} day(s) missing - their self-consumption savings are excluded, not zeroed)"
           if missing_days else ""))
    if unsynced_recent:
        log(f"AGL coverage: {unsynced_recent} day(s) in this period have no AGL usage data yet "
            f"(a manual-export source - not synced, not measured zero; excluded from the export cross-check below)")
    if conflicts:
        log(f"\n⚠ {len(conflicts)} day(s) where AGL's and the PV provider's independent export readings disagree "
            f"by >={EXPORT_CONFLICT_RATIO:.0%} - a data-quality flag, not necessarily an error:")
        for row in conflicts[:10]:
            log(f"    {row['date']}: AGL export {row['agl_export']:.2f} kWh vs PV provider export {row['pv_export']:.2f} kWh")
        if len(conflicts) > 10:
            log(f"    ... and {len(conflicts) - 10} more")
    log("")
    log(f"{'Real generation (PV provider):':<38}{total_gen:9.1f} kWh")
    log(f"{'Real grid import (AGL):':<38}{total_agl_import:9.1f} kWh  -> usage charge   ${bill['usage_ex_gst']:.2f} ex GST")
    log(f"{'Real grid export (AGL, billed):':<38}{total_agl_export:9.1f} kWh  -> export credit  ${bill['solar_credit_ex_gst']:.2f}")
    log(f"{'Real self-consumption (PV provider):':<38}{total_self_consumed:9.1f} kWh  -> avoided cost   ${self_cons_savings:.2f}")
    log("")
    log(f"Actual bill:            usage ${bill['usage_ex_gst']:.2f} + supply ${bill['supply_ex_gst']:.2f} "
        f"+ demand ${bill['demand_ex_gst']:.2f} - export credit ${bill['solar_credit_ex_gst']:.2f} "
        f"= ${bill['total_ex_gst']:.2f} ex GST (${bill['total_incl_gst']:.2f} incl GST)")
    log(f"Without solar, this would have cost: ${no_solar_total_ex_gst:.2f} ex GST (${no_solar_total_incl_gst:.2f} incl GST)")
    log(f"Total value of solar this period:    ${total_solar_value:.2f} ex GST "
        f"(${self_cons_savings:.2f} self-consumption + ${bill['solar_credit_ex_gst']:.2f} export credit)")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--month", help="YYYY-MM")
    parser.add_argument("--year", help="YYYY")
    parser.add_argument("--from", dest="date_from", help="YYYY-MM-DD (use with --to)")
    parser.add_argument("--to", dest="date_to", help="YYYY-MM-DD (use with --from)")
    parser.add_argument("--no-fetch", action="store_true", help="Use only whatever PV provider data is already cached - no API calls")
    parser.add_argument("--out-dir", help="Where to write the report files (default: <Tesla data home>/energy_reports)")
    args = parser.parse_args()

    start, end, label = resolve_period(args)
    if end > date.today():
        end = date.today()
        print(f"(period's end clipped to today, {end} - can't report on the future)")

    provider = get_power_provider()
    periods = load_rate_periods()
    cfg = load_config()
    pv_provider = get_pv_provider()
    pv_api_key, pv_account_id = pv_provider.resolve_credentials(cfg)

    out_dir = args.out_dir or os.path.join(resolve_tessie_data_home(), "energy_reports")
    os.makedirs(out_dir, exist_ok=True)

    agl_conn = get_usage_db_connection(provider)
    pv_conn = get_pv_db_connection()
    try:
        if not pv_api_key or not pv_account_id:
            print(f"({pv_provider.display_name} not configured in config.json - reporting AGL data only; "
                  "self-consumption savings and the generation/usage chart lines will be empty)")
        elif not args.no_fetch:
            fetched = sync_daily_range(pv_conn, start, end, pv_provider, pv_api_key, pv_account_id, log=print)
            if fetched:
                print(f"  Fetched {fetched} new {pv_provider.display_name} day(s)")

        pv_by_day = query_daily_range(pv_conn, start, end)
        daily = build_daily_table(agl_conn, pv_by_day, start, end)
        conflicts = export_conflicts(daily)
        self_cons_savings, have_days, missing_days = self_consumption_savings(daily, periods, provider)
        bill = reconstruct_bill(start, end, periods=periods, log=None)

        print_summary(start, end, label, daily, bill, self_cons_savings, have_days, missing_days, conflicts)

        html_path = os.path.join(out_dir, f"energy_report_{label}.html")
        render_html_chart(daily, start, end, label, bill, self_cons_savings, html_path)
        print(f"\nWrote {html_path}")

        month_rows = []
        for m_start, m_end, m_label in month_bounds_within(start, end):
            m_bill = reconstruct_bill(m_start, m_end, periods=periods, log=None)
            m_daily = [r for r in daily if m_start <= r["date"] <= m_end]
            m_savings, _, _ = self_consumption_savings(m_daily, periods, provider)
            no_solar_ex_gst = m_bill["usage_ex_gst"] + m_savings + m_bill["supply_ex_gst"] + m_bill["demand_ex_gst"]
            month_rows.append({
                "label": m_label,
                "actual_incl_gst": m_bill["total_incl_gst"],
                "no_solar_incl_gst": round(no_solar_ex_gst * 1.10, 2),
            })

        png_path = os.path.join(out_dir, f"energy_report_{label}.png")
        if render_png_chart(month_rows, label, png_path):
            print(f"Wrote {png_path}")
        else:
            print("(matplotlib not installed - skipped the PNG chart; HTML chart and text summary above are unaffected)")
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
