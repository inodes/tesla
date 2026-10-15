#!/usr/bin/env python3
"""
price_history_report.py - fuel + electricity price history over time
(HTML + PNG), REQ-156 (2026-09-28).

User: "with fuel prices and electricity prices - is there a way to
graph these overtime in the usual html/png way you do? ... plotting
should be defaulting to the major varieties. But we should be able to
1. Choose the car to plot against as an example 2. Plot that fuel type
price 3. Plot electricity."

Fuel prices: real NSW FuelCheck daily averages (lib/fuel_common.py's
fuel_daily_averages table, synced by sync_fuel_history.py) - by
default the three "major varieties" already treated as this repo's
own reference trio elsewhere (lib/fuel_common.py's "Ref Fuel Prices"
line, sync_fuel_history.py's summary line): U91, P98, Diesel. Pass
--vehicle to instead show just ONE comparison vehicle's own fuel type
(e.g. --vehicle "ranger" for the Ford Ranger's Diesel) - the same
registry --vehicle already selects from elsewhere in this repo
(lib.fuel_common.get_comparison_vehicle()).

Electricity price: the real AGL usage rate in effect on each date,
resolved through the SAME rate-resolution helper every other cost
figure in this repo goes through (providers_power's own
usage_rate_for_window()) rather than a raw usage_rate_per_kwh field
read - deliberately, because the newest real tariff period
(2026-09-07 onward - see providers_power/power_agl.py's own
usage_rate_for_window() docstring, REQ-155) has NO flat usage_rate_per_kwh at all,
only a genuine peak/off-peak time-of-use split, so a raw field read
would silently come back empty for it. One representative blended
rate is computed per tariff period (across a fixed representative
week - AGL's own time-of-use classification depends only on weekday/
weekend + hour-of-day, never month/season, so any calendar week gives
the same blend) and that period's own real effective-date range then
fills every day within it - a correct step function, not a daily
recomputation of the same number ~2000+ times over.

Date range: defaults to 2020-03-06 through today. That's not a round
"7 years" - it's the exact date AGL's own real, invoice-confirmed rate
history in agl_tariff_rates.json starts (every earlier period is an
auto-proposed placeholder with usage_rate_per_kwh: None - see
AGENTS.local.md); anchoring here means every point on the electricity
line is real, not a guess, while still comfortably covering "back
further" on the fuel side per the user's own request. Override with
--from/--to.

Usage:
    ./price_history_report.py
    ./price_history_report.py --vehicle "ranger"
    ./price_history_report.py --from 2018-01-01 --to 2025-01-01
    ./price_history_report.py --out-dir ~/somewhere
"""

import argparse
import bisect
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.tessie_api_common import resolve_tessie_data_home  # noqa: E402
from lib.fuel_common import (  # noqa: E402
    get_db_connection as get_fuel_db_connection,
    get_fuel_price,
    normalize_fuel_code,
    get_comparison_vehicle,
)
from power_demand_calculator import load_rate_periods  # noqa: E402
from providers_power import get_power_provider  # noqa: E402
from lib.analysis_deepdive_charts import write_interactive_chart  # noqa: E402

# --- dataviz reference palette (light mode) - matches power_solar_report.py,
# analyse_battery_health.py etc. elsewhere in this repo. Fixed categorical
# order (dataviz skill: "assign categorical hues in fixed order, never
# cycled") - S1 is always the first series shown, regardless of which mode
# (default multi-fuel vs --vehicle) picked what that series actually is.
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e1e0d9"
S1_BLUE = "#2a78d6"
S2_ORANGE = "#eb6834"
S3_AQUA = "#1baf7a"
S4_YELLOW = "#eda100"
CATEGORICAL = [S1_BLUE, S2_ORANGE, S3_AQUA, S4_YELLOW]

