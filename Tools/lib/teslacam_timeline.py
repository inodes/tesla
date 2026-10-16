"""teslacam_timeline.py - one continuous timeline, and a map with telemetry, from nothing but the files on a TeslaCam drive.

No Tessie and no other app code is used here (only dashcam_series.py, which reads the telemetry the car writes into every video
frame), so this can become a standalone tool. What is on a drive:

  RecentClips   one-minute clips while driving (the car's rolling buffer)
  SavedClips    folders of clips you saved (while driving)
  SentryClips   folders of clips of an event while parked (always stationary)

Only a moving car writes telemetry (speed, GPS, heading, steering, pedals, blinkers, autopilot / FSD state), so:

  * a minute that has telemetry and moves is "drive"; one that records but does not move is "stopped"
  * Recent and Saved minutes are stitched into one drive when they follow each other (they are never the same minute)
  * a Sentry event is its own item (stationary), and may sit in the middle of a day of driving
  * the time between items is "parked" when the car's last position and its next one are the same place, otherwise "no footage"

Reading the telemetry costs about a second per clip, so each clip is read once (front camera only) and remembered under
<data>/teslacam_timeline/ (index.json = a summary per minute; <day>.json = one reading per second), keyed by the minute's name, so
the same clip copied to another disk is not read again. Nothing on a drive is changed.

Time: every t0 / t1 / t in here is UTC epoch seconds (a clip's name is the car's clock, see clip_clock.py); start / end / ts texts are the local clock, for display.
"""
import datetime
import json
import math
import os
import struct
import threading
import time

import clip_clock as CC
import dashcam_series as DS

# thresholds (the app overrides them from config.json -> app.teslacam, see app_config.py)
OPTS = dict(moving_kmh=3.0,           # a minute whose top speed is under this is not moving
            run_gap_s=75,             # minutes this close belong to one continuous recording
            gap_min_s=120,            # a hole shorter than this is not shown
            same_place_m=300,         # parked: last and next position this close ...
            same_place_est_m=800,     # ... (this far when one of them is a Sentry event's approximate position)
            same_minute_s=30,         # a Saved minute this close to a Recent one is the same minute
            read_workers=4)           # clips read at once (the reading is CPU work)
_LOCK = threading.RLock()


def secs(stamp):
    """UTC epoch of a clip's name: the name is the CAR's clock, which clip_clock turns into UTC (it can differ from local time, and change mid-drive)."""
    return CC.utc(stamp)


def iso(t, lo=None):
    """Display text (local clock) of a UTC epoch: the configured zone's, or a clock lo seconds ahead of UTC. Never compared, only shown."""
    return CC.local_iso(t, lo)


def show(it, lo=None):
    """Sets the display fields of a timeline item (start, end, lo) from its UTC t0 / t1: on the clock of where it began (its first position),
    else app.timezone's. lo = a clock to use instead. Display only - nothing is ever compared on these texts."""
    if lo is None:
        p = it.get("p0") or it.get("p1")
        lo = CC.place_offset(it["t0"], p[0], p[1]) if p and p[0] is not None else CC.local_offset(it["t0"])
    it.update(lo=lo, start=CC.local_iso(it["t0"], lo), end=CC.local_iso(it["t1"], lo))
    return it


def hav(a, b, c, d):
    r = math.pi / 180
    p = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 12742000 * math.asin(math.sqrt(p))


def _valid(lat, lon):
    return abs(lat) > 1 and abs(lon) > 1


