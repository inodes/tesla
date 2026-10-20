#!/usr/bin/env python3
"""
Tessie Drive Log Analyzer & Master Consolidator
===============================================
- Multi-level drill-down: Place ➔ Days ➔ Trips ➔ Camera Footage
- Visual footage indicators:
    🔄 Recent (Continuous Driving Loop)
    💾 Saved (Honks & Dashcam Taps)
    🔴 Sentry (Sentry Alert Events)
    No local footage (Missing / not archived)
- Consolidates & deduplicates raw Tessie CSVs into drives_master.csv
- PII-safe Known Place nicknames
"""

import os
import sys

_tools_dir = os.path.dirname(os.path.abspath(__file__))
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

import argparse
from datetime import datetime
from collections import defaultdict
from datetime import timedelta
from lib.drives.analyzer import DriveAnalyzer, export_all_deepdives
from lib.drives.browser import display_days_menu, display_flagged_deletions_menu, display_months_menu, display_timeline, drill_down_day
from lib.footage_keeping import purge_home_recent_cli
from lib.drives.helpers import parse_relative_date


def main():
    parser = argparse.ArgumentParser(
        usage="""analyse_drives.py [--drives] [--today | --yesterday | --month YYYY-MM | --since DATE | --days N]
                                  [--place NAME]
   or:  analyse_drives.py --timeline [DATE]
   or:  analyse_drives.py --inspect-drive INDEX_OR_DATE
   or:  analyse_drives.py --consolidate [CSV]
   or:  analyse_drives.py --sync | --no-sync
   or:  analyse_drives.py [--purge-home-recent | --stage-home-recent] [--dry-run] [--yes]
""",
        description="""
NAME
    analyse_drives.py - Tessie Drive Log Analyzer & Master Consolidator

DESCRIPTION
    Analyze, filter, and deep-dive Tessie drive logs, generate 24-hour vehicle &
    camera activity timelines, consolidate drive CSVs into drives_master.csv, and
    manage Home-parked RecentClips dashcam footage.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES
  analyse_drives.py --drives --today
  analyse_drives.py --drives --place Work --days 30
  analyse_drives.py --timeline yesterday
  analyse_drives.py --inspect-drive "2026-08-30 21:01"
  analyse_drives.py --consolidate
  analyse_drives.py --stage-home-recent --dry-run
"""
    )

    g_filter = parser.add_argument_group("Drive Selection & Filtering")
    g_filter.add_argument("--drives", action="store_true", help="Analyze and inspect drives")
    g_filter.add_argument("--today", action="store_true", help="Inspect only today's drives")
    g_filter.add_argument("--yesterday", action="store_true", help="Inspect only yesterday's drives")
    g_filter.add_argument("--month", help="Filter drives by month (e.g. '2026-09', 'september', 'aug')")
    g_filter.add_argument("--since", help="Filter drives since date or day name (e.g. 'wednesday', '2026-09-02')")
    g_filter.add_argument("--days", type=int, help="Filter drives from past N days")
    g_filter.add_argument("--place", help="Filter drives by place nickname (e.g. 'School', 'Work', 'Gym')")

    g_timeline = parser.add_argument_group("Timeline & Inspection")
    g_timeline.add_argument("--timeline", nargs="?", const="today", default=None, help="Generate 24-hour event-driven vehicle & camera activity timeline for a date (e.g. today, yesterday, 2026-09-02, wednesday)")
    g_timeline.add_argument("--inspect-drive", help="Deep-dive inspect a specific drive by 1-based index (start-time order) or date/datetime (e.g. '2026-08-30', '2026-08-30 21:01')")
    g_timeline.add_argument("--vehicle", "--compare-vehicle", dest="vehicle", help="Specific ICE/Hybrid vehicle key for fuel comparison (e.g. ford_ranger_diesel, rav4_hybrid, petrol_standard, prado_diesel, golf_gti_p98)")

    g_sync = parser.add_argument_group("Consolidation & Sync")
    g_sync.add_argument("--consolidate", nargs="?", const=True, help="Consolidate drives CSV(s) into drives_master.csv (optionally specify path to a new CSV)")
    g_sync.add_argument("--tessie-dir", help="Custom path to directory containing Tessie CSV exports")
    g_sync.add_argument("--sync", action="store_true", help="Force sync latest drives from Tessie API before running")
    g_sync.add_argument("--no-sync", action="store_true", help="Disable automatic API sync check when data is stale")

    g_camera = parser.add_argument_group("Home Camera Clip Management")
    g_camera.add_argument("--flagged", "--staged", "--tombstones", action="store_true", help="View and manage staged deletion packages and active tombstones")
    g_camera.add_argument("--purge-home-recent", "--auto-purge-home-recent", action="store_true", help="Auto-purge all unflagged RecentClips packages recorded while parked at Home")
    g_camera.add_argument("--stage-home-recent", "--auto-flag-home-recent", action="store_true", help="Stage all unflagged Home RecentClips packages for deletion without purging")
    g_camera.add_argument("--dry-run", action="store_true", help="Preview footage to be staged or purged without modifying disks")
    g_camera.add_argument("--yes", "-y", action="store_true", help="Automatically confirm purge without interactive prompt")

    g_export = parser.add_argument_group("Deep-Dive Export")
    g_export.add_argument("--export-deepdives", metavar="DEST_DIR", help="Bundle every real drive + charging deep-dive artifact (CSV/JSON/HTML/PNG, whichever already exist) into DEST_DIR/drives and DEST_DIR/charges")

    args = parser.parse_args()

    analyzer = DriveAnalyzer(tessie_dir=args.tessie_dir)
    if args.no_sync:
        analyzer._sync_disabled = True
    if args.sync:
        analyzer.sync_drives_if_stale(force=True)
    if args.consolidate:
        path = args.consolidate if isinstance(args.consolidate, str) else None
        analyzer.consolidate_drives(new_csv_path=path, verbose=True)
        return

    if args.flagged:
        display_flagged_deletions_menu(analyzer)
        return

    if args.export_deepdives:
        dest = os.path.abspath(os.path.expanduser(args.export_deepdives))
        n_files, n_bytes, by_ext = export_all_deepdives(analyzer, dest, dry_run=args.dry_run)
        if n_files == 0:
            print("No deep-dive artifacts found to export.")
        else:
            mb = n_bytes / (1024 * 1024)
            sz_str = f"{mb/1024:.1f} GB" if mb >= 1024 else f"{mb:.1f} MB"
            ext_summary = ", ".join(f"{n} {ext.lstrip('.').upper()}" for ext, n in sorted(by_ext.items()))
            verb = "Would copy" if args.dry_run else "Copied"
            print(f"{verb} {n_files} deep-dive file(s) ({sz_str}) to {dest} ({ext_summary})")
            if not args.dry_run:
                print(f"  -> {os.path.join(dest, 'drives')}")
                print(f"  -> {os.path.join(dest, 'charges')}")
        return

    drives = analyzer.load_drives()

    # TODO-040: this used to hard-exit here whenever no drive records were
    # found, which blocked --timeline, --inspect-drive and the Home Recent
    # purge flags too - even though all three already degrade gracefully
    # with zero drives (get_timeline_data() falls back to a single all-day
    # "Unknown" parked event and still attaches footage; inspect_drive()
    # prints a friendly "Could not find drive matching" message; the purge
    # flow works off footage_db/places, not drive records). Only the
    # default month/day drive-grouped view below genuinely needs `drives`
    # populated, so the empty check now gates just that path.

    if args.purge_home_recent or args.stage_home_recent:
        target_dates = None
        if args.today:
            target_dates = [parse_relative_date("today").date()]
        elif args.yesterday:
            target_dates = [parse_relative_date("yesterday").date()]
        elif args.since:
            s_dt = parse_relative_date(args.since).date()
            target_dates = [s_dt + timedelta(days=i) for i in range((datetime.now().date() - s_dt).days + 1)]
        elif args.days:
            s_dt = (datetime.now() - timedelta(days=args.days)).date()
            target_dates = [s_dt + timedelta(days=i) for i in range((datetime.now().date() - s_dt).days + 1)]
        elif args.timeline is not None:
            t_dt = parse_relative_date(args.timeline)
            if t_dt:
                target_dates = [t_dt.date()]

        purge_home_recent_cli(
            analyzer,
            target_dates=target_dates,
            stage_only=args.stage_home_recent,
            dry_run=args.dry_run,
            yes=args.yes
        )
        return

    if args.inspect_drive:
        analyzer.inspect_drive(args.inspect_drive, vehicle_key=args.vehicle)
        return

    if args.timeline is not None:
        target_date = parse_relative_date(args.timeline)
        if not target_date:
            print(f"Error: Could not parse date '{args.timeline}'. Supported formats: YYYYMMDD, YYYY-MM-DD, today, yesterday, weekday name.")
            sys.exit(1)
        display_timeline(target_date, analyzer)
        return

    if not drives:
        print("No drive records found - the default month/day view groups drives, so there's nothing "
              "to show here. If you have local dashcam footage but no Tessie drive history, try "
              "'--timeline' instead to browse by date using footage alone (no telemetry overlay).")
        sys.exit(0)

    cutoff_dt = None
    end_dt = None

    if args.today:
        cutoff_dt = parse_relative_date("today")
    elif args.yesterday:
        cutoff_dt = parse_relative_date("yesterday")
        end_dt = parse_relative_date("today")
    elif args.since:
        cutoff_dt = parse_relative_date(args.since)
    elif args.days:
        cutoff_dt = datetime.now() - timedelta(days=args.days)

    filtered = []
    for d in drives:
        if cutoff_dt and d["start_dt"] < cutoff_dt:
            continue
        if end_dt and d["start_dt"] >= end_dt:
            continue
        if args.month:
            m_target = args.month.strip().lower()
            d_ym = d["start_dt"].strftime("%Y-%m")
            d_month_name = d["start_dt"].strftime("%B").lower()
            if m_target not in d_ym and not d_month_name.startswith(m_target):
                continue
        if args.place:
            q = args.place.lower()
            if q not in d["start_place"].lower() and q not in d["end_place"].lower():
                continue
        filtered.append(d)

    if not filtered:
        print("No drives matched your filter criteria.")
        return

    days_dict = defaultdict(list)
    for d in filtered:
        d_key = d["start_dt"].strftime("%Y-%m-%d")
        days_dict[d_key].append(d)

 # If specific day or single day filtered, go directly to day view
    if args.today or args.yesterday:
        selected_d = list(days_dict.keys())[0]
        drill_down_day(selected_d, days_dict[selected_d], analyzer)
        return

 # If filtered to a specific month or single place/short range
    unique_months = set(d_str[:7] for d_str in days_dict.keys())
    if args.month or len(unique_months) == 1:
        m_str = list(unique_months)[0]
        dt_m = datetime.strptime(m_str + "-01", "%Y-%m-%d")
        title = args.place if args.place else dt_m.strftime("%B %Y")
        display_days_menu(days_dict, title, analyzer, can_go_back=False)
        return

    title = args.place if args.place else ("Drives Since " + cutoff_dt.strftime('%Y-%m-%d') if cutoff_dt else "All Drive History")
    display_months_menu(days_dict, analyzer)


if __name__ == "__main__":
    main()
