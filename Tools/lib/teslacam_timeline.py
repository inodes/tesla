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
            join_max_s=300,           # a longer hole than run_gap_s (the car skipped a few seconds) still joins two MOVING minutes whose positions fit: up to this long ...
            join_slack_m=300,         # ... when the two positions are no further apart than this plus the distance the car could have covered in the hole (at 1.5 x its top speed)
            gap_min_s=120,            # a hole shorter than this is not shown
            same_place_m=300,         # parked: last and next position this close ...
            same_place_est_m=800,     # ... (this far when one of them is a Sentry event's approximate position)
            blip_kmh=1.0,             # a shift out of Park whose top speed stays under this did not move the car: a blip (nothing), not a drive or even a re-park
            short_m=200,              # a drive shorter than this is marked short (a re-park: the car was only moved a little)
            same_minute_s=30,         # a Saved minute this close to a Recent one is the same minute
            read_workers=4)           # clips read at once (the reading is CPU work)
_LOCK = threading.RLock()


def secs(stamp):
    """UTC epoch of a clip's name: the name is the CAR's clock, which clip_clock turns into UTC (it can differ from local time, and change mid-drive)."""
    return CC.utc(stamp)


def iso(t, lo=None):
    """Display text (local clock) of a UTC epoch: the configured zone's, or a clock lo seconds ahead of UTC. Never compared, only shown."""
    return CC.local_iso(t, lo)


def show(it, lo=None, own_end=False):
    """Sets the display fields of a timeline item (start, end, lo, lo1) from its UTC t0 / t1: the start on the clock of where it began (its first position), else
    app.timezone's, and the end on the clock of where it ended (its last position), so a drive that crosses a border reads 14:40 to 17:35, not 14:40 to 16:35.
    lo / lo1 = the start's and the end's offsets from UTC in seconds. lo given = one clock for both, unless own_end: lo is then only the start's and the end still reads on the clock of its own last position. Display only - nothing is ever compared on these texts."""
    lo1 = lo
    if lo is not None and own_end:
        q = it.get("p1") or it.get("p0")
        if q and q[0] is not None:
            lo1 = CC.place_offset(it["t1"], q[0], q[1])
    if lo is None:
        p = it.get("p0") or it.get("p1")
        lo = CC.place_offset(it["t0"], p[0], p[1]) if p and p[0] is not None else CC.local_offset(it["t0"])
        q = it.get("p1") or it.get("p0")
        lo1 = CC.place_offset(it["t1"], q[0], q[1]) if q and q[0] is not None else lo
    it.update(lo=lo, lo1=lo1, start=CC.local_iso(it["t0"], lo), end=CC.local_iso(it["t1"], lo1))
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
        sm = self.sum
        if sm is not None:                  # loaded: a dict lookup needs no lock (asked tens of thousands of times for one timeline)
            return sm.get(stamp)
        with _LOCK:
            return self._load_sum().get(stamp)

    def series(self, stamp):
        d = self.days.get(stamp[:10])
        if d is not None:
            return d.get(stamp)
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
                             fsd=sum(1 for x, y in zip(ser["ap"], v) if x == 1 and y >= OPTS["moving_kmh"]),
                             aut=sum(1 for x, y in zip(ser["ap"], v) if x in (2, 3) and y >= OPTS["moving_kmh"]),
                             fsdk=round(sum(y for x, y in zip(ser["ap"], v) if x == 1) / 3600.0, 3),
                             autk=round(sum(y for x, y in zip(ser["ap"], v) if x in (2, 3)) / 3600.0, 3),
                             p0=list(pos[0]) if pos else None, p1=list(pos[-1]) if pos else None)
            day = stamp[:10]
            self.series(stamp)                  # loads the day
            self.days[day][stamp] = ser
            self._dirty.add(day)

    def forget(self, stamps):
        """Drops what was read for these minutes (their summary and per-second series). Returns how many had something."""
        n = 0
        with _LOCK:
            sm = self._load_sum()
            for st in stamps:
                if sm.pop(st, None) is not None:
                    n += 1
                day = st[:10]
                self.series(st)                     # loads the day
                if self.days.get(day, {}).pop(st, None) is not None:
                    self._dirty.add(day)
            self.flush()
        return n

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
TELEM_CAMS = ("front", "back", "left_repeater", "right_repeater", "left_pillar", "right_pillar")


