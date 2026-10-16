"""app_data.py - read-only data layer for tesla_app.py (REQ-199).

Everything the app shows comes from the files the sync scripts already write into the Tesla data folder:

    drives/drives_master.csv                      every drive (Tessie)
    drives/deepdive/<stamp>/drive_summary.json    one per drive that has a deep-dive page (drive_report.py)
    charges/charges_master.csv                    every charge
    charges/deepdive/charge_deepdive_*.csv        telemetry of a charge (one file each)
    idles/idles_master.csv                        every idle (parked) period
    battery_health_log.json                       battery capacity readings

Nothing here writes anything. Results are cached in memory and rebuilt when a source file's mtime changes.
"""
import csv
import datetime
import glob
import json
import math
import os
import re
import threading

import app_alerts
import drive_paths as DP
import charge_paths as CP
import app_config
import app_places

_LOCK = threading.Lock()
_CACHE = {}


# Tessie records every move of the car as a drive, including shuffling it a few metres in a car park. A "re-park" is a drive that covers
# less than REPARK_KM (odometer steps are 10 m, so 0.02 = 20 m, 0.03 = 30 m) in no more than REPARK_MIN minutes. A trip that starts and ends at
# the same address (or saved place) is a re-park up to the larger repark_same_place_km. Everything else is a drive.
# The limits are settings (config.json "app.drives.repark_max_km" / "repark_same_place_km" / "repark_max_minutes").


def _num(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _short(addr):
    """'1 Example Street, Sampletown New South Wales 2000, Australia' -> '1 Example Street, Sampletown'"""
    p = [x.strip() for x in (addr or "").split(",")]
    return ", ".join(p[:2]) if p and p[0] else ""


def _stat_key(paths):
    """What changed since last time: each path's mtime and, for a folder, the newest mtime among its sub-folders - so a new drive or charge
    folder inside an existing day folder (deepdive/<day>/<stamp>/) is noticed, not only a new day."""
    out = []
    for p in paths:
        try:
            m = os.path.getmtime(p)
        except OSError:
            out.append((p, 0))
            continue
        sub = 0
        if os.path.isdir(p):
            try:
                with os.scandir(p) as it:
                    for e in it:
                        if e.is_dir(follow_symlinks=False):
                            sub = max(sub, e.stat().st_mtime)
            except OSError:
                pass
        out.append((p, m, sub))
    return tuple(out)


def _cached(name, deps, build):
    key = _stat_key(deps)
    with _LOCK:
        hit = _CACHE.get(name)
        if hit and hit[0] == key:
            return hit[1]
    val = build()
    with _LOCK:
        _CACHE[name] = (key, val)
    return val


def _rows(path):
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def _local(s):
    """'2026-10-12 16:41 AEDT' -> (datetime, 'AEDT')"""
    m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})(?::(\d{2}))?\s*(\S*)", s or "")
    if not m:
        return None, ""
    return datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M") + datetime.timedelta(seconds=int(m.group(2) or 0)), m.group(3)


