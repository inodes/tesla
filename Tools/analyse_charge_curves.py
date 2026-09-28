#!/usr/bin/env python3
"""Builds two charging-curve charts (PNG + interactive HTML) from a charge
deep-dive telemetry CSV - the per-sample vehicle-state export
analyse_charging.py's fetch_charge_deepdive_from_api() produces for
EVERY session Tessie itself sees (Superchargers, 3rd-party DC/AC, and home
Evnex sessions alike - AGENTS.md REQ-050 reversed an earlier, wrong
assumption that home sessions have no per-sample telemetry).

Two chart types, matching the two reference shapes the user confirmed both
have merit:

  <stem>_curve.png / .html   - the classic "charging curve": Charger Power
                                (kW) vs State of Charge (%), the shape every
                                EV charging-curve site (evkx.net etc.)
                                publishes, PLUS Pack Temperature (deg C, its
                                own right axis) over the same SoC range - a
                                cold, un-preconditioned pack visibly
                                suppresses the power curve, so seeing both
                                on one chart is the point (a secondary top
                                axis also shows elapsed time at the same
                                points).

  <stem>_profile.png / .html - the CC/CV-style "electrical profile": Pack
                                Voltage (V, left axis) and Pack Current (A,
                                far-right axis) vs elapsed time, with State
                                of Charge overlaid on its own right axis -
                                the classic battery-charging-theory diagram,
                                built from this car's own real numbers
                                instead of a schematic.

Both a session's OWN real telemetry (solid line) and - where this session
didn't cover the full range - a DASHED estimate built from this car's own
charging history are drawn. That estimate is pooled from OTHER sessions at
the exact same charger first (matched via resources/tesla_superchargers.json's
short_name, e.g. "Macquarie_NSW") - a V3 250kW stall's curve says nothing
trustworthy about this V2 130kW one. Only when the same-site pool doesn't
yet have 2+ points in a given direction does it fall back to OTHER sites of
the SAME rated tier (V2-with-V2, V3-with-V3, V4-with-V4 - never mixed) as a
rougher stand-in for a site this car hasn't visited often yet; the chart
caption always says which pool actually supplied the dashed line. SoC
ranges no session (site or tier) has ever reached are left blank rather
than extrapolated past real data.

The charger's own rated max power (Tesla's V2/V3/V4 stall tiers) is drawn
as a light reference line on the power-vs-SoC chart when this session's
site matches an entry in resources/tesla_superchargers.json, so a session that
never reached the stall's cap (shared power, cold battery, etc.) is visibly
distinguishable from a session that did.
"""
import argparse
import csv
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    from lib.tessie_api_common import resolve_tessie_data_home
except Exception:
    def resolve_tessie_data_home():
        return None

# TODO-038: real per-moment solar-vs-grid split for home (Evnex) charging
# sessions, sourced from the same PV provider/credentials/cache
# analyse_session_cost.py already trusts for its own whole-session solar
# split (REQ-071/REQ-140) - never re-derived or approximated differently
# here, just applied at finer (per-interval) granularity. Guarded the same
# defensive way as resolve_tessie_data_home() above: if lib/providers_pv
# aren't importable (or no PV credentials are configured), home sessions
# still get their charts - just without the solar/grid split, per-total-
# power only (see render_home_curve_chart's split=None fallback).
try:
    from datetime import datetime as _dt, timedelta as _timedelta
    from providers_pv import get_pv_provider
    from lib.pv_history_db import get_pv_db_connection, pv_window_split, pv_interval_series
    from lib.tessie_api_common import load_config as _load_config
    _PV_PROVIDER = get_pv_provider()
    _PV_KEY, _PV_ACCOUNT_ID = _PV_PROVIDER.resolve_credentials(_load_config())
    _PV_CONN = get_pv_db_connection() if (_PV_KEY and _PV_ACCOUNT_ID) else None
except Exception:
    _PV_PROVIDER = None
    _PV_KEY = _PV_ACCOUNT_ID = None
    _PV_CONN = None


def compute_home_solar_grid_split(points):
    """TODO-038: for a home Evnex session's telemetry points (each with a
    naive-local "t" timestamp and "power_kw"), return a parallel list of
    (solar_kw, grid_kw) tuples - or None if no PV data is available for
    this session at all (missing credentials, or zero real interval
    coverage), so the caller can fall back to plotting total power alone.

    Method: for each point, find the pv_intraday interval whose
    [start, end) contains its timestamp and use THAT interval's own real
    solar_fraction (self_consumption_kwh/consumption_kwh - REQ-140's
    per-interval SolarEdge/PVOutput meter fields, never approximated).
    A point whose interval has no usable fraction (missing data, or zero
    household consumption that interval) falls back to the whole-session
    solar_fraction from pv_window_split() - the exact same blended figure
    analyse_session_cost.py already surfaces and the user already trusts
    for billing. Only if NEITHER is available does a point go unsplit
    (solar_kw=0.0, grid_kw=power_kw - conservative, never invents solar
    credit from nothing)."""
    if not points or _PV_CONN is None or not (_PV_KEY and _PV_ACCOUNT_ID):
        return None
    try:
        t0 = _dt.strptime(points[0]["t"], "%Y-%m-%d %H:%M:%S")
        t1 = _dt.strptime(points[-1]["t"], "%Y-%m-%d %H:%M:%S") + _timedelta(minutes=1)
    except (ValueError, KeyError):
        return None

    intervals = pv_interval_series(_PV_CONN, t0, t1, _PV_PROVIDER, _PV_KEY, _PV_ACCOUNT_ID, log=lambda *a, **k: None)
    whole = pv_window_split(_PV_CONN, t0, t1, _PV_PROVIDER, _PV_KEY, _PV_ACCOUNT_ID)
    fallback_fraction = whole.get("solar_fraction")

    if not intervals and fallback_fraction is None:
        return None

    out = []
    any_real = False
    for p in points:
        power_kw = p.get("power_kw") or 0.0
        try:
            ts = _dt.strptime(p["t"], "%Y-%m-%d %H:%M:%S")
        except (ValueError, KeyError):
            out.append((0.0, power_kw))
            continue
        frac = None
        for iv in intervals:
            if iv["start"] <= ts < iv["end"]:
                frac = iv["solar_fraction"]
                break
        if frac is not None:
            any_real = True
        elif fallback_fraction is not None:
            frac = fallback_fraction
        if frac is None:
            out.append((0.0, power_kw))
            continue
        frac = max(0.0, min(1.0, frac))
        solar_kw = power_kw * frac
        out.append((solar_kw, power_kw - solar_kw))

    if not any_real and fallback_fraction is None:
        return None
    return out


# --- dataviz reference palette (light mode) - see AGENTS.md / dataviz skill ---
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
TEXT_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
S1_BLUE = "#2a78d6"
S2_ORANGE = "#eb6834"
S3_AQUA = "#1baf7a"

DEEPDIVE_FILENAME_RE = re.compile(
    r"^charge_deepdive_\d{4}-\d{2}-\d{2}_\d{4}(?:_(?:AEST|AEDT))?_(?:\d{2}_)?(?P<slug>.+)\.csv$"
)
# The "(?:\d{2}_)?" skips unique_stamped_path()'s "_NN" disambiguator when
# present (every Evnex CSV, and any future multi-sub-session non-Evnex one -
# see AGENTS.md REQ-050) so it never leaks into the slug, e.g.
# "charge_deepdive_2026-06-20_1327_00_Evnex_Home.csv" -> slug "Evnex_Home",
# NOT "00_Evnex_Home" (which would fragment one site's history into a
# separate historical-pooling bucket per disambiguator digit). Manually
# ingested/legacy files with no disambiguator at all still match the same
# way since the group is optional.


