#!/usr/bin/env python3
"""Dashcam flip review (REQ-192): shows what the car did, and what the road showed, at every 100/110 flip.

For each 100<->110 flip found by analyse_fsd_osm_limits.py (fsd_transitions.csv) that has TeslaCam front
footage, this reads the per-frame telemetry embedded in the clip (speed, FSD state, brake, GPS; see
lib/dashcam_series.py), finds the exact moment the speed starts to change, and pulls 20 stills (one per second) from
12 s before to 12 s after. Everything is written as one page:

    <data>/drives/analysis/dashcam_flip_review/index.html   (+ frames_1s/E###.jpg, one 5x4 contact sheet per event, a still every second)
    <data>/drives/analysis/dashcam_flip_events.csv

so each event can be judged by eye: real 100 sign / traffic / 110 sign but the car slowed anyway / no sign.
Review choices are kept in the browser (and can be exported from the page).

Usage:
    dashcam_flip_review.py [--data-dir DIR] [--teslacam DIR] [--since YYYY-MM-DD]
                           [--budget SECONDS] [--force] [--no-frames]
Re-running is cheap: finished contact sheets are skipped. --budget stops extracting new ones after N seconds
(use ~85 on the device bridge, then run again).
"""
import argparse
import bisect
import csv
import datetime
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
from dashcam_series import read_series, AP_NAMES  # noqa: E402

DEFAULT_DATA = os.path.expanduser(os.environ.get("TESLA_DATA_DIR", "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla"))
# Default footage folders: the Archive ONLY, found by the marker folder in the drive root (never by mount name - TESLADRIVE / TESLADRIVE 1 / 2 are
# interchangeable). JOWUA and the Tesla USB are opt-in (--with-secondary), see lib/drive_roles.py and AGENTS.md 3.5.1.
import drive_roles as DR  # noqa: E402
TESLACAM_CANDIDATES = DR.teslacam_roots()
OFFSETS = (0, 3600)           # clip names were an hour behind Tessie on some days (AEST vs AEDT)
FRAME_OFFSETS = tuple(range(-12, 16))  # one still per second, 12 s before to 15 s after the speed starts to change (28 stills)
CW = 1280                     # width of each still (source is 2896 px wide); 1280 so signs and lettering stay readable when enlarged
COLS, ROWS = 5, 6             # contact sheet layout (COLS * ROWS >= len(FRAME_OFFSETS); the last cells stay dark)
DIRS = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}


def hav_km(a, b, c, d):
    p = math.pi / 180
    x = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))


def find_clips(roots, since):
    """[(start datetime in the clip's own clock, path)] for every front-camera clip since the date, from every TeslaCam folder."""
    out = {}
    if isinstance(roots, str):
        roots = [roots]

    def add(d):
        try:
            it = list(os.scandir(d))
        except OSError:
            return
        for e in it:
            n = e.name
            if n.endswith("-front.mp4") and not n.startswith("._") and n[:10] >= since:
                try:
                    s = datetime.datetime.strptime(n[:19], "%Y-%m-%d_%H-%M-%S")
                except ValueError:
                    continue
                try:
                    sz = e.stat().st_size
                except OSError:
                    sz = 0
                if s not in out or sz > out[s][1]:      # same start in two folders = same footage; keep the longer (not cut short by a save)
                    out[s] = (e.path, sz)

    # RecentClips, SavedClips and SentryClips are one timeline: when the car saves an event, those minutes
    # move out of RecentClips into SavedClips, so only all three together give continuous footage.
    for root in roots:
        add(os.path.join(root, "RecentClips"))
        for sub in ("SavedClips", "SentryClips"):
            try:
                for e in os.scandir(os.path.join(root, sub)):
                    if e.is_dir() and e.name[:10] >= since:
                        add(e.path)
            except OSError:
                pass
    return sorted((k, v[0]) for k, v in out.items())


REASONS = {
    "user_interaction_honk": "you honked",
    "user_interaction_dashcam_icon_tapped": "dashcam icon tapped",
    "user_interaction_dashcam_launcher_action_tapped": "dashcam button tapped",
    "user_interaction_dashcam_panel_save": "dashcam save button",
    "sentry_aware_object_detection": "Sentry saw an object",
    "sentry_aware_accel": "Sentry felt a bump",
    "sentry_locked_handle_pulled": "Sentry: door handle pulled",
    "vehicle_auto_emergency_braking": "automatic emergency braking",
}


