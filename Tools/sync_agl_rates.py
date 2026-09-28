#!/usr/bin/env python3
"""
sync_agl_rates.py - REQ-112: scans real AGL bill PDFs (the provider's own
configured invoices folder, PLUS the landing/Downloads folder - a fresh
bill often sits there before being filed away by hand) for their actual
dated tariff rates, and proposes new dated entries for the active power
provider's tariff-rates JSON (agl_tariff_rates.json's "periods" array -
see providers_power/power_base.py's load_rate_periods_from_file()).

Why this exists: the AGL usage-export CSV (sync_power_usage.py) only ever
carries half-hourly METER READINGS - it has no idea what you're actually
charged per kWh. Only the bill PDF itself carries that. Rates were
previously entered by hand after reading an invoice - this instead reads
every real bill PDF's own "Usage and supply charges" table (via
lib.invoice_common.AGLInvoiceExtractor.extract_rates()) and reports
whichever real rate combinations aren't already recorded, so a genuine
tariff change gets caught automatically instead of only when someone
happens to reread a bill by hand.

This NEVER touches an EXISTING period in the real rates file - it only
proposes appending genuinely new ones (a rate combination not already
present), and only when --apply is passed; the default is report-only.
Same 2-point safety net already established for the usage master and
drives/charges masters elsewhere in this repo: back up before writing,
sanity-check (re-read + count) after.

Usage:
    ./sync_agl_rates.py              # scan every real bill PDF, report only
    ./sync_agl_rates.py --apply      # also append genuinely new period(s)
"""

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.invoice_common import PDFTextExtractor, AGLInvoiceExtractor  # noqa: E402
from lib.tessie_api_common import load_config  # noqa: E402
from lib.archive_naming import next_archive_path  # noqa: E402
from providers_power import get_power_provider  # noqa: E402

RATE_FIELDS = (
    "usage_rate_per_kwh",
    "supply_charge_per_day",
    "solar_feed_in_tier1_rate_per_kwh",
    "solar_feed_in_tier2_rate_per_kwh",
    # REQ-152: a genuine TOU/tou3 period's usage_rate_per_kwh is
    # intentionally None (REQ-149/151) - these three carry the real
    # per-tier rates instead, so both the raw-candidate dict comprehension
    # and the consecutive-period-collapsing key below (and
    # already_present()'s comparison) see the real tiered numbers, not
    # just a None that would wrongly collapse every TOU period together.
    "peak_rate_per_kwh",
    "shoulder_rate_per_kwh",
    "offpeak_rate_per_kwh",
)


def resolve_landing_dir():
    cfg = load_config()
    val = cfg.get("directories", {}).get("landing") or "~/Downloads"
    return os.path.abspath(os.path.expanduser(val))


def find_agl_bill_pdfs(dirs, extractor, log=print):
    """Scans every directory in `dirs` for a PDF this extractor detects as
    its own (by content, not filename - so a not-yet-renamed download in
    Downloads is still found). Dedup by basename across folders."""
    out = []
    seen = set()
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.lower().endswith(".pdf") or f.startswith("."):
                continue
            if f in seen:
                continue
            p = os.path.join(d, f)
            try:
                txt = PDFTextExtractor.extract_text(p)
            except Exception as e:
                log(f"  ⚠ Could not read {f}: {e}")
                continue
            if not txt or not extractor.detect(txt, p):
                continue
            seen.add(f)
            out.append((p, txt))
    return out


