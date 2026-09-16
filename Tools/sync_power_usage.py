#!/usr/bin/env python3
"""
sync_power_usage.py - Consolidate the active power provider's
half-hourly usage-export CSVs (e.g. AGL's, downloaded manually from its
"My Usage" portal - see below) into one master CSV under that
provider's own data home (get_power_provider().resolve_data_home()).

REQ-070 (2026-09-15): generalized from agl_usage_sync.py - the
retailer-specific bits (which files count as a usage export, how to
parse its timestamp format, its register-code -> role mapping, how to
read one export CSV) now live in Tools/providers_power/power_agl.py's
AGLProvider, the first and so far only BasePowerProvider implementation
(get_power_provider(), default "agl" - see providers_power/__init__.py).
This script's own job - find export files, merge them into one
deduplicated master CSV, back up before writing - is the same for any
provider.

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
save the CSV into the AGL invoices folder above.

Register semantics (confirmed against real bills - see AGENTS.md
REQ-031, for AGL specifically): RegisterCode "...#E1" is grid IMPORT
(billed as Usage charges; its RateTypeDescription has drifted across
exports - "Generalusage", "SeasonTimeofUse", plain "Usage" - all mean
the same E1 import register, never trust the description text, only
the register CODE). "...#B1" is Solar EXPORT (feeds the solar credit).
"...#E2" is Controlled Load (a separate circuit/tariff this repo
doesn't model a charge for yet - kept in the master for completeness).
See AGLProvider.register_role() for the actual mapping; a future
provider with a different register scheme implements its own.

Row identity / dedup key: (StartDate, EndDate, RegisterCode) - a
half-hour interval reading is uniquely identified by its window and
register; two overlapping exports covering the same period produce
identical keys and collapse to one row, never a duplicate.

Same 4-point safety net already established for drives/charges
(BUG-025): back up the master before writing, sanity-check the row
count and duplicate-key count after writing, and report what changed.

REQ-071 (2026-09-15): every run now ends with a non-blocking staleness
reminder (check_staleness() below) for a manual-sync provider
(provider.manual_usage_sync - AGL, currently the only one) - the user's
own framing of the three-source picture this repo integrates ("AGL is
manual at the moment, but the other two [Evnex, PVOutput] are not...
AGL is a nice to have to correlate... it would be nice to have a
reminder to get csv's if there are gaps") - reported even when nothing
new was found to consolidate, since staleness isn't about whether a
FILE showed up, it's about how old the newest DATA already in the
master is. Threshold: config.json's "power_usage_staleness_days"
(default DEFAULT_STALENESS_DAYS below).

Usage:
    ./sync_power_usage.py              # consolidate, write the master, report staleness
    ./sync_power_usage.py --dry-run    # show what would happen, write nothing (still reports staleness)
"""

import argparse
import csv
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from providers_power import get_power_provider, MASTER_FIELDS  # noqa: E402
from lib.archive_naming import next_archive_path  # noqa: E402
from lib.tessie_api_common import load_config  # noqa: E402


def find_usage_csvs(invoices_dir, provider=None):
    provider = provider or get_power_provider()
    if not os.path.isdir(invoices_dir):
        return []
    out = []
    for f in sorted(os.listdir(invoices_dir)):
        if provider.is_usage_export_filename(f):
            out.append(os.path.join(invoices_dir, f))
    return out


