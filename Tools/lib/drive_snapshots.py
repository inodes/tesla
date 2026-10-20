"""drive_snapshots.py - a still for every event of a drive, kept in that drive's own folder.

    <data>/drives/deepdive/<day>/<drive>/snapshots/timeline/<local time>.jpg        one still every `every_s` seconds (default 30) along the whole drive
    <data>/drives/deepdive/<day>/<drive>/snapshots/events/<id>/<+-sec>.jpg          a still every SECOND around the events that are to be analysed, large enough to read signs (OCR)
    <data>/drives/deepdive/<day>/<drive>/snapshots/index.json                      what each is: group, text, UTC and local time with its zone, place

Notable events get a still every second from event_before_s before to event_after_s after (review settings), 1000 px wide: FSD handed back, accelerator
pressed while FSD was on, saved clips, and a lasting speed change to or from 40, 50, 100 or 110 km/h (school zones, the 100 / 110 plateaus;
config.json snapshots.burst_speeds). Every other event gets the one still at the moment.
An event is what the dashcam telemetry shows (app_drive_review.find_events: FSD handed back, accelerator pressed while FSD was on, a lasting
speed change) or a saved clip (the dashcam button). The still is the front-camera still of that second from the kept stills (teslacam_stills): made
from the video while it is on a connected drive (and kept), or taken from the kept ones once the video is gone. Nothing on a drive is changed.

Time: UTC is the clock; each snapshot carries the local time of the place where it was taken, with its zone (a drive can cross a time zone).
"""
import bisect
import datetime
import json
import os
import re
import subprocess
import tempfile

import app_config
import app_drive_review as AR
import clip_clock as CC
import drive_fuse as DF
import drive_paths as DP
import teslacam_stills as ST
import teslacam_timeline as TL

FMT = "%Y-%m-%d_%H-%M-%S"


def _local(utc, la, lo):
    """(naive local datetime, zone abbreviation) at the place; the zone from the position, else Sydney."""
    from zoneinfo import ZoneInfo
    import timezones as TZ
    z = None
    if la is not None and lo is not None:
        try:
            z = TZ.resolve_timezone(la, lo)
        except Exception:
            z = None
    loc = datetime.datetime.fromtimestamp(utc, datetime.timezone.utc).astimezone(ZoneInfo(z or "Australia/Sydney"))
    return loc.replace(tzinfo=None), loc.tzname()


def _text(ev):
    k = ev["kind"]
    if k == "intervention":
        how = "/".join(ev.get("flags") or [])
        return "FSD off -> %s%s" % (ev.get("to_mode", ""), " (%s)" % how if how else "")
    if k == "accelerator":
        return "Accelerator pressed at %.0f km/h" % (ev.get("v") or 0)
    return "Speed %.0f -> %.0f km/h" % (ev.get("v") or 0, ev.get("v1") or 0)


def _burst(item, kinds, zones, tol):
    """True when the event gets a still every second: its kind is in `kinds`, and a speed change only when it starts or ends near one of the zone speeds
    (school zones 40, 50, the 100 / 110 plateaus); every other speed change gets the one still at the moment."""
    if item["kind"] not in kinds:
        return False
    if item["kind"] == "speed" and zones:
        return any(abs(v - z) <= tol for z in zones for v in (item.get("v"), item.get("v1")) if v is not None)
    return True


def front_clips(tc, teslacam):
    """{clip stamp: front video path} of the Archive (empty when it is not connected)."""
    if not teslacam:
        return {}
    recent, saved, _s = tc._sources("A", teslacam)
    return {m["stamp"]: tc._front(teslacam, m) for m in TL.minutes(recent, saved) if "front" in m["cams"]}


def _still(stills, front, stamp, sec):
    if stamp in front and not stills.has(stamp):
        stills.make(stamp, front[stamp])
    return stills.get(stamp, sec) if stills.has(stamp) else None


