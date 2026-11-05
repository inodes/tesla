"""run_cuts.py - where a Recent recording is cut into its drive and parked sections.

The car writes one continuous run of one-minute clips for as long as it records; a run can hold a park, a drive and a park. For the TeslaCam list that is
three things, so a run is cut where the timeline (teslacam_timeline.py, the same one the Drives list is made from) changes from a parked spell to a drive,
or back. Every place that groups the minutes into runs (the list, the player, what is kept) asks `is_cut(stamp)` and starts a new run at a cut, so a run is
named by its first minute everywhere, as before.

    update(stamps, items) -> works the cuts out from the timeline's items (kind drive | stopped | parked | recording, t0, t1), keeps them in the file
    is_cut(stamp)         -> True when a run starts at this minute although the minute before it is close enough to belong to the same run
    kind(stamp), pos(stamp) -> what the section that starts at this minute is: "drive" or "park" ("" when not known), and where (lat, lon) when known
    section(stamp)        -> (kind, lat, lon) of the section any minute lies in; None when the minute is in no section worked out
    section_last(stamp)   -> UTC time of the last minute of that section
    version()             -> changes whenever the cuts do

A minute is told apart by its middle. A minute with no reading (kind "recording": the telemetry has not been read) takes the section it follows, so it
never makes a cut of its own.
"""
import bisect
import json
import os
import threading

import clip_clock as CC

MIN_UNREAD = 180            # seconds: an unread stretch this long after a drive is a parked section (a shorter one is a hole in the drive)
ADJ_S = 300                 # seconds: a parked section this close to a drive (before or after it) is where that drive ended or began
CARRY_S = 86400             # seconds: a parked minute with no position of its own takes the one of the section before it when that ended no longer ago than this
GAP = 75                    # as in app_teslacam: minutes this close are one run
PAUSE_MIN = 5               # minutes: a spell with telemetry but the car standing still, between two minutes moving, is a pause in that drive (a door to open, a child to put in)
SHIFT_MAX = 3               # minutes (clips): how far the other record (Tessie) may move the start or end of a drive found from the clips
_LOCK = threading.RLock()
_S = {}                     # first minute of a section -> [kind, lat, lon, cut, UTC time of its last minute]
_IX = (None, [], [])        # (version, start times, start stamps) for section()
_PATH = None
_VER = 0
_ON_CHANGE = []


def set_file(path):
    """Where the cuts are kept across runs of the app; loads them."""
    global _PATH, _S, _VER
    with _LOCK:
        _PATH = path
        try:
            with open(path) as f:
                _S = {k: list(v) for k, v in json.load(f).items()}
        except (OSError, ValueError):
            _S = {}
        _VER += 1


def on_change(fn):
    _ON_CHANGE.append(fn)


def version():
    return _VER


def is_cut(stamp):
    s = _S.get(stamp)
    return bool(s and s[3])


def kind(stamp):
    s = _S.get(stamp)
    return s[0] if s else ""


def section(stamp):
    global _IX
    with _LOCK:
        if _IX[0] != _VER:
            ks = sorted(_S, key=CC.utc)
            _IX = (_VER, [CC.utc(k) for k in ks], ks)
        _v, ts, ks = _IX
        t = CC.utc(stamp)
        i = bisect.bisect_right(ts, t) - 1
        if i < 0:
            return None
        s = _S.get(ks[i])                                   # (the index may be older than the sections: a test that sets them straight)
        if s is None or len(s) < 5 or s[4] is None or t > s[4]:
            return None
        return s[0], s[1], s[2]


def drive_edges(first, last):
    """(lead, trail) for a run of minutes from `first` to `last` that is a parked section: True when a drive's own section runs straight into it (lead: the drive was the section before, no more than a
    clip's gap away) or straight out of it (trail). The first or last minute of such a park is where the drive ended or began: the parking movements and the pulling away are in it."""
    with _LOCK:
        section(first)
        _v, ts, ks = _IX
        t0, t1 = CC.utc(first), CC.utc(last)
        i = bisect.bisect_left(ts, t0) - 1
        a = _S.get(ks[i]) if i >= 0 else None
        lead = bool(a and a[0] == "drive" and len(a) >= 5 and a[4] is not None and t0 - a[4] <= GAP + 60)
        j = bisect.bisect_right(ts, t1)
        b = _S.get(ks[j]) if j < len(ks) else None
        trail = bool(b and b[0] == "drive" and ts[j] - t1 <= GAP + 60)
        return lead, trail


