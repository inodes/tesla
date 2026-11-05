#!/usr/bin/env python3
"""make_perf_fixture.py - a synthetic data folder for timing the app (perf_run.py), shaped like the real one but made up.

    make_perf_fixture.py OUT [--days N] [--seed N] [--scale F]

OUT gets three things (all made up, nothing is copied from real data):

    OUT/data/         the Tesla data folder: drives, charges and idles master CSVs, drive folders with their summary and Tessie point file,
                      places, charging stations, battery log, teslacam_timeline (telemetry per minute), teslacam_ledger, teslacam_provenance
    OUT/home/mnt/     three TeslaCam drives (Archive, JOWUA, Tesla USB) with tens of thousands of tiny clip files, found the way a
                      connected folder is found (~/mnt/*/TeslaCam) when HOME is OUT/home
    OUT/config.json   config.example.json with the data folder set to OUT/data

The default size is close to the owner's folder: 150 days, about 1,050 drives, 200 charges; the drives hold about 54k, 23k and 13k files.
--scale 0.1 makes a small one (tests, a quick look). OUT must not be inside the repository.
"""
import argparse
import csv
import datetime
import json
import math
import os
import random
import shutil
import sys
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.realpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))

ZONE = ZoneInfo("Australia/Sydney")
CAMS = ("front", "back", "left_repeater", "right_repeater", "left_pillar", "right_pillar")
HOME = (-33.80, 151.00)
SUBURBS = ["Gladesville", "Ryde", "Parramatta", "Epping", "Chatswood", "Hornsby", "Strathfield", "Burwood", "Concord", "Drummoyne",
           "Ermington", "Rydalmere", "Meadowbank", "Putney", "Hunters Hill", "Lane Cove", "Macquarie Park", "Eastwood", "Carlingford", "Dundas"]
STREETS = ["Victoria Rd", "Church St", "Pennant Hills Rd", "Lane Cove Rd", "Epping Rd", "Silverwater Rd", "Concord Rd", "Blaxland Rd", "Marsden Rd", "Devlin St"]

DRIVE_HEAD = ["Started At (Local)", "Ended At (Local)", "Started At (UTC)", "Ended At (UTC)", "Starting Location", "Starting Saved Location",
              "Starting Latitude", "Starting Longitude", "Ending Location", "Ending Saved Location", "Ending Latitude", "Ending Longitude",
              "Duration (Minutes)", "Distance (km)", "Total Energy Used (kWh)", "Average Energy Used (Wh/km)", "Average Speed (km/h)",
              "Max Speed (km/h)", "Starting Battery (%)", "Ending Battery (%)", "Autopilot Distance (km)", "Starting Odometer (km)",
              "Ending Odometer (km)", "Average Inside Temperature (°C)", "Average Outside Temperature (°C)", "Tag"]
CHARGE_HEAD = ["Started At (Local)", "Ended At (Local)", "Started At (UTC)", "Ended At (UTC)", "Location", "Saved Location", "Latitude", "Longitude",
               "Duration (Minutes)", "Energy Added (kWh)", "Energy Used (kWh)", "Starting Battery (%)", "Ending Battery (%)", "Cost", "Cost Per kWh",
               "Rated Range Added (km)", "Odometer (km)", "Supercharger", "Fast Charger", "Tag"]
IDLE_HEAD = ["Idle ID", "Started At (Local)", "Ended At (Local)", "Started At (UTC)", "Ended At (UTC)", "Location", "Saved Location", "Latitude",
             "Longitude", "Duration (min)", "Starting Battery (%)", "Ending Battery (%)", "Rated Range Used (km)", "Climate Fraction",
             "Sentry Fraction", "Energy Used (kWh)"]


def local_text(t):
    d = datetime.datetime.fromtimestamp(t, ZONE)
    return d.strftime("%Y-%m-%d %H:%M:%S ") + d.tzname()


def car_stamp(t):
    """The clip name: the car's own clock, local time."""
    return datetime.datetime.fromtimestamp(t, ZONE).strftime("%Y-%m-%d_%H-%M-%S")


def naive_epoch(stamp):
    return datetime.datetime.strptime(stamp, "%Y-%m-%d_%H-%M-%S").replace(tzinfo=datetime.timezone.utc).timestamp()


