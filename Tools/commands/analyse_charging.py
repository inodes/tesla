#!/usr/bin/env python3
"""
Tessie Charging & Supercharger Reconciliation Engine
=====================================================
- Multi-Registry Place & Charger Resolver (superchargers.json, charging.json, places.json)
- Config File Support (config.json / config.json / ~/.config/tesla/config.json)
- Custom Local Invoices Directory (Kept private outside repo)
- Tesla Supercharger PDF & CSV Tax Invoice Parser (Zero external dependencies)
- 3rd-Party Fast/AC Charging Parser & Network Identifier (Chargefox, Evie, BP Pulse, Jolt, etc.)
- Dispenser Meter vs Battery BMS Charging Efficiency Loss Calculator
- Time-of-Use (TOU) Rate Schedule & GST Auditor
- Charges Master Consolidator (charges_master.csv)
- Multi-Level Rich Terminal Reporting & Deep-Dive Session Inspector
- Export Reconciled Reports to CSV / JSON
"""
import os as _os, sys as _sys; _sys.path[:0] = [p for p in (_os.path.dirname(_os.path.abspath(__file__)), _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))) if p not in _sys.path]      # the commands beside it, and Tools/ (lib, providers)

import os
import sys
import time

# Auto re-exec inside local direnv/pyenv virtual environment if not already active
_script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_repo_root = os.path.dirname(_script_dir)

for _py_candidate in [
    os.path.join(_repo_root, ".direnv", "python-3.11", "bin", "python3"),
    os.path.join(_repo_root, ".direnv", "python-3.11", "bin", "python"),
    os.path.join(_repo_root, ".venv", "bin", "python3"),
    os.path.join(_repo_root, ".venv", "bin", "python")
]:
    if os.path.isfile(_py_candidate) and os.path.abspath(sys.executable) != os.path.abspath(_py_candidate):
        try:
            import pypdf
        except ImportError:
            try:
                _orig = getattr(sys, "orig_argv", None)
                if _orig and len(_orig) > 1:
                    os.execv(_py_candidate, [_py_candidate] + _orig[1:])
                else:
                    os.execv(_py_candidate, [_py_candidate] + sys.argv)
            except Exception:
                pass

import re
import signal
import argparse
import unicodedata

def clean_station_short_name(name: str, max_length: int = 80) -> str:
    """
    Normalizes station names into filesystem-safe, cross-platform identifiers:
    - Transliterates Unicode accents (e.g. 'Café' -> 'Cafe')
    - Strips or replaces non-alphanumeric characters with underscores
    - Condenses consecutive underscores and strips leading/trailing underscores
    - Enforces safe length limits and prevents empty string results
    """
    if not name:
        return "Station"
    s = unicodedata.normalize('NFKD', str(name))
    s = s.encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s)
    s = s.strip("_")
    if max_length and len(s) > max_length:
        s = s[:max_length].rstrip("_")
    return s or "Station"

try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except Exception:
    pass
from datetime import datetime, timedelta
from collections import defaultdict


# -----------------------------------------------------------------------------
# Terminal Formatting & Unicode Helpers (wcwidth compatible)
# -----------------------------------------------------------------------------

_tools_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

from lib.table_formatter import (
    char_width,
    display_len,
    truncate_display,
    pad_display,
    format_row,
    format_title_line,
    format_box_line,
)
from lib.charging_analyzer import C_BOLD, C_CYAN, C_DIM, C_GREEN, C_RESET, C_YELLOW, ChargingAnalyzer, TeslaInvoiceParser, parse_flexible_date  # noqa: E402
from lib.fuel_common import vehicle_l_per_100km  # noqa: E402


def _litres(v):
    """A table cell for an L/100km figure; "-" when the vehicle has none published."""
    return f"{v:.1f} L " if v is not None else "- "

C_WHITE   = "\033[97m"

ChargingInvoiceParser = TeslaInvoiceParser


# -----------------------------------------------------------------------------
# CLI Entrypoint
# -----------------------------------------------------------------------------