def section_last(stamp):
    """UTC time of the last minute of the section this minute lies in; None when it lies in none."""
    with _LOCK:
        section(stamp)
        _v, ts, ks = _IX
        t = CC.utc(stamp)
        i = bisect.bisect_right(ts, t) - 1
        if i < 0:
            return None
        s = _S[ks[i]]
        return s[4] if len(s) >= 5 and s[4] is not None and t <= s[4] else None


def pos_est(stamp):
    s = _S.get(stamp)
    return bool(s and len(s) > 5 and s[5])


def pos(stamp):
    s = _S.get(stamp)
    return [s[1], s[2]] if s and s[1] is not None else None


def _cls(v):
    """A minute's class: "go" (telemetry, moving), "pk" (telemetry, standing still), "" (no telemetry), None (not known). True / False are "go" / ""."""
    return "go" if v is True else "" if v is False else v


def stop_marks(gear):
    """What a minute's gear says about a stop in Park: (seconds in Park at its start, seconds in Park at its end, where a stop inside it lies as a share 0..1 of the minute or None). One reading
    a second. A car in Park between two stretches of driving was stopped on purpose (a drop-off, a pick-up): checked against Tessie's drives, 5 of 5 stops that fall across the join of two clips are
    where Tessie ends one drive and begins the next, and about three in four inside a clip."""
    g = list(gear or [])
    n = len(g)
    if not n:
        return (0, 0, None)
    lead = next((i for i, x in enumerate(g) if x != 0), n)
    trail = next((i for i, x in enumerate(reversed(g)) if x != 0), n)
    mid, best, i = None, 0, lead
    while i < n - trail:
        if g[i] == 0:
            j = i
            while j < n and g[j] == 0:
                j += 1
            if j - i > best and j < n - trail:
                best, mid = j - i, (i + j) / 2.0 / n
            i = j
        else:
            i += 1
    return (lead if lead < n else 0, trail if trail < n else 0, mid)


def gear_edges(gear):
    """(seconds in gear at the start of the minute before Park first engages, seconds in gear at its end after Park last was left): the parking movements and the pulling away.
    A minute with no Park second, or none in gear, has none of either: (0, 0)."""
    g = list(gear or [])
    if 0 not in g:
        return (0, 0)
    head = g.index(0)
    tail = next((i for i, x in enumerate(reversed(g)) if x == 0), 0)
    return (head, tail)


def refine_edges(items, marks, stamp_at, edge_of=None):
    """The dashcam's own drives and parks, with the edge between a drive and a park at the second Park was engaged (or left) instead of the edge of a clip's minute.
    items: timeline items (dict(kind, t0, t1)), only those from the dashcam alone (Tessie's drives are already to the second); marks: {stamp: stop_marks(gear)};
    stamp_at: {UTC second: stamp of the minute that starts then}; edge_of(stamp) -> gear_edges or None, asked only for the minutes at a join.
    A drive cannot end in motion: the parking movements come before Park is engaged, and that can be in the next clip's minute (which the clips call a park). So the edge is
        drive -> park: the second Park was engaged in the drive's last minute, else in the park's first minute (gear before it);
        park -> drive: the second Park was left in the drive's first minute, else in the park's last minute (gear after it).
    The minute stays on both sides (a page shows every clip that overlaps its times); only the time on the heading, the thumbnail and the timeline's edge moves.
    Returns new item dicts."""
    out = sorted((dict(i) for i in items), key=lambda i: i["t0"])

    def st_of(t):
        for d in (0, -1, 1):
            st = stamp_at.get(int(round(t)) + d)
            if st is not None:
                return st
        return None

    def edge(t):
        st = st_of(t)
        return (edge_of(st) if edge_of and st else None) or (0, 0)

    for a, b in zip(out, out[1:]):
        if abs(a["t1"] - b["t0"]) > 2 or a.get("src") not in (None, "cam") or b.get("src") not in (None, "cam"):
            continue
        cut = None
        if a["kind"] == "drive" and b["kind"] != "drive":
            m = marks.get(st_of(a["t1"] - 60)) or (0, 0, None)
            if 0 < m[1] < 60:
                cut = a["t1"] - m[1]
            elif 0 < edge(b["t0"])[0] < 60:
                cut = b["t0"] + edge(b["t0"])[0]
        elif a["kind"] != "drive" and b["kind"] == "drive":
            m = marks.get(st_of(b["t0"])) or (0, 0, None)
            if 0 < m[0] < 60:
                cut = b["t0"] + m[0]
            elif 0 < edge(a["t1"] - 60)[1] < 60:
                cut = a["t1"] - edge(a["t1"] - 60)[1]
        if cut is not None and a["t0"] + 20 < cut < b["t1"] - 20:
            a["t1"] = b["t0"] = cut
    return out


