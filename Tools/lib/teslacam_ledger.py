"""teslacam_ledger.py - a log of every TeslaCam video file: which drive it is on, how big, when it was first and last seen, when it went.

One scan of a connected drive lists its clips (teslacam_files.walk) and updates the log:

    <data>/teslacam_ledger/<day>.json        one per clip day: {key: {size, drives: {role: {first, last, gone}}}}
    <data>/teslacam_ledger/scans.json        the last scan of each drive: when, how many files, bytes

Times are UTC ('2026-10-16T21:51:05Z'). A file that is listed has last = this scan and no `gone`; a file the drive had at an earlier scan and
no longer lists gets gone = this scan (only when the whole drive could be read, and not when it suddenly lists nothing). So the log can say:

    on Archive / on JOWUA ...      listed at the last scan of that drive
    copied to the Archive          first seen on the Archive after it was seen on another drive
    can be purged from X           on X and on the Archive with the same size (nothing is deleted by this module)
    deleted                        gone from every drive that ever had it

Only the log is written here; no video is opened, moved or deleted. The stills (teslacam_stills.py) and the telemetry (teslacam_timeline.py)
of a deleted video are kept, which is what makes "video deleted, stills kept" possible.
"""
import json
import os
import time

import teslacam_files as TF

DIRNAME = "teslacam_ledger"
EMPTY_GUARD = 0.5        # a drive that lists less than this share of what it listed last time is not trusted (not mounted properly)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _day(stamp):
    return stamp[:10]


