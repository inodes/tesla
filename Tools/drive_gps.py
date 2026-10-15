#!/usr/bin/env python3
"""drive_gps.py - street-accurate drive paths from the dashcam's own GPS, in a mirror of the Tessie drive CSVs (REQ-201).

NAME
    drive_gps.py - a copy of each drive's Tessie CSV with the dashcam's GPS fixes added

SYNOPSIS
    drive_gps.py [--data-dir DIR] [--teslacam DIR] [--only STAMP] [--since DATE] [--force] [--budget SEC]

DESCRIPTION
    Tessie records a position about every 30 s (600-900 m apart while driving), so its line cuts corners. Every TeslaCam
    clip carries the car's GPS in each video frame. For each drive that has footage this writes, in a mirror folder

        drives/deepdive_gps/<stamp>/drive_deepdive_<stamp>.csv     same file name and same columns as Tessie's
        drives/deepdive_gps/<stamp>/drive_gps.json                 coverage and how well it agrees with Tessie

    The CSV holds every Tessie row unchanged plus one extra row per dashcam GPS fix (about one a second) with time, latitude,
    longitude, speed and shift state; its other Tessie columns are left empty rather than invented. Columns added at the end:
    Source (tessie / dashcam), Heading (deg), Autopilot State, Brake, Accelerator (%), Steering Angle (deg), Blinker,
    Camera Clip. A tool that reads Tessie's CSV can read this one by pointing at deepdive_gps instead of deepdive.
    Tessie's own files are never changed.

    The path is kept only when Tessie's own points lie on it (90% within 40 m, none beyond 120 m); otherwise only the json
    is written, saying why. drive_report.py draws a kept path over Tessie's line on the drive map.

OPTIONS
    --data-dir DIR   Tesla data folder (default: TESLA_DATA_DIR or the iCloud "Tesla" folder)
    --teslacam DIR   TeslaCam folder to read (default: the Archive drive, found by what is on it)
    --only STAMP     one drive folder, e.g. 2026-10-10_19-44_AEDT
    --since DATE     oldest drive to consider (default 2026-08-01)
    --force          redo drives that already have drive_gps.json
    --budget SEC     stop after this many seconds (re-run to carry on; newest drives first)

EXIT STATUS
    0 done, 1 data folder or footage not found
"""
import argparse
import csv
import datetime
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
import drive_report as DRV  # noqa: E402
import dashcam_series as DS  # noqa: E402
import drive_roles as DR  # noqa: E402

AP = DS.AP_NAMES
ON_ROUTE_M, ON_ROUTE_FRACTION, ON_ROUTE_MAX_M = 40.0, 0.90, 120.0


def hav(a, b, c, d):
    r = math.pi / 180
    p = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 12742000 * math.asin(math.sqrt(p))


def seg_dist(py, px, ay, ax, by, bx):
    """Metres from point P to segment AB (small areas: flat projection)."""
    k = math.cos(math.radians(py)) * 111320.0
    ax_, ay_, bx_, by_, px_, py_ = (ax - px) * k, (ay - py) * 110540.0, (bx - px) * k, (by - py) * 110540.0, 0.0, 0.0
    dx, dy = bx_ - ax_, by_ - ay_
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px_ - ax_) * dx + (py_ - ay_) * dy) / L))
    return math.hypot(ax_ + t * dx, ay_ + t * dy)


def dist_to_path(la, lo, t, path, window=180):
    """Distance from a Tessie point to the dashcam path, looking only at path points within +/- window seconds."""
    best = None
    prev = None
    for p in path:
        if abs((p["t"] - t).total_seconds()) > window:
            prev = None
            continue
        d = hav(la, lo, p["la"], p["lo"])
        best = d if best is None or d < best else best
        if prev is not None and prev["seg"] == p["seg"]:
            d2 = seg_dist(la, lo, prev["la"], prev["lo"], p["la"], p["lo"])
            best = d2 if d2 < best else best
        prev = p
    return best


