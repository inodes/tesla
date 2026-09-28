"""
power_usage_db.py - REQ-115: SQLite-backed canonical store for a power
provider's consolidated half-hourly usage rows, replacing one
ever-growing single master CSV (agl_usage_master.csv) - mirrors
lib/fuel_common.py's fuel_history.db: one `usage_readings` table (the
same natural dedup key sync_power_usage.py has always used - StartDate/
EndDate/RegisterCode - as its PRIMARY KEY) plus a `usage_sync_log`
table tracking per-source-file ingestion history (mirrors fuel's own
`fuel_sync_log`).

Every row also carries a derived `start_date_iso`/`year_month` pair
computed at insert time via the provider's own parse_datetime() -
needed because the raw StartDate text (AGL: "25/07/2026 12:00:00 AM")
does NOT sort or range-filter correctly as a plain string (day-first
format), unlike an ISO string. The original raw StartDate/EndDate text
is still stored and returned unchanged from every read function here,
so every existing caller's own re-parsing of row["StartDate"] via
provider.parse_datetime() keeps working completely unchanged - only
where the rows come FROM changes (a SQL query instead of
csv.DictReader), never their shape.

Monthly plain-text CSVs (`AGL_<YYYYMM>.csv`, one per calendar month,
in the provider's invoices folder, beside its bill PDFs - REQ-117)
are written FROM this
database as a human-readable/portable mirror - a smaller, dated file
per month instead of one ever-growing master, mirroring the spirit of
fuel_common.py's own raw/ folder of monthly files. The database, not
the monthly CSVs, is what every reader in this repo actually queries.
"""

import csv
import os
import sqlite3
from datetime import datetime

from providers_power import MASTER_FIELDS


def resolve_usage_db_path(provider, data_home=None):
    data_home = data_home or provider.resolve_data_home()
    return os.path.join(data_home, "usage", f"{provider.name}_usage_history.db")


def resolve_monthly_dir(provider, invoices_dir=None):
    """REQ-117 (2026-09-24): monthly CSVs now live directly in the
    provider's own invoices folder, beside the bill PDF covering the
    same billing period, rather than under data_home/usage/monthly/ -
    the point of AGL_YYYYMM.csv sitting next to
    Statement_electricity_<period>.pdf is that a person cross-checking a
    bill can see the source usage data and the bill in one glance,
    without having to know a separate usage/monthly/ folder exists.
    User: "Write monthly CSVs into the invoices folder"."""
    return invoices_dir or provider.resolve_invoices_dir()


def monthly_csv_filename(provider, year_month):
    """'AGL_202609.csv' - <PROVIDER-NAME-UPPER>_<YYYYMM>.csv."""
    return f"{provider.name.upper()}_{year_month}.csv"


def get_usage_db_connection(provider, data_home=None):
    db_path = resolve_usage_db_path(provider, data_home)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    # REQ-115: deliberately TRUNCATE, not WAL (unlike fuel_common.py's
    # fuel_history.db) - this file lives under ~/iCloud/Tesla, an
    # iCloud-synced folder. WAL mode keeps a separate -wal/-shm side
    # file and needs to create/replace them; confirmed live (2026-09-24)
    # that a brand-new WAL (or default DELETE-journal) database under
    # this data_home fails with "disk I/O error" the moment it needs to
    # create those side files, while TRUNCATE mode - a single-file
    # rollback journal that's truncated in place rather than
    # created/deleted each transaction - works cleanly. TRUNCATE gives
    # the same atomicity/crash-safety guarantee as WAL for this script's
    # actual usage pattern (one exclusive batch writer, no concurrent
    # readers to benefit from WAL's read-concurrency) and avoids the
    # extra moving parts that iCloud's own sync daemon is more likely to
    # trip over on a multi-file WAL database anyway.
    conn.execute("PRAGMA journal_mode = TRUNCATE")
    conn.execute("PRAGMA synchronous = NORMAL")
    _init_usage_schema(conn)
    return conn


