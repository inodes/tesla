"""clip_move.py - moving a clip's files from one drive to another without clobbering and without leaving shells.

A Saved or Sentry event is a FOLDER (event.json, thumb.png, event.mp4 and a video per minute and camera): moving it moves the whole folder, and the folder it leaves is removed, so no
shell with a lone thumb.png stays behind. A file is never replaced silently: when the destination already has one the two are compared (name, size, date and time, and a checksum) and the person
chooses what happens to every such file at once:

    skip        leave both where they are (the folder it is in stays too)
    overwrite   the file on the destination is replaced by this one, then this one is removed
    delete      the destination's file is kept and the copy on the drive it leaves is removed

    compare(src, dst, budget)        dict(name, src={size, mtime, crc?}, dst={...}, same_time, identical, checked)
    put(src, dst, choice, stop)      "moved" | "skipped" | "dropped"; one file is moved whole or not at all (see put)
    sweep_parts(folder)              removes the .part copies an interrupted move left beside the names
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


class Cancelled(OSError):
    """The move was stopped while a file was being copied: the half copy is gone and the file is still where it was."""


VERIFY = True                          # the copy is read back from the destination and its checksum compared with the video's before the video is removed


def _copy(src, part, stop=None):
    """Copies src to part in chunks, returns the checksum of what was read, and has the copy written to the drive (fsync) before it returns."""
    c = 0
    with open(src, "rb") as a, open(part, "wb") as b:
        while True:
            if stop and stop():
                raise Cancelled("stopped")
            buf = a.read(4 * 1024 ** 2)
            if not buf:
                break
            c = zlib.crc32(buf, c)
            b.write(buf)
        b.flush()
        os.fsync(b.fileno())
    return "%08x" % (c & 0xFFFFFFFF)


def _crc_from_drive(path, pause=None):
    """The checksum of a file read back from the drive (macOS: not from the cache that just took the write), else from the file as the system gives it."""
    fd = os.open(path, os.O_RDONLY)
    try:
        try:
            import fcntl
            fcntl.fcntl(fd, getattr(fcntl, "F_NOCACHE", 48), 1)
        except (ImportError, OSError):
            pass
        c = 0
        while True:
            if pause:
                pause()                                    # a page's request is answered first
            b = os.read(fd, 4 * 1024 ** 2)
            if not b:
                break
            c = zlib.crc32(b, c)
        return "%08x" % (c & 0xFFFFFFFF)
    finally:
        os.close(fd)


def put(src, dst, choice, stop=None, verify=None):
    """Put src at dst, whole or not at all. An existing dst is only replaced when the choice is "overwrite"; "delete" keeps it and removes src; anything else (or none) leaves both alone.
    The video is copied beside the name (dst.part), written to the drive, read back and checked against the original by size and checksum, put in place with one rename, and only
    then is the original removed. So whatever stops the move (a Stop, the drive pulled, a crash) leaves either the video on the old drive alone, or on both (the rename done, the
    original not yet removed: the next move sees two identical files and just removes the old one), and never a half file under the video's name; a .part left behind is
    replaced by the next copy of that file or removed by sweep_parts.
    Only the data and the modification time are copied, never the extended attributes: copying those (shutil.copy2) makes macOS write a ._name file beside every file it puts on an
    exFAT drive (thousands of them). The ._ file beside the source goes with the source."""
    if not os.path.lexists(src):
        raise FileNotFoundError(src)                    # nothing to move: said first, not hidden behind "already there" (the footage log then learns the drive no longer has it)
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
    try:
        crc = _copy(src, part, stop)
        os.utime(part, ns=(st.st_atime_ns, st.st_mtime_ns))
        if os.path.getsize(part) != st.st_size:
            raise OSError("the copy has a different size: the video was left where it was")
        if VERIFY if verify is None else verify:
            if _crc_from_drive(part) != crc:
                raise OSError("the copy read back from the drive differs from the video: the video was left where it was")
    except BaseException:
        _drop(part)
        _drop(sidecar(part))
        raise
    os.replace(part, dst)
    _drop(sidecar(part))
    if not had:
        _drop(sidecar(dst))
    os.remove(src)
    _drop(sidecar(src))
    return "moved"


def copy_in(src, dst, choice, stop=None):
    """The first half of a move, for a move that checks in the background: the video is copied to dst (beside the name as .part, written to the drive, size checked, one rename) and is NOT
    removed from src. Returns ("copied", crc) with the checksum of what was read, ("skipped", None) or ("dropped", None) as put() does. finish() follows, later: until then the video is on
    both drives, whole on each, and a cut-off at any moment leaves it so (the next move sees two identical files and removes the old one)."""
    if not os.path.exists(src):
        raise FileNotFoundError(src)                    # nothing to move: said first, not hidden behind "already there" (the footage log then learns the drive no longer has it)
    if os.path.lexists(dst):
        if choice == "delete":
            os.remove(src)
            _drop(sidecar(src))
            return "dropped", None
        if choice != "overwrite":
            return "skipped", None
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    had = os.path.lexists(sidecar(dst))
    part = dst + ".part"
    st = os.stat(src)
    try:
        crc = _copy(src, part, stop)
        os.utime(part, ns=(st.st_atime_ns, st.st_mtime_ns))
        if os.path.getsize(part) != st.st_size:
            raise OSError("the copy has a different size: the video was left where it was")
    except BaseException:
        _drop(part)
        _drop(sidecar(part))
        raise
    os.replace(part, dst)
    _drop(sidecar(part))
    if not had:
        _drop(sidecar(dst))
    return "copied", crc


def finish(src, dst, crc, pause=None):
    """The second half: the copy at dst is read back from the drive and its checksum compared with the one taken as it was copied. Equal: src is removed, True. Different: the bad copy is
    removed, src stays, OSError. src already gone: False (nothing to do)."""
    if not os.path.lexists(src):
        return False
    if _crc_from_drive(dst, pause) != crc:
        _drop(dst)
        raise OSError("the copy read back from the drive differs from the video: the video was left on the old drive")
    os.remove(src)
    _drop(sidecar(src))
    return True


def sweep_parts(folder):
    """Removes the .part copies under `folder` (what an interrupted move left): never a video, only half of one. Returns how many."""
    n = 0
    for dp, _dn, fn in os.walk(folder):
        for f in fn:
            if f.endswith(".mp4.part") or f.endswith(".part") and not junk(f):
                _drop(os.path.join(dp, f))
                n += 1
    return n


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
