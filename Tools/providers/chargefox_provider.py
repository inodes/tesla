#!/usr/bin/env python3
"""
Chargefox Charger Provider (Tier 1 First-Party)
===============================================
First-party provider for Chargefox, NRMA, WA EV Network, and QLD Electric Highway.
Connects directly to Chargefox GraphQL API with local JSON caching.
"""

import os
import re
import json
import time
import random
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from .base import BaseChargerProvider, UnifiedStationRecord, haversine_km, filter_station_records, detect_station_changes, archive_station_record, save_json_with_fallback

_script_dir = os.path.dirname(os.path.abspath(__file__))
_tools_dir = os.path.dirname(_script_dir)
_repo_root = os.path.dirname(_tools_dir)

CHARGEFOX_GRAPHQL_URL = "https://app.chargefox.com/graphql"


def _resolve_chargefox_registry_path() -> str:
    """Finds path to chargefox_chargers.json."""
    candidates = []
    tessie_home = None
    try:
        from tessie_api_common import resolve_tessie_data_home
        tessie_home = resolve_tessie_data_home()
    except Exception:
        pass
    if tessie_home:
        candidates.append(os.path.join(tessie_home, "chargefox_chargers.json"))
        
    candidates.append(os.path.join(_repo_root, "Tessie", "chargefox_chargers.json"))
    config_path = os.path.join(_repo_root, "Tessie", "config.json")
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                td = cfg.get("tessie_directory")
                if td:
                    td_exp = os.path.expanduser(td)
                    candidates.insert(0, os.path.join(td_exp, "chargefox_chargers.json"))
        except Exception:
            pass

    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0] if candidates else os.path.join(_repo_root, "Tessie", "chargefox_chargers.json")


def resolve_au_state(lat: float, lon: float) -> str:
    """Deterministically resolves Australian State/Territory code from GPS coordinates."""
    # ACT: approx -36.0 to -35.1 lat, 148.7 to 149.4 lon
    if -36.0 <= lat <= -35.1 and 148.7 <= lon <= 149.4:
        return "ACT"
    # TAS: approx -44.0 to -39.5 lat, 143.5 to 149.0 lon
    if -44.0 <= lat <= -39.5 and 143.5 <= lon <= 149.0:
        return "TAS"
    # WA: lon < 129.0
    if 112.0 <= lon < 129.0 and -36.0 <= lat <= -13.0:
        return "WA"
    # NT: 129.0 <= lon <= 138.0 and lat > -26.0
    if 129.0 <= lon <= 138.0 and -26.0 < lat <= -10.0:
        return "NT"
    # SA: 129.0 <= lon <= 141.0 and lat <= -26.0
    if 129.0 <= lon <= 141.0 and -38.5 <= lat <= -26.0:
        return "SA"
    # QLD: lat > -29.0 and lon >= 138.0
    if lat > -29.0 and 138.0 <= lon <= 154.0:
        return "QLD"
    # VIC: lat <= -34.0 and lon <= 150.0 and lat > -39.5
    if -39.5 <= lat <= -34.0 and 140.9 <= lon <= 150.0:
        if lat <= -36.0 or (lat <= -34.2 and lon < 143.0) or (lat <= -35.8 and lon < 147.0):
            return "VIC"
        if lat < -36.1:
            return "VIC"
    # NSW fallback for eastern Australia
    if -37.6 <= lat <= -28.0 and 141.0 <= lon <= 154.0:
        return "NSW"
    # New Zealand
    if -48.0 <= lat <= -34.0 and 166.0 <= lon <= 179.0:
        return "NZ"
    return "NSW"