def minute_sections(stamps, kinds, moves=None, moves_upto=0, marks=None):
    """{stamp: "drive" | "park"} for the minutes that have a class in `kinds`, from the clips themselves, in this order:
    1. a minute with the car moving is a drive, and every other minute is parked;
    2. a spell of standing-still-with-telemetry minutes (never a minute with no telemetry), PAUSE_MIN minutes at most, between two drive minutes is a pause: the drive goes on, unless the other record has a trip ending before the spell and another beginning after it (the car was parked there: two drives);
    3. the six cameras of a minute stay together and a file is never cut: the minute is the unit.
    Then the other record, when it has the drive: a drive found from the clips that a drive of Tessie's overlaps takes its start and end from Tessie's, when that moves either edge by no more than
    SHIFT_MAX minutes (the clips may begin a block early, or end a block late); where it has no drive, or the edges are further apart, the clips stand."""
    runs, cur = [], []
    for st in sorted((x for x in stamps if _cls(kinds.get(x)) is not None), key=CC.utc):
        if cur and CC.utc(st) - CC.utc(cur[-1]) > GAP:
            runs.append(cur)
            cur = []
        cur.append(st)
    if cur:
        runs.append(cur)
    mv = sorted((m for m in (moves or []) if m.get("t0") and m.get("t1")), key=lambda m: m["t0"])
    out = {}
    for run in runs:
        c = [_cls(kinds[st]) for st in run]
        d = [x == "go" for x in c]
        ts = [CC.utc(st) for st in run]

        def tessie_parked(i, j):
            """True when the other record, complete over the spell of minutes i..j-1, ends one trip before it and begins another after it: the car was parked there, not paused (a school drop-off
            that Tessie lists as two drives). One trip of Tessie's across the spell, or none, or a record that is not complete there, says nothing."""
            if not mv or moves_upto < ts[j] + 60:
                return False
            a = [m for m in mv if m["t0"] < ts[i] and m["t1"] > ts[i - 1] and m["t1"] <= ts[j - 1] + 60]
            b = [m for m in mv if m["t1"] > ts[j] and m["t0"] <= ts[j] + 60 and m["t0"] >= ts[i]]
            return bool(a and b and b[0]["t0"] - a[-1]["t1"] >= 30 and a[-1] is not b[0])
        i = 0
        while i < len(c):                                     # a pause between two minutes in gear
            if c[i] == "pk":
                j = i
                while j < len(c) and c[j] == "pk":
                    j += 1
                if 0 < i and j < len(c) and d[i - 1] and d[j] and j - i <= PAUSE_MIN and not tessie_parked(i, j):
                    for q in range(i, j):
                        d[q] = True
                i = j
            else:
                i += 1
        blocks, i = [], 0
        while i < len(d):
            if d[i]:
                j = i
                while j + 1 < len(d) and d[j + 1]:
                    j += 1
                blocks.append([i, j])
                i = j + 1
            else:
                i += 1
        for bi, (i0, i1) in enumerate(blocks):                # the other record, where it has the drive
            lo, hi = ts[i0], ts[i1] + 60
            if not mv or moves_upto < hi:
                continue
            hit = [m for m in mv if min(m["t1"], hi) - max(m["t0"], lo) >= 30]
            if not hit:
                continue
            if any(min(m["t1"], ts[j1] + 60) - max(m["t0"], ts[j0]) >= 30 for m in hit for q, (j0, j1) in enumerate(blocks) if q != bi):
                continue                                      # one trip of Tessie's across two drives of the clips: they disagree, the clips stand
            a = max((q for q in range(len(ts)) if ts[q] <= hit[0]["t0"]), default=0)
            b = max((q for q in range(len(ts)) if ts[q] <= hit[-1]["t1"]), default=len(ts) - 1)
            floor = blocks[bi - 1][1] + 1 if bi else 0
            ceil = blocks[bi + 1][0] - 1 if bi + 1 < len(blocks) else len(ts) - 1
            a, b = max(a, floor), min(b, ceil)
            if a <= b and abs(a - i0) <= SHIFT_MAX and abs(b - i1) <= SHIFT_MAX:
                for q in range(i0, i1 + 1):
                    d[q] = False
                for q in range(a, b + 1):
                    d[q] = True
                blocks[bi] = [a, b]
        cut_before = set()                                    # a stop in Park between two stretches of driving: the drive is two drives (the minute is never cut: it goes with the side it mostly belongs to)
        for q in range(len(run) - 1):
            if d[q] and d[q + 1] and marks and run[q] in marks and run[q + 1] in marks:
                if marks[run[q]][1] + marks[run[q + 1]][0] >= 1:
                    cut_before.add(q + 1)
        for q in range(1, len(run) - 1):
            m = marks.get(run[q]) if marks else None
            if d[q - 1] and d[q] and d[q + 1] and m and m[2] is not None and kinds.get(run[q]) == "go":
                cut_before.add(q if m[2] < 0.5 else q + 1)
        n = 0
        for q, (st, x) in enumerate(zip(run, d)):
            if not x:
                out[st] = "park"
                n = 0                                         # a park between drives starts the count again
                continue
            if q in cut_before:
                n += 1
            out[st] = "drive" if n == 0 else "drive#%d" % n
    return out


