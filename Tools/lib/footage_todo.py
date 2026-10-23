"""footage_todo.py - what each connected TeslaCam drive still needs: files not in the footage log yet, minutes without telemetry, clips without stills.

The page shows it next to each drive so a button is only offered when there is something to do. Working it out walks the drives (slow), so it runs in
one background thread and the answer is kept (in the local cache folder) against a signature of each drive and of what has been kept from it; it is
worked out again only when that signature changes (a drive changed, a job finished) and nothing is reading a drive.

  Todo(cache_file, data_dir, tc, fg, roots_fn, idle_fn).snapshot() -> dict(checking, drives={role: dict(log_new, log_gone, telemetry, stills, at)})
"""
import json
import os
import threading
import time

import teslacam_files as TF
import teslacam_ledger as TLG

FOLDERS = ("RecentClips", "SavedClips", "SentryClips")


def _mtime(root):
    best = 0.0
    for f in FOLDERS:
        try:
            best = max(best, os.path.getmtime(os.path.join(root, f)))
        except OSError:
            pass
    return best


class Todo:
    def __init__(self, cache_file, data_dir, tc, fg, roots_fn, idle_fn=lambda: True):
        self.cache_file, self.dir, self.tc, self.fg, self.roots_fn, self.idle_fn = cache_file, data_dir, tc, fg, roots_fn, idle_fn
        self._lock = threading.Lock()
        self._thread = None
        self._mem = self._load()
        self.error = ""

    def _load(self):
        try:
            with open(self.cache_file, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            tmp = self.cache_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._mem, f)
            os.replace(tmp, self.cache_file)
        except OSError:
            pass

    def _sig(self, roots):
        """What the answer was worked out from: each drive's newest top-level change, when it was last listed, and how much telemetry and how many stills are kept."""
        scans = TLG.Ledger(self.dir).scans()
        try:
            tele = len(self.tc._tl("A").stamps())
        except Exception:
            tele = -1
        try:
            stills = len(self.fg.stills.stamps())
        except Exception:
            stills = -1
        return [[r, round(_mtime(p)), (scans.get(r) or {}).get("at") or ""] for r, p in sorted(roots.items())] + [tele, stills]

    def snapshot(self):
        roots = {r: p for r, p in (self.roots_fn() or {}).items() if p}
        sig = self._sig(roots) if roots else None
        cur = self._mem.get("sig")
        working = bool(self._thread and self._thread.is_alive())
        stale = bool(roots) and sig != cur
        if stale and not working and self.idle_fn():
            self._thread = threading.Thread(target=self._work, args=(roots, sig), daemon=True)
            self._thread.start()
            working = True
        drives = {}
        for r in roots:
            d = (self._mem.get("drives") or {}).get(r)
            drives[r] = dict(d) if d else None
        return dict(checking=working or stale, drives=drives, error=self.error)

    def _work(self, roots, sig):
        try:
            with self._lock:
                led = TLG.Ledger(self.dir)
                out = {}
                for r, p in roots.items():
                    files, ok = TF.walk(p)
                    present = {}
                    for f in files:
                        present.setdefault(f["stamp"][:10], set()).add(TF.key(f))
                    new = gone = 0
                    for day, keys in present.items():
                        d = led.day(day)
                        for k in keys:
                            e = d.get(k)
                            r_ = e["drives"].get(r) if e else None
                            if r_ is None or r_.get("gone"):
                                new += 1
                    if ok:
                        for day in led.days():
                            here = present.get(day, set())
                            for k, e in led.day(day).items():
                                x = e["drives"].get(r)
                                if x is not None and x.get("gone") is None and k not in here:
                                    gone += 1
                    out[r] = dict(log_new=new, log_gone=gone, telemetry=0, stills=0, at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                for d in self.tc.scan_report(roots)["drives"]:          # with every connected drive together: a minute two drives hold is read once
                    out[d["role"]]["telemetry"] = d["to_read"]
                rep = self.fg.stills_report(roots)
                for d in rep["drives"]:
                    out[d["role"]]["stills"] = d["to_make"]
                self._mem = dict(sig=sig, drives=out)
                self.error = ""
                self._save()
        except Exception as e:                                  # a drive pulled out in the middle: say so and try again at the next look
            self.error = "%s: %s" % (type(e).__name__, e)
            self._mem = dict(self._mem, sig=None)
