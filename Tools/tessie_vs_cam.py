#!/usr/bin/env python3
"""Why do Tessie and the dashcam disagree at a flip? (REQ-192)

For every flip with dashcam footage, take the Tessie drive points around the flip (one every ~30 s: time, GPS,
speed, odometer) and find where the dashcam GPS track passes the same spot. That gives, per Tessie point:
  clock_diff  = Tessie time - clip clock at the same place   (a whole number of hours = timezone; the rest = Tessie lag)
  speed_diff  = Tessie speed - dashcam speed at that place
  odo_kmh     = speed from the odometer between two Tessie points (what the car really did on average)
Writes <data>/drives/analysis/tessie_vs_cam.csv and prints a summary per day.
Usage: tessie_vs_cam.py [--data-dir DIR] [--teslacam DIR]
"""
import argparse, csv, datetime, glob, json, math, os, statistics, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dashcam_flip_review as D  # noqa: E402


def load_drive(path, cache={}):
    if path not in cache:
        rows = []
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                try:
                    rows.append((datetime.datetime.strptime(r["Timestamp (AEDT)" if "Timestamp (AEDT)" in r else [k for k in r if k.startswith("Timestamp")][0]], "%Y-%m-%d %H:%M:%S"),
                                 float(r["Latitude"]), float(r["Longitude"]), float(r["Speed (km/h)"] or 0), float(r["Odometer (km)"] or 0)))
                except Exception:
                    pass
        cache[path] = rows
    return cache[path]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=D.DEFAULT_DATA)
    ap.add_argument("--teslacam", default=None)
    a = ap.parse_args()
    ana = os.path.join(a.data_dir, "drives", "analysis")
    root = a.teslacam or next((os.path.expanduser(c) for c in D.TESLACAM_CANDIDATES if os.path.isdir(os.path.expanduser(c))), None)
    clips = D.find_clips(root, "2026-10-01")
    starts = [c[0] for c in clips]
    names = {os.path.basename(c[1]): i for i, c in enumerate(clips)}
    flips = list(csv.DictReader(open(os.path.join(ana, "fsd_transitions.csv"))))
    cache = D.Cache()
    out = []
    SHIFTS = list(range(-60, 61, 5))
    scan = {x: [] for x in SHIFTS}
    for n, fl in enumerate(flips):
        eid = "E%03d" % (n + 1)
        cp = os.path.join(ana, "dashcam_flip_review", "cache2", eid + ".json")
        if not os.path.exists(cp):
            continue
        rec = json.load(open(cp))
        if "clip" not in rec:
            continue
        ci = names.get(rec["clip"])
        if ci is None:
            continue
        off = rec["clip_offset_h"] * 3600
        tl = D.Timeline(clips, starts, ci, cache)
        t0 = clips[ci][0]
        dr = None
        for p in glob.glob(os.path.join(a.data_dir, "drives", "*", fl["file"])):
            dr = load_drive(p)
        if not dr:
            continue
        T = datetime.datetime.strptime(fl["time"], "%Y-%m-%d %H:%M:%S")
        pts = [r for r in dr if abs((r[0] - T).total_seconds()) <= 95]
        prev = None
        for r in pts:
            t, la, lo, sp, odo = r
            # nearest dashcam GPS sample (within +-150 s of the expected clip time)
            exp = (t - datetime.timedelta(seconds=off) - t0).total_seconds()
            best = None
            for i in range(0, len(tl.t), 6):
                if abs(tl.t[i] - exp) > 150 or not tl.lat[i]:
                    continue
                d = D.hav_km(la, lo, tl.lat[i], tl.lon[i]) * 1000
                if best is None or d < best[0]:
                    best = (d, i)
            if not best or best[0] > 400:
                prev = r
                continue
            d, i = best
            # refine around i
            for j in range(max(0, i - 6), min(len(tl.t), i + 7)):
                dd = D.hav_km(la, lo, tl.lat[j], tl.lon[j]) * 1000
                if dd < d:
                    d, i = dd, j
            cam_t = t0 + datetime.timedelta(seconds=tl.t[i])
            clock = (t - cam_t).total_seconds()
            # dashcam speed over 3 s around that spot
            lo_i, hi_i = tl.idx(tl.t[i] - 1.5), tl.idx(tl.t[i] + 1.5)
            cv = statistics.median(tl.v[lo_i:hi_i + 1])
            odo_kmh = ""
            if prev and 0 < (t - prev[0]).total_seconds() <= 70:
                odo_kmh = round((odo - prev[4]) / ((t - prev[0]).total_seconds() / 3600), 1)
            out.append(dict(id=eid, flip_time=fl["time"], kind=fl["kind"], tessie_time=t.strftime("%H:%M:%S"), tessie_kmh=sp, dashcam_kmh=round(cv, 1),
                            speed_diff=round(sp - cv, 1), odo_kmh=odo_kmh, gps_dist_m=round(d), clock_diff_s=round(clock), clip_hours_behind=rec["clip_offset_h"],
                            lag_after_hours_s=round(clock - off)))
            w = tl.v[tl.idx(tl.t[i] - 40):tl.idx(tl.t[i] + 40) + 1]
            for sh in (SHIFTS if max(w) - min(w) > 5 else []):       # does the Tessie speed match the dashcam speed some seconds earlier/later?
                tt = tl.t[i] + sh
                if tl.covers(tt):
                    q = tl.idx(tt)
                    scan[sh].append(abs(sp - statistics.median(tl.v[tl.idx(tt - 1.5):tl.idx(tt + 1.5) + 1]) - 2.7))
            prev = r
    path = os.path.join(ana, "tessie_vs_cam.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(len(out), "Tessie points matched ->", path)
    print("Tessie speed vs dashcam speed at +shift seconds (median |difference - 2.7 km/h|, lower = better match):")
    print("  ".join("%+d:%.1f" % (x, statistics.median(scan[x])) for x in SHIFTS if scan[x]))
    by = {}
    for r in out:
        by.setdefault(r["flip_time"][:10], []).append(r)
    for day, rs in sorted(by.items()):
        cd = [r["clock_diff_s"] for r in rs]
        sd = [r["speed_diff"] for r in rs]
        print("%s n=%3d clock_diff median %6.0fs (min %d max %d)  after removing whole hours: median %5.1fs  speed_diff median %+.1f km/h (IQR %+.1f..%+.1f)  |gps| median %d m" % (
            day, len(rs), statistics.median(cd), min(cd), max(cd), statistics.median([r["lag_after_hours_s"] for r in rs]), statistics.median(sd),
            sorted(sd)[len(sd) // 4], sorted(sd)[3 * len(sd) // 4], statistics.median([r["gps_dist_m"] for r in rs])))


if __name__ == "__main__":
    main()
