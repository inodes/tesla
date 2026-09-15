#!/usr/bin/env python3
"""
PlugShare Charger Provider (Tier 2 Multi-Network Aggregator)
============================================================
Full-featured provider for PlugShare data:
- Local cached registry management (Tessie/plugshare_chargers.json)
- Live PlugShare v3 API client (bounding box, text search, tiled grid discovery)
- Deep hardware & gross pricing extraction ($/kWh)
- History auto-discovery from charges_master.csv
- Rate-limit resilient price refresh engine
"""

import os
import re
import csv
import json
import math
import time
import random
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple, Any, Set
import urllib.request
import urllib.parse
import urllib.error

from .chargers_base import (
    BaseChargerProvider,
    UnifiedStationRecord,
    haversine_km,
    filter_station_records,
    detect_station_changes,
    archive_station_record,
    atomic_write_json
)

_script_dir = os.path.dirname(os.path.abspath(__file__))
_tools_dir = os.path.dirname(_script_dir)
_repo_root = os.path.dirname(_tools_dir)

# -----------------------------------------------------------------------------
# PlugShare Constants & Mappings
# -----------------------------------------------------------------------------

PLUGSHARE_NETWORKS = {
    48: "Chargefox",
    60: "Evie Networks",
    63: "NRMA",
    69: "JOLT",
    70: "Exploren",
    81: "BP Pulse",
    1292: "AmpCharge",
    1: "ChargePoint",
    57: "EVUp",
    1041: "EV Range",
    35: "Tesla Destination",
    8: "Tesla Supercharger",
    24: "Tesla",
}

PLUGSHARE_CONNECTORS = {
    2: "CHAdeMO",
    3: "CHAdeMO",
    7: "Type 2",
    11: "Tesla (Roadster)",
    12: "Wall (AU/NZ)",
    13: "CHAdeMO",
    14: "Three Phase (5-pin)",
    15: "Caravan Mains (15A)",
    20: "CCS2"
}

AUSTRALIAN_STATES_BOUNDS = {
    "NSW": {"name": "New South Wales", "lat": -32.5, "lon": 149.0, "spanLat": 6.0, "spanLng": 8.0},
    "VIC": {"name": "Victoria", "lat": -37.0, "lon": 144.5, "spanLat": 4.0, "spanLng": 6.0},
    "QLD": {"name": "Queensland", "lat": -22.5, "lon": 144.5, "spanLat": 12.0, "spanLng": 10.0},
    "WA":  {"name": "Western Australia", "lat": -26.0, "lon": 121.0, "spanLat": 14.0, "spanLng": 12.0},
    "SA":  {"name": "South Australia", "lat": -32.0, "lon": 136.0, "spanLat": 8.0, "spanLng": 8.0},
    "TAS": {"name": "Tasmania", "lat": -42.0, "lon": 146.5, "spanLat": 3.0, "spanLng": 3.0},
    "ACT": {"name": "Australian Capital Territory", "lat": -35.3, "lon": 149.1, "spanLat": 0.8, "spanLng": 0.8},
    "NT":  {"name": "Northern Territory", "lat": -19.5, "lon": 133.5, "spanLat": 10.0, "spanLng": 8.0},
}

AUSTRALIAN_SEARCH_REGIONS = [
    ("Sydney Metro & Central Coast", -33.75, 151.10, 1.8, 1.8),
    ("Melbourne Metro & Port Phillip", -37.85, 145.00, 1.8, 1.8),
    ("Brisbane Metro & South East QLD", -27.47, 153.05, 1.8, 1.8),
    ("Gold Coast & Tweed", -28.05, 153.40, 1.0, 1.0),
    ("Sunshine Coast", -26.65, 153.05, 1.0, 1.0),
    ("Perth Metro & Peel", -31.95, 115.85, 1.8, 1.8),
    ("Adelaide Metro & Hills", -34.93, 138.60, 1.5, 1.5),
    ("Canberra & ACT", -35.30, 149.13, 0.8, 0.8),
    ("Hobart & Southern TAS", -42.88, 147.32, 1.0, 1.0),
    ("Launceston & Northern TAS", -41.44, 147.14, 1.0, 1.0),
    ("Newcastle & Hunter Valley", -32.85, 151.60, 1.5, 1.5),
    ("Wollongong & Illawarra", -34.43, 150.88, 1.2, 1.2),
    ("Geelong & Bellarine", -38.15, 144.36, 1.0, 1.0),
    ("Cairns & Far North QLD", -16.92, 145.77, 1.5, 1.5),
    ("Townsville & North QLD", -19.26, 146.81, 1.5, 1.5),
    ("Darwin & Top End", -12.46, 130.84, 1.5, 1.5),
    ("Alice Springs & Red Centre", -23.70, 133.88, 1.5, 1.5),
    ("Albury / Wodonga / Hume", -36.08, 146.92, 1.5, 1.5),
    ("Dubbo & Central West NSW", -32.25, 148.60, 2.0, 2.0),
    ("Ballarat & Western VIC", -37.56, 143.85, 1.5, 1.5),
    ("Bendigo & Central VIC", -36.76, 144.28, 1.5, 1.5),
    ("Toowoomba & Darling Downs", -27.56, 151.95, 1.5, 1.5),
]


def _get_utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_plugshare_registry_path() -> str:
    """Finds path to plugshare_chargers.json."""
    candidates = []
    tessie_home = None
    try:
        from lib.tessie_api_common import resolve_tessie_data_home
        tessie_home = resolve_tessie_data_home()
    except Exception:
        pass
    if tessie_home:
        candidates.append(os.path.join(tessie_home, "plugshare_chargers.json"))

    candidates.append(os.path.join(_repo_root, "Tessie", "plugshare_chargers.json"))
    config_path = os.path.join(_repo_root, "config.json")  # TODO-034: root is primary now
    if not os.path.isfile(config_path):
        config_path = os.path.join(_repo_root, "Tessie", "config.json")  # legacy fallback
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                td = cfg.get("tessie_directory")
                if td:
                    td_exp = os.path.expanduser(td)
                    candidates.insert(0, os.path.join(td_exp, "plugshare_chargers.json"))
        except Exception:
            pass

    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0] if candidates else os.path.join(_repo_root, "Tessie", "plugshare_chargers.json")


