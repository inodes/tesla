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
import app_config
import teslacam_timeline as TL

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
        self.store = None                   # telemetry read from clips, shared by every drive (see _tl)
        self.ix = {}                        # role -> Indexer

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

    # ------------------------------------------------------------------------------------------------- timeline
    def _tl(self, role):
        TL.OPTS.update({k: v for k, v in (app_config.get("teslacam") or {}).items() if k in TL.OPTS})
        if self.store is None:
            base = os.path.join(self.dir, "teslacam_timeline")
            self.store = TL.Store(base)
            self._migrate(base)
        if role not in self.ix:
            self.ix[role] = TL.Indexer(self.store)
        return self.store, self.ix[role]

    @staticmethod
    def _migrate(base):
        """One cache for every drive: a clip is the same minute whichever disk it was copied to, so it is read once.
        (An earlier version kept a folder per drive; fold those into the shared one.)"""
        import json
        try:
            subs = [d for d in sorted(os.listdir(base)) if os.path.isdir(os.path.join(base, d))]
        except OSError:
            return
        for d in subs:
            src = os.path.join(base, d)
            for fn in sorted(os.listdir(src)):
                if not fn.endswith(".json"):
                    continue
                a, b = os.path.join(src, fn), os.path.join(base, fn)
                try:
                    with open(a, encoding="utf-8") as fh:
                        mine = json.load(fh)
                    try:
                        with open(b, encoding="utf-8") as fh:
                            mine = dict(json.load(fh), **mine) if os.path.exists(b) else mine
                    except (OSError, ValueError):
                        pass
                    with open(b + ".tmp", "w", encoding="utf-8") as fh:
                        json.dump(mine, fh, separators=(",", ":"))
                    os.replace(b + ".tmp", b)
                    os.remove(a)
                except (OSError, ValueError):
                    continue
            try:
                os.rmdir(src)
            except OSError:
                pass

    def _sources(self, role, teslacam):
        c = self._scan(role, teslacam)
        recent = c["clips"] if c else {}
        saved, _ = self.events(role, "saved", teslacam)
        sentry, _ = self.events(role, "sentry", teslacam)
        return recent, saved, sentry

    def timeline(self, role, teslacam):
        if not teslacam:
            return dict(connected=False, items=[])
        store, ix = self._tl(role)
        recent, saved, sentry = self._sources(role, teslacam)
        items = TL.build(recent, saved, sentry, store)
        mins = TL.minutes(recent, saved)
        read = sum(1 for m in mins if store.summary(m["stamp"]) is not None)
        return dict(connected=True, items=items, minutes=len(mins), read=read, index=ix.status())

    def index(self, role, teslacam, action):
        """Start / stop reading the telemetry of every minute not read yet (newest first), or just report."""
        store, ix = self._tl(role)
        if action == "stop":
            return ix.stop()
        if action != "start" or not teslacam:
            return ix.status()
        recent, saved, _ = self._sources(role, teslacam)
        todo = [(m["stamp"], self._front(teslacam, m)) for m in reversed(TL.minutes(recent, saved)) if "front" in m["cams"]]
        return ix.start(todo)

    @staticmethod
    def _front(teslacam, m):
        sub = "RecentClips" if m["src"] == "recent" else os.path.join("SavedClips", m["ref"])
        return os.path.join(teslacam, sub, "%s-front.mp4" % m["stamp"])

    def segment(self, role, teslacam, sid, budget=20.0):
        """One drive with its telemetry; reads the minutes not read yet (up to `budget` seconds, the page asks again while `pending`)."""
        import time
        if not teslacam:
            return None
        store, ix = self._tl(role)
        recent, saved, sentry = self._sources(role, teslacam)
        allm = TL.minutes(recent, saved)
        t_id = TL.secs(sid) if sid[:2] == "20" else None
        if t_id is None:
            return None
        for _ in range(2):
            items = TL.build(recent, saved, [], store)
            it = next((x for x in items if x["kind"] in ("drive", "stopped", "recording") and x["t0"] <= t_id < x["t1"]), None)
            if not it:
                return None
            mins = [m for m in allm if it["t0"] <= m["t"] < it["t1"]]
            todo = [m for m in mins if store.summary(m["stamp"]) is None and "front" in m["cams"]]
            if not todo or _ == 1:
                break
            if budget > 0:
                TL.read_many(store, [(m["stamp"], self._front(teslacam, m)) for m in todo], deadline=time.time() + budget)
            store.flush()
        ser = TL.detail(mins, store, it)
        return dict(item=it, minutes=[dict(stamp=m["stamp"], src=m["src"], ref=m["ref"], cams=m["cams"], off=round(m["t"] - it["t0"])) for m in mins],
                    series=ser, pending=ser["pending"])
