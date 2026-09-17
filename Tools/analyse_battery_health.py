#!/usr/bin/env python3
"""analyse_battery_health.py - Battery-health degradation scatterplots
(capacity vs odometer, range vs odometer), from real battery_health_log.json
history.

Context: sync_api_tessie.py --battery-health has been pulling the
vehicle's full real battery_health history for a while now (REQ-045/047),
and analyse_car_efficiency.py already reads that history
(load_battery_health_history()) to pick a live usable-capacity figure for
its speed-vs-range table - but nothing in this repo ever actually CHARTS
that history. This is the first script that does: a real degradation
view, not just a single current-vs-original number.

Plots each real reading as a point - deliberately NOT connected by a line
between consecutive readings, since a raw battery_health reading is noisy
(BMS estimate variance from ambient temp, SoC at the moment of the poll,
etc. - visible in the real data as a +/-1kWh wobble from one day to the
next even while the underlying trend is flat-to-slowly-declining) and a
connected line would visually overstate every up-tick/down-tick as a real
swing. A linear trend line (simple least-squares fit vs odometer) is
drawn instead to show the actual direction without pretending the raw
noise is signal.

TWO SEPARATE charts, both vs Odometer (km) on the x-axis (the
degradation-relevant variable - a battery fades with distance/cycles, not
calendar time), not one combined dual-axis chart:
  - Usable capacity (kWh) vs odometer.
  - Max rated range (km) vs odometer.
Originally built as one chart with capacity on the left axis and range on
the right (REQ-052) - the user's own feedback after seeing it (with a
reference screenshot of a Tesla-style fleet app doing exactly this as two
separate charts) was that capacity and range are strongly correlated
TODAY, but that's an artifact of efficiency being roughly constant so far
- if efficiency itself drifts (seasonal, driving-mix changes, etc.) the
two would decouple, and a shared/dual axis would then be actively
misleading about which one is doing what. Two independent single-axis
charts read correctly regardless of whether that correlation holds.

Skips any reading with no odometer_km (a handful of live-poll snapshots
came back without one - see battery_health_log.json) since they can't be
placed on this x-axis; reported as excluded rather than silently dropped.
The range chart further skips any reading with no max_range_km of its own
(rare, but not guaranteed to align with which readings have an odometer).

Output goes to <tessie_directory>/battery/ (a dedicated subfolder), not
the Tessie directory root - matching the charges/deepdive and drives
output convention elsewhere in this repo.

The HTML is real SVG (not a <canvas>), with every point getting its own
generous invisible hit-circle at its exact plotted coordinates - the same
approach as the one-off battery_health_scatter.html prototype the user
compared this against. A first attempt kept the canvas rendering and
patched the hit-test/tooltip-position logic instead; the user tried it
and found it still less clear than the SVG prototype, so this rewrites
the HTML output as SVG outright rather than continuing to patch canvas
hit-testing around the edges.

Usage:
    ./analyse_battery_health.py                    # writes to <tessie_directory>/battery/
    ./analyse_battery_health.py --tessie-dir DIR    # override the Tessie data home
    ./analyse_battery_health.py --out-dir DIR       # override just the output location
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyse_drives import TessieAnalyzer  # noqa: E402
from analyse_car_efficiency import load_battery_health_history  # noqa: E402

# --- dataviz reference palette (light mode) - matches analyse_charge_curves.py
# so every chart in this repo reads as one system (AGENTS.md / dataviz skill).
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
TEXT_MUTED = "#898781"
GRID = "#e1e0d9"
S1_BLUE = "#2a78d6"
S3_AQUA = "#1baf7a"


def _linear_fit(xs, ys):
    """Least-squares slope/intercept for a simple y = m*x + b trend line.
    Returns None if there are fewer than 2 points or x has no spread
    (can't fit a line to a single odometer value)."""
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None
    m = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
    b = mean_y - m * mean_x
    return m, b


def load_points(tessie_dir=None):
    """Real (odometer_km, capacity_kwh, max_range_km, synced_at) points,
    oldest-to-newest, plus counts of readings excluded for having no
    odometer_km or being flagged as statistical outliers. Reuses
    analysis_car_efficiency's own history loader (battery_health_log.json + any
    manually-exported CSV, deduplicated) rather than re-reading/re-parsing the
    log a second way."""
    analyzer = TessieAnalyzer(tessie_dir=tessie_dir)
    history = load_battery_health_history(analyzer.tessie_data_home, include_outliers=True) or []
    points, excluded_no_odo, excluded_outliers = [], 0, 0
    for e in history:
        odo = e.get("odometer_km")
        cap = e.get("capacity_kwh")
        # Check "no odometer" before "outlier": a reading with no odometer_km
        # was never going to appear as a plotted point on this chart either
        # way, so it should count against that bucket, not against
        # "outliers excluded" - otherwise the outlier count on this chart
        # can include readings the user could never have seen removed from
        # it, making the caption look more aggressive than what it actually
        # trimmed off the visible trend line.
        if odo is None or cap is None:
            excluded_no_odo += 1
            continue
        if e.get("is_outlier") is True:
            excluded_outliers += 1
            continue
        points.append({
            "odometer_km": float(odo),
            "capacity_kwh": float(cap),
            "max_range_km": e.get("max_range_km"),
            "synced_at": e.get("synced_at"),
        })
    points.sort(key=lambda p: p["odometer_km"])
    return points, excluded_no_odo, excluded_outliers, history, analyzer.tessie_data_home


def _metric_trend_str(pts, key, unit, decimals=2):
    """'first -> latest (delta) across N reading(s)' for whichever metric
    key ('capacity_kwh' or 'max_range_km') pts already carries, sorted by
    odometer. None with fewer than 2 points - a "trend" from one point
    isn't one."""
    if len(pts) < 2:
        return None
    first, latest = pts[0][key], pts[-1][key]
    return (f"{first:.{decimals}f} -> {latest:.{decimals}f} {unit} "
            f"({latest - first:+.{decimals}f} {unit}) across {len(pts)} reading(s)")


def build_subtitle(pts, key, unit, excluded_note, date_range):
    parts = [f"{len(pts)} real reading(s) with odometer data"
             + (f" ({excluded_note})" if excluded_note else "")]
    trend = _metric_trend_str(pts, key, unit)
    if trend:
        parts.append(f"{trend} ({date_range[0][:10]} to {date_range[1][:10]})")
    return "\n".join(parts)


def render_png(pts, key, unit, label, color, title, subtitle, out_png):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    odo = [p["odometer_km"] for p in pts]
    vals = [p[key] for p in pts]

    FIG_DPI = 220
    fig, ax = plt.subplots(figsize=(10, 6), dpi=FIG_DPI)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    ax.scatter(odo, vals, s=40, color=color, edgecolors=SURFACE, linewidths=1.2, zorder=4)
    fit = _linear_fit(odo, vals)
    if fit:
        m, b = fit
        ax.plot([min(odo), max(odo)], [m * min(odo) + b, m * max(odo) + b],
                 color=color, linewidth=2, linestyle=(0, (5, 3)), alpha=0.55, zorder=3,
                 label="Trend (least-squares)")
        ax.legend(loc="lower right", frameon=False, fontsize=9, labelcolor=TEXT_SECONDARY)

    ax.set_xlabel("Odometer (km)", color=TEXT_SECONDARY)
    ax.set_ylabel(f"{label} ({unit})", color=TEXT_SECONDARY)
    ax.tick_params(axis="both", labelcolor=TEXT_SECONDARY)
    ax.grid(True, color=GRID, linewidth=0.8, zorder=1)
    for spine in ax.spines.values():
        spine.set_color(GRID)

    ax.set_title(title, color=TEXT_PRIMARY, fontsize=15, fontweight="bold", loc="left", pad=32)
    fig.text(0.125, 0.905, subtitle, color=TEXT_SECONDARY, fontsize=9)

    fig.savefig(out_png, facecolor=SURFACE, bbox_inches="tight", dpi=FIG_DPI)
    plt.close(fig)
    return True


_HTML_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: {surface}; color: {text_primary}; }}
  .viz-root {{ padding: 20px 24px 18px; max-width: 1100px; margin: 0 auto; box-sizing: border-box; }}
  h1 {{ font-size: 18px; margin: 0 0 4px; font-weight: 700; }}
  .sub {{ margin: 0 0 14px; color: {text_secondary}; font-size: 13px; white-space: pre-line; }}
  .chart-wrap {{ position: relative; width: 100%; }}
  svg {{ display: block; width: 100%; height: auto; overflow: visible; }}
  .grid-line {{ stroke: {grid}; stroke-width: 1; }}
  .axis-text {{ fill: {text_secondary}; font-size: 12px; }}
  .axis-label {{ fill: {text_secondary}; font-size: 13px; }}
  .trend-line {{ stroke: {color}; stroke-width: 2; stroke-dasharray: 6 4; opacity: 0.55; fill: none; stroke-linecap: round; }}
  .dot {{ fill: {color}; stroke: {surface}; stroke-width: 1.4; }}
  .hit {{ fill: transparent; cursor: pointer; }}
  #tooltip {{
    position: absolute; pointer-events: none; background: rgba(11,11,11,0.92); color: #fff;
    padding: 8px 10px; border-radius: 6px; font-size: 12px; line-height: 1.5; display: none;
    white-space: nowrap; z-index: 10; transform: translate(-50%, -100%); top: 0; left: 0;
  }}
  #tooltip .val {{ font-weight: 700; font-size: 14px; }}
  #legend {{ margin: 8px 0 0; font-size: 12px; color: {text_muted}; }}