class Store:
    def __init__(self, data_dir):
        self.d = data_dir
        self.pl = app_places.Places(data_dir)

    def p(self, *a):
        return os.path.join(self.d, *a)

    # ---- drives -------------------------------------------------------------------------------------------------
    def _summaries(self):
        root = self.p("drives", "deepdive")

        def build():
            out = []
            for _stamp, folder in DP.iter_folders(root):
                f = os.path.join(folder, "drive_summary.json")
                try:
                    j = json.load(open(f, encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if j.get("start"):
                    out.append(j)
            return out
        dirs = [root]
        for _stamp, folder in DP.iter_folders(root):         # a day folder changes when a drive appears, a drive folder when its summary is rewritten
            dirs += [os.path.dirname(folder), folder]
        return _cached("summ", list(dict.fromkeys(dirs)), build)

    def drives(self):
        master = self.p("drives", "drives_master.csv")

        def build():
            # A drive page's folder stamp is the drive's start in the hour-lagging local clock around DST changes, while
            # "start" inside it comes from the CSV; so a master row may match either. Best (smallest gap) wins, once per page.
            summ = {}
            for j in self._summaries():
                times = [datetime.datetime.strptime(j["start"][:19], "%Y-%m-%d %H:%M:%S")]
                m = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})", j.get("stamp", ""))
                if m:
                    times.append(datetime.datetime.strptime("%s %s:%s" % m.groups(), "%Y-%m-%d %H:%M"))
                for day in {t.strftime("%Y-%m-%d") for t in times}:
                    summ.setdefault(day, []).append((times, j))
            used = set()
            out = []
            for r in _rows(master):
                dt, tz = _local(r.get("Started At (Local)"))
                if not dt:
                    continue
                st, gap = None, 151
                for times, j in summ.get(dt.strftime("%Y-%m-%d"), []):
                    if j["stamp"] in used:
                        continue
                    g = min(abs((t - dt).total_seconds()) for t in times)
                    if g < gap:
                        st, gap = j, g
                if st:
                    used.add(st["stamp"])       # one page per drive
                t0 = int(_num(r.get("Started At (UTC)"), 0))
                t1 = int(_num(r.get("Ended At (UTC)"), t0))
                km = _num(r.get("Distance (km)"), 0)
                mins = _num(r.get("Duration (Minutes)"), 0)
                a0, a1 = (r.get("Starting Location") or "").strip().lower(), (r.get("Ending Location") or "").strip().lower()
                s0, s1 = (r.get("Starting Saved Location") or "").strip().lower(), (r.get("Ending Saved Location") or "").strip().lower()
                same = bool((a0 and a0 == a1) or (s0 and s0 == s1))      # the trip began and ended at the same address / saved place
                if not same and not ((a0 and a1) or (s0 and s1)):        # no address to compare: use how far apart the two ends are
                    la0, lo0, la1, lo1 = (_num(r.get(k)) for k in ("Starting Latitude", "Starting Longitude", "Ending Latitude", "Ending Longitude"))
                    if None not in (la0, lo0, la1, lo1):
                        same = math.hypot((la1 - la0) * 111320.0, (lo1 - lo0) * 111320.0 * math.cos(math.radians(la0))) <= app_config.get("drives.repark_same_place_m")
                quick = mins <= app_config.get("drives.repark_max_minutes")
                repark = quick and (km < app_config.get("drives.repark_max_km") or (same and km < app_config.get("drives.repark_same_place_km")))
                out.append(dict(k="drive", repark=repark, id=st["stamp"] if st else "t%d" % t0, page=bool(st), t0=t0, t1=t1, day=dt.strftime("%Y-%m-%d"), hm=dt.strftime("%H:%M"),
                                tz=tz, frm=(st or {}).get("frm") or r.get("Starting Saved Location") or _short(r.get("Starting Location")), to=(st or {}).get("to") or r.get("Ending Saved Location") or _short(r.get("Ending Location")),
                                min=_num(r.get("Duration (Minutes)"), 0), km=km, kwh=_num(r.get("Total Energy Used (kWh)"), 0), whkm=_num(r.get("Average Energy Used (Wh/km)")),
                                avg=_num(r.get("Average Speed (km/h)")), max=_num(r.get("Max Speed (km/h)")), b0=_num(r.get("Starting Battery (%)")), b1=_num(r.get("Ending Battery (%)")),
                                ap=_num(r.get("Autopilot Distance (km)"), 0), lat=_num(r.get("Starting Latitude")), lon=_num(r.get("Starting Longitude")),
                                addr1=r.get("Ending Location") or "", pkey1=self.pl.label(_num(r.get("Ending Latitude")), _num(r.get("Ending Longitude")), False, r.get("Ending Saved Location") or "")[1],
                                lat1=_num(r.get("Ending Latitude")), lon1=_num(r.get("Ending Longitude")), odo=_num(r.get("Starting Odometer (km)")),
                                tin=_num(r.get("Average Inside Temperature (°C)")), tout=_num(r.get("Average Outside Temperature (°C)")),
                                foot=(st or {}).get("footage_pct"), clips=(st or {}).get("footage_clips"), saved=(st or {}).get("saved_clip_events") or 0,
                                flips=(st or {}).get("flips") or 0))
            return out
        return _cached("drives", [master, self.p("places.json"), app_config.path()] + [self.p("drives", "deepdive")], build)

    # ---- charges ------------------------------------------------------------------------------------------------
    def _charge_files(self):
        root = self.p("charges", "deepdive")

        def build():
            out = {}
            for f in CP.glob_files(root, "charge_deepdive_*.csv"):          # day folders and any flat leftovers
                m = re.match(r"charge_deepdive_(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})_", os.path.basename(f))
                if m:
                    out.setdefault((m.group(1), m.group(2) + ":" + m.group(3)), f)
            return out
        return _cached("cfiles", [root], build)

    def charges(self):
        master = self.p("charges", "charges_master.csv")

        def build():
            files = self._charge_files()
            out = []
            for r in _rows(master):
                dt, tz = _local(r.get("Started At (Local)"))
                if not dt:
                    continue
                t0 = int(_num(r.get("Started At (UTC)"), 0))
                f = None
                for dm in (0, -1, 1):
                    x = dt + datetime.timedelta(minutes=dm)
                    f = files.get((x.strftime("%Y-%m-%d"), x.strftime("%H:%M")))
                    if f:
                        break
                kind = "supercharger" if r.get("Supercharger") == "true" else "dc" if r.get("Fast Charger") == "true" else "ac"
                sc = r.get("Supercharger") == "true"
                lat, lon = _num(r.get("Latitude")), _num(r.get("Longitude"))
                pname, pkey = self.pl.label(lat, lon, sc, r.get("Saved Location") or "", True)
                out.append(dict(k="charge", id=str(t0), t0=t0, t1=int(_num(r.get("Ended At (UTC)"), t0)), day=dt.strftime("%Y-%m-%d"), hm=dt.strftime("%H:%M"), tz=tz,
                                min=_num(r.get("Duration (Minutes)"), 0), place=pname or _short(r.get("Location")) or r.get("Location") or "", pkey=pkey, addr=r.get("Location") or "",
                                kwh=_num(r.get("Energy Added (kWh)"), 0), used=_num(r.get("Energy Used (kWh)")), b0=_num(r.get("Starting Battery (%)")), b1=_num(r.get("Ending Battery (%)")),
                                cost=_num(r.get("Cost")), rate=_num(r.get("Cost Per kWh")), kind=kind, lat=_num(r.get("Latitude")), lon=_num(r.get("Longitude")),
                                range=_num(r.get("Rated Range Added (km)")), odo=_num(r.get("Odometer (km)")), has=bool(f)))
            return self._merge_plugs(out)
        return _cached("charges", [master, self.p("charges", "deepdive"), self.p("charges", "app_chargebook.json")] + self.pl.deps(), build)

    def _plug_groups(self):
        """{first record id: [every record id]} for plug-ins Tessie recorded as several sessions (from the charge book that
        analyse_charging.py's reconcile produced; empty until the Charges page has built it once)."""
        f = self.p("charges", "app_chargebook.json")

        def build():
            try:
                with open(f, encoding="utf-8") as fh:
                    j = json.load(fh)
            except (OSError, ValueError):
                return {}
            return {p["parts"][0]["id"]: [x["id"] for x in p["parts"]] for p in j.get("plugs", []) if len(p.get("parts") or []) > 1}
        return _cached("plugg", [f], build)

    def _plug_nets(self):
        """{charge id: network} from the charge book (Tesla Supercharger, Exploren, AC Public, Evnex ...)."""
        f = self.p("charges", "app_chargebook.json")

        def build():
            try:
                with open(f, encoding="utf-8") as fh:
                    j = json.load(fh)
            except (OSError, ValueError):
                return {}
            return {x["id"]: p.get("network") or "" for p in j.get("plugs", []) for x in (p.get("parts") or [])}
        return _cached("plugn", [f], build)

    def _merge_plugs(self, out):
        """One plug-in = one charge: the first record carries the whole plug-in and lists the records as `parts`."""
        by = {r["id"]: r for r in out}
        nets = self._plug_nets()
        for r in out:
            r["net"] = nets.get(r["id"], "")
        gone = set()
        for lead, ids in self._plug_groups().items():
            rs = [by[i] for i in ids if i in by]
            if len(rs) < 2 or rs[0]["id"] != lead:
                continue
            m = rs[0]
            m["parts"] = [dict(id=r["id"], t0=r["t0"], t1=r["t1"], day=r["day"], hm=r["hm"], min=r["min"], b0=r["b0"], b1=r["b1"], kwh=r["kwh"], cost=r["cost"]) for r in rs]
            m["t1"] = rs[-1]["t1"]
            m["min"] = round((m["t1"] - m["t0"]) / 60.0, 1)
            m["b1"] = rs[-1]["b1"]
            for k in ("kwh", "used", "cost", "range"):
                vals = [r[k] for r in rs if r.get(k) is not None]
                m[k] = round(sum(vals), 2) if vals else m.get(k)
            m["rate"] = round(m["cost"] / m["kwh"], 3) if m.get("cost") is not None and m.get("kwh") else m.get("rate")
            m["has"] = any(r["has"] for r in rs)
            gone.update(r["id"] for r in rs[1:])
        return [r for r in out if r["id"] not in gone]

    def charge_detail(self, cid):
        c = next((x for x in self.charges() if x["id"] == cid), None)
        if not c:
            return None
        files = self._charge_files()
        dt = datetime.datetime.strptime(c["day"] + " " + c["hm"], "%Y-%m-%d %H:%M")
        f = None
        for dm in (0, -1, 1):
            x = dt + datetime.timedelta(minutes=dm)
            f = files.get((x.strftime("%Y-%m-%d"), x.strftime("%H:%M")))
            if f:
                break
        series = None
        if f:
            rows = _rows(f)
            keep = rows if len(rows) <= 700 else [rows[int(i * (len(rows) - 1) / 699)] for i in range(700)]
            t_first = None
            cols = {"t": [], "kw": [], "pct": [], "kwh": [], "rng": [], "amps": [], "volts": [], "mod": [], "out": [], "rate": []}
            for r in keep:
                tt = next((v for k, v in r.items() if k.startswith("Timestamp")), "")
                try:
                    t = datetime.datetime.strptime(tt[:19], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if t_first is None:
                    t_first = t
                cols["t"].append(int((t - t_first).total_seconds()))
                cols["kw"].append(_num(r.get("Charger Power (kW)")))
                cols["pct"].append(_num(r.get("Battery Level (%)")))
                cols["kwh"].append(_num(r.get("Energy Added (kWh)")))
                cols["rng"].append(_num(r.get("Rated Range Added (km)")))
                cols["amps"].append(_num(r.get("Current (A)")))
                cols["volts"].append(_num(r.get("Charger Voltage (V)")))
                cols["mod"].append(_num(r.get("Max Battery Module Temp (°C)")))
                cols["out"].append(_num(r.get("Outside Temp (°C)")))
                cols["rate"].append(_num(r.get("Charge Rate (km/h)")))
            series = cols
        return dict(charge=c, series=series, file=os.path.basename(f) if f else None)

    # ---- idles --------------------------------------------------------------------------------------------------
    def idles(self):
        master = self.p("idles", "idles_master.csv")

        def build():
            out = []
            for r in _rows(master):
                dt, tz = _local(r.get("Started At (Local)"))
                if not dt:
                    continue
                t0 = int(_num(r.get("Started At (UTC)"), 0))
                out.append(dict(k="idle", id=r.get("Idle ID") or str(t0), t0=t0, t1=int(_num(r.get("Ended At (UTC)"), t0)), day=dt.strftime("%Y-%m-%d"), hm=dt.strftime("%H:%M"), tz=tz,
                                min=_num(r.get("Duration (min)"), 0), place=self.pl.label(_num(r.get("Latitude")), _num(r.get("Longitude")), False, r.get("Saved Location") or "")[0] or _short(r.get("Location")) or r.get("Location") or "", pkey=self.pl.label(_num(r.get("Latitude")), _num(r.get("Longitude")), False, r.get("Saved Location") or "")[1], addr=r.get("Location") or "",
                                b0=_num(r.get("Starting Battery (%)")), b1=_num(r.get("Ending Battery (%)")), range=_num(r.get("Rated Range Used (km)")),
                                climate=_num(r.get("Climate Fraction"), 0), sentry=_num(r.get("Sentry Fraction"), 0), kwh=_num(r.get("Energy Used (kWh)"), 0),
                                lat=_num(r.get("Latitude")), lon=_num(r.get("Longitude"))))
            return out
        return _cached("idles", [master, self.p("places.json")], build)

    # ---- everything on one timeline ------------------------------------------------------------------------------
    def alerts(self):
        """Firmware alerts as episodes with plain-English text, each tied to the drive / charge / parked period it happened in."""
        return _cached("alerts", app_alerts.deps(self.d) + [self.p("drives", "drives_master.csv"), self.p("charges", "charges_master.csv"), self.p("idles", "idles_master.csv")],
                       lambda: app_alerts.build(self.d, self.drives(), self.charges(), self.idles()))

    def activity(self):
        items = self.drives() + self.charges() + self.idles()
        sev = {"safety": 0, "attention": 1, "notice": 2, "info": 3}
        for it in items:
            it["al"], it["alsev"] = 0, ""
        idx = {(it["k"], it["id"]): it for it in items if it.get("id")}
        for e in self.alerts()["episodes"]:
            c = e.get("ctx")
            it = idx.get((c["k"], c["id"])) if c else None
            if it:
                it["al"] += 1
                if not it["alsev"] or sev[e["severity"]] < sev[it["alsev"]]:
                    it["alsev"] = e["severity"]
        return sorted(items, key=lambda x: -x["t0"])

    # ---- battery ------------------------------------------------------------------------------------------------
    def battery(self):
        path = self.p("battery_health_log.json")

        def build():
            try:
                j = json.load(open(path, encoding="utf-8"))
            except (OSError, ValueError):
                return []
            out = []
            for r in j:
                if r.get("is_outlier") or r.get("capacity_kwh") is None:
                    continue
                out.append(dict(t=r.get("synced_at", "")[:16], kwh=r.get("capacity_kwh"), range=r.get("max_range_km"), odo=r.get("odometer_km"), health=r.get("health_percent")))
            return out
        return _cached("battery", [path], build)

    def spec(self):
        try:
            return json.load(open(self.p("vehicle_spec.json"), encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    # ---- monthly analytics ----------------------------------------------------------------------------------------
    def analytics(self):
        def build():
            m = {}

            def mo(day):
                return m.setdefault(day[:7], dict(month=day[:7], drives=0, km=0.0, kwh=0.0, ap=0.0, charges=0, added=0.0, cost=0.0, home=0.0, super=0.0, idle_kwh=0.0, idle_min=0.0, max=0.0))
            for d in self.drives():
                x = mo(d["day"])
                x["drives"] += 1
                x["km"] += d["km"] or 0
                x["kwh"] += d["kwh"] or 0
                x["ap"] += d["ap"] or 0
                x["max"] = max(x["max"], d["max"] or 0)
            for c in self.charges():
                x = mo(c["day"])
                x["charges"] += 1
                x["added"] += c["kwh"] or 0
                x["cost"] += c["cost"] or 0
                if c["kind"] == "ac":
                    x["home"] += c["kwh"] or 0
                else:
                    x["super"] += c["kwh"] or 0
            for i in self.idles():
                x = mo(i["day"])
                x["idle_kwh"] += i["kwh"] or 0
                x["idle_min"] += i["min"] or 0
            out = []
            for k in sorted(m):
                x = m[k]
                x["whkm"] = round(x["kwh"] * 1000 / x["km"], 1) if x["km"] > 0.5 else None
                x["ap_pct"] = round(100 * x["ap"] / x["km"], 1) if x["km"] > 0.5 else None
                x["cost_km"] = round(x["cost"] * 100 / x["km"], 2) if x["km"] > 0.5 else None
                for f in ("km", "kwh", "ap", "added", "cost", "home", "super", "idle_kwh", "idle_min"):
                    x[f] = round(x[f], 2)
                out.append(x)
            return out
        return _cached("analytics", [self.p("drives", "drives_master.csv"), self.p("charges", "charges_master.csv"), self.p("idles", "idles_master.csv")], build)

    def summary(self):
        dr, ch, idl = self.drives(), self.charges(), self.idles()
        last = max([x["t0"] for x in dr + ch + idl] or [0])
        return dict(drives=len(dr), charges=len(ch), idles=len(idl), km=round(sum(d["km"] or 0 for d in dr)), last=last,
                    vehicle=(self.spec().get("vehicle") or {}).get("model", "Tesla"), data_dir=self.d)
