#!/usr/bin/env python3
"""migrate_drive_folders.py - move flat drive deep-dive files into per-drive folders.

  drives/deepdive/drive_deepdive_2026-06-01_08-34_AEST.csv
  drives/deepdive/drive_deepdive_2026-06-01_08-34_AEST_profile.html
      ->
  drives/deepdive/2026-06-01_08-34_AEST/drive_deepdive_2026-06-01_08-34_AEST.csv
  drives/deepdive/2026-06-01_08-34_AEST/drive_deepdive_2026-06-01_08-34_AEST_profile.html

Usage:
  migrate_drive_folders.py [--data-dir DIR] [--apply]

Default is a DRY RUN: prints what would move and changes nothing. --apply moves
the files (os.rename within the same folder tree - nothing is copied, nothing is
deleted, file mtimes are kept). Safe to re-run; already-moved drives are skipped.
A stray file whose folder already holds a different file of the same name is left
in place and reported.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import drive_paths as dp  # noqa: E402

SIDE = re.compile(r"^drive_deepdive_(?P<stamp>.+?)(?P<suffix>_profile\.html|\.csv)$")


def default_data_dir():
    try:
        from lib.tessie_api_common import resolve_tessie_data_home  # noqa
        return resolve_tessie_data_home()
    except Exception:
        return os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--apply", action="store_true", help="actually move (default: dry run)")
    a = ap.parse_args()
    data = os.path.abspath(os.path.expanduser(a.data_dir)) if a.data_dir else default_data_dir()
    root = os.path.join(data, "drives", "deepdive")
    if not os.path.isdir(root):
        sys.exit(f"no such folder: {root}")

    moves, conflicts = [], []
    for fn in sorted(os.listdir(root)):
        src = os.path.join(root, fn)
        if not os.path.isfile(src):
            continue
        m = SIDE.match(fn)
        if not m:
            continue
        stamp = m.group("stamp")
        dst = os.path.join(root, stamp, fn)
        if os.path.exists(dst):
            conflicts.append((src, dst))
        else:
            moves.append((src, dst))

    print(f"{len(moves)} file(s) to move, {len(conflicts)} conflict(s)  [{root}]")
    for s, d in conflicts[:20]:
        print(f"  CONFLICT (left in place): {os.path.basename(s)}")
    if not a.apply:
        for s, d in moves[:5]:
            print(f"  would move {os.path.basename(s)} -> {os.path.relpath(d, root)}")
        print("dry run - nothing changed. Re-run with --apply.")
        return
    done = 0
    for s, d in moves:
        os.makedirs(os.path.dirname(d), exist_ok=True)
        os.rename(s, d)
        done += 1
    print(f"moved {done} file(s) into {len(set(os.path.dirname(d) for _, d in moves))} drive folder(s)")


if __name__ == "__main__":
    main()
