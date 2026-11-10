"""suburb_lookup.py - the suburb a GPS position is in: offline once seen, instant, from OpenStreetMap's suburb boundaries.

    S = Suburbs(cache_dir)
    S.at(lat, lon)                -> "Drummoyne" | None      the suburb whose boundary holds the point (None: not known yet, or in no suburb)
    S.at(lat, lon, wait=True)     as above, but asks OpenStreetMap at once when no kept boundary holds the point (a script, not a page)
    S.many([(lat, lon), ...])     -> [name | None, ...]       one answer per point (wait=True)
    S.prefetch([(lat, lon), ...]) queues the points no kept boundary holds, to be fetched one at a time in the background

How it works. One suburb at a time, not an area: the first time a point is in no kept boundary, one small Overpass request (`is_in` the point, level 9 and 10 boundaries) brings back the
suburb that holds it; its rings are stitched, simplified to about 2 m and kept as rel_<id>.json in cache_dir. Driving through a suburb never asks again, and every position in it is answered
from memory: a lookup is a grid cell (0.02 degree) of the kept boundaries, a bounding box test on the few in it and a point-in-polygon test on the one or two that survive, and the answer for a
point (to 11 m) is remembered. A point in no suburb (water, a road on a border) is kept in none.json so it is not asked again. A server that does not answer is left alone for `retry_s` seconds;
requests go out one at a time from one background thread (Overpass is shared and slow: 1-40 s), so a drive across ten suburbs is ten requests over a minute or two, once, ever.

Where suburbs overlap (a locality inside a bigger one) the smallest wins. The state is not here: au_state.state(lat, lon) gives it from the position.
"""
import json
import math
import os
import queue
import subprocess
import threading
import time