# ----------------------------------------------------------------------------------------------------- telemetry store
class Store:
    """Per-minute telemetry read from the front clips, kept so each clip is read once."""

    def __init__(self, base):
        self.base = base
        self.sum = None
        self.days = {}
        self._dirty = set()

    def _load_sum(self):
        if self.sum is None:
            try:
                with open(os.path.join(self.base, "index.json"), encoding="utf-8") as fh:
                    self.sum = json.load(fh)
            except (OSError, ValueError):
                self.sum = {}
        return self.sum

    def summary(self, stamp):
        """None = not read yet, 0 = read, no telemetry in it, dict = summary."""
        with _LOCK:
            return self._load_sum().get(stamp)

    def series(self, stamp):
        with _LOCK:
            day = stamp[:10]
            if day not in self.days:
                try:
                    with open(os.path.join(self.base, day + ".json"), encoding="utf-8") as fh:
                        self.days[day] = json.load(fh)
                except (OSError, ValueError):
                    self.days[day] = {}
            return self.days[day].get(stamp)

    def put(self, stamp, S):
        """S = dashcam_series.read_series result (or None when it could not be read)."""
        with _LOCK:
            sm = self._load_sum()
            n = S["n"] if S else 0
            if not n:
                sm[stamp] = 0
                return
            m = max(1, int(round(S["dur"] or n / 36.0)))
            idx = [min(n - 1, int(k * n / m)) for k in range(m)]
            ser = dict(v=[round(S["v"][i], 1) for i in idx], lat=[round(S["lat"][i], 5) for i in idx], lon=[round(S["lon"][i], 5) for i in idx],
                       hd=[int(S["hd"][i]) for i in idx], ap=[S["ap"][i] for i in idx], steer=[int(S["steer"][i]) for i in idx],
                       accel=[int(S["accel"][i]) for i in idx], br=[S["br"][i] for i in idx], blk=[S["blk"][i] for i in idx], gear=[S["gear"][i] for i in idx])
            v = ser["v"]
            pos = [(a, b) for a, b in zip(ser["lat"], ser["lon"]) if _valid(a, b)]
            sm[stamp] = dict(n=m, vmax=max(v), km=round(sum(v) / 3600.0, 3), mov=sum(1 for x in v if x >= OPTS["moving_kmh"]),
                             fsd=sum(1 for x in ser["ap"] if x == 1), aut=sum(1 for x in ser["ap"] if x in (2, 3)),
                             p0=list(pos[0]) if pos else None, p1=list(pos[-1]) if pos else None)
            day = stamp[:10]
            self.series(stamp)                  # loads the day
            self.days[day][stamp] = ser
            self._dirty.add(day)

    def stamps(self):
        with _LOCK:
            return list(self._load_sum().keys())

    def flush(self):
        with _LOCK:
            os.makedirs(self.base, exist_ok=True)
            for day in list(self._dirty):
                self._write(os.path.join(self.base, day + ".json"), self.days[day])
            self._dirty = set()
            if self.sum is not None:
                self._write(os.path.join(self.base, "index.json"), self.sum)

    @staticmethod
    def _write(path, obj):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, separators=(",", ":"))
        os.replace(tmp, path)


def _job(path):
    try:
        return DS.read_series(path)
    except (OSError, struct.error):
        return None


def read_many(store, todo, deadline=None, stop=None, progress=None):
    """Reads the telemetry of [(stamp, front clip path)] in worker processes (a few at a time) into the store. Stops at `deadline`
    (epoch seconds) or when stop() is true; returns how many were read."""
    import collections
    import concurrent.futures as cf
    todo = [(s, p) for s, p in todo if store.summary(s) is None]
    n, w = 0, max(1, int(OPTS["read_workers"]))
    if not todo:
        return 0
    with cf.ProcessPoolExecutor(w) as ex:
        win, it = collections.deque(), iter(todo)

        def fill():
            while len(win) < w * 2:
                nxt = next(it, None)
                if nxt is None:
                    return
                win.append((nxt[0], ex.submit(_job, nxt[1])))
        fill()
        while win:
            stamp, fut = win.popleft()
            if (stop and stop()) or (deadline and time.time() > deadline):
                fut.cancel()
                for _, f in win:
                    f.cancel()
                break
            store.put(stamp, fut.result())
            n += 1
            if progress:
                progress(n, stamp)
            fill()
    store.flush()
    return n


