#!/usr/bin/env python3
"""frac_fill.py - add onset_frac (the sub-second part cut off onset_local) to existing cache7 event records.
Usage: frac_fill.py [--data-dir DIR] [--budget S]   (re-run until it says 0 left)"""
import argparse, csv, json, os, sys, time, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dashcam_flip_review as D
ap = argparse.ArgumentParser(); ap.add_argument("--data-dir", default=D.DEFAULT_DATA); ap.add_argument("--budget", type=float, default=85)
a = ap.parse_args(); t0 = time.time()
base = os.path.join(a.data_dir, "drives", "analysis")
cdir = os.path.join(base, "dashcam_flip_review", "cache7")
roots = [os.path.expanduser(c) for c in D.TESLACAM_CANDIDATES if os.path.isdir(os.path.expanduser(c))]
clips = D.find_clips(roots, "2026-10-01"); starts = [c[0] for c in clips]
flips = list(csv.DictReader(open(os.path.join(base, "fsd_transitions.csv"))))
cache = D.Cache(); left = 0
for n, fl in enumerate(flips):
    eid = "E%03d" % (n + 1); cp = os.path.join(cdir, eid + ".json")
    if not os.path.exists(cp): continue
    rec = json.load(open(cp))
    if "clip" not in rec or "onset_frac" in rec: continue
    if time.time() - t0 > a.budget: left += 1; continue
    m = D.match_flip(fl, clips, starts, cache)
    if not m: continue
    _d, off, ci = m
    tl = D.Timeline(clips, starts, ci, cache); an = D.analyse(tl, fl)
    lo = clips[ci][0] + datetime.timedelta(seconds=an["onset"] + off)
    chk = lo.strftime("%Y-%m-%d %H:%M:%S") == rec["onset_local"]
    rec["onset_frac"] = round(lo.microsecond / 1e6, 2)
    json.dump(rec, open(cp, "w"))
    print(eid, rec["onset_frac"], "ok" if chk else "MISMATCH " + lo.strftime("%H:%M:%S") + " vs " + rec["onset_local"], flush=True)
    if len(cache.d) > 12: cache.d.clear()
print(left, "left")