def interactive_charge_session_loop(analyzer, filtered, allow_back=False):
    """Shared interactive [#]/[u]pdate/[q]uit prompt loop for a charging
    session list. Used both at the top level (all months, no month
    breakdown) and after drilling into a single month from
    display_charging_months_menu() below - allow_back=True adds a [b]ack
    option in the latter case so the user can return to the months table
    instead of quitting the whole tool. Returns True if the user chose to
    go back (only possible when allow_back=True), otherwise None on quit."""
    while True:
        try:
            bad_sessions = [
                s for s in analyzer.reconciled_sessions
                if "MISMATCH" in s.get("status", "") or "WRONG" in s.get("status", "") or
                (s.get("invoice_number") and s.get("invoice_cost") is not None and (
                    abs(s.get("invoice_cost", 0) - s.get("tessie_cost", 0)) >= 0.01 or
                    (s.get("invoice_rate") is not None and abs(s.get("invoice_rate", 0) - s.get("tessie_rate", 0)) >= 0.01)
                ))
            ]
            if bad_sessions:
                print(f"  {C_BOLD}[#]{C_RESET} Inspect a session")
                print(f"  {C_YELLOW}{C_BOLD}[u]{C_RESET}{C_YELLOW}pdate all bad entries from invoices{C_RESET}")
            else:
                print(f"  {C_BOLD}[#]{C_RESET} Inspect / manually correct a session")
            if allow_back:
                print(f"  {C_BOLD}[b]{C_RESET}ack to months")
            print(f"  {C_BOLD}[q]{C_RESET}uit")

            choice = input(f"{C_BOLD}Choice:{C_RESET} ").strip().lower()
            if not choice or choice in ['q', 'quit', 'exit']:
                return None
            if allow_back and choice in ['b', 'back']:
                return True
            if choice in ['u', 'update', 'fix', 'fix-all', 'sync']:
                analyzer.update_master_from_invoices()
                filtered = analyzer.consolidate_for_display([s for s in analyzer.reconciled_sessions if any(s["charge_index"] == orig["charge_index"] for orig in filtered)] or analyzer.reconciled_sessions)
                analyzer.print_sessions_table(filtered)
            elif choice.isdigit():
                analyzer.inspect_session(int(choice))
 # Refresh filtered list and reprint table
                filtered = analyzer.consolidate_for_display([s for s in analyzer.reconciled_sessions if any(s["charge_index"] == orig["charge_index"] for orig in filtered)] or analyzer.reconciled_sessions)
                analyzer.print_sessions_table(filtered)
            else:
                back_hint = "'b' to go back, " if allow_back else ""
                print(f"Enter a session #, 'u' to update all bad entries, {back_hint}or 'q' to quit.")
        except (KeyboardInterrupt, EOFError):
            print()
            return None


