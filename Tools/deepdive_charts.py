#!/usr/bin/env python3
"""
deepdive_charts.py - Self-contained interactive HTML line charts for
high-frequency telemetry (charging curves, drive elevation/efficiency
profiles), with zero external dependency (no plotly/matplotlib, no CDN
script) - the chart file has to keep working years from now with no
network at view time, and this sandbox has no network to install a
charting library into anyway. Plain vanilla JS + <canvas>, hover
tooltip, nothing else.

Usage: write_interactive_chart(output_path, title, x_values, x_label,
series) where `series` is a list of dicts:
    {"name": str, "unit": str, "axis": "left"|"right",
     "color": "#rrggbb", "values": [float or None, ...]}
(values aligned 1:1 with x_values; None = no data at that x, series
just breaks there rather than interpolating across the gap).
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
const padL = 60, padR = DATA.series.some(s => s.axis === 'right') ? 60 : 20, padT = 16, padB = 40;
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

const [loL, hiL] = axisRange(DATA.series, 'left');
const [loR, hiR] = axisRange(DATA.series, 'right');

function yAt(v, axis) {{
  const [lo, hi] = axis === 'right' ? [loR, hiR] : [loL, hiL];
  return padT + plotH - ((v - lo) / (hi - lo)) * plotH;
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
  if (DATA.series.some(s => s.axis === 'right')) {{
    ctx.textAlign = 'left';
    for (let g = 0; g <= gridN; g++) {{
      const v = loR + (hiR - loR) * (g / gridN);
      const y = yAt(v, 'right');
      ctx.fillText(v.toFixed(1), W - padR + 8, y + 3);
    }}
  }}

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

  // series lines
  DATA.series.forEach(s => {{
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    let started = false;
    for (let i = 0; i < n; i++) {{
      const v = s.values[i];
      if (v === null || v === undefined) {{ started = false; continue; }}
      const x = xAt(i), y = yAt(v, s.axis);
      if (!started) {{ ctx.moveTo(x, y); started = true; }} else {{ ctx.lineTo(x, y); }}
    }}
    ctx.stroke();
  }});

  // legend
  legend.innerHTML = DATA.series.map(s =>
    `<span><span class="dot" style="background:${{s.color}}"></span>${{s.name}} (${{s.unit}}${{s.axis === 'right' ? ', right axis' : ''}})</span>`
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


def write_interactive_chart(output_path, title, x_values, x_label, series, subtitle=""):
    """Write a self-contained interactive HTML chart to output_path.

    x_values: list of str labels (already formatted - a timestamp
    string, a distance string, whatever the caller wants shown).
    series: list of {"name", "unit", "axis" ("left"/"right"), "color",
    "values"} dicts, each "values" the same length as x_values (use
    None for a missing sample rather than omitting it, so the x-axis
    alignment across series stays correct).
    """
    data = {"x": x_values, "x_label": x_label, "series": series}
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
