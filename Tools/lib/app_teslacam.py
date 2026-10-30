"""app_teslacam.py - browse the clips on any connected TeslaCam drive, for the TeslaCam page of tesla_app.py.

Saved and Sentry events are folders (TeslaCam/SavedClips|SentryClips/<stamp>/ with <stamp>-<camera>.mp4, event.json,
thumb.png), read by app_sentry.Sentry. RecentClips is one flat folder of <stamp>-<camera>.mp4, one minute per stamp; it is
grouped here into continuous runs (minutes less than 75 s apart), which is what a drive looks like. Nothing is changed
on a drive, and a drive is only read when the page asks for it (an Archive-only default, see drive_roles.py).
"""
import os
import re
import threading
import time

import app_sentry
import drive_roles as DR
import app_config
import clip_clock as CC
import run_cuts as RC
import clips_purged as PG
import home_ignore as IGN
import teslacam_timeline as TL
import teslacam_scan as TS
import app_review
import teslacam_ledger as TLG
import teslacam_files as TF

KINDS = {"sentry": "SentryClips", "saved": "SavedClips"}
_REC = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-(%s)\.mp4$" % "|".join(app_sentry.CAMS))
GAP = 75                                    # seconds between two minutes that still belong to one run
_LOCK = threading.Lock()


def _ts(stamp):
    """The local clock text (display) of a clip's name, which is the car's clock (clip_clock.py)."""
    return CC.local_of(stamp)


def _secs(stamp):
    """UTC epoch of a clip's name."""
    return CC.utc(stamp)


