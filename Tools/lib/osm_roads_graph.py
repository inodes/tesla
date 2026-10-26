"""osm_roads_graph.py - the country's main roads as arrays, and what they can tell: where a car can get to from some chargers, and where no charger is near.

  build(pbf, out_dir, progress=None)      read an OpenStreetMap extract (.osm.pbf) once and keep the road network under out_dir (arrays, read back memory-mapped)
  g = RoadGraph(out_dir, fit)
  g.reach(points, basis)                  the best charge left at every road point, from chargers that each start with their own charge
  g.nearest(points, basis)                the cost from every road point to the nearest of the chargers
  g.raster(values, vmax, bbox, size, km)  a result drawn as a grid (0 = nothing, 1..255 = share of vmax)

Roads: motorway, trunk, primary, secondary, tertiary and unclassified with their links. One-way roads, roundabouts and motorways run one way only.
The network is kept the way a router wants it: only the road ends and junctions are graph nodes ("key" nodes) and the road between two of them is one chain; the
points along a chain (the bends) are kept apart with their distance from the chain's start. A search runs over the key nodes (about a tenth of all points) and the
points along each chain get their value from the two ends of it, so the result is as exact as a search over every point and a good deal faster.
`basis` is "km" (distance) or "kwh" (energy, from the car's own consumption by speed: car_profile.json). A reach search puts one virtual source in front of all
the chargers, joined to each by an edge of (largest charge - its own charge): a single Dijkstra search from it gives every node its best charge left, whichever
charger gave it, and the charger that gave it. Chargers whose circles cross cost nothing extra, because each node is settled once.
"""
import json
import math
import os
import re
import time
from array import array

import numpy as np

CLASSES = {"motorway": 110, "motorway_link": 70, "trunk": 100, "trunk_link": 70, "primary": 90, "primary_link": 60,
           "secondary": 80, "secondary_link": 60, "tertiary": 70, "tertiary_link": 50, "unclassified": 60}      # class -> km/h of a road with no maxspeed tag
ONEWAY_CLASSES = {"motorway", "motorway_link"}
MIN_NET = 300                      # key nodes: a network smaller than this (a service loop, a cut-off stub) is not somewhere a charger can be snapped to
DETOUR = 1.3                       # a charger's access road: straight-line distance times this
SNAP_KM = 25.0                     # a charger further than this from any road is left out
FILES = ("nodes", "knode", "ca", "cb", "clen", "ckmh", "cone", "snode", "sc", "soff", "indptr", "dst", "len_m", "kmh", "comp")


def _limit(v):
    m = re.match(r"^\s*(\d+)\s*(mph)?\s*$", v or "")
    if not m:
        return None
    n = int(m.group(1))
    return round(n * 1.609) if m.group(2) else n


