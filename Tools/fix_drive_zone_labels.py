#!/usr/bin/env python3
"""fix_drive_zone_labels.py - rename drive folders whose zone label is not the zone the drive started in.

NAME
    fix_drive_zone_labels.py - correct the zone at the end of drive stamps (AEDT -> AEST and so on)

SYNOPSIS
    fix_drive_zone_labels.py [--data-dir DIR] [--apply]

DESCRIPTION
    A drive's stamp is its local start minute plus the zone it started in: 2026-10-11_14-40_AEST. Older drives were labelled
    with the account's own zone instead (2026-10-11_14-40_AEDT for a drive that began in Queensland, which has no daylight
    saving), so the time and the label disagreed. The right zone is the one drives_master.csv records in "Started At (Local)".

    For each drive whose label differs, this renames the folder under drives/deepdive/<day>/, the files in it that carry the
    stamp, the stamp written inside its drive_summary.json and drive.html, the line in drives/deepdive/index.html, and the
    Review data kept for it (drive_review/drives, drive_review/events, drive_review/crops).

    Files are renamed, not copied or deleted. A drive whose new name already exists is left alone and listed.
    Safe to run again. Without --apply nothing changes: you see what would be renamed.

OPTIONS
    --data-dir DIR   Tesla data folder (default: config.json)
    --apply          do the renames
    -h, --help       this text

EXIT STATUS
    0 done (or dry run), 1 no drives_master.csv or deepdive folder.

EXAMPLES
    ./fix_drive_zone_labels.py
    ./fix_drive_zone_labels.py --apply
"""
import argparse
import csv
import glob
import os
import re
import sys

LOCAL = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2})(?::\d{2})?\s+([A-Za-z]{2,5})\s*$")
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2})_([A-Za-z]{2,5})$")
TEXT = (".json", ".html", ".csv")


def default_data_dir():
    try:
        from tessie_api_common import resolve_tessie_data_home
        return resolve_tessie_data_home()
    except Exception:
        return os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla")


def wrong_labels(data):
    """[(old stamp, new stamp)] for every deepdive folder whose zone is not the zone its drive started in."""
    zone = {}
    with open(os.path.join(data, "drives", "drives_master.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            m = LOCAL.match((r.get("Started At (Local)") or "").strip())
            if m:
                zone["%s_%s-%s" % (m.group(1), m.group(2), m.group(3))] = m.group(4)
    out = []
    for d in sorted(glob.glob(os.path.join(data, "drives", "deepdive", "*", "*"))):
        m = STAMP.match(os.path.basename(d))
        if m and os.path.isdir(d) and zone.get(m.group(1)) and zone[m.group(1)] != m.group(2):
            out.append((os.path.basename(d), "%s_%s" % (m.group(1), zone[m.group(1)])))
    return out


def replace_in(path, old, new):
    try:
        with open(path, encoding="utf-8") as fh:
            s = fh.read()
    except (OSError, UnicodeDecodeError):
        return False
    if old not in s:
        return False
    with open(path, "w", encoding="utf-8") as fh:               # same file, same place: the exec bit and times of the folder are kept
        fh.write(s.replace(old, new))
    return True


def apply_one(data, old, new):
    day = os.path.join(data, "drives", "deepdive", old[:10])
    src, dst = os.path.join(day, old), os.path.join(day, new)
    if os.path.exists(dst):
        return "exists: %s" % new
    os.rename(src, dst)
    for n in os.listdir(dst):                                     # the files that carry the stamp (the csv, the profile page)
        p = os.path.join(dst, n)
        if old in n and os.path.isfile(p):
            os.rename(p, os.path.join(dst, n.replace(old, new)))
    for n in ("drive_summary.json", "drive.html"):
        if os.path.exists(os.path.join(dst, n)):
            replace_in(os.path.join(dst, n), old, new)
    rv = os.path.join(data, "drive_review")
    for a, b in ((os.path.join(rv, "drives", old + ".json"), os.path.join(rv, "drives", new + ".json")),
                 (os.path.join(rv, "events", old), os.path.join(rv, "events", new))):
        if os.path.exists(a) and not os.path.exists(b):
            os.rename(a, b)
    if os.path.exists(os.path.join(rv, "drives", new + ".json")):
        replace_in(os.path.join(rv, "drives", new + ".json"), old, new)
    for p in glob.glob(os.path.join(rv, "crops", old + "_*")):
        os.rename(p, os.path.join(os.path.dirname(p), os.path.basename(p).replace(old, new, 1)))
    replace_in(os.path.join(data, "drives", "deepdive", "index.html"), old, new)
    return "ok"


def main():
    ap = argparse.ArgumentParser(add_help=False, usage=argparse.SUPPRESS)
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("-h", "--help", action="store_true")
    a = ap.parse_args()
    if a.help:
        print(__doc__.strip("\n"))
        return 0
    data = os.path.expanduser(a.data_dir) if a.data_dir else default_data_dir()
    if not os.path.isfile(os.path.join(data, "drives", "drives_master.csv")) or not os.path.isdir(os.path.join(data, "drives", "deepdive")):
        print("No drives_master.csv or deepdive folder in %s" % data)
        return 1
    todo = wrong_labels(data)
    if not todo:
        print("Every drive folder has the zone it started in.")
        return 0
    for old, new in todo:
        print("%s -> %s%s" % (old, new, "" if not a.apply else "  " + apply_one(data, old, new)))
    print("%d drive folder(s) %s." % (len(todo), "renamed" if a.apply else "would be renamed (run with --apply)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
