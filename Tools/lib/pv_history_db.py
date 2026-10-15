#!/usr/bin/env python3
"""
pv_history_db.py - REQ-127: local SQLite cache of a PV provider's
daily AND 15-minute intraday figures (providers_pv/'s get_pv_provider()),
generalized from the SolarEdge-specific lib/solaredge_history_db.py
(REQ-125) at the user's own request: "create a pv database to store
all finer resolution data - everything you can get. If you have all
data - then there is no issue in future - and not extra fetches" /
"pv for me is solaredge - but flexible to move to different system in
future". Mirrors lib/pvoutput_history_db.py's own TRUNCATE-journal
pattern (see that file's docstring, and AGENTS.local.md REQ-115/118).

Two tables, both provider-agnostic (column names are the five generic
PV_FIELDS from providers_pv/pv_base.py, never a vendor-specific meter
name):
  - pv_daily: one row per calendar day - what power_solar_report.py /
    power_roi_report.py actually need for period totals.
  - pv_intraday: one row per 15-minute interval - finer than any
    current report uses, kept purely so a future need (e.g. a proper
    SolarEdge-style mirrored area chart, REQ-119's chart-style
    follow-up) never needs a fresh API round-trip for data already
    fetched once.

Today's row (and any future-dated row) is always re-fetched on every
sync rather than trusted from cache, same "still accumulating" rule
lib/pvoutput_history_db.py already applies.
"""

import os
import sqlite3
from datetime import date, datetime, timedelta

from lib.tessie_api_common import load_config, resolve_tessie_data_home

PV_FIELDS = ("production_kwh", "consumption_kwh", "feedin_kwh", "purchased_kwh", "self_consumption_kwh")


def resolve_pv_data_home():
    cfg_dir = load_config().get("directories", {}).get("pv")
    if cfg_dir:
        return os.path.abspath(os.path.expanduser(cfg_dir))
    return os.path.join(resolve_tessie_data_home(), "PV")


def resolve_pv_db_path(data_home=None):
    data_home = data_home or resolve_pv_data_home()
    return os.path.join(data_home, "pv_history.db")


def get_pv_db_connection(data_home=None):
    db_path = resolve_pv_db_path(data_home)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=TRUNCATE")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def _init_schema(conn):
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS pv_daily (
            day TEXT PRIMARY KEY,
            {", ".join(f"{f} REAL" for f in PV_FIELDS)},
            provider TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
    """)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS pv_intraday (
            timestamp TEXT PRIMARY KEY,
            {", ".join(f"{f} REAL" for f in PV_FIELDS)},
            provider TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
    """)
    conn.commit()


def daily_days_present(conn):
    return {r["day"] for r in conn.execute("SELECT day FROM pv_daily")}


def intraday_days_present(conn):
    """Distinct calendar days that have AT LEAST ONE intraday row
    cached - used by sync_intraday_range() to decide what's missing at
    day granularity (a day is either fully synced or not attempted;
    this repo doesn't chase individual missing 15-minute slots within
    an otherwise-synced day, since a real gap there is a genuine
    provider-side absence, same as any other field)."""
    return {r[0][:10] for r in conn.execute("SELECT timestamp FROM pv_intraday")}


def upsert_daily(conn, rows, provider):
    """rows: [{"day": date_obj, **PV_FIELDS}, ...]."""
    now = datetime.now().isoformat()
    conn.executemany(
        f"INSERT INTO pv_daily (day, {', '.join(PV_FIELDS)}, provider, fetched_at) "
        f"VALUES (?, {', '.join(['?'] * len(PV_FIELDS))}, ?, ?) "
        f"ON CONFLICT(day) DO UPDATE SET "
        f"{', '.join(f'{f}=excluded.{f}' for f in PV_FIELDS)}, provider=excluded.provider, fetched_at=excluded.fetched_at",
        [(r["day"].isoformat(), *[r.get(f) for f in PV_FIELDS], provider, now) for r in rows],
    )
    conn.commit()


def upsert_intraday(conn, rows, provider):
    """rows: [{"timestamp": datetime_obj, **PV_FIELDS}, ...]."""
    now = datetime.now().isoformat()
    conn.executemany(
        f"INSERT INTO pv_intraday (timestamp, {', '.join(PV_FIELDS)}, provider, fetched_at) "
        f"VALUES (?, {', '.join(['?'] * len(PV_FIELDS))}, ?, ?) "
        f"ON CONFLICT(timestamp) DO UPDATE SET "
        f"{', '.join(f'{f}=excluded.{f}' for f in PV_FIELDS)}, provider=excluded.provider, fetched_at=excluded.fetched_at",
        [(r["timestamp"].strftime("%Y-%m-%d %H:%M:%S"), *[r.get(f) for f in PV_FIELDS], provider, now) for r in rows],
    )
    conn.commit()


