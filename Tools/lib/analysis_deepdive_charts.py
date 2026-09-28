#!/usr/bin/env python3
"""
analysis_deepdive_charts.py - Self-contained interactive HTML line charts for
high-frequency telemetry (charging curves, drive elevation/efficiency
profiles), with zero external dependency (no plotly/matplotlib, no CDN
script) - the chart file has to keep working years from now with no
network at view time, and this sandbox has no network to install a
charting library into anyway. Plain vanilla JS + <canvas>, hover
tooltip, nothing else.

Usage: write_interactive_chart(output_path, title, x_values, x_label,
series) where `series` is a list of dicts:
    {"name": str, "unit": str, "axis": str,
     "color": "#rrggbb", "values": [float or None, ...]}
(values aligned 1:1 with x_values; None = no data at that x, series
just breaks there rather than interpolating across the gap - drawn as
a smooth curve through each contiguous run of real values, not a
straight-line polyline, so a real user report (2026-09-15) of a
"disjointed" drive chart doesn't recur just because the rendering
itself was jagged). "left" is the one gridlined axis; any other axis
id gets its own stacked tick-label column on the right (in that
axis's own series color) so two-plus differently-scaled metrics -
e.g. Elevation (m) / Efficiency (kWh/100km) / Speed (km/h) - can
share one chart without a shared/misleading scale.

Optional `locations`: a list of str-or-None the same length as
x_values (REQ AGENTS.local.md 2026-09-15: user - "while dragging
cursor along the timeline, it would be good to have an extra line
for where the car is at that point in time") - shown as an extra
line in the hover tooltip when the mouse is at that x position.
Omit (or pass None) for a chart with no location data (e.g. the
charging-curve chart, which doesn't move) - the tooltip simply
doesn't grow that line.

Optional `zoomable=True` (REQ-119, 2026-09-24 - user, looking at a
year-long daily chart: "The resolution is too high for a year. Should
provide rolling averages that change the more you zoom in"): drag on
the plot to zoom into an x-range, double-click to reset. Whenever
zoomable, the drawn line is a CENTERED ROLLING AVERAGE whose window
scales continuously with how many points are currently in view
(roughly points-in-view / 20, capped at 21) - zoomed out over a year
shows a smoothed line instead of a noisy 365-point zigzag; zoomed in
to a couple of weeks the window shrinks back to 1 (raw daily values).
The rolling average skips (never zeroes) null points, so a gap in the
underlying data still shows as a gap, just a shorter one than the raw
series would. Existing callers that don't pass zoomable get the exact
same rendering as before this REQ - the whole feature is additive and
opt-in, nothing changes unless a caller asks for it. Assumes ONE data
point per x-step is a meaningful smoothing unit (true for the daily
energy report this was built for) - a future zoomable chart at a
different cadence (hourly, 5-minute) should double check the window
math in windowForSpan() below still makes sense for it.
"""

import json
import os


