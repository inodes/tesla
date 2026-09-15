#!/usr/bin/env python3
"""
BP Pulse Charger Provider (Tier 1 First-Party)
==============================================
First-party provider for bp pulse DC Fast & Ultra-Fast charging infrastructure across Australia.
Connects directly to the official bp pulse Uberall Locator API:
- Stage 1: Discovers all locations (GPS, suburb, address, state, postcode, identifier).
- Stage 2: Batches detailed EV hardware queries (connectors, kW ratings, live status, hours, photos).
- Caches locally in Tessie/bppulse_chargers.json with historical archiving in Tessie/bppulse_chargers_archived.json.
"""

import os
import re
import json
import time
import urllib.request
import urllib.parse
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone

from .chargers_base import (
    BaseChargerProvider,
    UnifiedStationRecord,
    haversine_km,
    filter_station_records,
    compute_tax_breakdown,
    detect_station_changes,
    archive_station_record,
    atomic_write_json
)

from .chargers_chargefox import resolve_au_state

_script_dir = os.path.dirname(os.path.abspath(__file__))
_tools_dir = os.path.dirname(_script_dir)
_repo_root = os.path.dirname(_tools_dir)

BP_PULSE_LOCATIONS_ALL_URL = (
    "https://locator.uberall.com/api/storefinders/2FHQerF7PbZtW9bn80gN0bFE18A5i3/locations/all"
    "?v=20260101&language=en&fieldMask=id&fieldMask=identifier&fieldMask=googlePlaceId"
    "&fieldMask=lat&fieldMask=lng&fieldMask=name&fieldMask=country&fieldMask=city"
    "&fieldMask=province&fieldMask=streetAndNumber&fieldMask=zip&fieldMask=businessId"
    "&fieldMask=addressExtra&country=AU"
)

BP_PULSE_LOCATIONS_DETAIL_URL = (
    "https://locator.uberall.com/api/storefinders/2FHQerF7PbZtW9bn80gN0bFE18A5i3/locations"
    "?v=20260101&language=en"
    "&fieldMask=id&fieldMask=callToActions&fieldMask=futureOpeningDate&fieldMask=openingHours"
    "&fieldMask=openNow&fieldMask=nextOpen&fieldMask=phone&fieldMask=photos"
    "&fieldMask=specialOpeningHours&fieldMask=streetAndNumber&fieldMask=temporarilyClosedInfo&fieldMask=evData"
)

BP_PULSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Referer": "https://www.bppulse.com/",
    "Origin": "https://www.bppulse.com",
    "Accept": "*/*",
}

CONNECTOR_MAP = {
    "IEC_62196_T2_COMBO": "CCS2",
    "CHADEMO": "CHAdeMO",
    "IEC_62196_T2": "Type 2",
    "TYPE2": "Type 2",
    "CCS2": "CCS2",
}


def _resolve_bppulse_registry_path() -> str:
    """Finds path to bppulse_chargers.json in Tessie data directory or local fallback."""
    candidates = []
    tessie_home = None
    try:
        from lib.tessie_api_common import resolve_tessie_data_home
        tessie_home = resolve_tessie_data_home()
    except Exception:
        pass
    if tessie_home:
        candidates.append(os.path.join(tessie_home, "bppulse_chargers.json"))

    candidates.append(os.path.join(_repo_root, "Tessie", "bppulse_chargers.json"))
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
                    candidates.insert(0, os.path.join(td_exp, "bppulse_chargers.json"))
        except Exception:
            pass

    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0] if candidates else os.path.join(_repo_root, "Tessie", "bppulse_chargers.json")


AU_PROVINCES = {
    "new south wales": "NSW",
    "nsw": "NSW",
    "victoria": "VIC",
    "vic": "VIC",
    "queensland": "QLD",
    "qld": "QLD",
    "western australia": "WA",
    "wa": "WA",
    "south australia": "SA",
    "sa": "SA",
    "tasmania": "TAS",
    "tas": "TAS",
    "australian capital territory": "ACT",
    "act": "ACT",
    "northern territory": "NT",
    "nt": "NT",
}


def resolve_bppulse_state(raw_prov: str, lat: float, lon: float) -> str:
    """Resolves Australian state code from Uberall province string or GPS coordinates."""
    p_clean = (raw_prov or "").strip().lower()
    if p_clean in AU_PROVINCES:
        return AU_PROVINCES[p_clean]
    if lat and lon:
        try:
            return resolve_au_state(lat, lon)
        except Exception:
            pass
    return "NSW"