def clip_source(e, roots):
    """Where the clip behind an event sits (Recent / Saved / Sentry) and, for saved ones, why (the event.json beside it)."""
    path = None
    for r in roots:
        c = os.path.join(r, e.get("clip_rel", ""))
        if e.get("clip_rel") and os.path.exists(c):
            path = c
            break
    e["clip_src"] = "recent"
    e["clip_event"] = None
    if not path:
        return
    top = e["clip_rel"].split(os.sep)[0]
    if top not in ("SavedClips", "SentryClips"):
        return
    e["clip_src"] = "saved" if top == "SavedClips" else "sentry"
    try:
        ev = json.load(open(os.path.join(os.path.dirname(path), "event.json")))
    except Exception:
        return
    reason = ev.get("reason", "")
    rel = None
    try:
        t = datetime.datetime.strptime(ev["timestamp"], "%Y-%m-%dT%H:%M:%S")
        rel = round((t - datetime.datetime.strptime(e["onset_local"], "%Y-%m-%d %H:%M:%S")).total_seconds())
    except Exception:
        pass
    e["clip_event"] = dict(reason=reason, label=REASONS.get(reason, reason.replace("_", " ")), time=ev.get("timestamp", "")[11:19], rel=rel,
                           place=", ".join(x for x in (ev.get("city"), ev.get("street")) if x), folder=os.path.dirname(e["clip_rel"]).split(os.sep)[-1])


def coverage_report(flips, clips, args):
    """Say where the footage is and why flips have none: before the first clip, in a gap, or no clips that day."""
    if not clips:
        return
    starts = [c[0] for c in clips]
    kinds = {"Recent": 0, "Saved": 0, "Sentry": 0}
    for _s, p in clips:
        kinds["Recent" if "/RecentClips/" in p else "Saved" if "/SavedClips/" in p else "Sentry"] += 1
    why = {"no clips that day": 0, "before first clip": 0, "in a gap (RecentClips rolled over, 24 h when not synced)": 0, "after last clip": 0}
    for fl in flips:
        if fl["time"][:10] < args.since:
            continue
        T = datetime.datetime.strptime(fl["time"], "%Y-%m-%d %H:%M:%S")
        best = None
        for off in OFFSETS:
            t = T - datetime.timedelta(seconds=off)
            k = bisect.bisect_right(starts, t) - 1
            if k >= 0 and (best is None or (t - starts[k]).total_seconds() < best):
                best = (t - starts[k]).total_seconds()
        if best is not None and best <= 90:
            continue
        if not any(abs((s - T).total_seconds()) < 86400 * 1.1 and s.date() in (T.date(), (T - datetime.timedelta(seconds=3600)).date()) for s in starts):
            why["no clips that day"] += 1
        elif T < starts[0]:
            why["before first clip"] += 1
        elif T > starts[-1] + datetime.timedelta(seconds=90):
            why["after last clip"] += 1
        else:
            why["in a gap (RecentClips rolled over, 24 h when not synced)"] += 1
    print("Clips (Recent + Saved + Sentry as one timeline): %s" % ", ".join("%s %d" % kv for kv in kinds.items()))
    print("Flips without footage since %s: %s" % (args.since, ", ".join("%s %d" % kv for kv in why.items() if kv[1]) or "none"))


class Cache:
    def __init__(self):
        self.d = {}

    def get(self, path):
        if path not in self.d:
            self.d[path] = read_series(path)
        return self.d[path]