def collect_candidate_periods(bills, extractor, log=print):
    """bills: [(path, text), ...] -> sorted, collapsed list of candidate
    periods (consecutive bills sharing the same usage/supply/solar rate
    tuple collapse into one period at the EARLIEST date that combination
    was observed). Demand is tracked separately per RATE_FIELDS-collapsing
    note in the module docstring - it doesn't reliably split on the same
    boundary as the other rates (confirmed on the one real bill whose own
    billing period straddled a genuine rate change).

    REQ-141: a GAS bill (extractor.extract()'s own service_type field,
    already fuel-aware) is skipped here entirely rather than fed to
    extract_rates() - that function's "Usage and supply charges...Total
    charges" table regex isn't fuel-aware, so on a real gas bill it was
    matching THAT bill's own gas supply-charge/demand-shaped lines and
    reporting them as if they were electricity tariff numbers (confirmed
    live: several periods in this account's own real rates file, sourced
    ONLY from Statement_gas_*.pdf, carried a supply_charge_per_day/
    demand_rate_per_kw_day that was really this household's GAS pricing,
    mislabeled - see AGENTS.local.md's REQ-141 entry). This repo's whole
    tariff model (RATE_FIELDS, the Ausgrid electricity demand charge) is
    electricity-only; a gas bill has nothing to contribute to it."""
    raw = []
    for p, txt in bills:
        meta = extractor.extract(txt, p)
        if meta.get("service_type") != "electricity":
            log(f"  (skipping {os.path.basename(p)} - detected as a {meta.get('service_type') or 'non-electricity'} bill, not electricity)")
            continue
        rates = extractor.extract_rates(txt)
        for w in rates["warnings"]:
            log(f"  ⚠ {os.path.basename(p)}: {w}")
        for block in rates["blocks"]:
            eff = block.get("date_from") or meta.get("period_start") or meta.get("issue_date")
            if not eff:
                log(f"  ⚠ {os.path.basename(p)}: found a rate block but no usable date for it - skipped")
                continue
            raw.append({
                "effective_from": eff,
                **{f: block[f] for f in RATE_FIELDS},
                "rate_tou_mismatch": block["rate_tou_mismatch"],
                "demand_rate_per_kw_day": rates["demand_rate_per_kw_day"],
                "demand_confirmed": rates["demand_confirmed"] and not rates["demand_period_ambiguous"],
                "source": os.path.basename(p),
            })
            if block["rate_tou_mismatch"]:
                log(f"  ℹ {os.path.basename(p)}: Peak/Off-peak/Shoulder rates are NOT all equal - "
                    f"a genuine TOU bill (peak=${block.get('peak_rate_per_kwh')}, "
                    f"shoulder=${block.get('shoulder_rate_per_kwh')}, "
                    f"offpeak=${block.get('offpeak_rate_per_kwh')}). This repo's tariff model "
                    f"handles genuine TOU periods (REQ-149/151/152) - --apply will propose a "
                    f"tariff_type: tou/tou3 period below with default window hours flagged for "
                    f"manual confirmation against this bill's own plan-change notice.")

    raw.sort(key=lambda r: r["effective_from"])

    collapsed = []
    for r in raw:
        key = tuple(r[f] for f in RATE_FIELDS)
        if collapsed and tuple(collapsed[-1][f] for f in RATE_FIELDS) == key:
            period = collapsed[-1]
            period["sources"].append(r["source"])
            if r["demand_rate_per_kw_day"] is not None:
                prev = period.get("demand_rate_per_kw_day")
                if prev is not None and prev != r["demand_rate_per_kw_day"]:
                    period.setdefault("demand_rate_conflicts", []).append(
                        (r["source"], r["demand_rate_per_kw_day"])
                    )
                period["demand_rate_per_kw_day"] = r["demand_rate_per_kw_day"]
                period["demand_confirmed"] = period["demand_confirmed"] or r["demand_confirmed"]
            continue
        collapsed.append({
            "effective_from": r["effective_from"],
            **{f: r[f] for f in RATE_FIELDS},
            "demand_rate_per_kw_day": r["demand_rate_per_kw_day"],
            "demand_confirmed": r["demand_confirmed"],
            "sources": [r["source"]],
        })

    # Backfill demand onto any earlier period(s) with no demand sighting of
    # their own yet (a shoulder-season gap in THAT period's own bills, not
    # necessarily a real absence of the charge) from the nearest LATER
    # confirmed sighting - mirrors this repo's existing real rates file,
    # which already carries its newer confirmed demand rate backward onto
    # its older, demand_confirmed:false period.
    fallback_demand = None
    for period in reversed(collapsed):
        if period["demand_rate_per_kw_day"] is not None:
            fallback_demand = period["demand_rate_per_kw_day"]
        elif fallback_demand is not None:
            period["demand_rate_per_kw_day"] = fallback_demand
            period["demand_confirmed"] = False
    return collapsed


