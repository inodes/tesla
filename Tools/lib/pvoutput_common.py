#!/usr/bin/env python3
"""
pvoutput_common.py - Shared PVOutput API plumbing
====================================================
Config/auth loading and the low-level HTTP call, factored out the same
way tessie_api_common.py serves test_tessie_api.py/tessie_api_sync.py -
so test_pvoutput_api.py and any later real sync script share one
implementation instead of drifting apart.

See Tessie/PVOUTPUT_API.md for endpoint/field notes, confirmed
parameters, rate limits, and known limitations (this module is not a
full API client - just enough plumbing for this repo's own scripts).

REQ-071 (2026-09-15): added the window-level solar-split helpers below
(parse_history_readings/day_intervals/window_energy_split/
fetch_day_readings/pv_window_split) - PVOutput becoming the PRIMARY
source (not just a secondary cross-check) for a charging session's
solar-vs-grid cost split, per the user's own explicit reasoning: of the
three data sources this repo integrates, Evnex only sees the car's own
usage plus a single CT clamp (good enough for ITS OWN solar% estimate,
but that estimate is driven by a CONFIGURED tariff-hours schedule, not
a live per-session measurement - see distributionByTariff in
analysis_ev_session_cost.py); AGL only sees what crosses its meter
(import/export) and is manual-sync besides; PVOutput has the only TWO
real CT clamps (generation AND consumption, independently), so it's the
only source that can measure - not infer - real household self-
consumption at 5-minute resolution. See analysis_ev_session_cost.py's
module docstring for how the resulting pv_solar_fraction gets applied
to a session's own kWh.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# REQ-067 (2026-09-15, AGENTS.local.md): this file moved from Tools/ into
# Tools/lib/ (mirroring the Tools/providers_chargers/ package), one directory
# deeper than when SCRIPT_DIR->REPO_ROOT was a single dirname() hop - now needs
# the same two-hop pattern providers_chargers/*.py already uses (_script_dir -> _tools_dir
# -> _repo_root) so REPO_ROOT still resolves to the actual repo root, not Tools/.
TOOLS_DIR = os.path.dirname(SCRIPT_DIR)
REPO_ROOT = os.path.dirname(TOOLS_DIR)
API_BASE = "https://pvoutput.org/service/r2"

# Response headers PVOutput returns when the request itself sends
# "X-Rate-Limit: 1" - see Tessie/PVOUTPUT_API.md's Rate Limits section.
# Names confirmed against the real spec the user pasted (an earlier
# secondary-sourced pass of this doc guessed "X-Rate-Limit-Total" for the
# second one, which was wrong).
RATE_LIMIT_HEADERS = ("X-Rate-Limit-Remaining", "X-Rate-Limit-Limit", "X-Rate-Limit-Reset")

# History (h=1) row shape - confirmed field-for-field against the real
# account (Tessie/PVOUTPUT_API.md, "Confirmed live against the real
# account" #2): Date, Time, Energy Generation, Energy Efficiency,
# Instantaneous Power, Average Power, Normalised Output, Energy
# Consumption, Power Consumption, Temperature, Voltage. This is a
# DIFFERENT field order from the "Basic" (no h=1) shape - Energy
# Consumption sits at index 7 here, NOT index 4 (index 4 is only
# Energy Consumption in the Basic shape, which this repo never
# requests). REQ-071: an earlier version of
# tessie_charging_analyzer.py's get_energy_source_crosscheck() only
# ever read index 2 (Energy Generation, which happens to sit at the
# same index in both shapes) and never touched Energy Consumption at
# all, so this mislabeling was harmless until PVOutput's own
# consumption column became load-bearing for the PVOutput-primary
# solar split below.
HISTORY_ENERGY_GEN_IDX = 2
HISTORY_ENERGY_CONS_IDX = 7

# Cross-source (PVOutput-real-import vs AGL-real-import) conflict
# thresholds - single source of truth since two different top-level
# scripts (tessie_charging_analyzer.py's live single-session path,
# analysis_ev_session_cost.py's batch path) both need to agree on them.
# See either caller's own comment near its use for the full rationale:
# PVOutput and AGL are two INDEPENDENT real meters measuring the same
# physical import for the same window, so a material disagreement is a
# data-quality flag (clock drift, an un-synced PVOutput day, a stale
# AGL export), not a claim about solar usage.
PV_AGL_CONFLICT_MIN_COVERAGE = 0.9  # ignore if PVOutput didn't have readings for most of the window
PV_AGL_CONFLICT_MIN_KWH = 0.5  # ignore tiny background-import noise on near-zero windows
PV_AGL_CONFLICT_RATIO = 0.3  # flag if the two sources differ by >=30% of the larger figure


def load_config():
    # TODO-034 (2026-09-15): used to duplicate tessie_api_common.py's own
    # CONFIG_PATH/load_config() with its own hardcoded "config.json"
    # path - now just delegates, so this module and every other caller
    # agree on where the real config.json is (root, with a Tessie/ legacy
    # fallback) from ONE place rather than two copies that could drift.
    # Kept as its own function (not removed) since analysis_ev_session_cost.py
    # and others already import it by this name.
    from .tessie_api_common import load_config as _shared_load_config
    return _shared_load_config()


def get_api_key(cli_value, config):
    return cli_value or os.environ.get("PVOUTPUT_API_KEY") or config.get("pvoutput_api_key")


def get_system_id(cli_value, config):
    return cli_value or os.environ.get("PVOUTPUT_SYSTEM_ID") or config.get("pvoutput_system_id")


def api_get(endpoint, api_key, system_id, params=None, timeout=30):
    """GET https://pvoutput.org/service/r2/{endpoint}.jsp?... using the
    confirmed header-based auth (X-Pvoutput-Apikey/X-Pvoutput-SystemId -
    NOT the X-Pid/X-Apikey names an earlier secondary-sourced pass of
    this repo's docs guessed, before the user supplied the real spec -
    see AGENTS.md REQ-039). Always sends "X-Rate-Limit: 1" so the
    response carries the account's real remaining-request headers.

    Returns (status_code_or_None, raw_bytes, response_headers_dict).
    status is None only for a connection-level failure (DNS/timeout/
    blocked egress), not an HTTP error - PVOutput returns plain-text
    error bodies with 4xx/5xx statuses, not JSON, so the caller decides
    how to surface those."""
    url = f"{API_BASE}/{endpoint}.jsp"
    if params:
        url += f"?{urllib.parse.urlencode(params)}"
    headers = {
        "X-Pvoutput-Apikey": api_key,
        "X-Pvoutput-SystemId": system_id,
        "X-Rate-Limit": "1",
        "User-Agent": "tesla-tools/1.0",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})
    except Exception as e:
        return None, str(e).encode("utf-8"), {}


def print_rate_limit(headers, log=print):
    """HTTP headers are case-insensitive, but api_get() returns a plain
    dict() of whatever casing the server actually sent - a real response
    seen from the user's own account came back with different casing
    than assumed here, so this does a case-insensitive lookup instead of
    a direct dict .get() on the documented header names."""
    lowered = {k.lower(): v for k, v in headers.items()}
    found = {h: lowered.get(h.lower()) for h in RATE_LIMIT_HEADERS if lowered.get(h.lower()) is not None}
    if found:
        parts = [f"{k.replace('X-Rate-Limit-', '')}={v}" for k, v in found.items()]
        log(f"   rate limit: {', '.join(parts)}")
    else:
        log("   rate limit: (no X-Rate-Limit-* headers in response)")


def parse_status_rows(body):
    """Splits a getstatus.jsp response body into rows - PVOutput
    separates multiple readings (h=1 history) with ';', and each
    reading's own fields with ','. A single non-history reading is just
    one such comma-separated row with no semicolon."""
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    return [row.split(",") for row in text.split(";") if row]


def parse_history_readings(rows):
    """rows from parse_status_rows() (a getstatus.jsp?h=1 response) ->
    sorted [(datetime, energy_gen_wh, energy_cons_wh), ...], skipping
    any row too short or with an unparseable Date/Time/Energy field.
    Both energy figures are CUMULATIVE-FOR-THE-DAY counters (reset to
    ~0 at each day's first reading), never a per-reading delta - see
    day_intervals() below for turning these into real per-interval
    deltas."""
    out = []
    for row in rows:
        if len(row) <= HISTORY_ENERGY_CONS_IDX:
            continue
        try:
            r_time = datetime.strptime(f"{row[0]} {row[1]}", "%Y%m%d %H:%M")
            r_gen_wh = float(row[HISTORY_ENERGY_GEN_IDX])
            r_cons_wh = float(row[HISTORY_ENERGY_CONS_IDX])
        except (ValueError, IndexError):
            continue
        out.append((r_time, r_gen_wh, r_cons_wh))
    out.sort(key=lambda t: t[0])
    return out


def day_intervals(readings):
    """[(interval_start, interval_end, gen_delta_wh, cons_delta_wh), ...]
    from parse_history_readings()'s per-day cumulative readings. The
    FIRST reading of a day is treated as an interval from that day's
    own midnight (never from whatever reading preceded it - which may
    belong to a different day, whose cumulative value means nothing
    here, since the real counters reset to ~0 at midnight - confirmed
    against a real reading in Tessie/PVOUTPUT_API.md) to that first
    reading's own time, with delta = the reading's raw value (baseline
    0). Every later reading in the same day is a delta from the
    previous one. Deltas are clamped to >=0 - a lower reading than the
    one before it within the same day means a counter reset or bad
    data, never real negative generation/consumption."""
    if not readings:
        return []
    out = []
    prev_time = readings[0][0].replace(hour=0, minute=0, second=0, microsecond=0)
    prev_gen = 0.0
    prev_cons = 0.0
    for r_time, r_gen, r_cons in readings:
        # A NaN reading (PVOutput sends literal "NaN" text for a field
        # it didn't log at that instant - confirmed real, e.g.
        # Temperature/Voltage often, and seen LIVE on this account's own
        # very first 00:00 reading of a day for Energy Generation) is
        # skipped entirely rather than treated as a real 0 or allowed to
        # poison every later delta. `x != x` detects it without needing
        # `import math` (a NaN never equals itself). Skipping just
        # widens the NEXT valid reading's own interval to span the gap,
        # rather than zeroing real generation/consumption during it - an
        # earlier version let a NaN reading become prev_gen/prev_cons,
        # which (since anything - NaN is also NaN) silently zeroed every
        # later delta for the rest of the day via the max(0.0, ...)
        # clamp below, caught in this file's own live smoke test against
        # a real account response.
        if r_gen != r_gen or r_cons != r_cons:
            continue
        gen_delta = max(0.0, r_gen - prev_gen)
        cons_delta = max(0.0, r_cons - prev_cons)
        if r_time > prev_time:
            out.append((prev_time, r_time, gen_delta, cons_delta))
        prev_time, prev_gen, prev_cons = r_time, r_gen, r_cons
    return out


def window_energy_split(intervals, window_start, window_end):
    """Sums each interval's (gen_delta, cons_delta) from day_intervals(),
    prorated by its overlap fraction with [window_start, window_end),
    into real, directly-measured generation/consumption/self-
    consumption/export/import for that window. Self-consumption is
    summed at the INTERVAL level (min(gen_delta, cons_delta) per
    5-minute interval, not min() of the two window TOTALS) - this is
    what actually uses both of PVOutput's CT clamps together rather
    than approximating from day-level totals, which can silently
    overstate self-consumption whenever generation and consumption
    don't track together within the window (e.g. a sunny midday dip in
    household usage during a long overnight-to-morning session: a
    totals-only min() would credit the whole window as self-consumed
    even though usage genuinely dipped below generation only part of
    the time)."""
    generation_kwh = 0.0
    consumption_kwh = 0.0
    self_consumed_kwh = 0.0
    covered_seconds = 0.0
    for i_start, i_end, gen_delta_wh, cons_delta_wh in intervals:
        overlap_start = max(i_start, window_start)
        overlap_end = min(i_end, window_end)
        overlap_secs = (overlap_end - overlap_start).total_seconds()
        if overlap_secs <= 0:
            continue
        i_secs = (i_end - i_start).total_seconds()
        frac = (overlap_secs / i_secs) if i_secs > 0 else 0.0
        gen_wh = gen_delta_wh * frac
        cons_wh = cons_delta_wh * frac
        generation_kwh += gen_wh / 1000.0
        consumption_kwh += cons_wh / 1000.0
        self_consumed_kwh += min(gen_wh, cons_wh) / 1000.0
        covered_seconds += overlap_secs
    window_secs = (window_end - window_start).total_seconds()
    coverage = (covered_seconds / window_secs) if window_secs > 0 else 0.0
    return {
        "generation_kwh": generation_kwh,
        "consumption_kwh": consumption_kwh,
        "self_consumed_kwh": self_consumed_kwh,
        "export_kwh": max(0.0, generation_kwh - self_consumed_kwh),
        "import_kwh": max(0.0, consumption_kwh - self_consumed_kwh),
        "coverage": coverage,
    }


def fetch_day_readings(date_obj, api_key, system_id):
    """(readings, error) for one calendar date() - readings from
    parse_history_readings(), error is None on success (even if the day
    has zero readings - a legitimate "nothing logged yet" case for
    today, not a fetch failure) or a short string describing what went
    wrong (HTTP status, connection failure)."""
    date_str = date_obj.strftime("%Y%m%d")
    status, body, _hdrs = api_get("getstatus", api_key, system_id, params={"d": date_str, "h": 1, "limit": 288})
    if status is None:
        return [], f"connection failed: {body.decode('utf-8', errors='replace')}"
    if status != 200:
        return [], f"HTTP {status}: {body.decode('utf-8', errors='replace')[:200]}"
    rows = parse_status_rows(body)
    return parse_history_readings(rows), None


def pv_window_split(local_start, local_end, api_key, system_id, day_cache=None):
    """Real PVOutput-measured generation/consumption/self-consumption/
    export/import for [local_start, local_end) - the PRIMARY source
    (REQ-071) for a session's solar-vs-grid cost split, using BOTH of
    PVOutput's CT clamps directly rather than Evnex's own configured
    tariff-hours schedule or inferring self-consumption from AGL's
    export figure alone.

    `day_cache` (optional, caller-owned dict keyed by "YYYYMMDD") lets a
    batch caller share one fetch per calendar day across every session
    that falls on it, instead of refetching the same day's up-to-288
    readings once per session - meaningful against PVOutput's own rate
    limit (300/hr donation-tier, 60/hr free - Tessie/PVOUTPUT_API.md).

    Returns a dict with generation_kwh/consumption_kwh/
    self_consumed_kwh/export_kwh/import_kwh/solar_fraction/coverage/
    error - every numeric field is None (and error set) only when NO
    reading at all could be obtained for the window; a partial-coverage
    window (session extends past the last synced reading) still
    returns real numbers plus a coverage fraction < 1.0 so the caller
    can decide whether to trust it."""
    day_cache = day_cache if day_cache is not None else {}
    dates_needed = sorted({local_start.date(), local_end.date()})
    intervals = []
    errors = []
    for d in dates_needed:
        key = d.strftime("%Y%m%d")
        if key not in day_cache:
            readings, err = fetch_day_readings(d, api_key, system_id)
            day_cache[key] = (day_intervals(readings), err)
        day_ivals, err = day_cache[key]
        intervals.extend(day_ivals)
        if err:
            errors.append(f"{key}: {err}")

    if not intervals:
        return {
            "generation_kwh": None, "consumption_kwh": None, "self_consumed_kwh": None,
            "export_kwh": None, "import_kwh": None, "solar_fraction": None, "coverage": 0.0,
            "error": "; ".join(errors) if errors else "no PVOutput readings for this window",
        }

    split = window_energy_split(intervals, local_start, local_end)
    solar_fraction = (
        split["self_consumed_kwh"] / split["consumption_kwh"] if split["consumption_kwh"] > 0 else None
    )
    out = dict(split)
    out["solar_fraction"] = solar_fraction
    out["error"] = "; ".join(errors) if errors else None
    return out


def pv_agl_import_conflict(pv_import_kwh, pv_coverage, agl_import_kwh, agl_buckets_missing):
    """True if PVOutput's own real import figure for a window and AGL's
    real smart-meter import figure for the SAME window disagree enough
    to be worth flagging - both are independent real meters for the
    same physical quantity (grid import to the whole house), so unlike
    evaluate_session()'s own "conflict" field (Evnex's CLAIMED solar%
    vs AGL's real import), this is purely a cross-source data-quality
    check, not a solar-usage question. Shared here (REQ-071) so
    tessie_charging_analyzer.py's live single-session path and
    analysis_ev_session_cost.py's batch path can't drift apart on the
    thresholds. Returns False (never flags) whenever either source
    doesn't have enough real data for the window to trust the
    comparison at all (pv_coverage below PV_AGL_CONFLICT_MIN_COVERAGE,
    or AGL has any missing half-hour bucket for the window)."""
    if pv_import_kwh is None or agl_import_kwh is None:
        return False
    if pv_coverage is None or pv_coverage < PV_AGL_CONFLICT_MIN_COVERAGE:
        return False
    if agl_buckets_missing:
        return False
    larger = max(pv_import_kwh, agl_import_kwh)
    if larger < PV_AGL_CONFLICT_MIN_KWH:
        return False
    return abs(pv_import_kwh - agl_import_kwh) / larger >= PV_AGL_CONFLICT_RATIO
