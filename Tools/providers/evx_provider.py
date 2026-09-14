#!/usr/bin/env python3
"""
EVX Charger Provider (Tier 1 First-Party)
=========================================
First-party provider for EVX Kerbside Polecharger network across Australia.
Connects directly to EVX Storepoint Locator API with local JSON caching in Tessie/evx_chargers.json.
"""

import os
import re
import json
import time
import urllib.request
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone

from .base import (
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

EVX_LOCATIONS_URL = "https://api.storepoint.co/v1/164b9d3915a271/locations?rq"


def _resolve_evx_registry_path() -> str:
    """Finds path to evx_chargers.json in Tessie data directory or local fallback."""
    candidates = []
    tessie_home = None
    try:
        from tessie_api_common import resolve_tessie_data_home
        tessie_home = resolve_tessie_data_home()
    except Exception:
        pass
    if tessie_home:
        candidates.append(os.path.join(tessie_home, "evx_chargers.json"))
        
    candidates.append(os.path.join(_repo_root, "Tessie", "evx_chargers.json"))
    config_path = os.path.join(_repo_root, "Tessie", "config.json")
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                td = cfg.get("tessie_directory")
                if td:
                    td_exp = os.path.expanduser(td)
                    candidates.insert(0, os.path.join(td_exp, "evx_chargers.json"))
        except Exception:
            pass

    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0] if candidates else os.path.join(_repo_root, "Tessie", "evx_chargers.json")


