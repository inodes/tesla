#!/usr/bin/env python3
"""
cost_per_100km_report.py - fuel car vs EV, cost per 100km over time,
urban and highway (HTML + PNG), REQ-157 (2026-09-28) + REQ-160
(2026-09-28 correction below).

User: "as a separate type of graph plot a selected example fuel car
and my EV simulating the cost per 100km over time for urban & highway.
This would be 4 lines over time. a) cost per km for fuel car on
highway at set fuel price (NSW prices) b) cost per km for fuel car on
urban at set fuel price (NSW prices) c) cost per km for EV car on
highway at set electricity price d) cost per km for EV car on urban at
set electricity price."

REQ-160 correction: the first version priced BOTH EV lines off the
real AGL home usage rate, which the user correctly called out - "Why
is cost on highway so low. I think what is genuinely required is
using highway against Tesla Supercharging costs." The real-world
driving pattern this models: home (AGL) charging for urban/around-town
use, Supercharging on an actual highway trip. So as of REQ-160:
- EV Urban still prices against the real AGL home usage rate
  (price_history_report.build_electricity_series() unchanged).
- EV Highway now prices against lib.supercharger_pricing's real AU
  Supercharger rate history instead - the user's own 3 historical
  anchor points (Jan 2019 $0.35, late Jan 2019 $0.42, Oct 2020 $0.52)
  plus published price-change coverage found by web search, stepping
  forward to this account's own real, empirically-observed current
  daytime Supercharger rate once real sessions exist (see
  lib/supercharger_pricing.py's own module docstring for full
  citations and the daytime-vs-night split found in this account's
  own real data).

Four lines, all in $/100km (one shared axis - dataviz: never a dual
axis):
- <fuel car> Urban / Highway = that vehicle's own real urban/highway
  L/100km (lib.fuel_common's comparison_vehicles registry -
  get_comparison_vehicle(), same --vehicle selection as
  price_history_report.py) x the real NSW fuel price for its own fuel
  type on each date (reuses price_history_report.build_fuel_series()
  unchanged - fuel is bought at the same market rate regardless of
  urban/highway driving, so no correction needed on this side).
- <EV> Urban = this account's own real, empirically-measured urban
  kWh/100km (lib.ev_efficiency.compute_ev_urban_highway_efficiency() -
  task #56/REQ-159) x the real AGL home usage rate on each date.
- <EV> Highway = this account's own real, empirically-measured highway
  kWh/100km x the real/published Supercharger rate on each date
  (lib.supercharger_pricing.supercharger_rate_for_date() - REQ-160).

Deliberately a FIXED consumption/efficiency figure for each vehicle
multiplied by a real price that moves over time, not a re-measured
efficiency per date - matches the user's own "at set fuel price"/"at
set electricity price" framing: this chart isolates how much of the
cost swing is pure price movement, holding both vehicles' own real
efficiency constant.

Date range: same default as price_history_report.py (2020-03-06,
AGL's real confirmed rate history start, through today) - override
with --from/--to. lib.supercharger_pricing's own published rate
history reaches back further (2017) for anyone who widens --from past
2020-03-06.

Usage:
    ./cost_per_100km_report.py
    ./cost_per_100km_report.py --vehicle "ranger"
    ./cost_per_100km_report.py --from 2021-01-01 --to 2025-01-01
"""

import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.tessie_api_common import resolve_tessie_data_home, load_vehicle_spec  # noqa: E402
from lib.fuel_common import (  # noqa: E402
    get_db_connection as get_fuel_db_connection,
    get_comparison_vehicle,
    normalize_fuel_code,
)
from lib.ev_efficiency import compute_ev_urban_highway_efficiency  # noqa: E402
from lib.supercharger_pricing import (  # noqa: E402
    load_real_supercharger_sessions,
    load_published_rate_history,
    current_regime_rate,
    supercharger_rate_for_date,
)
from lib.analysis_deepdive_charts import write_interactive_chart  # noqa: E402
from price_history_report import (  # noqa: E402
    daterange,
    build_fuel_series,
    build_electricity_series,
    DEFAULT_START_DATE,
    SURFACE, TEXT_PRIMARY, TEXT_SECONDARY, GRID, CATEGORICAL,
)


def compute_cost_series(fuel_l_per_100km, fuel_prices, ev_kwh_per_100km, elec_prices):
    """$/100km series - a plain elementwise multiply, kept as its own
    function so a None price/consumption figure (a date the underlying
    series couldn't resolve at all) produces a None cost rather than a
    silently wrong number."""
    fuel_cost = [
        (fuel_l_per_100km * p) if (fuel_l_per_100km is not None and p is not None) else None
        for p in fuel_prices
    ]
    ev_cost = [
        (ev_kwh_per_100km * p) if (ev_kwh_per_100km is not None and p is not None) else None
        for p in elec_prices
    ]
    return fuel_cost, ev_cost


