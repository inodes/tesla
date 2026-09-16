#!/usr/bin/env python3
"""
NRMA Charger Provider (Tier 1, Chargefox-outranked)
====================================================
Provider for the NRMA EV Charging Network, sourced from the public KML
export of the Google My Maps embed on mynrma.com.au's own "EV Charging"
page (https://www.mynrma.com.au/electric-vehicles/charging).

Tier 1: NRMA is a real network operator with its own branded chargers, not
a crowd-sourced aggregator like PlugShare - it belongs with the other
first-party providers, not below them. HOWEVER, NRMA is also a Chargefox
network partner (see AGENTS.md's "chargefox (covers NRMA...)" convention),
so a meaningful slice of NRMA's own sites are ALSO present in
ChargefoxProvider's live GraphQL feed - as of REQ-088's investigation, 28
stations there are tagged network="NRMA", with full address, live pricing,
and confirmed hardware. Since this provider's own data has none of that
(see below), the two are explicitly ranked - NRMA-vs-Chargefox is the one
Tier-1-vs-Tier-1 pairing in this codebase where a second provider is
allowed to outrank the first at the SAME tier: chargers_manager.py's
merge_station_records() special-cases this pair so Chargefox's record
wins field-by-field whenever the two describe the same physical site
(same_net already lets "NRMA" network text match between them), while
still carrying the NRMA identity/branding through. Where the two sources
meaningfully disagree on a field (e.g. power rating), both values are kept
in metadata["source_conflicts"] and surfaced in the station detail view
rather than silently discarded - see merge_station_records() for the exact
mechanics. This design was worked out directly with the user (REQ-088,
AGENTS.local.md, 2026-09-16), superseding an earlier Tier-2 draft.

This provider's own data is worse than Chargefox's on every axis that
matters, which is exactly why the ranking above exists:
- The map itself is titled "NRMA EV Charging Network Nov 2022" - dated,
  likely stale/incomplete almost 4 years on.
- Each placemark is JUST a name and a GPS point - no address, no
  connector/power spec, no pricing. The map's own description text says
  outright: "Download the app for real-time status updates, charger
  details and pricing information" - i.e. NRMA's own live detail data is
  app-only, not published anywhere this provider can reach.
- Station "name" is the only signal for AC vs DC classification ("NRMA
  Fast Charger <place>" vs "NRMA AC Charger - <place>"), and the specific
  kW figures below are typical-for-class defaults, not per-station
  confirmed specs.

So in practice this provider mostly ADDS the ~106 locations Chargefox's
feed doesn't have (134 KML placemarks total vs 28 Chargefox-tagged NRMA
stations) as standalone Tier 1 records, and for the 28 that overlap,
contributes the confirmed "this is a real, currently-listed NRMA site"
signal while Chargefox supplies the actual detail.

Caches locally in Tessie/nrma_chargers.json with historical archiving in
Tessie/nrma_chargers_archived.json.
"""

import os
import re
import json
import urllib.request
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from .chargers_base import (
    BaseChargerProvider,
    UnifiedStationRecord,
    haversine_km,
    filter_station_records,
    detect_station_changes,
    archive_station_record,
    atomic_write_json
)
from .chargers_chargefox import resolve_au_state

_script_dir = os.path.dirname(os.path.abspath(__file__))
_tools_dir = os.path.dirname(_script_dir)
_repo_root = os.path.dirname(_tools_dir)

NRMA_KML_URL = "https://www.google.com/maps/d/kml?mid=1x7Qs_rCijBa-6hDJITFvBQNDN0lBkzk&forcekml=1"
NRMA_INFO_PAGE_URL = "https://www.mynrma.com.au/electric-vehicles/charging"

NRMA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
}

# Typical-for-class defaults, NOT per-station confirmed specs - see module
# docstring. The KML gives no power/connector data at all; classification
# is by name-text only ("Fast Charger" vs "AC Charger").
DEFAULT_FAST_KW = 50.0
DEFAULT_AC_KW = 22.0

_PLACEMARK_RE = re.compile(r"<Placemark>(.*?)</Placemark>", re.S)
_NAME_RE = re.compile(r"<name>(.*?)</name>", re.S)
_COORD_RE = re.compile(r"<coordinates>\s*([\-0-9.]+),([\-0-9.]+)(?:,[\-0-9.]+)?\s*</coordinates>", re.S)


def _resolve_nrma_registry_path() -> str:
    """Finds path to nrma_chargers.json in Tessie data directory or local fallback."""
    candidates = []
    tessie_home = None
    try:
        from lib.tessie_api_common import resolve_tessie_data_home
        tessie_home = resolve_tessie_data_home()
    except Exception:
        pass
    if tessie_home:
        candidates.append(os.path.join(tessie_home, "nrma_chargers.json"))

    candidates.append(os.path.join(_repo_root, "Tessie", "nrma_chargers.json"))
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
                    candidates.insert(0, os.path.join(td_exp, "nrma_chargers.json"))
        except Exception:
            pass

    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0] if candidates else os.path.join(_repo_root, "Tessie", "nrma_chargers.json")


