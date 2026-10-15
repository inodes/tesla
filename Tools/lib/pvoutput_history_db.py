#!/usr/bin/env python3
"""
pvoutput_history_db.py - REQ-118 (2026-09-24): a local SQLite cache of
PVOutput's own daily Energy Generated/Exported/Used figures
(lib/pvoutput_common.py's fetch_output_range(), getoutput.jsp), so a
period report (power_solar_report.py) can be re-run without re-fetching
days it already has. Mirrors lib/power_usage_db.py's own
usage_readings.db pattern deliberately - same TRUNCATE-journal
reasoning (this file lives under an iCloud-synced folder, and a
brand-new WAL/DELETE-mode SQLite database there hit a real real "disk
I/O error" the day power_usage_db.py was first written - see
AGENTS.local.md REQ-115 - traced to SQLite's own journal-file lifecycle
colliding with this session's remote-device bridge's file-deletion
restriction on connected folders).

One row per calendar day - getoutput.jsp is already an end-of-day
summary, there is no finer granularity to store here. An individual
charging SESSION's own 5-minute solar split still goes through
pv_window_split()'s live getstatus.jsp?h=1 path in pvoutput_common.py,
entirely unaffected by this cache - this is for period-level
REPORTING, not session-level cost splitting, and the two never need to
agree on data source.

Today's row (and any future-dated row, which should never legitimately
occur) is always treated as still-accumulating and re-fetched on every
sync rather than trusted from cache - the same "current period isn't a
gap, it's still filling in" rule power_coverage_report.py already
applies to the current calendar month.
"""

import os
import sqlite3
from datetime import date, datetime, timedelta

from lib.tessie_api_common import load_config, resolve_tessie_data_home


def resolve_pvoutput_data_home():
    """config.json's directories.pvoutput, or <Tesla data home>/PVOutput
    by default - the same directories.<key>-first, sensible-default-
    second pattern every provider's own resolve_data_home() already
    follows (providers_power/power_base.py)."""
    cfg_dir = load_config().get("directories", {}).get("pvoutput")
    if cfg_dir:
        return os.path.abspath(os.path.expanduser(cfg_dir))
    return os.path.join(resolve_tessie_data_home(), "PVOutput")


def resolve_pvoutput_db_path(data_home=None):
    data_home = data_home or resolve_pvoutput_data_home()
    return os.path.join(data_home, "pvoutput_daily.db")


def get_pvoutput_db_connection(data_home=None):
    db_path = resolve_pvoutput_db_path(data_home)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=TRUNCATE")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def _init_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pvoutput_daily (
            day TEXT PRIMARY KEY,
            generation_kwh REAL NOT NULL,
            exported_kwh REAL NOT NULL,
            used_kwh REAL NOT NULL,
            fetched_at TEXT NOT NULL
        )
    """)
    conn.commit()


def days_present(conn):
    return {r["day"] for r in conn.execute("SELECT day FROM pvoutput_daily")}


def upsert_days(conn, rows):
    """rows: [(date_obj, generation_kwh, exported_kwh, used_kwh), ...]."""
    now = datetime.now().isoformat()
    conn.executemany(
        "INSERT INTO pvoutput_daily (day, generation_kwh, exported_kwh, used_kwh, fetched_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(day) DO UPDATE SET generation_kwh=excluded.generation_kwh, "
        "exported_kwh=excluded.exported_kwh, used_kwh=excluded.used_kwh, fetched_at=excluded.fetched_at",
        [(d.isoformat(), gen, exp, used, now) for d, gen, exp, used in rows],
    )
    conn.commit()


def query_range(conn, start, end):
    """{date_obj: {"generation_kwh", "exported_kwh", "used_kwh"}, ...}
    for every cached day in [start, end] inclusive - a day with no
    cache entry (never synced, or synced but PVOutput itself had
    nothing logged) is simply absent from the result, not a zero."""
    rows = conn.execute(
        "SELECT day, generation_kwh, exported_kwh, used_kwh FROM pvoutput_daily "
        "WHERE day >= ? AND day <= ? ORDER BY day",
        (start.isoformat(), end.isoformat()),
    )
    out = {}
    for r in rows:
        d = datetime.strptime(r["day"], "%Y-%m-%d").date()
        out[d] = {
            "generation_kwh": r["generation_kwh"],
            "exported_kwh": r["exported_kwh"],
            "used_kwh": r["used_kwh"],
        }
    return out


def sync_range(conn, start, end, api_key, system_id, log=print):
    """Fetches and caches every day in [start, end] not already cached,
    plus today (and any future date, though that should never occur),
    which is always re-fetched even if already cached since a
    still-accumulating day's total isn't final. Consecutive missing
    days are collapsed into as few getoutput.jsp calls as possible
    (fetch_output_range() chunks internally past its own per-call day
    cap). Returns the number of days actually fetched from the API (0
    means everything needed was already cached and stable)."""
    from lib.pvoutput_common import fetch_output_range

    present = days_present(conn)
    today = date.today()

    missing_ranges = []
    run_start = None
    d = start
    while d <= end + timedelta(days=1):
        needs_fetch = d <= end and (d.isoformat() not in present or d >= today)
        if needs_fetch and run_start is None:
            run_start = d
        elif not needs_fetch and run_start is not None:
            missing_ranges.append((run_start, d - timedelta(days=1)))
            run_start = None
        d += timedelta(days=1)

    if not missing_ranges:
        log(f"PVOutput cache: all {(end - start).days + 1} day(s) in [{start} to {end}] already cached and stable")
        return 0

    total_fetched = 0
    for r_start, r_end in missing_ranges:
        log(f"  Fetching PVOutput daily data for {r_start} to {r_end}...")
        rows, err = fetch_output_range(r_start, r_end, api_key, system_id)
        if err:
            log(f"  ⚠ {err}")
        if rows:
            upsert_days(conn, rows)
            total_fetched += len(rows)
    return total_fetched
