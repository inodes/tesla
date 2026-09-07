#!/usr/bin/env python3
"""
agl_usage_sync.py - Consolidate AGL half-hourly usage-export CSVs
(downloaded manually from AGL's "My Usage" portal - see below) into one
master CSV under the AGL data home (see agl_common.resolve_agl_data_home()).
Renamed from agl_usage_importer.py - "sync" is the one verb this repo
now uses for every source-consolidation script, whether the pull is an
API call (Tessie's, Evnex's) or a manually-dropped-in export (this one).

AGL doesn't offer a usage-history API for residential accounts (unlike
Tessie/Evnex) - this stays a manual download, dropped into the AGL
invoices folder (agl_common.resolve_agl_invoices_dir(), currently
~/iCloud/PDF/AGL) as-is, then this script picks up every
"*Usage*.csv"/"AGL_Usage_*.csv" file sitting there.

To get a fresh export: visit
  https://myaccount.agl.com.au/usage?c=<account>&billPeriod=<from>/<to>
(the account's own usage page) with the date range adjusted to cover
what you need - a year at a time is the practical granularity - and
save the CSV into the AGL invoices folder above.

Register semantics (confirmed against real bills - see AGENTS.md
REQ-031): RegisterCode "...#E1" is grid IMPORT (billed as Usage charges;
its RateTypeDescription has drifted across exports - "Generalusage",
"SeasonTimeofUse", plain "Usage" - all mean the same E1 import register,
never trust the description text, only the register CODE). "...#B1" is
Solar EXPORT (feeds the solar credit). "...#E2" is Controlled Load (a
separate circuit/tariff this repo doesn't model a charge for yet - kept
in the master for completeness).

Row identity / dedup key: (StartDate, EndDate, RegisterCode) - a
half-hour interval reading is uniquely identified by its window and
register; two overlapping exports covering the same period produce
identical keys and collapse to one row, never a duplicate.

Same 4-point safety net already established for drives/charges
(BUG-025): back up the master before writing, sanity-check the row
count and duplicate-key count after writing, and report what changed.

Usage:
    ./agl_usage_sync.py              # consolidate, write the master
    ./agl_usage_sync.py --dry-run    # show what would happen, write nothing
"""

import argparse
import csv
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agl_common import resolve_agl_data_home, resolve_agl_invoices_dir  # noqa: E402
from archive_naming import next_archive_path  # noqa: E402

REGISTER_ROLE = {
    "E1": "import",
    "B1": "solar_export",
    "E2": "controlled_load",
}

MASTER_FIELDS = ["StartDate", "EndDate", "RegisterCode", "Role", "kWh", "SourceFile"]


def register_role(register_code):
    """'...#E1' -> 'import', etc. Unknown suffixes pass through raw so
    a future register type shows up visibly instead of being dropped."""
    suffix = register_code.rsplit("#", 1)[-1]
    return REGISTER_ROLE.get(suffix, suffix)


def parse_agl_datetime(s):
    """AGL exports use DD/MM/YYYY H:MM:SS AM/PM (no leading zero on
    hour) consistently across every export format seen so far."""
    return datetime.strptime(s.strip(), "%d/%m/%Y %I:%M:%S %p")


def find_usage_csvs(invoices_dir):
    if not os.path.isdir(invoices_dir):
        return []
    out = []
    for f in sorted(os.listdir(invoices_dir)):
        f_lower = f.lower()
        if f_lower.endswith(".csv") and ("usage" in f_lower):
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


def load_source_rows(csv_path):
    rows = {}
    skipped = 0
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            try:
                start = row["StartDate"].strip()
                end = row["EndDate"].strip()
                reg = row["RegisterCode"].strip()
                kwh = float(row["ProfileReadValue"])
                # Validate the timestamp parses even though we store the
                # original string - a row we can't date-parse isn't safe
                # to trust for anything downstream.
                parse_agl_datetime(start)
            except Exception:
                skipped += 1
                continue
            key = (start, end, reg)
            rows[key] = {
                "StartDate": start,
                "EndDate": end,
                "RegisterCode": reg,
                "Role": register_role(reg),
                "kWh": f"{kwh:.4f}",
                "SourceFile": os.path.basename(csv_path),
            }
    return rows, skipped


def consolidate(dry_run=False, invoices_dir=None, data_home=None, log=print):
    invoices_dir = invoices_dir or resolve_agl_invoices_dir()
    data_home = data_home or resolve_agl_data_home()
    usage_dir = os.path.join(data_home, "usage")
    master_path = os.path.join(usage_dir, "agl_usage_master.csv")
    archive_dir = os.path.join(usage_dir, "archive")

    csvs = find_usage_csvs(invoices_dir)
    log(f"Found {len(csvs)} usage export file(s) in {invoices_dir}")
    if not csvs:
        return 0

    existing = load_master_rows(master_path)
    initial_count = len(existing)
    log(f"Existing master: {initial_count} row(s)")

    merged = dict(existing)
    total_skipped = 0
    for csv_path in csvs:
        source_rows, skipped = load_source_rows(csv_path)
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
        backup_path = next_archive_path(archive_dir, "agl_usage_master.csv")
        shutil.copy2(master_path, backup_path)
        log(f"Backed up existing master to {backup_path}")

    ordered_keys = sorted(merged.keys(), key=lambda k: (k[0] == "", parse_agl_datetime(k[0]) if k[0] else datetime.min, k[2]))
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    consolidate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
