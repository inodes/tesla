#!/usr/bin/env python3
"""
Tesla Charger Provider (Tier 1 First-Party)
===========================================
First-party provider for Tesla Superchargers and Destination Chargers.
Reads from local authoritative registries with live WebKit Find Us support.
"""

import os
import json
import re
from typing import Dict, List, Optional, Any

from .chargers_base import BaseChargerProvider, UnifiedStationRecord, haversine_km, filter_station_records

_script_dir = os.path.dirname(os.path.abspath(__file__))
_tools_dir = os.path.dirname(_script_dir)
_repo_root = os.path.dirname(_tools_dir)


def _resolve_tesla_registry_paths() -> List[str]:
    """Finds paths to tesla_chargers.json and tesla_superchargers.json."""
    found = []
    candidates = []
    
    tessie_home = None
    try:
        from lib.tessie_api_common import resolve_tessie_data_home
        tessie_home = resolve_tessie_data_home()
    except Exception:
        pass
    if tessie_home:
        candidates.extend([
            os.path.join(tessie_home, "tesla_superchargers.json"),
            os.path.join(tessie_home, "tesla_chargers.json"),
        ])
        
    candidates.extend([
        os.path.join(_repo_root, "Tessie", "tesla_superchargers.json"),
        os.path.join(_repo_root, "Tessie", "tesla_chargers.json"),
    ])
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
                    candidates.insert(0, os.path.join(td_exp, "tesla_superchargers.json"))
                    candidates.insert(1, os.path.join(td_exp, "tesla_chargers.json"))
        except Exception:
            pass

    for c in candidates:
        if os.path.isfile(c) and c not in found:
            found.append(c)
    return found