def match_flip(flip, clips, starts, cache):
    """Best (distance_km, offset_s, clip_index) for the clip passing the flip location, or None."""
    T = datetime.datetime.strptime(flip["time"], "%Y-%m-%d %H:%M:%S")
    gap = float(flip["gap_s"] or 60)
    la, lo = float(flip["lat"]), float(flip["lon"])
    best = None
    for off in OFFSETS:
        t0 = T - datetime.timedelta(seconds=gap + off)
        t1 = T - datetime.timedelta(seconds=off)
        i0 = bisect.bisect_left(starts, t0 - datetime.timedelta(seconds=62))
        i1 = bisect.bisect_right(starts, t1)
        for ci in range(i0, i1):
            S = cache.get(clips[ci][1])
            if not S["n"]:
                continue
            md = min((hav_km(la, lo, S["lat"][i], S["lon"][i]) for i in range(0, S["n"], 18) if S["lat"][i]), default=9e9)
            if md < 1.0 and (best is None or md < best[0]):
                best = (md, off, ci)
    return best


class Timeline:
    """prev + current + next clip on one time axis (seconds from the current clip's start)."""

    def __init__(self, clips, starts, ci, cache):
        cur = clips[ci][0]
        self.parts = []  # (t_start, path, S)
        for k in (ci - 1, ci, ci + 1):
            if 0 <= k < len(clips):
                dt = (clips[k][0] - cur).total_seconds()
                if k != ci and abs(dt) > 75:
                    continue
                S = cache.get(clips[k][1])
                if S["n"]:
                    self.parts.append((dt, clips[k][1], S))
        self.t, self.v, self.ap, self.br, self.lat, self.lon, self.hd, self.blk = [], [], [], [], [], [], [], []
        for dt, _p, S in self.parts:
            step = S["dur"] / S["n"]
            for i in range(S["n"]):
                self.t.append(dt + i * step)
                self.v.append(S["v"][i])
                self.ap.append(S["ap"][i])
                self.br.append(S["br"][i])
                self.lat.append(S["lat"][i])
                self.lon.append(S["lon"][i])
                self.hd.append(S["hd"][i])
                self.blk.append(S["blk"][i])
        order = sorted(range(len(self.t)), key=self.t.__getitem__)
        for name in ("t", "v", "ap", "br", "lat", "lon", "hd", "blk"):
            a = getattr(self, name)
            setattr(self, name, [a[i] for i in order])

    def idx(self, t):
        i = bisect.bisect_left(self.t, t)
        if i <= 0:
            return 0
        if i >= len(self.t):
            return len(self.t) - 1
        return i if abs(self.t[i] - t) < abs(self.t[i - 1] - t) else i - 1

    def covers(self, t):
        return bool(self.t) and self.t[0] <= t <= self.t[-1]

    def clip_at(self, t):
        """(path, seconds into that clip) for a time on the axis."""
        for dt, p, S in self.parts:
            if dt <= t < dt + S["dur"]:
                return p, t - dt
        dt, p, S = min(self.parts, key=lambda x: min(abs(t - x[0]), abs(t - x[0] - x[2]["dur"])))
        return p, max(0.0, min(S["dur"] - 0.1, t - dt))


def med(xs):
    return statistics.median(xs) if xs else None


def grid_speed(tl, t0, t1, dt=0.25):
    """Speed on a regular grid, median over 1 s so single-frame noise does not matter."""
    out = []
    t = t0
    while t <= t1 + 1e-9:
        if tl.covers(t):
            i0, i1 = tl.idx(t - 0.5), tl.idx(t + 0.5)
            out.append((t, med(tl.v[i0:i1 + 1])))
        else:
            out.append((t, None))
        t += dt
    return out


