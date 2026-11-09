"""clip_move.py - moving a clip's files from one drive to another without clobbering and without leaving shells.

A Saved or Sentry event is a FOLDER (event.json, thumb.png, event.mp4 and a video per minute and camera): moving it moves the whole folder, and the folder it leaves is removed, so no
shell with a lone thumb.png stays behind. A file is never replaced silently: when the destination already has one the two are compared (name, size, date and time, and a checksum) and the person
chooses what happens to every such file at once:

    skip        leave both where they are (the folder it is in stays too)
    overwrite   the file on the destination is replaced by this one, then this one is removed
    delete      the destination's file is kept and the copy on the drive it leaves is removed

    compare(src, dst, budget)        dict(name, src={size, mtime, crc?}, dst={...}, same_time, identical, checked)
    put(src, dst, choice)            "moved" | "skipped" | "dropped"; the copy is made beside the name (.part), checked by size, then put in place
    leftovers(dir)                   the names in an event folder that are not minute videos (event.json, thumb.png, event.mp4, ...)
    move_event_dir(src, dst, choice) moves what is left in a folder whose minutes have all gone, removes the folder; dict(moved, skipped, dropped, removed)
"""
import os
import re
import shutil
import time
import zlib

MINUTE = re.compile(r"^\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d-[a-z_]+\.mp4$")
CRC_FILE_MAX = 400 * 1024 ** 2          # a bigger file is compared by size and time only
CRC_BUDGET = 1536 * 1024 ** 2           # the most that is read for checksums in one check
CHOICES = ("skip", "overwrite", "delete")


def junk(name):
    return name == ".DS_Store" or name.startswith("._")


def crc32(path):
    c = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(4 * 1024 ** 2)
            if not b:
                break
            c = zlib.crc32(b, c)
    return "%08x" % (c & 0xFFFFFFFF)


def _info(path):
    st = os.stat(path)
    return dict(size=st.st_size, mtime=int(st.st_mtime))


class Budget:
    def __init__(self, left=CRC_BUDGET):
        self.left = left


def compare(src, dst, budget=None):
    """How the two files differ. The checksum is read when both are the same size and small enough (within the budget); otherwise identical rests on size and time."""
    a, b = _info(src), _info(dst)
    same_time = abs(a["mtime"] - b["mtime"]) <= 2
    out = dict(name=os.path.basename(src), src=a, dst=b, same_time=same_time, checked=False)
    if a["size"] != b["size"]:
        out["identical"] = False
        return out
    budget = budget if budget is not None else Budget()
    if a["size"] <= CRC_FILE_MAX and budget.left >= 2 * a["size"]:
        budget.left -= 2 * a["size"]
        a["crc"], b["crc"] = crc32(src), crc32(dst)
        out["checked"] = True
        out["identical"] = a["crc"] == b["crc"]
    else:
        out["identical"] = same_time
    return out


def sidecar(path):
    """The AppleDouble file macOS keeps beside a file on an exFAT / FAT drive to hold its extended attributes (._name)."""
    return os.path.join(os.path.dirname(path), "._" + os.path.basename(path))


def _drop(path):
    try:
        os.remove(path)
    except OSError:
        pass


def put(src, dst, choice):
    """Put src at dst. An existing dst is only replaced when the choice is "overwrite"; "delete" keeps it and removes src; anything else (or none) leaves both alone.
    Only the data and the modification time are copied, never the extended attributes: copying those (shutil.copy2) makes macOS write a ._name file beside every file it puts on an
    exFAT drive (thousands of them). The ._ file beside the source goes with the source."""
    if os.path.lexists(dst):
        if choice == "delete":
            os.remove(src)
            _drop(sidecar(src))
            return "dropped"
        if choice != "overwrite":
            return "skipped"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    had = os.path.lexists(sidecar(dst))                  # one that was there before is not ours to remove
    part = dst + ".part"
    st = os.stat(src)
    shutil.copyfile(src, part)
    os.utime(part, ns=(st.st_atime_ns, st.st_mtime_ns))
    if os.path.getsize(part) != st.st_size:
        os.remove(part)
        _drop(sidecar(part))
        raise OSError("the copy has a different size")
    os.replace(part, dst)
    _drop(sidecar(part))
    if not had:
        _drop(sidecar(dst))
    os.remove(src)
    _drop(sidecar(src))
    return "moved"


def sweep_junk(top, seconds=20.0, stop=None):
    """Removes the ._ files and .DS_Store files under `top` (a drive's SavedClips / SentryClips / RecentClips folder): macOS metadata, never video. Stops after `seconds` (a drive holds tens of
    thousands and each removal is slow): (removed, finished)."""
    t0, n = time.time(), 0
    for dp, _dn, fn in os.walk(top):
        for f in fn:
            if junk(f):
                _drop(os.path.join(dp, f))
                n += 1
        if time.time() - t0 > seconds or (stop and stop()):
            return n, False
    return n, True


def leftovers(folder):
    try:
        return sorted(n for n in os.listdir(folder) if not MINUTE.match(n) and not junk(n) and os.path.isfile(os.path.join(folder, n)))
    except OSError:
        return []


def move_event_dir(src, dst, choice):
    """The files left in an event folder once its minutes have gone (event.json, thumb.png, event.mp4 ...) go to the same folder on the other drive, and the folder is removed. A folder that
    still holds a minute video is left alone (something was skipped or failed there)."""
    out = dict(moved=0, skipped=0, dropped=0, removed=False)
    try:
        names = os.listdir(src)
    except OSError:
        return out
    if any(MINUTE.match(n) for n in names):
        return out
    for n in names:
        p = os.path.join(src, n)
        if junk(n):
            try:
                os.remove(p)
            except OSError:
                pass
            continue
        if os.path.isfile(p):
            out[put(p, os.path.join(dst, n), choice)] += 1
    try:
        os.rmdir(src)
        out["removed"] = True
    except OSError:
        pass
    return out


def tidy_dir(src, dst):
    """A shell (an event folder with no minute video: event.json, thumb.png ... left by an earlier move) whose event is kept, with its video, in `dst`: what is not there yet goes there, what is
    there already and the same is removed from the shell, what is there and different stays (nothing is replaced). The folder goes when it is empty. dict(moved, same, differs, removed)."""
    out = dict(moved=0, same=0, differs=0, removed=False)
    try:
        names = os.listdir(src)
    except OSError:
        return out
    if any(MINUTE.match(n) for n in names):
        return out
    for n in names:
        p = os.path.join(src, n)
        if junk(n):
            try:
                os.remove(p)
            except OSError:
                pass
            continue
        if not os.path.isfile(p):
            continue
        d = os.path.join(dst, n)
        if not os.path.lexists(d):
            put(p, d, None)
            out["moved"] += 1
        elif compare(p, d)["identical"]:
            os.remove(p)
            out["same"] += 1
        else:
            out["differs"] += 1
    try:
        os.rmdir(src)
        out["removed"] = True
    except OSError:
        pass
    return out
