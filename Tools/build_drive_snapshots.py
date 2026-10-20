#!/usr/bin/env python3
"""build_drive_snapshots.py - keep a still for every event of each drive, in that drive's own folder.

NAME
    build_drive_snapshots.py - write snapshots/ (one still per event) into the drive folders

SYNOPSIS
    build_drive_snapshots.py [--data-dir DIR] [--since YYYY-MM-DD] [--only STAMP[,STAMP]] [--force] [--budget SECONDS]

DESCRIPTION
    Each drive folder gets a snapshots/ folder: a front-camera still every 30 seconds along the whole drive (timeline/), and a still
    every second around every event worth analysing (events/<id>/), large (1000 px wide, cut from the full-size video
    while it is on the Archive, so signs can be read; the small kept still once the video is gone): FSD handed back, accelerator pressed while FSD was on, a
    speed change to or from 40, 50, 100 or 110 km/h (school zones, 100 to 110) and every saved clip; other events get one still. snapshots/index.json says what
    each one is, with its UTC time and the local time with its zone. The period and the kinds of event that get a still every
    second are settings (snapshots.every_s, snapshots.burst in config.json). The stills are the kept ones (make_teslacam_stills); where a
    clip has none yet and its video is on the Archive, they are made from the video and kept. Once the video is deleted the kept
    stills are what is left. A drive with no dashcam telemetry on its route gets only its saved clips. drive.html shows them.

    Only the Archive is read for video. A drive whose snapshots are newer than its summary is skipped. Nothing on a drive is changed.

OPTIONS
    --data-dir DIR       Tesla data folder (default: config.json)
    --since YYYY-MM-DD   only drives starting on or after this date
    --only STAMP[,..]    these drives, e.g. 2026-10-11_14-40_AEST
    --force              redo drives that already have snapshots
    --budget SECONDS     stop starting new drives after this long (run again to continue)
    -h, --help           this text

EXIT STATUS
    0 done, 1 no data folder.

EXAMPLES
    ./build_drive_snapshots.py --only 2026-10-11_14-40_AEST
    ./build_drive_snapshots.py --since 2026-08-14 --budget 120
"""
import argparse
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
import app_config  # noqa: E402
import app_teslacam  # noqa: E402
import drive_roles as DR  # noqa: E402
import drive_snapshots as SN  # noqa: E402
import teslacam_stills as ST  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False, usage=argparse.SUPPRESS)
    ap.add_argument("-h", "--help", action="help", help=argparse.SUPPRESS)
    ap.add_argument("--data-dir")
    ap.add_argument("--since")
    ap.add_argument("--only")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--budget", type=float, default=1e9)
    a = ap.parse_args()
    if a.data_dir:
        data = os.path.abspath(os.path.expanduser(a.data_dir))
    else:
        from tessie_api_common import resolve_tessie_data_home
        data = resolve_tessie_data_home()
    if not data:
        print("No data folder.")
        return 1
    found = {r: p for p, r in DR.discover()}
    teslacam = found.get("A")
    tc = app_teslacam.TeslaCam(data, None)
    front = SN.front_clips(tc, teslacam) if teslacam and ST.have_ffmpeg() else {}
    stills = ST.Stills(data, app_config.get("stills.width", 400), app_config.get("stills.quality", 6))
    if not teslacam:
        print("The Archive is not connected: only the kept stills are used.")
    only = set(a.only.split(",")) if a.only else None
    t_start, done = time.time(), 0
    for sj in sorted(glob.glob(os.path.join(data, "drives", "deepdive", "*", "*", "drive_summary.json")), reverse=True):
        folder = os.path.dirname(sj)
        stamp = os.path.basename(folder)
        if only and stamp not in only:
            continue
        if a.since and stamp[:10] < a.since:
            continue
        idx = os.path.join(folder, "snapshots", "index.json")
        if not a.force and os.path.exists(idx) and os.path.getmtime(idx) > os.path.getmtime(sj):
            continue
        if time.time() - t_start > a.budget:
            print("budget reached - run again to continue")
            break
        try:
            r = SN.build(data, stamp, tc, front, stills, stop=lambda: time.time() - t_start > a.budget)
        except Exception as e:                                  # one bad drive must not stop the rest
            print("  %s: %s: %s" % (stamp, type(e).__name__, e))
            continue
        done += 1 if r.get("done", True) else 0
        print("  %s: %d snapshot(s), %d event(s)%s" % (stamp, r["n"], r["events"], " - " + r["note"] if r["note"] else ""))
    print("%d drive(s) done" % done)
    return 0


if __name__ == "__main__":
    sys.exit(main())
