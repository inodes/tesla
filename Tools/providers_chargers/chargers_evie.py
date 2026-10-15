#!/usr/bin/env python3
"""
Evie Networks Charger Provider (Tier 1 First-Party)
===================================================
First-party provider for Evie Networks fast charging infrastructure across Australia.
Connects directly to the Evie Store Search API with local JSON caching in Tessie/evie_chargers.json.
"""

import os
import re
import html
import json
import time
import urllib.request
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone

from .chargers_base import (
    RegistryReadError,
    BaseChargerProvider,
    UnifiedStationRecord,
    haversine_km,
    filter_station_records,
    compute_tax_breakdown,
    detect_station_changes,
    archive_station_record,
    atomic_write_json,
    merge_with_staleness
)

from .chargers_chargefox import resolve_au_state

_script_dir = os.path.dirname(os.path.abspath(__file__))
_tools_dir = os.path.dirname(_script_dir)
_repo_root = os.path.dirname(_tools_dir)

EVIE_STORE_SEARCH_URL = "https://evie.com.au/wp-admin/admin-ajax.php?action=store_search&lat=-25.2744&lng=133.77514&max_results=500&search_radius=5000&autoload=1"

EVIE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Referer": "https://evie.com.au/find-a-charger/",
    "X-Requested-With": "XMLHttpRequest"
}

STATE_NORMALIZATION = {
    "ADELAIDE": "SA",
    "SOUTH AUSTRALIA": "SA",
    "VICTORIA": "VIC",
    "VIC": "VIC",
    "QUEENSLAND": "QLD",
    "QLD": "QLD",
    "NEW SOUTH WALES": "NSW",
    "NSW": "NSW",
    "WESTERN AUSTRALIA": "WA",
    "WA": "WA",
    "TASMANIA": "TAS",
    "TAS": "TAS",
    "AUSTRALIAN CAPITAL TERRITORY": "ACT",
    "ACT": "ACT",
    "NORTHERN TERRITORY": "NT",
    "NT": "NT"
}

CONNECTOR_MAP = {
    "IEC_62196_T2_COMBO": "CCS2",
    "CHADEMO": "CHAdeMO",
    "IEC_62196_T2": "Type 2",
    "COMBO": "CCS2",
    "CCS2": "CCS2",
    "TYPE 2": "Type 2"
}


def _resolve_evie_registry_path() -> str:
    """Finds path to evie_chargers.json in Tessie data directory or local fallback."""
    candidates = []
    tessie_home = None
    try:
        from lib.tessie_api_common import resolve_tessie_data_home
        tessie_home = resolve_tessie_data_home()
    except Exception:
        pass
    if tessie_home:
        candidates.append(os.path.join(tessie_home, "chargers", "evie_chargers.json"))
        
    candidates.append(os.path.join(_repo_root, "resources", "evie_chargers.json"))
    config_path = os.path.join(_repo_root, "config.json")  # TODO-034: root is primary now
    if not os.path.isfile(config_path):
        config_path = os.path.join(_repo_root, "resources", "config.json")  # legacy fallback
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                td = cfg.get("directories", {}).get("data")
                if td:
                    td_exp = os.path.expanduser(td)
                    candidates.insert(0, os.path.join(td_exp, "chargers", "evie_chargers.json"))
        except Exception:
            pass

    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0] if candidates else os.path.join(_repo_root, "resources", "evie_chargers.json")


def _to_24h(t_str: str) -> str:
    """Converts 12-hour AM/PM time string (e.g. '9:00 AM', '9:00 PM') to 24-hour 'HH:MM' format."""
    m = re.match(r"(\d+)(?::(\d+))?\s*(AM|PM)", t_str.strip(), re.IGNORECASE)
    if m:
        h = int(m.group(1))
        mins = int(m.group(2) or 0)
        ampm = m.group(3).upper()
        if ampm == "PM" and h != 12:
            h += 12
        elif ampm == "AM" and h == 12:
            h = 0
        return f"{h:02d}:{mins:02d}"
    return t_str.strip()


