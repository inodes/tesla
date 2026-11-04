"""minute_kind.py - what a Recent minute says about the car, from the clip itself (the dashcam first, no other record).

"go" = telemetry with the car moving (3 km/h or more), "pk" = telemetry with the car standing still (Park, or a gear touched; awake and stationary), "" = no telemetry (parked). A minute whose telemetry has not been
read yet (the Metadata step has not run) is told by walking its front camera file (dashcam_series.telemetry_kind), once; the answer is kept. The six cameras of a minute stay together
and a file is never cut: the minute is the unit. run_cuts turns the minutes into drives and parks.

    Kinds(path).get(stamp)            -> "go", "pk", "" or None (not looked at, or the file could not be read)
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
                self.d = {k: v for k, v in json.load(f).items() if v in ("go", "pk", "")}
        except (OSError, ValueError):
            self.d = {}

    def get(self, stamp):
        return self.d.get(stamp)

    def all(self):
        with self.lock:
            return dict(self.d)

    def put(self, stamp, kind):
        with self.lock:
            self.d[stamp] = kind

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
        r = DS.telemetry_kind(p)
        if r is not None:
            kinds.put(st, r)
            n += 1
    if n:
        kinds.save()
    return n


_BG = {"on": False}


def probe_bg(kinds, unread, path_of, workers=4, save_every=24):
    """Reads every minute of `unread` that has no answer yet, in the background, several at once (newest first), and keeps the answers as it goes (the file changing is what makes the lists rebuild
    with what has been read). One job at a time; a call while one is running does nothing. Returns True when a job was started."""
    todo = [x for x in sorted(unread, reverse=True) if kinds.get(x) is None and path_of(x)]
    if not todo or _BG["on"]:
        return False
    _BG["on"] = True

    def one(st):
        try:
            return st, DS.telemetry_kind(path_of(st))
        except Exception:
            return st, None

    def job():
        from concurrent.futures import ThreadPoolExecutor
        try:
            n = 0
            with ThreadPoolExecutor(workers) as ex:
                for st, r in ex.map(one, todo):
                    if r is not None:
                        kinds.put(st, r)
                        n += 1
                        if n % save_every == 0:
                            kinds.save()
            kinds.save()
        finally:
            _BG["on"] = False
    threading.Thread(target=job, daemon=True).start()
    return True