def analyse(tl, flip):
    """Find the step in speed that belongs to the flip. Returns dict (times are on the timeline axis).

    Tessie's speed field runs about 20-30 s behind its GPS fix (see tessie_vs_cam.py), so the real step on the
    dashcam is usually BEFORE the flip location: search from 75 s before to 25 s after it, and prefer a step
    that goes the way the flip says (110 -> 100 = drop) and lands on a 100-ish / 110-ish plateau."""
    la, lo = float(flip["lat"]), float(flip["lon"])
    k = min(range(len(tl.t)), key=lambda i: hav_km(la, lo, tl.lat[i], tl.lon[i]) if tl.lat[i] else 9e9)
    tk = tl.t[k]
    g = grid_speed(tl, tk - 100, tk + 45)
    gv = {round(t, 2): v for t, v in g}

    def win(a, b):
        return [v for t, v in g if a <= t <= b and v is not None]

    want = -1 if flip.get("kind", "").startswith("110") else 1
    cands = []
    tb = tk - 75
    while tb <= tk + 25:
        pre, post = win(tb - 8, tb - 3), win(tb + 3, tb + 8)
        if len(pre) >= 8 and len(post) >= 8:
            cands.append((med(post) - med(pre), tb, med(pre), med(post)))
        tb += 0.25
    res = dict(tk=tk, k=k)

    def ok_plateau(c):
        d, _tb, pre, post = c
        hi, lo_ = (pre, post) if want < 0 else (post, pre)
        return d * want >= 3 and 105 <= hi <= 116 and 96 <= lo_ <= 107

    good = [c for c in cands if ok_plateau(c)]
    if good:
        best = max(good, key=lambda c: (abs(c[0]) - 0.02 * abs(c[1] - (tk - 25))))
        res["fit"] = "plateau"
    else:
        same = [c for c in cands if c[0] * want >= 3]
        if same:
            best = max(same, key=lambda c: abs(c[0]))
            res["fit"] = "other step"
        elif cands:
            best = max(cands, key=lambda c: abs(c[0]))
            res["fit"] = "wrong way" if abs(best[0]) >= 3 else "flat"
        else:
            best = None
    if best is None:
        res.update(kind="none", pre=None, post=None, delta=0.0, onset=tk, end=tk, fit="no data")
        return res
    d, tb, _p, _q = best
    pre, post = med(win(tb - 9, tb - 4)), med(win(tb + 4, tb + 9))
    d = post - pre
    if abs(d) < 3:
        res.update(kind="none", pre=round(pre, 1), post=round(post, 1), delta=round(d, 1), onset=tk, end=tk)
        res["fit"] = "flat"
        return res
    # The step search above is blurred by the median windows: tb can sit up to ~2.5 s before the speed really
    # starts to move (the match is flat across that range). So take the start of the slowdown / speed-up as the
    # first time, searching from tb - 4 s, that the speed is more than 1 km/h from the old plateau and stays so.
    def off(t):
        v = gv.get(round(t, 2))
        return v is not None and abs(v - pre) > 1.0

    t = tb - 4
    onset = tb
    while t < tb + 12:
        if off(t) and all(off(t + 0.25 * j) for j in range(1, 9)):
            onset = t - 0.25
            break
        t += 0.25
    end = onset
    while end < onset + 14 and gv.get(round(end, 2)) is not None and abs(gv[round(end, 2)] - post) > 1.0:
        end += 0.25
    res.update(kind="drop" if d < 0 else "rise", pre=round(pre, 1), post=round(post, 1), delta=round(d, 1), onset=onset, end=end)
    return res


def _grab(job):
    path, ts, tmp = job
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.2f" % ts, "-i", path, "-frames:v", "1",
                    "-vf", "scale=%d:-2" % CW, "-q:v", "2", tmp], check=False, stdin=subprocess.DEVNULL)
    return tmp