def query_daily_range(conn, start, end):
    rows = conn.execute(
        f"SELECT day, {', '.join(PV_FIELDS)} FROM pv_daily WHERE day >= ? AND day <= ? ORDER BY day",
        (start.isoformat(), end.isoformat()),
    )
    out = {}
    for r in rows:
        d = datetime.strptime(r["day"], "%Y-%m-%d").date()
        out[d] = {f: r[f] for f in PV_FIELDS}
    return out


# BUG-175: today's data is never "stable", so every call used to re-fetch
# it - one --inspect of a same-day home charge printed "Fetching PV
# intraday data" four times (one per cost/cross-check window). Within one
# run, a day fetched in the last few minutes is reused instead.
_RECENT_FETCH_SECONDS = 300
_recent_fetches = {}


def _fetched_recently(kind, r_start, r_end):
    import time as _t
    t = _recent_fetches.get((kind, r_start, r_end))
    return t is not None and (_t.time() - t) < _RECENT_FETCH_SECONDS


def _mark_fetched(kind, r_start, r_end):
    import time as _t
    _recent_fetches[(kind, r_start, r_end)] = _t.time()


def sync_daily_range(conn, start, end, provider, api_key, account_id, log=print):
    """Fetches and caches every day in [start, end] not already
    cached, plus today. Returns days actually fetched."""
    present = daily_days_present(conn)
    today = date.today()
    missing = _collapse_missing(start, end, present, today)
    if not missing:
        log(f"PV daily cache: all {(end - start).days + 1} day(s) in [{start} to {end}] already cached and stable")
        return 0
    total = 0
    for r_start, r_end in missing:
        if _fetched_recently("daily", r_start, r_end):
            continue
        log(f"  Fetching PV daily data for {r_start} to {r_end}...")
        rows, err = provider.fetch_daily_range(r_start, r_end, api_key, account_id)
        _mark_fetched("daily", r_start, r_end)
        if err:
            log(f"  ⚠ {err}")
        if rows:
            upsert_daily(conn, rows, provider.name)
            total += len(rows)
    return total


def sync_intraday_range(conn, start, end, provider, api_key, account_id, log=print):
    """Same shape as sync_daily_range() but at day-presence granularity
    for the intraday table (see intraday_days_present())."""
    present = intraday_days_present(conn)
    today = date.today()
    missing = _collapse_missing(start, end, present, today)
    if not missing:
        log(f"PV intraday cache: all {(end - start).days + 1} day(s) in [{start} to {end}] already cached and stable")
        return 0
    total = 0
    for r_start, r_end in missing:
        if _fetched_recently("intraday", r_start, r_end):
            continue
        log(f"  Fetching PV intraday data for {r_start} to {r_end}...")
        rows, err = provider.fetch_intraday_range(r_start, r_end, api_key, account_id)
        _mark_fetched("intraday", r_start, r_end)
        if err:
            log(f"  ⚠ {err}")
        if rows:
            upsert_intraday(conn, rows, provider.name)
            total += len(rows)
    return total


