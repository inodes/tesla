"""Green Vehicle Guide CSV exports -> the comparison cars' fuel figures.

The guide (greenvehicleguide.gov.au) has no API. Its search pages offer "Download as CSV"; this module reads
those files, finds one row for each comparison car, and gives back the car's entry with the guide's label
figures (combined, urban and extra-urban L/100km, CO2 g/km). Nothing is guessed: a car with no row, or with
several different rows, is reported and left alone.

The CSV files stay on the owner's machine (the data folder or Downloads), never in the repo.
"""
import copy
import csv
import datetime
import glob
import json
import os
import re

# car key -> how to find its row. make/model are compared without spaces, dashes or case. A model starting
# with "*" matches when it contains the rest. disp = engine litres, fuel = diesel|petrol|hybrid,
# wheels = driven wheels (4 = all), variant = text the variant must contain.
MATCH = {
    "mazda_cx8_diesel": dict(make="Mazda", model=["CX-8"], disp=2.2, fuel="diesel"),
    "petrol_standard": dict(make="Mazda", model=["CX-5"], disp=2.5, fuel="petrol"),
    "mazda_cx5_petrol": dict(make="Mazda", model=["CX-5"], disp=2.5, fuel="petrol"),
    "ford_ranger_diesel": dict(make="Ford", model=["Ranger"], disp=2.0, fuel="diesel", wheels=4),
    "rav4_hybrid": dict(make="Toyota", model=["RAV4"], disp=2.5, fuel="hybrid", wheels=4),
    "hilux_diesel": dict(make="Toyota", model=["HiLux"], disp=2.8, fuel="diesel", wheels=4),
    "dmax_diesel": dict(make="Isuzu", model=["D-MAX"], disp=3.0, fuel="diesel", wheels=4),
    "ford_everest_diesel": dict(make="Ford", model=["Everest"], disp=2.0, fuel="diesel", wheels=4),
    "prado_diesel": dict(make="Toyota", model=["*Prado"], disp=2.8, fuel="diesel", wheels=4),
    "outlander_petrol": dict(make="Mitsubishi", model=["Outlander"], disp=2.5, fuel="petrol", wheels=4),
    "hyundai_tucson_petrol": dict(make="Hyundai", model=["Tucson"], disp=2.0, fuel="petrol", wheels=2),
    "mg_zs_petrol": dict(make="MG", model=["ZS", "ZST"], disp=1.5, fuel="petrol"),
    "corolla_hybrid": dict(make="Toyota", model=["Corolla"], disp=1.8, fuel="hybrid"),
    "corolla_petrol": dict(make="Toyota", model=["Corolla"], disp=2.0, fuel="petrol"),
    "hyundai_i30_petrol": dict(make="Hyundai", model=["i30"], disp=2.0, fuel="petrol"),
    "kia_sportage_diesel": dict(make="Kia", model=["Sportage"], disp=2.0, fuel="diesel", wheels=4),
    "subaru_forester_petrol": dict(make="Subaru", model=["Forester"], disp=2.5, fuel="petrol"),
    "yaris_cross_hybrid": dict(make="Toyota", model=["Yaris Cross"], disp=1.5, fuel="hybrid"),
    "kia_carnival_diesel": dict(make="Kia", model=["Carnival"], disp=2.2, fuel="diesel"),
    "golf_gti_p98": dict(make="Volkswagen", model=["Golf"], disp=2.0, fuel="petrol", variant="GTI"),
    "kluger_hybrid": dict(make="Toyota", model=["Kluger"], disp=2.5, fuel="hybrid", wheels=4),
    "camry_hybrid": dict(make="Toyota", model=["Camry"], disp=2.5, fuel="hybrid"),
}


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _num(s):
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return None


def fuel_class(s):
    s = str(s or "").lower()
    if "diesel" in s:
        return "diesel"
    if "hybrid" in s or "electric" in s:
        return "hybrid"
    if any(w in s for w in ("petrol", "unleaded", "ulp", "premium", "ron")):
        return "petrol"
    return "other"


def file_date(path):
    """The download date in a name like GVG-SearchResults-20261103203613.csv, as YYYY-MM-DD, or None."""
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", os.path.basename(path))
    return "%s-%s-%s" % m.groups() if m else None


def find_files(args, data_dir=None, downloads=None):
    """CSV paths from files and folders given, else GVG-*.csv in <data_dir>/GVG and Downloads."""
    out = []
    if args:
        places, pat = list(args), "*.csv"
    else:
        places = ([os.path.join(data_dir, "GVG")] if data_dir else []) + [downloads or os.path.expanduser("~/Downloads")]
        pat = "GVG-*.csv"
    for p in places:
        if os.path.isdir(p):
            out += sorted(glob.glob(os.path.join(p, pat)))
        elif os.path.isfile(p):
            out.append(p)
    seen, uniq = set(), []
    for p in out:
        r = os.path.realpath(p)
        if r not in seen:
            seen.add(r)
            uniq.append(p)
    return uniq