def tcam(cams):
    """The camera whose clip the telemetry is read from: the front, else any other (the car writes the same speed, GPS and pedals into all six)."""
    return next((c for c in TELEM_CAMS if c in cams), None)


def overlaps_recent(stamp, t, rt):
    """True when the clip named `stamp` (UTC `t`) covers footage a Recent minute (sorted UTC starts `rt`, a minute long) already holds.
    A clip the car closed early (a Sentry or Saved clip of 9 s, say, whose own header says so) that falls in the hole between two Recent minutes is NOT a repeat of either, even
    though its name is within same_minute_s of one: dropping it leaves a black hole where there is footage. A whole-minute clip, or one of unknown length, is a repeat."""
    d = CC.duration(stamp)
    if d is None or d >= 55:
        return True
    a, b = t + 1.0, t + d - 1.0
    return any(r < b and a < r + 60.0 for r in rt if abs(r - t) < 90)


def minutes(recent, saved, all_saved=False):
    """Every Recent and Saved minute, oldest first, each once (a Saved minute that is also in Recent is left out unless `all_saved` asks for every distinct stamp)."""
    import bisect
    mins = {}
    for st, cams in recent.items():
        mins[st] = dict(stamp=st, t=secs(st), src="recent", ref="", cams=cams, tcam=tcam(cams))
    rt = sorted(m["t"] for m in mins.values())
    for ev in saved:
        for st, cams in ev["clips"].items():
            t = secs(st)
            i = bisect.bisect(rt, t)
            near = min([abs(rt[j] - t) for j in (i - 1, i) if 0 <= j < len(rt)] or [1e9])
            if (all_saved or near > OPTS["same_minute_s"] or not overlaps_recent(st, t, rt)) and st not in mins:
                mins[st] = dict(stamp=st, t=t, src="saved", ref=ev["id"], cams=cams, tcam=tcam(cams))
    return sorted(mins.values(), key=lambda m: m["t"])


def build(recent, saved, sentry, store, cache=None):
    """recent {stamp: [cams]}; saved / sentry = lists of events (id, ts, clips {stamp: [cams]}, cam, reason, city, street, lat, lon).
    Returns the items of one continuous timeline, newest first: drive, stopped, recording (telemetry not read yet), sentry, parked, gap.
    With `cache` (a DayCache) only the recordings on days whose sources changed since the last build are worked out again; the other days are taken from
    that build. The answer is the same either way (tests/test_timeline_days.py)."""
    if cache is None:
        return _build(recent, saved, sentry, store, None)
    with cache.lock:                                    # one build at a time uses the cache
        return _build(recent, saved, sentry, store, cache)


def _build(recent, saved, sentry, store, cache):
    items = []
    ms = minutes(recent, saved)
    if cache is not None:
        cache.begin(ms, store)
    runs, cur = [], None
    for m in ms:
        if cur and (m["t"] - cur[-1]["t"] <= OPTS["run_gap_s"] or (cache.joins(cur[-1], m, store) if cache is not None else _continues(cur[-1], m, store))):
            cur.append(m)
        else:
            cur = [m]
            runs.append(cur)
    for run in runs:
        seq = cache.kept_run(run) if cache is not None else None
        if seq is None:
            seq = []
            for part in _split(run, store):
                seq += _part_items(part, store)
            _butt(seq)
            if cache is not None:
                cache.keep_run(run, seq)
        items += seq
    if cache is not None:
        cache.end()
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


_CODE = []


def _code_print():
    """What the code that makes the items is: a different version of this file (or of the cache) means nothing kept can be used."""
    if not _CODE:
        import hashlib
        try:
            with open(os.path.abspath(__file__), "rb") as fh:
                _CODE.append(hashlib.md5(fh.read()).hexdigest())
        except OSError:
            _CODE.append("?")
    return "%d:%s" % (DayCache.VERSION, _CODE[0])


