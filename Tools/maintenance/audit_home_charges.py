#!/usr/bin/env python3
"""audit_home_charges.py - check every Home charge's cost against the model, Tessie and the master CSV.

For each Home charge: the cost the scripts work out now (lib/charge_cost_model.home_charge_cost: solar share at the feed-in it replaced, grid share at the usage rate
+ GST), what Tessie holds, what charges_master.csv holds, the solar share, and what looks wrong (no solar data so priced as all grid, low data coverage, no cumulative
export figure, Energy Used missing, a model cost that differs from Tessie's by more than the tolerance, a charge not found in Tessie's cache).

Working a charge out reads a lot of solar data, so the run is resumable: every answer is kept in <data_home>/charges/reports/home_audit_progress.json and a run stops
after --budget seconds; run it again to carry on. --fresh starts over (a changed model or new solar readings). Writes <data_home>/charges/reports/home_charges_audit_<stamp>.csv
when every charge has an answer, and prints the charges that need a look.

    ./audit_home_charges.py [--budget 150] [--fresh] [--tolerance 0.05]
"""
import os as _os, sys as _sys; _sys.path[:0] = [p for p in (_os.path.dirname(_os.path.abspath(__file__)), _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "commands"), _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))) if p not in _sys.path]      # the tools beside it, the commands, and Tools/ (lib, providers)
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS_DIR)

from report_all_charges_cost import load_deduped_analyzer  # noqa: E402
from lib.charge_cost_model import (HOME_EMOJI, find_cache_record_by_epoch, home_charge_cost, load_raw_tessie_cache)  # noqa: E402


def flags(row, tol):
    """What looks wrong about one audited charge: a list of short phrases."""
    out = []
    if row["source"] != "tiered_solar_model":
        out.append("no solar data: priced as all grid")
    for n in row["notes"]:
        if n not in ("",) and not n.startswith("no PV data"):
            out.append(n)
    if row["tessie_cost"] is None:
        out.append("not found in Tessie's cache")
    elif row["cost"] is not None and abs(row["cost"] - row["tessie_cost"]) > tol:
        out.append("Tessie holds $%.2f, model $%.2f" % (row["tessie_cost"], row["cost"]))
    if row["cost"] is not None and row["kwh"] and row["cost"] / row["kwh"] > 0.35:
        out.append("above $0.35/kWh: check the usage rate")
    if row["cost"] is not None and row["cost"] < 0:
        out.append("negative cost")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--budget", type=float, default=150.0, help="seconds to work before stopping (default 150)")
    ap.add_argument("--fresh", action="store_true", help="forget the answers kept so far")
    ap.add_argument("--tolerance", type=float, default=0.05, help="dollars of difference from Tessie that is flagged (default 0.05)")
    a = ap.parse_args()
    t0 = time.time()
    analyzer = load_deduped_analyzer()
    from lib.power_demand import load_rate_periods
    periods = load_rate_periods()
    reports = os.path.join(analyzer.charges_dirs[0], "reports")
    os.makedirs(reports, exist_ok=True)
    prog_path = os.path.join(reports, "home_audit_progress.json")
    prog = {}
    if not a.fresh and os.path.isfile(prog_path):
        try:
            with open(prog_path, encoding="utf-8") as f:
                prog = json.load(f)
        except (OSError, ValueError):
            prog = {}
    sessions = analyzer.reconcile()
    _recs, by_epoch = load_raw_tessie_cache(analyzer.repo_root)
    home = [s for s in sessions if s["emoji"] == HOME_EMOJI and s.get("raw_charge")]
    home.sort(key=lambda s: s["raw_charge"].get("start_utc_epoch") or 0)
    print("[audit] %d Home charges; %d answered before" % (len(home), sum(1 for s in home if str(s["raw_charge"].get("start_utc_epoch")) in prog)))
    stopped = False
    for s in home:
        raw = s["raw_charge"]
        key = str(raw.get("start_utc_epoch"))
        if key in prog:
            continue
        if time.time() - t0 > a.budget:
            stopped = True
            break
        rec = find_cache_record_by_epoch(by_epoch, raw.get("start_utc_epoch"))
        row = dict(when=s["datetime_str"], kwh=raw.get("energy_used_kwh") or raw.get("energy_added_kwh"), master_cost=s.get("tessie_cost"),
                   tessie_cost=rec.get("cost") if rec else None, cost=None, source="", pv_pct=None, notes=[])
        if not raw.get("ended_at"):
            row.update(source="in progress", notes=["still in progress: not priced"])
        else:
            try:
                p = home_charge_cost(raw, periods)
                row.update(cost=round(p["cost"], 4), source=p["source"], pv_pct=None if p["pv_pct"] is None else round(p["pv_pct"] * 100, 1), notes=p["notes"])
            except Exception as e:                      # one bad charge must not stop the audit
                row.update(source="error", notes=["%s: %s" % (type(e).__name__, e)])
        row["flags"] = flags(row, a.tolerance)
        prog[key] = row
        with open(prog_path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(prog, f)
        os.replace(prog_path + ".tmp", prog_path)
    done = sum(1 for s in home if str(s["raw_charge"].get("start_utc_epoch")) in prog)
    print("[audit] %d of %d answered%s" % (done, len(home), " - run again to carry on" if stopped else ""))
    if stopped:
        return 2
    rows = [prog[str(s["raw_charge"].get("start_utc_epoch"))] for s in home]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = os.path.join(reports, "home_charges_audit_%s.csv" % stamp)
    cols = ["when", "kwh", "cost", "source", "pv_pct", "tessie_cost", "master_cost"]
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Datetime", "kWh", "Model cost", "Source", "Solar %", "Tessie cost", "Master CSV cost", "Flags"])
        for r in rows:
            w.writerow([r[c] for c in cols] + ["; ".join(r["flags"])])
    bad = [r for r in rows if r["flags"]]
    tot = lambda k: sum(r[k] or 0 for r in rows)  # noqa: E731
    print("[audit] wrote %s" % out)
    print("[audit] model $%.2f, Tessie $%.2f, master $%.2f over %.1f kWh" % (tot("cost"), tot("tessie_cost"), tot("master_cost"), tot("kwh")))
    print("[audit] %d of %d charges need a look:" % (len(bad), len(rows)))
    for r in bad:
        print("  %s  %6.2f kWh  model %s  Tessie %s  solar %s%%  - %s" % (r["when"], r["kwh"] or 0, r["cost"], r["tessie_cost"], r["pv_pct"], "; ".join(r["flags"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
