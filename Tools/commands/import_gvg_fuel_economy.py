#!/usr/bin/env python3
"""import_gvg_fuel_economy.py - the comparison cars' L/100km and CO2 from Green Vehicle Guide CSV exports.

NAME
    import_gvg_fuel_economy.py - set the comparison cars' fuel figures from the Green Vehicle Guide

SYNOPSIS
    ./Tools/tesla sync fuel-economy [CSV or folder ...] [--write] [--pick car=N ...] [--data-dir DIR]

DESCRIPTION
    On greenvehicleguide.gov.au search for a make and use "Download as CSV". With no files given this reads
    GVG-*.csv from <data folder>/GVG and from Downloads. Each comparison car gets the guide's combined, urban
    and extra-urban L/100km and CO2. A car with no row, or with several different rows, is listed and left
    alone; choose a row with --pick car=N (N is the number in the list). Running costs are not changed.
    Without --write it only prints before and after. With --write it saves comparison_vehicles.json in the
    data folder (never the repo).
"""
import argparse
import os
import sys

import os as _os, sys as _sys; _sys.path[:0] = [p for p in (_os.path.dirname(_os.path.abspath(__file__)), _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))) if p not in _sys.path]      # the commands beside it, and Tools/ (lib, providers)

from lib import gvg_import as G  # noqa: E402
from lib import fuel_common as F  # noqa: E402


def _data_dir(arg):
    if arg:
        return os.path.abspath(os.path.expanduser(arg))
    from lib.tessie_api_common import resolve_tessie_data_home
    return resolve_tessie_data_home()


def _fig(spec):
    c = (spec.get("consumption") or {}).get("combined_l_per_100km")
    return "%.1f" % c if isinstance(c, (int, float)) else "-"


def run(files, data_dir, write=False, picks=None, out=print):
    """Returns (changed car keys, left alone car keys)."""
    rows = G.read_rows(files)
    out("%d row%s from %d file%s" % (len(rows), "" if len(rows) == 1 else "s", len(files), "" if len(files) == 1 else "s"))
    if not rows:
        out("No Green Vehicle Guide rows found. Download a CSV from greenvehicleguide.gov.au (Download as CSV).")
        return [], []
    current = F.load_comparison_vehicles(data_dir)["vehicles"]
    rules = G.load_rules(data_dir)
    new, left = {}, []
    for key, rule in rules.items():
        if key not in current:
            continue
        row, cands, note = G.choose(rows, rule, (picks or {}).get(key))
        name = current[key].get("name", key)
        if row is None:
            left.append(key)
            out("  %-24s left alone (%s)" % (key, note))
            if len(cands) > 0 and note.endswith("different rows"):
                for i, r in enumerate(cands, 1):
                    f = G._figures(r)
                    out("      %d  %s %s %s %s %s: %s/%s/%s L/100km, %s g/km" % (
                        i, r.get("ModelReleaseYear"), r.get("Make"), r.get("Model"), r.get("Variant"),
                        r.get("TransmissionTypeDescription"), f[0], f[1], f[2], f[3]))
            continue
        new[key] = G.apply_row(current[key], row)
        g = new[key]["gvg"]
        out("  %-24s %s -> %s L/100km  (%s %s %s %s; %s)" % (
            key, _fig(current[key]), _fig(new[key]), g["model_year"], g["make"], g["model"], g["variant"], note))
    out("")
    out("%d car%s set, %d left alone." % (len(new), "" if len(new) == 1 else "s", len(left)))
    if new and write:
        path = G.merge_into_file(os.path.join(data_dir, "comparison_vehicles.json"), new)
        out("Saved to %s" % path)
    elif new:
        out("Nothing saved. Add --write to save.")
    return sorted(new), left


def main(argv=None):
    ap = argparse.ArgumentParser(description="Set the comparison cars' fuel figures from Green Vehicle Guide CSV exports.")
    ap.add_argument("files", nargs="*", help="CSV files or folders (default: GVG-*.csv in the data folder's GVG folder and Downloads)")
    ap.add_argument("--data-dir", help="the data folder (default: the usual one)")
    ap.add_argument("--write", action="store_true", help="save comparison_vehicles.json in the data folder")
    ap.add_argument("--pick", action="append", default=[], metavar="CAR=N", help="row number for a car with several rows")
    a = ap.parse_args(argv)
    picks = {}
    for p in a.pick:
        k, _, n = p.partition("=")
        if not n.isdigit():
            ap.error("--pick wants car=N, e.g. --pick corolla_hybrid=2")
        picks[k] = int(n)
    d = _data_dir(a.data_dir)
    run(G.find_files(a.files, d), d, a.write, picks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
