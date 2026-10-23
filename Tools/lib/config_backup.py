"""config_backup.py - dated copies of config.json, kept in .bak_archive/ (AGENTS.md 3.6).

config.json holds your settings, the drive registry and keys, and is not in git, so a bad write could not be undone. backup() copies it before
anything changes it (and the app does so at start-up) unless the newest copy is already identical. The newest KEEP copies stay in .bak_archive/;
older ones are moved, never deleted, to .bak_archive/config.json.older/ and each move is logged in .bak_archive/archive_log.md.
Names are config.json.bak-auto-<UTC time>, so they sort in time order.
"""
import datetime
import hashlib
import os
import shutil

KEEP = 20
PREFIX = "config.json.bak-auto-"          # AGENTS.md 3.6 names a copy <name>.bak-<ticket>; "auto" is the ticket of these


def folder(path):
    return os.path.join(os.path.dirname(os.path.abspath(path)), ".bak_archive")


def _sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def copies(path):
    """The copies kept (not the older ones), oldest first."""
    d = folder(path)
    try:
        return sorted(os.path.join(d, n) for n in os.listdir(d) if n.startswith(PREFIX))
    except OSError:
        return []


def backup(path, keep=KEEP):
    """Copy config.json to .bak_archive/config.json.bak-auto-<UTC time> unless the newest copy is identical. Returns the new copy's path or None."""
    if not os.path.isfile(path):
        return None
    have = copies(path)
    if have and _sha(have[-1]) == _sha(path):
        return None
    d = folder(path)
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, PREFIX + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S-%f"))      # to the microsecond: unique
    shutil.copy2(path, dst)
    os.chmod(dst, 0o600)                                       # it holds keys
    _move_old(path, keep)
    return dst


def _move_old(path, keep):
    old = copies(path)[:-keep] if keep > 0 else []
    if not old:
        return
    d = folder(path)
    older = os.path.join(d, "config.json.older")
    os.makedirs(older, exist_ok=True)
    log = os.path.join(d, "archive_log.md")
    new_log = not os.path.exists(log)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(log, "a", encoding="utf-8") as fh:
        if new_log:
            fh.write("# Archive log\n\nNothing here is deleted. Why each file was moved is written below.\n\n")
        for p in old:
            dst = os.path.join(older, os.path.basename(p))
            if os.path.exists(dst):                            # never replace an archived copy
                dst += "-again"
            shutil.move(p, dst)
            fh.write("- %s  %s moved to config.json.older/: more than %d newer copies of config.json exist\n" % (now, os.path.basename(p), keep))
