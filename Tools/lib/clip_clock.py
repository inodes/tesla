"""clip_clock.py - UTC is the clock; local time is for display only. The dashcam's clock, minute by minute.

Every comparison, match and ordering in the app is done on UTC epoch seconds. Local time (the zone in config.json app.timezone) is only
what a person reads. Two kinds of clock have to be turned into UTC:

  Tessie's drive files   "naive local" times on the clock of app.timezone, daylight saving included (from_local()). The abbreviation in a file's
                         header ("Timestamp (AEDT)") is NOT trusted: it says AEDT on a drive from September, whose times are AEST
  the dashcam's names    <YYYY-MM-DD_HH-MM-SS> are the CAR's clock, which is not always the local clock. After the clocks change the car
                         can stay on standard time for days, and it can change clock mid-drive when the car crosses a time-zone border
                         (Queensland has no daylight saving, New South Wales has). So the car's offset from UTC is not a setting: it is
                         learned, minute by minute, and can change inside one drive.

How the car's clock is learned (learn()): for a minute of footage whose GPS was read (teslacam_timeline.py), try each offset the car could
have (the zone's standard offset, and standard + 1 h) and see which one puts the camera's track on top of the Tessie drive that was driven
at that instant. A minute is believed only when the car was moving, the best offset lies within match_km of the route and beats the other
by margin_km. Lone outliers are dropped and equal neighbours merged into runs, kept in <data>/clip_clock.json. A minute with no evidence
of its own takes the nearest learned minute within reuse_hours (the car's clock stays put for days), and with none nearby the local clock
of the configured zone (daylight saving included). Nothing is changed on a drive and no Tessie record is touched.

    utc(stamp)            UTC epoch of a dashcam file name (or an event.json timestamp)        <- use this for every comparison
    local_iso(u)          "YYYY-MM-DDTHH:MM:SS" on the local clock, for display only
    from_local(n, off)    UTC epoch of a naive local clock reading n (a time read as if it were UTC)
    parse_local(text)     UTC epoch of a local ISO text, or of a number sent by the page
    version()             changes whenever the learned clock changes (cached answers keyed on stamps must be rebuilt)
"""
import bisect
import calendar
import datetime
import json
import math
import os
import re
import threading

import app_config as CFG
import drive_paths as DP

_STAMP = "%Y-%m-%d_%H-%M-%S"
_LOCK = threading.RLock()
_S = dict(dir=None, runs=[], firsts=[], version=0, sig=None, thread=None, error="", learned_at=None, evidence=0, routes={}, drives=None, ev={}, ev_drives=-1)


def _opt(k, d):
    v = CFG.get("clock." + k)
    return d if v is None else v


# ------------------------------------------------------------------------------------------------------------ local time
def zone():
    import zoneinfo
    try:
        return zoneinfo.ZoneInfo(CFG.get("timezone") or "Australia/Sydney")
    except Exception:
        return datetime.timezone.utc


def local_offset(u):
    """Seconds the local clock is ahead of UTC at the UTC instant u (daylight saving included)."""
    return datetime.datetime.fromtimestamp(u, zone()).utcoffset().total_seconds()


_PLACE = {}


def place_offset(u, lat, lon):
    """Seconds the clock AT A PLACE is ahead of UTC at instant u (Queensland has no daylight saving, New South Wales has), for showing a drive or an
    event on the clock of where it happened, as the Drives list does. app.timezone's when the place is unknown. Display only."""
    if lat is None or lon is None:
        return local_offset(u)
    try:
        import tessie_timezone as TZ
        name = TZ.resolve_timezone(lat, lon)
        if name not in _PLACE:
            import zoneinfo
            _PLACE[name] = zoneinfo.ZoneInfo(name)
        return datetime.datetime.fromtimestamp(u, _PLACE[name]).utcoffset().total_seconds()
    except Exception:
        return local_offset(u)