class TeslaProvider(BaseChargerProvider):
    name: str = "Tesla"
 # verified via grep 'speed_tier\s*=' - line ~126 sets
 # "ac_destination" for destination chargers, "supercharger" otherwise.
    POSSIBLE_SPEED_TIERS = {"supercharger", "ac_destination"}
    tier: int = 1  # First-Party Authoritative

    def __init__(self, registry_paths: Optional[List[str]] = None):
        self.registry_paths = registry_paths or _resolve_tesla_registry_paths()
        self._cache: Optional[List[UnifiedStationRecord]] = None

    def load_cached_registry(self) -> List[UnifiedStationRecord]:
        """Loads and normalizes Tesla stations from local JSON registry files."""
        if self._cache is not None:
            return self._cache

        records: List[UnifiedStationRecord] = []
        seen_keys = set()

        for reg_file in self.registry_paths:
            if not os.path.isfile(reg_file):
                continue

            try:
                with open(reg_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue

            stations_dict = {}
            if isinstance(data, dict):
                if "stations" in data and isinstance(data["stations"], dict):
                    stations_dict = data["stations"]
                elif "stations" in data and isinstance(data["stations"], list):
                    stations_dict = {s.get("name", f"Station_{i}"): s for i, s in enumerate(data["stations"])}
                else:
                    stations_dict = data
            elif isinstance(data, list):
                stations_dict = {s.get("name", f"Station_{i}"): s for i, s in enumerate(data)}

            for key, info in stations_dict.items():
                if not isinstance(info, dict):
                    continue

 # Metadata & naming (findus_location_name is authoritative over findus_list_name)
                t_meta = info.get("tesla_metadata", {}) if isinstance(info.get("tesla_metadata"), dict) else {}
                findus_loc_name = t_meta.get("findus_location_name") or ""
                findus_list_name = t_meta.get("findus_list_name") or ""
                raw_name = findus_loc_name or t_meta.get("name") or info.get("name") or key
                clean_name = raw_name.replace("Tesla Supercharger - ", "").strip()

                st_type = str(t_meta.get("type") or info.get("type", "supercharger")).lower()
                is_dest = (st_type in ["destination", "destination_charging", "destination_charger"] or 
                           "destination" in key.lower() or 
                           info.get("is_destination", False))

                if key in seen_keys:
                    if is_dest and f"{key} (Destination)" not in seen_keys:
                        key = f"{key} (Destination)"
                    elif not is_dest and f"{key} (Supercharger)" not in seen_keys:
                        key = f"{key} (Supercharger)"
                    else:
                        continue

 # Location block (handles nested schema)
                loc = info.get("location", {}) if isinstance(info.get("location"), dict) else {}
                lat = loc.get("lat") or loc.get("latitude") or info.get("lat") or info.get("latitude")
                lon = loc.get("lon") or loc.get("longitude") or loc.get("lng") or info.get("lon") or info.get("longitude") or info.get("lng")
                if lat is None or lon is None:
                    continue
                try:
                    lat = float(lat)
                    lon = float(lon)
                except (ValueError, TypeError):
                    continue
                
                speed_tier = "ac_destination" if is_dest else "supercharger"
                network = "Tesla Destination" if is_dest else "Tesla"

 # Hardware block
                hw = info.get("hardware", {}) if isinstance(info.get("hardware"), dict) else {}
                max_kw = hw.get("max_power_kw") or hw.get("maxPowerKw") or hw.get("power_kw") or info.get("max_power_kw") or info.get("maxPowerKw")
                if max_kw is None:
                    max_kw = 22.0 if is_dest else 250.0
                else:
                    try:
                        max_kw = float(max_kw)
                    except (ValueError, TypeError):
                        max_kw = 22.0 if is_dest else 250.0

                stalls = hw.get("stalls") or hw.get("stall_count") or info.get("stalls") or info.get("stall_count") or 0
                try:
                    stalls = int(stalls)
                except (ValueError, TypeError):
                    stalls = 0

                hw_tier = hw.get("tier") or ("V4" if max_kw >= 300 else ("V3" if max_kw >= 250 else ("V2" if not is_dest else "AC")))

 # Compatibility block
                comp = info.get("compatibility", {}) if isinstance(info.get("compatibility"), dict) else {}

 # Tariffs block
                tariffs = info.get("tariffs", {}) if isinstance(info.get("tariffs"), dict) else {}
                pricing_block = info.get("pricing", {}) if isinstance(info.get("pricing"), dict) else tariffs

 # Non-Tesla rate schedule + effective-date extraction - both were
 # entirely missing from this provider until 2026-09-14 (see
 # AGENTS.local.md). charger_find_tesla.py's own
 # display_preview() already reads these same registry keys
 # (tariffs.non_tesla.rate_schedules, and top-level valid_from /
 # last_verified on the raw entry) - this mirrors that exactly so
 # charger_find.py can show the same "Non-Tesla ... Rates" and
 # "Effective Date" info the old script always showed.
                non_tesla_rate_schedules = []
                _nt_block = tariffs.get("non_tesla", {}) if isinstance(tariffs.get("non_tesla"), dict) else {}
                for _nt_item in _nt_block.get("rate_schedules", []) or []:
                    if isinstance(_nt_item, dict):
                        _nt_r = _nt_item.get("rate_per_kwh") or _nt_item.get("rate")
                        if _nt_r is not None:
                            try:
                                _nt_r_f = float(_nt_r)
                                non_tesla_rate_schedules.append({
                                    "name": _nt_item.get("label") or _nt_item.get("name") or "Non-Tesla Rate",
                                    "start": _nt_item.get("start_time") or _nt_item.get("start", "00:00"),
                                    "end": _nt_item.get("end_time") or _nt_item.get("end", "24:00"),
                                    "rate": _nt_r_f
                                })
                            except (ValueError, TypeError):
                                pass
                effective_date = info.get("valid_from") or info.get("first_seen") or ""

                schedule = []
                current_rate = None
                rate_range = ""
                pricing_model = "unknown"

 # Check tesla_members sub-dict in tariffs or tessie_cost_config
                tm = tariffs.get("tesla_members", {})
                schedules_list = tm.get("rate_schedules", []) if isinstance(tm, dict) else []
                if not schedules_list and "schedule" in pricing_block:
                    schedules_list = pricing_block.get("schedule", [])
                if not schedules_list and "tessie_cost_config" in info:
                    schedules_list = (info.get("tessie_cost_config", {}) or {}).get("rate_schedules", [])

                if schedules_list:
                    pricing_model = "time_of_use" if len(schedules_list) > 1 else "flat"
                    rates = []
                    for item in schedules_list:
                        if isinstance(item, dict):
                            r = item.get("rate_per_kwh") or item.get("rate")
                            if r is not None:
                                try:
                                    r_f = float(r)
                                    rates.append(r_f)
                                    schedule.append({
                                        "name": item.get("label") or item.get("name") or "Standard Rate",
                                        "start": item.get("start_time") or item.get("start", "00:00"),
                                        "end": item.get("end_time") or item.get("end", "24:00"),
                                        "rate": r_f
                                    })
                                except (ValueError, TypeError):
                                    pass
                    if rates:
                        min_r, max_r = min(rates), max(rates)
                        if min_r == max_r:
                            rate_range = f"${min_r:.2f}/kWh"
                            pricing_model = "flat"
                            current_rate = min_r
                        else:
                            rate_range = f"${min_r:.2f} - ${max_r:.2f}/kWh"
                            current_rate = rates[0]
                else:
                    flat_r = tariffs.get("per_kwh_flat") or pricing_block.get("rate_per_kwh") or (info.get("tessie_cost_config", {}) or {}).get("per_kwh_flat")
                    if flat_r is not None and float(flat_r) > 0:
                        try:
                            current_rate = float(flat_r)
                            pricing_model = "flat"
                            rate_range = f"${current_rate:.2f}/kWh"
                            schedule.append({
                                "name": "Base Rate",
                                "start": "00:00",
                                "end": "24:00",
                                "rate": current_rate
                            })
                        except (ValueError, TypeError):
                            pass
                    elif is_dest:
                        pricing_model = "free"
                        current_rate = 0.0
                        rate_range = "Free"

                idle_fee = None
                cong_fee = None
                if not is_dest:
                    raw_idle = tariffs.get("idle_fee_per_min")
                    raw_cong = tariffs.get("congestion_fee_per_min") or (info.get("tessie_cost_config", {}) or {}).get("congestion_fee_per_min")
 # Enforce Tesla's real either/or policy (per findus's own fee
 # pages - a site has congestion pricing OR idle pricing, never
 # both): CONGESTION WINS whenever both are present. Fixed
 # 2026-09-14 - this used to prefer idle whenever it was
 # present/nonzero, on the mistaken assumption that "standard
 # AU Superchargers have an idle fee" as a default. That's
 # backwards: charger_find_tesla.py's own scraper already
 # documents the same either/or rule and nulls idle_fee once a
 # real congestion_fee is found - this function must not
 # second-guess that by resurrecting idle_fee whenever the
 # upstream registry still has a stale nonzero value in both
 # fields (see AGENTS.local.md - Macquarie, NSW showed "Idle
 # $1.00/min" in charger_find.py when the findus page itself
 # only lists "Congestion fees (up to) $0.50/min").
                    if raw_cong is not None:
                        try:
                            if float(raw_cong) > 0:
                                cong_fee = float(raw_cong)
                        except (ValueError, TypeError):
                            pass
                    if cong_fee is None and raw_idle is not None:
                        try:
                            idle_fee = float(raw_idle)
                        except (ValueError, TypeError):
                            pass

 # Plugs
                plugs = ["IEC 62196 Type 2"] if is_dest else ["Tesla Supercharger (CCS2)"]

                address_str = loc.get("address") or info.get("address", "")
                suburb = loc.get("suburb") or info.get("suburb", "") or loc.get("city", "") or info.get("city", "")
                state = loc.get("state") or info.get("state", "")

                record = UnifiedStationRecord(
                    key=key,
                    name=clean_name,
                    network=network,
                    operator_name="Tesla",
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
                    hardware_model=f"Tesla {hw_tier}" if not is_dest else "Tesla Wall Connector",
                    pricing_model=pricing_model,
                    current_rate_per_kwh=current_rate,
                    rate_range=rate_range,
                    schedule=schedule,
                    non_tesla_schedule=non_tesla_rate_schedules,
                    idle_fee_per_min=idle_fee,
                    congestion_fee_per_min=cong_fee,
                    tier=1,
                    source="tesla",
                    source_id=str(t_meta.get("findus_id") or t_meta.get("short_name") or info.get("id") or key),
                    url=t_meta.get("findus_url") or info.get("url", f"https://www.tesla.com/findus/location/supercharger/{key.lower().replace(' ', '')}"),
                    last_verified_utc=info.get("last_verified", "") or info.get("last_verified_utc", ""),
                    effective_date_utc=effective_date,
                    metadata={
                        "access_hours": info.get("access", {}).get("hours", "24/7") if isinstance(info.get("access"), dict) else "24/7",
                        "hardware_tier": hw_tier,
                        "open_to_non_tesla": comp.get("open_to_non_tesla", False),
                        "tesla_only": comp.get("tesla_only", not comp.get("open_to_non_tesla", False)),
                        "site_type": speed_tier,
                        "congestion_fee_per_min": cong_fee,
                        "findus_id": str(t_meta.get("findus_id") or info.get("id") or ""),
                        "findus_list_name": findus_list_name,
                        "findus_list_short_name": t_meta.get("findus_list_short_name", ""),
                        "findus_location_name": findus_loc_name,
                        "findus_location_short_name": t_meta.get("findus_location_short_name", ""),
 # Human-readable, file-name-safe, location-based short name -
 # this is what invoice matching (analyse_charging.py)
 # keys off, so it must never be the arbitrary numeric findus_id.
 # Fixed 2026-09-14: this key didn't exist before, so
 # charger_find.py's display fell back to source_id
 # (the numeric findus_id) for every single Tesla station.
                        "short_name": t_meta.get("findus_location_short_name") or t_meta.get("short_name") or key,
                        "general_location": t_meta.get("general_location") or t_meta.get("location_name") or suburb
                    },
                    amenities=info.get("amenities", {}) if isinstance(info.get("amenities"), dict) else {"items": info.get("amenities", [])},
                    keywords=[
                        k for k in dict.fromkeys([
                            key,
                            raw_name,
                            clean_name,
                            findus_list_name,
                            findus_loc_name,
                            str(t_meta.get("findus_id") or ""),
                            str(t_meta.get("short_name") or ""),
                            t_meta.get("general_location", ""),
                            suburb,
                            state,
                            address_str,
                            *info.get("keywords", []),
                            *t_meta.get("keywords", []),
                            *info.get("aliases", [])
                        ]) if k and str(k).strip()
                    ],
                    views={"tesla": info}
                )
                records.append(record)
                seen_keys.add(key)

        self._cache = records
        return self._cache

    def search_nearby(self, lat: float, lon: float, radius_km: float = 15.0, filters: Optional[Dict[str, Any]] = None) -> List[UnifiedStationRecord]:
        """Finds Tesla stations within geographic radius."""
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
        """Searches Tesla stations by name, suburb, state, ID, or keywords."""
        all_stations = self.load_cached_registry()
        q_clean = query.lower().strip()
        results = []
        for st in all_stations:
            kw_str = " ".join(st.keywords) if isinstance(st.keywords, list) else str(st.keywords or "")
            meta_str = " ".join(str(v) for v in st.metadata.values() if isinstance(v, (str, int, float)))
            searchable = f"{st.key} {st.name} {st.suburb} {st.state} {st.address} {st.network} {st.source_id} {kw_str} {meta_str}".lower()
            if q_clean in searchable:
                results.append(st)
        return filter_station_records(results, filters)

    def get_station_details(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Fetches station record by key or ID."""
        for st in self.load_cached_registry():
            if st.source_id == station_id or st.key.lower() == station_id.lower() or st.name.lower() == station_id.lower():
                return st
        return None
