"""app_drive_review.py - a review of one drive, started by hand from the Review tab of that drive (tesla_app.py).

Three kinds of event are found from the dashcam telemetry (one reading a second per minute of footage, kept in <data>/teslacam_timeline, see
teslacam_timeline.py - nothing is read from the video for this part):

  intervention   FSD switched off (to manual, Autosteer or TACC): what the driver was doing at that moment (brake / accelerator / steering),
                 how long FSD was off, and whether it was the end of the trip (stopped)
  accelerator    the accelerator pressed while FSD was on (a run of seconds at or above accel_min_pct): length, peak, speed
  speed change   a lasting change between two speeds the user picks (default 100 -> 110 km/h, optionally both ways) while FSD stayed on

Then, for each event only, the FRONT camera is looked at: a still every second from event_before_s before to event_after_s after (the usual
28 stills; a longer event is thinned to max_event_stills), every round red-ringed speed-limit sign in them is read (sign_finder.py + tesseract,
as app_review.py does for a whole drive), and the stills are kept as one contact sheet per event. No other camera is touched, and a drive's
other minutes are not looked at. The events and the sheets are kept in <data>/drive_review/drives/<stamp>.json and .../events/<stamp>/ so
a finished review opens instantly; running it again replaces them. Nothing on a drive or in Tessie is changed.

Time: UTC is the clock. The dashcam's file names are the car's clock, which clip_clock.py turns into UTC minute by minute (it can sit on standard time for
days, and change when the car crosses a time-zone border, even inside this drive), and Tessie's local times are turned into UTC with app.timezone, so a drive and its footage are always compared as the same instant. Each event keeps its UTC instant ("utc"); "t" is the drive page's local
clock, which is only what is shown.
"""
import datetime
import json
import math
import os
import statistics
import subprocess
import tempfile
import threading
import time

import app_config
import app_review
import clip_clock as CC
import drive_paths as DP
import drive_fuse as DF
import teslacam_timeline as TL

OPTS = dict(accel_min_pct=5,        # accelerator at or above this (%) counts as pressed
            merge_s=3,              # presses / levels with a gap shorter than this are one
            speed_from=100,         # the default pair of the speed-change finder (km/h)
            speed_to=110,
            speed_tol_kmh=3,        # a speed this close to the target is "at" it
            speed_hold_s=8,         # and has to stay there this long to be a level, not a pass-through
            speed_window_s=90,      # the second level has to start within this long of the first one ending
            event_before_s=12,      # stills from this long before an event ...
            event_after_s=15,       # ... to this long after it (28 stills with the one a second)
            max_event_stills=40,    # a longer event is thinned to this many
            steer_deg=15,           # the wheel moved this much around a switch-off = "steering"
            sheet_cols=7,
            sheet_tile_px=400)
MODES = {0: "Manual", 1: "FSD", 2: "Autosteer", 3: "TACC"}
MAXGAP = 3                          # seconds between two readings that are still consecutive
_LOCK = threading.RLock()


def _mode(n):
    return MODES.get(n, "mode %s" % n)


def _opts(extra=None):
    o = dict(OPTS)
    o.update({k: v for k, v in (app_config.get("review") or {}).items() if k in OPTS})
    for k, v in (extra or {}).items():
        if k in OPTS and v is not None:
            try:
                o[k] = type(OPTS[k])(v)
            except (TypeError, ValueError):
                pass
    return o


# ---------------------------------------------------------------------------------------------------------------- events
def to_rows(cam):
    """camera_points tuples -> dicts."""
    return [dict(t=c[0], la=c[1], lo=c[2], v=c[3], ap=c[4], ac=c[5], st=c[6], blk=c[7], br=c[8], stamp=c[9], i=c[10]) for c in cam]