def read_rows(paths):
    """Every row of every file that has the guide's columns; each gets _file and _date."""
    rows = []
    for p in paths:
        with open(p, newline="", encoding="utf-8-sig") as f:
            rd = csv.DictReader(f)
            if not rd.fieldnames or "FuelConsumptionCombined" not in rd.fieldnames or "Make" not in rd.fieldnames:
                continue
            for r in rd:
                r["_file"] = os.path.basename(p)
                r["_date"] = file_date(p)
                rows.append(r)
    return rows


def load_rules(data_dir=None):
    """MATCH, with any changes the owner keeps in <data_dir>/gvg_matches.json ({car key: rule})."""
    rules = copy.deepcopy(MATCH)
    p = os.path.join(data_dir, "gvg_matches.json") if data_dir else None
    if p and os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            for k, v in (json.load(f) or {}).items():
                if isinstance(v, dict):
                    rules[k] = dict(rules.get(k, {}), **v)
    return rules


def _model_ok(model, wanted):
    m = _norm(model)
    for w in wanted:
        if w.startswith("*"):
            if _norm(w[1:]) in m:
                return True
        elif _norm(w) == m:
            return True
    return False


def _figures(r):
    return tuple(_num(r.get(k)) for k in
                 ("FuelConsumptionCombined", "FuelConsumptionUrban", "FuelConsumptionExtraUrban", "CO2EmissionsCombined"))


def candidates(rows, rule):
    """The rows that fit a rule: newest model year, current models first, sorted so that numbering is stable."""
    out = []
    for r in rows:
        if _norm(r.get("Make")) != _norm(rule["make"]) or not _model_ok(r.get("Model"), rule["model"]):
            continue
        if rule.get("fuel") and fuel_class(r.get("FuelType")) != rule["fuel"]:
            continue
        d = _num(r.get("EngineDisplacement"))
        if rule.get("disp") is not None and (d is None or abs(d - rule["disp"]) > 0.05):
            continue
        if rule.get("wheels") is not None and _num(r.get("DrivingWheelsNo")) != rule["wheels"]:
            continue
        if rule.get("variant") and rule["variant"].lower() not in str(r.get("Variant") or "").lower():
            continue
        if _figures(r)[0] is None:
            continue
        out.append(r)
    cur = [r for r in out if str(r.get("IsCurrentModel")).strip().lower() == "yes"]
    out = cur or out
    if out:
        top = max(int(_num(r.get("ModelReleaseYear")) or 0) for r in out)
        out = [r for r in out if int(_num(r.get("ModelReleaseYear")) or 0) == top]
    out.sort(key=lambda r: (str(r.get("Variant")), str(r.get("TransmissionTypeDescription")), str(r.get("BodyStyle")), _figures(r)))
    return out


def choose(rows, rule, pick=None):
    """(row or None, candidates, note). Several rows with different figures need pick (1-based) to be chosen."""
    c = candidates(rows, rule)
    if not c:
        return None, c, "no row"
    if pick:
        if 1 <= pick <= len(c):
            return c[pick - 1], c, "picked %d of %d" % (pick, len(c))
        return None, c, "pick %d is not 1-%d" % (pick, len(c))
    if len({_figures(r) for r in c}) == 1:
        return c[0], c, "%d variant%s, same figures" % (len(c), "" if len(c) == 1 else "s")
    return None, c, "%d different rows" % len({_figures(r) for r in c})


def apply_row(spec, row):
    """The car's entry with the guide's figures. Running costs and everything else stay as they were."""
    comb, urb, extra, co2 = _figures(row)
    new = copy.deepcopy(spec)
    cons = {"combined_l_per_100km": comb}
    if urb is not None:
        cons["urban_l_per_100km"] = urb
    if extra is not None:
        cons["highway_l_per_100km"] = extra
    new["consumption"] = cons
    new["adr_consumption"] = dict(cons)
    if co2 is not None:
        new["co2_g_per_km"] = int(co2)
    new["gvg"] = {
        "source": "Green Vehicle Guide CSV export",
        "file": row.get("_file"),
        "downloaded": row.get("_date"),
        "model_year": row.get("ModelReleaseYear"),
        "make": row.get("Make"), "model": row.get("Model"), "variant": row.get("Variant"),
        "engine_l": row.get("EngineDisplacement"), "fuel_type": row.get("FuelType"),
        "annual_fuel_cost": _num(row.get("AnnualFuelCost")),
    }
    return new


def merge_into_file(path, entries):
    """Writes entries into {"vehicles": {...}} at path, keeping what else the file holds. Atomic."""
    data = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("vehicles", {}).update(entries)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return path