class DayCache:
    """The timeline's recordings from the last build, by the day (the car's clock) of their minutes, so a build works out again only the days whose sources changed.

    A day's sources are its minutes (name, UTC time, Recent or Saved, cameras), the summary kept for each, and its file of per-second readings (<day>.json, by its
    time and size; a day read in this program and not yet written out counts as changed). A recording that touches a changed day, or that is not the same list
    of minutes as before, is worked out again; so is a join over a short hole between two minutes when either day changed. Sentry events, the holes between
    items and the clock they are shown on are always done afresh (they are quick). A change to the thresholds (OPTS) or to this code starts again from nothing.

    `path` keeps a copy on this computer (a local cache folder, not the data folder), so a start of the app has the last build to begin from."""

    VERSION = 1

    def __init__(self, path=None):
        self.path = path
        self.lock = threading.RLock()
        self._val = None                    # {"key", "days": {day: print}, "joins": {"a|b": bool}, "runs": {first stamp: {"stamps", "items"}}}
        self._clean = set()
        self._new = self._old = None
        self._changed = False
        self.stats = dict(days=0, rebuilt_days=0, runs=0, rebuilt_runs=0)

    def _load(self):
        if self._val is None:
            self._val = {}
            if self.path:
                try:
                    with open(self.path, encoding="utf-8") as fh:
                        self._val = json.load(fh)
                except (OSError, ValueError):
                    self._val = {}
        return self._val

    @staticmethod
    def _key():
        return [_code_print(), json.dumps(OPTS, sort_keys=True)]

    @staticmethod
    def day_prints(ms, store):
        """{day: a short print of everything a build reads for that day}; None for a day that is always worked out again."""
        import hashlib
        by = {}
        for m in ms:
            by.setdefault(m["stamp"][:10], []).append([m["stamp"], m["t"], m["src"], m["ref"], list(m["cams"]), m["tcam"], store.summary(m["stamp"])])
        base, dirty = getattr(store, "base", None), getattr(store, "_dirty", None) or set()
        out = {}
        for day, rows in by.items():
            if day in dirty:
                out[day] = None                         # read here and not written out yet: always worked out again
                continue
            if base is not None:
                try:
                    st = os.stat(os.path.join(base, day + ".json"))
                    rows.append([st.st_mtime_ns, st.st_size])
                except OSError:
                    rows.append(None)
            else:                                       # a store with no files (tests): the readings themselves
                rows.append([store.series(r[0]) for r in rows])
            out[day] = hashlib.md5(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return out

    def begin(self, ms, store):
        old = self._load()
        key = self._key()
        if old.get("key") != key:
            old = dict(key=key, days={}, joins={}, runs={})
        prints = self.day_prints(ms, store)
        self._clean = {d for d, p in prints.items() if p is not None and old.get("days", {}).get(d) == p}
        self._old = old
        self._new = dict(key=key, days=prints, joins={}, runs={})
        self._changed = old is not self._val or set(prints) != set(old.get("days") or {}) or len(self._clean) != len(prints)
        self.stats = dict(days=len(prints), rebuilt_days=len(prints) - len(self._clean), runs=0, rebuilt_runs=0)

    def joins(self, a, b, store):
        """_continues(a, b), taken from the last build when both minutes' days are unchanged."""
        if b["t"] - a["t"] > OPTS["join_max_s"]:
            return False                                # never joined: nothing to keep
        k = a["stamp"] + "|" + b["stamp"]
        got = self._old["joins"].get(k)
        if got is None or a["stamp"][:10] not in self._clean or b["stamp"][:10] not in self._clean:
            got = _continues(a, b, store)
            self._changed = self._changed or self._old["joins"].get(k) != got
        self._new["joins"][k] = got
        return got

    def kept_run(self, run):
        """The items of this recording from the last build (copies), or None when it has to be worked out."""
        self.stats["runs"] += 1
        sts = [m["stamp"] for m in run]
        got = self._old["runs"].get(sts[0])
        if got is None or got["stamps"] != sts or any(st[:10] not in self._clean for st in sts):
            self.stats["rebuilt_runs"] += 1
            return None
        self._new["runs"][sts[0]] = got
        return json.loads(json.dumps(got["items"]))

    def keep_run(self, run, seq):
        sts = [m["stamp"] for m in run]
        self._new["runs"][sts[0]] = dict(stamps=sts, items=json.loads(json.dumps(seq)))
        self._changed = True

    def end(self):
        new, old = self._new, self._old
        if self._changed or set(new["runs"]) != set(old.get("runs") or {}) or set(new["joins"]) != set(old.get("joins") or {}):
            self._save(new)
        self._val = new
        self._new = self._old = None

    def _save(self, val):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(val, fh, separators=(",", ":"))
            os.replace(tmp, self.path)
        except (OSError, TypeError, ValueError):
            pass


def _continues(a, b, store):
    """True when minute b follows minute a after a hole in the recording (more than run_gap_s, up to join_max_s) and the drive did not stop in between: the car was not in
    Park at the end of a nor at the start of b (the car's own gear; a drive ends when it goes into Park). Where the gear is not known (no telemetry read, or none in the
    clip) it falls back to the car moving at both ends and being where it could have got to (top speed x 1.5 over the hole, plus join_slack_m)."""
    dt = b["t"] - a["t"]
    if dt > OPTS["join_max_s"]:
        return False
    ga, gb = gear_of(a, store), gear_of(b, store)
    if ga is not None and gb is not None:
        return ga[1] != 0 and gb[0] != 0
    sa, sb = store.summary(a["stamp"]), store.summary(b["stamp"])
    if not sa or not sb or sa["vmax"] < OPTS["moving_kmh"] or sb["vmax"] < OPTS["moving_kmh"]:
        return False
    p, q = sa.get("p1"), sb.get("p0")
    if not p or not q or not _valid(p[0], p[1]) or not _valid(q[0], q[1]):
        return False
    d = math.hypot((q[0] - p[0]) * 111320.0, (q[1] - p[1]) * 111320.0 * math.cos(math.radians(p[0])))
    can = OPTS["join_slack_m"] + max(0, dt - 60) * max(sa["vmax"], sb["vmax"]) / 3.6 * 1.5
    return d <= can


def gear_of(m, store):
    """(first, last, any_in_gear) of the car's gear over a minute: 0 = Park, 1 = Drive, 2 = Reverse, 3 = Neutral; None when the minute has no telemetry series. The car's own
    gear is what says a drive has stopped: Park ends a drive, while a stop at a light or in a queue stays in Drive and is still the drive."""
    ser = store.series(m["stamp"]) if m["stamp"] and store.summary(m["stamp"]) else None
    g = (ser or {}).get("gear")
    if not g:
        return None
    return g[0], g[-1], any(x != 0 for x in g)


def _state(m, store):
    s = store.summary(m["stamp"])
    if s is None:
        return "unknown"
    g = gear_of(m, store) if s else None
    if g is not None:
        return "move" if g[2] else "still"                  # in gear at any moment of the minute = driving; all Park = parked
    return "move" if s and s["vmax"] >= OPTS["moving_kmh"] else "still"


def _split(run, store):
    st = [_state(m, store) for m in run]
    for i in range(1, len(st) - 1):                     # one still minute between two moving ones (a red light) is part of the drive - where the gear is not known:
        if st[i] in ("still", "unknown") and st[i - 1] == "move" and st[i + 1] == "move" and gear_of(run[i], store) is None and _glitch(run, i, store):
            st[i] = "move"
    i = 0
    while i < len(st):                                  # minutes in gear only for a blip (no movement) are a stop, not a drive
        if st[i] != "move":
            i += 1
            continue
        j = i
        while j < len(st) and st[j] == "move":
            j += 1
        if _moves(run[i:j], store) == []:
            st[i:j] = ["still"] * (j - i)
        i = j
    parts, cur = [], []
    for m, s in zip(run, st):
        if cur and cur[-1][1] != s:
            parts.append(cur)
            cur = []
        cur.append((m, s))
    if cur:
        parts.append(cur)
    return [[(m, s) for m, s in p] for p in parts]


def _glitch(run, i, store):
    """True when minute i, which has no gear of its own (no telemetry in it, or not read yet), sits inside a drive: the car was in gear at the end of the minute before and at the start
    of the one after. A car in Park writes no telemetry after a few seconds, so a minute without any between a Park and a drive is the stop. Where the neighbours' gear is not known: yes."""
    ga, gb = gear_of(run[i - 1], store), gear_of(run[i + 1], store)
    return ga is None or gb is None or (ga[1] != 0 and gb[0] != 0)


def _samples(mm, store):
    """Every second of these minutes as (t, gear, v, lat, lon, ap, minute index), oldest first; None when a minute has no gear series (not read, or none in the clip)."""
    out = []
    for k, m in enumerate(mm):
        ser = store.series(m["stamp"]) if m["stamp"] and store.summary(m["stamp"]) else None
        g = (ser or {}).get("gear")
        if not g:
            return None
        n = len(g)
        v, la, lo, ap = ser["v"], ser.get("lat") or [None] * n, ser.get("lon") or [None] * n, ser.get("ap") or [0] * n
        out += [(m["t"] + i, g[i], v[i], la[i], lo[i], ap[i], k) for i in range(n)]
    return out


def _excursions(S):
    """[(a, b)] sample indexes of every stretch out of Park in which the car moved (top speed OPTS blip_kmh or more). Park ends one; what follows it is another. A stretch with
    no movement - a shift out of Park and back - is a blip and is left out. A stop at a light stays in Drive, so it stays inside its stretch."""
    out, cur = [], None
    for k, s in enumerate(S):
        if s[1] != 0:
            cur = [k, k, 0.0] if cur is None else cur
            cur[1] = k
            cur[2] = max(cur[2], abs(s[2] or 0))
        elif cur:
            out.append(cur)
            cur = None
    if cur:
        out.append(cur)
    return [(a, b) for a, b, v in out if v >= OPTS["blip_kmh"]]


def _moves(mm, store):
    """The drives inside these minutes cut by the car's gear: a list of (a, b) (see _excursions), empty when it only shifted gear; None when the gear is not known for them all."""
    S = _samples(mm, store)
    return None if S is None else _excursions(S)


def _part_items(part, store):
    """The items of one part of a run: a moving part is cut to the second into its drives where the gear is known, else it is one item of whole minutes."""
    if part[0][1] == "move":
        mm = [m for m, _ in part]
        S = _samples(mm, store)
        ex = _excursions(S) if S is not None else []
        if ex:
            return [seg_item(mm, S, a, b) for a, b in ex]
    return [drive_item(part, store)]


def _label(m, t):
    """A name in the form of a clip's, for the second t inside minute m: its own name when t is the minute's start, else the name plus the seconds (an id only)."""
    k = int(round(t - m["t"]))
    if not k:
        return m["stamp"]
    import datetime
    d = datetime.datetime.strptime(m["stamp"], "%Y-%m-%d_%H-%M-%S") + datetime.timedelta(seconds=k)
    return d.strftime("%Y-%m-%d_%H-%M-%S")


def seg_item(mm, S, a, b):
    """One drive from second a to second b of the samples S of the minutes mm: starts the second the car left Park, ends the second it went into Park."""
    q = S[a:b + 1]
    t0, t1 = q[0][0], q[-1][0] + 1
    ms = sorted({s[6] for s in q})
    mine = [mm[k] for k in ms]
    v = [s[2] or 0 for s in q]
    pts = [(s[3], s[4]) for s in q if s[3] is not None and _valid(s[3], s[4])]
    mov = sum(1 for x in v if x >= OPTS["moving_kmh"])
    fsd = sum(1 for s in q if s[5] == 1)
    aut = sum(1 for s in q if s[5] in (2, 3))
    km = sum(v) / 3600.0
    it = dict(id=_label(mm[ms[0]], t0), kind="drive", start=iso(t0), end=iso(t1), t0=t0, t1=t1, n=len(mine), recent=sum(1 for m in mine if m["src"] == "recent"),
              saved=sum(1 for m in mine if m["src"] == "saved"), read=len(mine), km=round(km, 1), vmax=round(max(v)), fsd=min(1.0, round(fsd / mov, 2)) if mov else 0,
              aut=min(1.0, round(aut / mov, 2)) if mov else 0, p0=[round(pts[0][0], 5), round(pts[0][1], 5)] if pts else None,
              p1=[round(pts[-1][0], 5), round(pts[-1][1], 5)] if pts else None, short=km * 1000.0 < OPTS["short_m"])
    it["thumb"] = _thumb_pts(pts[::6])
    return it


def _butt(seq):
    """A stop beside a drive that begins or ends within a minute runs up to the second the drive begins / ended (no sliver between them)."""
    for i, s in enumerate(seq):
        if s["kind"] != "stopped":
            continue
        p, n = (seq[i - 1] if i else None), (seq[i + 1] if i + 1 < len(seq) else None)
        if p and p["kind"] == "drive" and abs(s["t0"] - p["t1"]) < 60 and p["t1"] < s["t1"]:
            s["t0"] = p["t1"]
        if n and n["kind"] == "drive" and abs(n["t0"] - s["t1"]) < 60 and n["t0"] > s["t0"]:     # a clip shorter than 60 s can leave its minute overlapping the next drive
            s["t1"] = n["t0"]


def _share(sums, nkey, kkey, km, n, mov):
    """The share of the drive on a driving mode: by distance when every minute has it (stored from the speeds), else by moving seconds (older stored minutes); never above 1."""
    if km and all(kkey in x for x in sums):
        return min(1.0, round(sum(x[kkey] for x in sums) / km, 2))
    return min(1.0, round(n / mov, 2)) if mov else 0


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
              fsd=_share(sums, "fsd", "fsdk", km, fsd, mov), aut=_share(sums, "aut", "autk", km, aut, mov), p0=p0, p1=p1)
    if kind == "drive" and sums:                # a minute that could not be read (or is still to be read) does not stop the route being drawn
        it["thumb"] = _thumb(mm, store)
    return it


