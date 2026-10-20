"""drive_registry.py - which TeslaCam volume is which, by its own ID; what each is for is asked once and kept in config.json.

A volume is a TeslaCam drive when it holds a TeslaCam folder with the car's structure (RecentClips / SavedClips / SentryClips and
YYYY-MM-DD_HH-MM-SS-<camera>.mp4 files). Its NAME is not used for anything: macOS hands out TESLADRIVE, TESLADRIVE 1, TESLADRIVE 2 in the order the
drives were plugged in, and the car formats every drive with the same name. A drive is recognised by its volume UUID (diskutil on macOS, findmnt
on Linux) and, the first time it is seen, the user says what it is for:

    car       goes in the car and records; the source of footage
    archive   the master copy that everything is imported to and judged against ("safe to delete" means the archive has what matters)
    copy      any other copy / backup / transfer drive

with a one-word name of their own (Archive, JOWUA, ...). That goes in config.json:

    "teslacam": {"drives": [{"id": "<volume UUID>", "label": "Archive", "function": "archive", "role": "A", "size_gb": 1862, "first_seen": "2026-10-20"}]}

`role` is the short stable key the commands use on their command lines (--drive A,J). The first archive is "A"; others take the first letter of
their label when free. Nothing is ever written to a drive.
"""
import datetime
import json
import os
import plistlib
import re
import shutil
import subprocess

FUNCTIONS = ("car", "archive", "copy")
SUBDIRS = ("RecentClips", "SavedClips", "SentryClips")
_CLIP = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-[a-z_]+\.mp4$")


def config_path():
    import tessie_api_common as T
    return T.resolve_config_path()


def _read():
    try:
        with open(config_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def drives():
    """The registered drives [dict(id, label, function, role, size_gb, first_seen)]."""
    d = _read().get("teslacam", {}).get("drives", [])
    return [x for x in d if isinstance(x, dict) and x.get("id") and x.get("role")]


def _write(entries):
    path = config_path()
    cfg = _read()
    cfg.setdefault("teslacam", {})["drives"] = entries
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def drive_root(path):
    path = os.path.expanduser(path).rstrip(os.sep)
    return os.path.dirname(path) if os.path.basename(path) == "TeslaCam" else path


def structure_ok(teslacam):
    """A TeslaCam folder shaped like the car's: one of the three clip folders, and any clip found has the car's file name."""
    if not os.path.isdir(teslacam) or not any(os.path.isdir(os.path.join(teslacam, s)) for s in SUBDIRS):
        return False
    try:
        for sub in SUBDIRS:
            p = os.path.join(teslacam, sub)
            if not os.path.isdir(p):
                continue
            for n in os.listdir(p)[:50]:
                q = os.path.join(p, n)
                if os.path.isdir(q):                          # SavedClips/<event>/ and RecentClips (flat, on the car) both hold the clips
                    for m in os.listdir(q)[:20]:
                        if m.endswith(".mp4"):
                            return bool(_CLIP.match(m))
                elif n.endswith(".mp4"):
                    return bool(_CLIP.match(n))
    except OSError:
        return False
    return True                                               # a drive with the folders and no clips yet


def _mount_point(path):
    p = os.path.realpath(path)
    while not os.path.ismount(p) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    return p


def volume_id(path):
    """The volume's UUID, or None when the system cannot say (a network share, a sandbox)."""
    mp = _mount_point(drive_root(path))
    try:
        if shutil.which("diskutil"):
            out = subprocess.run(["diskutil", "info", "-plist", mp], capture_output=True, timeout=20, stdin=subprocess.DEVNULL).stdout
            info = plistlib.loads(out)
            return str(info.get("VolumeUUID") or info.get("DiskUUID") or "").upper() or None
        if shutil.which("findmnt"):
            out = subprocess.run(["findmnt", "-n", "-o", "UUID", "-T", mp], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL).stdout.strip()
            return out.upper() or None
    except (OSError, subprocess.SubprocessError, ValueError, plistlib.InvalidFileException):
        return None
    return None


def lookup(vid):
    return next((d for d in drives() if d["id"] == vid), None) if vid else None


def _free_role(label, function, taken):
    cands = ["A"] if function == "archive" else []
    cands += [c for c in (re.sub(r"[^A-Za-z]", "", label or "")[:1].upper(),) if c]
    cands += list("BCDEFGHIJKLMNOPQRSTUVWXYZ")
    return next(c for c in cands if c not in taken and c != "?")


def register(vid, label, function, size_gb=None, role=None):
    """Remember a drive (or change what it is). Returns the entry. One word labels are kept as typed."""
    if function not in FUNCTIONS:
        raise ValueError("function must be one of %s" % ", ".join(FUNCTIONS))
    label = (label or "").strip().split()[0] if (label or "").strip() else function.capitalize()
    cur = drives()
    old = next((d for d in cur if d["id"] == vid), None)
    taken = {d["role"] for d in cur if d["id"] != vid}
    entry = dict(id=vid, label=label, function=function, role=role or (old["role"] if old else _free_role(label, function, taken)),
                 size_gb=size_gb if size_gb is not None else (old or {}).get("size_gb"), first_seen=(old or {}).get("first_seen") or datetime.date.today().isoformat())
    _write([d for d in cur if d["id"] != vid] + [entry])
    return entry


def forget(vid):
    cur = drives()
    _write([d for d in cur if d["id"] != vid])
    return len(cur) != len(drives())


def size_gb(path):
    try:
        return round(shutil.disk_usage(drive_root(path)).total / 1024 ** 3)
    except OSError:
        return None
