"""app_teslacam.py - browse the clips on any connected TeslaCam drive, for the TeslaCam page of tesla_app.py.

Saved and Sentry events are folders (TeslaCam/SavedClips|SentryClips/<stamp>/ with <stamp>-<camera>.mp4, event.json,
thumb.png), read by app_sentry.Sentry. RecentClips is one flat folder of <stamp>-<camera>.mp4, one minute per stamp; it is
grouped here into continuous runs (minutes less than 75 s apart), which is what a drive looks like. Nothing is changed
on a drive, and a drive is only read when the page asks for it (an Archive-only default, see drive_roles.py).
"""
import os
import re
import threading

import app_sentry
import drive_roles as DR

KINDS = {"sentry": "SentryClips", "saved": "SavedClips"}
_REC = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-(%s)\.mp4$" % "|".join(app_sentry.CAMS))
GAP = 75                                    # seconds between two minutes that still belong to one run
_LOCK = threading.Lock()


def _ts(stamp):
    return stamp[:10] + "T" + stamp[11:].replace("-", ":")


def _secs(stamp):
    import datetime
    return datetime.datetime.strptime(stamp, "%Y-%m-%d_%H-%M-%S").timestamp()


class TeslaCam:
    def __init__(self, data_dir, sentry=None):
        self.dir = data_dir
        self.ev = {}                        # (role, kind) -> Sentry
        if sentry:
            self.ev[("A", "sentry")] = sentry   # the Parked page shares this index
        self.rec = {}                       # role -> dict(mt, runs, clips)

    def drives(self):
        return [dict(role=r, name=DR.NAMES.get(r, r), kind="archive" if r == "A" else "other") for p, r in DR.discover()]

    def events(self, role, kind, teslacam):
        if kind not in KINDS:
            return [], False
        with _LOCK:
            s = self.ev.get((role, kind))
            if s is None:
                s = self.ev[(role, kind)] = app_sentry.Sentry(self.dir, "teslacam_index_%s_%s.json" % (role, kind), KINDS[kind])
        ev, ok = s.events(teslacam)
        return (ev if ok else []), ok

    def _scan(self, role, teslacam):
        root = os.path.join(teslacam, "RecentClips")
        try:
            mt = os.path.getmtime(root)
        except OSError:
            return None
        c = self.rec.get(role)
        if c and c["mt"] == mt and c["root"] == root:
            return c
        clips = {}
        try:
            for fn in os.listdir(root):
                m = _REC.match(fn)
                if m:
                    clips.setdefault(m.group(1), []).append(m.group(2))
        except OSError:
            return None
        runs, cur = [], None
        for st in sorted(clips):
            t = _secs(st)
            if cur and t - cur["t1"] <= GAP:
                cur["stamps"].append(st)
                cur["t1"] = t
            else:
                cur = dict(t1=t, stamps=[st])
                runs.append(cur)
        out = []
        for r in runs:
            a, b = r["stamps"][0], r["stamps"][-1]
            out.append(dict(id=a, ts=_ts(a), end=_ts(b), n=len(r["stamps"]), stamps=r["stamps"],
                            full=sum(1 for s in r["stamps"] if len(clips[s]) == 6)))
        c = self.rec[role] = dict(mt=mt, root=root, runs=sorted(out, key=lambda x: x["ts"], reverse=True), clips=clips)
        return c

    def recent(self, role, teslacam):
        """Continuous runs of RecentClips (newest first), without the per-minute lists."""
        if not teslacam:
            return [], False
        c = self._scan(role, teslacam)
        if c is None:
            return [], True
        return [{k: v for k, v in r.items() if k != "stamps"} for r in c["runs"]], True

    def run_clips(self, role, teslacam, rid):
        """{stamp: [cameras]} of one run (id = its first minute)."""
        c = self._scan(role, teslacam) if teslacam else None
        r = next((x for x in c["runs"] if x["id"] == rid), None) if c else None
        return {s: sorted(c["clips"][s], key=app_sentry.CAMS.index) for s in r["stamps"]} if r else None
