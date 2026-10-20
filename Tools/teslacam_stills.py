#!/usr/bin/env python3
"""teslacam_stills.py - keep a still for every second of the front-camera video on the connected TeslaCam drives.

NAME
    teslacam_stills.py - take one-second stills of every front clip and keep them (they outlive the video)

SYNOPSIS
    teslacam_stills.py [--data-dir DIR] [--with-secondary | --drive ROLE[,ROLE]] [--make] [--limit N] [--workers N] [--pack]

DESCRIPTION
    Every minute of front-camera video gets 60 small stills (400 px wide, one per second), kept in
    <data>/teslacam_stills/<day>/<clip time>.zip - one file per clip, the images inside are 00.jpg to 59.jpg. When the clips are
    made they are packed, one file per hour (<day>_<HH>.zip, hour on the car's clock like the clip names, so the name never changes),
    which keeps the number of files iCloud has to sync at about 24 a day. The drive
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
    --pack               only fold the single-clip files into hourly files (also done after --make); safe to repeat and to stop
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
import time

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
    ap.add_argument("--pack", action="store_true")
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
    if a.pack and not a.make:
        print("Packing into hourly files ...")
        h, c = stills.pack(progress=lambda n, hour, k: sys.stdout.write("\r  %s hour file(s) written (%s)\033[K" % (_n(n), hour)) or sys.stdout.flush())
        print("\nPacked %s clip(s) into %s hour file(s)." % (_n(c), _n(h)))
        return 0
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

    t0 = time.time()
    tty = sys.stdout.isatty()

    def prog(n, stamp):
        """A one-line progress bar: percent, count, speed, time left, and the clip being made."""
        if not tty and n % 100 and n != total:
            return
        el = max(time.time() - t0, 1e-6)
        rate = n / el
        left = (total - n) / rate if rate else 0
        w = 24
        fill = int(w * n / total) if total else w
        line = "[%s%s] %3d%%  %s of %s  %.1f clips/s  %s left  %s" % ("#" * fill, "-" * (w - fill), 100 * n // max(total, 1), _n(n), _n(total), rate,
                                                                   "%dh%02dm" % (left // 3600, left % 3600 // 60) if left >= 3600 else "%dm%02ds" % (left // 60, left % 60),
                                                                   stamp.replace("_", " "))
        if tty:
            sys.stdout.write("\r" + line + "\033[K")
            sys.stdout.flush()
            if n == total:
                sys.stdout.write("\n")
        else:
            print(line)
    try:
        n = ST.make_many(stills, todo, a.workers or app_config.get("stills.workers", 3), progress=prog, limit=a.limit)
    except KeyboardInterrupt:
        print("\nStopped. What was made is kept.")
        return 130
    print("Made %s clip(s)." % _n(n))
    h, c = stills.pack()
    if c:
        print("Packed %s clip(s) into %s hour file(s)." % (_n(c), _n(h)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