def interventions(rows, o):
    out = []
    for k in range(1, len(rows)):
        p, c = rows[k - 1], rows[k]
        if c["t"] - p["t"] > MAXGAP or p["ap"] != 1 or c["ap"] in (1, None):
            continue
        back = next((r["t"] for r in rows[k + 1:k + 241] if r["ap"] == 1), None)
        j = k
        while j > 0 and rows[j - 1]["ap"] == 1 and rows[j]["t"] - rows[j - 1]["t"] <= MAXGAP:
            j -= 1
        win = rows[max(0, k - 4):k + 2]
        flags = []
        if any(r["br"] for r in win):
            flags.append("brake")
        if any((r["ac"] or 0) >= o["accel_min_pct"] for r in win):
            flags.append("accelerator")
        sv = [r["st"] for r in win if r["st"] is not None]
        if sv and max(sv) - min(sv) >= o["steer_deg"]:
            flags.append("steering")
        out.append(dict(kind="intervention", t=c["t"], t1=back if back is not None else c["t"], v=c["v"], la=c["la"], lo=c["lo"],
                        from_mode=_mode(p["ap"]), to_mode=_mode(c["ap"]), flags=flags,
                        off_s=round(back - c["t"]) if back is not None else None, fsd_before_s=round(c["t"] - rows[j]["t"]),
                        at_stop=(c["v"] or 0) < 3))
    return out


def accel_presses(rows, o):
    out, cur = [], None

    def close():
        if not cur:
            return
        vs = cur["vs"]
        after = [r for r in rows if cur["t1"] < r["t"] <= cur["t1"] + 5]
        out.append(dict(kind="accelerator", t=cur["t"], t1=cur["t1"], dur_s=round(cur["t1"] - cur["t"]) + 1, peak=max(cur["ac"]), mean=round(statistics.mean(cur["ac"])),
                        v=cur["v0"], v1=cur["v1"], la=cur["la"], lo=cur["lo"], ended_in_off=any(r["ap"] not in (1, None) for r in after)))
    for r in rows:
        on = r["ap"] == 1 and (r["ac"] or 0) >= o["accel_min_pct"]
        if not on:
            continue
        if cur and r["t"] - cur["t1"] <= o["merge_s"] + 1:
            cur["t1"] = r["t"]
            cur["ac"].append(r["ac"])
            cur["vs"].append(r["v"])
            cur["v1"] = r["v"]
        else:
            close()
            cur = dict(t=r["t"], t1=r["t"], ac=[r["ac"]], vs=[r["v"]], v0=r["v"], v1=r["v"], la=r["la"], lo=r["lo"])
    close()
    return out


def _levels(rows, target, o):
    """Runs of consecutive readings within the tolerance of a speed that last at least speed_hold_s: [(first index, last index)]."""
    runs, a, b = [], None, None

    def close():
        if a is not None and rows[b]["t"] - rows[a]["t"] + 1 >= o["speed_hold_s"]:
            runs.append((a, b))
    for k, r in enumerate(rows):
        ok = r["v"] is not None and abs(r["v"] - target) <= o["speed_tol_kmh"]
        if ok and a is not None and r["t"] - rows[b]["t"] <= MAXGAP:
            b = k
            continue
        close()
        a = b = k if ok else None
    close()
    return runs


def speed_changes(rows, o, a_kmh, b_kmh, both=False):
    out = []
    for lo, hi in ([(a_kmh, b_kmh), (b_kmh, a_kmh)] if both else [(a_kmh, b_kmh)]):
        A, B = _levels(rows, lo, o), _levels(rows, hi, o)
        for n, (a0, a1) in enumerate(A):
            nxt = A[n + 1][0] if n + 1 < len(A) else None
            nb = next((x for x in B if x[0] > a1), None)
            if not nb or rows[nb[0]]["t"] - rows[a1]["t"] > o["speed_window_s"] or (nxt is not None and nxt < nb[0]):
                continue
            mid = rows[a1:nb[0] + 1]
            if rows[a1]["ap"] != 1 or rows[nb[0]]["ap"] != 1 or sum(1 for r in mid if r["ap"] == 1) < 0.9 * len(mid):
                continue
            out.append(dict(kind="speed", t=rows[a1]["t"], t1=rows[nb[0]]["t"], v=round(statistics.median(r["v"] for r in rows[a0:a1 + 1])),
                            v1=round(statistics.median(r["v"] for r in rows[nb[0]:nb[1] + 1])), la=rows[a1]["la"], lo=rows[a1]["lo"],
                            dur_s=round(rows[nb[0]]["t"] - rows[a1]["t"]), pressed=any((r["ac"] or 0) >= o["accel_min_pct"] for r in mid)))
    return out


