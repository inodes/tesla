#!/usr/bin/env python3
"""
⚡ Unified Multi-Network EV Charging Discovery Engine (charger_find.py) ⚡
========================================================================
Finds, compares, and explores EV charging stations across all public networks:
- ⚡ Tesla Superchargers & Destination Chargers (Tier 1 First-Party)
- 🦊 Chargefox, NRMA, WA EV Network, QLD Electric Highway (Tier 1 First-Party)
- 🕊️ Evie, BP Pulse, AmpCharge, Exploren, Chargepoint, Engie (Tier 2 Aggregator)

Key Features:
- Spatial Deduplication (R <= 60m) & First-Party Truth Hierarchy (Tier 1 > Tier 2)
- Unified Sorting by Distance, Active $/kWh Price, Max kW Power, or Stall Count
- Time-of-Use (TOU) Tariff Evaluation (-t HH:MM)
- Interactive Drill-Down with Google Maps Integration & Live Status Inspection
"""

import os
import sys
import re
import json
import argparse
import urllib.request
import urllib.parse
import webbrowser
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime

_tools_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_tools_dir)
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

from lib.table_formatter import (
    char_width,
    display_len,
    pad_display,
    truncate_display,
    format_row,
    format_title_line,
    format_box_line,
    format_field_line,
    format_local_timestamp,
    hyperlink,
    format_display_url,
    format_type_badge,
    clean_station_short_name
)

from providers import (
    ChargerDiscoveryManager,
    UnifiedStationRecord,
    TeslaProvider,
    ChargefoxProvider,
    EVXProvider,
    EvieProvider,
    BPPulseProvider,
    PlugShareProvider,
    AmpChargeProvider
)

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"
C_CYAN = "\033[36m"
C_BLUE = "\033[34m"
C_MAGENTA = "\033[35m"
C_ORANGE = "\033[38;5;214m"
C_GRAY = "\033[90m"

_GEOCODE_CACHE = {}


def geocode_address(address_str: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """Geocodes an arbitrary address string or suburb to (lat, lon, label) with fallbacks."""
    if not address_str:
        return None, None, None
    clean = address_str.strip()
    if clean in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[clean]

    # 1. Nominatim (OpenStreetMap)
    try:
        params = urllib.parse.urlencode({
            "q": f"{clean}, Australia" if "australia" not in clean.lower() else clean,
            "format": "json",
            "limit": 1
        })
        url = f"https://nominatim.openstreetmap.org/search?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "TeslaChargerExplorer/1.0"})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data:
                first = data[0]
                lat = float(first["lat"])
                lon = float(first["lon"])
                display = first.get("display_name", clean)
                parts = display.split(", ")
                short_display = ", ".join(parts[:3]) if len(parts) > 3 else display
                res = (lat, lon, short_display)
                _GEOCODE_CACHE[clean] = res
                return res
    except Exception:
        pass

    # 2. Open-Meteo Geocoding API
    try:
        params = urllib.parse.urlencode({"name": clean, "count": 1, "format": "json"})
        url = f"https://geocoding-api.open-meteo.com/v1/search?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "TeslaChargerExplorer/1.0"})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results", [])
            if results:
                r = results[0]
                display = f"{r.get('name')}, {r.get('admin1', '')}".strip(", ")
                res = (float(r["latitude"]), float(r["longitude"]), display)
                _GEOCODE_CACHE[clean] = res
                return res
    except Exception:
        pass

    return None, None, None


