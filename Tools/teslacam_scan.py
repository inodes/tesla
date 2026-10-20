#!/usr/bin/env python3
"""teslacam_scan.py - read the telemetry the connected TeslaCam drives still lack.

NAME
    teslacam_scan.py - compare the footage on TeslaCam drives with the telemetry already kept, and read what is missing

SYNOPSIS
    teslacam_scan.py [--data-dir DIR] [--with-secondary | --drive ROLE[,ROLE]] [--read] [--limit N]

DESCRIPTION
    The speed, GPS, steering, pedals and autopilot of every minute of Recent and Saved video are read once and kept in
    <data>/teslacam_timeline. The kept telemetry outlives the video, so a drive can still be drawn from it ("Enrich with
    TeslaCam metadata") after the clips are deleted. This lists, for each drive, how many minutes are already read and
    how many are not. A minute on two drives is read once, from the first (Archive, JOWUA, Tesla USB). Drives are
    recognised by what is on them, not by their name. Nothing on a drive is changed.

    Without --read nothing is read: you only see the counts.

OPTIONS
    --data-dir DIR       Tesla data folder (default: config.json)
    --with-secondary     scan every connected drive, not only the Archive
    --drive ROLE[,ROLE]  scan these drives: A Archive, J JOWUA, U Tesla USB (e.g. --drive A,J)
    --read               read the missing telemetry, newest first
    --limit N            with --read, stop after N minutes
    -h, --help           this text

EXIT STATUS
    0 done, 1 no TeslaCam drive found, 130 stopped with Ctrl-C (what was read is kept).

EXAMPLES
    ./teslacam_scan.py
    ./teslacam_scan.py --with-secondary
    ./teslacam_scan.py --drive A,J --read

NOTES
    Only the Archive is read unless you ask for more (see AGENTS.md 3.5.1): a drive that is read stays "in use" until the
    folder is disconnected from an agent session. Reading a minute opens its front clip. The same panel is on the
    TeslaCam page of tesla_app.py.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
import drive_roles as DR  # noqa: E402
import teslacam_scan as TS  # noqa: E402


def _n(x):
    return "{:,}".format(x)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False, usage=argparse.SUPPRESS)
    ap.add_argument("-h", "--help", action="help", help=argparse.SUPPRESS)
    ap.add_argument("--data-dir")
    ap.add_argument("--with-secondary", action="store_true")
    ap.add_argument("--drive")
    ap.add_argument("--read", action="store_true")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    if a.data_dir:
        data = os.path.abspath(os.path.expanduser(a.data_dir))
    else:
        from tessie_api_common import resolve_tessie_data_home
        data = resolve_tessie_data_home()
    found = {r: p for p, r in DR.discover()}
    want = [r.strip().upper() for r in a.drive.split(",")] if a.drive else (list(found) if a.with_secondary else ["A"])
    roots = {r: found[r] for r in want if r in found}
    for r in want:
        if r not in found:
            print("%s is not connected." % DR.NAMES.get(r, r))
    if not roots:
        print("No TeslaCam drive to scan.")
        return 1
    import app_teslacam
    tc = app_teslacam.TeslaCam(data)
    rep = tc.scan_report(roots)
    print("%-10s %9s %9s %9s %11s  %s" % ("Drive", "Minutes", "Read", "To read", "On another", "Dates"))
    for d in rep["drives"]:
        print("%-10s %9s %9s %9s %11s  %s" % (d["name"], _n(d["minutes"]), _n(d["read"]), _n(d["to_read"]), _n(d["elsewhere"]), TS.days(d["first"], d["last"])))
        if d["no_front"]:
            print("%-10s %s minute(s) have no front clip and cannot be read." % ("", _n(d["no_front"])))
    print("Telemetry kept: %s minutes, %s of them with no footage on the scanned drive(s) (kept anyway)." % (_n(rep["kept"]), _n(rep["kept_orphans"])))
    todo = tc._scan_todo
    if not todo:
        print("Nothing to read.")
        return 0
    if not a.read:
        print("%s minute(s) to read. Run again with --read." % _n(len(todo)))
        return 0
    total = min(len(todo), a.limit) if a.limit else len(todo)
    print("Reading %s minute(s), newest first. Ctrl-C stops; what is read is kept." % _n(total))
    store, _ = tc._tl("A")

    from progress import Bar
    bar = Bar(total, "Telemetry")

    def prog(n, stamp):
        bar.update(n, stamp.replace("_", " "))
    try:
        n = TS.read(store, todo, data, progress=prog, limit=a.limit)
    except KeyboardInterrupt:
        store.flush()
        print("Stopped. What was read is kept.")
        return 130
    bar.done("Read %s minute(s)." % _n(n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