class Ledger:
    def __init__(self, data_dir):
        self.root = os.path.join(data_dir, DIRNAME)
        self._days = {}

    # ---- files ---------------------------------------------------------------------------------------------------
    def _path(self, day):
        return os.path.join(self.root, day + ".json")

    def day(self, day):
        d = self._days.get(day)
        if d is None:
            try:
                with open(self._path(day), encoding="utf-8") as f:
                    d = json.load(f)
            except (OSError, ValueError):
                d = {}
            self._days[day] = d
        return d

    def days(self):
        try:
            return sorted(n[:-5] for n in os.listdir(self.root) if len(n) == 15 and n.endswith(".json"))
        except OSError:
            return []

    def flush(self, only=None):
        os.makedirs(self.root, exist_ok=True)
        for day, d in self._days.items():
            if only is not None and day not in only:
                continue
            tmp = self._path(day) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, separators=(",", ":"), sort_keys=True)
            os.replace(tmp, self._path(day))

    def scans(self):
        try:
            with open(os.path.join(self.root, "scans.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_scans(self, s):
        os.makedirs(self.root, exist_ok=True)
        with open(os.path.join(self.root, "scans.json"), "w", encoding="utf-8") as f:
            json.dump(s, f, indent=1, sort_keys=True)

    # ---- scanning ------------------------------------------------------------------------------------------------
    def scan(self, role, teslacam, at=None):
        """List the drive and update the log. Returns dict(ok, files, bytes, new, gone, back, note)."""
        at = at or now_iso()
        files, ok = TF.walk(teslacam)
        last = self.scans().get(role) or {}
        res = dict(ok=ok, files=len(files), bytes=sum(f["size"] for f in files), new=0, gone=0, back=0, note="")
        can_mark_gone = ok
        if ok and last.get("files") and len(files) < last["files"] * EMPTY_GUARD:
            can_mark_gone = False
            res["note"] = "The drive lists far fewer files than last time (%d against %d), so nothing was marked as gone." % (len(files), last["files"])
        elif not ok:
            res["note"] = "The drive could not be read completely, so nothing was marked as gone."
        present = {}
        for f in files:
            present.setdefault(_day(f["stamp"]), {})[TF.key(f)] = f
        touched = set()
        for day, fs in present.items():
            d = self.day(day)
            for k, f in fs.items():
                e = d.get(k)
                if e is None:
                    e = d[k] = dict(size=f["size"], drives={})
                    res["new"] += 1
                e["size"] = f["size"]
                r = e["drives"].get(role)
                if r is None:
                    e["drives"][role] = dict(first=at, last=at, gone=None)
                else:
                    if r.get("gone"):
                        res["back"] += 1
                    r["last"], r["gone"] = at, None
            touched.add(day)
        if can_mark_gone:
            for day in self.days() + [x for x in present if x not in self.days()]:
                d = self.day(day)
                here = present.get(day, {})
                for k, e in d.items():
                    r = e["drives"].get(role)
                    if r is not None and r.get("gone") is None and k not in here:
                        r["gone"] = at
                        res["gone"] += 1
                        touched.add(day)
        self.flush(touched)
        sc = self.scans()
        sc[role] = dict(at=at, files=len(files), bytes=res["bytes"], ok=ok)
        self._save_scans(sc)
        return res

    # ---- asking --------------------------------------------------------------------------------------------------
    def _all(self):
        for day in self.days():
            for k, e in self.day(day).items():
                yield day, k, e

    def summary(self):
        """Per drive and overall counts (a drive's 'listed' is what it listed at its last scan, connected or not)."""
        per, total = {}, dict(files=0, bytes=0, deleted=0, deleted_bytes=0, only_secondary=0, only_secondary_bytes=0, purgeable=0, purgeable_bytes=0)
        for day, k, e in self._all():
            dr = e["drives"]
            total["files"] += 1
            total["bytes"] += e["size"]
            live = [r for r, x in dr.items() if not x.get("gone")]
            for r, x in dr.items():
                p = per.setdefault(r, dict(listed=0, listed_bytes=0, gone=0))
                if x.get("gone"):
                    p["gone"] += 1
                else:
                    p["listed"] += 1
                    p["listed_bytes"] += e["size"]
            if not live:
                total["deleted"] += 1
                total["deleted_bytes"] += e["size"]
            elif "A" not in live:
                total["only_secondary"] += 1
                total["only_secondary_bytes"] += e["size"]
            else:
                n = [r for r in live if r != "A"]
                if n:
                    total["purgeable"] += len(n)
                    total["purgeable_bytes"] += e["size"] * len(n)
        return dict(drives=per, total=total, scans=self.scans())

    def listing(self, what, role=None, limit=None):
        """Files for a report: what = 'deleted' | 'import' (on a drive other than the Archive only) | 'purge' (on `role` and on the Archive)."""
        out = []
        for day, k, e in self._all():
            dr = e["drives"]
            live = [r for r, x in dr.items() if not x.get("gone")]
            if what == "deleted" and not live:
                gone = max(x["gone"] for x in dr.values())
                out.append((day, k, e["size"], "deleted " + gone[:10] + " (was on " + ",".join(sorted(dr)) + ")"))
            elif what == "import" and live and "A" not in live:
                out.append((day, k, e["size"], "only on " + ",".join(sorted(live))))
            elif what == "purge" and "A" in live and role in live and role != "A":
                out.append((day, k, e["size"], "on " + role + " and the Archive"))
        out.sort(reverse=True)
        return out[:limit] if limit else out

    def status(self, stamps):
        """{clip stamp: dict(state, on, gone, size)} for the front clip of each stamp. state: 'kept' (on a drive), 'deleted' (gone from every
        drive that had it), 'unknown' (never logged)."""
        out = {}
        for st in stamps:
            hit = [e for k, e in self.day(_day(st)).items() if k.endswith("|%s-front" % st)]
            if not hit:
                out[st] = dict(state="unknown")
                continue
            live = sorted({r for e in hit for r, x in e["drives"].items() if not x.get("gone")})
            gone = max((x["gone"] for e in hit for x in e["drives"].values() if x.get("gone")), default=None)
            out[st] = dict(state="kept" if live else "deleted", on=live, gone=gone, size=max(e["size"] for e in hit))
        return out
