"""app_timeline.py - the footage timeline: when each volume has Recent, Sentry and Saved video, where Recent has gaps, and the same for all volumes together.

  build(data_dir)       -> dict, kept until a ledger file changes

Built only from the footage log (teslacam_ledger.py: one file per clip day, every video file with the drives that hold it); no drive is read, so a volume that is not
plugged in is on the timeline too, as of its last scan. Times are the car's clock as the file names give them (2026-11-20_08-15-35 is 08:15:35 that day), counted in
seconds as if that clock were UTC, so a day on the timeline is the same day as a day in the log and in the TeslaCam page.

A minute of video is the interval [stamp, stamp + 60). Minutes that follow each other (less than MIN_GAP_S apart) are one interval; a break of MIN_GAP_S or more
between two intervals of Recent is a gap. Nothing is judged here about whether a gap is a fault: a parked night with Sentry off is a gap too. What the answer carries
for each gap is what is known about it:

  by   the other volumes that have Recent video for at least half of it (the footage exists, on another drive)
  ss   the share of it (0..1) that Sentry or Saved video of any volume covers (the car was recording, as an event, not as Recent)

The answer:
  gap_min, t0, t1                 the smallest gap counted, the first and last second of any video
  roles   {role: {recent, sentry, saved: [[s, e], ...], gaps: [{s, e, by, ss}], minutes: {kind: n}}}
  all     {recent, sentry, saved: [[s, e], ...], gaps: [{s, e, ss}]}        every volume together (video on any volume counts)
"""
import calendar
import os
import re
import threading

from lib import teslacam_ledger as TLG

MIN_GAP_S = 30            # a break between two minutes of video shorter than this is clock jitter (stamps are 60-62 s apart), not a gap
MINUTE = 60
KINDS = ("recent", "sentry", "saved")
_STAMP = re.compile(r"^(\d{4})-(\d\d)-(\d\d)_(\d\d)-(\d\d)-(\d\d)-")

_LOCK = threading.Lock()
_KEPT = {}


def _secs(rest):
    m = _STAMP.match(rest)
    if not m:
        return None
    y, mo, d, h, mi, s = (int(x) for x in m.groups())
    return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0))


def merge(spans, tol=MIN_GAP_S):
    """[[s, e], ...] in any order -> sorted, with spans that touch or are less than `tol` apart made one."""
    out = []
    for s, e in sorted(spans):
        if out and s - out[-1][1] < tol:
            if e > out[-1][1]:
                out[-1][1] = e
        else:
            out.append([s, e])
    return out


def _from_minutes(mins):
    return merge([(t, t + MINUTE) for t in mins])


def overlap(spans, s, e):
    """Seconds of [s, e) that the sorted, merged `spans` cover."""
    n = 0
    lo, hi = 0, len(spans)
    while lo < hi:                              # the first span that ends after s
        mid = (lo + hi) // 2
        if spans[mid][1] <= s:
            lo = mid + 1
        else:
            hi = mid
    for a, b in spans[lo:]:
        if a >= e:
            break
        n += min(b, e) - max(a, s)
    return n


def gaps_of(spans):
    """[(s, e), ...] between consecutive spans of a merged list."""
    return [(spans[i][1], spans[i + 1][0]) for i in range(len(spans) - 1)]


def _gaps(recent, others, ss_spans):
    out = []
    for s, e in gaps_of(recent):
        d = e - s
        g = dict(s=s, e=e)
        if others is not None:
            g["by"] = sorted(r for r, sp in others.items() if overlap(sp, s, e) * 2 >= d)
        g["ss"] = round(min(1.0, overlap(ss_spans, s, e) / d), 2)
        out.append(g)
    return out


def summarise(minutes):
    """minutes {role: {kind: set(seconds)}} -> the answer described at the top (also what the tests call)."""
    spans = {r: {k: _from_minutes(ks.get(k, ())) for k in KINDS} for r, ks in minutes.items()}
    every = {k: merge([sp for r in spans for sp in spans[r][k]]) for k in KINDS}
    ss_all = merge(every["sentry"] + every["saved"])
    roles = {}
    for r, ks in spans.items():
        others = {o: spans[o]["recent"] for o in spans if o != r}
        roles[r] = dict(recent=ks["recent"], sentry=ks["sentry"], saved=ks["saved"], gaps=_gaps(ks["recent"], others, ss_all),
                        minutes={k: len(minutes[r].get(k, ())) for k in KINDS})
    allsp = [x for k in KINDS for x in every[k]]
    t0 = min((s for s, _ in allsp), default=None)
    t1 = max((e for _, e in allsp), default=None)
    return dict(gap_min=MIN_GAP_S, t0=t0, t1=t1, roles=roles,
                all=dict(recent=every["recent"], sentry=every["sentry"], saved=every["saved"], gaps=_gaps(every["recent"], None, ss_all)))


def _signature(root):
    try:
        return tuple(sorted((e.name, e.stat().st_mtime_ns, e.stat().st_size) for e in os.scandir(root) if e.name.endswith(".json")))
    except OSError:
        return ()


def build(data_dir):
    """The timeline of the footage log in `data_dir`, kept until a file of the log changes."""
    root = os.path.join(data_dir, TLG.DIRNAME)
    sig = _signature(root)
    with _LOCK:
        hit = _KEPT.get(data_dir)
        if hit and hit[0] == sig:
            return hit[1]
    led = TLG.Ledger(data_dir, readonly=True)
    minutes = {}
    for day in led.days():
        for key, rec in led.day(day).items():
            kind, _ev, rest = key.split("|", 2)
            if kind not in KINDS:
                continue
            t = _secs(rest)
            if t is None:
                continue
            for role, x in (rec.get("drives") or {}).items():
                if not x.get("gone"):
                    minutes.setdefault(role, {k: set() for k in KINDS})[kind].add(t)
    out = summarise(minutes)
    with _LOCK:
        _KEPT[data_dir] = (sig, out)
    return out
