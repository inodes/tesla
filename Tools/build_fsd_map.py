#!/usr/bin/env python3
"""Builds the interactive FSD highway map: <data>/drives/analysis/fsd_highway_map.html

Reads the deep-dive telemetry since --since plus the analysis CSVs written by
analyse_fsd_105.py and analyse_fsd_osm_limits.py (REQ-190 / REQ-191) and bakes them, with the
OpenStreetMap roads the car was matched to, into one self-contained HTML page
(Tools/lib/fsd_highway_map_template.html). Works without the OSM files (it then shows the speed
bands only). Called at the end of analyse_fsd_osm_limits.py; can also be run alone:
    build_fsd_map.py [--data-dir DIR] [--since YYYY-MM-DD]
"""
import bisect, calendar, collections, csv, datetime, glob, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
BC = {"100s": 0, "105h": 1, "110s": 2, "115h": 3, "x": 4}


def _simplify(pts, tol_m=3.0):
    """Douglas-Peucker on [lat, lon] points (metres tolerance)."""
    if len(pts) < 3:
        return pts
    ky = 110540.0
    kx = 111320.0 * math.cos(math.radians(pts[0][0]))
    P = [(p[1] * kx, p[0] * ky) for p in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = P[a]; bx, by = P[b]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        md, mi = -1.0, -1
        for i in range(a + 1, b):
            px, py = P[i]
            if L2 == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                u = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
                d = math.hypot(px - ax - u * dx, py - ay - u * dy)
            if d > md:
                md, mi = d, i
        if md > tol_m:
            keep[mi] = True
            stack += [(a, mi), (mi, b)]
    return [p for p, k in zip(pts, keep) if k]


def _limit(v):
    try:
        return int(str(v).strip())
    except Exception:
        return None


def _ts(s):
    """'YYYY-MM-DD HH:MM[:SS]' (Sydney clock, naive) -> seconds, treating the text as UTC so the page shows it unchanged."""
    fmt = "%Y-%m-%d %H:%M:%S" if len(s) > 16 else "%Y-%m-%d %H:%M"
    return calendar.timegm(datetime.datetime.strptime(s, fmt).timetuple())


sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import drive_paths as _dp   # REQ-193


def _read(path):
    return list(csv.DictReader(open(path, encoding="utf-8"))) if os.path.exists(path) else []


def build(data_dir, since, log=print):
    import analyse_fsd_osm_limits as O   # shared telemetry helpers (band, load, bearing, hav_m)
    out_dir = os.path.join(data_dir, "drives", "analysis")
    cache = os.path.join(out_dir, "osm_cache")
    files = [f for f in sorted(_dp.list_csvs(os.path.join(data_dir, "drives", "deepdive")))
             if os.path.basename(f)[15:25] >= since]

    # --- OSM match per telemetry sample
    osm = {}
    for r in _read(os.path.join(out_dir, "fsd_osm_limit_samples.csv")):
        osm[(r["file"], r["time"])] = (_limit(r["osm_limit"]) or -1, int(r["way"]), r["road"])
    way_ids = sorted({v[1] for v in osm.values()})
    widx = {w: i for i, w in enumerate(way_ids)}

    # --- samples and drives
    S, drives, fidx = [], [], {}
    per_drive_ts = []
    for f in files:
        rows = O.load(f)
        if len(rows) < 5:
            continue
        base = os.path.basename(f)
        d = dict(i=len(drives), name=base[15:31], date=f"{rows[0][0]:%a %d %b}", t0=f"{rows[0][0]:%H:%M}")
        drives.append(d); fidx[base] = d["i"]
        tss = []
        for k, (t, sp, lat, lon, od) in enumerate(rows):
            if sp < 5:
                continue
            pk, nk = max(k - 1, 0), min(k + 1, len(rows) - 1)
            hd = -1
            if O.hav_m(rows[pk][2], rows[pk][3], rows[nk][2], rows[nk][3]) >= 15:
                hd = round(O.bearing(rows[pk][2], rows[pk][3], rows[nk][2], rows[nk][3]))
            key = f"{t:%Y-%m-%d %H:%M:%S}"
            lim, way, _road = osm.get((base, key), (0, None, ""))
            ts = calendar.timegm(t.timetuple())
            tss.append((ts, len(S)))
            S.append([round(lat, 5), round(lon, 5), round(sp, 1), round(od, 1), ts, d["i"], BC[O.band(sp)],
                      lim, widx[way] if way in widx else -1, hd])
        per_drive_ts.append(tss)
    sample_at = {(S[i][5], S[i][4]): i for tss in per_drive_ts for _, i in tss}

    def span(file, s0, s1):
        """[first, last] sample index of a drive between two clock strings, or None."""
        di = fidx.get(file)
        if di is None:
            return None
        tss = per_drive_ts[di]
        a, b = _ts(s0), _ts(s1)
        keys = [x[0] for x in tss]
        i0 = bisect.bisect_left(keys, a); i1 = bisect.bisect_right(keys, b) - 1
        return [tss[i0][1], tss[i1][1]] if 0 <= i0 <= i1 < len(tss) else None

    # --- OSM way geometry (only the ways the car was matched to)
    W = []
    if way_ids and os.path.isdir(cache):
        found = {}
        want = set(way_ids)
        for p in glob.glob(os.path.join(cache, "*.json")):
            for el in json.load(open(p)).get("elements", []):
                if el.get("type") == "way" and el["id"] in want and el["id"] not in found and el.get("geometry"):
                    found[el["id"]] = el
        for w in way_ids:
            el = found.get(w)
            if not el:
                W.append(dict(id=w, n="", r="", h="", l=0, p=[])); continue
            t = el.get("tags", {})
            lim = _limit(t.get("maxspeed:forward")) or _limit(t.get("maxspeed")) or _limit(t.get("maxspeed:backward")) or 0
            pts = _simplify([[g["lat"], g["lon"]] for g in el["geometry"]])
            W.append(dict(id=w, n=t.get("name", ""), r=t.get("ref", ""), h=t.get("highway", ""), l=lim,
                          p=[[round(a, 5), round(b, 5)] for a, b in pts]))

    # --- 105 sections (+ what other passes did, + OSM mix)
    cross = {(r["file"], r["start"][:16]): r for r in _read(os.path.join(out_dir, "fsd_105_cross_passes.csv"))}
    osm_sec = {(r["file"], r["start"]): r for r in _read(os.path.join(out_dir, "fsd_osm_105_sections.csv"))}
    sl_rows = _read(os.path.join(out_dir, "fsd_osm_tomtom_shortlist.csv"))
    sl_by = {(r["file"], r["start"]): r["id"] for r in sl_rows}

    def parse(s):
        import ast
        try:
            return ast.literal_eval(s)
        except Exception:
            return {}
    secs = []
    for r in _read(os.path.join(out_dir, "fsd_105_sections.csv")):
        if r["start"][:10] < since:
            continue
        c = cross.get((r["file"], r["start"][:16]), {})
        sb, ob = parse(c.get("same_bands", "{}")), parse(c.get("opp_bands", "{}"))
        hi = sum(v for d in (sb, ob) for k, v in d.items() if k in ("110s", "115h"))
        lo = sum(v for d in (sb, ob) for k, v in d.items() if k in ("100s", "105h"))
        v = "no other pass" if hi + lo == 0 else "others at 110" if hi >= 2 * lo else "others at 100/105" if lo >= 2 * hi else "mixed"
        o = osm_sec.get((r["file"], r["start"]), {})
        verdict = o.get("verdict", "")
        sv = ("110" if verdict.startswith("OSM says 110") else "100" if verdict.startswith("OSM says 100")
              else "chg" if "changes" in verdict else "none" if o else "")
        secs.append(dict(
            start=r["start"], end=r["end"], km=float(r["km"]), mins=float(r["mins"]),
            lat=float(r["lat0"]), lon=float(r["lon0"]), lat1=float(r["lat1"]), lon1=float(r["lon1"]),
            v=v, hi=hi, lo=lo, sv=sv, o110=int(o.get("osm_110", 0) or 0), o100=int(o.get("osm_100", 0) or 0),
            oo=int(o.get("osm_other", 0) or 0), ou=int(o.get("unmatched", 0) or 0), roads=o.get("roads", ""),
            sl=sl_by.get((r["file"], r["start"]), ""),
            ix=span(r["file"], r["start"], r["start"][:11] + r["end"])))

    # --- shortlist
    shortlist = []
    for r in sl_rows:
        shortlist.append(dict(id=r["id"], g=r["group"], start=r["start"], end=r["end"], km=float(r["km"]), road=r["road"],
                              hd=r["heading"], note=r["note"], lat=float(r["lat0"]), lon=float(r["lon0"]),
                              lat1=float(r["lat1"]), lon1=float(r["lon1"]),
                              ix=span(r["file"], r["start"], r["start"][:11] + r["end"])))

    # --- flips in the car's perceived limit
    flips = []
    for r in _read(os.path.join(out_dir, "fsd_transitions.csv")):
        di = fidx.get(r["file"])
        i = sample_at.get((di, _ts(r["time"]))) if di is not None else None
        flips.append(dict(t=r["time"], k=r["kind"], c=r["osm_class"], lat=float(r["lat"]), lon=float(r["lon"]),
                          la=float(r["lat_before"]), oa=float(r["lon_before"]), lb=float(r["lat_after"]), ob=float(r["lon_after"]),
                          gap=float(r["gap_km"]), gs=int(r["gap_s"]), sa=float(r["speed_before"]), sb=float(r["speed_after"]),
                          osa=r["osm_before"], osb=r["osm_after"], ra=r["road_before"], rb=r["road_after"], hd=r["heading"], i=i))

    # --- highway stretches
    stretches = []
    for r in _read(os.path.join(out_dir, "fsd_highway_stretches.csv")):
        sp = span(r["file"], f"{r['date']} {r['t0']}", f"{r['date']} {r['t1']}")
        n110 = n100 = nm = 0
        if sp:
            for p in S[sp[0]:sp[1] + 1]:
                if p[2] >= 85 and p[7] != 0:
                    nm += 1
                    n110 += p[7] == 110
                    n100 += p[7] == 100
        stretches.append(dict(date=r["date"], t0=r["t0"], t1=r["t1"], km=float(r["km"]), frm=r["frm"], to=r["to"], avg=float(r["avg"]),
                              b=[int(r["p100s"]), int(r["p105h"]), int(r["p110s"]), int(r["p115h"]), int(r["other"])],
                              sw=int(r["switches"]), m=nm, o110=n110, o100=n100, ix=sp))

    # --- calibration (car band x OSM limit), highway samples only
    cal = [[0, 0, 0, 0] for _ in range(4)]   # OSM 110, 100, other limit, matched without a limit tag
    for p in S:
        if p[2] >= 85 and p[7] != 0 and p[6] < 4:
            cal[p[6]][0 if p[7] == 110 else 1 if p[7] == 100 else 3 if p[7] < 0 else 2] += 1

    # --- place names near the route
    subs = []
    gp = os.path.join(data_dir, "geo_cache.json")
    if os.path.exists(gp) and S:
        geo = json.load(open(gp))
        cells = {(round(s[0] / 0.2), round(s[1] / 0.2)) for s in S}
        for k, v in geo.items():
            try:
                la, lo = map(float, k.split(","))
            except Exception:
                continue
            if isinstance(v, dict) and v.get("suburb") and any((round(la / 0.2) + a, round(lo / 0.2) + b) in cells for a in (-1, 0, 1) for b in (-1, 0, 1)):
                subs.append([round(la, 3), round(lo, 3), v["suburb"]])

    km_by = collections.Counter()
    for s in secs:
        km_by[s["sv"]] += s["km"]
    hw = [p for p in S if p[2] >= 85]
    summary = dict(since=since, drives=len(drives), samples=len(S), hw=len(hw), matched=sum(1 for p in hw if p[7] != 0),
                   sec=len(secs), sec110=sum(1 for s in secs if s["sv"] == "110"), sec100=sum(1 for s in secs if s["sv"] == "100"),
                   km110=round(km_by["110"], 1), km100=round(km_by["100"], 1), kmall=round(sum(s["km"] for s in secs), 1),
                   hwkm=round(sum(x["km"] for x in stretches), 0), flips=len(flips),
                   flips_in110=sum(1 for f in flips if f["c"] == "OSM 110 both sides"),
                   flips_osm_changes=sum(1 for f in flips if f["c"].startswith("OSM changes") and "too" in f["c"]),
                   sl=len(shortlist), has_osm=bool(osm), built=datetime.datetime.now().strftime("%d %b %Y %H:%M"))
    data = dict(s=S, d=drives, g=subs, w=W, sec=secs, sl=shortlist, fl=flips, st=stretches, cal=cal, sum=summary)
    tpl = open(os.path.join(HERE, "lib", "fsd_highway_map_template.html"), encoding="utf-8").read()
    html = tpl.replace("/*DATA*/null", json.dumps(data, separators=(",", ":")))
    outp = os.path.join(out_dir, "fsd_highway_map.html")
    tmp = outp + ".tmp"
    open(tmp, "w", encoding="utf-8").write(html)
    os.replace(tmp, outp)
    log(f"map: {outp} ({len(S)} samples, {len(W)} OSM roads, {len(secs)} sections, {len(shortlist)} shortlist, {len(flips)} flips, {round(len(html) / 1024)} KB)")
    return outp


if __name__ == "__main__":
    dd = os.path.expanduser(os.environ.get("TESLA_DATA_DIR", "~/Library/Mobile Documents/com~apple~CloudDocs/Tesla"))
    if "--data-dir" in sys.argv:
        dd = os.path.expanduser(sys.argv[sys.argv.index("--data-dir") + 1])
    since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else "2026-10-01"
    build(dd, since)