def read_path(clips, off):
    """GPS fixes of the front clips: [{t (Tessie clock), seg, la, lo, hd, v, ap, clip}]; segments break where a clip is missing."""
    out, seg, last_end = [], 0, None
    for start, path in clips:
        try:
            S = DS.read_series(path)
        except Exception:
            continue
        n = S["n"]
        if not n:
            continue
        step = (S["dur"] or n / 36.0) / n
        if last_end is not None and (start - last_end).total_seconds() > 5:
            seg += 1
        last_end = start + datetime.timedelta(seconds=S["dur"] or 60)
        prev = None
        for i in range(n):
            la, lo = S["lat"][i], S["lon"][i]
            if not la or not lo or (la, lo) == prev:
                continue
            prev = (la, lo)
            t = start + datetime.timedelta(seconds=off + i * step)
            if out and out[-1]["seg"] == seg and (t - out[-1]["t"]).total_seconds() < 0.9:
                continue
            out.append(dict(t=t, seg=seg, la=la, lo=lo, hd=S["hd"][i], v=S["v"][i], ap=AP.get(S["ap"][i], str(S["ap"][i])), clip=os.path.basename(path)[:19],
                            br=S["br"][i], accel=S["accel"][i], steer=S["steer"][i], blk=S["blk"][i], gear=S["gear"][i]))
    return out


def judge(path, win, covered_s, drive_s, off, n_clips):
    """Compare with Tessie's own points inside the span the dashcam path covers."""
    j = dict(source="TeslaCam SEI GPS (front camera)", generated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), clock_offset_s=off, clips=n_clips,
             points=len(path), covered_s=round(covered_s), drive_s=round(drive_s), coverage_pct=round(100 * covered_s / drive_s) if drive_s else 0)
    if len(path) < 20:
        return dict(j, verdict="rejected", reason="too few GPS fixes in the clips")
    t0, t1 = path[0]["t"], path[-1]["t"]
    inside = [p for p in win if p["la"] is not None and t0 <= p["t"] <= t1]
    ds = []
    for p in inside:
        d = dist_to_path(p["la"], p["lo"], p["t"], path)
        if d is not None:
            ds.append(d)
    if len(ds) < 3:
        return dict(j, verdict="rejected", reason="fewer than 3 Tessie points inside the footage to compare with")
    ds.sort()
    good = sum(1 for d in ds if d <= ON_ROUTE_M) / len(ds)
    chord = []      # how far the middle of each Tessie chord is from the real path = how much Tessie's line cuts the corner
    for a, b in zip(inside, inside[1:]):
        if a["la"] is None or b["la"] is None or (b["t"] - a["t"]).total_seconds() > 90:
            continue
        m = dist_to_path((a["la"] + b["la"]) / 2, (a["lo"] + b["lo"]) / 2, a["t"] + (b["t"] - a["t"]) / 2, path, 60)
        if m is not None:
            chord.append(m)
    chord.sort()
    pct = lambda v, q: round(v[min(len(v) - 1, int(len(v) * q))], 1) if v else None
    j.update(tessie_points_checked=len(ds), on_route_median_m=pct(ds, .5), on_route_p90_m=pct(ds, .9), on_route_max_m=round(ds[-1], 1),
             corner_cut_median_m=pct(chord, .5), corner_cut_p90_m=pct(chord, .9))
    if good >= ON_ROUTE_FRACTION and ds[-1] <= ON_ROUTE_MAX_M:
        j.update(verdict="used", reason="Tessie's points lie on the dashcam path; the path adds %s m of corner detail (90th percentile)" % (pct(chord, .9) or 0))
    else:
        j.update(verdict="rejected", reason="Tessie's points are not on the dashcam path (%d%% within %d m, worst %d m): clip clock or GPS does not match"
                 % (round(100 * good), ON_ROUTE_M, ds[-1]))
    return j


GEAR = {0: "P", 1: "D", 2: "R", 3: "N"}
BLINK = {0: "", 1: "left", 2: "right", 3: "both"}
EXTRA = ["Source", "Heading (deg)", "Autopilot State", "Brake", "Accelerator (%)", "Steering Angle (deg)", "Blinker", "Camera Clip"]


