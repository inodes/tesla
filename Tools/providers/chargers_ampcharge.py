#!/usr/bin/env python3
"""
Ampol AmpCharge Charger Provider (Tier 1 First-Party)
======================================================
First-party provider for Ampol's AmpCharge network - both the dedicated
"AmpCharge" EV hub sites (Brand="Ampcharge", StoreType="EVUM", usually at
shopping centres) and the ordinary Ampol/"The Foodary" servo stations that
also have EV fast chargers installed (Brand="The Foodary", StoreType="SSTN").
Both are tagged into `network` so they stay distinguishable while both being
covered by a single first-party fetch, per the user's own framing: "Also
have another charging provider that is covered by plugshare Ampol's
AmpCharge" - PlugShare only sees these indirectly via its own aggregation;
this talks to Ampol's own siteservice API directly instead.

Connects directly to Ampol's public digital-api siteservice endpoint (the
same one https://ampcharge.ampol.com.au/find-a-charging-station itself
calls - `ocp-apim-subscription-key` below is the key that site's own
frontend uses in its network requests, not a private credential) with local
JSON caching in Tessie/ampcharge_chargers.json.

NOTE ON PRICING: the siteservice response has no tariff/rate fields
anywhere (confirmed against a live sample) - per house convention (never
guess pricing), every record here is left pricing_model="unknown",
current_rate_per_kwh=None, rate_range="".
"""

import os
import re
import json
import time
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from .chargers_base import (
    BaseChargerProvider,
    UnifiedStationRecord,
    haversine_km,
    filter_station_records,
    detect_station_changes,
    archive_station_record,
    save_json_with_fallback
)

_script_dir = os.path.dirname(os.path.abspath(__file__))
_tools_dir = os.path.dirname(_script_dir)
_repo_root = os.path.dirname(_tools_dir)

# Live-verified 2026-09-15 against the user's own network-inspect capture:
# a single call centred near the middle of the continent with a huge
# distance radius returns every EVFastCharging site in one response (no
# @odata.nextLink / @odata.count in the payload - not a paginated API).
AMPOL_SITESERVICE_URL = (
    "https://digital-api.ampol.com.au/siteservice/Service/"
    "Ext.Closest(lg=131.01708984375,lt=-29.49930425836781,distance=4000000)"
)
AMPOL_SELECT_FIELDS = (
    "Brand,Longitude,Latitude,LocationID,DisplayName,Address,Suburb,State,"
    "Postcode,Phone,StoreType,FuelTypeFriendlyName,EVChargingData,"
    "Mon_Openning,Mon_Closing,Tue_Openning,Tue_Closing,Wed_Openning,"
    "Wed_Closing,Thu_Openning,Thu_Closing,Fri_Openning,Fri_Closing,"
    "Sat_Openning,Sat_Closing,Sun_Openning,Sun_Closing"
)
AMPOL_FILTER = "(EVFastCharging+eq+true)"
AMPOL_SUBSCRIPTION_KEY = "fd5342b7b7304260910be1d73a296114"

_DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _resolve_ampcharge_registry_path() -> str:
    """Finds path to ampcharge_chargers.json in Tessie data directory or local fallback."""
    candidates = []
    tessie_home = None
    try:
        from lib.tessie_api_common import resolve_tessie_data_home
        tessie_home = resolve_tessie_data_home()
    except Exception:
        pass
    if tessie_home:
        candidates.append(os.path.join(tessie_home, "ampcharge_chargers.json"))

    candidates.append(os.path.join(_repo_root, "Tessie", "ampcharge_chargers.json"))
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
                    candidates.insert(0, os.path.join(td_exp, "ampcharge_chargers.json"))
        except Exception:
            pass

    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0] if candidates else os.path.join(_repo_root, "Tessie", "ampcharge_chargers.json")


