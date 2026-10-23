"""drive_eject.py - eject a TeslaCam drive from the app (macOS `diskutil eject`), saying why when it is held open.

  eject(teslacam_folder) -> dict(ok, text)
"""
import os
import shutil
import subprocess
import sys

import drive_registry as REG


def eject(teslacam, platform=None, run=subprocess.run, which=shutil.which):
    platform = platform or sys.platform
    if platform != "darwin" or not which("diskutil"):
        return dict(ok=False, text="Ejecting from here only works on a Mac.")
    mp = REG._mount_point(REG.drive_root(teslacam))
    if not mp.startswith("/Volumes/"):
        return dict(ok=False, text="This drive is not mounted under /Volumes, so it was not touched.")
    name = os.path.basename(mp)
    try:
        r = run(["diskutil", "eject", mp], capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        return dict(ok=False, text="%s could not be ejected: %s" % (name, e))
    if r.returncode == 0:
        return dict(ok=True, text="%s ejected. It is safe to unplug." % name)
    why = ((r.stderr or r.stdout or "").strip().splitlines() or ["it did not eject"])[-1]
    users = []
    if which("lsof"):
        try:
            o = run(["lsof", "+f", "--", mp], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL).stdout.splitlines()[1:]
            users = sorted({line.split()[0] for line in o if line.split()})
        except (OSError, subprocess.SubprocessError):
            pass
    return dict(ok=False, text="%s was not ejected: %s%s" % (name, why, ". Open in: " + ", ".join(users[:6]) if users else ""))
