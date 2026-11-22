"""app_places.py - places for tesla_app.py (REQ-200): who a location is, the Tesla Supercharger names, nearby OpenStreetMap places.

Sources (all in the Tesla data folder):
    places.json              your saved places (name -> lat, lon, radius_m, keywords, notes); the only file the app can change,
                             and only when you press Save / Delete on the Places pages (a backup copy is kept beside it first)
    charging_stations.json   every charger, all networks (written by sync_charger_all.sh); the Tesla ones carry the name shown on tesla.com ("Macquarie Park, NSW")
    station_facilities.json  OpenStreetMap places near each Tesla charger (already collected by the charger tools)
    places_osm_cache.json    OpenStreetMap places fetched from the app for the other places (written by the app)
"""
import bisect
import clip_clock as CC
import collections
import datetime
import json
import math
import os
import re
import shutil
import threading
import time

import app_config
import charger_site as CS

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
        self.f_personal = os.path.join(data_dir, "chargers", "personal_chargers.json")
        self.f_busy = os.path.join(data_dir, "chargers", "tesla_busy_times.json")
        self.f_ignored = os.path.join(data_dir, "ignored_places.json")
        self.f_cache = os.path.join(data_dir, "places_osm_cache.json")
        self.f_addr = os.path.join(data_dir, "place_address_cache.json")

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
                                    keywords=v.get("keywords") or [], notes=v.get("notes") or "",
                                    chargers=[c for c in (v.get("chargers") or []) if isinstance(c, dict)], home=(v.get("type") == "home")))
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

    @staticmethod
    def _plan(pp):
        """The plan name a retailer's tariff-rates file declares ("Solar VIP")."""
        try:
            with open(os.path.join(pp.resolve_data_home(), pp.tariff_rates_filename()), encoding="utf-8") as f:
                return (json.load(f).get("plan") or "").strip()
        except Exception:
            return ""

    def site(self, key):
        """Everything known about a charging site (charger_site.site_info) from its charging_stations.json record, or None."""
        def build():
            j = _load(self.f_stations) or {}
            by_id, by_name = {}, {}
            for sid, v in (j.get("stations") or {}).items():
                by_id[v.get("id") or sid] = v
                if v.get("network") == "Tesla":
                    by_name[v.get("name") or v.get("id") or sid] = v
            return by_id, by_name
        by_id, by_name = _memo("raw", [self.f_stations], build)
        if key.startswith("p:"):
            own = _load(self.f_personal) or {}
            rec = own.get(key[2:])
            if not isinstance(rec, dict):
                return None
            agl = None
            if (rec.get("type") or "").lower() == "home":     # a home charge costs the household tariff (as the scripts price it), not the charger's own rate card
                try:
                    from providers.power import get_power_provider
                    pp = get_power_provider()
                    agl = CS.agl_tariff(pp.load_rate_periods(), pp.gst_multiplier(), datetime.date.today().isoformat(), getattr(pp, "display_name", "") or "", self._plan(pp))
                except Exception:
                    agl = None
            return CS.personal_info(key[2:], rec, agl)
        rec = by_name.get(key[2:]) if key.startswith("s:") else by_id.get(key[2:]) if key.startswith("c:") else None
        if not rec:
            return None
        busy = _memo("busy", [self.f_busy], lambda: _load(self.f_busy) or {})
        fac = _memo("fac", [self.f_fac], lambda: _load(self.f_fac) or {})
        return CS.site_info(rec, busy, fac)

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
        defs = self.defs()
        if defs is not getattr(self, "_sa_defs", None):      # the saved places changed: forget what was found
            self._sa_defs, self._sa = defs, {}
        k = (round(lat, 5), round(lon, 5))
        if k in self._sa:
            return self._sa[k]
        best = None
        for p in defs:
            d = hav(lat, lon, p["lat"], p["lon"])
            r = max(p["radius"], 40)
            if d <= r and (best is None or d / r < best[0]):
                best = (d / r, p)
        self._sa[k] = best[1] if best else None
        return self._sa[k]

    def station_at(self, lat, lon, within=400):
        best = None
        for s in self.stations():
            d = hav(lat, lon, s["lat"], s["lon"])
            if d <= within and (best is None or d < best[0]):
                best = (d, s)
        return best[1] if best else None

    def label(self, lat, lon, supercharger=False, fallback="", charger=False):
        """(name, key) for a spot: (for a Supercharger) the tesla.com station name - always, even where a saved place of yours covers the spot
        (a shopping centre's car park) - otherwise a saved place, then (for any charge, charger=True) the nearest known charger of any network,
        then the fallback text."""
        if lat is not None and lon is not None:
            if supercharger:
                s = self.station_at(lat, lon)
                if s:
                    return s["name"], "s:" + s["name"]
            p = self.saved_at(lat, lon)
            if p:
                return p["name"], "p:" + p["name"]
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
            lt = CC.local_iso(t0)                           # t0 is UTC; the local clock is only for showing
            ev.append(dict(k="stay", id="stay%d" % t0, t0=t0, day=lt[:10], hm=lt[11:16], min=round((nxt - t0) / 60.0, 1), key=key, reparks=len(reps), kwh=0))
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
                            last=CC.local_iso(last)[:10] if last else "", t_last=last)
            saved = [dict(key="p:" + p["name"], name=p["name"], lat=p["lat"], lon=p["lon"], radius=p["radius"], notes=p["notes"], **stat("p:" + p["name"])) for p in self.defs()]
            sc = [dict(key="s:" + s["name"], name=s["name"], lat=s["lat"], lon=s["lon"], addr=s["addr"], stalls=s["stalls"], kw=s["kw"], tier=s["tier"], hours=s["hours"], **stat("s:" + s["name"])) for s in self.stations()]
            return dict(saved=saved, superchargers=sc, found=[], charger_count=len(self.chargers()["rows"]))
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
                r = {k: c[k] for k in ("key", "name", "network", "addr", "suburb", "state", "lat", "lon", "stalls", "kw", "tier", "conn", "access", "tesla_only", "hours")}
                r.update(charges=len(es), kwh=round(sum(e.get("kwh") or 0 for e in es), 1), cost=round(sum(e.get("cost") or 0 for e in es), 2),
                         last=CC.local_iso(last)[:10] if last else "")
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
                                 last=CC.local_iso(last)[:10]))
            nets = collections.Counter(r["network"] for r in rows)
            return dict(rows=rows, networks=sorted(nets.items(), key=lambda x: -x[1]), updated=C["updated"], sources=C["sources"])
        return _memo("charger_listing", self.deps() + [os.path.join(S.d, "charges", "charges_master.csv"), os.path.join(S.d, "charges", "app_chargebook.json"), self.f_places], build)

    REVIEW_MAX = 150
    REVIEW_CELL = 0.002         # degrees (~200 m): the parks in one cell are one spot

    def review_stops(self, items, chg=()):
        """The "Stops to review" list, made from the Drives list's own rows (tesla_app.drivelist: items, chg): every parked row that shows "Save" there (not inside a
        saved place, its radius but at least 60 m as the page has it; no charge during the stay), less the ignored spots. One entry per spot, the most visited
        (visits: its parks; days: the days they were on) first, then the latest, each with its parks (newest first) and the drive before and after each park. It used to be worked out from Tessie's drives and parked
        periods on its own, so it listed stops the Drives list does not show (and, needing 3 days at a spot, was empty once those were saved)."""
        import bisect
        defs = self.defs()
        ign = _load(self.f_ignored) or []
        ign = [(e.get("center_lat"), e.get("center_lon"), e.get("radius_m") or 100) for e in ign if isinstance(e, dict) and e.get("center_lat") is not None]
        f = lambda x: float(x) if x not in (None, "") else None
        drives = sorted((x for x in items if x.get("kind") == "drive" and x.get("t0") is not None), key=lambda x: f(x["t0"]))
        d_t0 = [f(x["t0"]) for x in drives]
        d_t1 = sorted((f(x.get("t1")) or f(x["t0"]), n) for n, x in enumerate(drives))
        d_t1k = [a for a, _n in d_t1]
        cs = [(f(c.get("t0")), f(c.get("t1")) or f(c.get("t0")) + 60 * (f(c.get("min")) or 0), c.get("lat"), c.get("lon")) for c in chg if c.get("t0") is not None]

        def drv(x):
            return dict(id=x.get("id"), frm=x.get("frm") or "", to=x.get("to") or "", start=x.get("start") or "", end=x.get("end") or "", km=f(x.get("km")))

        def near(pt, la, lo):
            return bool(pt) and pt[0] is not None and pt[1] is not None and hav(la, lo, float(pt[0]), float(pt[1])) <= 300
        cells = collections.defaultdict(list)
        for v in items:
            if v.get("kind") != "parked":
                continue
            pt = v.get("p0") or v.get("p1")
            if not pt or pt[0] is None or pt[1] is None:
                continue
            la, lo = float(pt[0]), float(pt[1])
            if any(hav(la, lo, d["lat"], d["lon"]) <= max(d.get("radius") or 100, 60) for d in defs):
                continue                                                # "Saved" on the Drives list
            t0, t1 = f(v.get("t0")), f(v.get("t1")) or f(v.get("t0"))
            if t0 is None:
                continue
            if any(a <= t1 + 300 and b >= t0 - 300 and (c_la is None or hav(la, lo, c_la, c_lo) <= 300) for a, b, c_la, c_lo in cs):
                continue                                                # charged while parked: the page shows the charge, not "Save"
            if any(hav(la, lo, a, b) <= r for a, b, r in ign):
                continue
            k = bisect.bisect_right(d_t1k, t0 + 120) - 1                 # the last drive that ended by the time the park began
            before = drives[d_t1[k][1]] if k >= 0 else None
            n = bisect.bisect_left(d_t0, t1 - 120)                       # the first drive that began once it ended
            after = drives[n] if n < len(drives) else None
            names = ([before.get("to")] if before and near(before.get("p1"), la, lo) else []) + ([after.get("frm")] if after and near(after.get("p0"), la, lo) else [])
            cells[(round(la / self.REVIEW_CELL), round(lo / self.REVIEW_CELL))].append(dict(
                id=v.get("id"), lat=la, lon=lo, start=v.get("start") or "", end=v.get("end") or "", t0=t0, t1=t1, min=round((t1 - t0) / 60) if t1 else None,
                ongoing=bool(v.get("ongoing")), before=drv(before) if before else None, after=drv(after) if after else None, names=[n for n in names if n]))
        out = []
        for g in cells.values():
            g.sort(key=lambda x: x["t0"], reverse=True)
            la, lo = sum(x["lat"] for x in g) / len(g), sum(x["lon"] for x in g) / len(g)
            names = collections.Counter(n for x in g for n in x.pop("names"))           # what the drives that ended or began right here call it
            out.append(dict(key=spot_key(la, lo), lat=la, lon=lo, visits=len(g), days=len({x["start"][:10] for x in g}), parks=g, last=g[0]["start"][:10],
                            addr=names.most_common(1)[0][0] if names else ""))
        out.sort(key=lambda x: x["last"], reverse=True)
        out.sort(key=lambda x: -x["visits"])                            # most visited first; within the same, the latest first (sorts are stable)
        return out[:self.REVIEW_MAX]

    # ---- one place -----------------------------------------------------------------------------------------------
    _STATES = {"New South Wales": "NSW", "Victoria": "VIC", "Queensland": "QLD", "South Australia": "SA", "Western Australia": "WA", "Tasmania": "TAS",
               "Northern Territory": "NT", "Australian Capital Territory": "ACT"}

    def suburb_state(self, lat, lon):
        """(suburb, state abbreviation) of a point: the suburb lookup first (lib/suburb_lookup.py: kept boundaries, instant once seen; one not kept yet is fetched in the
        background, never waited for), else Nominatim (6 s at most); the state comes from the
        position (au_state) when the answer has none. ("", "") when no suburb is found."""
        import places_common as PM
        import au_state
        ck = (round(lat, 4), round(lon, 4))
        memo = self.__dict__.setdefault("_ss", {})
        if ck not in memo:
            sub, st = "", ""
            try:
                import suburb_lookup
                S = self.__dict__.get("_sublk") or self.__dict__.setdefault("_sublk", suburb_lookup.Suburbs(os.path.join(self.d, "suburbs")))
                sub = S.at(lat, lon) or ""       # a kept boundary, else fetched behind for next time: Overpass took up to 50 s per server (85 s to open a spot)
            except Exception:
                sub = ""
            if not sub:
                try:
                    a = (PM.reverse_geocode(lat, lon) or {}).get("address") or {}
                except Exception:
                    a = {}
                sub = a.get("suburb") or a.get("city_district") or a.get("town") or a.get("village") or a.get("city") or ""
                st = (a.get("ISO3166-2-lvl4") or "").split("-")[-1] or self._STATES.get(a.get("state") or "", a.get("state") or "")
            if not sub:
                return "", ""
            memo[ck] = (sub, st or au_state.state(lat, lon) or "")
        return memo[ck]

    def address_of(self, S, lat, lon):
        """The street address of a point, shown under the map on a place page ("28a Gordon Street, Brighton, QLD 4017"): OpenStreetMap's address for the spot (kept in
        place_address_cache.json, so it is asked once), else the address Tessie recorded for the stops here. "" when neither has a street."""
        ck = "%.5f,%.5f" % (lat, lon)
        cache = _load(self.f_addr) or {}
        if cache.get(ck):
            return cache[ck]
        addr = ""
        try:
            import places_common as PM
            a = (PM.reverse_geocode(lat, lon) or {}).get("address") or {}
            road = (a.get("road") or a.get("pedestrian") or a.get("footway") or "").strip()
            if road:
                st = (a.get("ISO3166-2-lvl4") or "").split("-")[-1] or self._STATES.get(a.get("state") or "", a.get("state") or "")
                sub = a.get("suburb") or a.get("city_district") or a.get("town") or a.get("village") or a.get("city") or ""
                street = ((a.get("house_number") or "") + " " + road).strip()
                tail = " ".join(x for x in (st, a.get("postcode") or "") if x)
                addr = ", ".join(x for x in (street, sub, tail) if x)
        except Exception:
            addr = ""
        if not addr:
            tess = collections.Counter()
            for d in S.drives():
                if d.get("addr1") and d.get("lat1") is not None and hav(lat, lon, d["lat1"], d["lon1"]) <= 150:
                    tess[d["addr1"]] += 1
            for c in S.charges():
                if c.get("addr") and c.get("lat") is not None and hav(lat, lon, c["lat"], c["lon"]) <= 150:
                    tess[c["addr"]] += 1
            addr = next((x for x, _ in tess.most_common() if not x.split(",")[0].strip().replace("/", "").replace("-", "").isdigit()), "")
        if addr:
            cache[ck] = addr
            try:
                tmp = self.f_addr + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(cache, f, ensure_ascii=False, indent=1)
                os.replace(tmp, self.f_addr)
            except OSError:
                pass
        return addr

    @staticmethod
    def own_suburb(addr, name):
        """The address with the suburb and state the place is saved under ("Mum, Bardwell Valley, NSW"), where the map data puts the same street in a neighbouring suburb."""
        m = re.search(r",\s*([^,]+),\s*(NSW|VIC|QLD|SA|WA|TAS|NT|ACT)\s*$", name or "")
        a = re.match(r"^(.*?),\s*[^,]+,\s*[A-Z]{2,3}(\s+\d{4})?\s*$", addr or "")
        if not (m and a):
            return addr or ""
        return "%s, %s, %s%s" % (a.group(1), m.group(1).strip(), m.group(2), a.group(2) or "")

    _END_STATE = re.compile(r",\s*[^,]+,\s*(?:NSW|VIC|QLD|SA|WA|TAS|NT|ACT)\s*$")

    def with_suburb(self, name, lat, lon, charger=False):
        """(name, error): a saved place's name ends with its suburb and state ("Flower Power, Croydon Park, NSW"). A name that already ends that way is kept. A branch named after its suburb says
        it once: "Anytime Fitness Carlingford" becomes "Anytime Fitness, Carlingford, NSW" (a business only: a place with chargers keeps the name it has, chargers have their own naming; a name
        that merely holds the suburb, "The Concord Club", is kept whole). "Anzac Park, West Ryde" gets the state only."""
        if self._END_STATE.search(name):
            return name, None
        sub, st = self.suburb_state(lat, lon)
        if sub and re.sub(r",\s*(NSW|VIC|QLD|SA|WA|TAS|NT|ACT)\s*$", "", name).strip().lower() == sub.lower() and name.strip() != sub:
            return name, None                       # a place named after its suburb says it once: "Sydney Olympic Park, NSW"
        if not sub:
            return name, "The suburb and state of this spot could not be found: add them to the name, as 'Name, Suburb, NSW'."
        base = name.strip()
        if base.lower().endswith(", " + sub.lower()):
            base = base[:-len(sub) - 2].rstrip(", ")
        elif not charger:
            m = re.match(r"^(.*\S)\s+" + re.escape(sub) + r"$", base, re.I)
            if m:
                base = m.group(1)
        return ", ".join(x for x in (base, sub, st) if x), None

    def spot_keywords(self, S, lat, lon, name="", addr=""):
        """Keywords for a place that is not saved yet, as places_manager.py suggests them: the street of the address (and its abbreviation) plus the
        place name. The address comes from OpenStreetMap: the one on the OSM place itself (addr), else what Nominatim says is at that point. Only when
        OSM has no street there is the address Tessie recorded used, and only if it has a street (Tessie sometimes has just "12, Pialba")."""
        import places_common as PM
        usable = lambda a: bool(a) and not (a.split(",")[0].strip().replace("/", "").replace("-", "").isdigit())
        if not usable(addr):
            addr = ""
            try:
                g = PM.reverse_geocode(lat, lon) or {}
                a = g.get("address") or {}
                road = (a.get("road") or "").strip()
                if road and not road.replace("/", "").replace("-", "").isdigit():
                    street = ((a.get("house_number") or "") + " " + road).strip()
                    addr = ", ".join(x for x in (street, a.get("suburb") or a.get("city") or a.get("town") or "") if x)
            except Exception:
                addr = ""
        if not addr:
            tess = collections.Counter()
            for d in S.drives():
                if d.get("addr1") and d.get("lat1") is not None and hav(lat, lon, d["lat1"], d["lon1"]) <= 150:
                    tess[d["addr1"]] += 1
            for c in S.charges():
                if c.get("addr") and c.get("lat") is not None and hav(lat, lon, c["lat"], c["lon"]) <= 150:
                    tess[c["addr"]] += 1
            addr = next((a for a, _ in tess.most_common() if usable(a)), "")
        try:
            return PM.suggest_keywords(addr, name), addr
        except Exception:
            return [], addr

    def detail(self, S, key, name="", addr=""):
        info = None
        if key.startswith("p:"):
            p = next((x for x in self.defs() if x["name"] == key[2:]), None)
            if p:
                info = dict(kind="saved", name=p["name"], lat=p["lat"], lon=p["lon"], radius=p["radius"], keywords=p["keywords"], notes=p["notes"], chargers=p.get("chargers"), home=p.get("home"))
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
            kws, addr = self.spot_keywords(S, la, lo, name, addr)
            info = dict(kind="spot", name="Unnamed place", lat=la, lon=lo, radius=150, keywords=kws, notes="", addr=addr)
            if name:                                    # a name suggested from an OpenStreetMap result: the suburb and state make it specific ("Ampol, Ermington, NSW")
                info["suburb"], info["state"] = self.suburb_state(la, lo)
        if not info:
            return None
        info["key"] = key
        if info["kind"] in ("saved", "spot") and info.get("lat") is not None:
            try:
                info["address"] = self.own_suburb(info.get("addr") or self.address_of(S, info["lat"], info["lon"]), info["name"] if info["kind"] == "saved" else "")
            except Exception:
                info["address"] = info.get("addr") or ""
        elif info.get("addr"):
            info["address"] = info["addr"]
        if key[:2] in ("s:", "c:", "p:"):
            try:
                info["site"] = self.site(key)
            except Exception:
                info["site"] = None
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
        # where the car itself recorded stops near here (drive ends, parked periods, charges): used to size the radius of a place saved from an
        # OpenStreetMap result, so the spot where the car actually stopped is inside it
        cl = float(app_config.get("places.save_radius_cluster_m"))
        pts = set()
        for d in S.drives():
            if d.get("lat1") is not None and hav(info["lat"], info["lon"], d["lat1"], d["lon1"]) <= cl:
                pts.add((round(d["lat1"], 5), round(d["lon1"], 5)))
        for r in list(S.idles()) + list(S.charges()):
            if r.get("lat") is not None and hav(info["lat"], info["lon"], r["lat"], r["lon"]) <= cl:
                pts.add((round(r["lat"], 5), round(r["lon"], 5)))
        info["pts"] = sorted(pts)[:300]
        info["visits"] = sorted(visits, key=lambda e: -e["t0"])[:400]
        info["nvisits"] = len(visits)
        if info["kind"] in ("saved", "spot"):
            info["chargers_show"] = self.chargers_show(info, visits)
        return info

    def chargers_show(self, info, visits):
        """Whether the place page offers 'Chargers here' (which charger was used, and its price). Only when a charge was made here AND the charger is not already known:
        no Supercharger or charger on record at this spot, or two or more of them that the speed of each charge cannot tell apart (a DC and an AC charger side by side:
        a charge averaging 40 kW is the DC one). Home never."""
        if info.get("home") or info.get("lat") is None:
            return False
        chg = [v for v in visits if v.get("k") == "charge"]
        if not chg:
            return False
        r = max(150, info.get("radius") or 0)

        def kw(x):
            try:
                return float(x) if x not in (None, "") else None
            except (TypeError, ValueError):
                return None
        near = [kw(x.get("kw")) for x in list(self.stations()) + list(self.chargers()["rows"])
                if x.get("lat") is not None and x.get("lon") is not None and hav(info["lat"], info["lon"], x["lat"], x["lon"]) <= r]
        if not near:
            return True
        if len(near) < 2:
            return False
        for v in chg:
            avg = (v.get("kwh") or 0) / ((v.get("min") or 0) / 60.0) if v.get("min") else None
            able = [k for k in near if k is None or avg is None or avg <= k * 1.2]
            if len(able) != 1:
                return True
        return False

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

    @staticmethod
    def _tag_addr(tags):
        """"12 Esplanade, Pialba" from an OSM feature's addr:* tags ("" when it has no street)."""
        if not tags.get("addr:street"):
            return ""
        street = ((tags.get("addr:housenumber") or "") + " " + tags["addr:street"]).strip()
        return ", ".join(x for x in (street, tags.get("addr:suburb") or tags.get("addr:city") or "") if x)

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
            street = ((pr.get("housenumber") or "") + " " + (pr.get("street") or "")).strip()
            out.append(dict(name=name, group=grp, emoji=emo, category=(k if v == "yes" else v).replace("_", " ").title(), dist_m=round(d, 1), lat=la, lon=lo,
                            addr=", ".join(x for x in (street, pr.get("district") or pr.get("city") or "") if x) if pr.get("street") else ""))
        out.sort(key=lambda o: o["dist_m"])
        return out

    def osm(self, key, lat, lon, radius=300, refresh=False):
        """OSM places around a spot: cached; a Tesla charger uses the facilities already collected; otherwise ask Overpass (and cache)."""
        ck = "%s|%d" % (key, radius)
        if key.startswith("@"):         # an unsaved spot (@new, @lat,lon): the same key stands for different places, so keep it per position
            ck = "@%.5f,%.5f|%d" % (lat, lon, radius)
        cache = self._cache()
        if not refresh:
            if ck in cache:
                return dict(cache[ck], cached=True)
            if key.startswith("s:"):
                f = self._facilities(key[2:])
                if f:
                    return dict(f, cached=True)
        import places_common as PM
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
        items = [dict(name=r["name"], group=r.get("group") or "Other", emoji=r.get("emoji") or "", category=r.get("category") or "", dist_m=r.get("dist_m"), lat=r.get("lat"), lon=r.get("lon"), addr=self._tag_addr(r.get("tags") or {})) for r in res]
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
        import places_common as PM
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
        entry = dict(places.get(old) or places.get(name) or {})
        if (name != old or not old) and entry.get("type") != "home":         # a new or renamed place carries its suburb and state; a place saved earlier keeps its name when only something else is edited
            name, why = self.with_suburb(name, lat, lon, charger=bool(body.get("chargers") or entry.get("chargers")))
            if why:
                return dict(ok=False, error=why)
            ok, why = PM.validate_place_name(name)
            if not ok:
                return dict(ok=False, error=why)
        if name != old and name in places:
            return dict(ok=False, error="a place called '%s' already exists" % name)
        kw = body.get("keywords")
        if isinstance(kw, str):
            kw = [k.strip() for k in kw.replace("\n", ",").split(",") if k.strip()]
        kw = kw if kw is not None else entry.get("keywords", [])
        notes = (body.get("notes") or "").strip() or self.default_notes(name, kw)
        entry.update(lat=round(lat, 5), lon=round(lon, 5), radius_m=radius, keywords=kw, notes=notes)
        if "chargers" in body:                  # a saved place is not normally a charger: a list of {name, network, free, rate_per_kwh} marks the chargers it has (empty removes them)
            chs, why = self.clean_chargers(body.get("chargers"))
            if why:
                return dict(ok=False, error=why)
            if chs:
                entry["chargers"] = chs
            else:
                entry.pop("chargers", None)
        if old and old != name:
            places.pop(old, None)
        places[name] = entry
        self._backup()
        PM.save_places(places, self.f_places)
        return dict(ok=True, key="p:" + name)

    @classmethod
    def default_notes(cls, name, keywords):
        """The note places_manager.py writes for a place: its name and the street it is on ("Aga and Adam (28a Gordon Street)"; the street is the first keyword). Notes are never left empty."""
        base = cls._END_STATE.sub("", name).strip() or name
        street = next((k for k in keywords or [] if k and k.strip() and k.strip().lower() != base.lower()), "")
        return "%s (%s)" % (base, street) if street else base

    DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

    @classmethod
    def parse_days(cls, v):
        """["Mon", ...] from a list or text such as "Mon-Fri", "Sat Sun", "Mon,Wed" (empty or "daily" = every day, []). None when something is not a day."""
        if isinstance(v, list):
            v = " ".join(str(x) for x in v)
        t = str(v or "").replace(",", " ").replace("–", "-").strip()
        if not t or t.lower() in ("daily", "every day", "all", "any"):
            return []
        out = []
        for tok in t.split():
            if "-" in tok:
                a, _, b = tok.partition("-")
                a, b = a[:3].title(), b[:3].title()
                if a not in cls.DAYS or b not in cls.DAYS:
                    return None
                i, j = cls.DAYS.index(a), cls.DAYS.index(b)
                out += [cls.DAYS[(i + k) % 7] for k in range(((j - i) % 7) + 1)]
            elif tok[:3].title() in cls.DAYS:
                out.append(tok[:3].title())
            else:
                return None
        return [d for d in cls.DAYS if d in out]

    @staticmethod
    def _hm(v, default):
        t = str(v or "").strip() or default
        try:
            h, m = t.split(":")
            ok = 0 <= int(h) <= 24 and 0 <= int(m) < 60 and (int(h) < 24 or int(m) == 0)
        except ValueError:
            return None
        return "%02d:%02d" % (int(h), int(m)) if ok else None

    @classmethod
    def clean_chargers(cls, raw):
        """(list, error): the chargers of a saved place, checked. Each has a name (what the charger is called, e.g. Rhodes Waterside), a network, and a price: free, one price per kWh for all days and
        times, or a schedule (rate_schedules: label, days, start_time, end_time, rate_per_kwh, valid_from, valid_to - the shape of the Tesla entries, plus the dates). Prices are what the charger charges,
        GST included (the standard of the Tesla entries); gst "ex" says the figures are before GST. `kw` is the charger's max power: it tells two chargers apart by the speed seen."""
        import datetime as _dt
        out, seen = [], set()
        for c in raw or []:
            if not isinstance(c, dict) or c.get("remove"):
                continue
            name = (c.get("name") or "").strip()
            if not name:
                return [], "every charger needs a name (what it is called, e.g. Rhodes Waterside)"
            if name.lower() in seen:
                return [], "two chargers here are both called '%s'" % name
            seen.add(name.lower())

            def money(v, what):
                if v in (None, ""):
                    return None, ""
                try:
                    r = float(v)
                except (TypeError, ValueError):
                    return None, "%s of '%s' must be a number" % (what, name)
                return (round(r, 4), "") if 0 <= r <= 5 else (None, "%s of '%s' must be between 0 and 5 dollars" % (what, name))
            free = bool(c.get("free"))
            e = dict(name=name, network=(c.get("network") or "").strip() or "AC Public", free=free)
            if not free:
                rate, why = money(c.get("rate_per_kwh"), "the price per kWh")
                if why:
                    return [], why
                if rate is not None:
                    e["rate_per_kwh"] = rate
                scs = []
                for sc in c.get("rate_schedules") or []:
                    if not isinstance(sc, dict) or sc.get("remove"):
                        continue
                    r, why = money(sc.get("rate_per_kwh"), "a schedule price")
                    if why:
                        return [], why
                    if r is None:
                        continue                                    # an empty row
                    days = cls.parse_days(sc.get("days"))
                    a, b = cls._hm(sc.get("start_time"), "00:00"), cls._hm(sc.get("end_time"), "24:00")
                    if days is None or a is None or b is None:
                        return [], "a schedule of '%s' has days or times that are not valid (days like Mon-Fri, times like 09:00)" % name
                    one = dict(rate_per_kwh=r, start_time=a, end_time=b)
                    if days:
                        one["days"] = days
                    if str(sc.get("label") or "").strip():
                        one["label"] = str(sc["label"]).strip()
                    for k in ("valid_from", "valid_to"):
                        v = str(sc.get(k) or "").strip()
                        if v:
                            try:
                                _dt.date.fromisoformat(v)
                            except ValueError:
                                return [], "a schedule of '%s' has a date that is not valid (use YYYY-MM-DD)" % name
                            one[k] = v
                    scs.append(one)
                if scs:
                    e["rate_schedules"] = scs
                if str(c.get("gst") or "inc").lower() == "ex" and (rate is not None or scs):
                    e["gst"] = "ex"
                idle, why = money(c.get("idle_fee_per_min"), "the idle fee")
                if why:
                    return [], why
                if idle:
                    e["idle_fee_per_min"] = idle
            if c.get("kw") not in (None, ""):
                try:
                    kw = float(c["kw"])
                except (TypeError, ValueError):
                    return [], "the max power of '%s' must be a number of kW" % name
                if not 0 < kw <= 500:
                    return [], "the max power of '%s' must be between 0 and 500 kW" % name
                e["kw"] = kw
            if c.get("default"):
                e["default"] = True
            if c.get("note"):
                e["note"] = str(c["note"]).strip()
            out.append(e)
        if sum(1 for e in out if e.get("default")) > 1:
            first = True
            for e in out:
                if e.get("default"):
                    if first:
                        first = False
                    else:
                        e.pop("default")
        return out, ""

    def set_default_charger(self, place, name):
        """The charger charges at this place use when nothing tells them apart (the others lose the mark). name None removes the default."""
        import places_common as PM
        places = PM.load_places(self.f_places)
        entry = places.get(place)
        chs = [dict(c) for c in (entry or {}).get("chargers") or [] if isinstance(c, dict)]
        if not chs or (name and not any(c.get("name") == name for c in chs)):
            return dict(ok=False, error="no such charger")
        for c in chs:
            c.pop("default", None)
            if name and c.get("name") == name:
                c["default"] = True
        entry["chargers"] = chs
        self._backup()
        PM.save_places(places, self.f_places)
        return dict(ok=True)

    def add_charger(self, place, charger):
        """Adds (or updates, by name) one charger on a saved place, keeping the others; a dated backup first."""
        import places_common as PM
        places = PM.load_places(self.f_places)
        entry = places.get(place)
        if not isinstance(entry, dict):
            return dict(ok=False, error="no such saved place")
        cur = [c for c in (entry.get("chargers") or []) if isinstance(c, dict)]
        nm = (charger.get("name") or "").strip().lower()
        merged = [dict(charger) if (c.get("name") or "").strip().lower() == nm else c for c in cur]
        if not any((c.get("name") or "").strip().lower() == nm for c in cur):
            merged.append(dict(charger))
        chs, why = self.clean_chargers(merged)
        if why:
            return dict(ok=False, error=why)
        entry["chargers"] = chs
        places[place] = entry
        self._backup()
        PM.save_places(places, self.f_places)
        return dict(ok=True, key="p:" + place)

    def delete(self, name):
        import places_common as PM
        places = PM.load_places(self.f_places)
        if name not in places:
            return dict(ok=False, error="no such place")
        self._backup()
        places.pop(name)
        PM.save_places(places, self.f_places)
        return dict(ok=True)

    def _backup(self):
        """A copy of places.json before a change, in <data>/.bak_archive/ (not beside it); the three newest are kept."""
        bak = os.path.join(self.d, ".bak_archive")
        if os.path.isfile(self.f_places):
            os.makedirs(bak, exist_ok=True)
            shutil.copy2(self.f_places, os.path.join(bak, "places.json.bak-%s" % time.strftime("%Y%m%d-%H%M%S")))
        for folder in (self.d, bak):            # older copies kept beside places.json are tidied away too
            try:
                old = sorted(x for x in os.listdir(folder) if x.startswith("places.json.bak-"))
            except OSError:
                continue
            for x in old[:-3] if folder == bak else old:
                try:
                    os.remove(os.path.join(folder, x))
                except OSError:
                    pass
