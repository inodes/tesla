"""minute_kind.py - is a Recent minute a drive or a park, from the clip itself (the dashcam first, no other record).

A clip that carries telemetry was written while driving; one with none was written while parked. A minute whose telemetry has not been read yet (the Metadata step has
not run) is told by looking at the first frames of its front camera file (dashcam_series.has_telemetry), once; the answer is kept. The six cameras of a minute stay together
and a file is never cut: the minute is the unit. run_cuts then lets a parked run stay parked, and a drive stay a drive, until the answer changes.

    Kinds(path).get(stamp)            -> True (drive), False (parked) or None (not looked at, or the file could not be read)
    probe(kinds, unread, path_of, limit, seconds) -> looks at up to `limit` minutes of `unread` (newest first) that path_of(stamp) can give a file for; returns how many it looked at
"""
import json
import os
import threading
import time

import dashcam_series as DS


class Kinds:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        try:
            with open(path, encoding="utf-8") as f:
                self.d = {k: bool(v) for k, v in json.load(f).items()}
        except (OSError, ValueError):
            self.d = {}

    def get(self, stamp):
        return self.d.get(stamp)

    def all(self):
        with self.lock:
            return dict(self.d)

    def put(self, stamp, drive):
        with self.lock:
            self.d[stamp] = bool(drive)

    def save(self):
        with self.lock:
            snap = dict(self.d)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(snap, f)
            os.replace(self.path + ".tmp", self.path)
        except OSError:
            pass


def probe(kinds, unread, path_of, limit=300, seconds=8.0):
    """Look at the minutes in `unread` that have no answer yet, newest first. path_of(stamp) -> the front camera file to look at, or None when it is on no connected drive."""
    t0, n = time.time(), 0
    for st in sorted((x for x in unread if kinds.get(x) is None), reverse=True):
        if n >= limit or time.time() - t0 > seconds:
            break
        p = path_of(st)
        if not p:
            continue
        r = DS.has_telemetry(p)
        if r is not None:
            kinds.put(st, r)
            n += 1
    if n:
        kinds.save()
    return n