def extract_suburb_from_station_name(name: str) -> str:
    """Extracts a clean suburb/location name from Chargefox / NRMA station names."""
    if not name:
        return ""
    clean = name.strip()
    
    # Strip common operator, dealership, and retailer prefixes
    prefixes = [
        r"^NRMA\s+",
        r"^RACV\s+",
        r"^RACQ\s+",
        r"^RAA\s+",
        r"^RACT\s+",
        r"^Chargefox\s+",
        r"^WA EV Network\s+",
        r"^Qld Electric Highway\s+",
        r"^Teachers Mutual Bank\s+",
        r"^Meriton\s+",
        r"^Woolworths\s+",
        r"^Coles\s+",
        r"^Westfield\s+",
        r"^ALDI\s+",
        r"^IGA\s+",
        r"^Audi\s+Alto\s+",
        r"^Audi\s+",
        r"^BMW\s+",
        r"^Porsche\s+Centre\s+",
        r"^Porsche\s+",
        r"^Mercedes-Benz\s+",
        r"^Mercedes\s+",
        r"^Hyundai\s+",
        r"^Nissan\s+",
        r"^BP\s+Pulse\s+",
        r"^BP\s+",
        r"^Ampol\s+Amplify\s+",
        r"^Ampol\s+",
        r"^Wilson Parking\s+",
        r"^Secure Parking\s+",
        r"^Care Park\s+",
        r"^City of\s+",
        r"^Shire of\s+",
    ]
    for p in prefixes:
        clean = re.sub(p, "", clean, flags=re.IGNORECASE).strip()
    
    # Strip common suffixes / descriptors
    suffixes = [
        r"\s+Polecharger.*$",
        r"\s+Supercharger.*$",
        r"\s+Destination.*$",
        r"\s+Charging Station.*$",
        r"\s+EV Charger.*$",
        r"\s+Fast Charger.*$",
        r"\s+Resort.*$",
        r"\s+Square.*$",
        r"\s+Plaza.*$",
        r"\s+Shopping Centre.*$",
        r"\s+Shopping Village.*$",
        r"\s+Outlet Centre.*$",
        r"\s+Marketplace.*$",
        r"\s+Central.*$",
        r"\s+City Hub.*$",
        r"\s+Hub.*$",
        r"\s+Car Park.*$",
        r"\s+Carpark.*$",
        r"\s+Council.*$",
        r"\s+Civic Centre.*$",
        r"\s+MSA.*$",
        r"\s+Level\s+P\d+.*$",
        r"\s*-\s*Level\s+P\d+.*$",
        r"\s*-\s*Carpark.*$",
        r"\s*-\s*Basement.*$",
    ]
    for s in suffixes:
        clean = re.sub(s, "", clean, flags=re.IGNORECASE).strip()
        
    if " - " in clean:
        clean = clean.split(" - ")[0].strip()
        
    return clean or name


