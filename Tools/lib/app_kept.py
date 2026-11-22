"""app_kept.py - lists the pages ask for, answered at once from what was kept and refreshed behind.

Building the Activity, Places, Chargers, Alerts... lists means reading and merging thousands of small files; that is seconds, and it was paid by the page that
asked first after every start. Here the last answer is kept in memory and in a folder on this computer (not in the data folder, which iCloud shares between the
Macs), so a page gets it in milliseconds, also right after the app starts. A refresh runs in the background (at most one per list, at most every `gap` seconds);
the answer is marked stale while it runs and the page asks again a few seconds later. A page that must see the newest (after a sync or an edit) asks with fresh=<the
time of the edit>: an answer built since then is given at once; otherwise one is built, waited for at most FRESH_WAIT_S, and the old answer is given (stale) if it
takes longer. It used to rebuild on every ask for a minute after any edit, each page waiting for it (seconds to go back to Drives after saving a place).

    Kept(folder, version).get(name, build, fresh=False) -> (json bytes, stale)       fresh: False, True (now) or the time (epoch s) the answer must be newer than
"""
import glob
import json
import os
import re
import threading
import time


class Kept:
    def __init__(self, folder, version="0", gap=8.0):
        self.dir, self.version, self.gap = folder, str(version), gap
        self.mem = {}                                   # name -> [bytes, checked_at, refreshing]
        self.lock = threading.Lock()
        self.locks = {}

    def _path(self, name):
        return os.path.join(self.dir, "api_%s_%s.json" % (name, self.version))

    def _make(self, name, build):
        raw = json.dumps(build(), separators=(",", ":")).encode()
        with self.lock:
            ent = self.mem.get(name)
            changed = ent is None or ent[0] != raw
            self.mem[name] = [raw, time.time(), False]
        if changed:
            try:
                os.makedirs(self.dir, exist_ok=True)
                tmp = self._path(name) + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(raw)
                os.replace(tmp, self._path(name))
                for old in glob.glob(os.path.join(self.dir, "api_%s_*.json" % name)):      # the copies kept by earlier versions of the program are not needed once this one has its own
                    if old != self._path(name) and re.fullmatch(r"api_%s_[\d.]+\.json" % re.escape(name), os.path.basename(old)):
                        try:
                            os.remove(old)
                        except OSError:
                            pass
            except OSError:
                pass
        return raw

    def _fresh(self, name, build, since, lk, ent):
        """An answer newer than `since`: built now (one build at a time), waited for at most FRESH_WAIT_S; else the old one, stale (the page asks again)."""
        done = threading.Event()

        def run():
            try:
                with lk:
                    with self.lock:
                        e2 = self.mem.get(name)
                    if e2 is None or e2[1] < since:
                        self._make(name, build)
            except Exception:
                pass
            finally:
                done.set()
        threading.Thread(target=run, daemon=True).start()
        done.wait(self.FRESH_WAIT_S)
        with self.lock:
            e3 = self.mem.get(name) or ent
            if e3[1] >= since:
                return e3[0], False
            return e3[0], True

    def _refresh(self, name, build):
        try:
            self._make(name, build)
        except Exception:
            with self.lock:
                if name in self.mem:
                    self.mem[name][1], self.mem[name][2] = time.time(), False

    FRESH_WAIT_S = 3.0

    def get(self, name, build, fresh=False):
        since = None if fresh is False or fresh is None else (time.time() if fresh is True else float(fresh))
        fresh = since is not None
        with self.lock:
            ent = self.mem.get(name)
            lk = self.locks.setdefault(name, threading.Lock())
            if ent is not None and fresh and ent[1] >= since:
                return ent[0], bool(ent[2])                                   # built since the edit: nothing to wait for
        if ent is not None and fresh:
            return self._fresh(name, build, since, lk, ent)
        if ent is None and not fresh:
            try:
                path = self._path(name)
                if not os.path.isfile(path):                                  # kept by an earlier version of the program: shown now, built again at once behind it
                    older = sorted(glob.glob(os.path.join(self.dir, "api_%s_*.json" % name)), key=os.path.getmtime)
                    older = [p for p in older if re.fullmatch(r"api_%s_[\d.]+\.json" % re.escape(name), os.path.basename(p))]
                    if older:
                        path = older[-1]
                with open(path, "rb") as f:
                    raw = f.read()
                json.loads(raw)
                with self.lock:
                    ent = self.mem.setdefault(name, [raw, 0.0, False])      # from an earlier run: shown now, checked at once
            except (OSError, ValueError):
                ent = None
        if ent is None or fresh:
            with lk:                                                          # one build at a time; a second asker waits for it
                with self.lock:
                    ent2 = self.mem.get(name)
                    if ent2 is not None and fresh and time.time() - ent2[1] < 1.0:
                        return ent2[0], False                                 # just built for someone else
                return self._make(name, build), False
        start = False
        with self.lock:
            if not ent[2] and time.time() - ent[1] >= self.gap:
                ent[2] = start = True
            stale = ent[2]
        if start:
            threading.Thread(target=self._refresh, args=(name, build), daemon=True).start()
        return ent[0], stale
