"""drive_roles.py - which TeslaCam drive is which, by what is ON it - never by its mount name.

The volume names (TESLADRIVE, TESLADRIVE 1, TESLADRIVE 2) are handed out in mount order and are interchangeable, so a
name says nothing. A drive is identified by the marker folder in its root (same markers as analyse_drives.py
get_drive_identifier): ARCHIVE_2TB / ARCHIVE = the 2 TB Archive (master), JOWUA_1TB / JOWUA = the JOWUA 1 TB drive,
TESLA_USB_* = the car's USB. With no marker the size decides (> 1600 GB Archive, 700-1600 GB JOWUA, 30-700 GB USB).

Roles: "A" Archive, "J" JOWUA, "U" Tesla USB, "?" unknown.

Standing rule (AGENTS.md 3.5.1): only the Archive is read by default. Every other drive is opt-in (--with-secondary or an
explicit --teslacam), because a drive read through a connected-folder share stays "in use" on the Mac until that folder is
disconnected.
"""
import glob
import os
import shutil

MARKERS = {"A": ("ARCHIVE_2TB", "ARCHIVE"), "J": ("JOWUA_1TB", "JOWUA"), "U": ("TESLA_USB_128GB", "TESLA_USB_256GB", "TESLA_USB")}
NAMES = {"A": "Archive", "J": "JOWUA", "U": "Tesla USB", "?": "unknown drive"}


def drive_root(path):
    path = os.path.expanduser(path).rstrip(os.sep)
    return os.path.dirname(path) if os.path.basename(path) == "TeslaCam" else path


def role_of(path):
    root = drive_root(path)
    for role, names in MARKERS.items():
        if any(os.path.isdir(os.path.join(root, n)) for n in names):
            return role
    try:
        gb = shutil.disk_usage(root).total / 1024 ** 3
    except OSError:
        return "?"
    return "A" if gb > 1600 else "J" if gb >= 700 else "U" if gb >= 30 else "?"


def discover():
    """[(TeslaCam dir, role)] of every TeslaCam folder visible here (Mac: /Volumes, Claude session: ~/mnt), Archive first."""
    seen, out = set(), []
    for pat in ("/Volumes/*/TeslaCam", os.path.expanduser("~/mnt/*/TeslaCam")):
        for p in sorted(glob.glob(pat)):
            rp = os.path.realpath(p)
            if os.path.isdir(p) and rp not in seen:
                seen.add(rp)
                out.append((p, role_of(p)))
    return sorted(out, key=lambda x: "AJU?".index(x[1]))


def teslacam_roots(include_secondary=False):
    return [p for p, r in discover() if r == "A" or include_secondary]


def find_root(role):
    for p, r in discover():
        if r == role:
            return p
    return None
