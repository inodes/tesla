#!/usr/bin/env python3
"""FSD 105 plateau vs OpenStreetMap posted limits (REQ-190 follow-up).

For every highway sample in the drive deep-dive telemetry since --since (default 2026-10-01)
this finds the OpenStreetMap road the car was on (nearest way, honouring one-way / carriageway
direction by the car's bearing) and reads its posted limit (`maxspeed`, `maxspeed:forward` /
`:backward` for the direction travelled). It then compares that with what the car did.

What the driver did is the best proxy for the signed limit: STANDARD mode runs about 102 in a
100 zone and 112 in a 110 zone, so a 100s/110s stretch tells us what was really posted. The
105 "hurry" plateau is ambiguous (hurry in a 100 or hurry in a 110 that FSD read as 100), so for
those OSM is the tie-breaker: an OSM 110 under a 105 section is a candidate where the car's map
data (or OSM) is wrong, and the stretch is worth a TomTom MapShare check.

Outputs in <data>/drives/analysis/:
  fsd_osm_limit_samples.csv    every matched highway sample
  fsd_osm_105_sections.csv     each 105 section with OSM limit mix and verdict
  fsd_osm_calibration.csv      how often OSM agrees with the driver's STANDARD-mode speed
  fsd_osm_disagreements.csv    stretches where the car's perceived limit and OSM differ
  osm_cache/                   Overpass tiles (re-runs only fetch what is missing)
Usage: analyse_fsd_osm_limits.py [--data-dir DIR] [--since YYYY-MM-DD] [--fetch-only] [--budget SECONDS] [--no-fetch]
(--budget stops tile fetching after that many seconds and exits, re-run to resume - for hosts that cap command time.)
"""
import csv, sys, glob, os, re, json, math, time, subprocess, collections, datetime

try:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
    from osm_client_common import OVERPASS_ENDPOINTS
except Exception:
    OVERPASS_ENDPOINTS = [
        "https://lz4.overpass-api.de/api/interpreter",
        "https://overpass.openstreetmap.fr/api/interpreter",
        "https://overpass-api.de/api/interpreter",
    ]
UA = "tesla-fsd-speed-analysis/1.0 (personal research)"

_DATA = os.path.expanduser(os.environ.get("TESLA_DATA_DIR", "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla"))
if "--data-dir" in sys.argv:
    _DATA = os.path.expanduser(sys.argv[sys.argv.index("--data-dir") + 1])
SINCE = "2026-10-01"
if "--since" in sys.argv:
    SINCE = sys.argv[sys.argv.index("--since") + 1]
DEEPDIVE_DIR = os.path.join(_DATA, "drives", "deepdive")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import drive_paths as _dp   # REQ-193: per-drive folders + legacy flat files
OUT_DIR = os.path.join(_DATA, "drives", "analysis")
CACHE = os.path.join(OUT_DIR, "osm_cache")
os.makedirs(CACHE, exist_ok=True)

TILE = 0.05         # degrees per Overpass tile (0.1 tiles from earlier runs are still reused)
MIN_SPEED = 85.0    # km/h; only highway-speed samples are matched
MAX_DIST_M = 30.0   # how far a GPS sample may sit from the OSM centreline
HIGHWAYS = "motorway|trunk|primary|secondary"


# ---------------------------------------------------------------- telemetry
def band(sp):
    s = int(round(sp))
    if 99 <= s <= 102: return "100s"
    if 103 <= s <= 107: return "105h"
    if 110 <= s <= 113: return "110s"
    if 114 <= s <= 116: return "115h"
    return "x"

def hav_m(a, b, c, d):
    R = 6371000.0; p = math.pi / 180
    x = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))

def bearing(a, b, c, d):
    p = math.pi / 180
    y = math.sin((d - b) * p) * math.cos(c * p)
    x = math.cos(a * p) * math.sin(c * p) - math.sin(a * p) * math.cos(c * p) * math.cos((d - b) * p)
    return (math.degrees(math.atan2(y, x)) + 360) % 360

