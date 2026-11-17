#!/usr/bin/env python3
"""sign_check_drive.py - checks the speed limit signs read on a drive against the posted limits in OpenStreetMap, and keeps the answers as training data for the sign reader.

NAME
    sign_check_drive.py - correlate a drive's sign readings with map speed limits, write labelled faces for train_sign_reader.py

SYNOPSIS
    ./Tools/maintenance/sign_check_drive.py --day 2026-11-08 [--from 10:56 --to 11:12] [--data-dir DIR] [--no-fetch] [--max-m 25]

DESCRIPTION
    For every sign the app kept a picture of (<data>/drive_review/<day>.json, best frame of each sign) in the time window, finds where the car was (the drive's telemetry,
    drives/deepdive/<day>/*/drive_deepdive_*.json), asks OpenStreetMap for the ways with a `maxspeed` tag round the drive (every road class: one Overpass query, kept in
    <data>/roads/maxspeed_<day>.json; --no-fetch uses only that) and compares the reading with the limit of the nearest way within --max-m metres.

    Verdicts: agree (same number), near (within 10 km/h), differ, nomap (no tagged way near: most suburban streets in OSM carry no maxspeed, so this says nothing).
    It also runs the red-ring gate (lib/sign_finder.ring_check) on each picture.

    A face is written to the training set (<data>/sign_training/real_faces.npz, the format of train_sign_reader.py --real: g 32 x 32 grey, t text or "none", d day, c 100):
      - a reading that agrees with the map and passes the ring gate: a positive, labelled with its number;
      - a reading that differs from the map by 30 km/h or more and fails the ring gate: a hard negative, labelled "none";
      - the answers given on the sign cards (<data>/sign_training/feedback.json, lib/sign_feedback.py) and anything in <data>/sign_training/overrides.json ({"<stamp>|<second>|<text>": "60" | "none"}), which is the person's own answer and wins.
    Everything else is left out: it is not known which is right.
    The report goes to <data>/drive_review/signcheck/<day>_<from>.csv and the counts are printed. Re-run train_sign_reader.py with
    --real <data>/sign_training/real_faces.npz to retrain (docs/SIGNS.md).

    Overpass is reached with curl, from the Mac (the cloud workspace cannot reach it).
"""
import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))


def _limit(v):
    try:
        return int(str(v).split()[0])
    except (ValueError, IndexError):
        return None


def _m(lat, lon, la2, lo2):
    return math.hypot((lat - la2) * 111320.0, (lon - lo2) * 111320.0 * math.cos(math.radians(lat)))


def _seg_m(lat, lon, a, b):
    k = math.cos(math.radians(lat))
    ax, ay, bx, by, px, py = a[1] * k, a[0], b[1] * k, b[0], lon * k, lat
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy)) * 111320.0


def fetch_ways(pts, path, fetch=True):
    """Ways with a maxspeed tag in the box round the points: [{limit, name, hw, geom}]. Kept in `path`."""
    if not os.path.exists(path) and fetch:
        s, w = min(p[1] for p in pts) - 0.002, min(p[2] for p in pts) - 0.002
        n, e = max(p[1] for p in pts) + 0.002, max(p[2] for p in pts) + 0.002
        q = '[out:json][timeout:60];way["highway"]["maxspeed"](%.5f,%.5f,%.5f,%.5f);out tags geom;' % (s, w, n, e)
        for ep in ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"):
            r = subprocess.run(["curl", "-s", "--max-time", "90", "-A", "tesellate-sign-check", "--data-urlencode", "data=" + q, ep], capture_output=True, text=True)
            try:
                d = json.loads(r.stdout)
            except ValueError:
                continue
            if "elements" in d:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as fh:
                    json.dump(d, fh)
                break
    try:
        with open(path) as fh:
            els = json.load(fh)["elements"]
    except (OSError, ValueError, KeyError):
        return None
    out = []
    for el in els:
        t = el.get("tags", {})
        g = [(p["lat"], p["lon"]) for p in el.get("geometry", [])]
        lim = _limit(t.get("maxspeed"))
        if lim and len(g) >= 2:
            out.append(dict(limit=lim, name=t.get("name", ""), hw=t.get("highway", ""), geom=g))
    return out


