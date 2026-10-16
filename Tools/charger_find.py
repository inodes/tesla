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
from datetime import datetime, timezone

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

from lib.tessie_api_common import shown_path
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
    NRMAProvider
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
        os.path.join(_repo_root, "resources", "places.json"),
        os.path.join(_repo_root, "resources", "places.example.json")
    ])
    cfg_path = os.path.join(_repo_root, "config.json")  # TODO-034: root is primary now
    if not os.path.isfile(cfg_path):
        cfg_path = os.path.join(_repo_root, "resources", "config.json")  # legacy fallback
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                td = cfg.get("directories", {}).get("data")
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
            os.path.join(tessie_home, "chargers", "tesla_superchargers.json"),
            os.path.join(tessie_home, "chargers", "tesla_chargers.json"),
            os.path.join(tessie_home, "chargers", "chargefox_chargers.json"),
            os.path.join(tessie_home, "chargers", "evx_chargers.json"),
            os.path.join(tessie_home, "chargers", "plugshare_chargers.json"),
            os.path.join(tessie_home, "geo_cache.json"),
        ])
    reg_candidates.extend([
        os.path.join(_repo_root, "resources", "tesla_superchargers.json"),
        os.path.join(_repo_root, "resources", "tesla_chargers.json"),
        os.path.join(_repo_root, "resources", "chargefox_chargers.json"),
        os.path.join(_repo_root, "resources", "evx_chargers.json"),
        os.path.join(_repo_root, "resources", "geo_cache.json"),
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


def _print_reverify_result(name: str, ok: bool, diffs, confirmed_not_found: bool = False) -> None:
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

    (2026-09-29): `confirmed_not_found` distinguishes an unambiguous "the
    source of truth says this doesn't exist" result (HTTP 404 from Tesla's
    own findus page or PlugShare's own API) from every other kind of
    failure - see reverify_station()'s own docstring. Callers that see
    confirmed_not_found=True are expected to follow up with an explicit
    removal prompt (this function only prints; it never prompts or
    deletes itself - see _maybe_prompt_remove_station()).
    """
    if confirmed_not_found:
        print(f"{C_RED}❌ '{name}' no longer exists at its own source (confirmed - not a network/timeout error).{C_RESET}")
        return
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


def _maybe_prompt_remove_station(mgr, station: UnifiedStationRecord) -> bool:
    """
    2026-09-29 user design requirement (prompted by BUG-156/BUG-157): "If the
    single source of truth has no entry - then the truth is the entry
    should be noted to user as false and removed with user prompt." Call
    this right after _print_reverify_result() has already told the user a
    station is confirmed gone. It asks ONE explicit y/N question and, only
    on 'y', calls ChargerDiscoveryManager.remove_station() (the only code
    path anywhere in this codebase that deletes a station). Returns True if
    the station was actually removed, so the caller knows to drop it from
    whatever in-memory list/view it's showing.
    """
    try:
        confirm = input(f"{C_YELLOW}Remove '{station.name}' from the registry now? [y/N]: {C_RESET}").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if confirm != "y":
        print(f"{C_DIM}Left untouched - '{station.name}' still in the registry.{C_RESET}")
        return False
    ok, msg = mgr.remove_station(station)
    if ok:
        print(f"{C_GREEN}✔ {msg}{C_RESET}")
    else:
        print(f"{C_RED}❌ Could not remove: {msg}{C_RESET}")
    return ok


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
    # --charger-stops (multi-leg re-planning): adds a "Dep SoC" column, shown
    # only for the chosen stops, alongside the always-populated "Arr SoC".
    multi_stop = has_route and bool(route_info and route_info.get("charger_stops"))
    show_soc = not (route_info or {}).get("no_soc")   # REQ-175 --no-soc
    multi_stop = multi_stop and show_soc
    
    max_name_len = max((display_len(s.name) for s in stations), default=28)
    w_name = max(28, min(40, max_name_len + 2))
    
    max_suburb_len = max((display_len(s.suburb or s.state or "") for s in stations), default=18)
    w_suburb = max(18, min(24, max_suburb_len + 2))

    widths = [8, 9, 7, w_name, 6, 6, 13, 12, 22]
    headers = ["   #    ", "  Type   ", " State ", "          Station Name          ", " Tier ", " Bays ", "   Access    ", " Rate (Now) ", "   Period / Window    "]
    
    # 🏪 = nearby facilities score (real cached OSM score only; "-" when not
    # checked yet) - user, 2026-09-30: "a Facilities Score in a new column -
    # just use a Facilities emoji", in both the terminal and --email.
    from lib.station_report_email import FACILITIES_ICON, station_facilities_score, facilities_level
    if has_route and multi_stop:
        widths.extend([11, 9, 10, 9, 6, 8, w_suburb])
        headers.extend([" Progress  ", " Detour  ", " Arr SoC  ", " Dep SoC  ", FACILITIES_ICON, " Score  ", " Location / Suburb "])
    elif has_route and not show_soc:
        widths.extend([11, 9, 6, 8, w_suburb])
        headers.extend([" Progress  ", " Detour  ", FACILITIES_ICON, " Score  ", " Location / Suburb "])
    elif has_route:
        widths.extend([11, 9, 10, 6, 8, w_suburb])
        headers.extend([" Progress  ", " Detour  ", " Arr SoC  ", FACILITIES_ICON, " Score  ", " Location / Suburb "])
    elif has_dist:
        widths.extend([9, 6, w_suburb])
        headers.extend(["  Dist   ", FACILITIES_ICON, " Location / Suburb "])
    else:
        widths.extend([6, w_suburb])
        headers.extend([FACILITIES_ICON, " Location / Suburb "])

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
    if has_route and multi_stop:
        aligns.extend(["right", "right", "center", "center", "center", "center", "left"])
    elif has_route:
        aligns.extend(["right", "right", "center", "center", "center", "left"])
    elif has_dist:
        aligns.extend(["right", "center", "left"])
    else:
        aligns.extend(["center", "left"])

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
        fac_val = station_facilities_score(st)
        fac_col = {"good": C_GREEN, "warn": C_YELLOW, "low": C_ORANGE, "bad": C_RED}.get(facilities_level(fac_val), "")
        fac_disp = f"{fac_col}{fac_val:.1f}{C_RESET}" if fac_val is not None else f"{C_DIM}-{C_RESET}"
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
            # Fix (2026-09-21): this used to fall back to its own invented
            # 165.0 Wh/km / 60.0 kWh pair - the 60.0 kWh figure is ~30% under
            # this vehicle's real 85.0 kWh usable capacity per vehicle_spec.json.
            # Reuse abrp_common's own DEFAULT_CONSUMPTION_WH_KM/DEFAULT_USABLE_KWH
            # (which are themselves only a last-resort fallback behind
            # config.json and vehicle_spec.json) instead of a third, different,
            # wrong pair.
            # Arrival SoC via the shared helper (also used by --email), leg-
            # aware when --charger-stops re-planned the trip.
            from lib.station_report_email import station_arrival_soc
            from lib.abrp_common import format_soc_badge
            tgt_min = route_info.get("target_arrival_soc", 10.0) if route_info else 10.0
            arr_soc = station_arrival_soc(st, route_info) if show_soc else None
            soc_badge = format_soc_badge(arr_soc, tgt_min)[0] if arr_soc is not None else "—"

            if multi_stop:
                stop_dep_soc = getattr(st, "stop_dep_soc", None)
                dep_soc_disp = f"🔌 {stop_dep_soc:.0f}%" if stop_dep_soc is not None else f"{C_DIM}-{C_RESET}"

            sc_data = getattr(st, "stop_score_data", None)
            if not sc_data:
                try:
                    from lib.route_common import compute_route_stop_score
                    c_rad = route_info.get("corridor_radius_km", 5.0) if route_info else 5.0
                    sc_data = compute_route_stop_score(st, detour_val, c_rad)
                    st.stop_score_data = sc_data
                    st.stop_score = sc_data["score"]
                except Exception:
                    sc_data = {"badge": "—"}
            score_disp = sc_data.get("badge", "—")

            route_cols = [(prog_text, "right"), (detour_text, "right")]
            if show_soc:
                route_cols.append((soc_badge, "center"))
            if multi_stop:
                route_cols.append((dep_soc_disp, "center"))
            route_cols += [(fac_disp, "center"), (score_disp, "center"), (f" {C_DIM}{suburb_disp}{C_RESET}", "left")]
            for k_, (txt_, al_) in enumerate(route_cols):
                row_cells.append(pad_display(txt_, widths[9 + k_], al_))
        elif has_dist:
            dist_val = st.distance_km
            dist_text = f"{dist_val:.1f} km " if dist_val is not None else "-- "
            row_cells.append(pad_display(dist_text, widths[9], "right"))
            row_cells.append(pad_display(fac_disp, widths[10], "center"))
            row_cells.append(pad_display(f" {C_DIM}{suburb_disp}{C_RESET}", widths[11], "left"))
        else:
            row_cells.append(pad_display(fac_disp, widths[9], "center"))
            row_cells.append(pad_display(f" {C_DIM}{suburb_disp}{C_RESET}", widths[10], "left"))

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
        print(f"\u2714 Saved {len(live_records)} Chargefox/NRMA locations to {shown_path(provider.registry_path)}")
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
        print(f"\u2714 Saved {len(live_records)} Chargefox/NRMA locations to {shown_path(provider.registry_path)}")
        return

    if pick.isdigit() and int(pick) in station_lookup:
        display_station_inspection_card(station_lookup[int(pick)])
    else:
        print(f"{C_YELLOW}Invalid selection.{C_RESET}")


def _load_raw_stations_by_key(provider) -> Dict[str, dict]:
    """
    Reads a provider's registry JSON straight off disk, keyed the same way
    each provider's own save path keys it - bypassing load_cached_registry(),
    which reconstructs UnifiedStationRecord objects and has no dataclass slot
    for the staleness fields merge_with_staleness() writes (first_seen,
    last_verified, seen_count - chargers_base.py). Handles both registry
    shapes in play: the six dict-of-dicts providers (JOLT, NRMA, Evie, EVX,
    Exploren, BP Pulse - keyed by r.key already) and AmpCharge's list-shaped
    one (keyed by its own raw LocationID/id field instead). REQ-104
    follow-on, 2026-09-23.
    """
    reg_path = getattr(provider, "registry_path", None)
    if not reg_path or not os.path.isfile(reg_path):
        return {}
    try:
        with open(reg_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}
    stations = raw.get("stations") if isinstance(raw, dict) else raw
    if isinstance(stations, dict):
        return stations
    if isinstance(stations, list):
        out: Dict[str, dict] = {}
        for item in stations:
            if isinstance(item, dict):
                rid = item.get("LocationID") or item.get("id")
                if rid is not None:
                    out[str(rid)] = item
        return out
    return {}


def _find_raw_entry(raw_by_key: Dict[str, dict], rec: UnifiedStationRecord) -> Optional[dict]:
    """Looks up rec's raw registry entry, coping with AmpCharge's bare
    LocationID keys not matching UnifiedStationRecord.key's "Ampol_<id>"
    form (and the equivalent for any future list-shaped provider)."""
    if rec.key in raw_by_key:
        return raw_by_key[rec.key]
    if rec.source_id and str(rec.source_id) in raw_by_key:
        return raw_by_key[str(rec.source_id)]
    if "_" in rec.key:
        suffix = rec.key.split("_", 1)[1]
        if suffix in raw_by_key:
            return raw_by_key[suffix]
    return None


# Matches charger_find_tesla.py's get_station_status() 90-day threshold
# exactly (AGENTS.local.md REQ-104) - the fields it reads
# (last_verified/last_updated/first_seen) are the same ones
# merge_with_staleness() now writes for every other full-list provider,
# so one classifier serves both.
_LIVE_DIFF_STALE_DAYS = 90.0


def _classify_live_entry(raw_entry: Optional[dict], rec: UnifiedStationRecord) -> str:
    """RENAMED / STALE / UP_TO_DATE / NOT_IN_JSON, mirroring Tesla's own
    get_station_status() and browse_chargefox_catalog_grid()'s name-diff
    check."""
    if raw_entry is None:
        return "NOT_IN_JSON"
    old_name = (raw_entry.get("name") or raw_entry.get("DisplayName") or "").strip().lower()
    new_name = (rec.name or "").strip().lower()
    if old_name and new_name and old_name != new_name:
        return "RENAMED"
    ts = raw_entry.get("last_verified") or raw_entry.get("last_updated") or raw_entry.get("first_seen")
    if not ts:
        return "STALE"
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    except Exception:
        return "STALE"
    return "STALE" if age_days > _LIVE_DIFF_STALE_DAYS else "UP_TO_DATE"


def _fetch_provider_live(provider) -> List[UnifiedStationRecord]:
    """Adaptive live-fetch: fetch_all_remote() where it exists (JOLT, NRMA,
    Evie, EVX, Exploren, BP Pulse), else sync_all_australia(auto_save=False)
    (AmpCharge) - never sync()/save_cached_registry() at this stage, so
    nothing is written until the user explicitly picks 'save'."""
    if hasattr(provider, "fetch_all_remote"):
        return provider.fetch_all_remote()
    if hasattr(provider, "sync_all_australia"):
        return provider.sync_all_australia(auto_save=False)
    raise AttributeError(f"{provider.name} provider has no live-fetch method (fetch_all_remote/sync_all_australia)")


def _save_provider_live(provider, records: List[UnifiedStationRecord]) -> None:
    """Adaptive save: save_cached_registry(records) where it exists
    (AmpCharge, Chargefox - saves the records already in hand); the six
    fetch_all_remote()-only providers have no way to save already-fetched
    records without a redundant re-fetch, so sync() (which re-fetches AND
    saves) is the only option there - a known, accepted inefficiency
    (AGENTS.local.md REQ-104)."""
    if hasattr(provider, "save_cached_registry"):
        provider.save_cached_registry(records)
    else:
        provider.sync()


def browse_live_diff_grid(provider, network_label: str, country: str = "Australia", auto_save: bool = False) -> None:
    """
    Generic live-diff browser for the Tier 1 full-list-scrape providers that
    don't have their own bespoke view (JOLT, NRMA, Evie, EVX, Exploren,
    BP Pulse, AmpCharge) - same state-grouped grid + status legend +
    save/inspect/skip menu UX as browse_chargefox_catalog_grid(), generalized
    to read staleness (first_seen/last_verified/seen_count,
    merge_with_staleness() in chargers_base.py) straight from the raw
    registry JSON rather than through load_cached_registry(), which drops
    those fields on reconstruction. REQ-104 follow-on, 2026-09-23 - "I'd
    love a live report for all."

    Tesla keeps its own browse_stations_by_type() (a genuinely different
    scrape shape - per-station findus pages, not one catalog fetch) and
    Chargefox keeps browse_chargefox_catalog_grid() (its existing 3-status
    view) untouched; this covers everything else with a single canonical
    live list to diff against.
    """
    from charger_find_tesla import AU_STATE_MAP

    raw_by_key = _load_raw_stations_by_key(provider)

    print(f"\n{C_CYAN}🌐 Fetching {network_label} locations across {country}...{C_RESET}")
    try:
        live_records = _fetch_provider_live(provider)
    except Exception as e:
        print(f"{C_RED}❌ Live fetch failed: {e}{C_RESET}")
        return
    if not live_records:
        print(f"{C_YELLOW}No {network_label} locations returned.{C_RESET}")
        return

    grouped: Dict[str, List[UnifiedStationRecord]] = {}
    statuses: Dict[str, str] = {}
    renamed_from: Dict[str, str] = {}
    for rec in live_records:
        raw_entry = _find_raw_entry(raw_by_key, rec)
        status = _classify_live_entry(raw_entry, rec)
        statuses[rec.key] = status
        if status == "RENAMED" and raw_entry:
            renamed_from[rec.key] = raw_entry.get("name") or raw_entry.get("DisplayName") or "?"
        state_key = rec.state or "-"
        grouped.setdefault(state_key, []).append(rec)

    counts = {"UP_TO_DATE": 0, "RENAMED": 0, "STALE": 0, "NOT_IN_JSON": 0}
    for s in statuses.values():
        counts[s] += 1

    STATUS_COLOR = {"UP_TO_DATE": C_GREEN, "RENAMED": C_MAGENTA, "STALE": C_YELLOW, "NOT_IN_JSON": C_ORANGE}

    print(f"\n{C_BOLD}{'='*80}{C_RESET}")
    print(f"  ⚡ {C_BOLD}{network_label.upper()} STATIONS ({len(live_records)} total){C_RESET}")
    print(f"  {C_BOLD}📊 Status Breakdown:{C_RESET} "
          f"[{C_GREEN} Up to Date: {counts['UP_TO_DATE']} {C_RESET}] | "
          f"[{C_MAGENTA} Renamed: {counts['RENAMED']} {C_RESET}] | "
          f"[{C_YELLOW} Stale (>{int(_LIVE_DIFF_STALE_DAYS)}d): {counts['STALE']} {C_RESET}] | "
          f"[{C_ORANGE} Not in JSON: {counts['NOT_IN_JSON']} {C_RESET}]")
    print(f"{C_BOLD}{'='*80}{C_RESET}\n")

    def fmt_entry(rec: UnifiedStationRecord, counter: int) -> str:
        status = statuses[rec.key]
        color = STATUS_COLOR[status]
        title_str = rec.name
        if status == "RENAMED":
            title_str += f" {C_MAGENTA}🔄 (in JSON: {renamed_from[rec.key]}){C_RESET}"
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
        _save_provider_live(provider, live_records)
        print(f"✔ Saved {len(live_records)} {network_label} locations to {shown_path(provider.registry_path)}")
        return

    print(f"{C_BOLD}Options:{C_RESET}")
    print(f"  • Enter a station number [1-{len(live_records)}] to inspect details")
    print(f"  • Type {C_GREEN}'save'{C_RESET} to write all {len(live_records)} freshly-fetched locations into the registry (archives prior data)")
    print(f"  • Press {C_YELLOW}Enter{C_RESET} or Ctrl+C to skip without saving")
    print()

    try:
        pick = input("Select option (number, 'save', or Enter to skip): ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nExiting.")
        return

    if not pick:
        return

    if pick.lower() in ("save", "s", "y", "yes"):
        _save_provider_live(provider, live_records)
        print(f"✔ Saved {len(live_records)} {network_label} locations to {shown_path(provider.registry_path)}")
        return

    if pick.isdigit() and int(pick) in station_lookup:
        display_station_inspection_card(station_lookup[int(pick)])
    else:
        print(f"{C_YELLOW}Invalid selection.{C_RESET}")


def plugshare_local_staleness_report(provider) -> None:
    """
    PlugShare has no single canonical upstream list to fetch-and-diff (it's
    a moving-viewport aggregator, not a full-list scrape - REQ-104), so
    unlike the other providers' --live, this never hits the network: it
    reads the already-scanned local registry (plugshare_chargers.json - a
    flat {key: record} dict, not the {"stations": ...}-wrapped shape the
    full-list providers use) and reports how stale each entry's own
    first_seen/last_verified/seen_count is, so the user can decide which
    ones are overdue for scan_country_priority() to revisit or a manual
    get_location_details() check. Threshold matches
    run_priority_box_scan()'s own stale_after_days default (30) rather than
    Tesla's 90 - PlugShare entries age faster since nothing here re-fetches
    them automatically the way a full-list sync() does.
    """
    reg_path = getattr(provider, "registry_path", None)
    if not reg_path or not os.path.isfile(reg_path):
        print(f"{C_YELLOW}No local PlugShare registry found at {reg_path}.{C_RESET}")
        return
    try:
        with open(reg_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        print(f"{C_RED}❌ Failed to read {reg_path}: {e}{C_RESET}")
        return
    if not isinstance(raw, dict):
        print(f"{C_YELLOW}Unexpected PlugShare registry shape.{C_RESET}")
        return

    stale_after_days = 30.0
    now = datetime.now(timezone.utc)
    rows = []
    fresh_count = 0
    stale_count = 0
    never_count = 0
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        name = (entry.get("plugshare_metadata") or {}).get("name") or key
        state = (entry.get("location") or {}).get("state") or "-"
        ts = entry.get("last_verified") or entry.get("last_updated") or entry.get("first_seen")
        seen_count = entry.get("seen_count", 0)
        if not ts:
            never_count += 1
            rows.append((float("inf"), name, state, None, seen_count))
            continue
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_days = (now - dt).total_seconds() / 86400.0
        except Exception:
            never_count += 1
            rows.append((float("inf"), name, state, None, seen_count))
            continue
        if age_days > stale_after_days:
            stale_count += 1
        else:
            fresh_count += 1
        rows.append((age_days, name, state, age_days, seen_count))

    print(f"\n{C_BOLD}{'='*80}{C_RESET}")
    print(f"  🕊️ {C_BOLD}PLUGSHARE LOCAL STALENESS REPORT ({len(raw)} total entries){C_RESET}")
    print(f"  {C_BOLD}📊 Status Breakdown:{C_RESET} "
          f"[{C_GREEN} Fresh (<={int(stale_after_days)}d): {fresh_count} {C_RESET}] | "
          f"[{C_YELLOW} Stale (>{int(stale_after_days)}d): {stale_count} {C_RESET}] | "
          f"[{C_ORANGE} Never verified: {never_count} {C_RESET}]")
    print(f"{C_BOLD}{'='*80}{C_RESET}\n")
    print(f"{C_DIM}No live fetch here - PlugShare has no single canonical list to diff against; this is purely local. Use scan_country_priority() to actually refresh coverage.{C_RESET}\n")

    rows.sort(key=lambda r: -r[0])
    print(f"{C_BOLD}Stalest entries:{C_RESET}")
    for age_days, name, state, raw_age, seen_count in rows[:30]:
        if raw_age is None:
            age_str = f"{C_ORANGE}never verified{C_RESET}"
        else:
            color = C_YELLOW if raw_age > stale_after_days else C_GREEN
            age_str = f"{color}{raw_age:.0f}d{C_RESET}"
        print(f"  [{state:>3}] {name} - {age_str} (seen {seen_count}x)")
    print()


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
        reg_name = "chargers/tesla_superchargers.json" if station.speed_tier == "supercharger" else "chargers/tesla_chargers.json"
 # Matches charger_find_tesla.py's own display_preview() coloring
 # (green = trusted local registry) - this line lost its color when
 # the display logic was ported into the provider system. Fixed 2026-09-14.
        _field("📂", "Data Source", f"{C_GREEN}Local JSON Registry ({reg_name}){C_RESET}")
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
    
    # BUG-135 (2026-09-22 follow-up): originally just flipped this to prefer
    # general_location over suburb outright - user pushed back: they want
    # BOTH shown, same as Find Us List/Location Name just above. Tesla's own
    # provider parser (chargers_tesla.py) sets metadata.general_location to
    # the real named venue when Tesla's data has one (e.g. "Cassegrain Wines"
    # for the Port Macquarie Supercharger, sited in its car park), falling
    # back to the bare suburb only when Tesla's data has nothing more
    # specific - so general_location is NEVER worse than suburb, and when it
    # differs it's the more useful, more specific description. Only Tesla's
    # provider sets general_location at all (other networks leave it unset),
    # so the "differs" branch below is Tesla-only; everything else still
    # gets the same single Suburb line as before.
    suburb_val = (station.suburb or "").strip()
    general_loc = ((meta.get("general_location") if meta else "") or "").strip()
    if general_loc and suburb_val and general_loc.lower() != suburb_val.lower():
        _field("🏢", "General Location", general_loc)
        _field("🏙️", "Suburb / Place", suburb_val)
    elif general_loc or suburb_val:
        _field("🏢", "General Location", general_loc or suburb_val)

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
                from lib.supercharge_common import find_supercharge_site
                loc_id = meta.get("location_id") or meta.get("short_name") or station.source_id
                sc_site = find_supercharge_site(station.name, station.lat, station.lon, location_id=loc_id)
            except Exception:
                sc_site = None

        # Auto-populate and auto-cache OSM node and physical GPS if available from supercharge.info
        # BUG-152 (2026-09-22): find_supercharge_site() can return the closest
        # PLAUSIBLE site it has, which is not always THIS station - two distinct
        # Tesla superchargers can sit well under 1km apart (Coffs Harbour, NSW's
        # old Park Beach Plaza site and its new Pacific Hwy/Big Banana replacement
        # are ~787m apart), so this block was silently writing one station's
        # verified GPS into the OTHER station's registry entry on nothing more
        # than a plain --inspect call - no [a] audit, no confirmation. A sanity
        # cap here (same idea as station_facilities_common.py's REFINEMENT_SANITY_CAP_M)
        # means a too-far match is just not trusted/cached, rather than accepted
        # as ground truth - "no confident OSM data yet" beats "confidently wrong".
        _SC_AUTOCACHE_MAX_OFFSET_M = 300.0
        if is_tesla_sc and sc_site:
            sc_osm_id = sc_site.get("osmId")
            sc_gps = sc_site.get("gps", {})
            sc_lat_val = sc_gps.get("latitude")
            sc_lon_val = sc_gps.get("longitude")
            sc_offset_ok = True
            if sc_lat_val is not None and sc_lon_val is not None and station.lat is not None and station.lon is not None:
                try:
                    from lib.geo_common import haversine_distance_km as _hkm
                    sc_offset_ok = (_hkm(station.lat, station.lon, float(sc_lat_val), float(sc_lon_val)) * 1000.0) <= _SC_AUTOCACHE_MAX_OFFSET_M
                except Exception:
                    sc_offset_ok = True
            if sc_osm_id and sc_offset_ok:
                if not meta.get("osm_node_id"):
                    meta["osm_node_id"] = sc_osm_id
                if osm_lat is None and sc_lat_val is not None and sc_lon_val is not None:
                    osm_lat = sc_lat_val
                    osm_lon = sc_lon_val
                    meta["osm_lat"] = osm_lat
                    meta["osm_lon"] = osm_lon
                    try:
                        from lib.tessie_api_common import save_audited_osm_to_tesla_registry
                        save_audited_osm_to_tesla_registry(station.name, float(osm_lat), float(osm_lon), int(sc_osm_id), is_destination=False)
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
                from lib.route_common import compute_route_stop_score
                sc_data = compute_route_stop_score(station, station.detour_km)
            except Exception:
                pass
        if sc_data:
            _ps_part = f", PlugShare: {sc_data['plugshare_score']:.1f}" if sc_data.get('plugshare_score') is not None else ""
            _field("⭐", "Stop Quality Score", f"{sc_data['badge']}/10.0 (Facilities: {sc_data['facilities_score']:.1f}, Detour: {sc_data['detour_score']:.1f}, Speed & Bays: {sc_data['speed_score']:.1f}{_ps_part})")

        # Arrival SoC Projection
        try:
            from lib.abrp_common import load_abrp_config, estimate_arrival_soc, format_soc_badge, resolve_departure_soc
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
        _field("🕒", "Effective Since Date", f"{format_local_timestamp(station.effective_date_utc)} (Verified: {format_local_timestamp(station.last_verified_utc)})")
    elif station.last_verified_utc:
        _field("🕒", "Last Verified", format_local_timestamp(station.last_verified_utc))
    elif station.effective_date_utc:
        _field("🕒", "Effective Since Date", format_local_timestamp(station.effective_date_utc))

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

 # Tesla Busy Times (REQ-174) - from the Find Us page's own chart data
    try:
        from lib.tesla_busy_times import station_busy_profile, busy_summary, WEEKDAYS, WEEKENDS
        _prof = station_busy_profile(station)
    except Exception:
        _prof = None
    if _prof:
        _as_of = str(_prof.get("profile_date") or _prof.get("fetched") or "")[:10]
        print(f"\n  🚦 {C_BOLD}Busy Times{C_RESET} {C_DIM}(Tesla, as of {_as_of}; 4am → 3am, full bar = all stalls in use){C_RESET}")
        for _lbl, _wds in (("Weekdays", WEEKDAYS), ("Weekends", WEEKENDS)):
            _sm = busy_summary(_prof, _wds)
            _col = C_YELLOW if _sm["level"] == "some" else C_GREEN
            print(f"     {_lbl:<9} {_col}{_sm['spark']}{C_RESET}  {_sm['text']}")

 # Cached Facilities Quality Rating / Summary (REQ-082)
    try:
        from station_facilities_common import get_facilities_registry, format_facilities_summary_card
        _fac_reg = get_facilities_registry()
    except ImportError:
        try:
            from lib.station_facilities_common import get_facilities_registry, format_facilities_summary_card
            _fac_reg = get_facilities_registry()
        except ImportError:
            _fac_reg = None
            format_facilities_summary_card = None

    if _fac_reg and station.lat and station.lon:
        try:
            from lib.station_facilities_common import resolve_station_physical_coords
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
    # BUG-153 (2026-09-22): is_tesla_dest was referenced below but never
    # defined in this function (only display_station_inspection_card()
    # computed it) - crashed with NameError on every single --inspect
    # of a Tesla Destination Charger (is_tesla_sc is False for those, so
    # the `or is_tesla_dest` short-circuit below was actually reached).
    # Same definition as display_station_inspection_card() uses.
    is_tesla_dest = (
        ("tesla" in (station.source or "").lower() or "tesla" in (station.network or "").lower() or "tesla" in (station.views or {}))
        and not is_tesla_sc
    )
    sc_site = None
    if is_tesla_sc:
        try:
            from lib.supercharge_common import find_supercharge_site
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
 # BUG (found 2026-09-29, drive-by while wiring the confirmed-not-found
 # removal flow): ChargerDiscoveryManager has no load_all_providers()
 # method at all - __init__ already loads every provider unconditionally
 # - so this call has ALWAYS raised AttributeError, silently caught below
 # and printed as a generic "Error reverifying station" every single time
 # [u] was pressed from THIS specific menu (this station-inspection card's
 # own re-verify handler, as opposed to the other [u] handlers elsewhere
 # in this file that construct their manager without this extra call and
 # therefore never hit it). Removed - nothing else here needs it.
                mgr = ChargerDiscoveryManager()
                print(f"🔄 Re-verifying station '{station.name}' live...")
                ok, diffs, fresh, confirmed_not_found = mgr.reverify_station(station)
                _print_reverify_result(station.name, ok, diffs, confirmed_not_found)
                if confirmed_not_found:
                    if _maybe_prompt_remove_station(mgr, station):
                        print()
                        break
                elif ok and fresh:
                    station = fresh
                    print()
                    display_station_inspection_card(station)
            except Exception as e:
                print(f"{C_RED}❌ Error reverifying station: {e}{C_RESET}")
            print()
        elif choice.startswith("f") or choice in ("facilities", "nearby"):
            parts = choice.split()
            radius = 500
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
                # BUG-152 (2026-09-22): pass the already-selected station object
                # directly instead of re-deriving it by name string - two real
                # stations can share a name/key that's a prefix of the other
                # (e.g. "Coffs Harbour, NSW" vs "Coffs Harbour, NSW - Pacific
                # Hwy"), which made the name-based re-search inside
                # _audit_tesla_station ambiguous for no reason, since this
                # code already knows exactly which station it means.
                _audit_tesla_station(query=station.key or station.name, token=cfg.get("access_token"), preselected_station=station)
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


def sync_busy_times_for_stations(stations: list, delay_s: float = 1.0) -> dict:
    """REQ-174: fetch Tesla's Busy Times chart data for every Tesla
    Supercharger in `stations` from its Find Us page (one WebKit session),
    saved to <data>/chargers/tesla_busy_times.json. The Supercharger
    registry itself is not touched."""
    tesla_sc = [s for s in stations if s.speed_tier == "supercharger" and "tesla" in (s.source or "").lower()
                and (s.source_id or (s.metadata or {}).get("findus_id"))]
    print(f"\n🚦 Fetching Tesla Busy Times for {len(tesla_sc)} Supercharger(s)...")
    if not tesla_sc:
        return {"saved": 0, "failed": 0}
    archive_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "archive")
    if archive_dir not in sys.path:
        sys.path.insert(0, archive_dir)
    from charger_find_tesla import TeslaChargerExplorer
    from lib.tesla_busy_times import extract_busy_profile, save_busy_profile, busy_summary
    import lib.tesla_busy_times as _busy_mod
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(f"{C_RED}❌ Playwright isn't installed in this Python environment.{C_RESET}")
        return {"saved": 0, "failed": len(tesla_sc)}
    explorer = TeslaChargerExplorer(headless=True)
    saved, failed = 0, []
    with sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        context = browser.new_context(
            locale="en-AU", timezone_id="Australia/Sydney",
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15")
        page = context.new_page()
        for n, st in enumerate(tesla_sc, 1):
            fid = str(st.source_id or (st.metadata or {}).get("findus_id"))
            url = f"https://www.tesla.com/en_AU/findus/location/supercharger/{fid}"
            print(f"  [{n}/{len(tesla_sc)}] {st.name}... ", end="", flush=True)
            try:
                _api, next_data = explorer._scrape_page_payload(page, url)
                prof = extract_busy_profile(next_data)
            except Exception as e:
                prof = None
                print(f"{C_RED}error: {str(e)[:60]}{C_RESET}")
            if prof and save_busy_profile(fid, st.name, prof):
                saved += 1
                print(f"{C_GREEN}✔{C_RESET} {busy_summary(prof)['text']}")
            else:
                failed.append(st.name)
                if prof is None:
                    print(f"{C_YELLOW}no busy-times data on the page{C_RESET}")
            if n < len(tesla_sc):
                time.sleep(delay_s)
        browser.close()
    _busy_mod._CACHE = None   # so the list below reads the fresh file
    print(f"✔ Busy Times saved for {saved} station(s)" + (f", {len(failed)} without data" if failed else "") + ".")
    return {"saved": saved, "failed": len(failed)}


def sync_facilities_for_stations(stations: list, radius_m: int = 500, delay_s: float = 1.0) -> dict:
    """Batch-refreshes the nearby-facilities cache for a list of stations,
    skipping any already fresh (per station_facilities_common.py's TTL/schema-cutoff
    checks), never caching an ambiguous empty result (BUG-149), and retrying
    once at the end of the run for anything that came back empty or errored
    the first time (BUG-150). Shared by the `--sync-facilities` CLI flag and
    the interactive station-list menu's `facilities` command, so both get the
    same resumable, rate-limit-aware behaviour instead of two copies drifting
    apart.

    Returns {"synced": int, "skip_fresh": int, "still_failed": int,
    "still_failed_names": [str, ...], "total_candidates": int}.
    """
    try:
        from places_manager import query_overpass_pois
        try:
            from lib.station_facilities_common import get_facilities_registry, resolve_station_physical_coords
        except ImportError:
            from station_facilities_common import get_facilities_registry, resolve_station_physical_coords
        fac_reg = get_facilities_registry()
    except ImportError:
        fac_reg = None
        resolve_station_physical_coords = None

 # BUG-149: this batch sync used to (a) always re-query every matching
 # station even if its cache was already fresh - wasteful and makes a
 # big run un-resumable - and (b) call store_station_facilities() with
 # whatever query_overpass_pois() returned, INCLUDING an empty list.
 # query_overpass_pois() collapses "confirmed zero POIs nearby" and
 # "every Overpass mirror failed/rate-limited" into the same `[]` return
 # (see its own docstring / BUG-146's notes on mirrors rate-limiting
 # under repeated testing) - the interactive [f] pullup already knows
 # this and deliberately skips caching an empty result for exactly this
 # reason, but this batch path didn't, so a rate-limited run could
 # silently write false "Sparse 0/10, no facilities" records that then
 # look like confirmed ground truth for days. Also used raw st.lat/lon
 # instead of resolve_station_physical_coords() (OSM-aligned when
 # available), unlike every other facilities call site - Harwood's own
 # Tesla GPS is ~18m off from its OSM-aligned position, so this could
 # cache a station's most notable facility just far enough that others
 # queried later actually returned different results.
    skip_fresh = 0
    candidates = []
    for st in stations:
        if not st.lat or not st.lon:
            continue
        if fac_reg:
            phys_lat, phys_lon, _coord_src = (
                resolve_station_physical_coords(st) if resolve_station_physical_coords else (st.lat, st.lon, "Source GPS")
            )
            existing = fac_reg.get_station_facilities(station_key=st.key, station_name=st.name, lat=phys_lat, lon=phys_lon)
 # REQ-109: a cached entry captured at a SMALLER radius than what this
 # run is asking for can't answer the wider question (Overpass was
 # never asked about anything past its own old radius_m) - treat it as
 # not-fresh so a code-level radius bump actually reaches already-cached
 # stations via a batch --sync-facilities run, mirroring the same check
 # already applied to the interactive single-station [f] pullup path.
            if existing is not None and existing.get("radius_m", 0) >= radius_m:
                skip_fresh += 1
                continue
            candidates.append((st, phys_lat, phys_lon))
        else:
            candidates.append((st, st.lat, st.lon))

    print(f"\n🔄 Batch syncing facilities for {len(candidates)} station(s) needing a refresh (search radius: {radius_m}m; {skip_fresh} already fresh, skipped)...")
    if not candidates:
        print("✔ Nothing to do - every matching station's facilities cache is already current.")
        return {"synced": 0, "skip_fresh": skip_fresh, "still_failed": 0, "still_failed_names": [], "total_candidates": 0}

    def _sync_one(st, plat, plon):
        """Attempts a single station's facilities refresh; prints the
        outcome. Returns True if a real result was cached, False if this
        station should be retried (empty result or a request error - both
        ambiguous per BUG-149: could be a genuinely quiet vicinity or a
        rate-limited/failed Overpass mirror, and we can't tell which from
        here)."""
        try:
            pois = query_overpass_pois(plat, plon, radius_m=radius_m)
        except Exception as e:
            print(f" ❌ Error: {e}")
            return False
        if not pois:
            print(" ⚠ No result (genuinely empty, or every Overpass mirror failed)")
            return False
        if fac_reg:
            rec = fac_reg.store_station_facilities(
                station_key=st.key,
                station_name=st.name,
                network=st.network or "unknown",
                lat=plat,
                lon=plon,
                pois=pois,
                radius_m=radius_m
            )
            print(f" ✔ {rec.get('badge')}")
        else:
            print(f" ✔ Found {len(pois)} facilities")
        return True

    synced_count = 0
    retry_list = []
    for i, (st, plat, plon) in enumerate(candidates, 1):
        print(f"  [{i}/{len(candidates)}] {st.name} ({st.state or ''})...", end="", flush=True)
        if _sync_one(st, plat, plon):
            synced_count += 1
        else:
            retry_list.append((st, plat, plon))
        time.sleep(max(delay_s, 1.0))

 # BUG-150 (user request): retry stations that came back empty/errored once
 # at the end of THIS SAME run, rather than only ever picking them up on a
 # separate future invocation. By the time this retry pass starts, every
 # other station in the batch has already had its own turn - which alone
 # gives a rate-limited mirror a real gap to recover before we ask it again.
    still_failed = []
    if retry_list:
        print(f"\n🔁 Retrying {len(retry_list)} station(s) with no result from this run...")
        for i, (st, plat, plon) in enumerate(retry_list, 1):
            print(f"  [retry {i}/{len(retry_list)}] {st.name} ({st.state or ''})...", end="", flush=True)
            if _sync_one(st, plat, plon):
                synced_count += 1
            else:
                still_failed.append(st.name)
            time.sleep(max(delay_s, 1.0))

    print(f"\n✔ Facilities sync complete: {synced_count} cached, {len(still_failed)} still empty/failed after retry (will retry next run), {skip_fresh} already fresh.")
    if still_failed:
        print(f"   Still failed: {', '.join(still_failed)}")
    return {
        "synced": synced_count,
        "skip_fresh": skip_fresh,
        "still_failed": len(still_failed),
        "still_failed_names": still_failed,
        "total_candidates": len(candidates),
    }


def display_nearby_facilities(station: UnifiedStationRecord, radius_m: int = 500, force_refresh: bool = False) -> None:
    """
    Looks up real, named nearby facilities (shops, food, amenities, services)
    around a charging station via OpenStreetMap's free/keyless Overpass API -
 # reusing Tools/places_manager.py's already-proven query_overpass_pois(),
    the same lookup that already identifies drive-log stop locations.

    Persisted in station_facilities.json for instant offline pullup with
    a 0-10 Quality Scoring Engine (REQ-082).
    """
    try:
        from lib.station_facilities_common import resolve_station_physical_coords
        phys_lat, phys_lon, coord_src = resolve_station_physical_coords(station)
    except Exception:
        phys_lat, phys_lon, coord_src = station.lat, station.lon, "Source GPS"

    if not phys_lat or not phys_lon:
        print(f"{C_YELLOW}⚠ No coordinates on file for this station - can't look up nearby facilities.{C_RESET}")
        return

    try:
        from station_facilities_common import get_facilities_registry
        reg = get_facilities_registry()
    except ImportError:
        try:
            from lib.station_facilities_common import get_facilities_registry
            reg = get_facilities_registry()
        except ImportError:
            reg = None

    cached = reg.get_station_facilities(station_key=station.key, station_name=station.name, lat=phys_lat, lon=phys_lon) if (reg and not force_refresh) else None
    if cached and cached.get("radius_m", 0) < radius_m:
        print(f"{C_GRAY}  (cached facilities were captured at {cached.get('radius_m')}m - re-querying live for the requested {radius_m}m){C_RESET}")
        cached = None

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
    ./Tools/charger_find.py --sync-tesla --sc          # compare tesla.com's list, fetch only new/renamed/moved
    ./Tools/charger_find.py --sync-tesla --sc --stale  # re-check only entries not verified for 90+ days
    ./Tools/charger_find.py --sync-tesla --all         # re-fetch every listed station

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
    g_route.add_argument("--corridor-radius", "--corridor", "--detour-max", type=float, default=None, help="Corridor radius / max highway detour in km (default: 5.0 km, or config.json's abrp.corridor_radius_km if set; -r/--radius also sets it on a route)")

    g_abrp = parser.add_argument_group("ABRP & EV Trip Planning")
    g_abrp.add_argument("--soc", "--departure-soc", dest="departure_soc", type=str, help="Departure battery percentage (0-100 or 'live' to query Tessie real-time)")
    g_abrp.add_argument("--no-soc", dest="no_soc", action="store_true", help="Route list with no battery figures: no departure/arrival SoC, planned stops or ABRP link, and no Tessie call")
    g_abrp.add_argument("--target-soc", "--min-soc", dest="target_soc", type=float, help="Target minimum arrival battery percentage (default: config.json's abrp.target_arrival_soc, else 10.0)")
    g_abrp.add_argument("--max-soc", dest="max_soc", type=float, help="Maximum charging target percentage (default: config.json's abrp.max_charge_soc, else 85.0)")
    g_abrp.add_argument("--charger-stops", dest="charger_stops", action="append", metavar="NAME", help="Chosen enroute charging stop, matched by station name in THIS query's results (repeatable, e.g. --charger-stops \"Heatherbrae, NSW\" --charger-stops \"Coffs Harbour, NSW\") - re-plans Arr/Dep SoC leg-by-leg through these stops instead of one non-stop estimate from departure")
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
    # (2026-09-23, AGENTS.local.md REQ-106) --tesla-sc/--tesla-dc are
    # PRODUCT-LINE shortcuts - literally "which file did this station come
    # from" (tesla_superchargers.json vs tesla_chargers.json) - distinct
    # from --network tesla --sc/--dc/--ac, which filter by MEASURED
    # hardware tier instead. Both imply --network tesla automatically.
    g_speed.add_argument("--tesla-sc", action="store_true", help="Tesla Superchargers only, straight from tesla_superchargers.json - implies --network tesla. Equivalent to --network tesla --sc (Superchargers are hardware-homogeneous, so there's no measured-tier ambiguity here).")
    g_speed.add_argument("--tesla-dc", action="store_true", help="Tesla Destination Chargers only, straight from tesla_chargers.json, REGARDLESS of measured AC/DC hardware tier - implies --network tesla. NOT the same as --network tesla --dc, which filters by measured power (>= 25 kW) instead of by source file.")

    g_network = parser.add_argument_group("Network Filter")
    g_network.add_argument("--network", "-n", type=str, help="Filter by network - free-text substring match against network/operator/station-name/source (case-insensitive). Known first-party networks: tesla, chargefox (covers WA EV Network, Qld Electric Highway sub-brands), nrma (own Tier 1 KML feed, merged with Chargefox's ~28 overlapping NRMA sites where both cover the same site), evx, evie, bppulse, ampcharge (covers Ampol Foodary), exploren, jolt. Use 'plugshare' to see only PlugShare-sourced listings, or any other text to match a PlugShare-aggregated sub-brand.")

    g_sort = parser.add_argument_group("Sorting & Time of Use")
    g_sort.add_argument("--sort", "-s", choices=["distance", "price", "power", "stalls", "bays", "name", "state", "facilities", "facility", "rating", "progress", "route", "detour", "score", "stop_score", "quality"], default="distance", help="Sort order (default: distance; or progress/detour/score along a route)")
    g_sort.add_argument("-t", "--time", type=str, help="Evaluate TOU pricing at specific local time (HH:MM)")

    g_output = parser.add_argument_group("Output Controls")
    g_output.add_argument("--limit", type=int, default=100, help="Maximum number of stations to display (default: 100)")
    g_output.add_argument("--json", action="store_true", help="Output results in JSON format")
    g_output.add_argument("--email", action="store_true", help="Also copy the results to the clipboard, formatted for an email (trip summary + table)")
    g_output.add_argument("--share", action="store_true", help="Email version for someone else (implies --email): no battery, arrival or planned-stop details, non-Tesla prices, Tesla-only stations marked to avoid")

    g_sync = parser.add_argument_group("Synchronization")
    g_sync.add_argument("--sync-all", action="store_true", help="End-to-end sync across all providers (Tesla, Chargefox, EVX, Evie, bp pulse, PlugShare, AmpCharge, Exploren, Jolt, NRMA, Master Catalog)")
    g_sync.add_argument("--sync-tesla", action="store_true", help="Compare tesla.com's station list with the registry and fetch only the new, renamed or moved ones (--stale, --unpriced, --all widen it)")
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
    g_facilities.add_argument("--facilities-radius", "--radius-m", type=int, default=500, help="Search radius in meters for nearby facilities (default: 500)")
    g_facilities.add_argument("--min-facilities", "--facilities-min", choices=["excellent", "good", "basic", "sparse"], help="Filter stations meeting minimum facility quality rating tier")
    g_facilities.add_argument("--supermarket", "--groceries", action="store_true", help="Filter stations located at or near a major supermarket (Woolworths, Coles, Aldi, IGA, Costco, etc.)")
    g_facilities.add_argument("--sync-facilities", action="store_true", help="Look up nearby facilities for the matching stations that need it, then show the list as usual (works with --email)")
    g_facilities.add_argument("--sync-busy-times", action="store_true", help="Fetch Tesla's Busy Times (congestion by hour) for the matching Superchargers from their Find Us pages, then show the list as usual")
    g_facilities.add_argument("--reclassify-facilities", action="store_true", help="Instantly re-run facility categorization on every cached record that has raw OSM tags saved, no network call - use after a classify_poi_group() logic change instead of --sync-facilities")

    g_registry = parser.add_argument_group("Registry Filters & Batch Updates")
    g_registry.add_argument("--plugshare", "--inspect-plugshare", type=str, help="PlugShare location ID(s) to inspect / scrape live (comma-separated)")
    g_registry.add_argument("--refresh-prices", action="store_true", help="Fetch live PlugShare details for stations missing pricing and update the registry")
    g_registry.add_argument("--from-history", action="store_true", help="Auto-discover unregistered charging locations from charges_master.csv")
    g_registry.add_argument("--save", "--update", action="store_true", help="Save / update inspected stations into local registry")
    g_registry.add_argument("--add-all", action="store_true", help="Auto-add all discovered stations to local registry")
    g_registry.add_argument("--status", choices=["UP_TO_DATE", "RENAMED", "STALE", "NOT_IN_JSON", "uptodate", "up_to_date", "up-to-date", "renamed", "stale", "new", "not_in_json"], help="Filter by registry status")
    g_registry.add_argument("--update-stale", action="store_true", help="Batch re-verify and update all stale stations across providers (archives changes)")
    g_registry.add_argument("--update-renamed", action="store_true", help="Batch re-verify and reconcile all renamed stations across providers (archives changes)")
    g_registry.add_argument("--update-all", "--refresh-all", action="store_true", help="Batch re-verify EVERY matching station live from its source (Tesla, PlugShare, Chargefox, ...), regardless of current status - not just STALE/RENAMED (archives changes)")
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
    # (2026-09-23, AGENTS.local.md REQ-106) User: "To cover all bases...
    # --tesla-sc = Tesla Superchargers (automatically assumes --network
    # tesla) - only tesla_superchargers.json ... --tesla-dc = Tesla
    # Destination Chargers (automatically assumes --network tesla) - only
    # tesla_chargers.json". Force --network to "tesla" here so every
    # downstream check (the fuzzy-match block right below, the
    # --live-requires--network gate, the provider fetch itself) sees it
    # without the caller having to pass both.
    if args.tesla_sc and args.tesla_dc:
        parser.error("--tesla-sc and --tesla-dc are mutually exclusive - a station cannot be in both tesla_superchargers.json and tesla_chargers.json.")
    if (args.tesla_sc or args.tesla_dc) and args.network and args.network.strip().lower() != "tesla":
        parser.error(f"--tesla-sc/--tesla-dc already imply --network tesla - drop the conflicting --network {args.network!r}.")
    if args.tesla_sc or args.tesla_dc:
        args.network = "tesla"

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

    # (2026-09-23, AGENTS.local.md REQ-104 follow-on): --live used to
    # silently fall through to a generic "not available" warning for
    # anything other than Tesla/--sc or Chargefox, which made sense when
    # only those two had a live-diff view. Now that JOLT, NRMA, Evie, EVX,
    # Exploren, BP Pulse, AmpCharge and PlugShare all route through --live
    # too, which network's live catalog to browse is no longer optional -
    # the user asked for this explicitly: "--live should require the
    # network."
    if args.live and not args.network and not args.sc:
        parser.error("--live requires --network <name> (or --sc). Known: tesla, chargefox, nrma, evie, evx, bppulse, ampcharge, exploren, jolt, plugshare.")

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
                    print(f"  {C_GREEN}✔ Cached {len(synced_cf)} Chargefox/NRMA stations to {shown_path(p.registry_path)}{C_RESET}")
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
                    print(f"  {C_GREEN}✔ Cached {len(synced_evx)} EVX stations to {shown_path(p.registry_path)}{C_RESET}")
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
                    print(f"  {C_GREEN}✔ Cached {len(synced_evie)} Evie stations to {shown_path(p.registry_path)}{C_RESET}")
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
                    print(f"  {C_GREEN}✔ Cached {len(synced_bp)} bp pulse stations to {shown_path(p.registry_path)}{C_RESET}")
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
                    print(f"  {C_GREEN}✔ Discovered and saved {len(synced_ps)} PlugShare stations to {shown_path(p.registry_path)}{C_RESET}")
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
                    print(f"  {C_GREEN}✔ Cached {len(synced_amp)} AmpCharge/Ampol Foodary stations to {shown_path(p.registry_path)}{C_RESET}")
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
                    print(f"  {C_GREEN}✔ Cached {len(synced_exploren)} Exploren stations to {shown_path(p.registry_path)}{C_RESET}")
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
                    print(f"  {C_GREEN}✔ Cached {len(synced_jolt)} Jolt stations to {shown_path(p.registry_path)}{C_RESET}")
                    jolt_ok = True
                except Exception as e:
                    print(f"  {C_YELLOW}⚠️ Jolt sync encountered error: {e}{C_RESET}")
        sync_results.append(("Jolt", jolt_ok))

        print(f"\n{C_CYAN}⚡ [10/11] Syncing NRMA EV Charging Network across Australia...{C_RESET}")
        nrma_ok = False
        for p in manager.providers:
            if isinstance(p, NRMAProvider):
                try:
                    synced_nrma = p.sync()
                    print(f"  {C_GREEN}✔ Cached {len(synced_nrma)} NRMA stations to {shown_path(p.registry_path)}{C_RESET}")
                    nrma_ok = True
                except Exception as e:
                    print(f"  {C_YELLOW}⚠️ NRMA sync encountered error: {e}{C_RESET}")
        sync_results.append(("NRMA", nrma_ok))

        print(f"\n{C_CYAN}🌐 [11/11] Compiling and Exporting Consolidated Master Registry...{C_RESET}")
        try:
            out_path, total_count = manager.export_consolidated_registry()
            print(f"  {C_GREEN}✔ Successfully exported {total_count} consolidated multi-view stations to:{C_RESET}")
            print(f"    {shown_path(out_path)}")
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
            # The list is compared with the registry first (no page fetched). Only what differs is scraped, unless a flag asks for more.
            plan = explorer.plan_sync(stations)
            print(f"  {len(stations)} listed: {len(plan['same']) + len(plan['stale'])} match the registry, {len(plan['new'])} new, "
                  f"{len(plan['renamed'])} renamed, {len(plan['moved'])} moved, {len(plan['gone'])} no longer listed")
            for k in plan["gone"]:
                print(f"  {C_YELLOW}no longer listed on tesla.com (kept): {k}{C_RESET}")
            if plan["stale"]:
                print(f"  {C_DIM}{len(plan['stale'])} not re-checked for 90+ days (--stale to re-check){C_RESET}")
            changed = {x["url"] for x in plan["new"] + plan["renamed"] + plan["moved"]}
            by_flag = bool(args.unpriced or args.new or args.renamed or args.stale or args.all)
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
            if not by_flag:
                stations = [x for x in stations if x["url"] in changed]
            elif subset_filter:
                print(f"  {C_DIM}🎯 Applying subset filter: '{subset_filter}'{C_RESET}")
            if not stations:
                print(f"  {C_GREEN}Nothing to fetch.{C_RESET}")
            else:
                explorer.scrape_all_stations(
                    stations,
                    pacing_delay=args.delay,
                    timeout_sec=args.timeout,
                    max_retries=args.retries,
                    subset_filter=subset_filter
                )

        # charging_stations.json (what the app and the other tools read) is rebuilt whenever a Tesla registry file is newer than it.
        try:
            from lib.tessie_api_common import resolve_tessie_data_home
            cons = os.path.join(resolve_tessie_data_home(), "charging_stations.json")
            srcs = [explorer.superchargers_path, explorer.destination_chargers_path]
            if os.path.isfile(cons) and any(os.path.isfile(x) and os.path.getmtime(x) > os.path.getmtime(cons) for x in srcs):
                out_path, total_count = manager.export_consolidated_registry()
                print(f"{C_GREEN}✔ Rebuilt {os.path.basename(out_path)} ({total_count} stations).{C_RESET}")
        except Exception as e:
            print(f"{C_YELLOW}⚠ charging_stations.json was not rebuilt: {e}{C_RESET}")
        print(f"{C_GREEN}✔ Tesla synchronization complete.{C_RESET}")
        return

    if args.sync_chargefox:
        for p in manager.providers:
            if isinstance(p, ChargefoxProvider):
                print(f"{C_CYAN}🌐 Fetching all Chargefox & NRMA stations across Australia via GraphQL...{C_RESET}")
                synced = p.sync_all_australia()
                print(f"{C_GREEN}✔ Successfully saved {len(synced)} Chargefox stations to {shown_path(p.registry_path)}{C_RESET}")
                return

    if args.sync_ampcharge:
        for p in manager.providers:
            if isinstance(p, AmpChargeProvider):
                print(f"{C_CYAN}⛽ Fetching all Ampol AmpCharge & Ampol Foodary EV fast-charging sites across Australia...{C_RESET}")
                synced = p.sync_all_australia()
                print(f"{C_GREEN}✔ Successfully saved {len(synced)} AmpCharge/Ampol Foodary stations to {shown_path(p.registry_path)}{C_RESET}")
                return

    if args.sync_evx:
        for p in manager.providers:
            if isinstance(p, EVXProvider):
                print(f"{C_CYAN}🌐 Fetching all EVX Kerbside Polechargers across Australia...{C_RESET}")
                synced = p.sync()
                print(f"{C_GREEN}✔ Successfully saved {len(synced)} EVX stations to {shown_path(p.registry_path)}{C_RESET}")
                return

    if args.sync_evie:
        for p in manager.providers:
            if isinstance(p, EvieProvider):
                print(f"{C_CYAN}🌐 Fetching all Evie Networks fast charging stations across Australia...{C_RESET}")
                synced = p.sync()
                print(f"{C_GREEN}✔ Successfully saved {len(synced)} Evie stations to {shown_path(p.registry_path)}{C_RESET}")
                return

    if args.sync_bppulse:
        for p in manager.providers:
            if isinstance(p, BPPulseProvider):
                print(f"{C_CYAN}⚡ Fetching all bp pulse fast charging stations across Australia...{C_RESET}")
                synced = p.sync()
                print(f"{C_GREEN}✔ Successfully saved {len(synced)} bp pulse stations to {shown_path(p.registry_path)}{C_RESET}")
                return

    if args.sync_exploren:
        for p in manager.providers:
            if isinstance(p, ExplorenProvider):
                print(f"{C_CYAN}🔋 Fetching all Exploren AC/DC charging stations across Australia...{C_RESET}")
                synced = p.sync()
                print(f"{C_GREEN}✔ Successfully saved {len(synced)} Exploren stations to {shown_path(p.registry_path)}{C_RESET}")
                return

    if args.sync_jolt:
        for p in manager.providers:
            if isinstance(p, JoltProvider):
                print(f"{C_CYAN}🔵 Fetching all Jolt DC fast charging stations across Australia...{C_RESET}")
                synced = p.sync()
                print(f"{C_GREEN}✔ Successfully saved {len(synced)} Jolt stations to {shown_path(p.registry_path)}{C_RESET}")
                return

    if args.sync_nrma:
        for p in manager.providers:
            if isinstance(p, NRMAProvider):
                print(f"{C_CYAN}⚡ Fetching all NRMA EV Charging Network stations across Australia...{C_RESET}")
                synced = p.sync()
                print(f"{C_GREEN}✔ Successfully saved {len(synced)} NRMA stations to {shown_path(p.registry_path)}{C_RESET}")
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
                            print(f"{C_GREEN}✔ Saved to {shown_path(p.registry_path)}{C_RESET}")
                        stations = synced
                    else:
                        print(f"{C_RED}❌ Could not resolve reference location '{args.near}'.{C_RESET}", file=sys.stderr)
                        return
                elif args.state:
                    print(f"{C_CYAN}🌐 Fetching PlugShare Chargers for {args.state.upper()}...{C_RESET}")
                    synced = p.sync_state(args.state.upper(), auto_save=auto_save)
                    print(f"{C_GREEN}✔ Discovered {len(synced)} PlugShare stations in {args.state.upper()}.{C_RESET}")
                    if auto_save:
                        print(f"{C_GREEN}✔ Saved to {shown_path(p.registry_path)}{C_RESET}")
                    stations = synced
                else:
                    print(f"{C_CYAN}🌐 Fetching PlugShare Chargers across Australia...{C_RESET}")
                    synced = p.sync_australia(auto_save=auto_save)
                    print(f"{C_GREEN}✔ Discovered {len(synced)} PlugShare stations across Australia.{C_RESET}")
                    if auto_save:
                        print(f"{C_GREEN}✔ Saved to {shown_path(p.registry_path)}{C_RESET}")
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
                            print(f"{C_GREEN}✔ Saved station to {shown_path(p.registry_path)}{C_RESET}")
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
                    print(f"{C_GREEN}✔ Successfully saved {len(discovered)} station(s) to {shown_path(p.registry_path)}{C_RESET}")
                else:
                    render_stations_table(discovered, "DISCOVERED CHARGERS FROM HISTORY", eval_time=args.time)
                return

    if args.export_consolidated:
        out_path, total_count = manager.export_consolidated_registry()
        print(f"{C_GREEN}✔ Successfully exported {total_count} consolidated multi-view stations to:{C_RESET}")
        print(f"  {shown_path(out_path)}")
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
            if args.sc or args.tesla_sc:
                types_to_fetch.append("superchargers")
            if args.dc or args.ac or args.tesla_dc:
                types_to_fetch.append("chargers")
            if not types_to_fetch:
                types_to_fetch = ["superchargers", "chargers"]
            tesla_explorer = TeslaChargerExplorer()
            browse_stations_by_type(tesla_explorer, types_to_fetch, args.country or "Australia")
            return
        if net_lower == "chargefox":
            browse_chargefox_catalog_grid(manager, args.country or "Australia", auto_save=args.save)
            return
        # (2026-09-23, REQ-104 follow-on) NRMA used to piggyback on
        # Chargefox's view (its 28-ish overlapping sites made that a
        # reasonable shortcut), but NRMA has its own independent first-party
        # source (chargers_nrma.py's own fetch_all_remote(), a Google
        # MyMaps KML feed) and now gets its own native live-diff like every
        # other full-list provider, rather than being folded into
        # Chargefox's catalog.
        _GENERIC_LIVE_DIFF_NETWORKS = {
            "nrma": "NRMA",
            "jolt": "JOLT",
            "evie": "Evie",
            "evx": "EVX",
            "exploren": "Exploren",
            "bppulse": "BP Pulse",
            "ampcharge": "AmpCharge",
        }
        if net_lower in _GENERIC_LIVE_DIFF_NETWORKS:
            provider_cls_map = {
                "nrma": NRMAProvider, "jolt": JoltProvider, "evie": EvieProvider,
                "evx": EVXProvider, "exploren": ExplorenProvider,
                "bppulse": BPPulseProvider, "ampcharge": AmpChargeProvider,
            }
            provider = next((p for p in manager.providers if isinstance(p, provider_cls_map[net_lower])), None)
            if provider is None:
                print(f"{C_RED}❌ {_GENERIC_LIVE_DIFF_NETWORKS[net_lower]} provider not configured.{C_RESET}")
                return
            browse_live_diff_grid(provider, _GENERIC_LIVE_DIFF_NETWORKS[net_lower], args.country or "Australia", auto_save=args.save)
            return
        if net_lower == "plugshare":
            # PlugShare is Tier 2 (a moving-viewport aggregator, not a
            # full-list scrape) - it has no single canonical upstream list
            # to live-fetch-and-diff, so it gets a local-only staleness
            # report instead of a live browse.
            provider = next((p for p in manager.providers if isinstance(p, PlugShareProvider)), None)
            if provider is None:
                print(f"{C_RED}❌ PlugShare provider not configured.{C_RESET}")
                return
            plugshare_local_staleness_report(provider)
            return
        print(f"{C_YELLOW}⚠ --live browsing isn't available for this network selection - it needs one canonical upstream catalog to diff against (tesla, chargefox, nrma, evie, evx, bppulse, ampcharge, exploren, jolt), or use plugshare for a local staleness report.{C_RESET}")
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

        # Resolve ABRP & EV Route Intelligence config early - corridor_radius_km
        # needs it too now (config.json follow-up, 2026-09-21: --target-soc/
        # --max-soc already fell back to abrp_cfg when unset on the CLI;
        # --corridor-radius used to hardcode default=5.0 in argparse itself,
        # so it could never fall back to anything - moved load_abrp_config()
        # up here so all three routing knobs resolve the same way: CLI flag
        # wins, else config.json's abrp.* value, else the original literal).
        from lib.abrp_common import (
            load_abrp_config,
            resolve_departure_soc,
            generate_abrp_deep_link,
            estimate_arrival_soc,
            DEFAULT_CONSUMPTION_WH_KM,
            DEFAULT_USABLE_KWH
        )
        abrp_cfg = load_abrp_config()
        # REQ-175: -r/--radius given on a route sets the corridor width too
        # (--corridor still wins if both are given).
        if args.corridor_radius is not None:
            corridor_radius_km = args.corridor_radius
        elif explicit_radius and args.radius:
            corridor_radius_km = float(args.radius)
        else:
            corridor_radius_km = float(abrp_cfg.get("corridor_radius_km", 5.0))

        if not args.json:
            print(f"🛣️  {C_CYAN}Calculating driving corridor from {f_label} to {t_label}...{C_RESET}")
        stations, route_info = manager.search_route_corridor(
            waypoints=waypoints,
            corridor_radius_km=corridor_radius_km,
            filters=filters,
            sort_by=effective_sort,
            eval_time=args.time
        )
        route_info["origin_label"] = f_label
        route_info["destination_label"] = t_label
        route_info["corridor_radius_km"] = corridor_radius_km
        route_info["via_labels"] = via_labels

        no_soc = bool(getattr(args, "no_soc", False))
        route_info["no_soc"] = no_soc
        if no_soc:
            dep_soc, dep_src, live_detail = None, None, {}
        else:
            dep_soc, dep_src, live_detail = resolve_departure_soc(args.departure_soc)
        tgt_soc = args.target_soc if args.target_soc is not None else float(abrp_cfg.get("target_arrival_soc", 10.0))
        max_soc = args.max_soc if args.max_soc is not None else float(abrp_cfg.get("max_charge_soc", 85.0))

        via_pts = waypoints[1:-1] if len(waypoints) > 2 else []
        abrp_url = None
        if getattr(args, "abrp_link", True) and not no_soc:
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
        # Fix (2026-09-21): reuse the same shared constants as the fallback
        # above instead of yet another independently-invented pair (175.0 vs
        # 165.0 vs abrp_common.py's own 171.1), and a generic vehicle-name
        # fallback instead of a hardcoded specific car (abrp_cfg always sets
        # "vehicle_name" itself - see load_abrp_config() - so this fallback
        # is last-resort only).
        route_info["consumption_wh_km"] = abrp_cfg.get("consumption_wh_km", DEFAULT_CONSUMPTION_WH_KM)
        route_info["usable_kwh"] = abrp_cfg.get("usable_kwh", DEFAULT_USABLE_KWH)
        route_info["vehicle_name"] = abrp_cfg.get("vehicle_name", "Tesla Model Y")

        # --charger-stops (2026-09-21): multi-leg re-plan through user-chosen
        # enroute charging stops, matched by station name against THIS
        # query's own results. Every station's Arr SoC is recomputed leg-by-
        # leg (the PRECEDING chosen stop's charge-to target and the distance
        # SINCE that stop, not the original single non-stop departure/
        # distance-from-origin pair); Dep SoC is set only on the chosen
        # stops themselves - see render_stations_table()'s multi_stop branch.
        chosen_stops = []
        if getattr(args, "charger_stops", None) and no_soc:
            print(f"{C_YELLOW}ℹ️  --charger-stops ignored with --no-soc (it only re-plans battery figures).{C_RESET}")
        if getattr(args, "charger_stops", None) and not no_soc:
            for want in args.charger_stops:
                want_l = want.strip().lower()
                if not want_l:
                    continue
                exact = [s for s in stations if (s.name or "").strip().lower() == want_l]
                if exact:
                    chosen_stops.append(exact[0])
                    continue
                sub = [s for s in stations if want_l in (s.name or "").lower()]
                if len(sub) == 1:
                    chosen_stops.append(sub[0])
                elif len(sub) > 1:
                    names = ", ".join(s.name for s in sub[:5])
                    more = ", ..." if len(sub) > 5 else ""
                    print(f"⚠️  '{want}' matches multiple stations in this query ({names}{more}) - use a more specific name.", file=sys.stderr)
                else:
                    print(f"⚠️  '{want}' did not match any station in this query's results.", file=sys.stderr)

            seen_ids = {}
            for s in chosen_stops:
                seen_ids[id(s)] = s
            chosen_stops = sorted(
                seen_ids.values(),
                key=lambda s: getattr(s, "route_progress_km", None) if getattr(s, "route_progress_km", None) is not None else 0.0
            )

        if chosen_stops:
            route_info["charger_stops"] = [s.name for s in chosen_stops]
            stop_charge_target = max_soc  # same charge-to % at every chosen stop (user decision, 2026-09-21)
            leg_bounds = [(0.0, dep_soc)]
            for stop in chosen_stops:
                stop_prog = getattr(stop, "route_progress_km", None)
                if stop_prog is None:
                    continue
                leg_start_prog, leg_start_soc = leg_bounds[-1]
                arr_here, _ = estimate_arrival_soc(
                    leg_start_soc, max(0.0, stop_prog - leg_start_prog),
                    getattr(stop, "detour_km", 0.0) or 0.0,
                    route_info["consumption_wh_km"], route_info["usable_kwh"]
                )
                if arr_here < 0:
                    print(f"⚠️  Leg to '{stop.name}' needs ~{-arr_here:.0f}% more battery than available departing the previous stop at {leg_start_soc:.0f}% - not reachable as chosen.", file=sys.stderr)
                stop.leg_arrival_soc = arr_here
                stop.stop_dep_soc = stop_charge_target
                leg_bounds.append((stop_prog, stop_charge_target))

            for st in stations:
                prog = getattr(st, "route_progress_km", None)
                if prog is None:
                    continue
                leg_start_prog, leg_start_soc = leg_bounds[0]
                for lp, ls in leg_bounds:
                    if lp < prog:
                        leg_start_prog, leg_start_soc = lp, ls
                st.leg_departure_soc = leg_start_soc
                st.leg_distance_km = max(0.0, prog - leg_start_prog)

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

 # (2026-09-23, AGENTS.local.md REQ-106) --tesla-sc/--tesla-dc PRODUCT-LINE
 # post-filter - independent of speed_tier/max_power_kw, this checks the
 # record's own `network` field (set purely by which JSON file it was
 # scraped from - chargers_tesla.py's `network = "Tesla Destination" if
 # is_dest else "Tesla"`), so it stays correct even for a hypothetical
 # future DC-capable Destination Charger that `--network tesla --dc`
 # would (correctly, after this same REQ-106) also start picking up on
 # hardware grounds alone.
    if args.tesla_sc:
        stations = [s for s in stations if (s.network or "") == "Tesla"]
    elif args.tesla_dc:
        stations = [s for s in stations if (s.network or "") == "Tesla Destination"]

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
    if args.update_stale or args.update_renamed or getattr(args, "update_all", False):
 # REQ-102: user asked for the ability to refresh ALL matching stations live
 # from their sources (Tesla, PlugShare, ...), not just whatever our own
 # STALE/RENAMED status heuristics happen to flag - "ALL" is a sentinel
 # batch_reverify_stations() specifically recognizes to skip that filter
 # entirely (see chargers_manager.py).
        target_status = "ALL" if getattr(args, "update_all", False) else ("STALE" if args.update_stale else "RENAMED")
        label = "EVERY matching" if target_status == "ALL" else target_status
        print(f"🔄 Starting batch update for {label} station(s) (historical changes will be archived)...")
        stats = manager.batch_reverify_stations(stations, status_filter=target_status)
        print(f"\n✔ Batch update complete: {stats['SUCCESS']} updated, {stats['ERROR']} failed out of {stats['TOTAL']} target stations.")
        if stats.get("NOT_FOUND"):
 # (2026-09-29) Confirmed-gone stations are counted separately from
 # ordinary failures (see batch_reverify_stations()) - a batch run
 # never auto-deletes, so surface them by name here rather than
 # silently folding them into "failed" or leaving them unmentioned.
            print(f"⚠ {stats['NOT_FOUND']} station(s) confirmed NO LONGER EXIST at their own source (not deleted automatically - re-run '[u]' on each individually to review and confirm removal):")
            for _name in stats.get("_not_found_names", []):
                print(f"    - {_name}")
        return

 # Offline Facilities Reclassify Action (BUG-151, no network call)
    if getattr(args, "reclassify_facilities", False):
        try:
            from lib.station_facilities_common import get_facilities_registry
        except ImportError:
            from station_facilities_common import get_facilities_registry
        fac_reg = get_facilities_registry()
        print("\U0001f504 Reclassifying cached facilities offline (no network calls)...")
        result = fac_reg.reclassify_cached_facilities()
        print(f"\u2714 {result['reclassified']} record(s) reclassified, {result['unchanged']} already correct, "
              f"{result['needs_live_refetch']} still need a live refetch (predate raw-tag saving - use --sync-facilities for those).")
        return

 # Tesla Busy Times sync (REQ-174) - additive, carries on to the list
    if getattr(args, "sync_busy_times", False) and stations:
        sync_busy_times_for_stations(stations, delay_s=args.delay)

 # Batch Facilities Sync Action
    if getattr(args, "sync_facilities", False):
        if not stations:
            print(f"❌ No stations found matching current criteria to sync facilities for.")
            return
        sync_result = sync_facilities_for_stations(stations, radius_m=args.facilities_radius or 500, delay_s=args.delay) or {}
        # BUG-173 (2026-09-30): the sync used to end the run, so
        # `--near Brisbane --sc --sync-facilities --email` never showed the
        # list or built the email. It's additive now: after syncing, carry on
        # with the normal output. If anything was actually refreshed, re-run
        # the same command without --sync-facilities so stop scores, a
        # facilities sort and a route's planned stops all use the new data.
        if sync_result.get("synced"):
            argv = [a for a in sys.argv[1:]
                    if not (a.startswith("--sync-f") and "--sync-facilities".startswith(a))]
            print(f"\n{C_CYAN}Showing results with the refreshed facilities...{C_RESET}", flush=True)
            sys.stdout.flush()
            sys.stderr.flush()
            os.execv(sys.executable, [sys.executable, os.path.abspath(sys.argv[0])] + argv)

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
        sort_mode_desc = "Stop Quality Score (Highest First, Facilities + Detour + Speed & Bays + PlugShare)"
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

    if (getattr(args, "email", False) or getattr(args, "share", False)) and stations:
        from lib.station_report_email import build_station_report, copy_report_to_clipboard
        html_body, text_body = build_station_report(
            stations, title, route_info=route_info, eval_time=args.time,
            type_icon_fn=lambda st: _contextual_type_icon(st, args.network),
            near_info={"label": ref_label, "radius_km": active_radius, "sort": sort_mode_desc} if ref_label else None,
            share=bool(getattr(args, "share", False)))
        ok, msg = copy_report_to_clipboard(html_body, text_body)
        print(f"\n 📧 {C_GREEN if ok else C_YELLOW}{msg}{C_RESET}")

 # Facilities CLI direct execution mode
    if args.facilities:
        if not stations:
            print(f"{C_YELLOW}⚠ No stations found to look up nearby facilities.{C_RESET}")
            return
        radius = args.facilities_radius or 500
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

            # Action rows (label, color, description) - width computed from
            # the actual labels every time, not hand-typed spacing, so the
            # description column stays aligned regardless of how wide
            # num_range happens to be (e.g. "1" vs "1-11" vs "1-100") or
            # which optional rows (abrp) are present this run.
            action_rows = [(num_range, C_GREEN, f"Inspect station details & view nearby amenities (e.g. {C_GREEN}14{C_RESET})")]
            if has_abrp:
                action_rows.append(("abrp", C_GREEN, "Open ABRP 1-Click Road Trip in your browser"))
            action_rows.append(("u <#>", C_CYAN, f"Update / re-verify a station live (e.g. {C_CYAN}u 14{C_RESET})"))
            action_rows.append(("stale", C_BLUE, "Batch re-verify all stale stations in this query"))
            action_rows.append(("renamed", C_MAGENTA, "Batch re-verify all renamed stations in this query"))
            action_rows.append(("all", C_ORANGE, "Batch re-verify EVERY station in this list live from its source (Tesla, PlugShare, ...) - not just stale/renamed"))
            action_rows.append(("facilities", C_YELLOW, "Batch re-fetch nearby OSM facilities for every station in this list"))
            action_rows.append(("r", C_GREEN, "Redisplay station table"))
            action_rows.append(("q", C_GREEN, "Quit (or press Enter)"))

            label_col_w = max(len(label) + 2 for label, _, _ in action_rows)  # +2 for the brackets
            print(f"\n{C_BOLD}Actions:{C_RESET}")
            for label, color, desc in action_rows:
                bracketed = f"[{label}]"
                print(f"  {color}{bracketed}{C_RESET}{' ' * (label_col_w - len(bracketed) + 2)}{desc}")

            prompt_parts = [num_range]
            if has_abrp:
                prompt_parts.append("abrp")
            prompt_parts.extend(["u <#>", "stale", "renamed", "all", "facilities", "r", "q"])
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
                if stats.get("NOT_FOUND"):
                    print(f"⚠ {stats['NOT_FOUND']} station(s) confirmed no longer exist at their source (not auto-deleted): {', '.join(stats.get('_not_found_names', []))}")
                input("\nPress Enter to refresh list...")
                render_cb()
                continue

            if choice in ("renamed", "update-renamed"):
                print(f"🔄 Re-verifying all renamed stations in current query...")
                stats = manager.batch_reverify_stations(stations, status_filter="RENAMED")
                print(f"✔ Done: {stats['SUCCESS']} updated, {stats['ERROR']} failed.")
                if stats.get("NOT_FOUND"):
                    print(f"⚠ {stats['NOT_FOUND']} station(s) confirmed no longer exist at their source (not auto-deleted): {', '.join(stats.get('_not_found_names', []))}")
                input("\nPress Enter to refresh list...")
                render_cb()
                continue

            if choice in ("all", "update-all", "refresh-all"):
 # REQ-102: user request - refresh literally every station in this list live
 # from its own source (Tesla via Playwright, PlugShare/Chargefox/etc via
 # their own APIs - reverify_station() already dispatches by provider), not
 # only whatever STALE/RENAMED happens to flag. "ALL" is a sentinel
 # batch_reverify_stations() recognizes to skip that status filter entirely.
                confirm = input(f"This will re-verify all {len(stations)} station(s) in this list live, regardless of status - continue? [y/N]: ").strip().lower()
                if confirm == "y":
                    print(f"🔄 Re-verifying all {len(stations)} station(s) in current query live from source...")
                    stats = manager.batch_reverify_stations(stations, status_filter="ALL")
                    print(f"✔ Done: {stats['SUCCESS']} updated, {stats['ERROR']} failed.")
                    if stats.get("NOT_FOUND"):
                        print(f"⚠ {stats['NOT_FOUND']} station(s) confirmed no longer exist at their source (not auto-deleted): {', '.join(stats.get('_not_found_names', []))}")
                    input("\nPress Enter to refresh list...")
                render_cb()
                continue

            if choice in ("facilities", "osm", "sync-facilities"):
 # User request: "not only batch refresh all of them but batch refresh OSM
 # too" - the CLI-level --sync-facilities flag already existed but required
 # leaving this list and re-invoking the whole program; this lets you cover
 # exactly the stations already in front of you (e.g. one route's worth)
 # without a separate, much slower whole-registry run.
                sync_facilities_for_stations(stations, radius_m=args.facilities_radius or 500, delay_s=args.delay)
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
                        ok, diffs, _fresh, confirmed_not_found = manager.reverify_station(target_st)
                        _print_reverify_result(target_st.name, ok, diffs, confirmed_not_found)
                        if confirmed_not_found and _maybe_prompt_remove_station(manager, target_st):
                            stations.remove(target_st)
                        input("\nPress Enter to refresh list...")
                        render_cb()
                        continue

            target_num = None
            if choice.isdigit():
                target_num = int(choice)

            if target_num is None or target_num < 1 or target_num > len(stations):
                print(f"{C_RED}❌ Invalid selection. Please enter a number between 1 and {len(stations)}.{C_RESET}\n")
                continue

            break_outer = False

            # Inner loop: lets [n]ext/[p]revious step straight to the next/prev
            # station's own card+menu without dropping back out to the full
            # table each time (which is what re-entering a number here would
            # otherwise require). Every other action keeps its exact previous
            # behaviour of falling through to render_cb() below via the shared
            # `break` at the bottom of this loop.
            while True:
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
                        from lib.supercharge_common import find_supercharge_site
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
                if target_num < len(stations):
                    print(f"  [{C_GREEN}n{C_RESET}]      Next station in list ({stations[target_num].name})")
                if target_num > 1:
                    print(f"  [{C_GREEN}p{C_RESET}]      Previous station in list ({stations[target_num - 2].name})")
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
                prompt_parts.append("f")
                if target_num < len(stations):
                    prompt_parts.append("n")
                if target_num > 1:
                    prompt_parts.append("p")
                prompt_parts.extend(["b", "q"])
                prompt_options = "/".join(prompt_parts)

                try:
                    act = input(f"\n{C_BOLD}Choice [{prompt_options}, default: b]: {C_RESET}").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    break_outer = True
                    break

                if act in ("q", "quit"):
                    break_outer = True
                    break
                elif act in ("n", "next"):
                    if target_num < len(stations):
                        target_num += 1
                        continue
                    print(f"{C_YELLOW}⚠ Already at the last station in this list.{C_RESET}")
                    continue
                elif act in ("p", "prev", "previous"):
                    if target_num > 1:
                        target_num -= 1
                        continue
                    print(f"{C_YELLOW}⚠ Already at the first station in this list.{C_RESET}")
                    continue
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
                    ok, diffs, fresh, confirmed_not_found = manager.reverify_station(sel)
                    _print_reverify_result(sel.name, ok, diffs, confirmed_not_found)
                    if confirmed_not_found:
                        removed = _maybe_prompt_remove_station(manager, sel)
                        if removed and sel in stations:
                            stations.remove(sel)
                        input("\nPress Enter to continue...")
                        if removed:
                            break
                    elif ok and fresh:
                        sel = fresh
                        print()
                        display_station_inspection_card(sel, eval_time=args.time)
                        input("\nPress Enter to continue...")
                    else:
                        input("\nPress Enter to continue...")
                elif act in ("b", "back"):
 # BUG-134: this menu's own printed action list (and its "default: b")
 # promise "[b] Back to station list" - a pure no-op that just lets
 # control fall through to render_cb() below like every other branch
 # does. Without this explicit branch, "b" fell all the way through to
 # _handle_browser_open_combo(), which ALSO treats a bare "b" as a
 # valid single-letter trigger - but for it, "b" means "open the
 # station's own Charger URL in the browser" (see that function's
 # 'elif choice_clean in ("b", "browser", "url")' branch) - a real,
 # unwanted side effect (an unexpected browser tab) on every plain
 # "back" in exactly this menu. The OTHER menu that reuses
 # _handle_browser_open_combo() (display_station_inspection_card's own
 # "Actions:" prompt) has no "back" concept at all - its quit key is
 # "q"/Enter - so "b" genuinely means "browser" there with no clash;
 # this fix is scoped to just this station-list-return menu.
                    pass
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
                    radius = args.facilities_radius or 500
                    force = "refresh" in act or act == "fr"
                    for p in parts[1:]:
                        if p.isdigit():
                            radius = int(p)
                        elif p in ("r", "refresh", "-r", "--refresh"):
                            force = True
                    display_nearby_facilities(sel, radius_m=radius, force_refresh=force)
                    input("\nPress Enter to continue...")

                break

            if break_outer:
                break
            render_cb()


if __name__ == "__main__":
    main()