def load(f):
    out = []
    for r in csv.DictReader(open(f, encoding="utf-8")):
        try:
            sp = float(r["Speed (km/h)"]); lat = float(r["Latitude"]); lon = float(r["Longitude"]); od = float(r["Odometer (km)"])
        except Exception:
            continue
        if r["Shift State"] != "D":
            continue
        t = datetime.datetime.strptime(r[next(k for k in r if k.startswith("Timestamp"))], "%Y-%m-%d %H:%M:%S")
        out.append((t, sp, lat, lon, od))
    return out

def segs(rows):
    b = [band(r[1]) for r in rows]
    for i in range(1, len(b) - 1):
        if b[i] == "x" and b[i-1] == b[i+1] and b[i-1] != "x" and (rows[i+1][0] - rows[i-1][0]).total_seconds() <= 150:
            b[i] = b[i-1]
    out = []; i = 0
    while i < len(rows):
        j = i
        while j + 1 < len(rows) and b[j+1] == b[i] and (rows[j+1][0] - rows[j][0]).total_seconds() <= 120:
            j += 1
        out.append((b[i], i, j)); i = j + 1
    return out, b


# ---------------------------------------------------------------- Overpass
def tile_path(ti, tj):
    """File holding this TILE-sized square: its own cache file, or the bigger 0.1 degree file that covers it."""
    own = os.path.join(CACHE, f"hw05_{ti}_{tj}.json")
    old = os.path.join(CACHE, f"hw_{ti // 2}_{tj // 2}.json")
    if not os.path.exists(own) and os.path.exists(old):
        return old
    return own

GOOD_MIRRORS = [m for m in OVERPASS_ENDPOINTS if any(h in m for h in ("lz4.overpass-api.de", "//overpass-api.de"))] or OVERPASS_ENDPOINTS   # kumi / private.coffee / openstreetmap.fr were unreachable or 403 and only added minutes of timeouts

def fetch_tile(ti, tj, log, first=0):
    p = tile_path(ti, tj)
    if os.path.exists(p):
        return True
    s, w = ti * TILE, tj * TILE   # p is the hw05_ file here: no 0.1 file covers this tile
    n, e = s + TILE, w + TILE
    pad = 0.003
    q = (f'[out:json][timeout:90];way["highway"~"^({HIGHWAYS})(_link)?$"]'
         f'({s-pad:.4f},{w-pad:.4f},{n+pad:.4f},{e+pad:.4f});out tags geom;')
    for attempt in range(3):
        for ep in GOOD_MIRRORS[first % len(GOOD_MIRRORS):] + GOOD_MIRRORS[:first % len(GOOD_MIRRORS)]:
            try:
                r = subprocess.run(["curl", "-s", "--connect-timeout", "6", "--max-time", "45", "-A", UA, "-H", "Accept: application/json",
                                    "--data-urlencode", f"data={q}", ep],
                                   capture_output=True, text=True, timeout=55)
                d = json.loads(r.stdout)
                if "elements" in d:
                    tmp = p + ".tmp"
                    with open(tmp, "w") as fh:
                        json.dump({"tile": [ti, tj], "fetched": time.strftime("%Y-%m-%d"), "elements": d["elements"]}, fh)
                    os.replace(tmp, p)
                    return True
            except Exception:
                pass
        time.sleep(3 * (attempt + 1))
    log(f"  tile {ti},{tj} failed on every mirror")
    return False


# ---------------------------------------------------------------- OSM tags
def parse_limit(v):
    if v is None:
        return None
    m = re.match(r"^\s*(\d+)\s*(mph)?\s*$", v)
    if not m:
        return None
    n = int(m.group(1))
    return round(n * 1.609) if m.group(2) else n

def oneway_dir(tags):
    ow = tags.get("oneway")
    if ow in ("yes", "true", "1"): return 1
    if ow == "-1": return -1
    if ow == "no": return 0
    if tags.get("highway") in ("motorway", "motorway_link") and tags.get("junction") != "roundabout":
        return 1
    return 0