def _safe_float(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_telemetry_csv(path):
    """Reads one charge_deepdive_*.csv into a list of point dicts:
    {t, elapsed_min, soc, power_kw, voltage_v, current_a, temp_c}. Returns
    None if this file isn't a per-sample telemetry export (e.g. a CSV that
    doesn't carry "Charger Power (kW)" isn't one of these deep-dives).
    temp_c is the average of "Min Battery Module Temp (°C)" and "Max
    Battery Module Temp (°C)" when both are present (a simple midpoint,
    not a weighted pack model - good enough to show "cold vs warm", which
    is the point per the module docstring), else None if either is
    missing for that sample."""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception:
        return None
    if not rows or "Charger Power (kW)" not in rows[0]:
        return None

    from datetime import datetime
    t0 = None
    pts = []
    for row in rows:
        ts = row.get("Timestamp (AEST)")
        if not ts:
            continue
        try:
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if t0 is None:
            t0 = dt
        soc = _safe_float(row.get("Battery Level (%)"))
        if soc is None:
            continue
        tmin = _safe_float(row.get("Min Battery Module Temp (°C)"))
        tmax = _safe_float(row.get("Max Battery Module Temp (°C)"))
        temp_c = (tmin + tmax) / 2.0 if tmin is not None and tmax is not None else None
        pts.append({
            "t": ts,
            "elapsed_min": (dt - t0).total_seconds() / 60.0,
            "soc": soc,
            "power_kw": _safe_float(row.get("Charger Power (kW)")),
            "voltage_v": _safe_float(row.get("Pack Voltage (V)")),
            "current_a": _safe_float(row.get("Pack Current (A)")),
            "temp_c": temp_c,
        })
    return pts or None


def slug_from_deepdive_filename(path):
    """"charge_deepdive_2026-08-14_2109_Tesla_Macquarie_NSW.csv" ->
    "Tesla_Macquarie_NSW" - the raw network+location stamp
    unique_stamped_path() gave the file when it was first written."""
    m = DEEPDIVE_FILENAME_RE.match(os.path.basename(path))
    return m.group("slug") if m else None


def load_charger_registries():
    """Merges resources/tesla_superchargers*.json and tesla_chargers*.json
    from THIS repo's own resources/ folder (not the iCloud data home -
    these are the hand/scraper-maintained hardware registries
    charger_find_tesla.py builds, gitignored real-data files, same as
    vehicle_spec.json). Missing files are a normal "not scraped yet" case,
    not an error."""
    merged = {}
    tessie_dir = os.path.join(REPO_ROOT, "resources")
    for fname in ("tesla_superchargers.json", "tesla_superchargers_archived.json",
                  "tesla_chargers.json", "tesla_chargers_archived.json"):
        p = os.path.join(tessie_dir, fname)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if isinstance(data, dict):
            for k, v in data.items():
                if k not in merged:
                    merged[k] = v
    return merged


def match_hardware(slug, registry):
    """Matches a deepdive filename slug ("Tesla_Macquarie_NSW") against a
    registry's short_name field ("Macquarie_NSW") - the network prefix
    (Tesla_, Exploren_, ...) is the only difference between the two, so
    this tries the full slug first, then strips the leading network
    token and tries again. Returns (station_name, hardware_dict) or
    (None, None)."""
    if not slug:
        return None, None
    candidates = [slug]
    if "_" in slug:
        candidates.append(slug.split("_", 1)[1])
    for name, data in registry.items():
        short_name = (data.get("tesla_metadata", {}) or {}).get("short_name", "")
        if short_name and short_name in candidates:
            return name, data.get("hardware")
    return None, None


def discover_deepdive_sessions(deepdive_dir):
    """All parseable per-sample telemetry sessions in deepdive_dir, each as
    {"path": ..., "slug": ..., "points": [...]}. Evnex home sessions (JSON
    only, no matching CSV) and any CSV that isn't telemetry-shaped are
    silently skipped."""
    sessions = []
    for path in sorted(glob.glob(os.path.join(deepdive_dir, "charge_deepdive_*.csv"))):
        slug = slug_from_deepdive_filename(path)
        pts = parse_telemetry_csv(path)
        if slug and pts:
            sessions.append({"path": path, "slug": slug, "points": pts})
    return sessions


def _interp(xs, ys, x):
    """Linear interpolation over sorted (xs, ys); returns None outside
    [xs[0], xs[-1]] or when fewer than 2 points."""
    if len(xs) < 2 or x < xs[0] or x > xs[-1]:
        return None
    for i in range(1, len(xs)):
        if x <= xs[i]:
            x0, x1 = xs[i - 1], xs[i]
            y0, y1 = ys[i - 1], ys[i]
            if x1 == x0:
                return y0
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return ys[-1]


def historical_extension(sessions, current, registry=None):
    """Builds a dashed "estimated from history" extension for the metrics
    that matter (power, voltage, current, pack temperature), pooled from
    other real sessions - see module docstring for the two-tier pooling
    rule. Returns None if there is no usable history at all (neither same-
    site nor same-tier); otherwise a dict with per-metric {"before":
    [(soc, value), ...], "after": [...]} lists (each possibly empty) plus
    "n_site_sessions"/"n_tier_sessions"/"tier_label"/"used_tier_fallback"
    for the chart caption. "before"/"after" are relative to the CURRENT
    session's own observed SoC range - never extrapolated past the
    combined historical range itself.

    Home Evnex sessions are deliberately excluded (return None) - this
    pooling only makes sense where a SESSION'S SoC genuinely predicts its
    charger power, which is true for a Supercharger/DC tier (a real,
    physics-based curve, consistent across sessions of the same
    hardware) but NOT true at home: solar-diversion power is driven by
    sunlight/schedule/house load at that MOMENT, essentially independent
    of the car's SoC. Pooling ~150 unrelated home sessions' (soc, power)
    points and sorting by SoC produces a chaotic zigzag with no
    physical meaning - confirmed live (a 6th Jun 2026 home session,
    49%->49% SoC, rendered as a wall of criss-crossing dashed lines from
    148 other sessions covering the full 16-83% SoC range)."""
    registry = registry or {}
    if current["slug"] == "Evnex_Home":
        return None
    same_site = [s for s in sessions if s["path"] != current["path"] and s["slug"] == current["slug"]]

    _, cur_hw = match_hardware(current["slug"], registry)
    tier_label = cur_hw.get("tier") if cur_hw else None
    same_tier_other_sites = []
    if tier_label:
        for s in sessions:
            if s["path"] == current["path"] or s["slug"] == current["slug"]:
                continue
            _, hw = match_hardware(s["slug"], registry)
            if hw and hw.get("tier") == tier_label:
                same_tier_other_sites.append(s)

    if not same_site and not same_tier_other_sites:
        return None

    cur_socs = [p["soc"] for p in current["points"] if p["soc"] is not None]
    cur_lo, cur_hi = min(cur_socs), max(cur_socs)

    def _pairs(pool, metric):
        pairs = []
        for o in pool:
            for p in o["points"]:
                if p["soc"] is not None and p.get(metric) is not None:
                    pairs.append((p["soc"], p[metric]))
        pairs.sort()
        return pairs

 # Deliberately NOT anchored/offset to match this session's own boundary
 # value - an earlier version shifted the historical curve so it met the
 # current curve exactly at the edge, but that breaks badly when the
 # boundary sample itself is atypical (e.g. the very first sample of a
 # session, often still 0-2 kW while the car and charger are still
 # negotiating): the shift then drags the WHOLE historical line toward
 # that one noisy point. Plotting the other sessions' real values
 # unmodified is more honest - a visible gap where the dashed line
 # doesn't quite meet the solid one is real session-to-session
 # variation (shared power, battery temp, a different day), not a
 # rendering issue.
    result = {
        "n_site_sessions": len(same_site),
        "n_tier_sessions": len(same_tier_other_sites),
        "tier_label": tier_label,
        "used_tier_fallback": False,
    }
    for metric in ("power_kw", "voltage_v", "current_a", "temp_c"):
        site_pairs = _pairs(same_site, metric)
        site_before = [(x, y) for x, y in site_pairs if x <= cur_lo]
        site_after = [(x, y) for x, y in site_pairs if x >= cur_hi]

 # Only reach for the coarser same-tier/other-site pool when the
 # exact-site pool doesn't have enough of its own history yet
 # (fewer than 2 points in that direction) - see module docstring.
        before, after = site_before, site_after
        if len(before) < 2 and same_tier_other_sites:
            tier_pairs = _pairs(same_tier_other_sites, metric)
            tier_before = [(x, y) for x, y in tier_pairs if x <= cur_lo]
            if len(tier_before) >= 2:
                before = tier_before
                result["used_tier_fallback"] = True
        if len(after) < 2 and same_tier_other_sites:
            tier_pairs = _pairs(same_tier_other_sites, metric)
            tier_after = [(x, y) for x, y in tier_pairs if x >= cur_hi]
            if len(tier_after) >= 2:
                after = tier_after
                result["used_tier_fallback"] = True

 # Require >=2 points so a lone stray sample never renders as a line.
        result[metric] = {
            "before": before if len(before) >= 2 else [],
            "after": after if len(after) >= 2 else [],
        }
    return result


def _history_caption(hist):
    if not hist:
        return "no charging history yet for this site or tier - no historical estimate"
    n_site = hist["n_site_sessions"]
    if not hist["used_tier_fallback"] or not hist.get("n_tier_sessions"):
        return f"dashed = estimated from {n_site} other session{'s' if n_site != 1 else ''} at this site"
    n_tier = hist["n_tier_sessions"]
    tier = hist.get("tier_label") or "same-tier"
    if n_site:
        return (f"dashed = estimated from {n_site} session{'s' if n_site != 1 else ''} at this site "
                f"+ {n_tier} other {tier} session{'s' if n_tier != 1 else ''} (this site's own history is still thin)")
    return f"dashed = estimated from {n_tier} other {tier} session{'s' if n_tier != 1 else ''} (no history at this exact site yet)"


def _ordinal_date(dt):
    """datetime -> "1st Aug 2026" (day-of-month with its ordinal suffix,
    short month name, 4-digit year) - the heading format asked for,
    instead of the ISO "2026-08-01" the charts used before."""
    day = dt.day
    suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix} {dt.strftime('%b %Y')}"