def pv_window_split(conn, local_start, local_end, provider, api_key, account_id, log=print):
    """Real per-interval generation/consumption/self-consumption/export/
    import for [local_start, local_end) - the provider-agnostic
    successor (REQ-140) to lib/pvoutput_common.py's own pv_window_split(),
    built on this module's already-cached pv_intraday table instead of a
    live PVOutput fetch. Unlike PVOutput's version - which only had two
    real CT clamps (generation, consumption) and had to APPROXIMATE
    self-consumption/export/import from them (min(gen_delta, cons_delta)
    per interval, see lib/pvoutput_common.py's window_energy_split()) -
    this trusts the provider's own real per-interval self_consumption_kwh/
    feedin_kwh/purchased_kwh fields directly. SolarEdge's energyDetails
    API reports all five PV_FIELDS as independently-metered figures per
    15-minute interval (confirmed live, providers_pv/pv_solaredge.py), so
    no delta/approximation math is needed here at all - just proration of
    each cached interval's own real values by its overlap with the
    requested window (same overlap-fraction shape as PVOutput's
    window_energy_split(), minus the min() approximation it needed).

    Ensures the needed calendar day(s) are already cached first (via
    sync_intraday_range() - a no-op if a batch caller, e.g. a
    --precompute run, already synced them), so a single live session
    inspect pays for the fetch on demand but a batch run never re-fetches
    the same day twice.

    Returns a dict shaped exactly like the old PVOutput version -
    generation_kwh/consumption_kwh/self_consumed_kwh/export_kwh/
    import_kwh/solar_fraction/coverage/error - so callers built against
    that shape (analyse_charging.py's get_energy_source_crosscheck())
    never had to change their own field names."""
    EMPTY = {
        "generation_kwh": None, "consumption_kwh": None, "self_consumed_kwh": None,
        "export_kwh": None, "import_kwh": None, "solar_fraction": None, "coverage": 0.0,
    }
    if not api_key or not account_id:
        return dict(EMPTY, error=f"no {provider.display_name} credentials configured")

    day_start, day_end = min(local_start.date(), local_end.date()), max(local_start.date(), local_end.date())
    try:
        sync_intraday_range(conn, day_start, day_end, provider, api_key, account_id, log=log)
    except Exception as e:
        return dict(EMPTY, error=f"{provider.display_name} sync failed: {e}")

    interval = timedelta(minutes=provider.intraday_interval_minutes or 15)
 # Widened by one interval below local_start so a row that STARTS
 # just before the window but overlaps into it is still fetched -
 # pv_intraday's timestamp is always an interval's START, never its
 # midpoint or end.
    rows = conn.execute(
        f"SELECT timestamp, {', '.join(PV_FIELDS)} FROM pv_intraday "
        f"WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp",
        (
            (local_start - interval).strftime("%Y-%m-%d %H:%M:%S"),
            local_end.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    ).fetchall()

    generation_kwh = 0.0
    consumption_kwh = 0.0
    self_consumed_kwh = 0.0
    export_kwh = 0.0
    import_kwh = 0.0
    covered_seconds = 0.0
    interval_secs = interval.total_seconds()
    for r in rows:
        i_start = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
        i_end = i_start + interval
        overlap_start = max(i_start, local_start)
        overlap_end = min(i_end, local_end)
        overlap_secs = (overlap_end - overlap_start).total_seconds()
        if overlap_secs <= 0:
            continue
        frac = (overlap_secs / interval_secs) if interval_secs > 0 else 0.0
        generation_kwh += (r["production_kwh"] or 0.0) * frac
        consumption_kwh += (r["consumption_kwh"] or 0.0) * frac
        self_consumed_kwh += (r["self_consumption_kwh"] or 0.0) * frac
        export_kwh += (r["feedin_kwh"] or 0.0) * frac
        import_kwh += (r["purchased_kwh"] or 0.0) * frac
        covered_seconds += overlap_secs

    window_secs = (local_end - local_start).total_seconds()
    coverage = (covered_seconds / window_secs) if window_secs > 0 else 0.0
    if not rows or coverage <= 0:
        return dict(EMPTY, error=f"no {provider.display_name} intraday data for this window")

    solar_fraction = (self_consumed_kwh / consumption_kwh) if consumption_kwh > 0 else None
    return {
        "generation_kwh": generation_kwh,
        "consumption_kwh": consumption_kwh,
        "self_consumed_kwh": self_consumed_kwh,
        "export_kwh": export_kwh,
        "import_kwh": import_kwh,
        "solar_fraction": solar_fraction,
        "coverage": coverage,
        "error": None,
    }


def pv_interval_series(conn, local_start, local_end, provider, api_key, account_id, log=print):
    """TODO-038: the per-INTERVAL sibling of pv_window_split() above -
    same real self_consumption_kwh/consumption_kwh fields, same
    sync-then-query shape, but returns each raw pv_intraday row
    overlapping [local_start, local_end) individually instead of summed
    into one whole-window figure. Used to derive a per-moment solar
    fraction across a session (e.g. for a stacked solar/grid power
    chart) rather than one single session-wide blend.

    Returns a list of dicts, each shaped
    {start, end, consumption_kwh, self_consumed_kwh, solar_fraction} -
    solar_fraction is self_consumed_kwh/consumption_kwh for that one
    interval (None if consumption_kwh isn't positive - no reliable
    ratio, never assumed 0), sorted by start. Empty list on any
    credentials/sync failure (mirrors pv_window_split's EMPTY-on-error
    shape, but as an empty list since there's no single dict to return
    an error inside)."""
    if not api_key or not account_id:
        return []

    day_start, day_end = min(local_start.date(), local_end.date()), max(local_start.date(), local_end.date())
    try:
        sync_intraday_range(conn, day_start, day_end, provider, api_key, account_id, log=log)
    except Exception:
        return []

    interval = timedelta(minutes=provider.intraday_interval_minutes or 15)
    rows = conn.execute(
        "SELECT timestamp, consumption_kwh, self_consumption_kwh FROM pv_intraday "
        "WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp",
        (
            (local_start - interval).strftime("%Y-%m-%d %H:%M:%S"),
            local_end.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    ).fetchall()

    out = []
    for r in rows:
        i_start = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
        i_end = i_start + interval
        cons = r["consumption_kwh"] or 0.0
        self_c = r["self_consumption_kwh"] or 0.0
        solar_fraction = (self_c / cons) if cons > 0 else None
        out.append({
            "start": i_start, "end": i_end,
            "consumption_kwh": cons, "self_consumed_kwh": self_c,
            "solar_fraction": solar_fraction,
        })
    return out


def _collapse_missing(start, end, present_days, today):
    missing_ranges = []
    run_start = None
    d = start
    while d <= end + timedelta(days=1):
        needs_fetch = d <= end and (d.isoformat() not in present_days or d >= today)
        if needs_fetch and run_start is None:
            run_start = d
        elif not needs_fetch and run_start is not None:
            missing_ranges.append((run_start, d - timedelta(days=1)))
            run_start = None
        d += timedelta(days=1)
    return missing_ranges
