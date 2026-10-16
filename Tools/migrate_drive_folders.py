#!/usr/bin/env python3
"""migrate_drive_folders.py - move the drive deep-dive files into day folders.

NAME
    migrate_drive_folders.py - put each drive's files in <day>/<drive>/ under drives/deepdive

SYNOPSIS
    migrate_drive_folders.py [--data-dir DIR] [--apply]

DESCRIPTION
    Every drive gets its own folder inside the folder of the day it started (the date in its stamp, the local date
    where the drive began):

        drives/deepdive/2026-06-01/2026-06-01_08-34_AEST/drive_deepdive_2026-06-01_08-34_AEST.csv

    Two older layouts are moved into it:

        drives/deepdive/drive_deepdive_<stamp>.csv                  (files directly in deepdive/)
        drives/deepdive/<stamp>/...                                 (one folder per drive)

    Nothing is copied or deleted: files and folders are renamed within deepdive/, so modification times are kept. A drive
    whose new place already exists is left where it is and listed. Safe to run again.

    Without --apply nothing changes: you see how many drives would move, per layout, and a few examples.

OPTIONS
    --data-dir DIR   Tesla data folder (default: config.json)
    --apply          do the moves
    -h, --help       this text

EXIT STATUS
    0 done (or dry run), 1 no deepdive folder.

EXAMPLES
    ./migrate_drive_folders.py
    ./migrate_drive_folders.py --apply
    ./drive_report.py

NOTES
    Run ./drive_report.py afterwards: it rebuilds each drive page and the index of all drives for the new places, and adds the
    UTC start and end, the zone and the offset to each drive_summary.json. The app finds a drive in either layout, so it
    keeps working before and after.
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
import drive_paths as dp  # noqa: E402

SIDE = re.compile(r"^drive_deepdive_(?P<stamp>.+?)(?P<suffix>_profile\.html|\.csv)$")


def default_data_dir():
    try:
        from tessie_api_common import resolve_tessie_data_home
        return resolve_tessie_data_home()
    except Exception:
        return os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla")


def plan(root):
    """([(src, dst, kind)], [(src, dst)] conflicts) for flat files and per-drive folders."""
    moves, conflicts = [], []
    for fn in sorted(os.listdir(root)):
        src = os.path.join(root, fn)
        if os.path.isfile(src):
            m = SIDE.match(fn)
            if not m:
                continue
            stamp = m.group("stamp")
            dst = os.path.join(dp.folder_path(root, stamp), fn)
            kind = "file"
        elif os.path.isdir(src) and dp.STAMP_RE.match(fn):
            dst = dp.folder_path(root, fn)
            kind = "folder"
        else:
            continue
        if os.path.exists(dst):
            if kind == "file" and os.path.isdir(os.path.dirname(dst)):
                conflicts.append((src, dst))
            elif kind == "folder":
                conflicts.append((src, dst))
            else:
                moves.append((src, dst, kind))
        else:
            moves.append((src, dst, kind))
    return moves, conflicts


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False, usage=argparse.SUPPRESS)
    ap.add_argument("-h", "--help", action="help", help=argparse.SUPPRESS)
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    data = os.path.abspath(os.path.expanduser(a.data_dir)) if a.data_dir else default_data_dir()
    root = os.path.join(data, "drives", "deepdive")
    if not os.path.isdir(root):
        print(f"No such folder: {root}")
        return 1
    moves, conflicts = plan(root)
    n_files = sum(1 for m in moves if m[2] == "file")
    n_dirs = sum(1 for m in moves if m[2] == "folder")
    days = {os.path.basename(os.path.dirname(d)) if k == "folder" else os.path.basename(os.path.dirname(os.path.dirname(d))) for _, d, k in moves}
    already = len([1 for s, f in dp.iter_folders(root) if dp.DAY_RE.match(os.path.basename(os.path.dirname(f)))])
    print(f"Already in day folders: {already} drive(s).")
    print(f"To move: {n_dirs} drive folder(s) and {n_files} loose file(s) into {len(days)} day folder(s).")
    if conflicts:
        print(f"Left where they are (the new place already exists): {len(conflicts)}")
        for s, d in conflicts[:10]:
            print(f"  {os.path.relpath(s, root)}")
    if not a.apply:
        for s, d, k in moves[:5]:
            print(f"  would move {os.path.relpath(s, root)} -> {os.path.relpath(d, root)}")
        print("Nothing changed. Run again with --apply to move them.")
        return 0
    done = 0
    for s, d, k in moves:
        os.makedirs(os.path.dirname(d), exist_ok=True)
        os.rename(s, d)
        done += 1
    print(f"Moved {done}. Next: ./drive_report.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