def find_events(rows, o, a_kmh, b_kmh, both):
    ev = interventions(rows, o) + accel_presses(rows, o) + speed_changes(rows, o, a_kmh, b_kmh, both)
    ev.sort(key=lambda e: e["t"])
    n = {"intervention": 0, "accelerator": 0, "speed": 0}
    for e in ev:
        n[e["kind"]] += 1
        e["id"] = {"intervention": "I", "accelerator": "A", "speed": "S"}[e["kind"]] + str(n[e["kind"]])
    return ev


# ----------------------------------------------------------------------------------------------------------------- stills
def _extract(path, lo, count, step, tmp, tag):
    """Stills of a front clip: `count` frames from second `lo`, one every `step` seconds -> [file]."""
    pat = os.path.join(tmp, "%s_%%03d.jpg" % tag)
    subprocess.run(["ffmpeg", "-v", "error", "-ss", "%g" % lo, "-i", path, "-t", "%g" % (count * step), "-vf", "fps=%g" % (1.0 / step), "-q:v", "2", pat],
                   stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
    return sorted(os.path.join(tmp, f) for f in os.listdir(tmp) if f.startswith(tag + "_") and f.endswith(".jpg"))


def _end(ev):
    """Where an event ends for the stills: a switch-off is one moment (how long FSD stayed off is a fact about it, not part of it)."""
    return ev["t"] if ev["kind"] == "intervention" else ev["t1"]


def _read_signs(im, o_rv):
    import sign_finder as SF
    out = []
    for side, roi in (("L", SF.ROI_LEFT), ("R", SF.ROI_RIGHT)):
        for c in SF.find_signs(im, roi=roi):
            if c.get("ok") and c["conf"] >= o_rv["min_conf"] and c["text"]:
                out.append(dict(text=c["text"], side=side, conf=round(c["conf"]), crop=c["crop"]))
                break
    return out


def look(ev, rows, front, base, stamp, o, stop):
    """The front-camera stills of one event: reads the signs and writes the contact sheet. Sets ev["sheet"], ev["signs"], ev["n_stills"]."""
    import cv2
    from concurrent.futures import ThreadPoolExecutor
    o_rv = dict(app_review.OPTS)
    t_a, t_b = ev["t"] - o["event_before_s"], _end(ev) + o["event_after_s"]
    span = t_b - t_a
    step = max(1, int(math.ceil(span / float(o["max_event_stills"]))))
    by_t = {int(r["t"]): r for r in rows}
    want = []                                               # (T, stamp, second in the clip)
    for T in range(int(math.ceil(t_a)), int(t_b) + 1, step):
        r = by_t.get(T) or by_t.get(T - 1) or by_t.get(T + 1)
        if r and r["stamp"] in front:
            want.append((T, r["stamp"], r["i"]))
    shots = []                                              # (T, image)
    with tempfile.TemporaryDirectory() as tmp:
        k = 0
        while k < len(want):                                # a run in one clip, in steps of `step` seconds
            j = k
            while j + 1 < len(want) and want[j + 1][1] == want[k][1] and want[j + 1][2] - want[j][2] == step:
                j += 1
            files = _extract(front[want[k][1]], want[k][2], j - k + 1, step, tmp, "r%d" % k)
            for n, f in enumerate(files[:j - k + 1]):
                im = cv2.imread(f)
                if im is not None:
                    shots.append((want[k + n][0], im))
            k = j + 1
        if stop():
            return
        found = []
        with ThreadPoolExecutor(max(1, int(o_rv["workers"]))) as ex:
            for (T, im), lst in zip(shots, ex.map(lambda s: _read_signs(s[1], o_rv), shots)):
                for d in lst:
                    found.append(dict(d, t=T))
        signs = []
        for d in found:
            g = next((x for x in reversed(signs) if x["text"] == d["text"] and x["side"] == d["side"] and d["t"] - x["t1"] <= o_rv["merge_s"]), None)
            if g is None:
                signs.append(dict(text=d["text"], side=d["side"], t=d["t"], t0=d["t"], t1=d["t"], n=0, conf=-1, crop=None, f=""))
                g = signs[-1]
            g["n"] += 1
            g["t1"] = d["t"]
            if d["conf"] > g["conf"]:
                g.update(conf=d["conf"], crop=d["crop"], t=d["t"])
        os.makedirs(os.path.join(base, "crops"), exist_ok=True)
        for n, g in enumerate(signs):
            crop = g.pop("crop")
            name = "%s_%s_%02d.jpg" % (stamp, ev["id"], n)
            if crop is not None and crop.shape[0] > 0 and crop.shape[1] > 0:
                if max(crop.shape[:2]) > 160:
                    z = 160.0 / max(crop.shape[:2])
                    crop = cv2.resize(crop, (max(1, int(crop.shape[1] * z)), max(1, int(crop.shape[0] * z))))
                cv2.imwrite(os.path.join(base, "crops", name), crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                g["f"] = name
        ev["signs"] = signs
        ev["n_stills"] = len(shots)
        ev["sheet"] = _sheet(shots, ev, base, stamp, o) if shots else ""
        cols, w, h = _geom(o)                               # so the page can cut the sheet into its stills: where each one is (seconds from the event) and how the sheet is laid out
        ev["shots"] = [round(T - ev["t"], 1) for T, _ in shots]
        ev["tile"] = dict(cols=cols, w=w, h=h)


def _geom(o):
    """(columns, tile width, tile height) of a contact sheet."""
    w = int(o["sheet_tile_px"])
    return max(1, int(o["sheet_cols"])), w, int(w * 9 / 16)


def _sheet(shots, ev, base, stamp, o):
    import cv2
    import numpy as np
    cols, w, h = _geom(o)
    sc = w / 240.0
    col = {"intervention": (60, 60, 230), "accelerator": (30, 160, 250), "speed": (230, 130, 40)}[ev["kind"]]      # BGR
    rows_n = (len(shots) + cols - 1) // cols
    sheet = np.zeros((rows_n * h, cols * w, 3), np.uint8)
    for n, (T, im) in enumerate(shots):
        tile = cv2.resize(im, (w, h))
        rel = int(round(T - ev["t"]))
        if ev["t"] - 0.5 <= T <= _end(ev) + 0.5:
            cv2.rectangle(tile, (0, 0), (w - 1, h - 1), col, max(4, int(4 * sc)))
        lab = "%+ds" % rel if rel else "0"
        cv2.rectangle(tile, (0, 0), (int((8 * len(lab) + 12) * sc), int(20 * sc)), (0, 0, 0), -1)
        cv2.putText(tile, lab, (int(5 * sc), int(15 * sc)), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * sc, (255, 255, 255), 1 if sc < 1.3 else 2, cv2.LINE_AA)
        r_, c_ = divmod(n, cols)
        sheet[r_ * h:(r_ + 1) * h, c_ * w:(c_ + 1) * w] = tile
    d = os.path.join(base, "events", stamp)
    os.makedirs(d, exist_ok=True)
    name = ev["id"] + ".jpg"
    cv2.imwrite(os.path.join(d, name), sheet, [cv2.IMWRITE_JPEG_QUALITY, 78])
    return name


# -------------------------------------------------------------------------------------------------------------------- job
class DriveReview:
    def __init__(self, data_dir, tc):
        self.dir = data_dir
        self.base = os.path.join(data_dir, "drive_review")
        self.tc = tc
        self.t = None
        self.stop_flag = False
        self.st = dict(running=False, stamp="", stage="", done=0, total=0, error="", t0=0)

    # ---- stored results
    def _path(self, stamp):
        return os.path.join(self.base, "drives", stamp + ".json")

    def result(self, stamp):
        try:
            with open(self._path(stamp), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def _save(self, stamp, res):
        os.makedirs(os.path.dirname(self._path(stamp)), exist_ok=True)
        with open(self._path(stamp) + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(res, fh, separators=(",", ":"))
        os.replace(self._path(stamp) + ".tmp", self._path(stamp))

    def status(self):
        s = dict(self.st)
        s["running"] = bool(self.t and self.t.is_alive())
        return s

    def defaults(self):
        o = _opts()
        return dict(speed_from=o["speed_from"], speed_to=o["speed_to"], both=False)

    def start(self, stamp, teslacam, params):
        if self.t and self.t.is_alive():
            return self.status()
        self.stop_flag = False
        self.st = dict(running=True, stamp=stamp, stage="Starting", done=0, total=0, error="", t0=time.time())
        self.t = threading.Thread(target=self._run, args=(stamp, teslacam, params or {}), daemon=True)
        self.t.start()
        return self.status()

    def stop(self):
        self.stop_flag = True
        return self.status()

    def _stage(self, s, done=0, total=0):
        self.st.update(stage=s, done=done, total=total)

    # ---- the job
    def _run(self, stamp, teslacam, params):
        try:
            self._review(stamp, teslacam, params)
        except Exception as e:                                  # a missing tool, a drive pulled out, a bad file: say so
            self.st["error"] = "%s: %s" % (type(e).__name__, e)

    def _review(self, stamp, teslacam, params):
        o = _opts(params)
        a_kmh, b_kmh, both = float(o["speed_from"]), float(o["speed_to"]), bool(params.get("both"))
        folder = DP.folder_for(os.path.join(self.dir, "drives", "deepdive"), stamp)
        self._stage("Reading the drive")
        with open(os.path.join(folder, "drive_summary.json"), encoding="utf-8") as fh:
            sm = json.load(fh)
        t0, t1 = DF._t(sm["start"], "%Y-%m-%d %H:%M:%S"), DF._t(sm["end"], "%Y-%m-%d %H:%M:%S")
        tp = DF.tessie_points(folder)
        if len(tp) < 2:
            raise ValueError("this drive has no sample file")
        store, _ = self.tc._tl("A")
        front, mins = {}, []
        if teslacam:
            recent, saved, _s = self.tc._sources("A", teslacam)
            mins = TL.minutes(recent, saved)
            front = {m["stamp"]: self.tc._front(teslacam, m) for m in mins if "front" in m["cams"]}
        ts, las, los = [p["t"] for p in tp], [p["la"] for p in tp], [p["lo"] for p in tp]
        self._stage("Matching the dashcam telemetry")
        clock, cam = DF.clock_offset(ts, las, los, store, t0, t1, None, self.dir)
        if clock is None and front:                             # nothing read for this drive yet: read the minutes that cover it (front camera only)
            u0, u1 = DF.utc(t0), DF.utc(t1)
            todo = [(m["stamp"], front[m["stamp"]]) for m in mins if m["stamp"] in front and store.summary(m["stamp"]) is None and CC.maybe_in(m["stamp"], u0 - 60, u1 + 60)]
            if todo:                                            # under any offset the car's clock may have had: reading them is what teaches the clock
                self._stage("Reading the telemetry of %d minute(s) of footage" % len(todo), 0, len(todo))
                TL.read_many(store, todo, stop=lambda: self.stop_flag)
            if self.stop_flag:
                return
            CC.refresh(self.dir, store, wait=True)
            clock, cam = DF.clock_offset(ts, las, los, store, t0, t1, None, self.dir)
        if clock is None:
            res = dict(stamp=stamp, made=_now(), params=dict(o, both=both), clock=None, events=[], stills=False,
                       note="No dashcam telemetry lies on this drive's route (the footage is not on a connected drive, or was never read).")
            self._save(stamp, res)
            return
        rows = to_rows(cam)
        events = find_events(rows, o, a_kmh, b_kmh, both)
        req = app_review.requirements()
        can_look = bool(front) and not (req["py"] or req["cmd"])
        note = ""
        if not front:
            note = "The Archive is not connected, so the events are listed without stills."
        elif not can_look:
            note = "Stills and sign reading need: %s" % "; ".join(req["hint"])
        done_ev = 0
        if can_look:
            for ev in events:
                if self.stop_flag:
                    return
                self._stage("Looking at the front camera: %s" % ev["id"], done_ev, len(events))
                try:
                    look(ev, rows, front, self.base, stamp, o, lambda: self.stop_flag)
                except Exception as e:
                    ev["look_error"] = "%s: %s" % (type(e).__name__, e)
                done_ev += 1
        for ev in events:
            ev["utc"] = DF.utc(ev["t"])
            ev["clip"] = _clip_of(rows, ev["t"])
        secs_n = len(rows)
        res = dict(stamp=stamp, made=_now(), params=dict(o, both=both), clock=clock, t0=t0, t1=t1, poff=CC.local_offset(DF.utc(t0)), events=events, stills=can_look, note=note,
                   coverage=dict(seconds=secs_n, pct=round(100.0 * min(1.0, secs_n / max(1.0, t1 - t0)))),
                   counts={k: sum(1 for e in events if e["kind"] == k) for k in ("intervention", "accelerator", "speed")})
        self._save(stamp, res)


def _clip_of(rows, t):
    r = min(rows, key=lambda x: abs(x["t"] - t)) if rows else None
    return dict(stamp=r["stamp"], s=r["i"]) if r else None


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
