#!/usr/bin/env python3
"""Find green location signs in the dashcam_flip_review contact sheets and write them as marks.

NAME
    sign_auto.py - automatic sign marks (green location boards) for the dashcam flip review page

SYNOPSIS
    sign_auto.py [--data-dir DIR] [--budget SECONDS] [--force]

DESCRIPTION
    Reads each frames_1s/E###.jpg sheet (the stills are already extracted), runs lib/sign_finder.find_green_signs on
    every still and joins neighbouring hits into marks {k, t0, t1, text}. Output is signs/auto_marks.json, which
    dashcam_flip_review.py embeds (the by-eye marks in sign_reads.json are separate and are never overwritten).
    Finished events are skipped, so re-run until it says 0 left.

OPTIONS
    --data-dir DIR   Tesla data folder (default TESLA_DATA_DIR or the iCloud Tesla folder)
    --budget SEC     stop after SEC seconds (use ~80 over the device bridge)
    --force          redo events already done
"""
import argparse
import json
import os
import sys
import time

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
import sign_finder  # noqa: E402

COLS, ROWS = 5, 6
FIRST_REL = -12        # the first still is 12 s before the speed starts to change


def marks_from_hits(hits):
    """hits: [(rel, width_px)] -> marks. A single still counts only if the board is big (close) - otherwise it needs a neighbour."""
    hits = sorted(hits)
    groups, cur = [], []
    for h in hits:
        if cur and h[0] - cur[-1][0] > 2:
            groups.append(cur)
            cur = []
        cur.append(h)
    if cur:
        groups.append(cur)
    out = []
    for g in groups:
        if len(g) < 2 and g[0][1] < 70:
            continue
        out.append(dict(k="green", t0=g[0][0] - 0.5, t1=g[-1][0] + 0.5, text="green location sign"))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=os.path.expanduser(os.environ.get("TESLA_DATA_DIR", "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla")))
    ap.add_argument("--budget", type=float, default=1e9)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    base = os.path.join(os.path.expanduser(a.data_dir), "drives", "analysis", "dashcam_flip_review")
    out_p = os.path.join(base, "signs", "auto_marks.json")
    os.makedirs(os.path.dirname(out_p), exist_ok=True)
    res = json.load(open(out_p)) if os.path.exists(out_p) and not a.force else {}
    t0 = time.time()
    sheets = sorted(f for f in os.listdir(os.path.join(base, "frames_1s")) if f.endswith(".jpg"))
    left = 0
    for f in sheets:
        eid = f[:-4]
        if eid in res:
            continue
        if time.time() - t0 > a.budget:
            left += 1
            continue
        im = cv2.imread(os.path.join(base, "frames_1s", f))
        if im is None:
            continue
        H, W = im.shape[:2]
        cw, ch = W // COLS, H // ROWS
        hits = []
        for i in range(COLS * ROWS):
            r, c = divmod(i, COLS)
            cell = im[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw]
            if cell.mean() < 12:        # empty cell (past the last still)
                continue
            g = sign_finder.find_green_signs(cell)
            if g:
                hits.append((FIRST_REL + i, max(d["box"][2] for d in g)))
        res[eid] = marks_from_hits(hits)
    json.dump(res, open(out_p + ".tmp", "w"))
    os.replace(out_p + ".tmp", out_p)
    print("%d events done, %d left" % (len(res), left))
    return 0


if __name__ == "__main__":
    sys.exit(main())