LEVELS = "9|10"                 # OSM admin_level of Australian suburbs / localities (NSW, VIC, QLD: 9; some others 10)
SIMPLIFY = 0.00002              # degrees (about 2 m)
CELL = 0.02                     # degrees: the grid the kept boundaries are indexed by
MEMO_MAX = 60000
UA = "Tesellate/1.0 (personal dashcam labels; https://github.com/inodes/tesla)"
MIRRORS = ["https://overpass-api.de/api/interpreter", "https://lz4.overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]


# ---------------------------------------------------------------------------------------------- geometry
def stitch(ways):
    """Closed rings from the ways of a boundary (lists of (lat, lon) that share end points); a chain that never closes is dropped."""
    ways = [list(w) for w in ways if w and len(w) > 1]
    rings = []
    while ways:
        cur = ways.pop()
        while cur[0] != cur[-1]:
            for i, w in enumerate(ways):
                if w[0] == cur[-1]:
                    cur.extend(w[1:])
                elif w[-1] == cur[-1]:
                    cur.extend(reversed(w[:-1]))
                elif w[-1] == cur[0]:
                    cur[:0] = w[:-1]
                elif w[0] == cur[0]:
                    cur[:0] = list(reversed(w[1:]))
                else:
                    continue
                del ways[i]
                break
            else:
                break
        if len(cur) > 3 and cur[0] == cur[-1]:
            rings.append(cur)
    return rings


def simplify(pts, tol=SIMPLIFY):
    """Douglas-Peucker on a ring or line (iterative); the first and last points stay."""
    n = len(pts)
    if n < 5:
        return list(pts)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        far, fi = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            if l2 == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d > far:
                far, fi = d, i
        if far > tol:
            keep[fi] = True
            stack.append((a, fi))
            stack.append((fi, b))
    return [p for p, k in zip(pts, keep) if k]


def inside(rings, lat, lon):
    """Even-odd test of the point against all the rings of one suburb (a ring inside another is a hole)."""
    c = False
    for ring in rings:
        j = len(ring) - 1
        for i in range(len(ring)):
            yi, xi = ring[i]
            yj, xj = ring[j]
            if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                c = not c
            j = i
    return c


def suburbs_from_overpass(elements):
    """[{id, n: name, b: [s, w, n, e], r: [ring, ...]}] from the relations of an Overpass `out geom` answer."""
    out = []
    for el in elements or []:
        if el.get("type") != "relation":
            continue
        name = ((el.get("tags") or {}).get("name") or "").strip()
        ways = []
        for m in el.get("members") or []:
            g = m.get("geometry")
            if m.get("type") == "way" and g and m.get("role") in ("outer", "inner", ""):
                ways.append([(round(p["lat"], 5), round(p["lon"], 5)) for p in g])
        rings = [simplify(r) for r in stitch(ways)]
        rings = [r for r in rings if len(r) > 3]
        if not name or not rings:
            continue
        lats = [p[0] for r in rings for p in r]
        lons = [p[1] for r in rings for p in r]
        out.append(dict(id=el.get("id"), n=name, b=[min(lats), min(lons), max(lats), max(lons)], r=[[list(p) for p in r] for r in rings]))
    return out


# ---------------------------------------------------------------------------------------------- the fetch
def overpass_fetch(query, timeout=50):
    """The Overpass answer to `query` as a dict, or None (every mirror tried, curl as the other tools do)."""
    for ep in MIRRORS:
        try:
            r = subprocess.run(["curl", "-s", "--connect-timeout", "6", "--max-time", str(timeout), "-A", UA, "-H", "Accept: application/json",
                                "--data-urlencode", "data=" + query, ep], capture_output=True, text=True, timeout=timeout + 5)
            d = json.loads(r.stdout)
            if isinstance(d, dict) and "elements" in d and not d.get("remark"):
                return d
        except Exception:
            continue
    return None


def query_for(lat, lon):
    return ('[out:json][timeout:40];is_in(%.6f,%.6f)->.a;rel(pivot.a)["boundary"="administrative"]["admin_level"~"^(%s)$"]["name"];out geom;' % (lat, lon, LEVELS))


class Suburbs:
    def __init__(self, cache_dir, fetch=True, fetcher=None, retry_s=900, timeout=50):
        self.dir = cache_dir
        self.fetch = fetch
        self.fetcher = fetcher or (lambda q: overpass_fetch(q, timeout))
        self.retry_s = retry_s
        self.subs = {}                  # id -> (name, bbox, rings, area)
        self.grid = {}                  # (i, j) -> [id, ...]
        self.none = set()               # (lat, lon) to 4 places: asked, in no suburb
        self.memo = {}
        self.failed_until = 0.0
        self.pending = set()
        self.q = queue.Queue()
        self.worker = None
        self.lock = threading.Lock()
        self._load()

    # -- what is kept
    def _load(self):
        try:
            names = os.listdir(self.dir)
        except OSError:
            names = []
        for fn in names:
            if fn.startswith("rel_") and fn.endswith(".json"):
                try:
                    with open(os.path.join(self.dir, fn), encoding="utf-8") as fh:
                        self._add(json.load(fh), keep=False)
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        try:
            with open(os.path.join(self.dir, "none.json"), encoding="utf-8") as fh:
                self.none = {tuple(x) for x in json.load(fh)}
        except (OSError, ValueError, TypeError):
            pass

    def _add(self, s, keep=True):
        b = s["b"]
        sid = s.get("id") or s["n"]
        with self.lock:
            self.subs[sid] = (s["n"], b, s["r"], (b[2] - b[0]) * (b[3] - b[1]))
            for i in range(int(math.floor(b[0] / CELL)), int(math.floor(b[2] / CELL)) + 1):
                for j in range(int(math.floor(b[1] / CELL)), int(math.floor(b[3] / CELL)) + 1):
                    ids = self.grid.setdefault((i, j), [])
                    if sid not in ids:
                        ids.append(sid)
            self.memo.clear()
        if keep:
            try:
                os.makedirs(self.dir, exist_ok=True)
                p = os.path.join(self.dir, "rel_%s.json" % sid)
                with open(p + ".tmp", "w", encoding="utf-8") as fh:
                    json.dump(s, fh, separators=(",", ":"))
                os.replace(p + ".tmp", p)
            except OSError:
                pass

    def _keep_none(self, key):
        self.none.add(key)
        try:
            os.makedirs(self.dir, exist_ok=True)
            p = os.path.join(self.dir, "none.json")
            with open(p + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(sorted(self.none), fh, separators=(",", ":"))
            os.replace(p + ".tmp", p)
        except OSError:
            pass

    # -- the answer from what is kept
    def _held(self, lat, lon):
        """The name of the smallest kept suburb holding the point, '' when none does."""
        best, ba = "", 1e18
        for sid in self.grid.get((int(math.floor(lat / CELL)), int(math.floor(lon / CELL))), ()):
            name, b, rings, area = self.subs[sid]
            if b[0] <= lat <= b[2] and b[1] <= lon <= b[3] and area < ba and inside(rings, lat, lon):
                best, ba = name, area
        return best

    # -- the ask
    def _ask(self, lat, lon):
        """Asks Overpass which suburbs hold the point and keeps them; True when it answered (even with none)."""
        if time.time() < self.failed_until:
            return False
        try:
            d = self.fetcher(query_for(lat, lon))
        except Exception:
            d = None
        if not isinstance(d, dict) or "elements" not in d:
            self.failed_until = time.time() + self.retry_s
            return False
        got = suburbs_from_overpass(d["elements"])
        for s in got:
            self._add(s)
        if not got:
            self._keep_none((round(lat, 4), round(lon, 4)))
        return True

    def _run(self):
        while True:
            try:
                lat, lon = self.q.get(timeout=30)
            except queue.Empty:
                with self.lock:
                    if self.q.empty():
                        self.worker = None
                        return
                continue
            try:
                if not self._held(lat, lon):
                    self._ask(lat, lon)
            finally:
                with self.lock:
                    self.pending.discard((round(lat, 4), round(lon, 4)))

    def _queue(self, lat, lon):
        key = (round(lat, 4), round(lon, 4))
        with self.lock:
            if key in self.pending or key in self.none or time.time() < self.failed_until:
                return
            self.pending.add(key)
            self.q.put((lat, lon))
            if self.worker is None:
                self.worker = threading.Thread(target=self._run, daemon=True)
                self.worker.start()

    def at(self, lat, lon, wait=False):
        if lat is None or lon is None:
            return None
        key = (round(lat, 4), round(lon, 4))
        hit = self.memo.get(key)
        if hit is not None:
            return hit or None
        name = self._held(lat, lon)
        if not name:
            if key in self.none or not self.fetch:
                return None
            if wait:
                if self._ask(lat, lon):
                    name = self._held(lat, lon)
                if not name:
                    return None
            else:
                self._queue(lat, lon)
                return None                           # not known yet: not remembered, asked again
        if len(self.memo) >= MEMO_MAX:
            self.memo.clear()
        self.memo[key] = name
        return name or None

    def many(self, points, wait=True):
        return [self.at(la, lo, wait) for la, lo in points]

    def prefetch(self, points):
        """Queues the points no kept boundary holds (one in each 0.005 degree cell), to be fetched one at a time in the background; returns how many were queued."""
        n, seen = 0, set()
        for la, lo in points:
            if la is None or lo is None:
                continue
            c = (round(la / 0.005), round(lo / 0.005))
            if c in seen:
                continue
            seen.add(c)
            if not self._held(la, lo) and (round(la, 4), round(lo, 4)) not in self.none and self.fetch:
                self._queue(la, lo)
                n += 1
        return n