def compute(stamps, items, moves=None, moves_upto=0, kinds=None, marks=None):
    """{first minute of every section: [kind, lat, lon, cut, last minute, estimated]} for these minutes and the timeline's items.
    moves: the drives some other record (Tessie) has, [dict(t0, t1, p0, p1)], or None when there is none; moves_upto: the time up to which that record is complete.
    kinds: {stamp: "go" | "pk" | ""} what each minute's own clip says (minute_sections): the dashcam comes first. A minute that has a class is a drive or a park by that; the other record
    (Tessie) only moves the edges of a drive by up to SHIFT_MAX minutes and gives a position, and a minute with none falls back on the timeline's items."""
    its = sorted((i for i in items if i.get("kind") in ("drive", "stopped", "parked", "recording")), key=lambda i: i["t0"])
    t0s = [i["t0"] for i in its]

    def at(t):
        k = bisect.bisect_right(t0s, t) - 1
        for j in (k, k - 1):
            if 0 <= j < len(its) and its[j]["t0"] <= t < its[j]["t1"]:
                return its[j]
        return None

    def moving(t):
        """The drive any second of the minute starting at t belongs to: a clip is a whole minute, so a minute the car moved in is part of the drive (it cannot be cut to the second)."""
        k = bisect.bisect_left(t0s, t + 60) - 1                  # the last item that begins before the minute ends
        for j in range(k, max(k - 3, -1), -1):
            x = its[j]
            if x["kind"] == "drive" and x["t0"] < t + 60 and x["t1"] > t:
                return x
        return None

    def where(it, kd):
        p = (it.get("p0") or it.get("p1")) if it else None
        return (p[0], p[1]) if p and p[0] is not None else (None, None)

    own = minute_sections(stamps, kinds or {}, moves, moves_upto, marks) if kinds else {}

    def move_item(t):
        for m in sorted((m for m in (moves or []) if m.get("t0") and m.get("t1")), key=lambda m: m["t0"]):
            if m["t0"] < t + 60 and m["t1"] > t:
                return dict(kind="drive", id="t%d" % m["t0"], t0=m["t0"], t1=m["t1"], p0=m.get("p0"), p1=m.get("p1"))
        return None

    out, key, last, sec, secit = {}, None, None, None, None
    ref = {}                                          # first minute of a section -> the timeline item it began in
    prev, days = None, {}                             # the minute before, and the local day of each minute looked at

    def day(x):
        d = days.get(x)
        if d is None:
            d = days[x] = str(CC.local_of(x))[:10]
        return d
    for st in sorted(stamps, key=CC.utc):
        t = CC.utc(st)
        it = moving(t) or at(t + 30)
        k = None
        if st in own:                                     # the clips say: a drive or a park; the timeline's item and Tessie's drive only give the position
            k = ("drive", "c" + own[st][5:]) if own[st].startswith("drive") else ("park", "")
            if k[0] == "drive" and not (it and it["kind"] == "drive"):
                it = move_item(t) or it
            elif k[0] == "park" and it and it["kind"] == "drive":
                it = dict(it, kind="stopped")             # not a drive by its clips: the timeline's drive item gives only its place
        elif it and it["kind"] == "drive":
            k = ("drive", it["id"])
        elif it and it["kind"] in ("stopped", "parked"):
            k = ("park", "")
        elif it and it["kind"] == "recording" and key is not None and key[0] == "drive" and it["t1"] - it["t0"] >= MIN_UNREAD:
            k = ("park", "")                              # minutes nobody read after a drive (parked at Home: they are left alone, so never read) are the car parked, not more of the drive
        new = last is None or t - last > GAP
        cut = False
        if not new and k is not None and key is not None and k != key:
            cut = True
        if not new and not cut and k is not None and key is not None and k[0] == "park" and key[0] == "park" and prev is not None and day(prev) != day(st):
            cut = True                                    # no overnight parks: a parked recording is cut where the day changes (a night at home is two sections, the evening and the morning)
        if new or cut:
            sec = st
            kd = k[0] if k else ""
            la, lo = where(it, kd) if k else (None, None)
            out[st] = [kd, la, lo, bool(cut)]
            ref[st] = it
            key = k
        elif k is not None and key is None:               # the section began with minutes not read: it is what the first read minute is
            key = k
            la, lo = where(it, k[0])
            out[sec][0], out[sec][1], out[sec][2] = k[0], la, lo
            ref[sec] = it
        last = t
        prev = st
        if sec in out:
            out[sec][4:] = [t]                            # the last minute of the section so far
    # a parked spell has no position of its own: it is where the drive before it ended, or where the drive after it began (in the same run)
    order = sorted(out, key=CC.utc)
    for n, st in enumerate(order):
        v = out[st]
        if v[0] != "park" or v[1] is not None:
            continue
        near_prev = n and (v[3] or CC.utc(st) - (out[order[n - 1]][4] + 60) <= ADJ_S)           # the drive before it ended just before this began
        near_next = n + 1 < len(order) and (out[order[n + 1]][3] or CC.utc(order[n + 1]) - (v[4] + 60) <= ADJ_S)
        pv = ref.get(order[n - 1]) if near_prev else None
        nx = ref.get(order[n + 1]) if near_next else None
        p = (pv.get("p1") or pv.get("p0")) if pv and pv.get("kind") == "drive" else None
        if not p and nx and nx.get("kind") == "drive":
            p = nx.get("p0") or nx.get("p1")
        if p and p[0] is not None:
            v[1], v[2] = p[0], p[1]
        else:
            _carry(v, st, n, order, out, ref, moves, moves_upto)
    for st, v in out.items():                         # a position the timeline carried from far away is an estimate here too
        if v[1] is not None and len(v) < 6 and (ref.get(st) or {}).get("pest"):
            v[5:] = [True]
    return out