def _hav(lat1, lon1, lat2, lon2):
    """Metres between points (arrays or numbers, degrees)."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 12742000.0 * np.arcsin(np.sqrt(np.minimum(1.0, a)))


# ---- reading the extract ---------------------------------------------------------------------------------------------------------

def read_ways(pbf):
    """(refs, way_of_ref, oneway, kmh): node ids of every road way in order, which way each belongs to, per way 1 forward / -1 backward / 0 both, and its km/h."""
    import osmium
    refs, way_of, one, kmh = array("q"), array("i"), array("b"), array("B")
    k = 0
    for w in osmium.FileProcessor(pbf, osmium.osm.WAY).with_filter(osmium.filter.KeyFilter("highway")):
        t = w.tags
        hw = t.get("highway")
        if hw not in CLASSES or t.get("area") == "yes" or t.get("access") in ("no", "private"):
            continue
        ow = t.get("oneway")
        if ow in ("yes", "true", "1"):
            o = 1
        elif ow == "-1":
            o = -1
        elif ow in ("no", "false", "0"):
            o = 0
        else:
            o = 1 if (t.get("junction") in ("roundabout", "circular") or hw in ONEWAY_CLASSES) else 0
        ids = [n.ref for n in w.nodes]
        if len(ids) < 2:
            continue
        refs.extend(ids)
        way_of.extend([k] * len(ids))
        one.append(o)
        kmh.append(min(250, _limit(t.get("maxspeed")) or CLASSES[hw]))
        k += 1
    return np.frombuffer(refs, np.int64), np.frombuffer(way_of, np.int32), np.frombuffer(one, np.int8), np.frombuffer(kmh, np.uint8)


def read_nodes(pbf, ids):
    """(sorted ids, lat, lon) of the given node ids."""
    import osmium
    ids = np.unique(ids)
    out_id, lat, lon = array("q"), array("d"), array("d")
    for n in osmium.FileProcessor(pbf, osmium.osm.NODE).with_filter(osmium.filter.IdFilter(ids.tolist())):
        out_id.append(n.id)
        lat.append(n.location.lat)
        lon.append(n.location.lon)
    o = np.argsort(np.frombuffer(out_id, np.int64))
    return np.frombuffer(out_id, np.int64)[o], np.frombuffer(lat, np.float64)[o], np.frombuffer(lon, np.float64)[o]


def assemble(refs, way_of, oneway, kmh, node_id, lat, lon):
    """The graph arrays (see FILES) from the ways and the node positions."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    pos = np.searchsorted(node_id, refs)
    pos[pos >= len(node_id)] = 0
    ok = node_id[pos] == refs                                  # a way cut at the edge of the extract points at nodes it does not have: those points are dropped
    pos, way_of = pos[ok], way_of[ok]
    N = len(node_id)
    same = way_of[1:] == way_of[:-1]
    seg = np.where(same, _hav(lat[pos[:-1]], lon[pos[:-1]], lat[pos[1:]], lon[pos[1:]]), 0.0)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    is_start = np.concatenate([[True], ~same])
    is_end = np.concatenate([~same, [True]])
    cw = cum - cum[np.flatnonzero(is_start)][np.cumsum(is_start) - 1]          # metres from the start of its way
    cnt = np.bincount(pos, minlength=N)
    key_occ = (cnt[pos] >= 2) | is_start | is_end
    ko = np.flatnonzero(key_occ)
    sk = way_of[ko[1:]] == way_of[ko[:-1]]
    ca_o, cb_o = ko[:-1][sk], ko[1:][sk]                       # a chain runs from one key point of a way to the next
    knode = np.unique(pos[ko])
    kid = np.full(N, -1, np.int32)
    kid[knode] = np.arange(len(knode), dtype=np.int32)
    ca, cb = kid[pos[ca_o]], kid[pos[cb_o]]
    clen = np.maximum(cw[cb_o] - cw[ca_o], 0.01).astype(np.float32)
    wa = way_of[ca_o]
    ckmh, cone = kmh[wa], oneway[wa]
    so = np.flatnonzero(~key_occ)                              # the bends: which chain, how far along it
    sc = (np.searchsorted(ca_o, so, "right") - 1).astype(np.int32)
    soff = (cw[so] - cw[ca_o[sc]]).astype(np.float32)
    snode = pos[so].astype(np.int32)
    fw, bw = cone >= 0, cone <= 0
    src = np.concatenate([ca[fw], cb[bw]])
    dst = np.concatenate([cb[fw], ca[bw]])
    ln = np.concatenate([clen[fw], clen[bw]])
    sp = np.concatenate([ckmh[fw], ckmh[bw]])
    K = len(knode)
    order = np.argsort(src, kind="stable")
    src, dst, ln, sp = src[order], dst[order].astype(np.int32), ln[order], sp[order]
    indptr = np.zeros(K + 1, np.int64)
    np.cumsum(np.bincount(src, minlength=K), out=indptr[1:])
    _n, comp = connected_components(coo_matrix((np.ones(len(src), np.int8), (src, dst)), shape=(K, K)).tocsr(), directed=False)
    return dict(nodes=np.stack([np.round(lat * 1e7), np.round(lon * 1e7)], 1).astype(np.int32), knode=knode.astype(np.int32), ca=ca.astype(np.int32), cb=cb.astype(np.int32),
                clen=clen, ckmh=ckmh, cone=cone, snode=snode, sc=sc, soff=soff, indptr=indptr, dst=dst, len_m=ln, kmh=sp, comp=comp.astype(np.int32))