def display_charging_months_menu(filtered_sessions, analyzer):
    """Months overview for charging sessions - user asked why this tool
    doesn't break results into months the way tessie_drives_analyzer.py's
    default view already does, and wanted a total $ per month alongside
    it (mirroring that tool's total-km-per-month). Charging sessions don't
    need a further days sub-level the way trip legs do (most days have at
    most one session), so this drills straight from a month row into that
    month's normal print_summary()/print_sessions_table() view."""
    months_dict = defaultdict(list)
    for s in filtered_sessions:
        if not s.get("datetime"):
            continue
        months_dict[s["datetime"].strftime("%Y-%m")].append(s)

    sorted_months = sorted(months_dict.keys(), reverse=True)

    while True:
        rows_data = []
        for idx, m_key in enumerate(sorted_months):
            m_sessions = months_dict[m_key]
            dt_m = datetime.strptime(m_key + "-01", "%Y-%m-%d")
            n_disp = sum(s["dispenser_kwh"] for s in m_sessions)
            n_cost = sum((s["invoice_cost"] if s["invoice_cost"] is not None else s["tessie_cost"]) for s in m_sessions)
            n_avg = (n_cost / n_disp) if n_disp > 0 else 0.0
            rows_data.append({
                "idx": f" [{idx + 1}]",
                "month": f" {dt_m.strftime('%B %Y')}",
                "sessions": f" {len(m_sessions)}",
                "energy": f" {n_disp:,.1f} kWh",
                "spend": f" ${n_cost:,.2f}",
                "avg": f" ${n_avg:.2f}/kWh",
            })

 # The grand-total row (added below) can be wider than any individual
 # month row (e.g. "1,205.7 kWh" vs. "418.2 kWh") - its cells must
 # feed into the column-width calculation too, or it gets truncated.
        gt_disp = sum(s["dispenser_kwh"] for s in filtered_sessions)
        gt_cost = sum((s["invoice_cost"] if s["invoice_cost"] is not None else s["tessie_cost"]) for s in filtered_sessions)
        gt_avg = (gt_cost / gt_disp) if gt_disp > 0 else 0.0
        total_row = {
            "idx": "",
            "month": " TOTAL",
            "sessions": f" {len(filtered_sessions)}",
            "energy": f" {gt_disp:,.1f} kWh",
            "spend": f" ${gt_cost:,.2f}",
            "avg": f" ${gt_avg:.2f}/kWh",
        }
        width_rows = rows_data + [total_row]

        w_idx = max(len(" # "), max((display_len(r["idx"]) + 1 for r in width_rows), default=5))
        w_month = max(len(" Month "), max((display_len(r["month"]) + 1 for r in width_rows), default=18))
        w_sessions = max(len(" Sessions "), max((display_len(r["sessions"]) + 1 for r in width_rows), default=11))
        w_energy = max(len(" Energy "), max((display_len(r["energy"]) + 1 for r in width_rows), default=13))
        w_spend = max(len(" Total Spend "), max((display_len(r["spend"]) + 1 for r in width_rows), default=15))
        w_avg = max(len(" Avg $/kWh "), max((display_len(r["avg"]) + 1 for r in width_rows), default=13))

        total_inner = w_idx + w_month + w_sessions + w_energy + w_spend + w_avg + 5
        title = f" 🗓️ CHARGING SESSIONS BY MONTH ({len(filtered_sessions):,} Sessions Across {len(sorted_months)} Months)"
        t_len = display_len(title)
        if t_len + 2 > total_inner:
            w_avg += (t_len + 2 - total_inner)
            total_inner = w_idx + w_month + w_sessions + w_energy + w_spend + w_avg + 5

        border_top = format_box_line("┌", "─", "┐", [total_inner])
        border_top_header = format_box_line("├", "┬", "┤", [w_idx, w_month, w_sessions, w_energy, w_spend, w_avg])
        border_data_divider = format_box_line("├", "┼", "┤", [w_idx, w_month, w_sessions, w_energy, w_spend, w_avg])
        border_bot = format_box_line("└", "┴", "┘", [w_idx, w_month, w_sessions, w_energy, w_spend, w_avg])

        print(f"\n{border_top}")
        print(format_title_line(title, total_inner, "left"))
        print(border_top_header)

        m_widths = [w_idx, w_month, w_sessions, w_energy, w_spend, w_avg]
        m_headers = [" #", " Month", " Sessions", " Energy", " Total Spend", " Avg $/kWh"]
        m_aligns = ["right", "left", "right", "right", "right", "right"]
        print(format_row([f"{C_BOLD}{h}{C_RESET}" for h in m_headers], m_widths))  # TODO-015: was the one unbolded header row in this file
        print(border_data_divider)

        for r in rows_data:
            m_cells = [r["idx"] + " ", r["month"], r["sessions"] + " ", r["energy"] + " ", r["spend"] + " ", r["avg"] + " "]
            print(format_row(m_cells, m_widths, m_aligns))

        print(border_data_divider)
        gt_cells = [total_row["idx"] + " ", total_row["month"], total_row["sessions"] + " ", total_row["energy"] + " ", total_row["spend"] + " ", total_row["avg"] + " "]
        print(format_row(gt_cells, m_widths, m_aligns))
        print(border_bot)

        try:
            print(f"  {C_BOLD}[1-{len(sorted_months)}]{C_RESET} Select a month")
            print(f"  {C_BOLD}[a]{C_RESET}ll months")
            print(f"  {C_BOLD}[q]{C_RESET}uit")
            choice = input(f"{C_BOLD}Choice:{C_RESET} ").strip().lower()
            if not choice or choice in ("q", "quit", "exit"):
                return
            elif choice in ("a", "all"):
                analyzer.print_summary(filtered_sessions)
                analyzer.print_sessions_table(filtered_sessions)
                interactive_charge_session_loop(analyzer, filtered_sessions, allow_back=True)
            elif choice.isdigit() and 1 <= int(choice) <= len(sorted_months):
                selected_m = sorted_months[int(choice) - 1]
                m_sessions = months_dict[selected_m]
                analyzer.print_summary(m_sessions)
                analyzer.print_sessions_table(m_sessions)
                interactive_charge_session_loop(analyzer, m_sessions, allow_back=True)
            else:
                print("Invalid choice.")
        except (KeyboardInterrupt, EOFError):
            print()
            return


