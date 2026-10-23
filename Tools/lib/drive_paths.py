"""drive_paths.py - one place that knows where a drive's files live.

Layout (day folders): every Tessie drive gets its own folder inside the folder of its LOCAL DAY, under <data>/drives/deepdive/:

    drives/deepdive/2026-06-01/                          the day (the date in the drive's stamp)
        2026-06-01_08-34_AEST/                           one drive, named by its start stamp
            drive_deepdive_2026-06-01_08-34_AEST.json    Tessie telemetry, as Tessie sent it (UTC epoch per point, no zone anywhere inside; see lib/drive_points.py)
                                                         (older drives hold the same as .csv, local clock text; both are read through lib/drive_points.py)
            drive_summary.json                           what drive_report.py found, incl. t0/t1 (UTC epoch seconds), tz and utc_offset_s
            drive.html                                   summary page (drive_report.py)
            clips/                                       event snippets + stills, when Recent/Saved footage overlaps the drive

The day is the local date at the place the drive STARTED (the stamp's date), so a Queensland morning drive and the QLD/NSW border do not
move a drive to another day; day_of(stamp) needs nothing but the stamp, so a drive is found without a lookup. Times inside the data are UTC;
the drive's zone is worked out from where it began (lib/drive_points.zone_of) when a local clock is to be shown. drive_summary.json is a COPY made from Tessie's points and the camera store (rebuild it with drive_report.py --force); it is never the source of anything.

Older layouts are still found (and listed once, the newest layout winning), so a half-migrated data home, or an older sync, never hides a
drive: per-drive folders directly under deepdive/ (deepdive/<stamp>/) and flat files (deepdive/drive_deepdive_<stamp>.csv).
migrate_drive_folders.py moves everything to the day layout.
"""
import glob
import os
import re

PREFIX = "drive_deepdive_"
EXTS = (".json", ".csv")          # the stored forms of a drive's telemetry, newest form first
STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}(_[A-Z]{3,4})?(_\d{2})?$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def stamp_of(path):
    """'.../drive_deepdive_2026-06-01_08-34_AEST.json' (or .csv) -> '2026-06-01_08-34_AEST'."""
    b = os.path.basename(path)
    if b.startswith(PREFIX):
        for e in EXTS:
            if b.endswith(e):
                return b[len(PREFIX):-len(e)]
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


def data_path(deepdive_root, stamp, create=False, ext=".json"):
    """Where the telemetry file for `stamp` belongs: in its existing folder, else in the day layout. New files are JSON; a manual CSV export asks for ext='.csv'."""
    folder = folder_for(deepdive_root, stamp)
    if create:
        os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{PREFIX}{stamp}{ext}")


def csv_path(deepdive_root, stamp, create=False):
    """Where a CSV (manual export) for `stamp` belongs."""
    return data_path(deepdive_root, stamp, create, ".csv")


def find_data(deepdive_root, stamp):
    """Existing telemetry file (JSON, else CSV) for stamp in any layout, else None."""
    f = find_folder(deepdive_root, stamp)
    for e in EXTS:
        for p in ((os.path.join(f, f"{PREFIX}{stamp}{e}"),) if f else ()) + (os.path.join(deepdive_root, f"{PREFIX}{stamp}{e}"),):
            if os.path.isfile(p):
                return p
    return None


find_csv = find_data       # the old name; the file it finds may be JSON now


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


def list_data(deepdive_root):
    """Every drive telemetry file (JSON or CSV) under a deepdive dir, all layouts, each stamp once (the newest layout wins, and JSON wins over CSV),
    sorted by stamp."""
    found = {}
    for e in reversed(EXTS):                                                                                              # CSV first, so JSON overwrites it
        for pat in (os.path.join(deepdive_root, f"{PREFIX}*{e}"), os.path.join(deepdive_root, "*", f"{PREFIX}*{e}")):    # flat, per-drive
            for p in glob.glob(pat):
                found[stamp_of(p)] = p
        for p in glob.glob(os.path.join(deepdive_root, "*", "*", f"{PREFIX}*{e}")):                                      # day layout
            found[stamp_of(p)] = p
    return sorted(found.values(), key=lambda p: (stamp_of(p) or "", p))


list_csvs = list_data      # the old name; the files it lists may be JSON now


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
