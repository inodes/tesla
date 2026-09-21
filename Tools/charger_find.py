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
import time
import json
import shutil
import argparse
import urllib.request
import urllib.parse
import webbrowser
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime

_tools_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_tools_dir)
_lib_dir = os.path.join(_tools_dir, "lib")
_archive_dir = os.path.join(_tools_dir, "archive")
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)
if _archive_dir not in sys.path:
    sys.path.insert(0, _archive_dir)

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

from providers_chargers import (
    ChargerDiscoveryManager,
    UnifiedStationRecord,
    TeslaProvider,
    ChargefoxProvider,
    EVXProvider,
    EvieProvider,
    BPPulseProvider,
    PlugShareProvider,
    AmpChargeProvider,
    ExplorenProvider,
    JoltProvider,
    NrmaProvider
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

# (AGENTS.local.md, 2026-09-16): when --near is combined with a
# non-distance --sort (e.g. --sort price) and no explicit --radius, the
# closest N stations by distance are gathered FIRST, then re-sorted by the
# requested field - rather than sorting/truncating a nationwide list, which
# let a Supercharger 1400km away outrank anything actually near the given
# point. A candidate COUNT (not a fixed km radius) is used deliberately so a
# search from a remote/sparse area never returns zero results just because
# nothing falls inside an arbitrary radius.
NEAR_SORT_CANDIDATE_COUNT = 10

_GEOCODE_CACHE = {}


def geocode_address(address_str: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """Geocodes an arbitrary address string or suburb to (lat, lon, label) with fallbacks."""
    if not address_str:
        return None, None, None
    clean = address_str.strip()
    if clean in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[clean]

 # Primary: Delegate to places_manager's geocode_candidates
    try:
        from places_manager import geocode_candidates
        cands = geocode_candidates(clean, limit=5)
        if cands:
            top = cands[0]
            res = (top["lat"], top["lon"], top["label"])
            _GEOCODE_CACHE[clean] = res
            return res
    except Exception:
        pass

 # 1. Nominatim (OpenStreetMap) Fallback
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

 # 2. Open-Meteo Geocoding API Fallback
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
    Checks places.json (Home, Work, etc.), direct 'lat,lon' coordinates, or online geocoding with disambiguation.
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

    home_coords = None
    for p_file in places_candidates:
        if os.path.isfile(p_file):
            try:
                with open(p_file, "r", encoding="utf-8") as f:
                    p_data = json.load(f)
                    places_dict = p_data.get("places") if (isinstance(p_data, dict) and "places" in p_data and isinstance(p_data["places"], dict)) else p_data
                    if isinstance(places_dict, dict):
 # Extract Home coords if present for distance ranking
                        if "home" in [k.lower() for k in places_dict.keys()]:
                            for pk, pv in places_dict.items():
                                if pk.lower() == "home" and isinstance(pv, dict):
                                    h_lat = pv.get("lat") or pv.get("latitude")
                                    h_lon = pv.get("lon") or pv.get("longitude") or pv.get("lng")
                                    if h_lat is not None and h_lon is not None:
                                        home_coords = (float(h_lat), float(h_lon))
                                    break
                        for p_name, p_info in places_dict.items():
                            if not isinstance(p_info, dict):
                                continue
                            if p_name.lower() == q_clean.lower():
                                lat = p_info.get("lat") or p_info.get("latitude")
                                lon = p_info.get("lon") or p_info.get("longitude") or p_info.get("lng")
                                if lat is not None and lon is not None:
                                    return float(lat), float(lon), p_name
            except Exception:
                pass

 # 3. Online geocoding with smart disambiguation for suburb, city, or street address
    try:
        from places_manager import resolve_location_disambiguated
        g_lat, g_lon, g_label = resolve_location_disambiguated(q_clean, anchor_coords=home_coords, interactive=True)
        if g_lat is not None and g_lon is not None:
            return g_lat, g_lon, g_label or q_clean
    except Exception:
        g_lat, g_lon, g_label = geocode_address(q_clean)
        if g_lat is not None and g_lon is not None:
            return g_lat, g_lon, g_label or q_clean

 # 4. Known station match in local registries (fallback for offline or exact station name queries)
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
                            if q_clean.lower() == s_k.lower() or q_clean.lower() == name_to_check.lower():
                                loc = s_v.get("location", {}) if isinstance(s_v.get("location"), dict) else {}
                                lat = loc.get("lat") or loc.get("latitude") or s_v.get("lat") or s_v.get("latitude")
                                lon = loc.get("lon") or loc.get("longitude") or s_v.get("lon") or s_v.get("longitude")
                                if lat and lon:
                                    return float(lat), float(lon), name_to_check or s_k
            except Exception:
                pass

 # 5. Default Sydney fallback if Home requested
    if q_clean.lower() == "home":
        return -33.8688, 151.2093, "Sydney"

    return None, None, q_clean


def _contextual_type_icon(st: UnifiedStationRecord, network_filter: Optional[str]) -> str:
    """
    Normally just st.type_icon(). (2026-09-15, AGENTS.local.md):
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
        return "🟠 EVX"
    if nf == "evie":
        return "🕊️ Evie"
    if nf == "exploren":
        return "🟣 Exp"
    if "bp" in nf:
        return "🟢 BP"
    if "ampcharge" in nf or "ampol" in nf:
        return "⛽ Amp"
    if nf == "jolt":
        return "🔵 Jolt"
    if nf == "tesla":
        return "🔴 SC" if st.speed_tier == "supercharger" else "🔌 Dest"
    return st.type_icon()


def _print_reverify_result(name: str, ok: bool, diffs) -> None:
    """
    (2026-09-16, AGENTS.local.md): the "Update / Re-verify" action
    used to print the same "Successfully updated and saved (historical
    version archived if changed)" line every time, regardless of whether the
    live re-fetch actually found anything different - user report: re-ran it
    on a station, got that message, but "nothing shows as updated" and asked
    for either an honest "no change" message or a visible diff. `diffs` is
    the (ok, diffs) tuple's second element from
    ChargerDiscoveryManager.reverify_station(): None means the re-fetch
    itself failed or a diff couldn't be computed, {} means it succeeded with
    no display-relevant change, and a non-empty dict lists exactly what did.
    """
    if not ok:
        print(f"{C_RED}❌ Failed to update station '{name}'.{C_RESET}")
        return
    if diffs is None:
        print(f"{C_GREEN}✔ Re-verified '{name}' live and saved.{C_RESET} {C_DIM}(unable to compute a field-level diff for this source){C_RESET}")
        return
    if not diffs:
        print(f"{C_GREEN}✔ No changes detected for '{name}' - already up to date.{C_RESET}")
        return
    print(f"{C_GREEN}✔ Re-verified '{name}' - {len(diffs)} field(s) changed:{C_RESET}")
    for label, (old_v, new_v) in diffs.items():
        old_disp = old_v if old_v not in (None, "") else "-"
        new_disp = new_v if new_v not in (None, "") else "-"
        print(f"   {C_DIM}{label}:{C_RESET}  {old_disp}  {C_DIM}->{C_RESET}  {new_disp}")


def render_stations_table(
    stations: List[UnifiedStationRecord],
    title: str,
    eval_time: Optional[str] = None,
    ref_label: Optional[str] = None,
    ref_lat: Optional[float] = None,
    ref_lon: Optional[float] = None,
    active_radius: Optional[float] = None,
    sort_mode: Optional[str] = None,
    network_filter: Optional[str] = None,
    route_info: Optional[Dict[str, Any]] = None
) -> None:
    """Renders rich executive 10/11/12-column Unicode table of stations matching Tesla Explorer styling."""
    if not stations:
        total_inner = 88
        print(f"\n┌{'─'*total_inner}┐")
        print(format_title_line(f" {title}", total_inner, "left"))
        print(format_box_line("├", "─", "┤", [total_inner]))
        print(f"│{pad_display(' ℹ️  No charging stations found matching the criteria.', total_inner, 'left')}│")
        print(format_box_line("└", "─", "┘", [total_inner]))
        return

    has_route = route_info is not None and any(getattr(s, "route_progress_km", None) is not None for s in stations)
    has_dist = (not has_route) and any(s.distance_km is not None for s in stations)
    
    max_name_len = max((display_len(s.name) for s in stations), default=28)
    w_name = max(28, min(40, max_name_len + 2))
    
    max_suburb_len = max((display_len(s.suburb or s.state or "") for s in stations), default=18)
    w_suburb = max(18, min(24, max_suburb_len + 2))

    widths = [8, 9, 7, w_name, 6, 6, 13, 12, 22]
    headers = ["   #    ", "  Type   ", " State ", "          Station Name          ", " Tier ", " Bays ", "   Access    ", " Rate (Now) ", "   Period / Window    "]
    
    if has_route:
        widths.extend([11, 9, 10, 8, w_suburb])
        headers.extend([" Progress  ", " Detour  ", " Arr SoC  ", " Score  ", " Location / Suburb "])
    elif has_dist:
        widths.extend([9, w_suburb])
        headers.extend(["  Dist   ", " Location / Suburb "])
    else:
        widths.append(w_suburb)
        headers.append(" Location / Suburb ")

    total_inner_w = sum(widths) + len(widths) - 1

 # Top Banner Box
    print(f"\n┌{'─' * total_inner_w}┐")
    print(f"│{pad_display(f' ⚡ {title.upper()} ({len(stations)} found)', total_inner_w, 'left')}│")
    
    if route_info:
        orig = route_info.get("origin_label", "Origin")
        dest = route_info.get("destination_label", "Destination")
        vias = route_info.get("via_labels", [])
        via_str = f" via {', '.join(vias)}" if vias else ""
        tot_dist = route_info.get("distance_km", 0.0)
        tot_dur = route_info.get("duration_mins", 0.0)
        dur_str = f"{int(tot_dur // 60)}h {int(tot_dur % 60):02d}m" if tot_dur >= 60 else f"{int(tot_dur)}m"
        r_sum = route_info.get("summary", "")
        summary_str = f" [{r_sum}]" if r_sum and r_sum != "Driving Route" else ""
        
        print(f"│{pad_display(f' 🚗 {C_CYAN}Driving Corridor:{C_RESET} {orig} ➔ {dest}{via_str} ({tot_dist:.1f} km, ~{dur_str}){summary_str}', total_inner_w, 'left')}│")
        
        dep_soc = route_info.get("departure_soc")
        dep_src = route_info.get("departure_soc_src", "Config")
        tgt_soc = route_info.get("target_arrival_soc", 10)
        max_soc = route_info.get("max_charge_soc", 85)
        if dep_soc is not None:
            live_detail = route_info.get("live_detail") or {}
            range_info = f" (~{live_detail['battery_range_km']:.0f} km range)" if live_detail.get("battery_range_km") else ""
            print(f"│{pad_display(f' 🔋 {C_GREEN}Departure Battery:{C_RESET} {dep_soc}% [{dep_src}{range_info}]  |  {C_YELLOW}Target Min:{C_RESET} {tgt_soc:.0f}%  |  {C_CYAN}Max Charge:{C_RESET} {max_soc:.0f}%', total_inner_w, 'left')}│")

        c_rad = route_info.get("corridor_radius_km", 5.0)
        sort_str = f" [Sorted by: {sort_mode}]" if sort_mode else ""
        print(f"│{pad_display(f' 🛣️  {C_CYAN}Corridor Radius:{C_RESET} {c_rad:.1f} km max lateral detour{sort_str}', total_inner_w, 'left')}│")
    elif ref_label:
        radius_note = f" [within {active_radius:.0f} km]" if (active_radius and active_radius > 0) else ""
        sort_str = f" [Sorted by: {sort_mode}]" if sort_mode else ""
        coords_str = f" ({ref_lat:.4f}, {ref_lon:.4f})" if (ref_lat is not None and ref_lon is not None) else ""
        print(f"│{pad_display(f' 📍 {C_CYAN}Proximity Origin:{C_RESET} {ref_label}{coords_str}{radius_note}{sort_str}', total_inner_w, 'left')}│")
    elif sort_mode:
        print(f"│{pad_display(f' 📊 {C_BOLD}Sort Order:{C_RESET} {sort_mode}', total_inner_w, 'left')}│")

    eval_label = f"Target Time: {eval_time}" if eval_time else "Current Local Time"
    print(f"│{pad_display(f' ⏰ {C_BOLD}Pricing Evaluation:{C_RESET} {eval_label}', total_inner_w, 'left')}│")

    C_ALERT_RED = "\033[91;1m"

    c_up = sum(1 for s in stations if s.get_status() == "UP_TO_DATE")
    c_renamed = sum(1 for s in stations if s.get_status() == "RENAMED")
    c_stale = sum(1 for s in stations if s.get_status() == "STALE")
    c_new = sum(1 for s in stations if s.get_status() == "NOT_IN_JSON")
    c_degraded = sum(1 for s in stations if ((s.metadata or {}).get("operational_status") or "").strip())
    legend_line = f" 📊 {C_BOLD}Status Legend:{C_RESET} [{C_GREEN} 1 {C_RESET}] Up to Date: {c_up}  |  [{C_MAGENTA} 2 {C_RESET}] Renamed: {c_renamed}  |  [{C_BLUE} 3 {C_RESET}] Stale (>3mo): {c_stale}  |  [{C_ORANGE} 4 {C_RESET}] Not in JSON: {c_new}"
    if c_degraded > 0:
        legend_line += f"  |  [{C_ALERT_RED}!{C_RESET}] Degraded/Repair: {c_degraded}"
    print(f"│{pad_display(legend_line, total_inner_w, 'left')}│")
    
 # Table Header Line
    top_b = "├" + "┬".join("─" * w for w in widths) + "┤"
    print(top_b)
    
    h_cells = [pad_display(f"{C_BOLD}{h.strip()}{C_RESET}", w, "center") for h, w in zip(headers, widths)]
    print("│" + "│".join(h_cells) + "│")
    
    mid_b = "├" + "┼".join("─" * w for w in widths) + "┤"
    print(mid_b)

    aligns = ["left", "left", "center", "left", "center", "right", "left", "right", "left"]
    if has_route:
        aligns.extend(["right", "right", "center", "center", "left"])
    elif has_dist:
        aligns.extend(["right", "left"])
    else:
        aligns.append("left")

    for idx, st in enumerate(stations, 1):
        status = st.get_status()
        color = C_GREEN if status == "UP_TO_DATE" else (C_MAGENTA if status == "RENAMED" else (C_BLUE if status == "STALE" else C_ORANGE))
        op_status_row = ((st.metadata or {}).get("operational_status") or "").strip()
        if op_status_row:
            if idx < 100:
                num_str = f" [{color}{idx:2d}{C_RESET}] {C_ALERT_RED}!{C_RESET} "
            else:
                num_str = f"[{color}{idx:3d}{C_RESET}] {C_ALERT_RED}!{C_RESET} "
        else:
            if idx < 100:
                num_str = f" [{color}{idx:2d}{C_RESET}]   "
            else:
                num_str = f"[{color}{idx:3d}{C_RESET}]   "

        t_icon = _contextual_type_icon(st, network_filter)
        state_str = st.state or "-"
        name_disp = truncate_display(st.name, w_name - 2)
        tier_str = st.tier_badge()
        stalls_str = str(st.stalls_total) if st.stalls_total > 0 else "-"
        
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

        if has_route:
            prog_val = getattr(st, "route_progress_km", st.distance_km)
            prog_text = f"{prog_val:.1f} km " if prog_val is not None else "-- "
            detour_val = getattr(st, "detour_km", None)
            if detour_val is not None:
                if detour_val <= 1.0:
                    d_col = C_GREEN
                elif detour_val <= 2.5:
                    d_col = C_YELLOW
                elif detour_val <= 4.0:
                    d_col = C_ORANGE
                else:
                    d_col = C_RED
                detour_text = f"{d_col}{detour_val:.1f} km{C_RESET} "
            else:
                detour_text = "-- "
            
            # Arrival SoC Calculation
            dep_soc = route_info.get("departure_soc", 90) if route_info else 90
            c_wh_km = route_info.get("consumption_wh_km", 165.0) if route_info else 165.0
            u_kwh = route_info.get("usable_kwh", 60.0) if route_info else 60.0
            tgt_min = route_info.get("target_arrival_soc", 10.0) if route_info else 10.0

            if prog_val is not None:
                try:
                    from lib.abrp_planner import estimate_arrival_soc, format_soc_badge
                    arr_soc, _ = estimate_arrival_soc(dep_soc, prog_val, detour_val or 0.0, c_wh_km, u_kwh)
                    soc_badge, _ = format_soc_badge(arr_soc, tgt_min)
                except Exception:
                    soc_badge = "—"
            else:
                soc_badge = "—"

            sc_data = getattr(st, "stop_score_data", None)
            if not sc_data:
                try:
                    from lib.route_planner import compute_route_stop_score
                    c_rad = route_info.get("corridor_radius_km", 5.0) if route_info else 5.0
                    sc_data = compute_route_stop_score(st, detour_val, c_rad)
                    st.stop_score_data = sc_data
                    st.stop_score = sc_data["score"]
                except Exception:
                    sc_data = {"badge": "—"}
            score_disp = sc_data.get("badge", "—")

            row_cells.append(pad_display(prog_text, widths[9], "right"))
            row_cells.append(pad_display(detour_text, widths[10], "right"))
            row_cells.append(pad_display(soc_badge, widths[11], "center"))
            row_cells.append(pad_display(score_disp, widths[12], "center"))
            row_cells.append(pad_display(f" {C_DIM}{suburb_disp}{C_RESET}", widths[13], "left"))
        elif has_dist:
            dist_val = st.distance_km
            dist_text = f"{dist_val:.1f} km " if dist_val is not None else "-- "
            row_cells.append(pad_display(dist_text, widths[9], "right"))
            row_cells.append(pad_display(f" {C_DIM}{suburb_disp}{C_RESET}", widths[10], "left"))
        else:
            row_cells.append(pad_display(f" {C_DIM}{suburb_disp}{C_RESET}", widths[9], "left"))

        print(format_row(row_cells, widths, aligns))

    bot_b = "└" + "┴".join("─" * w for w in widths) + "┘"
    print(bot_b)

    if route_info and route_info.get("abrp_link"):
        abrp_url = route_info["abrp_link"]
        print(f"\n 🌐 {C_BOLD}ABRP 1-Click Road Trip:{C_RESET} {C_CYAN}{abrp_url}{C_RESET}")


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
 # etc.) instead of a hardcoded generic plug - follow-on.
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
    
    is_tesla_sc = (
        station.speed_tier == "supercharger"
        and ("tesla" in (station.source or "").lower() or (station.network or "").lower() == "tesla" or "tesla" in (station.views or {}))
    )
    is_tesla_dest = (
        ("tesla" in (station.source or "").lower() or "tesla" in (station.network or "").lower() or "tesla" in (station.views or {}))
        and not is_tesla_sc
    )

    if is_tesla_sc:
        title_content = f"TESLA SUPERCHARGER: {station.name}"
    elif is_tesla_dest:
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

 # (2026-09-16, AGENTS.local.md): PlugShare's own operational
 # status ("PARTIALLY_UNDER_REPAIR", "OUT_OF_ORDER", etc. - distinct from
 # the coming_soon/under_construction cases that are rejected on import
 # entirely) was being captured nowhere and shown nowhere - user found a
 # station "PARTIALLY UNDER REPAIR" on PlugShare's own page with no sign
 # of it in this tool at all. Surfaced first, above Source & Provenance,
 # so it can't be missed.
    op_status = (meta.get("operational_status") or "").strip()
    if op_status:
        _field("🚧", "Operational Status", f"{C_RED}{C_BOLD}⚠️  {op_status.replace('_', ' ').title()}{C_RESET}")

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
    elif station.source == "exploren":
        _field("📂", "Data Source", f"Exploren Registry (ID: {station.source_id})")
    elif station.source == "jolt":
        _field("📂", "Data Source", f"Jolt Registry (ID: {station.source_id})")
    elif station.source == "plugshare":
        _field("📂", "Data Source", f"PlugShare Registry (ID: {station.source_id})")
    elif station.source == "nrma":
        _field("📂", "Data Source", f"NRMA Registry (ID: {station.source_id})")
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

 # REQ-088 (AGENTS.local.md, 2026-09-16): when two same-tier sources (in
 # practice, NRMA vs Chargefox for the ~28 sites they both list) disagree
 # on a field, merge_station_records() keeps both values instead of
 # silently picking one - surface that here rather than only in raw
 # metadata, so a real discrepancy is visible instead of hidden behind
 # whichever source happened to win the merge.
    src_conflicts = meta.get("source_conflicts") or {}
    for field_name, values in src_conflicts.items():
        conflict_str = " vs. ".join(f"{src.title()}: {val}" for src, val in values.items())
        _field("⚠️", f"Conflict ({field_name})", conflict_str)

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
        meta = station.metadata or {}
        osm_lat = meta.get("osm_lat")
        osm_lon = meta.get("osm_lon")

        # Look up supercharge.info data strictly for Tesla Superchargers
        sc_site = None
        if is_tesla_sc:
            try:
                from lib.supercharge_info import find_supercharge_site
                loc_id = meta.get("location_id") or meta.get("short_name") or station.source_id
                sc_site = find_supercharge_site(station.name, station.lat, station.lon, location_id=loc_id)
            except Exception:
                sc_site = None

        # Auto-populate and auto-cache OSM node and physical GPS if available from supercharge.info
        if is_tesla_sc and sc_site:
            sc_osm_id = sc_site.get("osmId")
            sc_gps = sc_site.get("gps", {})
            sc_lat_val = sc_gps.get("latitude")
            sc_lon_val = sc_gps.get("longitude")
            if sc_osm_id:
                if not meta.get("osm_node_id"):
                    meta["osm_node_id"] = sc_osm_id
                if osm_lat is None and sc_lat_val is not None and sc_lon_val is not None:
                    osm_lat = sc_lat_val
                    osm_lon = sc_lon_val
                    meta["osm_lat"] = osm_lat
                    meta["osm_lon"] = osm_lon
                    try:
                        from lib.tessie_api_common import save_audited_osm_to_tesla_registry
                        save_audited_osm_to_tesla_registry(station.name, float(osm_lat), float(osm_lon), int(sc_osm_id))
                    except Exception:
                        pass

        try:
            from lib.geo_common import haversine_distance_km
        except Exception:
            haversine_distance_km = None

        st_src_lower = (station.source or "").lower()
        if is_tesla_sc or is_tesla_dest or "tesla" in st_src_lower:
            src_lbl = "Tesla GPS"
        elif "chargefox" in st_src_lower:
            src_lbl = "Chargefox GPS"
        elif "nrma" in st_src_lower:
            src_lbl = "NRMA GPS"
        elif "evie" in st_src_lower:
            src_lbl = "Evie GPS"
        elif "evx" in st_src_lower:
            src_lbl = "EVX GPS"
        elif "ampcharge" in st_src_lower or "ampol" in st_src_lower:
            src_lbl = "Ampol GPS"
        elif "bppulse" in st_src_lower:
            src_lbl = "bp pulse GPS"
        elif "jolt" in st_src_lower:
            src_lbl = "Jolt GPS"
        elif "exploren" in st_src_lower:
            src_lbl = "Exploren GPS"
        elif "plugshare" in st_src_lower:
            src_lbl = "PlugShare GPS"
        else:
            src_lbl = "Entry GPS"

        if osm_lat is not None or sc_site is not None:
            _field("🌐", src_lbl, f"({station.lat:.6f}, {station.lon:.6f})")
        else:
            _field("🌐", "GPS Coordinates", f"({station.lat:.6f}, {station.lon:.6f})")

        if sc_site and sc_site.get("gps"):
            sc_lat = sc_site["gps"].get("latitude")
            sc_lon = sc_site["gps"].get("longitude")
            if sc_lat is not None and sc_lon is not None:
                if haversine_distance_km:
                    sc_offset = haversine_distance_km(station.lat, station.lon, float(sc_lat), float(sc_lon)) * 1000.0
                    sc_err = f" {C_YELLOW}~{sc_offset:.0f}m offset !{C_RESET}" if sc_offset > 25.0 else f" {C_GREEN}~{sc_offset:.0f}m (aligned){C_RESET}"
                else:
                    sc_err = ""
                _field("🌐", "SC.info GPS", f"({float(sc_lat):.6f}, {float(sc_lon):.6f}){sc_err}")

        if osm_lat is not None and osm_lon is not None:
            if haversine_distance_km:
                offset_m = haversine_distance_km(station.lat, station.lon, float(osm_lat), float(osm_lon)) * 1000.0
                err_str = f" {C_YELLOW}~{offset_m:.0f}m offset !{C_RESET}" if offset_m > 25.0 else f" {C_GREEN}~{offset_m:.0f}m (aligned){C_RESET}"
            else:
                err_str = ""
            _field("🌐", "OSM GPS", f"({float(osm_lat):.6f}, {float(osm_lon):.6f}){err_str}")
    
    if getattr(station, "route_progress_km", None) is not None and getattr(station, "detour_km", None) is not None:
        _field("🚗", "Route Progress", f"{station.route_progress_km:.1f} km from departure")
        d_val = station.detour_km
        if d_val <= 1.0:
            d_col = C_GREEN
        elif d_val <= 2.5:
            d_col = C_YELLOW
        elif d_val <= 4.0:
            d_col = C_ORANGE
        else:
            d_col = C_RED
        _field("🛣️", "Highway Detour", f"{d_col}{d_val:.1f} km off route{C_RESET}")

        sc_data = getattr(station, "stop_score_data", None)
        if not sc_data:
            try:
                from lib.route_planner import compute_route_stop_score
                sc_data = compute_route_stop_score(station, station.detour_km)
            except Exception:
                pass
        if sc_data:
            _field("⭐", "Stop Quality Score", f"{sc_data['badge']}/10.0 (Facilities: {sc_data['facilities_score']:.1f}, Detour: {sc_data['detour_score']:.1f}, Speed & Bays: {sc_data['speed_score']:.1f})")

        # Arrival SoC Projection
        try:
            from lib.abrp_planner import load_abrp_config, estimate_arrival_soc, format_soc_badge, resolve_departure_soc
            ab_cfg = load_abrp_config()
            dep_soc, dep_src, _ = resolve_departure_soc(None)
            arr_soc, energy_kwh = estimate_arrival_soc(
                dep_soc,
                station.route_progress_km,
                station.detour_km,
                ab_cfg.get("consumption_wh_km", 165.0),
                ab_cfg.get("usable_kwh", 60.0)
            )
            soc_badge, _ = format_soc_badge(arr_soc, ab_cfg.get("target_arrival_soc", 10.0))
            _field("🔋", "Projected Arrival SoC", f"{soc_badge} (from {dep_soc}% departure | ~{energy_kwh:.1f} kWh consumed)")
        except Exception:
            pass
    elif station.distance_km is not None:
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
        url_label = "Find Us URL" if (is_tesla_sc or is_tesla_dest) else "Web Link / URL"
        _field("🔗", url_label, format_display_url(station.url, max_len=75))

 # 2. Hardware & Access
    pwr_str = f"Up to {station.formatted_power()}" if station.max_power_kw else "Standard Power"
    hw_model = f" ({station.hardware_model})" if station.hardware_model else ""
    hw_desc = f"{station.stalls_total} Stalls | {pwr_str}{hw_model}"
    _field("🔌", "Hardware", hw_desc)
    
    cb = (station.metadata or {}).get("connector_breakdown")
 # (AGENTS.local.md, 2026-09-16): connector_breakdown is PlugShare
 # metadata ("2 x CCS2 (DC 120kW)") that survives onto a merged record even
 # when the primary (Tier 1) source's own "Hardware" line above already
 # says the exact same thing in different words ("2 Stalls | Up to 120 kW")
 # - user-reported as confusing duplication on Bunnings Gladesville. Only
 # show the granular per-unit breakdown when it's actually informative,
 # i.e. this site has more than one distinct connector/power combination
 # (mixed hardware) - otherwise collapse to a plain connector type name,
 # since the count and power are already stated once, above.
    if cb and isinstance(cb, list) and len(cb) > 0:
        if len(set(cb)) > 1:
            _field("🔌", "Connectors", ", ".join(cb))
        elif station.plugs:
            _field("🔌", "Connectors", ", ".join(sorted(set(station.plugs))))
        else:
            _field("🔌", "Connectors", ", ".join(cb))
    elif station.plugs:
        _field("🔌", "Connectors", ", ".join(station.plugs))

    meta = station.metadata or {}
 # Non-Tesla Access / Compatibility
    if station.speed_tier == "supercharger":
        open_all = meta.get("open_to_non_tesla")
        acc_str = f"{C_GREEN}YES (CCS2 All EVs){C_RESET}" if open_all else f"{C_RED}NO (Tesla Only){C_RESET}"
        _field("🚗", "Non-Tesla Access", acc_str)
    elif is_tesla_dest or "tesla" in (station.network or "").lower() or "tesla" in (station.source or "").lower():
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
    if is_tesla_dest:
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
 # to; analyse_charging.py needs the effective date to match old
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
        pricing_title = "Tesla & Members" if (is_tesla_sc or is_tesla_dest) else (station.network or "Network")
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
    elif is_tesla_sc and (station.metadata or {}).get("open_to_non_tesla"):
        print(f"\n  🔌 {C_BOLD}Non-Tesla Pricing:{C_RESET} {C_YELLOW}Not available (re-scrape to fetch){C_RESET}")

 # "Also Listed On" - (2026-09-15, AGENTS.local.md). A merged
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

 # Cached Facilities Quality Rating / Summary (REQ-082)
    try:
        from station_facilities import get_facilities_registry, format_facilities_summary_card
        _fac_reg = get_facilities_registry()
    except ImportError:
        try:
            from lib.station_facilities import get_facilities_registry, format_facilities_summary_card
            _fac_reg = get_facilities_registry()
        except ImportError:
            _fac_reg = None
            format_facilities_summary_card = None

    if _fac_reg and station.lat and station.lon:
        try:
            from lib.station_facilities import resolve_station_physical_coords
            phys_lat, phys_lon, _ = resolve_station_physical_coords(station)
        except Exception:
            phys_lat, phys_lon = station.lat, station.lon
        cached_fac = _fac_reg.get_station_facilities(station_key=station.key, station_name=station.name, lat=phys_lat, lon=phys_lon)
        if cached_fac and format_facilities_summary_card:
            lines = format_facilities_summary_card(cached_fac, indent="  ")
            if lines:
                print()
                for line in lines:
                    print(line)

    print()


def _handle_browser_open_combo(
    choice: str,
    gmaps_coords: Optional[Tuple[Optional[float], Optional[float]]] = None,
    osm_element: Optional[Tuple[str, Any]] = None,
    osm_coords: Optional[Tuple[Optional[float], Optional[float]]] = None,
    sc_id: Optional[Any] = None,
    url: Optional[str] = None,
    provider_name: str = "Tesla"
) -> bool:
    """
    Handles single or multi-target browser opening commands:
    e.g. 'm', 'o', 's', 'b', 'mo', 'ms', 'os', 'mos', 'som', 'smo', 'm o s', 'm,o,s'.
    Returns True if choice was handled as a browser command, False otherwise.
    """
    choice_clean = choice.lower().strip()
    if not choice_clean:
        return False

    if choice_clean in ("1", "2", "3", "4", "5", "6", "7", "8", "9", "q", "quit", "exit", "u", "update", "refresh"):
        return False
    if choice_clean.startswith("f ") or choice_clean in ("f", "facilities", "nearby", "a", "audit"):
        return False

    valid_browser_chars = set("mosb")
    letters = re.findall(r"[a-z]", choice_clean)
    if not letters:
        return False

    is_known_word = choice_clean in ("m", "maps", "gmaps", "o", "osm", "s", "sc", "supercharge", "supercharge.info", "b", "browser", "url")
    is_char_combo = set(letters).issubset(valid_browser_chars)

    if not (is_known_word or is_char_combo):
        return False

    targets_to_open: List[str] = []
    if choice_clean in ("m", "maps", "gmaps"):
        targets_to_open = ["m"]
    elif choice_clean in ("o", "osm"):
        targets_to_open = ["o"]
    elif choice_clean in ("s", "sc", "supercharge", "supercharge.info"):
        targets_to_open = ["s"]
    elif choice_clean in ("b", "browser", "url"):
        targets_to_open = ["b"]
    else:
        seen = set()
        for ch in letters:
            if ch in valid_browser_chars and ch not in seen:
                seen.add(ch)
                targets_to_open.append(ch)

    for target in targets_to_open:
        if target == "m":
            if gmaps_coords and gmaps_coords[0] is not None and gmaps_coords[1] is not None:
                g_url = f"https://www.google.com/maps/search/?api=1&query={gmaps_coords[0]},{gmaps_coords[1]}"
                webbrowser.open(g_url)
                print(f"🌐 {C_GREEN}Opened Google Maps ({provider_name} location) in browser: {g_url}{C_RESET}")
        elif target == "o":
            if osm_element and osm_element[1]:
                osm_url = f"https://www.openstreetmap.org/{osm_element[0]}/{osm_element[1]}"
            elif osm_coords and osm_coords[0] is not None and osm_coords[1] is not None:
                osm_url = f"https://www.openstreetmap.org/?mlat={osm_coords[0]}&mlon={osm_coords[1]}#map=19/{osm_coords[0]}/{osm_coords[1]}"
            elif gmaps_coords and gmaps_coords[0] is not None and gmaps_coords[1] is not None:
                osm_url = f"https://www.openstreetmap.org/?mlat={gmaps_coords[0]}&mlon={gmaps_coords[1]}#map=19/{gmaps_coords[0]}/{gmaps_coords[1]}"
            else:
                osm_url = "https://www.openstreetmap.org"
            webbrowser.open(osm_url)
            print(f"🌐 {C_GREEN}Opened OpenStreetMap in browser: {osm_url}{C_RESET}")
        elif target == "s":
            if sc_id:
                sc_url = f"https://supercharge.info/map?siteId={sc_id}"
                webbrowser.open(sc_url)
                print(f"🌐 {C_GREEN}Opened supercharge.info in browser: {sc_url}{C_RESET}")
            else:
                print(f"{C_YELLOW}⚠ No supercharge.info site ID available.{C_RESET}")
        elif target == "b":
            if url:
                webbrowser.open(url)
                print(f"🌐 {C_GREEN}Opened Charger URL in browser: {url}{C_RESET}")
            else:
                print(f"{C_YELLOW}⚠ No charger URL available.{C_RESET}")

    print()
    return True


def interactive_station_actions(station: UnifiedStationRecord) -> None:
    """Provides interactive browser launch and audit actions for inspected station."""
    if not sys.stdin.isatty():
        return

    meta = station.metadata or {}
    lat = station.lat
    lon = station.lon
    osm_lat = meta.get("osm_lat")
    osm_lon = meta.get("osm_lon")
    osm_id = meta.get("osm_node_id") or meta.get("osm_id")
    url = station.url or (f"https://www.google.com/maps/search/?api=1&query={lat},{lon}" if lat and lon else None)

    is_tesla_sc = (
        station.speed_tier == "supercharger"
        and ("tesla" in (station.source or "").lower() or (station.network or "").lower() == "tesla" or "tesla" in (station.views or {}))
    )
    sc_site = None
    if is_tesla_sc:
        try:
            from lib.supercharge_info import find_supercharge_site
            loc_id = meta.get("location_id") or meta.get("short_name") or station.source_id
            sc_site = find_supercharge_site(station.name, station.lat, station.lon, location_id=loc_id)
            if sc_site:
                if sc_site.get("osmId") and not osm_id:
                    osm_id = sc_site.get("osmId")
                sc_gps = sc_site.get("gps", {})
                if sc_gps.get("latitude") and not osm_lat:
                    osm_lat = sc_gps.get("latitude")
                    osm_lon = sc_gps.get("longitude")
        except Exception:
            sc_site = None

    loc_provider_name = "Tesla" if (is_tesla_sc or is_tesla_dest or "tesla" in (station.source or "").lower()) else (station.network or "Station")

    while True:
        print(f"{C_BOLD}Actions:{C_RESET}")
        print(f"  [{C_GREEN}u{C_RESET}]   Update / re-verify this station live (archives historical changes)")
        print(f"  [{C_GREEN}m{C_RESET}]   Open {loc_provider_name} location in your browser (Google Maps)")
        print(f"  [{C_GREEN}o{C_RESET}]   Open OSM location in your browser (OpenStreetMap)")
        if sc_site and sc_site.get("id"):
            print(f"  [{C_GREEN}s{C_RESET}]   Open SC.info location in your browser (supercharge.info)")
        print(f"  [{C_GREEN}b{C_RESET}]   Open Charger URL in browser")
        print(f"  [{C_GREEN}f{C_RESET}]   Nearby Facilities (instant pullup; type 'f 500' for radius or 'f r' to refresh live)")
        print(f"  [{C_GREEN}a{C_RESET}]   Audit this station against OpenStreetMap")
        print(f"  [{C_GREEN}q{C_RESET}]   Quit (or press Enter)")

        prompt_choices = "u/m/o/s/b/f/a/q" if (sc_site and sc_site.get("id")) else "u/m/o/b/f/a/q"
        try:
            choice = input(f"\n{C_BOLD}Choice [{prompt_choices}]: {C_RESET}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if choice in ("q", "quit", "exit", ""):
            break
        elif choice in ("u", "update", "refresh"):
            try:
                from providers_chargers.chargers_manager import ChargerDiscoveryManager
                mgr = ChargerDiscoveryManager()
                mgr.load_all_providers()
                print(f"🔄 Re-verifying station '{station.name}' live...")
                ok, diffs, fresh = mgr.reverify_station(station)
                _print_reverify_result(station.name, ok, diffs)
                if ok and fresh:
                    station = fresh
                    print()
                    display_station_inspection_card(station)
            except Exception as e:
                print(f"{C_RED}❌ Error reverifying station: {e}{C_RESET}")
            print()
        elif choice.startswith("f") or choice in ("facilities", "nearby"):
            parts = choice.split()
            radius = 250
            force = "refresh" in choice or choice in ("fr", "f r")
            for p in parts[1:]:
                if p.isdigit():
                    radius = int(p)
                elif p in ("r", "refresh", "-r", "--refresh"):
                    force = True
            display_nearby_facilities(station, radius_m=radius, force_refresh=force)
            print()
        elif _handle_browser_open_combo(
            choice,
            gmaps_coords=(lat, lon),
            osm_element=("node", osm_id) if osm_id else None,
            osm_coords=(osm_lat, osm_lon) if (osm_lat and osm_lon) else (lat, lon),
            sc_id=sc_site.get("id") if sc_site else None,
            url=url,
            provider_name=loc_provider_name
        ):
            pass
        elif choice in ("a", "audit"):
            try:
                from places_osm_editor import _audit_tesla_station, load_osm_config
                cfg = load_osm_config()
                _audit_tesla_station(query=station.key or station.name, token=cfg.get("access_token"))
            except Exception as e:
                print(f"{C_RED}❌ Error running audit: {e}{C_RESET}")
            print()


def inspect_single_station(manager: ChargerDiscoveryManager, query: str, eval_time: Optional[str] = None) -> None:
    """Natively resolves and inspects a single station across all unified providers."""
    all_stations = manager.get_all_stations()
    q_lower = query.strip().lower()

    matches = []
    for s in all_stations:
        meta = s.metadata or {}
        findus_loc = (meta.get("findus_location_name") or "").lower()
        findus_list = (meta.get("findus_list_name") or "").lower()
        short_n = (meta.get("short_name") or "").lower()
        if (q_lower == s.key.lower() or
            q_lower == s.name.lower() or
            q_lower in s.name.lower() or
            q_lower in s.key.lower() or
            q_lower in s.suburb.lower() or
            q_lower in s.address.lower() or
            q_lower == short_n or
            q_lower in findus_loc or
            q_lower in findus_list or
            q_lower == str(s.source_id).lower()):
            matches.append(s)

    # Prefer exact match if exists
    exact = [m for m in matches if m.name.lower() == q_lower or m.key.lower() == q_lower or (m.metadata or {}).get("short_name", "").lower() == q_lower]
    if len(exact) == 1:
        matches = exact

    if not matches:
        print(f"{C_RED}❌ No charging station matching '{query}' found in registries.{C_RESET}")
        return

    if len(matches) == 1:
        st = matches[0]
        display_station_inspection_card(st, eval_time=eval_time)
        interactive_station_actions(st)
        return

    # Multiple matches
    render_stations_table(matches, title=f"SEARCH RESULTS FOR '{query}'", eval_time=eval_time)
    if sys.stdin.isatty():
        num_m = f"1-{len(matches)}" if len(matches) > 1 else "1"
        print(f"\n{C_BOLD}Actions:{C_RESET}")
        print(f"  [{C_GREEN}{num_m}{C_RESET}]    Inspect station details & nearby amenities (e.g. {C_GREEN}1{C_RESET})")
        print(f"  [{C_GREEN}q{C_RESET}]      Quit (or press Enter)")
        try:
            pick = input(f"\n{C_BOLD}Choice [{num_m}/q]: {C_RESET}").strip().lower()
            if pick.isdigit() and 1 <= int(pick) <= len(matches):
                st = matches[int(pick) - 1]
                display_station_inspection_card(st, eval_time=eval_time)
                interactive_station_actions(st)
        except (EOFError, KeyboardInterrupt):
            print()


def display_nearby_facilities(station: UnifiedStationRecord, radius_m: int = 250, force_refresh: bool = False) -> None:
    """
    Looks up real, named nearby facilities (shops, food, amenities, services)
    around a charging station via OpenStreetMap's free/keyless Overpass API -
 # reusing Tools/places_manager.py's already-proven query_overpass_pois(),
    the same lookup that already identifies drive-log stop locations.

    Persisted in station_facilities.json for instant offline pullup with
    a 0-10 Quality Scoring Engine (REQ-082).
    """
    try:
        from lib.station_facilities import resolve_station_physical_coords
        phys_lat, phys_lon, coord_src = resolve_station_physical_coords(station)
    except Exception:
        phys_lat, phys_lon, coord_src = station.lat, station.lon, "Source GPS"

    if not phys_lat or not phys_lon:
        print(f"{C_YELLOW}⚠ No coordinates on file for this station - can't look up nearby facilities.{C_RESET}")
        return

    try:
        from station_facilities import get_facilities_registry
        reg = get_facilities_registry()
    except ImportError:
        try:
            from lib.station_facilities import get_facilities_registry
            reg = get_facilities_registry()
        except ImportError:
            reg = None

    cached = reg.get_station_facilities(station_key=station.key, station_name=station.name, lat=phys_lat, lon=phys_lon) if (reg and not force_refresh) else None

    if cached:
        src_tag = f" {C_GRAY}[coords: {coord_src}]{C_RESET}" if coord_src != "Source GPS" else ""
        print(f"\n{C_BOLD}🏙️  Nearby Facilities (within {cached.get('radius_m', radius_m)}m of {station.name}{src_tag}):{C_RESET}")
        print(f"  {C_BOLD}Quality Score:{C_RESET} {cached.get('badge', '')}  {C_GRAY}[⚡ Cached Registry - instant pullup]{C_RESET}\n")
        pois = cached.get("pois", [])
    else:
        src_tag = f" via {coord_src}" if coord_src != "Source GPS" else ""
        print(f"\n{C_BOLD}🏙️  Nearby Facilities (within {radius_m}m of {station.name}{src_tag}, via OpenStreetMap):{C_RESET}")
        print(f"{C_GRAY}   Looking up live - this can take a few seconds...{C_RESET}")

        try:
            from places_manager import query_overpass_pois
        except ImportError as e:
            print(f"{C_RED}❌ Could not load the facilities lookup (Tools/places_manager.py): {e}{C_RESET}")
            return

        try:
            pois = query_overpass_pois(phys_lat, phys_lon, radius_m=radius_m)
        except Exception as e:
            print(f"{C_RED}❌ Facilities lookup failed: {e}{C_RESET}")
            return

        if not pois:
            print(f"{C_YELLOW}  No named facilities found within {radius_m}m (or OpenStreetMap's Overpass API didn't respond - try again in a moment).{C_RESET}\n")
            return

        if reg:
            cached = reg.store_station_facilities(
                station_key=station.key,
                station_name=station.name,
                network=station.network or "unknown",
                lat=phys_lat,
                lon=phys_lon,
                pois=pois,
                radius_m=radius_m
            )
            print(f"  {C_BOLD}Quality Score:{C_RESET} {cached.get('badge', '')}\n")

    term_width = shutil.get_terminal_size((100, 24)).columns

 # Prominently display immediate doorstep facilities (<= 100m)
    doorstep = [p for p in pois if p.get("dist_m", 999.0) <= 100.0]
    if doorstep:
        print(f"  🚪 {C_BOLD}Immediate Doorstep (≤ 100m — 1-2 min walk) ({len(doorstep)}):{C_RESET}")
        for poi in doorstep:
            dist_str = f"{poi['dist_m']:.0f}m"
            emoji = poi.get("emoji", "📍")
            prefix = f"     • {emoji} "
            suffix = f" ({dist_str})"
            avail = max(20, term_width - display_len(prefix) - display_len(suffix))
            name_val = poi.get("name", "")
            cat_val = poi.get("category", "")
            if cat_val:
                full_c = f"{name_val} — {cat_val}"
                if display_len(full_c) <= avail:
                    print(f"{prefix}{C_CYAN}{name_val}{C_RESET} — {cat_val}{suffix}")
                else:
                    cat_short = truncate_display(cat_val, 16)
                    rem_n = max(8, avail - display_len(f" — {cat_short}"))
                    name_short = truncate_display(name_val, rem_n)
                    print(f"{prefix}{C_CYAN}{name_short}{C_RESET} — {cat_short}{suffix}")
            else:
                name_short = truncate_display(name_val, avail)
                print(f"{prefix}{C_CYAN}{name_short}{C_RESET}{suffix}")
        print()
    elif pois:
        closest = pois[0]
        print(f"  🚪 {C_BOLD}Immediate Doorstep (≤ 100m):{C_RESET} {C_DIM}None within 100m (closest is {closest['name']} at {closest['dist_m']:.0f}m){C_RESET}\n")

    plural = "y" if len(pois) == 1 else "ies"
    print(f"  {C_BOLD}All Facilities within {radius_m}m ({len(pois)} total):{C_RESET}\n")

 # Group POIs by category group
    groups = {}
    for p in pois:
        grp = p.get("group", "Other Facilities")
        emoji = p.get("emoji", "📍")
        key = (grp, emoji)
        if key not in groups:
            groups[key] = []
        groups[key].append(p)

    total_shown = 0
    max_total = 35
    for (grp_name, grp_emoji), grp_pois in groups.items():
        if total_shown >= max_total:
            break
        print(f"  {grp_emoji} {C_BOLD}{grp_name}{C_RESET} ({len(grp_pois)}):")
        for poi in grp_pois:
            if total_shown >= max_total:
                break
            dist_str = f"{poi['dist_m']:.0f}m"
            prefix = "     • "
            suffix = f" ({dist_str})"
            avail = max(20, term_width - display_len(prefix) - display_len(suffix))
            name_val = poi.get("name", "")
            cat_val = poi.get("category", "")
            if cat_val:
                full_c = f"{name_val} — {cat_val}"
                if display_len(full_c) <= avail:
                    print(f"{prefix}{C_CYAN}{name_val}{C_RESET} — {cat_val}{suffix}")
                else:
                    cat_short = truncate_display(cat_val, 16)
                    rem_n = max(8, avail - display_len(f" — {cat_short}"))
                    name_short = truncate_display(name_val, rem_n)
                    print(f"{prefix}{C_CYAN}{name_short}{C_RESET} — {cat_short}{suffix}")
            else:
                name_short = truncate_display(name_val, avail)
                print(f"{prefix}{C_CYAN}{name_short}{C_RESET}{suffix}")
            total_shown += 1
        print()

    if len(pois) > total_shown:
        print(f"  {C_GRAY}... and {len(pois) - total_shown} more facilities within {radius_m}m.{C_RESET}\n")


def main():
 # AGENTS.local.md , 2026-09-15: user - "The usage / man here is
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
 # split below (continued); EXAMPLES is the epilog, unchanged in
 # content but now under one consistent heading style.
    synopsis = """charger_find.py --sync-all | --sync-tesla | --sync-chargefox | --sync-evx |
                     --sync-evie | --sync-bppulse | --sync-plugshare | --sync-ampcharge | --sync-exploren |
                     --sync-jolt | --sync-nrma | --export-consolidated
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

  Route Corridor Search:
  # Find Superchargers along a road trip from Sydney to Brisbane:
    ./Tools/charger_find.py --from "Sydney" --to "Brisbane" --sc

  # Find DC fast chargers (>= 150 kW) from Home to Canberra via Goulburn:
    ./Tools/charger_find.py --from Home --to "Canberra" --via "Goulburn" --dc --min-kw 150

  # Find cheapest AmpCharge / Ampol stations along Hume Highway (Sydney -> Melbourne):
    ./Tools/charger_find.py --from "Sydney" --to "Melbourne" --network ampcharge --sort price --corridor 10

  Inspection & Drill-Down:
  # Inspect detailed card for a specific station by query:
    ./Tools/charger_find.py -q "Macquarie"

  # Inspect a PlugShare location ID directly:
    ./Tools/charger_find.py --plugshare 801149 --save
"""
    )
    
 # AGENTS.local.md , 2026-09-15: --help had become unreadable - a
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
    g_location.add_argument("--radius", "-r", "--radius-km", type=float, default=50.0, help="Search radius in km (default: 50)")
    g_location.add_argument("--query", "-q", type=str, help="Text search by station name, suburb, or network")
    g_location.add_argument("--all", "-a", action="store_true", help="List all registered stations across the entire country or selected state")
    g_location.add_argument("--state", choices=["NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT", "nsw", "vic", "qld", "wa", "sa", "tas", "act", "nt"], help="Filter by State/Territory (e.g. NSW, VIC)")
    g_location.add_argument("--country", default="Australia", help="Country name (default: Australia)")

    g_route = parser.add_argument_group("Route & Highway Corridor Search")
    g_route.add_argument("--from", "--origin", dest="from_loc", type=str, help="Route origin / departure point (place name, address, or lat,lon)")
    g_route.add_argument("--to", "--destination", dest="to_loc", type=str, help="Route destination point (place name, address, or lat,lon)")
    g_route.add_argument("--via", nargs="+", help="Intermediate route waypoint(s) (e.g. --via 'Goulburn' 'Canberra')")
    g_route.add_argument("--corridor-radius", "--corridor", "--detour-max", type=float, default=5.0, help="Corridor radius / max highway detour in km (default: 5.0 km)")

    g_abrp = parser.add_argument_group("ABRP & EV Trip Planning")
    g_abrp.add_argument("--soc", "--departure-soc", dest="departure_soc", type=str, help="Departure battery percentage (0-100 or 'live' to query Tessie real-time)")
    g_abrp.add_argument("--target-soc", "--min-soc", dest="target_soc", type=float, help="Target minimum arrival battery percentage (default: 10.0)")
    g_abrp.add_argument("--max-soc", dest="max_soc", type=float, help="Maximum charging target percentage (default: 85.0)")
    g_abrp.add_argument("--abrp-link", "--abrp", action="store_true", default=True, help="Generate and display 1-click ABRP interactive road trip URL (default: True)")
    g_abrp.add_argument("--no-abrp", action="store_false", dest="abrp_link", help="Suppress ABRP 1-click URL generation")

    g_speed = parser.add_argument_group("Charging Speed & Type Filter")
 # (2026-09-15) tried adding a dedicated --tesla-dest/--tesla-sc/
 # --tesla-dc flag family to disambiguate --dc from its Tesla-only meaning.
 # (2026-09-15, AGENTS.local.md): user - "We've just done
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
    g_network.add_argument("--network", "-n", type=str, help="Filter by network - free-text substring match against network/operator/station-name/source (case-insensitive). Known first-party networks: tesla, chargefox (covers WA EV Network, Qld Electric Highway sub-brands), nrma (own Tier 1 KML feed, merged with Chargefox's ~28 overlapping NRMA sites where both cover the same site), evx, evie, bppulse, ampcharge (covers Ampol Foodary), exploren, jolt. Use 'plugshare' to see only PlugShare-sourced listings, or any other text to match a PlugShare-aggregated sub-brand.")

    g_sort = parser.add_argument_group("Sorting & Time of Use")
    g_sort.add_argument("--sort", "-s", choices=["distance", "price", "power", "stalls", "bays", "name", "state", "facilities", "facility", "rating", "progress", "route", "detour", "score", "stop_score", "quality"], default="distance", help="Sort order (default: distance; or progress/detour/score along a route)")
    g_sort.add_argument("-t", "--time", type=str, help="Evaluate TOU pricing at specific local time (HH:MM)")

    g_output = parser.add_argument_group("Output Controls")
    g_output.add_argument("--limit", type=int, default=100, help="Maximum number of stations to display (default: 100)")
    g_output.add_argument("--json", action="store_true", help="Output results in JSON format")

    g_sync = parser.add_argument_group("Synchronization")
    g_sync.add_argument("--sync-all", action="store_true", help="End-to-end sync across all providers (Tesla, Chargefox, EVX, Evie, bp pulse, PlugShare, AmpCharge, Exploren, Jolt, NRMA, Master Catalog)")
    g_sync.add_argument("--sync-tesla", action="store_true", help="Download, verify and cache Tesla Superchargers and Destination Chargers")
    g_sync.add_argument("--sync-chargefox", action="store_true", help="Download and cache all Chargefox & NRMA stations across Australia")
    g_sync.add_argument("--sync-evx", action="store_true", help="Download and cache all EVX Kerbside Polechargers across Australia")
    g_sync.add_argument("--sync-evie", action="store_true", help="Download and cache all Evie Networks fast charging stations across Australia")
    g_sync.add_argument("--sync-bppulse", action="store_true", help="Download and cache all bp pulse DC Fast & Ultra-Fast charging stations across Australia")
    g_sync.add_argument("--sync-plugshare", action="store_true", help="Discover and cache PlugShare 3rd-party stations across Australia or near a location")
    g_sync.add_argument("--sync-ampcharge", action="store_true", help="Download and cache all Ampol AmpCharge & Ampol Foodary EV fast-charging sites across Australia")
    g_sync.add_argument("--sync-exploren", action="store_true", help="Download and cache all Exploren AC/DC charging stations across Australia")
    g_sync.add_argument("--sync-jolt", action="store_true", help="Download and cache all Jolt DC fast charging stations across Australia")
    g_sync.add_argument("--sync-nrma", action="store_true", help="Download and cache all NRMA EV Charging Network stations across Australia")
    g_sync.add_argument("--export-consolidated", "--sync-master", action="store_true", help="Compile and export multi-view master charging_stations.json to Tessie data home")

    g_facilities = parser.add_argument_group("Nearby Facilities & POI Lookup")
    g_facilities.add_argument("--facilities", "-f", "--poi", action="store_true", help="Look up and display nearby facilities (cafes, dining, supermarkets, retail, amenities) around matching station(s)")
    g_facilities.add_argument("--facilities-radius", "--radius-m", type=int, default=250, help="Search radius in meters for nearby facilities (default: 250)")
    g_facilities.add_argument("--min-facilities", "--facilities-min", choices=["excellent", "good", "basic", "sparse"], help="Filter stations meeting minimum facility quality rating tier")
    g_facilities.add_argument("--supermarket", "--groceries", action="store_true", help="Filter stations located at or near a major supermarket (Woolworths, Coles, Aldi, IGA, Costco, etc.)")
    g_facilities.add_argument("--sync-facilities", action="store_true", help="Batch look up and cache nearby facilities for matching stations in station_facilities.json")

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
 # explorer. (2026-09-15): dropped the short-lived --tesla-dest
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

 # (2026-09-15, AGENTS.local.md): user typo'd "--network changefox"
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
        _KNOWN_NETWORKS = ["tesla", "chargefox", "nrma", "evie", "evx", "bppulse", "plugshare", "ampcharge", "exploren", "jolt"]
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
 # AGENTS.md every step below used to catch its own
 # Exception, print a "⚠️ encountered error" line, and just carry
 # on - nothing tracked whether any step actually failed, so this
 # command always printed "✨ Full multi-network sync completed."
 # and exited 0 even if EVERY provider failed (auth error, network
 # down, a schema break like 's chargefox one). sync_results
 # now records a (label, ok) pair per step so the final banner and
 # exit code reflect what actually happened.
        sync_results = []

        print("🚀 [1/11] Starting Full Network Sync: Tesla Superchargers & Destination Chargers...")
        try:
            from charger_find_tesla import TeslaChargerExplorer
            explorer = TeslaChargerExplorer()
            ref_lat, ref_lon, ref_label = (None, None, None)
            if args.near:
                ref_lat, ref_lon, ref_label = resolve_reference_coords(args.near)
            
            radius = args.radius if args.radius is not None else 50.0
            for c_type in ["superchargers", "chargers"]:
                t_lbl = "Superchargers" if c_type == "superchargers" else "Destination Chargers"
                print(f"  {C_CYAN}⚡ Fetching Tesla {t_lbl}...{C_RESET}")
                t_stations = explorer.fetch_station_list(country=args.country or "Australia", charger_type=c_type)
                if ref_lat is not None and ref_lon is not None:
                    from providers_chargers.chargers_base import haversine_km
                    t_stations = [s for s in t_stations if s.get("_lat") is not None and s.get("_lon") is not None and haversine_km(ref_lat, ref_lon, s["_lat"], s["_lon"]) <= radius]
                explorer.scrape_all_stations(t_stations, subset_filter="new", pacing_delay=args.delay, timeout_sec=args.timeout, max_retries=args.retries)
            sync_results.append(("Tesla", True))
        except Exception as e:
            print(f"  {C_YELLOW}⚠️ Tesla sync encountered error: {e}{C_RESET}")
            sync_results.append(("Tesla", False))

        print(f"\n{C_CYAN}🦊 [2/11] Syncing Chargefox Network across Australia via GraphQL...{C_RESET}")
        cf_ok = False
        for p in manager.providers:
            if isinstance(p, ChargefoxProvider):
                try:
                    synced_cf = p.sync_all_australia()
                    print(f"  {C_GREEN}✔ Cached {len(synced_cf)} Chargefox/NRMA stations to {p.registry_path}{C_RESET}")
                    cf_ok = True
                except Exception as e:
                    print(f"  {C_YELLOW}⚠️ Chargefox sync encountered error: {e}{C_RESET}")
        sync_results.append(("Chargefox/NRMA", cf_ok))

        print(f"\n{C_CYAN}🔌 [3/11] Syncing EVX Kerbside Polecharger Network across Australia...{C_RESET}")
        evx_ok = False
        for p in manager.providers:
            if isinstance(p, EVXProvider):
                try:
                    synced_evx = p.sync()
                    print(f"  {C_GREEN}✔ Cached {len(synced_evx)} EVX stations to {p.registry_path}{C_RESET}")
                    evx_ok = True
                except Exception as e:
                    print(f"  {C_YELLOW}⚠️ EVX sync encountered error: {e}{C_RESET}")
        sync_results.append(("EVX", evx_ok))

        print(f"\n{C_CYAN}🕊️ [4/11] Syncing Evie Networks Fast Charging Stations across Australia...{C_RESET}")
        evie_ok = False
        for p in manager.providers:
            if isinstance(p, EvieProvider):
                try:
                    synced_evie = p.sync()
                    print(f"  {C_GREEN}✔ Cached {len(synced_evie)} Evie stations to {p.registry_path}{C_RESET}")
                    evie_ok = True
                except Exception as e:
                    print(f"  {C_YELLOW}⚠️ Evie sync encountered error: {e}{C_RESET}")
        sync_results.append(("Evie", evie_ok))

        print(f"\n{C_CYAN}⚡ [5/11] Syncing bp pulse DC Fast & Ultra-Fast Charging Stations across Australia...{C_RESET}")
        bp_ok = False
        for p in manager.providers:
            if isinstance(p, BPPulseProvider):
                try:
                    synced_bp = p.sync()
                    print(f"  {C_GREEN}✔ Cached {len(synced_bp)} bp pulse stations to {p.registry_path}{C_RESET}")
                    bp_ok = True
                except Exception as e:
                    print(f"  {C_YELLOW}⚠️ bp pulse sync encountered error: {e}{C_RESET}")
        sync_results.append(("bp pulse", bp_ok))

        print(f"\n{C_CYAN}🔌 [6/11] Syncing PlugShare 3rd-party Network...{C_RESET}")
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
                    print(f"  {C_GREEN}✔ Discovered and saved {len(synced_ps)} PlugShare stations to {p.registry_path}{C_RESET}")
                    ps_ok = True
                except Exception as e:
                    print(f"  {C_YELLOW}⚠️ PlugShare sync encountered error: {e}{C_RESET}")
        sync_results.append(("PlugShare", ps_ok))

        print(f"\n{C_CYAN}⛽ [7/11] Syncing Ampol AmpCharge & Ampol Foodary EV Fast Charging Sites across Australia...{C_RESET}")
        amp_ok = False
        for p in manager.providers:
            if isinstance(p, AmpChargeProvider):
                try:
                    synced_amp = p.sync_all_australia()
                    print(f"  {C_GREEN}✔ Cached {len(synced_amp)} AmpCharge/Ampol Foodary stations to {p.registry_path}{C_RESET}")
                    amp_ok = True
                except Exception as e:
                    print(f"  {C_YELLOW}⚠️ AmpCharge sync encountered error: {e}{C_RESET}")
        sync_results.append(("AmpCharge", amp_ok))

        print(f"\n{C_CYAN}🔋 [8/11] Syncing Exploren AC/DC Charging Network across Australia...{C_RESET}")
        exploren_ok = False
        for p in manager.providers:
            if isinstance(p, ExplorenProvider):
                try:
                    synced_exploren = p.sync()
                    print(f"  {C_GREEN}✔ Cached {len(synced_exploren)} Exploren stations to {p.registry_path}{C_RESET}")
                    exploren_ok = True
                except Exception as e:
                    print(f"  {C_YELLOW}⚠️ Exploren sync encountered error: {e}{C_RESET}")
        sync_results.append(("Exploren", exploren_ok))

        print(f"\n{C_CYAN}🔵 [9/11] Syncing Jolt DC Fast Charging Network across Australia...{C_RESET}")
        jolt_ok = False
        for p in manager.providers:
            if isinstance(p, JoltProvider):
                try:
                    synced_jolt = p.sync()
                    print(f"  {C_GREEN}✔ Cached {len(synced_jolt)} Jolt stations to {p.registry_path}{C_RESET}")
                    jolt_ok = True
                except Exception as e:
                    print(f"  {C_YELLOW}⚠️ Jolt sync encountered error: {e}{C_RESET}")
        sync_results.append(("Jolt", jolt_ok))

        print(f"\n{C_CYAN}⚡ [10/11] Syncing NRMA EV Charging Network across Australia...{C_RESET}")
        nrma_ok = False
        for p in manager.providers:
            if isinstance(p, NrmaProvider):
                try:
                    synced_nrma = p.sync()
                    print(f"  {C_GREEN}✔ Cached {len(synced_nrma)} NRMA stations to {p.registry_path}{C_RESET}")
                    nrma_ok = True
                except Exception as e:
                    print(f"  {C_YELLOW}⚠️ NRMA sync encountered error: {e}{C_RESET}")
        sync_results.append(("NRMA", nrma_ok))

        print(f"\n{C_CYAN}🌐 [11/11] Compiling and Exporting Consolidated Master Registry...{C_RESET}")
        try:
            out_path, total_count = manager.export_consolidated_registry()
            print(f"  {C_GREEN}✔ Successfully exported {total_count} consolidated multi-view stations to:{C_RESET}")
            print(f"    {out_path}")
            sync_results.append(("Master export", True))
        except Exception as e:
            print(f"  {C_YELLOW}⚠️ Master export encountered error: {e}{C_RESET}")
            sync_results.append(("Master export", False))

        failed = [label for label, ok in sync_results if not ok]
        if failed:
            print(f"\n{C_RED}💥 Full multi-network sync FINISHED WITH {len(failed)} FAILURE(S): {', '.join(failed)}{C_RESET}")
            print(f"   {C_DIM}(see the ⚠️ lines above for each one's error - nothing else below this line was skipped, only these steps.){C_RESET}")
            sys.exit(1)
        print(f"\n{C_GREEN}✨ Full multi-network sync completed - all 11 steps succeeded.{C_RESET}")
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
            print(f"{C_CYAN}🌐 Synchronizing Tesla {type_label}...{C_RESET}")
            stations = explorer.fetch_station_list(country=args.country or "Australia", charger_type=c_type)
            if args.near:
                ref_lat, ref_lon, ref_label = resolve_reference_coords(args.near)
                if ref_lat is not None and ref_lon is not None:
                    radius = args.radius or 50.0
                    from providers_chargers.chargers_base import haversine_km
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
                    print(f"  {C_DIM}📍 Filtered to {len(stations)} stations within {radius:.0f}km of {ref_label}{C_RESET}")
            
            subset_filter = "unpriced" if args.unpriced else ("new" if args.new else ("renamed" if args.renamed else ("stale" if args.stale else None)))
            if subset_filter:
                print(f"  {C_DIM}🎯 Applying subset filter: '{subset_filter}'{C_RESET}")
            explorer.scrape_all_stations(
                stations,
                pacing_delay=args.delay,
                timeout_sec=args.timeout,
                max_retries=args.retries,
                subset_filter=subset_filter
            )
        print(f"{C_GREEN}✔ Tesla synchronization complete.{C_RESET}")
        return

    if args.sync_chargefox:
        for p in manager.providers:
            if isinstance(p, ChargefoxProvider):
                print(f"{C_CYAN}🌐 Fetching all Chargefox & NRMA stations across Australia via GraphQL...{C_RESET}")
                synced = p.sync_all_australia()
                print(f"{C_GREEN}✔ Successfully saved {len(synced)} Chargefox stations to {p.registry_path}{C_RESET}")
                return

    if args.sync_ampcharge:
        for p in manager.providers:
            if isinstance(p, AmpChargeProvider):
                print(f"{C_CYAN}⛽ Fetching all Ampol AmpCharge & Ampol Foodary EV fast-charging sites across Australia...{C_RESET}")
                synced = p.sync_all_australia()
                print(f"{C_GREEN}✔ Successfully saved {len(synced)} AmpCharge/Ampol Foodary stations to {p.registry_path}{C_RESET}")
                return

    if args.sync_evx:
        for p in manager.providers:
            if isinstance(p, EVXProvider):
                print(f"{C_CYAN}🌐 Fetching all EVX Kerbside Polechargers across Australia...{C_RESET}")
                synced = p.sync()
                print(f"{C_GREEN}✔ Successfully saved {len(synced)} EVX stations to {p.registry_path}{C_RESET}")
                return

    if args.sync_evie:
        for p in manager.providers:
            if isinstance(p, EvieProvider):
                print(f"{C_CYAN}🌐 Fetching all Evie Networks fast charging stations across Australia...{C_RESET}")
                synced = p.sync()
                print(f"{C_GREEN}✔ Successfully saved {len(synced)} Evie stations to {p.registry_path}{C_RESET}")
                return

    if args.sync_bppulse:
        for p in manager.providers:
            if isinstance(p, BPPulseProvider):
                print(f"{C_CYAN}⚡ Fetching all bp pulse fast charging stations across Australia...{C_RESET}")
                synced = p.sync()
                print(f"{C_GREEN}✔ Successfully saved {len(synced)} bp pulse stations to {p.registry_path}{C_RESET}")
                return

    if args.sync_exploren:
        for p in manager.providers:
            if isinstance(p, ExplorenProvider):
                print(f"{C_CYAN}🔋 Fetching all Exploren AC/DC charging stations across Australia...{C_RESET}")
                synced = p.sync()
                print(f"{C_GREEN}✔ Successfully saved {len(synced)} Exploren stations to {p.registry_path}{C_RESET}")
                return

    if args.sync_jolt:
        for p in manager.providers:
            if isinstance(p, JoltProvider):
                print(f"{C_CYAN}🔵 Fetching all Jolt DC fast charging stations across Australia...{C_RESET}")
                synced = p.sync()
                print(f"{C_GREEN}✔ Successfully saved {len(synced)} Jolt stations to {p.registry_path}{C_RESET}")
                return

    if args.sync_nrma:
        for p in manager.providers:
            if isinstance(p, NrmaProvider):
                print(f"{C_CYAN}⚡ Fetching all NRMA EV Charging Network stations across Australia...{C_RESET}")
                synced = p.sync()
                print(f"{C_GREEN}✔ Successfully saved {len(synced)} NRMA stations to {p.registry_path}{C_RESET}")
                return

    if args.sync_plugshare:
        for p in manager.providers:
            if isinstance(p, PlugShareProvider):
                auto_save = args.save or args.add_all
                if args.near:
                    ref_lat, ref_lon, ref_label = resolve_reference_coords(args.near)
                    if ref_lat is not None and ref_lon is not None:
                        print(f"{C_CYAN}🌐 Fetching PlugShare Chargers near {ref_label} ({args.radius:.0f} km radius)...{C_RESET}")
                        synced = p.sync_nearby(ref_lat, ref_lon, radius_km=args.radius, limit=args.limit or 250, auto_save=auto_save)
                        print(f"{C_GREEN}✔ Discovered {len(synced)} PlugShare stations.{C_RESET}")
                        if auto_save:
                            print(f"{C_GREEN}✔ Saved to {p.registry_path}{C_RESET}")
                        stations = synced
                    else:
                        print(f"{C_RED}❌ Could not resolve reference location '{args.near}'.{C_RESET}", file=sys.stderr)
                        return
                elif args.state:
                    print(f"{C_CYAN}🌐 Fetching PlugShare Chargers for {args.state.upper()}...{C_RESET}")
                    synced = p.sync_state(args.state.upper(), auto_save=auto_save)
                    print(f"{C_GREEN}✔ Discovered {len(synced)} PlugShare stations in {args.state.upper()}.{C_RESET}")
                    if auto_save:
                        print(f"{C_GREEN}✔ Saved to {p.registry_path}{C_RESET}")
                    stations = synced
                else:
                    print(f"{C_CYAN}🌐 Fetching PlugShare Chargers across Australia...{C_RESET}")
                    synced = p.sync_australia(auto_save=auto_save)
                    print(f"{C_GREEN}✔ Discovered {len(synced)} PlugShare stations across Australia.{C_RESET}")
                    if auto_save:
                        print(f"{C_GREEN}✔ Saved to {p.registry_path}{C_RESET}")
                    stations = synced

                if not auto_save and stations:
                    render_stations_table(stations, "PLUGSHARE DISCOVERY RESULTS", eval_time=args.time)
                return

    if args.plugshare:
        for p in manager.providers:
            if isinstance(p, PlugShareProvider):
                ids = [i.strip() for i in args.plugshare.split(",") if i.strip()]
                for item_id in ids:
                    print(f"{C_CYAN}🔎 Fetching PlugShare location #{item_id}...{C_RESET}")
                    rec = p.sync_location(item_id, auto_save=args.save)
                    if rec:
                        display_station_inspection_card(rec, eval_time=args.time)
                        if args.save:
                            print(f"{C_GREEN}✔ Saved station to {p.registry_path}{C_RESET}")
                    else:
                        print(f"{C_RED}❌ Station #{item_id} not found or returned no usable data.{C_RESET}")
                return

    if args.refresh_prices:
        for p in manager.providers:
            if isinstance(p, PlugShareProvider):
                print(f"{C_CYAN}💲 Refreshing live pricing for unlisted PlugShare stations...{C_RESET}")
                refreshed, total, failed = p.refresh_prices(limit=args.limit)
                print(f"{C_GREEN}✔ Refreshed pricing for {refreshed}/{total} station(s). (Failed: {failed}){C_RESET}")
                return

    if args.from_history:
        for p in manager.providers:
            if isinstance(p, PlugShareProvider):
                print(f"{C_CYAN}🔍 Auto-discovering unregistered charging locations from charges history...{C_RESET}")
                discovered = p.discover_from_history()
                if not discovered:
                    print(f"{C_GREEN}✔ All charging locations in history are already registered.{C_RESET}")
                    return
                print(f"{C_GREEN}✔ Discovered {len(discovered)} unregistered station(s) near past charging locations.{C_RESET}")
                if args.add_all or args.save:
                    for st in discovered:
                        raw = st.views.get("plugshare", {})
                        if raw:
                            p.add_or_update(st.name, raw)
                    print(f"{C_GREEN}✔ Successfully saved {len(discovered)} station(s) to {p.registry_path}{C_RESET}")
                else:
                    render_stations_table(discovered, "DISCOVERED CHARGERS FROM HISTORY", eval_time=args.time)
                return

    if args.export_consolidated:
        out_path, total_count = manager.export_consolidated_registry()
        print(f"{C_GREEN}✔ Successfully exported {total_count} consolidated multi-view stations to:{C_RESET}")
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
        inspect_single_station(manager, args.inspect, eval_time=args.time)
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
        "state": args.state,
        "min_facilities": getattr(args, "min_facilities", None),
        "supermarket": getattr(args, "supermarket", False)
    }

 # Execute search
    stations: List[UnifiedStationRecord] = []
    title = "PUBLIC EV CHARGING STATIONS"
    ref_lat, ref_lon, ref_label = None, None, None
    active_radius = None
    route_info = None
 # Tracks the sort key actually handed to the manager for this branch, so the
 # displayed "Sort Order" label always matches the real ordering below () -
 # it must never be derived from the raw args.sort default alone.
    effective_sort = args.sort

    explicit_sort = any(a in sys.argv for a in ("--sort", "-s")) or any(a.startswith("--sort=") for a in sys.argv)
    explicit_radius = any(a in sys.argv for a in ("--radius", "-r", "--radius-km")) or any(a.startswith(("--radius=", "-r=", "--radius-km=")) for a in sys.argv)
    explicit_limit = any(a == "--limit" for a in sys.argv) or any(a.startswith("--limit=") for a in sys.argv)
    is_route_search = bool(args.from_loc and args.to_loc)
    is_state_or_all = bool(args.all or args.state)

    if bool(args.from_loc) ^ bool(args.to_loc):
        missing = "--to / --destination" if args.from_loc else "--from / --origin"
        print(f"❌ Error: Route corridor search requires both an origin and destination. Missing {missing}.", file=sys.stderr)
        sys.exit(1)
    elif args.via and not is_route_search:
        print("❌ Error: --via can only be used with --from and --to for route corridor search.", file=sys.stderr)
        sys.exit(1)

    if is_route_search:
        f_lat, f_lon, f_label = resolve_reference_coords(args.from_loc)
        if f_lat is None or f_lon is None:
            print(f"❌ Error: Could not resolve route departure origin '{args.from_loc}'.", file=sys.stderr)
            sys.exit(1)
        t_lat, t_lon, t_label = resolve_reference_coords(args.to_loc)
        if t_lat is None or t_lon is None:
            print(f"❌ Error: Could not resolve route destination '{args.to_loc}'.", file=sys.stderr)
            sys.exit(1)

        waypoints = [(f_lat, f_lon)]
        via_labels = []
        if args.via:
            for v_str in args.via:
                v_lat, v_lon, v_label = resolve_reference_coords(v_str)
                if v_lat is None or v_lon is None:
                    print(f"❌ Error: Could not resolve route intermediate waypoint '{v_str}'.", file=sys.stderr)
                    sys.exit(1)
                waypoints.append((v_lat, v_lon))
                via_labels.append(v_label)
        waypoints.append((t_lat, t_lon))

        type_lbl = "SUPERCHARGERS" if args.sc else ("DC FAST CHARGERS" if args.dc else ("AC CHARGERS" if args.ac else "CHARGING STATIONS"))
        title = f"{type_lbl} ALONG ROUTE: {f_label.upper()} ➔ {t_label.upper()}"

        if not explicit_sort and args.sort == "distance":
            effective_sort = "progress"
        else:
            effective_sort = args.sort

        if not args.json:
            print(f"🛣️  {C_CYAN}Calculating driving corridor from {f_label} to {t_label}...{C_RESET}")
        stations, route_info = manager.search_route_corridor(
            waypoints=waypoints,
            corridor_radius_km=args.corridor_radius,
            filters=filters,
            sort_by=effective_sort,
            eval_time=args.time
        )
        route_info["origin_label"] = f_label
        route_info["destination_label"] = t_label
        route_info["corridor_radius_km"] = args.corridor_radius
        route_info["via_labels"] = via_labels

        # Resolve ABRP & EV Route Intelligence
        from lib.abrp_planner import (
            load_abrp_config,
            resolve_departure_soc,
            generate_abrp_deep_link
        )
        abrp_cfg = load_abrp_config()
        dep_soc, dep_src, live_detail = resolve_departure_soc(args.departure_soc)
        tgt_soc = args.target_soc if args.target_soc is not None else float(abrp_cfg.get("target_arrival_soc", 10.0))
        max_soc = args.max_soc if args.max_soc is not None else float(abrp_cfg.get("max_charge_soc", 85.0))

        via_pts = waypoints[1:-1] if len(waypoints) > 2 else []
        abrp_url = None
        if getattr(args, "abrp_link", True):
            abrp_url = generate_abrp_deep_link(
                origin_coords=(f_lat, f_lon),
                dest_coords=(t_lat, t_lon),
                via_coords=via_pts,
                vehicle_typecode=abrp_cfg.get("vehicle_typecode"),
                departure_soc=dep_soc,
                target_soc=int(tgt_soc),
                max_soc=int(max_soc)
            )

        route_info["departure_soc"] = dep_soc
        route_info["departure_soc_src"] = dep_src
        route_info["live_detail"] = live_detail or {}
        route_info["target_arrival_soc"] = tgt_soc
        route_info["max_charge_soc"] = max_soc
        route_info["abrp_link"] = abrp_url
        route_info["consumption_wh_km"] = abrp_cfg.get("consumption_wh_km", 175.0)
        route_info["usable_kwh"] = abrp_cfg.get("usable_kwh", 85.0)
        route_info["vehicle_name"] = abrp_cfg.get("vehicle_name", "Tesla Model YL AWD")

    elif args.near or explicit_sort or not is_state_or_all:
        ref_loc = args.near or "Home"
        ref_lat, ref_lon, ref_label = resolve_reference_coords(ref_loc)

    if is_route_search:
        pass
    elif is_state_or_all:
        state_suffix = f" IN {args.state.upper()}" if args.state else " IN AUSTRALIA"
        type_lbl = "SUPERCHARGERS" if args.sc else ("DC FAST CHARGERS" if args.dc else ("AC CHARGERS" if args.ac else "CHARGING STATIONS"))
        title = f"{type_lbl}{state_suffix}"
        if not explicit_sort and not args.near and args.sort == "distance":
            effective_sort = "state"
        else:
            effective_sort = args.sort
        stations = manager.get_all_stations(
            filters=filters,
            sort_by=effective_sort,
            eval_time=args.time,
            ref_lat=ref_lat,
            ref_lon=ref_lon
        )
    elif args.query:
        title = "MATCHING CHARGING STATIONS"
        ref_label = f'Query: "{args.query}"'
        if not explicit_sort and not args.near and args.sort == "distance":
            effective_sort = "name"
        else:
            effective_sort = args.sort
        stations = manager.search_by_text(
            args.query,
            filters=filters,
            sort_by=effective_sort,
            eval_time=args.time,
            ref_lat=ref_lat,
            ref_lon=ref_lon
        )
    else:
        ref_loc = args.near or "Home"
        if ref_lat is not None and ref_lon is not None:
            # Default radius for --near search is 50km unless explicitly overridden with --radius / -r
            effective_radius = args.radius if (explicit_radius and args.radius is not None and args.radius > 0) else (args.radius or 50.0)
            active_radius = effective_radius
            type_lbl = "SUPERCHARGERS" if args.sc else ("DC FAST CHARGERS" if args.dc else ("AC CHARGERS" if args.ac else "CHARGING STATIONS"))
            title = f"{type_lbl} NEAR {ref_label.upper()}"
            effective_sort = args.sort
            stations = manager.search_nearby(
                ref_lat, ref_lon,
                radius_km=effective_radius,
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

 # Batch Facilities Sync Action
    if getattr(args, "sync_facilities", False):
        if not stations:
            print(f"❌ No stations found matching current criteria to sync facilities for.")
            return
        radius = args.facilities_radius or 250
        print(f"\n🔄 Batch syncing facilities for {len(stations)} station(s) (search radius: {radius}m)...")
        try:
            from places_manager import query_overpass_pois
            from station_facilities import get_facilities_registry
            fac_reg = get_facilities_registry()
        except ImportError:
            try:
                from lib.station_facilities import get_facilities_registry
                fac_reg = get_facilities_registry()
            except ImportError:
                fac_reg = None

        synced_count = 0
        for i, st in enumerate(stations, 1):
            if not st.lat or not st.lon:
                continue
            print(f"  [{i}/{len(stations)}] {st.name} ({st.state or ''})...", end="", flush=True)
            try:
                pois = query_overpass_pois(st.lat, st.lon, radius_m=radius)
                if fac_reg:
                    rec = fac_reg.store_station_facilities(
                        station_key=st.key,
                        station_name=st.name,
                        network=st.network or "unknown",
                        lat=st.lat,
                        lon=st.lon,
                        pois=pois,
                        radius_m=radius
                    )
                    print(f" ✔ {rec.get('badge')}")
                    synced_count += 1
                else:
                    print(f" ✔ Found {len(pois)} facilities")
            except Exception as e:
                print(f" ❌ Error: {e}")
            time.sleep(max(args.delay, 1.0))
        print(f"\n✔ Facilities sync complete: {synced_count} station records cached in station_facilities.json.")
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
 # this label can never claim a distance sort that didn't happen ().
    sort_mode_desc = None
    if effective_sort in ("progress", "route"):
        sort_mode_desc = "Route Progress (Start to Finish)"
    elif effective_sort == "detour":
        sort_mode_desc = "Detour Distance (Smallest Lateral Offset First)"
    elif effective_sort in ("score", "stop_score", "quality"):
        sort_mode_desc = "Stop Quality Score (Highest First, Facilities + Detour + Speed & Bays)"
    elif effective_sort == "distance":
        has_real_dist = any(s.distance_km is not None for s in stations)
        if has_real_dist:
            sort_mode_desc = "Distance (Closest First)"
        else:
 # No reference point/coordinates were available, so sort_records()
 # fell back to its type-then-state-then-name ordering internally.
            sort_mode_desc = "Charger Type, then State, then Station Name"
    elif effective_sort == "price":
        sort_mode_desc = "Price (Lowest First, Secondary: Route Progress)" if route_info else "Price (Lowest First)"
    elif effective_sort == "power":
        sort_mode_desc = "Max Power (kW, Highest First, Secondary: Route Progress)" if route_info else "Max Power (kW, Highest First)"
    elif effective_sort in ("stalls", "bays"):
        sort_mode_desc = "Bay Count (Highest First, Secondary: Route Progress)" if route_info else "Bay Count (Highest First)"
    elif effective_sort in ("facilities", "facility", "rating"):
        sort_mode_desc = "Facilities Quality Rating (Best Amenities First)"
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
            network_filter=args.network,
            route_info=route_info
        )

    render_cb()

 # Facilities CLI direct execution mode
    if args.facilities:
        if not stations:
            print(f"{C_YELLOW}⚠ No stations found to look up nearby facilities.{C_RESET}")
            return
        radius = args.facilities_radius or 250
        limit_fac = min(len(stations), 5 if len(stations) > 1 else 1)
        for st in stations[:limit_fac]:
            display_nearby_facilities(st, radius_m=radius)
        if len(stations) > limit_fac:
            print(f"  {C_GRAY}(Showing facilities for top {limit_fac} matching stations. Select a specific station to inspect further.){C_RESET}\n")
        if not sys.stdin.isatty():
            return

    # Interactive Station Selector Loop
    if sys.stdin.isatty():
        has_abrp = bool(route_info and route_info.get("abrp_link"))
        while True:
            num_range = f"1-{len(stations)}" if len(stations) > 1 else "1"
            print(f"\n{C_BOLD}Actions:{C_RESET}")
            print(f"  [{C_GREEN}{num_range}{C_RESET}]     Inspect station details & view nearby amenities (e.g. {C_GREEN}14{C_RESET})")
            if has_abrp:
                print(f"  [{C_GREEN}abrp{C_RESET}]    Open ABRP 1-Click Road Trip in your browser")
            print(f"  [{C_CYAN}u <#>{C_RESET}]    Update / re-verify a station live (e.g. {C_CYAN}u 14{C_RESET})")
            print(f"  [{C_BLUE}stale{C_RESET}]    Batch re-verify all stale stations in this query")
            print(f"  [{C_MAGENTA}renamed{C_RESET}]  Batch re-verify all renamed stations in this query")
            print(f"  [{C_GREEN}r{C_RESET}]        Redisplay station table")
            print(f"  [{C_GREEN}q{C_RESET}]        Quit (or press Enter)")

            prompt_parts = [num_range]
            if has_abrp:
                prompt_parts.append("abrp")
            prompt_parts.extend(["u <#>", "stale", "renamed", "r", "q"])
            prompt_str = "/".join(prompt_parts)

            try:
                choice = input(f"\n{C_BOLD}Choice [{prompt_str}]: {C_RESET}").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not choice or choice in ("q", "quit", "exit"):
                break

            if has_abrp and choice in ("abrp", "a", "roadtrip", "open-abrp"):
                abrp_url = route_info["abrp_link"]
                webbrowser.open(abrp_url)
                print(f"🌐 {C_GREEN}Opened ABRP Road Trip in browser: {abrp_url}{C_RESET}\n")
                continue

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
                        ok, diffs, _fresh = manager.reverify_station(target_st)
                        _print_reverify_result(target_st.name, ok, diffs)
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
            is_sel_tesla_sc = (
                sel.speed_tier == "supercharger"
                and ("tesla" in (sel.source or "").lower() or (sel.network or "").lower() == "tesla" or "tesla" in (sel.views or {}))
            )
            is_sel_tesla_dest = (
                ("tesla" in (sel.source or "").lower() or "tesla" in (sel.network or "").lower() or "tesla" in (sel.views or {}))
                and not is_sel_tesla_sc
            )
            sc_site = None
            if is_sel_tesla_sc:
                try:
                    from lib.supercharge_info import find_supercharge_site
                    sc_meta = sel.metadata or {}
                    loc_id = sc_meta.get("location_id") or sc_meta.get("short_name") or sel.source_id
                    sc_site = find_supercharge_site(sel.name, sel.lat, sel.lon, location_id=loc_id)
                except Exception:
                    sc_site = None

            sel_loc_provider = "Tesla" if (is_sel_tesla_sc or is_sel_tesla_dest or "tesla" in (sel.source or "").lower()) else (sel.network or "Station")

            print(f"{C_BOLD}Actions for {sel.name}:{C_RESET}")
            
            url_actions = {}
            idx = 1
            for u in source_urls:
                print(f"  [{C_GREEN}{idx}{C_RESET}]      Open {u['label']} in default browser ({format_display_url(u['url'])})")
                url_actions[str(idx)] = u["url"]
                idx += 1
                
            print(f"  [{C_GREEN}u{C_RESET}]      Update / re-verify this station live (archives historical changes)")
            print(f"  [{C_GREEN}m{C_RESET}]      Open {sel_loc_provider} location in your browser (Google Maps)")
            print(f"  [{C_GREEN}o{C_RESET}]      Open OSM location in your browser (OpenStreetMap)")
            if sc_site and sc_site.get("id"):
                print(f"  [{C_GREEN}s{C_RESET}]      Open SC.info location in your browser (supercharge.info)")
            print(f"  [{C_GREEN}f{C_RESET}]      Nearby Facilities (instant pullup; type 'f 500' for radius or 'f r' to refresh live)")
            print(f"  [{C_GREEN}b{C_RESET}]      Back to station list")
            print(f"  [{C_GREEN}q{C_RESET}]      Quit")

            prompt_parts = []
            if len(source_urls) > 1:
                prompt_parts.append(f"1-{len(source_urls)}")
            elif len(source_urls) == 1:
                prompt_parts.append("1")
            prompt_parts.extend(["u", "m", "o"])
            if sc_site and sc_site.get("id"):
                prompt_parts.append("s")
            prompt_parts.extend(["f", "b", "q"])
            prompt_options = "/".join(prompt_parts)

            try:
                act = input(f"\n{C_BOLD}Choice [{prompt_options}, default: b]: {C_RESET}").strip().lower()
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
                ok, diffs, fresh = manager.reverify_station(sel)
                _print_reverify_result(sel.name, ok, diffs)
                if ok and fresh:
                    sel = fresh
                    print()
                    display_station_inspection_card(sel, eval_time=args.time)
                input("\nPress Enter to continue...")
            elif _handle_browser_open_combo(
                act,
                gmaps_coords=(sel.lat, sel.lon),
                osm_element=("node", (sel.metadata or {}).get("osm_node_id") or (sel.metadata or {}).get("osm_id")),
                osm_coords=((sel.metadata or {}).get("osm_lat"), (sel.metadata or {}).get("osm_lon")),
                sc_id=sc_site.get("id") if sc_site else None,
                url=sel.url,
                provider_name="Station"
            ):
                pass
            elif act.startswith("f") or act in ("facilities", "nearby"):
                parts = act.split()
                radius = args.facilities_radius or 250
                force = "refresh" in act or act == "fr"
                for p in parts[1:]:
                    if p.isdigit():
                        radius = int(p)
                    elif p in ("r", "refresh", "-r", "--refresh"):
                        force = True
                display_nearby_facilities(sel, radius_m=radius, force_refresh=force)
                input("\nPress Enter to continue...")

            render_cb()


if __name__ == "__main__":
    main()