def display_comparison_vehicles_table(tessie_dir=None):
    """Prints a formatted table of all benchmark comparison vehicles from comparison_vehicles.json."""
    try:
        from lib.fuel_common import load_comparison_vehicles
    except ImportError:
        try:
            from Tools.lib.fuel_common import load_comparison_vehicles
        except ImportError:
            print("Unable to load fuel_common library.")
            return

    registry = load_comparison_vehicles(tessie_dir)
    vehicles = registry.get("vehicles", {})
    default_key = registry.get("default_vehicle", "petrol_standard")

    if not vehicles:
        print("No comparison vehicles configured.")
        return

    headers = ["Key", "Vehicle Name", "Category", "Fuel", "Comb L/100", "Urban L", "Hwy L", "Maint/km", "Tyres/km", "Default"]
    widths = [22, 42, 22, 6, 11, 8, 7, 10, 10, 8]
    total_w = sum(widths) + len(widths) - 1

    print(f"\n┌{'─' * total_w}┐")
    title = f" 🚗 {C_BOLD}BENCHMARK COMPARISON VEHICLES REGISTRY (Tessie/comparison_vehicles.json){C_RESET}"
    print(f"│{pad_display(title, total_w, 'left', truncate=True)}│")
    print("├" + "┬".join("─" * w for w in widths) + "┤")
    print("│" + "│".join(pad_display(f"{C_BOLD}{h}{C_RESET}", w, "center") for h, w in zip(headers, widths)) + "│")
    print("├" + "┼".join("─" * w for w in widths) + "┤")

    for key, spec in vehicles.items():
        name = spec.get("name") or spec.get("label") or key
        category = spec.get("category", "-")
        fuel = spec.get("fuel_type", "U91")
        comb, urban, hwy = vehicle_l_per_100km(spec)      # None when the vehicle has no published figure: shown as "-"

        running_costs = spec.get("running_costs", {})
        maint = running_costs.get("service_maintenance_per_km", 0.0) if isinstance(running_costs, dict) else 0.0
        tyres = running_costs.get("tyres_per_km", 0.0) if isinstance(running_costs, dict) else 0.0
        is_default = " ⭐ YES" if key == default_key else ""

        print("│" + "│".join([
            pad_display(f" {key}", widths[0], "left", truncate=True),
            pad_display(f" {name}", widths[1], "left", truncate=True),
            pad_display(f" {category}", widths[2], "left", truncate=True),
            pad_display(fuel, widths[3], "center", truncate=True),
            pad_display(_litres(comb), widths[4], "right", truncate=True),
            pad_display(_litres(urban), widths[5], "right", truncate=True),
            pad_display(_litres(hwy), widths[6], "right", truncate=True),
            pad_display(f"${maint:.3f} " if maint > 0 else "- ", widths[7], "right", truncate=True),
            pad_display(f"${tyres:.3f} " if tyres > 0 else "- ", widths[8], "right", truncate=True),
            pad_display(is_default, widths[9], "center", truncate=True),
        ]) + "│")

    print("└" + "┴".join("─" * w for w in widths) + "┘")
    print(f"👉 {C_DIM}To compare against any vehicle, pass --vehicle <key>. Customize in Tessie/comparison_vehicles.json{C_RESET}\n")


def display_gvg_top20_table(tessie_dir=None):
    """Displays official Australian Green Vehicle Guide (GVG) Top 20 best-sellers and benchmark ratings."""
    try:
        from lib.fuel_common import get_gvg_top20_vehicles, load_comparison_vehicles
    except ImportError:
        try:
            from Tools.lib.fuel_common import get_gvg_top20_vehicles, load_comparison_vehicles
        except ImportError:
            print("Unable to load fuel_common library.")
            return

    top20_list = get_gvg_top20_vehicles(tessie_dir)
    reg = load_comparison_vehicles(tessie_dir)
    default_key = reg.get("default_vehicle", "petrol_standard")

    if not top20_list:
        print("No Green Vehicle Guide Top 20 vehicles found.")
        return

    headers = ["Rank", "Key", "Green Vehicle Guide (GVG) Model", "Category", "Fuel", "Comb L/100", "CO2 g/km", "Maint/km", "Tyres/km", "Total $/km"]
    widths = [8, 22, 44, 20, 6, 11, 10, 10, 10, 11]
    total_w = sum(widths) + len(widths) - 1

    print(f"\n┌{'─' * total_w}┐")
    title = f" 🏆 {C_BOLD}AUSTRALIAN GREEN VEHICLE GUIDE (GVG) TOP 20 BEST-SELLING VEHICLES{C_RESET}"
    print(f"│{pad_display(title, total_w, 'left', truncate=True)}│")
    print(f"│{pad_display(f' {C_DIM}Source: Official GVG ADR 81/02 Testing (https://greenvehicleguide.gov.au/#top20){C_RESET}', total_w, 'left', truncate=True)}│")
    print("├" + "┬".join("─" * w for w in widths) + "┤")
    print("│" + "│".join(pad_display(f"{C_BOLD}{h}{C_RESET}", w, "center") for h, w in zip(headers, widths)) + "│")
    print("├" + "┼".join("─" * w for w in widths) + "┤")

    for key, spec in top20_list:
        rank_num = spec.get("gvg_rank_top20", "-")
        if rank_num == 1:
            rank_s = "🥇 #1"
        elif rank_num == 2:
            rank_s = "🥈 #2"
        elif rank_num == 3:
            rank_s = "🥉 #3"
        else:
            rank_s = f"#{rank_num}"

        name = spec.get("name", key)
        category = spec.get("category", "-")
        fuel = spec.get("fuel_type", "U91")
        co2 = spec.get("co2_g_per_km", "-")
        co2_s = f"{co2} g" if co2 != "-" else "-"

        comb = vehicle_l_per_100km(spec)[0]

        running_costs = spec.get("running_costs", {})
        maint = running_costs.get("service_maintenance_per_km", 0.0) if isinstance(running_costs, dict) else 0.0
        tyres = running_costs.get("tyres_per_km", 0.0) if isinstance(running_costs, dict) else 0.0
        total_running = maint + tyres

        key_display = f"{C_CYAN}{key}{C_RESET}" if key != default_key else f"{C_GREEN}{key} ⭐{C_RESET}"

        print("│" + "│".join([
            pad_display(rank_s, widths[0], "center", truncate=True),
            pad_display(f" {key_display}", widths[1], "left", truncate=True),
            pad_display(f" {name}", widths[2], "left", truncate=True),
            pad_display(f" {category}", widths[3], "left", truncate=True),
            pad_display(fuel, widths[4], "center", truncate=True),
            pad_display(_litres(comb), widths[5], "right", truncate=True),
            pad_display(f"{co2_s} ", widths[6], "right", truncate=True),
            pad_display(f"${maint:.3f} " if maint > 0 else "- ", widths[7], "right", truncate=True),
            pad_display(f"${tyres:.3f} " if tyres > 0 else "- ", widths[8], "right", truncate=True),
            pad_display(f"${total_running:.3f} " if total_running > 0 else "- ", widths[9], "right", truncate=True),
        ]) + "│")

    print("└" + "┴".join("─" * w for w in widths) + "┘")
    print(f"👉 {C_DIM}Run with --vehicle <key> to map real EV charging costs against any GVG model (e.g. --vehicle ford_ranger_diesel){C_RESET}\n")