class Indexer:
    """Reads telemetry for minutes not read yet, newest first, in the background."""

    def __init__(self, store):
        self.store = store
        self.t = None
        self.stop_flag = False
        self.st = dict(running=False, done=0, total=0, current="", error="", t0=0)

    def status(self):
        s = dict(self.st)
        s["running"] = bool(self.t and self.t.is_alive())
        return s

    def start(self, todo):
        """todo = [(stamp, front clip path)] already newest first."""
        if self.t and self.t.is_alive():
            return self.status()
        todo = [(s, p) for s, p in todo if self.store.summary(s) is None]
        self.stop_flag = False
        self.st = dict(running=True, done=0, total=len(todo), current="", error="", t0=time.time())
        self.t = threading.Thread(target=self._run, args=(todo,), daemon=True)
        self.t.start()
        return self.status()

    def stop(self):
        self.stop_flag = True
        return self.status()

    def _run(self, todo):
        def prog(n, stamp):
            self.st["done"], self.st["current"] = n, stamp
            if n % 20 == 0:
                self.store.flush()
        try:
            read_many(self.store, todo, stop=lambda: self.stop_flag, progress=prog)
        except Exception as e:                       # a drive pulled out, a bad file: say so, keep what was read
            self.st["error"] = "%s: %s" % (type(e).__name__, e)
            try:
                self.store.flush()
            except OSError:
                pass


# ---------------------------------------------------------------------------------------------------------- timeline
def minutes(recent, saved):
    """Every Recent and Saved minute, oldest first, each once (a Saved minute that is also in Recent is left out)."""
    import bisect
    mins = {}
    for st, cams in recent.items():
        mins[st] = dict(stamp=st, t=secs(st), src="recent", ref="", cams=cams)
    rt = sorted(m["t"] for m in mins.values())
    for ev in saved:
        for st, cams in ev["clips"].items():
            t = secs(st)
            i = bisect.bisect(rt, t)
            near = min([abs(rt[j] - t) for j in (i - 1, i) if 0 <= j < len(rt)] or [1e9])
            if near > OPTS["same_minute_s"] and st not in mins:
                mins[st] = dict(stamp=st, t=t, src="saved", ref=ev["id"], cams=cams)
    return sorted(mins.values(), key=lambda m: m["t"])


def build(recent, saved, sentry, store):
    """recent {stamp: [cams]}; saved / sentry = lists of events (id, ts, clips {stamp: [cams]}, cam, reason, city, street, lat, lon).
    Returns the items of one continuous timeline, newest first: drive, stopped, recording (telemetry not read yet), sentry, parked, gap."""
    items = []
    ms = minutes(recent, saved)
    runs, cur = [], None
    for m in ms:
        if cur and m["t"] - cur[-1]["t"] <= OPTS["run_gap_s"]:
            cur.append(m)
        else:
            cur = [m]
            runs.append(cur)
    for run in runs:
        for part in _split(run, store):
            items.append(drive_item(part, store))
    for ev in sentry:
        if not ev["clips"]:
            continue
        sts = sorted(ev["clips"])
        t0, t1 = secs(sts[0]), secs(sts[-1]) + 60
        la, lo = ev.get("lat"), ev.get("lon")
        items.append(dict(id="s:" + ev["id"], kind="sentry", ev=ev["id"], start=iso(t0), end=iso(t1), t0=t0, t1=t1, n=len(sts), reason=ev.get("reason", ""),
                          cam=ev.get("cam", ""), city=ev.get("city", ""), street=ev.get("street", ""), trig=ev.get("ts"), p0=[la, lo] if la is not None else None,
                          p1=[la, lo] if la is not None else None, est=True))
    items.sort(key=lambda x: (x["t0"], x["t1"]))
    out = []
    for a, b in zip(items, items[1:] + [None]):
        out.append(a)
        if b and b["t0"] - a["t1"] > OPTS["gap_min_s"]:
            out.append(_gap(a, b))
    for it in out:                                      # shown on the clock of where it was (a drive that began in Queensland reads on Queensland's clock)
        show(it)
    return sorted(out, key=lambda x: x["t0"], reverse=True)


def _state(m, store):
    s = store.summary(m["stamp"])
    if s is None:
        return "unknown"
    return "move" if s and s["vmax"] >= OPTS["moving_kmh"] else "still"


def _split(run, store):
    st = [_state(m, store) for m in run]
    for i in range(1, len(st) - 1):                     # one still minute between two moving ones (a red light) is part of the drive
        if st[i] in ("still", "unknown") and st[i - 1] == "move" and st[i + 1] == "move":
            st[i] = "move"
    parts, cur = [], []
    for m, s in zip(run, st):
        if cur and cur[-1][1] != s:
            parts.append(cur)
            cur = []
        cur.append((m, s))
    if cur:
        parts.append(cur)
    return [[(m, s) for m, s in p] for p in parts]