DEFAULT_START_DATE = date(2020, 3, 6)
DEFAULT_FUEL_CODES = ["U91", "P98", "DL"]
FUEL_DISPLAY_NAMES = {
    "U91": "U91", "P95": "P95", "P98": "P98", "DL": "Diesel",
    "PDL": "Premium Diesel", "E10": "E10", "LPG": "LPG",
    "B20": "Biodiesel (B20)", "CNG": "CNG",
}

# 2026-09-30 review (D2/D7): the blended rate used to come from one fixed
# week, 2024-01-01 - a NSW public holiday, in a peak-season month - for
# every date, and was plotted ex-GST next to GST-inclusive pump prices.
# Now each date uses a normal mid-month week from its OWN month (so peak
# and off-peak seasons differ correctly) and the rate includes GST.
GST_MULTIPLIER = 1.10


def _representative_week(d):
    """A 7-day window starting the first Tuesday on/after the 15th of d's
    month - clear of the 1st-of-month and Monday public holidays."""
    start = datetime(d.year, d.month, 15)
    while start.weekday() != 1:  # Tuesday
        start += timedelta(days=1)
    return start, start + timedelta(days=7)


def daterange(start, end):
    days = (end - start).days
    return [start + timedelta(days=i) for i in range(days + 1)]


# Below this many real FuelCheck station readings for a given day, that
# day's own "average" is one or a handful of stations, not a real market
# rate - live-verified against this exact table (2022-03-21's Diesel/all
# row is a single n=1 sample at $1.239/L against a genuine ~$1.90-2.00/L
# neighborhood, a single-day spike/dip that would otherwise stand out on
# the chart as if the market itself moved) - excluded here in favour of
# the nearest day that clears the bar, same fallback shape as a real gap.
# 2026-09-28: live-verified against this exact table - a threshold of 5
# still let a genuine n=5 P98 outlier through (2022-03-17, $1.52/L vs a
# real ~$1.70-2.00/L neighborhood); 30 cleanly excludes every low-sample
# day found (a small real gap in the March 2022 FuelCheck sync, plus one
# isolated day in July 2022 - 14 days total across the whole U91/P98/DL
# x 2020-03-06-to-today range, so this costs almost nothing in coverage).
MIN_SAMPLE_COUNT = 30
# Longest gap (days) bridged with the nearest real reading - see build_fuel_series.
MAX_FILL_DAYS = 7


def build_fuel_series(conn, fuel_code, region, dates):
    """One price per date in `dates` for `fuel_code`/`region` - a single
    bulk SELECT of every real daily row actually in fuel_daily_averages
    with at least MIN_SAMPLE_COUNT real station readings behind it (fast -
    one query for the whole range, not one per date), then the same
    graceful-degradation shape get_fuel_price() uses for anything that
    bulk query didn't cover (a gap, a too-thin sample, or a date outside
    the synced range): nearest available real reading, via bisect on the
    sorted ISO date strings rather than an O(n) scan per date."""
    cur = conn.cursor()
    cur.execute(
        "SELECT date, avg_price_cents FROM fuel_daily_averages "
        "WHERE fuel_type = ? AND region = ? AND sample_count >= ?",
        (fuel_code, region, MIN_SAMPLE_COUNT),
    )
    by_date = {row[0]: row[1] / 100.0 for row in cur.fetchall() if row[1]}
    if not by_date:
        # No real rows at all for this fuel/region. This used to fall back
        # to get_fuel_price(), whose last resort is a hard-coded default
        # ($1.95 U91 etc.) - drawn as if it were FuelCheck data (2026-09-30
        # review, D6). Show nothing instead.
        return [None for _ in dates]

    known = sorted(by_date.keys())
    values = []
    for d in dates:
        d_str = d.strftime("%Y-%m-%d")
        exact = by_date.get(d_str)
        if exact is not None:
            values.append(exact)
            continue
        i = bisect.bisect_left(known, d_str)
        candidates = []
        if i < len(known):
            candidates.append(known[i])
        if i > 0:
            candidates.append(known[i - 1])
        nearest = min(candidates, key=lambda k: abs((date.fromisoformat(k) - d).days))
        # Only bridge short gaps; a date weeks away from any real reading
        # (e.g. the unpublished current month) is a gap, not a copy (D6).
        if abs((date.fromisoformat(nearest) - d).days) <= MAX_FILL_DAYS:
            values.append(by_date[nearest])
        else:
            values.append(None)
    return values