def build_supercharger_series(dates, daytime_only=True):
    """One Supercharger $/kWh rate per date in `dates` - REQ-160:
    lib.supercharger_pricing's own step-function history (published AU
    price changes from charges/supercharger_rate_history.json - REQ-161
    moved these out of that module's own source code - then, for the
    CURRENT still-open real-data regime only (REQ-162 -
    current_regime_rate(), not an all-time blend), this account's own
    real average), resolved once per date rather than re-reading
    charges_master.csv, re-reading the rate-history JSON, or
    re-averaging on every call."""
    real_sessions = load_real_supercharger_sessions()
    published_history = load_published_rate_history()
    current_rate, current_n, _since = current_regime_rate(
        daytime_only=daytime_only, real_sessions=real_sessions, published_history=published_history,
    )
    return [
        supercharger_rate_for_date(
            d, daytime_only=daytime_only, real_sessions=real_sessions,
            current_rate=current_rate, published_history=published_history,
        )
        for d in dates
    ], real_sessions, current_rate, current_n


def render_html(dates, fuel_name, fuel_urban, fuel_highway, ev_name, ev_urban, ev_highway,
                 fuel_l_per_100km, ev_kwh_per_100km, supercharger_n, label, out_path):
    x_values = [d.strftime("%Y-%m-%d") for d in dates]
    series = [
        {"name": f"{fuel_name} - Urban", "unit": "$/100km", "axis": "left",
         "color": CATEGORICAL[0], "values": fuel_urban},
        {"name": f"{fuel_name} - Highway", "unit": "$/100km", "axis": "left",
         "color": CATEGORICAL[1], "values": fuel_highway},
        {"name": f"{ev_name} - Urban (AGL home)", "unit": "$/100km", "axis": "left",
         "color": CATEGORICAL[2], "values": ev_urban},
        {"name": f"{ev_name} - Highway (Supercharger)", "unit": "$/100km", "axis": "left",
         "color": CATEGORICAL[3], "values": ev_highway},
    ]
    supercharger_note = (
        f"this account's own real current daytime rate (n={supercharger_n} session(s))"
        if supercharger_n else "published AU Supercharger price history (no real sessions yet)"
    )
    subtitle = (
        f"{dates[0]} to {dates[-1]} &middot; {fuel_name}: "
        f"{fuel_l_per_100km['urban']:.1f} L/100km urban / {fuel_l_per_100km['highway']:.1f} L/100km highway "
        f"at real NSW FuelCheck prices &middot; {ev_name}: "
        f"{ev_kwh_per_100km['urban']:.1f} kWh/100km urban at the real AGL home usage rate incl. GST / "
        f"{ev_kwh_per_100km['highway']:.1f} kWh/100km highway at the Supercharger rate ({supercharger_note}) "
        f"- both efficiency figures empirically measured from real drives"
    )
    write_interactive_chart(
        out_path,
        title=f"Cost per 100km over time - {fuel_name} vs {ev_name}",
        x_values=x_values,
        x_label="Date",
        series=series,
        subtitle=subtitle,
        zoomable=True,
    )
    return out_path


def render_png(dates, fuel_name, fuel_urban, fuel_highway, ev_name, ev_urban, ev_highway, label, out_path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    fig, ax = plt.subplots(figsize=(12, 6), dpi=220)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    ax.plot(dates, fuel_urban, color=CATEGORICAL[0], linewidth=1.6, label=f"{fuel_name} - Urban", zorder=3)
    ax.plot(dates, fuel_highway, color=CATEGORICAL[1], linewidth=1.6, label=f"{fuel_name} - Highway", zorder=3)
    ax.plot(dates, ev_urban, color=CATEGORICAL[2], linewidth=1.6, label=f"{ev_name} - Urban (AGL home)", zorder=3)
    ax.plot(dates, ev_highway, color=CATEGORICAL[3], linewidth=1.6, label=f"{ev_name} - Highway (Supercharger)", zorder=3)

    ax.set_ylabel("$ AUD / 100km", color=TEXT_SECONDARY)
    ax.tick_params(axis="x", labelcolor=TEXT_SECONDARY)
    ax.tick_params(axis="y", labelcolor=TEXT_SECONDARY)
    ax.grid(True, axis="y", color=GRID, linewidth=0.8, zorder=1)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=TEXT_SECONDARY)
    ax.set_title(f"Cost per 100km over time - {fuel_name} vs {ev_name}", color=TEXT_PRIMARY,
                 fontsize=15, fontweight="bold", loc="left", pad=20)

    fig.savefig(out_path, facecolor=SURFACE, bbox_inches="tight", dpi=220)
    plt.close(fig)
    return True