def load_real_rates_file(rates_path):
    if not os.path.isfile(rates_path):
        return None
    with open(rates_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _usage_desc(c):
    """REQ-152: human-readable usage-rate description for a candidate -
    a flat "usage=$X/kWh" when usage_rate_per_kwh is set, or a
    peak/shoulder/off-peak tier breakdown when it's None because this
    candidate is a genuine TOU period (REQ-149/151) - shows only the
    tiers that were actually found on the bill (a 2-tier Peak/Off-peak
    bill has no shoulder_rate_per_kwh at all)."""
    if c.get("usage_rate_per_kwh") is not None:
        return f"usage=${c['usage_rate_per_kwh']}/kWh"
    parts = []
    if c.get("peak_rate_per_kwh") is not None:
        parts.append(f"peak=${c['peak_rate_per_kwh']}")
    if c.get("shoulder_rate_per_kwh") is not None:
        parts.append(f"shoulder=${c['shoulder_rate_per_kwh']}")
    if c.get("offpeak_rate_per_kwh") is not None:
        parts.append(f"offpeak=${c['offpeak_rate_per_kwh']}")
    if not parts:
        return "usage=$None/kWh (no usable rate found - manual review needed)"
    return "TOU " + "/".join(parts) + "/kWh"


def already_present(candidate, existing_periods):
    """True if the real file's OWN existing periods, resolved for this
    candidate's own effective_from date using the exact same range logic
    as rate_for_date() (REQ-141: _effective_from_date <= d <=
    _effective_to_date), already return this SAME rate tuple.

    REQ-141: this replaces a date-BLIND "does this combo exist ANYWHERE
    in the file" check, which silently dropped nearly a year of real,
    correctly-dated electricity periods - their real rate combo also
    happened to be recorded, under a much LATER date, on the file's own
    "1970-01-01 placeholder ... confirmed present on invoices from Apr
    2026 ... assumed to extend backward" catch-all entry, so the old
    check saw "already recorded" and never proposed the real, earlier-
    dated period at all (see AGENTS.local.md's REQ-141 entry for the
    full trace). Matching against what actually resolves for THIS
    candidate's own date, the same way every other caller in this repo
    resolves a date to a rate, can't make that mistake."""
    if not existing_periods or not existing_periods[0].get("_effective_from_date"):
        return False  # file has periods but they haven't been through load_rate_periods_from_file() (e.g. a bare dict list) - can't range-match, be conservative
    from datetime import datetime as _dt
    d = _dt.strptime(candidate["effective_from"], "%Y-%m-%d").date()
    applicable = [p for p in existing_periods if p["_effective_from_date"] <= d <= p["_effective_to_date"]]
    if not applicable and d > existing_periods[-1]["_effective_from_date"]:
        applicable = [existing_periods[-1]]
    if not applicable:
        return False
    resolved = applicable[-1]
    return all(resolved.get(f) == candidate[f] for f in RATE_FIELDS)


def _build_period_dict(c):
    """REQ-152: the period dict to append for one candidate - a plain flat
    period when usage_rate_per_kwh is set, or a genuine tariff_type: tou/
    tou3 period (REQ-149/151) when it's None because >=2 real tiers were
    found on the bill. The window-boundary fields (which hours/months/days
    count as peak vs shoulder vs off-peak) can NOT be derived from a rate
    table alone - a bill's "Usage and supply charges" table only ever
    prints the per-kWh rates, never the clock times they apply to - so
    this fills in this account's own two REAL, invoice-confirmed window
    shapes (tou: this account's 2026-09-07 plan - see agl_tariff_rates.json;
    tou3: this account's 2022-04-06 to 2026-09-06 structure - see
    AGENTS.local.md's REQ-151 entry) as defaults, and always flags them in
    the note for manual confirmation against THIS bill's own plan-change
    notice before trusting them blindly - a future plan change could well
    use different hours."""
    base_note = (f"Auto-proposed by sync_agl_rates.py (REQ-112/152) from real bill(s): "
                 f"{', '.join(c['sources'])}. Review before trusting demand_confirmed blindly "
                 f"if this period's own bills never showed a Demand line.")
    period = {
        "effective_from": c["effective_from"],
        "supply_charge_per_day": c["supply_charge_per_day"],
        "solar_feed_in_tier1_rate_per_kwh": c["solar_feed_in_tier1_rate_per_kwh"],
        "solar_feed_in_tier2_rate_per_kwh": c["solar_feed_in_tier2_rate_per_kwh"],
        "demand_rate_per_kw_day": c["demand_rate_per_kw_day"],
        "demand_confirmed": c["demand_confirmed"],
    }
    has_shoulder = c.get("shoulder_rate_per_kwh") is not None
    has_peak = c.get("peak_rate_per_kwh") is not None
    has_offpeak = c.get("offpeak_rate_per_kwh") is not None
    if c["usage_rate_per_kwh"] is not None:
        period["usage_rate_per_kwh"] = c["usage_rate_per_kwh"]
        period["note"] = base_note
    elif has_peak and has_shoulder and has_offpeak:
        period["usage_rate_per_kwh"] = None
        period["tariff_type"] = "tou3"
        period["peak_rate_per_kwh"] = c["peak_rate_per_kwh"]
        period["shoulder_rate_per_kwh"] = c["shoulder_rate_per_kwh"]
        period["offpeak_rate_per_kwh"] = c["offpeak_rate_per_kwh"]
        period["peak_hour_start"] = 14
        period["peak_hour_end"] = 20
        period["shoulder_weekday_windows"] = [[7, 14], [20, 22]]
        period["shoulder_weekend_hour_start"] = 7
        period["shoulder_weekend_hour_end"] = 22
        period["note"] = (base_note + " GENUINE 3-TIER TOU (Peak/Shoulder/Off-peak) - window hours "
                           "default to this account's own long-running REQ-151 structure "
                           "(Peak 2pm-8pm Mon-Fri excl. NSW public holidays; Shoulder 7am-2pm & "
                           "8pm-10pm Mon-Fri, 7am-10pm weekends/holidays; Off-peak all other times) "
                           "- CONFIRM against this bill's own plan-change notice before trusting, "
                           "a future plan change could use different hours.")
    elif has_peak and has_offpeak:
        period["usage_rate_per_kwh"] = None
        period["tariff_type"] = "tou"
        period["peak_rate_per_kwh"] = c["peak_rate_per_kwh"]
        period["offpeak_rate_per_kwh"] = c["offpeak_rate_per_kwh"]
        period["peak_months"] = [11, 12, 1, 2, 3, 6, 7, 8]
        period["peak_hour_start"] = 15
        period["peak_hour_end"] = 21
        period["note"] = (base_note + " GENUINE 2-TIER TOU (Peak/Off-peak, no Shoulder line on the "
                           "bill) - window hours/months default to this account's own 2026-09-07 "
                           "plan-change structure (Peak 3pm-9pm during Nov-Mar & Jun-Aug, Off-peak "
                           "the rest) - CONFIRM against this bill's own plan-change notice before "
                           "trusting, a future plan change could use different hours/months.")
    else:
        # <2 real tiers found and usage_rate_per_kwh is still None - shouldn't
        # normally happen (a bill either has one flat rate or >=2 tiers), but
        # never silently write a period with no usable rate at all.
        period["usage_rate_per_kwh"] = None
        period["note"] = base_note + " ⚠ NO usable rate extracted for this period at all - manual review required before trusting this entry."
    return period


def main():
    parser = argparse.ArgumentParser(
        description="Scan real AGL bill PDFs for tariff rates and propose new dated periods "
                    "for agl_tariff_rates.json. Report-only by default.",
    )
    parser.add_argument("--apply", action="store_true",
                         help="Append genuinely new period(s) to the real rates file (backs up first)")
    args = parser.parse_args()

    provider = get_power_provider()
    invoices_dir = provider.resolve_invoices_dir()
    landing_dir = resolve_landing_dir()
    ext = AGLInvoiceExtractor()

    print(f"Scanning for AGL bill PDFs in: {invoices_dir or '(unset)'}, {landing_dir}")
    bills = find_agl_bill_pdfs([invoices_dir, landing_dir], ext)
    print(f"Found {len(bills)} real AGL bill PDF(s).\n")
    if not bills:
        return

    candidates = collect_candidate_periods(bills, ext)
    if not candidates:
        print("No usable rate data extracted from any bill (unexpected bill layout?).")
        return

    print(f"{len(candidates)} distinct rate period(s) found across scanned bills:\n")
    for c in candidates:
        conflict = ""
        if c.get("demand_rate_conflicts"):
            conflict = f"  ⚠ demand rate varied within this period: {c['demand_rate_conflicts']}"
        print(f"  from {c['effective_from']}: {_usage_desc(c)}, "
              f"supply=${c['supply_charge_per_day']}/day, "
              f"solar1=${c['solar_feed_in_tier1_rate_per_kwh']}/kWh, "
              f"solar2={c['solar_feed_in_tier2_rate_per_kwh']}, "
              f"demand={c['demand_rate_per_kw_day']} (confirmed={c['demand_confirmed']})"
              f"{conflict}")
        print(f"      sources: {', '.join(c['sources'])}")

    rates_path = os.path.join(provider.resolve_data_home(), provider.tariff_rates_filename())
    real_file = load_real_rates_file(rates_path)
    if real_file is None:
        print(f"\n⚠ {rates_path} doesn't exist yet - not proposing anything automatically "
              f"into a file this repo has never validated the shape of. Copy "
              f"{provider.tariff_rates_example_path()}'s shape there first.")
        return

    existing_periods_raw = list(real_file.get("periods", []))  # copy - real_file["periods"] is mutated below via .append()
 # REQ-141: already_present() needs the SAME range-annotated periods
 # rate_for_date() itself uses (_effective_from_date/_effective_to_date),
 # not the bare dicts straight off disk - provider.load_rate_periods()
 # is exactly that (it goes through load_rate_periods_from_file(), the
 # one place that computes them). Loaded separately from real_file
 # (which stays the raw, appendable structure this function writes back)
 # so a bug in one can't corrupt the other.
    existing_periods = provider.load_rate_periods()
    new_periods = [c for c in candidates if not already_present(c, existing_periods)]

    print(f"\n{len(new_periods)} candidate period(s) NOT already present in {rates_path}:")
    if not new_periods:
        print("  (none - every rate combination found is already recorded)")
        return
    for c in new_periods:
        print(f"  + from {c['effective_from']}: {_usage_desc(c)}, "
              f"supply=${c['supply_charge_per_day']}, solar1=${c['solar_feed_in_tier1_rate_per_kwh']}, "
              f"solar2={c['solar_feed_in_tier2_rate_per_kwh']}, "
              f"demand={c['demand_rate_per_kw_day']} (confirmed={c['demand_confirmed']}) "
              f"[{', '.join(c['sources'])}]")

    if not args.apply:
        print("\n[dry-run] Nothing written - re-run with --apply to append these to the real rates file.")
        return

    backup_dir = os.path.join(os.path.dirname(rates_path), "archive")
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = next_archive_path(backup_dir, os.path.basename(rates_path))
    shutil.copy2(rates_path, backup_path)
    print(f"\nBacked up existing rates file to {backup_path}")

    for c in new_periods:
        real_file["periods"].append(_build_period_dict(c))
    real_file["periods"].sort(key=lambda p: p["effective_from"])

 # REQ-141: recompute every period's effective_to right before writing
 # (chain-derived: the day before the NEXT period's effective_from, or
 # None/"still current" for the last one) - the persisted field is only
 # ever a human-readable mirror of this, never trusted for lookups (see
 # power_base.py's load_rate_periods_from_file()), but it should still
 # never go stale on disk just because this script appended or
 # reordered periods.
    from datetime import datetime as _dt, timedelta as _td
    ps = real_file["periods"]
    for i, p in enumerate(ps):
        if i + 1 < len(ps):
            next_from = _dt.strptime(ps[i + 1]["effective_from"], "%Y-%m-%d").date()
            p["effective_to"] = (next_from - _td(days=1)).isoformat()
        else:
            p["effective_to"] = None

    tmp_path = rates_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(real_file, f, indent=2)
    os.replace(tmp_path, rates_path)

    with open(rates_path, "r", encoding="utf-8") as f:
        written = json.load(f)
    if len(written.get("periods", [])) != len(existing_periods_raw) + len(new_periods):
        print("⚠ Sanity check failed: period count after write doesn't match expected total.")
    else:
        print(f"Sanity check passed: {len(written['periods'])} period(s) on disk "
              f"({len(new_periods)} new).")
    print(f"Wrote {rates_path}")


if __name__ == "__main__":
    main()