def _normalize_plug_type(raw: str) -> str:
    """Normalizes Ampol's 'CCS 2' (with a space) to this repo's 'CCS2' convention; passes CHAdeMO through."""
    if not raw:
        return ""
    cleaned = raw.strip()
    if cleaned.upper().replace(" ", "") == "CCS2":
        return "CCS2"
    return cleaned


def _parse_power_kw(raw: str) -> Optional[float]:
    """Parses Ampol's 'XXX kW' MaxPowerOut string into a float."""
    if not raw:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", raw)
    if m:
        try:
            return float(m.group(1))
        except (ValueError, TypeError):
            return None
    return None


def _summarize_hours(item: Dict[str, Any]) -> (Dict[str, str], str):
    """
    Builds a {day: 'open-close'} map from the raw per-day Mon_Openning/
    Mon_Closing... fields, plus a human-readable summary string. Ampol's own
    convention: both Opening AND Closing == 'Midnight' means open 24 hours;
    a 'Midnight' Closing alone (with a real Opening time) means "open until
    midnight", not 24/7.
    """
    per_day = {}
    for day in _DAY_ORDER:
        o = item.get(f"{day}_Openning") or ""
        c = item.get(f"{day}_Closing") or ""
        if o or c:
            per_day[day] = f"{o}-{c}"

    if not per_day:
        return {}, ""

    distinct = set(per_day.values())
    if len(distinct) == 1:
        only = next(iter(distinct))
        if only == "Midnight-Midnight":
            return per_day, "Open 24/7"
        return per_day, f"Daily {only}"
    return per_day, "Varies by day (see opening_hours)"


