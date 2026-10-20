"""drive_roles.py - which TeslaCam drive is which: by its volume ID, never by its mount name or size.

A volume is a TeslaCam drive when it holds a TeslaCam folder shaped like the car's (drive_registry.structure_ok). Which one it is comes from
the registry in config.json (teslacam.drives, see drive_registry.py): the volume UUID -> a one-word label, what it is for (car / archive /
copy) and a short role key for the command lines (A, J, U, ...). The mount names (TESLADRIVE, TESLADRIVE 1, TESLADRIVE 2) are handed out in
the order the drives were plugged in and say nothing.

A drive that is not registered yet is "?" until someone says what it is (./manage_teslacam_drives.py). The old marker folders in a drive's
root (ARCHIVE_2TB / JOWUA_1TB / TESLA_USB_*) are still honoured: such a drive is registered from its marker the first time it is seen with a volume ID.

Standing rule (AGENTS.md 3.5.1): only the Archive is read by default. Every other drive is opt-in (--with-secondary or an
explicit --teslacam), because a drive read through a connected-folder share stays "in use" on the Mac until that folder is
disconnected.
"""
import glob
import os

import drive_registry as REG

MARKERS = {"A": ("ARCHIVE_2TB", "ARCHIVE"), "J": ("JOWUA_1TB", "JOWUA"), "U": ("TESLA_USB_128GB", "TESLA_USB_256GB", "TESLA_USB")}
_MARKER_AS = {"A": ("Archive", "archive"), "J": ("JOWUA", "car"), "U": ("TeslaUSB", "car")}
NAMES = {"A": "Archive", "J": "JOWUA", "U": "Tesla USB", "?": "unknown drive"}      # the labels of the registered drives are added by details()
PATTERNS = ("/Volumes/*/TeslaCam", os.path.expanduser("~/mnt/*/TeslaCam"))
drive_root = REG.drive_root


def _marker_role(root):
    for role, names in MARKERS.items():
        if any(os.path.isdir(os.path.join(root, n)) for n in names):
            return role
    return None


def role_of(path):
    """The role of the drive holding this TeslaCam folder ('?' when it has not been identified)."""
    root = drive_root(path)
    e = REG.lookup(REG.volume_id(root))
    if e:
        return e["role"]
    return _marker_role(root) or "?"


def details():
    """[dict(path, role, id, label, function, known, size_gb)] of every TeslaCam folder visible here with the car's structure, Archive first.
    Registers a drive that carries an old marker folder (the user's own statement of what it is) the first time its volume ID can be read.
    Two connected drives with the same volume ID (a sector-level clone) are both left unidentified ('?')."""
    seen, out = set(), []
    for pat in PATTERNS:
        for p in sorted(glob.glob(pat)):
            rp = os.path.realpath(p)
            if rp in seen or not REG.structure_ok(p):
                continue
            seen.add(rp)
            vid = REG.volume_id(p)
            e = REG.lookup(vid)
            mr = _marker_role(drive_root(p))
            if not e and mr and vid:
                try:
                    e = REG.register(vid, _MARKER_AS[mr][0], _MARKER_AS[mr][1], REG.size_gb(p), role=mr)
                except (OSError, ValueError):
                    e = None
            if e:
                NAMES[e["role"]] = e["label"]
                out.append(dict(path=p, role=e["role"], id=vid, label=e["label"], function=e["function"], known=True, size_gb=REG.size_gb(p)))
            elif mr:                                                # no volume ID to go on (a share, a sandbox): the marker is all there is
                out.append(dict(path=p, role=mr, id=vid, label=_MARKER_AS[mr][0], function=_MARKER_AS[mr][1], known=False, size_gb=REG.size_gb(p)))
            else:
                out.append(dict(path=p, role="?", id=vid, label="", function="", known=False, size_gb=REG.size_gb(p)))
    ids = [d["id"] for d in out if d["id"]]
    for d in out:
        if d["id"] and ids.count(d["id"]) > 1:
            d.update(role="?", label="", function="", known=False)
    return sorted(out, key=lambda d: ("A" != d["role"], "JU".index(d["role"]) if d["role"] in "JU" else 9, d["role"]))


def discover():
    """[(TeslaCam dir, role)] of every TeslaCam drive visible here (Mac: /Volumes, Claude session: ~/mnt), Archive first."""
    return [(d["path"], d["role"]) for d in details()]


def unidentified():
    """The connected TeslaCam drives nobody has said what they are for yet."""
    return [d for d in details() if d["role"] == "?"]


def teslacam_roots(include_secondary=False):
    return [p for p, r in discover() if r == "A" or include_secondary]


def find_root(role):
    for p, r in discover():
        if r == role:
            return p
    return None
