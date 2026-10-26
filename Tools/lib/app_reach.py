"""app_reach.py - the range shading and the coverage gaps of the Places map.

  R = ReachService(tools_dir, data_dir)
  R.status()                  the road network on disk (built or not), the build job, the car's range
  R.start_build()             download the OpenStreetMap extract of the country (once, kept only as a temporary file) and build the road network in <data>/roads/graph
  R.compute(body)             a picture to lay over the map (with the longest charger-free stretches when it shows the gaps)
R.at(body)                  what is around a spot: the nearest chargers on the roads and how far, the next one the other way, the charge left there

body: pts [[lat, lon, key], ...] the chargers picked, mode "reach" (where the car can get to from them) or "gaps" (how far each road is from the nearest of them),
frac the charge at the chargers (0..1), reserve the share of the battery to keep (0..1), basis "km" or "kwh", bbox [south, west, north, east] and size (pixels) of the picture.
at: pts as above, lat, lon of the spot, basis; mode, frac and reserve as for compute (the charge left is given when that picture has been made).
The picture is a PNG as a data URL with the box it covers. The search itself is lib/osm_roads_graph.py (one search for all the chargers, whichever way the ranges overlap);
it takes about a second for the whole country. A finished search is kept, so moving or zooming the map only redraws it.
"""
import base64
import glob
import hashlib
import json
import math
import os
import struct
import tempfile
import threading
import time
import urllib.request
import zlib

import numpy as np

import app_config

PBF_URL = "https://download.geofabrik.de/australia-oceania/australia-latest.osm.pbf"


def png_bytes(rgba):
    """A PNG file from an (H, W, 4) uint8 array."""
    h, w = rgba.shape[:2]
    raw = np.concatenate([np.zeros((h, 1), np.uint8), rgba.reshape(h, w * 4)], 1).tobytes()

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 3)) + chunk(b"IEND", b"")


def _lut_reach():
    """Charge left, 1 (the edge of what can be reached) .. 255 (full): pale mint to light teal, light so the chargers (red, green, blue) stay the strongest colours."""
    t = np.linspace(0, 1, 256)[:, None]
    c0, c1 = np.array([205, 240, 230, 105]), np.array([95, 195, 180, 150])
    lut = (c0 + (c1 - c0) * t).astype(np.uint8)
    lut[0] = 0
    return lut


def _lut_gaps():
    """Distance to the nearest charger as a share of 1.5 x the safe range: clear up to half of it, amber up to all of it, red beyond."""
    lut = np.zeros((256, 4), np.uint8)
    for i in range(1, 256):
        q = (i - 1) / 254.0 * 1.5                        # in units of the safe range
        lut[i] = (0, 0, 0, 0) if q < 0.5 else ((245, 158, 11, 170) if q < 1.0 else (220, 38, 38, 190))
    return lut