def map_limit(ways, lat, lon, max_m):
    best = None
    for w in ways:
        for a, b in zip(w["geom"], w["geom"][1:]):
            d = _seg_m(lat, lon, a, b)
            if d <= max_m and (best is None or d < best[0]):
                best = (d, w)
    return (best[1]["limit"], best[1]["name"], best[1]["hw"], round(best[0])) if best else (None, "", "", None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--from", dest="t0", default="00:00")
    ap.add_argument("--to", dest="t1", default="23:59")
    ap.add_argument("--data-dir", default=os.path.expanduser("~/Tesla"))
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--max-m", type=float, default=25.0)
    a = ap.parse_args()
    import cv2
    import numpy as np
    import app_route
    import clip_clock as CC
    import sign_finder as SF
    import sign_reader as SR
    D = a.data_dir
    rv = json.load(open(os.path.join(D, "drive_review", a.day + ".json")))
    pts = []
    root = os.path.join(D, "drives", "deepdive", a.day)
    for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        for f in os.listdir(os.path.join(root, d)):
            if f.startswith("drive_deepdive_") and f.endswith(".json"):
                pts += app_route.points(os.path.join(root, d, f))
    pts.sort()
    if not pts:
        sys.exit("no telemetry for " + a.day)
    ways = fetch_ways(pts, os.path.join(D, "roads", "maxspeed_%s.json" % a.day), not a.no_fetch)
    if ways is None:
        sys.exit("no map limits (Overpass not reached, and none kept): run from the Mac without --no-fetch")
    over_p = os.path.join(D, "sign_training", "overrides.json")
    try:
        over = json.load(open(over_p))
    except (OSError, ValueError):
        over = {}
    fbs = {}
    try:
        for k, a in (json.load(open(os.path.join(D, "sign_training", "feedback.json"))) or {}).items():
            fbs.setdefault(k.split("|")[0], []).append(a)
    except (OSError, ValueError):
        pass
    rows, faces = [], []
    for stamp, rec in sorted(rv.items()):
        hm = stamp[11:16].replace("-", ":")
        if not a.t0 <= hm <= a.t1:
            continue
        for x in rec.get("d", []):
            if x.get("kind") == "school" or not x.get("text"):
                continue
            ep = CC.utc(stamp) + x["s"]
            p = min(pts, key=lambda q: abs(q[0] - ep))
            if abs(p[0] - ep) > 20:
                continue
            lim, name, hw, dist = map_limit(ways, p[1], p[2], a.max_m)
            rd = int(x["text"])
            verdict = "nomap" if lim is None else "agree" if lim == rd else "near" if abs(lim - rd) <= 10 else "differ"
            ring = None
            face = None
            if x.get("f"):
                im = cv2.imread(os.path.join(D, "drive_review", "crops", x["f"]))
                if im is not None:
                    sx = im.shape[1] / float(x.get("w") or im.shape[1])
                    cx, cy, r = x["x"] * sx, x["y"] * sx, x["r"] * sx
                    ring = SF.ring_check(im, cx, cy, r)
                    ri = int(r * 0.74)
                    fc = im[max(0, int(cy - ri)):int(cy + ri), max(0, int(cx - ri)):int(cx + ri)]
                    if fc.size:
                        face = cv2.cvtColor(cv2.resize(fc, (32, 32), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            key = "%s|%s|%s" % (stamp, x["s"], x["text"])
            label = over.get(key)
            if label is None:       # the person's answer about the whole sign (sign_feedback.py: s0..s1 are the seconds its stills span), not just its first still
                for a in fbs.get(stamp, ()):
                    if a.get("text") == x["text"] and a.get("s0") is not None and a["s0"] <= x["s"] <= (a.get("s1") if a.get("s1") is not None else a["s0"]):
                        label = "none" if a.get("reason") == "none" else str(a["speed"]) if a.get("reason") == "speed" and a.get("speed") else None
                        break
            if label is None and ring is not None:
                if verdict == "agree" and ring["ok"]:
                    label = x["text"]
                elif verdict == "differ" and abs(lim - rd) >= 30 and not ring["ok"]:
                    label = "none"
            if label is not None and face is not None:
                faces.append((face, label))
            rows.append(dict(stamp=stamp, s=x["s"], read=x["text"], conf=x.get("c"), side=x.get("side"), map=lim, road=name, hw=hw, off_m=dist, car_kmh=round(p[3]),
                             verdict=verdict, ring=None if ring is None else ring["cover"], ring_ok=None if ring is None else ring["ok"], label=label or "", picture=x.get("f", "")))
    out = os.path.join(D, "drive_review", "signcheck")
    os.makedirs(out, exist_ok=True)
    fn = os.path.join(out, "%s_%s.csv" % (a.day, a.t0.replace(":", "")))
    with open(fn, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["stamp"])
        w.writeheader()
        w.writerows(rows)
    cnt = {}
    for r in rows:
        cnt[(r["verdict"], r["ring_ok"])] = cnt.get((r["verdict"], r["ring_ok"]), 0) + 1
    print("%d readings; verdict / ring gate:" % len(rows))
    for k, n in sorted(cnt.items(), key=lambda kv: str(kv[0])):
        print("  %-6s ring_ok=%-5s %d" % (k[0], k[1], n))
    print("report:", fn)
    if faces:
        tp = os.path.join(D, "sign_training", "real_faces.npz")
        g, t, d, c = [], [], [], []
        if os.path.exists(tp):
            z = np.load(tp, allow_pickle=False)
            g, t, d, c = list(z["g"]), list(z["t"]), list(z["d"]), list(z["c"])
        dayi = int(hashlib.md5(a.day.encode()).hexdigest()[:6], 16) % 1000
        seen = {(tuple(f.reshape(-1)[:64]), l) for f, l in zip(g, t)}
        added = 0
        for f, l in faces:
            if (tuple(f.reshape(-1)[:64]), l) in seen:
                continue
            g.append(f); t.append(l); d.append(dayi); c.append(100); added += 1
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        np.savez_compressed(tp, g=np.array(g, np.uint8), t=np.array(t), d=np.array(d), c=np.array(c, np.float32))
        print("training faces: +%d (now %d) in %s" % (added, len(g), tp))
    else:
        print("no labelled faces from this window")


if __name__ == "__main__":
    main()