def write_mirror(out_dir, src_csv, path, info):
    """Tessie's rows unchanged + a row per dashcam fix, same file name, same columns, extra columns on the right."""
    os.makedirs(out_dir, exist_ok=True)
    if info["verdict"] == "used":
        rows = list(csv.reader(open(src_csv, encoding="utf-8")))
        head = rows[0]
        ix = {n: i for i, n in enumerate(head)}
        tess = []
        for r in rows[1:]:
            try:
                tess.append((DRV.pdt(r[0]), 0, r + [""] * (len(head) - len(r)) + ["tessie"] + [""] * (len(EXTRA) - 1)))
            except Exception:
                pass
        seen = {t for t, _, _ in tess}
        new, prev_sec = [], None
        for p in path:
            sec = p["t"].replace(microsecond=0)
            if sec == prev_sec:
                continue
            prev_sec = sec
            r = [""] * len(head)
            r[0] = sec.strftime("%Y-%m-%d %H:%M:%S")
            for name, val in (("Latitude", "%.6f" % p["la"]), ("Longitude", "%.6f" % p["lo"]), ("Speed (km/h)", str(round(p["v"]))), ("Shift State", GEAR.get(p["gear"], ""))):
                if name in ix:
                    r[ix[name]] = val
            r += ["dashcam", round(p["hd"]), p["ap"], p["br"], round(p["accel"]), round(p["steer"], 1), BLINK.get(p["blk"], ""), p["clip"]]
            new.append((sec, 1, r))
        merged = sorted(tess + new, key=lambda x: (x[0], x[1]))
        with open(os.path.join(out_dir, os.path.basename(src_csv)), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(head + EXTRA)
            for _, _, r in merged:
                w.writerow(r)
        info["rows_tessie"], info["rows_dashcam"] = len(tess), len(new)
    with open(os.path.join(out_dir, "drive_gps.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=1)


def do_drive(folder, out_dir, master, foot, a):
    csvs = [n for n in os.listdir(folder) if n.startswith("drive_deepdive_") and n.endswith(".csv")]
    if not csvs:
        return "no csv"
    src_csv = os.path.join(folder, csvs[0])
    pts = DRV.load_drive_csv(src_csv)
    if not pts:
        return "empty csv"
    mv = next((p["t"] for p in pts if (p["v"] or 0) > 0), pts[0]["t"])
    mm = DRV.find_master(master, mv)
    win = DRV.drive_window(pts, (mm[0], mm[1]) if mm else None)
    if len(win) < 2:
        return "too short"
    t0, t1 = win[0]["t"], win[-1]["t"]
    off, n = foot.best_offset(t0, t1, win)
    clips = foot.in_window(t0, t1, off)
    if not clips:
        return "no footage"
    path = read_path(clips, off)
    drive_s = (t1 - t0).total_seconds()
    covered = sum(min(60.0, drive_s) for _ in clips)
    info = judge(path, win, min(covered, drive_s), drive_s, off, len(clips))
    write_mirror(out_dir, src_csv, path, info)
    return "%s - %d fixes, %s%% of the drive, %s" % (info["verdict"], len(path), info["coverage_pct"], info["reason"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=DRV.default_data())
    ap.add_argument("--teslacam", action="append", default=None)
    ap.add_argument("--only", default="")
    ap.add_argument("--since", default="2026-08-01")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--budget", type=float, default=0)
    a = ap.parse_args()
    data = os.path.abspath(os.path.expanduser(a.data_dir))
    root = os.path.join(data, "drives", "deepdive")
    if not os.path.isdir(root):
        print("Data folder not found: %s" % root)
        return 1
    roots = [os.path.expanduser(c) for c in (a.teslacam or DR.teslacam_roots(False)) if os.path.isdir(os.path.expanduser(c))]
    if not roots:
        print("No TeslaCam folder found (is the Archive drive connected?)")
        return 1
    master = DRV.load_master(data)
    foot = DRV.Footage(roots, a.since)
    t_end = time.time() + a.budget if a.budget else 1e18
    names = sorted((n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n)) and n[:10] >= a.since and (not a.only or n == a.only)), reverse=True)
    done = skipped = 0
    for n in names:
        folder = os.path.join(root, n)
        out_dir = os.path.join(data, "drives", "deepdive_gps", n)
        if os.path.exists(os.path.join(out_dir, "drive_gps.json")) and not a.force:
            skipped += 1
            continue
        if time.time() > t_end:
            print("Budget reached; run again to carry on.")
            break
        msg = do_drive(folder, out_dir, master, foot, a)
        if msg not in ("no footage", "no csv", "empty csv", "too short"):
            done += 1
            print("  %s: %s" % (n, msg), flush=True)
    print("%d drive(s) done, %d already had a path." % (done, skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
