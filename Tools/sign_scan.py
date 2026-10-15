#!/usr/bin/env python3
"""Look for round speed-limit signs in the dashcam footage around each flip (REQ-192).

For every event in dashcam_flip_review/cache3 this decodes full-resolution front-camera frames from 14 s before
to 4 s after the speed change (every 0.5 s), finds round red-ringed signs (lib/sign_finder.py) and writes
    <data>/drives/analysis/dashcam_flip_review/signs/E###.json   (every candidate: time, position, size, OCR text)
    <data>/drives/analysis/dashcam_flip_review/signs/E###.jpg    (a strip of the best candidate crops, nearest-first)
OCR on motion-blurred signs is unreliable, so the strips are meant to be read by eye (or by Claude); the page shows them.
Usage: sign_scan.py [--data-dir DIR] [--teslacam DIR] [--budget SECONDS]
"""
import argparse, bisect, datetime, glob, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
import cv2
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
sys.path.insert(0, HERE)
import dashcam_flip_review as D  # noqa: E402
import sign_finder  # noqa: E402

REL = [x * 0.5 for x in range(-28, 27)]    # -14 s .. +13 s around the speed change


def grab(path, ts):
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.2f" % ts, "-i", path, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "1", "-"],
                       capture_output=True, stdin=subprocess.DEVNULL)
    return cv2.imdecode(np.frombuffer(p.stdout, np.uint8), 1) if p.stdout else None


def scan_event(rec, clips, starts, outdir, hough=False):
    off = rec["clip_offset_h"] * 3600
    # onset_local is cut to whole seconds; add the cut-off part back so strip times match the contact-sheet stills
    onset = (datetime.datetime.strptime(rec["onset_local"], "%Y-%m-%d %H:%M:%S") + datetime.timedelta(seconds=rec.get("onset_frac", 0))
             - datetime.timedelta(seconds=off))

    def one(rel):
        T = onset + datetime.timedelta(seconds=rel)
        i = bisect.bisect_right(starts, T) - 1
        if i < 0:
            return rel, None
        ts = (T - starts[i]).total_seconds()
        if ts > 61:
            return rel, None
        im = grab(clips[i][1], ts)
        if im is None:
            return rel, None
        found = sign_finder.find_signs(im, ocr=False)                      # round limit signs, left of the road (LHT)
        for h in (sign_finder.find_signs_hough(im) if hough else []):      # low-light fallback
            if not any(abs(h["x"] - f["x"]) < 40 and abs(h["y"] - f["y"]) < 40 for f in found):
                found.append(h)
        for k in found:
            k.setdefault("kind", "ring")
        found += sign_finder.find_signs_right(im)                         # secondary: round signs on the right
        found += sign_finder.find_blue_panels(im)                          # "CHECK YOUR SPEED" style panels
        found += sign_finder.find_white_signs(im)                          # white / pale boards (distance, information)
        found += sign_finder.find_road_marks(im)                           # painted numerals on the road
        return rel, found

    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(one, REL))
    cands = []
    for rel, ss in res:
        for s in (ss or []):
            cands.append(dict(rel=rel, x=s["x"], y=s["y"], r=s["r"], text=s["text"], conf=s["conf"], ok=s["ok"], red=s.get("red", 0), ringness=s.get("ringness", 0.0), crop=s["crop"],
                              kind=s.get("kind", "ring"), score=s.get("score", 0.0)))
    # best candidates: biggest rings first, one per time step and not the same sign twice in a row
    cands.sort(key=lambda c: -((1.0 + 3.0 * c["ringness"]) * min(c["r"], 45) * max(c["red"], 1.0)))
    pick = []
    for c in cands:
        if c["kind"] != "ring" or c["r"] > 135:
            continue
        if any(abs(c["rel"] - p["rel"]) < 1.0 and abs(c["x"] - p["x"]) < 400 for p in pick):
            continue
        pick.append(c)
        if len(pick) == 6:
            break
    for kind, n in (("side", 2), ("panel", 2), ("white", 2), ("road", 2)):       # blue information panels and painted numerals: best few, spread in time
        got = []
        for c in sorted([c for c in cands if c["kind"] == kind], key=lambda c: -c["score"]):
            if all(abs(c["rel"] - g["rel"]) >= 1.5 for g in got):
                got.append(c)
            if len(got) == n:
                break
        pick += got
    pick.sort(key=lambda c: c["rel"])
    tiles = []
    for c in pick:
        t = cv2.resize(c["crop"], (240, 240), interpolation=cv2.INTER_AREA)
        tag = {"panel": "P ", "road": "R ", "side": "S ", "white": "W "}.get(c["kind"], "")
        cv2.putText(t, tag + "%+.1fs" % c["rel"], (6, 232), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 3, cv2.LINE_AA)
        cv2.putText(t, tag + "%+.1fs" % c["rel"], (6, 232), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 1, cv2.LINE_AA)
        tiles.append(t)
    jpg = None
    if tiles:
        cv2.imwrite(os.path.join(outdir, rec["id"] + ".jpg"), np.hstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 84])
        jpg = rec["id"] + ".jpg"
    meta = [{k: v for k, v in c.items() if k != "crop"} for c in cands]
    json.dump(dict(id=rec["id"], n=len(cands), shown=[dict(rel=c["rel"], r=c["r"], text=c["text"], kind=c["kind"]) for c in pick], strip=jpg, cands=meta),
              open(os.path.join(outdir, rec["id"] + ".json"), "w"), default=lambda o: o.item() if hasattr(o, "item") else str(o))
    return len(cands), len(pick)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=D.DEFAULT_DATA)
    ap.add_argument("--teslacam", default=None)
    ap.add_argument("--budget", type=float, default=95)
    ap.add_argument("--ids", default="", help="comma list of event ids to (re)scan even if done")
    ap.add_argument("--hough", action="store_true", help="also use the low-light circle detector")
    a = ap.parse_args()
    t0 = time.time()
    base = os.path.join(a.data_dir, "drives", "analysis", "dashcam_flip_review")
    root = a.teslacam or next((os.path.expanduser(c) for c in D.TESLACAM_CANDIDATES if os.path.isdir(os.path.expanduser(c))), None)
    clips = D.find_clips(root, "2026-10-01")
    starts = [c[0] for c in clips]
    outdir = os.path.join(base, "signs")
    os.makedirs(outdir, exist_ok=True)
    todo = 0
    for f in sorted(glob.glob(os.path.join(base, "cache7", "E*.json"))):
        rec = json.load(open(f))
        if "clip" not in rec:
            continue
        want = [x for x in a.ids.split(",") if x]
        if want and rec["id"] not in want:
            continue
        if not want and os.path.exists(os.path.join(outdir, rec["id"] + ".json")):
            continue
        if time.time() - t0 > a.budget:
            todo += 1
            continue
        n, k = scan_event(rec, clips, starts, outdir, a.hough)
        print(rec["id"], "candidates", n, "shown", k, "%.0fs" % (time.time() - t0), flush=True)
    print("remaining", todo)


if __name__ == "__main__":
    main()