def _hires(video, lo, n, px, quality):
    """{second in the clip: JPEG bytes} of seconds lo .. lo+n-1 of a front clip, `px` wide, cut from the full-size frames (for reading signs)."""
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["ffmpeg", "-v", "error", "-ss", "%d" % lo, "-i", video, "-t", "%d" % n, "-vf", "fps=1,scale=%d:-2" % px, "-q:v", str(quality),
                        os.path.join(tmp, "f%03d.jpg")], stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
        for k, f in enumerate(sorted(x for x in os.listdir(tmp) if x.endswith(".jpg"))):
            with open(os.path.join(tmp, f), "rb") as fh:
                out[lo + k] = fh.read()
    return out


def build(data_dir, stamp, tc, front, stills, stop=None):
    """Write the snapshots of one drive. Returns dict(n, events, note)."""
    o = AR._opts()
    folder = DP.folder_for(os.path.join(data_dir, "drives", "deepdive"), stamp)
    with open(os.path.join(folder, "drive_summary.json"), encoding="utf-8") as fh:
        sm = json.load(fh)
    t0, t1 = DF._t(sm["start"], "%Y-%m-%d %H:%M:%S"), DF._t(sm["end"], "%Y-%m-%d %H:%M:%S")
    tp = DF.tessie_points(folder)
    if len(tp) < 2:
        return dict(n=0, events=0, note="no sample file")
    store, _ = tc._tl("A")
    ts, las, los = [p["t"] for p in tp], [p["la"] for p in tp], [p["lo"] for p in tp]
    clock, cam = DF.clock_offset(ts, las, los, store, t0, t1, None, data_dir)
    rows = []
    items = []                                              # dict(kind, id, text, utc, la, lo, stamp, sec, v)
    if clock is not None:
        rows = AR.to_rows(cam)
        by_t = {int(r["t"]): r for r in rows}
        for ev in AR.find_events(rows, o):
            r = by_t.get(int(ev["t"])) or by_t.get(int(ev["t"]) - 1) or by_t.get(int(ev["t"]) + 1)
            if r:
                items.append(dict(kind=ev["kind"], id=ev["id"], text=_text(ev), utc=DF.utc(ev["t"]), la=ev.get("la"), lo=ev.get("lo"), stamp=r["stamp"], sec=r["i"], v=ev.get("v"), v1=ev.get("v1")))
    # saved clips: the dashcam button, named by the car's clock
    known = sorted(stills.stamps() | set(front))
    for fn in sorted(os.listdir(os.path.join(folder, "clips"))) if os.path.isdir(os.path.join(folder, "clips")) else []:
        m = re.match(r"saved_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.mp4$", fn)
        if not m:
            continue
        s = m.group(1)
        k = bisect.bisect_right(known, s) - 1
        if k < 0:
            continue
        sec = int((datetime.datetime.strptime(s, FMT) - datetime.datetime.strptime(known[k], FMT)).total_seconds())
        if 0 <= sec < 60:
            items.append(dict(kind="saved", id="V", text="Saved clip (dashcam button)", utc=CC.utc(s), la=None, lo=None, stamp=known[k], sec=sec, v=None))
    items.sort(key=lambda x: x["utc"])
    out = os.path.join(folder, "snapshots")
    every = int(app_config.get("snapshots.every_s", 30))
    burst_kinds = set(app_config.get("snapshots.burst", ["intervention", "accelerator", "speed", "saved"]))
    zones = [float(z) for z in app_config.get("snapshots.burst_speeds", [40, 50, 100, 110])]      # a speed change to or from one of these gets a still every second
    tol = float(app_config.get("snapshots.burst_speed_tol", 6))
    index, n_img = [], [0]

    def clip_at(base, rel):
        """(clip stamp, second) of the car-clock instant base + rel seconds, or None."""
        dt = base + datetime.timedelta(seconds=rel)
        k = bisect.bisect_right(known, dt.strftime(FMT)) - 1
        if k < 0:
            return None
        sec = int((dt - datetime.datetime.strptime(known[k], FMT)).total_seconds())
        return (known[k], sec) if 0 <= sec < 60 else None

    def keep(rel_path, stamp_, sec_, utc, la, lo, group, text, v=None, jpg=None, px=0):
        px = px or int(app_config.get("stills.width", 400))
        dest = os.path.join(out, rel_path)
        if os.path.isfile(dest) and os.path.getsize(dest) > 0:      # made by an earlier, stopped run: kept as it is
            jpg = b"x"
        else:
            jpg = jpg or _still(stills, front, stamp_, sec_)
            if not jpg:
                return False
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest + ".part", "wb") as fh:
                fh.write(jpg)
            os.replace(dest + ".part", dest)
        loc, zone = _local(utc, la, lo)
        n_img[0] += 1
        index.append(dict(group=group, text=text, utc=int(utc), local=loc.strftime("%Y-%m-%d %H:%M:%S") + " " + (zone or ""),
                          la=round(la, 5) if la is not None else None, lo=round(lo, 5) if lo is not None else None, v=v, clip=stamp_, sec=sec_, px=px, file=rel_path.replace(os.sep, "/")))
        return True

    # a still every `every_s` seconds along the whole drive (where the dashcam telemetry covers it)
    if clock is not None and every > 0:
        nxt = None
        for r in rows:
            if stop and stop():
                break
            if nxt is None or r["t"] >= nxt:
                nxt = r["t"] + every
                utc = DF.utc(r["t"])
                loc, _z = _local(utc, r["la"], r["lo"])
                keep("timeline/%s.jpg" % loc.strftime("%H-%M-%S"), r["stamp"], r["i"], utc, r["la"], r["lo"], "timeline", "Timeline, one every %d s" % every, r["v"])
    for item in items:
        if stop and stop():
            break
        la, lo = item["la"], item["lo"]
        if la is None:
            r = min(tp, key=lambda p: abs(DF.utc(p["t"]) - item["utc"]))
            la, lo = r["la"], r["lo"]
        base = datetime.datetime.strptime(item["stamp"], FMT) + datetime.timedelta(seconds=item["sec"])
        gid = "%s_%s" % (item["id"], datetime.datetime.fromtimestamp(item["utc"], datetime.timezone.utc).strftime("%H%M%S")) if item["kind"] == "saved" else item["id"]
        if _burst(item, burst_kinds, zones, tol):
            wanted = []                                         # (rel, clip stamp, second)
            for rel in range(-int(o["event_before_s"]), int(o["event_after_s"]) + 1):
                c = clip_at(base, rel)
                if c:
                    wanted.append((rel, c[0], c[1]))
            big = {}                                            # (clip stamp, second) -> full-size JPEG, from the video while it is on the Archive
            px, q = int(app_config.get("snapshots.event_px", 1000)), int(app_config.get("snapshots.event_quality", 5))
            have_all = all(os.path.isfile(os.path.join(out, "events", gid, "%+03d.jpg" % w[0])) for w in wanted)
            for st in sorted({w[1] for w in wanted}):
                secs = [w[2] for w in wanted if w[1] == st]
                if st in front and px and not have_all:
                    for sec_, b in _hires(front[st], min(secs), max(secs) - min(secs) + 1, px, q).items():
                        big[(st, sec_)] = b
            for rel, st, sec_ in wanted:
                b = big.get((st, sec_))
                keep("events/%s/%+03d.jpg" % (gid, rel), st, sec_, item["utc"] + rel, la, lo, gid, item["text"] + (" (%+d s)" % rel if rel else ""), item["v"], b, px if b else 0)
        else:
            keep("events/%s/+00.jpg" % gid, item["stamp"], item["sec"], item["utc"], la, lo, gid, item["text"], item["v"])
    if stop and stop():
        return dict(n=n_img[0], events=len(items), note="stopped - run again to finish", done=False)
    if index:
        with open(os.path.join(out, "index.json.tmp"), "w", encoding="utf-8") as fh:
            json.dump(dict(stamp=stamp, made=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), every_s=every,
                           events=[dict(id=i["id"] if "id" in i else "", kind=i["kind"], text=i["text"], utc=int(i["utc"])) for i in items],
                           snapshots=index), fh, indent=1)
        os.replace(os.path.join(out, "index.json.tmp"), os.path.join(out, "index.json"))
    note = "" if clock is not None else "no dashcam telemetry on this drive's route"
    return dict(n=n_img[0], events=len(items), note=note, done=True)