class AmpChargeProvider(BaseChargerProvider):
    name: str = "AmpCharge"
    tier: int = 1  # First-Party Authoritative
    # Every EVChargingData entry seen in a live sample is CCS2/CHAdeMO DC
    # fast/rapid charging (85-400 kW) - the siteservice query itself filters
    # on EVFastCharging=true, so this provider never contributes an
    # ac_destination or supercharger record.
    POSSIBLE_SPEED_TIERS = {"dc_fast"}

    def __init__(self, registry_path: Optional[str] = None):
        self.registry_path = registry_path or _resolve_ampcharge_registry_path()
        self._cache: Optional[List[UnifiedStationRecord]] = None

    def _fetch_raw(self, timeout: int = 20, max_retries: int = 4) -> List[Dict[str, Any]]:
        """Fetches the full live station list from Ampol's siteservice endpoint, with retry/backoff on 429."""
        url = f"{AMPOL_SITESERVICE_URL}?$select={AMPOL_SELECT_FIELDS}&$filter={AMPOL_FILTER}"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "*/*",
                "Origin": "https://ampcharge.ampol.com.au",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
                "content-type": "application/json",
                "ocp-apim-subscription-key": AMPOL_SUBSCRIPTION_KEY,
            }
        )
        for attempt in range(1, max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data.get("value", []) or []
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < max_retries:
                    time.sleep((2 ** attempt) + 0.5)
                    continue
                elif attempt < max_retries:
                    time.sleep(1.0)
                    continue
                return []
            except Exception:
                if attempt < max_retries:
                    time.sleep(1.0)
                    continue
                return []
        return []

    def _convert_ampol_node(self, item: Dict[str, Any]) -> Optional[UnifiedStationRecord]:
        """Converts a raw Ampol siteservice station record into a UnifiedStationRecord."""
        if not item or not isinstance(item, dict):
            return None

        loc_id = str(item.get("LocationID", "")).strip()
        if not loc_id:
            return None

        lat = item.get("Latitude")
        lon = item.get("Longitude")
        if lat is None or lon is None:
            return None
        try:
            lat = float(lat)
            lon = float(lon)
        except (ValueError, TypeError):
            return None

        brand = (item.get("Brand") or "").strip()
        network = "AmpCharge" if brand.lower() == "ampcharge" else "Ampol Foodary"

        name = item.get("DisplayName") or ("AmpCharge Station" if network == "AmpCharge" else "Ampol Foodary Station")

        evd = item.get("EVChargingData") or []
        bays = set()
        plugs = set()
        powers = []
        hw_models = set()
        for e in evd:
            if not isinstance(e, dict):
                continue
            bay = e.get("ChargingBayNo")
            if bay is not None:
                bays.add(bay)
            plug = _normalize_plug_type(e.get("EVPlugType") or "")
            if plug:
                plugs.add(plug)
            p_kw = _parse_power_kw(e.get("MaxPowerOut") or "")
            if p_kw is not None:
                powers.append(p_kw)
            desc = (e.get("ChargerModelDesc") or "").strip()
            model = (e.get("ChargerModel") or "").strip()
            if desc:
                hw_models.add(desc)
            elif model:
                hw_models.add(model)

        stalls_total = len(bays) if bays else max(1, len(evd))
        max_kw = max(powers) if powers else 0.0
        default_model = "Ampol AmpCharge DC Fast Charger" if network == "AmpCharge" else "Ampol Foodary DC Fast Charger"
        hardware_model = ", ".join(sorted(hw_models)) if hw_models else default_model

        state = (item.get("State") or "").upper()
        opening_hours, hours_summary = _summarize_hours(item)
        fuel_types = item.get("FuelTypeFriendlyName") or []

        return UnifiedStationRecord(
            key=f"Ampol_{loc_id}",
            name=name,
            network=network,
            operator_name="Ampol",
            address=item.get("Address") or "",
            suburb=item.get("Suburb") or "",
            state=state,
            postcode=str(item.get("Postcode") or ""),
            country="Australia",
            lat=lat,
            lon=lon,
            timezone="Australia/Sydney",
            speed_tier="dc_fast",
            max_power_kw=max_kw,
            stalls_total=stalls_total,
            stalls_available=None,
            plugs=sorted(plugs) if plugs else ["CCS2"],
            tethered=True,
            hardware_model=hardware_model,
            # Never guess pricing: the siteservice response has no tariff
            # fields at all - see module docstring.
            pricing_model="unknown",
            current_rate_per_kwh=None,
            rate_range="",
            schedule=[],
            idle_fee_per_min=None,
            tier=1,
            source="ampol",
            source_id=loc_id,
            url="https://ampcharge.ampol.com.au/find-a-charging-station",
            last_verified_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            metadata={
                "brand": brand,
                "store_type": item.get("StoreType"),
                "phone": item.get("Phone") or None,
                "opening_hours": opening_hours,
                "hours": hours_summary,
                "access_hours": hours_summary,
                "has_fuel_station": bool(fuel_types),
                "fuel_types": fuel_types,
                "open_to_non_tesla": True,
                "tesla_only": False,
                "hardware_tier": f"Ampol {network} DC Fast",
            },
            amenities={},
            keywords=[name, item.get("Suburb") or "", state, network, brand],
            views={"ampol": item}
        )

    def load_cached_registry(self) -> List[UnifiedStationRecord]:
        """Loads AmpCharge/Ampol Foodary stations from local cache file."""
        if self._cache is not None:
            return self._cache

        records = []
        if os.path.isfile(self.registry_path):
            try:
                with open(self.registry_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    stations_list = data.get("stations", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                    for raw_item in stations_list:
                        rec = self._convert_ampol_node(raw_item)
                        if rec:
                            records.append(rec)
            except Exception:
                pass

        self._cache = records
        return records

    def save_cached_registry(self, records: List[UnifiedStationRecord]) -> None:
        """Saves records to local JSON cache file, archiving any changed records."""
        archive_path = os.path.join(os.path.dirname(self.registry_path), "ampcharge_chargers_archived.json")
        now_utc = datetime.now(timezone.utc).isoformat()

        existing_map = {}
        if os.path.isfile(self.registry_path):
            try:
                with open(self.registry_path, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    old_list = old_data.get("stations", []) if isinstance(old_data, dict) else (old_data if isinstance(old_data, list) else [])
                    for old_item in old_list:
                        if isinstance(old_item, dict):
                            s_id = str(old_item.get("LocationID", ""))
                            if s_id:
                                existing_map[s_id] = old_item
            except Exception:
                pass

        data = []
        for r in records:
            r_dict = r.views.get("ampol", r.to_dict())
            s_id = str(r_dict.get("LocationID") or r.source_id or r.key)

            if s_id in existing_map:
                old_node = existing_map[s_id]
                if detect_station_changes(old_node, r_dict):
                    archive_station_record(archive_path, f"Ampol_{s_id}", old_node, now_utc)

            data.append(r_dict)

        payload = {
            "version": "1.0",
            "updated_at": now_utc,
            "station_count": len(records),
            "stations": data
        }
        target_paths = [self.registry_path, os.path.join(_repo_root, "Tessie", "ampcharge_chargers.json")]
        self.registry_path = save_json_with_fallback(target_paths, payload)
        self._cache = records

    def sync_all_australia(self, auto_save: bool = True) -> List[UnifiedStationRecord]:
        """
        Fetches every AmpCharge / Ampol Foodary EV-fast-charging site across
        Australia in a single siteservice call and saves the complete
        dataset to ampcharge_chargers.json.
        """
        raw_stations = self._fetch_raw()
        records: List[UnifiedStationRecord] = []
        for item in raw_stations:
            rec = self._convert_ampol_node(item)
            if rec:
                records.append(rec)

        if records and auto_save:
            self.save_cached_registry(records)
        return records

    # Alias matching the simpler single-fetch providers' (EVX/Evie/bp pulse)
    # CLI convention (p.sync()), while sync_all_australia() stays the more
    # descriptive name matching ChargefoxProvider's.
    def sync(self) -> List[UnifiedStationRecord]:
        return self.sync_all_australia()

    def reverify_station(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Re-fetches all stations fresh, updates cache, archives if changed, and returns the matching one."""
        clean_id = str(station_id).replace("Ampol_", "").strip()
        raw_stations = self._fetch_raw()
        matched = None
        fresh_records = []
        for item in raw_stations:
            rec = self._convert_ampol_node(item)
            if rec:
                fresh_records.append(rec)
                if str(item.get("LocationID", "")).strip() == clean_id:
                    matched = rec

        if fresh_records:
            self.save_cached_registry(fresh_records)
        return matched

    def search_nearby(self, lat: float, lon: float, radius_km: float = 15.0, filters: Optional[Dict[str, Any]] = None) -> List[UnifiedStationRecord]:
        """Finds AmpCharge/Ampol Foodary stations within radius of coordinates."""
        cached = self.load_cached_registry()
        results = []
        for st in cached:
            dist = haversine_km(lat, lon, st.lat, st.lon)
            if dist <= radius_km:
                st.distance_km = round(dist, 2)
                results.append(st)
        results = filter_station_records(results, filters)
        results.sort(key=lambda s: s.distance_km if s.distance_km is not None else 999999)
        return results

    def search_by_text(self, query: str, filters: Optional[Dict[str, Any]] = None) -> List[UnifiedStationRecord]:
        """Searches AmpCharge/Ampol Foodary stations by name, suburb, or address."""
        all_stations = self.load_cached_registry()
        q_clean = query.lower().strip()
        results = []
        for st in all_stations:
            searchable = f"{st.name} {st.suburb} {st.state} {st.address} {st.network}".lower()
            if q_clean in searchable:
                results.append(st)
        return filter_station_records(results, filters)

    def get_station_details(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Finds a specific station by key or LocationID from the cached registry."""
        clean_id = str(station_id).replace("Ampol_", "").strip().lower()
        for st in self.load_cached_registry():
            if st.key.lower() == f"ampol_{clean_id}" or st.source_id.lower() == clean_id:
                return st
        return None