class ReachService:
    def __init__(self, tools_dir, data_dir):
        self.tools, self.data = tools_dir, data_dir
        self.root = os.path.join(data_dir, "roads")
        self.dir = os.path.join(self.root, "graph")
        self.g = None
        self.fit = None
        self.lock = threading.Lock()
        self.job = dict(running=False, step="", done=0, total=0, error="", at=0)
        self.cache = {}
        self.prep = {}
        self.timer = None
        self.luts = dict(reach=_lut_reach(), gaps=_lut_gaps())

    # ---- what is on disk
    def meta(self):
        try:
            with open(os.path.join(self.dir, "meta.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def car(self):
        """The car's full range in km and usable energy in kWh (car_profile.json: what the battery reports now)."""
        out = dict(km=560.0, kwh=85.0, fit=None)
        try:
            with open(os.path.join(self.data, "car_profile.json"), encoding="utf-8") as f:
                j = json.load(f)
            p = next(iter(j.values()))
            u = p.get("usable_battery_capacity_kwh") or {}
            out["kwh"] = float(u.get("kwh_used_for_range") or out["kwh"])
            out["km"] = float((u.get("live_reading") or {}).get("max_range_km") or out["km"])
            out["profile"] = p
        except (OSError, ValueError, StopIteration, TypeError):
            pass
        return out

    def status(self):
        c = self.car()
        return dict(built=self.meta() is not None, meta=self.meta(), job=dict(self.job), km=round(c["km"]), kwh=round(c["kwh"], 1),
                    reserve=app_config.get("reach.reserve_pct"))

    # ---- building the network
    def start_build(self):
        with self.lock:
            if self.job["running"]:
                return dict(ok=True, **self.job)
            self.job = dict(running=True, step="Starting", done=0, total=1, error="", at=time.time())
        threading.Thread(target=self._build, daemon=True).start()
        return dict(ok=True, **self.job)

    def _step(self, step, done, total):
        self.job.update(step=step, done=done, total=total)

    def _build(self):
        tmp = None
        try:
            from lib import osm_roads_graph as G
        except ImportError:
            import osm_roads_graph as G
        try:
            os.makedirs(self.root, exist_ok=True)
            have = sorted(glob.glob(os.path.join(self.root, "*.osm.pbf")))
            if have:
                pbf = have[-1]
            else:
                url = app_config.get("reach.pbf_url") or PBF_URL
                fd, tmp = tempfile.mkstemp(suffix=".osm.pbf")
                os.close(fd)
                req = urllib.request.Request(url, headers={"User-Agent": "tesellate (personal use)"})
                with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                    total = int(r.headers.get("Content-Length") or 0)
                    got = 0
                    while True:
                        b = r.read(1 << 20)
                        if not b:
                            break
                        f.write(b)
                        got += len(b)
                        self._step("Downloading road data", got // (1 << 20), max(1, total // (1 << 20)))
                pbf = tmp
            self._step("Reading roads", 0, 4)
            G.build(pbf, self.dir, progress=lambda a, b, s: self._step(s, a, b))
            self.g = None
            self.cache = {}
            self.prep = {}
            self.job.update(running=False, step="Done", done=1, total=1, error="")
        except Exception as e:
            self.job.update(running=False, error="%s: %s" % (type(e).__name__, e))
        finally:
            if tmp and os.path.isfile(tmp):
                try:
                    os.remove(tmp)                      # our own temporary download
                except OSError:
                    pass

    # ---- the graph in memory (let go of when not used for a while)
    def _graph(self):
        try:
            from lib import osm_roads_graph as G
        except ImportError:
            import osm_roads_graph as G
        with self.lock:
            if self.g is None:
                c = self.car()
                fit = G.kwh_per_100km_fit(c.get("profile") or {})
                self.g = G.RoadGraph(self.dir, fit)
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(max(1, int(app_config.get("reach.idle_unload_min") or 15)) * 60, self._unload)
            self.timer.daemon = True
            self.timer.start()
            return self.g

    def _unload(self):
        with self.lock:
            self.g = None
            self.cache = {}
            self.prep = {}

    # ---- a picture
    def compute(self, b):
        if not self.meta():
            return dict(error="needs_graph")
        t0 = time.time()
        pts = [(float(p[0]), float(p[1]), str(p[2]) if len(p) > 2 else "") for p in (b.get("pts") or []) if len(p) >= 2 and p[0] is not None and p[1] is not None]
        mode = "gaps" if b.get("mode") == "gaps" else "reach"
        basis = "kwh" if b.get("basis") == "kwh" else "km"
        frac = min(1.0, max(0.0, float(b.get("frac", 1.0))))
        reserve = min(0.9, max(0.0, float(b.get("reserve", app_config.get("reach.reserve_pct") / 100.0))))
        bbox = [float(x) for x in (b.get("bbox") or [-45, 110, -9, 155])]
        size = min(1400, max(100, int(b.get("size") or app_config.get("reach.image_px"))))
        if not pts:
            return dict(empty=True)
        c = self.car()
        full = c["kwh"] if basis == "kwh" else c["km"]
        key = hashlib.sha1(json.dumps([mode, basis, round(frac, 3), round(reserve, 3), [(round(p[0], 5), round(p[1], 5)) for p in pts]]).encode()).hexdigest()
        g = self._graph()
        hit = self.cache.get(key)
        if hit is None:
            if mode == "reach":
                charge = max(frac - reserve, 0.0) * full
                if charge <= 0:
                    return dict(error="The charge is not above the reserve.")
                r = g.reach([(p[0], p[1], charge) for p in pts], basis)
                hit = dict(val=r["left"], vmax=r["full"], outline=[pts[i][2] for i in r["outline"] if i < len(pts) and pts[i][2]], full=full)
            else:
                d, who = g.nearest([(p[0], p[1]) for p in pts], basis, want_who=True)
                safe = (1.0 - reserve) * full
                st = [dict(a=pts[a][2], b=pts[b][2], cost=round(c, 1), lat=round(float(g.lat[nd]), 5), lon=round(float(g.lon[nd]), 5)) for a, b, c, nd in g.stretches(d, who, basis)[:12]]
                hit = dict(val=np.where(np.isfinite(d), d, -1.0), vmax=1.5 * safe, outline=[], full=full, safe=safe, stretches=st)
            if len(self.cache) >= 4:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = hit
        grid = g.raster(hit["val"], hit["vmax"], bbox, size)
        img = png_bytes(self.luts[mode][grid])
        return dict(img="data:image/png;base64," + base64.b64encode(img).decode(), bbox=bbox, mode=mode, unit="kWh" if basis == "kwh" else "km", full=round(full, 1),
                    vmax=round(hit["vmax"], 1), safe=round(hit.get("safe", 0), 1), outline=hit["outline"], n=len(pts), stretches=hit.get("stretches", []), took=round(time.time() - t0, 2))

    # ---- a spot
    def at(self, b):
        """The roads around a spot (see the module text). -> dict(lat, lon, near=[{key, cost}], other={key, cost}, between=cost, left=charge left or None, unit) / dict(error)"""
        if not self.meta():
            return dict(error="needs_graph")
        pts = [(float(p[0]), float(p[1]), str(p[2]) if len(p) > 2 else "") for p in (b.get("pts") or []) if len(p) >= 2 and p[0] is not None and p[1] is not None]
        if not pts or b.get("lat") is None or b.get("lon") is None:
            return dict(empty=True)
        basis = "kwh" if b.get("basis") == "kwh" else "km"
        c = self.car()
        full = c["kwh"] if basis == "kwh" else c["km"]
        g = self._graph()
        pk = hashlib.sha1(json.dumps([basis, [(round(p[0], 5), round(p[1], 5)) for p in pts]]).encode()).hexdigest()
        prep = self.prep.get(pk)
        if prep is None:
            prep = g.prepare([(p[0], p[1]) for p in pts], basis)
            if len(self.prep) >= 3:
                self.prep.pop(next(iter(self.prep)))
            self.prep[pk] = prep
        r = g.at(float(b["lat"]), float(b["lon"]), prep, basis, limit=2.5 * full)
        if r is None:
            return dict(error="No road near there.")
        near = [dict(key=pts[i][2], cost=round(cost, 1)) for i, cost, _s in r["found"]]
        other = dict(key=pts[r["back"][0]][2], cost=round(r["back"][1], 1)) if r["back"] else None
        left = None
        frac = min(1.0, max(0.0, float(b.get("frac", 1.0))))
        reserve = min(0.9, max(0.0, float(b.get("reserve", app_config.get("reach.reserve_pct") / 100.0))))
        key = hashlib.sha1(json.dumps(["reach", basis, round(frac, 3), round(reserve, 3), [(round(p[0], 5), round(p[1], 5)) for p in pts]]).encode()).hexdigest()
        hit = self.cache.get(key)
        if hit is not None:
            nd, _m = g.snap(float(b["lat"]), float(b["lon"]))
            v = float(hit["val"][nd]) if nd is not None else -1.0
            left = round(v, 1) if v >= 0 else None
        return dict(lat=r["lat"], lon=r["lon"], near=near, other=other, between=round(near[0]["cost"] + other["cost"], 1) if near and other else None, left=left,
                    unit="kWh" if basis == "kwh" else "km", full=round(full, 1))