def _carry(v, st, n, order, out, ref, moves, moves_upto):
    """A parked minute on its own (a clip written while parked, hours after the last drive) has no position in its video. Where it is, best source first:
    a drive of the other record that ended since the section before it (the car moved: it is where that ended); the section before it, when the other record is complete
    up to this minute and has no drive since (confirmed); the section before it alone, within CARRY_S (an estimate, marked as one). Nothing else is guessed."""
    t = CC.utc(st)
    q = out[order[n - 1]] if n else None
    r = ref.get(order[n - 1]) if n else None
    ap = None                                                   # where the section before it ended
    te = None
    if q and len(q) > 4 and q[4] is not None:
        te = q[4]
        if q[0] == "park" and q[1] is not None:
            ap = (q[1], q[2])
        elif q[0] == "drive" and r:
            pp = r.get("p1") or r.get("p0")
            if pp and pp[0] is not None:
                ap = (pp[0], pp[1])
    if moves:
        since = [m for m in moves if m.get("p1") and m["p1"][0] is not None and m["t1"] <= t and (te is None or m["t1"] > te)]
        if since:
            m = max(since, key=lambda x: x["t1"])
            v[1], v[2] = m["p1"][0], m["p1"][1]
            return
        if ap is None and moves_upto >= t:                      # the other record says where the car last was, and that it has not moved since
            before = [m for m in moves if m.get("p1") and m["p1"][0] is not None and m["t1"] <= t]
            if before:
                m = max(before, key=lambda x: x["t1"])
                v[1], v[2] = m["p1"][0], m["p1"][1]
                return
    if ap is not None and te is not None and t - te <= CARRY_S:
        v[1], v[2] = ap
        if (q and len(q) > 5 and q[5]) or not (moves is not None and moves_upto >= t):         # nothing confirms the car stayed
            v[5:] = [True]