def build(pbf, out_dir, progress=None):
    """Reads the extract and keeps the graph in out_dir. About a minute and 2-3 GB of memory for the whole of Australia."""
    t0 = time.time()
    prog = progress or (lambda *a: None)
    prog(0, 4, "Reading roads")
    refs, way_of, oneway, kmh = read_ways(pbf)
    prog(1, 4, "Reading road points")
    node_id, lat, lon = read_nodes(pbf, refs)
    prog(2, 4, "Joining roads")
    g = assemble(refs, way_of, oneway, kmh, node_id, lat, lon)
    prog(3, 4, "Saving")
    tmp = out_dir.rstrip("/") + ".new"
    os.makedirs(tmp, exist_ok=True)
    for k in FILES:
        np.save(os.path.join(tmp, k + ".npy"), g[k])
    meta = dict(source=os.path.basename(pbf), points=int(len(g["nodes"])), junctions=int(len(g["knode"])), arcs=int(len(g["dst"])), built=time.strftime("%Y-%m-%d %H:%M:%S"),
                seconds=round(time.time() - t0))
    with open(os.path.join(tmp, "meta.json"), "w") as f:
        json.dump(meta, f)
    os.makedirs(out_dir, exist_ok=True)
    for k in FILES + ("meta",):
        ext = ".json" if k == "meta" else ".npy"
        os.replace(os.path.join(tmp, k + ext), os.path.join(out_dir, k + ext))
    try:
        os.rmdir(tmp)
    except OSError:
        pass
    prog(4, 4, "Done")
    return meta


# ---- consumption -----------------------------------------------------------------------------------------------------------------

def kwh_per_100km_fit(profile):
    """(c0, c1): kWh/100 km = c0 + c1 * v^2 at v km/h, least squares over the car's measured speed bands (car_profile.json speed_vs_efficiency_grade_corrected) weighted
    by distance. The bands are noisy, so a smooth curve with two terms (rolling + aerodynamic) is used rather than the bands themselves."""
    bands = []
    try:
        for r in profile["speed_vs_efficiency_grade_corrected"]:
            lo, hi = r["speed_range_kmh"]
            if hi is None:
                hi = lo + 10
            if r.get("confidence") in ("high", "medium") and r["total_distance_km"] > 20:
                bands.append(((lo + hi) / 2.0, float(r["avg_efficiency_kwh_per_100km"]), float(r["total_distance_km"])))
    except (KeyError, TypeError, ValueError):
        pass
    if len(bands) < 3:
        return 11.0, 0.0010
    v = np.array([b[0] for b in bands])
    e = np.array([b[1] for b in bands])
    w = np.sqrt([b[2] for b in bands])
    A = np.stack([np.ones_like(v), v * v], 1) * w[:, None]
    c, *_ = np.linalg.lstsq(A, e * w, rcond=None)
    return max(float(c[0]), 6.0), max(float(c[1]), 0.0004)


# ---- the graph -------------------------------------------------------------------------------------------------------------------