def extract_au_state_and_suburb(addr: str) -> Tuple[str, str]:
    if not addr:
        return "-", ""
    st_m = re.search(r"\b(NSW|VIC|QLD|WA|SA|TAS|ACT|NT)\b", addr, re.IGNORECASE)
    st = st_m.group(1).upper() if st_m else "-"

    # 1. Standard ", Suburb STATE [postcode]"
    sub_m = re.search(r",\s*([^,]+?)\s+(?:NSW|VIC|QLD|WA|SA|TAS|ACT|NT)\b", addr, re.IGNORECASE)
    sub = sub_m.group(1).strip() if sub_m else ""

    # 2. If no match, check split by comma
    if not sub:
        parts = [p.strip() for p in addr.split(",") if p.strip()]
        for p in parts[1:]:
            cleaned = re.sub(r"\b(NSW|VIC|QLD|WA|SA|TAS|ACT|NT|\d{4}|Australia)\b", "", p, flags=re.IGNORECASE).strip()
            if cleaned and len(cleaned) >= 2:
                sub = cleaned
                break

    # 3. If still empty or just state/postcode, extract from parts[0]
    if not sub or re.match(r"^(?:NSW|VIC|QLD|WA|SA|TAS|ACT|NT)?\s*\d{0,4}$", sub, re.IGNORECASE):
        parts = [p.strip() for p in addr.split(",") if p.strip()]
        if parts:
            p0 = parts[0]
            m_road = re.search(r"\b(?:Road|Rd|Street|St|Avenue|Ave|Highway|Hwy|Boulevard|Bvd|Drive|Dr|Place|Pl|Parade|Pde|Lane|Ln|Crescent|Cres|Way)\s+([A-Za-z\s]+)$", p0, re.IGNORECASE)
            if m_road:
                cand = m_road.group(1).strip()
                cand_clean = re.sub(r"\b(NSW|VIC|QLD|WA|SA|TAS|ACT|NT|\d{4}|Australia)\b", "", cand, flags=re.IGNORECASE).strip()
                if cand_clean:
                    sub = cand_clean

    return st, sub


def extract_gross_rate_per_kwh(details_json: dict) -> float:
    """Extracts consumer-facing gross rate per kWh ($/kWh) following Gross-First architecture."""
    if not isinstance(details_json, dict):
        return 0.0

    cd = (details_json.get("cost_description") or "").strip()
    m = re.search(r"Price per kWh:\s*\$?([0-9.]+)", cd, re.I)
    if not m:
        m = re.search(r"([0-9.]+)\s*/\s*kWh", cd, re.I)
    if m:
        try:
            return round(float(m.group(1)), 2)
        except ValueError:
            pass

    for st in details_json.get("stations", []):
        st_cd = (st.get("cost_description") or "").strip()
        m = re.search(r"([0-9.]+)\s*/\s*kWh", st_cd, re.I)
        if m:
            try:
                return round(float(m.group(1)), 2)
            except ValueError:
                pass
        for o in st.get("outlets", []):
            for pr in o.get("prices", []):
                for el in pr.get("elements", []):
                    for pc in el.get("price_components", []):
                        if pc.get("type") == "ENERGY" and pc.get("price") is not None:
                            try:
                                return round(float(pc["price"]), 2)
                            except (ValueError, TypeError):
                                pass
    return 0.0


