"""run_cuts.py - where a Recent recording is cut into its drive and parked sections.

The car writes one continuous run of one-minute clips for as long as it records; a run can hold a park, a drive and a park. For the TeslaCam list that is
three things, so a run is cut where the timeline (teslacam_timeline.py, the same one the Drives list is made from) changes from a parked spell to a drive,
or back. Every place that groups the minutes into runs (the list, the player, what is kept) asks `is_cut(stamp)` and starts a new run at a cut, so a run is
named by its first minute everywhere, as before.

    update(stamps, items) -> works the cuts out from the timeline's items (kind drive | stopped | parked | recording, t0, t1), keeps them in the file
    is_cut(stamp)         -> True when a run starts at this minute although the minute before it is close enough to belong to the same run
    kind(stamp), pos(stamp) -> what the section that starts at this minute is: "drive" or "park" ("" when not known), and where (lat, lon) when known
    section(stamp)        -> (kind, lat, lon) of the section any minute lies in; None when the minute is in no section worked out
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
CARRY_S = 86400             # seconds: a parked minute with no position of its own takes the one of the section before it when that ended no longer ago than this
GAP = 75                    # as in app_teslacam: minutes this close are one run
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
        s = _S[ks[i]]
        if len(s) < 5 or s[4] is None or t > s[4]:
            return None
        return s[0], s[1], s[2]


def pos(stamp):
    s = _S.get(stamp)
    return [s[1], s[2]] if s and s[1] is not None else None


def compute(stamps, items):
    """{first minute of every section: [kind, lat, lon, cut]} for these minutes and the timeline's items."""
    its = sorted((i for i in items if i.get("kind") in ("drive", "stopped", "parked", "recording")), key=lambda i: i["t0"])
    t0s = [i["t0"] for i in its]

    def at(t):
        k = bisect.bisect_right(t0s, t) - 1
        for j in (k, k - 1):
            if 0 <= j < len(its) and its[j]["t0"] <= t < its[j]["t1"]:
                return its[j]
        return None

    def where(it, kd):
        p = (it.get("p0") or it.get("p1")) if it else None
        return (p[0], p[1]) if p and p[0] is not None else (None, None)

    out, key, last, sec, secit = {}, None, None, None, None
    ref = {}                                          # first minute of a section -> the timeline item it began in
    for st in sorted(stamps, key=CC.utc):
        t = CC.utc(st)
        it = at(t + 30)
        k = None
        if it and it["kind"] == "drive":
            k = ("drive", it["id"])
        elif it and it["kind"] in ("stopped", "parked"):
            k = ("park", "")
        elif it and it["kind"] == "recording" and key is not None and key[0] == "drive" and it["t1"] - it["t0"] >= MIN_UNREAD:
            k = ("park", "")                              # minutes nobody read after a drive (parked at Home: they are left alone, so never read) are the car parked, not more of the drive
        new = last is None or t - last > GAP
        cut = False
        if not new and k is not None and key is not None and k != key:
            cut = True
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
        if sec in out:
            out[sec][4:] = [t]                            # the last minute of the section so far
    # a parked spell has no position of its own: it is where the drive before it ended, or where the drive after it began (in the same run)
    order = sorted(out, key=CC.utc)
    for n, st in enumerate(order):
        v = out[st]
        if v[0] != "park" or v[1] is not None:
            continue
        pv = ref.get(order[n - 1]) if n and v[3] else None
        nx = ref.get(order[n + 1]) if n + 1 < len(order) and out[order[n + 1]][3] else None
        p = (pv.get("p1") or pv.get("p0")) if pv and pv.get("kind") == "drive" else None
        if not p and nx and nx.get("kind") == "drive":
            p = nx.get("p0") or nx.get("p1")
        if p and p[0] is not None:
            v[1], v[2] = p[0], p[1]
        elif n and CC.utc(st) - (out[order[n - 1]][4] if len(out[order[n - 1]]) > 4 else 0) <= CARRY_S:
            # a parked minute on its own (a clip the car wrote while parked, hours after the last drive): the car has not been recorded moving since, so it is where the section before ended
            q, r = out[order[n - 1]], ref.get(order[n - 1])
            if q[0] == "park" and q[1] is not None:
                v[1], v[2] = q[1], q[2]
            elif q[0] == "drive" and r and (r.get("p1") or r.get("p0")) and (r.get("p1") or r.get("p0"))[0] is not None:
                pp = r.get("p1") or r.get("p0")
                v[1], v[2] = pp[0], pp[1]
    return out


def update(stamps, items):
    """Work the cuts out again; keeps them, and tells the listeners when they changed."""
    global _S, _VER
    new = compute(stamps, items)
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
