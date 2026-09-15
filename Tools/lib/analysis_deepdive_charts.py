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

const W = canvas.width, H = canvas.height;
const axisIds = [];
DATA.series.forEach(s => {{ if (!axisIds.includes(s.axis)) axisIds.push(s.axis); }});
const rightAxisIds = axisIds.filter(a => a !== 'left');
const padL = 60, padR = 20 + rightAxisIds.length * 60, padT = 16, padB = 40;
const plotW = W - padL - padR, plotH = H - padT - padB;

const n = DATA.x.length;
function xAt(i) {{ return padL + (n <= 1 ? 0 : (i / (n - 1)) * plotW); }}

function axisRange(series, axis) {{
  let vals = [];
  series.filter(s => s.axis === axis).forEach(s => s.values.forEach(v => {{ if (v !== null && v !== undefined) vals.push(v); }}));
  if (vals.length === 0) return [0, 1];
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (lo === hi) {{ lo -= 1; hi += 1; }}
  const pad = (hi - lo) * 0.08;
  return [lo - pad, hi + pad];
}}

const ranges = {{}};
axisIds.forEach(a => {{ ranges[a] = axisRange(DATA.series, a); }});
const [loL, hiL] = ranges['left'] || [0, 1];

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

  // x-axis labels (sparse)
  ctx.textAlign = 'center';
  const xTicks = Math.min(8, n);
  for (let t = 0; t < xTicks; t++) {{
    const i = Math.round(t * (n - 1) / Math.max(1, xTicks - 1));
    ctx.fillText(DATA.x[i], xAt(i), H - padB + 16);
  }}
  ctx.fillStyle = '#444';
  ctx.textAlign = 'center';
  ctx.fillText(DATA.x_label, padL + plotW / 2, H - 6);

  // series lines - smooth curve through each contiguous run of real
  // values (quadratic-through-midpoints: no overshoot, no library) -
  // a real user report (2026-09-15) pointed at a reference chart with
  // a smooth connected curve rather than this chart's old straight-
  // line polyline.
  DATA.series.forEach(s => {{
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    let run = [];
    const flushRun = () => {{
      if (run.length < 2) {{ run = []; return; }}
      ctx.beginPath();
      ctx.moveTo(run[0].x, run[0].y);
      if (run.length === 2) {{
        ctx.lineTo(run[1].x, run[1].y);
      }} else {{
        for (let k = 1; k < run.length - 1; k++) {{
          const p1 = run[k], p2 = run[k + 1];
          const midX = (p1.x + p2.x) / 2, midY = (p1.y + p2.y) / 2;
          ctx.quadraticCurveTo(p1.x, p1.y, midX, midY);
        }}
        const last = run[run.length - 1];
        ctx.lineTo(last.x, last.y);
      }}
      ctx.stroke();
      run = [];
    }};
    for (let i = 0; i < n; i++) {{
      const v = s.values[i];
      if (v === null || v === undefined) {{ flushRun(); continue; }}
      run.push({{ x: xAt(i), y: yAt(v, s.axis) }});
    }}
    flushRun();
  }});

  // legend
  legend.innerHTML = DATA.series.map(s =>
    `<span><span class="dot" style="background:${{s.color}}"></span>${{s.name}} (${{s.unit}}${{s.axis !== 'left' ? ', ' + s.axis + ' axis' : ''}})</span>`
  ).join('');
}}

canvas.addEventListener('mousemove', (ev) => {{
  const rect = canvas.getBoundingClientRect();
  const mx = (ev.clientX - rect.left) * (canvas.width / rect.width);
  if (mx < padL || mx > W - padR) {{ tooltip.style.display = 'none'; return; }}
  let i = Math.round(((mx - padL) / plotW) * (n - 1));
  i = Math.max(0, Math.min(n - 1, i));

  draw();
  const gx = xAt(i);
  ctx.strokeStyle = '#bbb';
  ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(gx, padT); ctx.lineTo(gx, H - padB); ctx.stroke();
  ctx.setLineDash([]);

  let lines = [`<b>${{DATA.x[i]}}</b>`];
  DATA.series.forEach(s => {{
    const v = s.values[i];
    lines.push(`${{s.name}}: ${{v === null || v === undefined ? '-' : v.toFixed(2)}} ${{s.unit}}`);
  }});
  if (DATA.locations && DATA.locations[i]) {{
    lines.push(`📍 ${{DATA.locations[i]}}`);
  }}
  tooltip.innerHTML = lines.join('<br>');
  tooltip.style.display = 'block';
  const my = (ev.clientY - rect.top) * (canvas.height / rect.height);
  let left = gx + 12, top = my - 10;
  if (left > W - 220) left = gx - 12 - 200;
  tooltip.style.left = left + 'px';
  tooltip.style.top = Math.max(0, top) + 'px';
}});
canvas.addEventListener('mouseleave', () => {{ tooltip.style.display = 'none'; draw(); }});

draw();
</script>
</body>
</html>
"""


def write_interactive_chart(output_path, title, x_values, x_label, series, subtitle="", locations=None):
    """Write a self-contained interactive HTML chart to output_path.

    x_values: list of str labels (already formatted - a timestamp
    string, a distance string, whatever the caller wants shown).
    series: list of {"name", "unit", "axis", "color", "values"} dicts,
    each "values" the same length as x_values (use None for a missing
    sample rather than omitting it, so the x-axis alignment across
    series stays correct). "axis" is any str id - "left" is the one
    gridlined axis; every other id gets its own stacked tick-label
    column on the right, so 3+ differently-scaled series can share one
    chart.

    locations: optional list of str-or-None, same length as x_values -
    an extra "Location: ..." tooltip line at that x, or omitted
    entirely (None) for a chart with nothing to show there.
    """
    data = {"x": x_values, "x_label": x_label, "series": series, "locations": locations}
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