def plugshare_to_registry_record(basic_or_detail: dict, full_detail: Optional[dict] = None) -> Tuple[str, dict]:
    """Converts a PlugShare station payload into the dedicated plugshare_chargers.json schema."""
    d = full_detail if full_detail else basic_or_detail
    loc_id = d.get("id")
    name = (d.get("name") or f"PlugShare Station {loc_id}").strip()

    network = "3rd-Party"
    maj_net_id = d.get("majority_network_id")
    if maj_net_id and maj_net_id in PLUGSHARE_NETWORKS:
        network = PLUGSHARE_NETWORKS[maj_net_id]
    else:
        for st in d.get("stations", []):
            st_net = st.get("network_id")
            if st_net and st_net in PLUGSHARE_NETWORKS:
                network = PLUGSHARE_NETWORKS[st_net]
                break
            if isinstance(st.get("network"), dict) and st["network"].get("name"):
                network = st["network"]["name"]
                break

    name_lower = name.lower()
    if "exploren" in name_lower:
        network = "Exploren"
    elif "bp pulse" in name_lower or "bp " in name_lower:
        network = "BP Pulse"
    elif "evie" in name_lower:
        network = "Evie Networks"
    elif "chargefox" in name_lower:
        network = "Chargefox"
    elif "nrma" in name_lower:
        network = "NRMA"
    elif "jolt" in name_lower:
        network = "JOLT"
    elif "ampcharge" in name_lower or "ampol" in name_lower:
        network = "AmpCharge"
    elif "tesla supercharger" in name_lower:
        network = "Tesla Supercharger"
    elif "tesla destination" in name_lower:
        network = "Tesla Destination"
    elif "tesla" in name_lower:
        network = "Tesla"

    lat = d.get("latitude")
    lon = d.get("longitude")
    addr_str = d.get("address") or ""

    connectors: Set[str] = set()
    max_kw = 0.0
    stalls_count = d.get("station_count") or len(d.get("stations", [])) or 1
    is_fast = bool(d.get("is_fast_charger"))

    outlet_counts: Dict[Tuple[str, float, bool], int] = {}
    for st in d.get("stations", []):
        for o in st.get("outlets", []):
            c_code = o.get("connector")
            c_name = PLUGSHARE_CONNECTORS.get(c_code, o.get("connector_name"))
            if c_name:
                connectors.add(c_name)
            else:
                c_name = "CCS2"
            kw = float(o.get("kilowatts") or o.get("power") or 0.0)
            if kw > max_kw:
                max_kw = kw
            is_dc = bool(o.get("is_dc")) or kw >= 25.0
            if is_dc:
                is_fast = True
            key = (c_name, kw, is_dc)
            outlet_counts[key] = outlet_counts.get(key, 0) + 1

    connector_breakdown = []
    for (c_name, kw, is_dc), count in sorted(outlet_counts.items(), key=lambda x: (-x[0][1], x[0][0])):
        if kw > 0:
            kw_val = round(kw, 1)
            kw_str = f"{int(kw_val)}kW" if kw_val.is_integer() else f"{kw_val:.0f}kW"
            dc_str = "DC " if is_dc else "AC "
            connector_breakdown.append(f"{count} x {c_name} ({dc_str}{kw_str})")
        else:
            connector_breakdown.append(f"{count} x {c_name}")

    if not connectors and d.get("connector_types"):
        connectors = set(d["connector_types"])

    if not connectors:
        connectors.add("CCS2")

    gross_rate = extract_gross_rate_per_kwh(d)
    idle_fee = 0.0
    cd = d.get("cost_description") or ""
    m_idle = re.search(r"Idle fee per minute:\s*\$?([0-9.]+)", cd, re.I)
    if m_idle:
        try:
            idle_fee = round(float(m_idle.group(1)), 2)
        except ValueError:
            pass

    keywords: Set[str] = set()
    keywords.add(name)
    for part in name.split():
        if len(part) >= 3 and not part.isdigit():
            keywords.add(part)
    if addr_str:
        for part in addr_str.split(","):
            part_clean = part.strip()
            if len(part_clean) >= 4 and not part_clean.isdigit():
                keywords.add(part_clean)
    if network and network != "3rd-Party":
        keywords.add(network)

    clean_title = re.sub(r"\s*-\s*\d+\s*$", "", name)
    station_key = clean_title

    acc_code = d.get("access")
    is_res = bool(d.get("is_residential")) or acc_code == 3
    is_coming_soon = bool(d.get("coming_soon")) or bool(d.get("under_construction")) or str(d.get("status", "")).upper() == "COMING_SOON" or "coming soon" in name.lower() or "under construction" in name.lower()
    if is_res or acc_code == 3 or is_coming_soon:
        return "", {}

    # BUG-088 (2026-09-15): PlugShare's `access` field (1/2/3) is only a
    # coarse base classification - a station can ALSO carry a separate
    # `access_restrictions` list (e.g. ["EMPLOYEES_ONLY"], with a matching
    # human-readable `access_restriction_descriptions`) that PlugShare's
    # own site renders as an ADDITIONAL red "RESTRICTED"/aqua "EMPLOYEES
    # ONLY" badge on top of the base access level. Real user report:
    # plugshare.com/location/525387 (Mortlake Slipway) has access=2 (the
    # bare "Customer / Patron" mapping below), but ALSO carries
    # access_restrictions=["EMPLOYEES_ONLY"] - confirmed live against
    # PlugShare's own API - so this tool was showing it as ordinary
    # customer-accessible when it's actually staff-only. The restriction
    # descriptions, when present, now take priority over the base code.
    restriction_descs = d.get("access_restriction_descriptions") or []
    if not restriction_descs:
        restriction_items = d.get("access_restriction_items") or []
        restriction_descs = [
            it.get("description") for it in restriction_items
            if isinstance(it, dict) and it.get("description")
        ]

    access_str = "Public" if acc_code == 1 else ("Customer / Patron" if acc_code == 2 else "Restricted")
    if restriction_descs:
        access_str = f"Restricted ({', '.join(restriction_descs)})"
    if d.get("open247"):
        access_str += " 24/7"

    now_iso = _get_utc_now_iso()
    state, suburb = extract_au_state_and_suburb(addr_str)

    record = {
        "plugshare_metadata": {
            "id": loc_id,
            "name": name,
            "network": network,
            "score": d.get("score"),
            "url": f"https://www.plugshare.com/location/{loc_id}",
            "keywords": sorted(list(keywords))
        },
        "location": {
            "address": addr_str,
            "street": addr_str.split(",")[0].strip() if addr_str else "",
            "suburb": suburb,
            "state": state,
            "postcode": "",
            "country": "Australia",
            "country_code": "AU",
            "lat": lat,
            "lon": lon,
            "radius_m": 150
        },
        "hardware": {
            "stalls": stalls_count,
            "max_power_kw": round(max_kw, 1) if max_kw > 0 else (150.0 if is_fast else 22.0),
            "connector_types": sorted(list(connectors)),
            "connector_breakdown": connector_breakdown
        },
        "compatibility": {
            "open_to_non_tesla": True,
            "tesla_only": False,
            "access": access_str
        },
        "access": {
            "hours": d.get("hours") or ("Available 24/7" if d.get("open247") else None),
            "description": d.get("description") or d.get("cost_description")
        },
        "tariffs": {
            "currency": "AUD",
            "per_kwh_flat": gross_rate,
            "idle_fee_per_min": idle_fee,
            "has_tou_pricing": False
        },
        "amenities": {
            "parking": True,
            "restrooms": True,
            "shopping": True,
            "dining": True
        },
        "first_seen": now_iso,
        "last_updated": now_iso,
        "last_verified": now_iso,
        "valid_from": now_iso
    }
    return station_key, record