def disagreements(kinds, moves, moves_upto, margin_s=150, park_s=45):
    """Where the other record (Tessie) does not confirm what the clips say: [(stamp, "clip drive, no drive of Tessie's" | "clip parked, Tessie drove")] for the minutes Tessie is complete for
    (up to moves_upto). kinds = {stamp: "go" | "pk" | ""}, the clips' own answer for each minute. The car sends telemetry from when it is awake in gear, a little before Tessie's trip
    starts and after it ends, so a clip drive within `margin_s` of a drive of Tessie's is confirmed; a clip parked is contradicted only when a drive of Tessie's covers `park_s` seconds of it.
    The cuts are the clips' own; this says when the two disagree, so the logic can be looked at where it is wrong."""
    mv = sorted((m for m in (moves or []) if m.get("t0") and m.get("t1")), key=lambda m: m["t0"])
    out = []
    own = minute_sections(list(kinds), kinds)                  # what the clips alone say
    for st, drive in sorted(((st, str(own.get(st)).startswith("drive")) for st in kinds if st in own), key=lambda kv: CC.utc(kv[0])):
        t = CC.utc(st)
        if t + 60 > moves_upto:
            continue
        if drive:
            if not any(m["t0"] - margin_s < t + 60 and m["t1"] + margin_s > t for m in mv):
                out.append((st, "clip drive, no drive of Tessie's"))
        elif any(min(m["t1"], t + 60) - max(m["t0"], t) >= park_s for m in mv):
            out.append((st, "clip parked, Tessie drove"))
    return out


def update(stamps, items, moves=None, moves_upto=0, kinds=None, marks=None):
    """Work the cuts out again; keeps them, and tells the listeners when they changed."""
    global _S, _VER
    new = compute(stamps, items, moves, moves_upto, kinds, marks)
    with _LOCK:
        if new == _S:
            return False
        _S = new
        _VER += 1
        if _PATH:
            try:
                os.makedirs(os.path.dirname(_PATH), exist_ok=True)
                with open(_PATH + ".tmp", "w") as f:
                    json.dump(new, f)
                os.replace(_PATH + ".tmp", _PATH)
            except OSError:
                pass
    for fn in list(_ON_CHANGE):
        try:
            fn()
        except Exception:
            pass
    return True