class ChargefoxProvider(BaseChargerProvider):
    name: str = "Chargefox"
    tier: int = 1  # First-Party Authoritative

    def __init__(self, registry_path: Optional[str] = None):
        self.registry_path = registry_path or _resolve_chargefox_registry_path()
        self._cache: Optional[List[UnifiedStationRecord]] = None

    def _query_graphql(self, query: str, variables: Optional[Dict[str, Any]] = None, timeout: int = 15, max_retries: int = 4) -> Dict[str, Any]:
        """Executes GraphQL request against Chargefox API with retry and backoff on rate-limits (HTTP 429)."""
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            CHARGEFOX_GRAPHQL_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"
            }
        )
        for attempt in range(1, max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < max_retries:
                    wait_time = (2 ** attempt) + random.uniform(0.5, 1.5)
                    time.sleep(wait_time)
                    continue
                elif attempt < max_retries:
                    time.sleep(1.0)
                    continue
                return {}
            except Exception:
                if attempt < max_retries:
                    time.sleep(1.0)
                    continue
                return {}
        return {}

    def _convert_chargefox_node(self, node: Dict[str, Any]) -> Optional[UnifiedStationRecord]:
        """Converts raw Chargefox GraphQL station object into UnifiedStationRecord."""
        if not node or not isinstance(node, dict):
            return None

        loc_id = str(node.get("id", ""))
        name = node.get("name") or "Chargefox Station"
        lat = node.get("lat")
        lon = node.get("lng") or node.get("lon")
        if lat is None or lon is None:
            return None
        try:
            lat = float(lat)
            lon = float(lon)
        except (ValueError, TypeError):
            return None

        # Determine Network branding
        net = "Chargefox"
        name_lower = name.lower()
        if "nrma" in name_lower:
            net = "NRMA"
        elif "wa ev network" in name_lower or "synergy" in name_lower:
            net = "WA EV Network"
        elif "queensland electric highway" in name_lower or "qeh" in name_lower:
            net = "Qld Electric Highway"

        # Max power & Speed Tier
        max_power = node.get("maxPower")
        speed_raw = str(node.get("chargingSpeed", "")).lower()
        
        max_kw = 0.0
        if max_power is not None:
            try:
                max_kw = float(max_power)
            except (ValueError, TypeError):
                pass

        if max_kw == 0.0:
            if "ultra" in speed_raw or "350" in speed_raw:
                max_kw = 350.0
            elif "fast" in speed_raw or "50" in speed_raw:
                max_kw = 50.0
            elif "standard" in speed_raw or "ac" in speed_raw or "22" in speed_raw:
                max_kw = 22.0
            else:
                max_kw = 50.0

        speed_tier = "dc_fast" if max_kw >= 25.0 else "ac_destination"

        # Stalls and Connectors
        stations = node.get("chargeStations", [])
        stalls_total = len(stations) if stations else 1
        stalls_avail = 0
        plugs = set()
        hardware_models = set()

        if stations:
            for cs in stations:
                if cs.get("status") == "AVAILABLE":
                    stalls_avail += 1
                model = cs.get("model")
                if model:
                    hardware_models.add(model)
                for conn in cs.get("connectors", []):
                    plug_info = conn.get("plug", {})
                    plug_name = plug_info.get("typeName") or plug_info.get("name")
                    if plug_name:
                        plugs.add(plug_name)
        else:
            teth = node.get("tetheredConnectors", 0) or 0
            unteth = node.get("untetheredConnectors", 0) or 0
            stalls_total = max(1, teth + unteth)
            stalls_avail = None

        if not plugs:
            plugs = ["CCS2", "CHAdeMO"] if speed_tier == "dc_fast" else ["Type 2"]

        # Tariffs & Pricing
        pr = node.get("priceRange") or {}
        e_desc = pr.get("energyDescription") or ""
        t_desc = pr.get("timeDescription") or ""
        
        current_rate = None
        rate_range = ""
        pricing_model = "unknown"

        if e_desc:
            rate_range = e_desc.strip()
            if "free" in rate_range.lower():
                pricing_model = "free"
                current_rate = 0.0
            else:
                m = re.search(r"\$([0-9]+\.[0-9]+)", e_desc)
                if m:
                    current_rate = float(m.group(1))
                    pricing_model = "flat" if "-" not in e_desc else "time_of_use"

        if not rate_range and net == "NRMA":
            rate_range = "$0.54 - $0.60/kWh"
            pricing_model = "flat"

        # Clean address components
        addr_obj = node.get("address") or {}
        address_str = ""
        suburb = ""
        state = ""
        postcode = ""
        if isinstance(addr_obj, dict):
            address_str = addr_obj.get("streetAddress", "") or addr_obj.get("address", "")
            suburb = addr_obj.get("suburb", "") or addr_obj.get("city", "")
            state = addr_obj.get("state", "")
            postcode = addr_obj.get("postcode", "")

        # Fallback state resolution from GPS coordinates
        if not state and lat is not None and lon is not None:
            state = resolve_au_state(lat, lon)

        # Fallback suburb resolution from station name
        if not suburb:
            suburb = extract_suburb_from_station_name(name)

        return UnifiedStationRecord(
            key=f"Chargefox_{loc_id}",
            name=name,
            network=net,
            operator_name="Chargefox",
            address=address_str or (f"{suburb}, {state}" if suburb and state else ""),
            suburb=suburb,
            state=state.upper() if state else "",
            postcode=str(postcode),
            country="Australia",
            lat=lat,
            lon=lon,
            timezone="Australia/Sydney",
            speed_tier=speed_tier,
            max_power_kw=max_kw,
            stalls_total=stalls_total,
            stalls_available=stalls_avail,
            plugs=sorted(list(plugs)),
            tethered=True,
            hardware_model=", ".join(hardware_models) if hardware_models else ("Chargefox Fast Charger" if speed_tier == "dc_fast" else "Chargefox Destination"),
            pricing_model=pricing_model,
            current_rate_per_kwh=current_rate,
            rate_range=rate_range,
            schedule=[],
            idle_fee_per_min=None,
            tier=1,
            source="chargefox",
            source_id=loc_id,
            url=f"https://app.chargefox.com/charge-stations/{loc_id}",
            last_verified_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            metadata={
                "branding_logo": node.get("brandingLogoUrl"),
                "time_description": t_desc,
                "open_to_non_tesla": True,
                "tesla_only": False,
                "hardware_tier": "Chargefox " + speed_tier.replace("_", " ").title()
            },
            amenities={},
            keywords=[name, suburb, state, net],
            views={"chargefox": node}
        )

    def load_cached_registry(self) -> List[UnifiedStationRecord]:
        """Loads Chargefox stations from local cache file."""
        if self._cache is not None:
            return self._cache

        records = []
        if os.path.isfile(self.registry_path):
            try:
                with open(self.registry_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    stations_list = data.get("stations", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                    for item in stations_list:
                        rec = self._convert_chargefox_node(item)
                        if rec:
                            records.append(rec)
            except Exception:
                pass

        self._cache = records
        return self._cache

    def save_cached_registry(self, records: List[UnifiedStationRecord]) -> None:
        """Saves records to local JSON cache file, archiving any changed records."""
        archive_path = os.path.join(os.path.dirname(self.registry_path), "chargefox_chargers_archived.json")
        now_utc = datetime.now(timezone.utc).isoformat()

        # Load existing registry to detect tariff or hardware changes
        existing_map = {}
        if os.path.isfile(self.registry_path):
            try:
                with open(self.registry_path, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    old_list = old_data.get("stations", []) if isinstance(old_data, dict) else (old_data if isinstance(old_data, list) else [])
                    for item in old_list:
                        if isinstance(item, dict):
                            s_id = str(item.get("id", ""))
                            if s_id:
                                existing_map[s_id] = item
            except Exception:
                pass

        data = []
        for r in records:
            r_dict = r.views.get("chargefox", r.to_dict())
            s_id = str(r_dict.get("id") or r.source_id or r.key)
            
            # Check if existing station changed
            if s_id in existing_map:
                old_node = existing_map[s_id]
                if detect_station_changes(old_node, r_dict):
                    archive_station_record(archive_path, f"Chargefox_{s_id}", old_node, now_utc)
            
            data.append(r_dict)

        payload = {
            "version": "1.0",
            "updated_at": now_utc,
            "station_count": len(records),
            "stations": data
        }
        # AGENTS.md BUG-067: this used to try each target path in a loop
        # and just fall through silently if EVERY write failed - nothing
        # checked the resulting `saved` flag, so a total failure still let
        # the caller print "successfully saved N stations". Also never
        # atomic - a killed process mid-write could corrupt the file that
        # load_json_registry()'s own callers (tessie_charging_analyzer.py)
        # depend on. save_json_with_fallback() (base.py) now raises if
        # every candidate path fails, and writes atomically either way.
        target_paths = [self.registry_path, os.path.join(_repo_root, "Tessie", "chargefox_chargers.json")]
        self.registry_path = save_json_with_fallback(target_paths, payload)
        self._cache = records

    def reverify_station(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Fetches fresh details for a single station, updates cache, archives if changed, and returns updated record."""
        clean_id = str(station_id).replace("Chargefox_", "").strip()
        rec = self.get_station_details(clean_id)
        if not rec:
            return None

        # Update cache in place
        cached = self.load_cached_registry()
        updated_list = []
        found = False
        for c in cached:
            c_id = str(c.source_id or c.key).replace("Chargefox_", "").strip()
            if c_id == clean_id:
                updated_list.append(rec)
                found = True
            else:
                updated_list.append(c)
        if not found:
            updated_list.append(rec)

        self.save_cached_registry(updated_list)
        return rec

    def sync_all_australia(self, batch_size: int = 20, pacing_delay: float = 0.25, auto_save: bool = True) -> List[UnifiedStationRecord]:
        """
        Discovers all Chargefox / NRMA stations across Australia via regional bounding boxes,
        fetches full details in aliased GraphQL batches, and saves the complete dataset to chargefox_chargers.json.
        """
        regional_bounds = [
            ("NSW / ACT", -37.5, 140.0, -28.0, 154.0),
            ("VIC / TAS", -44.0, 140.0, -34.0, 150.0),
            ("QLD", -29.0, 137.0, -10.0, 154.0),
            ("SA / NT", -38.0, 128.0, -11.0, 141.0),
            ("WA", -36.0, 112.0, -13.0, 130.0),
            ("NZ", -47.5, 166.0, -34.0, 179.0),
        ]
        
        all_ids = []
        seen_ids = set()
        for reg_name, sw_lat, sw_lng, ne_lat, ne_lng in regional_bounds:
            bounds_q = f"""
            query {{
              locationsByBounds(sw: {{lat: {sw_lat}, lng: {sw_lng}}}, ne: {{lat: {ne_lat}, lng: {ne_lng}}}) {{
                id
                lat
                lng
                maxPower
                chargingSpeed
              }}
            }}
            """
            r = self._query_graphql(bounds_q)
            nodes = r.get("data", {}).get("locationsByBounds", [])
            for n in nodes:
                nid = n.get("id")
                if nid and nid not in seen_ids:
                    seen_ids.add(nid)
                    all_ids.append(nid)
            time.sleep(0.1)

        if not all_ids:
            return []

        records: List[UnifiedStationRecord] = []
        
        # Batch query locations using aliased GraphQL fields
        for i in range(0, len(all_ids), batch_size):
            chunk = all_ids[i:i + batch_size]
            loc_queries = []
            for idx, lid in enumerate(chunk):
                loc_queries.append(f"""
                loc_{idx}: location(id: \"{lid}\") {{
                  id
                  name
                  lat
                  lng
                  maxPower
                  chargingSpeed
                  tetheredConnectors
                  untetheredConnectors
                  priceRange {{
                    energyDescription
                    timeDescription
                  }}
                  chargeStations {{
                    id
                    name
                    model
                    power
                    status
                    connectors {{
                      id
                      status
                      plug {{
                        typeName
                      }}
                    }}
                  }}
                }}""")
            batch_q = "query {\n" + "\n".join(loc_queries) + "\n}"
            r = self._query_graphql(batch_q)
            data_dict = r.get("data", {})
            for node in data_dict.values():
                if node:
                    rec = self._convert_chargefox_node(node)
                    if rec:
                        records.append(rec)
            time.sleep(pacing_delay)

        if records and auto_save:
            self.save_cached_registry(records)
        return records

    def search_nearby(self, lat: float, lon: float, radius_km: float = 15.0, filters: Optional[Dict[str, Any]] = None) -> List[UnifiedStationRecord]:
        """Finds stations near coordinates, fetching detailed metadata for nearby candidates."""
        cached = self.load_cached_registry()
        results = []

        # If cache exists with populated names, query from cache
        if cached and any(s.name != "Chargefox Station" for s in cached):
            for st in cached:
                dist = haversine_km(lat, lon, st.lat, st.lon)
                if dist <= radius_km:
                    st.distance_km = round(dist, 2)
                    results.append(st)
            results = filter_station_records(results, filters)
            results.sort(key=lambda s: s.distance_km if s.distance_km is not None else 999999)
            return results

        # Fallback to live API bounding box + concurrent details enrichment
        deg_lat = radius_km / 111.0
        deg_lon = radius_km / 90.0
        bounds_q = f"""
        query {{
          locationsByBounds(sw: {{lat: {lat - deg_lat:.5f}, lng: {lon - deg_lon:.5f}}}, ne: {{lat: {lat + deg_lat:.5f}, lng: {lon + deg_lon:.5f}}}) {{
            id
            lat
            lng
            maxPower
            chargingSpeed
          }}
        }}
        """
        candidate_ids = []
        try:
            r = self._query_graphql(bounds_q)
            locs = r.get("data", {}).get("locationsByBounds", [])
            for l in locs:
                l_lat = l.get("lat")
                l_lon = l.get("lng")
                if l_lat is not None and l_lon is not None:
                    d = haversine_km(lat, lon, float(l_lat), float(l_lon))
                    if d <= radius_km:
                        candidate_ids.append((l.get("id"), d))
        except Exception:
            pass

        # Sort candidate IDs by distance and fetch top 20 detailed station records in parallel
        candidate_ids.sort(key=lambda x: x[1])
        top_ids = [c[0] for c in candidate_ids[:20]]

        if top_ids:
            with ThreadPoolExecutor(max_workers=min(10, len(top_ids))) as executor:
                detailed_records = list(executor.map(self.get_station_details, top_ids))
            for rec in detailed_records:
                if rec:
                    dist = haversine_km(lat, lon, rec.lat, rec.lon)
                    rec.distance_km = round(dist, 2)
                    results.append(rec)

        results = filter_station_records(results, filters)
        results.sort(key=lambda s: s.distance_km if s.distance_km is not None else 999999)
        return results

    def search_by_text(self, query: str, filters: Optional[Dict[str, Any]] = None) -> List[UnifiedStationRecord]:
        """Searches Chargefox stations by name or suburb."""
        all_stations = self.load_cached_registry()
        q_clean = query.lower().strip()
        results = []
        for st in all_stations:
            searchable = f"{st.name} {st.suburb} {st.state} {st.address} {st.network}".lower()
            if q_clean in searchable:
                results.append(st)
        return filter_station_records(results, filters)

    def get_station_details(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Fetches full deep station details via GraphQL query."""
        detail_q = f"""
        query {{
          location(id: "{station_id}") {{
            id
            name
            lat
            lng
            maxPower
            chargingSpeed
            tetheredConnectors
            untetheredConnectors
            priceRange {{
              energyDescription
              timeDescription
            }}
            chargeStations {{
              id
              name
              model
              power
              status
              connectors {{
                id
                status
                plug {{
                  typeName
                }}
              }}
            }}
          }}
        }}
        """
        try:
            r = self._query_graphql(detail_q)
            node = r.get("data", {}).get("location")
            if node:
                return self._convert_chargefox_node(node)
        except Exception:
            pass
        return None
