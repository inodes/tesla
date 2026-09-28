#!/usr/bin/env python3
"""
solaredge_history_db.py - REQ-125: local SQLite cache of SolarEdge's
own daily energyDetails meters (lib/solaredge_common.py's
fetch_energy_details_range()), mirroring lib/pvoutput_history_db.py's
own pattern deliberately - same TRUNCATE-journal reasoning (see that
file's docstring and AGENTS.local.md REQ-115/REQ-118).

One row per calendar day, five real SolarEdge meters: Production,
Consumption, FeedIn (export to grid), Purchased (import from grid),
SelfConsumption (production consumed on-site, without ever crossing
the meter) - the last one is a real SolarEdge-computed figure, not a
derived one, unlike PVOutput's generation/exported/used triple where
self-consumption has to be computed as generation - exported.
"""

import os
import sqlite3
from datetime import date, datetime, timedelta

from lib.tessie_api_common import load_config, resolve_tessie_data_home


def resolve_solaredge_data_home():
    cfg_dir = load_config().get("directories", {}).get("solaredge")
    if cfg_dir:
        return os.path.abspath(os.path.expanduser(cfg_dir))
    return os.path.join(resolve_tessie_data_home(), "SolarEdge")


def resolve_solaredge_db_path(data_home=None):
    data_home = data_home or resolve_solaredge_data_home()
    return os.path.join(data_home, "solaredge_daily.db")


def get_solaredge_db_connection(data_home=None):
    db_path = resolve_solaredge_db_path(data_home)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=TRUNCATE")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def _init_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS solaredge_daily (
            day TEXT PRIMARY KEY,
            production_kwh REAL,
            consumption_kwh REAL,
            feedin_kwh REAL,
            purchased_kwh REAL,
            self_consumption_kwh REAL,
            fetched_at TEXT NOT NULL
        )
    """)
    conn.commit()


def days_present(conn):
    return {r["day"] for r in conn.execute("SELECT day FROM solaredge_daily")}


def upsert_days(conn, rows):
    """rows: [{"day": date_obj, "production_kwh", "consumption_kwh",
    "feedin_kwh", "purchased_kwh", "self_consumption_kwh"}, ...]."""
    now = datetime.now().isoformat()
    conn.executemany(
        "INSERT INTO solaredge_daily (day, production_kwh, consumption_kwh, feedin_kwh, "
        "purchased_kwh, self_consumption_kwh, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(day) DO UPDATE SET production_kwh=excluded.production_kwh, "
        "consumption_kwh=excluded.consumption_kwh, feedin_kwh=excluded.feedin_kwh, "
        "purchased_kwh=excluded.purchased_kwh, self_consumption_kwh=excluded.self_consumption_kwh, "
        "fetched_at=excluded.fetched_at",
        [(r["day"].isoformat(), r["production_kwh"], r["consumption_kwh"], r["feedin_kwh"],
          r["purchased_kwh"], r["self_consumption_kwh"], now) for r in rows],
    )
    conn.commit()


def query_range(conn, start, end):
    """{date_obj: {...5 fields...}, ...} for every cached day in
    [start, end] inclusive - a day with no cache entry is simply
    absent, not a zero."""
    rows = conn.execute(
        "SELECT day, production_kwh, consumption_kwh, feedin_kwh, purchased_kwh, self_consumption_kwh "
        "FROM solaredge_daily WHERE day >= ? AND day <= ? ORDER BY day",
        (start.isoformat(), end.isoformat()),
    )
    out = {}
    for r in rows:
        d = datetime.strptime(r["day"], "%Y-%m-%d").date()
        out[d] = {
            "production_kwh": r["production_kwh"],
            "consumption_kwh": r["consumption_kwh"],
            "feedin_kwh": r["feedin_kwh"],
            "purchased_kwh": r["purchased_kwh"],
            "self_consumption_kwh": r["self_consumption_kwh"],
        }
    return out


def sync_range(conn, start, end, api_key, site_id, log=print):
    """Fetches and caches every day in [start, end] not already
    cached, plus today (still-accumulating, never trusted from cache).
    Returns the number of days actually fetched from the API."""
    from lib.solaredge_common import fetch_energy_details_range

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
        log(f"SolarEdge cache: all {(end - start).days + 1} day(s) in [{start} to {end}] already cached and stable")
        return 0

    total_fetched = 0
    for r_start, r_end in missing_ranges:
        log(f"  Fetching SolarEdge daily data for {r_start} to {r_end}...")
        rows, err = fetch_energy_details_range(r_start, r_end, api_key, site_id)
        if err:
            log(f"  ⚠ {err}")
        if rows:
            upsert_days(conn, rows)
            total_fetched += len(rows)
    return total_fetched
