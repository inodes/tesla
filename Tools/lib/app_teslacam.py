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
import app_review

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
        self.rv = None                      # signs read from the video (see app_review.py)

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

    def fuse(self, stamp):
        """The finer track of a Tessie drive (drive_fuse.py): its own points, with the dashcam's per-second readings where they exist."""
        import drive_fuse
        store, _ = self._tl("A")
        return drive_fuse.fuse_drive(self.dir, stamp, store)

    def enrich(self, rows, clock=None):
        """The points of a drive page with the dashcam's readings put in (drive_fuse.enrich); None when the footage's telemetry does not cover it."""
        import drive_fuse
        store, _ = self._tl("A")
        return drive_fuse.enrich(rows, store, clock)

    @staticmethod
    def _front(teslacam, m):
        sub = "RecentClips" if m["src"] == "recent" else os.path.join("SavedClips", m["ref"])
        return os.path.join(teslacam, sub, "%s-front.mp4" % m["stamp"])

    def segment(self, role, teslacam, sid, budget=20.0):
        """One drive of the timeline with its telemetry; reads the minutes not read yet (up to `budget` seconds, the page asks again while `pending`)."""
        if not teslacam or sid[:2] != "20":
            return None
        t_id = TL.secs(sid)

        def pick(store, allm, saved, sentry):
            items = TL.build({m["stamp"]: m["cams"] for m in allm if m["src"] == "recent"}, saved, [], store)
            it = next((x for x in items if x["kind"] in ("drive", "stopped", "recording") and x["t0"] <= t_id < x["t1"]), None)
            return (it, [m for m in allm if it["t0"] <= m["t"] < it["t1"]]) if it else None
        return self._finish(role, teslacam, pick, budget)

    def window(self, role, teslacam, t0, t1, budget=20.0):
        """Everything recorded between two clock times (a Tessie drive): one item with telemetry and the clips, Recent and Saved stitched.
        With the drive not connected it still returns what was read before (map and charts, no video)."""
        def pick(store, allm, saved, sentry):
            mins = [m for m in allm if m["t"] + 60 > t0 and m["t"] < t1] if teslacam else TL.cached_minutes(store, t0, t1)
            if not mins:
                return None
            sums = [store.summary(m["stamp"]) for m in mins]
            state = "move" if any(x and x["vmax"] >= TL.OPTS["moving_kmh"] for x in sums) else "unknown" if any(x is None for x in sums) else "still"
            return TL.drive_item([(m, state) for m in mins], store), mins
        return self._finish(role, teslacam, pick, budget, events=(t0, t1))

    def _finish(self, role, teslacam, pick, budget, events=None):
        import time
        store, ix = self._tl(role)
        recent, saved, sentry = self._sources(role, teslacam) if teslacam else ({}, [], [])
        allm = TL.minutes(recent, saved) if teslacam else []
        for rnd in range(2):
            r = pick(store, allm, saved, sentry)
            if not r:
                return None
            it, mins = r
            todo = [m for m in mins if store.summary(m["stamp"]) is None and "front" in m["cams"]]
            if not todo or rnd == 1 or not teslacam:
                break
            if budget > 0:
                TL.read_many(store, [(m["stamp"], self._front(teslacam, m)) for m in todo], deadline=time.time() + budget)
        ser = TL.detail(mins, store, it)
        out = dict(item=it, connected=bool(teslacam), minutes=[dict(stamp=m["stamp"], src=m["src"], ref=m["ref"], cams=m["cams"], off=round(m["t"] - it["t0"])) for m in mins],
                   series=ser, pending=ser["pending"])
        if events:
            a, b = events
            ev = []
            for folder, lst in (("SavedClips", saved), ("SentryClips", sentry)):
                for e in lst:
                    ts = [TL.secs(k) for k in e["clips"]]
                    if ts and any(t + 60 > a and t < b for t in ts) and (folder == "SavedClips" or a - 120 <= TL.secs(e["ts"][:10] + "_" + e["ts"][11:].replace(":", "-")) <= b + 120):
                        ev.append(dict(id=e["id"], folder=folder, ts=e["ts"], reason=e.get("reason", ""), city=e.get("city", ""), cam=e.get("cam", ""), thumb=e.get("thumb", False),
                                       front=("front" in e["clips"][sorted(e["clips"])[0]])))
            out["events"] = sorted(ev, key=lambda x: x["ts"])
        return out

    # ------------------------------------------------------------------------------------------------------ review
    def _rv(self):
        app_review.OPTS.update({k: v for k, v in (app_config.get("review") or {}).items() if k in app_review.OPTS})
        if self.rv is None:
            self.rv = app_review.Review(self.dir)
        return self.rv

    def _review_mins(self, role, teslacam, t0, t1):
        store, _ = self._tl(role)
        if teslacam:
            recent, saved, _s = self._sources(role, teslacam)
            mins = [m for m in TL.minutes(recent, saved) if m["t"] + 60 > t0 and m["t"] < t1]
        else:
            mins = TL.cached_minutes(store, t0, t1)
        return store, mins

    def review(self, role, teslacam, t0, t1, action="status"):
        """The signs read so far in a time window (a Tessie drive), and the state of the reading job. action: status | start | redo | stop."""
        rv = self._rv()
        store, mins = self._review_mins(role, teslacam, t0, t1)
        front = [m for m in mins if "front" in m["cams"]] if teslacam else []
        if action == "stop":
            rv.stop()
        elif action in ("start", "redo") and teslacam:
            items = []
            for m in front:
                ser = store.series(m["stamp"])
                items.append((m["stamp"], self._front(teslacam, m), ser["v"] if ser else None))
            rv.start(items, force=action == "redo")
        done = rv.scanned()
        stamps = [m["stamp"] for m in mins]
        return dict(signs=rv.signs(stamps), minutes=len(front), scanned=sum(1 for m in front if m["stamp"] in done),
                    connected=bool(teslacam), job=rv.status(), missing=app_review.requirements())

    def review_scanned(self):
        return self._rv().scanned()
