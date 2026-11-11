"""stat_memo.py - what was read from a file or a folder, kept until the file or folder changes.

The app's lists are built from thousands of small files (an info.json per event, a summary per drive, a zip per minute of stills) in a data folder that iCloud serves:
opening and listing them again for every build costs seconds, while asking each "have you changed?" (one stat) costs next to nothing. So:

    file(path, load, default=None)   -> load(path), from memory while the file's size and modification time are unchanged; `default` when it is missing or load() fails
    listing(path)                    -> {name: size in bytes (None for a folder)} of a folder, from memory while the folder's modification time is unchanged
    names(path)                      -> {name: is a folder}: the same without the sizes (no stat of each entry), for folders that are only walked through
    exists(path)                     -> True when the file is there
    forget(path=None)                -> drop what is kept for one path (or everything): for code that just wrote there and wants no doubt

A folder's time changes when something in it is added, removed or replaced (written beside and renamed in, as the app does), not when a file is rewritten in place:
its listing then still has the old size, which only matters to the sizes shown. What changed within RACY_S of being looked at is read again next time (the clock a
file system stamps with is coarser than the changes can be), so a second change in the same instant is never missed. The answers are shared: do not change them
(file() callers that need to should copy).
"""
import os
import threading
import time

RACY_S = 2.0
MAX_ENTRIES = 200000
_FILES = {}             # path -> (mtime_ns, size, looked at, value)
_DIRS = {}              # path -> (mtime_ns, looked at, {name: size})
_NAMES = {}             # path -> (mtime_ns, looked at, {name: is a folder})
_LOCK = threading.Lock()
_MISSING = object()


def _settled(mtime_ns, looked):
    return looked - mtime_ns / 1e9 > RACY_S


def file(path, load, default=None):
    try:
        st = os.stat(path)
    except OSError:
        _FILES.pop(path, None)
        return default
    hit = _FILES.get(path)
    if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size and _settled(hit[0], hit[2]):
        return hit[3]
    try:
        val = load(path)
    except (OSError, ValueError, KeyError, TypeError):
        return default
    with _LOCK:
        if len(_FILES) >= MAX_ENTRIES:
            _FILES.clear()
        _FILES[path] = (st.st_mtime_ns, st.st_size, time.time(), val)
    return val


def listing(path):
    try:
        st = os.stat(path)
    except OSError:
        _DIRS.pop(path, None)
        return {}
    hit = _DIRS.get(path)
    if hit is not None and hit[0] == st.st_mtime_ns and _settled(hit[0], hit[1]):
        return hit[2]
    out = {}
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    out[e.name] = None if e.is_dir() else e.stat().st_size
                except OSError:
                    out[e.name] = 0
    except OSError:
        return {}
    with _LOCK:
        if len(_DIRS) >= MAX_ENTRIES:
            _DIRS.clear()
        _DIRS[path] = (st.st_mtime_ns, time.time(), out)
    return out


def names(path):
    """{name: True for a folder, False for a file} of a folder, from memory while the folder's modification time is unchanged. Nothing is asked of the entries themselves
    (no size), so a folder of thousands costs one listing: for folders only walked through."""
    try:
        st = os.stat(path)
    except OSError:
        _NAMES.pop(path, None)
        return {}
    hit = _NAMES.get(path)
    if hit is not None and hit[0] == st.st_mtime_ns and _settled(hit[0], hit[1]):
        return hit[2]
    out = {}
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    out[e.name] = e.is_dir()
                except OSError:
                    out[e.name] = False
    except OSError:
        return {}
    with _LOCK:
        if len(_NAMES) >= MAX_ENTRIES:
            _NAMES.clear()
        _NAMES[path] = (st.st_mtime_ns, time.time(), out)
    return out


def exists(path):
    """True when the file is there, from memory while its size and time are unchanged (one stat, as os.path.isfile, but remembered alongside the rest)."""
    return bool(file(path, _there, False))


def _there(path):
    return True


def forget(path=None):
    with _LOCK:
        if path is None:
            _FILES.clear()
            _DIRS.clear()
            _NAMES.clear()
        else:
            _FILES.pop(path, None)
            _DIRS.pop(path, None)
            _NAMES.pop(path, None)