class Way:
    __slots__ = ("id", "tags", "pts", "ow", "fwd", "bwd", "hw", "name", "ref", "var", "cond")
    def __init__(self, el):
        self.id = el["id"]; t = el.get("tags", {}); self.tags = t
        self.pts = [(g["lat"], g["lon"]) for g in el.get("geometry", [])]
        self.ow = oneway_dir(t)
        base = parse_limit(t.get("maxspeed"))
        self.fwd = parse_limit(t.get("maxspeed:forward")) or base
        self.bwd = parse_limit(t.get("maxspeed:backward")) or base
        self.hw = t.get("highway", ""); self.name = t.get("name", ""); self.ref = t.get("ref", "")
        self.var = ("variable" in t.get("maxspeed", "") or "signals" in t.get("maxspeed", "")
                    or any(k.startswith("maxspeed:variable") for k in t))
        self.cond = t.get("maxspeed:conditional", "")

def load_ways(tiles):
    ways = {}
    paths = sorted({tile_path(ti, tj) for (ti, tj) in tiles})
    for p in paths:
        if not os.path.exists(p):
            continue
        for el in json.load(open(p))["elements"]:
            if el.get("type") == "way" and el["id"] not in ways and len(el.get("geometry", [])) >= 2:
                ways[el["id"]] = Way(el)
    return ways

SC = 0.002
def build_index(ways):
    idx = collections.defaultdict(list)
    for w in ways.values():
        for k in range(len(w.pts) - 1):
            a, b = w.pts[k], w.pts[k+1]
            for ci in range(int(math.floor(min(a[0], b[0]) / SC)), int(math.floor(max(a[0], b[0]) / SC)) + 1):
                for cj in range(int(math.floor(min(a[1], b[1]) / SC)), int(math.floor(max(a[1], b[1]) / SC)) + 1):
                    idx[(ci, cj)].append((w, k))
    return idx

def seg_dist_m(lat, lon, a, b):
    kx = 111320.0 * math.cos(math.radians(lat)); ky = 110540.0
    ax, ay = (a[1] - lon) * kx, (a[0] - lat) * ky
    bx, by = (b[1] - lon) * kx, (b[0] - lat) * ky
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    u = 0.0 if L2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L2))
    return math.hypot(ax + u * dx, ay + u * dy)

def match(idx, lat, lon, brg):
    ci, cj = int(math.floor(lat / SC)), int(math.floor(lon / SC))
    best = None
    seen = set()
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for (w, k) in idx.get((ci + di, cj + dj), ()):
                if (w.id, k) in seen:
                    continue
                seen.add((w.id, k))
                a, b = w.pts[k], w.pts[k+1]
                d = seg_dist_m(lat, lon, a, b)
                if d > MAX_DIST_M:
                    continue
                sb = bearing(a[0], a[1], b[0], b[1])
                diff = abs((sb - brg + 180) % 360 - 180)
                fwd = diff < 60; bwd = diff > 120
                if not (fwd or bwd):
                    continue
                if w.ow == 1 and not fwd: continue
                if w.ow == -1 and not bwd: continue
                direction = "fwd" if fwd else "bwd"
                score = d + (0 if w.hw in ("motorway", "trunk", "primary") else 6)
                if best is None or score < best[0]:
                    best = (score, d, w, direction)
    return best


