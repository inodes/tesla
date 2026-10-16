"""charge_paths.py - one place that knows where a charge's deep-dive files live (the charges twin of drive_paths.py).

Layout (day folders), under <data>/charges/deepdive/:

    charges/deepdive/2026-06-12/                          the day (the date in the charge's stamp)
        2026-06-12_17-02_AEST/                            one charge, named by its start stamp
            charge_deepdive_2026-06-12_1702_AEST_00_Evnex_Home.csv        Tessie telemetry (one per Tessie record of the plug-in: _00, _01, ...)
            charge_deepdive_2026-06-12_1702_AEST_00_Evnex_Home.json       Evnex session (home charger)
            charge_deepdive_..._sources.json                              energy-source cross-check cache (+ .bak)
            charge_deepdive_..._curve.png/.html, _profile.png/.html       charts

File NAMES are unchanged (every reader and writer still parses the same names); only where they sit changed. The folder name is the
file's own start stamp with the time written HH-MM, like a drive folder, so a charge's folder is found from any one of its file names
with no lookup. A charge's start minute is unique (one car), so every file of a plug-in lands in one folder.

Older layout still found: flat files directly under deepdive/. migrate_charge_folders.py moves them into day folders.
"""
import fnmatch
import os
import re

PREFIX = "charge_deepdive_"
NAME_RE = re.compile(r"^charge_deepdive_(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(?:_([A-Z]{3,5}))?(?=_|\.)")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}(_[A-Z]{3,5})?$")
SKIP_DIRS = ("_unusable",)


def stamp_of(name):
    """'charge_deepdive_2026-06-12_1702_AEST_00_Evnex_Home.csv' -> '2026-06-12_17-02_AEST' (None if not a charge deep-dive file)."""
    m = NAME_RE.match(os.path.basename(name))
    if not m:
        return None
    return "%s_%s-%s%s" % (m.group(1), m.group(2), m.group(3), "_" + m.group(4) if m.group(4) else "")


def day_of(stamp):
    """'2026-06-12_17-02_AEST' -> '2026-06-12'."""
    return stamp[:10]


def folder_path(root, stamp):
    """Where the folder of charge `stamp` belongs."""
    return os.path.join(root, day_of(stamp), stamp)


def folder_of_name(root, name):
    """Folder a file called `name` belongs in (None when the name is not a charge deep-dive name)."""
    s = stamp_of(name)
    return folder_path(root, s) if s else None


def path_for(root, name, create=False):
    """Path of file `name`: where it already is (any layout), else where it belongs. create=True makes the folder."""
    p = find(root, name)
    if p:
        return p
    folder = folder_of_name(root, name)
    if folder is None:
        folder = root
    if create:
        os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, os.path.basename(name))


def find(root, name):
    """Existing path of file `name` in the day layout or flat under root, else None."""
    name = os.path.basename(name)
    for p in (os.path.join(folder_of_name(root, name) or root, name), os.path.join(root, name)):
        if os.path.isfile(p):
            return p
    return None


def list_files(root):
    """[(name, path)] of every charge_deepdive_* file under root (day layout and flat), each name once (the day layout wins), sorted by name."""
    found = {}
    try:
        names = os.listdir(root)
    except OSError:
        return []
    for nm in names:
        p = os.path.join(root, nm)
        if nm.startswith(PREFIX) and os.path.isfile(p):
            found[nm] = p
    for nm in names:
        d = os.path.join(root, nm)
        if not (DAY_RE.match(nm) and os.path.isdir(d)):
            continue
        try:
            subs = os.listdir(d)
        except OSError:
            continue
        for sn in subs:
            sd = os.path.join(d, sn)
            if not os.path.isdir(sd):
                continue
            try:
                files = os.listdir(sd)
            except OSError:
                continue
            for fn in files:
                if fn.startswith(PREFIX) and os.path.isfile(os.path.join(sd, fn)):
                    found[fn] = os.path.join(sd, fn)
    return sorted(found.items())


def glob_files(root, pattern):
    """Paths of the charge deep-dive files under root whose NAME matches the glob `pattern` (e.g. 'charge_deepdive_*.csv'), sorted by name."""
    return [p for n, p in list_files(root) if fnmatch.fnmatch(n, pattern)]


def names(root):
    """Names of every charge deep-dive file under root (a drop-in for os.listdir that sees the day folders)."""
    return [n for n, _ in list_files(root)]


def unique_path(root, stamp, suffix="", ext="", prefix="charge_deepdive"):
    """Path for <prefix>_<stamp>_<NN><suffix><ext>, NN from 00 up, free in EVERY layout. `stamp` is the file-name stamp
    ('2026-06-12_1702_AEST'), as unique_stamped_path() took it. The folder is NOT created: os.makedirs(os.path.dirname(p), exist_ok=True)
    when you write."""
    n = 0
    while True:
        name = "%s_%s_%02d%s%s" % (prefix, stamp, n, suffix, ext)
        if not find(root, name):
            return path_for(root, name)
        n += 1


def root_of(path):
    """The deepdive root holding `path` (a file, in either layout): the folder named 'deepdive' above it, else its own folder."""
    p = os.path.dirname(os.path.abspath(path))
    q = p
    for _ in range(3):
        if os.path.basename(q) == "deepdive":
            return q
        q = os.path.dirname(q)
    return p


def signature(root):
    """A cheap fingerprint of which charge files exist (changes when a day or a file appears), for caches."""
    total, newest = 0, 0
    try:
        kids = os.listdir(root)
    except OSError:
        return (0, 0)
    for nm in kids:
        p = os.path.join(root, nm)
        try:
            st = os.stat(p)
        except OSError:
            continue
        total += 1
        newest = max(newest, st.st_mtime)
        if DAY_RE.match(nm) and os.path.isdir(p):
            try:
                total += len(os.listdir(p))
            except OSError:
                pass
    return (total, int(newest))