def build_title(session, hardware_name, hardware):
    """"1st Aug 2026 - West Gosford, NSW (V3 250kW)" - date, then place,
    then tier+rated-power in parens when known. Home Evnex sessions (no
    tier/hardware entry - it's not a Supercharger) get "(Evnex)" instead
    of a tier so the heading still says what kind of charging this was."""
    from datetime import datetime
    first_ts = session["points"][0]["t"] if session["points"] else ""
    date_str = ""
    if first_ts:
        try:
            date_str = _ordinal_date(datetime.strptime(first_ts, "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            date_str = first_ts[:10]

    if session["slug"] == "Evnex_Home":
        place, tier_bit = "Home", " (Evnex)"
    else:
        place = hardware_name or session["slug"].replace("_", " ")
        if hardware and hardware.get("tier") and hardware.get("max_power_kw"):
            tier_bit = f" ({hardware['tier']} {hardware['max_power_kw']:.0f}kW)"
        else:
            tier_bit = ""

    title = f"{place}{tier_bit}"
    return f"{date_str} - {title}" if date_str else title


def render_home_curve_chart(current, out_png, out_html, title):
    """TODO-038: the home-Evnex variant of render_curve_chart() below -
    elapsed TIME on the x-axis instead of SoC (BUG-039 flagged SoC as a
    poor fit for home sessions, since it barely moves across most of
    them), with the total charger-power trace SPLIT into a solar-kW area
    (below) and grid-kW area (stacked on top) when real PV interval data
    is available for this session's window (compute_home_solar_grid_split()
    above) - matching the Evnex app's own solar=green/grid=blue
    power-vs-time convention. Falls back to a single plain power line
    (still time-based) when no PV split is available, rather than
    skipping the chart or reverting to the SoC axis.

    Deliberately a SEPARATE function from render_curve_chart(), not a
    branch inside it - keeps the well-tested Supercharger/3rd-party
    SoC-based path completely untouched by this change."""
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pts = [p for p in current["points"] if p["power_kw"] is not None]
    elapsed = np.array([p["elapsed_min"] for p in pts])
    power = np.array([p["power_kw"] for p in pts])
    split = compute_home_solar_grid_split(pts)

    temp_pts = [p for p in current["points"] if p.get("temp_c") is not None]
    temp_elapsed = np.array([p["elapsed_min"] for p in temp_pts])
    temp_c = np.array([p["temp_c"] for p in temp_pts])

    FIG_DPI = 220
    fig, ax = plt.subplots(figsize=(10, 6), dpi=FIG_DPI)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    if split:
        solar_kw = np.array([s for s, _ in split])
        grid_kw = np.array([g for _, g in split])
        ax.stackplot(elapsed, solar_kw, grid_kw, colors=[S3_AQUA, S1_BLUE],
                     labels=["Solar (kW)", "Grid (kW)"], alpha=0.85, zorder=2)
        ax.plot(elapsed, power, color=TEXT_PRIMARY, linewidth=1, alpha=0.35, zorder=3)
        _trapz = np.trapezoid if hasattr(np, "trapezoid") else np.trapz
        total_solar_kwh = float(_trapz(solar_kw, elapsed) / 60.0)
        total_kwh = float(_trapz(power, elapsed) / 60.0)
        solar_pct = (total_solar_kwh / total_kwh * 100.0) if total_kwh > 0 else 0.0
        ax.legend(loc="upper right", frameon=False, fontsize=9.5, labelcolor=TEXT_SECONDARY)
        split_caption = f"{solar_pct:.0f}% solar (real PV meter data, this session's window)"
    else:
        ax.plot(elapsed, power, color=S1_BLUE, linewidth=2.5, solid_capstyle="round", zorder=3)
        ax.scatter(elapsed, power, s=36, color=S1_BLUE, edgecolors=SURFACE, linewidths=1.2, zorder=4)
        split_caption = "solar/grid split unavailable (no PV data for this session's window)"

    peak_i = int(power.argmax())
 # Flip the annotation to the LEFT of its point (and anchor its text
 # box on that side) whenever the peak sits in the right half of the
 # chart - otherwise the offset text collides with the pack-temperature
 # axis's own tick labels, which live just past the right edge (a real
 # collision seen on a session whose peak was near the end of charging).
    peak_on_right = elapsed[peak_i] > (elapsed.min() + elapsed.max()) / 2.0
    ann_dx, ann_ha = (-12, "right") if peak_on_right else (12, "left")
    ax.annotate(f"peak {power[peak_i]:.1f} kW\nat {elapsed[peak_i]:.0f} min", (elapsed[peak_i], power[peak_i]),
                xytext=(ann_dx, -30), textcoords="offset points", fontsize=9.5, color=TEXT_PRIMARY,
                fontfamily="DejaVu Sans", va="top", ha=ann_ha)

    ax.yaxis.grid(True, color=GRID, linewidth=1)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.tick_params(axis="both", colors=TEXT_SECONDARY, labelsize=10, length=0)
    ax.set_xlim(elapsed.min(), elapsed.max())
    ax.set_ylim(0, max(power) * 1.18 if len(power) else 1)
    ax.set_xlabel("Elapsed time (min)", color=TEXT_SECONDARY, fontsize=11, fontfamily="DejaVu Sans")
    ax.set_ylabel("Charger power (kW)", color=S1_BLUE if not split else TEXT_SECONDARY, fontsize=11,
                  fontfamily="DejaVu Sans")

    if len(temp_elapsed) >= 2:
        ax_temp = ax.twinx()
        ax_temp.plot(temp_elapsed, temp_c, color=S2_ORANGE, linewidth=1.8, solid_capstyle="round", zorder=5, alpha=0.9)
        ax_temp.set_ylabel("Pack temperature (°C)", color=S2_ORANGE, fontsize=11, fontfamily="DejaVu Sans")
        ax_temp.tick_params(axis="y", colors=S2_ORANGE, labelsize=10, length=0)
        tpad = (max(temp_c) - min(temp_c)) * 0.25 or 3
        ax_temp.set_ylim(min(temp_c) - tpad, max(temp_c) + tpad)
        for spine in ("top", "left"):
            ax_temp.spines[spine].set_visible(False)
        ax_temp.spines["right"].set_color(BASELINE)

    fig.suptitle("Charging curve", x=0.085, y=0.975, ha="left", va="top", color=TEXT_PRIMARY,
                 fontsize=17, fontweight="bold", fontfamily="DejaVu Sans")
    subtitle = f"{title}\n{len(pts)} samples over {elapsed.max():.0f} min  •  {split_caption}"
    ax.set_title(subtitle, loc="left", color=TEXT_SECONDARY, fontsize=10.5, pad=30, fontfamily="DejaVu Sans",
                 linespacing=1.5)

    fig.tight_layout(rect=(0, 0, 1, 0.88))
    fig.savefig(out_png, facecolor=SURFACE, bbox_inches="tight", dpi=FIG_DPI)
    plt.close(fig)


def render_home_curve_html(current, out_html, title):
    """HTML twin of render_home_curve_chart() above, reusing the existing
    generic series/axes _write_html()/_HTML_TEMPLATE machinery unchanged
    (that hand-rolled canvas renderer only strokes lines, no fill/stack
    support - see its own code - so this draws Solar/Grid as two
    overlaid lines rather than a true stacked fill; the PNG above is the
    one with the fuller Evnex-style stacked-area look)."""
    pts = [p for p in current["points"] if p["power_kw"] is not None]
    elapsed = [p["elapsed_min"] for p in pts]
    power = [p["power_kw"] for p in pts]
    split = compute_home_solar_grid_split(pts)

    if split:
        solar_kw = [s for s, _ in split]
        grid_kw = [g for _, g in split]
        series = [
            {"name": "Solar power", "unit": "kW", "axis": "power", "color": S3_AQUA, "dash": False,
             "x": elapsed, "y": solar_kw},
            {"name": "Grid power", "unit": "kW", "axis": "power", "color": S1_BLUE, "dash": False,
             "x": elapsed, "y": grid_kw},
        ]
        total_kwh = sum(power[i] * (elapsed[i + 1] - elapsed[i]) / 60.0 for i in range(len(power) - 1)) or 0.0
        total_solar_kwh = sum(solar_kw[i] * (elapsed[i + 1] - elapsed[i]) / 60.0 for i in range(len(solar_kw) - 1)) or 0.0
        solar_pct = (total_solar_kwh / total_kwh * 100.0) if total_kwh > 0 else 0.0
        split_caption = f"{solar_pct:.0f}% solar (real PV meter data, this session's window)"
    else:
        series = [{"name": "Charger power", "unit": "kW", "axis": "power", "color": S1_BLUE,
                   "dash": False, "x": elapsed, "y": power}]
        split_caption = "solar/grid split unavailable (no PV data for this session's window)"

    axes = {"power": {"label": "Charger power (kW)", "color": S1_BLUE, "side": "left"}}

    temp_pts = [p for p in current["points"] if p.get("temp_c") is not None]
    if len(temp_pts) >= 2:
        series.append({"name": "Pack temperature", "unit": "°C", "axis": "temp", "color": S2_ORANGE,
                        "dash": False, "x": [p["elapsed_min"] for p in temp_pts], "y": [p["temp_c"] for p in temp_pts]})
        axes["temp"] = {"label": "Pack temperature (°C)", "color": S2_ORANGE, "side": "right"}

    peak = max(power)
    peak_min = elapsed[power.index(peak)]
    subtitle = f"{title} - {len(pts)} samples over {max(elapsed):.0f} min, peak {peak:.1f} kW at {peak_min:.0f} min\n{split_caption}"
    _write_html(out_html, "Charging curve", subtitle, series, axes, "Elapsed time (min)")


def render_curve_chart(current, hist, hardware_name, hardware, out_png, out_html, title):
    if current["slug"] == "Evnex_Home":
        render_home_curve_chart(current, out_png, out_html, title)
        return
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MultipleLocator

    pts = [p for p in current["points"] if p["soc"] is not None and p["power_kw"] is not None]
    soc = np.array([p["soc"] for p in pts])
    power = np.array([p["power_kw"] for p in pts])
    elapsed = np.array([p["elapsed_min"] for p in pts])
    temp_pts = [p for p in current["points"] if p["soc"] is not None and p.get("temp_c") is not None]
    temp_soc = np.array([p["soc"] for p in temp_pts])
    temp_c = np.array([p["temp_c"] for p in temp_pts])

 # DejaVu Sans Bold hits noticeably different hinting/anti-aliasing than
 # the browser's own font at typical PNG export sizes ("rough" heading
 # complaint) - a slightly larger base DPI and pinning savefig() to the
 # SAME dpi as figure creation (bbox_inches="tight" otherwise leaves the
 # effective resolution to chance) is the concrete, repeatable fix
 # available from matplotlib itself; there's no way to make a rasterized
 # PNG font look byte-identical to the browser's own vector text.
    FIG_DPI = 220
    fig, ax = plt.subplots(figsize=(10, 6), dpi=FIG_DPI)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    for seg_key in ("before", "after"):
        seg = (hist or {}).get("power_kw", {}).get(seg_key, [])
        if seg:
            xs, ys = zip(*seg)
            ax.plot(xs, ys, color=S1_BLUE, linewidth=2, linestyle=(0, (5, 3)), alpha=0.55, zorder=2)

    ax.plot(soc, power, color=S1_BLUE, linewidth=2.5, solid_capstyle="round", zorder=3)
    ax.scatter(soc, power, s=36, color=S1_BLUE, edgecolors=SURFACE, linewidths=1.2, zorder=4)

    peak_i = int(power.argmax())
    ax.scatter([soc[peak_i]], [power[peak_i]], s=90, color=S1_BLUE, edgecolors=SURFACE, linewidths=2, zorder=5)
    ax.annotate(f"peak {power[peak_i]:.0f} kW\nat {soc[peak_i]:.0f}% SoC", (soc[peak_i], power[peak_i]),
                xytext=(12, -30), textcoords="offset points", fontsize=9.5, color=TEXT_PRIMARY,
                fontfamily="DejaVu Sans", va="top")

    if hardware and hardware.get("max_power_kw"):
        cap = hardware["max_power_kw"]
        ax.axhline(cap, color=TEXT_MUTED, linewidth=1, linestyle=(0, (1, 3)), zorder=1)
        cap_x = max(list(soc) + [x for seg in (hist or {}).get("power_kw", {}).values() for x, _ in seg])
        ax.text(cap_x, cap + max(power) * 0.02, f"{hardware.get('tier', 'stall')} cap ({cap:.0f} kW)",
                color=TEXT_MUTED, fontsize=9.5, fontfamily="DejaVu Sans", va="bottom", ha="right")

    ax.yaxis.grid(True, color=GRID, linewidth=1)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.tick_params(axis="both", colors=TEXT_SECONDARY, labelsize=10, length=0)
    all_socs = list(soc) + [x for seg in (hist or {}).get("power_kw", {}).values() for x, _ in seg]
    all_powers = list(power) + [y for seg in (hist or {}).get("power_kw", {}).values() for _, y in seg]
    xlo, xhi = min(all_socs) - 2, max(all_socs) + 2
    ax.set_xlim(xlo, xhi)
    ymax = max(all_powers + ([hardware["max_power_kw"]] if hardware and hardware.get("max_power_kw") else []))
    ax.set_ylim(0, ymax * 1.18)
    ax.xaxis.set_major_locator(MultipleLocator(10))
    ax.set_xlabel("State of charge (%)", color=TEXT_SECONDARY, fontsize=11, fontfamily="DejaVu Sans")
    ax.set_ylabel("Charger power (kW)", color=S1_BLUE, fontsize=11, fontfamily="DejaVu Sans")
    ax.tick_params(axis="y", colors=S1_BLUE)

 # Pack temperature - a THIRD line on this chart (alongside the real
 # power line and its dashed historical estimate), on its own right
 # axis: a cold, un-preconditioned pack visibly suppresses the power
 # curve, so seeing both together is the point (user-requested).
    if len(temp_soc) >= 2:
        ax_temp = ax.twinx()
        for seg_key in ("before", "after"):
            seg = (hist or {}).get("temp_c", {}).get(seg_key, [])
            if seg:
                xs, ys = zip(*seg)
                ax_temp.plot(xs, ys, color=S3_AQUA, linewidth=2, linestyle=(0, (5, 3)), alpha=0.5, zorder=2)
        ax_temp.plot(temp_soc, temp_c, color=S3_AQUA, linewidth=1.8, solid_capstyle="round", zorder=2, alpha=0.9)
        ax_temp.set_ylabel("Pack temperature (°C)", color=S3_AQUA, fontsize=11, fontfamily="DejaVu Sans")
        ax_temp.tick_params(axis="y", colors=S3_AQUA, labelsize=10, length=0)
        all_temps = list(temp_c) + [y for seg in (hist or {}).get("temp_c", {}).values() for _, y in seg]
        tpad = (max(all_temps) - min(all_temps)) * 0.25 or 3
        ax_temp.set_ylim(min(all_temps) - tpad, max(all_temps) + tpad)
        for spine in ("top", "left"):
            ax_temp.spines[spine].set_visible(False)
        ax_temp.spines["right"].set_color(BASELINE)

    ax2 = ax.twiny()
    ax2.set_xlim(ax.get_xlim())
    tick_soc = np.linspace(soc.min(), soc.max(), 6)
    tick_min = np.interp(tick_soc, soc, elapsed)
    ax2.set_xticks(tick_soc)
    ax2.set_xticklabels([f"{m:.0f} min" for m in tick_min])
    ax2.tick_params(axis="x", colors=TEXT_MUTED, labelsize=9, length=0)
    for spine in ("top", "right", "left", "bottom"):
        ax2.spines[spine].set_visible(False)

    fig.suptitle("Charging curve", x=0.085, y=0.975, ha="left", va="top", color=TEXT_PRIMARY,
                 fontsize=17, fontweight="bold", fontfamily="DejaVu Sans")
    subtitle = (f"{title}\n{len(pts)} samples over {elapsed.max():.0f} min, "
                f"{soc.min():.0f}% → {soc.max():.0f}% SoC  •  {_history_caption(hist)}")
    ax.set_title(subtitle, loc="left", color=TEXT_SECONDARY, fontsize=10.5, pad=30, fontfamily="DejaVu Sans",
                 linespacing=1.5)

    fig.tight_layout(rect=(0, 0, 1, 0.88))
    fig.savefig(out_png, facecolor=SURFACE, bbox_inches="tight", dpi=FIG_DPI)
    plt.close(fig)


def render_profile_chart(current, hist, title, out_png, out_html):
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pts = [p for p in current["points"] if p["soc"] is not None]
    elapsed = np.array([p["elapsed_min"] for p in pts])
    soc = np.array([p["soc"] for p in pts])
    voltage = np.array([p["voltage_v"] for p in pts])
    current_a = np.array([p["current_a"] for p in pts])

 # This session's own SoC-per-minute slope near each boundary, used only
 # to place a historical (soc, value) point at a plausible elapsed-time
 # x-position for the dashed extension - never to compute the dashed
 # VALUES themselves (those come from history, per historical_extension()).
    def _elapsed_for_soc(target_soc):
        if target_soc <= soc.min():
            i0, i1 = 0, min(3, len(soc) - 1)
        elif target_soc >= soc.max():
            i0, i1 = max(0, len(soc) - 4), len(soc) - 1
        else:
            return float(np.interp(target_soc, soc, elapsed))
        if soc[i1] == soc[i0]:
            return float(elapsed[i0])
        slope = (elapsed[i1] - elapsed[i0]) / (soc[i1] - soc[i0])
        return float(elapsed[i0] + slope * (target_soc - soc[i0]))

    FIG_DPI = 220
    fig, ax = plt.subplots(figsize=(10, 6), dpi=FIG_DPI)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    for seg_key, style in (("before", None), ("after", None)):
        seg = (hist or {}).get("voltage_v", {}).get(seg_key, [])
        if seg:
            xs = [_elapsed_for_soc(s) for s, _ in seg]
            ys = [v for _, v in seg]
            ax.plot(xs, ys, color=S1_BLUE, linewidth=2, linestyle=(0, (5, 3)), alpha=0.55, zorder=2)

    l1, = ax.plot(elapsed, voltage, color=S1_BLUE, linewidth=2, solid_capstyle="round", zorder=3, label="Pack voltage (V)")
    ax.set_ylabel("Pack voltage (V)", color=S1_BLUE, fontsize=11, fontfamily="DejaVu Sans")
    ax.tick_params(axis="y", colors=S1_BLUE, labelsize=10, length=0)
 # (real file caught live: charge_deepdive_2026-08-01_1733_Tesla_West_
 # Gosford_NSW_profile.png): these limits used to come from ONLY this
 # session's own voltage/current arrays, so a dashed historical segment
 # whose real values fell outside this session's own range got silently
 # clipped off the PNG canvas entirely (the HTML version was unaffected -
 # its own axis ranges are already computed from every series' y-values,
 # same class of fix as the x-axis one below). Both ax and ax_cur's
 # limits now include the historical segments' own values too.
    all_voltages = list(voltage) + [y for seg in (hist or {}).get("voltage_v", {}).values() for _, y in seg]
    vpad = (max(all_voltages) - min(all_voltages)) * 0.5 or 5
    ax.set_ylim(min(all_voltages) - vpad, max(all_voltages) + vpad * 2)

    ax_soc = ax.twinx()
    l2, = ax_soc.plot(elapsed, soc, color=TEXT_SECONDARY, linewidth=2, linestyle=(0, (5, 3)), zorder=2, label="State of charge (%)")
    ax_soc.set_ylabel("State of charge (%)", color=TEXT_SECONDARY, fontsize=11, fontfamily="DejaVu Sans")
    ax_soc.tick_params(axis="y", colors=TEXT_SECONDARY, labelsize=10, length=0)
    ax_soc.set_ylim(0, 105)
    for spine in ("top", "left"):
        ax_soc.spines[spine].set_visible(False)
    ax_soc.spines["right"].set_color(BASELINE)

    ax_cur = ax.twinx()
    ax_cur.spines["right"].set_position(("axes", 1.16))
    for seg_key in ("before", "after"):
        seg = (hist or {}).get("current_a", {}).get(seg_key, [])
        if seg:
            xs = [_elapsed_for_soc(s) for s, _ in seg]
            ys = [v for _, v in seg]
            ax_cur.plot(xs, ys, color=S2_ORANGE, linewidth=2, linestyle=(0, (5, 3)), alpha=0.55, zorder=2)
    l3, = ax_cur.plot(elapsed, current_a, color=S2_ORANGE, linewidth=2, solid_capstyle="round", zorder=3, label="Pack current (A)")
    ax_cur.set_ylabel("Pack current (A)", color=S2_ORANGE, fontsize=11, fontfamily="DejaVu Sans")
    ax_cur.tick_params(axis="y", colors=S2_ORANGE, labelsize=10, length=0)
    all_currents = list(current_a) + [y for seg in (hist or {}).get("current_a", {}).values() for _, y in seg]
    cpad = (max(all_currents) - min(all_currents)) * 0.15 or 5
    ax_cur.set_ylim(min(all_currents) - cpad, max(all_currents) + cpad)
    for spine in ("top", "left"):
        ax_cur.spines[spine].set_visible(False)
    ax_cur.spines["right"].set_color(BASELINE)

    ax.xaxis.grid(False)
    ax.yaxis.grid(True, color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_visible(True)
    ax.spines["left"].set_color(BASELINE)
    ax.spines["bottom"].set_color(BASELINE)
    ax.tick_params(axis="x", colors=TEXT_SECONDARY, labelsize=10, length=0)
    ax.set_xlabel("Elapsed time (min)", color=TEXT_SECONDARY, fontsize=11, fontfamily="DejaVu Sans")
    hist_x = []
    for metric in ("voltage_v", "current_a"):
        for seg_key in ("before", "after"):
            seg = (hist or {}).get(metric, {}).get(seg_key, [])
            hist_x.extend(_elapsed_for_soc(s) for s, _ in seg)
    all_x = list(elapsed) + hist_x
    ax.set_xlim(min(all_x), max(all_x))

    lines = [l1, l2, l3]
    leg = ax.legend(lines, [ln.get_label() for ln in lines], loc="lower center",
                     bbox_to_anchor=(0.5, -0.34), ncol=3, frameon=False, fontsize=9.5)
    for text in leg.get_texts():
        text.set_color(TEXT_SECONDARY)

    fig.suptitle("Charging electrical profile", x=0.085, y=0.975, ha="left", va="top", color=TEXT_PRIMARY,
                 fontsize=17, fontweight="bold", fontfamily="DejaVu Sans")
    ax.set_title(f"{title}\n{_history_caption(hist)}", loc="left", color=TEXT_SECONDARY,
                 fontsize=10.5, pad=16, fontfamily="DejaVu Sans", linespacing=1.5)

    fig.tight_layout(rect=(0, 0.02, 0.86, 0.88))
    fig.savefig(out_png, facecolor=SURFACE, bbox_inches="tight", dpi=FIG_DPI)
    plt.close(fig)


_HTML_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: {surface}; color: {text_primary}; }}
  h1 {{ font-size: 18px; margin: 16px 20px 4px; }}
  .sub {{ margin: 0 20px 12px; color: {text_secondary}; font-size: 13px; white-space: pre-line; }}
 #wrap {{ position: relative; margin: 0 12px 12px; }}
  canvas {{ display: block; background: {surface}; border: 1px solid {grid}; border-radius: 6px; }}
 #tooltip {{
    position: absolute; pointer-events: none; background: rgba(11,11,11,0.92); color: #fff;
    padding: 8px 10px; border-radius: 6px; font-size: 12px; line-height: 1.5; display: none;
    white-space: nowrap; z-index: 10;
  }}
 #legend {{ margin: 4px 20px 14px; font-size: 12px; }}
 #legend span {{ margin-right: 16px; }}
 #legend .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; }}
 #legend .dash {{ display: inline-block; width: 14px; height: 0; border-top: 2px dashed currentColor; margin-right: 5px; vertical-align: middle; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="sub">{subtitle}</div>