def parse_evie_price(price_str: Optional[str]) -> Dict[str, Any]:
    """
    Parses Evie pricing strings into canonical structured tariffs and schedule blocks.
    Supports:
      - TOU: '0.75/kWh + 0.04/kWh (9:00 AM-9:00 PM)'
      - Flat: '0.75/kWh'
    """
    if not price_str or not isinstance(price_str, str):
        return {
            "pricing_model": "unknown",
            "per_kwh_gross": None,
            "rate_range": "",
            "schedule": []
        }

    clean = price_str.strip()

 # Time-of-Use pattern: "0.75/kWh + 0.04/kWh (9:00 AM-9:00 PM)"
    tou_m = re.search(r"(\d+(?:\.\d+)?)/kWh\s*\+\s*(\d+(?:\.\d+)?)/kWh\s*\(([^)]+)\)", clean)
    if tou_m:
        base_rate = float(tou_m.group(1))
        adder = float(tou_m.group(2))
        peak_rate = round(base_rate + adder, 4)
        time_window = tou_m.group(3).strip()

        tw_parts = [p.strip() for p in time_window.split("-")]
        if len(tw_parts) == 2:
            start_24 = _to_24h(tw_parts[0])
            end_24 = _to_24h(tw_parts[1])

            schedule = [
                {
                    "name": "Off-Peak",
                    "label": "Off-Peak",
                    "start": end_24,
                    "end": start_24,
                    "start_time": end_24,
                    "end_time": start_24,
                    "rate": base_rate,
                    "rate_per_kwh": base_rate,
                    "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                },
                {
                    "name": "Peak / Day",
                    "label": "Peak / Day",
                    "start": start_24,
                    "end": end_24,
                    "start_time": start_24,
                    "end_time": end_24,
                    "rate": peak_rate,
                    "rate_per_kwh": peak_rate,
                    "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                }
            ]
            return {
                "pricing_model": "time_of_use",
                "per_kwh_gross": base_rate,
                "rate_range": f"${base_rate:.2f} - ${peak_rate:.2f}/kWh",
                "schedule": schedule
            }

 # Flat rate pattern: "0.75/kWh"
    flat_m = re.search(r"(\d+(?:\.\d+)?)/kWh", clean)
    if flat_m:
        rate = float(flat_m.group(1))
        return {
            "pricing_model": "flat",
            "per_kwh_gross": rate,
            "rate_range": f"${rate:.2f}/kWh",
            "schedule": [
                {
                    "name": "Standard Rate",
                    "label": "Standard Rate",
                    "start": "00:00",
                    "end": "24:00",
                    "start_time": "00:00",
                    "end_time": "24:00",
                    "rate": rate,
                    "rate_per_kwh": rate,
                    "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                }
            ]
        }

    return {
        "pricing_model": "unknown",
        "per_kwh_gross": None,
        "rate_range": clean,
        "schedule": []
    }


def parse_evie_location_record(loc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parses a raw Evie Store Search item into canonical multi-view JSON dictionary.
    """
    evie_id = str(loc.get("id") or "").strip()
    raw_store = loc.get("store") or ""
    clean_name = html.unescape(raw_store).strip()
    clean_name = re.sub(r"\s*–\s*|\s*—\s*", " - ", clean_name)
    clean_name = re.sub(r"\s+", " ", clean_name).strip()

    addr = (loc.get("address") or "").strip()
    city = (loc.get("city") or "").strip()
    raw_state = (loc.get("state") or "").strip()
    postcode = str(loc.get("zip") or "").strip()
    
    try:
        lat = float(loc.get("lat") or 0.0)
    except (ValueError, TypeError):
        lat = 0.0
        
    try:
        lon = float(loc.get("lng") or 0.0)
    except (ValueError, TypeError):
        lon = 0.0

 # Resolve State/Territory
    state = STATE_NORMALIZATION.get(raw_state.upper(), "")
    if not state and lat and lon:
        state = resolve_au_state(lat, lon)
    if not state:
        state = raw_state.upper()

    suburb = city or ""
    if not suburb and addr:
        parts = [p.strip() for p in addr.split(",") if p.strip()]
        if len(parts) >= 2:
            suburb = parts[1]

 # Parse pedestals & connectors
    evie_ch_raw = loc.get("evie_chargers")
    pedestals = []
    if isinstance(evie_ch_raw, str) and evie_ch_raw.strip():
        try:
            pedestals = json.loads(evie_ch_raw)
        except Exception:
            pedestals = []
    elif isinstance(evie_ch_raw, list):
        pedestals = evie_ch_raw

    plugs_set = set()
    powers = []
    all_prices = []
    total_connectors = 0
    available_connectors = 0
    parsed_pedestals = []

    for ped in pedestals:
        p_ref = ped.get("physicalReference", "")
        conns = ped.get("connectors", [])
        parsed_conns = []
        for c in conns:
            total_connectors += 1
            std_raw = c.get("standard", "")
            plug_norm = CONNECTOR_MAP.get(std_raw, std_raw)
            if plug_norm:
                plugs_set.add(plug_norm)

            pwr_w = c.get("maxElectricPowerW")
            if pwr_w and isinstance(pwr_w, (int, float)):
                powers.append(pwr_w / 1000.0)

            status = str(c.get("status") or "").upper()
            if status == "AVAILABLE":
                available_connectors += 1

            price_raw = c.get("price")
            if price_raw:
                all_prices.append(price_raw)

            parsed_conns.append({
                "connector_id": c.get("connectorId"),
                "standard": plug_norm,
                "raw_standard": std_raw,
                "power_kw": (pwr_w / 1000.0) if pwr_w else None,
                "status": status,
                "price_raw": price_raw
            })

        parsed_pedestals.append({
            "physical_reference": p_ref,
            "connectors": parsed_conns
        })

    max_pwr = max(powers) if powers else 50.0
    speed_tier = "dc_fast" if max_pwr >= 25.0 else "ac_destination"

    primary_price_str = all_prices[0] if all_prices else None
    price_info = parse_evie_price(primary_price_str)

    station_key = f"Evie_{evie_id}"
    now_utc = datetime.now(timezone.utc).isoformat()

    full_address = f"{addr}, {suburb} {state} {postcode}".strip(", ")

    tz = "Australia/Sydney"
    if state in ("VIC", "TAS"):
        tz = "Australia/Melbourne" if state == "VIC" else "Australia/Hobart"
    elif state == "QLD":
        tz = "Australia/Brisbane"
    elif state == "SA":
        tz = "Australia/Adelaide"
    elif state == "WA":
        tz = "Australia/Perth"
    elif state == "NT":
        tz = "Australia/Darwin"

    notes = [
        "Evie Networks Fast Charging Station",
        "Download Evie App: https://evie.com.au/how-to-charge/",
        "100% Powered by renewable energy"
    ]
    raw_desc = loc.get("description")
    if raw_desc and str(raw_desc).lower() != "none":
        clean_desc = re.sub(r"<[^>]+>", " ", str(raw_desc)).strip()
        if clean_desc:
            notes.insert(0, clean_desc)

    return {
        "id": station_key,
        "name": clean_name if clean_name.startswith("Evie") else f"Evie {clean_name}",
        "network": "Evie",
        "operator": "Evie Networks",
        "location": {
            "address": full_address,
            "street": addr,
            "suburb": suburb,
            "state": state,
            "postcode": postcode,
            "country": "Australia",
            "country_code": "AU",
            "lat": lat,
            "lon": lon,
            "radius_m": 200,
            "timezone": tz
        },
        "hardware": {
            "speed_tier": speed_tier,
            "stalls": total_connectors or len(pedestals) or 2,
            "stalls_available": available_connectors if total_connectors > 0 else None,
            "max_power_kw": max_pwr,
            "tier": f"{int(max_pwr)}kW" if max_pwr.is_integer() else f"{max_pwr:.0f}kW",
            "connector_types": sorted(list(plugs_set)) if plugs_set else ["CCS2", "CHAdeMO"],
            "tethered": True,
            "pedestals": parsed_pedestals,
            "hardware_models": ["Evie DC Fast Charger"]
        },
        "compatibility": {
            "open_to_non_tesla": True,
            "tesla_only": False,
            "access": "Public 24/7",
            "connector_types": sorted(list(plugs_set)) if plugs_set else ["CCS2", "CHAdeMO"]
        },
        "access": {
            "hours": "Available 24/7",
            "description": "\n".join(notes)
        },
        "tariffs": {
            "currency": "AUD",
            "tax_name": "GST",
            "tax_rate": 0.10,
            "tax_included_in_listed_price": True,
            "pricing_model": price_info["pricing_model"],
            "has_tou_pricing": price_info["pricing_model"] == "time_of_use",
            "per_kwh_gross": price_info["per_kwh_gross"],
            "per_kwh_net": round(price_info["per_kwh_gross"] / 1.10, 4) if price_info["per_kwh_gross"] else None,
            "tax_per_kwh": round(price_info["per_kwh_gross"] - (price_info["per_kwh_gross"] / 1.10), 4) if price_info["per_kwh_gross"] else None,
            "rate_range": price_info["rate_range"],
            "schedule": price_info["schedule"]
        },
        "amenities": {},
        "keywords": ["evie", "evie networks", "dc fast", "ccs2", "chademo", suburb.lower(), state.lower()],
        "aliases": [clean_name, f"Evie {suburb}"],
        "provenance": {
            "tier": 1,
            "sources": ["evie"],
            "source_ids": [evie_id],
            "url": "https://evie.com.au/find-a-charger/",
            "last_verified_utc": now_utc
        },
        "views": {
            "evie": {
                "id": evie_id,
                "store": raw_store,
                "address": addr,
                "city": city,
                "state": raw_state,
                "zip": postcode,
                "lat": lat,
                "lng": lon,
                "pedestals": parsed_pedestals,
                "url": "https://evie.com.au/find-a-charger/"
            }
        },
        "metadata": {
            "evie_id": evie_id,
            "store": clean_name,
            "hardware_tier": f"{int(max_pwr)}kW" if max_pwr.is_integer() else f"{max_pwr:.0f}kW",
            "open_to_non_tesla": True,
            "tesla_only": False,
            "access_type": "Public 24/7",
            "access_hours": "Available 24/7",
            "access_description": "\n".join(notes)
        }
    }


class EvieProvider(BaseChargerProvider):
 # verified via grep 'speed_tier\s*=' - only "dc_fast" or
 # "ac_destination" (by max_power_kw threshold, or a stored hardware
 # speed_tier defaulting to "dc_fast"). Never "supercharger".
    POSSIBLE_SPEED_TIERS = {"dc_fast", "ac_destination"}
    """
    Tier 1 First-Party Authoritative Provider for Evie Networks.
    """
    name: str = "Evie"
    tier: int = 1

    def __init__(self, registry_path: Optional[str] = None):
        self.registry_path = registry_path or _resolve_evie_registry_path()
        self._cache: Optional[List[UnifiedStationRecord]] = None

    def fetch_all_remote(self, timeout: int = 25) -> List[UnifiedStationRecord]:
        """Fetches all live Evie stations directly from Evie Store Search endpoint."""
        req = urllib.request.Request(
            EVIE_STORE_SEARCH_URL,
            headers=EVIE_HEADERS
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)

        records: List[UnifiedStationRecord] = []
        for loc in data:
            parsed = parse_evie_location_record(loc)
            meta = parsed.get("metadata", {})
            loc_data = parsed.get("location", {})
            hw_data = parsed.get("hardware", {})
            tariffs = parsed.get("tariffs", {})
            prov = parsed.get("provenance", {})

            rec = UnifiedStationRecord(
                key=parsed["id"],
                name=parsed["name"],
                network="Evie",
                operator_name="Evie Networks",
                address=loc_data.get("address", ""),
                suburb=loc_data.get("suburb", ""),
                state=loc_data.get("state", ""),
                postcode=loc_data.get("postcode", ""),
                country=loc_data.get("country", "Australia"),
                lat=loc_data.get("lat", 0.0),
                lon=loc_data.get("lon", 0.0),
                timezone=loc_data.get("timezone", "Australia/Sydney"),
                speed_tier=hw_data.get("speed_tier", "dc_fast"),
                max_power_kw=hw_data.get("max_power_kw", 50.0),
                stalls_total=hw_data.get("stalls", 2),
                stalls_available=hw_data.get("stalls_available"),
                plugs=hw_data.get("connector_types", ["CCS2", "CHAdeMO"]),
                tethered=True,
                hardware_model=hw_data.get("hardware_models", ["Evie DC Fast Charger"])[0],
                pricing_model=tariffs.get("pricing_model", "flat"),
                current_rate_per_kwh=tariffs.get("per_kwh_gross"),
                rate_range=tariffs.get("rate_range", ""),
                schedule=tariffs.get("schedule", []),
                currency=tariffs.get("currency", "AUD"),
                tier=1,
                source="evie",
                source_id=str(prov.get("source_ids", [""])[0]),
                url=prov.get("url", "https://evie.com.au/find-a-charger/"),
                last_verified_utc=prov.get("last_verified_utc", ""),
                metadata=meta,
                amenities={},
                keywords=parsed.get("keywords", []),
                views=parsed.get("views", {})
            )
            records.append(rec)

        return records

    def sync(self, save_path: Optional[str] = None) -> List[UnifiedStationRecord]:
        """Fetches remote Evie stations and saves them to the local JSON registry, archiving changes."""
        target_path = save_path or self.registry_path
        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
        from lib.tessie_api_common import resolve_tessie_archive_home
        _archive_dir = os.path.join(resolve_tessie_archive_home(), "chargers")
        os.makedirs(_archive_dir, exist_ok=True)
        archive_path = os.path.join(_archive_dir, "evie_chargers_archived.json")
        now_utc = datetime.now(timezone.utc).isoformat()

 # Load existing registry to detect tariff or hardware changes
        existing_stations = {}
        if os.path.isfile(target_path):
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    existing_stations = old_data.get("stations", {}) if isinstance(old_data, dict) else {}
            except Exception as e:
                # Never treat an unreadable existing registry as empty and
                # save over it (loses every station's history) - stop here.
                raise RegistryReadError(f"{target_path} is unreadable ({e}) - not overwriting it") from e

        records = self.fetch_all_remote()
        fresh_by_key = {}
        for r in records:
            d = r.to_dict()
            if r.views:
                d["views"] = r.views
            fresh_by_key[r.key] = d
        stations_dict = merge_with_staleness(existing_stations, fresh_by_key, now_utc, archive_path)

        payload = {
            "version": "1.0",
            "updated_at": now_utc,
            "station_count": len(stations_dict),
            "source": "https://evie.com.au/find-a-charger/",
            "stations": stations_dict
        }

 # AGENTS.md was a bare open(path, "w") - a process killed
 # mid-write (Ctrl-C, laptop sleep, OOM) could leave a truncated/
 # corrupt registry; atomic_write_json() (base.py) writes to a temp
 # file in the same directory and os.replace()s it into place, so
 # target_path is always either the old complete file or the new
 # complete one, never something in between.
        atomic_write_json(target_path, payload)

        self._cache = records
        return records

    def reverify_station(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Re-fetches and updates a single Evie station, archiving changes."""
        clean_id = str(station_id).replace("Evie_", "").strip()
        all_recs = self.fetch_all_remote()
        matched = None
        for r in all_recs:
            if str(r.source_id) == clean_id or r.key == f"Evie_{clean_id}" or r.key == station_id:
                matched = r
                break
        if matched:
            self.sync()
            return matched
        return None

    def sync_all_australia(self, save_path: Optional[str] = None) -> List[UnifiedStationRecord]:
        """Alias for sync() to match ChargefoxProvider interface."""
        return self.sync(save_path=save_path)

    def load_cached_registry(self) -> List[UnifiedStationRecord]:
        """Loads cached Evie stations from local JSON registry."""
        if self._cache is not None:
            return self._cache

        if not os.path.isfile(self.registry_path):
            return []

        try:
            with open(self.registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return []

        raw_stations = data.get("stations", {})
        records: List[UnifiedStationRecord] = []

        for key, s in raw_stations.items():
            loc = s.get("location", {})
            hw = s.get("hardware", {})
            tariffs = s.get("tariffs", {})
            prov = s.get("provenance", {})
            meta = s.get("metadata", {})

            rec = UnifiedStationRecord(
                key=s.get("id") or key,
                name=s.get("name", "Evie Station"),
                network=s.get("network", "Evie"),
                operator_name=s.get("operator", "Evie Networks"),
                address=loc.get("address", ""),
                suburb=loc.get("suburb", ""),
                state=loc.get("state", ""),
                postcode=loc.get("postcode", ""),
                country=loc.get("country", "Australia"),
                lat=loc.get("lat", 0.0),
                lon=loc.get("lon", 0.0),
                timezone=loc.get("timezone", "Australia/Sydney"),
                speed_tier=hw.get("speed_tier", "dc_fast"),
                max_power_kw=hw.get("max_power_kw", 50.0),
                stalls_total=hw.get("stalls", 2),
                stalls_available=hw.get("stalls_available"),
                plugs=hw.get("connector_types", ["CCS2", "CHAdeMO"]),
                tethered=hw.get("tethered", True),
                hardware_model=hw.get("hardware_models", ["Evie DC Fast Charger"])[0] if hw.get("hardware_models") else "Evie DC Fast Charger",
                pricing_model=tariffs.get("pricing_model", "flat"),
                current_rate_per_kwh=tariffs.get("per_kwh_gross"),
                schedule=tariffs.get("schedule") or tariffs.get("rate_schedules") or tariffs.get("tesla_members", {}).get("rate_schedules") or s.get("tessie_cost_config", {}).get("rate_schedules") or [],
                idle_fee_per_min=tariffs.get("idle_fee_per_min_gross"),
                currency=tariffs.get("currency", "AUD"),
                tier=1,
                source=prov.get("sources", ["evie"])[0] if prov.get("sources") else "evie",
                source_id=str(prov.get("source_ids", [""])[0]),
                url=prov.get("url", "https://evie.com.au/find-a-charger/"),
                last_verified_utc=prov.get("last_verified_utc", ""),
                metadata=meta,
                amenities=s.get("amenities", {}),
                keywords=s.get("keywords", []),
                views=s.get("views", {})
            )
            records.append(rec)

        self._cache = records
        return records

    def search_nearby(
        self,
        lat: float,
        lon: float,
        radius_km: float = 15.0,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[UnifiedStationRecord]:
        """Finds Evie stations within radius of coordinates."""
        all_recs = self.load_cached_registry()
        if not all_recs:
            try:
                all_recs = self.sync()
            except Exception:
                all_recs = []

        in_radius = []
        for r in all_recs:
            if r.lat and r.lon:
                d = haversine_km(lat, lon, r.lat, r.lon)
                if d <= radius_km:
                    r_copy = UnifiedStationRecord(**r.__dict__)
                    r_copy.distance_km = d
                    in_radius.append(r_copy)

        return filter_station_records(in_radius, filters)

    def search_by_text(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[UnifiedStationRecord]:
        """Searches Evie stations by text match on name, suburb, address, or state."""
        all_recs = self.load_cached_registry()
        if not all_recs:
            try:
                all_recs = self.sync()
            except Exception:
                all_recs = []

        q = query.lower().strip()
        matched = []
        for r in all_recs:
            searchable = f"{r.name} {r.suburb} {r.address} {r.state} {r.network} {' '.join(r.keywords)}".lower()
            if q in searchable:
                matched.append(r)

        return filter_station_records(matched, filters)

    def get_station_details(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Fetches detailed record for a specific station ID."""
        all_recs = self.load_cached_registry()
        for r in all_recs:
            if r.key == station_id or r.source_id == station_id or str(r.metadata.get("evie_id")) == station_id:
                return r
        return None