def build_electricity_series(dates):
    """One real AGL usage rate per date in `dates` - a representative
    blended rate computed ONCE per real dated tariff period (via the
    provider's own usage_rate_for_window(), see module docstring), then
    applied to every date whose real effective-date range that period
    covers. Returns (values, period_boundaries) - period_boundaries is
    every date where the rate actually changes, for annotating the
    chart."""
    provider = get_power_provider()
    periods = load_rate_periods()
    month_rate = {}

    def _rate(p, d):
        key = (id(p), d.year, d.month)
        if key not in month_rate:
            w_start, w_end = _representative_week(d)
            try:
                rate, _breakdown = provider.usage_rate_for_window(p, w_start, w_end)
            except Exception:
                rate = p.get("usage_rate_per_kwh")
            month_rate[key] = round(rate * GST_MULTIPLIER, 4) if rate is not None else None
        return month_rate[key]

    values = []
    last_period_id = None
    boundaries = []
    for d in dates:
        try:
            p = provider.rate_for_date(d, periods)
        except Exception:
            p = None
        rate = _rate(p, d) if p is not None else None
        values.append(rate)
        if p is not None and id(p) != last_period_id:
            boundaries.append(d)
            last_period_id = id(p)
    return values, boundaries


def resolve_fuel_lines(vehicle_query, region):
    """Returns [(fuel_code, display_name), ...] to plot - the default
    major-varieties trio, or (per --vehicle) just that one comparison
    vehicle's own fuel type."""
    if not vehicle_query:
        return [(code, FUEL_DISPLAY_NAMES.get(code, code)) for code in DEFAULT_FUEL_CODES]
    key, spec = get_comparison_vehicle(vehicle_query)
    fuel_code = normalize_fuel_code(spec.get("fuel_type"))
    display = f"{FUEL_DISPLAY_NAMES.get(fuel_code, fuel_code)} ({spec.get('name', key)})"
    return [(fuel_code, display)]


def render_html(dates, fuel_lines, fuel_series, elec_values, elec_boundaries, label, out_path):
    x_values = [d.strftime("%Y-%m-%d") for d in dates]
    series = []
    color_i = 0
    for (fuel_code, display), values in zip(fuel_lines, fuel_series):
        series.append({
            "name": display, "unit": "$/L", "axis": "left",
            "color": CATEGORICAL[color_i % len(CATEGORICAL)], "values": values,
        })
        color_i += 1
    series.append({
        "name": "Electricity (AGL usage rate, incl. GST)", "unit": "$/kWh", "axis": "left",
        "color": CATEGORICAL[color_i % len(CATEGORICAL)], "values": elec_values,
    })

    # write_interactive_chart's ref_lines are horizontal (a fixed y
    # value), not vertical date markers, so AGL rate-change dates
    # aren't passed that way - they're already visible as the
    # electricity line's own step shape, and called out in the
    # subtitle's period count below.
    subtitle = (
        f"{dates[0]} to {dates[-1]} &middot; fuel: real NSW FuelCheck daily averages &middot; "
        f"electricity: real AGL usage rate ({len(elec_boundaries)} rate period(s) in this window)"
    )
    write_interactive_chart(
        out_path,
        title=f"Fuel & electricity prices over time - {label}",
        x_values=x_values,
        x_label="Date",
        series=series,
        subtitle=subtitle,
        zoomable=True,
    )
    return out_path