<div id="legend"></div>
<div id="wrap">
  <canvas id="chart" width="1100" height="520"></canvas>
  <div id="tooltip"></div>
</div>
<script>
const SERIES = {series_json};
const AXES = {axes_json};
const HLINE = {hline_json};
const X_LABEL = {x_label_json};

const canvas = document.getElementById('chart');
const ctx = canvas.getContext('2d');
const tooltip = document.getElementById('tooltip');
const legend = document.getElementById('legend');
const W = canvas.width, H = canvas.height;

const rightAxes = Object.keys(AXES).filter(k => AXES[k].side === 'right');
const padL = 64, padR = 24 + rightAxes.length * 58, padT = 16, padB = 44;
const plotW = W - padL - padR, plotH = H - padT - padB;

let xAll = [];
SERIES.forEach(s => xAll = xAll.concat(s.x));
const xLo = Math.min(...xAll), xHi = Math.max(...xAll);
function xAt(x) {{ return padL + (xHi === xLo ? 0 : (x - xLo) / (xHi - xLo) * plotW); }}

function axisRange(key) {{
  let vals = [];
  SERIES.filter(s => s.axis === key).forEach(s => s.y.forEach(v => {{ if (v !== null && v !== undefined) vals.push(v); }}));
  if (HLINE && HLINE.axis === key) vals.push(HLINE.value);
  if (vals.length === 0) return [0, 1];
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (lo === hi) {{ lo -= 1; hi += 1; }}
  const pad = (hi - lo) * 0.12;
  return [lo - pad, hi + pad];
}}
const ranges = {{}};
Object.keys(AXES).forEach(k => ranges[k] = axisRange(k));