def _thumb(mm, store, k=40):
    pts = []
    for m in mm:
        ser = store.series(m["stamp"])
        if ser:
            pts += [(a, b) for a, b in zip(ser["lat"][::6], ser["lon"][::6]) if _valid(a, b)]
    return _thumb_pts(pts, k)


def _thumb_pts(pts, k=40):
    if len(pts) > k:
        step = len(pts) / float(k)
        pts = [pts[int(i * step)] for i in range(k)] + [pts[-1]]
    return [[round(a, 4), round(b, 4)] for a, b in pts]


PLACE_ADJ_S = 300           # a stop this close to a drive (before or after it) is where that drive ended or began
PLACE_CARRY_S = 86400       # a stop with no drive beside it is where the last known position was, when that was no longer ago than this (an estimate)


def fill_places(items, moves=None, moves_upto=0):
    """The stops and recordings the dashcam made while the car was parked have no GPS in them. Gives each the position it must have had, from the best source:
      1. a drive that ended just before it or began just after it (assumed, not an estimate). A drive that ends in Park and one that begins from it, both beside the
         stop and no further apart than same_place_m, make the best evidence: the stop is sandwiched between two drives at one place (psrc "both");
      2. another record's drives (Tessie: `moves` = [dict(t0, t1, p0, p1)], complete up to `moves_upto`): the drive that ended since the last known position, else the last
         known position when that record shows no drive since (confirmed);
      3. the last known position alone, no more than PLACE_CARRY_S before (an estimate: pest=True).
    Returns new items (those that got a position are copies with p0, p1, pest and psrc, and their display times worked out again on the clock of that place: a stop in
    Queensland reads on Queensland's clock); the rest are as they were. psrc: "both" | "before" | "after" (a drive beside it), "stay" (the stop beside it, which has a place),
    "tessie" or "last" (another record, or the last known position)."""
    asc = sorted(items, key=lambda x: (x["t0"], x["t1"]))
    ok = lambda p: bool(p) and p[0] is not None
    drives = [x for x in asc if x["kind"] == "drive"]
    known = None                                        # (position, time it was last there, was it an estimate)
    out = []
    for it in asc:
        if it["kind"] == "drive":
            if ok(it.get("p1")):
                known = (it["p1"], it["t1"], False)
            out.append(it)
            continue
        if it["kind"] not in ("stopped", "recording") or ok(it.get("p0")) or ok(it.get("p1")):
            if ok(it.get("p1")) and it["kind"] not in ("sentry", "gap"):
                known = (it["p1"], it["t1"], bool(it.get("pest")))
            out.append(it)
            continue
        pos, est, src = None, False, ""
        prev = [d for d in drives if d["t1"] <= it["t0"] + 1 and it["t0"] - d["t1"] <= PLACE_ADJ_S and ok(d.get("p1"))]
        nxt = [d for d in drives if d["t0"] >= it["t1"] - 1 and d["t0"] - it["t1"] <= PLACE_ADJ_S and ok(d.get("p0"))]
        if prev and nxt and hav(prev[-1]["p1"][0], prev[-1]["p1"][1], nxt[0]["p0"][0], nxt[0]["p0"][1]) <= OPTS["same_place_m"]:
            pos, src = prev[-1]["p1"], "both"
        elif prev:
            pos, src = prev[-1]["p1"], "before"
        elif nxt:
            pos, src = nxt[0]["p0"], "after"
        else:
            mv = [m for m in (moves or []) if ok(m.get("p1")) and m["t1"] <= it["t0"]]
            since = [m for m in mv if known is None or m["t1"] > known[1]]
            complete = bool(moves) and moves_upto >= it["t0"]
            if since:
                pos, src = max(since, key=lambda m: m["t1"])["p1"], "tessie"
            elif known and it["t0"] - known[1] <= PLACE_CARRY_S:
                pos, est, src = known[0], known[2] or not complete, "last"
            elif known is None and complete and mv:
                pos, src = max(mv, key=lambda m: m["t1"])["p1"], "tessie"
        if pos:
            it = show(dict(it, p0=list(pos), p1=list(pos), pest=est, psrc=src))
            if not est:
                known = (pos, it["t1"], False)                  # an estimate does not make the last known place any newer
        out.append(it)
    for rng in (range(len(out) - 2, -1, -1), range(1, len(out))):        # stops that follow on from one another are one stay: a stop with no place beside a stop that has one is there too
        for i in rng:
            it = out[i]
            if it["kind"] not in ("stopped", "recording") or ok(it.get("p0")) or ok(it.get("p1")):
                continue
            j = i + 1 if rng.step < 0 else i - 1
            nb = out[j] if 0 <= j < len(out) else None
            if nb and nb["kind"] in ("stopped", "recording") and ok(nb.get("p0")) and abs((nb["t0"] - it["t1"]) if j > i else (it["t0"] - nb["t1"])) <= PLACE_ADJ_S:
                out[i] = show(dict(it, p0=list(nb["p0"]), p1=list(nb["p0"]), pest=bool(nb.get("pest")), psrc="stay"))
    return out


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
    lo, hi = t0 - 60000, t1 + 60000                 # the car's clock is within +-15 h of UTC: only the minutes near the window need their offset worked out
    out = []
    for st in sorted(store.stamps()):
        if not lo < CC.naive(st) < hi:
            continue
        t = secs(st)
        if t + 60 > t0 and t < t1:
            out.append(dict(stamp=st, t=t, src="cache", ref="", cams=[]))
    return out


def detail(mins, store, item):
    """The telemetry of a drive item for the map and charts: per-second lists over the item's whole time, with None where a minute was not read."""
    out = dict(t0=item["t0"], v=[], lat=[], lon=[], hd=[], ap=[], steer=[], accel=[], br=[], blk=[], gear=[])
    keys = ("v", "lat", "lon", "hd", "ap", "steer", "accel", "br", "blk", "gear")
    pending = 0
    base = min([m["t"] for m in mins] + [item["t0"]])           # the first minute starts at or before the item (a drive begins within a minute)
    for m in mins:
        ser = store.series(m["stamp"])
        off = int(round(m["t"] - base))
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
    lo, hi = int(round(item["t0"] - base)), int(round(item["t1"] - base))
    for k in keys:
        out[k] = out[k][lo:hi]
    out["pending"] = pending
    return out
