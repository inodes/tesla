#!/usr/bin/env python3
"""
report_all_charges_cost.py - "Our calculated/known cost" vs "Tessie's own
reported cost" for every real Tessie charge record (REQ-147).

User's request (2026-09-26, verbatim): "go through all the charges in
Tessie (including DC charging). For Home charging, you'll need to work
it out. For DC charging you know the amounts from invoice. Do a table
for all with our calculated or known cost vs Tessie."

The actual per-charge cost computation lives in lib/charge_cost_model.py
(shared with analyse_charging.py's --sync-tessie-costs, REQ-148, which
pushes these same figures back to Tessie's own live account via
set_cost) - this script is just the read-only local report over it.

Data-quality note (handled in lib/charge_cost_model.py, kept here for
visibility): analyse_charging.py's own load_charges() merges the live
charges_master.csv together with several old archived/pre-migration
master backups under charges/archive/ that use slightly different
"Started At" text formats, producing near-duplicate (same underlying
charge, different dedup key) entries when loaded unmodified - confirmed
live (478 raw charges loaded vs 239 real rows in the live
charges_master.csv). This script loads charges/invoices/detailed
telemetry once, then FILTERS self.charges down to only rows whose
source_file is the live charges_master.csv before calling reconcile() -
avoiding both the duplication and any risk of the invoice-matching step
handing one real invoice to a duplicate row instead of the real one.

Output: a CSV written next to charges_master.csv itself
(<data_home>/charges/reports/all_charges_cost_comparison_<UTCSTAMP>.csv).
"""
import csv
import os
import sys
from datetime import datetime, timezone

TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS_DIR)

from analyse_charging import TessieChargingAnalyzer  # noqa: E402
from lib.charge_cost_model import compute_all_charge_costs  # noqa: E402


def load_deduped_analyzer():
    """A TessieChargingAnalyzer with self.charges already filtered down
    to the live charges_master.csv only (see module docstring) - shared
    by this script and analyse_charging.py's --sync-tessie-costs so
    both compute over exactly the same 239-row set."""
    analyzer = TessieChargingAnalyzer()
    analyzer.load_charges()
    analyzer.load_invoices()
    analyzer.load_detailed_charges()
    live_master = os.path.join(analyzer.charges_dirs[0], "charges_master.csv")
    before = len(analyzer.charges)
    analyzer.charges = [c for c in analyzer.charges if c["source_file"] == live_master]
    print(f"[info] loaded {before} raw charge rows across current+archived master files; "
          f"filtered to {len(analyzer.charges)} rows from the live master CSV only ({live_master})")
    analyzer._loaded = True
    return analyzer


