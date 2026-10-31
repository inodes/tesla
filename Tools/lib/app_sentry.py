"""app_sentry.py - saved Sentry events on the Archive drive, for the Parked pages of tesla_app.py (REQ-200).

TeslaCam/SentryClips/<event folder>/ holds one-minute clips per camera (<stamp>-<camera>.mp4), event.json (when it
triggered, the reason, an approximate place) and thumb.png. This reads those, remembers the answer in
<data>/teslacam_events/sentry_index.json (only folders not seen before are read again), and never changes anything on the drive.
"""
import json
import os
import re
import threading
import time

EVENTS_DIR = "teslacam_events"          # the kept lists of every drive's Saved and Sentry events: <data>/teslacam_events/<file>


def index_path(data_dir, name):
    """Where a kept event list lives: in the teslacam_events folder of the data folder. One still at the top of the data folder (where they were kept before) is moved in."""
    new = os.path.join(data_dir, EVENTS_DIR, name)
    old = os.path.join(data_dir, name)
    if not os.path.exists(new) and os.path.exists(old):
        try:
            os.makedirs(os.path.dirname(new), exist_ok=True)
            os.replace(old, new)
        except OSError:
            return old
    return new

import clip_clock as CC

CAMS = ("front", "back", "left_repeater", "right_repeater", "left_pillar", "right_pillar")
_CLIP = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-(%s)\.mp4$" % "|".join(CAMS))
_LOCK = threading.Lock()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _shown(e):
    """An event as the app uses it. The index keeps what the car wrote (its own clock, as the clip names are). Here the time is put in UTC and
    the car's clock turned into the local clock for display:
        t     UTC epoch seconds of the event (compare and sort with this)
        ts    local clock text, for display
        cts   the car's clock text as written (what the clip names are in, for the player)
        loc   {clip stamp: local clock text}"""
    try:
        t = CC.utc(e["ts"])
        lo = CC.place_offset(t, e.get("lat"), e.get("lon"))          # the clock of where it happened
        loc = {k: CC.local_iso(CC.utc(k), lo) for k in e.get("clips", {})}
    except ValueError:
        return dict(e, t=0, cts=e.get("ts", ""), loc={})
    return dict(e, t=t, cts=e["ts"], ts=CC.local_iso(t, lo), loc=loc)


class Sentry:
    def __init__(self, data_dir, index="sentry_index.json", folder="SentryClips"):
        self.dir, self.name = data_dir, index
        self.f = index_path(data_dir, index)
        self.folder = folder                    # SentryClips or SavedClips: both hold one folder per event
        self.mem = None
        self.memo = None                        # (teslacam, when, events, connected) of the last answer

    def _load(self):
        if self.mem is None:
            self.f = index_path(self.dir, self.name)
            try:
                with open(self.f, encoding="utf-8") as fh:
                    self.mem = json.load(fh)
            except (OSError, ValueError):
                self.mem = {}
        return self.mem

    def events(self, teslacam):
        """Every event folder (newest first). teslacam = a drive's TeslaCam folder, or None when it is not connected.
        Nothing the index has learnt is ever dropped: an event whose folder is no longer on the connected drive (deleted to make room, say) is marked
        gone (`gone` = when it was first found missing), stays in the kept file, and is left out of the answer while that drive is connected; with no
        drive connected, every kept event is answered. A folder that comes back is un-marked."""
        with _LOCK:
            idx = self._load()
            root = os.path.join(teslacam, self.folder) if teslacam else None
            try:
                dm = os.stat(root).st_mtime_ns if root else None
            except OSError:
                dm = None
            m = self.memo
            if m and m[0] == (teslacam, dm) and time.time() - m[1] < 30 and (dm is None or m[1] - dm / 1e9 > 2):
                return m[2], m[3]                   # the folder is as it was a moment ago (a list of drives asks once per drive): the same answer, no listing again
            if root and os.path.isdir(root):
                try:
                    names = sorted(n for n in os.listdir(root) if re.match(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$", n))
                except OSError:
                    names = []
                new = [n for n in names if n not in idx]
                for n in new:
                    r = self._read(os.path.join(root, n), n)
                    if r:
                        idx[n] = r
                changed = bool(new)
                if names:                           # a drive that listed nothing may be a glitch: nothing is marked from an empty listing
                    here, now = set(names), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    for n, e in idx.items():
                        if n in here and e.get("gone"):
                            del e["gone"]
                            changed = True
                        elif n not in here and not e.get("gone"):
                            e["gone"] = now
                            changed = True
                if changed:
                    try:
                        os.makedirs(os.path.dirname(self.f), exist_ok=True)
                        tmp = self.f + ".tmp"
                        with open(tmp, "w", encoding="utf-8") as fh:
                            json.dump(idx, fh, ensure_ascii=False)
                        os.replace(tmp, self.f)
                    except OSError:
                        pass
            connected = bool(root and os.path.isdir(root))
            shown = [_shown(e) for e in idx.values() if not (connected and e.get("gone"))]
            out = sorted(shown, key=lambda e: e["t"], reverse=True)
            self.memo = ((teslacam, dm), time.time(), out, connected)
            return out, connected

    def _read(self, path, name):
        try:
            files = os.listdir(path)
        except OSError:
            return None
        clips = {}
        for fn in files:
            m = _CLIP.match(fn)
            if m:
                clips.setdefault(m.group(1), []).append(m.group(2))
        ev = {}
        try:
            with open(os.path.join(path, "event.json"), encoding="utf-8") as fh:
                ev = json.load(fh)
        except (OSError, ValueError):
            pass
        ts = ev.get("timestamp") or name.replace("_", "T", 1)[:11] + name[11:].replace("-", ":")
        return dict(id=name, ts=ts, city=ev.get("city") or "", street=ev.get("street") or "", lat=_num(ev.get("est_lat")), lon=_num(ev.get("est_lon")),
                    reason=ev.get("reason") or "", cam=ev.get("camera") or "", clips={k: sorted(v, key=CAMS.index) for k, v in sorted(clips.items())},
                    thumb="thumb.png" in files, whole="event.mp4" in files)