def _init_usage_schema(conn):
    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS usage_readings (
            start_date TEXT,
            end_date TEXT,
            register_code TEXT,
            role TEXT,
            kwh REAL,
            source_file TEXT,
            start_date_iso TEXT,
            year_month TEXT,
            PRIMARY KEY (start_date, end_date, register_code)
        );

        CREATE INDEX IF NOT EXISTS idx_usage_start_iso ON usage_readings(start_date_iso);
        CREATE INDEX IF NOT EXISTS idx_usage_role ON usage_readings(role);
        CREATE INDEX IF NOT EXISTS idx_usage_year_month ON usage_readings(year_month);

        CREATE TABLE IF NOT EXISTS usage_sync_log (
            source_file TEXT PRIMARY KEY,
            row_count INTEGER DEFAULT 0,
            new_row_count INTEGER DEFAULT 0,
            last_synced_at TEXT
        );
        """)


def existing_keys(conn):
    """set of (StartDate, EndDate, RegisterCode) tuples already in the
    database - used to compute an accurate per-source-file "new rows"
    count the same way the old CSV-based master did (cumulative across
    every file processed in one run), without loading full row data."""
    return {
        (r[0], r[1], r[2])
        for r in conn.execute("SELECT start_date, end_date, register_code FROM usage_readings")
    }


def upsert_rows(conn, rows_by_key, provider):
    """rows_by_key: {(StartDate,EndDate,RegisterCode): {StartDate,EndDate,
    RegisterCode,Role,kWh,SourceFile}} - the exact shape
    provider.load_source_rows() already returns. Call inside a caller-
    managed transaction (conn.execute("BEGIN") ... conn.commit()/rollback())
    so a dry-run preview can inspect the post-upsert row count and then
    roll back instead of committing."""
    batch = []
    for k, v in rows_by_key.items():
        start_iso = None
        year_month = None
        try:
            dt = provider.parse_datetime(v["StartDate"])
            start_iso = dt.isoformat()
            year_month = dt.strftime("%Y%m")
        except Exception:
            pass
        batch.append((k[0], k[1], k[2], v["Role"], float(v["kWh"]), v["SourceFile"], start_iso, year_month))

    conn.executemany("""
    INSERT INTO usage_readings (start_date, end_date, register_code, role, kwh, source_file, start_date_iso, year_month)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(start_date, end_date, register_code) DO UPDATE SET
        role = excluded.role,
        kwh = excluded.kwh,
        source_file = excluded.source_file,
        start_date_iso = excluded.start_date_iso,
        year_month = excluded.year_month
    """, batch)


def record_sync(conn, source_file, row_count, new_row_count):
    conn.execute("""
    INSERT INTO usage_sync_log (source_file, row_count, new_row_count, last_synced_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(source_file) DO UPDATE SET
        row_count = excluded.row_count,
        new_row_count = excluded.new_row_count,
        last_synced_at = excluded.last_synced_at
    """, (source_file, row_count, new_row_count, datetime.now().isoformat()))


def iter_rows(conn, role=None, start_date=None, end_date=None):
    """Yields dicts shaped exactly like the old master CSV's rows
    (StartDate/EndDate/RegisterCode/Role/kWh/SourceFile, original text
    preserved) - a drop-in replacement for csv.DictReader(open(...)) at
    every existing call site. `start_date`/`end_date` are date() objects
    (inclusive), filtered against the derived start_date_iso column so
    the comparison is chronologically correct regardless of the
    provider's own raw on-disk date text format."""
    q = "SELECT start_date, end_date, register_code, role, kwh, source_file FROM usage_readings WHERE 1=1"
    params = []
    if role:
        q += " AND role = ?"
        params.append(role)
    if start_date:
        q += " AND start_date_iso >= ?"
        params.append(start_date.isoformat())
    if end_date:
        q += " AND start_date_iso < ?"
        params.append((datetime(end_date.year, end_date.month, end_date.day) .replace(hour=23, minute=59, second=59)).isoformat())
    q += " ORDER BY start_date_iso"
    for r in conn.execute(q, params):
        yield {
            "StartDate": r[0], "EndDate": r[1], "RegisterCode": r[2],
            "Role": r[3], "kWh": str(r[4]), "SourceFile": r[5],
        }


def year_months_present(conn, only=None):
    """Sorted list of distinct 'YYYYMM' buckets in the database, or just
    the ones in `only` (an iterable) if given - for regenerating monthly
    CSVs after a sync without re-writing every month that already exists
    unchanged."""
    if only is not None:
        return sorted({ym for ym in only if ym})
    return sorted(r[0] for r in conn.execute(
        "SELECT DISTINCT year_month FROM usage_readings WHERE year_month IS NOT NULL"
    ))


def write_monthly_csv(conn, provider, year_month, invoices_dir=None, log=print):
    """(Re)writes one month's worth of rows to its own AGL_<YYYYMM>.csv
    in the provider's invoices folder (REQ-117) - a full overwrite each
    time (tmp file + atomic replace, same pattern as the old
    single-master write), since this file is a derived/regenerable
    export, not itself the source of truth - the database is."""
    monthly_dir = resolve_monthly_dir(provider, invoices_dir)
    os.makedirs(monthly_dir, exist_ok=True)
    dest = os.path.join(monthly_dir, monthly_csv_filename(provider, year_month))
    rows = list(conn.execute(
        "SELECT start_date, end_date, register_code, role, kwh, source_file "
        "FROM usage_readings WHERE year_month = ? ORDER BY start_date_iso",
        (year_month,),
    ))
    tmp_path = dest + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(MASTER_FIELDS)
        for r in rows:
            w.writerow(r)
    os.replace(tmp_path, dest)
    log(f"  Wrote {os.path.basename(dest)} ({len(rows)} row(s))")
    return dest, len(rows)


def migrate_legacy_master_csv(conn, provider, master_csv_path, log=print):
    """One-time import (REQ-115): folds an existing single
    agl_usage_master.csv's rows into the database exactly like any other
    source file, then returns the row count imported so the caller can
    decide what to do with the now-superseded file (this function never
    deletes or moves it itself - see sync_power_usage.py's own migration
    step for that)."""
    if not os.path.isfile(master_csv_path):
        return 0
    rows_by_key = {}
    with open(master_csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = (row["StartDate"], row["EndDate"], row["RegisterCode"])
            rows_by_key[key] = {
                "StartDate": row["StartDate"], "EndDate": row["EndDate"],
                "RegisterCode": row["RegisterCode"], "Role": row["Role"],
                "kWh": row["kWh"], "SourceFile": row.get("SourceFile", ""),
            }
    if not rows_by_key:
        return 0
    upsert_rows(conn, rows_by_key, provider)
    log(f"  Migrated {len(rows_by_key)} row(s) from legacy {os.path.basename(master_csv_path)} into the database")
    return len(rows_by_key)