</style>
</head>
<body>
<div class="viz-root">
<h1>{title}</h1>
<div class="sub">{subtitle}</div>
<div class="chart-wrap">
  <svg id="chart" viewBox="0 0 1000 520" preserveAspectRatio="xMidYMid meet"></svg>
  <div id="tooltip"></div>
</div>
<div id="legend">- - - trend line (least-squares fit vs odometer)</div>
</div>
<script>
const DATA = {points_json};
const TREND = {trend_json};
const UNIT = {unit_json};
const Y_LABEL = {y_label_json};

const W = 1000, H = 520;
const pad = {{ top: 20, right: 28, bottom: 46, left: 64 }};
const plotW = W - pad.left - pad.right, plotH = H - pad.top - pad.bottom;

const xs = DATA.map(d => d.odo);
const ys = DATA.map(d => d.val);
const xLo = Math.min(...xs), xHi = Math.max(...xs);
let yLo = Math.min(...ys), yHi = Math.max(...ys);
if (yLo === yHi) {{ yLo -= 1; yHi += 1; }}
const yPad = (yHi - yLo) * 0.15;
yLo -= yPad; yHi += yPad;

function xAt(v) {{ return pad.left + (xHi === xLo ? plotW / 2 : (v - xLo) / (xHi - xLo) * plotW); }}
function yAt(v) {{ return pad.top + plotH - (v - yLo) / (yHi - yLo) * plotH; }}