def parse_evx_location_record(loc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parses raw Storepoint EVX record into canonical multi-view JSON dictionary.
    """
    loc_id = loc.get("id")
    raw_name = loc.get("name", "").strip()
    addr = loc.get("streetaddress", "").strip()
    cf_raw = loc.get("custom_fields", "{}")
    
    cf = {}
    if isinstance(cf_raw, str) and cf_raw.strip():
        try:
            cf = json.loads(cf_raw)
        except Exception:
            cf = {}
    elif isinstance(cf_raw, dict):
        cf = cf_raw

    # Custom field extraction
    hw_model = cf.get("9rn4ydnspl") or "Polecharger"
    pwr_type = cf.get("48ckicykouj") or "AC"
    plug_type = cf.get("qi6rkgzgrn") or "Type 2"
    pwr_raw = cf.get("dq3ho4dk08") or "22 KW"
    pole_id = str(cf.get("7ulej7jp2u") or "").strip()

    # Parse power float
    max_pwr = 22.0
    pwr_m = re.search(r"(\d+(?:\.\d+)?)", pwr_raw)
    if pwr_m:
        max_pwr = float(pwr_m.group(1))

    # Parse Postcode and State
    postcode = ""
    state = "NSW"
    m_postcode = re.search(r"\b(2\d{3}|3\d{3}|4\d{3}|0\d{3}|5\d{3}|6\d{3}|7\d{3})\b", addr)
    if m_postcode:
        postcode = m_postcode.group(1)
        if postcode.startswith("3"):
            state = "VIC"
        elif postcode.startswith("4"):
            state = "QLD"
        elif postcode.startswith("0") or postcode.startswith("26"):
            state = "ACT" if postcode.startswith("260") or postcode.startswith("261") or postcode.startswith("29") else "NSW"
        elif postcode.startswith("2"):
            state = "NSW"
    
    m_state = re.search(r"\b(NSW|VIC|QLD|WA|SA|TAS|ACT|NT)\b", addr, re.IGNORECASE)
    if m_state:
        state = m_state.group(1).upper()

    # Parse Suburb and Street Address
    suburb = ""
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    if len(parts) >= 2:
        street = parts[0]
        sub_cand = re.sub(r"\b(NSW|VIC|QLD|WA|SA|TAS|ACT|NT|\d{4}|Australia)\b", "", parts[1], flags=re.IGNORECASE).strip()
        if sub_cand:
            suburb = sub_cand
    else:
        street = addr

    # Clean display name from raw_name e.g. "21-27 Wattle St-West Ryde-20203A"
    name_parts = raw_name.rsplit("-", 2)
    if len(name_parts) == 3:
        clean_site, parsed_suburb, _ = [p.strip() for p in name_parts]
        if not suburb:
            suburb = parsed_suburb
        clean_name = f"{clean_site} Polecharger" if "polecharger" not in clean_site.lower() else clean_site
    elif len(name_parts) == 2:
        clean_site, parsed_suburb = [p.strip() for p in name_parts]
        if not suburb:
            suburb = parsed_suburb
        clean_name = f"{clean_site} Polecharger" if "polecharger" not in clean_site.lower() else clean_site
    else:
        clean_name = f"{raw_name} Polecharger" if "polecharger" not in raw_name.lower() else raw_name

    station_key = f"EVX_{pole_id}" if pole_id else f"EVX_{loc_id}"
    short_name = f"EVX_{pole_id}" if pole_id else f"EVX_{clean_name}"
    lat = float(loc.get("loc_lat") or 0.0)
    lon = float(loc.get("loc_long") or 0.0)

    # EVX standard pricing
    rate_val = 0.50  # Typical standard rate
    now_utc = datetime.now(timezone.utc).isoformat()

    desc = loc.get("description", "").strip()
    notes = [
        "EVX Kerbside Polecharger",
        "Dual 22 kW Type 2 Sockets (BYO cable)",
        "Download EVX App: https://evx.tech/evx-drivers-app/",
        "100% Powered by renewable energy"
    ]
    if desc and desc.lower() not in ("near", "replace this row"):
        notes.insert(0, desc)

    return {
        "id": station_key,
        "name": clean_name,
        "network": "EVX",
        "operator": "EVX",
        "location": {
            "address": addr,
            "street": street,
            "suburb": suburb,
            "state": state,
            "postcode": postcode,
            "country": "Australia",
            "country_code": "AU",
            "lat": lat,
            "lon": lon,
            "radius_m": 150,
            "timezone": "Australia/Sydney" if state in ("NSW", "ACT") else "Australia/Melbourne" if state == "VIC" else "Australia/Brisbane"
        },
        "hardware": {
            "speed_tier": "ac_destination",
            "stalls": 2,
            "stalls_available": None,
            "max_power_kw": max_pwr,
            "tier": "AC",
            "connector_types": [plug_type],
            "tethered": False,
            "hardware_models": [hw_model]
        },
        "compatibility": {
            "open_to_non_tesla": True,
            "tesla_only": False,
            "access": "Public 24/7",
            "connector_types": [plug_type]
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
            "pricing_model": "time_of_use",
            "has_tou_pricing": True,
            "per_kwh_gross": 0.50,
            "per_kwh_net": round(0.50 / 1.10, 4),
            "tax_per_kwh": round(0.50 - (0.50 / 1.10), 4),
            "idle_fee_per_min_gross": 0.20,
            "idle_fee_per_min_net": round(0.20 / 1.10, 4),
            "rate_range": "$0.39 - $0.50/kWh",
            "schedule": [
                {
                    "name": "Off-Peak",
                    "start": "20:00",
                    "end": "08:00",
                    "rate": 0.39,
                    "rate_per_kwh": 0.39,
                    "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                },
                {
                    "name": "Peak / Day",
                    "start": "08:00",
                    "end": "20:00",
                    "rate": 0.50,
                    "rate_per_kwh": 0.50,
                    "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                }
            ]
        },
        "tessie_cost_config": {
            "currency": "AUD",
            "pricing_model": "time_of_use",
            "per_kwh_flat": 0.0,
            "per_minute": 0.0,
            "per_session": 0.0,
            "idle_fee_per_min": 0.20,
            "rate_schedules": [
                {
                    "label": "Off-Peak",
                    "start_time": "20:00",
                    "end_time": "08:00",
                    "rate_per_kwh": 0.39,
                    "rate_per_kwh_gross": 0.39,
                    "rate_per_kwh_net": round(0.39 / 1.10, 4),
                    "tax_amount": round(0.39 - (0.39 / 1.10), 4),
                    "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                    "months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                },
                {
                    "label": "Peak / Day",
                    "start_time": "08:00",
                    "end_time": "20:00",
                    "rate_per_kwh": 0.50,
                    "rate_per_kwh_gross": 0.50,
                    "rate_per_kwh_net": round(0.50 / 1.10, 4),
                    "tax_amount": round(0.50 - (0.50 / 1.10), 4),
                    "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                    "months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                }
            ]
        },
        "amenities": {},
        "keywords": ["evx", "polecharger", "kerbside", "ac", "type 2", suburb.lower(), pole_id.lower()],
        "aliases": [raw_name, f"EVX {suburb}", f"Polecharger {pole_id}"],
        "provenance": {
            "tier": 1,
            "sources": ["evx"],
            "source_ids": [str(loc_id), str(pole_id)],
            "url": "https://evx.tech/charge-locator/",
            "last_verified_utc": now_utc
        },
        "views": {
            "evx": {
                "storepoint_id": loc_id,
                "raw_name": raw_name,
                "pole_id": pole_id,
                "custom_fields": cf,
                "tags": loc.get("tags"),
                "email": loc.get("email"),
                "image_url": loc.get("image_url")
            }
        },
        "metadata": {
            "pole_id": pole_id,
            "storepoint_id": loc_id,
            "raw_name": raw_name,
            "short_name": short_name,
            "hardware_tier": "AC",
            "open_to_non_tesla": True,
            "tesla_only": False,
            "access_type": "Public 24/7",
            "access_hours": "Available 24/7",
            "access_description": "\n".join(notes)
        }
    }


class EVXProvider(BaseChargerProvider):
    name: str = "EVX"
    tier: int = 1  # First-Party Authoritative

    def __init__(self, registry_path: Optional[str] = None):
        self.registry_path = registry_path or _resolve_evx_registry_path()
        self._cache: Optional[List[UnifiedStationRecord]] = None

    def fetch_all_remote(self, timeout: int = 15) -> List[UnifiedStationRecord]:
        """Fetches all live EVX polechargers directly from EVX Storepoint endpoint."""
        req = urllib.request.Request(
            EVX_LOCATIONS_URL,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)",
                "Accept": "*/*"
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)

        raw_locs = data.get("results", {}).get("locations", [])
        records: List[UnifiedStationRecord] = []

        for loc in raw_locs:
            parsed = parse_evx_location_record(loc)
            meta = parsed.get("metadata", {})
            loc_data = parsed.get("location", {})
            hw_data = parsed.get("hardware", {})
            tariffs = parsed.get("tariffs", {})
            prov = parsed.get("provenance", {})

            rec = UnifiedStationRecord(
                key=parsed["id"],
                name=parsed["name"],
                network="EVX",
                operator_name="EVX",
                address=loc_data.get("address", ""),
                suburb=loc_data.get("suburb", ""),
                state=loc_data.get("state", ""),
                postcode=loc_data.get("postcode", ""),
                country=loc_data.get("country", "Australia"),
                lat=loc_data.get("lat", 0.0),
                lon=loc_data.get("lon", 0.0),
                timezone=loc_data.get("timezone", "Australia/Sydney"),
                speed_tier="ac_destination",
                max_power_kw=hw_data.get("max_power_kw", 22.0),
                stalls_total=hw_data.get("stalls", 2),
                stalls_available=None,
                plugs=hw_data.get("connector_types", ["Type 2"]),
                tethered=False,
                hardware_model=hw_data.get("hardware_models", ["Polecharger"])[0] if hw_data.get("hardware_models") else "Polecharger",
                pricing_model=tariffs.get("pricing_model", "time_of_use"),
                current_rate_per_kwh=tariffs.get("per_kwh_gross", 0.50),
                rate_range=tariffs.get("rate_range", "$0.39 - $0.50/kWh"),
                schedule=tariffs.get("schedule", []),
                idle_fee_per_min=tariffs.get("idle_fee_per_min_gross", 0.20),
                currency=tariffs.get("currency", "AUD"),
                tier=1,
                source="evx",
                source_id=str(prov.get("source_ids", [""])[0]),
                url=prov.get("url", "https://evx.tech/charge-locator/"),
                last_verified_utc=prov.get("last_verified_utc", ""),
                metadata=meta,
                amenities={},
                keywords=parsed.get("keywords", []),
                views=parsed.get("views", {})
            )
            records.append(rec)

        return records

    def sync(self, save_path: Optional[str] = None) -> List[UnifiedStationRecord]:
        """Fetches remote EVX stations and saves them to the local JSON registry, archiving changes."""
        target_path = save_path or self.registry_path
        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
        archive_path = os.path.join(os.path.dirname(target_path), "evx_chargers_archived.json")
        now_utc = datetime.now(timezone.utc).isoformat()

        # Load existing registry to detect tariff or hardware changes
        existing_stations = {}
        if os.path.isfile(target_path):
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    existing_stations = old_data.get("stations", {}) if isinstance(old_data, dict) else {}
            except Exception:
                existing_stations = {}

        records = self.fetch_all_remote()
        stations_dict = {}
        for r in records:
            d = r.to_dict()
            if r.views:
                d["views"] = r.views
            stations_dict[r.key] = d

            # Archive if previous version exists and changed
            if r.key in existing_stations:
                old_st = existing_stations[r.key]
                if detect_station_changes(old_st, d):
                    archive_station_record(archive_path, r.key, old_st, now_utc)

        payload = {
            "updated_at": now_utc,
            "total_stations": len(records),
            "source": "evx_storepoint_api",
            "stations": stations_dict
        }

        # AGENTS.md BUG-067: was a bare open(path, "w") - a process killed
        # mid-write (Ctrl-C, laptop sleep, OOM) could leave a truncated/
        # corrupt registry; atomic_write_json() (base.py) writes to a temp
        # file in the same directory and os.replace()s it into place, so
        # target_path is always either the old complete file or the new
        # complete one, never something in between.
        atomic_write_json(target_path, payload)

        self._cache = records
        return records

    def reverify_station(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Re-fetches and updates a single EVX station, archiving changes."""
        clean_id = str(station_id).replace("EVX_", "").strip()
        all_recs = self.fetch_all_remote()
        matched = None
        for r in all_recs:
            if str(r.source_id) == clean_id or r.key == f"EVX_{clean_id}" or r.key == station_id:
                matched = r
                break
        if matched:
            self.sync()
            return matched
        return None

    def load_cached_registry(self) -> List[UnifiedStationRecord]:
        """Loads locally cached EVX stations from JSON registry."""
        if self._cache is not None:
            return self._cache

        if not os.path.isfile(self.registry_path):
            try:
                return self.sync()
            except Exception:
                return []

        try:
            with open(self.registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            st_dict = data.get("stations", {})
            records: List[UnifiedStationRecord] = []
            for s_id, s_data in st_dict.items():
                loc_data = s_data.get("location", {})
                hw_data = s_data.get("hardware", {})
                tariffs = s_data.get("tariffs", {})
                prov = s_data.get("provenance", {})
                meta = s_data.get("metadata", {})

                rec = UnifiedStationRecord(
                    key=s_id,
                    name=s_data.get("name", s_id),
                    network=s_data.get("network", "EVX"),
                    operator_name=s_data.get("operator", "EVX"),
                    address=loc_data.get("address", ""),
                    suburb=loc_data.get("suburb", ""),
                    state=loc_data.get("state", ""),
                    postcode=loc_data.get("postcode", ""),
                    country=loc_data.get("country", "Australia"),
                    lat=float(loc_data.get("lat") or 0.0),
                    lon=float(loc_data.get("lon") or 0.0),
                    timezone=loc_data.get("timezone", "Australia/Sydney"),
                    speed_tier="ac_destination",
                    max_power_kw=float(hw_data.get("max_power_kw") or 22.0),
                    stalls_total=int(hw_data.get("stalls") or 2),
                    stalls_available=hw_data.get("stalls_available"),
                    plugs=hw_data.get("connector_types", ["Type 2"]),
                    tethered=hw_data.get("tethered", False),
                    hardware_model=hw_data.get("hardware_models", ["Polecharger"])[0] if hw_data.get("hardware_models") else "Polecharger",
                    pricing_model=tariffs.get("pricing_model", "time_of_use"),
                    current_rate_per_kwh=tariffs.get("per_kwh_gross", 0.50),
                    schedule=tariffs.get("schedule") or tariffs.get("rate_schedules") or tariffs.get("tesla_members", {}).get("rate_schedules") or s_data.get("tessie_cost_config", {}).get("rate_schedules") or [],
                    idle_fee_per_min=tariffs.get("idle_fee_per_min_gross", 0.20),
                    currency=tariffs.get("currency", "AUD"),
                    tier=1,
                    source="evx",
                    source_id=prov.get("source_ids", [""])[0] if prov.get("source_ids") else s_id,
                    url=prov.get("url", "https://evx.tech/charge-locator/"),
                    last_verified_utc=prov.get("last_verified_utc", ""),
                    metadata=meta,
                    amenities=s_data.get("amenities", {}),
                    keywords=s_data.get("keywords", []),
                    views=s_data.get("views", {})
                )
                records.append(rec)

            self._cache = records
            return records
        except Exception:
            return []

    def search_nearby(
        self,
        lat: float,
        lon: float,
        radius_km: float = 15.0,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[UnifiedStationRecord]:
        """Finds EVX stations within radius_km of coordinates."""
        all_stations = self.load_cached_registry()
        matched = []
        for st in all_stations:
            dist = haversine_km(lat, lon, st.lat, st.lon)
            if dist <= radius_km:
                st.distance_km = dist
                matched.append(st)

        matched.sort(key=lambda s: s.distance_km or float("inf"))
        return filter_station_records(matched, filters)

    def search_by_text(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[UnifiedStationRecord]:
        """Searches EVX stations by keyword match on name, suburb, address, or pole ID."""
        if not query or not query.strip():
            return []

        q_clean = query.strip().lower()
        all_stations = self.load_cached_registry()
        matched = []

        for st in all_stations:
            meta = st.metadata or {}
            pole_id = str(meta.get("pole_id", "")).lower()
            store_id = str(meta.get("storepoint_id", "")).lower()
            
            searchable = f"{st.name} {st.suburb} {st.address} {st.state} {st.postcode} {st.network} {pole_id} {store_id} {' '.join(st.keywords)}".lower()
            if q_clean in searchable:
                matched.append(st)

        return filter_station_records(matched, filters)

    def get_station_details(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Finds specific station by key, pole ID, or Storepoint ID."""
        all_stations = self.load_cached_registry()
        s_clean = str(station_id).strip().lower()

        for st in all_stations:
            if st.key.lower() == s_clean or st.source_id.lower() == s_clean:
                return st
            meta = st.metadata or {}
            if str(meta.get("pole_id", "")).lower() == s_clean or str(meta.get("storepoint_id", "")).lower() == s_clean:
                return st

        return None