def main():
    analyzer = load_deduped_analyzer()
    results = compute_all_charge_costs(analyzer)
    print(f"[info] raw Tessie API cache matched for {sum(1 for r in results if r['cache_record'])} "
          f"of {len(results)} charges")

    rows_out = []
    n_home = n_dc_invoice = n_dc_theoretical = n_dc_unknown = 0
    n_no_cache_match = n_no_pv_data = n_flat_tier1_fallback = 0
    sum_our_cost = sum_tessie_cost = 0.0
    sum_our_cost_home = sum_tessie_cost_home = 0.0
    sum_our_cost_dc = sum_tessie_cost_dc = 0.0

    for r in results:
        s = r["session"]
        raw = r["raw_charge"]
        is_home = r["is_home"]
        our_cost = r["our_cost"]
        tessie_original_cost = r["tessie_original_cost"]
        notes = list(r["notes"])

        if r["tessie_cost_source"] != "raw_api_cache":
            n_no_cache_match += 1
        if is_home:
            n_home += 1
            if r["our_cost_source"] == "flat_rate_fallback(no_pv_data)":
                n_no_pv_data += 1
            if any("flat tier-1" in n for n in notes):
                n_flat_tier1_fallback += 1
        else:
            if r["our_cost_source"] and r["our_cost_source"].startswith("invoice"):
                n_dc_invoice += 1
            elif r["our_cost_source"] == "theoretical(expected_rate x kWh, no invoice matched)":
                n_dc_theoretical += 1
            else:
                n_dc_unknown += 1

        diff = (our_cost - tessie_original_cost) if (our_cost is not None and tessie_original_cost is not None) else None
        if our_cost is not None and tessie_original_cost is not None:
            sum_our_cost += our_cost
            sum_tessie_cost += tessie_original_cost
            if is_home:
                sum_our_cost_home += our_cost
                sum_tessie_cost_home += tessie_original_cost
            else:
                sum_our_cost_dc += our_cost
                sum_tessie_cost_dc += tessie_original_cost

        rows_out.append({
            "Datetime": s["datetime_str"],
            "Type": "Home" if is_home else "DC/Public",
            "Place": s["place_name"],
            "Network": s["network"],
            "kWh": round(raw["energy_used_kwh"], 3) if raw else None,
            "Solar %": round(r["pv_pct"] * 100, 1) if r["pv_pct"] is not None else "",
            "Our Cost": round(our_cost, 4) if our_cost is not None else "",
            "Our Cost Source": r["our_cost_source"],
            "Tessie Reported Cost": round(tessie_original_cost, 2) if tessie_original_cost is not None else "",
            "Tessie Cost Source": r["tessie_cost_source"],
            "Master CSV Cost (may be patched)": s.get("tessie_cost"),
            "Diff (Our - Tessie)": round(diff, 4) if diff is not None else "",
            "Notes": "; ".join(notes),
        })

    reports_dir = os.path.join(analyzer.charges_dirs[0], "reports")
    os.makedirs(reports_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(reports_dir, f"all_charges_cost_comparison_{stamp}.csv")
    fieldnames = list(rows_out[0].keys())
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)

    print()
    print(f"Wrote {len(rows_out)} rows to: {out_path}")
    print()
    print(f"Home charges: {n_home} | DC/public: invoice-matched {n_dc_invoice}, "
          f"theoretical fallback {n_dc_theoretical}, unknown {n_dc_unknown}")
    print(f"Data-quality flags: {n_no_cache_match} row(s) with no raw-cache match "
          f"(fell back to master CSV cost), {n_no_pv_data} home charge(s) with no PV "
          f"data at all, {n_flat_tier1_fallback} home charge(s) using the flat tier-1 "
          f"fallback (no cumulative-export figure available)")
    print()
    print(f"TOTAL  - our cost: ${sum_our_cost:,.2f}  |  Tessie's reported cost: ${sum_tessie_cost:,.2f}  "
          f"|  diff: ${sum_our_cost - sum_tessie_cost:,.2f}")
    print(f"HOME   - our cost: ${sum_our_cost_home:,.2f}  |  Tessie's reported cost: ${sum_tessie_cost_home:,.2f}  "
          f"|  diff: ${sum_our_cost_home - sum_tessie_cost_home:,.2f}")
    print(f"DC/PUB - our cost: ${sum_our_cost_dc:,.2f}  |  Tessie's reported cost: ${sum_tessie_cost_dc:,.2f}  "
          f"|  diff: ${sum_our_cost_dc - sum_tessie_cost_dc:,.2f}")
    print()

    biggest = sorted(
        (r for r in rows_out if r["Diff (Our - Tessie)"] != ""),
        key=lambda r: abs(r["Diff (Our - Tessie)"]),
        reverse=True,
    )[:15]
    print("Biggest 15 discrepancies (|our cost - Tessie's reported cost|):")
    for r in biggest:
        print(f"  {r['Datetime']}  {r['Type']:10s}  {r['Place']:30.30s}  "
              f"our=${r['Our Cost']:<8}  tessie=${r['Tessie Reported Cost']:<8}  "
              f"diff=${r['Diff (Our - Tessie)']}")

    return out_path


if __name__ == "__main__":
    main()