const svg = document.getElementById('chart');
const ns = 'http://www.w3.org/2000/svg';
function el(tag, attrs) {{
  const e = document.createElementNS(ns, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}}

const yTicks = 5;
for (let g = 0; g <= yTicks; g++) {{
  const v = yLo + (yHi - yLo) * g / yTicks;
  const y = yAt(v);
  svg.appendChild(el('line', {{ class: 'grid-line', x1: pad.left, x2: W - pad.right, y1: y, y2: y }}));
  const t = el('text', {{ class: 'axis-text', x: pad.left - 10, y: y + 4, 'text-anchor': 'end' }});
  t.textContent = v.toFixed(1);
  svg.appendChild(t);
}}
const yLabel = el('text', {{ class: 'axis-label', x: -H / 2, y: 18, 'text-anchor': 'middle', transform: 'rotate(-90)' }});
yLabel.textContent = Y_LABEL;
svg.appendChild(yLabel);

const xTicks = 8;
for (let i = 0; i <= xTicks; i++) {{
  const v = xLo + (xHi - xLo) * i / xTicks;
  const x = xAt(v);
  const t = el('text', {{ class: 'axis-text', x: x, y: H - pad.bottom + 20, 'text-anchor': 'middle' }});
  t.textContent = Math.round(v).toLocaleString();
  svg.appendChild(t);
}}
const xLabel = el('text', {{ class: 'axis-label', x: pad.left + plotW / 2, y: H - 8, 'text-anchor': 'middle' }});
xLabel.textContent = 'Odometer (km)';
svg.appendChild(xLabel);

if (TREND) {{
  svg.appendChild(el('line', {{
    class: 'trend-line', x1: xAt(TREND.x0), y1: yAt(TREND.y0), x2: xAt(TREND.x1), y2: yAt(TREND.y1),
  }}));
}}

const tooltip = document.getElementById('tooltip');

function fmtDate(iso) {{
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, {{ year: 'numeric', month: 'short', day: 'numeric' }});
}}

DATA.forEach(d => {{
  const cx = xAt(d.odo), cy = yAt(d.val);
  svg.appendChild(el('circle', {{ class: 'dot', cx, cy, r: 5 }}));
  const hit = el('circle', {{ class: 'hit', cx, cy, r: 13 }});
  hit.addEventListener('pointerenter', () => showTooltip(d, cx, cy));
  hit.addEventListener('pointerleave', hideTooltip);
  svg.appendChild(hit);
}});

