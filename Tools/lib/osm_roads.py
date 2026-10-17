"""osm_roads.py - what OpenStreetMap says the speed limit is at a point on a road (and which way the car faces).

  r = Roads(cache_dir, fetch=True)
  r.at(lat, lon, heading_deg) -> dict(limit, name, ref, hw, var, cond) | None

The roads are the major highways (motorway, trunk, primary, secondary and their links) in 0.05 degree tiles, kept as hw05_<i>_<j>.json in
cache_dir: the same files analyse_fsd_osm_limits.py writes and reads, so a tile fetched by either is used by both. A tile that is not there is fetched
from Overpass when `fetch` is on (one short request, a few seconds) and an unreachable server just leaves the point without an answer.
`limit` is the maxspeed tag for the direction of travel in km/h (None when the way has none). Used by app_drive_review.py to set a speed sign against the map.
"""
import collections
import json
import math
import os
import re
import subprocess
import threading
import time

TILE = 0.05
MAX_DIST_M = 30.0
HIGHWAYS = "motorway|trunk|primary|secondary"
SC = 0.002
UA = "tesla-fsd-speed-analysis/1.0 (personal research)"
MIRRORS = ["https://overpass-api.de/api/interpreter", "https://lz4.overpass-api.de/api/interpreter"]


def _limit(v):
    m = re.match(r"^\s*(\d+)\s*(mph)?\s*$", v or "")
    if not m:
        return None
    n = int(m.group(1))
    return round(n * 1.609) if m.group(2) else n


def _bearing(a, b, c, d):
    p1, p2 = math.radians(a), math.radians(c)
    dl = math.radians(d - b)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _seg_dist_m(lat, lon, a, b):
    kx = 111320.0 * math.cos(math.radians(lat))
    ky = 110540.0
    ax, ay = (a[1] - lon) * kx, (a[0] - lat) * ky
    bx, by = (b[1] - lon) * kx, (b[0] - lat) * ky
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    u = 0.0 if l2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
    return math.hypot(ax + u * dx, ay + u * dy)


class _Way:
    def __init__(self, el):
        t = el.get("tags", {})
        self.id = el["id"]
        self.pts = [(g["lat"], g["lon"]) for g in el.get("geometry", [])]
        self.hw = t.get("highway", "")
        ow = t.get("oneway")
        self.ow = 1 if ow in ("yes", "true", "1") else -1 if ow == "-1" else 0 if ow == "no" else (1 if self.hw in ("motorway", "motorway_link") and t.get("junction") != "roundabout" else 0)
        base = _limit(t.get("maxspeed"))
        self.fwd = _limit(t.get("maxspeed:forward")) or base
        self.bwd = _limit(t.get("maxspeed:backward")) or base
        self.name, self.ref = t.get("name", ""), t.get("ref", "")
        self.var = "variable" in t.get("maxspeed", "") or "signals" in t.get("maxspeed", "") or any(k.startswith("maxspeed:variable") for k in t)
        self.cond = t.get("maxspeed:conditional", "")


class Roads:
    def __init__(self, cache_dir, fetch=True, timeout=20):
        self.dir = cache_dir
        self.fetch = fetch
        self.timeout = timeout
        self.idx = collections.defaultdict(list)
        self.tiles = {}                 # (ti, tj) -> loaded?
        self.lock = threading.Lock()

    def _path(self, ti, tj):
        own = os.path.join(self.dir, "hw05_%d_%d.json" % (ti, tj))
        old = os.path.join(self.dir, "hw_%d_%d.json" % (ti // 2, tj // 2))
        return old if (not os.path.exists(own) and os.path.exists(old)) else own

    def _get(self, ti, tj):
        p = self._path(ti, tj)
        if not os.path.exists(p) and self.fetch:
            s, w, n, e = ti * TILE, tj * TILE, (ti + 1) * TILE, (tj + 1) * TILE
            q = ('[out:json][timeout:30];way["highway"~"^(%s)(_link)?$"](%.4f,%.4f,%.4f,%.4f);out tags geom;'
                 % (HIGHWAYS, s - 0.003, w - 0.003, n + 0.003, e + 0.003))
            for ep in MIRRORS:
                try:
                    r = subprocess.run(["curl", "-s", "--connect-timeout", "5", "--max-time", str(self.timeout), "-A", UA, "-H", "Accept: application/json",
                                        "--data-urlencode", "data=" + q, ep], capture_output=True, text=True, timeout=self.timeout + 5)
                    d = json.loads(r.stdout)
                    if "elements" in d:
                        os.makedirs(self.dir, exist_ok=True)
                        with open(p + ".tmp", "w") as fh:
                            json.dump({"tile": [ti, tj], "fetched": time.strftime("%Y-%m-%d"), "elements": d["elements"]}, fh)
                        os.replace(p + ".tmp", p)
                        break
                except Exception:
                    continue
        try:
            with open(p) as fh:
                els = json.load(fh)["elements"]
        except (OSError, ValueError, KeyError):
            return False
        seen = set()
        for el in els:
            if el.get("type") != "way" or len(el.get("geometry", [])) < 2 or el["id"] in seen:
                continue
            seen.add(el["id"])
            w = _Way(el)
            for k in range(len(w.pts) - 1):
                a, b = w.pts[k], w.pts[k + 1]
                for ci in range(int(math.floor(min(a[0], b[0]) / SC)), int(math.floor(max(a[0], b[0]) / SC)) + 1):
                    for cj in range(int(math.floor(min(a[1], b[1]) / SC)), int(math.floor(max(a[1], b[1]) / SC)) + 1):
                        self.idx[(ci, cj)].append((w, k))
        return True

    def at(self, lat, lon, brg):
        """The best matching way within MAX_DIST_M going the same way (or against it, for a two-way road) -> dict | None."""
        if lat is None or lon is None or brg is None:
            return None
        ti, tj = int(math.floor(lat / TILE)), int(math.floor(lon / TILE))
        with self.lock:
            if (ti, tj) not in self.tiles:
                self.tiles[(ti, tj)] = self._get(ti, tj)
            ci, cj = int(math.floor(lat / SC)), int(math.floor(lon / SC))
            best, seen = None, set()
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for w, k in self.idx.get((ci + di, cj + dj), ()):
                        if (w.id, k) in seen:
                            continue
                        seen.add((w.id, k))
                        a, b = w.pts[k], w.pts[k + 1]
                        d = _seg_dist_m(lat, lon, a, b)
                        if d > MAX_DIST_M:
                            continue
                        diff = abs((_bearing(a[0], a[1], b[0], b[1]) - brg + 180) % 360 - 180)
                        fwd, bwd = diff < 60, diff > 120
                        if not (fwd or bwd) or (w.ow == 1 and not fwd) or (w.ow == -1 and not bwd):
                            continue
                        score = d + (0 if w.hw in ("motorway", "trunk", "primary") else 6)
                        if best is None or score < best[0]:
                            best = (score, w, fwd)
        if best is None:
            return None
        w = best[1]
        return dict(limit=w.fwd if best[2] else w.bwd, name=w.name, ref=w.ref, hw=w.hw, var=w.var, cond=w.cond)