_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: #f7f7f9; color: #1a1a1a; }}
  h1 {{ font-size: 18px; margin: 16px 20px 4px; }}
  .sub {{ margin: 0 20px 12px; color: #666; font-size: 13px; }}
 #wrap {{ position: relative; margin: 0 12px 12px; }}
  canvas {{ display: block; background: #fff; border: 1px solid #ddd; border-radius: 6px; }}
 #tooltip {{
    position: absolute; pointer-events: none; background: rgba(20,20,20,0.92); color: #fff;
    padding: 8px 10px; border-radius: 6px; font-size: 12px; line-height: 1.5; display: none;
    white-space: nowrap; z-index: 10;
  }}
 #legend {{ margin: 4px 20px 14px; font-size: 12px; }}
 #legend span {{ margin-right: 16px; }}
 #legend .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="sub">{subtitle}</div>
<div id="legend"></div>
<div id="zoomNote" style="margin:0 20px 10px;font-size:12px;color:#898781;"></div>
<div id="wrap">
  <canvas id="chart" width="1100" height="480"></canvas>
  <div id="tooltip"></div>
</div>
<script>
const DATA = {data_json};
const canvas = document.getElementById('chart');
const ctx = canvas.getContext('2d');
const tooltip = document.getElementById('tooltip');
const legend = document.getElementById('legend');
const zoomNote = document.getElementById('zoomNote');

const W = canvas.width, H = canvas.height;
const axisIds = [];
DATA.series.forEach(s => {{ if (!axisIds.includes(s.axis)) axisIds.push(s.axis); }});
const rightAxisIds = axisIds.filter(a => a !== 'left');
const padL = 60, padR = 20 + rightAxisIds.length * 60, padT = 16, padB = 40;
const plotW = W - padL - padR, plotH = H - padT - padB;

function axisRange(series, axis) {{
  let vals = [];
  const sList = series.filter(s => s.axis === axis);
  sList.forEach(s => s.values.forEach(v => {{ if (v !== null && v !== undefined) vals.push(v); }}));
  if (vals.length === 0) return [0, 1];
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (sList.some(s => s.fill_negative || s.name === 'Efficiency')) {{
    if (lo > 0) lo = 0; // ensure 0 baseline is visible for efficiency/regen
  }}
  if (lo === hi) {{ lo -= 1; hi += 1; }}
  const pad = (hi - lo) * 0.08;
  return [lo - pad, hi + pad];
}}

// REQ-119: zoom (drag-select on the plot, double-click to reset) plus a
// rolling average whose window scales with how much of the series is
// currently in view - "resolution changes the more you zoom in". Inert
// (view === the full raw data, window always 1) unless the caller passed
// zoomable=True, so every EXISTING chart (charging curves, drive
// profiles) renders identically to before this REQ.
let viewStartIdx = 0, viewEndIdx = DATA.x.length - 1;
let view = {{ x: DATA.x, series: DATA.series, window: 1 }};
let n = view.x.length;
let ranges = {{}};

function rollingAverage(values, window) {{
  if (window <= 1) return values.slice();
  const half = Math.floor(window / 2);
  const out = [];
  for (let i = 0; i < values.length; i++) {{
    let sum = 0, count = 0;
    for (let k = Math.max(0, i - half); k <= Math.min(values.length - 1, i + half); k++) {{
      const v = values[k];
      if (v !== null && v !== undefined) {{ sum += v; count++; }}
    }}
    out.push(count > 0 ? sum / count : null);
  }}
  return out;
}}

// Continuous, not tiered - the window shrinks smoothly as you zoom in
// rather than snapping between a few fixed levels. ~20 points per
// smoothing step, capped at 21 (about a 3-week window even at full
// zoom-out on daily data) so a very long range never over-smooths into
// a flat line.
function windowForSpan(spanPoints) {{
  return Math.max(1, Math.min(21, Math.round(spanPoints / 20)));
}}

function updateView() {{
  const rawX = DATA.x.slice(viewStartIdx, viewEndIdx + 1);
  const window = DATA.zoomable ? windowForSpan(rawX.length) : 1;
  const rawSeries = DATA.series.map(s => Object.assign({{}}, s, {{ values: s.values.slice(viewStartIdx, viewEndIdx + 1) }}));
  const smoothed = window <= 1 ? rawSeries : rawSeries.map(s => Object.assign({{}}, s, {{ values: rollingAverage(s.values, window) }}));
  view = {{ x: rawX, series: smoothed, window: window }};
  n = view.x.length;
  axisIds.forEach(a => {{ ranges[a] = axisRange(view.series, a); }});
}}
updateView();

function xAt(i) {{ return padL + (n <= 1 ? 0 : (i / (n - 1)) * plotW); }}

function yAt(v, axis) {{
  const [lo, hi] = ranges[axis] || ranges['left'] || [0, 1];
  return padT + plotH - ((v - lo) / (hi - lo)) * plotH;
}}

function axisColor(axis) {{
  const s = DATA.series.find(s => s.axis === axis);
  return s ? s.color : '#888';
}}

function draw() {{
  ctx.clearRect(0, 0, W, H);
  const [loL, hiL] = ranges['left'] || [0, 1];

 // gridlines + left axis labels
  ctx.strokeStyle = '#eee';
  ctx.fillStyle = '#888';
  ctx.font = '11px sans-serif';
  ctx.textAlign = 'right';
  const gridN = 5;
  for (let g = 0; g <= gridN; g++) {{
    const v = loL + (hiL - loL) * (g / gridN);
    const y = yAt(v, 'left');
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
    ctx.fillText(v.toFixed(1), padL - 8, y + 3);
  }}
  rightAxisIds.forEach((axis, ri) => {{
    const [lo, hi] = ranges[axis];
    const colX = W - padR + 8 + ri * 60;
    ctx.textAlign = 'left';
    ctx.fillStyle = axisColor(axis);
    for (let g = 0; g <= gridN; g++) {{
      const v = lo + (hi - lo) * (g / gridN);
      const y = yAt(v, axis);
      ctx.fillText(v.toFixed(1), colX, y + 3);
    }}
  }});
  ctx.fillStyle = '#888';

 // 0-baseline dashed line for right/efficiency axis if range crosses zero
  rightAxisIds.forEach(axis => {{
    const [lo, hi] = ranges[axis];
    if (lo < 0 && hi > 0) {{
      const y0 = yAt(0, axis);
      ctx.save();
      ctx.strokeStyle = '#64748b'; // clear slate dashed line
      ctx.lineWidth = 1.5;
      ctx.setLineDash([5, 4]);
      ctx.beginPath(); ctx.moveTo(padL, y0); ctx.lineTo(W - padR, y0); ctx.stroke();
      ctx.setLineDash([]);

 // Label at 0 on the right axis
      ctx.font = 'bold 11px sans-serif';
      ctx.fillStyle = '#16a34a';
      ctx.textAlign = 'left';
      ctx.fillText('0.0 (Regen ⬇)', W - padR + 8, y0 + 3);
      ctx.restore();
    }}
  }});

 // x-axis labels (sparse)
  ctx.textAlign = 'center';
  const xTicks = Math.min(8, n);
  for (let t = 0; t < xTicks; t++) {{
    const i = Math.round(t * (n - 1) / Math.max(1, xTicks - 1));
    ctx.fillText(view.x[i], xAt(i), H - padB + 16);
  }}
  ctx.fillStyle = '#444';
  ctx.textAlign = 'center';
  ctx.fillText(DATA.x_label, padL + plotW / 2, H - 6);

 // series lines & shaded regen below 0 (view.series - the currently
 // zoomed/smoothed data; identical to DATA.series when not zoomable)
  view.series.forEach(s => {{
    let runs = [];
    let curRun = [];
    for (let i = 0; i < n; i++) {{
      const v = s.values[i];
      if (v === null || v === undefined) {{
        if (curRun.length > 0) {{ runs.push(curRun); curRun = []; }}
      }} else {{
        curRun.push({{ i, x: xAt(i), y: yAt(v, s.axis), v }});
      }}
    }}
    if (curRun.length > 0) runs.push(curRun);

 // 1. Shaded area below 0 (Regenerative braking / descent)
    if (s.fill_negative) {{
      const [lo, hi] = ranges[s.axis];
      if (lo < 0) {{
        const y0 = yAt(0, s.axis);
        runs.forEach(run => {{
          if (run.length < 2) return;
          ctx.save();
          ctx.beginPath();
          ctx.rect(padL, y0, plotW, Math.max(1, padT + plotH - y0));
          ctx.clip();

          ctx.beginPath();
          ctx.moveTo(run[0].x, y0);
          ctx.lineTo(run[0].x, run[0].y);
          if (run.length === 2) {{
            ctx.lineTo(run[1].x, run[1].y);
          }} else {{
            for (let k = 1; k < run.length - 1; k++) {{
              const p1 = run[k], p2 = run[k + 1];
              ctx.quadraticCurveTo(p1.x, p1.y, (p1.x + p2.x) / 2, (p1.y + p2.y) / 2);
            }}
            ctx.lineTo(run[run.length - 1].x, run[run.length - 1].y);
          }}
          ctx.lineTo(run[run.length - 1].x, y0);
          ctx.closePath();
          ctx.fillStyle = 'rgba(22, 163, 74, 0.35)'; // translucent green regen fill
          ctx.fill();
          ctx.restore();
        }});
      }}
    }}

 // 2. Smooth line stroke
    const [lo, hi] = ranges[s.axis];
    if (s.fill_negative && lo < 0 && hi > 0) {{
      const y0 = yAt(0, s.axis);
 // Positive / consumption stroke (above y0)
      ctx.save();
      ctx.beginPath();
      ctx.rect(padL, padT, plotW, Math.max(1, y0 - padT));
      ctx.clip();
      ctx.strokeStyle = s.color;
      ctx.lineWidth = 2;
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';
      runs.forEach(run => {{
        if (run.length < 2) return;
        ctx.beginPath();
        ctx.moveTo(run[0].x, run[0].y);
        if (run.length === 2) {{
          ctx.lineTo(run[1].x, run[1].y);
        }} else {{
          for (let k = 1; k < run.length - 1; k++) {{
            const p1 = run[k], p2 = run[k + 1];
            ctx.quadraticCurveTo(p1.x, p1.y, (p1.x + p2.x) / 2, (p1.y + p2.y) / 2);
          }}
          ctx.lineTo(run[run.length - 1].x, run[run.length - 1].y);
        }}
        ctx.stroke();
      }});
      ctx.restore();

 // Negative / regen stroke (below y0)
      ctx.save();
      ctx.beginPath();
      ctx.rect(padL, y0, plotW, Math.max(1, padT + plotH - y0));
      ctx.clip();
      ctx.strokeStyle = '#16a34a'; // green for regen
      ctx.lineWidth = 2.5;
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';
      runs.forEach(run => {{
        if (run.length < 2) return;
        ctx.beginPath();
        ctx.moveTo(run[0].x, run[0].y);
        if (run.length === 2) {{
          ctx.lineTo(run[1].x, run[1].y);
        }} else {{
          for (let k = 1; k < run.length - 1; k++) {{
            const p1 = run[k], p2 = run[k + 1];
            ctx.quadraticCurveTo(p1.x, p1.y, (p1.x + p2.x) / 2, (p1.y + p2.y) / 2);
          }}
          ctx.lineTo(run[run.length - 1].x, run[run.length - 1].y);
        }}
        ctx.stroke();
      }});
      ctx.restore();
    }} else {{
      ctx.strokeStyle = s.color;
      ctx.lineWidth = 2;
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';
      runs.forEach(run => {{
        if (run.length < 2) return;
        ctx.beginPath();
        ctx.moveTo(run[0].x, run[0].y);
        if (run.length === 2) {{
          ctx.lineTo(run[1].x, run[1].y);
        }} else {{
          for (let k = 1; k < run.length - 1; k++) {{
            const p1 = run[k], p2 = run[k + 1];
            ctx.quadraticCurveTo(p1.x, p1.y, (p1.x + p2.x) / 2, (p1.y + p2.y) / 2);
          }}
          ctx.lineTo(run[run.length - 1].x, run[run.length - 1].y);
        }}
        ctx.stroke();
      }});
    }}
  }});

 // Reference lines (e.g. Drive Average efficiency dashed line)
  if (DATA.ref_lines && DATA.ref_lines.length > 0) {{
    DATA.ref_lines.forEach(ref => {{
      const y = yAt(ref.value, ref.axis || 'right');
      if (y >= padT && y <= padT + plotH) {{
        ctx.strokeStyle = ref.color || '#d97706';
        ctx.lineWidth = 1.75;
        ctx.setLineDash(ref.style === 'dashed' ? [6, 4] : []);
        ctx.beginPath();
        ctx.moveTo(padL, y);
        ctx.lineTo(W - padR, y);
        ctx.stroke();
        ctx.setLineDash([]);

        if (ref.label) {{
          ctx.font = 'bold 11px sans-serif';
          ctx.textAlign = 'right';
          const txtW = ctx.measureText(ref.label).width;
          ctx.fillStyle = 'rgba(255, 255, 255, 0.85)';
          ctx.fillRect(W - padR - txtW - 12, y - 14, txtW + 8, 14);
          ctx.fillStyle = ref.color || '#d97706';
          ctx.fillText(ref.label, W - padR - 8, y - 3);
        }}
      }}
    }});
  }}

 // legend
  legend.innerHTML = DATA.series.map(s =>
    `<span><span class="dot" style="background:${{s.color}}"></span>${{s.name}} (${{s.unit}}${{s.axis !== 'left' ? ', ' + s.axis + ' axis' : ''}}${{s.fill_negative ? ' · <span style="color:#16a34a">■</span> regen' : ''}})</span>`
  ).join('') + ((DATA.ref_lines || []).map(r =>
    `<span><span style="display:inline-block;width:14px;border-top:2px dashed ${{r.color}};margin-right:5px;vertical-align:middle"></span>${{r.label || 'Average'}}</span>`
  ).join(''));

  zoomNote.textContent = !DATA.zoomable ? '' : (
    view.window > 1
      ? `Showing a ${{view.window}}-day rolling average (${{n}} of ${{DATA.x.length}} point(s) in view) - drag to zoom in, double-click to reset`
      : `Showing daily values (${{n}} of ${{DATA.x.length}} point(s) in view) - drag to zoom in, double-click to reset`
  );
}}

let isDragging = false;
let dragStartPx = 0;
let dragCurrentPx = 0;

canvas.addEventListener('mousedown', (ev) => {{
  if (!DATA.zoomable) return;
  const rect = canvas.getBoundingClientRect();
  const mx = (ev.clientX - rect.left) * (canvas.width / rect.width);
  dragStartPx = Math.max(padL, Math.min(W - padR, mx));
  dragCurrentPx = dragStartPx;
  isDragging = true;
  tooltip.style.display = 'none';
}});

canvas.addEventListener('mousemove', (ev) => {{
  const rect = canvas.getBoundingClientRect();
  const mx = (ev.clientX - rect.left) * (canvas.width / rect.width);

  if (isDragging) {{
    dragCurrentPx = Math.max(padL, Math.min(W - padR, mx));
    draw();
    const x0 = Math.min(dragStartPx, dragCurrentPx);
    const x1 = Math.max(dragStartPx, dragCurrentPx);
    ctx.fillStyle = 'rgba(42,120,214,0.15)';
    ctx.fillRect(x0, padT, x1 - x0, H - padT - padB);
    ctx.strokeStyle = '#2a78d6';
    ctx.setLineDash([]);
    ctx.strokeRect(x0, padT, x1 - x0, H - padT - padB);
    return;
  }}

  if (mx < padL || mx > W - padR) {{ tooltip.style.display = 'none'; return; }}
  let i = Math.round(((mx - padL) / plotW) * (n - 1));
  i = Math.max(0, Math.min(n - 1, i));

  draw();
  const gx = xAt(i);
  ctx.strokeStyle = '#bbb';
  ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(gx, padT); ctx.lineTo(gx, H - padB); ctx.stroke();
  ctx.setLineDash([]);

  let lines = [`<b>${{view.x[i]}}</b>`];
  view.series.forEach(s => {{
    const v = s.values[i];
    const isRegen = s.fill_negative && v !== null && v !== undefined && v < 0;
    const valStr = v === null || v === undefined ? '-' : `${{v.toFixed(2)}} ${{s.unit}}${{isRegen ? ' (Regen ⚡)' : ''}}`;
    lines.push(`${{s.name}}: ${{valStr}}`);
  }});
  if (DATA.locations && DATA.locations[viewStartIdx + i]) {{
    lines.push(`📍 ${{DATA.locations[viewStartIdx + i]}}`);
  }}
  tooltip.innerHTML = lines.join('<br>');
  tooltip.style.display = 'block';
  const my = (ev.clientY - rect.top) * (canvas.height / rect.height);
  let left = gx + 12, top = my - 10;
  if (left > W - 220) left = gx - 12 - 200;
  tooltip.style.left = left + 'px';
  tooltip.style.top = Math.max(0, top) + 'px';
}});

canvas.addEventListener('mouseup', () => {{
  if (!isDragging) return;
  isDragging = false;
  const x0 = Math.min(dragStartPx, dragCurrentPx);
  const x1 = Math.max(dragStartPx, dragCurrentPx);
  if (x1 - x0 < 6) {{ draw(); return; }}

  let i0 = Math.round(((x0 - padL) / plotW) * (n - 1));
  let i1 = Math.round(((x1 - padL) / plotW) * (n - 1));
  i0 = Math.max(0, Math.min(n - 1, i0));
  i1 = Math.max(0, Math.min(n - 1, i1));
  if (i1 - i0 < 2) {{ draw(); return; }}

  const baseStart = viewStartIdx;
  viewStartIdx = baseStart + i0;
  viewEndIdx = baseStart + i1;
  updateView();
  draw();
}});

canvas.addEventListener('dblclick', () => {{
  if (!DATA.zoomable) return;
  viewStartIdx = 0;
  viewEndIdx = DATA.x.length - 1;
  updateView();
  draw();
}});

canvas.addEventListener('mouseleave', () => {{
  if (isDragging) return;
  tooltip.style.display = 'none';
  draw();
}});

draw();
</script>
</body>
</html>
"""


def write_interactive_chart(output_path, title, x_values, x_label, series, subtitle="", locations=None, ref_lines=None, zoomable=False):
    """Write a self-contained interactive HTML chart to output_path.

    x_values: list of str labels (already formatted - a timestamp
    string, a distance string, whatever the caller wants shown).
    series: list of {"name", "unit", "axis", "color", "values", "fill_negative"} dicts.
    locations: optional list of str-or-None, same length as x_values.
    ref_lines: optional list of {"value", "axis", "label", "color", "style"} reference lines.
    zoomable: opt-in (REQ-119) - enables drag-to-zoom/double-click-reset with a
    continuous rolling-average smoothing level (see the module docstring for
    the full behavior). False by default so every existing caller renders
    exactly as before this parameter was added.
    """
    data = {
        "x": x_values,
        "x_label": x_label,
        "series": series,
        "locations": locations,
        "ref_lines": ref_lines or [],
        "zoomable": bool(zoomable),
    }
    html = _TEMPLATE.format(
        title=title,
        subtitle=subtitle,
        data_json=json.dumps(data),
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return output_path


def try_open_in_browser(path):
    """Best-effort `open` on macOS - never raises, this is a nice-to-have,
    not something that should block or fail the caller's own flow."""
    import subprocess
    import sys as _sys
    try:
        if _sys.platform == "darwin":
            subprocess.run(["open", path], check=False, timeout=5)
            return True
    except Exception:
        pass
    return False
