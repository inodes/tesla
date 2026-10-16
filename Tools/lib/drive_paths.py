"""drive_paths.py - one place that knows where a drive's files live.

Layout (day folders): every Tessie drive gets its own folder inside the folder of its LOCAL DAY, under <data>/drives/deepdive/:

    drives/deepdive/2026-06-01/                          the day (the date in the drive's stamp)
        2026-06-01_08-34_AEST/                           one drive, named by its start stamp
            drive_deepdive_2026-06-01_08-34_AEST.csv     Tessie telemetry (UTC-based; the CSV header names the zone it was written in)
            drive_summary.json                           what drive_report.py found, incl. t0/t1 (UTC epoch seconds), tz and utc_offset_s
            drive.html                                   summary page (drive_report.py)
            clips/                                       event snippets + stills, when Recent/Saved footage overlaps the drive

The day is the local date at the place the drive STARTED (the stamp's date), so a Queensland morning drive and the QLD/NSW border do not
move a drive to another day; day_of(stamp) needs nothing but the stamp, so a drive is found without a lookup. Times inside the data are UTC;
the zone and offset are stored with each drive for converting back.

Older layouts are still found (and listed once, the newest layout winning), so a half-migrated data home, or an older sync, never hides a
drive: per-drive folders directly under deepdive/ (deepdive/<stamp>/) and flat files (deepdive/drive_deepdive_<stamp>.csv).
migrate_drive_folders.py moves everything to the day layout.
"""
import glob
import os
import re

PREFIX = "drive_deepdive_"
STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}(_[A-Z]{3,4})?(_\d{2})?$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def stamp_of(path):
    """'.../drive_deepdive_2026-06-01_08-34_AEST.csv' -> '2026-06-01_08-34_AEST'."""
    b = os.path.basename(path)
    if b.startswith(PREFIX) and b.endswith(".csv"):
        return b[len(PREFIX):-4]
    return None


def day_of(stamp):
    """'2026-06-01_08-34_AEST' -> '2026-06-01' (the local date the drive started on)."""
    return stamp[:10]


def folder_path(deepdive_root, stamp):
    """Where the folder of drive `stamp` belongs (day layout)."""
    return os.path.join(deepdive_root, day_of(stamp), stamp)


def find_folder(deepdive_root, stamp):
    """The existing folder of a drive in any layout (day, then per-drive), else None."""
    for p in (folder_path(deepdive_root, stamp), os.path.join(deepdive_root, stamp)):
        if os.path.isdir(p):
            return p
    return None


def folder_for(deepdive_root, stamp):
    """The folder to use for a drive: the existing one, else where it belongs (not created)."""
    return find_folder(deepdive_root, stamp) or folder_path(deepdive_root, stamp)


def csv_path(deepdive_root, stamp, create=False):
    """Where the CSV for `stamp` belongs: in its existing folder, else in the day layout."""
    folder = folder_for(deepdive_root, stamp)
    if create:
        os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{PREFIX}{stamp}.csv")


def unique_stamp(deepdive_root, stamp):
    """First stamp (stamp, stamp_01, stamp_02 ...) with no folder or flat file yet, in any layout."""
    cand, n = stamp, 1
    while find_folder(deepdive_root, cand) or os.path.exists(os.path.join(deepdive_root, f"{PREFIX}{cand}.csv")):
        cand = f"{stamp}_{n:02d}"
        n += 1
    return cand


def find_csv(deepdive_root, stamp):
    """Existing CSV for stamp in any layout, else None."""
    f = find_folder(deepdive_root, stamp)
    for p in ((os.path.join(f, f"{PREFIX}{stamp}.csv"),) if f else ()) + (os.path.join(deepdive_root, f"{PREFIX}{stamp}.csv"),):
        if os.path.isfile(p):
            return p
    return None


def iter_folders(deepdive_root):
    """[(stamp, folder)] of every drive folder, day layout and per-drive layout, each stamp once (the day layout wins), sorted by stamp."""
    seen = {}
    try:
        names = os.listdir(deepdive_root)
    except OSError:
        return []
    for nm in names:
        p = os.path.join(deepdive_root, nm)
        if DAY_RE.match(nm) and os.path.isdir(p):
            try:
                subs = os.listdir(p)
            except OSError:
                continue
            for sn in subs:
                if STAMP_RE.match(sn) and os.path.isdir(os.path.join(p, sn)):
                    seen[sn] = os.path.join(p, sn)
    for nm in names:
        p = os.path.join(deepdive_root, nm)
        if STAMP_RE.match(nm) and nm not in seen and os.path.isdir(p):
            seen[nm] = p
    return sorted(seen.items())


def list_csvs(deepdive_root):
    """Every drive CSV under a deepdive dir, all layouts, each stamp once (the newest layout wins), sorted by stamp."""
    found = {}
    for pat in (os.path.join(deepdive_root, f"{PREFIX}*.csv"), os.path.join(deepdive_root, "*", f"{PREFIX}*.csv")):    # flat, per-drive
        for p in glob.glob(pat):
            found[stamp_of(p)] = p
    for p in glob.glob(os.path.join(deepdive_root, "*", "*", f"{PREFIX}*.csv")):                                          # day layout
        found[stamp_of(p)] = p
    return sorted(found.values(), key=lambda p: (stamp_of(p) or "", p))


def signature(deepdive_root):
    """A cheap fingerprint of which drives exist (changes when a drive or a day appears), for caches."""
    try:
        names = os.listdir(deepdive_root)
    except OSError:
        return (0, 0)
    newest = 0
    for nm in names:
        if DAY_RE.match(nm):
            try:
                newest = max(newest, os.stat(os.path.join(deepdive_root, nm)).st_mtime)
            except OSError:
                pass
    return (len(names), newest)


def drive_folder_of(csv_file):
    """The per-drive folder holding csv_file (either layout), or None for a legacy flat file."""
    parent = os.path.dirname(os.path.abspath(csv_file))
    stamp = stamp_of(csv_file)
    if stamp and os.path.basename(parent) == stamp:
        return parent
    return None


def drives_dir(folder):
    """<data>/drives for a drive folder in any layout (the folder above 'deepdive')."""
    p = os.path.abspath(folder)
    while True:
        up = os.path.dirname(p)
        if os.path.basename(p) == "deepdive" or up == p:
            return up
        p = up
