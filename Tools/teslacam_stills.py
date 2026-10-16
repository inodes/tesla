#!/usr/bin/env python3
"""teslacam_stills.py - keep a still for every second of the front-camera video on the connected TeslaCam drives.

NAME
    teslacam_stills.py - take one-second stills of every front clip and keep them (they outlive the video)

SYNOPSIS
    teslacam_stills.py [--data-dir DIR] [--with-secondary | --drive ROLE[,ROLE]] [--make] [--limit N] [--workers N]

DESCRIPTION
    Every minute of front-camera video gets 60 small stills (400 px wide, one per second), kept in
    <data>/teslacam_stills/<day>/<clip time>.zip - one file per clip, the images inside are 00.jpg to 59.jpg. The drive
    reviews show them one by one, and they stay when the video is deleted, so nothing has to be fetched twice. A clip that
    already has its stills is skipped. A clip on two drives (a copy) is made once, from the first (Archive, JOWUA, Tesla USB).
    Recent, Saved and Sentry clips are all included. Nothing on a drive is changed.

    Without --make you only see the counts. About 750 KB per minute of video (all of it: roughly 8 GB), about 2 s a minute.

OPTIONS
    --data-dir DIR       Tesla data folder (default: config.json)
    --with-secondary     every connected drive, not only the Archive
    --drive ROLE[,ROLE]  these drives: A Archive, J JOWUA, U Tesla USB
    --make               take the missing stills, newest first
    --limit N            with --make, stop after N clips
    --workers N          clips made at once (default: app.stills.workers in config.json, 3)
    -h, --help           this text

EXIT STATUS
    0 done, 1 no TeslaCam drive found or no ffmpeg, 130 stopped with Ctrl-C (what was made is kept).

EXAMPLES
    ./teslacam_stills.py
    ./teslacam_stills.py --with-secondary --make
    ./teslacam_stills.py --make --limit 100

NOTES
    Width, quality and workers are settings (app.stills in config.json). Only the Archive is read unless you ask for more
    (AGENTS.md 3.5.1). See also teslacam_ledger.py (which files are on which drive, and which were deleted).
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
import app_config  # noqa: E402
import drive_roles as DR  # noqa: E402
import teslacam_stills as ST  # noqa: E402


def _n(x):
    return "{:,}".format(x)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False, usage=argparse.SUPPRESS)
    ap.add_argument("-h", "--help", action="help", help=argparse.SUPPRESS)
    ap.add_argument("--data-dir")
    ap.add_argument("--with-secondary", action="store_true")
    ap.add_argument("--drive")
    ap.add_argument("--make", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int)
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
        print("No TeslaCam drive to look at.")
        return 1
    if not ST.have_ffmpeg():
        print("ffmpeg is not installed.")
        return 1
    stills = ST.Stills(data, app_config.get("stills.width", 400), app_config.get("stills.quality", 6))
    rep = ST.plan(stills, roots)
    print("%-10s %9s %9s %9s %11s  %s" % ("Drive", "Clips", "Done", "To make", "On another", "Dates"))
    for d in rep["drives"]:
        print("%-10s %9s %9s %9s %11s  %s" % (DR.NAMES.get(d["role"], d["role"]), _n(d["clips"]), _n(d["done"]), _n(d["to_make"]), _n(d["elsewhere"]),
                                              d["first"] if d["first"] == d["last"] else "%s to %s" % (d["first"], d["last"])))
        if not d["ok"]:
            print("%-10s some folders could not be read." % "")
    print("Stills kept: %s clip(s), including video that is gone." % _n(rep["kept"]))
    todo = rep["todo"]
    if not todo:
        print("Nothing to make.")
        return 0
    if not a.make:
        print("%s clip(s) to make. Run again with --make." % _n(len(todo)))
        return 0
    total = min(len(todo), a.limit) if a.limit else len(todo)
    print("Making %s clip(s), newest first. Ctrl-C stops; what is made is kept." % _n(total))

    def prog(n, stamp):
        if n % 20 == 0 or n == total:
            print("  %s of %s  (%s)" % (_n(n), _n(total), stamp.replace("_", " ")))
    try:
        n = ST.make_many(stills, todo, a.workers or app_config.get("stills.workers", 3), progress=prog, limit=a.limit)
    except KeyboardInterrupt:
        print("Stopped. What was made is kept.")
        return 130
    print("Made %s clip(s)." % _n(n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