def move(p, km, rnd):
    a = rnd.uniform(0, 2 * math.pi)
    la = p[0] + km * math.cos(a) / 111.3
    lo = p[1] + km * math.sin(a) / (111.3 * math.cos(math.radians(p[0])))
    if math.hypot(la - HOME[0], lo - HOME[1]) > 0.5:      # stay around Sydney
        la, lo = HOME[0] + (la - HOME[0]) * 0.3, HOME[1] + (lo - HOME[1]) * 0.3
    return (la, lo)


def addr(rnd):
    return "%d %s, %s NSW %d, Australia" % (rnd.randint(1, 400), rnd.choice(STREETS), rnd.choice(SUBURBS), rnd.randint(2000, 2200))


def write_csv(path, head, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, head)
        w.writeheader()
        w.writerows(rows)


def plan(days, rnd, end):
    """The car's life: drives, charges and idles (UTC epochs), oldest first."""
    t = end - days * 86400
    pos, odo, batt = HOME, 31000.0, 80.0
    drives, charges, idles = [], [], []
    day0 = datetime.datetime.fromtimestamp(t, ZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    for d in range(days):
        base = (day0 + datetime.timedelta(days=d)).timestamp()
        n = rnd.choice([4, 6, 8, 8, 9, 10, 11])
        starts = sorted(base + rnd.uniform(6.5, 21.5) * 3600 for _ in range(n))
        last_end = None
        for i, s in enumerate(starts):
            if last_end and s < last_end + 300:
                continue
            mins = rnd.choice([3, 6, 9, 12, 15, 20, 25, 32, 45, 70])
            km = mins * rnd.uniform(0.35, 0.9)
            to = HOME if i == len(starts) - 1 else move(pos, km * 0.6, rnd)
            e = s + mins * 60
            used = km * 0.155
            b1 = max(8.0, batt - used / 0.75)
            drives.append(dict(t0=s, t1=e, p0=pos, p1=to, km=km, mins=mins, kwh=used, b0=batt, b1=b1, odo0=odo, odo1=odo + km, a0=addr(rnd), a1=addr(rnd),
                               s0="Home" if pos == HOME else "", s1="Home" if to == HOME else ""))
            if last_end:
                idles.append(dict(t0=last_end, t1=s, p=pos, b0=batt, b1=batt - 0.3, s="Home" if pos == HOME else ""))
            pos, odo, batt, last_end = to, odo + km, b1, e
        for _ in range(2 if rnd.random() < 0.3 else 1):      # charge at home overnight, sometimes a Supercharger; some days twice
            if not (batt < 70 or rnd.random() < 0.85):
                continue
            sc = rnd.random() < 0.15
            c0 = (last_end or base + 20 * 3600) + 600
            if charges and charges[-1]["t1"] > c0:
                c0 = charges[-1]["t1"] + 600
            add = max(2.0, (90 - batt) * 0.75)
            mins = add / (110.0 if sc else 7.0) * 60
            charges.append(dict(t0=c0, t1=c0 + mins * 60, p=pos if sc else HOME, kwh=add, b0=batt, b1=90.0, sc=sc, odo=odo,
                                addr=addr(rnd), saved="" if sc else "Home"))
            batt = 70.0 + rnd.uniform(0, 20)
    return drives, charges, idles


def write_tessie(data, drives, charges, idles, rnd, page_share=0.6):
    rows = []
    for i, d in enumerate(drives):
        rows.append({"Started At (Local)": local_text(d["t0"]), "Ended At (Local)": local_text(d["t1"]), "Started At (UTC)": int(d["t0"]), "Ended At (UTC)": int(d["t1"]),
                     "Starting Location": d["a0"], "Starting Saved Location": d["s0"], "Starting Latitude": "%.5f" % d["p0"][0], "Starting Longitude": "%.5f" % d["p0"][1],
                     "Ending Location": d["a1"], "Ending Saved Location": d["s1"], "Ending Latitude": "%.5f" % d["p1"][0], "Ending Longitude": "%.5f" % d["p1"][1],
                     "Duration (Minutes)": d["mins"], "Distance (km)": "%.2f" % d["km"], "Total Energy Used (kWh)": "%.2f" % d["kwh"],
                     "Average Energy Used (Wh/km)": "%.0f" % (d["kwh"] * 1000 / max(d["km"], 0.1)), "Average Speed (km/h)": "%.0f" % (d["km"] / d["mins"] * 60),
                     "Max Speed (km/h)": "%.0f" % (d["km"] / d["mins"] * 60 * 1.6), "Starting Battery (%)": "%.0f" % d["b0"], "Ending Battery (%)": "%.0f" % d["b1"],
                     "Autopilot Distance (km)": "%.1f" % (d["km"] * rnd.random()), "Starting Odometer (km)": "%.1f" % d["odo0"], "Ending Odometer (km)": "%.1f" % d["odo1"],
                     "Average Inside Temperature (°C)": "22", "Average Outside Temperature (°C)": "%.0f" % rnd.uniform(10, 32), "Tag": ""})
    write_csv(os.path.join(data, "drives", "drives_master.csv"), DRIVE_HEAD, rows)
    root = os.path.join(data, "drives", "deepdive")
    for d in drives:                                      # a drive page folder (summary + Tessie's points) for most drives
        if rnd.random() > page_share:
            continue
        loc = datetime.datetime.fromtimestamp(d["t0"], ZONE)
        stamp = loc.strftime("%Y-%m-%d_%H-%M_") + loc.tzname()
        folder = os.path.join(root, stamp[:10], stamp)
        os.makedirs(folder, exist_ok=True)
        n = int((d["t1"] - d["t0"]) / 15) + 1             # a point every 15 s
        pts = []
        for k in range(n):
            f = k / max(1, n - 1)
            pts.append(dict(timestamp=int(d["t0"] + k * 15), state="driving", shift_state="D", battery_level=round(d["b0"] + (d["b1"] - d["b0"]) * f),
                            latitude=round(d["p0"][0] + (d["p1"][0] - d["p0"][0]) * f, 6), longitude=round(d["p0"][1] + (d["p1"][1] - d["p0"][1]) * f, 6),
                            speed=round(rnd.uniform(20, 80)), power=round(rnd.uniform(-20, 60)), odometer=round(d["odo0"] + d["km"] * f, 2),
                            inside_temp=22, outside_temp=20))
        with open(os.path.join(folder, "drive_deepdive_%s.json" % stamp), "w", encoding="utf-8") as fh:
            json.dump(pts, fh, separators=(",", ":"))
        summ = dict(stamp=stamp, start=loc.strftime("%Y-%m-%d %H:%M:%S"), end=datetime.datetime.fromtimestamp(d["t1"], ZONE).strftime("%Y-%m-%d %H:%M:%S"),
                    frm=d["s0"] or d["a0"].split(",")[1].strip(), to=d["s1"] or d["a1"].split(",")[1].strip(), km="%.2f" % d["km"], minutes=d["mins"],
                    flips=0, saved_clip_events=0, footage_clips=0, footage_pct=0, videos=False, t0=d["t0"], t1=d["t1"])
        with open(os.path.join(folder, "drive_summary.json"), "w", encoding="utf-8") as fh:
            json.dump(summ, fh)
        with open(os.path.join(folder, "drive.html"), "w", encoding="utf-8") as fh:
            fh.write("<!doctype html><title>%s</title><body>drive</body>" % stamp)
    rows = []
    for c in charges:
        rows.append({"Started At (Local)": local_text(c["t0"]), "Ended At (Local)": local_text(c["t1"]), "Started At (UTC)": int(c["t0"]), "Ended At (UTC)": int(c["t1"]),
                     "Location": c["addr"], "Saved Location": c["saved"], "Latitude": "%.5f" % c["p"][0], "Longitude": "%.5f" % c["p"][1],
                     "Duration (Minutes)": "%.0f" % ((c["t1"] - c["t0"]) / 60), "Energy Added (kWh)": "%.2f" % c["kwh"], "Energy Used (kWh)": "%.2f" % (c["kwh"] * 1.08),
                     "Starting Battery (%)": "%.0f" % c["b0"], "Ending Battery (%)": "%.0f" % c["b1"], "Cost": "%.2f" % (c["kwh"] * (0.62 if c["sc"] else 0.12)),
                     "Cost Per kWh": "0.62" if c["sc"] else "0.12", "Rated Range Added (km)": "%.0f" % (c["kwh"] * 6.4), "Odometer (km)": "%.1f" % c["odo"],
                     "Supercharger": "true" if c["sc"] else "false", "Fast Charger": "true" if c["sc"] else "false", "Tag": ""})
    write_csv(os.path.join(data, "charges", "charges_master.csv"), CHARGE_HEAD, rows)
    rows = []
    for i, x in enumerate(idles):
        rows.append({"Idle ID": "i%d" % i, "Started At (Local)": local_text(x["t0"]), "Ended At (Local)": local_text(x["t1"]), "Started At (UTC)": int(x["t0"]),
                     "Ended At (UTC)": int(x["t1"]), "Location": addr(rnd), "Saved Location": x["s"], "Latitude": "%.5f" % x["p"][0], "Longitude": "%.5f" % x["p"][1],
                     "Duration (min)": "%.0f" % ((x["t1"] - x["t0"]) / 60), "Starting Battery (%)": "%.0f" % x["b0"], "Ending Battery (%)": "%.0f" % x["b1"],
                     "Rated Range Used (km)": "2", "Climate Fraction": "0.1", "Sentry Fraction": "%.2f" % rnd.random(), "Energy Used (kWh)": "0.3"})
    write_csv(os.path.join(data, "idles", "idles_master.csv"), IDLE_HEAD, rows)


def write_registries(data, rnd, n_stations):
    places = {"Home": dict(keywords=["Home St"], lat=HOME[0], lon=HOME[1], radius_m=100)}
    for i in range(40):
        p = move(HOME, rnd.uniform(1, 30), rnd)
        places["Place %02d" % i] = dict(keywords=[rnd.choice(SUBURBS)], lat=round(p[0], 5), lon=round(p[1], 5), radius_m=rnd.choice([100, 150, 250]))
    with open(os.path.join(data, "places.json"), "w", encoding="utf-8") as f:
        json.dump(places, f, indent=1)
    st = {}
    for i in range(n_stations):
        p = (rnd.uniform(-38.0, -28.0), rnd.uniform(141.0, 153.5))
        net = "Tesla" if i % 9 == 0 else rnd.choice(["Chargefox", "Evie", "NRMA", "Ampol AmpCharge", "BP Pulse", "Exploren", "PlugShare"])
        sid = "x%05d" % i
        st[sid] = dict(id=sid, name="%s %s %d" % (net, rnd.choice(SUBURBS), i), network=net,
                       location=dict(lat=round(p[0], 5), lon=round(p[1], 5), address=addr(rnd), suburb=rnd.choice(SUBURBS), state="NSW"),
                       hardware=dict(stalls=rnd.randint(1, 12), max_power_kw=rnd.choice([7, 22, 50, 75, 120, 250]), speed_tier="DC", connector_types=["CCS2", "Type 2"]),
                       compatibility=dict(tesla_only=net == "Tesla" and rnd.random() < 0.3, access="public"),
                       access=dict(hours="24/7", description="Public\nOpen to all"))
    with open(os.path.join(data, "charging_stations.json"), "w", encoding="utf-8") as f:
        json.dump(dict(updated_at="2026-11-01T00:00:00Z", sources=["fixture"], stations=st), f)
    log = []
    for k in range(120):
        log.append(dict(synced_at="2026-%02d-%02dT08:00:00Z" % (6 + k // 25, 1 + k % 25), capacity_kwh=round(74 - k * 0.01, 2), max_range_km=500, odometer_km=31000 + k * 60, health_percent=95))
    with open(os.path.join(data, "battery_health_log.json"), "w", encoding="utf-8") as f:
        json.dump(log, f)


def recordings(drives, idles, rnd, sentry_share=0.25, drive_share=0.30):
    """[(UTC, moving, pos)] one per recorded minute: the minutes of most drives (some were never kept), and some parked minutes (Sentry)."""
    mins = []
    for d in drives:
        if rnd.random() > drive_share:
            continue
        n = int((d["t1"] - d["t0"]) // 60) + 1
        t = d["t0"] - (d["t0"] % 60)
        for k in range(n):
            f = k / max(1, n - 1)
            mins.append((t + k * 60, True, (d["p0"][0] + (d["p1"][0] - d["p0"][0]) * f, d["p0"][1] + (d["p1"][1] - d["p0"][1]) * f)))
    for x in idles:
        if rnd.random() < sentry_share:
            t = x["t0"] + 300 - (x["t0"] % 60)
            for k in range(rnd.randint(2, 12)):
                mins.append((t + k * 60, False, x["p"]))
    mins.sort()
    return mins


def make_drives(home, mins, events, rnd, scale):
    """Three TeslaCam drives of tiny clip files. Archive: everything but the newest 8% of the time span; JOWUA: the newest 38%;
    Tesla USB: the newest 22% (the car's current drive). Returns {role: TeslaCam path}."""
    end = mins[-1][0]
    span = end - mins[0][0]
    spec = [("A", "ARCHIVE_DRIVE", "ARCHIVE_2TB", lambda t: t < end - 0.08 * span), ("J", "JOWUA_DRIVE", "JOWUA_1TB", lambda t: t >= end - 0.38 * span),
            ("U", "TESLA_USB_DRIVE", "TESLA_USB_128GB", lambda t: t >= end - 0.22 * span)]
    out = {}
    for role, vol, marker, keep in spec:
        root = os.path.join(home, "mnt", vol)
        tc = os.path.join(root, "TeslaCam")
        os.makedirs(os.path.join(root, marker), exist_ok=True)
        rc = os.path.join(tc, "RecentClips")
        os.makedirs(rc, exist_ok=True)
        for t, _mv, _p in mins:
            if keep(t):
                st = car_stamp(t)
                for c in CAMS:
                    with open(os.path.join(rc, "%s-%s.mp4" % (st, c)), "wb") as f:
                        f.write(b"\0" * 64)
        for kind, folder in (("saved", "SavedClips"), ("sentry", "SentryClips")):
            os.makedirs(os.path.join(tc, folder), exist_ok=True)
            for e in events:
                if e["kind"] != kind or not keep(e["t"]):
                    continue
                ed = os.path.join(tc, folder, e["id"])
                os.makedirs(ed, exist_ok=True)
                for k in range(e["n"]):
                    st = car_stamp(e["t"] - (e["n"] - 1 - k) * 60)
                    for c in CAMS:
                        with open(os.path.join(ed, "%s-%s.mp4" % (st, c)), "wb") as f:
                            f.write(b"\0" * 64)
                with open(os.path.join(ed, "event.json"), "w", encoding="utf-8") as f:
                    json.dump(dict(timestamp=datetime.datetime.fromtimestamp(e["t"], ZONE).strftime("%Y-%m-%dT%H:%M:%S"), city=rnd.choice(SUBURBS), street=rnd.choice(STREETS),
                                   est_lat="%.5f" % e["p"][0], est_lon="%.5f" % e["p"][1], reason="sentry_aware_object_detection" if kind == "sentry" else "user_interaction_dashcam_icon_tapped",
                                   camera="0"), f)
        out[role] = tc
    return out


def write_footage_stores(data, mins, events, roots, rnd):
    from lib import teslacam_timeline as TL
    from lib import teslacam_ledger as TLG
    store = TL.Store(os.path.join(data, "teslacam_timeline"))
    for t, moving, p in mins:                               # telemetry read from most minutes (a few never read)
        if rnd.random() < 0.05:
            continue
        n = 60
        v = [rnd.uniform(15, 75) if moving else 0.0 for _ in range(n)]
        gear = [1 if moving else 0] * n
        if moving and rnd.random() < 0.03:
            gear[:10] = [0] * 10
        S = dict(n=n, dur=60, v=v, lat=[p[0] + k * 1e-5 for k in range(n)], lon=[p[1] + k * 1e-5 for k in range(n)], hd=[90] * n, ap=[1 if rnd.random() < 0.3 else 0] * n,
                 steer=[0] * n, accel=[20 if moving else 0] * n, br=[0] * n, blk=[0] * n, gear=gear)
        store.put(car_stamp(t), S)
    store.flush()
    led = TLG.Ledger(data)                                  # the footage log, made by the app's own scan of each fake drive
    for role in sorted(roots):
        led.scan(role, roots[role])
    prov = {}                                               # where each front clip came from, as the background keeper records it
    for t, _mv, p in mins:
        st = car_stamp(t)
        rec = dict(created=t + rnd.uniform(0, 0.9), modified=t + 60, name_stamp=naive_epoch(st), offset=int(round((naive_epoch(st) - t) / 900.0) * 900),
                   street=rnd.choice(STREETS), suburb=rnd.choice(SUBURBS), size=64, tracks_agree=True, sources={"A": dict(rel="RecentClips/%s-front.mp4" % st, size=64, read="2026-11-01T00:00:00Z")})
        if rnd.random() < 0.01:                              # a few clips whose name is out from their own recording time
            rec["created"] = t - 45
        prov.setdefault(st[:10], {})["recent||%s-front" % st] = [rec]
    for e in events:
        for k in range(e["n"]):
            t = e["t"] - (e["n"] - 1 - k) * 60
            st = car_stamp(t)
            prov.setdefault(st[:10], {})["%s|%s|%s-front" % (e["kind"], e["id"], st)] = [dict(created=t, modified=t + 60, name_stamp=naive_epoch(st), offset=0,
                                                                                               street=rnd.choice(STREETS), suburb=rnd.choice(SUBURBS), size=64)]
    pd = os.path.join(data, "teslacam_provenance")
    os.makedirs(pd, exist_ok=True)
    for day, j in prov.items():
        with open(os.path.join(pd, day + ".json"), "w", encoding="utf-8") as f:
            json.dump(j, f, separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", help="folder to make (must not exist, or be empty; not inside the repository)")
    ap.add_argument("--days", type=int, default=150, help="how many days of driving (default 150)")
    ap.add_argument("--scale", type=float, default=1.0, help="share of the default size of the TeslaCam drives and charger list (default 1.0)")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    out = os.path.realpath(a.out)
    if out == REPO or out.startswith(REPO + os.sep):
        print("The fixture must not be inside the repository: %s" % out)
        return 1
    if os.path.isdir(out) and os.listdir(out):
        print("Not empty: %s" % out)
        return 1
    rnd = random.Random(a.seed)
    data, home = os.path.join(out, "data"), os.path.join(out, "home")
    os.makedirs(data, exist_ok=True)
    end = datetime.datetime(2026, 11, 1, 12, tzinfo=ZONE).timestamp()
    days = max(3, int(round(a.days * min(1.0, a.scale))) if a.scale < 1 else a.days)
    drives, charges, idles = plan(days, rnd, end)
    write_tessie(data, drives, charges, idles, rnd)
    write_registries(data, rnd, max(50, int(6000 * a.scale)))
    mins = recordings(drives, idles, rnd)
    events = []
    for i in range(int(len(drives) * 0.12)):                  # Saved events on some drives, Sentry events on some parks
        d = rnd.choice(drives)
        t = d["t0"] + rnd.uniform(60, d["t1"] - d["t0"])
        events.append(dict(kind="saved", t=t - t % 60 + 59, n=rnd.randint(1, 10), p=d["p0"]))
    for i in range(int(len(idles) * 0.25)):
        x = rnd.choice(idles)
        t = x["t0"] + rnd.uniform(600, max(700, x["t1"] - x["t0"]))
        events.append(dict(kind="sentry", t=t - t % 60 + 59, n=rnd.randint(1, 10), p=x["p"]))
    seen = set()
    for e in events:
        e["id"] = car_stamp(e["t"])
    events = [e for e in events if not (e["id"] in seen or seen.add(e["id"]))]
    roots = make_drives(home, mins, events, rnd, a.scale)
    write_footage_stores(data, mins, events, roots, rnd)
    shutil.copy(os.path.join(REPO, "config.example.json"), os.path.join(out, "config.json"))
    with open(os.path.join(out, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("directories", {})["data"] = data
    with open(os.path.join(out, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    nfiles = {r: sum(len(fs) for _d, _s, fs in os.walk(p)) for r, p in roots.items()}
    print("fixture: %s\n  %d drives, %d charges, %d idles, %d recorded minutes, %d events\n  TeslaCam files: %s" % (
        out, len(drives), len(charges), len(idles), len(mins), len(events), ", ".join("%s %d" % kv for kv in sorted(nfiles.items()))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