def _clean_kml_text(s: str) -> str:
    """Strips CDATA wrapping/HTML entities/NBSP and collapses whitespace from
    a raw KML <name> or <description> value."""
    s = s.strip()
    if s.startswith("<![CDATA[") and s.endswith("]]>"):
        s = s[9:-3]
    s = s.replace("\xa0", " ").replace("&amp;", "&").replace("&#39;", "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_nrma_placemarks(kml_text: str) -> List[Dict[str, Any]]:
    """Extracts (name, lat, lon) from every <Placemark> in the KML export.
    Skips the document's own top-level <name> (already outside any
    <Placemark> block) and any placemark missing usable coordinates."""
    results = []
    for block in _PLACEMARK_RE.findall(kml_text):
        name_m = _NAME_RE.search(block)
        coord_m = _COORD_RE.search(block)
        if not name_m or not coord_m:
            continue
        name = _clean_kml_text(name_m.group(1))
        if not name:
            continue
        lon = float(coord_m.group(1))
        lat = float(coord_m.group(2))
        results.append({"name": name, "lat": lat, "lon": lon})
    return results


def parse_nrma_location_record(idx: int, placemark: Dict[str, Any]) -> Dict[str, Any]:
    """Transforms one raw KML placemark into the canonical multi-view JSON
    dictionary shared by every provider."""
    name = placemark["name"]
    lat = placemark["lat"]
    lon = placemark["lon"]

    name_low = name.lower()
    is_ac = "ac charger" in name_low
    speed_tier = "ac_destination" if is_ac else "dc_fast"
    max_power_kw = DEFAULT_AC_KW if is_ac else DEFAULT_FAST_KW

    state = resolve_au_state(lat, lon) if lat and lon else ""
    now_utc = datetime.now(timezone.utc).isoformat()

    return {
        "id": f"NRMA_{idx}",
        "name": name,
        "keywords": [name, "NRMA"],
        "location": {
            "address": "",
            "suburb": "",
            "state": state,
            "postcode": "",
            "country": "Australia",
            "lat": lat,
            "lon": lon,
            "timezone": "Australia/Sydney",
        },
        "hardware": {
            "max_power_kw": max_power_kw,
            "stalls": 1,
            "connector_types": ["Type 2"] if is_ac else ["CCS2"],
            "hardware_models": ["NRMA AC Charger" if is_ac else "NRMA Fast Charger"],
        },
        "tariffs": {
            "pricing_model": "unknown",
            "per_kwh_gross": None,
            "rate_range": "See NRMA app for pricing",
            "currency": "AUD",
        },
        "provenance": {
            "source_ids": [str(idx)],
            "url": NRMA_INFO_PAGE_URL,
            "last_verified_utc": now_utc,
        },
        "metadata": {
            "kml_map_title": "NRMA EV Charging Network Nov 2022",
            "hardware_confirmed": False,
        },
        "views": {},
    }


class NrmaProvider(BaseChargerProvider):
    """
    Tier 2 Aggregator for the NRMA EV Charging Network - see module
    docstring for why this is Tier 2 (not Tier 1, unlike this package's
    other providers) and how it relates to ChargefoxProvider's own,
    richer, live NRMA coverage.
    """
    name: str = "NRMA"
    tier: int = 1
    POSSIBLE_SPEED_TIERS = {"dc_fast", "ac_destination"}

    def __init__(self, registry_path: Optional[str] = None):
        self.registry_path = registry_path or _resolve_nrma_registry_path()
        self._cache: Optional[List[UnifiedStationRecord]] = None

    def fetch_all_remote(self, timeout: int = 15) -> List[UnifiedStationRecord]:
        """Fetches the NRMA KML export and parses every placemark into a record."""
        req = urllib.request.Request(NRMA_KML_URL, headers=NRMA_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            kml_text = resp.read().decode("utf-8", errors="replace")

        placemarks = parse_nrma_placemarks(kml_text)
        records: List[UnifiedStationRecord] = []
        for idx, placemark in enumerate(placemarks, 1):
            parsed = parse_nrma_location_record(idx, placemark)
            meta = parsed.get("metadata", {})
            loc_data = parsed.get("location", {})
            hw_data = parsed.get("hardware", {})
            tariffs = parsed.get("tariffs", {})
            prov = parsed.get("provenance", {})

            rec = UnifiedStationRecord(
                key=parsed["id"],
                name=parsed["name"],
                network="NRMA",
                operator_name="NRMA",
                address=loc_data.get("address", ""),
                suburb=loc_data.get("suburb", ""),
                state=loc_data.get("state", ""),
                postcode=loc_data.get("postcode", ""),
                country=loc_data.get("country", "Australia"),
                lat=loc_data.get("lat", 0.0),
                lon=loc_data.get("lon", 0.0),
                timezone=loc_data.get("timezone", "Australia/Sydney"),
                speed_tier="ac_destination" if hw_data.get("max_power_kw") == DEFAULT_AC_KW else "dc_fast",
                max_power_kw=hw_data.get("max_power_kw", DEFAULT_FAST_KW),
                stalls_total=hw_data.get("stalls", 1),
                stalls_available=None,
                plugs=hw_data.get("connector_types", ["CCS2"]),
                tethered=True,
                hardware_model=hw_data.get("hardware_models", ["NRMA Fast Charger"])[0],
                pricing_model=tariffs.get("pricing_model", "unknown"),
                current_rate_per_kwh=tariffs.get("per_kwh_gross"),
                rate_range=tariffs.get("rate_range", ""),
                schedule=[],
                idle_fee_per_min=None,
                currency=tariffs.get("currency", "AUD"),
                tier=1,
                source="nrma",
                source_id=str(prov.get("source_ids", [""])[0]),
                url=prov.get("url", NRMA_INFO_PAGE_URL),
                last_verified_utc=prov.get("last_verified_utc", ""),
                metadata=meta,
                amenities={},
                keywords=parsed.get("keywords", []),
                views={}
            )
            records.append(rec)

        return records

    def sync(self, save_path: Optional[str] = None) -> List[UnifiedStationRecord]:
        """Fetches remote NRMA stations and saves them to the local JSON registry, archiving changes."""
        target_path = save_path or self.registry_path
        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
        archive_path = os.path.join(os.path.dirname(target_path), "nrma_chargers_archived.json")
        now_utc = datetime.now(timezone.utc).isoformat()

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

            if r.key in existing_stations:
                old_st = existing_stations[r.key]
                if detect_station_changes(old_st, d):
                    archive_station_record(archive_path, r.key, old_st, now_utc)

        payload = {
            "updated_at": now_utc,
            "total_stations": len(records),
            "source": "nrma_google_mymaps_kml",
            "stations": stations_dict
        }

        atomic_write_json(target_path, payload)

        self._cache = records
        return records

    def reverify_station(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Re-fetches all NRMA stations and returns the requested one, archiving changes."""
        clean_id = str(station_id).replace("NRMA_", "").strip()
        all_recs = self.fetch_all_remote()
        matched = None
        for r in all_recs:
            if str(r.source_id) == clean_id or r.key == f"NRMA_{clean_id}" or r.key == station_id:
                matched = r
                break
        if matched:
            self.sync()
            return matched
        return None

    def load_cached_registry(self) -> List[UnifiedStationRecord]:
        """Loads locally cached NRMA stations from JSON registry."""
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

                max_pwr = float(hw_data.get("max_power_kw") or DEFAULT_FAST_KW)
                speed_tier = "ac_destination" if max_pwr == DEFAULT_AC_KW else "dc_fast"

                rec = UnifiedStationRecord(
                    key=s_id,
                    name=s_data.get("name", s_id),
                    network=s_data.get("network", "NRMA"),
                    operator_name=s_data.get("operator", "NRMA"),
                    address=loc_data.get("address", ""),
                    suburb=loc_data.get("suburb", ""),
                    state=loc_data.get("state", ""),
                    postcode=loc_data.get("postcode", ""),
                    country=loc_data.get("country", "Australia"),
                    lat=float(loc_data.get("lat") or 0.0),
                    lon=float(loc_data.get("lon") or 0.0),
                    timezone=loc_data.get("timezone", "Australia/Sydney"),
                    speed_tier=speed_tier,
                    max_power_kw=max_pwr,
                    stalls_total=int(hw_data.get("stalls") or 1),
                    stalls_available=hw_data.get("stalls_available"),
                    plugs=hw_data.get("connector_types", ["CCS2"]),
                    tethered=hw_data.get("tethered", True),
                    hardware_model=(hw_data.get("hardware_models") or ["NRMA Fast Charger"])[0],
                    pricing_model=tariffs.get("pricing_model", "unknown"),
                    current_rate_per_kwh=tariffs.get("per_kwh_gross"),
                    rate_range=tariffs.get("rate_range", ""),
                    schedule=tariffs.get("schedule") or [],
                    idle_fee_per_min=tariffs.get("idle_fee_per_min_gross"),
                    currency=tariffs.get("currency", "AUD"),
                    tier=1,
                    source="nrma",
                    source_id=prov.get("source_ids", [""])[0] if prov.get("source_ids") else s_id,
                    url=prov.get("url", NRMA_INFO_PAGE_URL),
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
        """Finds NRMA stations within radius_km of coordinates."""
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
        """Searches NRMA stations by keyword match on name."""
        if not query or not query.strip():
            return []

        q_clean = query.strip().lower()
        all_stations = self.load_cached_registry()
        matched = []

        for st in all_stations:
            searchable = f"{st.name} {st.state} {st.network} {' '.join(st.keywords)}".lower()
            if q_clean in searchable:
                matched.append(st)

        return filter_station_records(matched, filters)

    def get_station_details(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Finds specific station by key or source ID."""
        all_stations = self.load_cached_registry()
        s_clean = str(station_id).strip().lower()

        for st in all_stations:
            if st.key.lower() == s_clean or st.source_id.lower() == s_clean:
                return st

        return None