def record_dict_to_unified(key: str, info: dict) -> Optional[UnifiedStationRecord]:
    """Converts a dict from plugshare_chargers.json to a UnifiedStationRecord."""
    if not isinstance(info, dict):
        return None

    # Location block
    loc = info.get("location", {}) if isinstance(info.get("location"), dict) else {}
    lat = loc.get("lat") or loc.get("latitude") or info.get("lat") or info.get("latitude")
    lon = loc.get("lon") or loc.get("longitude") or loc.get("lng") or info.get("lon") or info.get("longitude") or info.get("lng")
    if lat is None or lon is None:
        return None
    try:
        lat = float(lat)
        lon = float(lon)
    except (ValueError, TypeError):
        return None

    # Metadata block
    ps_meta = info.get("plugshare_metadata", {}) if isinstance(info.get("plugshare_metadata"), dict) else {}
    name = ps_meta.get("name") or info.get("name") or key
    network = ps_meta.get("network") or info.get("network") or info.get("operator") or "Public"

    # Hardware block
    hw = info.get("hardware", {}) if isinstance(info.get("hardware"), dict) else {}
    max_kw = hw.get("max_power_kw") or hw.get("max_kw") or info.get("max_power_kw") or info.get("max_kw") or 0.0
    try:
        max_kw = float(max_kw)
    except (ValueError, TypeError):
        max_kw = 0.0

    speed_tier = "dc_fast" if max_kw >= 25.0 else "ac_destination"
    # "Supercharger" is a Tesla-specific hardware/network term - matching it
    # anywhere in the network OR station name string was misclassifying
    # non-Tesla stations whose name happens to contain the word colloquially
    # (e.g. PlugShare's own "Coochin Creek Evie supercharger" entry, an Evie
    # AC destination charger, was showing up under --sc/tesla_superchargers
    # filters). Now requires "tesla" AND "supercharger" together in the
    # network string specifically. Fixed 2026-09-14.
    if "tesla" in network.lower() and "supercharger" in network.lower():
        speed_tier = "supercharger"

    # Tariffs block
    tariffs = info.get("tariffs", {}) if isinstance(info.get("tariffs"), dict) else {}
    flat_rate = tariffs.get("per_kwh_flat") or info.get("rate_per_kwh")
    tariff_str = str(info.get("tariff", "") or info.get("cost_description", "")).strip()

    current_rate = None
    rate_range = tariff_str
    pricing_model = "unknown"

    if flat_rate is not None:
        try:
            current_rate = float(flat_rate)
            pricing_model = "flat"
            if not rate_range:
                rate_range = f"${current_rate:.2f}/kWh"
        except (ValueError, TypeError):
            pass

    if "free" in tariff_str.lower() or flat_rate == 0.0:
        pricing_model = "free"
        current_rate = 0.0
        rate_range = "Free"
    elif not current_rate and tariff_str:
        m = re.search(r"\$([0-9]+\.[0-9]+)", tariff_str)
        if m:
            current_rate = float(m.group(1))
            pricing_model = "flat" if "-" not in tariff_str else "time_of_use"

    # Stalls & Plugs
    stalls = hw.get("stalls") or info.get("stalls") or info.get("outlets_count") or info.get("stalls_total") or 1
    try:
        stalls = int(stalls)
    except (ValueError, TypeError):
        stalls = 1

    plugs = hw.get("connector_types") or info.get("plugs") or info.get("connectors") or []
    if isinstance(plugs, str):
        plugs = [p.strip() for p in plugs.split(",") if p.strip()]

    address_str = loc.get("address") or info.get("address", "")
    suburb = loc.get("suburb") or info.get("suburb", "") or loc.get("city", "") or info.get("city", "")
    state = loc.get("state") or info.get("state", "")
    access_dict = info.get("access", {}) if isinstance(info.get("access"), dict) else {}
    compat_dict = info.get("compatibility", {}) if isinstance(info.get("compatibility"), dict) else {}
    acc_type = access_dict.get("access") or compat_dict.get("access") or "Public 24/7"
    if "private" in str(acc_type).lower():
        return None

    is_cs = (
        bool(info.get("coming_soon")) or
        bool(info.get("under_construction")) or
        bool(ps_meta.get("coming_soon")) or
        bool(ps_meta.get("under_construction")) or
        str(info.get("status", "")).upper() == "COMING_SOON" or
        "coming soon" in name.lower() or
        "under construction" in name.lower() or
        "in development" in name.lower() or
        "planned" in name.lower()
    )

    metadata = {
        "access_type": acc_type,
        "hours": access_dict.get("hours") or "Available 24/7",
        "description": access_dict.get("description", ""),
        "score": ps_meta.get("score") if ps_meta.get("score") is not None else info.get("score"),
        "cost_description": tariffs.get("cost_description") or info.get("cost_description") or "",
        "hardware_tier": hw.get("tier", ""),
        "connector_breakdown": hw.get("connector_breakdown", []),
        "open_to_non_tesla": compat_dict.get("open_to_non_tesla", True),
        "tesla_only": compat_dict.get("tesla_only", False),
        "coming_soon": is_cs,
        "under_construction": is_cs,
        "status": "COMING_SOON" if is_cs else "OPEN",
    }

    return UnifiedStationRecord(
        key=f"PlugShare_{key}",
        name=name,
        network=network,
        operator_name=network,
        address=address_str,
        suburb=suburb,
        state=str(state).upper(),
        postcode=str(loc.get("postcode") or info.get("postcode", "")),
        country=loc.get("country") or info.get("country", "Australia"),
        lat=lat,
        lon=lon,
        timezone=info.get("timezone", "Australia/Sydney"),
        speed_tier=speed_tier,
        max_power_kw=max_kw,
        stalls_total=stalls,
        stalls_available=None,
        plugs=plugs,
        tethered=True,
        hardware_model=hw.get("tier", ""),
        pricing_model=pricing_model,
        current_rate_per_kwh=current_rate,
        rate_range=rate_range,
        schedule=[],
        idle_fee_per_min=tariffs.get("idle_fee_per_min"),
        tier=2,
        source="plugshare",
        source_id=str(ps_meta.get("id") or info.get("id") or key),
        url=ps_meta.get("url") or info.get("url", f"https://www.plugshare.com/location/{ps_meta.get('id', '')}"),
        last_verified_utc=info.get("last_verified", "") or info.get("last_verified_utc", ""),
        metadata=metadata,
        amenities=info.get("amenities", {}) if isinstance(info.get("amenities"), dict) else {"items": info.get("amenities", [])},
        keywords=ps_meta.get("keywords", []) if isinstance(ps_meta.get("keywords"), list) else [],
        views={"plugshare": info}
    )


# -----------------------------------------------------------------------------
# PlugShare Live API Client
# -----------------------------------------------------------------------------

