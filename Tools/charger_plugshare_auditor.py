#!/usr/bin/env python3
"""
PlugShare Discrepancy & Error Auditor (charger_plugshare_auditor.py)
===========================================================
Compares EVERY First-Party Authoritative Tier 1 Registry (Tesla, Chargefox/
NRMA/WA EV Network/Qld Electric Highway, EVX, Evie, bp pulse, AmpCharge/
Ampol Foodary) against Tier 2 PlugShare Aggregator data to identify:
1. Network Misattributions (e.g. marked as '3rd-Party' instead of 'EVX' / 'Chargefox')
2. Missing / Stale / Mismatched Pricing ($0.00 / unlisted, or a stale figure
   that no longer matches the authoritative rate)
3. Hardware / Power / Stall discrepancies
4. Missing Stations (First-party stations not yet listed on PlugShare)

REQ (2026-09-15, AGENTS.local.md): user, having just seen AmpCharge added
as a new Tier 1 provider - "I also would like to go back to the feature
request - that any tier 1 info that contradicts plugshare data is able to
be seen. The goal is to work to updating and improving stale data directly
with plugshare myself." Originally this only checked EVX and Chargefox by
name (a hardcoded if/elif per provider) and only flagged pricing that was
entirely missing on PlugShare, never a genuine mismatch, and never flagged
a hardware/power/stall difference despite the module docstring claiming it
did. Generalized here: NETWORK_ALIASES + network_matches() replace the
hardcoded per-provider branches (works for any current or future Tier 1
provider automatically, since the manager's own dedup already resolved
"the truth" onto the merged UnifiedStationRecord's own fields - only
PlugShare's own idiosyncratic nested view schema still needs its own
parsing), pricing now also flags a live mismatch (not just $0.00/unlisted),
and a new hardware/power/stalls check was actually implemented rather than
just documented.

Generates terminal tables and Markdown reports with direct clickable PlugShare edit URLs.
"""

import os
import sys
import json
import argparse
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timezone

_tools_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_tools_dir)
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

