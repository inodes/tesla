"""teslacam_files.py - every video file on a TeslaCam drive, as the car names it.

    TeslaCam/RecentClips/2026-10-11_16-12-14-front.mp4
    TeslaCam/SavedClips/<event>/2026-10-11_16-12-14-front.mp4          (the same for SentryClips)

A clip is one minute of one camera; the six cameras of one minute share the stamp (the car's local clock, YYYY-MM-DD_HH-MM-SS).
walk() lists them with their size and modification time; nothing is opened or changed.
"""
import os
import re

CLIP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-([a-z_]+)\.mp4$")
SOURCES = (("RecentClips", "recent"), ("SavedClips", "saved"), ("SentryClips", "sentry"))
ORDER = {"recent": 0, "saved": 1, "sentry": 2}


def _listing(path):
    try:
        with os.scandir(path) as it:
            return list(it)
    except OSError:
        return None


def walk(teslacam):
    """(files, ok): files = [dict(src, ev, stamp, cam, size, mtime)] of every clip under the TeslaCam folder; ok is False when a
    top-level folder could not be read at all (so a missing file must not be taken as deleted)."""
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
            if e.is_dir(follow_symlinks=False):
                if src == "recent":
                    continue
                inner = _listing(e.path)
                if inner is None:
                    ok = False
                    continue
                for f in inner:
                    _add(out, f, src, e.name)
            else:
                _add(out, e, src, "")
    return out, ok and seen_any


def _add(out, e, src, ev):
    m = CLIP_RE.match(e.name)
    if not m:
        return
    try:
        st = e.stat()
    except OSError:
        return
    out.append(dict(src=src, ev=ev, stamp=m.group(1), cam=m.group(2), size=st.st_size, mtime=int(st.st_mtime)))


def key(f):
    """Identity of a file inside one drive: 'recent||2026-10-11_16-12-14-front', 'saved|<event>|...'."""
    return "%s|%s|%s-%s" % (f["src"], f["ev"], f["stamp"], f["cam"])


def front_path(teslacam, f):
    sub = {"recent": "RecentClips", "saved": "SavedClips", "sentry": "SentryClips"}[f["src"]]
    return os.path.join(teslacam, sub, f["ev"], "%s-%s.mp4" % (f["stamp"], f["cam"])) if f["ev"] else os.path.join(teslacam, sub, "%s-%s.mp4" % (f["stamp"], f["cam"]))
