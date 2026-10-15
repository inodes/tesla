#!/usr/bin/env python3
"""
sync_power_usage.py - Consolidate the active power provider's
half-hourly usage-export CSVs (e.g. AGL's, downloaded manually from its
"My Usage" portal - see below) into a queryable SQLite database, under
that provider's own data home (get_power_provider().resolve_data_home()).

REQ-070 (2026-09-15): generalized from agl_usage_sync.py - the
retailer-specific bits (which files count as a usage export, how to
parse its timestamp format, its register-code -> role mapping, how to
read one export CSV) now live in Tools/providers_power/power_agl.py's
AGLProvider, the first and so far only BasePowerProvider implementation
(get_power_provider(), default "agl" - see providers_power/__init__.py).
This script's own job - find export files, merge them, keep one
canonical dataset - is the same for any provider.

REQ-115 (2026-09-24): the canonical store used to be one single,
ever-growing agl_usage_master.csv, hand-loaded by every reader via
csv.DictReader. User: "can they be written in this format rather than
one massive csv? AGL_YYYYMM.csv. Then store any of the entries in db
file similar to fuel_history.db" - now mirrors lib/fuel_common.py's own
fuel_history.db exactly: a SQLite `usage_readings` table (same
StartDate/EndDate/RegisterCode dedup key as always, now the real
PRIMARY KEY) is the actual source of truth every reader queries
(lib/power_usage_db.py), and a plain-text `AGL_<YYYYMM>.csv` per
calendar month (in the provider's invoices folder, beside its bill
PDFs - REQ-117) is written FROM the database as a
human-readable/portable mirror - nothing in this repo re-parses those
monthly files, they're for you to open directly if you ever want to.
Any pre-existing single agl_usage_master.csv is migrated into the
database once, automatically, the first time this runs after the
upgrade (see migrate_legacy_master() below) - never deleted, moved into
usage/archive/ instead so nothing is lost.

AGL doesn't offer a usage-history API for residential accounts (unlike
Tessie/Evnex) - this stays a manual download, dropped into the AGL
invoices folder (get_power_provider().resolve_invoices_dir(),
currently ~/iCloud/PDF/AGL) as-is, then this script picks up every
file the active provider recognises as its own usage export (AGL:
"*Usage*.csv"/"AGL_Usage_*.csv").

To get a fresh AGL export: visit
  https://myaccount.agl.com.au/usage?c=<account>&billPeriod=<from>/<to>
(the account's own usage page) with the date range adjusted to cover
what you need - a year at a time is the practical granularity - and
save the CSV into the AGL invoices folder above, or just leave it in
Downloads (REQ-112 - scanned there too, and REQ-114 - filed away into
the invoices folder automatically once ingested).

Register semantics (confirmed against real bills - see AGENTS.md
REQ-031, for AGL specifically): RegisterCode "...#E1" is grid IMPORT
(billed as Usage charges; its RateTypeDescription has drifted across
exports - "Generalusage", "SeasonTimeofUse", plain "Usage" - all mean
the same E1 import register, never trust the description text, only
the register CODE). "...#B1" is Solar EXPORT (feeds the solar credit).
"...#E2" is Controlled Load (a separate circuit/tariff this repo
doesn't model a charge for yet - kept in the database for completeness).
See AGLProvider.register_role() for the actual mapping; a future
provider with a different register scheme implements its own.

Row identity / dedup key: (StartDate, EndDate, RegisterCode) - a
half-hour interval reading is uniquely identified by its window and
register; two overlapping exports covering the same period produce
identical keys and collapse to one row, never a duplicate - now
enforced by the database's own PRIMARY KEY rather than an in-memory
dict.

Same safety net already established for drives/charges/the old
master: back up the database file before writing, sanity-check the row
count after writing, and report what changed.

REQ-071 (2026-09-15): every run now ends with a non-blocking staleness
reminder (check_staleness() below) for a manual-sync provider
(provider.manual_usage_sync - AGL, currently the only one) - the user's
own framing of the three-source picture this repo integrates ("AGL is
manual at the moment, but the other two [Evnex, PVOutput] are not...
AGL is a nice to have to correlate... it would be nice to have a
reminder to get csv's if there are gaps") - reported even when nothing
new was found to consolidate, since staleness isn't about whether a
FILE showed up, it's about how old the newest DATA already in the
database is. Threshold: config.json's "power_usage_staleness_days"
(default DEFAULT_STALENESS_DAYS below).

Usage:
    ./sync_power_usage.py              # consolidate, write the database + monthly CSVs, report staleness
    ./sync_power_usage.py --dry-run    # show what would happen, write nothing (still reports staleness)
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from providers_power import get_power_provider  # noqa: E402
from lib.archive_naming import next_archive_path  # noqa: E402
from lib.tessie_api_common import load_config  # noqa: E402
from lib.power_usage_db import (  # noqa: E402
    get_usage_db_connection, resolve_usage_db_path, existing_keys, upsert_rows,
    record_sync, year_months_present, write_monthly_csv, migrate_legacy_master_csv,
    iter_rows,
)


def _resolve_landing_dir():
    """config.json's directories.landing (default ~/Downloads) - REQ-112:
    AGL's "My Usage" export is a manual download that lands here first,
    same as a freshly-downloaded AGL statement PDF - scanned in ADDITION
    to the provider's own invoices_dir so a usage-export CSV still sitting
    in Downloads (not yet filed away by hand) is picked up automatically
    rather than silently missed until someone remembers to move it."""
    cfg = load_config()
    val = cfg.get("directories", {}).get("landing") or "~/Downloads"
    return os.path.abspath(os.path.expanduser(val))


def find_usage_csvs(invoices_dir, provider=None, extra_dirs=None):
    """Scans `invoices_dir` (the provider's own configured invoices
    folder) plus every directory in `extra_dirs` (default: just the
    landing/Downloads folder - REQ-112) for files this provider
    recognises as its own usage-export format. A file present in more
    than one scanned directory (same real path, or same basename found
    in two different folders) is only returned once - dedup is by
    basename across folders so a CSV already filed away in invoices_dir
    doesn't get double-counted if a stale copy is still sitting in
    Downloads too."""
    provider = provider or get_power_provider()
    dirs = [invoices_dir] if invoices_dir else []
    dirs.extend(extra_dirs if extra_dirs is not None else [_resolve_landing_dir()])

    # 2026-09-30 review (D3): de-duplicated by file NAME, so a new export
    # that reused an already-filed name was silently skipped. Now only an
    # identical file (same content) counts as a duplicate.
    import hashlib
    out = []
    seen = {}
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not provider.is_usage_export_filename(f):
                continue
            path = os.path.join(d, f)
            try:
                with open(path, "rb") as fh:
                    digest = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                continue
            if digest in seen:
                continue
            seen[digest] = path
            out.append(path)
    return out


def _archive_landing_csv(csv_path, invoices_dir, log=print):
    """REQ-114: moves a successfully-ingested usage-export CSV out of the
    landing/Downloads folder into the provider's own invoices_dir, once
    its rows are safely folded into the database - the same canonical
    home an AGL statement PDF already gets filed into (sync_invoices.py's
    --import), so a raw export doesn't pile up in Downloads forever and a
    future run never needs to rediscover it there. Never overwrites: a
    same-named file already present in invoices_dir means this is left in
    place (with a warning) rather than risking silently clobbering
    something unrelated that happens to share a name."""
    if not invoices_dir:
        log(f"  (leaving {os.path.basename(csv_path)} in Downloads - no invoices_dir configured to file it into)")
        return False
    dest = os.path.join(invoices_dir, os.path.basename(csv_path))
    if os.path.abspath(dest) == os.path.abspath(csv_path):
        return False  # already filed, nothing to do
    os.makedirs(invoices_dir, exist_ok=True)
    if os.path.exists(dest):
        import filecmp
        if filecmp.cmp(dest, csv_path, shallow=False):
            log(f"  (an identical {os.path.basename(csv_path)} is already filed in {invoices_dir} - leaving the Downloads copy)")
            return False
        base, ext = os.path.splitext(dest)
        n = 1
        while os.path.exists(f"{base}_{n:02d}{ext}"):
            n += 1
        dest = f"{base}_{n:02d}{ext}"  # different content, same name: file it alongside, never over
    shutil.move(csv_path, dest)
    log(f"  Archived {os.path.basename(csv_path)} -> {dest}")
    return True


def migrate_legacy_master(conn, provider, data_home, log=print):
    """REQ-115 one-time upgrade path: if an old single agl_usage_master.csv
    still exists and the database doesn't have its rows yet, fold them in
    (exactly like any other source file) and move the now-superseded CSV
    into usage/archive/ - never deleted, just no longer the live file, so
    nothing already-consolidated is lost in the switch to the database."""
    legacy_path = os.path.join(data_home, "usage", provider.usage_master_filename())
    if not os.path.isfile(legacy_path):
        return 0
    n = migrate_legacy_master_csv(conn, provider, legacy_path, log=log)
    if n == 0:
        return 0
    archive_dir = os.path.join(data_home, "usage", "archive")
    os.makedirs(archive_dir, exist_ok=True)
    moved_to = next_archive_path(archive_dir, os.path.basename(legacy_path))
    shutil.move(legacy_path, moved_to)
    log(f"  Legacy master superseded - moved to {moved_to}")
    return n


def consolidate(dry_run=False, invoices_dir=None, data_home=None, log=print, provider=None):
    provider = provider or get_power_provider()
    invoices_dir = invoices_dir or provider.resolve_invoices_dir()
    data_home = data_home or provider.resolve_data_home()
    db_path = resolve_usage_db_path(provider, data_home)

    landing_dir = _resolve_landing_dir()
    csvs = find_usage_csvs(invoices_dir, provider, extra_dirs=[landing_dir])
    scanned = ", ".join(d for d in (invoices_dir, landing_dir) if d)
    log(f"Found {len(csvs)} usage export file(s) across: {scanned}")

    conn = get_usage_db_connection(provider, data_home)
    try:
        before_count = conn.execute("SELECT COUNT(*) FROM usage_readings").fetchone()[0]
        log(f"Existing database: {before_count} row(s) ({db_path})")

        # REQ-115: fold in any pre-existing single master CSV exactly once,
        # before touching anything else, so a fresh sync this run and an
        # old already-consolidated history end up in the same place.
        migrated_count = 0
        if not dry_run:
            migrated_count = migrate_legacy_master(conn, provider, data_home, log=log)
        elif os.path.isfile(os.path.join(data_home, "usage", provider.usage_master_filename())):
            log("  (a legacy agl_usage_master.csv exists and would be migrated into the database - dry-run, not touched)")

        seen_keys = existing_keys(conn)
        all_rows = {}
        total_skipped = 0
        usable_csvs = []
        touched_year_months = set()
        for csv_path in csvs:
            source_rows, skipped = provider.load_source_rows(csv_path)
            total_skipped += skipped
            new_here = sum(1 for k in source_rows if k not in seen_keys)
            seen_keys.update(source_rows.keys())
            all_rows.update(source_rows)
            log(f"  {os.path.basename(csv_path)}: {len(source_rows)} row(s) read, "
                f"{new_here} new, {skipped} unparseable row(s) skipped")
            if source_rows:
                usable_csvs.append((csv_path, len(source_rows), new_here))
                for v in source_rows.values():
                    try:
                        touched_year_months.add(provider.parse_datetime(v["StartDate"]).strftime("%Y%m"))
                    except Exception:
                        pass

        if migrated_count > 0:
            # A legacy-master migration is effectively a full historical
            # import - make sure every month it touched has an up-to-date
            # monthly CSV mirror, not just months touched by today's new
            # export files (there may be none, if this run's only job was
            # the one-time migration itself).
            touched_year_months |= set(year_months_present(conn))

        if total_skipped:
            log(f"WARNING: {total_skipped} row(s) across all sources could not be parsed and were dropped")

        landing_dir_abs = os.path.abspath(landing_dir)

        def _archive_usable_landing_csvs():
            """REQ-114: file away every landing-dir CSV that contributed at
            least one usable row, regardless of whether ITS rows turned out
            to be new - its data is already safely represented either way,
            so it shouldn't sit in Downloads forever just because nothing
            changed this run."""
            for csv_path, _, _ in usable_csvs:
                if os.path.dirname(os.path.abspath(csv_path)) == landing_dir_abs:
                    _archive_landing_csv(csv_path, invoices_dir, log)

        if not csvs and migrated_count == 0:
            log("Nothing new to consolidate - database unchanged")
            conn.commit()
            return 0

        total_new_from_csvs = sum(new_here for _, _, new_here in usable_csvs)
        data_changed = migrated_count > 0 or total_new_from_csvs > 0

        if not data_changed:
            # Every scanned file's rows were already fully known (0 new)
            # and no legacy migration ran - nothing to write, back up, or
            # regenerate. Still worth recording that we saw these files
            # (usage_sync_log's last_synced_at) and archiving any of them
            # sitting in the landing dir, since their data is already safe.
            log(f"No new rows across {len(usable_csvs)} scanned file(s) - database unchanged")
            if dry_run:
                conn.rollback()
                return 0
            for csv_path, row_count, new_here in usable_csvs:
                record_sync(conn, os.path.basename(csv_path), row_count, new_here)
            conn.commit()
            _archive_usable_landing_csvs()
            return 0

        if all_rows:
            upsert_rows(conn, all_rows, provider)
        after_count = conn.execute("SELECT COUNT(*) FROM usage_readings").fetchone()[0]
        new_count = after_count - before_count
        log(f"Total after merge: {after_count} row(s) ({new_count} new)")

        if dry_run:
            log(f"[dry-run] would add {new_count} new row(s), refresh {len(touched_year_months)} "
                f"monthly CSV file(s) - nothing written, nothing archived")
            conn.rollback()
            return new_count

        # Back up the database file itself before this run's write
        # commits, same "back up before writing" convention as the old
        # single-master approach (a plain file copy works fine for a
        # WAL-mode SQLite file that isn't concurrently being written).
        if os.path.isfile(db_path) and before_count > 0:
            archive_dir = os.path.join(data_home, "usage", "archive")
            os.makedirs(archive_dir, exist_ok=True)
            backup_path = next_archive_path(archive_dir, os.path.basename(db_path))
            shutil.copy2(db_path, backup_path)
            log(f"Backed up existing database to {backup_path}")

        for csv_path, row_count, new_here in usable_csvs:
            record_sync(conn, os.path.basename(csv_path), row_count, new_here)
        conn.commit()

        # Sanity check: re-read what we just committed.
        reread_count = conn.execute("SELECT COUNT(*) FROM usage_readings").fetchone()[0]
        if reread_count != after_count:
            log(f"⚠ Consolidate sanity check failed: expected {after_count} rows but re-read {reread_count}")
        else:
            log(f"Sanity check passed: {reread_count} row(s) in the database, all keys unique by construction")

        # REQ-115: (re)write only the monthly CSVs actually touched this
        # run - a full month's worth of rows each time (not just the new
        # ones), since the file is a derived export, not itself the
        # source of truth.
        for ym in sorted(touched_year_months):
            write_monthly_csv(conn, provider, ym, invoices_dir, log=log)

        log(f"Database updated ({new_count} new row(s))")

        _archive_usable_landing_csvs()

        return new_count
    finally:
        conn.close()


DEFAULT_STALENESS_DAYS = 14


def check_staleness(provider=None, threshold_days=None, log=print):
    """Non-blocking reminder (REQ-071) for a provider whose usage-export
    data is a manual pickup (provider.manual_usage_sync - currently just
    AGL, which has no history API for residential accounts): reports how
    many days old the database's newest row is, and if that exceeds
    `threshold_days` (config.json's "power_usage_staleness_days", default
    DEFAULT_STALENESS_DAYS), prints a clear reminder to grab a fresh CSV
    export - never raises, never changes this script's exit code, since
    per the user's own framing this is "a nice to have to correlate", not
    something that should block the rest of a sync run.

    Does nothing (silently) for a provider that doesn't need this - one
    whose usage data syncs automatically has no manual step to be
    reminded about.

    Returns the age in days (int) if a check was made, or None if it
    wasn't (not a manual-sync provider, or no usable date found in the
    database at all - an empty/missing database isn't this function's
    business to complain about, that's already covered by consolidate()'s
    own "Found 0 usage export file(s)" reporting)."""
    provider = provider or get_power_provider()
    if not provider.manual_usage_sync:
        return None

    data_home = provider.resolve_data_home()
    db_path = resolve_usage_db_path(provider, data_home)
    if not os.path.isfile(db_path):
        return None

    conn = get_usage_db_connection(provider, data_home)
    try:
        row = conn.execute("SELECT MAX(start_date_iso) FROM usage_readings").fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    newest = datetime.fromisoformat(row[0])

    if threshold_days is None:
        cfg = load_config()
        threshold_days = cfg.get("power", {}).get("usage_staleness_days", DEFAULT_STALENESS_DAYS)

    age_days = (datetime.now() - newest).days
    if age_days > threshold_days:
        log(f"⏰ {provider.display_name} usage data is {age_days} day(s) old (newest row: {newest.date()}) - "
            f"{provider.display_name} has no history API for this, so it's worth grabbing a fresh usage-export "
            f"CSV and dropping it in {provider.resolve_invoices_dir()} when convenient (not blocking - "
            f"just a reminder; threshold is {threshold_days}d, set \"power_usage_staleness_days\" in "
            f"config.json to change it).")
    else:
        log(f"{provider.display_name} usage data is {age_days} day(s) old (newest row: {newest.date()}) - within the {threshold_days}d freshness threshold.")
    return age_days


def main():
    parser = argparse.ArgumentParser(
        usage="sync_power_usage.py [--dry-run]\n",
        description="""
NAME
    sync_power_usage.py - consolidate the active power provider's usage-export CSVs into a queryable database

DESCRIPTION
    Consolidates the active power provider's half-hourly usage-export CSVs
    (for AGL: manually downloaded from AGL's "My Usage" portal) sitting in
    that provider's invoices folder (or Downloads) into a SQLite database
    under its data home, plus one AGL_<YYYYMM>.csv per calendar month as a
    human-readable mirror. AGL doesn't offer a usage-history API for
    residential accounts, so this stays a manual-export pickup rather than
    a live sync for AGL - see the module docstring for how to get a fresh
    export. The active provider is config.json's "power_provider" key
    (default "agl").

    Every run ends with a non-blocking staleness reminder for a manual-
    sync provider - see "power_usage_staleness_days" in config.json.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES
  sync_power_usage.py
  sync_power_usage.py --dry-run
"""
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be consolidated, write nothing")
    args = parser.parse_args()
    consolidate(dry_run=args.dry_run)
    check_staleness()


if __name__ == "__main__":
    main()