def compass(a, b, c, d):
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((bearing(a, b, c, d) + 22.5) // 45) % 8]

def mapshare(lat, lon):
    return f"https://www.tomtom.com/mapshare/tools/new/mapshare/#loc={float(lat):.5f},{float(lon):.5f},17"

SHORTLIST_GROUPS = {
    "A": "OSM 110 but every other pass ran 100/105",
    "B": "Car at standard 100 where OSM says 110",
    "C": "Car at standard 110 where OSM says 100",
}

def write_shortlist(sec_out, dis, log):
    """Points worth checking in TomTom MapShare -> fsd_osm_tomtom_shortlist.csv (also drawn on the map)."""
    import ast
    cross = {}
    cp = os.path.join(OUT_DIR, "fsd_105_cross_passes.csv")
    if os.path.exists(cp):
        cross = {(r["file"], r["start"]): r for r in csv.DictReader(open(cp))}
    rows = []
    for s in sec_out:
        if not s["verdict"].startswith("OSM says 110"):
            continue
        c = cross.get((s["file"], s["start"][:16]))
        if not c:
            continue
        ob = collections.Counter()
        for col in ("same_bands", "opp_bands"):
            try:
                for k, v in ast.literal_eval(c[col]).items():
                    ob[k] += v
            except Exception:
                pass
        hi = ob["110s"] + ob["115h"]; lo = ob["100s"] + ob["105h"]
        if lo > 0 and lo >= 2 * hi:
            rows.append(dict(group="A", file=s["file"], start=s["start"], end=s["end"], km=s["km"], road=s["roads"],
                             note=f"other passes: {hi} samples at 110/115, {lo} at 100/105",
                             lat0=s["lat0"], lon0=s["lon0"], lat1=s["lat1"], lon1=s["lon1"]))
    for d in dis:
        g = "B" if d["kind"].startswith("std 100") else "C" if d["kind"].startswith("std 110") else None
        if g:
            rows.append(dict(group=g, file=d["file"], start=d["start"], end=d["end"], km=d["km"], road=d["roads"],
                             note=d["kind"], lat0=d["lat0"], lon0=d["lon0"], lat1=d["lat1"], lon1=d["lon1"]))
    rows.sort(key=lambda r: (r["group"], r["start"]))
    n = collections.Counter()
    for r in rows:
        n[r["group"]] += 1
        r["id"] = f"{r['group']}{n[r['group']]}"
        r["heading"] = compass(float(r["lat0"]), float(r["lon0"]), float(r["lat1"]), float(r["lon1"]))
        r["mapshare_start"] = mapshare(r["lat0"], r["lon0"]); r["mapshare_end"] = mapshare(r["lat1"], r["lon1"])
    cols = ["id", "group", "file", "start", "end", "km", "road", "heading", "note", "lat0", "lon0", "lat1", "lon1", "mapshare_start", "mapshare_end"]
    with open(os.path.join(OUT_DIR, "fsd_osm_tomtom_shortlist.csv"), "w", newline="") as fh:
        w_ = csv.DictWriter(fh, fieldnames=cols); w_.writeheader(); w_.writerows(rows)
    log(f"TomTom shortlist: {len(rows)} points ({', '.join(f'{k}={v}' for k, v in sorted(n.items()))})")

PERCEIVED = {"100s": 100, "105h": 100, "110s": 110, "115h": 110}

def find_transitions(data, per_file, log):
    """Every point where the car's perceived limit flips 100 <-> 110 between two highway samples.
    The driver's observation: such flips happen where a speed sign is visible, i.e. at the start of a
    map section whose stored limit may be wrong. Writes fsd_transitions.csv with the OSM limit each side."""
    out = []
    for f, rows, sg, b in data:
        valid = [i for i in range(len(rows)) if rows[i][1] >= MIN_SPEED and PERCEIVED.get(b[i])]
        for a, c in zip(valid, valid[1:]):
            if c - a > 3 or (rows[c][0] - rows[a][0]).total_seconds() > 150:
                continue
            pa, pc = PERCEIVED[b[a]], PERCEIVED[b[c]]
            if pa == pc:
                continue
            ra, rc = per_file[f][a], per_file[f][c]
            ma = ra if ra and ra[0] != "nomatch" else None
            mc = rc if rc and rc[0] != "nomatch" else None
            oa = (ma[1] if ma else None); oc = (mc[1] if mc else None)
            kind = f"{pa}->{pc}"
            if oa and oc:
                if oa == oc == 110: cls = "OSM 110 both sides"
                elif oa == oc == 100: cls = "OSM 100 both sides"
                elif (oa, oc) == (pa, pc): cls = f"OSM changes {pa}->{pc} too"
                elif oa == oc: cls = f"OSM {oa} both sides"
                else: cls = f"OSM changes {oa}->{oc} (car {pa}->{pc})"
            else:
                cls = "no OSM limit on one side"
            la, lo_a, lc, lo_c = rows[a][2], rows[a][3], rows[c][2], rows[c][3]
            out.append(dict(
                file=os.path.basename(f), time=f"{rows[c][0]:%Y-%m-%d %H:%M:%S}", kind=kind, osm_class=cls,
                lat=round((la + lc) / 2, 6), lon=round((lo_a + lo_c) / 2, 6),
                lat_before=la, lon_before=lo_a, lat_after=lc, lon_after=lo_c,
                gap_km=round(rows[c][4] - rows[a][4], 2), gap_s=int((rows[c][0] - rows[a][0]).total_seconds()),
                speed_before=round(rows[a][1], 1), speed_after=round(rows[c][1], 1), band_before=b[a], band_after=b[c],
                osm_before=oa or "", osm_after=oc or "",
                road_before=(ma[0].name or ma[0].ref) if ma else "", road_after=(mc[0].name or mc[0].ref) if mc else "",
                way_before=ma[0].id if ma else "", way_after=mc[0].id if mc else "",
                heading=compass(la, lo_a, lc, lo_c), mapshare=mapshare((la + lc) / 2, (lo_a + lo_c) / 2)))
    if not out:
        log("no 100/110 flips found"); return out
    with open(os.path.join(OUT_DIR, "fsd_transitions.csv"), "w", newline="") as fh:
        w_ = csv.DictWriter(fh, fieldnames=list(out[0])); w_.writeheader(); w_.writerows(out)
    log(f"{len(out)} 100/110 flips in the car's perceived limit (median gap {sorted(o['gap_km'] for o in out)[len(out)//2]} km):")
    cc = collections.Counter((o["kind"], o["osm_class"]) for o in out)
    for (k, c), n in sorted(cc.items(), key=lambda kv: (kv[0][0], -kv[1])):
        log(f"  {k}  {c}: {n}")
    return out

def build_map(log):
    try:
        import build_fsd_map
        build_fsd_map.build(_DATA, SINCE, log)
    except Exception as e:   # the analysis CSVs are already written; the map is a view of them
        log(f"map not built: {e}")

# ---------------------------------------------------------------- main
def log(m):
    print(m, flush=True)

def main():
    since = datetime.datetime.strptime(SINCE, "%Y-%m-%d")
    files = []
    for f in sorted(_dp.list_csvs(DEEPDIVE_DIR)):
        m = re.search(r"drive_deepdive_(\d{4}-\d{2}-\d{2})_", os.path.basename(f))
        if m and datetime.datetime.strptime(m.group(1), "%Y-%m-%d") >= since:
            files.append(f)
    log(f"{len(files)} deep-dive files since {SINCE}")
    data = []
    tiles = set()
    for f in files:
        rows = load(f)
        if len(rows) < 5:
            continue
        sg, b = segs(rows)
        data.append((f, rows, sg, b))
        for r in rows:
            if r[1] >= MIN_SPEED:
                tiles.add((int(math.floor(r[2] / TILE)), int(math.floor(r[3] / TILE))))
    log(f"{len(tiles)} Overpass tiles of {TILE} degrees needed")
    todo = [t for t in sorted(tiles) if not os.path.exists(tile_path(*t))]
    if "--no-fetch" in sys.argv:
        log(f"--no-fetch: {len(todo)} tiles missing, those areas stay unmatched")
        todo = []
    log(f"{len(todo)} to fetch ({len(tiles) - len(todo)} cached)")
    budget = float(sys.argv[sys.argv.index("--budget") + 1]) if "--budget" in sys.argv else 1e9
    t_start = time.time()
    ok_n = fail_n = streak = 0
    for n, t in enumerate(todo):
        if time.time() - t_start > budget:
            log(f"  time budget reached - run again to continue (cached tiles are kept)")
            return
        if fetch_tile(*t, log, first=n):
            ok_n += 1; streak = 0
        else:
            fail_n += 1; streak += 1
        if (ok_n + fail_n) % 10 == 0:
            log(f"  tried {ok_n + fail_n}/{len(todo)}: {ok_n} saved, {fail_n} failed")
        if streak >= 5:
            log(f"  stopping: {streak} tiles in a row failed, so Overpass is refusing or throttling this connection.")
            log(f"  {ok_n} tiles saved this run. Wait 20-30 minutes and run the same command again - saved tiles are skipped.")
            return
        time.sleep(2.0)
    log(f"  done: {ok_n} saved, {fail_n} failed")
    if fail_n:
        log("  run again to retry the failed tiles")
        return
    if "--fetch-only" in sys.argv:
        return
    ways = load_ways(tiles)
    log(f"{len(ways)} OSM ways loaded")
    idx = build_index(ways)

    sample_rows = []
    per_file = {}
    for f, rows, sg, b in data:
        res = [None] * len(rows)
        for i, (t, sp, lat, lon, od) in enumerate(rows):
            if sp < MIN_SPEED:
                continue
            j = min(i + 1, len(rows) - 1); k = max(i - 1, 0)
            if hav_m(rows[k][2], rows[k][3], rows[j][2], rows[j][3]) < 15:
                continue
            brg = bearing(rows[k][2], rows[k][3], rows[j][2], rows[j][3])
            m = match(idx, lat, lon, brg)
            if not m:
                res[i] = ("nomatch", None)
                continue
            _, d, w, direction = m
            lim = w.fwd if direction == "fwd" else w.bwd
            res[i] = (w, lim, d, brg)
            sample_rows.append(dict(file=os.path.basename(f), time=f"{t:%Y-%m-%d %H:%M:%S}", lat=lat, lon=lon,
                                    speed=round(sp, 1), band=b[i], heading=round(brg), way=w.id, road=w.name or w.ref,
                                    ref=w.ref, highway=w.hw, osm_limit=lim if lim is not None else "",
                                    osm_variable="y" if w.var else "", osm_conditional=w.cond, dist_m=round(d, 1)))
        per_file[f] = res
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "fsd_osm_limit_samples.csv"), "w", newline="") as fh:
        w_ = csv.DictWriter(fh, fieldnames=list(sample_rows[0])); w_.writeheader(); w_.writerows(sample_rows)
    log(f"{len(sample_rows)} matched samples")

    # --- calibration: driver's STANDARD-mode speed vs OSM
    cal = collections.defaultdict(collections.Counter)
    for f, rows, sg, b in data:
        for i, r in enumerate(per_file[f]):
            if r is None or r[0] == "nomatch":
                cal[b[i]]["unmatched"] += 1 if r else 0
                continue
            lim = r[1]
            cal[b[i]][str(lim) if lim else "no limit"] += 1
    keys = sorted({k for c in cal.values() for k in c})
    with open(os.path.join(OUT_DIR, "fsd_osm_calibration.csv"), "w", newline="") as fh:
        w_ = csv.writer(fh); w_.writerow(["car_band"] + keys + ["matched_total"])
        for bd in ("100s", "105h", "110s", "115h", "x"):
            c = cal.get(bd, collections.Counter())
            w_.writerow([bd] + [c.get(k, 0) for k in keys] + [sum(v for k, v in c.items() if k != "unmatched")])
    log("calibration (car band -> OSM limit):")
    for bd in ("100s", "105h", "110s", "115h"):
        c = cal.get(bd, collections.Counter())
        tot = sum(v for k, v in c.items() if k != "unmatched")
        log(f"  {bd}: " + ", ".join(f"{k}={v} ({100*v/max(tot,1):.0f}%)" for k, v in sorted(c.items(), key=lambda kv: -kv[1]) if k != 'unmatched'))

    # --- 105 sections
    sec_out = []
    for f, rows, sg, b in data:
        for bd, i, j in sg:
            if bd != "105h" or j - i + 1 < 3:
                continue
            cnt = collections.Counter(); names = collections.Counter(); wid = collections.Counter(); var = 0
            for q in range(i, j + 1):
                r = per_file[f][q]
                if r is None or r[0] == "nomatch":
                    cnt["unmatched"] += 1; continue
                cnt[str(r[1]) if r[1] else "no limit"] += 1
                names[r[0].name or r[0].ref] += 1; wid[r[0].id] += 1
                var += 1 if r[0].var else 0
            n = j - i + 1
            matched = n - cnt["unmatched"]
            l110 = cnt.get("110", 0); l100 = cnt.get("100", 0)
            other = matched - l110 - l100
            if matched == 0: verdict = "no OSM road matched"
            elif l110 >= 0.6 * matched: verdict = "OSM says 110 - car ran 105 (candidate: car/map read it as 100)"
            elif l100 >= 0.6 * matched: verdict = "OSM says 100 - 105 is hurry in a genuine 100"
            elif l110 and l100: verdict = "OSM limit changes inside this section - check the change point"
            elif other >= 0.6 * matched: verdict = "OSM limit other/no limit tag (" + ",".join(k for k in cnt if k not in ("100","110","unmatched")) + ")"
            else: verdict = "mixed"
            a, z = rows[i], rows[j]
            sec_out.append(dict(file=os.path.basename(f), start=f"{a[0]:%Y-%m-%d %H:%M:%S}", end=f"{z[0]:%H:%M:%S}",
                                samples=n, km=round(z[4] - a[4], 1), lat0=a[2], lon0=a[3], lat1=z[2], lon1=z[3],
                                osm_110=l110, osm_100=l100, osm_other=other, unmatched=cnt["unmatched"],
                                variable_signs=var, roads="; ".join(f"{k} ({v})" for k, v in names.most_common(3) if k),
                                verdict=verdict))
    with open(os.path.join(OUT_DIR, "fsd_osm_105_sections.csv"), "w", newline="") as fh:
        w_ = csv.DictWriter(fh, fieldnames=list(sec_out[0])); w_.writeheader(); w_.writerows(sec_out)
    vc = collections.Counter(s["verdict"].split(" - ")[0].split(" (")[0] for s in sec_out)
    km = collections.defaultdict(float)
    for s in sec_out:
        km[s["verdict"].split(" - ")[0].split(" (")[0]] += s["km"]
    log(f"{len(sec_out)} 105-sections:")
    for k, v in vc.most_common():
        log(f"  {k}: {v} sections, {km[k]:.1f} km")

    # --- disagreements: runs where driver STANDARD-mode band contradicts OSM, plus OSM110 under 105h
    dis = []
    for f, rows, sg, b in data:
        run = None
        def close():
            nonlocal run
            if run and run["n"] >= 2:
                a, z = rows[run["i"]], rows[run["j"]]
                dis.append(dict(file=os.path.basename(f), start=f"{a[0]:%Y-%m-%d %H:%M:%S}", end=f"{z[0]:%H:%M:%S}",
                                km=round(z[4] - a[4], 1), samples=run["n"], kind=run["kind"],
                                car_band=run["bd"], osm_limit=run["lim"], roads="; ".join(sorted(run["roads"]))[:80],
                                lat0=a[2], lon0=a[3], lat1=z[2], lon1=z[3]))
            run = None
        for i, r in enumerate(per_file[f]):
            kind = None
            if r and r[0] != "nomatch" and r[1]:
                lim = r[1]; bd = b[i]
                if bd == "110s" and lim == 100: kind = "std 110 but OSM 100"
                elif bd == "100s" and lim == 110: kind = "std 100 but OSM 110"
                elif bd == "105h" and lim == 110: kind = "105 but OSM 110"
                elif bd == "115h" and lim == 100: kind = "115 but OSM 100"
            if kind and run and run["kind"] == kind and run["lim"] == r[1] and i - run["j"] <= 2:
                run["j"] = i; run["n"] += 1; run["roads"].add(r[0].name or r[0].ref)
            else:
                close()
                if kind:
                    run = dict(kind=kind, bd=b[i], lim=r[1], i=i, j=i, n=1, roads={r[0].name or r[0].ref})
        close()
    if dis:
        with open(os.path.join(OUT_DIR, "fsd_osm_disagreements.csv"), "w", newline="") as fh:
            w_ = csv.DictWriter(fh, fieldnames=list(dis[0])); w_.writeheader(); w_.writerows(dis)
    kc = collections.Counter(d["kind"] for d in dis)
    kk = collections.defaultdict(float)
    for d in dis: kk[d["kind"]] += d["km"]
    log(f"{len(dis)} disagreement stretches (>=2 samples):")
    for k, v in kc.most_common():
        log(f"  {k}: {v} stretches, {kk[k]:.1f} km")
    write_shortlist(sec_out, dis, log)
    find_transitions(data, per_file, log)
    build_map(log)

if __name__ == "__main__":
    main()
