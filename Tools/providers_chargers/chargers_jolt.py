#!/usr/bin/env python3
"""
Jolt Charger Provider (Tier 1 First-Party)
===========================================
First-party provider for the JOLT free-daily-allowance DC fast charging network
across Australia (Adelaide, Melbourne, Sydney, Brisbane).

Connects directly to the official "Find a Charger" page - all station data
(id, site code, description, full address, GPS, live status) is embedded
inline in the page's own markup as a single `var jolt = {...}` JSON object
(the `charging_points` key), so unlike Exploren this needs only ONE page
fetch - no separate per-station AJAX detail calls.

Two things worth knowing about the raw feed, both handled here:
- It's fronted by Cloudflare and returns a hard block (HTTP 403, "Attention
  Required!") to a bare User-Agent-only request - a full browser-shaped
  header set (Accept/Accept-Language/Referer) is required to get HTTP 200.
- The SAME `var jolt = {...}` blob under the `/au/` path also embeds ~44
  New Zealand stations (Auckland/Christchurch/Wellington) alongside the AU
  ones - JOLT apparently operates there too, but this repo is AU-scoped
  throughout (COUNTRY_TAX_CONFIG, state filters, etc. all assume AU as the
  default), so non-AU records (no AU state token found in their address)
  are silently skipped rather than half-supported.

Hardware (max_power_kw=50.0, single CCS2 connector) is NOT in the live feed
at all - it's a documented default sourced from the feed's own station
`image` filenames (`JOLT-tritium-50kwh.png`, `Alpha-50-v2.png` - both
50kW hardware generations), since JOLT's public site doesn't publish a
per-station spec table. Pricing is similarly not in the feed: JOLT's own
Fair Use Policy and JOLT Plus pages confirm a free 7kWh/day allowance
("only one free charging session per vehicle per day"; JOLT Daily is
$0/month, JOLT Flex is $14.99/month, both include "Free 7kWh per day") but
no page found during REQ-087's investigation states a numeric $/kWh
overage rate - rather than invent one, `pricing_model` stays "unknown" and
the 7kWh/day allowance is surfaced as plain text in `rate_range` and
`metadata["free_allowance_kwh_per_day"]` instead.

Caches locally in Tessie/jolt_chargers.json with historical archiving in
Tessie/jolt_chargers_archived.json.
"""

import os
import re
import json
import urllib.request
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone

from .chargers_base import (
    RegistryReadError,
    BaseChargerProvider,
    UnifiedStationRecord,
    haversine_km,
    filter_station_records,
    detect_station_changes,
    archive_station_record,
    atomic_write_json,
    merge_with_staleness
)

_script_dir = os.path.dirname(os.path.abspath(__file__))
_tools_dir = os.path.dirname(_script_dir)
_repo_root = os.path.dirname(_tools_dir)

JOLT_FIND_A_CHARGER_URL = "https://joltcharge.com/au/find-a-charger/"

JOLT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://joltcharge.com/au/",
}

AU_STATE_CODES = ("NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT")

# Sourced from the feed's own `image` filenames (JOLT-tritium-50kwh.png,
# Alpha-50-v2.png) - not published anywhere as an explicit spec table.
DEFAULT_MAX_POWER_KW = 50.0
FREE_ALLOWANCE_KWH_PER_DAY = 7


def _resolve_jolt_registry_path() -> str:
    """Finds path to jolt_chargers.json in Tessie data directory or local fallback."""
    candidates = []
    tessie_home = None
    try:
        from lib.tessie_api_common import resolve_tessie_data_home
        tessie_home = resolve_tessie_data_home()
    except Exception:
        pass
    if tessie_home:
        candidates.append(os.path.join(tessie_home, "chargers", "jolt_chargers.json"))

    candidates.append(os.path.join(_repo_root, "resources", "jolt_chargers.json"))
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
                    candidates.insert(0, os.path.join(td_exp, "chargers", "jolt_chargers.json"))
        except Exception:
            pass

    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0] if candidates else os.path.join(_repo_root, "resources", "jolt_chargers.json")


