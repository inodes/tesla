#!/usr/bin/env python3
"""
PlugShare Discrepancy & Error Auditor (plugshare_auditor.py)
===========================================================
Compares First-Party Authoritative Tier 1 Registries (Tesla, Chargefox, EVX)
against Tier 2 PlugShare Aggregator data to identify:
1. Network Misattributions (e.g. marked as '3rd-Party' instead of 'EVX' / 'Chargefox')
2. Missing / Stale Pricing ($0.00 / unlisted vs authoritative TOU tariffs)
3. Hardware / Power / Stall discrepancies
4. Name & Address inaccuracies
5. Missing Stations (First-party stations not yet listed on PlugShare)

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
from table_formatter import (
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


def inspect_station_views(station: UnifiedStationRecord) -> str:
    """Renders a comprehensive side-by-side / multi-view comparison for a single station."""
    views = station.views or {}
    lines = []
    lines.append(f"\n{C_BOLD}══════════════════════════════════════════════════════════════════════════════════{C_RESET}")
    lines.append(f"  🔍 MULTI-VIEW AUDIT: {C_CYAN}{station.name}{C_RESET} ({station.network})")
    lines.append(f"  📍 Canonical Key: {station.key} | Co-located Sources: {station.source}")
    lines.append(f"{C_BOLD}══════════════════════════════════════════════════════════════════════════════════{C_RESET}\n")

    # Display First-Party Truth View
    fp_view_name = "evx" if "evx" in views else ("chargefox" if "chargefox" in views else ("tesla" if "tesla" in views else None))
    if fp_view_name:
        fp = views[fp_view_name]
        lines.append(f"  ⭐ {C_GREEN}{C_BOLD}FIRST-PARTY AUTHORITATIVE TRUTH (Tier 1: {fp_view_name.upper()}):{C_RESET}")
        if fp_view_name == "evx":
            cf = fp.get("custom_fields", {})
            lines.append(f"    • Network / Operator:  EVX")
            lines.append(f"    • Pole Unit ID:        {fp.get('pole_id') or cf.get('7ulej7jp2u', '-')}")
            lines.append(f"    • Raw Upstream Name:   {fp.get('raw_name')}")
            lines.append(f"    • Hardware & Power:    {cf.get('9rn4ydnspl', 'Polecharger')} | 2 Bays | {cf.get('dq3ho4dk08', '22 KW')} {cf.get('qi6rkgzgrn', 'Type 2')}")
            lines.append(f"    • Authoritative Tariff:Off-Peak $0.39/kWh (8pm-8am) | Peak $0.50/kWh (8am-8pm) | Idle $0.20/min")
            lines.append(f"    • Official URL:        https://evx.tech/charge-locator/")
        elif fp_view_name == "chargefox":
            loc_data = fp.get("location", {})
            tariffs = fp.get("tariffs", {})
            lines.append(f"    • Network / Operator:  {fp.get('network', 'Chargefox')} (ID: {fp.get('id')})")
            lines.append(f"    • Upstream Name:       {fp.get('name')}")
            lines.append(f"    • Address:             {loc_data.get('address')}")
            lines.append(f"    • Power & Sockets:     {fp.get('hardware', {}).get('max_power_kw')} kW | {fp.get('hardware', {}).get('stalls')} stalls")
            lines.append(f"    • Authoritative Tariff:${tariffs.get('per_kwh_gross', tariffs.get('per_kwh_flat', '-'))}/kWh")
            lines.append(f"    • Official URL:        https://app.chargefox.com/")
        elif fp_view_name == "tesla":
            meta = fp.get("metadata", {})
            lines.append(f"    • Network / Operator:  Tesla ({meta.get('tier', 'Supercharger')})")
            lines.append(f"    • Site Location Name:  {meta.get('findus_location_name') or fp.get('name')}")
            lines.append(f"    • Stalls & Power:      {fp.get('hardware', {}).get('stalls')} Stalls | {fp.get('hardware', {}).get('max_power_kw')} kW")
            lines.append(f"    • Non-Tesla Access:    {'Open to all CCS2 EVs' if meta.get('open_to_non_tesla') else 'Tesla Only'}")
            lines.append(f"    • Official URL:        {fp.get('provenance', {}).get('url') or 'https://www.tesla.com/findus'}")
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

        # Discrepancy Analysis
        lines.append(f"  ⚠️ {C_RED}{C_BOLD}DISCREPANCY SUMMARY / RECOMMENDED PLUGSHARE EDITS:{C_RESET}")
        discrepancies = []
        if fp_view_name == "evx":
            if ps_meta.get("network") != "EVX":
                discrepancies.append(f"Network: Change from '{ps_meta.get('network')}' ➔ 'EVX'")
            if ps_tariffs.get("per_kwh_flat", 0.0) == 0.0:
                discrepancies.append("Tariff: Add TOU rates (Off-Peak: $0.39/kWh 8pm-8am, Peak: $0.50/kWh 8am-8pm, Idle: $0.20/min)")
            if ps_meta.get("name") and "polecharger" not in ps_meta.get("name", "").lower():
                discrepancies.append(f"Name: Standardize title to include Polecharger / Pole ID {fp.get('pole_id')}")
        elif fp_view_name == "chargefox":
            if ps_meta.get("network") not in ("Chargefox", "NRMA"):
                discrepancies.append(f"Network: Change from '{ps_meta.get('network')}' ➔ 'Chargefox'")
            if ps_tariffs.get("per_kwh_flat", 0.0) == 0.0:
                discrepancies.append("Tariff: Update pricing to active Chargefox live rate")

        if not discrepancies:
            lines.append(f"    {C_GREEN}✔ No major discrepancies detected between Tier 1 and PlugShare records.{C_RESET}")
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


def run_audit(network_filter: Optional[str] = None, state_filter: Optional[str] = None, output_md: Optional[str] = None):
    """Audits all consolidated stations to produce a comprehensive error report."""
    manager = ChargerDiscoveryManager()
    all_stations = manager.get_all_stations()

    if state_filter:
        all_stations = [s for s in all_stations if (s.state or "").upper() == state_filter.upper()]

    if network_filter:
        net_clean = network_filter.strip().lower()
        all_stations = [s for s in all_stations if (s.network or "").lower() == net_clean]

    matched_count = 0
    missing_from_ps = 0
    network_errors = []
    pricing_errors = []
    missing_stations = []

    for s in all_stations:
        # We only audit First-Party networks (EVX, Chargefox, Tesla)
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
        ps_tariffs = ps.get("tariffs", {})
        ps_id = ps_meta.get("id") or ps.get("id")
        ps_url = ps_meta.get("url") or f"https://www.plugshare.com/location/{ps_id}"

        # 1. Check Network Misattribution
        ps_net = ps_meta.get("network", "Unknown")
        if s.network == "EVX" and ps_net != "EVX":
            network_errors.append({
                "station": s,
                "ps_id": ps_id,
                "ps_name": ps_meta.get("name"),
                "ps_net": ps_net,
                "expected_net": "EVX",
                "ps_url": ps_url
            })
        elif s.network == "Chargefox" and ps_net not in ("Chargefox", "NRMA", "WA EV Network", "Queensland Electric Highway"):
            network_errors.append({
                "station": s,
                "ps_id": ps_id,
                "ps_name": ps_meta.get("name"),
                "ps_net": ps_net,
                "expected_net": s.network,
                "ps_url": ps_url
            })

        # 2. Check Missing / Zero Pricing
        ps_price = ps_tariffs.get("per_kwh_flat", 0.0)
        has_tou = ps_tariffs.get("has_tou_pricing", False)
        if ps_price == 0.0 and not has_tou and s.current_rate_per_kwh and s.current_rate_per_kwh > 0:
            pricing_errors.append({
                "station": s,
                "ps_id": ps_id,
                "ps_name": ps_meta.get("name"),
                "ps_price": "$0.00 / Unlisted",
                "expected_price": f"${s.current_rate_per_kwh:.2f}/kWh" if not s.schedule else f"${s.schedule[0].get('rate', 0.39):.2f} - ${s.schedule[-1].get('rate', 0.50):.2f}/kWh (TOU)",
                "ps_url": ps_url
            })

    print(f"\n{C_BOLD}📊 PLUGSHARE DISCREPANCY AUDIT SUMMARY{C_RESET}")
    print(f"──────────────────────────────────────────────────────────────────────────")
    print(f"  • Total Authoritative Tier 1 Stations: {len(all_stations)}")
    print(f"  • Co-located & Matched on PlugShare:   {C_GREEN}{matched_count}{C_RESET}")
    print(f"  • Missing from PlugShare Entirely:     {C_YELLOW}{missing_from_ps}{C_RESET}")
    print(f"  • Network Misattributions in PlugShare: {C_RED}{len(network_errors)}{C_RESET}")
    print(f"  • Missing / Stale Tariffs in PlugShare: {C_RED}{len(pricing_errors)}{C_RESET}")
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
        print(f"{C_BOLD}💰 MISSING / UNLISTED TARIFFS IN PLUGSHARE ({len(pricing_errors)} stations):{C_RESET}")
        for item in pricing_errors[:15]:
            st = item["station"]
            print(f"  • {C_CYAN}{st.name}{C_RESET} ({st.state})")
            print(f"    Listed on PS: {item['ps_price']} ➔ Official Rate: {item['expected_price']}")
            print(f"    Edit Link:    {item['ps_url']}\n")
        if len(pricing_errors) > 15:
            print(f"    ... and {len(pricing_errors) - 15} more.\n")

    if output_md:
        generate_markdown_report(output_md, all_stations, matched_count, missing_from_ps, network_errors, pricing_errors, missing_stations)
        print(f"✔ Full Discrepancy Markdown report exported to: {output_md}")


def generate_markdown_report(
    filepath: str,
    all_stations: List[UnifiedStationRecord],
    matched_count: int,
    missing_from_ps: int,
    network_errors: List[Dict[str, Any]],
    pricing_errors: List[Dict[str, Any]],
    missing_stations: List[UnifiedStationRecord]
):
    """Generates a detailed Markdown discrepancy report with actionable edit links."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"# PlugShare Discrepancy & Error Audit Report",
        f"**Generated:** {now_str}  ",
        f"**Scope:** Tier 1 Authoritative Providers (Tesla, Chargefox, EVX) vs Tier 2 PlugShare Aggregator\n",
        f"## 1. Executive Summary\n",
        f"| Metric | Count |",
        f"| :--- | :--- |",
        f"| **Total First-Party Stations** | {len(all_stations)} |",
        f"| **Matched & Consolidated with PlugShare** | {matched_count} |",
        f"| **Missing from PlugShare (New Deployments)** | {missing_from_ps} |",
        f"| **Network Misattributions (e.g. '3rd-Party' instead of 'EVX')** | {len(network_errors)} |",
        r"| **Missing / Unlisted Tariffs ($0.00)** | " + str(len(pricing_errors)) + " |\n",
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
        r"## 3. Missing or Unlisted Tariffs on PlugShare",
        r"These stations currently show $0.00 or unlisted rates on PlugShare, but have verified First-Party pricing schedules:",
        r"",
        f"| Station Name | State | Listed on PlugShare | Authoritative Operator Tariff | PlugShare Edit Link |",
        f"| :--- | :---: | :--- | :--- | :--- |"
    ])

    for item in pricing_errors:
        st = item["station"]
        lines.append(f"| **{st.name}** | {st.state} | {item['ps_price']} | **{item['expected_price']}** | [Update Tariff #{item['ps_id']}]({item['ps_url']}) |")

    lines.extend([
        f"\n---",
        f"## 4. First-Party Stations Missing from PlugShare\n",
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
    parser = argparse.ArgumentParser(description="Audit PlugShare records against Authoritative First-Party Registries")
    parser.add_argument("--query", "-q", type=str, help="Inspect multi-views and PlugShare comparison for a specific station (e.g. '1 Endeavour')")
    parser.add_argument("--network", "-n", choices=["evx", "chargefox", "tesla", "all"], default="all", help="Filter audit by network")
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
