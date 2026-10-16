#!/usr/bin/env python3
"""migrate_charge_folders.py - move the charge deep-dive files into day folders.

NAME
    migrate_charge_folders.py - put each charge's files in <day>/<charge>/ under charges/deepdive

SYNOPSIS
    migrate_charge_folders.py [--data-dir DIR] [--apply]

DESCRIPTION
    Same layout as the drives. Every charge gets its own folder inside the folder of the day it started (the date in its
    stamp), named by its start time:

        charges/deepdive/2026-06-12/2026-06-12_17-02_AEST/charge_deepdive_2026-06-12_1702_AEST_00_Evnex_Home.csv

    The folder holds everything of that charge: the telemetry CSV of each Tessie record (_00, _01, ...), the Evnex JSON,
    the energy-source file and the curve and profile charts. File names do not change.

    Nothing is copied or deleted: files are renamed into place, so modification times are kept. A file whose new place
    already exists is left where it is and listed. Hidden files (.evnex_*) and _unusable/ stay in deepdive/.
    Safe to run again.

    Without --apply nothing changes: you see how many files and charges would move, and a few examples.

OPTIONS
    --data-dir DIR   Tesla data folder (default: config.json)
    --apply          do the moves
    -h, --help       this text

EXIT STATUS
    0 done (or dry run), 1 no deepdive folder.

EXAMPLES
    ./migrate_charge_folders.py
    ./migrate_charge_folders.py --apply
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
sys.path.insert(0, HERE)
import charge_paths as cp  # noqa: E402


def default_data_dir():
    try:
        from tessie_api_common import resolve_tessie_data_home
        return resolve_tessie_data_home()
    except Exception:
        return os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla")


def plan(root):
    """([(src, dst)], [src left where it is because dst exists], [names that are not charge files])."""
    moves, conflicts, other = [], [], []
    for nm in sorted(os.listdir(root)):
        p = os.path.join(root, nm)
        if not os.path.isfile(p) or nm.startswith("."):
            continue
        folder = cp.folder_of_name(root, nm)
        if folder is None:
            other.append(nm)
            continue
        dst = os.path.join(folder, nm)
        (conflicts if os.path.exists(dst) else moves).append((p, dst))
    return moves, conflicts, other


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False, usage=argparse.SUPPRESS)
    ap.add_argument("-h", "--help", action="help", help=argparse.SUPPRESS)
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    data = os.path.abspath(os.path.expanduser(a.data_dir)) if a.data_dir else default_data_dir()
    root = os.path.join(data, "charges", "deepdive")
    if not os.path.isdir(root):
        print(f"No such folder: {root}")
        return 1
    moves, conflicts, other = plan(root)
    stamps = {cp.stamp_of(os.path.basename(s)) for s, _ in moves}
    days = {cp.day_of(s) for s in stamps}
    print(f"Already in day folders: {len(cp.list_files(root)) - len(moves)} file(s).")
    print(f"To move: {len(moves)} file(s) of {len(stamps)} charge(s) into {len(days)} day folder(s).")
    if conflicts:
        print(f"Left where they are (the new place already exists): {len(conflicts)}")
        for s, _d in conflicts[:10]:
            print(f"  {os.path.relpath(s, root)}")
    if other:
        print(f"Not charge files, left alone: {len(other)}")
        for nm in other[:10]:
            print(f"  {nm}")
    if not a.apply:
        for s, d in moves[:5]:
            print(f"  would move {os.path.relpath(s, root)} -> {os.path.relpath(d, root)}")
        print("Nothing changed. Run again with --apply to move them.")
        return 0
    done = 0
    for s, d in moves:
        os.makedirs(os.path.dirname(d), exist_ok=True)
        os.rename(s, d)
        done += 1
    print(f"Moved {done}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