def render_png(dates, fuel_lines, fuel_series, elec_values, label, out_path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    fig, ax = plt.subplots(figsize=(12, 6), dpi=220)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    color_i = 0
    for (fuel_code, display), values in zip(fuel_lines, fuel_series):
        ax.plot(dates, values, color=CATEGORICAL[color_i % len(CATEGORICAL)],
                linewidth=1.6, label=display, zorder=3)
        color_i += 1
    ax.plot(dates, elec_values, color=CATEGORICAL[color_i % len(CATEGORICAL)],
            linewidth=1.6, label="Electricity (AGL usage rate, incl. GST)", zorder=3)

    ax.set_ylabel("$ AUD (per L or per kWh)", color=TEXT_SECONDARY)
    ax.tick_params(axis="x", labelcolor=TEXT_SECONDARY)
    ax.tick_params(axis="y", labelcolor=TEXT_SECONDARY)
    ax.grid(True, axis="y", color=GRID, linewidth=0.8, zorder=1)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=TEXT_SECONDARY)
    ax.set_title(f"Fuel & electricity prices over time - {label}", color=TEXT_PRIMARY,
                 fontsize=15, fontweight="bold", loc="left", pad=20)

    fig.savefig(out_path, facecolor=SURFACE, bbox_inches="tight", dpi=220)
    plt.close(fig)
    return True


def main():
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--vehicle", help="Comparison vehicle key/name/substring - shows just that "
                                           "vehicle's own fuel type instead of the default major varieties")
    parser.add_argument("--region", default="all", choices=["all", "metro", "regional"],
                         help="NSW FuelCheck region for fuel prices (default: all)")
    parser.add_argument("--from", dest="date_from", help="YYYY-MM-DD (default: 2020-03-06, AGL's own real rate history start)")
    parser.add_argument("--to", dest="date_to", help="YYYY-MM-DD (default: today)")
    parser.add_argument("--out-dir", help="Where to write the report files (default: <Tesla data home>/price_reports)")
    args = parser.parse_args()

    start = date.fromisoformat(args.date_from) if args.date_from else DEFAULT_START_DATE
    end = date.fromisoformat(args.date_to) if args.date_to else date.today()
    if end > date.today():
        end = date.today()

    dates = daterange(start, end)
    label = f"{start.isoformat()}_to_{end.isoformat()}"

    fuel_lines = resolve_fuel_lines(args.vehicle, args.region)
    fuel_conn = get_fuel_db_connection()
    try:
        fuel_series = [build_fuel_series(fuel_conn, code, args.region, dates) for code, _ in fuel_lines]
    finally:
        fuel_conn.close()

    elec_values, elec_boundaries = build_electricity_series(dates)

    out_dir = args.out_dir or os.path.join(resolve_tessie_data_home(), "price_reports")
    os.makedirs(out_dir, exist_ok=True)

    print(f"=== Fuel & electricity price history: {start} to {end} ({len(dates)} day(s)) ===\n")
    for (code, display), values in zip(fuel_lines, fuel_series):
        real_values = [v for v in values if v is not None]
        if real_values:
            last_i = max(i for i, v in enumerate(values) if v is not None)
            print(f"{display:<28} ${min(real_values):.2f} - ${max(real_values):.2f} /L "
                  f"(latest real: ${values[last_i]:.2f}/L on {dates[last_i]})")
    real_elec = [v for v in elec_values if v is not None]
    if real_elec:
        last_i = max(i for i, v in enumerate(elec_values) if v is not None)
        print(f"{'Electricity (AGL, incl. GST)':<28} ${min(real_elec):.2f} - ${max(real_elec):.2f} /kWh "
              f"(latest: ${elec_values[last_i]:.2f}/kWh on {dates[last_i]}, {len(elec_boundaries)} rate period(s))")

    html_path = os.path.join(out_dir, f"price_history_{label}.html")
    render_html(dates, fuel_lines, fuel_series, elec_values, elec_boundaries, label, html_path)
    print(f"\nWrote {html_path}")

    png_path = os.path.join(out_dir, f"price_history_{label}.png")
    if render_png(dates, fuel_lines, fuel_series, elec_values, label, png_path):
        print(f"Wrote {png_path}")
    else:
        print("(matplotlib not installed - skipped the PNG chart; HTML chart and text summary above are unaffected)")


if __name__ == "__main__":
    main()