def std_offset(u):
    """The same without daylight saving."""
    dt = datetime.datetime.fromtimestamp(u, zone())
    return (dt.utcoffset() - (dt.dst() or datetime.timedelta(0))).total_seconds()


def naive(text):
    """A clock reading as seconds read as if it were UTC (calendar.timegm): the dashcam name, or an ISO text, whatever the time zone."""
    t = text.strip().replace("T", "_", 1).replace(" ", "_", 1)
    if len(t) > 19:
        t = t[:19]
    d = re.sub(r"[:]", "-", t[11:]) if len(t) > 10 else ""
    return calendar.timegm(datetime.datetime.strptime(t[:10] + "_" + d, _STAMP).timetuple())


def from_local(n, off=None):
    """UTC epoch of the local clock reading n (naive seconds). off = an offset to use instead of the configured zone's (daylight saving included)."""
    if off is not None:
        return n - off
    dt = datetime.datetime.fromtimestamp(n, datetime.timezone.utc).replace(tzinfo=None).replace(tzinfo=zone())
    return n - dt.utcoffset().total_seconds()


def local_secs(u, off=None):
    """The local clock reading (naive seconds) at UTC instant u."""
    return u + (off if off is not None else local_offset(u))


def local_iso(u, off=None):
    """Display text of UTC instant u on the local clock."""
    return datetime.datetime.fromtimestamp(local_secs(u, off), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def parse_local(txt):
    """UTC epoch of what a page sent: a number (already UTC), an ISO text with a zone, or a local ISO text (the configured zone)."""
    s = str(txt).strip()
    try:
        return float(s)
    except ValueError:
        pass
    if re.search(r"(Z|[+-]\d\d:?\d\d)$", s):
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    return from_local(naive(s))


# ------------------------------------------------------------------------------------------------------- the car's clock
def candidates(n):
    """The offsets (seconds ahead of UTC) the car's clock could have at the reading n: standard time, daylight time, and the local clock."""
    u = from_local(n)
    std = std_offset(u)
    return sorted({std, std + 3600.0, local_offset(u)})


def _lookup(n):
    runs, firsts = _S["runs"], _S["firsts"]
    if not runs:
        return None
    k = bisect.bisect_right(firsts, n) - 1
    best = None
    for j in (k, k + 1):
        if 0 <= j < len(runs):
            a, b, o = runs[j]
            d = 0 if a <= n <= b else min(abs(n - a), abs(n - b))
            if best is None or d < best[0]:
                best = (d, o)
    return best[1] if best and best[0] <= float(_opt("reuse_hours", 36)) * 3600 else None


def known(stamp):
    """True when the car's clock at this dashcam stamp is backed by evidence within reuse_hours (not just assumed to be local)."""
    with _LOCK:
        return _lookup(naive(stamp)) is not None


def car_offset(n):
    """Seconds the car's clock was ahead of UTC at the reading n."""
    with _LOCK:
        o = _lookup(n)
    return float(o) if o is not None else float(n - from_local(n))


def utc(stamp):
    """UTC epoch of a dashcam file name or an event.json timestamp (the car's clock)."""
    n = naive(stamp)
    return n - car_offset(n)


def local_of(stamp):
    """Display text of a dashcam stamp on the local clock."""
    return local_iso(utc(stamp))


def maybe_in(stamp, u0, u1):
    """True when the minute at this stamp falls in [u0, u1] (UTC) under ANY offset the car's clock could have had - for choosing what to read
    when the car's clock at that time is not known yet."""
    n = naive(stamp)
    return any(u0 - 60 <= n - o <= u1 for o in candidates(n))


def version():
    with _LOCK:
        return _S["version"]


# -------------------------------------------------------------------------------------------------------------- learning
def configure(data_dir):
    """Where the learned clock is kept; loads it. Safe to call again."""
    with _LOCK:
        if _S["dir"] == data_dir:
            return
        _S.update(dir=data_dir, drives=None, routes={}, sig=None)
        try:
            with open(os.path.join(data_dir, "clip_clock.json"), encoding="utf-8") as fh:
                j = json.load(fh)
            _set_runs([r for r in j.get("runs", []) if len(r) == 3], bump=False)
        except (OSError, ValueError):
            _set_runs([], bump=False)


def _set_runs(runs, bump=True):
    runs = sorted([list(map(float, r)) for r in runs])
    with _LOCK:
        if bump and runs != _S["runs"]:
            _S["version"] += 1
        _S.update(runs=runs, firsts=[r[0] for r in runs])


def _save():
    d = _S["dir"]
    if not d:
        return
    try:
        tmp = os.path.join(d, "clip_clock.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(dict(v=1, runs=_S["runs"]), fh, separators=(",", ":"))
        os.replace(tmp, os.path.join(d, "clip_clock.json"))
    except OSError:
        pass


def _km(la1, lo1, la2, lo2):
    x = math.radians(lo2 - lo1) * math.cos(math.radians((la1 + la2) / 2))
    return 6371.0 * math.hypot(x, math.radians(la2 - la1))


def _drive_list(data_dir):
    """[(coarse u0, coarse u1, folder)] of the Tessie drives, read from their summaries (cheap; the points are loaded only when needed)."""
    with _LOCK:
        if _S["drives"] is not None:
            return _S["drives"]
    base = os.path.join(data_dir, "drives", "deepdive")
    out = []
    for nm, folder in DP.iter_folders(base):                  # day layout and older layouts
        try:
            with open(os.path.join(folder, "drive_summary.json"), encoding="utf-8") as fh:
                sm = json.load(fh)
            out.append((from_local(naive(sm["start"].replace(" ", "_"))), from_local(naive(sm["end"].replace(" ", "_"))), folder))
        except (OSError, ValueError, KeyError):
            continue
    out.sort()
    with _LOCK:
        _S["drives"] = out
    return out


def _route(folder):
    """(u[], lat[], lon[]) of a Tessie drive, UTC from its own header zone; None when it has no samples."""
    with _LOCK:
        if folder in _S["routes"]:
            return _S["routes"][folder]
    import drive_fuse as DF
    pts = DF.tessie_points(folder)
    r = ([p["u"] for p in pts], [p["la"] for p in pts], [p["lo"] for p in pts]) if len(pts) >= 2 else None
    with _LOCK:
        _S["routes"][folder] = r
    return r


def _at(r, u, slack=45.0):
    ts, las, los = r
    if u < ts[0] - slack or u > ts[-1] + slack:
        return None
    k = bisect.bisect_left(ts, u)
    if k <= 0:
        return las[0], los[0]
    if k >= len(ts):
        return las[-1], los[-1]
    f = (u - ts[k - 1]) / (ts[k] - ts[k - 1]) if ts[k] > ts[k - 1] else 0.0
    return las[k - 1] + (las[k] - las[k - 1]) * f, los[k - 1] + (los[k] - los[k - 1]) * f


def _evidence(stamp, ser, drives, data_dir):
    """The offset that puts this minute's GPS on a Tessie route, or None (not moving, no route near, ambiguous)."""
    vs = [v for v in ser.get("v", []) if v is not None]
    if not vs or max(vs) < 5:
        return None
    pts = [(i, a, b) for i, (a, b) in enumerate(zip(ser["lat"], ser["lon"])) if a is not None and b is not None and abs(a) > 1 and abs(b) > 1][::5]
    if len(pts) < 4:
        return None
    n = naive(stamp)
    errs = {}
    for o in candidates(n):
        u0 = n - o
        near = [d for d in drives if d[0] - 7200 <= u0 <= d[1] + 7200]
        best = None
        for _, _, folder in near:
            r = _route(folder)
            if not r:
                continue
            es = []
            for i, a, b in pts:
                p = _at(r, u0 + i)
                if p:
                    es.append(_km(a, b, p[0], p[1]))
            if len(es) >= 4:
                e = sum(es) / len(es)
                best = e if best is None else min(best, e)
        if best is not None:
            errs[o] = best
    if not errs:
        return None
    o = min(errs, key=errs.get)
    if errs[o] > float(_opt("match_km", 0.5)):
        return None
    others = [e for k, e in errs.items() if k != o]
    if others and min(others) - errs[o] < float(_opt("margin_km", 1.0)):
        return None
    return o


def _runs_of(ev):
    """ev = [(reading, offset)] by reading -> runs [first, last, offset]; a lone reading that disagrees with equal neighbours is dropped."""
    ev = sorted(ev)
    keep = []
    for k, (n, o) in enumerate(ev):
        a = ev[k - 1] if k > 0 else None
        b = ev[k + 1] if k + 1 < len(ev) else None
        if a and b and a[1] == b[1] != o and n - a[0] <= 600 and b[0] - n <= 600:
            continue
        keep.append((n, o))
    runs = []
    for n, o in keep:
        if runs and runs[-1][2] == o:
            runs[-1][1] = n
        else:
            runs.append([n, n, o])
    return runs


def learn(data_dir, store, stop=None):
    """Learn (or refresh) the car's clock from the telemetry in `store` and the Tessie drives in <data>/drives/deepdive. Returns the number of
    minutes that gave evidence."""
    configure(data_dir)
    stamps = sorted(store.stamps())
    drives = _drive_list(data_dir)
    if _S["ev_drives"] != len(drives):                        # a drive arrived (or went): minutes that had no route may have one now
        _S.update(ev={}, ev_drives=len(drives), routes={})
    seen = _S["ev"]                                           # stamp -> offset or None, so a pass only looks at minutes it has not seen
    ev = {}
    for a, b, o in list(_S["runs"]):                         # what was learned before still counts at its edges
        ev[a] = o
        ev[b] = o
    n_new = 0
    for st in stamps:
        if stop and stop():
            return 0
        if not store.summary(st):
            continue
        ser = store.series(st)
        if not ser:
            continue
        if st in seen:
            o = seen[st]
        else:
            try:
                o = seen[st] = _evidence(st, ser, drives, data_dir)
            except ValueError:
                continue
        if o is not None:
            ev[naive(st)] = o
            n_new += 1
    _set_runs(_runs_of(list(ev.items())))
    with _LOCK:
        _S.update(evidence=n_new, learned_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  sig=(len(stamps), stamps[-1] if stamps else "", len(drives)))
        _save()
    return n_new


def refresh(data_dir, store, wait=False):
    """Learn again when the store or the drives changed (in a background thread unless wait). Returns True when a pass ran or started."""
    configure(data_dir)
    th = _S["thread"]
    if wait and th and th.is_alive():
        th.join(120)                                          # a pass is already running: wait for it rather than skip
    stamps = store.stamps()
    sig = (len(stamps), max(stamps) if stamps else "", DP.signature(os.path.join(data_dir, "drives", "deepdive")))
    with _LOCK:
        if sig == _S["sig"] or (_S["thread"] and _S["thread"].is_alive()):
            return False
        _S["drives"] = None

    def run():
        try:
            learn(data_dir, store)
            _S["error"] = ""
        except Exception as e:
            _S["error"] = "%s: %s" % (type(e).__name__, e)
    if wait:
        run()
        return True
    t = threading.Thread(target=run, daemon=True)
    with _LOCK:
        _S["thread"] = t
    t.start()
    return True


def status():
    """What was learned, for the page / api: runs on the local clock, how many minutes of each offset."""
    with _LOCK:
        runs = list(_S["runs"])
        out = dict(version=_S["version"], runs=len(runs), evidence=_S["evidence"], learned_at=_S["learned_at"], error=_S["error"],
                   learning=bool(_S["thread"] and _S["thread"].is_alive()), zone=str(CFG.get("timezone")))
    out["spans"] = [dict(from_utc=local_iso(a - o, 0), to_utc=local_iso(b - o, 0), offset_h=o / 3600.0) for a, b, o in runs[-200:]]
    return out