class PlugShareClient:
    BASE_URL = "https://api.plugshare.com/v3"
    AUTH_HEADER = "Basic d2ViX3YyOkVOanNuUE54NHhXeHVkODU="

    def __init__(self, headers: Optional[dict] = None):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Authorization": self.AUTH_HEADER,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.plugshare.com",
            "Referer": "https://www.plugshare.com/map/australia"
        }
        if headers:
            self.headers.update(headers)
        self.last_error: Optional[str] = None

    def _get(self, endpoint: str, params: Optional[dict] = None, timeout: int = 15, retries: int = 1, backoff: float = 1.5) -> Any:
        url = f"{self.BASE_URL}{endpoint}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        self.last_error = None
        attempt = 0
        while True:
            req = urllib.request.Request(url, headers=self.headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    self.last_error = "HTTP 404 (not found)"
                    return None
                self.last_error = f"HTTP {e.code}" + (" (likely rate-limited/blocked)" if e.code in (403, 429) else "")
            except urllib.error.URLError as e:
                self.last_error = f"network error: {e.reason}"
            except TimeoutError:
                self.last_error = "request timed out"
            except json.JSONDecodeError:
                self.last_error = "non-JSON response (likely a rate-limit / bot-challenge page)"
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
            attempt += 1
            if attempt > retries:
                return None
            time.sleep(backoff * attempt + random.uniform(0, 0.5))

    def get_location_details(self, location_id: Any) -> Optional[dict]:
        """Fetches complete station record including tariffs, hardware, hours, and amenities."""
        return self._get(f"/locations/{location_id}")

    def search_region(self, lat: float, lon: float, span_lat: float = 0.1, span_lng: float = 0.1, count: int = 250, access: str = "1,2") -> List[dict]:
        """Fetches station clusters across a bounding box region (defaults to public & customer locations, excluding residential)."""
        params = {
            "latitude": lat,
            "longitude": lon,
            "spanLat": span_lat,
            "spanLng": span_lng,
            "count": count,
            "access": access,
            "minimal": 0
        }
        data = self._get("/locations/region", params=params)
        return data if isinstance(data, list) else []

    def search_by_text(self, query: str) -> List[dict]:
        """Uses PlugShare search API to search by name/query."""
        params = {"query": query}
        data = self._get("/locations/search", params=params)
        return data if isinstance(data, list) else []

    def search_nearby(self, lat: float, lon: float, distance_km: float = 15, limit: int = 50) -> List[dict]:
        """Queries PlugShare using a tiled grid of small bounding boxes (0.05° deg)."""
        TILE_DEG = 0.05
        deg_lat = (distance_km * 2.0) / 111.0
        deg_lng = (distance_km * 2.0) / 92.0
        lat_min = lat - deg_lat / 2
        lon_min = lon - deg_lng / 2

        rows = math.ceil(deg_lat / TILE_DEG) + 1
        cols = math.ceil(deg_lng / TILE_DEG) + 1
        tiles = [
            (lat_min + r * TILE_DEG, lon_min + c * TILE_DEG)
            for r in range(rows)
            for c in range(cols)
        ]

        def _fetch_tile(t):
            return self.search_region(t[0], t[1], span_lat=TILE_DEG, span_lng=TILE_DEG, count=250)

        with ThreadPoolExecutor(max_workers=10) as ex:
            tile_results = list(ex.map(_fetch_tile, tiles))

        seen = set()
        all_stations = []
        for res in tile_results:
            for s in res:
                sid = s.get("id")
                if sid and sid not in seen:
                    seen.add(sid)
                    all_stations.append(s)

        filtered = []
        for loc in all_stations:
            l_lat, l_lon = loc.get("latitude"), loc.get("longitude")
            if l_lat is not None and l_lon is not None:
                d_km = haversine_km(lat, lon, l_lat, l_lon)
                if d_km <= distance_km:
                    filtered.append((d_km, loc))
        filtered.sort(key=lambda x: x[0])
        return [loc for _, loc in filtered[:limit]]

    def search_australia(self) -> List[dict]:
        """Fast concurrent discovery across Australian metro and regional centers."""
        def _fetch_region(item):
            name, lat, lon, slat, slon = item
            return self.search_region(lat, lon, span_lat=slat, span_lng=slon, count=250)

        with ThreadPoolExecutor(max_workers=10) as ex:
            results = list(ex.map(_fetch_region, AUSTRALIAN_SEARCH_REGIONS))

        seen = set()
        stations = []
        for res in results:
            for s in res:
                s_id = s.get("id")
                if s_id and s_id not in seen:
                    seen.add(s_id)
                    stations.append(s)
        return stations

    def search_state(self, state_code: str) -> List[dict]:
        """Discovers stations across a given Australian state."""
        st = state_code.upper()
        if st not in AUSTRALIAN_STATES_BOUNDS:
            return []
        cfg = AUSTRALIAN_STATES_BOUNDS[st]
        matching_regions = [r for r in AUSTRALIAN_SEARCH_REGIONS if st in r[0] or cfg["name"].split()[0] in r[0]]
        if not matching_regions:
            matching_regions = [(cfg["name"], cfg["lat"], cfg["lon"], cfg["spanLat"], cfg["spanLng"])]

        def _fetch_reg(item):
            name, lat, lon, slat, slon = item
            return self.search_region(lat, lon, span_lat=slat, span_lng=slon, count=250)

        with ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(_fetch_reg, matching_regions))

        seen = set()
        stations = []
        for res in results:
            for s in res:
                s_id = s.get("id")
                if s_id and s_id not in seen:
                    seen.add(s_id)
                    stations.append(s)
        return stations


# -----------------------------------------------------------------------------
# Main PlugShare Provider Class
# -----------------------------------------------------------------------------

