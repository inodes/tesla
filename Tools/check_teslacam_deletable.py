#!/usr/bin/env python3
"""check_teslacam_deletable.py - which TeslaCam video is safe to delete, and what is still missing for the rest.

NAME
    check_teslacam_deletable.py - report, per drive, the video whose telemetry, stills and snapshots are all kept

SYNOPSIS
    check_teslacam_deletable.py [--data-dir DIR] [--drive ROLE[,ROLE]] [--show N]

DESCRIPTION
    A minute of video (every camera) is safe to delete from the Archive when its telemetry is kept (teslacam_timeline), its
    front-camera stills are kept (teslacam_stills) and, if it lies inside a drive, that drive's snapshots have been built
    (build_drive_snapshots), and it is not newer than the last drive synced from Tessie. A minute on JOWUA or the Tesla USB is safe to delete as soon as the Archive has the same files with
    the same sizes. The Archive is always needed to judge a copy, so it is always read.

    This only lists drives and looks at what is kept. Nothing is opened, changed or deleted.

OPTIONS
    --data-dir DIR       Tesla data folder (default: config.json)
    --drive ROLE[,ROLE]  drives to report: A Archive, J JOWUA, U Tesla USB (default: A and J)
    --show N             list N example minutes for each thing that is missing (default 5)
    -h, --help           this text

EXIT STATUS
    0 done, 1 no Archive or no data folder.

EXAMPLES
    ./check_teslacam_deletable.py
    ./check_teslacam_deletable.py --drive A,J,U --show 10

NOTES
    Only the drives you name are read (AGENTS.md 3.5.1); a drive that is read stays in use until its folder is disconnected.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
import drive_roles as DR  # noqa: E402
import teslacam_deletable as TD  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False, usage=argparse.SUPPRESS)
    ap.add_argument("-h", "--help", action="help", help=argparse.SUPPRESS)
    ap.add_argument("--data-dir")
    ap.add_argument("--drive")
    ap.add_argument("--show", type=int, default=5)
    a = ap.parse_args()
    if a.data_dir:
        data = os.path.abspath(os.path.expanduser(a.data_dir))
    else:
        from tessie_api_common import resolve_tessie_data_home
        data = resolve_tessie_data_home()
    found = {r: p for p, r in DR.discover()}
    want = [r.strip().upper() for r in a.drive.split(",")] if a.drive else ["A", "J"]
    if "A" not in found:
        print("The Archive is not connected.")
        return 1
    roots = {r: found[r] for r in ["A"] + [x for x in want if x != "A"] if r in found}
    for r in want:
        if r not in found:
            print("%s is not connected." % DR.NAMES.get(r, r))
    rep = TD.report(data, roots)
    for role in sorted(rep, key=lambda r: "AJU".index(r) if r in "AJU" else 9):
        if role not in want and role != "A":
            continue
        r = rep[role]
        name = DR.NAMES.get(role, role)
        print("%s: %s files in %s minutes%s" % (name, format(r["files"], ","), format(r["minutes"], ","), "" if r["listed_ok"] else "  (part of the drive could not be read)"))
        if role == "A":
            print("  safe to delete:            %7s" % format(r["ready"], ","))
            print("  telemetry not kept:        %7s" % format(r["no_telemetry"], ","))
            print("  stills not kept:           %7s" % format(r["no_stills"], ","))
            print("  drive snapshots not built: %7s" % format(r["no_snapshots"], ","))
            print("  newer than the last synced drive: %s  (sync drives first)" % format(r["not_synced"], ","))
            print("  (a minute can be in more than one of these)")
        else:
            print("  safe to delete (Archive has the same files): %s" % format(r["copy_on_archive"], ","))
            print("  not on the Archive (import first):           %s" % format(r["not_on_archive"], ","))
        for k, lst in r["examples"].items():
            if lst and a.show:
                print("  e.g. %s: %s" % (k.replace("_", " "), ", ".join(sorted(lst, reverse=True)[:a.show])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
