"""app_alerts.py - Tessie firmware alerts as a plain-English timeline tied to drives, charges and parked periods (REQ-202).

Reads both copies of the alert history in the Tesla data folder and merges them (an alert present in both is counted once):
    firmware_alerts/firmware_alerts_master.csv   from the Tessie API (sync_api_tessie.py --firmware-alerts), kept up to date
    firmware_alerts_history.csv                  the earlier export from dash.tessie.com/firmware/alerts (richer text)
Same alert repeating within 10 minutes is shown once as an episode with a count. The meaning of each alert comes from
firmware_alert_catalog.py. Nothing here writes anything.
"""
import csv
import datetime
import os

import clip_clock as CC
import firmware_alert_catalog as CAT

GAP_S = 600


def _rows(p):
    try:
        with open(p, encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def _local_epoch(s):
    try:
        d = datetime.datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S" if len(s) >= 19 else "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return int(CC.from_local(CC.naive(d.strftime("%Y-%m-%d_%H-%M-%S"))))           # the alert's local clock (app.timezone) turned into UTC


def deps(data_dir):
    return [os.path.join(data_dir, "firmware_alerts", "firmware_alerts_master.csv"), os.path.join(data_dir, "firmware_alerts_history.csv")]


def load(data_dir):
    """Merged events [dict(t, name, row)], oldest first."""
    by = {}
    for r in _rows(deps(data_dir)[0]):
        try:
            t = int(r["Timestamp (UTC)"])
        except (KeyError, ValueError):
            continue
        by.setdefault(r.get("Name", ""), []).append(dict(t=t, name=r.get("Name", ""), row=r, src="api"))
    for r in _rows(deps(data_dir)[1]):
        t = _local_epoch(r.get("Timestamp (AEST)", ""))
        if t is None:
            continue
        lst = by.setdefault(r.get("Name", ""), [])
        if any(abs(e["t"] - t) <= 90 for e in lst if e["src"] == "api"):
            for e in lst:       # keep the API row's time but borrow the export's richer text
                if abs(e["t"] - t) <= 90 and e["src"] == "api":
                    e["row"] = dict(r, **{k: v for k, v in e["row"].items() if v})
                    break
            continue
        lst.append(dict(t=t, name=r.get("Name", ""), row=r, src="export"))
    out = [e for v in by.values() for e in v]
    out.sort(key=lambda e: e["t"])
    return out


def build(data_dir, drives, charges, idles):
    ev = load(data_dir)
    # same alert repeating = one episode
    eps, open_ = [], {}
    for e in ev:
        cur = open_.get(e["name"])
        if cur and e["t"] - cur["t1"] <= GAP_S:
            cur["t1"] = e["t"]
            cur["n"] += 1
            continue
        cur = dict(name=e["name"], t0=e["t"], t1=e["t"], n=1, row=e["row"])
        open_[e["name"]] = cur
        eps.append(cur)
    wins = [("drive", d["id"], d["t0"] - 60, d["t1"] + 60, d) for d in drives] + [("charge", c["id"], c["t0"] - 60, c["t1"] + 60, c) for c in charges] + \
           [("idle", i["id"], i["t0"] - 60, i["t1"] + 60, i) for i in idles]
    wins.sort(key=lambda w: w[2])
    out = []
    for p in eps:
        x = CAT.explain(p["name"], p["row"])
        r = p["row"]
        ctx = None
        for k, i, a, b, o in wins:
            if a <= p["t0"] <= b and (ctx is None or {"drive": 0, "charge": 1, "idle": 2}[k] < {"drive": 0, "charge": 1, "idle": 2}[ctx["k"]]):
                ctx = dict(k=k, id=i, label=(o.get("frm", "") + " → " + o.get("to", "")) if k == "drive" else (o.get("place") or ""))
        lt = CC.local_iso(p["t0"])                                                    # UTC kept in t0 / t1; the local clock is only for showing
        out.append(dict(id="%s:%d" % (p["name"], p["t0"]), name=p["name"], t0=p["t0"], t1=p["t1"], n=p["n"], day=lt[:10], hm=lt[11:16],
                        title=x["title"], category=x["category"], severity=x["severity"], what=x["what"], advice=x["advice"], known=x["known"],
                        screen=(r.get("Customer Facing Message 1") or "").strip(), rate=r.get("Incidence Rate") or "", ctx=ctx))
    out.sort(key=lambda e: -e["t0"])
    types = {}
    for e in out:
        t = types.setdefault(e["name"], dict(name=e["name"], title=e["title"], category=e["category"], severity=e["severity"], what=e["what"], advice=e["advice"], screen=e["screen"],
                                              rate=e["rate"], episodes=0, events=0, first=e["t0"], last=e["t0"], known=e["known"]))
        t["episodes"] += 1
        t["events"] += e["n"]
        t["first"] = min(t["first"], e["t0"])
        t["last"] = max(t["last"], e["t0"])
    return dict(episodes=out, types=sorted(types.values(), key=lambda t: (CAT.SEVERITY_ORDER[t["severity"]], -t["episodes"])),
                first=out[-1]["t0"] if out else None, last=out[0]["t0"] if out else None, events=len(ev))