def main():
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--vehicle", help="Comparison fuel vehicle key/name/substring "
                                           "(default: this repo's own default comparison vehicle)")
    parser.add_argument("--region", default="all", choices=["all", "metro", "regional"],
                         help="NSW FuelCheck region for the fuel car's price (default: all)")
    parser.add_argument("--from", dest="date_from", help="YYYY-MM-DD (default: 2020-03-06)")
    parser.add_argument("--to", dest="date_to", help="YYYY-MM-DD (default: today)")
    parser.add_argument("--out-dir", help="Where to write the report files (default: <Tesla data home>/price_reports)")
    args = parser.parse_args()

    start = date.fromisoformat(args.date_from) if args.date_from else DEFAULT_START_DATE
    end = date.fromisoformat(args.date_to) if args.date_to else date.today()
    if end > date.today():
        end = date.today()
    dates = daterange(start, end)
    label = f"{start.isoformat()}_to_{end.isoformat()}"

    veh_key, veh_spec = get_comparison_vehicle(args.vehicle)
    fuel_code = normalize_fuel_code(veh_spec.get("fuel_type"))
    fuel_name = veh_spec.get("name", veh_key)
    consumption = veh_spec.get("consumption", {})
    fuel_l_per_100km = {
        "urban": consumption.get("urban_l_per_100km"),
        "highway": consumption.get("highway_l_per_100km"),
    }
    if fuel_l_per_100km["urban"] is None or fuel_l_per_100km["highway"] is None:
        print(f"ERROR: comparison vehicle '{veh_key}' has no urban_l_per_100km/highway_l_per_100km "
              "in its consumption record - can't build urban/highway cost lines for it.")
        sys.exit(1)

    ev_profile = compute_ev_urban_highway_efficiency()
    ev_kwh_per_100km = {
        "urban": ev_profile["urban"]["avg_efficiency_kwh_per_100km"],
        "highway": ev_profile["highway"]["avg_efficiency_kwh_per_100km"],
    }
    if ev_kwh_per_100km["urban"] is None or ev_kwh_per_100km["highway"] is None:
        print("ERROR: not enough real drive telemetry yet to derive an empirical urban/highway "
              "EV efficiency split (see lib.ev_efficiency.compute_ev_urban_highway_efficiency()).")
        sys.exit(1)

    ev_vehicle_spec = load_vehicle_spec() or {}
    ev_name = (ev_vehicle_spec.get("vehicle") or {}).get("model") or "This EV"

    fuel_conn = get_fuel_db_connection()
    try:
        fuel_prices = build_fuel_series(fuel_conn, fuel_code, args.region, dates)
    finally:
        fuel_conn.close()
    elec_prices, elec_boundaries = build_electricity_series(dates)
    # REQ-160: highway EV cost prices against Supercharger, not the AGL
    # home rate - see lib.supercharger_pricing and this file's own
    # module docstring for why.
    # D8: report the n actually behind the rate being used (current
    # regime only), not every daytime session ever.
    supercharger_prices, supercharger_sessions, supercharger_current_rate, supercharger_n = \
        build_supercharger_series(dates)

    fuel_urban, ev_urban = compute_cost_series(
        fuel_l_per_100km["urban"], fuel_prices, ev_kwh_per_100km["urban"], elec_prices
    )
    fuel_highway, ev_highway = compute_cost_series(
        fuel_l_per_100km["highway"], fuel_prices, ev_kwh_per_100km["highway"], supercharger_prices
    )

    out_dir = args.out_dir or os.path.join(resolve_tessie_data_home(), "price_reports")
    os.makedirs(out_dir, exist_ok=True)

    print(f"=== Cost per 100km over time: {fuel_name} vs {ev_name} ({start} to {end}) ===\n")
    print(f"{fuel_name}: {fuel_l_per_100km['urban']:.1f} L/100km urban, "
          f"{fuel_l_per_100km['highway']:.1f} L/100km highway ({fuel_code})")
    print(f"{ev_name}: {ev_kwh_per_100km['urban']:.1f} kWh/100km urban (priced at AGL home rate), "
          f"{ev_kwh_per_100km['highway']:.1f} kWh/100km highway (priced at Supercharger rate) - "
          f"empirical, {ev_profile['urban']['total_distance_km']:.0f} km urban / "
          f"{ev_profile['highway']['total_distance_km']:.0f} km highway sampled")
    if supercharger_n and supercharger_current_rate is not None:
        print(f"Supercharger current daytime rate: ${supercharger_current_rate:.3f}/kWh "
              f"(this account's own real average, n={supercharger_n} daytime session(s))")
    else:
        print("Supercharger current rate: no real daytime sessions yet - using published AU price history only")
    print()
    for name, series in (("Fuel car - Urban", fuel_urban), ("Fuel car - Highway", fuel_highway),
                          ("EV - Urban", ev_urban), ("EV - Highway", ev_highway)):
        real = [v for v in series if v is not None]
        if real:
            latest = f"${series[-1]:.2f}" if series[-1] is not None else "n/a"
            print(f"{name:<22} ${min(real):.2f} - ${max(real):.2f} /100km (latest: {latest})")

    html_path = os.path.join(out_dir, f"cost_per_100km_{label}.html")
    render_html(dates, fuel_name, fuel_urban, fuel_highway, ev_name, ev_urban, ev_highway,
                fuel_l_per_100km, ev_kwh_per_100km, supercharger_n, label, html_path)
    print(f"\nWrote {html_path}")

    png_path = os.path.join(out_dir, f"cost_per_100km_{label}.png")
    if render_png(dates, fuel_name, fuel_urban, fuel_highway, ev_name, ev_urban, ev_highway, label, png_path):
        print(f"Wrote {png_path}")
    else:
        print("(matplotlib not installed - skipped the PNG chart; HTML chart and text summary above are unaffected)")


if __name__ == "__main__":
    main()