def search_and_display_vehicles(query_str, tessie_dir=None):
    """Searches and prints matching vehicles from GVG and comparison registry."""
    try:
        from lib.fuel_common import search_comparison_vehicles
    except ImportError:
        try:
            from Tools.lib.fuel_common import search_comparison_vehicles
        except ImportError:
            print("Unable to load fuel_common library.")
            return

    matches = search_comparison_vehicles(query_str, tessie_dir)
    if not matches:
        print(f"\n{C_YELLOW}No comparison vehicles found matching '{query_str}'.{C_RESET}")
        print(f"👉 Run --gvg-top20 or --list-vehicles to view available comparison vehicles.\n")
        return

    print(f"\n🔍 Found {len(matches)} comparison vehicle(s) matching '{C_BOLD}{query_str}{C_RESET}':\n")
    for key, spec in matches:
        rank = spec.get("gvg_rank_top20")
        rank_badge = f"[GVG Top 20: #{rank}] " if rank else ""
        name = spec.get("name", key)
        cat = spec.get("category", "-")
        fuel = spec.get("fuel_type", "U91")
        comb, urban, hwy = vehicle_l_per_100km(spec)
        co2 = spec.get("co2_g_per_km")
        co2_s = f", {co2} g CO2/km" if co2 else ""

        rc = spec.get("running_costs", {})
        maint = rc.get("service_maintenance_per_km", 0.0) if isinstance(rc, dict) else 0.0
        tyres = rc.get("tyres_per_km", 0.0) if isinstance(rc, dict) else 0.0

        print(f"  • {C_GREEN}{C_BOLD}{key}{C_RESET}: {name} ({cat})")
        print(f"    {C_DIM}Fuel:{C_RESET} {fuel} | {C_DIM}ADR Consumption:{C_RESET} " + (f"{comb:.1f} L/100km combined ({urban:.1f} urban / {hwy:.1f} hwy)" if comb is not None else "not published") + f"{co2_s}")
        print(f"    {C_DIM}Running Costs:{C_RESET} ${maint:.3f}/km service/maint + ${tyres:.3f}/km tyres = ${maint+tyres:.3f}/km total")
        print(f"    {C_DIM}Command:{C_RESET} {C_CYAN}./Tools/analyse_charging.py --vehicle {key}{C_RESET}\n")


