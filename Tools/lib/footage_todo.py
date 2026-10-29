"""footage_todo.py - what each connected TeslaCam drive still needs: files not in the footage log yet, minutes without telemetry, clips without stills, minutes whose signs were not read.

The page shows it next to each drive so a button is only offered when there is something to do. Working it out walks the drives (slow), so it runs in
one background thread and the answer is kept (in the local cache folder) against a signature of each drive and of what has been kept from it; it is
worked out again only when that signature changes (a drive changed, a job finished) and nothing is reading a drive.

  Todo(cache_file, data_dir, tc, fg, roots_fn, idle_fn).snapshot() -> dict(checking, progress, drives={role: dict(log_new, log_gone, telemetry, stills, signs, at)}, error)
progress = None, or dict(done, total, step, role) while it works: one step per drive listed, then the telemetry count, the stills count, then the signs count
"""
import json
import os
import threading
import time

import app_config
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


def _mtime_ns(root):
    out = []
    for f in FOLDERS:
        try:
            out.append(os.stat(os.path.join(root, f)).st_mtime_ns)
        except OSError:
            out.append(0)
    return tuple(out)


class Todo:
    def __init__(self, cache_file, data_dir, tc, fg, roots_fn, idle_fn=lambda: True):
        self.cache_file, self.dir, self.tc, self.fg, self.roots_fn, self.idle_fn = cache_file, data_dir, tc, fg, roots_fn, idle_fn
        self._lock = threading.Lock()
        self._thread = None
        self._mem = self._load()
        self._lcache = os.path.join(os.path.dirname(cache_file), "folder_listings.json")
        TF.set_cache_file(self._lcache)     # the folders listed in an earlier run: a folder not changed since is not listed again (the footage log scan uses them too)
        self._walked = {}               # role -> (drive_key, files, ok, log_new, log_gone, days_new), kept while the drive's folders and scan time are unchanged
        self.error = ""
        self._err_at = 0.0
        self._prog = None

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
        return [[r, round(_mtime(p)), (scans.get(r) or {}).get("at") or ""] for r, p in sorted(roots.items())] + [tele, stills, self._signs_token()]

    def _signs_token(self):
        """How much of the signs has been read: the newest change among the day files the sign review keeps (a file changes as minutes are added to it)."""
        try:
            base = self.tc._rv().base
            return round(sum(os.path.getmtime(os.path.join(base, f)) for f in os.listdir(base) if f.endswith(".json")))
        except Exception:
            return -1

    def _signs_left(self, walked):
        """{role: minutes whose signs were not read yet, or not had the second look}: the minutes with a front clip on that drive, read for telemetry, driven fast enough."""
        rv, store = self.tc._rv(), self.tc._tl("A")
        done = rv.scanned()
        again = rv.unrefined() if hasattr(rv, "unrefined") else set()
        min_kmh = float((app_config.get("review") or {}).get("min_kmh", 30.0))
        out = {}
        for r, (files, ok) in walked.items():
            n = 0
            for st in {f["stamp"] for f in files if f["cam"] == "front"}:
                if st in done:
                    n += st in again
                    continue
                sm = store.summary(st)
                if isinstance(sm, dict) and sm.get("vmax", 0) >= min_kmh:
                    n += 1
            out[r] = n
        return out

    def snapshot(self):
        roots = {r: p for r, p in (self.roots_fn() or {}).items() if p}
        sig = self._sig(roots) if roots else None
        cur = self._mem.get("sig")
        working = bool(self._thread and self._thread.is_alive())
        retry = time.time() - self._err_at < 60                  # after a failure (a drive pulled out) wait a minute before looking again
        stale = bool(roots) and sig != cur and not retry
        if stale and not working and self.idle_fn():
            self._thread = threading.Thread(target=self._work, args=(roots, sig), daemon=True)
            self._thread.start()
            working = True
        drives = {}
        for r in roots:
            d = (self._mem.get("drives") or {}).get(r)
            drives[r] = dict(d) if d else None
        return dict(checking=working or stale, progress=dict(self._prog) if working and self._prog else None, drives=drives, error=self.error)

    def forget(self):
        """What is on the drives changed under the cached look (clips moved or deleted by the app): the next look walks the drives again."""
        self._walked.clear()
        self._mem = dict(self._mem, sig=None)

    def subtract(self, stage, done_by_role):
        """A job did some of the work (or was stopped part way): take it off what is left for each drive now. The next look at the drives (the signature has changed)
        works the numbers out afresh."""
        field = dict(telemetry="telemetry", stills="stills").get(stage)
        if not self._lock.acquire(blocking=False):                  # being worked out right now: that answer is the fresh one
            return
        try:
            drives = self._mem.get("drives") or {}
            for r, n in (done_by_role or {}).items():
                d = drives.get(r)
                if not d:
                    continue
                if stage == "log":
                    if n:
                        d["log_new"] = d["log_gone"] = 0
                elif field:
                    d[field] = max(0, d.get(field, 0) - n)
            self._mem["drives"] = drives
            self._save()
        finally:
            self._lock.release()

    def _work(self, roots, sig):
        try:
            with self._lock:
                scans = TLG.Ledger(self.dir).scans()
                led = None
                out, walked = {}, {}
                keys_by_role = {r: (p, _mtime_ns(p), (scans.get(r) or {}).get("at") or "") for r, p in roots.items()}
                need = [r for r in sorted(roots) if (self._walked.get(r) or (None,))[0] != keys_by_role[r]]
                total = len(need) + 3
                step_i = 0
                for r, p in sorted(roots.items()):
                    cached = self._walked.get(r)
                    if cached and cached[0] == keys_by_role[r]:
                        _, files, ok, new, gone, days_new, ghosts, sample = cached
                    else:
                        self._prog = dict(done=step_i, total=total, step="list", role=r, pct=round(100 * (step_i + 0.2) / total))
                        files, ok = TF.walk(p, stat=False)
                        TF.save_cache(self._lcache)
                        self._prog = dict(done=step_i, total=total, step="list", role=r, pct=round(100 * (step_i + 0.65) / total))
                        step_i += 1
                        if led is None:
                            led = TLG.Ledger(self.dir)
                        present = {}
                        for f in files:
                            present.setdefault(f["stamp"][:10], {})[TF.key(f)] = f
                        new = gone = ghosts = 0
                        days_new, sample = {}, []
                        for day, keys in present.items():
                            d = led.day(day)
                            for k, f in keys.items():
                                e = d.get(k)
                                r_ = e["drives"].get(r) if e else None
                                if r_ is None or r_.get("gone"):
                                    if not os.path.isfile(TF.front_path(p, f)):         # a name the folder still lists but the file is not there (a deleted clip the drive has not let go of): not a new file
                                        ghosts += 1
                                        continue
                                    new += 1
                                    days_new[day] = days_new.get(day, 0) + 1
                                    if len(sample) < 4:
                                        sample.append(k)
                        if ok:
                            for day in led.days():
                                here = present.get(day, set())
                                for k, e in led.day(day).items():
                                    x = e["drives"].get(r)
                                    if x is not None and x.get("gone") is None and k not in here:
                                        gone += 1
                        self._walked[r] = (keys_by_role[r], files, ok, new, gone, days_new, ghosts, sample)
                    walked[r] = (files, ok)
                    out[r] = dict(log_new=new, log_gone=gone, ghosts=ghosts, new_sample=sample, new_days=dict(sorted(days_new.items(), reverse=True)[:5]), telemetry=0, stills=0, signs=0, at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                self._prog = dict(done=len(need), total=total, step="telemetry", role="", pct=round(100 * (len(need) + 0.4) / total))
                for d in self.tc.scan_report(roots)["drives"]:          # with every connected drive together: a minute two drives hold is read once
                    out[d["role"]]["telemetry"] = d["to_read"]
                self._prog = dict(done=len(need) + 1, total=total, step="stills", role="", pct=round(100 * (len(need) + 1.4) / total))
                rep = self.fg.stills_report(roots, walked)
                for d in rep["drives"]:
                    out[d["role"]]["stills"] = d["to_make"]
                self._prog = dict(done=len(need) + 2, total=total, step="signs", role="", pct=round(100 * (len(need) + 2.4) / total))
                for r, n in self._signs_left(walked).items():
                    out[r]["signs"] = n
                self._mem = dict(sig=sig, drives=out)
                self.error = ""
                self._save()
        except Exception as e:                                  # a drive pulled out in the middle: say so and try again at the next look
            self.error = "%s: %s" % (type(e).__name__, e)
            self._err_at = time.time()
            self._mem = dict(self._mem, sig=None)
        finally:
            self._prog = None