def parse_bppulse_location(basic: Dict[str, Any], detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Transforms raw Uberall bp pulse location payload into canonical multi-view JSON dictionary.
    """
    bp_id = str(basic.get("id", ""))
    identifier = str(basic.get("identifier", "")).strip()
    city = str(basic.get("city", "")).strip()
    street = str(basic.get("streetAndNumber", "")).strip()
    extra = str(basic.get("addressExtra", "")).strip() if basic.get("addressExtra") else ""
    postcode = str(basic.get("zip", "")).strip()
    raw_prov = str(basic.get("province", "")).strip()

    lat = float(basic.get("lat") or 0.0)
    lon = float(basic.get("lng") or 0.0)
    state = resolve_bppulse_state(raw_prov, lat, lon)

    # Clean address assembly
    addr_parts = [p for p in [street, extra] if p]
    street_full = ", ".join(addr_parts) if addr_parts else street
    full_address = f"{street_full}, {city} {state} {postcode}".strip(", ")

    # Detailed EV Data parsing
    stalls_total = 0
    stalls_available = 0
    connectors_set = set()
    powers_kw = []
    pedestals = []
    is_temp_closed = False

    if detail:
        temp_info = detail.get("temporarilyClosedInfo", {}) or {}
        is_temp_closed = bool(temp_info.get("temporarilyClosedNow"))

        ev_data = detail.get("evData") or []
        if isinstance(ev_data, list):
            for ev_group in ev_data:
                equipments = ev_group.get("supplyEquipments") or []
                for eq in equipments:
                    stalls_total += 1
                    eq_status = str(eq.get("status") or "").upper()
                    if eq_status == "AVAILABLE":
                        stalls_available += 1

                    eq_conns = []
                    for c in eq.get("connectors", []):
                        std_raw = c.get("standard") or "IEC_62196_T2_COMBO"
                        norm_std = CONNECTOR_MAP.get(std_raw, std_raw)
                        connectors_set.add(norm_std)

                        max_w = c.get("maxPower") or 75000
                        pwr_kw = round(max_w / 1000.0, 1)
                        powers_kw.append(pwr_kw)

                        eq_conns.append({
                            "standard": norm_std,
                            "raw_standard": std_raw,
                            "power_kw": pwr_kw,
                            "voltage": c.get("voltage"),
                            "amperage": c.get("amperage"),
                            "status": eq_status
                        })

                    pedestals.append({
                        "uid": eq.get("uid"),
                        "status": eq_status,
                        "connectors": eq_conns
                    })

    if not stalls_total:
        stalls_total = 2
        connectors_set = {"CCS2"}
        powers_kw = [75.0]

    max_pwr = max(powers_kw) if powers_kw else 75.0
    speed_tier = "dc_fast" if max_pwr >= 25.0 else "ac_destination"
    hw_tier = f"{int(max_pwr)}kW" if max_pwr.is_integer() else f"{max_pwr:.0f}kW"

    # Default bp pulse Australia tariff ($0.65/kWh for 75kW, $0.69/kWh for 150kW, $0.72/kWh for 300kW)
    if max_pwr >= 300.0:
        base_rate = 0.72
    elif max_pwr >= 150.0:
        base_rate = 0.69
    else:
        base_rate = 0.65

    gross_r, net_r, tax_r, tax_n, tax_pct = compute_tax_breakdown(base_rate, "Australia")

    station_key = f"BPPulse_{bp_id}"
    clean_name = f"bp pulse - {city}" if city else f"bp pulse Station #{bp_id}"

    # Timezone resolution
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

    now_utc = datetime.now(timezone.utc).isoformat()

    phone = detail.get("phone", "+61 1300 002 738") if detail else "+61 1300 002 738"
    photos = [p.get("publicUrl") for p in (detail.get("photos") or []) if p.get("publicUrl")] if detail else []

    # Direct station deep-link search URL on bp pulse
    # Example: https://www.bppulse.com/en-au/public-ev-charging/ev-charging-locations/s?q=bp+pulse+Charging+Station+-+1233+Marsden+Road%2C+2114+West+Ryde
    if street_full and postcode and city:
        query_str = f"bp pulse Charging Station - {street_full}, {postcode} {city}"
    elif street_full and city:
        query_str = f"bp pulse Charging Station - {street_full}, {city}"
    elif city:
        query_str = f"bp pulse Charging Station - {city}"
    else:
        query_str = f"bp pulse Charging Station #{bp_id}"

    bppulse_url = f"https://www.bppulse.com/en-au/public-ev-charging/ev-charging-locations/s?q={urllib.parse.quote_plus(query_str)}"

    notes = [
        "bp pulse Fast EV Charging Station",
        f"Support Phone: {phone}",
        f"bp pulse Station Link: {bppulse_url}",
        "bp pulse app available on iOS and Android: https://www.bppulse.com/en-au/public-ev-charging",
        "100% Powered by renewable electricity"
    ]

    return {
        "id": station_key,
        "name": clean_name,
        "network": "BP Pulse",
        "operator": "BP Pulse",
        "location": {
            "address": full_address,
            "street": street_full,
            "suburb": city,
            "state": state,
            "postcode": postcode,
            "country": "Australia",
            "country_code": "AU",
            "lat": lat,
            "lon": lon,
            "radius_m": 200,
            "timezone": tz,
            "google_place_id": basic.get("googlePlaceId")
        },
        "hardware": {
            "speed_tier": speed_tier,
            "stalls": stalls_total,
            "stalls_available": stalls_available if not is_temp_closed else 0,
            "max_power_kw": max_pwr,
            "tier": hw_tier,
            "connector_types": sorted(list(connectors_set)),
            "tethered": True,
            "pedestals": pedestals,
            "hardware_models": [f"bp pulse {hw_tier} DC Fast Charger"],
        },
        "compatibility": {
            "open_to_non_tesla": True,
            "tesla_only": False,
            "access": "Public 24/7" if not is_temp_closed else "Temporarily Closed",
            "connector_types": sorted(list(connectors_set)),
        },
        "access": {
            "hours": "Available 24/7" if not is_temp_closed else "Temporarily Closed",
            "description": "\n".join(notes)
        },
        "tariffs": {
            "currency": "AUD",
            "tax_name": tax_n,
            "tax_rate": tax_pct,
            "tax_included_in_listed_price": True,
            "pricing_model": "flat",
            "has_tou_pricing": False,
            "per_kwh_gross": gross_r,
            "per_kwh_net": net_r,
            "tax_per_kwh": tax_r,
            "rate_range": f"${gross_r:.2f}/kWh",
            "schedule": [{
                "name": "Standard Rate",
                "start": "00:00",
                "end": "24:00",
                "rate": gross_r,
                "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            }],
            "idle_fee_per_min_gross": 0.0,
            "idle_fee_per_min_net": 0.0,
            "parking_fee": None
        },
        "amenities": {
            "restrooms": True,
            "convenience_store": True,
            "coffee": True,
            "food": True
        },
        "keywords": [
            "bp pulse",
            "bp",
            city,
            state,
            identifier,
            f"bp {city}"
        ],
        "first_seen": now_utc,
        "last_updated": now_utc,
        "last_verified": now_utc,
        "valid_from": now_utc,
        "provenance": {
            "tier": 1,
            "sources": ["bppulse"],
            "source_ids": [bp_id, identifier] if identifier else [bp_id],
            "url": bppulse_url,
            "last_verified_utc": now_utc
        },
        "views": {
            "bppulse": {
                "id": bp_id,
                "identifier": identifier,
                "name": clean_name,
                "address": full_address,
                "suburb": city,
                "state": state,
                "postcode": postcode,
                "lat": lat,
                "lon": lon,
                "max_power_kw": max_pwr,
                "stalls": stalls_total,
                "stalls_available": stalls_available,
                "connectors": sorted(list(connectors_set)),
                "pedestals": pedestals,
                "photos": photos,
                "phone": phone,
                "url": bppulse_url
            }
        },
        "metadata": {
            "bp_id": bp_id,
            "identifier": identifier,
            "hardware_tier": hw_tier,
            "open_to_non_tesla": True,
            "tesla_only": False,
            "access_type": "Public 24/7",
            "access_hours": "Available 24/7",
            "access_description": "\n".join(notes),
            "phone": phone,
            "photos": photos
        }
    }


class BPPulseProvider(BaseChargerProvider):
    """
    Tier 1 First-Party Authoritative Provider for bp pulse Australia.
    """
    name: str = "BP Pulse"
    tier: int = 1
    # BUG-079: verified via grep 'speed_tier\s*=' - only "dc_fast" or
    # "ac_destination" (by max_power_kw threshold, or a stored hardware
    # speed_tier defaulting to "dc_fast"). Never "supercharger".
    POSSIBLE_SPEED_TIERS = {"dc_fast", "ac_destination"}

    def __init__(self, registry_path: Optional[str] = None):
        self.registry_path = registry_path or _resolve_bppulse_registry_path()
        self._cache: Optional[List[UnifiedStationRecord]] = None

    def fetch_all_remote(self, max_workers: int = 8, timeout: int = 15) -> List[UnifiedStationRecord]:
        """
        Fetches all live bp pulse stations across Australia from the Uberall Locator API:
        1. Fetch all station summaries from locator endpoint (92 stations).
        2. Query detailed station metadata (phone, photos, hours) in parallel.
        """
        req1 = urllib.request.Request(BP_PULSE_LOCATIONS_ALL_URL, headers=BP_PULSE_HEADERS)
        with urllib.request.urlopen(req1, timeout=timeout) as resp:
            raw_all = json.load(resp)

        loc_summaries = raw_all.get("response", {}).get("locations", [])
        if not loc_summaries:
            return []

        detail_map: Dict[int, Dict[str, Any]] = {}

        def _fetch_single_detail(lid: int) -> Tuple[int, Optional[Dict[str, Any]]]:
            url = f"https://locator.uberall.com/api/storefinders/2FHQerF7PbZtW9bn80gN0bFE18A5i3/locations/{lid}?v=20260101&language=en"
            try:
                req = urllib.request.Request(url, headers=BP_PULSE_HEADERS)
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    d = json.load(r)
                    return lid, d.get("response", {}).get("location") or d.get("response", {})
            except Exception:
                return lid, None

        all_ids = [l.get("id") for l in loc_summaries if l.get("id")]
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for lid, d_obj in executor.map(_fetch_single_detail, all_ids):
                if d_obj:
                    detail_map[lid] = d_obj

        records: List[UnifiedStationRecord] = []
        for loc in loc_summaries:
            lid = loc.get("id")
            d_node = detail_map.get(lid)
            parsed = parse_bppulse_location(loc, d_node)

            meta = parsed.get("metadata", {})
            loc_data = parsed.get("location", {})
            hw_data = parsed.get("hardware", {})
            tariffs = parsed.get("tariffs", {})
            prov = parsed.get("provenance", {})

            rec = UnifiedStationRecord(
                key=parsed["id"],
                name=parsed["name"],
                network="BP Pulse",
                operator_name="BP Pulse",
                address=loc_data.get("address", ""),
                suburb=loc_data.get("suburb", ""),
                state=loc_data.get("state", ""),
                postcode=loc_data.get("postcode", ""),
                country=loc_data.get("country", "Australia"),
                lat=loc_data.get("lat", 0.0),
                lon=loc_data.get("lon", 0.0),
                timezone=loc_data.get("timezone", "Australia/Sydney"),
                speed_tier=hw_data.get("speed_tier", "dc_fast"),
                max_power_kw=hw_data.get("max_power_kw", 75.0),
                stalls_total=hw_data.get("stalls", 2),
                stalls_available=hw_data.get("stalls_available"),
                plugs=hw_data.get("connector_types", ["CCS2"]),
                tethered=True,
                hardware_model=hw_data.get("hardware_models", ["bp pulse DC Fast Charger"])[0],
                pricing_model=tariffs.get("pricing_model", "flat"),
                current_rate_per_kwh=tariffs.get("per_kwh_gross"),
                rate_range=tariffs.get("rate_range", ""),
                schedule=tariffs.get("schedule", []),
                currency=tariffs.get("currency", "AUD"),
                tier=1,
                source="bppulse",
                source_id=str(prov.get("source_ids", [""])[0]),
                url=prov.get("url", "https://www.bppulse.com/en-au/public-ev-charging/ev-charging-locations"),
                last_verified_utc=prov.get("last_verified_utc", ""),
                metadata=meta,
                amenities=parsed.get("amenities", {}),
                keywords=parsed.get("keywords", []),
                views=parsed.get("views", {})
            )
            records.append(rec)

        return records

    def sync(self, save_path: Optional[str] = None) -> List[UnifiedStationRecord]:
        """Fetches remote bp pulse stations and saves to local JSON registry, archiving changes."""
        target_path = save_path or self.registry_path
        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
        archive_path = os.path.join(os.path.dirname(target_path), "bppulse_chargers_archived.json")
        now_utc = datetime.now(timezone.utc).isoformat()

        # Load existing registry to detect changes
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
            "version": "1.0",
            "updated_at": now_utc,
            "station_count": len(stations_dict),
            "source": "https://www.bppulse.com/en-au/public-ev-charging/ev-charging-locations",
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

    def sync_all_australia(self, save_path: Optional[str] = None) -> List[UnifiedStationRecord]:
        """Alias for sync() matching provider interface."""
        return self.sync(save_path=save_path)

    def reverify_station(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Re-fetches and updates a single bp pulse station, archiving changes."""
        clean_id = str(station_id).replace("BPPulse_", "").strip()
        all_recs = self.fetch_all_remote()
        matched = None
        for r in all_recs:
            if str(r.source_id) == clean_id or r.key == f"BPPulse_{clean_id}" or r.key == station_id:
                matched = r
                break
        if matched:
            self.sync()
            return matched
        return None

    def load_cached_registry(self) -> List[UnifiedStationRecord]:
        """Loads cached bp pulse stations from local JSON registry."""
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
            views = s.get("views", {})

            rec = UnifiedStationRecord(
                key=key,
                name=s.get("name", key),
                network=s.get("network", "BP Pulse"),
                operator_name=s.get("operator", "BP Pulse"),
                address=loc.get("address", ""),
                suburb=loc.get("suburb", ""),
                state=loc.get("state", ""),
                postcode=loc.get("postcode", ""),
                country=loc.get("country", "Australia"),
                lat=float(loc.get("lat") or 0.0),
                lon=float(loc.get("lon") or 0.0),
                timezone=loc.get("timezone", "Australia/Sydney"),
                speed_tier=hw.get("speed_tier", "dc_fast"),
                max_power_kw=float(hw.get("max_power_kw") or 75.0),
                stalls_total=int(hw.get("stalls") or 2),
                stalls_available=hw.get("stalls_available"),
                plugs=hw.get("connector_types", ["CCS2"]),
                tethered=hw.get("tethered", True),
                hardware_model=hw.get("hardware_models", ["bp pulse DC Fast Charger"])[0] if hw.get("hardware_models") else "bp pulse DC Fast Charger",
                pricing_model=tariffs.get("pricing_model", "flat"),
                current_rate_per_kwh=tariffs.get("per_kwh_gross", 0.65),
                rate_range=tariffs.get("rate_range", "$0.65/kWh"),
                schedule=tariffs.get("schedule", []),
                currency=tariffs.get("currency", "AUD"),
                tier=1,
                source="bppulse",
                source_id=prov.get("source_ids", [""])[0] if prov.get("source_ids") else key,
                url=prov.get("url") or s.get("url") or "https://www.bppulse.com/en-au/public-ev-charging/ev-charging-locations",
                last_verified_utc=prov.get("last_verified_utc", ""),
                metadata=meta,
                amenities=s.get("amenities", {}),
                keywords=s.get("keywords", []),
                views=views
            )
            records.append(rec)

        self._cache = records
        return records

    def search_nearby(self, lat: float, lon: float, radius_km: float = 15.0, filters: Optional[Dict[str, Any]] = None) -> List[UnifiedStationRecord]:
        """Finds bp pulse stations near coordinates."""
        all_stations = self.load_cached_registry()
        results = []
        for st in all_stations:
            d = haversine_km(lat, lon, st.lat, st.lon)
            if d <= radius_km:
                st.distance_km = round(d, 2)
                results.append(st)
        filtered = filter_station_records(results, filters)
        filtered.sort(key=lambda s: s.distance_km if s.distance_km is not None else 999999)
        return filtered

    def search_by_text(self, query: str, filters: Optional[Dict[str, Any]] = None) -> List[UnifiedStationRecord]:
        """Searches bp pulse stations by text."""
        all_stations = self.load_cached_registry()
        q_clean = query.lower().strip()
        results = []
        for st in all_stations:
            searchable = f"{st.name} {st.suburb} {st.state} {st.address} {' '.join(st.keywords)}".lower()
            if q_clean in searchable:
                results.append(st)
        return filter_station_records(results, filters)

    def get_station_details(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Retrieves station details from local cache or remote."""
        all_stations = self.load_cached_registry()
        clean_id = str(station_id).replace("BPPulse_", "").strip()
        for st in all_stations:
            if st.key == station_id or str(st.source_id) == clean_id or st.key == f"BPPulse_{clean_id}":
                return st
        return None