def drive_item(part, store):
    state = part[0][1]
    mm = [m for m, _ in part]
    t0, t1 = mm[0]["t"], mm[-1]["t"] + 60
    sums = [store.summary(m["stamp"]) for m in mm]
    sums = [s for s in sums if s]
    kind = {"move": "drive", "still": "stopped", "unknown": "recording"}[state]
    km = sum(s["km"] for s in sums)
    mov = sum(s["mov"] for s in sums)
    fsd = sum(s["fsd"] for s in sums)
    aut = sum(s["aut"] for s in sums)
    p0 = next((s["p0"] for s in sums if s["p0"]), None)
    p1 = next((s["p1"] for s in reversed(sums) if s["p1"]), None)
    it = dict(id=mm[0]["stamp"], kind=kind, start=iso(t0), end=iso(t1), t0=t0, t1=t1, n=len(mm), recent=sum(1 for m in mm if m["src"] == "recent"),
              saved=sum(1 for m in mm if m["src"] == "saved"), read=len(sums), km=round(km, 1), vmax=round(max([s["vmax"] for s in sums] or [0])),
              fsd=min(1.0, round(fsd / mov, 2)) if mov else 0, aut=min(1.0, round(aut / mov, 2)) if mov else 0, p0=p0, p1=p1)
    if kind == "drive" and sums:                # a minute that could not be read (or is still to be read) does not stop the route being drawn
        it["thumb"] = _thumb(mm, store)
    return it


def _thumb(mm, store, k=40):
    pts = []
    for m in mm:
        ser = store.series(m["stamp"])
        if ser:
            pts += [(a, b) for a, b in zip(ser["lat"][::6], ser["lon"][::6]) if _valid(a, b)]
    if len(pts) > k:
        step = len(pts) / float(k)
        pts = [pts[int(i * step)] for i in range(k)] + [pts[-1]]
    return [[round(a, 4), round(b, 4)] for a, b in pts]


def _gap(a, b):
    t0, t1 = a["t1"], b["t0"]
    pa, pb = a.get("p1"), b.get("p0")
    kind, km = "gap", None
    if pa and pb and pa[0] is not None and pb[0] is not None:
        d = hav(pa[0], pa[1], pb[0], pb[1])
        lim = OPTS["same_place_est_m"] if (a.get("est") or b.get("est")) else OPTS["same_place_m"]
        km = round(d / 1000.0, 1)
        kind = "parked" if d <= lim else "gap"
    return dict(id="g:%d" % t0, kind=kind, start=iso(t0), end=iso(t1), t0=t0, t1=t1, km=km, p0=pa, p1=pb)


def cached_minutes(store, t0, t1):
    """The minutes already read that overlap [t0, t1) - for when the drive they came from is not connected (telemetry only, no video)."""
    return [dict(stamp=st, t=secs(st), src="cache", ref="", cams=[]) for st in sorted(store.stamps()) if secs(st) + 60 > t0 and secs(st) < t1]


def detail(mins, store, item):
    """The telemetry of a drive item for the map and charts: per-second lists over the item's whole time, with None where a minute was not read."""
    out = dict(t0=item["t0"], v=[], lat=[], lon=[], hd=[], ap=[], steer=[], accel=[], br=[], blk=[], gear=[])
    keys = ("v", "lat", "lon", "hd", "ap", "steer", "accel", "br", "blk", "gear")
    pending = 0
    for m in mins:
        ser = store.series(m["stamp"])
        off = int(round(m["t"] - item["t0"]))
        while len(out["v"]) < off:
            for k in keys:
                out[k].append(None)
        if len(out["v"]) > off:                         # overlap: keep what is there
            for k in keys:
                del out[k][off:]
        if ser:
            for k in keys:
                out[k] += ser[k]
        else:
            if store.summary(m["stamp"]) is None:
                pending += 1
            for k in keys:
                out[k] += [None] * 60
    out["pending"] = pending
    return out
