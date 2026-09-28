"""
lib/charge_cost_model.py - shared "what should this charge really cost"
computation (REQ-147/148), used by both report_all_charges_cost.py (a
read-only local report) and analyse_charging.py's --sync-tessie-costs
(which pushes the same figures back to Tessie's own live account via
set_cost). Kept in lib/ rather than in either of those two files so
neither has to import the other - report_all_charges_cost.py already
imports TessieChargingAnalyzer FROM analyse_charging.py, so the reverse
import would be circular.

Method (unchanged from REQ-147's own report):
  - Home charges (emoji "🏠", via analyse_charging.py's own GPS/registry-
    based resolve_location() - not the raw "Saved Location" CSV text)
    are priced with analyse_session_cost.py's real tiered/solar-aware
    opportunity-cost model (evaluate_tessie_charge()), applied directly
    to Tessie's own Started/Ended At window and Energy Used (kWh).
  - DC/public charges are priced from the real matched invoice
    (analyse_charging.py's own reconcile()) - falling back to the
    expected-tariff-rate theoretical cost only when no invoice matched.
  - "Tessie's own reported cost" comes from the raw, un-patched Tessie
    API cache (resources/api_cache/charges.json), matched by UTC epoch -
    this is also where each charge's real Tessie numeric `id` comes
    from, needed to target set_cost's own {vin}/charges/{id}/set_cost
    URL - charges_master.csv itself carries no such id column at all.
"""
import bisect
import json
import os

HOME_EMOJI = "\U0001F3E0"


def load_raw_tessie_cache(repo_root):
    """id-> and epoch-sorted indexes into the raw, un-patched Tessie API
    cache (resources/api_cache/charges.json) - ground truth for what
    Tessie itself ORIGINALLY reported, independent of anything this
    repo's own invoice-reconciliation step may have since patched into
    the master CSV's own Cost column, and the only place carrying each
    charge's real Tessie numeric id (charges_master.csv has none)."""
    path = os.path.join(repo_root, "resources", "api_cache", "charges.json")
    if not os.path.isfile(path):
        return {}, []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    records = data.get("records", {})
    by_epoch = sorted(
        ((int(r["started_at"]), r) for r in records.values() if r.get("started_at") is not None),
        key=lambda t: t[0],
    )
    return records, by_epoch


def find_cache_record_by_epoch(by_epoch, target_epoch, tolerance_s=120):
    """Nearest raw-cache record to target_epoch within tolerance_s, or
    None. by_epoch must be sorted ascending (see load_raw_tessie_cache)."""
    if target_epoch is None or not by_epoch:
        return None
    epochs = [e for e, _ in by_epoch]
    idx = bisect.bisect_left(epochs, target_epoch)
    best = None
    best_diff = None
    for cand_idx in (idx - 1, idx):
        if 0 <= cand_idx < len(by_epoch):
            e, r = by_epoch[cand_idx]
            diff = abs(e - target_epoch)
            if diff <= tolerance_s and (best_diff is None or diff < best_diff):
                best = r
                best_diff = diff
    return best


def compute_all_charge_costs(analyzer):
    """Runs analyzer.reconcile() (assumes analyzer.charges has ALREADY
    been filtered to the live charges_master.csv only - see both
    callers' own dedup step, needed because analyse_charging.py's
    load_charges() otherwise also merges in old archived master
    backups) and returns one dict per real charge:

      session, is_home, raw_charge, our_cost, our_cost_source,
      tessie_original_cost, tessie_cost_source, cache_record (the raw
      resources/api_cache/charges.json record, or None - carries the
      real Tessie numeric `id` a caller needs for set_cost), pv_pct,
      notes (list of str).
    """
    from analyse_session_cost import evaluate_tessie_charge
    from power_demand_calculator import load_rate_periods

    sessions = analyzer.reconcile()
    cache_records, cache_by_epoch = load_raw_tessie_cache(analyzer.repo_root)
    periods = load_rate_periods()

    results = []
    for s in sessions:
        raw = s["raw_charge"]
        is_home = s["emoji"] == HOME_EMOJI
        start_epoch = raw.get("start_utc_epoch") if raw else None
        cache_rec = find_cache_record_by_epoch(cache_by_epoch, start_epoch)
        if cache_rec is not None:
            tessie_original_cost = cache_rec.get("cost")
            tessie_cost_source = "raw_api_cache"
        else:
            tessie_original_cost = s.get("tessie_cost")
            tessie_cost_source = "master_csv_fallback(no_cache_match)"

        notes = []
        our_cost = None
        our_cost_source = None
        pv_pct = None

        if is_home:
            tessie_row = {
                "started": raw["started_at"],
                "ended": raw["ended_at"],
                "kwh": raw["energy_used_kwh"],
                "tessie_cost": raw["cost"],
                "tessie_cost_per_kwh": raw["cost_per_kwh"],
            }
            result = evaluate_tessie_charge(tessie_row, periods)
            if result["real_cost_incl_gst"] is not None:
                our_cost = result["real_cost_incl_gst"]
                our_cost_source = "tiered_solar_model"
            else:
                our_cost = result["base_cost_incl_gst"]
                our_cost_source = "flat_rate_fallback(no_pv_data)"
                notes.append("no PV data for this window - priced as 100% grid import")
            if result.get("used_flat_tier1_fallback"):
                notes.append("no cumulative-export figure available - assumed flat tier-1")
            if result.get("pv_coverage") is not None and result["pv_coverage"] < 0.9:
                notes.append(f"low PV data coverage ({result['pv_coverage']:.0%})")
            pv_pct = result.get("pv_solar_fraction")
        else:
            if s.get("invoice_cost") is not None:
                our_cost = s["invoice_cost"]
                our_cost_source = f"invoice #{s.get('invoice_number') or '?'}"
            elif s.get("theoretical_cost") is not None:
                our_cost = s["theoretical_cost"]
                our_cost_source = "theoretical(expected_rate x kWh, no invoice matched)"
                notes.append("no invoice matched - theoretical cost from expected tariff rate")
            else:
                our_cost_source = "unknown(no invoice, no expected rate)"
                notes.append("no invoice matched and no expected rate available")

        results.append({
            "session": s,
            "raw_charge": raw,
            "is_home": is_home,
            "our_cost": our_cost,
            "our_cost_source": our_cost_source,
            "tessie_original_cost": tessie_original_cost,
            "tessie_cost_source": tessie_cost_source,
            "cache_record": cache_rec,
            "pv_pct": pv_pct,
            "notes": notes,
        })
    return results