def extract_sheet(tl, onset, out_path, tmpdir):
    """One still per second -> one 5x4 JPEG contact sheet. Returns (cw, ch)."""
    from concurrent.futures import ThreadPoolExecutor
    from PIL import Image
    jobs = []
    for j, rel in enumerate(FRAME_OFFSETS):
        t = onset + rel
        if not tl.covers(t):
            jobs.append(None)       # no footage (start or end of the recording): leave the cell dark
            continue
        path, ts = tl.clip_at(t)
        jobs.append((path, ts, os.path.join(tmpdir, "f%d.jpg" % j)))
    with ThreadPoolExecutor(max_workers=4) as ex:
        done = list(ex.map(lambda jb: _grab(jb) if jb else None, jobs))
    cells = [Image.open(d).convert("RGB") if d and os.path.exists(d) else None for d in done]
    first = next((c for c in cells if c is not None), None)
    if first is None:
        return None
    cw, ch = first.size
    sheet = Image.new("RGB", (cw * COLS, ch * ROWS), (20, 20, 20))
    for j, c in enumerate(cells):
        if c is not None:
            sheet.paste(c, ((j % COLS) * cw, (j // COLS) * ch))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    sheet.save(out_path + ".tmp", "JPEG", quality=62, optimize=True)
    os.replace(out_path + ".tmp", out_path)
    return cw, ch


def indicator_info(tl, onset):
    """Indicator use from 20 s before to 12 s after the speed starts to change: runs of left (L) / right (R) blinking."""
    runs, cur = [], None
    t = onset - 20
    while t <= onset + 16 + 1e-9:
        b = tl.blk[tl.idx(t)] if tl.covers(t) else 0
        side = "L" if b == 1 else "R" if b == 2 else "LR" if b == 3 else ""
        if side and (cur is None or cur[0] != side):
            cur = [side, round(t - onset, 1), round(t - onset, 1)]
            runs.append(cur)
        elif side:
            cur[2] = round(t - onset, 1)
        elif cur is not None and t - onset - cur[2] > 1.6:
            cur = None  # a blink gap is ~0.5 s; longer than that ends the run
        t += 0.25
    return [dict(side=r[0], t0=r[1], t1=r[2]) for r in runs if r[2] - r[1] >= 0.5]


def cruise_info(tl, onset, a):
    """Steady speed before the step (median of samples where the speed held within 1.5 km/h over 3 s, from 70 s to 5 s
    before the onset), and how far it sat from the 110 limit; plus the speed after the step against 100."""
    g = grid_speed(tl, onset - 70, onset - 5)
    steady = []
    for k in range(len(g) - 12):
        w = [v for _t, v in g[k:k + 13] if v is not None]
        if len(w) >= 10 and max(w) - min(w) <= 1.5:
            steady.append(statistics.median(w))
    pre = a.get("pre")
    post = a.get("post")
    return dict(steady=round(statistics.median(steady), 1) if len(steady) >= 8 else None, n=len(steady) // 4,
                pre=pre, post=post,
                pre_vs_110=round(pre - 110, 1) if pre is not None else None,
                post_vs_100=round(post - 100, 1) if post is not None and a.get("kind") == "drop" else None)


def compute_event(fl, eid, clips, starts, cache, fdir, tmpdir, args):
    m = match_flip(fl, clips, starts, cache)
    if not m:
        return None
    _d, off, ci = m
    tl = Timeline(clips, starts, ci, cache)
    a = analyse(tl, fl)
    onset = a["onset"]
    i0, i1 = tl.idx(onset - 12), tl.idx(onset + 16)
    aps = tl.ap[i0:i1 + 1]
    engaged = sum(1 for x in aps if x == 1) / max(1, len(aps))
    off_s = (sum(1 for x in aps if x != 1) / max(1, len(aps))) * 28.0
    states = {AP_NAMES.get(s, str(s)): aps.count(s) for s in set(aps)}
    brake = any(tl.br[i0:i1 + 1])
    ser_v, ser_ap, ser_br, ser_bk = [], "", "", ""
    t = onset - 20
    while t <= onset + 16 + 1e-9:
        if tl.covers(t):
            i = tl.idx(t)
            ser_v.append(round(tl.v[i], 1))
            ser_ap += str(tl.ap[i])
            ser_br += "1" if tl.br[i] else "0"
            ser_bk += str(tl.blk[i])
        else:
            ser_v.append(None)
            ser_ap += "-"
            ser_br += "0"
            ser_bk += "0"
        t += 0.5
    frames = []
    for rel in FRAME_OFFSETS:
        t = onset + rel
        if tl.covers(t):
            i = tl.idx(t)
            frames.append(dict(rel=rel, v=round(tl.v[i], 1), ap=tl.ap[i], br=tl.br[i], bk=tl.blk[i]))
        else:
            frames.append(dict(rel=rel, v=None, ap=None, br=0, bk=0))
    isg = tl.idx(onset - 4)
    io = tl.idx(onset)
    sheet = os.path.join(fdir, eid + ".jpg")
    cwch = None
    if os.path.exists(sheet) and not args.force:
        try:
            from PIL import Image
            im = Image.open(sheet)
            cwch = (im.size[0] // COLS, im.size[1] // ROWS)
        except Exception:
            cwch = None
    if cwch is None and not args.no_frames:
        cwch = extract_sheet(tl, onset, sheet, tmpdir)
    clip_path, clip_t = tl.clip_at(onset - 6)
    rt = next((r for r in args.roots if clip_path.startswith(r + os.sep)), None)
    clip_rel = os.path.relpath(clip_path, rt) if rt else os.path.basename(clip_path)
    clip_drive = os.path.basename(os.path.dirname(rt.rstrip(os.sep))) if rt else ""
    local_onset = clips[ci][0] + datetime.timedelta(seconds=onset + off)
    return dict(
        id=eid, flip_time=fl["time"], flip_kind=fl["kind"], road=fl["road_before"], heading=fl["heading"],
        osm_class=fl["osm_class"], osm_before=fl["osm_before"], osm_after=fl["osm_after"],
        clip=os.path.basename(clip_path), clip_rel=clip_rel, clip_drive=clip_drive, clip_t=round(clip_t), clip_offset_h=off // 3600,
        onset_local=local_onset.strftime("%Y-%m-%d %H:%M:%S"),
        onset_frac=round(local_onset.microsecond / 1e6, 2),      # onset_local is cut to whole seconds; the stills use the exact onset
        lat=round(tl.lat[io], 6), lon=round(tl.lon[io], 6),
        sign_lat=round(tl.lat[isg], 6), sign_lon=round(tl.lon[isg], 6), sign_hd=round(tl.hd[isg]),
        step=dict(kind=a["kind"], pre=a["pre"], post=a["post"], delta=a["delta"], secs=round(a["end"] - a["onset"], 1), fit=a.get("fit", ""), before_flip_s=round(a["tk"] - a["onset"], 1)),
        fsd=dict(pct=round(100 * engaged), off_s=round(off_s, 1), states=states, brake=brake),
        frames=frames, ser=dict(t0=-20, dt=0.5, v=ser_v, ap=ser_ap, br=ser_br, bk=ser_bk),
        indicator=indicator_info(tl, onset), cruise=cruise_info(tl, onset, a),
        sheet="frames_1s/%s.jpg" % eid if cwch else None, cw=cwch[0] if cwch else 0, ch=cwch[1] if cwch else 0,
    )


def build(args):
    t_start = time.time()
    data = os.path.expanduser(args.data_dir)
    ana = os.path.join(data, "drives", "analysis")
    outdir = os.path.join(ana, "dashcam_flip_review")
    fdir = os.path.join(outdir, "frames_1s")
    os.makedirs(fdir, exist_ok=True)
    cands = args.teslacam or (DR.teslacam_roots(True) if getattr(args, "with_secondary", False) else TESLACAM_CANDIDATES)
    roots = []
    for c in cands:
        c = os.path.expanduser(c)
        if os.path.isdir(c) and c not in roots:
            roots.append(c)
    if not roots:
        print("TeslaCam folder not found; use --teslacam")
        return 1
    for r in roots:
        print("TeslaCam:", r)
    args.roots = roots
    clips = find_clips(roots, args.since)
    starts = [c[0] for c in clips]
    print(len(clips), "front clips since", args.since)
    flips = list(csv.DictReader(open(os.path.join(ana, "fsd_transitions.csv"))))
    cache = Cache()
    cdir = os.path.join(outdir, "cache7")
    os.makedirs(cdir, exist_ok=True)
    events, skipped, new_sheets, todo = [], 0, 0, 0
    tmpdir = tempfile.mkdtemp(prefix="dfr_")
    for n, fl in enumerate(flips):
        eid = "E%03d" % (n + 1)
        if args.only != "all" and fl["kind"] != args.only:
            continue
        cpath = os.path.join(cdir, eid + ".json")
        if os.path.exists(cpath) and not args.force:
            try:
                rec = json.load(open(cpath))
                if rec.get("none") and rec.get("n_clips") == len(clips):
                    skipped += 1
                    continue
                if rec.get("none"):      # clips were added since (saved events, another drive): look again
                    raise ValueError
                if "indicator" in rec:   # older cache records lack indicator/cruise: recompute them (stills are kept)
                    events.append(rec)
                    continue
            except Exception:
                pass
        if (time.time() - t_start) > args.budget:
            todo += 1
            continue
        rec = compute_event(fl, eid, clips, starts, cache, fdir, tmpdir, args)
        if rec is None:
            skipped += 1
            json.dump({"none": True, "n_clips": len(clips)}, open(cpath, "w"))
            continue
        if rec["sheet"]:
            new_sheets += 1
        if rec["sheet"] or args.no_frames:
            json.dump(rec, open(cpath, "w"))
        events.append(rec)
        if len(cache.d) > 12:
            cache.d.clear()
    shutil.rmtree(tmpdir, ignore_errors=True)
    coverage_report(flips, clips, args)
    # csv
    if args.only != "all":
        events = [e for e in events if e["flip_kind"] == args.only]
    try:
        sr = json.load(open(os.path.join(outdir, "signs", "sign_reads.json")))
    except Exception:
        sr = {}
    for e in events:
        clip_source(e, args.roots)
    try:      # sign_auto.py: automatic marks (green location signs) for every event
        auto = json.load(open(os.path.join(outdir, "signs", "auto_marks.json")))
    except Exception:
        auto = {}
    for e in events:
        e["marks_auto"] = auto.get(e["id"], [])
    for e in events:      # sign_scan.py strips + the readings of them (sign_reads.json)
        r = sr.get(e["id"])
        has = os.path.exists(os.path.join(outdir, "signs", e["id"] + ".jpg"))
        e["sign"] = dict(r, strip=("signs/%s.jpg" % e["id"]) if has else "") if r else None
    with open(os.path.join(ana, "dashcam_flip_events.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "tessie_time", "flip", "step", "speed_before", "speed_after", "delta", "fsd_pct", "brake", "road", "heading",
                    "osm_class", "lat", "lon", "clip"])
        for e in events:
            w.writerow([e["id"], e["flip_time"], e["flip_kind"], e["step"]["kind"], e["step"]["pre"], e["step"]["post"], e["step"]["delta"],
                        e["fsd"]["pct"], int(e["fsd"]["brake"]), e["road"], e["heading"], e["osm_class"], e["lat"], e["lon"], e["clip"]])
    summary = dict(built=datetime.datetime.now().strftime("%d %b %Y %H:%M"), events=len(events), flips=len(flips), skipped=skipped,
                   clips=len(clips), since=args.since, mac_root=args.mac_root, cols=COLS, rows=ROWS,
                   pending=todo + sum(1 for e in events if not e["sheet"]))
    tpl = open(os.path.join(HERE, "lib", "dashcam_flip_review_template.html")).read()
    try:   # your calls, exported from the page's shared store (see AGENTS.local.md): new pages start with them
        revs = json.load(open(os.path.join(ana, "dashcam_flip_reviews.json")))
    except Exception:
        revs = {}
    html = tpl.replace("/*DATA*/null", json.dumps(dict(e=events, s=summary, r=revs), separators=(",", ":")))
    with open(os.path.join(outdir, "index.html.tmp"), "w") as f:
        f.write(html)
    os.replace(os.path.join(outdir, "index.html.tmp"), os.path.join(outdir, "index.html"))
    print("%d events with footage (%d of %d flips have none), %d new contact sheets, %d still missing -> %s (%.0f KB)" % (
        len(events), skipped, len(flips), new_sheets, summary["pending"], os.path.join(outdir, "index.html"), len(html) / 1024))
    if summary["pending"] and not args.no_frames:
        print("run again to finish the remaining contact sheets")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    ap.add_argument("--teslacam", action="append", default=None, help="TeslaCam folder; repeat for several drives (default: every drive found)")
    ap.add_argument("--since", default="2026-10-01")
    ap.add_argument("--with-secondary", action="store_true", help="also read the JOWUA and Tesla USB drives (default: the Archive only)")
    ap.add_argument("--mac-root", default="/Volumes/TESLADRIVE/TeslaCam", help="where the TeslaCam folder lives on the Mac (for the Play clip links)")
    ap.add_argument("--budget", type=float, default=1e9, help="stop extracting new contact sheets after N seconds")
    ap.add_argument("--force", action="store_true", help="rebuild contact sheets that already exist")
    ap.add_argument("--only", default="110->100", help="keep only flips of this kind (Tessie): 110->100, 100->110 or all")
    ap.add_argument("--no-frames", action="store_true", help="skip the stills (page shows speed charts only)")
    sys.exit(build(ap.parse_args()))


if __name__ == "__main__":
    main()