def resolve_reference_coords(near_query: str) -> Tuple[Optional[float], Optional[float], str]:
    """
    Resolves reference location name into (lat, lon, label).
    Checks places.json (Home, Work, etc.), direct 'lat,lon' coordinates, or online geocoding.
    """
    if not near_query:
        return None, None, ""

    q_clean = near_query.strip()
    
    # 1. Direct coordinates: "lat,lon"
    if "," in q_clean:
        parts = [p.strip() for p in q_clean.split(",")]
        if len(parts) == 2:
            try:
                lat = float(parts[0])
                lon = float(parts[1])
                return lat, lon, f"Coords ({lat:.4f}, {lon:.4f})"
            except ValueError:
                pass

    # 2. Look in places.json
    places_candidates = []
    tessie_home = None
    try:
        from lib.tessie_api_common import resolve_tessie_data_home
        tessie_home = resolve_tessie_data_home()
    except Exception:
        pass
    if tessie_home:
        places_candidates.append(os.path.join(tessie_home, "places.json"))

    places_candidates.extend([
        os.path.join(_repo_root, "Tessie", "places.json"),
        os.path.join(_repo_root, "Tessie", "places.example.json")
    ])
    cfg_path = os.path.join(_repo_root, "config.json")  # TODO-034: root is primary now
    if not os.path.isfile(cfg_path):
        cfg_path = os.path.join(_repo_root, "Tessie", "config.json")  # legacy fallback
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                td = cfg.get("tessie_directory")
                if td:
                    places_candidates.insert(0, os.path.join(os.path.expanduser(td), "places.json"))
        except Exception:
            pass

    for p_file in places_candidates:
        if os.path.isfile(p_file):
            try:
                with open(p_file, "r", encoding="utf-8") as f:
                    p_data = json.load(f)
                    places_dict = p_data.get("places") if (isinstance(p_data, dict) and "places" in p_data and isinstance(p_data["places"], dict)) else p_data
                    if isinstance(places_dict, dict):
                        for p_name, p_info in places_dict.items():
                            if not isinstance(p_info, dict):
                                continue
                            if p_name.lower() == q_clean.lower() or q_clean.lower() in p_name.lower():
                                lat = p_info.get("lat") or p_info.get("latitude")
                                lon = p_info.get("lon") or p_info.get("longitude") or p_info.get("lng")
                                if lat is not None and lon is not None:
                                    return float(lat), float(lon), p_name
            except Exception:
                pass

    # 3. Known station match in local registries
    reg_candidates = []
    if tessie_home:
        reg_candidates.extend([
            os.path.join(tessie_home, "tesla_superchargers.json"),
            os.path.join(tessie_home, "tesla_chargers.json"),
            os.path.join(tessie_home, "chargefox_chargers.json"),
            os.path.join(tessie_home, "evx_chargers.json"),
            os.path.join(tessie_home, "plugshare_chargers.json"),
            os.path.join(tessie_home, "geo_cache.json"),
        ])
    reg_candidates.extend([
        os.path.join(_repo_root, "Tessie", "tesla_superchargers.json"),
        os.path.join(_repo_root, "Tessie", "tesla_chargers.json"),
        os.path.join(_repo_root, "Tessie", "chargefox_chargers.json"),
        os.path.join(_repo_root, "Tessie", "evx_chargers.json"),
        os.path.join(_repo_root, "Tessie", "geo_cache.json"),
    ])

    for reg_path in reg_candidates:
        if os.path.isfile(reg_path):
            try:
                with open(reg_path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    if isinstance(d, dict):
                        st_dict = d.get("stations", d) if isinstance(d.get("stations"), dict) else d
                        for s_k, s_v in st_dict.items():
                            if not isinstance(s_v, dict):
                                continue
                            name_to_check = s_v.get("name", s_k)
                            suburb_to_check = s_v.get("location", {}).get("suburb", "") if isinstance(s_v.get("location"), dict) else s_v.get("suburb", "")
                            if (q_clean.lower() in s_k.lower() or 
                                q_clean.lower() in name_to_check.lower() or 
                                (suburb_to_check and q_clean.lower() in suburb_to_check.lower())):
                                loc = s_v.get("location", {}) if isinstance(s_v.get("location"), dict) else {}
                                lat = loc.get("lat") or loc.get("latitude") or s_v.get("lat") or s_v.get("latitude")
                                lon = loc.get("lon") or loc.get("longitude") or s_v.get("lon") or s_v.get("longitude")
                                if lat and lon:
                                    return float(lat), float(lon), name_to_check or s_k
            except Exception:
                pass

    # 4. Fallback to geocoding
    g_lat, g_lon, g_label = geocode_address(q_clean)
    if g_lat is not None and g_lon is not None:
        return g_lat, g_lon, g_label or q_clean

    # 5. Default Sydney fallback if Home requested
    if q_clean.lower() == "home":
        return -33.8688, 151.2093, "Sydney"

    return None, None, q_clean


def _contextual_type_icon(st: UnifiedStationRecord, network_filter: Optional[str]) -> str:
    """
    Normally just st.type_icon(). BUG-075 (2026-09-15, AGENTS.local.md):
    user filtered --network chargefox and saw rows badged "Tesla Destination"
    or "Evie" with no visible sign why they matched - those ARE genuinely
    cross-listed (station.source = "tesla+chargefox" / "evie+chargefox" from
    manager.py's multi-source merge, not a filter leak), but the badge
    always shows the record's primary source. User's own framing: "when
    displaying Chargefox - list it as a Chargefox, when listing Tesla-dest -
    list it as that." When the active --network filter matched this row via a
    SECONDARY source in a merged record, show THAT network's badge instead of
    always the primary one, so the row reads as "why did this match".
    """
    if not network_filter:
        return st.type_icon()
    nf = network_filter.strip().lower()
    sources = [s.strip().lower() for s in (st.source or "").split("+") if s.strip()]
    if len(sources) < 2 or nf not in sources or nf == sources[0]:
        return st.type_icon()
    if nf == "chargefox":
        return "🦊 CF"
    if nf == "nrma":
        return "⚡ NRMA"
    if nf == "evx":
        return "🔌 EVX"
    if nf == "evie":
        return "🕊️ Evie"
    if "bp" in nf:
        return "🟢 BP"
    if nf == "tesla":
        return "🔴 SC" if st.speed_tier == "supercharger" else "🔌 Dest"
    return st.type_icon()


def render_stations_table(
    stations: List[UnifiedStationRecord],
    title: str,
    eval_time: Optional[str] = None,
    ref_label: Optional[str] = None,
    ref_lat: Optional[float] = None,
    ref_lon: Optional[float] = None,
    active_radius: Optional[float] = None,
    sort_mode: Optional[str] = None,
    network_filter: Optional[str] = None
) -> None:
    """Renders rich executive 10/11-column Unicode table of stations matching Tesla Explorer styling."""
    if not stations:
        total_inner = 88
        print(f"\n┌{'─'*total_inner}┐")
        print(format_title_line(f" {title}", total_inner, "left"))
        print(format_box_line("├", "─", "┤", [total_inner]))
        print(f"│{pad_display(' ℹ️  No charging stations found matching the criteria.', total_inner, 'left')}│")
        print(format_box_line("└", "─", "┘", [total_inner]))
        return

    has_dist = any(s.distance_km is not None for s in stations)
    
    max_name_len = max((display_len(s.name) for s in stations), default=28)
    w_name = max(28, min(40, max_name_len + 2))
    
    max_suburb_len = max((display_len(s.suburb or s.state or "") for s in stations), default=18)
    w_suburb = max(18, min(24, max_suburb_len + 2))

    widths = [6, 8, 7, w_name, 6, 9, 13, 12, 22]
    headers = ["  #   ", "  Type  ", " State ", "          Station Name          ", " Tier ", " Stalls  ", "   Access    ", " Rate (Now) ", "   Period / Window    "]
    
    if has_dist:
        widths.extend([9, w_suburb])
        headers.extend(["  Dist   ", " Location / Suburb "])
    else:
        widths.append(w_suburb)
        headers.append(" Location / Suburb ")

    total_inner_w = sum(widths) + len(widths) - 1

    # Top Banner Box
    print(f"\n┌{'─' * total_inner_w}┐")
    print(f"│{pad_display(f' ⚡ {title.upper()} ({len(stations)} found)', total_inner_w, 'left')}│")
    
    if ref_label:
        radius_note = f" [within {active_radius:.0f} km]" if (active_radius and active_radius > 0) else ""
        sort_str = f" [Sorted by: {sort_mode}]" if sort_mode else ""
        coords_str = f" ({ref_lat:.4f}, {ref_lon:.4f})" if (ref_lat is not None and ref_lon is not None) else ""
        print(f"│{pad_display(f' 📍 {C_CYAN}Proximity Origin:{C_RESET} {ref_label}{coords_str}{radius_note}{sort_str}', total_inner_w, 'left')}│")
    elif sort_mode:
        print(f"│{pad_display(f' 📊 {C_BOLD}Sort Order:{C_RESET} {sort_mode}', total_inner_w, 'left')}│")

    eval_label = f"Target Time: {eval_time}" if eval_time else "Current Local Time"
    print(f"│{pad_display(f' ⏰ {C_BOLD}Pricing Evaluation:{C_RESET} {eval_label}', total_inner_w, 'left')}│")

    c_up = sum(1 for s in stations if s.get_status() == "UP_TO_DATE")
    c_renamed = sum(1 for s in stations if s.get_status() == "RENAMED")
    c_stale = sum(1 for s in stations if s.get_status() == "STALE")
    c_new = sum(1 for s in stations if s.get_status() == "NOT_IN_JSON")
    legend_line = f" 📊 {C_BOLD}Status Legend:{C_RESET} [{C_GREEN} 1 {C_RESET}] Up to Date: {c_up}  |  [{C_MAGENTA} 2 {C_RESET}] Renamed: {c_renamed}  |  [{C_BLUE} 3 {C_RESET}] Stale (>3mo): {c_stale}  |  [{C_ORANGE} 4 {C_RESET}] Not in JSON: {c_new}"
    print(f"│{pad_display(legend_line, total_inner_w, 'left')}│")
    
    # Table Header Line
    top_b = "├" + "┬".join("─" * w for w in widths) + "┤"
    print(top_b)
    
    h_cells = [pad_display(f"{C_BOLD}{h.strip()}{C_RESET}", w, "center") for h, w in zip(headers, widths)]
    print("│" + "│".join(h_cells) + "│")
    
    mid_b = "├" + "┼".join("─" * w for w in widths) + "┤"
    print(mid_b)

    for idx, st in enumerate(stations, 1):
        status = st.get_status()
        color = C_GREEN if status == "UP_TO_DATE" else (C_MAGENTA if status == "RENAMED" else (C_BLUE if status == "STALE" else C_ORANGE))
        num_str = f"[{color}{idx:2d}{C_RESET}]"
        t_icon = _contextual_type_icon(st, network_filter)
        state_str = st.state or "-"
        name_disp = truncate_display(st.name, w_name - 2)
        tier_str = st.tier_badge()
        stalls_str = f"{st.stalls_total} bays" if st.stalls_total > 0 else "-"
        
        acc_lbl = st.access_label()
        if acc_lbl == "Tesla Only":
            access_str = f"{C_CYAN}Tesla Only{C_RESET}"
        elif acc_lbl == "Private":
            access_str = f"{C_RED}Private{C_RESET}"
        else:
            access_str = f"{C_GREEN}{acc_lbl}{C_RESET}"

        eff_rate, eff_lbl, eff_win = st.get_effective_pricing(eval_time)
        if eff_rate is not None:
            if eff_rate == 0.0:
                rate_str = "Free"
            else:
                rate_str = f"${eff_rate:.2f}/kWh"
            period_str = f"{eff_lbl} ({eff_win})" if (eff_win and eff_win != "24/7" and eff_win != "00:00–24:00") else eff_lbl
        else:
            rate_str = "-"
            period_str = "-"

        period_disp = truncate_display(period_str, 20)
        suburb_val = st.suburb or st.state or "-"
        suburb_disp = truncate_display(suburb_val, w_suburb - 2)

        row_cells = [
            pad_display(num_str, widths[0], "center"),
            pad_display(format_type_badge(t_icon), widths[1], "left"),
            pad_display(state_str, widths[2], "center"),
            pad_display(" " + name_disp, widths[3], "left"),
            pad_display(tier_str, widths[4], "center"),
            pad_display(stalls_str + " ", widths[5], "right"),
            pad_display(" " + access_str, widths[6], "left"),
            pad_display(rate_str + " ", widths[7], "right"),
            pad_display(" " + period_disp, widths[8], "left"),
        ]

        if has_dist:
            dist_val = st.distance_km
            dist_text = f"{dist_val:.1f} km " if dist_val is not None else "-- "
            row_cells.append(pad_display(dist_text, widths[9], "right"))
            row_cells.append(pad_display(f" {C_DIM}{suburb_disp}{C_RESET}", widths[10], "left"))
        else:
            row_cells.append(pad_display(f" {C_DIM}{suburb_disp}{C_RESET}", widths[9], "left"))

        print("│" + "│".join(row_cells) + "│")

    bot_b = "└" + "┴".join("─" * w for w in widths) + "┘"
    print(bot_b)


def browse_chargefox_catalog_grid(manager: ChargerDiscoveryManager, country: str = "Australia", auto_save: bool = False) -> None:
    """
    Live Chargefox/NRMA catalog browser mirroring charger_find_tesla.py's
    browse_stations_by_type() grid+status-legend+menu UX (REQ-060,
    AGENTS.local.md, 2026-09-14) - built so `charger_find.py --network
    chargefox --live` gets the same rich per-state browsing find_tesla_
    chargers.py --country Australia already has, instead of the flat
    render_stations_table() view.

    Chargefox's GraphQL catalog is cheap enough to fetch whole in a handful
    of batched queries (unlike Tesla's per-station browser scrape), so
    there's no separate light "list" vs heavy "detail" step to gate behind
    the menu the way Tesla's 'all'/'new'/'renamed'/'stale' subset-scrape
    does - fetching live IS the preview. sync_all_australia(auto_save=False)
    keeps this non-destructive: nothing overwrites
    Tessie/chargefox_chargers.json until the user explicitly picks 'save' (or passes
    auto_save=True, wired to the top-level --save/--update flag, for a one-shot
    non-interactive refresh: `--live --network chargefox --save`).
    """
    from charger_find_tesla import AU_STATE_MAP

    provider = next((p for p in manager.providers if isinstance(p, ChargefoxProvider)), None)
    if provider is None:
        print(f"{C_RED}❌ Chargefox provider not configured.{C_RESET}")
        return

    old_records = provider.load_cached_registry()
    old_by_id = {r.source_id: r for r in old_records if r.source_id}

    print(f"\n{C_CYAN}🌐 Fetching Chargefox & NRMA locations across {country}...{C_RESET}")
    print(f"   {C_DIM}https://app.chargefox.com/graphql{C_RESET}")
    live_records = provider.sync_all_australia(auto_save=False)
    if not live_records:
        print(f"{C_YELLOW}No Chargefox locations returned.{C_RESET}")
        return

    grouped: Dict[str, List[UnifiedStationRecord]] = {}
    statuses: Dict[str, str] = {}
    renamed_from: Dict[str, str] = {}
    for rec in live_records:
        old = old_by_id.get(rec.source_id)
        if old is None:
            status = "NOT_IN_JSON"
        elif (old.name or "").strip().lower() != (rec.name or "").strip().lower():
            status = "RENAMED"
            renamed_from[rec.key] = old.name
        else:
            status = "UP_TO_DATE"
        statuses[rec.key] = status
        state_key = rec.state or "-"
        grouped.setdefault(state_key, []).append(rec)

    counts = {"UP_TO_DATE": 0, "RENAMED": 0, "NOT_IN_JSON": 0}
    for s in statuses.values():
        counts[s] += 1

    print(f"\n{C_BOLD}{'='*80}{C_RESET}")
    print(f"  ⚡ {C_BOLD}CHARGEFOX & NRMA STATIONS ({len(live_records)} total){C_RESET}")
    print(f"  {C_BOLD}📊 Status Breakdown:{C_RESET} [{C_GREEN} Up to Date: {counts['UP_TO_DATE']} {C_RESET}] | [{C_MAGENTA} Renamed / Discrepancy: {counts['RENAMED']} {C_RESET}] | [{C_ORANGE} Not in JSON: {counts['NOT_IN_JSON']} {C_RESET}]")
    print(f"{C_BOLD}{'='*80}{C_RESET}\n")

    def fmt_entry(rec: UnifiedStationRecord, counter: int) -> str:
        status = statuses[rec.key]
        color = C_GREEN if status == "UP_TO_DATE" else (C_MAGENTA if status == "RENAMED" else C_ORANGE)
        title_str = rec.name
        if status == "RENAMED":
            title_str += f" {C_MAGENTA}🔄 (in JSON: {renamed_from[rec.key]}){C_RESET}"
        # Use the record's own network-appropriate icon (🦊 Chargefox, ⚡ NRMA,
        # etc.) instead of a hardcoded generic plug - BUG-090 follow-on.
        icon = rec.type_icon().split(" ", 1)[0]
        return f" [{color}{counter:3d}{C_RESET}] {icon} {title_str}"

    max_len = 0
    for rec in live_records:
        l_len = display_len(rec.name)
        if statuses[rec.key] == "RENAMED":
            l_len += display_len(f" 🔄 (in JSON: {renamed_from[rec.key]})")
        max_len = max(max_len, l_len)
    col_width = max(38, max_len + 12)

    station_lookup: Dict[int, UnifiedStationRecord] = {}
    counter = 1
    for state_key in sorted(grouped.keys()):
        st_list = sorted(grouped[state_key], key=lambda r: r.name or "")
        state_header = f"{AU_STATE_MAP.get(state_key, state_key)} ({state_key})" if state_key in AU_STATE_MAP else state_key
        print(f"{C_CYAN}{C_BOLD}📍 {state_header} [{len(st_list)} stations]:{C_RESET}")
        for i in range(0, len(st_list), 2):
            left = st_list[i]
            left_text = fmt_entry(left, counter)
            station_lookup[counter] = left
            counter += 1
            if i + 1 < len(st_list):
                right = st_list[i + 1]
                right_text = fmt_entry(right, counter)
                station_lookup[counter] = right
                counter += 1
                print(f"{pad_display(left_text, col_width)} {right_text}")
            else:
                print(left_text)
        print()

    if auto_save:
        provider.save_cached_registry(live_records)
        print(f"\u2714 Saved {len(live_records)} Chargefox/NRMA locations to {provider.registry_path}")
        return

    print(f"{C_BOLD}Options:{C_RESET}")
    print(f"  \u2022 Enter a station number [1-{len(live_records)}] to inspect details")
    print(f"  \u2022 Type {C_GREEN}'save'{C_RESET} to write all {len(live_records)} freshly-fetched locations into the registry (archives prior data)")
    print(f"  \u2022 Press {C_YELLOW}Enter{C_RESET} or Ctrl+C to skip without saving")
    print()

    try:
        pick = input("Select option (number, 'save', or Enter to skip): ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nExiting.")
        return

    if not pick:
        return

    if pick.lower() in ("save", "s", "y", "yes"):
        provider.save_cached_registry(live_records)
        print(f"\u2714 Saved {len(live_records)} Chargefox/NRMA locations to {provider.registry_path}")
        return

    if pick.isdigit() and int(pick) in station_lookup:
        display_station_inspection_card(station_lookup[int(pick)])
    else:
        print(f"{C_YELLOW}Invalid selection.{C_RESET}")


def format_station_address(station: UnifiedStationRecord) -> str:
    addr = (station.address or "").strip()
    if not addr:
        parts = [station.suburb, station.state, station.postcode]
        return ", ".join(p for p in parts if p) or "Address not specified"
    parts = [addr]
    addr_lower = addr.lower()
    if station.suburb and station.suburb.lower() not in addr_lower:
        parts.append(station.suburb)
    if station.state and station.state.lower() not in addr_lower:
        parts.append(station.state)
    if station.postcode and station.postcode not in addr:
        parts.append(station.postcode)
    return ", ".join(parts)


def display_station_inspection_card(station: UnifiedStationRecord, eval_time: Optional[str] = None) -> None:
    """Renders comprehensive station details inspection card matching Tesla explorer styling."""
    box_w = 86
    inner_w = box_w - 2
    
    if station.source == "tesla" and station.speed_tier == "supercharger":
        title_content = f"TESLA SUPERCHARGER: {station.name}"
    elif station.source == "tesla":
        title_content = f"TESLA DESTINATION CHARGER: {station.name}"
    else:
        title_content = f"⚡ STATION DETAILS: {station.name}"

    pad_total = max(0, inner_w - display_len(title_content))
    pad_l = pad_total // 2
    pad_r = pad_total - pad_l
    header_line = f"║{' ' * pad_l}{title_content}{' ' * pad_r}║"

    print(f"\n╔{'═' * inner_w}╗")
    print(header_line)
    print(f"╚{'═' * inner_w}╝\n")

    def _field(icon: str, label: str, value: Any):
        if value is None or value == "":
            return
        val_str = str(value).strip()
        print(format_field_line(icon, label, val_str, icon_w=2, label_w=22, indent=2))

    meta = station.metadata or {}

    # 1. Source & Provenance
    views_count = len(station.views) if station.views else 1
    view_names = ", ".join([v.title() for v in station.views.keys()]) if station.views else station.source.title()
    if views_count > 1:
        _field("📂", "Data Source", f"Multi-Source Consolidated ({views_count} sources: {view_names})")
    elif station.source == "tesla":
        reg_name = "tesla_superchargers.json" if station.speed_tier == "supercharger" else "tesla_chargers.json"
        # Matches charger_find_tesla.py's own display_preview() coloring
        # (green = trusted local registry) - this line lost its color when
        # the display logic was ported into the provider system. Fixed 2026-09-14.
        _field("📂", "Data Source", f"{C_GREEN}Local JSON Registry (Tessie/{reg_name}){C_RESET}")
    elif station.source == "chargefox":
        _field("📂", "Data Source", f"Chargefox Registry (ID: {station.source_id})")
    elif station.source == "evx":
        _field("📂", "Data Source", f"EVX Registry (ID: {station.source_id})")
    elif station.source == "plugshare":
        _field("📂", "Data Source", f"PlugShare Registry (ID: {station.source_id})")
    else:
        _field("📂", "Data Source", f"{station.source.title()} Registry (ID: {station.source_id})")

    _field("📍", "Station Key", station.key or station.name)
    short_id = clean_station_short_name(meta.get("short_name") or station.source_id or station.key)
    _field("🏷️", "Short ID", short_id)

    co_nets = meta.get("co_located_networks") or []
    if len(co_nets) > 1:
        _field("🌐", "Co-located Networks", ", ".join(co_nets))
    elif station.network and station.network not in ("3rd-Party", "unknown"):
        _field("⚡", "Network", station.network)

    findus_list = meta.get("findus_list_name")
    findus_loc = meta.get("findus_location_name")
    if findus_list and findus_loc and findus_list.strip().lower() != findus_loc.strip().lower():
        # findus_list_name (lesser importance) is Tesla's own findus LIST page
        # grouping name for this charger, which can genuinely differ from its
        # own findus_location_name (e.g. New Italy, NSW is grouped under
        # "Ballina, NSW" on the list page). Linked to the findus URL since
        # that's where the list name comes from. Fixed 2026-09-14.
        findus_url_for_link = meta.get("findus_url", "")
        list_display = hyperlink(findus_list, findus_url_for_link) if findus_url_for_link else findus_list
        _field("📋", "Find Us List Name", list_display)
        _field("📌", "Find Us Location Name", findus_loc)
    
    suburb_loc = station.suburb or (meta.get("general_location") if meta else "")
    if suburb_loc:
        _field("🏢", "General Location", suburb_loc)

    clean_addr = format_station_address(station)
    _field("📮", "Address", clean_addr)
    if station.lat is not None and station.lon is not None:
        _field("🌐", "GPS Coordinates", f"({station.lat:.6f}, {station.lon:.6f})")
    
    if station.distance_km is not None:
        _field("📏", "Distance", f"{station.distance_km:.2f} km")

    _field("🌐", "Timezone", station.timezone or "Australia/Sydney")
    # max_len raised from the 55-char default: this box is 86 wide with a
    # 22-char label column, so a typical ~60-70 char findus/plugshare URL
    # fits without truncation - the default was cutting off URLs (like
    # Tesla's findus/location/supercharger/<id> ones) that would display in
    # full. Fixed 2026-09-14.
    source_urls = station.get_source_urls()
    if len(source_urls) > 1:
        for u in source_urls:
            _field(u["icon"], f"{u['name']} URL", format_display_url(u["url"], max_len=75))
    elif source_urls:
        u = source_urls[0]
        _field(u["icon"], f"{u['label']} URL", format_display_url(u["url"], max_len=75))
    elif station.url:
        url_label = "Find Us URL" if station.source == "tesla" else "Web Link / URL"
        _field("🔗", url_label, format_display_url(station.url, max_len=75))

    # 2. Hardware & Access
    pwr_str = f"Up to {station.formatted_power()}" if station.max_power_kw else "Standard Power"
    hw_model = f" ({station.hardware_model})" if station.hardware_model else ""
    hw_desc = f"{station.stalls_total} Stalls | {pwr_str}{hw_model}"
    _field("🔌", "Hardware", hw_desc)
    
    cb = (station.metadata or {}).get("connector_breakdown")
    if cb and isinstance(cb, list) and len(cb) > 0:
        _field("🔌", "Connectors", ", ".join(cb))
    elif station.plugs:
        _field("🔌", "Connectors", ", ".join(station.plugs))

    meta = station.metadata or {}
    # Non-Tesla Access / Compatibility
    if station.speed_tier == "supercharger":
        open_all = meta.get("open_to_non_tesla")
        acc_str = f"{C_GREEN}YES (CCS2 All EVs){C_RESET}" if open_all else f"{C_RED}NO (Tesla Only){C_RESET}"
        _field("🚗", "Non-Tesla Access", acc_str)
    elif "tesla" in (station.network or "").lower() or "tesla" in (station.source or "").lower():
        open_all = meta.get("open_to_non_tesla")
        t_only = meta.get("tesla_only")
        if open_all:
            acc_str = f"{C_GREEN}YES (Type 2 All EVs){C_RESET}"
        elif t_only:
            acc_str = f"{C_CYAN}Tesla Only{C_RESET}"
        else:
            acc_str = f"{C_GREEN}YES (Type 2 All EVs){C_RESET}"
        _field("🚗", "Access / Non-Tesla", acc_str)
    else:
        acc_str = meta.get("access_type") or "Public (Open to All EVs)"
        _field("🚗", "Access / Non-Tesla", acc_str)

    hours = meta.get("hours") or meta.get("access_hours")
    if hours:
        _field("🕒", "Operating Hours", hours)

    score = meta.get("score")
    if score is not None:
        try:
            _field("⭐", "PlugShare Rating", f"⭐ {float(score):.1f} / 10")
        except (ValueError, TypeError):
            _field("⭐", "PlugShare Rating", f"⭐ {score}")

    # Fee Rules (Tesla Policy: Superchargers enforce either an Idle Rule OR a Congestion Rule, never both. Destination Chargers have no congestion fees.)
    if station.source == "tesla" and station.speed_tier != "supercharger":
        # Destination Chargers have no congestion fees or idle fees
        pass
    elif station.congestion_fee_per_min and station.congestion_fee_per_min > 0:
        _field("🚦", "Congestion Charge", f"${station.congestion_fee_per_min:.2f}/min")
        _field("🚦", "Congestion Rule", "Applies when busy over 80% SoC or charging ends (5 min grace)")
    elif station.idle_fee_per_min and station.idle_fee_per_min > 0:
        if station.speed_tier == "supercharger":
            _field("⏱️", "Idle Charge", f"${station.idle_fee_per_min:.2f}/min ($2.00/min at 100% occupancy)")
            _field("⏱️", "Idle Rule", "Applies after charging ends when ≥50% occupied (5 min grace)")
        else:
            grace = meta.get("idle_fee_grace_min")
            grace_str = f" ({grace} min grace)" if grace else ""
            _field("⏱️", "Idle Charge", f"${station.idle_fee_per_min:.2f}/min")
            _field("⏱️", "Idle Rule", f"Applies after charging completes{grace_str}")

    if station.parking_fee:
        _field("🅿️", "Parking Fee", station.parking_fee)

    # Combined Effective Date/Last Verified - mirrors charger_find_tesla.py's
    # display_preview() exactly. "Last Verified" alone told you when the data
    # was last re-checked but not which historic period it actually applies
    # to; tessie_charging_analyzer.py needs the effective date to match old
    # invoices against the right (possibly archived) registry entry. Fixed
    # 2026-09-14 - see AGENTS.local.md.
    if station.effective_date_utc and station.last_verified_utc:
        _field("🕒", "Effective Date", f"{format_local_timestamp(station.effective_date_utc)} (Verified: {format_local_timestamp(station.last_verified_utc)})")
    elif station.last_verified_utc:
        _field("🕒", "Last Verified", format_local_timestamp(station.last_verified_utc))
    elif station.effective_date_utc:
        _field("🕒", "Effective Date", format_local_timestamp(station.effective_date_utc))

    # 3. Tariff & Time-of-Use Rates
    curr = station.currency or "AUD"
    if station.schedule:
        is_tou = len(station.schedule) > 1
        pricing_title = "Tesla & Members" if station.source == "tesla" else (station.network or "Network")
        section_title = f"{pricing_title} {'Time-of-Use' if is_tou else 'Flat'} Rates ({curr})"
        print(f"\n  💰 {C_BOLD}{section_title}:{C_RESET}")
        for item in station.schedule:
            lbl = item.get("name") or item.get("label") or "Standard Rate"
            st = item.get("start") or item.get("start_time") or "00:00"
            et = item.get("end") or item.get("end_time") or "24:00"
            rate = item.get("rate") if item.get("rate") is not None else item.get("rate_per_kwh", 0.0)
            if st == "00:00" and et == "24:00" and not is_tou:
                print(f"    • {pad_display('Flat Rate (24/7)', 21)} {C_GREEN}${rate:.2f}/kWh{C_RESET}")
            else:
                print(f"    • {pad_display(lbl, 21)} {st} – {et}: ${rate:.2f}/kWh")
    elif station.current_rate_per_kwh is not None and station.current_rate_per_kwh > 0:
        section_title = f"{station.network or 'Network'} Flat Rate ({curr})"
        print(f"\n  💰 {C_BOLD}{section_title}:{C_RESET}")
        print(f"    • {pad_display('Flat Rate (24/7)', 21)} {C_GREEN}${station.current_rate_per_kwh:.2f}/kWh{C_RESET}")
    elif station.rate_range:
        section_title = f"{station.network or 'Network'} Pricing ({curr})"
        print(f"\n  💰 {C_BOLD}{section_title}:{C_RESET}")
        print(f"    • {pad_display('Rate Range', 21)} {station.rate_range}")
    else:
        print(f"\n  💰 {C_BOLD}Pricing:{C_RESET} {C_YELLOW}Free or Not Specified{C_RESET}")

    # Non-Tesla Flat/TOU Rates - mirrors charger_find_tesla.py's own
    # display_preview() nt_scheds block, which this provider-backed card was
    # entirely missing until 2026-09-14 (see AGENTS.local.md). Only Tesla
    # stations populate station.non_tesla_schedule today (tesla_provider.py),
    # so this only prints for those, same as the old script.
    if station.non_tesla_schedule:
        is_nt_tou = len(station.non_tesla_schedule) > 1
        nt_title = f"Non-Tesla {'Time-of-Use' if is_nt_tou else 'Flat'} Rates ({curr})"
        print(f"\n  🔌 {C_BOLD}{nt_title}:{C_RESET}")
        for item in station.non_tesla_schedule:
            lbl = item.get("name") or item.get("label") or "Non-Tesla Rate"
            st = item.get("start") or item.get("start_time") or "00:00"
            et = item.get("end") or item.get("end_time") or "24:00"
            rate = item.get("rate") if item.get("rate") is not None else item.get("rate_per_kwh", 0.0)
            if st == "00:00" and et == "24:00" and not is_nt_tou:
                print(f"    • {pad_display('Flat Rate (24/7)', 21)} {C_GREEN}${rate:.2f}/kWh{C_RESET}")
            else:
                print(f"    • {pad_display(lbl, 21)} {st} – {et}: ${rate:.2f}/kWh")
    elif station.source == "tesla" and station.speed_tier == "supercharger" and (station.metadata or {}).get("open_to_non_tesla"):
        print(f"\n  🔌 {C_BOLD}Non-Tesla Pricing:{C_RESET} {C_YELLOW}Not available (re-scrape to fetch){C_RESET}")

    # "Also Listed On" - BUG-075 (2026-09-15, AGENTS.local.md). A merged
    # multi-source record only ever displayed ONE side's pricing/access above
    # (manager.py's merge picks a single "primary" rate_range/access, the
    # other source's own numbers are discarded from the top-level fields even
    # though they're still sitting in station.views[source]). User: "when
    # displaying Chargefox - list it as a Chargefox, when listing Tesla-dest -
    # list it as that. But also somewhere in the deep dive you need to
    # provide both details clearly." A Tesla Destination Charger and its
    # co-listed Chargefox entry can genuinely have different prices/access
    # (Tesla member rate vs Chargefox public rate) - this surfaces the OTHER
    # source(s)' own raw price/access snippet from their untouched view data,
    # best-effort across each provider's own raw field shapes.
    if station.views and len(station.views) > 1:
        primary_key = (station.source or "").split("+")[0].strip().lower()
        other_views = {k: v for k, v in station.views.items() if k.lower() != primary_key and isinstance(v, dict)}
        if other_views:
            print(f"\n  🌐 {C_BOLD}Also Listed On:{C_RESET}")
            for src_key, view in other_views.items():
                price_snip = ""
                pr = view.get("priceRange")
                if isinstance(pr, dict) and pr.get("energyDescription"):
                    price_snip = str(pr["energyDescription"]).strip()
                if not price_snip:
                    for ped in (view.get("pedestals") or []):
                        for conn in (ped.get("connectors") or []):
                            if conn.get("price_raw"):
                                price_snip = str(conn["price_raw"]).strip()
                                break
                        if price_snip:
                            break
                if not price_snip:
                    for key in ("price_raw", "rate_range", "priceDescription"):
                        if view.get(key):
                            price_snip = str(view[key]).strip()
                            break
                access_snip = ""
                for key in ("access", "accessType", "status"):
                    v = view.get(key)
                    if isinstance(v, str) and v.strip():
                        access_snip = v.strip()
                        break
                bits = [b for b in (price_snip, access_snip) if b]
                detail = " | ".join(bits) if bits else "see its own URL above for pricing/access"
                print(f"    • {pad_display(src_key.title(), 12)} {detail}")

    cost_desc = meta.get("cost_description")
    if cost_desc and cost_desc != station.rate_range:
        _field("📝", "Pricing Notes", cost_desc)

    desc = meta.get("description") or meta.get("access_description")
    if not desc and station.views:
        for v in station.views.values():
            if isinstance(v, dict):
                v_desc = v.get("description") or v.get("access_description") or v.get("access", {}).get("description")
                if v_desc:
                    desc = v_desc
                    break

    if desc:
        print(f"\n  📝 {C_BOLD}Access Instructions & Notes:{C_RESET}")
        for line in desc.split("\n"):
            if line.strip():
                print(f"    {line.strip()}")

    if station.amenities:
        if isinstance(station.amenities, dict):
            amenity_list = [k.replace('_', ' ').title() for k, v in station.amenities.items() if v and k != "items"]
            if "items" in station.amenities and isinstance(station.amenities["items"], list):
                amenity_list.extend(station.amenities["items"])
            if amenity_list:
                print(f"\n  🛒 {C_BOLD}Amenities:{C_RESET} {', '.join(amenity_list)}")
        elif isinstance(station.amenities, list) and station.amenities:
            print(f"\n  🛒 {C_BOLD}Amenities:{C_RESET} {', '.join(str(x) for x in station.amenities)}")

    print()


def main():
    # AGENTS.local.md BUG-074, 2026-09-15: user - "The usage / man here is
    # terrible!!!" - followed by a request to structure --help the way a real
    # man page does (NAME / SYNOPSIS / DESCRIPTION / OPTIONS / EXAMPLES; see
    # https://gist.github.com/sebastiancarlos/a7fb89f73b4617ccc2ea9445abf62f90
    # for the conventions this follows). The old version left argparse to
    # auto-generate its usage line, which for 50+ flags with no `usage=`
    # override sprawled across 17 wrapped lines with every flag dumped in
    # alphabetical-ish order - nothing like a SYNOPSIS a human can scan. This
    # version supplies a curated, mode-based `usage=` (the tool really has 5
    # distinct modes - sync, browse/filter, live-catalog, inspect, plugshare -
    # so one flat bracket-soup can never represent it well) plus a NAME line
    # and a trimmed DESCRIPTION; OPTIONS is the real add_argument_group()
    # split below (BUG-074 continued); EXAMPLES is the epilog, unchanged in
    # content but now under one consistent heading style.
    synopsis = """charger_find.py --sync-all | --sync-tesla | --sync-chargefox | --sync-evx |
                     --sync-evie | --sync-bppulse | --sync-plugshare | --sync-ampcharge | --export-consolidated
                     [--near PLACE] [--radius KM] [--state ST] [--new] [--add-all]
   or:  charger_find.py [--near PLACE | --all | --query TEXT] [--state ST]
                     [--sc | --dc | --ac] [--network NAME] [--min-kw KW]
                     [--sort KEY] [--time HH:MM] [--limit N] [--json]
   or:  charger_find.py --live [--sc] [--network tesla | chargefox] [--country NAME]
   or:  charger_find.py --inspect QUERY [--live] [--save]
   or:  charger_find.py --plugshare ID[,ID...] [--save]
"""
    parser = argparse.ArgumentParser(
        usage=synopsis,
        description="""NAME
  charger_find.py - unified multi-network EV charging discovery, comparison,
  and synchronization engine (Tesla, Chargefox/NRMA, PlugShare-aggregated
  networks, and a consolidated master catalog) for Australia and beyond.

DESCRIPTION
  1. Tesla (Tier 1 First-Party): Find Us API + local JSON registries, with
     dual-layer naming (specific location name over regional list label),
     full hardware tiers (Supercharger V2/V3/V4, AC Destination Chargers),
     and Time-of-Use tariffs.
  2. Chargefox & NRMA (Tier 1 First-Party): direct GraphQL integration
     (918+ AU locations incl. NRMA, WA EV Network, QLD Electric Highway).
  3. PlugShare (Tier 2 Aggregator): Evie, BP Pulse, AmpCharge, Exploren,
     JOLT, EVUp, Engie, ChargePoint, and independent sites, with spatial
     grid tiling to avoid API bounding-box truncation on dense clusters.
  4. Consolidated Master: spatial dedup (<=60m) merges the same physical
     site across networks into one record, First-Party data taking
     precedence over aggregator estimates - see Tessie/charging_stations.json.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""EXAMPLES
  Synchronization:
    # Sync All Providers end-to-end (Tesla, Chargefox, PlugShare, and Master Catalog):
    ./Tools/charger_find.py --sync-all --near Home --radius 50

    # Sync Chargefox & NRMA nationwide via live GraphQL API:
    ./Tools/charger_find.py --sync-chargefox

    # Sync PlugShare stations near a location or across a state:
    ./Tools/charger_find.py --sync-plugshare --near "Ryde" --radius 15 --add-all
    ./Tools/charger_find.py --sync-plugshare --state NSW --add-all

    # Sync Tesla Superchargers & Destination Chargers:
    ./Tools/charger_find.py --sync-tesla --sc --near Home --radius 50 --new
    ./Tools/charger_find.py --sync-tesla --dc --near Home --radius 50 --new

    # Refresh unlisted pricing or discover chargers from past charging history:
    ./Tools/charger_find.py --refresh-prices
    ./Tools/charger_find.py --from-history --add-all

    # Compile & Export Consolidated Master Catalog (charging_stations.json):
    ./Tools/charger_find.py --export-consolidated

  Proximity & Search:
    # Find all chargers near an address sorted by distance:
    ./Tools/charger_find.py --near "100 George St, Sydney" --limit 10

    # Find cheapest charging options near Home (defaults to 15km radius):
    ./Tools/charger_find.py --near Home --sort price --radius 25

    # Evaluate Time-of-Use pricing at peak vs off-peak hours:
    ./Tools/charger_find.py --near Home --time 14:00 --sort price
    ./Tools/charger_find.py --near Home --time 23:30 --sort price

    # Filter high-power DC Fast Chargers (>= 150 kW, any network) or specific networks:
    ./Tools/charger_find.py --dc --min-kw 150 --near "Ryde"
    ./Tools/charger_find.py --network chargefox --state NSW
    ./Tools/charger_find.py --network tesla --sc --near Home

  Inspection & Drill-Down:
    # Inspect detailed card for a specific station by query:
    ./Tools/charger_find.py -q "Macquarie"

    # Inspect a PlugShare location ID directly:
    ./Tools/charger_find.py --plugshare 801149 --save
"""
    )
    
    # AGENTS.local.md BUG-074, 2026-09-15: --help had become unreadable - a
    # single flat argparse "options:" list of 50+ flags with no grouping
    # (the section comments below were never real argparse groups, just
    # visual dividers in the source), several help= strings carrying
    # multi-sentence commit-message-style rationale straight into user-facing
    # --help output (--tesla-dest, --unpriced, --live, --inspect). Split into
    # real add_argument_group()s matching those original section comments,
    # and trimmed every help= string to one concise line - the fuller
    # rationale for non-obvious flags now lives in a source comment instead.
    g_location = parser.add_argument_group("Location & Search Scope")
    g_location.add_argument("--near", "-l", type=str, help="Reference place name, address, or lat,lon (e.g. 'Home', 'Ryde')")
    g_location.add_argument("--radius", "-r", "--radius-km", type=float, default=15.0, help="Search radius in km (default: 15)")
    g_location.add_argument("--query", "-q", type=str, help="Text search by station name, suburb, or network")
    g_location.add_argument("--all", "-a", action="store_true", help="List all registered stations across the entire country or selected state")
    g_location.add_argument("--state", choices=["NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT", "nsw", "vic", "qld", "wa", "sa", "tas", "act", "nt"], help="Filter by State/Territory (e.g. NSW, VIC)")
    g_location.add_argument("--country", default="Australia", help="Country name (default: Australia)")

    g_speed = parser.add_argument_group("Charging Speed & Type Filter")
    # BUG-073 (2026-09-15) tried adding a dedicated --tesla-dest/--tesla-sc/
    # --tesla-dc flag family to disambiguate --dc from its Tesla-only meaning.
    # BUG-077 (2026-09-15, AGENTS.local.md): user - "We've just done
    # --tesla-sc and --tesla-dc. Maybe we should be consistent and use
    # --network." Right - the tool already has a --network mechanism for
    # exactly this (--network chargefox, --network evie, ...); giving Tesla
    # its own separate flag family instead of using that same mechanism was
    # inconsistent. Reverted: --sc/--dc/--ac are bare again, and Tesla's own
    # Destination Chargers are now selected the same way any other network's
    # subset is - "--network tesla --dc" (or --ac) - handled below wherever
    # this used to read args.tesla_dest.
    g_speed.add_argument("--sc", action="store_true", help="Tesla Superchargers only")
    g_speed.add_argument("--dc", action="store_true", help="DC Fast Chargers only (>= 25 kW), any network - combine with --network tesla for Tesla's own Destination Chargers")
    g_speed.add_argument("--ac", action="store_true", help="AC Public / Destination Chargers only (< 25 kW), any network - combine with --network tesla for Tesla's own Destination Chargers")
    g_speed.add_argument("--min-kw", type=float, default=0.0, help="Minimum charging power in kW (e.g. --min-kw 150)")

    g_network = parser.add_argument_group("Network Filter")
    g_network.add_argument("--network", "-n", type=str, help="Filter by network name (e.g. tesla, chargefox, nrma, evie, bp)")

    g_sort = parser.add_argument_group("Sorting & Time of Use")
    g_sort.add_argument("--sort", "-s", choices=["distance", "price", "power", "stalls", "name", "state"], default="distance", help="Sort order (default: distance)")
    g_sort.add_argument("-t", "--time", type=str, help="Evaluate TOU pricing at specific local time (HH:MM)")

    g_output = parser.add_argument_group("Output Controls")
    g_output.add_argument("--limit", type=int, default=100, help="Maximum number of stations to display (default: 100)")
    g_output.add_argument("--json", action="store_true", help="Output results in JSON format")

    g_sync = parser.add_argument_group("Synchronization")
    g_sync.add_argument("--sync-all", action="store_true", help="End-to-end sync across all providers (Tesla, Chargefox, EVX, Evie, bp pulse, PlugShare, AmpCharge, Master Catalog)")
    g_sync.add_argument("--sync-tesla", action="store_true", help="Download, verify and cache Tesla Superchargers and Destination Chargers")
    g_sync.add_argument("--sync-chargefox", action="store_true", help="Download and cache all Chargefox & NRMA stations across Australia")
    g_sync.add_argument("--sync-evx", action="store_true", help="Download and cache all EVX Kerbside Polechargers across Australia")
    g_sync.add_argument("--sync-evie", action="store_true", help="Download and cache all Evie Networks fast charging stations across Australia")
    g_sync.add_argument("--sync-bppulse", action="store_true", help="Download and cache all bp pulse DC Fast & Ultra-Fast charging stations across Australia")
    g_sync.add_argument("--sync-plugshare", action="store_true", help="Discover and cache PlugShare 3rd-party stations across Australia or near a location")
    g_sync.add_argument("--sync-ampcharge", action="store_true", help="Download and cache all Ampol AmpCharge & Ampol Foodary EV fast-charging sites across Australia")
    g_sync.add_argument("--export-consolidated", "--sync-master", action="store_true", help="Compile and export multi-view master charging_stations.json to Tessie data home")

    g_registry = parser.add_argument_group("Registry Filters & Batch Updates")
    g_registry.add_argument("--plugshare", "--inspect-plugshare", type=str, help="PlugShare location ID(s) to inspect / scrape live (comma-separated)")
    g_registry.add_argument("--refresh-prices", action="store_true", help="Fetch live PlugShare details for stations missing pricing and update the registry")
    g_registry.add_argument("--from-history", action="store_true", help="Auto-discover unregistered charging locations from charges_master.csv")
    g_registry.add_argument("--save", "--update", action="store_true", help="Save / update inspected stations into local registry")
    g_registry.add_argument("--add-all", action="store_true", help="Auto-add all discovered stations to local registry")
    g_registry.add_argument("--status", choices=["UP_TO_DATE", "RENAMED", "STALE", "NOT_IN_JSON", "uptodate", "up_to_date", "up-to-date", "renamed", "stale", "new", "not_in_json"], help="Filter by registry status")
    g_registry.add_argument("--update-stale", action="store_true", help="Batch re-verify and update all stale stations across providers (archives changes)")
    g_registry.add_argument("--update-renamed", action="store_true", help="Batch re-verify and reconcile all renamed stations across providers (archives changes)")
    g_registry.add_argument("--new", action="store_true", help="Filter only stations not present in registry")
    g_registry.add_argument("--stale", action="store_true", help="Filter only stale stations (> 90 days since verification)")
    g_registry.add_argument("--renamed", action="store_true", help="Filter only renamed / discrepancy stations")
    # REQ-062 (2026-09-14, AGENTS.local.md): mirrors charger_find_tesla.py's
    # --unpriced. With --sync-tesla --all, also drives a batch refresh instead
    # of just filtering.
    g_registry.add_argument("--unpriced", "--missing-price", "--missing-pricing", action="store_true", help="Filter (or with --sync-tesla --all, batch refresh) stations missing pricing")
    g_registry.add_argument("--views", action="store_true", help="Display multi-view comparison (First-Party vs PlugShare) for matching stations")
    g_registry.add_argument("--audit-plugshare", "--audit", action="store_true", help="Run full discrepancy audit between First-Party registries and PlugShare")

    g_tuning = parser.add_argument_group("Network Request Tuning")
    g_tuning.add_argument("--delay", type=float, default=0.5, help="Pacing delay in seconds between requests (default: 0.5)")
    g_tuning.add_argument("--timeout", type=int, default=35, help="Network timeout in seconds per page load (default: 35)")
    g_tuning.add_argument("--retries", type=int, default=3, help="Max retry attempts per station (default: 3)")

    g_live = parser.add_argument_group("Live Browsing & Inspection")
    # REQ-060 (2026-09-14, AGENTS.local.md): only Tesla (--network tesla or
    # bare --sc) and Chargefox have one canonical upstream list to diff a
    # local registry against - matches charger_find_tesla.py's --country
    # explorer. BUG-077 (2026-09-15): dropped the short-lived --tesla-dest
    # flag in favor of --network tesla, consistent with every other network.
    g_live.add_argument("--live", action="store_true", help="Browse the live upstream catalog grouped by state with a Renamed/Stale/Not-in-JSON legend (Tesla via --network tesla or --sc, or Chargefox via --network chargefox)")
    # REQ-061 (2026-09-14, AGENTS.local.md): reuses charger_find_tesla.py's
    # own inspect_or_scrape_station(), same behavior as --inspect there.
    g_live.add_argument("--inspect", type=str, help="Inspect a single Tesla station by name/query (offline cache by default; add --live to re-scrape, --save to write into the registry)")

    # User report (2026-09-15): running with literally no arguments at all
    # looked "frozen" - line 1416 below (`args.all or args.state or (not
    # args.near and not args.query)`) treats an all-defaults Namespace as
    # "full country / state list mode" and silently kicks off a nationwide,
    # every-provider get_all_stations() fetch with zero printed explanation
    # first. Bare `charger_find.py` should show help instead of guessing at
    # a real command, matching argparse's own convention for a tool with no
    # sensible zero-argument default action.
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)

    args = parser.parse_args()

    # BUG-078 (2026-09-15, AGENTS.local.md): user typo'd "--network changefox"
    # (an easy transposition of "chargefox") and got a generic, unhelpful
    # "--live browsing isn't available for this network selection" - nothing
    # told them the network NAME itself was probably the mistake, since
    # --network has always accepted any free-text string with zero
    # validation. Real aggregator sub-brand names (Exploren, WA EV Network,
    # QLD Electric Highway, ...) mean a hard allowlist would reject valid
    # input, so this only WARNS - via a fuzzy match against the known
    # first-party network names - and always still proceeds with whatever the
    # user typed as a free-text filter.
    if args.network:
        _KNOWN_NETWORKS = ["tesla", "chargefox", "nrma", "evie", "evx", "bppulse", "plugshare", "ampcharge"]
        _net_check = args.network.strip().lower()
        if _net_check not in _KNOWN_NETWORKS and not any(_net_check in n or n in _net_check for n in _KNOWN_NETWORKS):
            import difflib
            _close = difflib.get_close_matches(_net_check, _KNOWN_NETWORKS, n=1, cutoff=0.6)
            if _close:
                print(f"{C_YELLOW}⚠ Unknown network '{args.network}' - did you mean '{_close[0]}'? (Known: {', '.join(_KNOWN_NETWORKS)}. Proceeding with '{args.network}' as a free-text filter anyway.){C_RESET}")
            else:
                print(f"{C_YELLOW}⚠ '{args.network}' doesn't match a known first-party network ({', '.join(_KNOWN_NETWORKS)}) - proceeding as a free-text filter, which also matches PlugShare-aggregated sub-brands like Exploren or WA EV Network.{C_RESET}")

    manager = ChargerDiscoveryManager()

    if args.sync_all:
        # AGENTS.md BUG-067: every step below used to catch its own
        # Exception, print a "⚠️ encountered error" line, and just carry
        # on - nothing tracked whether any step actually failed, so this
        # command always printed "✨ Full multi-network sync completed."
        # and exited 0 even if EVERY provider failed (auth error, network
        # down, a schema break like BUG-067's chargefox one). sync_results
        # now records a (label, ok) pair per step so the final banner and
        # exit code reflect what actually happened.
        sync_results = []

        print("🚀 [1/8] Starting Full Network Sync: Tesla Superchargers & Destination Chargers...")
        try:
            from charger_find_tesla import TeslaChargerExplorer
            explorer = TeslaChargerExplorer()
            ref_lat, ref_lon, ref_label = (None, None, None)
            if args.near:
                ref_lat, ref_lon, ref_label = resolve_reference_coords(args.near)
            
            radius = args.radius if args.radius is not None else 50.0
            for c_type in ["superchargers", "chargers"]:
                t_lbl = "Superchargers" if c_type == "superchargers" else "Destination Chargers"
                print(f"  ⚡ Fetching Tesla {t_lbl}...")
                t_stations = explorer.fetch_station_list(country=args.country or "Australia", charger_type=c_type)
                if ref_lat is not None and ref_lon is not None:
                    from providers.chargers_base import haversine_km
                    t_stations = [s for s in t_stations if s.get("_lat") is not None and s.get("_lon") is not None and haversine_km(ref_lat, ref_lon, s["_lat"], s["_lon"]) <= radius]
                explorer.scrape_all_stations(t_stations, subset_filter="new", pacing_delay=args.delay, timeout_sec=args.timeout, max_retries=args.retries)
            sync_results.append(("Tesla", True))
        except Exception as e:
            print(f"  ⚠️ Tesla sync encountered error: {e}")
            sync_results.append(("Tesla", False))

        print("\n🦊 [2/8] Syncing Chargefox & NRMA Network across Australia via GraphQL...")
        cf_ok = False
        for p in manager.providers:
            if isinstance(p, ChargefoxProvider):
                try:
                    synced_cf = p.sync_all_australia()
                    print(f"  ✔ Cached {len(synced_cf)} Chargefox/NRMA stations to {p.registry_path}")
                    cf_ok = True
                except Exception as e:
                    print(f"  ⚠️ Chargefox sync encountered error: {e}")
        sync_results.append(("Chargefox/NRMA", cf_ok))

        print("\n🔌 [3/8] Syncing EVX Kerbside Polecharger Network across Australia...")
        evx_ok = False
        for p in manager.providers:
            if isinstance(p, EVXProvider):
                try:
                    synced_evx = p.sync()
                    print(f"  ✔ Cached {len(synced_evx)} EVX stations to {p.registry_path}")
                    evx_ok = True
                except Exception as e:
                    print(f"  ⚠️ EVX sync encountered error: {e}")
        sync_results.append(("EVX", evx_ok))

        print("\n🕊️ [4/8] Syncing Evie Networks Fast Charging Stations across Australia...")
        evie_ok = False
        for p in manager.providers:
            if isinstance(p, EvieProvider):
                try:
                    synced_evie = p.sync()
                    print(f"  ✔ Cached {len(synced_evie)} Evie stations to {p.registry_path}")
                    evie_ok = True
                except Exception as e:
                    print(f"  ⚠️ Evie sync encountered error: {e}")
        sync_results.append(("Evie", evie_ok))

        print("\n⚡ [5/8] Syncing bp pulse DC Fast & Ultra-Fast Charging Stations across Australia...")
        bp_ok = False
        for p in manager.providers:
            if isinstance(p, BPPulseProvider):
                try:
                    synced_bp = p.sync()
                    print(f"  ✔ Cached {len(synced_bp)} bp pulse stations to {p.registry_path}")
                    bp_ok = True
                except Exception as e:
                    print(f"  ⚠️ bp pulse sync encountered error: {e}")
        sync_results.append(("bp pulse", bp_ok))

        print("\n🔌 [6/8] Syncing PlugShare 3rd-party Network...")
        ps_ok = False
        for p in manager.providers:
            if isinstance(p, PlugShareProvider):
                try:
                    if args.near:
                        r_lat, r_lon, _ = resolve_reference_coords(args.near)
                        if r_lat is not None and r_lon is not None:
                            synced_ps = p.sync_nearby(r_lat, r_lon, radius_km=args.radius or 50.0, limit=args.limit or 250, auto_save=True)
                        else:
                            synced_ps = p.sync_australia(auto_save=True)
                    elif args.state:
                        synced_ps = p.sync_state(args.state.upper(), auto_save=True)
                    else:
                        synced_ps = p.sync_australia(auto_save=True)
                    print(f"  ✔ Discovered and saved {len(synced_ps)} PlugShare stations to {p.registry_path}")
                    ps_ok = True
                except Exception as e:
                    print(f"  ⚠️ PlugShare sync encountered error: {e}")
        sync_results.append(("PlugShare", ps_ok))

        print("\n⛽ [7/8] Syncing Ampol AmpCharge & Ampol Foodary EV Fast Charging Sites across Australia...")
        amp_ok = False
        for p in manager.providers:
            if isinstance(p, AmpChargeProvider):
                try:
                    synced_amp = p.sync_all_australia()
                    print(f"  ✔ Cached {len(synced_amp)} AmpCharge/Ampol Foodary stations to {p.registry_path}")
                    amp_ok = True
                except Exception as e:
                    print(f"  ⚠️ AmpCharge sync encountered error: {e}")
        sync_results.append(("AmpCharge", amp_ok))

        print("\n🌐 [8/8] Compiling and Exporting Consolidated Master Registry...")
        try:
            out_path, total_count = manager.export_consolidated_registry()
            print(f"  ✔ Successfully exported {total_count} consolidated multi-view stations to:")
            print(f"    {out_path}")
            sync_results.append(("Master export", True))
        except Exception as e:
            print(f"  ⚠️ Master export encountered error: {e}")
            sync_results.append(("Master export", False))

        failed = [label for label, ok in sync_results if not ok]
        if failed:
            print(f"\n💥 Full multi-network sync FINISHED WITH {len(failed)} FAILURE(S): {', '.join(failed)}")
            print("   (see the ⚠️ lines above for each one's error - nothing else below this line was skipped, only these steps.)")
            sys.exit(1)
        print("\n✨ Full multi-network sync completed - all 8 steps succeeded.")
        return

    if args.sync_tesla:
        from charger_find_tesla import TeslaChargerExplorer
        explorer = TeslaChargerExplorer()
        charger_types = []
        if args.sc:
            charger_types.append("superchargers")
        if args.dc or args.ac:
            charger_types.append("chargers")
        if not charger_types:
            charger_types = ["superchargers", "chargers"]
        
        for c_type in charger_types:
            type_label = "Superchargers" if c_type == "superchargers" else "Destination Chargers"
            print(f"🌐 Synchronizing Tesla {type_label}...")
            stations = explorer.fetch_station_list(country=args.country or "Australia", charger_type=c_type)
            if args.near:
                ref_lat, ref_lon, ref_label = resolve_reference_coords(args.near)
                if ref_lat is not None and ref_lon is not None:
                    radius = args.radius or 50.0
                    from providers.chargers_base import haversine_km
                    scoped = []
                    for s in stations:
                        s_lat = s.get("_lat")
                        s_lon = s.get("_lon")
                        if s_lat is not None and s_lon is not None:
                            d = haversine_km(ref_lat, ref_lon, s_lat, s_lon)
                            if d <= radius:
                                s["_distance_km"] = d
                                scoped.append(s)
                    stations = scoped
                    print(f"  📍 Filtered to {len(stations)} stations within {radius:.0f}km of {ref_label}")
            
            subset_filter = "unpriced" if args.unpriced else ("new" if args.new else ("renamed" if args.renamed else ("stale" if args.stale else None)))
            if subset_filter:
                print(f"  🎯 Applying subset filter: '{subset_filter}'")
            explorer.scrape_all_stations(
                stations,
                pacing_delay=args.delay,
                timeout_sec=args.timeout,
                max_retries=args.retries,
                subset_filter=subset_filter
            )
        print("✔ Tesla synchronization complete.")
        return

    if args.sync_chargefox:
        for p in manager.providers:
            if isinstance(p, ChargefoxProvider):
                print("🌐 Fetching all Chargefox & NRMA stations across Australia via GraphQL...")
                synced = p.sync_all_australia()
                print(f"✔ Successfully saved {len(synced)} Chargefox stations to {p.registry_path}")
                return

    if args.sync_ampcharge:
        for p in manager.providers:
            if isinstance(p, AmpChargeProvider):
                print("⛽ Fetching all Ampol AmpCharge & Ampol Foodary EV fast-charging sites across Australia...")
                synced = p.sync_all_australia()
                print(f"✔ Successfully saved {len(synced)} AmpCharge/Ampol Foodary stations to {p.registry_path}")
                return

    if args.sync_evx:
        for p in manager.providers:
            if isinstance(p, EVXProvider):
                print("🌐 Fetching all EVX Kerbside Polechargers across Australia...")
                synced = p.sync()
                print(f"✔ Successfully saved {len(synced)} EVX stations to {p.registry_path}")
                return

    if args.sync_evie:
        for p in manager.providers:
            if isinstance(p, EvieProvider):
                print("🌐 Fetching all Evie Networks fast charging stations across Australia...")
                synced = p.sync()
                print(f"✔ Successfully saved {len(synced)} Evie stations to {p.registry_path}")
                return

    if args.sync_bppulse:
        for p in manager.providers:
            if isinstance(p, BPPulseProvider):
                print("⚡ Fetching all bp pulse fast charging stations across Australia...")
                synced = p.sync()
                print(f"✔ Successfully saved {len(synced)} bp pulse stations to {p.registry_path}")
                return

    if args.sync_plugshare:
        for p in manager.providers:
            if isinstance(p, PlugShareProvider):
                auto_save = args.save or args.add_all
                if args.near:
                    ref_lat, ref_lon, ref_label = resolve_reference_coords(args.near)
                    if ref_lat is not None and ref_lon is not None:
                        print(f"🌐 Fetching PlugShare Chargers near {ref_label} ({args.radius:.0f} km radius)...")
                        synced = p.sync_nearby(ref_lat, ref_lon, radius_km=args.radius, limit=args.limit or 250, auto_save=auto_save)
                        print(f"✔ Discovered {len(synced)} PlugShare stations.")
                        if auto_save:
                            print(f"✔ Saved to {p.registry_path}")
                        stations = synced
                    else:
                        print(f"❌ Could not resolve reference location '{args.near}'.", file=sys.stderr)
                        return
                elif args.state:
                    print(f"🌐 Fetching PlugShare Chargers for {args.state.upper()}...")
                    synced = p.sync_state(args.state.upper(), auto_save=auto_save)
                    print(f"✔ Discovered {len(synced)} PlugShare stations in {args.state.upper()}.")
                    if auto_save:
                        print(f"✔ Saved to {p.registry_path}")
                    stations = synced
                else:
                    print("🌐 Fetching PlugShare Chargers across Australia...")
                    synced = p.sync_australia(auto_save=auto_save)
                    print(f"✔ Discovered {len(synced)} PlugShare stations across Australia.")
                    if auto_save:
                        print(f"✔ Saved to {p.registry_path}")
                    stations = synced

                if not auto_save and stations:
                    render_stations_table(stations, "PLUGSHARE DISCOVERY RESULTS", eval_time=args.time)
                return

    if args.plugshare:
        for p in manager.providers:
            if isinstance(p, PlugShareProvider):
                ids = [i.strip() for i in args.plugshare.split(",") if i.strip()]
                for item_id in ids:
                    print(f"🔎 Fetching PlugShare location #{item_id}...")
                    rec = p.sync_location(item_id, auto_save=args.save)
                    if rec:
                        display_station_inspection_card(rec, eval_time=args.time)
                        if args.save:
                            print(f"✔ Saved station to {p.registry_path}")
                    else:
                        print(f"❌ Station #{item_id} not found or returned no usable data.")
                return

    if args.refresh_prices:
        for p in manager.providers:
            if isinstance(p, PlugShareProvider):
                print(f"💲 Refreshing live pricing for unlisted PlugShare stations...")
                refreshed, total, failed = p.refresh_prices(limit=args.limit)
                print(f"✔ Refreshed pricing for {refreshed}/{total} station(s). (Failed: {failed})")
                return

    if args.from_history:
        for p in manager.providers:
            if isinstance(p, PlugShareProvider):
                print("🔍 Auto-discovering unregistered charging locations from charges history...")
                discovered = p.discover_from_history()
                if not discovered:
                    print("✔ All charging locations in history are already registered.")
                    return
                print(f"✔ Discovered {len(discovered)} unregistered station(s) near past charging locations.")
                if args.add_all or args.save:
                    for st in discovered:
                        raw = st.views.get("plugshare", {})
                        if raw:
                            p.add_or_update(st.name, raw)
                    print(f"✔ Successfully saved {len(discovered)} station(s) to {p.registry_path}")
                else:
                    render_stations_table(discovered, "DISCOVERED CHARGERS FROM HISTORY", eval_time=args.time)
                return

    if args.export_consolidated:
        out_path, total_count = manager.export_consolidated_registry()
        print(f"✔ Successfully exported {total_count} consolidated multi-view stations to:")
        print(f"  {out_path}")
        return

    if args.audit_plugshare:
        from charger_plugshare_auditor import run_audit
        net_filter = args.network if args.network else None
        run_audit(network_filter=net_filter, state_filter=args.state)
        return

    # Single-station inspect / live re-scrape - reuses charger_find_tesla.py's
    # own inspect_or_scrape_station() (extracted from its main() on
    # 2026-09-14, REQ-061, AGENTS.local.md) so a one-off Tesla re-scrape
    # (e.g. "Macquarie, NSW" had stale idle/congestion fee data but wasn't
    # RENAMED or STALE-by-age, so no --sync-tesla subset filter could target
    # it) never requires dropping into the old script directly.
    if args.inspect:
        from charger_find_tesla import TeslaChargerExplorer, inspect_or_scrape_station
        tesla_explorer = TeslaChargerExplorer()
        inspect_or_scrape_station(
            tesla_explorer, inspect=args.inspect, url=None,
            dc=args.dc, sc=args.sc, live=args.live,
            json_out=args.json, save=args.save, country=args.country or "Australia"
        )
        return

    # Live catalog browsing (grid + status legend + all/new/renamed/stale/near
    # menu) - only meaningful for networks with one canonical upstream list
    # to diff a local registry against (Tesla's own findus list pages,
    # Chargefox's GraphQL catalog). REQ-060, AGENTS.local.md, 2026-09-14.
    if args.live and not args.query and not args.near:
        net_lower = (args.network or "").strip().lower()
        if args.sc or net_lower == "tesla":
            from charger_find_tesla import TeslaChargerExplorer, browse_stations_by_type
            types_to_fetch = []
            if args.sc:
                types_to_fetch.append("superchargers")
            if args.dc or args.ac:
                types_to_fetch.append("chargers")
            if not types_to_fetch:
                types_to_fetch = ["superchargers", "chargers"]
            tesla_explorer = TeslaChargerExplorer()
            browse_stations_by_type(tesla_explorer, types_to_fetch, args.country or "Australia")
            return
        if net_lower in ("chargefox", "nrma"):
            browse_chargefox_catalog_grid(manager, args.country or "Australia", auto_save=args.save)
            return
        print(f"{C_YELLOW}⚠ --live browsing isn't available for this network selection - it needs one canonical upstream catalog to diff against (Tesla via --network tesla or --sc, or Chargefox via --network chargefox). PlugShare-aggregated networks (Evie, bp pulse, etc.) have no single such list.{C_RESET}")
        print(f"{C_DIM}Falling back to the local registry view below.{C_RESET}")

    # Determine filters
    speed_tier = None
    if args.sc:
        speed_tier = "supercharger"
    elif args.dc:
        speed_tier = "dc_fast"
    elif args.ac:
        speed_tier = "ac_destination"

    filters = {
        "speed_tier": speed_tier,
        "min_kw": args.min_kw,
        "network": args.network,
        "state": args.state
    }

    # Execute search
    stations: List[UnifiedStationRecord] = []
    title = "PUBLIC EV CHARGING STATIONS"
    ref_lat, ref_lon, ref_label = None, None, None
    active_radius = None
    # Tracks the sort key actually handed to the manager for this branch, so the
    # displayed "Sort Order" label always matches the real ordering below (BUG-066) -
    # it must never be derived from the raw args.sort default alone.
    effective_sort = args.sort

    if args.all or args.state or (not args.near and not args.query):
        # Full country / state list mode - there is no reference point here, so
        # "distance" (the CLI default) has no meaning; fall back to state ordering.
        state_suffix = f" IN {args.state.upper()}" if args.state else " IN AUSTRALIA"
        type_lbl = "SUPERCHARGERS" if args.sc else ("DC FAST CHARGERS" if args.dc else ("AC CHARGERS" if args.ac else "CHARGING STATIONS"))
        title = f"{type_lbl}{state_suffix}"
        effective_sort = args.sort if args.sort != "distance" else "state"
        stations = manager.get_all_stations(filters=filters, sort_by=effective_sort, eval_time=args.time)
    elif args.query:
        # Text query mode - also no fixed reference point, so "distance" falls back
        # to alphabetical-by-name (search_by_text's own default) rather than lying
        # about a distance sort that never happened.
        title = "MATCHING CHARGING STATIONS"
        ref_label = f'Query: "{args.query}"'
        effective_sort = args.sort if args.sort != "distance" else "name"
        stations = manager.search_by_text(args.query, filters=filters, sort_by=effective_sort, eval_time=args.time)
    else:
        ref_loc = args.near or "Home"
        ref_lat, ref_lon, ref_label = resolve_reference_coords(ref_loc)
        active_radius = args.radius
        if ref_lat is not None and ref_lon is not None:
            title = f"CHARGERS NEAR {ref_label.upper()}"
            effective_sort = args.sort
            stations = manager.search_nearby(
                ref_lat, ref_lon,
                radius_km=args.radius,
                filters=filters,
                sort_by=effective_sort,
                eval_time=args.time
            )
        else:
            print(f"❌ Error: Could not resolve reference location '{ref_loc}'.", file=sys.stderr)
            sys.exit(1)

    # Apply Status Filters
    if args.status:
        st_req = args.status.upper()
        if st_req in ("NEW", "NOT_IN_JSON"):
            stations = [s for s in stations if s.get_status() == "NOT_IN_JSON"]
        elif st_req in ("UP_TO_DATE", "UPTODATE"):
            stations = [s for s in stations if s.get_status() == "UP_TO_DATE"]
        elif st_req in ("RENAMED", "DISCREPANCY"):
            stations = [s for s in stations if s.get_status() == "RENAMED"]
        elif st_req == "STALE":
            stations = [s for s in stations if s.get_status() == "STALE"]
    elif args.stale:
        stations = [s for s in stations if s.get_status() == "STALE"]
    elif args.renamed:
        stations = [s for s in stations if s.get_status() == "RENAMED"]
    elif args.new:
        stations = [s for s in stations if s.get_status() == "NOT_IN_JSON"]

    if args.limit and len(stations) > args.limit:
        stations = stations[:args.limit]

    # Batch Update CLI Actions
    if args.update_stale or args.update_renamed:
        target_status = "STALE" if args.update_stale else "RENAMED"
        print(f"🔄 Starting batch update for {target_status} stations (historical changes will be archived)...")
        stats = manager.batch_reverify_stations(stations, status_filter=target_status)
        print(f"\n✔ Batch update complete: {stats['SUCCESS']} updated, {stats['ERROR']} failed out of {stats['TOTAL']} target stations.")
        return

    # Multi-View comparison mode
    if args.views:
        from charger_plugshare_auditor import inspect_station_views
        if not stations:
            print(f"❌ No stations found to display views.")
            return
        for st in stations:
            print(inspect_station_views(st))
        return

    # JSON Output Mode
    if args.json:
        data = [s.to_dict() for s in stations]
        print(json.dumps(data, indent=2))
        return

    # Determine sort mode description - derived from effective_sort (the key
    # actually handed to the manager above), never the raw args.sort default, so
    # this label can never claim a distance sort that didn't happen (BUG-066).
    sort_mode_desc = None
    if effective_sort == "distance":
        has_real_dist = any(s.distance_km is not None for s in stations)
        if has_real_dist:
            sort_mode_desc = "Distance (Closest First)"
        else:
            # No reference point/coordinates were available, so sort_records()
            # fell back to its type-then-state-then-name ordering internally.
            sort_mode_desc = "Charger Type, then State, then Station Name"
    elif effective_sort == "price":
        sort_mode_desc = "Price (Lowest First)"
    elif effective_sort == "power":
        sort_mode_desc = "Max Power (kW, Highest First)"
    elif effective_sort == "stalls":
        sort_mode_desc = "Stall Count (Highest First)"
    elif effective_sort == "name":
        sort_mode_desc = "Station Name (Alphabetical)"
    elif effective_sort == "state":
        sort_mode_desc = "State, then Station Name (Alphabetical)"

    def render_cb():
        render_stations_table(
            stations, title,
            eval_time=args.time,
            ref_label=ref_label,
            ref_lat=ref_lat,
            ref_lon=ref_lon,
            active_radius=active_radius,
            sort_mode=sort_mode_desc,
            network_filter=args.network
        )

    render_cb()

    # Interactive Station Selector Loop
    if sys.stdin.isatty():
        while True:
            print(f"\n{C_BOLD}Options:{C_RESET} Enter station number [{C_GREEN}1-{len(stations)}{C_RESET}] to inspect, {C_MAGENTA}'renamed'{C_RESET} to reconcile renames, {C_BLUE}'stale'{C_RESET} to update stale, {C_CYAN}'u <#>' {C_RESET}to update station, or {C_YELLOW}Enter{C_RESET}/'q' to quit.")
            try:
                choice = input(f"Select Station [1-{len(stations)}], 'stale', 'renamed', 'u <#>', [q]uit: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not choice or choice in ("q", "quit", "exit"):
                break

            if choice in ("r", "list", "table"):
                render_cb()
                continue

            if choice in ("stale", "update-stale"):
                print(f"🔄 Re-verifying all stale stations in current query...")
                stats = manager.batch_reverify_stations(stations, status_filter="STALE")
                print(f"✔ Done: {stats['SUCCESS']} updated, {stats['ERROR']} failed.")
                input("\nPress Enter to refresh list...")
                render_cb()
                continue

            if choice in ("renamed", "update-renamed"):
                print(f"🔄 Re-verifying all renamed stations in current query...")
                stats = manager.batch_reverify_stations(stations, status_filter="RENAMED")
                print(f"✔ Done: {stats['SUCCESS']} updated, {stats['ERROR']} failed.")
                input("\nPress Enter to refresh list...")
                render_cb()
                continue

            # Quick update command e.g. "u 3" or "update 3"
            if choice.startswith("u ") or choice.startswith("update ") or choice.startswith("s "):
                parts = choice.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    t_idx = int(parts[1])
                    if 1 <= t_idx <= len(stations):
                        target_st = stations[t_idx - 1]
                        print(f"🔄 Re-verifying station '{target_st.name}' live...")
                        ok = manager.reverify_station(target_st)
                        if ok:
                            print(f"✔ Successfully updated '{target_st.name}' (historical version archived if changed).")
                        else:
                            print(f"❌ Failed to update '{target_st.name}'.")
                        input("\nPress Enter to refresh list...")
                        render_cb()
                        continue

            target_num = None
            if choice.isdigit():
                target_num = int(choice)

            if target_num is None or target_num < 1 or target_num > len(stations):
                print(f"{C_RED}❌ Invalid selection. Please enter a number between 1 and {len(stations)}.{C_RESET}\n")
                continue

            sel = stations[target_num - 1]
            display_station_inspection_card(sel, eval_time=args.time)

            source_urls = sel.get_source_urls()
            print(f"{C_BOLD}Actions for {sel.name}:{C_RESET}")
            
            url_actions = {}
            idx = 1
            for u in source_urls:
                print(f"  [{C_GREEN}{idx}{C_RESET}] Open {u['label']} in default browser ({format_display_url(u['url'])})")
                url_actions[str(idx)] = u["url"]
                idx += 1
                
            print(f"  [{C_GREEN}u{C_RESET}] Update / Re-verify this station live (archives historical changes)")
            print(f"  [{C_GREEN}m{C_RESET}] Open in Google Maps (navigation / coordinates)")
            print(f"  [{C_GREEN}b{C_RESET}] Back to station list")
            print(f"  [{C_GREEN}q{C_RESET}] Quit")
            print()

            action_prompt_range = f"1-{len(source_urls)}" if len(source_urls) > 1 else "1"
            if len(source_urls) > 1:
                prompt_options = f"{action_prompt_range}, a, u, m, b, q"
            elif len(source_urls) == 1:
                prompt_options = f"1, u, m, b, q"
            else:
                prompt_options = f"u, m, b, q"

            try:
                act = input(f"Action [{prompt_options}, default: b]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                break

            if act in ("q", "quit"):
                break
            elif act in url_actions:
                target_url = url_actions[act]
                import subprocess
                subprocess.run(["open", target_url] if sys.platform == "darwin" else ["xdg-open", target_url], check=False)
            elif act == "a" and source_urls:
                import subprocess
                for u in source_urls:
                    subprocess.run(["open", u["url"]] if sys.platform == "darwin" else ["xdg-open", u["url"]], check=False)
            elif act in ("u", "update", "refresh"):
                print(f"🔄 Re-verifying station '{sel.name}' live...")
                ok = manager.reverify_station(sel)
                if ok:
                    print(f"✔ Successfully updated and saved {sel.name} (historical version archived if changed).")
                else:
                    print(f"❌ Failed to update station '{sel.name}'.")
                input("\nPress Enter to continue...")
            elif (act in ("m", "g", "map", "maps") or (act == "2" and len(source_urls) <= 1)) and sel.lat is not None and sel.lon is not None:
                gmap_url = f"https://www.google.com/maps/search/?api=1&query={sel.lat},{sel.lon}"
                import subprocess
                subprocess.run(["open", gmap_url] if sys.platform == "darwin" else ["xdg-open", gmap_url], check=False)

            render_cb()


if __name__ == "__main__":
    main()
