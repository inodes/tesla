"""app_places.py - places for tesla_app.py (REQ-200): who a location is, the Tesla Supercharger names, nearby OpenStreetMap places.

Sources (all in the Tesla data folder):
    places.json              your saved places (name -> lat, lon, radius_m, keywords, notes); the only file the app can change,
                             and only when you press Save / Delete on the Places pages (a backup copy is kept beside it first)
    charging_stations.json   every charger, all networks (written by sync_charger_all.sh); the Tesla ones carry the name shown on tesla.com ("Macquarie Park, NSW")
    station_facilities.json  OpenStreetMap places near each Tesla charger (already collected by the charger tools)
    places_osm_cache.json    OpenStreetMap places fetched from the app for the other places (written by the app)
"""
import bisect
import collections
import datetime
import json
import math
import os
import shutil
import sys
import threading
import time

import app_config

HERE = os.path.dirname(os.path.abspath(__file__))
_LOCK = threading.Lock()
_MEM = {}


def hav(lat1, lon1, lat2, lon2):
    """Metres between two points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(a))


def _mt(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _memo(name, deps, build):
    key = tuple(_mt(d) for d in deps)
    with _LOCK:
        h = _MEM.get(name)
        if h and h[0] == key:
            return h[1]
    v = build()
    with _LOCK:
        _MEM[name] = (key, v)
    return v


def spot_key(lat, lon):
    return "@%.5f,%.5f" % (lat, lon)


class Places:
    def __init__(self, data_dir):
        self.d = data_dir
        self.f_places = os.path.join(data_dir, "places.json")
        self.f_stations = os.path.join(data_dir, "charging_stations.json")
        self.f_fac = os.path.join(data_dir, "station_facilities.json")
        self.f_ignored = os.path.join(data_dir, "ignored_places.json")
        self.f_cache = os.path.join(data_dir, "places_osm_cache.json")

    def deps(self):
        return [self.f_places, self.f_stations]

    # ---- sources ------------------------------------------------------------------------------------------------
    def defs(self):
        """Saved places as a list of dicts."""
        def build():
            j = _load(self.f_places) or {}
            out = []
            for n, v in j.items():
                if isinstance(v, dict) and v.get("lat") is not None and v.get("lon") is not None:
                    out.append(dict(name=n, lat=float(v["lat"]), lon=float(v["lon"]), radius=float(v.get("radius_m") or 150),
                                    keywords=v.get("keywords") or [], notes=v.get("notes") or ""))
            return out
        return _memo("defs", [self.f_places], build)

    def stations(self):
        """Tesla Superchargers with their tesla.com names."""
        def build():
            j = _load(self.f_stations) or {}
            out = []
            for v in (j.get("stations") or {}).values():
                if v.get("network") != "Tesla":
                    continue
                loc, hw = v.get("location") or {}, v.get("hardware") or {}
                if loc.get("lat") is None or loc.get("lon") is None:
                    continue
                out.append(dict(name=v.get("name") or v.get("id"), lat=float(loc["lat"]), lon=float(loc["lon"]), addr=loc.get("address") or "",
                                stalls=hw.get("stalls"), kw=hw.get("max_power_kw"), tier=hw.get("tier") or "", hours=(v.get("access") or {}).get("hours") or "",
                                note=((v.get("access") or {}).get("description") or "").split("\n", 1)[-1].strip()))
            return out
        return _memo("stations", [self.f_stations], build)

    def chargers(self):
        """Every charger of every network, with its key (s:<tesla.com name> for Tesla, c:<id> for the rest)."""
        def build():
            j = _load(self.f_stations) or {}
            out = []
            for sid, v in (j.get("stations") or {}).items():
                loc, hw, cp, ac = v.get("location") or {}, v.get("hardware") or {}, v.get("compatibility") or {}, v.get("access") or {}
                if loc.get("lat") is None or loc.get("lon") is None:
                    continue
                net, nm = v.get("network") or "", v.get("name") or v.get("id") or sid
                cid = v.get("id") or sid
                out.append(dict(key=("s:" + nm) if net == "Tesla" else "c:" + cid, id=cid, name=nm, network=net, addr=loc.get("address") or "", suburb=loc.get("suburb") or "",
                                state=loc.get("state") or "", lat=float(loc["lat"]), lon=float(loc["lon"]), stalls=hw.get("stalls"), kw=hw.get("max_power_kw") or 0,
                                tier=hw.get("speed_tier") or "", conn=", ".join(hw.get("connector_types") or []), tesla_only=bool(cp.get("tesla_only")),
                                access=cp.get("access") or "", hours=ac.get("hours") or "", note=(ac.get("description") or "").split("\n", 1)[-1].strip()))
            return dict(rows=out, updated=j.get("updated_at") or "", sources=j.get("sources") or [], by_id={x["id"]: x for x in out})
        return _memo("chargers", [self.f_stations], build)

    def charger_at(self, lat, lon, within=150):
        """The nearest charger of any network within a distance (metres)."""
        dl = within / 111000.0
        dn = dl / max(0.2, math.cos(math.radians(lat)))
        best = None
        for c in self.chargers()["rows"]:
            if abs(c["lat"] - lat) > dl or abs(c["lon"] - lon) > dn:
                continue
            d = hav(lat, lon, c["lat"], c["lon"])
            if d <= within and (best is None or d < best[0]):
                best = (d, c)
        return best[1] if best else None

    # ---- who is at this spot --------------------------------------------------------------------------------------
    def saved_at(self, lat, lon):
        best = None
        for p in self.defs():
            d = hav(lat, lon, p["lat"], p["lon"])
            r = max(p["radius"], 40)
            if d <= r and (best is None or d / r < best[0]):
                best = (d / r, p)
        return best[1] if best else None

    def station_at(self, lat, lon, within=400):
        best = None
        for s in self.stations():
            d = hav(lat, lon, s["lat"], s["lon"])
            if d <= within and (best is None or d < best[0]):
                best = (d, s)
        return best[1] if best else None

    def label(self, lat, lon, supercharger=False, fallback="", charger=False):
        """(name, key) for a spot: a saved place first, then (for a Supercharger) the tesla.com station name, then (for any charge, charger=True)
        the nearest known charger of any network, then the fallback text."""
        if lat is not None and lon is not None:
            p = self.saved_at(lat, lon)
            if p:
                return p["name"], "p:" + p["name"]
            if supercharger:
                s = self.station_at(lat, lon)
                if s:
                    return s["name"], "s:" + s["name"]
            if charger:
                c = self.charger_at(lat, lon, 400 if supercharger else 120)
                if c:
                    return c["name"], c["key"]
        if fallback:
            low = fallback.strip().lower()
            for p in self.defs():
                if p["name"].lower() == low:
                    return p["name"], "p:" + p["name"]
        if lat is not None and lon is not None:
            return fallback, spot_key(lat, lon)
        return fallback, ""

    # ---- lists ---------------------------------------------------------------------------------------------------
    def _events(self, S):
        """Every drive arrival, charge and idle with the place it belongs to."""
        ev = []
        for c in S.charges():
            ev.append(dict(k="charge", id=c["id"], t0=c["t0"], day=c["day"], hm=c["hm"], min=c["min"], kwh=c["kwh"], cost=c["cost"], key=c.get("pkey") or "", parts=c.get("parts"), net=c.get("net") or "", kind=c.get("kind"), addr=c.get("addr") or "", lat=c.get("lat"), lon=c.get("lon"), pname=c.get("place") or ""))
        for i in S.idles():
            ev.append(dict(k="idle", id=i["id"], t0=i["t0"], day=i["day"], hm=i["hm"], min=i["min"], kwh=i["kwh"], key=i.get("pkey") or ""))
        idles = {}
        for i in S.idles():
            idles.setdefault(i.get("pkey") or "", []).append((i["t0"], i["t1"]))
        chg = {}
        for c in S.charges():
            chg.setdefault(c.get("pkey") or "", []).append((c["t0"], c.get("t1") or c["t0"] + 60 * (c.get("min") or 0)))
        drv = sorted(S.drives(), key=lambda d: d["t0"])
        real = [d for d in drv if not d.get("repark")]
        reparks = [d for d in drv if d.get("repark")]
        rep_t = [d["t0"] for d in reparks]
        # a drive that only shuffles the car (re-park) is not an arrival; it belongs to the stay it happens in: from arriving by a real
        # drive to the next real drive leaving. The stay is the parked period when Tessie has no parked record (idle) or charge for it.
        stay_of = {}
        for n, d in enumerate(real):
            key = d.get("pkey1") or ""
            nxt = real[n + 1]["t0"] if n + 1 < len(real) else None
            t0 = d["t1"]
            lo = bisect.bisect_left(rep_t, t0)
            hi = len(rep_t) if nxt is None else bisect.bisect_left(rep_t, nxt)
            reps = [r for r in reparks[lo:hi] if (r.get("pkey1") or "") == key] if key else []
            for r in reps:
                stay_of[r["id"]] = True
            if not key or nxt is None or nxt - t0 < app_config.get("places.stay_min_seconds"):
                continue
            if any(a < nxt and b > t0 for a, b in idles.get(key, [])) or any(a < nxt and b > t0 for a, b in chg.get(key, [])):
                continue
            dt = datetime.datetime.strptime(d["day"] + " " + d["hm"], "%Y-%m-%d %H:%M") + datetime.timedelta(seconds=t0 - d["t0"])
            ev.append(dict(k="stay", id="stay%d" % t0, t0=t0, day=dt.strftime("%Y-%m-%d"), hm=dt.strftime("%H:%M"), min=round((nxt - t0) / 60.0, 1), key=key, reparks=len(reps), kwh=0))
        for d in drv:
            ev.append(dict(k="repark" if d.get("repark") else "drive", id=d["id"], t0=d["t1"], day=d["day"], hm=d["hm"], min=d["min"], km=d["km"], key=d.get("pkey1") or "", frm=d.get("frm"), in_stay=bool(stay_of.get(d["id"]))))
        return ev

    def listing(self, S):
        def build():
            ev = self._events(S)
            by = collections.defaultdict(list)
            for e in ev:
                if e["key"]:
                    by[e["key"]].append(e)

            def stat(key):
                es = by.get(key, [])
                last = max((e["t0"] for e in es), default=0)
                return dict(charges=sum(e["k"] == "charge" for e in es), idles=sum(e["k"] in ("idle", "stay") for e in es), drives=sum(e["k"] == "drive" for e in es), reparks=sum(e["k"] == "repark" for e in es),
                            kwh=round(sum(e.get("kwh") or 0 for e in es if e["k"] == "charge"), 1), cost=round(sum(e.get("cost") or 0 for e in es if e["k"] == "charge"), 2),
                            last=datetime.datetime.fromtimestamp(last).strftime("%Y-%m-%d") if last else "", t_last=last)
            saved = [dict(key="p:" + p["name"], name=p["name"], lat=p["lat"], lon=p["lon"], radius=p["radius"], notes=p["notes"], **stat("p:" + p["name"])) for p in self.defs()]
            sc = [dict(key="s:" + s["name"], name=s["name"], lat=s["lat"], lon=s["lon"], addr=s["addr"], stalls=s["stalls"], kw=s["kw"], tier=s["tier"], **stat("s:" + s["name"])) for s in self.stations()]
            return dict(saved=saved, superchargers=sc, found=self.discover(S, ev), charger_count=len(self.chargers()["rows"]))
        return _memo("listing", self.deps() + [os.path.join(S.d, "drives", "drives_master.csv"), os.path.join(S.d, "charges", "charges_master.csv"), os.path.join(S.d, "charges", "app_chargebook.json"), os.path.join(S.d, "idles", "idles_master.csv"), self.f_ignored], build)

    def charger_listing(self, S):
        """Every known charger with how often you charged there (a charge is matched to a charger by its location)."""
        def build():
            by = collections.defaultdict(list)
            for e in self._events(S):
                if e["k"] == "charge" and e["key"]:
                    by[e["key"]].append(e)
            C = self.chargers()
            rows = []
            for c in C["rows"]:
                es = by.get(c["key"], [])
                last = max((e["t0"] for e in es), default=0)
                r = {k: c[k] for k in ("key", "name", "network", "addr", "suburb", "state", "lat", "lon", "stalls", "kw", "tier", "conn", "access", "tesla_only")}
                r.update(charges=len(es), kwh=round(sum(e.get("kwh") or 0 for e in es), 1), cost=round(sum(e.get("cost") or 0 for e in es), 2),
                         last=datetime.datetime.fromtimestamp(last).strftime("%Y-%m-%d") if last else "")
                rows.append(r)
            known = {c["key"] for c in C["rows"]}
            # charging you have done at a place that is not a registry charger (a saved place such as Home, a friend's house, a
            # business with a charger the networks do not list): shown too, so "used" is every place you have charged
            for key, es in by.items():
                if key in known:
                    continue
                es.sort(key=lambda e: e["t0"])
                cn = collections.Counter(e.get("net") or "" for e in es).most_common(1)[0][0]
                cn = "Tesla" if cn == "Tesla Supercharger" else cn
                kind = es[-1].get("kind")
                last = max(e["t0"] for e in es)
                rows.append(dict(key=key, name=es[-1].get("pname") or key[2:], network=cn or ("AC / home" if kind == "ac" else "DC fast"), addr=es[-1].get("addr") or "", suburb="", state="",
                                 lat=es[-1].get("lat"), lon=es[-1].get("lon"), stalls=None, kw=None, tier="saved place" if key.startswith("p:") else ("AC" if kind == "ac" else "DC fast"),
                                 conn="", access="", tesla_only=False, charges=len(es), kwh=round(sum(e.get("kwh") or 0 for e in es), 1), cost=round(sum(e.get("cost") or 0 for e in es), 2),
                                 last=datetime.datetime.fromtimestamp(last).strftime("%Y-%m-%d")))
            nets = collections.Counter(r["network"] for r in rows)
            return dict(rows=rows, networks=sorted(nets.items(), key=lambda x: -x[1]), updated=C["updated"], sources=C["sources"])
        return _memo("charger_listing", self.deps() + [os.path.join(S.d, "charges", "charges_master.csv"), os.path.join(S.d, "charges", "app_chargebook.json"), self.f_places], build)

    def discover(self, S, ev=None):
        """Stops you return to that are not a saved place and not a charger (the places-manager 'review drives' list)."""
        ign = _load(self.f_ignored) or []
        ign = [(e.get("center_lat"), e.get("center_lon"), e.get("radius_m") or 100) for e in ign if isinstance(e, dict) and e.get("center_lat") is not None]
        chg = [(c["lat"], c["lon"]) for c in S.charges() if c.get("lat") is not None and c.get("lon") is not None]
        cells = collections.defaultdict(list)
        pts = [(d["lat1"], d["lon1"], d["day"], d["t1"], d.get("addr1") or "") for d in S.drives() if d.get("lat1") is not None and not d.get("repark") and not d.get("pkey1", "").startswith(("p:", "s:"))]
        pts += [(i["lat"], i["lon"], i["day"], i["t0"], i.get("addr") or "") for i in S.idles() if i.get("lat") is not None and not (i.get("pkey") or "").startswith(("p:", "s:"))]
        for lat, lon, day, t, addr in pts:
            cells[(round(lat / 0.002), round(lon / 0.002))].append((lat, lon, day, t, addr))
        out = []
        for g in cells.values():
            days = {x[2] for x in g}
            if len(days) < 3:
                continue
            la, lo = sum(x[0] for x in g) / len(g), sum(x[1] for x in g) / len(g)
            if any(hav(la, lo, a, b) < 200 for a, b in chg) or any(hav(la, lo, a, b) <= r for a, b, r in ign):
                continue
            addr = collections.Counter(x[4] for x in g if x[4]).most_common(1)
            out.append(dict(key=spot_key(la, lo), lat=la, lon=lo, visits=len(days), last=max(x[2] for x in g), addr=addr[0][0] if addr else ""))
        out.sort(key=lambda x: -x["visits"])
        return out[:60]

    # ---- one place -----------------------------------------------------------------------------------------------
    def spot_keywords(self, S, lat, lon, name=""):
        """Keywords for a place that is not saved yet, as places_manager.py suggests them: the street of the address (and its abbreviation) plus the
        place name. The address is the one your own drives / charges already recorded there; failing that, Nominatim is asked."""
        sys.path.insert(0, os.path.dirname(HERE))
        import places_manager as PM
        addrs = collections.Counter()
        for d in S.drives():
            if d.get("addr1") and d.get("lat1") is not None and hav(lat, lon, d["lat1"], d["lon1"]) <= 150:
                addrs[d["addr1"]] += 1
        for c in S.charges():
            if c.get("addr") and c.get("lat") is not None and hav(lat, lon, c["lat"], c["lon"]) <= 150:
                addrs[c["addr"]] += 1
        addr = addrs.most_common(1)[0][0] if addrs else ""
        if not addr:
            try:
                g = PM.reverse_geocode(lat, lon) or {}
                a = g.get("address") or {}
                street = ((a.get("house_number") or "") + " " + (a.get("road") or "")).strip()
                addr = ", ".join(x for x in (street, a.get("suburb") or a.get("city") or a.get("town") or "") if x)
            except Exception:
                addr = ""
        try:
            return PM.suggest_keywords(addr, name), addr
        except Exception:
            return [], addr

    def detail(self, S, key, name=""):
        info = None
        if key.startswith("p:"):
            p = next((x for x in self.defs() if x["name"] == key[2:]), None)
            if p:
                info = dict(kind="saved", name=p["name"], lat=p["lat"], lon=p["lon"], radius=p["radius"], keywords=p["keywords"], notes=p["notes"])
        elif key.startswith("s:"):
            s = next((x for x in self.stations() if x["name"] == key[2:]), None)
            if s:
                info = dict(kind="supercharger", name=s["name"], lat=s["lat"], lon=s["lon"], radius=250, addr=s["addr"], stalls=s["stalls"], kw=s["kw"], tier=s["tier"], hours=s["hours"], notes=s["note"])
        elif key.startswith("c:"):
            c = self.chargers()["by_id"].get(key[2:])
            if c:
                info = dict(kind="charger", name=c["name"], lat=c["lat"], lon=c["lon"], radius=150, addr=c["addr"], network=c["network"], stalls=c["stalls"], kw=c["kw"],
                            tier=c["tier"], conn=c["conn"], hours=c["hours"], access=c["access"], notes=c["note"])
        elif key.startswith("@"):
            try:
                la, lo = [float(x) for x in key[1:].split(",")]
            except ValueError:
                return None
            sp = self.saved_at(la, lo)
            if sp:
                return self.detail(S, "p:" + sp["name"])
            kws, addr = self.spot_keywords(S, la, lo, name)
            info = dict(kind="spot", name="Unnamed place", lat=la, lon=lo, radius=150, keywords=kws, notes="", addr=addr)
        if not info:
            return None
        info["key"] = key
        if key.startswith("@"):
            visits = []
            for c in S.charges():
                if c.get("lat") is not None and hav(info["lat"], info["lon"], c["lat"], c["lon"]) <= 150:
                    visits.append(dict(k="charge", id=c["id"], t0=c["t0"], day=c["day"], hm=c["hm"], min=c["min"], kwh=c["kwh"], cost=c["cost"], parts=c.get("parts")))
            for i in S.idles():
                if i.get("lat") is not None and hav(info["lat"], info["lon"], i["lat"], i["lon"]) <= 150:
                    visits.append(dict(k="idle", id=i["id"], t0=i["t0"], day=i["day"], hm=i["hm"], min=i["min"], kwh=i["kwh"]))
            for d in S.drives():
                if d.get("lat1") is not None and not d.get("repark") and hav(info["lat"], info["lon"], d["lat1"], d["lon1"]) <= 150:
                    visits.append(dict(k="drive", id=d["id"], t0=d["t1"], day=d["day"], hm=d["hm"], min=d["min"], km=d["km"], frm=d.get("frm")))
        else:
            visits = [e for e in self._events(S) if e["key"] == key]
        info["visits"] = sorted(visits, key=lambda e: -e["t0"])[:400]
        info["nvisits"] = len(visits)
        return info

    # ---- OpenStreetMap places nearby --------------------------------------------------------------------------------
    def _cache(self):
        return _load(self.f_cache) or {}

    def _facilities(self, name):
        """The OSM places already collected around a Tesla charger (station_facilities.json), as a flat list."""
        j = _memo("fac", [self.f_fac], lambda: _load(self.f_fac) or {})
        rec = j.get("Tesla_" + name)
        if not rec:
            return None
        out = []
        for grp, items in (rec.get("categories") or {}).items():
            for t in items:
                nm, _, rest = t.rpartition(" (")
                try:
                    dist = float(rest.rstrip("m)"))
                except ValueError:
                    nm, dist = t, None
                out.append(dict(name=nm or t, group=grp, category=grp, dist_m=dist))
        for t in rec.get("doorstep_100m") or []:
            nm, _, rest = t.rpartition(" (")
            if not any(o["name"] == nm for o in out):
                try:
                    out.append(dict(name=nm, group="Doorstep", category="Within 100 m", dist_m=float(rest.rstrip("m)"))))
                except ValueError:
                    pass
        out.sort(key=lambda o: (o["dist_m"] is None, o["dist_m"] or 0))
        return dict(items=out, source="station_facilities.json", fetched=rec.get("last_updated"), radius=rec.get("radius_m"))

    def _save_cache(self, cache):
        try:
            tmp = self.f_cache + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
            os.replace(tmp, self.f_cache)
        except OSError:
            pass

    def _photon(self, PM, lat, lon, radius):
        """The nearest named OpenStreetMap features around a point from photon.komoot.io (an independent server over the same data)."""
        import urllib.request
        url = "%s?lat=%s&lon=%s&radius=%s&limit=%d&lang=en" % (app_config.get("nearby.photon_url"), lat, lon, round(radius / 1000.0, 3), int(app_config.get("nearby.photon_limit")))
        req = urllib.request.Request(url, headers={"User-Agent": PM.USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            feats = json.loads(r.read().decode("utf-8")).get("features", [])
        out, seen = [], set()
        for f in feats:
            pr = f.get("properties") or {}
            name = (pr.get("name") or "").strip()
            k, v = pr.get("osm_key") or "", pr.get("osm_value") or ""
            if not name or k in ("highway", "route", "boundary", "place") or v == "charging_station" or "supercharger" in name.lower():
                continue
            lo, la = f["geometry"]["coordinates"][:2]
            d = hav(lat, lon, la, lo)
            if d > radius or (name.lower(), round(la, 3), round(lo, 3)) in seen:
                continue
            seen.add((name.lower(), round(la, 3), round(lo, 3)))
            grp, emo = PM.classify_poi_group({k: v})
            out.append(dict(name=name, group=grp, emoji=emo, category=(k if v == "yes" else v).replace("_", " ").title(), dist_m=round(d, 1), lat=la, lon=lo))
        out.sort(key=lambda o: o["dist_m"])
        return out

    def osm(self, key, lat, lon, radius=300, refresh=False):
        """OSM places around a spot: cached; a Tesla charger uses the facilities already collected; otherwise ask Overpass (and cache)."""
        ck = "%s|%d" % (key, radius)
        cache = self._cache()
        if not refresh:
            if ck in cache:
                return dict(cache[ck], cached=True)
            if key.startswith("s:"):
                f = self._facilities(key[2:])
                if f:
                    return dict(f, cached=True)
        sys.path.insert(0, os.path.dirname(HERE))
        import places_manager as PM
        PM.OVERPASS_ERROR = ""
        res = PM.query_overpass_pois(lat, lon, radius)
        if not res and PM.OVERPASS_ERROR:
            # Overpass did not answer: ask Photon (komoot's search over the same OpenStreetMap data) for the nearest places instead. If that fails too, say so
            # (and keep nothing) rather than claim the street is empty.
            why = PM.OVERPASS_ERROR
            try:
                items = self._photon(PM, lat, lon, radius)
            except Exception as ex:
                return dict(items=[], error=why + " | Photon: " + str(ex)[:120], source="OpenStreetMap", radius=radius, cached=False)
            rec = dict(items=items, source="OpenStreetMap via Photon (Overpass did not answer, so this is the nearest 50 features, not everything)", fetched=datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), radius=radius, note=why)
            if items:
                cache[ck] = rec
                self._save_cache(cache)
            return dict(rec, cached=False)
        items = [dict(name=r["name"], group=r.get("group") or "Other", emoji=r.get("emoji") or "", category=r.get("category") or "", dist_m=r.get("dist_m"), lat=r.get("lat"), lon=r.get("lon")) for r in res]
        rec = dict(items=items, source="OpenStreetMap (Overpass)", fetched=datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), radius=radius)
        if items:       # an empty answer is more likely a busy server than an empty street, so it is not kept
            cache[ck] = rec
            try:
                tmp = self.f_cache + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(cache, f, ensure_ascii=False)
                os.replace(tmp, self.f_cache)
            except OSError:
                pass
        return dict(rec, cached=False)

    # ---- editing places.json ---------------------------------------------------------------------------------------
    def save(self, body):
        """Create / change a saved place. Keeps a dated backup of places.json first."""
        sys.path.insert(0, os.path.dirname(HERE))
        import places_manager as PM
        name = (body.get("name") or "").strip()
        old = (body.get("old_name") or "").strip()
        ok, why = PM.validate_place_name(name)
        if not ok:
            return dict(ok=False, error=why)
        try:
            lat, lon = float(body["lat"]), float(body["lon"])
            radius = int(float(body.get("radius_m") or 150))
        except (KeyError, TypeError, ValueError):
            return dict(ok=False, error="latitude, longitude and radius must be numbers")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or not (10 <= radius <= 5000):
            return dict(ok=False, error="coordinates or radius out of range (radius 10 to 5000 m)")
        places = PM.load_places(self.f_places)
        if name != old and name in places:
            return dict(ok=False, error="a place called '%s' already exists" % name)
        entry = dict(places.get(old) or places.get(name) or {})
        kw = body.get("keywords")
        if isinstance(kw, str):
            kw = [k.strip() for k in kw.replace("\n", ",").split(",") if k.strip()]
        entry.update(lat=round(lat, 5), lon=round(lon, 5), radius_m=radius, keywords=kw if kw is not None else entry.get("keywords", []), notes=(body.get("notes") or "").strip())
        if old and old != name:
            places.pop(old, None)
        places[name] = entry
        self._backup()
        PM.save_places(places, self.f_places)
        return dict(ok=True, key="p:" + name)

    def delete(self, name):
        sys.path.insert(0, os.path.dirname(HERE))
        import places_manager as PM
        places = PM.load_places(self.f_places)
        if name not in places:
            return dict(ok=False, error="no such place")
        self._backup()
        places.pop(name)
        PM.save_places(places, self.f_places)
        return dict(ok=True)

    def _backup(self):
        if os.path.isfile(self.f_places):
            shutil.copy2(self.f_places, "%s.bak-%s" % (self.f_places, time.strftime("%Y%m%d-%H%M%S")))
        old = sorted(x for x in os.listdir(self.d) if x.startswith("places.json.bak-"))
        for x in old[:-10]:                     # keep the ten newest
            try:
                os.remove(os.path.join(self.d, x))
            except OSError:
                pass