def load_master_rows(master_path):
    rows = {}
    if not os.path.isfile(master_path):
        return rows
    with open(master_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = (row["StartDate"], row["EndDate"], row["RegisterCode"])
            rows[key] = row
    return rows


def consolidate(dry_run=False, invoices_dir=None, data_home=None, log=print, provider=None):
    provider = provider or get_power_provider()
    invoices_dir = invoices_dir or provider.resolve_invoices_dir()
    data_home = data_home or provider.resolve_data_home()
    usage_dir = os.path.join(data_home, "usage")
    master_filename = provider.usage_master_filename()
    master_path = os.path.join(usage_dir, master_filename)
    archive_dir = os.path.join(usage_dir, "archive")

    csvs = find_usage_csvs(invoices_dir, provider)
    log(f"Found {len(csvs)} usage export file(s) in {invoices_dir}")
    if not csvs:
        return 0

    existing = load_master_rows(master_path)
    initial_count = len(existing)
    log(f"Existing master: {initial_count} row(s)")

    merged = dict(existing)
    total_skipped = 0
    for csv_path in csvs:
        source_rows, skipped = provider.load_source_rows(csv_path)
        total_skipped += skipped
        new_here = sum(1 for k in source_rows if k not in merged)
        merged.update(source_rows)
        log(f"  {os.path.basename(csv_path)}: {len(source_rows)} row(s) read, "
            f"{new_here} new, {skipped} unparseable row(s) skipped")

    new_count = len(merged) - initial_count
    log(f"Total after merge: {len(merged)} row(s) ({new_count} new)")
    if total_skipped:
        log(f"WARNING: {total_skipped} row(s) across all sources could not be parsed and were dropped")

    if dry_run:
        log("[dry-run] would write master, no changes made")
        return new_count

    if new_count == 0:
        log("Nothing new to write - master unchanged")
        return 0

    os.makedirs(usage_dir, exist_ok=True)
    if os.path.isfile(master_path):
        os.makedirs(archive_dir, exist_ok=True)
        backup_path = next_archive_path(archive_dir, master_filename)
        shutil.copy2(master_path, backup_path)
        log(f"Backed up existing master to {backup_path}")

    ordered_keys = sorted(
        merged.keys(),
        key=lambda k: (k[0] == "", provider.parse_datetime(k[0]) if k[0] else datetime.min, k[2]),
    )
    tmp_path = master_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MASTER_FIELDS)
        w.writeheader()
        for key in ordered_keys:
            w.writerow(merged[key])
    os.replace(tmp_path, master_path)

    # Sanity check: re-read what we just wrote.
    written = load_master_rows(master_path)
    if len(written) != len(merged):
        log(f"⚠ Consolidate sanity check failed: wrote {len(merged)} rows but re-read {len(written)}")
    else:
        log(f"Sanity check passed: {len(written)} row(s) on disk, all keys unique by construction")

    log(f"Wrote {master_path} ({new_count} new row(s))")
    return new_count


DEFAULT_STALENESS_DAYS = 14


def check_staleness(master_path=None, provider=None, threshold_days=None, log=print):
    """Non-blocking reminder (REQ-071) for a provider whose usage-export
    data is a manual pickup (provider.manual_usage_sync - currently just
    AGL, which has no history API for residential accounts): reports how
    many days old the master CSV's newest row is, and if that exceeds
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
    master at all - an empty/missing master isn't this function's
    business to complain about, that's already covered by consolidate()'s
    own "Found 0 usage export file(s)" reporting)."""
    provider = provider or get_power_provider()
    if not provider.manual_usage_sync:
        return None

    data_home = provider.resolve_data_home()
    master_path = master_path or os.path.join(data_home, "usage", provider.usage_master_filename())
    if not os.path.isfile(master_path):
        return None

    newest = None
    with open(master_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            end_str = row.get("EndDate") or row.get("StartDate")
            if not end_str:
                continue
            try:
                dt = provider.parse_datetime(end_str)
            except Exception:
                continue
            if newest is None or dt > newest:
                newest = dt
    if newest is None:
        return None

    if threshold_days is None:
        cfg = load_config()
        threshold_days = cfg.get("power_usage_staleness_days", DEFAULT_STALENESS_DAYS)

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
    sync_power_usage.py - consolidate the active power provider's usage-export CSVs into one master CSV

DESCRIPTION
    Consolidates the active power provider's half-hourly usage-export CSVs
    (for AGL: manually downloaded from AGL's "My Usage" portal) sitting in
    that provider's invoices folder into one master CSV under its data
    home. AGL doesn't offer a usage-history API for residential accounts,
    so this stays a manual-export pickup rather than a live sync for AGL -
    see the module docstring for how to get a fresh export. The active
    provider is config.json's "power_provider" key (default "agl").

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