function yAt(v, key) {{
  const [lo, hi] = ranges[key];
  return padT + plotH - (v - lo) / (hi - lo) * plotH;
}}

function draw() {{
  ctx.clearRect(0, 0, W, H);
  ctx.font = '11px sans-serif';

  const leftKey = Object.keys(AXES).find(k => AXES[k].side === 'left');
  if (leftKey) {{
    ctx.strokeStyle = '{grid}';
    ctx.fillStyle = AXES[leftKey].color;
    ctx.textAlign = 'right';
    const [lo, hi] = ranges[leftKey];
    for (let g = 0; g <= 5; g++) {{
      const v = lo + (hi - lo) * (g / 5);
      const y = yAt(v, leftKey);
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
      ctx.fillText(v.toFixed(0), padL - 8, y + 3);
    }}
    ctx.save();
    ctx.translate(16, padT + plotH / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.textAlign = 'center';
    ctx.fillText(AXES[leftKey].label, 0, 0);
    ctx.restore();
  }}

  rightAxes.forEach((key, idx) => {{
    const x = W - padR + 12 + idx * 58;
    ctx.fillStyle = AXES[key].color;
    ctx.textAlign = 'left';
    const [lo, hi] = ranges[key];
    for (let g = 0; g <= 5; g++) {{
      const v = lo + (hi - lo) * (g / 5);
      const y = yAt(v, key);
      ctx.fillText(v.toFixed(0), x, y + 3);
    }}
 // a right axis's own name (e.g. "Pack current (A)") was never
 // drawn at all - only its tick numbers - even though the left axis
 // (and the matching matplotlib PNG) always shows one. Mirrors the
 // left-axis label code above, rotated the other way since it sits on
 // the opposite side.
    ctx.save();
    ctx.translate(x + 34, padT + plotH / 2);
    ctx.rotate(Math.PI / 2);
    ctx.textAlign = 'center';
    ctx.fillText(AXES[key].label, 0, 0);
    ctx.restore();
  }});

  if (HLINE) {{
    const y = yAt(HLINE.value, HLINE.axis);
    ctx.strokeStyle = '{text_muted}';
    ctx.setLineDash([2, 4]);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '{text_muted}';
    ctx.textAlign = 'left';
    ctx.fillText(HLINE.label, padL + 4, y - 6);
  }}

  ctx.textAlign = 'center';
  ctx.fillStyle = '{text_secondary}';
  const xTicks = 8;
  for (let t = 0; t <= xTicks; t++) {{
    const xv = xLo + (xHi - xLo) * (t / xTicks);
    ctx.fillText(SERIES[0].x_is_time ? xv.toFixed(0) : xv.toFixed(0), xAt(xv), H - padB + 16);
  }}
  ctx.fillText(X_LABEL, padL + plotW / 2, H - 8);

  SERIES.forEach(s => {{
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 2;
    ctx.globalAlpha = s.dash ? 0.55 : 1;
    if (s.dash) ctx.setLineDash([6, 4]); else ctx.setLineDash([]);
    ctx.beginPath();
    let started = false;
    for (let i = 0; i < s.x.length; i++) {{
      const v = s.y[i];
      if (v === null || v === undefined) {{ started = false; continue; }}
      const px = xAt(s.x[i]), py = yAt(v, s.axis);
      if (!started) {{ ctx.moveTo(px, py); started = true; }} else {{ ctx.lineTo(px, py); }}
    }}
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
  }});

  legend.innerHTML = SERIES.filter(s => !s.dash).map(s =>
    `<span><span class="dot" style="background:${{s.color}}"></span>${{s.name}} (${{s.unit}})</span>`
  ).join('') + (SERIES.some(s => s.dash) ?
    `<span style="color:{text_muted}"><span class="dash"></span>estimated from history</span>` : '');
}}

canvas.addEventListener('mousemove', (ev) => {{
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width, scaleY = canvas.height / rect.height;
  const mx = (ev.clientX - rect.left) * scaleX;
  if (mx < padL || mx > W - padR) {{ tooltip.style.display = 'none'; draw(); return; }}
  const xv = xLo + (mx - padL) / plotW * (xHi - xLo);

  draw();
  ctx.strokeStyle = '#bbb';
  ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(mx, padT); ctx.lineTo(mx, H - padB); ctx.stroke();
  ctx.setLineDash([]);

  let lines = [];
  SERIES.forEach(s => {{
    if (s.dash) return;
 // Only read a value where this series actually has data - otherwise
 // hovering past where a shorter series (e.g. a partial telemetry
 // segment) ends would silently show its nearest stale endpoint as if
 // it were a real reading at the cursor's x.
    const sMin = Math.min(...s.x), sMax = Math.max(...s.x);
    if (xv < sMin || xv > sMax) return;
    let best = null, bestDist = Infinity;
    for (let i = 0; i < s.x.length; i++) {{
      const d = Math.abs(s.x[i] - xv);
      if (d < bestDist) {{ bestDist = d; best = i; }}
    }}
    if (best !== null && s.y[best] !== null && s.y[best] !== undefined) {{
      lines.push(`${{s.name}}: ${{s.y[best].toFixed(2)}} ${{s.unit}}`);
 // Pin a small marker at the exact sample this value comes from, so
 // each tooltip line is visibly anchored to a real point on its own
 // line, not just implied by the crosshair's position.
      const px = xAt(s.x[best]), py = yAt(s.y[best], s.axis);
      ctx.beginPath();
      ctx.arc(px, py, 6, 0, Math.PI * 2);
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = s.color;
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(px, py, 3.5, 0, Math.PI * 2);
      ctx.fillStyle = s.color;
      ctx.fill();
    }}
  }});
  if (lines.length === 0) {{ tooltip.style.display = 'none'; return; }}
  tooltip.innerHTML = lines.join('<br>');
  tooltip.style.display = 'block';
  const my = (ev.clientY - rect.top) * scaleY;
  let left = mx + 12, top = my - 10;
  if (left > W - 220) left = mx - 12 - 200;
 // Convert back from canvas-pixel space to CSS pixel space before
 // positioning the tooltip DIV - the crosshair/markers above are drawn
 // straight onto the canvas's own backing-pixel grid, but this tooltip
 // is a separate absolutely-positioned element, so its coordinates need
 // the inverse of the same scale factor whenever the canvas is
 // displayed at a different CSS size than its backing resolution (the
 // real reason a hover tooltip can drift away from the point it
 // describes).
  tooltip.style.left = (left / scaleX) + 'px';
  tooltip.style.top = (Math.max(0, top) / scaleY) + 'px';
}});
canvas.addEventListener('mouseleave', () => {{ tooltip.style.display = 'none'; draw(); }});

draw();
</script>
</body>
</html>
"""


def _write_html(out_html, title, subtitle, series, axes, x_label, hline=None):
    html = _HTML_TEMPLATE.format(
        title=title,
        subtitle=subtitle.replace("\n", "&#10;"),
        series_json=json.dumps(series),
        axes_json=json.dumps(axes),
        hline_json=json.dumps(hline),
        x_label_json=json.dumps(x_label),
        surface=SURFACE, text_primary=TEXT_PRIMARY, text_secondary=TEXT_SECONDARY,
        text_muted=TEXT_MUTED, grid=GRID,
    )
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)


def render_curve_html(current, hist, hardware, out_html, title):
    if current["slug"] == "Evnex_Home":
        render_home_curve_html(current, out_html, title)
        return
    pts = [p for p in current["points"] if p["soc"] is not None and p["power_kw"] is not None]
    soc = [p["soc"] for p in pts]
    power = [p["power_kw"] for p in pts]
    peak = max(power)
    peak_soc = soc[power.index(peak)]

    series = [{"name": "Charger power", "unit": "kW", "axis": "power", "color": S1_BLUE,
               "dash": False, "x": soc, "y": power}]
    for seg_key in ("before", "after"):
        seg = (hist or {}).get("power_kw", {}).get(seg_key, [])
        if seg:
            xs, ys = zip(*seg)
            series.append({"name": "Charger power (estimated)", "unit": "kW", "axis": "power",
                            "color": S1_BLUE, "dash": True, "x": list(xs), "y": list(ys)})

    hline = None
    if hardware and hardware.get("max_power_kw"):
        hline = {"axis": "power", "value": hardware["max_power_kw"],
                 "label": f"{hardware.get('tier', 'stall')} cap ({hardware['max_power_kw']:.0f} kW)"}

    axes = {"power": {"label": "Charger power (kW)", "color": S1_BLUE, "side": "left"}}

 # Pack temperature - the same third line the PNG now draws (a cold,
 # un-preconditioned pack visibly suppresses the power curve).
    temp_pts = [p for p in current["points"] if p["soc"] is not None and p.get("temp_c") is not None]
    if len(temp_pts) >= 2:
        series.append({"name": "Pack temperature", "unit": "°C", "axis": "temp", "color": S3_AQUA,
                        "dash": False, "x": [p["soc"] for p in temp_pts], "y": [p["temp_c"] for p in temp_pts]})
        for seg_key in ("before", "after"):
            seg = (hist or {}).get("temp_c", {}).get(seg_key, [])
            if seg:
                xs, ys = zip(*seg)
                series.append({"name": "Pack temperature (estimated)", "unit": "°C", "axis": "temp",
                                "color": S3_AQUA, "dash": True, "x": list(xs), "y": list(ys)})
        axes["temp"] = {"label": "Pack temperature (°C)", "color": S3_AQUA, "side": "right"}

    subtitle = (f"{title} - {len(pts)} samples, {min(soc):.0f}% -> {max(soc):.0f}% SoC, "
                f"peak {peak:.0f} kW at {peak_soc:.0f}%\n{_history_caption(hist)}")
    _write_html(out_html, "Charging curve", subtitle, series, axes, "State of charge (%)", hline)


def render_profile_html(current, hist, out_html, title):
    def _elapsed_for_soc(pts, target_soc):
        soc = [p["soc"] for p in pts]
        elapsed = [p["elapsed_min"] for p in pts]
        if target_soc <= soc[0]:
            i0, i1 = 0, min(3, len(soc) - 1)
        elif target_soc >= soc[-1]:
            i0, i1 = max(0, len(soc) - 4), len(soc) - 1
        else:
            for i in range(1, len(soc)):
                if target_soc <= soc[i]:
                    lo, hi = soc[i - 1], soc[i]
                    if hi == lo:
                        return elapsed[i]
                    frac = (target_soc - lo) / (hi - lo)
                    return elapsed[i - 1] + frac * (elapsed[i] - elapsed[i - 1])
            return elapsed[-1]
        if soc[i1] == soc[i0]:
            return elapsed[i0]
        slope = (elapsed[i1] - elapsed[i0]) / (soc[i1] - soc[i0])
        return elapsed[i0] + slope * (target_soc - soc[i0])

    pts = sorted([p for p in current["points"] if p["soc"] is not None], key=lambda p: p["elapsed_min"])
    elapsed = [p["elapsed_min"] for p in pts]
    soc = [p["soc"] for p in pts]
    voltage = [p["voltage_v"] for p in pts]
    current_a = [p["current_a"] for p in pts]

    series = [
        {"name": "Pack voltage", "unit": "V", "axis": "voltage", "color": S1_BLUE, "dash": False,
         "x": elapsed, "y": voltage},
        {"name": "State of charge", "unit": "%", "axis": "soc", "color": TEXT_SECONDARY, "dash": False,
         "x": elapsed, "y": soc},
        {"name": "Pack current", "unit": "A", "axis": "current", "color": S2_ORANGE, "dash": False,
         "x": elapsed, "y": current_a},
    ]
    for metric, axis, color in (("voltage_v", "voltage", S1_BLUE), ("current_a", "current", S2_ORANGE)):
        for seg_key in ("before", "after"):
            seg = (hist or {}).get(metric, {}).get(seg_key, [])
            if seg:
                xs = [_elapsed_for_soc(pts, s) for s, _ in seg]
                ys = [v for _, v in seg]
                series.append({"name": f"{metric} (estimated)", "unit": "", "axis": axis,
                                "color": color, "dash": True, "x": xs, "y": ys})

    axes = {
        "voltage": {"label": "Pack voltage (V)", "color": S1_BLUE, "side": "left"},
        "soc": {"label": "State of charge (%)", "color": TEXT_SECONDARY, "side": "right"},
        "current": {"label": "Pack current (A)", "color": S2_ORANGE, "side": "right"},
    }
    subtitle = f"{title}\n{_history_caption(hist)}"
    _write_html(out_html, "Charging electrical profile", subtitle, series, axes, "Elapsed time (min)")


def process_session(session, all_sessions, registry, force=False):
    stem = os.path.splitext(session["path"])[0]
    curve_png, curve_html = stem + "_curve.png", stem + "_curve.html"
    profile_png, profile_html = stem + "_profile.png", stem + "_profile.html"
    if not force and all(os.path.isfile(p) for p in (curve_png, curve_html, profile_png, profile_html)):
        return False

    hardware_name, hardware = match_hardware(session["slug"], registry)
    hist = historical_extension(all_sessions, session, registry)
    title = build_title(session, hardware_name, hardware)

    render_curve_chart(session, hist, hardware_name, hardware, curve_png, curve_html, title)
    render_curve_html(session, hist, hardware, curve_html, title)
    render_profile_chart(session, hist, title, profile_png, profile_html)
    render_profile_html(session, hist, profile_html, title)
    return True


def main():
    parser = argparse.ArgumentParser(
        usage="analyse_charge_curves.py [CSV ...] [--deepdive-dir DIR] [--force] [--refresh-related]\n",
        description="""
NAME
    analyse_charge_curves.py - build charging-curve charts (power-vs-SoC, voltage/current/SoC-vs-time)

DESCRIPTION
    Builds charging-curve charts (power-vs-SoC and voltage/current/SoC-vs-time,
    each as PNG + interactive HTML) from charge deep-dive telemetry CSVs - every
    session Tessie itself sees, Superchargers/3rd-party and home Evnex alike.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES
  analyse_charge_curves.py
  analyse_charge_curves.py charge_deepdive_142.csv --force
  analyse_charge_curves.py --refresh-related
"""
    )
    parser.add_argument("csv", nargs="*", help="Specific charge_deepdive_*.csv file(s) to chart. "
                        "Omit to process every telemetry CSV under the deep-dive folder.")
    parser.add_argument("--deepdive-dir", help="Override the charges/deepdive folder "
                        "(default: resolved from Tessie's own data-home config).")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate charts even if the output files already exist.")
    parser.add_argument("--refresh-related", action="store_true",
                        help="When a brand-new session is charted, also regenerate every OTHER "
                             "existing session at the same site/slug, so their dashed historical "
                             "estimate picks up this new session too (the 'big picture' keeps "
                             "growing as more sessions accumulate, instead of only ever helping "
                             "sessions charted after it). Off by default since it can mean "
                             "regenerating many files for one new session at a busy site.")
    args = parser.parse_args()

    deepdive_dir = args.deepdive_dir
    if not deepdive_dir:
        data_home = resolve_tessie_data_home()
        if data_home:
            deepdive_dir = os.path.join(data_home, "charges", "deepdive")
    if not deepdive_dir or not os.path.isdir(deepdive_dir):
        print("Could not resolve a charges/deepdive folder - pass --deepdive-dir explicitly.")
        return 1

    all_sessions = discover_deepdive_sessions(deepdive_dir)
    if not all_sessions:
        print(f"No telemetry deep-dive CSVs found under {deepdive_dir}")
        return 0

    registry = load_charger_registries()
    targets = all_sessions
    if args.csv:
        wanted = {os.path.abspath(p) for p in args.csv}
        targets = [s for s in all_sessions if os.path.abspath(s["path"]) in wanted]

    made, skipped = 0, 0
    newly_made_slugs = set()
    for session in targets:
        if process_session(session, all_sessions, registry, force=args.force):
            made += 1
            newly_made_slugs.add(session["slug"])
            print(f"✅ {os.path.basename(session['path'])} -> _curve/_profile .png/.html")
        else:
            skipped += 1

    refreshed = 0
    if args.refresh_related and newly_made_slugs:
        already_done = {s["path"] for s in targets}
        siblings = [s for s in all_sessions if s["slug"] in newly_made_slugs and s["path"] not in already_done]
        for session in siblings:
            if process_session(session, all_sessions, registry, force=True):
                refreshed += 1
                print(f"🔄 {os.path.basename(session['path'])} refreshed (new sibling session at this site)")

    summary = f"\n{made} session(s) charted, {skipped} already up to date (use --force to regenerate)."
    if args.refresh_related:
        summary += f" {refreshed} related session(s) refreshed."
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
