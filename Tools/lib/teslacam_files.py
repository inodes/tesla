"""teslacam_files.py - every video file on a TeslaCam drive, as the car names it.

    TeslaCam/RecentClips/2026-10-11_16-12-14-front.mp4
    TeslaCam/SavedClips/<event>/2026-10-11_16-12-14-front.mp4          (the same for SentryClips)

A clip is one minute of one camera; the six cameras of one minute share the stamp (the car's local clock, YYYY-MM-DD_HH-MM-SS).
walk() lists them with their size and modification time; nothing is opened or changed.
"""
import json
import os
import re
import time

CLIP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-([a-z_]+)\.mp4$")
SOURCES = (("RecentClips", "recent"), ("SavedClips", "saved"), ("SentryClips", "sentry"))
ORDER = {"recent": 0, "saved": 1, "sentry": 2}


def _listing(path):
    try:
        with os.scandir(path) as it:
            return list(it)
    except OSError:
        return None


_CACHE = {}              # folder -> (its modification time in ns, [clip file names in it]): a folder that has not changed is not listed again
_SETTLE_S = 5            # a folder changed less than this long ago may change again within the same timestamp: not remembered yet
_DIRTY = [False]


def load_cache(path):
    """Takes the folder listings kept in `path` by save_cache() (a missing or damaged file gives nothing)."""
    try:
        with open(path, encoding="utf-8") as f:
            j = json.load(f)
        for k, v in j.items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], int) and isinstance(v[1], list):
                _CACHE.setdefault(k, (v[0], v[1]))
    except (OSError, ValueError):
        pass


def save_cache(path):
    """Keeps the folder listings, but only when a listing was added or dropped since the last save."""
    if not _DIRTY[0]:
        return
    _DIRTY[0] = False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({k: list(v) for k, v in dict(_CACHE).items()}, f, separators=(",", ":"))
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def clip_names(path, mt=None):
    """The clip file names (the car's names only) in a folder, or None when it cannot be read. Answered from the kept listing while the folder's own modification
    time is the one the listing was made at. `mt` is that time in ns when the caller has it already."""
    try:
        if mt is None:
            mt = os.stat(path).st_mtime_ns
    except OSError:
        return None
    hit = _CACHE.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        with os.scandir(path) as it:
            names = [e.name for e in it if CLIP_RE.match(e.name)]
    except OSError:
        return None
    if time.time() - mt / 1e9 > _SETTLE_S:
        _CACHE[path] = (mt, names)
        _DIRTY[0] = True
    return names


def _forget_unseen(root, seen):
    """After a whole drive was listed: drop the kept listings of folders under it that are no longer there."""
    pre = root.rstrip(os.sep) + os.sep
    for k in [k for k in dict(_CACHE) if k.startswith(pre) and k not in seen]:
        _CACHE.pop(k, None)
        _DIRTY[0] = True


def _walk_names(teslacam):
    """walk(stat=False) from the kept listings: only a folder that changed is listed again. Returns (files, ok)."""
    out, ok, seen_any, seen = [], True, False, set()

    def add(names, src, ev):
        for n in names:
            m = CLIP_RE.match(n)
            if m:
                out.append(dict(src=src, ev=ev, stamp=m.group(1), cam=m.group(2), size=0, mtime=0))

    for sub, src in SOURCES:
        top = os.path.join(teslacam, sub)
        if src == "recent":
            names = clip_names(top)
            if names is None:
                if os.path.isdir(top):
                    ok = False
                continue
            seen_any = True
            seen.add(top)
            add(names, src, "")
            continue
        ents = _listing(top)
        if ents is None:
            if os.path.isdir(top):
                ok = False
            continue
        seen_any = True
        for e in ents:
            if e.name.startswith("."):
                continue
            if e.is_dir(follow_symlinks=False):
                try:
                    mt = e.stat().st_mtime_ns
                except OSError:
                    ok = False
                    continue
                names = clip_names(e.path, mt)
                if names is None:
                    ok = False
                    continue
                seen.add(e.path)
                add(names, src, e.name)
            else:
                add([e.name], src, "")
    if ok and seen_any:
        _forget_unseen(teslacam, seen)
    return out, ok and seen_any


def walk(teslacam, stat=True):
    """(files, ok): files = [dict(src, ev, stamp, cam, size, mtime)] of every clip under the TeslaCam folder; ok is False when a
    top-level folder could not be read at all (so a missing file must not be taken as deleted). With stat=False, size and mtime are 0
    (no stat per file on the drive), and a folder that has not changed since it was last listed is answered from the kept listing (clip_names)."""
    if not stat:
        return _walk_names(teslacam)
    out, ok = [], True
    seen_any = False
    for sub, src in SOURCES:
        top = os.path.join(teslacam, sub)
        ents = _listing(top)
        if ents is None:
            if os.path.isdir(top):
                ok = False
            continue
        seen_any = True
        for e in ents:
            if e.name.startswith("."):
                continue
            if src == "recent":
                _add(out, e, src, "", stat)
            elif e.is_dir(follow_symlinks=False):
                inner = _listing(e.path)
                if inner is None:
                    ok = False
                    continue
                for f in inner:
                    _add(out, f, src, e.name, stat)
            else:
                _add(out, e, src, "", stat)
    return out, ok and seen_any


def _add(out, e, src, ev, stat=True):
    m = CLIP_RE.match(e.name)
    if not m:
        return
    if stat:
        try:
            st = e.stat()
        except OSError:
            return
        size, mt = st.st_size, int(st.st_mtime)
    else:
        size = mt = 0
    out.append(dict(src=src, ev=ev, stamp=m.group(1), cam=m.group(2), size=size, mtime=mt))


def key(f):
    """Identity of a file inside one drive: 'recent||2026-10-11_16-12-14-front', 'saved|<event>|...'."""
    return "%s|%s|%s-%s" % (f["src"], f["ev"], f["stamp"], f["cam"])


def front_path(teslacam, f):
    sub = {"recent": "RecentClips", "saved": "SavedClips", "sentry": "SentryClips"}[f["src"]]
    return os.path.join(teslacam, sub, f["ev"], "%s-%s.mp4" % (f["stamp"], f["cam"])) if f["ev"] else os.path.join(teslacam, sub, "%s-%s.mp4" % (f["stamp"], f["cam"]))