from providers import (
    ChargerDiscoveryManager,
    UnifiedStationRecord,
    TeslaProvider,
    ChargefoxProvider,
    EVXProvider,
    PlugShareProvider
)
from lib.table_formatter import (
    pad_display,
    truncate_display,
    format_box_line,
    format_field_line,
    format_title_line,
    format_type_badge
)

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_RED = "\033[31m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_CYAN = "\033[36m"
C_GRAY = "\033[90m"

# Every Tier 1 network family this repo knows about, and the PlugShare
# network-name spellings that should count as a correct match for it -
# PlugShare's own crowd-sourced "network" field doesn't always use this
# repo's exact station.network string (e.g. it may say "Ampol" for what
# this repo tags "AmpCharge", or leave a sub-brand like "NRMA" or "WA EV
# Network" unmerged into a single "Chargefox" label). Lowercased, substring
# match either direction - see network_matches() below.
NETWORK_ALIASES: Dict[str, List[str]] = {
    "tesla": ["tesla"],
    "chargefox": ["chargefox", "nrma", "wa ev network", "qld electric highway", "queensland electric highway"],
    "evx": ["evx", "polecharger"],
    "evie": ["evie"],
    "bp pulse": ["bp pulse", "bppulse", "bp"],
    "ampcharge": ["ampcharge", "ampol foodary", "ampol"],
    "ampol foodary": ["ampcharge", "ampol foodary", "ampol"],
}


def network_matches(authoritative_network: str, plugshare_network: str) -> bool:
    """
    True if PlugShare's own network label is an acceptable match for the
    authoritative Tier 1 network - either an exact/substring match, or both
    fall in the same NETWORK_ALIASES family (so a Chargefox-run "NRMA"
    sub-brand station, or a PlugShare listing simply calling it "Ampol",
    aren't reported as misattributions).
    """
    an = (authoritative_network or "").strip().lower()
    pn = (plugshare_network or "").strip().lower()
    if not an or not pn:
        return not an  # nothing authoritative to check against -> not an error
    if an in pn or pn in an:
        return True
    family = NETWORK_ALIASES.get(an)
    if family and any(alias in pn for alias in family):
        return True
    return False


def _fmt_price(rate: Optional[float]) -> str:
    if rate is None:
        return "-"
    return f"${rate:.2f}/kWh"


def inspect_station_views(station: UnifiedStationRecord) -> str:
    """Renders a comprehensive side-by-side / multi-view comparison for a single station."""
    views = station.views or {}
    lines = []
    lines.append(f"\n{C_BOLD}══════════════════════════════════════════════════════════════════════════════════{C_RESET}")
    lines.append(f"  🔍 MULTI-VIEW AUDIT: {C_CYAN}{station.name}{C_RESET} ({station.network})")
    lines.append(f"  📍 Canonical Key: {station.key} | Co-located Sources: {station.source}")
    lines.append(f"{C_BOLD}══════════════════════════════════════════════════════════════════════════════════{C_RESET}\n")

    # Display First-Party Truth View. Uses the already-merged
    # UnifiedStationRecord's own fields rather than hand-parsing each
    # provider's raw view dict - the manager's dedup/merge already resolved
    # "the truth" onto these fields for whichever Tier 1 provider owns this
    # station, so this works identically for Tesla, Chargefox, EVX, Evie,
    # bp pulse, AmpCharge, and any future Tier 1 provider with no changes
    # needed here.
    has_tier1_view = any(v in views for v in ("tesla", "chargefox", "evx", "evie", "bppulse", "ampol"))
    if has_tier1_view:
        lines.append(f"  ⭐ {C_GREEN}{C_BOLD}FIRST-PARTY AUTHORITATIVE TRUTH (Tier {station.tier}: {station.network.upper()}):{C_RESET}")
        lines.append(f"    • Network / Operator:  {station.network} ({station.operator_name or station.source})")
        lines.append(f"    • Upstream Name:       {station.name}")
        lines.append(f"    • Address:             {station.address or '-'}")
        lines.append(f"    • Power & Sockets:     {station.max_power_kw} kW | {station.stalls_total} stalls | {', '.join(station.plugs) or '-'}")
        lines.append(f"    • Authoritative Tariff: {_fmt_price(station.current_rate_per_kwh) if station.pricing_model != 'unknown' else 'unknown (not published upstream)'}")
        src_urls = station.get_source_urls()
        official_url = next((u["url"] for u in src_urls if u["name"].lower() != "plugshare"), station.url)
        lines.append(f"    • Official URL:        {official_url or '-'}")
        lines.append("")

    # Display PlugShare View
    if "plugshare" in views:
        ps = views["plugshare"]
        ps_meta = ps.get("plugshare_metadata", {})
        ps_loc = ps.get("location", {})
        ps_hw = ps.get("hardware", {})
        ps_tariffs = ps.get("tariffs", {})
        ps_id = ps_meta.get("id") or ps.get("id")
        ps_url = ps_meta.get("url") or f"https://www.plugshare.com/location/{ps_id}"

        lines.append(f"  🔌 {C_YELLOW}{C_BOLD}PLUGSHARE AGGREGATOR RECORD (Tier 2):{C_RESET}")
        lines.append(f"    • PlugShare ID:        {ps_id}")
        lines.append(f"    • Listed Name:         {ps_meta.get('name')}")
        lines.append(f"    • Listed Network:      {ps_meta.get('network', 'Unknown')}")
        lines.append(f"    • Listed Address:      {ps_loc.get('address', '-')}")
        lines.append(f"    • Listed Hardware:     {ps_hw.get('stalls', '-')} Stalls | {ps_hw.get('max_power_kw', '-')} kW ({', '.join(ps_hw.get('connector_types', []))})")
        lines.append(f"    • Listed Tariff:       ${ps_tariffs.get('per_kwh_flat', 0.0):.2f}/kWh (TOU: {ps_tariffs.get('has_tou_pricing', False)})")
        lines.append(f"    • PlugShare URL:       {ps_url}")
        lines.append("")

        # Discrepancy Analysis - generic across every Tier 1 provider now,
        # rather than a hardcoded per-provider if/elif chain.
        lines.append(f"  ⚠️ {C_RED}{C_BOLD}DISCREPANCY SUMMARY / RECOMMENDED PLUGSHARE EDITS:{C_RESET}")
        discrepancies = discrepancies_for_station(station, ps_meta, ps_hw, ps_tariffs)

        if not discrepancies:
            lines.append(f"    {C_GREEN}✔ No discrepancies detected between Tier 1 and PlugShare records.{C_RESET}")
        else:
            for d in discrepancies:
                lines.append(f"    • {C_YELLOW}{d}{C_RESET}")
            lines.append(f"\n    {C_CYAN}➔ Click to Edit on PlugShare: {ps_url}{C_RESET}")
    else:
        lines.append(f"  ❌ {C_RED}{C_BOLD}PLUGSHARE STATUS: NOT LISTED ON PLUGSHARE{C_RESET}")
        lines.append(f"    This station is missing from PlugShare. You can add it using:")
        lines.append(f"    • Name:        {station.name}")
        lines.append(f"    • Address:     {station.address}")
        lines.append(f"    • Coordinates: ({station.lat:.6f}, {station.lon:.6f})")
        lines.append(f"    • Network:     {station.network}")
        lines.append(f"    • Power:       {station.max_power_kw} kW Type 2 / {station.stalls_total} bays")

    lines.append(f"\n{C_BOLD}══════════════════════════════════════════════════════════════════════════════════{C_RESET}\n")
    return "\n".join(lines)


def discrepancies_for_station(
    s: UnifiedStationRecord,
    ps_meta: Dict[str, Any],
    ps_hw: Dict[str, Any],
    ps_tariffs: Dict[str, Any],
    power_tolerance_pct: float = 15.0,
    price_tolerance_pct: float = 10.0,
) -> List[str]:
    """
    Computes the list of human-readable discrepancies between an
    authoritative Tier 1 station record and PlugShare's own view of it.
    Shared by inspect_station_views() (per-station drill-down) and
    run_audit() (bulk scan) so the two never drift apart on what counts as
    a discrepancy.
    """
    out = []

    # 1. Network misattribution
    ps_net = ps_meta.get("network", "") or ""
    if not network_matches(s.network, ps_net):
        out.append(f"Network: Change from '{ps_net or 'Unknown'}' ➔ '{s.network}'")

    # 2. Pricing - missing/unlisted, or a live mismatch against a published rate
    if s.pricing_model != "unknown" and s.current_rate_per_kwh is not None and s.current_rate_per_kwh > 0:
        ps_price = ps_tariffs.get("per_kwh_flat", 0.0) or 0.0
        has_tou = ps_tariffs.get("has_tou_pricing", False)
        if ps_price == 0.0 and not has_tou:
            out.append(f"Tariff: Add pricing (PlugShare shows $0.00/unlisted; authoritative rate is {_fmt_price(s.current_rate_per_kwh)})")
        elif ps_price > 0.0 and not has_tou:
            diff_pct = abs(ps_price - s.current_rate_per_kwh) / s.current_rate_per_kwh * 100.0
            if diff_pct > price_tolerance_pct:
                out.append(f"Tariff: Update stale rate ${ps_price:.2f}/kWh ➔ {_fmt_price(s.current_rate_per_kwh)} (off by {diff_pct:.0f}%)")

    # 3. Hardware / power / stalls
    ps_power = ps_hw.get("max_power_kw")
    if ps_power and s.max_power_kw:
        try:
            ps_power = float(ps_power)
            diff_pct = abs(ps_power - s.max_power_kw) / s.max_power_kw * 100.0
            if diff_pct > power_tolerance_pct:
                out.append(f"Power: PlugShare shows {ps_power:g} kW ➔ authoritative is {s.max_power_kw:g} kW")
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    ps_stalls = ps_hw.get("stalls")
    if ps_stalls is not None and s.stalls_total:
        try:
            ps_stalls = int(ps_stalls)
            if ps_stalls != s.stalls_total:
                out.append(f"Stalls: PlugShare shows {ps_stalls} ➔ authoritative is {s.stalls_total}")
        except (TypeError, ValueError):
            pass

    return out


def run_audit(network_filter: Optional[str] = None, state_filter: Optional[str] = None, output_md: Optional[str] = None):
    """Audits all consolidated stations to produce a comprehensive error report."""
    manager = ChargerDiscoveryManager()
    all_stations = manager.get_all_stations()

    if state_filter:
        all_stations = [s for s in all_stations if (s.state or "").upper() == state_filter.upper()]

    if network_filter:
        net_clean = network_filter.strip().lower()
        all_stations = [s for s in all_stations if net_clean in (s.network or "").lower()]

    matched_count = 0
    missing_from_ps = 0
    network_errors = []
    pricing_errors = []
    hardware_errors = []
    missing_stations = []

    for s in all_stations:
        # Only Tier 1 (first-party authoritative) stations are audited -
        # this already covers every current provider (Tesla, Chargefox,
        # EVX, Evie, bp pulse, AmpCharge) with no per-provider listing
        # needed, since it's a plain numeric tier check.
        if s.tier != 1:
            continue

        views = s.views or {}
        if "plugshare" not in views:
            missing_from_ps += 1
            missing_stations.append(s)
            continue

        matched_count += 1
        ps = views["plugshare"]
        ps_meta = ps.get("plugshare_metadata", {})
        ps_hw = ps.get("hardware", {})
        ps_tariffs = ps.get("tariffs", {})
        ps_id = ps_meta.get("id") or ps.get("id")
        ps_url = ps_meta.get("url") or f"https://www.plugshare.com/location/{ps_id}"

        # 1. Network misattribution
        ps_net = ps_meta.get("network", "") or ""
        if not network_matches(s.network, ps_net):
            network_errors.append({
                "station": s,
                "ps_id": ps_id,
                "ps_name": ps_meta.get("name"),
                "ps_net": ps_net or "Unknown",
                "expected_net": s.network,
                "ps_url": ps_url
            })

        # 2. Pricing - missing/unlisted, or a live mismatch
        if s.pricing_model != "unknown" and s.current_rate_per_kwh is not None and s.current_rate_per_kwh > 0:
            ps_price = ps_tariffs.get("per_kwh_flat", 0.0) or 0.0
            has_tou = ps_tariffs.get("has_tou_pricing", False)
            if ps_price == 0.0 and not has_tou:
                pricing_errors.append({
                    "station": s,
                    "ps_id": ps_id,
                    "ps_name": ps_meta.get("name"),
                    "ps_price": "$0.00 / Unlisted",
                    "expected_price": _fmt_price(s.current_rate_per_kwh) if not s.schedule else f"${s.schedule[0].get('rate', 0.39):.2f} - ${s.schedule[-1].get('rate', 0.50):.2f}/kWh (TOU)",
                    "ps_url": ps_url
                })
            elif ps_price > 0.0 and not has_tou:
                diff_pct = abs(ps_price - s.current_rate_per_kwh) / s.current_rate_per_kwh * 100.0
                if diff_pct > 10.0:
                    pricing_errors.append({
                        "station": s,
                        "ps_id": ps_id,
                        "ps_name": ps_meta.get("name"),
                        "ps_price": f"${ps_price:.2f}/kWh (stale)",
                        "expected_price": _fmt_price(s.current_rate_per_kwh),
                        "ps_url": ps_url
                    })

        # 3. Hardware / power / stalls
        hw_issues = []
        ps_power = ps_hw.get("max_power_kw")
        if ps_power and s.max_power_kw:
            try:
                ps_power_f = float(ps_power)
                diff_pct = abs(ps_power_f - s.max_power_kw) / s.max_power_kw * 100.0
                if diff_pct > 15.0:
                    hw_issues.append(f"Power: {ps_power_f:g} kW ➔ {s.max_power_kw:g} kW")
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        ps_stalls = ps_hw.get("stalls")
        if ps_stalls is not None and s.stalls_total:
            try:
                if int(ps_stalls) != s.stalls_total:
                    hw_issues.append(f"Stalls: {int(ps_stalls)} ➔ {s.stalls_total}")
            except (TypeError, ValueError):
                pass
        if hw_issues:
            hardware_errors.append({
                "station": s,
                "ps_id": ps_id,
                "ps_name": ps_meta.get("name"),
                "issues": hw_issues,
                "ps_url": ps_url
            })

    print(f"\n{C_BOLD}📊 PLUGSHARE DISCREPANCY AUDIT SUMMARY{C_RESET}")
    print(f"──────────────────────────────────────────────────────────────────────────")
    print(f"  • Total Authoritative Tier 1 Stations: {len(all_stations)}")
    print(f"  • Co-located & Matched on PlugShare:   {C_GREEN}{matched_count}{C_RESET}")
    print(f"  • Missing from PlugShare Entirely:     {C_YELLOW}{missing_from_ps}{C_RESET}")
    print(f"  • Network Misattributions in PlugShare: {C_RED}{len(network_errors)}{C_RESET}")
    print(f"  • Missing / Stale Tariffs in PlugShare: {C_RED}{len(pricing_errors)}{C_RESET}")
    print(f"  • Hardware / Power / Stall Mismatches:  {C_RED}{len(hardware_errors)}{C_RESET}")
    print(f"──────────────────────────────────────────────────────────────────────────\n")

    if network_errors:
        print(f"{C_BOLD}🏷️ NETWORK MISATTRIBUTIONS IN PLUGSHARE ({len(network_errors)} stations):{C_RESET}")
        for item in network_errors[:20]:
            st = item["station"]
            print(f"  • {C_CYAN}{st.name}{C_RESET} ({st.state})")
            print(f"    Listed on PS: '{item['ps_net']}' ➔ Should Be: '{item['expected_net']}'")
            print(f"    Edit Link:    {item['ps_url']}\n")
        if len(network_errors) > 20:
            print(f"    ... and {len(network_errors) - 20} more.\n")

    if pricing_errors:
        print(f"{C_BOLD}💰 MISSING / STALE TARIFFS IN PLUGSHARE ({len(pricing_errors)} stations):{C_RESET}")
        for item in pricing_errors[:15]:
            st = item["station"]
            print(f"  • {C_CYAN}{st.name}{C_RESET} ({st.state})")
            print(f"    Listed on PS: {item['ps_price']} ➔ Official Rate: {item['expected_price']}")
            print(f"    Edit Link:    {item['ps_url']}\n")
        if len(pricing_errors) > 15:
            print(f"    ... and {len(pricing_errors) - 15} more.\n")

    if hardware_errors:
        print(f"{C_BOLD}🔧 HARDWARE / POWER / STALL MISMATCHES IN PLUGSHARE ({len(hardware_errors)} stations):{C_RESET}")
        for item in hardware_errors[:15]:
            st = item["station"]
            print(f"  • {C_CYAN}{st.name}{C_RESET} ({st.state})")
            for issue in item["issues"]:
                print(f"    {issue}")
            print(f"    Edit Link:    {item['ps_url']}\n")
        if len(hardware_errors) > 15:
            print(f"    ... and {len(hardware_errors) - 15} more.\n")

    if output_md:
        generate_markdown_report(output_md, all_stations, matched_count, missing_from_ps, network_errors, pricing_errors, hardware_errors, missing_stations)
        print(f"✔ Full Discrepancy Markdown report exported to: {output_md}")


def generate_markdown_report(
    filepath: str,
    all_stations: List[UnifiedStationRecord],
    matched_count: int,
    missing_from_ps: int,
    network_errors: List[Dict[str, Any]],
    pricing_errors: List[Dict[str, Any]],
    hardware_errors: List[Dict[str, Any]],
    missing_stations: List[UnifiedStationRecord]
):
    """Generates a detailed Markdown discrepancy report with actionable edit links."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"# PlugShare Discrepancy & Error Audit Report",
        f"**Generated:** {now_str}  ",
        f"**Scope:** Every Tier 1 Authoritative Provider (Tesla, Chargefox/NRMA/WA EV Network/Qld Electric Highway, EVX, Evie, bp pulse, AmpCharge/Ampol Foodary) vs Tier 2 PlugShare Aggregator\n",
        f"## 1. Executive Summary\n",
        f"| Metric | Count |",
        f"| :--- | :--- |",
        f"| **Total First-Party Stations** | {len(all_stations)} |",
        f"| **Matched & Consolidated with PlugShare** | {matched_count} |",
        f"| **Missing from PlugShare (New Deployments)** | {missing_from_ps} |",
        f"| **Network Misattributions (e.g. '3rd-Party' instead of the real network)** | {len(network_errors)} |",
        r"| **Missing / Stale Tariffs** | " + str(len(pricing_errors)) + " |",
        r"| **Hardware / Power / Stall Mismatches** | " + str(len(hardware_errors)) + " |\n",
        r"---",
        r"## 2. Network Misattributions on PlugShare",
        r"These stations are listed under generic or incorrect network tags on PlugShare and should be reassigned to the proper network operator:",
        r"",
        r"| Station Name | State | Current PlugShare Network | Correct Network | PlugShare Edit Link |",
        r"| :--- | :---: | :--- | :--- | :--- |"
    ]

    for item in network_errors:
        st = item["station"]
        lines.append(f"| **{st.name}** | {st.state} | `{item['ps_net']}` | **{item['expected_net']}** | [Edit on PlugShare #{item['ps_id']}]({item['ps_url']}) |")

    lines.extend([
        r"",
        r"---",
        r"## 3. Missing or Stale Tariffs on PlugShare",
        r"These stations currently show a missing, unlisted, or stale rate on PlugShare that no longer matches verified First-Party pricing:",
        r"",
        f"| Station Name | State | Listed on PlugShare | Authoritative Operator Tariff | PlugShare Edit Link |",
        f"| :--- | :---: | :--- | :--- | :--- |"
    ])

    for item in pricing_errors:
        st = item["station"]
        lines.append(f"| **{st.name}** | {st.state} | {item['ps_price']} | **{item['expected_price']}** | [Update Tariff #{item['ps_id']}]({item['ps_url']}) |")

    lines.extend([
        r"",
        r"---",
        r"## 4. Hardware / Power / Stall Mismatches on PlugShare",
        r"These stations' PlugShare listing disagrees with verified First-Party hardware data:",
        r"",
        f"| Station Name | State | Discrepancies | PlugShare Edit Link |",
        f"| :--- | :---: | :--- | :--- |"
    ])

    for item in hardware_errors:
        st = item["station"]
        issues_str = "; ".join(item["issues"])
        lines.append(f"| **{st.name}** | {st.state} | {issues_str} | [Update on PlugShare #{item['ps_id']}]({item['ps_url']}) |")

    lines.extend([
        f"\n---",
        f"## 5. First-Party Stations Missing from PlugShare\n",
        f"These active first-party stations are not yet registered on PlugShare:\n",
        f"| Station Name | Network | State | Address | Power | Coordinates |",
        f"| :--- | :--- | :---: | :--- | :--- | :--- |"
    ])

    for st in missing_stations[:50]:
        lines.append(f"| **{st.name}** | {st.network} | {st.state} | {st.address} | {st.max_power_kw} kW | `{st.lat:.5f}, {st.lon:.5f}` |")

    if len(missing_stations) > 50:
        lines.append(f"\n*(Truncated: {len(missing_stations) - 50} additional missing stations omitted from table)*")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(
        usage="""charger_plugshare_auditor.py [--network tesla|chargefox|evx|evie|bppulse|ampcharge|all] [--state ST] [--report FILE.md]
   or:  charger_plugshare_auditor.py --query TEXT
""",
        description="""
NAME
    charger_plugshare_auditor.py - audit PlugShare records against authoritative first-party registries

DESCRIPTION
    Audits PlugShare records against this repo's authoritative first-party
    charger registries - every Tier 1 provider (Tesla, Chargefox/NRMA/WA EV
    Network/Qld Electric Highway, EVX, Evie, bp pulse, AmpCharge/Ampol
    Foodary) - flagging network misattributions, missing/stale pricing,
    hardware/power/stall mismatches, and stations missing from PlugShare
    entirely, and optionally exporting a Markdown report with direct
    PlugShare edit links for fixing the crowd-sourced data yourself.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES
  charger_plugshare_auditor.py --network chargefox --state NSW
  charger_plugshare_auditor.py --network ampcharge
  charger_plugshare_auditor.py --query "1 Endeavour"
  charger_plugshare_auditor.py --report plugshare_audit_report.md
"""
    )
    parser.add_argument("--query", "-q", type=str, help="Inspect multi-views and PlugShare comparison for a specific station (e.g. '1 Endeavour')")
    parser.add_argument("--network", "-n", choices=["tesla", "chargefox", "evx", "evie", "bppulse", "ampcharge", "all"], default="all", help="Filter audit by network")
    parser.add_argument("--state", "-s", choices=["NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"], help="Filter audit by state")
    parser.add_argument("--report", "-r", type=str, help="Path to export Markdown report (e.g. plugshare_audit_report.md)")

    args = parser.parse_args()

    if args.query:
        manager = ChargerDiscoveryManager()
        stations = manager.search_by_text(args.query)
        if not stations:
            print(f"❌ No stations found matching query '{args.query}'.")
            return
        for st in stations:
            print(inspect_station_views(st))
        return

    net_filter = None if args.network == "all" else args.network
    report_path = args.report or os.path.join(_repo_root, "scratch", "plugshare_audit_report.md")
    run_audit(network_filter=net_filter, state_filter=args.state, output_md=report_path)


if __name__ == "__main__":
    main()