class RoadGraph:
    def __init__(self, d, fit=(11.0, 0.0010)):
        self.dir = d
        self.fit = fit
        self.a = {k: np.load(os.path.join(d, k + ".npy"), mmap_mode="r") for k in FILES}
        for k in ("knode", "ca", "cb", "clen", "ckmh", "cone", "snode", "sc", "soff", "comp"):       # read all the time, so in memory rather than mapped
            self.a[k] = np.array(self.a[k])
        self.cstart = np.searchsorted(self.a["sc"], np.arange(len(self.a["clen"]) + 1))           # the bends of chain c are snode[cstart[c]:cstart[c + 1]]
        self.N = len(self.a["nodes"])
        self.K = len(self.a["knode"])
        self._w = {}
        self._csr = {}
        self._tree = None
        self.kid = np.full(self.N, -1, np.int32)
        self.kid[self.a["knode"]] = np.arange(self.K, dtype=np.int32)
        self.sid = np.full(self.N, -1, np.int32)                 # index into the bends, for a point that is one
        self.sid[self.a["snode"]] = np.arange(len(self.a["snode"]), dtype=np.int32)
        self.big_k = np.bincount(self.a["comp"])[self.a["comp"]] >= MIN_NET
        self.lat = np.asarray(self.a["nodes"][:, 0]) / 1e7
        self.lon = np.asarray(self.a["nodes"][:, 1]) / 1e7

    # cost factors: a chain's metres become km, or kWh at the chain's speed
    def _f(self, basis, kmh):
        kmh = np.asarray(kmh, np.float64)
        return np.ones_like(kmh) if basis == "km" else (self.fit[0] + self.fit[1] * kmh * kmh) / 100.0

    def weights(self, basis):
        if basis not in self._w:
            self._w[basis] = np.maximum(np.asarray(self.a["len_m"], np.float64) / 1000.0 * self._f(basis, self.a["kmh"]), 1e-6)
        return self._w[basis]

    def access(self, metres, basis):
        return metres / 1000.0 * DETOUR * float(self._f(basis, [50.0])[0])

    def _graph(self, basis, back):
        from scipy.sparse import csr_matrix
        key = (basis, back)
        if key not in self._csr:
            m = csr_matrix((self.weights(basis), np.asarray(self.a["dst"]), np.asarray(self.a["indptr"])), shape=(self.K, self.K))
            self._csr[key] = m.T.tocsr() if back else m
        return self._csr[key]

    # ---- putting a charger on the roads
    def snap(self, lat, lon):
        """(point, metres): the nearest road point of a big network; (None, None) when there is none close enough."""
        if self._tree is None:
            from scipy.spatial import cKDTree
            bigc = self.big_k
            chain_big = bigc[np.asarray(self.a["ca"])]
            ok = np.zeros(self.N, bool)
            ok[np.asarray(self.a["knode"])[bigc]] = True
            ok[np.asarray(self.a["snode"])[chain_big[np.asarray(self.a["sc"])]]] = True
            self._ids = np.flatnonzero(ok)
            self._tree = cKDTree(self._xyz(self.lat[self._ids], self.lon[self._ids]))
        d, i = self._tree.query(self._xyz(np.array([lat]), np.array([lon])))
        m = float(d[0]) * 6371000.0
        return (int(self._ids[i[0]]), m) if m < SNAP_KM * 1000 else (None, None)

    @staticmethod
    def _xyz(la, lo):
        la, lo = np.radians(la), np.radians(lo)
        return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], 1)

    def _entries(self, node, basis, back):
        """[(junction, cost)]: how a charger at a road point joins the junction graph. forward (reach): the junctions its car can drive to; back (nearest): the junctions a car
        can arrive from, which are the same ends the other way round."""
        k = self.kid[node]
        if k >= 0:
            return [(int(k), 0.0)]
        s = int(self.sid[node])
        c = int(self.a["sc"][s])
        off, ln, one = float(self.a["soff"][s]), float(self.a["clen"][c]), int(self.a["cone"][c])
        f = float(self._f(basis, [self.a["ckmh"][c]])[0])
        to_b, to_a = (ln - off) / 1000.0 * f, off / 1000.0 * f
        out = []
        if not back:
            if one >= 0:
                out.append((int(self.a["cb"][c]), to_b))
            if one <= 0:
                out.append((int(self.a["ca"][c]), to_a))
        else:
            if one >= 0:
                out.append((int(self.a["ca"][c]), to_a))
            if one <= 0:
                out.append((int(self.a["cb"][c]), to_b))
        return out

    def _bends(self, kcost, basis, back, kwho=None):
        """Cost at every road point from the cost at the junctions: junctions as they are, a bend from the two ends of its chain. forward: the cost to get there from the
        chargers; back: the cost to get from there to the nearest charger. With kwho (a charger index per junction) the charger behind each point is returned as well."""
        a = self.a
        out = np.full(self.N, np.inf)
        out[a["knode"]] = kcost
        c, off = np.asarray(a["sc"]), np.asarray(a["soff"], np.float64)
        ln = np.asarray(a["clen"], np.float64)[c]
        f = self._f(basis, np.asarray(a["ckmh"])[c])
        one = np.asarray(a["cone"])[c]
        ka, kb = kcost[np.asarray(a["ca"])[c]], kcost[np.asarray(a["cb"])[c]]
        if not back:                                            # arrive from a (when a->b may be driven) or from b (when b->a may be)
            v1 = np.where(one >= 0, ka + off / 1000.0 * f, np.inf)
            v2 = np.where(one <= 0, kb + (ln - off) / 1000.0 * f, np.inf)
        else:                                                   # leave toward b (a->b) or toward a (b->a)
            v1 = np.where(one >= 0, kb + (ln - off) / 1000.0 * f, np.inf)
            v2 = np.where(one <= 0, ka + off / 1000.0 * f, np.inf)
        out[a["snode"]] = np.minimum(v1, v2)
        if kwho is None:
            return out
        who = np.full(self.N, -1, np.int64)
        who[a["knode"]] = kwho
        who[a["snode"]] = np.where(v1 <= v2, kwho[np.asarray(a["ca"])[c]], kwho[np.asarray(a["cb"])[c]])
        return out, who

    def _fix_chargers(self, cost, st, basis, back):
        """A charger in the middle of a chain: the bends of that chain are worth the direct run to or from it, not the run round by the chain's ends.
        st: [(road point, cost at the charger itself)]."""
        for node, base in st:
            cost[node] = min(cost[node], base)
            s = int(self.sid[node])
            if s < 0:
                continue
            c = int(self.a["sc"][s])
            lo, hi = int(self.cstart[c]), int(self.cstart[c + 1])
            f = float(self._f(basis, [self.a["ckmh"][c]])[0])
            one = int(self.a["cone"][c])
            d = (np.asarray(self.a["soff"][lo:hi], np.float64) - float(self.a["soff"][s])) / 1000.0 * f       # > 0: the bend lies on the b side of the charger
            if not back:
                ok = ((d >= 0) & (one >= 0)) | ((d <= 0) & (one <= 0))        # driving charger -> bend must be allowed
            else:
                ok = ((d <= 0) & (one >= 0)) | ((d >= 0) & (one <= 0))        # driving bend -> charger must be allowed
            nodes = np.asarray(self.a["snode"][lo:hi])
            cost[nodes] = np.minimum(cost[nodes], np.where(ok, base + np.abs(d), np.inf))
        return cost

    # ---- the two searches
    def reach(self, points, basis="km", full=None):
        """points: [(lat, lon, charge)], the charge (km or kWh) each starts with. Returns dict(left, full, who, outline): `left` the charge left at every road point (-1 = out of
        reach), `who` the point that gave it at each junction (index into points), `outline` the points that give the charge near the edge of what can be reached."""
        from scipy.sparse import csr_matrix
        from scipy.sparse.csgraph import dijkstra
        st = {}
        for k, (la, lo, c) in enumerate(points):
            nd, m = self.snap(la, lo)
            if nd is None:
                continue
            c = c - self.access(m, basis)
            if c > 0 and (nd not in st or st[nd][0] < c):
                st[nd] = (c, k)
        if not st:
            return dict(left=np.full(self.N, -1.0, np.float32), full=0.0, who=np.zeros(self.K, np.int32), outline=[])
        R = float(full if full else max(c for c, _k in st.values()))
        ent_k, ent_w, ent_who, fixes = [], [], [], []
        for nd, (c, k) in st.items():
            for kk, cost in self._entries(nd, basis, False):
                if c - cost > 0:
                    ent_k.append(kk)
                    ent_w.append(R - c + cost)
                    ent_who.append(k)
            fixes.append((nd, R - c))
        K = self.K
        if not ent_k:
            return dict(left=np.full(self.N, -1.0, np.float32), full=R, who=np.full(K, -1, np.int32), outline=[])
        m = self._graph(basis, False)
        ent_k = np.array(ent_k, np.int64)
        indptr = np.concatenate([m.indptr, [m.indptr[-1] + len(ent_k)]])
        aug = csr_matrix((np.concatenate([m.data, np.maximum(ent_w, 1e-9)]), np.concatenate([m.indices, ent_k]), indptr), shape=(K + 1, K + 1))
        dist, pred = dijkstra(aug, directed=True, indices=K, limit=R, return_predecessors=True)
        # who gave each junction its charge: follow the predecessors up to the virtual source's neighbour (pointer jumping)
        who_of = np.full(K + 1, -1, np.int64)
        best = {}
        for kk, w, k in zip(ent_k.tolist(), ent_w, ent_who):
            if kk not in best or w < best[kk][0]:
                best[kk] = (w, k)
        for kk, (_w, k) in best.items():
            who_of[kk] = k
        p = pred.astype(np.int64)
        root = p == K
        p[p < 0] = K
        p[root] = np.flatnonzero(root)
        p[K] = K
        for _ in range(48):
            q = p[p]
            if np.array_equal(q, p):
                break
            p = q
        who = who_of[p[:K]]
        kd = dist[:K]
        cost, pwho = self._bends(kd, basis, False, who)
        cost = self._fix_chargers(cost, fixes, basis, False)
        left = np.where(cost <= R, R - cost, -1.0)
        edge = (left >= 0) & (left < 0.08 * R)                  # the road points where the charge is nearly gone: the chargers that give it are what shapes the edge
        outline = sorted({int(x) for x in np.unique(pwho[edge]) if x >= 0})
        return dict(left=left.astype(np.float32), full=R, who=who.astype(np.int32), outline=outline)

    def nearest(self, points, basis="km"):
        """Cost (km or kWh) from every road point to the nearest of the points [(lat, lon)], roads taken the way a car drives them to the charger. inf = unreachable."""
        from scipy.sparse import csr_matrix
        from scipy.sparse.csgraph import dijkstra
        ent_k, ent_w, fixes = [], [], []
        for la, lo in points:
            nd, m = self.snap(la, lo)
            if nd is None:
                continue
            acc = self.access(m, basis)
            for kk, cost in self._entries(nd, basis, True):
                ent_k.append(kk)
                ent_w.append(acc + cost)
            fixes.append((nd, acc))
        m = self._graph(basis, True)
        K = self.K
        ent_k = np.array(ent_k, np.int64)
        indptr = np.concatenate([m.indptr, [m.indptr[-1] + len(ent_k)]])
        aug = csr_matrix((np.concatenate([m.data, np.maximum(ent_w, 1e-9)]), np.concatenate([m.indices, ent_k]), indptr), shape=(K + 1, K + 1))
        d = dijkstra(aug, directed=True, indices=K)[:K]
        return self._fix_chargers(self._bends(d, basis, True), fixes, basis, True)

    # ---- drawing
    def raster(self, val, vmax, bbox, size=512, buf_km=None):
        """Road points with a value as a grid laid over bbox=(south, west, north, east) the way a web map lays an image (rows even in Mercator y): 0 = nothing there,
        1..255 = value / vmax. Each point paints about buf_km around itself so a road reads as a band (default: two cells, between 0.2 and 4 km)."""
        from scipy.ndimage import maximum_filter
        s, w, nn, e = bbox
        ok = (self.lat >= s) & (self.lat <= nn) & (self.lon >= w) & (self.lon <= e) & np.isfinite(val) & (val >= 0)
        my = lambda la: np.log(np.tan(np.pi / 4 + np.radians(la) / 2))
        y0, y1 = float(my(s)), float(my(nn))
        H = int(size)
        W = int(max(1, min(2 * size, round(size * math.radians(e - w) / (y1 - y0)))))
        iy = np.clip(((y1 - my(self.lat[ok])) / (y1 - y0) * H).astype(int), 0, H - 1)
        ix = np.clip(((self.lon[ok] - w) / (e - w) * W).astype(int), 0, W - 1)
        q = 1 + np.clip(np.rint(254.0 * np.asarray(val)[ok] / max(vmax, 1e-9)), 0, 254).astype(np.uint8)
        grid = np.zeros((H, W), np.uint8)
        np.maximum.at(grid, (iy, ix), q)
        ky = (nn - s) * 111.0 / H                                      # km per cell, down and across, in the middle of the picture
        kx = (e - w) * 111.0 * math.cos(math.radians((s + nn) / 2)) / W
        b = buf_km if buf_km is not None else min(4.0, max(0.2, 2.0 * max(kx, ky)))
        ry, rx = int(round(b / max(ky, 1e-6))), int(round(b / max(kx, 1e-6)))
        if ry >= 1 or rx >= 1:
            grid = maximum_filter(grid, size=(2 * ry + 1, 2 * rx + 1))
        return grid