def main():
    parser = argparse.ArgumentParser(
        usage="""analyse_charging.py [--superchargers | --third-party | --home]
                                    [--exclude-superchargers | --exclude-third-party | --exclude-home]
                                    [--unreconciled] [--month YYYY-MM] [--since DATE] [--until DATE] [--audit]
   or:  analyse_charging.py --inspect ID_OR_DATE [--correlation]
   or:  analyse_charging.py --vehicle VEHICLE [--compare-all] [--list-vehicles] [--gvg-top20] [--search-vehicles TERM]
   or:  analyse_charging.py --consolidate
   or:  analyse_charging.py --update-master
   or:  analyse_charging.py --rename-invoices [--rename-format TEMPLATE] [--dry-run] [--yes]
   or:  analyse_charging.py --export FILE
   or:  analyse_charging.py --precompute-sources [--since DATE] [--force-refresh]
   or:  analyse_charging.py --build-curves [--force-curves]
""",
        description="""
NAME
    analyse_charging.py - Tessie Charging & Supercharger Reconciliation Engine

DESCRIPTION
    Reconcile Tessie charge sessions against invoice PDFs/CSV receipts and detailed
    telemetry CSVs, filter and audit sessions by source or date, and manage invoice
    file naming.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES
  Filtering & Auditing:
    analyse_charging.py --superchargers --month 2026-09
    analyse_charging.py --unreconciled --audit
    analyse_charging.py --home --since monday

  Vehicle Cost Comparisons & Green Vehicle Guide (GVG):
    analyse_charging.py --vehicle ford_ranger_diesel
    analyse_charging.py --vehicle rav4_hybrid
    analyse_charging.py --vehicle hilux_diesel --month 2026-06
    analyse_charging.py --compare-all
    analyse_charging.py --gvg-top20
    analyse_charging.py --search-vehicles "hilux"
    analyse_charging.py --list-vehicles

  Inspection & Reconciliation:
    analyse_charging.py --inspect "2026-09-01"
    analyse_charging.py --inspect 142 --correlation
    analyse_charging.py --consolidate
    analyse_charging.py --update-master

  Invoice Management:
    analyse_charging.py --rename-invoices --dry-run
    analyse_charging.py --rename-invoices --yes
    analyse_charging.py --export reconciled.csv

  Prep (REQ-072):
    analyse_charging.py --precompute-sources
    analyse_charging.py --precompute-sources --since 2026-08-01
    analyse_charging.py --precompute-sources --force-refresh

  Charts (REQ-183):
    analyse_charging.py --build-curves
    analyse_charging.py --build-curves --force-curves
"""
    )

    g_config = parser.add_argument_group("Configuration & Paths")
    g_config.add_argument("--config", "-c", help="Path to custom config.json file")
    g_config.add_argument("--tessie-dir", "-d", help="Path to Tessie directory containing CSVs and JSONs")
    g_config.add_argument("--invoices-dir", "-i", help="Path to directory containing invoice PDFs / CSV receipts")

    g_filter = parser.add_argument_group("Session Filters")
    g_filter.add_argument("--superchargers", "-s", action="store_true", help="Filter for Tesla Supercharger sessions only")
    g_filter.add_argument("--third-party", "-t", action="store_true", help="Filter for 3rd-Party Fast/AC charging sessions only")
    g_filter.add_argument("--home", "-H", action="store_true", help="Filter for Home AC charging sessions only")
    g_filter.add_argument("--exclude-superchargers", action="store_true", help="Exclude Tesla Supercharger sessions (e.g. combine with other filters to show everything else)")
    g_filter.add_argument("--exclude-third-party", action="store_true", help="Exclude 3rd-Party Fast/AC charging sessions")
    g_filter.add_argument("--exclude-home", action="store_true", help="Exclude Home AC charging sessions (e.g. show all non-home sessions)")
    g_filter.add_argument("--unreconciled", "-u", action="store_true", help="Filter for unreconciled sessions or rate mismatches")
    g_filter.add_argument("--audit", "-a", action="store_true", help="List mismatches without interactive prompting")
    g_filter.add_argument("--month", help="Filter sessions by month (e.g. '2026-09', 'september', 'aug')")
    g_filter.add_argument("--since", help="Filter sessions on or after date (YYYY-MM-DD or relative: today, yesterday, monday)")
    g_filter.add_argument("--until", help="Filter sessions on or before date (YYYY-MM-DD)")

    g_ice = parser.add_argument_group("ICE & Hybrid Vehicle Comparison & Green Vehicle Guide (GVG)")
    g_ice.add_argument("--vehicle", "-V", help="Map EV running costs and savings against a specific comparison vehicle (e.g. 'ford_ranger_diesel', 'rav4_hybrid', 'hilux_diesel', 'prado', 'cx5')")
    g_ice.add_argument("--compare-all", action="store_true", help="Display side-by-side comparison matrix against all configured benchmark vehicles")
    g_ice.add_argument("--gvg-top20", action="store_true", help="Display official Australian Green Vehicle Guide (GVG) Top 20 best-sellers and ADR specs")
    g_ice.add_argument("--search-vehicles", metavar="TERM", help="Search GVG and comparison vehicle registry by make, model, category, or fuel")
    g_ice.add_argument("--list-vehicles", action="store_true", help="List all configured benchmark comparison vehicles and efficiency specs")

    g_action = parser.add_argument_group("Inspection, Reconciliation & Invoice Actions")
    g_action.add_argument("--inspect", help="Deep-dive inspect a specific session by Charge # or Date")
    g_action.add_argument("--correlation", "--compare", action="store_true", help="Display 3-way correlation audit comparing Tessie summary, detailed telemetry CSV, and Invoices")
    g_action.add_argument("--consolidate", action="store_true", help="Consolidate all charges into charges_master.csv")
    g_action.add_argument("--update-master", "--update-all", "--fix-all", "--sync-invoices", action="store_true", help="Update charges_master.csv and individual charge files with official invoice costs/rates")
    g_action.add_argument("--rename-invoices", "--rename", action="store_true", help="Rename invoice PDFs to <date/time>_<network>_<invoice_num>_<place>.pdf")
    g_action.add_argument("--rename-format", help="Custom renaming template (e.g. '{datetime}_{network}_{invoice_num}_{place}.pdf')")
    g_action.add_argument("--dry-run", action="store_true", help="Preview renaming without modifying files on disk")
    g_action.add_argument("--yes", "-y", action="store_true", help="Automatically confirm renaming without interactive prompt")
    g_action.add_argument("--export", help="Export reconciled results to a CSV or JSON file")
    g_action.add_argument("--tolerance-mins", type=int, default=None, help="Invoice matching time tolerance in minutes (default: 45 or config)")
    g_action.add_argument("--precompute-sources", action="store_true",
                           help="Batch-populate the PV provider/AGL/Evnex energy-source cross-check cache for every matching home Evnex session (combine with --since/--until/--month to scope it), so --inspect never waits on a live fetch (REQ-072)")
    g_action.add_argument("--build-curves", action="store_true",
                           help="Build the charging-curve and charge-profile charts (PNG + HTML) for every deep-dive telemetry file that lacks them, then list any DC session that has no telemetry or no chart (REQ-183)")
    g_action.add_argument("--force-curves", action="store_true",
                           help="With --build-curves, rebuild every chart even if it already exists")
    g_action.add_argument("--force-refresh", action="store_true",
                           help="With --precompute-sources, re-fetch every session's cross-check even if already cached (needed once after a change to the underlying calculation)")
    g_action.add_argument("--reconcile-invoices", action="store_true",
                           help="Cross-reference AGL's real invoiced totals against the aggregate of "
                                "computed per-session EV charging costs (REQ-071/REQ-154 - moved here from "
                                "the old standalone analyse_session_cost.py), instead of the normal listing")
    g_action.add_argument("--review-unmatched", action="store_true",
                           help="2026-09-28: batch-review every Evnex session with no matching Tessie charge "
                                "(and sync confirmed complete through its window) - Evnex reports no vehicle/VIN "
                                "at all, so this is the only way to catch a charge that wasn't your own Tesla; "
                                "confirmed answers persist to .evnex_other_vehicle_ids.json and are excluded "
                                "from --reconcile-invoices/--inspect from then on")
    g_action.add_argument("--sync-tessie-costs", action="store_true",
                           help="REQ-148: push our real calculated/known cost (tiered solar model for Home, matched invoice for DC/public) back to Tessie's own live account via set_cost. Always a dry-run preview unless --apply is also given.")
    g_action.add_argument("--apply", action="store_true",
                           help="With --sync-tessie-costs, actually make the real Tessie API writes (and patch charges_master.csv to match) instead of just previewing them")
    g_action.add_argument("--limit", type=int, default=None,
                           help="With --sync-tessie-costs, only consider the first N charges (oldest-first) - useful for a small real test before running the full batch")
    g_action.add_argument("--only-confident", action="store_true",
                           help="With --sync-tessie-costs, skip Home charges that have no PV data at all for their window rather than pushing the lower-confidence flat-rate-fallback figure")

    args = parser.parse_args()

    if args.gvg_top20:
        display_gvg_top20_table(args.tessie_dir)
        return

    if args.search_vehicles:
        search_and_display_vehicles(args.search_vehicles, args.tessie_dir)
        return

    if args.list_vehicles:
        display_comparison_vehicles_table(args.tessie_dir)
        return

    analyzer = ChargingAnalyzer(
        config_path=args.config,
        tessie_dir=args.tessie_dir,
        invoices_dir=args.invoices_dir,
        tolerance_mins=args.tolerance_mins
    )

    if args.consolidate:
        # Non-zero exit on failure so sync_api_tessie.py's run_consolidate()
        # doesn't advance the sync watermark past charges that never landed.
        if analyzer.consolidate_charges_master() is False:
            sys.exit(1)
        return

    if args.update_master:
        analyzer.update_master_from_invoices(auto_confirm=args.yes)
        return

    if args.sync_tessie_costs:
        analyzer.sync_tessie_costs(apply=args.apply, limit=args.limit, only_confident=args.only_confident)
        return

    if args.reconcile_invoices:
        from lib.session_cost import reconcile_against_invoices
        reconcile_against_invoices()
        return

    if args.review_unmatched:
        from lib.session_cost import review_unmatched_evnex_sessions
        review_unmatched_evnex_sessions()
        return

    if args.rename_invoices:
        analyzer.rename_invoices(
            format_pattern=args.rename_format,
            dry_run=args.dry_run,
            auto_confirm=args.yes
        )
        return

    reconciled = analyzer.reconcile(interactive=False)
    if sys.stdin.isatty() and not args.audit:
        analyzer.interactive_discrepancy_menu()
        reconciled = analyzer.reconciled_sessions

    if args.inspect:
        analyzer.inspect_session(args.inspect)
        return

    filtered = reconciled
    if args.superchargers:
        filtered = [s for s in filtered if s["is_supercharger"]]
    elif args.third_party:
        filtered = [s for s in filtered if s["is_fast_charger"] and not s["is_supercharger"]]
    elif args.home:
        filtered = [s for s in filtered if s["emoji"] == "🏠"]

 # Exclude filters apply independently of (and after) the include filters
 # above, so e.g. --exclude-home alone means "show everything except home".
    if args.exclude_superchargers:
        filtered = [s for s in filtered if not s["is_supercharger"]]
    if args.exclude_third_party:
        filtered = [s for s in filtered if not (s["is_fast_charger"] and not s["is_supercharger"])]
    if args.exclude_home:
        filtered = [s for s in filtered if s["emoji"] != "🏠"]

    if args.unreconciled:
        filtered = [s for s in filtered if s["status"] in ["UNRECONCILED ❓", "RATE MISMATCH ⚠️", "COST MISMATCH ⚠️", "TESSIE RATE WRONG ⚠️", "INVOICE ONLY 📄"]]

    if args.month:
        m_target = args.month.strip().lower()

        def _month_match(s):
            if not s.get("datetime"):
                return False
            ym = s["datetime"].strftime("%Y-%m")
            mname = s["datetime"].strftime("%B").lower()
            return m_target in ym or mname.startswith(m_target)

        filtered = [s for s in filtered if _month_match(s)]

    if args.since:
        s_dt = parse_flexible_date(args.since)
        if s_dt:
            filtered = [s for s in filtered if s["datetime"] and s["datetime"] >= s_dt]

    if args.until:
        u_dt = parse_flexible_date(args.until)
        if u_dt:
            u_dt_end = u_dt + timedelta(days=1)
            filtered = [s for s in filtered if s["datetime"] and s["datetime"] <= u_dt_end]

    if args.build_curves:
        analyzer.build_all_charging_curves(force=args.force_curves)
        return

    filtered = analyzer.consolidate_for_display(filtered)

    if args.precompute_sources:
        analyzer.precompute_energy_sources(sessions=filtered, force_refresh=args.force_refresh)
        return

    if args.correlation:
        analyzer.print_correlation_table(filtered)
        if args.export:
            analyzer.export_reconciliation(args.export, filtered)
        return

 # Months overview: mirrors tessie_drives_analyzer.py's default
 # months-menu behavior. Skipped (falls through to the flat summary +
 # table below, exactly as before) whenever the result is already
 # scoped to a single month, --month/--export/--audit were given, or
 # stdin isn't a tty (non-interactive use, e.g. scripted/piped).
    months_present = sorted(set(s["datetime"].strftime("%Y-%m") for s in filtered if s.get("datetime")), reverse=True)
    if sys.stdin.isatty() and not args.export and not args.audit and not args.month and len(months_present) > 1:
        display_charging_months_menu(filtered, analyzer)
        return

    analyzer.print_summary(filtered, selected_vehicle=args.vehicle, compare_all=args.compare_all)
    analyzer.print_sessions_table(filtered)

    if args.export:
        analyzer.export_reconciliation(args.export, filtered)

 # Interactive session selection loop: allows selecting session to inspect or manually correct rate/cost
    if sys.stdin.isatty() and not args.inspect and not args.export and not args.audit:
        interactive_charge_session_loop(analyzer, filtered)


if __name__ == "__main__":
    main()