def _extract_jolt_data(html: str) -> Dict[str, Any]:
    """
    Extracts the `var jolt = {...}` JSON object embedded in every page of the
    JOLT WordPress theme (present on find-a-charger AND every other page -
    it's theme-wide localized script data, not page-specific) by brace-
    counting from the `{` immediately after `var jolt = ` to its matching
    `}`, the same technique Exploren's own extractor uses for its `var
    locations = [...]` array, for the same reason: the blob (175 stations'
    worth of nested JSON) is far too long for a non-greedy regex to safely
    bound.
    """
    marker = "var jolt = "
    idx = html.find(marker)
    if idx == -1:
        return {}
    start = html.find("{", idx)
    if start == -1:
        return {}
    depth = 0
    end = -1
    for k in range(start, len(html)):
        c = html[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = k + 1
                break
    if end == -1:
        return {}
    try:
        return json.loads(html[start:end])
    except Exception:
        return {}


def _parse_jolt_address(raw_address: str) -> Optional[Dict[str, str]]:
    """
    Splits a JOLT address string into street/suburb/state/postcode, or
    returns None if no AU state token is found (this feed also embeds New
    Zealand stations - see module docstring - and the caller skips those).

    Handles the feed's real messiness: embedded \\r\\n access notes ahead of
    the actual street ("CCS2 Plug 1 is disabled bay 8am-6pm\\n\\n588 Balcombe
    Rd, ..."), a trailing ", Australia" that isn't always present, postcode
    appearing either before or after the state token, and postcode being
    absent entirely on some entries.
    """
    if not raw_address:
        return None
    clean = re.sub(r"[\r\n]+", " ", raw_address)
    clean = re.sub(r"\s+", " ", clean).strip()
    clean = re.sub(r",\s*(Australia|New Zealand)\s*$", "", clean, flags=re.IGNORECASE).strip()

    state_m = re.search(r"\b(" + "|".join(AU_STATE_CODES) + r")\b", clean)
    if not state_m:
        return None
    state = state_m.group(1)
    postcode_m = re.search(r"\b(\d{4})\b", clean)
    postcode = postcode_m.group(1) if postcode_m else ""

    segments = [s.strip() for s in clean.split(",") if s.strip()]
    suburb_seg = ""
    for seg in segments:
        if state in seg.split():
            suburb_seg = seg
            break
    if not suburb_seg:
        suburb_seg = segments[-1] if segments else ""

    suburb = re.sub(r"\b" + state + r"\b", "", suburb_seg)
    if postcode:
        suburb = suburb.replace(postcode, "")
    suburb = re.sub(r"\s+", " ", suburb).strip()

    if len(segments) > 1 and suburb_seg == segments[-1]:
        street = ", ".join(segments[:-1])
    else:
        street = clean

    return {"street": street, "suburb": suburb, "state": state, "postcode": postcode}


def parse_jolt_location_record(key: str, station: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Transforms one raw `charging_points` entry into the canonical multi-view
    JSON dictionary shared by every provider, or None if it's not an AU
    station (see `_parse_jolt_address`)."""
    addr_parts = _parse_jolt_address(station.get("address", ""))
    if addr_parts is None:
        return None

    station_id = station.get("id", key)
    station_code = str(station.get("name", "")).strip()
    description = str(station.get("description", "")).strip()
    name = description or station_code or f"Jolt Station #{station_id}"

    lat = float(station.get("lat") or 0.0)
    lon = float(station.get("lng") or 0.0)

    evse_status = str(station.get("evseStatus", "")).strip().lower()
    network_status = str(station.get("networkStatus", "")).strip().lower()
    site_status = str(station.get("status", "")).strip().lower()
    stalls_available = 1 if evse_status == "available" else (0 if evse_status else None)

    now_utc = datetime.now(timezone.utc).isoformat()

    return {
        "id": f"Jolt_{station_id}",
        "name": name,
        "keywords": [name, "Jolt", "JOLT", station_code, addr_parts["suburb"]],
        "location": {
            "address": re.sub(r"\s+", " ", (station.get("address") or "").replace("\r\n", " ")).strip(),
            "suburb": addr_parts["suburb"],
            "state": addr_parts["state"],
            "postcode": addr_parts["postcode"],
            "country": "Australia",
            "lat": lat,
            "lon": lon,
            "timezone": "Australia/Sydney",
        },
        "hardware": {
            "max_power_kw": DEFAULT_MAX_POWER_KW,
            "stalls": 1,
            "stalls_available": stalls_available,
            "connector_types": ["CCS2"],
            "hardware_models": ["JOLT 50kW DC Fast Charger"],
        },
        "tariffs": {
            "pricing_model": "unknown",
            "per_kwh_gross": None,
            "rate_range": f"Free (first {FREE_ALLOWANCE_KWH_PER_DAY}kWh/day via JOLT app)",
            "currency": "AUD",
        },
        "provenance": {
            "source_ids": [str(station_id)],
            "url": JOLT_FIND_A_CHARGER_URL,
            "last_verified_utc": now_utc,
        },
        "metadata": {
            "station_code": station_code,
            "site_status": site_status,
            "network_status": network_status,
            "evse_status": evse_status,
            "free_allowance_kwh_per_day": FREE_ALLOWANCE_KWH_PER_DAY,
            "image": station.get("image", ""),
        },
        "views": {},
    }


class JoltProvider(BaseChargerProvider):
    """
    Tier 1 First-Party Authoritative Provider for the JOLT free-daily-
    allowance DC fast charging network (Adelaide, Melbourne, Sydney,
    Brisbane).
    """
    name: str = "Jolt"
    tier: int = 1
 # Every station in the feed is a 50kW DC unit (see module docstring) -
 # never AC.
    POSSIBLE_SPEED_TIERS = {"dc_fast"}

    def __init__(self, registry_path: Optional[str] = None):
        self.registry_path = registry_path or _resolve_jolt_registry_path()
        self._cache: Optional[List[UnifiedStationRecord]] = None

    def fetch_all_remote(self, timeout: int = 15) -> List[UnifiedStationRecord]:
        """
        Fetches all live JOLT stations: a single page fetch of find-a-charger/
        (with full browser-shaped headers - Cloudflare hard-blocks a bare
        User-Agent-only request), then brace-counts and JSON-parses the
        embedded `var jolt = {...}` blob's `charging_points` dict. Non-AU
        (New Zealand) entries are skipped - see module docstring.
        """
        req = urllib.request.Request(JOLT_FIND_A_CHARGER_URL, headers=JOLT_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        jolt_data = _extract_jolt_data(html)
        charging_points = jolt_data.get("charging_points") or {}
        if not charging_points:
            return []

        records: List[UnifiedStationRecord] = []
        for key, station in charging_points.items():
            parsed = parse_jolt_location_record(key, station)
            if parsed is None:
                continue

            meta = parsed.get("metadata", {})
            loc_data = parsed.get("location", {})
            hw_data = parsed.get("hardware", {})
            tariffs = parsed.get("tariffs", {})
            prov = parsed.get("provenance", {})

            rec = UnifiedStationRecord(
                key=parsed["id"],
                name=parsed["name"],
                network="Jolt",
                operator_name="Jolt",
                address=loc_data.get("address", ""),
                suburb=loc_data.get("suburb", ""),
                state=loc_data.get("state", ""),
                postcode=loc_data.get("postcode", ""),
                country=loc_data.get("country", "Australia"),
                lat=loc_data.get("lat", 0.0),
                lon=loc_data.get("lon", 0.0),
                timezone=loc_data.get("timezone", "Australia/Sydney"),
                speed_tier="dc_fast",
                max_power_kw=hw_data.get("max_power_kw", DEFAULT_MAX_POWER_KW),
                stalls_total=hw_data.get("stalls", 1),
                stalls_available=hw_data.get("stalls_available"),
                plugs=hw_data.get("connector_types", ["CCS2"]),
                tethered=True,
                hardware_model=hw_data.get("hardware_models", ["JOLT 50kW DC Fast Charger"])[0],
                pricing_model=tariffs.get("pricing_model", "unknown"),
                current_rate_per_kwh=tariffs.get("per_kwh_gross"),
                rate_range=tariffs.get("rate_range", ""),
                schedule=[],
                idle_fee_per_min=None,
                currency=tariffs.get("currency", "AUD"),
                tier=1,
                source="jolt",
                source_id=str(prov.get("source_ids", [""])[0]),
                url=prov.get("url", JOLT_FIND_A_CHARGER_URL),
                last_verified_utc=prov.get("last_verified_utc", ""),
                metadata=meta,
                amenities={},
                keywords=parsed.get("keywords", []),
                views={}
            )
            records.append(rec)

        return records

    def sync(self, save_path: Optional[str] = None) -> List[UnifiedStationRecord]:
        """Fetches remote Jolt stations and saves them to the local JSON registry, archiving changes."""
        target_path = save_path or self.registry_path
        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
        from lib.tessie_api_common import resolve_tessie_archive_home
        _archive_dir = os.path.join(resolve_tessie_archive_home(), "chargers")
        os.makedirs(_archive_dir, exist_ok=True)
        archive_path = os.path.join(_archive_dir, "jolt_chargers_archived.json")
        now_utc = datetime.now(timezone.utc).isoformat()

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
            "updated_at": now_utc,
            "total_stations": len(stations_dict),
            "source": "jolt_embedded_page_data",
            "stations": stations_dict
        }

        atomic_write_json(target_path, payload)

        self._cache = records
        return records

    def reverify_station(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Re-fetches and updates a single Jolt station, archiving changes."""
        clean_id = str(station_id).replace("Jolt_", "").strip()
        all_recs = self.fetch_all_remote()
        matched = None
        for r in all_recs:
            if str(r.source_id) == clean_id or r.key == f"Jolt_{clean_id}" or r.key == station_id:
                matched = r
                break
        if matched:
            self.sync()
            return matched
        return None

    def load_cached_registry(self) -> List[UnifiedStationRecord]:
        """Loads locally cached Jolt stations from JSON registry."""
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
                    network=s_data.get("network", "Jolt"),
                    operator_name=s_data.get("operator", "Jolt"),
                    address=loc_data.get("address", ""),
                    suburb=loc_data.get("suburb", ""),
                    state=loc_data.get("state", ""),
                    postcode=loc_data.get("postcode", ""),
                    country=loc_data.get("country", "Australia"),
                    lat=float(loc_data.get("lat") or 0.0),
                    lon=float(loc_data.get("lon") or 0.0),
                    timezone=loc_data.get("timezone", "Australia/Sydney"),
                    speed_tier="dc_fast",
                    max_power_kw=float(hw_data.get("max_power_kw") or DEFAULT_MAX_POWER_KW),
                    stalls_total=int(hw_data.get("stalls") or 1),
                    stalls_available=hw_data.get("stalls_available"),
                    plugs=hw_data.get("connector_types", ["CCS2"]),
                    tethered=hw_data.get("tethered", True),
                    hardware_model=(hw_data.get("hardware_models") or ["JOLT 50kW DC Fast Charger"])[0],
                    pricing_model=tariffs.get("pricing_model", "unknown"),
                    current_rate_per_kwh=tariffs.get("per_kwh_gross"),
                    rate_range=tariffs.get("rate_range", ""),
                    schedule=tariffs.get("schedule") or [],
                    idle_fee_per_min=tariffs.get("idle_fee_per_min_gross"),
                    currency=tariffs.get("currency", "AUD"),
                    tier=1,
                    source="jolt",
                    source_id=prov.get("source_ids", [""])[0] if prov.get("source_ids") else s_id,
                    url=prov.get("url", JOLT_FIND_A_CHARGER_URL),
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
        """Finds Jolt stations within radius_km of coordinates."""
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
        """Searches Jolt stations by keyword match on name, suburb, address, or site code."""
        if not query or not query.strip():
            return []

        q_clean = query.strip().lower()
        all_stations = self.load_cached_registry()
        matched = []

        for st in all_stations:
            meta = st.metadata or {}
            station_code = str(meta.get("station_code", "")).lower()
            searchable = f"{st.name} {st.suburb} {st.address} {st.state} {st.postcode} {st.network} {station_code} {' '.join(st.keywords)}".lower()
            if q_clean in searchable:
                matched.append(st)

        return filter_station_records(matched, filters)

    def get_station_details(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Finds specific station by key, source ID, or site code."""
        all_stations = self.load_cached_registry()
        s_clean = str(station_id).strip().lower()

        for st in all_stations:
            if st.key.lower() == s_clean or st.source_id.lower() == s_clean:
                return st
            meta = st.metadata or {}
            if str(meta.get("station_code", "")).lower() == s_clean:
                return st

        return None