function showTooltip(d, cx, cy) {{
  tooltip.innerHTML = '';
  const valEl = document.createElement('div');
  valEl.className = 'val';
  valEl.textContent = d.val.toFixed(2) + ' ' + UNIT;
  const metaEl = document.createElement('div');
  metaEl.textContent = Math.round(d.odo).toLocaleString() + ' km' + (d.t ? ' \\u00b7 ' + fmtDate(d.t) : '');
  tooltip.appendChild(valEl);
  tooltip.appendChild(metaEl);
  const scale = svg.getBoundingClientRect().width / W;
  tooltip.style.left = (cx * scale) + 'px';
  tooltip.style.top = ((cy * scale) - 12) + 'px';
  tooltip.style.display = 'block';
}}
function hideTooltip() {{ tooltip.style.display = 'none'; }}
</script>
</body>
</html>
"""


def render_html(pts, key, unit, label, color, title, subtitle, out_html):
    points = [{"odo": p["odometer_km"], "val": p[key], "t": p["synced_at"]} for p in pts]
    odo = [p["odometer_km"] for p in pts]
    vals = [p[key] for p in pts]

    trend = None
    fit = _linear_fit(odo, vals)
    if fit:
        m, b = fit
        trend = {"x0": min(odo), "y0": m * min(odo) + b, "x1": max(odo), "y1": m * max(odo) + b}

    html = _HTML_TEMPLATE.format(
        title=title,
        subtitle=subtitle.replace("\n", "&#10;"),
        points_json=json.dumps(points),
        trend_json=json.dumps(trend),
        unit_json=json.dumps(unit),
        y_label_json=json.dumps(f"{label} ({unit})"),
        surface=SURFACE, text_primary=TEXT_PRIMARY, text_secondary=TEXT_SECONDARY,
        text_muted=TEXT_MUTED, grid=GRID, color=color,
    )
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    parser = argparse.ArgumentParser(
        usage="analyse_battery_health.py [--tessie-dir DIR] [--out-dir DIR]\n",
        description="""
NAME
    analyse_battery_health.py - chart battery_health history (capacity & range vs odometer)

DESCRIPTION
    Charts real battery_health history (capacity vs odometer, range vs
    odometer) as two scatterplots, each with its own trend line.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES
  analyse_battery_health.py
  analyse_battery_health.py --out-dir ~/iCloud/Tesla/Tessie/battery
"""
    )
    parser.add_argument("--tessie-dir", help="Custom path to directory containing Tessie CSV exports / battery_health_log.json")
    parser.add_argument("--out-dir", help="Where to write the chart files (default: <tessie_directory>/battery/, matching the charges/drives output convention)")
    args = parser.parse_args()

    points, excluded_no_odo, excluded_outliers, history, tessie_data_home = load_points(tessie_dir=args.tessie_dir)
    if len(points) < 2:
        print(f"Only {len(points)} real reading(s) with odometer data - need at least 2 to chart a trend. "
              f"Run ./sync_api_tessie.py --battery-health to sync more.")
        return

    if args.out_dir:
        out_dir = args.out_dir
    else:
        out_dir = os.path.join(tessie_data_home, "battery") if tessie_data_home else "battery"
    os.makedirs(out_dir, exist_ok=True)

    date_range = (points[0]["synced_at"], points[-1]["synced_at"])
    range_points = [p for p in points if p["max_range_km"] is not None]
    range_excluded = len(points) - len(range_points)

    excluded_notes = []
    if excluded_no_odo:
        excluded_notes.append(f"{excluded_no_odo} excluded - no odometer recorded")
    if excluded_outliers:
        excluded_notes.append(f"{excluded_outliers} outlier{'s' if excluded_outliers > 1 else ''} excluded")
    cap_excluded_note = " · ".join(excluded_notes) if excluded_notes else None

    range_notes = list(excluded_notes)
    if range_excluded:
        range_notes.append(f"{range_excluded} more excluded - no range recorded")
    range_excluded_note = " · ".join(range_notes) if range_notes else None

    charts = [
        ("capacity_kwh", "kWh", "Usable capacity", S1_BLUE, "battery_capacity_vs_odometer", points,
         cap_excluded_note),
        ("max_range_km", "km", "Max rated range", S3_AQUA, "battery_range_vs_odometer", range_points,
         range_excluded_note),
    ]
    written = []
    for key, unit, label, color, stem, pts, excluded_note in charts:
        if len(pts) < 2:
            print(f"Skipping {label} chart - only {len(pts)} real reading(s) have this field.")
            continue
        title = f"{label} vs odometer"
        subtitle = build_subtitle(pts, key, unit, excluded_note, date_range)
        out_png = os.path.join(out_dir, f"{stem}.png")
        out_html = os.path.join(out_dir, f"{stem}.html")
        if render_png(pts, key, unit, label, color, title, subtitle, out_png):
            written.append(out_png)
        render_html(pts, key, unit, label, color, title, subtitle, out_html)
        written.append(out_html)
        print(f"{label}: {_metric_trend_str(pts, key, unit)}")

    print("\n" + "\n".join(f"Wrote {p}" for p in written))


if __name__ == "__main__":
    main()
