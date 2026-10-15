"""drive_paths.py - one place that knows where a drive's files live.

Layout (REQ-193): every Tessie drive gets its own folder under
<data>/drives/deepdive/, named by the drive start stamp, e.g.

    drives/deepdive/2026-06-01_08-34_AEST/
        drive_deepdive_2026-06-01_08-34_AEST.csv   (Tessie telemetry)
        drive.html                                 (summary page, drive_report.py)
        clips/                                     (event snippets + stills, when
                                                    Recent/Saved footage overlaps)

The CSV keeps its old file name inside the folder so anything that parses the
stamp from the basename keeps working. The legacy flat layout
(deepdive/drive_deepdive_<stamp>.csv) is still found, so a half-migrated data
home, or an older sync, never hides a drive.
"""
import glob
import os
import re

PREFIX = "drive_deepdive_"
STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}(_[A-Z]{3,4})?(_\d{2})?$")


def stamp_of(path):
    """'.../drive_deepdive_2026-06-01_08-34_AEST.csv' -> '2026-06-01_08-34_AEST'."""
    b = os.path.basename(path)
    if b.startswith(PREFIX) and b.endswith(".csv"):
        return b[len(PREFIX):-4]
    return None


def folder_path(deepdive_root, stamp):
    return os.path.join(deepdive_root, stamp)


def csv_path(deepdive_root, stamp, create=False):
    """Where the CSV for `stamp` belongs in the folder layout."""
    folder = folder_path(deepdive_root, stamp)
    if create:
        os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{PREFIX}{stamp}.csv")


def unique_stamp(deepdive_root, stamp):
    """First stamp (stamp, stamp_01, stamp_02 ...) with no folder or flat file yet."""
    cand, n = stamp, 1
    while (os.path.exists(folder_path(deepdive_root, cand))
           or os.path.exists(os.path.join(deepdive_root, f"{PREFIX}{cand}.csv"))):
        cand = f"{stamp}_{n:02d}"
        n += 1
    return cand


def find_csv(deepdive_root, stamp):
    """Existing CSV for stamp in either layout, else None."""
    for p in (csv_path(deepdive_root, stamp),
              os.path.join(deepdive_root, f"{PREFIX}{stamp}.csv")):
        if os.path.isfile(p):
            return p
    return None


def list_csvs(deepdive_root):
    """Every drive CSV under a deepdive dir, both layouts, sorted by stamp."""
    out = glob.glob(os.path.join(deepdive_root, f"{PREFIX}*.csv"))
    out += glob.glob(os.path.join(deepdive_root, "*", f"{PREFIX}*.csv"))
    return sorted(out, key=lambda p: (stamp_of(p) or "", p))


def drive_folder_of(csv_file):
    """The per-drive folder holding csv_file, or None for a legacy flat file."""
    parent = os.path.dirname(os.path.abspath(csv_file))
    stamp = stamp_of(csv_file)
    if stamp and os.path.basename(parent) == stamp:
        return parent
    return None