class PlugShareProvider(BaseChargerProvider):
    name: str = "PlugShare"
    tier: int = 2  # Multi-network Aggregator
    # BUG-079: unlike the other aggregators, PlugShare CAN assign
    # speed_tier="supercharger" - see the "tesla" + "supercharger" network-name
    # check near line 433, added to correctly cross-list PlugShare's own
    # listing of a real Tesla Supercharger (while still rejecting false
    # positives like a non-Tesla "... supercharger"-named AC destination
    # charger). So PlugShare must stay in the "possible" set here rather than
    # being skippable under a --sc query, even though it's the rarer case.
    # PlugShare's load_cached_registry() only reads a local JSON file (no
    # live network calls), so keeping it loaded costs nothing.
    POSSIBLE_SPEED_TIERS = {"dc_fast", "ac_destination", "supercharger"}

    def __init__(self, registry_path: Optional[str] = None):
        self.registry_path = registry_path or _resolve_plugshare_registry_path()
        self._cache: Optional[List[UnifiedStationRecord]] = None
        self.client = PlugShareClient()

    # --- Local Registry Operations ---

    def load_raw_registry(self) -> Dict[str, dict]:
        """Loads raw dictionary of stations from plugshare_chargers.json."""
        if not os.path.isfile(self.registry_path):
            return {}
        try:
            with open(self.registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                if "stations" in data and isinstance(data["stations"], dict):
                    return data["stations"]
                return data
            elif isinstance(data, list):
                return {s.get("name", f"Station_{i}"): s for i, s in enumerate(data)}
        except Exception:
            pass
        return {}

    def save_raw_registry(self, data: Dict[str, dict]) -> bool:
        """Saves dictionary of stations to plugshare_chargers.json.

        AGENTS.md BUG-067: this used to catch a bare `Exception` around
        the write and just return False - most of THIS function's own
        callers (sync_nearby/sync_state/sync_australia/refresh_prices,
        each called from charger_find.py) never check that return
        value, so a save failure used to disappear silently rather than
        surfacing as a sync error. Now writes atomically
        (atomic_write_json(), base.py) and lets a real failure raise -
        charger_find.py's per-provider try/except is where that should
        be caught and reported, not swallowed twice."""
        atomic_write_json(self.registry_path, data)
        self._cache = None  # Invalidate cache
        return True

    def load_cached_registry(self) -> List[UnifiedStationRecord]:
        """Loads and normalizes stations from plugshare_chargers.json."""
        if self._cache is not None:
            return self._cache

        st_dict = self.load_raw_registry()
        records: List[UnifiedStationRecord] = []
        for key, info in st_dict.items():
            rec = record_dict_to_unified(key, info)
            if rec:
                records.append(rec)

        self._cache = records
        return self._cache

    def find_existing_match(self, station_key: str, record: dict, registry: Optional[Dict[str, dict]] = None) -> Tuple[Optional[str], Optional[dict]]:
        """Finds matching entry in local registry by ID, exact key, or spatial proximity."""
        if registry is None:
            registry = self.load_raw_registry()
        new_id = record.get("plugshare_metadata", {}).get("id")

        # 1. Authoritative PlugShare ID match
        if new_id:
            for k, v in registry.items():
                if v.get("plugshare_metadata", {}).get("id") == new_id:
                    return k, v

        def _id_conflicts(v):
            existing_id = v.get("plugshare_metadata", {}).get("id")
            return bool(existing_id) and bool(new_id) and existing_id != new_id

        # 2. Key match without ID conflict
        if station_key in registry and not _id_conflicts(registry[station_key]):
            return station_key, registry[station_key]
        for k, v in registry.items():
            if k.lower() == station_key.lower() and not _id_conflicts(v):
                return k, v

        # 3. Spatial proximity fallback (< 200m)
        new_lat = record.get("location", {}).get("lat")
        new_lon = record.get("location", {}).get("lon")
        if new_lat is not None and new_lon is not None:
            new_name_tokens = set(re.findall(r"\w+", station_key.lower()))
            for k, v in registry.items():
                if _id_conflicts(v):
                    continue
                ex_lat = v.get("location", {}).get("lat")
                ex_lon = v.get("location", {}).get("lon")
                if ex_lat is not None and ex_lon is not None:
                    dist = haversine_km(new_lat, new_lon, ex_lat, ex_lon)
                    if dist < 0.20:
                        ex_tokens = set(re.findall(r"\w+", k.lower()))
                        meaningful = {t for t in (new_name_tokens & ex_tokens) if len(t) >= 3}
                        if meaningful:
                            return k, v
        return None, None

    def add_or_update(self, station_key: str, record: dict, registry: Optional[Dict[str, dict]] = None) -> str:
        """Adds or updates a station record in the local registry."""
        meta = record.get("plugshare_metadata", {}) or {}
        loc = record.get("location", {}) or {}
        has_identity = bool(meta.get("id")) or bool(loc.get("address"))
        has_coords = loc.get("lat") is not None or loc.get("lon") is not None
        if not (has_identity or has_coords):
            return "REJECTED_EMPTY"

        compat = record.get("compatibility", {}) or {}
        acc_val = str(compat.get("access", "") or record.get("access", {}).get("access", "")).lower()
        if "private" in acc_val:
            return "REJECTED_PRIVATE"

        save_internally = False
        if registry is None:
            registry = self.load_raw_registry()
            save_internally = True

        now_utc = _get_utc_now_iso()
        existing_key, existing = self.find_existing_match(station_key, record, registry)

        if existing:
            # Check if tariffs or hardware changed
            if detect_station_changes(existing, record):
                archive_path = os.path.join(os.path.dirname(self.registry_path), "plugshare_chargers_archived.json")
                archive_station_record(archive_path, existing_key or station_key, existing, now_utc)

            ex_rate = existing.get("tariffs", {}).get("per_kwh_flat", 0.0)
            if ex_rate > 0 and record.get("tariffs", {}).get("per_kwh_flat", 0.0) == 0:
                record["tariffs"] = existing["tariffs"]
            ex_keywords = set(existing.get("plugshare_metadata", {}).get("keywords", []))
            new_keywords = set(record.get("plugshare_metadata", {}).get("keywords", []))
            record["plugshare_metadata"]["keywords"] = sorted(list(ex_keywords | new_keywords))
            record["first_seen"] = existing.get("first_seen", now_utc)
            record["last_updated"] = now_utc
            record["last_verified"] = now_utc
            registry[existing_key] = record
            result = "UPDATED"
        else:
            final_key = station_key
            if final_key in registry:
                new_id = record.get("plugshare_metadata", {}).get("id")
                network_hint = record.get("plugshare_metadata", {}).get("network") or ""
                if network_hint and network_hint != "3rd-Party":
                    candidate = f"{station_key} ({network_hint})"
                elif new_id:
                    candidate = f"{station_key} (PlugShare #{new_id})"
                else:
                    candidate = f"{station_key} (2)"
                n = 2
                while candidate in registry:
                    candidate = f"{station_key} ({n})"
                    n += 1
                final_key = candidate

            record["first_seen"] = now_utc
            record["last_updated"] = now_utc
            record["last_verified"] = now_utc
            registry[final_key] = record
            result = "CREATED"

        if save_internally:
            if self.save_raw_registry(registry):
                return result
            return "ERROR"
        return result

    # --- Live Sync Operations ---

    def sync_location(self, location_id: Any, auto_save: bool = True) -> Optional[UnifiedStationRecord]:
        """Fetches full details for a single PlugShare location and optionally persists it."""
        full_d = self.client.get_location_details(location_id)
        if not full_d or not isinstance(full_d, dict):
            return None
        has_identity = bool(full_d.get("id") or full_d.get("name"))
        has_location = full_d.get("latitude") is not None or full_d.get("longitude") is not None or full_d.get("address")
        if not (has_identity or has_location):
            return None

        key, record = plugshare_to_registry_record(full_d)
        if auto_save:
            self.add_or_update(key, record)

        return record_dict_to_unified(key, record)

    def sync_nearby(self, lat: float, lon: float, radius_km: float = 15.0, limit: int = 250, auto_save: bool = False) -> List[UnifiedStationRecord]:
        """Discovers stations near coordinates using tiled grid search and optionally persists them."""
        raw_list = self.client.search_nearby(lat, lon, distance_km=radius_km, limit=limit)
        results: List[UnifiedStationRecord] = []
        raw_reg = self.load_raw_registry() if auto_save else None

        for raw in raw_list:
            key, record = plugshare_to_registry_record(raw)
            if auto_save and raw_reg is not None:
                self.add_or_update(key, record, raw_reg)
            rec = record_dict_to_unified(key, record)
            if rec:
                rec.distance_km = round(haversine_km(lat, lon, rec.lat, rec.lon), 2)
                results.append(rec)

        if auto_save and raw_reg is not None:
            self.save_raw_registry(raw_reg)

        results.sort(key=lambda s: s.distance_km if s.distance_km is not None else 999999)
        return results

    def sync_state(self, state_code: str, auto_save: bool = False) -> List[UnifiedStationRecord]:
        """Discovers stations across a given state."""
        raw_list = self.client.search_state(state_code)
        results: List[UnifiedStationRecord] = []
        raw_reg = self.load_raw_registry() if auto_save else None

        for raw in raw_list:
            key, record = plugshare_to_registry_record(raw)
            if auto_save and raw_reg is not None:
                self.add_or_update(key, record, raw_reg)
            rec = record_dict_to_unified(key, record)
            if rec:
                results.append(rec)

        if auto_save and raw_reg is not None:
            self.save_raw_registry(raw_reg)

        return results

    def sync_australia(self, auto_save: bool = False) -> List[UnifiedStationRecord]:
        """Discovers stations across all major metro/regional areas in Australia."""
        raw_list = self.client.search_australia()
        results: List[UnifiedStationRecord] = []
        raw_reg = self.load_raw_registry() if auto_save else None

        for raw in raw_list:
            key, record = plugshare_to_registry_record(raw)
            if auto_save and raw_reg is not None:
                self.add_or_update(key, record, raw_reg)
            rec = record_dict_to_unified(key, record)
            if rec:
                results.append(rec)

        if auto_save and raw_reg is not None:
            self.save_raw_registry(raw_reg)

        return results

    def sync_text(self, query: str, auto_save: bool = False) -> List[UnifiedStationRecord]:
        """Searches PlugShare online by keyword/text."""
        raw_list = self.client.search_by_text(query)
        results: List[UnifiedStationRecord] = []
        raw_reg = self.load_raw_registry() if auto_save else None

        for raw in raw_list:
            key, record = plugshare_to_registry_record(raw)
            if auto_save and raw_reg is not None:
                self.add_or_update(key, record, raw_reg)
            rec = record_dict_to_unified(key, record)
            if rec:
                results.append(rec)

        if auto_save and raw_reg is not None:
            self.save_raw_registry(raw_reg)

        return results

    def refresh_prices(self, limit: Optional[int] = None) -> Tuple[int, int, int]:
        """
        Iterates over all stations in plugshare_chargers.json that have missing/zero flat rates,
        queries the live detail endpoint, and persists updated pricing.
        Returns (refreshed_count, total_missing, failed_count).
        """
        registry = self.load_raw_registry()
        missing_keys = []
        for k, v in registry.items():
            rate = v.get("tariffs", {}).get("per_kwh_flat", 0.0) or 0.0
            loc_id = v.get("plugshare_metadata", {}).get("id")
            if (not rate or rate == 0.0) and loc_id:
                missing_keys.append((k, loc_id, v))

        if limit and len(missing_keys) > limit:
            missing_keys = missing_keys[:limit]

        total_missing = len(missing_keys)
        if total_missing == 0:
            return 0, 0, 0

        refreshed = 0
        failed = 0

        # BUG-008 follow-up (2026-09-15, AGENTS.local.md): the original pacing
        # here was a flat 1.5-2.5s between every request, with only a single
        # one-off 5-7s pause right after a 429/rate-limit hit before
        # immediately resuming at the SAME flat pace - which just re-tripped
        # PlugShare's server-side throttling again almost immediately, so a
        # batched run kept leaving most stations unpriced (confirmed by the
        # user). `current_delay` now escalates for the REST of the run on
        # each rate-limit hit (doubling, capped at `max_delay`) so the
        # request rate actually drops under whatever threshold PlugShare is
        # enforcing, and eases back toward the base pace only after a
        # sustained run of successes (so it doesn't immediately re-trip the
        # limit the moment it recovers).
        base_delay = 1.5
        max_delay = 20.0
        current_delay = base_delay
        consecutive_successes = 0

        # BUG-008 follow-up: checkpoint the registry periodically instead of
        # only once at the very end - a rate-limited run can take a long
        # time, and losing every successful refresh from a run that was
        # interrupted (Ctrl-C, a crash, or the user giving up partway
        # through) forced a full re-run from scratch. Cheap to do (atomic
        # write of a JSON file this size), so checkpoint often.
        checkpoint_every = 10
        since_last_save = 0

        for i, (k, loc_id, rec_data) in enumerate(missing_keys, 1):
            full_d = self.client.get_location_details(loc_id)
            has_identity = isinstance(full_d, dict) and (full_d.get("id") or full_d.get("name") or full_d.get("latitude") is not None)
            if not has_identity:
                failed += 1
                was_rate_limited = bool(
                    self.client.last_error
                    and ("rate-limited" in self.client.last_error or "HTTP 429" in self.client.last_error)
                )
                if was_rate_limited:
                    consecutive_successes = 0
                    current_delay = min(current_delay * 2.0, max_delay)
                    time.sleep(current_delay + random.uniform(0, 2.0))
                else:
                    time.sleep(current_delay + random.uniform(0, 1.0))
                continue

            consecutive_successes += 1
            if consecutive_successes >= 5 and current_delay > base_delay:
                current_delay = max(base_delay, current_delay / 1.5)
                consecutive_successes = 0

            new_key, new_rec = plugshare_to_registry_record(rec_data, full_detail=full_d)
            new_rate = new_rec.get("tariffs", {}).get("per_kwh_flat", 0.0)
            if new_rate > 0:
                refreshed += 1
            self.add_or_update(k, new_rec, registry)
            since_last_save += 1

            if since_last_save >= checkpoint_every:
                self.save_raw_registry(registry)
                since_last_save = 0

            time.sleep(current_delay + random.uniform(0, 1.0))

        if since_last_save > 0:
            self.save_raw_registry(registry)
        return refreshed, total_missing, failed

    def discover_from_history(self, charges_master_path: Optional[str] = None) -> List[UnifiedStationRecord]:
        """Scans charges_master.csv for unregistered 3rd-party charging sessions and searches PlugShare."""
        registry = self.load_raw_registry()
        master_path = charges_master_path

        if not master_path:
            tessie_home = None
            try:
                from lib.tessie_api_common import resolve_tessie_data_home
                tessie_home = resolve_tessie_data_home()
            except Exception:
                pass

            charges_candidates = []
            if tessie_home:
                charges_candidates.extend([
                    os.path.join(tessie_home, "charges", "charges_master.csv"),
                    os.path.join(tessie_home, "charges_master.csv"),
                ])
            charges_candidates.extend([
                os.path.join(_repo_root, "Tessie", "charges", "charges_master.csv"),
                os.path.join(_repo_root, "Tessie", "charges_master.csv"),
                os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie/charges/charges_master.csv"),
                os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie/charges_master.csv"),
                os.path.expanduser("~/iCloud/Tesla/Tessie/charges/charges_master.csv"),
                os.path.expanduser("~/iCloud/Tesla/Tessie/charges_master.csv"),
            ])

            for p in charges_candidates:
                if os.path.isfile(p):
                    master_path = p
                    break

        if not master_path or not os.path.isfile(master_path):
            return []

        unmatched = {}
        try:
            with open(master_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    loc = row.get("Location") or row.get("Saved Location") or ""
                    if not loc or "home" in loc.lower() or "tesla" in loc.lower():
                        continue
                    is_super = str(row.get("Supercharger", "")).strip().lower() == "true"
                    if is_super:
                        continue
                    already_known = False
                    for k in registry:
                        if loc.lower() in k.lower() or k.lower() in loc.lower():
                            already_known = True
                            break
                        for kw in registry[k].get("plugshare_metadata", {}).get("keywords", []):
                            if loc.lower() in kw.lower() or kw.lower() in loc.lower():
                                already_known = True
                                break
                        if already_known:
                            break
                    if not already_known and loc not in unmatched:
                        lat = row.get("Latitude") or row.get("Starting Latitude")
                        lon = row.get("Longitude") or row.get("Starting Longitude")
                        if lat and lon:
                            try:
                                unmatched[loc] = (float(lat), float(lon))
                            except (ValueError, TypeError):
                                pass
        except Exception:
            return []

        discovered: List[UnifiedStationRecord] = []
        for loc_name, (lat, lon) in unmatched.items():
            results = self.client.search_nearby(lat, lon, distance_km=0.5, limit=3)
            if results:
                best = results[0]
                d = self.client.get_location_details(best["id"]) or best
                key, rec = plugshare_to_registry_record(d)
                u_rec = record_dict_to_unified(key, rec)
                if u_rec:
                    discovered.append(u_rec)
        return discovered

    # --- Standard BaseChargerProvider Query Methods ---

    def search_nearby(self, lat: float, lon: float, radius_km: float = 15.0, filters: Optional[Dict[str, Any]] = None) -> List[UnifiedStationRecord]:
        """Finds PlugShare stations within geographic radius."""
        all_stations = self.load_cached_registry()
        results = []

        for st in all_stations:
            dist = haversine_km(lat, lon, st.lat, st.lon)
            if dist <= radius_km:
                st.distance_km = round(dist, 2)
                results.append(st)

        results = filter_station_records(results, filters)
        results.sort(key=lambda s: s.distance_km if s.distance_km is not None else 999999)
        return results

    def search_by_text(self, query: str, filters: Optional[Dict[str, Any]] = None) -> List[UnifiedStationRecord]:
        """Searches PlugShare stations by name, suburb, or network."""
        all_stations = self.load_cached_registry()
        q_clean = query.lower().strip()
        results = []
        for st in all_stations:
            searchable = f"{st.name} {st.suburb} {st.state} {st.address} {st.network}".lower()
            if q_clean in searchable or any(q_clean in kw.lower() for kw in st.keywords):
                results.append(st)
        return filter_station_records(results, filters)

    def get_station_details(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Fetches PlugShare station record by ID."""
        for st in self.load_cached_registry():
            if st.source_id == station_id or st.key == station_id or st.key == f"PlugShare_{station_id}":
                return st
        return None

