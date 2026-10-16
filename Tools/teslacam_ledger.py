#!/usr/bin/env python3
"""teslacam_ledger.py - a log of every TeslaCam video: which drive it is on, its size, and when it was deleted.

NAME
    teslacam_ledger.py - keep and report the log of the TeslaCam files on the connected drives

SYNOPSIS
    teslacam_ledger.py [--data-dir DIR] [--with-secondary | --drive ROLE[,ROLE]] [--scan] [--list deleted|import|purge] [--role ROLE] [--limit N]

DESCRIPTION
    Each scan lists the clips on the drives you pick and updates <data>/teslacam_ledger (one file per day of video):
    for every clip, which drives have it, its size, when each drive first and last had it, and when it went. From the log:

        on one drive only        not yet on the Archive (to import)
        on the Archive and also  can be purged from that drive (same name and size); nothing is deleted by this script
        gone from every drive    deleted: the stills (teslacam_stills.py) and telemetry of it are still kept

    A clip is only marked gone when its whole drive could be read, and not when a drive suddenly lists almost nothing (a
    drive that is not mounted properly). Nothing on a drive is changed; no video is opened. Without --scan you only see the
    log as it stands.

OPTIONS
    --data-dir DIR       Tesla data folder (default: config.json)
    --with-secondary     scan every connected drive, not only the Archive
    --drive ROLE[,ROLE]  scan these drives: A Archive, J JOWUA, U Tesla USB
    --scan               list the drives and update the log
    --list WHAT          list files: deleted, import (only on a drive other than the Archive), purge (with --role: on that drive and the Archive)
    --role ROLE          with --list purge, the drive (J or U)
    --limit N            with --list, show the newest N (default 40)
    -h, --help           this text

EXIT STATUS
    0 done, 1 no TeslaCam drive found to scan.

EXAMPLES
    ./teslacam_ledger.py --scan
    ./teslacam_ledger.py --scan --with-secondary
    ./teslacam_ledger.py --list purge --role J
    ./teslacam_ledger.py --list deleted --limit 100

NOTES
    Only the Archive is scanned unless you ask for more (AGENTS.md 3.5.1). A file's first-seen time is when a scan first saw
    it, so the log gets more exact the more often it is scanned (a scan after each import and each purge is enough).
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
import drive_roles as DR  # noqa: E402
import teslacam_ledger as TLG  # noqa: E402


def _n(x):
    return "{:,}".format(x)


def gb(b):
    return "%.1f GB" % (b / 1024 ** 3) if b >= 1024 ** 3 else "%.0f MB" % (b / 1024 ** 2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False, usage=argparse.SUPPRESS)
    ap.add_argument("-h", "--help", action="help", help=argparse.SUPPRESS)
    ap.add_argument("--data-dir")
    ap.add_argument("--with-secondary", action="store_true")
    ap.add_argument("--drive")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--list", choices=("deleted", "import", "purge"))
    ap.add_argument("--role")
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args()
    if a.data_dir:
        data = os.path.abspath(os.path.expanduser(a.data_dir))
    else:
        from tessie_api_common import resolve_tessie_data_home
        data = resolve_tessie_data_home()
    led = TLG.Ledger(data)
    if a.scan:
        found = {r: p for p, r in DR.discover()}
        want = [r.strip().upper() for r in a.drive.split(",")] if a.drive else (list(found) if a.with_secondary else ["A"])
        roots = {r: found[r] for r in want if r in found}
        for r in want:
            if r not in found:
                print("%s is not connected." % DR.NAMES.get(r, r))
        if not roots:
            print("No TeslaCam drive to scan.")
            return 1
        for r in sorted(roots, key=lambda x: "AJU?".index(x) if x in "AJU?" else 9):
            res = led.scan(r, roots[r])
            print("%-10s %s file(s), %s: %s new, %s gone, %s back.%s" % (DR.NAMES.get(r, r), _n(res["files"]), gb(res["bytes"]), _n(res["new"]), _n(res["gone"]),
                                                                       _n(res["back"]), (" " + res["note"]) if res["note"] else ""))
    s = led.summary()
    if not s["scans"]:
        print("The log is empty. Run with --scan.")
        return 0
    if a.list:
        rows = led.listing(a.list, a.role.upper() if a.role else None, a.limit)
        for day, k, size, note in rows:
            print("%s  %-44s %8s  %s" % (day, k.split("|", 2)[2], gb(size), note))
        print("%d shown." % len(rows))
        return 0
    print("%-10s %12s %12s %9s  %s" % ("Drive", "Listed", "Size", "Gone", "Last scan (UTC)"))
    for r in sorted(s["drives"], key=lambda x: "AJU?".index(x) if x in "AJU?" else 9):
        d, sc = s["drives"][r], s["scans"].get(r, {})
        print("%-10s %12s %12s %9s  %s" % (DR.NAMES.get(r, r), _n(d["listed"]), gb(d["listed_bytes"]), _n(d["gone"]), sc.get("at", "never")))
    t = s["total"]
    print("Clips ever logged: %s (%s)." % (_n(t["files"]), gb(t["bytes"])))
    print("Deleted (gone from every drive): %s (%s). Stills and telemetry of them are kept." % (_n(t["deleted"]), gb(t["deleted_bytes"])))
    print("Only on a drive other than the Archive: %s (%s): not imported yet." % (_n(t["only_secondary"]), gb(t["only_secondary_bytes"])))
    print("Copies that can be purged (also on the Archive): %s (%s)." % (_n(t["purgeable"]), gb(t["purgeable_bytes"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