class TeslaCam:
    def __init__(self, data_dir, sentry=None):
        self.dir = data_dir
        self.ev = {}                        # (role, kind) -> Sentry
        if sentry:
            self.ev[("A", "sentry")] = sentry   # the Parked page shares this index
        self.rec = {}                       # role -> dict(mt, runs, clips)
        self._foot = {}                     # role -> (what it was made from, footage list)
        self._rt = 0.0                      # when the clock was last asked to refresh (see _tl)
        self.store = None                   # telemetry read from clips, shared by every drive (see _tl)
        self.rv = None                      # signs read from the video (see app_review.py)
        self.scan_ix = None                 # the reading job of the drive scan (teslacam_scan.py)
        self._scan_todo = []

    def drives(self):
        return [dict(role=r, name=DR.NAMES.get(r, r), kind="archive" if r == "A" else "other") for p, r in DR.discover()]

    def events(self, role, kind, teslacam):
        if kind not in KINDS:
            return [], False
        ev, ok = self._index(role, kind).events(teslacam)
        return (ev if ok else []), ok

    def _index(self, role, kind):
        """The kept list of a drive's Saved or Sentry events (teslacam_index_<role>_<kind>.json); it answers from the file when the drive is away."""
        with _LOCK:
            s = self.ev.get((role, kind))
            if s is None:
                s = self.ev[(role, kind)] = app_sentry.Sentry(self.dir, "teslacam_index_%s_%s.json" % (role, kind), KINDS[kind])
        return s

    def _scan(self, role, teslacam):
        root = os.path.join(teslacam, "RecentClips")
        c = self.rec.get(role)
        if c and c["root"] == root and c.get("cv") == (CC.version(), RC.version()) and time.time() - c.get("chk", 0) < 3:
            return c                                    # looked at a moment ago: a stat of a slow drive per request adds up (the Drives list asks for every drive)
        try:
            mt = os.path.getmtime(root)
        except OSError:
            return None
        if c and c["mt"] == mt and c["root"] == root and c.get("cv") == (CC.version(), RC.version()):
            c["chk"] = time.time()
            return c
        clips = {}
        names = TF.clip_names(root)                     # from the listing the footage check just made, while the folder is unchanged
        if names is None:
            return None
        for fn in names:
            m = _REC.match(fn)
            if m:
                clips.setdefault(m.group(1), []).append(m.group(2))
        runs, cur = [], None
        for st in sorted(clips):
            t = _secs(st)
            if cur and t - cur["t1"] <= GAP and not RC.is_cut(st):
                cur["stamps"].append(st)
                cur["t1"] = t
            else:
                cur = dict(t1=t, stamps=[st])
                runs.append(cur)
        out = []
        for r in runs:
            a, b = r["stamps"][0], r["stamps"][-1]
            out.append(dict(id=a, ts=_ts(a), end=_ts(b), t0=_secs(a), t1=_secs(b) + 60, n=len(r["stamps"]), stamps=r["stamps"],
                            full=sum(1 for s in r["stamps"] if len(clips[s]) == 6)))
        c = self.rec[role] = dict(mt=mt, root=root, cv=(CC.version(), RC.version()), chk=time.time(), runs=sorted(out, key=lambda x: x["t0"], reverse=True), clips=clips)
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

    @staticmethod
    def loc_of(stamps):
        """{clip stamp: local clock text}, for the player to show times on the local clock (the stamps are the car's)."""
        return {s: _ts(s) for s in stamps}

    # ------------------------------------------------------------------------------------------------- timeline
    def _tl(self, role):
        TL.OPTS.update({k: v for k, v in (app_config.get("teslacam") or {}).items() if k in TL.OPTS})
        if self.store is None:
            base = os.path.join(self.dir, "teslacam_timeline")
            self.store = TL.Store(base)
            self._migrate(base)
            CC.configure(self.dir)
        if time.time() - self._rt > 3:                  # looking at what changed means stat calls: not for every request of a list
            self._rt = time.time()
            CC.refresh(self.dir, self.store)            # learns the car's clock again in the background when the telemetry or the drives changed
        return self.store

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

    def _ledger_recent(self, want=None):
        """The RecentClips minutes in the footage log: ({stamp: [cameras]}, {stamp: {roles that hold it now}}). `want` limits it to those drives; with None every minute
        ever logged is listed, also one that is gone from every drive (its holders are then empty: only its stills are left)."""
        led = TLG.Ledger(self.dir)
        sig = None
        if want is None:                                # the whole log is only read again when one of its day files has changed (they are many and the lists ask often)
            try:
                sig = tuple(sorted((e.name, e.stat().st_mtime_ns, e.stat().st_size) for e in os.scandir(led.root) if e.name.endswith(".json")))
            except OSError:
                sig = None
            hit = getattr(self, "_lr", None)
            if sig is not None and hit and hit[0] == sig:
                return hit[1]
        cams, holders = {}, {}
        for day in led.days():
            for key, rec in led.day(day).items():
                if not key.startswith("recent|"):
                    continue
                now = {r for r, d in (rec.get("drives") or {}).items() if not d.get("gone")}
                if want is not None and not (now & want):
                    continue
                m = _REC.match(key.split("|", 2)[2] + ".mp4")
                if m:
                    cams.setdefault(m.group(1), []).append(m.group(2))
                    holders.setdefault(m.group(1), set()).update(now if want is None else now & want)
        out = {st: sorted(c, key=app_sentry.CAMS.index) for st, c in cams.items()}, holders
        if sig is not None:
            self._lr = (sig, out)
        return out

    def _events_kept(self, kind, want, roots=None):
        """The Saved or Sentry events of those drives, one entry per event (with `roles` = the drives that list it). A connected drive (in `roots`) is read
        afresh; a drive that is away answers from its kept list. The same event on two drives is one, with the minutes of both."""
        by = {}
        for r in sorted(want):
            evs, ok = self.events(r, kind, roots[r]) if roots and roots.get(r) else ([], False)
            if not ok:
                evs = self._index(r, kind).events(None)[0]              # away (or not readable now): the kept list
            for e in evs:
                got = by.get(e["id"])
                if got is None:
                    by[e["id"]] = dict(e, roles=[r])
                    continue
                clips = dict(got.get("clips") or {})                    # the same event kept on two drives: the minutes of both
                for st, cams in (e.get("clips") or {}).items():
                    clips[st] = sorted(set(clips.get(st, [])) | set(cams), key=app_sentry.CAMS.index)
                by[e["id"]] = dict(got, clips=clips, roles=got["roles"] + [r])
        return list(by.values())

    def clips_all(self, roots):
        """The Clips list, the same whatever drives are connected: every Recent run, Saved event and Sentry event ever logged. Each carries `roles` (the drives that hold it now,
        empty when it is gone from every drive) and `play` (a connected one to play it from, or None: then only its stills can be shown)."""
        known = sorted(set(TLG.Ledger(self.dir).scans()) | set(roots))
        want = set(known)
        out = dict(connected=sorted(r for r in roots if roots[r]), sentry=[], saved=[], recent=[])
        for kind in KINDS:
            evs = [e for e in self._events_kept(kind, want, roots) if not PG.has_event(kind, e["id"])]      # an event deleted with everything is no longer listed
            for e in evs:
                e["play"] = next((r for r in e["roles"] if roots.get(r)), None)
            out[kind] = evs
        cams, holders = self._ledger_recent(None)
        runs, cur = [], None
        for st in sorted(x for x in cams if not PG.has(x)):
            t = _secs(st)
            if cur and t - cur["t1"] <= GAP and not RC.is_cut(st):
                cur["stamps"].append(st)
                cur["t1"] = t
            else:
                cur = dict(t1=t, stamps=[st])
                runs.append(cur)
        gone_home = []
        for r in runs:
            sts = r["stamps"]
            a, b = sts[0], sts[-1]
            roles = sorted(set().union(*(holders[x] for x in sts)))
            if not roles and RC.kind(a) == "park":
                p0 = RC.pos(a)
                if p0 and IGN.at(p0[0], p0[1]):              # parked at an ignored place (Home) and gone from every drive: nothing is kept from it, so its record goes with the video
                    gone_home.extend(sts)
                    continue
            minute_play = {x: next((q for q in sorted(holders[x]) if roots.get(q)), None) for x in sts}
            out["recent"].append(dict(id=a, ts=_ts(a), end=_ts(b), t0=_secs(a), t1=_secs(b) + 60, part=RC.kind(a), pos=RC.pos(a), n=len(sts), full=sum(1 for x in sts if len(cams[x]) == 6),
                                      roles=roles, play=minute_play[a] or next((q for q in minute_play.values() if q), None), playable=sum(1 for q in minute_play.values() if q)))
        out["recent"].sort(key=lambda x: x["t0"], reverse=True)
        if gone_home:
            PG.add(gone_home)
        return out

    def run_all(self, rid, roots):
        """One Recent run for the player: {clips: {minute: [cameras]}, loc, play: {minute: connected drive or None}, roles: {minute: [drives]}}; None if there is no such run."""
        cams, holders = self._ledger_recent(None)
        sts = sorted(cams)
        if rid not in cams:
            return None
        run, last = [], None
        for st in sts:
            t = _secs(st)
            if last is not None and (t - last > GAP or RC.is_cut(st)):
                if rid in run:
                    break
                run = []
            run.append(st)
            last = t
        if rid not in run:
            return None
        return dict(clips={x: cams[x] for x in run}, loc=self.loc_of(run), roles={x: sorted(holders[x]) for x in run},
                    play={x: next((q for q in sorted(holders[x]) if roots.get(q)), None) for x in run})

    def after_all(self, stamp, n, roots, skip=()):
        """Up to `n` minutes of continuous (Recent) recording that follow the minute `stamp`, one after the other with no gap and each playable from a connected drive, minus
        those in `skip`: {clips, loc, play} like run_all. A Saved clip keeps only a little after the moment it was saved; the dashcam went on recording, so the minutes
        after the event are shown from there."""
        cams, holders = self._ledger_recent(None)
        out, last, t0 = [], _secs(stamp), _secs(stamp)
        for st in sorted(cams):
            t = _secs(st)
            if t <= t0 or st in skip:
                continue
            if t - last > GAP or len(out) >= n:
                break
            if not any(roots.get(q) for q in holders[st]):
                break
            out.append(st)
            last = t
        return dict(clips={x: cams[x] for x in out}, loc=self.loc_of(out), play={x: next((q for q in sorted(holders[x]) if roots.get(q)), None) for x in out})

    def timeline_stored(self, roles=None):
        """The same timeline built only from what is kept on this computer, so a drive being away changes nothing: the minutes of Recent clips come from the
        footage log, the Saved and Sentry events from the kept event lists, the telemetry from the shared store. `roles` limits it to what those drives
        hold (default: every drive ever seen). Nothing is read from a drive."""
        store = self._tl("A")
        known = sorted(TLG.Ledger(self.dir).scans())
        want = set(r for r in (roles or known) if r in known)
        recent, hold = self._ledger_recent(want)
        lists = {kind: self._events_kept(kind, want) for kind in KINDS}
        items = TL.build(recent, lists["saved"], lists["sentry"], store)
        self._held(items, want, hold)
        mins = TL.minutes(recent, lists["saved"])
        front = [m for m in mins if m.get("tcam")]
        read = sum(1 for m in front if store.summary(m["stamp"]) is not None)
        return dict(connected=True, stored=True, roles=sorted(want), items=items, minutes=len(front), read=read, no_front=len(mins) - len(front))

    def _held(self, items, want, recent_holders):
        """A recording (telemetry not read yet) says which drives hold the video of its minutes now (`held`, empty when it is gone from every drive), so the page offers
        to read it only when one of them is connected. Worked out from the footage log and the kept event lists."""
        rec = [x for x in items if x.get("kind") == "recording"]
        if not rec:
            return
        at = []                                                        # [(UTC second, {roles})] of every minute that is on a drive now
        for st, roles in recent_holders.items():
            at.append((_secs(st), set(roles)))
        for r in want:
            for e in self._index(r, "saved").events(None)[0]:
                if e.get("gone"):
                    continue
                for st in (e.get("clips") or {}):
                    at.append((_secs(st), {r}))
        for x in rec:
            got = set()
            for t, roles in at:
                if x["t0"] - 1 <= t < x["t1"]:
                    got |= roles
            x["held"] = sorted(got)

    # ------------------------------------------------------------------------------------------------- scan (teslacam_scan.py)
    def path_sources(self, t0, t1):
        """What the dashcam's telemetry holds for [t0, t1] (UTC): the minutes of footage the shared store knows there, how many were read (and had motion), and which
        drives hold (or once held) each minute's footage. Nothing is read from a drive."""
        store = self._tl("A")
        mins = TL.cached_minutes(store, t0, t1)
        sums = {m["stamp"]: store.summary(m["stamp"]) for m in mins}
        read = [st for st, x in sums.items() if isinstance(x, dict)]
        moving = [st for st in read if sums[st].get("vmax", 0) >= TL.OPTS["moving_kmh"]]
        led = TLG.Ledger(self.dir)
        held = {}
        for day in sorted({m["stamp"][:10] for m in mins}):
            for key, rec in led.day(day).items():
                if not key.startswith("recent|"):
                    continue
                mm = _REC.match(key.split("|", 2)[2] + ".mp4")
                if mm and mm.group(1) in sums:
                    for r, d in (rec.get("drives") or {}).items():
                        h = held.setdefault(r, dict(here=set(), gone=set()))
                        h["gone" if d.get("gone") else "here"].add(mm.group(1))
        return dict(minutes=len(mins), read=len(read), moving=len(moving), drives={r: dict(here=len(v["here"]), gone=len(v["gone"])) for r, v in sorted(held.items())})

    def pick_role(self, roots, t0, t1):
        """(role, TeslaCam folder) of the connected drive holding the most minutes of footage for [t0, t1] (UTC); the Archive wins a tie.
        None holds any: the Archive (or the first connected drive), so whatever was read before still shows."""
        best, most = None, 0
        for r in DR.read_roles():
            root = (roots or {}).get(r)
            if not root:
                continue
            n = sum(1 for m in self.footage(r, root) if t0 - 60000 <= m["t"] <= t1 + 60000 and CC.maybe_in(m["stamp"], t0, t1))      # m["t"] is the car's own clock: only those within a day of the window can match
            if n > most:
                best, most = (r, root), n
        if best:
            return best
        first = next(((r, (roots or {})[r]) for r in DR.read_roles() if (roots or {}).get(r)), None)
        return ("A", roots["A"]) if roots and roots.get("A") else first or ("A", None)

    def footage(self, role, teslacam, all_saved=False):
        """Every Recent and Saved minute on a drive (TL.minutes). With `all_saved`, Saved minutes within 30 s of a Recent clip are kept too so their telemetry is read."""
        c = self._scan(role, teslacam)
        saved, _ok = self.events(role, "saved", teslacam)
        if all_saved:
            return TL.minutes(c["clips"] if c else {}, saved, all_saved=True)
        key = (id(c), id(saved), len(saved), CC.version())
        memo = self._foot.get(role)
        if memo and memo[0] == key:
            return memo[1]                              # the same listing as a moment ago
        out = TL.minutes(c["clips"] if c else {}, saved)
        self._foot[role] = (key, out)
        return out

    def scan_report(self, roots):
        """Footage of the drives in roots {role: TeslaCam folder} against the telemetry kept; also what the reading job is doing."""
        store = self._tl("A")
        mins = {r: [m for m in self.footage(r, p, all_saved=True) if not (IGN.has(m["stamp"]) or PG.has(m["stamp"]))] for r, p in roots.items() if p}      # what is parked at Home, or was taken off the log, is left alone
        rep = TS.plan(store, mins, lambda r, m: self._front(roots[r], m))
        self._scan_todo = rep.pop("todo")
        rep["job"] = self.scan_status()
        return rep

    def scan_todo(self, roots):
        """How many minutes of footage on these drives have no telemetry read yet."""
        self.scan_report(roots)
        return len(self._scan_todo)

    def scan_status(self):
        return self.scan_ix.status() if getattr(self, "scan_ix", None) else dict(running=False, done=0, total=0, current="", error="")

    def scan_start(self, roots, reuse=False):
        """Start reading what the picked drives lack. reuse: scan_todo / scan_report has just worked it out (a walk of the drives is slow): do not walk again."""
        store = self._tl("A")
        if not (reuse and getattr(self, "_scan_todo", None) is not None):
            self.scan_report(roots)
        if getattr(self, "scan_ix", None) is None:
            self.scan_ix = TL.Indexer(store)
        return self.scan_ix.start([(x["stamp"], x["path"]) for x in self._scan_todo])

    def scan_stop(self):
        return self.scan_ix.stop() if getattr(self, "scan_ix", None) else self.scan_status()

    def fuse(self, stamp):
        """The finer track of a Tessie drive (drive_fuse.py): its own points, with the dashcam's per-second readings where they exist."""
        import drive_fuse
        store = self._tl("A")
        return drive_fuse.fuse_drive(self.dir, stamp, store)

    def enrich(self, rows):
        """The points of a drive page with the dashcam's readings put in (drive_fuse.enrich); None when the footage's telemetry does not cover it."""
        import drive_fuse
        store = self._tl("A")
        return drive_fuse.enrich(rows, store, None, self.dir)

    @staticmethod
    def _front(teslacam, m):
        sub = "RecentClips" if m["src"] == "recent" else os.path.join("SentryClips" if m["src"] == "sentry" else "SavedClips", m["ref"])
        return os.path.join(teslacam, sub, "%s-%s.mp4" % (m["stamp"], m.get("tcam") or "front"))

    def segment(self, role, teslacam, sid, budget=20.0):
        """One drive of the timeline with its telemetry; reads the minutes not read yet (up to `budget` seconds, the page asks again while `pending`)."""
        if not teslacam or sid[:2] != "20":
            return None
        t_id = TL.secs(sid)

        def pick(store, allm, saved, sentry):
            items = TL.build({m["stamp"]: m["cams"] for m in allm if m["src"] == "recent"}, saved, [], store)
            it = next((x for x in items if x["kind"] in ("drive", "stopped", "recording") and x["t0"] <= t_id < x["t1"]), None)
            if not it:
                return None
            full = self.footage(role, teslacam, all_saved=True) if teslacam else allm
            return it, [m for m in full if m["t"] + 60 > it["t0"] and m["t"] < it["t1"]]
        return self._finish(role, teslacam, pick, budget)

    def _settle(self, role, teslacam, store, t0, t1, budget):
        """The car's clock around [t0, t1] (UTC) may not be known yet - no minute of footage within clip_clock's reach has been read and matched to a
        Tessie drive. Then the minutes that could fall in the window under ANY offset the car's clock may have had are read first (they decide it),
        and the clock is learned, so the right minutes are the ones used. Once a minute near it is known this does nothing."""
        recent, saved, _s = self._sources(role, teslacam)
        unsure = [m for m in TL.minutes(recent, saved) if m.get("tcam") and not CC.known(m["stamp"]) and CC.maybe_in(m["stamp"], t0, t1)]
        if not unsure:
            return
        import time
        todo = [(m["stamp"], self._front(teslacam, m)) for m in unsure if store.summary(m["stamp"]) is None]
        if todo and budget > 0:
            TL.read_many(store, todo, deadline=time.time() + budget)
        CC.refresh(self.dir, store, wait=True)

    def window(self, role, teslacam, t0, t1, budget=20.0, lo=None, sentry=None):
        """Everything recorded between two instants (t0, t1 = UTC epoch seconds - a Tessie drive): one item with telemetry and the clips, Recent and Saved
        stitched. With the drive not connected it still returns what was read before (map and charts, no video). lo = seconds the clock the times are
        SHOWN on is ahead of UTC (the drive list shows a drive on the clock of where it was driven); without it, app.timezone's.
        sentry = the folder of a Sentry event: its minutes are part of the window too (they are not otherwise: a parked car has no drive to belong to)."""
        if teslacam:
            store = self._tl(role)
            self._settle(role, teslacam, store, t0, t1, budget)

        def pick(store, allm, saved, sentry):
            mins = [m for m in allm if m["t"] + 60 > t0 and m["t"] < t1] if teslacam else TL.cached_minutes(store, t0, t1)
            if not mins:
                return None
            sums = [store.summary(m["stamp"]) for m in mins]
            state = "move" if any(x and x["vmax"] >= TL.OPTS["moving_kmh"] for x in sums) else "unknown" if any(x is None for x in sums) else "still"
            return TL.drive_item([(m, state) for m in mins], store), mins
        out = self._finish(role, teslacam, pick, budget, events=(t0, t1), lo=lo, extra=self._sentry_minutes(role, teslacam, sentry))
        if out is None and teslacam:          # the connected drive holds none of it (the video is on a drive that is away): the telemetry read before still shows, with no video
            return self.window(role, None, t0, t1, budget, lo, sentry)
        return out

    def _sentry_minutes(self, role, teslacam, ev_id):
        """The minutes of one Sentry event as footage minutes (src "sentry"), those the continuous recording does not already hold."""
        if not (teslacam and ev_id):
            return []
        recent, _saved, sentry = self._sources(role, teslacam)
        e = next((x for x in sentry if x["id"] == ev_id), None)
        if not e:
            return []
        import bisect
        rt = sorted(TL.secs(st) for st in recent)
        out = []
        for st, cams in e["clips"].items():
            t = TL.secs(st)
            i = bisect.bisect(rt, t)
            near = min([abs(rt[j] - t) for j in (i - 1, i) if 0 <= j < len(rt)] or [1e9])
            if st not in recent and near > TL.OPTS["same_minute_s"]:
                out.append(dict(stamp=st, t=t, src="sentry", ref=e["id"], cams=cams, tcam=TL.tcam(cams)))
        return out

    def _finish(self, role, teslacam, pick, budget, events=None, lo=None, extra=None):
        import time
        store = self._tl(role)
        recent, saved, sentry = self._sources(role, teslacam) if teslacam else ({}, [], [])
        allm = self.footage(role, teslacam) if teslacam else []
        if extra:
            allm = sorted(allm + extra, key=lambda m: m["t"])
        for rnd in range(2):
            r = pick(store, allm, saved, sentry)
            if not r:
                return None
            it, mins = r
            todo = [m for m in mins if store.summary(m["stamp"]) is None and m.get("tcam")]
            if not todo or rnd == 1 or not teslacam:
                break
            if budget > 0:
                TL.read_many(store, [(m["stamp"], self._front(teslacam, m)) for m in todo], deadline=time.time() + budget)
        ser = TL.detail(mins, store, it)
        TL.show(it, lo)                                          # the clock the times are shown on (display only): everything below that is compared stays UTC
        lo = it["lo"]
        out = dict(item=it, connected=bool(teslacam), minutes=[dict(stamp=m["stamp"], src=m["src"], ref=m["ref"], cams=m["cams"], off=round(m["t"] - it["t0"]), loc=TL.iso(m["t"], lo)) for m in mins],
                   series=ser, pending=ser["pending"])
        if events:
            a, b = events
            ev = []
            for folder, lst in (("SavedClips", saved), ("SentryClips", sentry)):
                for e in lst:
                    ts = [TL.secs(k) for k in e["clips"]]
                    if ts and any(t + 60 > a and t < b for t in ts) and (folder == "SavedClips" or a - 120 <= e["t"] <= b + 120):
                        ev.append(dict(id=e["id"], folder=folder, ts=TL.iso(e["t"], lo), t=e["t"], reason=e.get("reason", ""), city=e.get("city", ""), cam=e.get("cam", ""), thumb=e.get("thumb", False), cts=e.get("cts", ""), clips=e["clips"],
                                       front=("front" in e["clips"][sorted(e["clips"])[0]])))
            out["events"] = sorted(ev, key=lambda x: x["t"])
        return out

    # ------------------------------------------------------------------------------------------------------ review
    def _rv(self):
        app_review.OPTS.update({k: v for k, v in (app_config.get("review") or {}).items() if k in app_review.OPTS})
        if self.rv is None:
            self.rv = app_review.Review(self.dir)
        return self.rv

    def _review_mins(self, role, teslacam, t0, t1):
        store = self._tl(role)
        if teslacam:
            recent, saved, _s = self._sources(role, teslacam)
            mins = [m for m in TL.minutes(recent, saved) if m["t"] + 60 > t0 and m["t"] < t1]
        else:
            mins = TL.cached_minutes(store, t0, t1)
        return store, mins

    def review(self, role, teslacam, t0, t1, action="status", lo=None):
        """The signs read so far in a time window (a Tessie drive), and the state of the reading job. action: status | start | redo | stop."""
        rv = self._rv()
        store, mins = self._review_mins(role, teslacam, t0, t1)
        front = [m for m in mins if m.get("tcam")] if teslacam else []
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
        # signs are looked for only while the car goes min_kmh or more: a minute that never did is not counted as one to read
        min_kmh = float((app_config.get("review") or {}).get("min_kmh", 30.0))
        fast = [m for m in front if (lambda sm: not isinstance(sm, dict) or sm.get("vmax", 0) >= min_kmh)(store.summary(m["stamp"]))]
        return dict(signs=rv.signs(stamps, lo), minutes=len(fast), slow=len(front) - len(fast), scanned=sum(1 for m in fast if m["stamp"] in done),
                    connected=bool(teslacam), job=rv.status(), missing=app_review.requirements())

    def review_scanned(self):
        """{UTC epoch seconds of each minute whose signs were read: signs found} (the Drives list compares these with a drive's UTC times)."""
        return {str(int(CC.utc(st))): n for st, n in self._rv().scanned().items()}
