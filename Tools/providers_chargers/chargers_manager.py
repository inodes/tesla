#!/usr/bin/env python3
"""
Unified Charger Discovery Manager & Spatial Deduplication Engine
================================================================
Orchestrates multi-network provider queries, applies spatial deduplication (R <= 60m),
and enforces the First-Party Truth Hierarchy (Tier 1 Operator > Tier 2 Aggregator).
"""

import os
import re
import sys
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
from concurrent.futures import ThreadPoolExecutor

from .chargers_base import (
    RegistryReadError,
    BaseChargerProvider,
    UnifiedStationRecord,
    haversine_km,
    filter_station_records,
    detect_station_changes,
    archive_station_record,
    save_json_with_fallback
)
from .chargers_tesla import TeslaProvider
from .chargers_chargefox import ChargefoxProvider
from .chargers_evx import EVXProvider
from .chargers_evie import EvieProvider
from .chargers_bppulse import BPPulseProvider
from .chargers_plugshare import PlugShareProvider
from .chargers_ampcharge import AmpChargeProvider
from .chargers_exploren import ExplorenProvider
from .chargers_jolt import JoltProvider
from .chargers_nrma import NRMAProvider


def _normalize_street_word(w: str) -> str:
    """Normalizes common Australian road abbreviations."""
    w = w.lower().strip()
    mapping = {
        "st": "street", "str": "street", "rd": "road", "ave": "avenue",
        "pde": "parade", "pl": "place", "cres": "crescent", "ct": "court",
        "hwy": "highway", "dr": "drive", "cl": "close", "bvd": "boulevard",
        "tce": "terrace", "sq": "square", "ln": "lane"
    }
    return mapping.get(w, w)


def normalize_plug_name(p: str) -> str:
    """Normalizes plug and socket technical identifiers to standard display names."""
    if not p:
        return ""
    p_low = p.lower().strip()
    if "iec_62196_type2" in p_low or "type 2" in p_low or "type2" in p_low or "mennekes" in p_low:
        if "combo" in p_low or "ccs" in p_low:
            return "CCS2"
        return "Type 2"
    if "ccs2" in p_low or "combo 2" in p_low:
        return "CCS2"
    if "chademo" in p_low:
        return "CHAdeMO"
    if "j1772" in p_low or "type 1" in p_low:
        return "Type 1 (J1772)"
    return p.strip()


def _tokenize(name: str) -> set:
    """Extracts alphanumeric tokens of length >= 2 with street normalization."""
    words = re.findall(r"[a-zA-Z0-9]+", name.lower())
 # Exclude very generic noise words
    stop_words = {"station", "charger", "charging", "supercharger", "australia", "nsw", "vic", "qld", "wa", "sa", "tas", "act"}
    return set(_normalize_street_word(w) for w in words if len(w) >= 2 and w not in stop_words)


def _jaccard_similarity(set1: set, set2: set) -> float:
    """Calculates Jaccard similarity index between two token sets."""
    if not set1 or not set2:
        return 0.0
    intersection = len(set1.intersection(set2))
    union = len(set1.union(set2))
    return intersection / union if union > 0 else 0.0


def _hardware_class_mismatch(s1: UnifiedStationRecord, s2: UnifiedStationRecord) -> bool:
    """
    True if the two records report fundamentally different charging classes
    (AC destination vs DC-capable/supercharger) - see in
    are_stations_co_located() for the concrete case this exists to catch.
    """
    t1, t2 = s1.speed_tier, s2.speed_tier
    if not t1 or not t2:
        return False
    return (t1 == "ac_destination") != (t2 == "ac_destination")


def are_stations_co_located(s1: UnifiedStationRecord, s2: UnifiedStationRecord, max_dist_m: float = 250.0) -> bool:
    """
    Determines if two station records represent the same physical charging site across apps.
    Criteria:
    0. Tesla's own data is source of truth for identity: two records BOTH
       sourced directly from Tesla (source == "tesla") with DIFFERENT
       Tesla-assigned identity (source_id, e.g. Balgowlah's Supercharger vs.
       its co-located Destination Charger, or Coffs Harbour's old vs. new
       site) are NEVER co-located, full stop - no distance/hardware
       calculation below is allowed to override that. If Tesla itself
       lists two distinct pages/URLs for a location, they are two sites.
       The distance + hardware-class checks that follow exist to correctly
       attach a LOWER-TIER source's own record (PlugShare, etc.) to
       whichever already-distinct Tesla site it actually belongs to - not
       to second-guess Tesla's own identity for two Tesla-sourced records.
    1. Distance <= max_dist_m (default 250 meters for carparks / shopping centres).
    2. Same network + token similarity >= 0.20 or identical suburb/postcode.
    3. Co-located if distance <= 35m, UNLESS the two are on different
       networks AND report fundamentally different charging classes (AC
       vs DC) - see , 2026-09-16.
    """
    if s1.source == "tesla" and s2.source == "tesla":
        id1 = s1.source_id or s1.key
        id2 = s2.source_id or s2.key
        if id1 and id2 and id1 != id2:
            return False

    dist_km = haversine_km(s1.lat, s1.lon, s2.lat, s2.lon)
    dist_m = dist_km * 1000.0
    if dist_m > max_dist_m:
        return False

 # Check network match
    net1 = (s1.network or "").lower()
    net2 = (s2.network or "").lower()
    same_net = (net1 in net2 or net2 in net1 or 
                ("tesla" in net1 and "tesla" in net2) or 
                ("chargefox" in net1 and "chargefox" in net2) or
                ("evx" in net1 and "evx" in net2) or
                ("evx" in net1 and ("polecharger" in s2.name.lower() or "evx" in s2.name.lower())) or
                ("evx" in net2 and ("polecharger" in s1.name.lower() or "evx" in s1.name.lower())) or
                ("bp" in net1 and "bp" in net2) or
                ("evie" in net1 and "evie" in net2) or
                (("ampcharge" in net1 or "ampol" in net1) and ("ampcharge" in net2 or "ampol" in net2)) or
                ("exploren" in net1 and "exploren" in net2) or
                ("jolt" in net1 and "jolt" in net2) or
                ("nrma" in net1 and "nrma" in net2))

    if _hardware_class_mismatch(s1, s2):
        return False

    if dist_m <= 35.0:
        return True

 # Two DIFFERENT Tier-1 (first-party authoritative) providers are never
 # the same physical charger just because their names/addresses look
 # similar - e.g. a genuine Chargefox install and a genuine AmpCharge
 # install can both sit in the same shopping centre car park, ~100m
 # apart, and both match "<Centre Name> Shopping Centre" on name tokens
 # without being the same charger. Only the unconditional <=35m branch
 # above, or an actual same-network match, may co-locate two Tier-1
 # sources - the two fuzzy paths below are blocked for this case. A
 # Tier-2 aggregator (PlugShare) fuzzy-matching a Tier-1 record by name
 # is unaffected - that is its actual job.
    cross_tier1_mismatch = (s1.tier == 1 and s2.tier == 1 and not same_net)

 # Token similarity on names and addresses
    tok1 = _tokenize(f"{s1.name} {s1.address}")
    tok2 = _tokenize(f"{s2.name} {s2.address}")
    sim = _jaccard_similarity(tok1, tok2)

    if not cross_tier1_mismatch and dist_m <= 60.0 and sim >= 0.25:
        return True

 # Token similarity (name only)
    tok1 = _tokenize(s1.name)
    tok2 = _tokenize(s2.name)
    sim = _jaccard_similarity(tok1, tok2)

    sub1 = (s1.suburb or "").lower().strip()
    sub2 = (s2.suburb or "").lower().strip()
    same_suburb = bool(sub1 and sub2 and (sub1 == sub2 or sub1 in sub2 or sub2 in sub1))

    if same_net:
        if sim >= 0.20 or same_suburb or dist_m <= 100.0:
            return True

    if not cross_tier1_mismatch and sim >= 0.40 and (same_suburb or dist_m <= 80.0):
        return True

    return False


def merge_station_records(primary: UnifiedStationRecord, secondary: UnifiedStationRecord) -> UnifiedStationRecord:
    """
    Merges two co-located station records with First-Party (Tier 1) Truth Hierarchy.
    Primary is assumed to be higher or equal tier (Tier 1 > Tier 2).
    """
    if primary.tier > secondary.tier:
 # Swap so primary is the higher authority (lower tier number)
        primary, secondary = secondary, primary

 # REQ-088 (AGENTS.local.md, 2026-09-16): NRMA and Chargefox are the one
 # Tier-1-vs-Tier-1 pairing in this codebase that CAN legitimately
 # co-locate (are_stations_co_located()'s same_net check matches both
 # sides' network="NRMA" text) - NRMA is a Chargefox network partner, so
 # the same physical site can show up in both feeds. Equal tier alone
 # doesn't pick a winner (the swap above only fires on tier MISMATCH), so
 # without this, whichever provider happened to sort first won by
 # accident. The user's explicit call: Chargefox's data is richer here
 # (address, live pricing, confirmed hardware vs NRMA's name+GPS-point-
 # only KML), so Chargefox is primary whenever both are present -
 # regardless of sort order - while NRMA's confirmation that the site is
 # still live carries through via merged_source/co_located_networks.
    def _src(r):
        return (r.source or "").lower()
    if primary.tier == 1 and secondary.tier == 1:
        if "nrma" in _src(primary) and "chargefox" in _src(secondary):
            primary, secondary = secondary, primary

 # Surface (rather than silently drop) meaningfully conflicting values
 # between two same-tier sources describing the same site - e.g. NRMA's
 # KML default-guess power rating vs Chargefox's confirmed hardware spec.
 # Recorded on the merged record's metadata so the station detail view
 # can show both sides with attribution instead of just picking one.
    source_conflicts: Dict[str, Dict[str, Any]] = {}
    if primary.tier == 1 and secondary.tier == 1 and _src(primary) != _src(secondary):
        if (
            primary.max_power_kw is not None
            and secondary.max_power_kw is not None
            and abs(primary.max_power_kw - secondary.max_power_kw) >= 1.0
        ):
            source_conflicts["max_power_kw"] = {
                primary.source: primary.max_power_kw,
                secondary.source: secondary.max_power_kw,
            }
        if (
            primary.address and secondary.address
            and primary.address.strip().lower() != secondary.address.strip().lower()
        ):
            source_conflicts["address"] = {
                primary.source: primary.address,
                secondary.source: secondary.address,
            }

 # Deduplicate unique sources cleanly
    raw_sources = [s.strip() for s in f"{primary.source}+{secondary.source}".split("+") if s.strip()]
    unique_sources = list(dict.fromkeys(raw_sources))
    merged_source = "+".join(unique_sources)

 # Primary authoritative source_id (must be clean and valid for filenames, no pipes)
    primary_id = primary.source_id or primary.key
    secondary_id = secondary.source_id or secondary.key

    merged_metadata = dict(secondary.metadata or {})
    for k, v in (primary.metadata or {}).items():
        if v is not None and v != "":
            merged_metadata[k] = v
        elif k not in merged_metadata:
            merged_metadata[k] = v

 # Preserve rich descriptions & access notes across views
    p_desc = (primary.metadata or {}).get("description") or (primary.metadata or {}).get("access_description")
    s_desc = (secondary.metadata or {}).get("description") or (secondary.metadata or {}).get("access_description")
    if not p_desc and s_desc:
        merged_metadata["description"] = s_desc
        merged_metadata["access_description"] = s_desc

 # Preserve operating hours if missing in primary
    p_hours = (primary.metadata or {}).get("access_hours") or (primary.metadata or {}).get("hours")
    s_hours = (secondary.metadata or {}).get("access_hours") or (secondary.metadata or {}).get("hours")
    if not p_hours and s_hours:
        merged_metadata["access_hours"] = s_hours
        merged_metadata["hours"] = s_hours

 # Track co-located networks
    raw_nets = [primary.network, secondary.network, primary.operator_name, secondary.operator_name]
    all_nets = []
    for n in raw_nets:
        if not n or n in ("3rd-Party", "unknown", ""):
            continue
        if n == "Tesla" and any("Tesla" in x for x in all_nets):
            continue
        if n not in all_nets:
            all_nets.append(n)
    if all_nets:
        merged_metadata["co_located_networks"] = all_nets

 # Preserve connector breakdown across views if available
    p_cb = (primary.metadata or {}).get("connector_breakdown")
    s_cb = (secondary.metadata or {}).get("connector_breakdown")
    if p_cb:
        merged_metadata["connector_breakdown"] = p_cb
    elif s_cb:
        merged_metadata["connector_breakdown"] = s_cb

    merged_metadata["secondary_source_ids"] = list(dict.fromkeys(
        merged_metadata.get("secondary_source_ids", []) + [secondary_id]
    ))

    if source_conflicts:
        merged_metadata["source_conflicts"] = source_conflicts

    merged_amenities: Any
    if isinstance(primary.amenities, dict) and isinstance(secondary.amenities, dict):
        merged_amenities = dict(secondary.amenities)
        merged_amenities.update(primary.amenities)
    elif isinstance(primary.amenities, list) and isinstance(secondary.amenities, list):
        merged_amenities = sorted(list(set(primary.amenities + secondary.amenities)))
    elif isinstance(primary.amenities, dict):
        merged_amenities = primary.amenities
    else:
        merged_amenities = secondary.amenities

    merged_keywords: Any
    if isinstance(primary.keywords, list) and isinstance(secondary.keywords, list):
        merged_keywords = sorted(list(set(primary.keywords + secondary.keywords)))
    elif isinstance(primary.keywords, list):
        merged_keywords = primary.keywords
    else:
        merged_keywords = secondary.keywords or []

    merged_views = dict(secondary.views or {})
    merged_views.update(primary.views or {})

    merged_max_power = max(primary.max_power_kw or 0.0, secondary.max_power_kw or 0.0)
    merged_stalls = max(primary.stalls_total or 0, secondary.stalls_total or 0) or primary.stalls_total or secondary.stalls_total

 # Resolve hardware model cleanly matching merged power
    merged_hw_model = primary.hardware_model or secondary.hardware_model
    if "bp" in (primary.network or "").lower() or "pulse" in (primary.network or "").lower():
        pwr_label = f"{int(merged_max_power)}kW" if merged_max_power.is_integer() else f"{merged_max_power:.0f}kW"
        desc_type = "Ultra-Fast" if merged_max_power >= 150 else "DC Fast"
        merged_hw_model = f"bp pulse {pwr_label} {desc_type} Charger"
    elif "evx" in (primary.network or "").lower() or primary.source == "evx":
        merged_hw_model = "EVX 22kW Dual AC Polecharger"
    elif "exploren" in (primary.network or "").lower() or primary.source == "exploren":
        pwr_label = f"{int(merged_max_power)}kW" if merged_max_power.is_integer() else f"{merged_max_power:.0f}kW"
        merged_hw_model = f"Exploren {pwr_label} Charger"
    elif "jolt" in (primary.network or "").lower() or primary.source == "jolt":
        merged_hw_model = "JOLT 50kW DC Fast Charger"
    elif primary.speed_tier == "supercharger":
        if merged_max_power >= 300:
            merged_hw_model = "Tesla Supercharger V4"
        elif merged_max_power >= 250:
            merged_hw_model = "Tesla Supercharger V3"
        elif merged_max_power >= 120:
            merged_hw_model = "Tesla Supercharger V2"

    merged = UnifiedStationRecord(
        key=primary.key,
        name=primary.name,
        network=primary.network,
        operator_name=primary.operator_name or secondary.operator_name,
        address=primary.address or secondary.address,
        suburb=primary.suburb or secondary.suburb,
        state=primary.state or secondary.state,
        postcode=primary.postcode or secondary.postcode,
        country=primary.country or secondary.country,
        lat=primary.lat,
        lon=primary.lon,
        timezone=primary.timezone or secondary.timezone,
        speed_tier=primary.speed_tier,
        max_power_kw=merged_max_power,
        stalls_total=merged_stalls,
        stalls_available=primary.stalls_available if primary.stalls_available is not None else secondary.stalls_available,
        plugs=sorted(list(set(normalize_plug_name(p) for p in (primary.plugs + secondary.plugs) if p))),
        tethered=primary.tethered,
        hardware_model=merged_hw_model,
        pricing_model=primary.pricing_model if primary.pricing_model != "unknown" else secondary.pricing_model,
        current_rate_per_kwh=primary.current_rate_per_kwh if primary.current_rate_per_kwh is not None else secondary.current_rate_per_kwh,
        rate_range=primary.rate_range or secondary.rate_range,
        schedule=primary.schedule or secondary.schedule,
 # non_tesla_schedule / effective_date_utc were both missing from this
 # merge until 2026-09-14 (see AGENTS.local.md) - same class of issue as
 # the congestion_fee_per_min omission just below: a Tesla-sourced
 # non-Tesla rate schedule or effective date silently vanished on any
 # multi-source merge.
        non_tesla_schedule=primary.non_tesla_schedule or secondary.non_tesla_schedule,
        idle_fee_per_min=primary.idle_fee_per_min if primary.idle_fee_per_min is not None else secondary.idle_fee_per_min,
 # congestion_fee_per_min was missing from this merge entirely until
 # 2026-09-14 - it silently fell back to UnifiedStationRecord's own
 # None default on every multi-source merge, regardless of what either
 # source actually had (e.g. Tesla's own $0.50/min congestion fee for
 # Macquarie, NSW vanished the moment it merged with a PlugShare match).
        congestion_fee_per_min=primary.congestion_fee_per_min if primary.congestion_fee_per_min is not None else secondary.congestion_fee_per_min,
        parking_fee=primary.parking_fee or secondary.parking_fee,
        currency=primary.currency or secondary.currency,
        tier=primary.tier,
        source=merged_source,
        source_id=primary_id,
        url=primary.url or secondary.url,
        last_verified_utc=primary.last_verified_utc or secondary.last_verified_utc,
        effective_date_utc=primary.effective_date_utc or secondary.effective_date_utc,
        distance_km=primary.distance_km if primary.distance_km is not None else secondary.distance_km,
        metadata=merged_metadata,
        amenities=merged_amenities,
        keywords=merged_keywords,
        views=merged_views
    )
    return merged


def _diff_station_fields(old: UnifiedStationRecord, new: UnifiedStationRecord) -> Dict[str, Tuple[Any, Any]]:
    """
    (2026-09-16, AGENTS.local.md): reverify_station() previously threw
    away the freshly-fetched record and returned a bare bool, so a live
    "Update / Re-verify" always printed the same canned "Successfully updated
    and saved (historical version archived if changed)" message whether or
    not anything actually changed - user report: re-verified a station, saw
    that message, but "nothing shows as updated" and asked for either an
    honest "No change detected" or an actual visible diff. Compares the
    display-relevant fields between the pre-reverify snapshot and the
    freshly-fetched record and returns only the fields that differ, each as
    (old_value, new_value) - an empty dict means a genuine no-op reverify.
    """
    diffs: Dict[str, Tuple[Any, Any]] = {}
    field_labels = [
        ("name", "Name"),
        ("network", "Network"),
        ("address", "Address"),
        ("suburb", "Suburb"),
        ("state", "State"),
        ("max_power_kw", "Max Power (kW)"),
        ("stalls_total", "Stalls"),
        ("current_rate_per_kwh", "Rate ($/kWh)"),
    ]
    for attr, label in field_labels:
        ov = getattr(old, attr, None)
        nv = getattr(new, attr, None)
        if ov != nv:
            diffs[label] = (ov, nv)
    old_plugs = sorted(old.plugs or [])
    new_plugs = sorted(new.plugs or [])
    if old_plugs != new_plugs:
        diffs["Connectors"] = (", ".join(old_plugs) or "-", ", ".join(new_plugs) or "-")
    return diffs


class ChargerDiscoveryManager:
    """Unified multi-network charging discovery manager."""

    def __init__(self, providers: Optional[List[BaseChargerProvider]] = None):
        if providers is not None:
            self.providers = providers
        else:
            self.providers = [
                TeslaProvider(),
                ChargefoxProvider(),
                EVXProvider(),
                EvieProvider(),
                BPPulseProvider(),
                PlugShareProvider(),
                AmpChargeProvider(),
                ExplorenProvider(),
                JoltProvider(),
                NRMAProvider()
            ]

    def deduplicate_records(self, records: List[UnifiedStationRecord]) -> List[UnifiedStationRecord]:
        """
        Groups co-located records and performs Tier 1 > Tier 2 merging.
        """
        if not records:
            return []

 # Sort records by authority tier (Tier 1 first)
        sorted_records = sorted(records, key=lambda r: (r.tier, -(r.max_power_kw or 0)))
        merged_results: List[UnifiedStationRecord] = []

        for record in sorted_records:
            matched_idx = -1
            for i, existing in enumerate(merged_results):
                if are_stations_co_located(existing, record):
                    matched_idx = i
                    break

            if matched_idx >= 0:
 # Merge into existing higher-tier record
                merged_results[matched_idx] = merge_station_records(merged_results[matched_idx], record)
            else:
                merged_results.append(record)

        return merged_results

    def search_nearby(
        self,
        lat: float,
        lon: float,
        radius_km: float = 15.0,
        filters: Optional[Dict[str, Any]] = None,
        sort_by: str = "distance",
        eval_time: Optional[str] = None
    ) -> List[UnifiedStationRecord]:
        """
        Queries all providers in parallel, deduplicates co-located stations, and applies sorting.
        """
        filters = filters or {}
        all_found: List[UnifiedStationRecord] = []

        def _fetch(p: BaseChargerProvider):
            try:
                return p.search_nearby(lat, lon, radius_km=radius_km, filters=filters)
            except Exception:
                return []

        with ThreadPoolExecutor(max_workers=len(self.providers)) as executor:
            results_lists = list(executor.map(_fetch, self.providers))

        for res in results_lists:
            all_found.extend(res)

 # Deduplicate
        deduped = self.deduplicate_records(all_found)
 # Apply standard filters
        filtered = filter_station_records(deduped, filters)
 # Sort results
        sorted_res = self.sort_records(filtered, sort_by=sort_by, eval_time=eval_time)
        return sorted_res

    def search_by_text(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        sort_by: str = "name",
        eval_time: Optional[str] = None,
        ref_lat: Optional[float] = None,
        ref_lon: Optional[float] = None
    ) -> List[UnifiedStationRecord]:
        """
        Queries all providers for text match, deduplicates, and sorts.
        Computes distance_km if ref_lat and ref_lon are supplied.
        """
        filters = filters or {}
        all_found: List[UnifiedStationRecord] = []

        def _fetch(p: BaseChargerProvider):
            try:
                return p.search_by_text(query, filters=filters)
            except Exception:
                return []

        with ThreadPoolExecutor(max_workers=len(self.providers)) as executor:
            results_lists = list(executor.map(_fetch, self.providers))

        for res in results_lists:
            all_found.extend(res)

 # Check facilities registry for matching POIs (e.g. searching "Woolworths", "Coles", "Aldi", "Kmart")
        try:
            from lib.station_facilities_common import get_facilities_registry, resolve_station_physical_coords
            _f_reg = get_facilities_registry()
            if _f_reg and query:
                q_low = query.lower().strip()
                for st in self.get_all_stations(filters=filters):
                    phys_lat, phys_lon, _ = resolve_station_physical_coords(st)
                    fac = _f_reg.get_station_facilities(station_key=st.key, station_name=st.name, lat=phys_lat, lon=phys_lon)
                    if fac:
                        pois = fac.get("pois", [])
                        if any(q_low in (p.get("name", "") + " " + p.get("category", "")).lower() for p in pois):
                            all_found.append(st)
        except Exception:
            pass

        deduped = self.deduplicate_records(all_found)
        filtered = filter_station_records(deduped, filters)
        if ref_lat is not None and ref_lon is not None:
            for r in filtered:
                if r.lat is not None and r.lon is not None:
                    r.distance_km = round(haversine_km(ref_lat, ref_lon, r.lat, r.lon), 1)
        sorted_res = self.sort_records(filtered, sort_by=sort_by, eval_time=eval_time)
        return sorted_res

    def get_all_stations(
        self,
        filters: Optional[Dict[str, Any]] = None,
        sort_by: str = "name",
        eval_time: Optional[str] = None,
        ref_lat: Optional[float] = None,
        ref_lon: Optional[float] = None
    ) -> List[UnifiedStationRecord]:
        """
        Retrieves all stations from all registered providers, filters by speed/state/network,
        deduplicates, and sorts. Computes distance_km if ref_lat and ref_lon are supplied.
        """
        filters = filters or {}
        requested_speed_tier = filters.get("speed_tier")

        all_found: List[UnifiedStationRecord] = []
        for p in self.providers:
            if not hasattr(p, "load_cached_registry"):
                continue

 # get_all_stations() used to unconditionally load every
 # provider's full registry before any filter was applied - so a
 # `--sc`-only query (Tesla Superchargers, provably Tesla-exclusive
 # hardware) still paid the cost of converting every OTHER
 # network's registry too, including ChargefoxProvider, whose
 # load_cached_registry() can trigger a live, rate-limited (~1
 # req/sec) Nominatim reverse-geocode HTTP call per uncached
 # station and can block for a long time on a cold/partial geo
 # cache. Skip providers that declare (via POSSIBLE_SPEED_TIERS -
 # see chargers_base.py) that they can never produce a record
 # matching the active speed_tier filter - a provider with
 # POSSIBLE_SPEED_TIERS=None (unknown/unrestricted) is never
 # skipped, so this is opt-in per provider and safe by default.
            possible_tiers = getattr(p, "POSSIBLE_SPEED_TIERS", None)
            if requested_speed_tier and possible_tiers is not None:
 # (AGENTS.local.md, 2026-09-16): filter_station_records()'s
 # "dc_fast" branch deliberately also accepts speed_tier=="supercharger"
 # records (`st.speed_tier not in ("dc_fast", "supercharger")`) because
 # a real Tesla Supercharger with non-Tesla (CCS2) access IS a DC fast
 # charging option. This provider-level skip must match that same
 # acceptance rule, or a provider whose POSSIBLE_SPEED_TIERS is only
 # {"supercharger", ...} (i.e. TeslaProvider) gets silently skipped
 # for every `--dc` query before filter_station_records ever runs -
 # which is exactly why real, closer Superchargers were vanishing
 # from `--near <place> --dc` results while `--sc` still found them
 # fine. Skip only when the provider truly cannot produce anything
 # that would pass either acceptance path.
                if requested_speed_tier == "dc_fast":
                    if "dc_fast" not in possible_tiers and "supercharger" not in possible_tiers:
                        continue
                elif requested_speed_tier not in possible_tiers:
                    continue

            try:
                recs = p.load_cached_registry()
                all_found.extend(recs)
            except Exception:
                pass

        deduped = self.deduplicate_records(all_found)
        filtered = filter_station_records(deduped, filters)
        if ref_lat is not None and ref_lon is not None:
            for r in filtered:
                if r.lat is not None and r.lon is not None:
                    r.distance_km = round(haversine_km(ref_lat, ref_lon, r.lat, r.lon), 1)
        sorted_res = self.sort_records(filtered, sort_by=sort_by, eval_time=eval_time)
        return sorted_res

    def search_route_corridor(
        self,
        waypoints: List[Tuple[float, float]],
        corridor_radius_km: float = 5.0,
        filters: Optional[Dict[str, Any]] = None,
        sort_by: str = "progress",
        eval_time: Optional[str] = None
    ) -> Tuple[List[UnifiedStationRecord], Dict[str, Any]]:
        """
        Discovers chargers along a driving corridor across waypoints within corridor_radius_km.
        Projects each station onto the route to compute highway progress and detour offsets.
        Returns (sorted_stations, route_info).
        """
        try:
            from lib.route_common import fetch_driving_route, calculate_corridor_stations
        except ImportError:
            from route_common import fetch_driving_route, calculate_corridor_stations

        # 1. Fetch driving route geometry
        route_info = fetch_driving_route(waypoints, fallback=True)
        polyline = route_info.get("polyline", [])

        # 2. Get all candidate stations matching filters
        candidates = self.get_all_stations(filters=filters)

        # 3. Project stations onto highway corridor
        projected = calculate_corridor_stations(candidates, polyline, corridor_radius_km=corridor_radius_km)
        corridor_records = [st for st, prog, detour in projected]

        # 4. Sort records
        sorted_records = self.sort_records(corridor_records, sort_by=sort_by, eval_time=eval_time)
        return sorted_records, route_info

    def sort_records(
        self,
        records: List[UnifiedStationRecord],
        sort_by: str = "distance",
        eval_time: Optional[str] = None
    ) -> List[UnifiedStationRecord]:
        """
        Sorts unified records by distance, route progress, detour, price, power, stalls, or name.
        """
        if sort_by in ("progress", "route"):
            return sorted(records, key=lambda r: (getattr(r, "route_progress_km", r.distance_km if r.distance_km is not None else 999999.0), getattr(r, "detour_km", 999999.0), (r.name or "").lower()))

        elif sort_by == "detour":
            return sorted(records, key=lambda r: (getattr(r, "detour_km", 999999.0), getattr(r, "route_progress_km", r.distance_km if r.distance_km is not None else 999999.0), (r.name or "").lower()))

        elif sort_by == "price":
            def price_key(r: UnifiedStationRecord):
                p = r.evaluate_rate(eval_time)
                dist = getattr(r, "route_progress_km", r.distance_km if r.distance_km is not None else 999999.0)
                name = (r.name or "").lower()
                if p is None:
                    return (3, 999.0, dist, name)
                elif p == 0.0:
                    return (0, 0.0, dist, name)
                else:
                    return (1, p, dist, name)
            return sorted(records, key=price_key)

        elif sort_by == "power":
            return sorted(records, key=lambda r: (- (r.max_power_kw or 0.0), getattr(r, "route_progress_km", r.distance_km if r.distance_km is not None else 999999.0), (r.name or "").lower()))

        elif sort_by == "stalls":
            return sorted(records, key=lambda r: (- (r.stalls_total or 0), getattr(r, "route_progress_km", r.distance_km if r.distance_km is not None else 999999.0), (r.name or "").lower()))

        elif sort_by in ["score", "stop_score", "quality"]:
            try:
                from lib.route_common import compute_route_stop_score
            except ImportError:
                from route_common import compute_route_stop_score

            def score_key(r: UnifiedStationRecord):
                sc_data = getattr(r, "stop_score_data", None)
                if not sc_data:
                    sc_data = compute_route_stop_score(r, getattr(r, "detour_km", None))
                    r.stop_score_data = sc_data
                    r.stop_score = sc_data["score"]
                sc = sc_data["score"]
                prog = getattr(r, "route_progress_km", r.distance_km if r.distance_km is not None else 999999.0)
                name = (r.name or "").lower()
                return (-sc, prog, name)

            return sorted(records, key=score_key)

        elif sort_by in ["facilities", "facility", "rating"]:
            try:
                from station_facilities_common import get_facilities_registry
                _f_reg = get_facilities_registry()
            except ImportError:
                try:
                    from lib.station_facilities_common import get_facilities_registry
                    _f_reg = get_facilities_registry()
                except ImportError:
                    _f_reg = None

            def fac_key(r: UnifiedStationRecord):
                dist = getattr(r, "route_progress_km", r.distance_km if r.distance_km is not None else 999999.0)
                name = (r.name or "").lower()
                if _f_reg:
                    try:
                        from lib.station_facilities_common import resolve_station_physical_coords
                        phys_lat, phys_lon, _ = resolve_station_physical_coords(r)
                    except Exception:
                        phys_lat, phys_lon = r.lat, r.lon
                    f = _f_reg.get_station_facilities(station_key=r.key, station_name=r.name, lat=phys_lat, lon=phys_lon)
                    if f:
                        score = f.get("score", 0.0)
                        return (-score, dist, name)
                return (1.0, dist, name)

            return sorted(records, key=fac_key)

        elif sort_by in ["name", "state"]:
            return sorted(records, key=lambda r: (r.state or "ZZ", (r.name or "").lower()))

        else:  # default "distance"
            has_dist = any(r.distance_km is not None for r in records)
            if has_dist:
                return sorted(records, key=lambda r: (r.distance_km if r.distance_km is not None else 999999.0, (r.name or "").lower()))
            else:
                tier_order = {"supercharger": 0, "dc_fast": 1, "ac_destination": 2}
                return sorted(records, key=lambda r: (tier_order.get(r.speed_tier, 3), r.state or "ZZ", (r.name or "").lower()))

    def export_consolidated_registry(self, target_path: Optional[str] = None) -> Tuple[str, int]:
        """
        Compiles all stations from all registered providers, performs spatial deduplication and
        First-Party Truth Hierarchy merging, and exports the full multi-view master document
        to local storage (charging_stations.json), archiving superseded records to charging_stations_archived.json.
        """
        if not target_path:
            tessie_home = None
            try:
                from lib.tessie_api_common import resolve_tessie_data_home
                tessie_home = resolve_tessie_data_home()
            except Exception:
                pass
            if not tessie_home:
                tools_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                repo_root = os.path.dirname(tools_dir)
                tessie_home = os.path.join(repo_root, "resources")
            target_path = os.path.join(tessie_home, "charging_stations.json")

        archive_path = os.path.join(os.path.dirname(target_path), "charging_stations_archived.json")
        now_utc = datetime.now(timezone.utc).isoformat()

 # Load existing registry to detect tariff or hardware changes
        existing_stations = {}
        if os.path.isfile(target_path):
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    existing_stations = old_data.get("stations", {}) if isinstance(old_data, dict) else {}
            except Exception as e:
                raise RegistryReadError(f"{target_path} is unreadable ({e}) - not overwriting it") from e

        all_records: List[UnifiedStationRecord] = []
        for p in self.providers:
            # A provider whose registry failed to load must abort the export -
            # it used to be skipped silently, so charging_stations.json was
            # rewritten WITHOUT that network's stations (2026-09-30 review).
            cached = p.load_cached_registry()
            if getattr(p, "last_load_error", None):
                raise RegistryReadError(f"{type(p).__name__}: {p.last_load_error}")
            all_records.extend(cached)

        deduped = self.deduplicate_records(all_records)
        sorted_records = self.sort_records(deduped, sort_by="name")

        stations_dict: Dict[str, Any] = {}
        for r in sorted_records:
            d = r.to_dict()
            stations_dict[r.key] = d

 # Archive superseded version if pricing/hardware/name changed
            if r.key in existing_stations:
                old_st = existing_stations[r.key]
                if detect_station_changes(old_st, d):
                    archive_station_record(archive_path, r.key, old_st, now_utc)

        payload = {
            "version": "1.0",
            "updated_at": now_utc,
            "station_count": len(stations_dict),
            "sources": [p.name for p in self.providers],
            "stations": stations_dict
        }

        target_candidates = [target_path]
        tools_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        repo_root = os.path.dirname(tools_dir)
        local_fallback = os.path.join(repo_root, "resources", "charging_stations.json")
        if local_fallback not in target_candidates:
            target_candidates.append(local_fallback)

 # AGENTS.md save_json_with_fallback() (base.py) now
 # carries this exact "try each candidate, raise if all fail"
 # behavior AND writes atomically (temp file + os.replace) - this
 # used to be a bare open(path, "w") per candidate, so a killed
 # process mid-write on this potentially large consolidated export
 # could corrupt charging_stations.json.
        final_path = save_json_with_fallback(target_candidates, payload)

        return final_path, len(stations_dict)

    def _refresh_consolidated_export(self) -> bool:
        """Re-export charging_stations.json after a single-station change.
        The station change itself is already saved by the time this runs,
        so an export failure is reported but never turns that success into
        a failure (2026-09-30 review: remove_station() used to report "could
        not remove" for a station it had already removed)."""
        try:
            self.export_consolidated_registry()
            return True
        except Exception as e:
            print(f"⚠️  Station saved, but charging_stations.json was not refreshed: {e}")
            return False

    def reverify_station(self, station: UnifiedStationRecord) -> Tuple[bool, Optional[Dict[str, Tuple[Any, Any]]], Optional[UnifiedStationRecord], bool]:
        """
        Re-verifies a single station live against its primary provider,
        updating local cache and archiving historical state if changes occur.
        Returns (ok, diffs, fresh, confirmed_not_found): ok is whether the
        live re-fetch itself succeeded; diffs is None when ok is False or a
        diff couldn't be computed, {} when the re-fetch succeeded but nothing
        display-relevant changed, or a {field: (old, new)} map when it did -
        see _diff_station_fields(). `fresh` is the newly-fetched
        UnifiedStationRecord (None if unavailable) so the caller can
        reload/redisplay it in place.

        `confirmed_not_found` (2026-09-29, user design requirement: "If the
        single source of truth has no entry - then the truth is the entry
        should be noted to user as false and removed with user prompt.") is
        True ONLY when the live source was actually reached and definitively
        said "this doesn't exist" (an unambiguous HTTP 404 from Tesla's own
        findus page or PlugShare's own /locations/{id} API) - never for a
        generic failure (timeout, network error, rate limit, parse error,
        WebKit crash), which is exactly the ambiguous case that must NOT be
        treated as "confirmed gone". Currently wired up for Tesla (via
        TeslaChargerExplorer.last_http_status, set from the main navigation's
        own response) and PlugShare (via PlugShareClient.last_error, already
        distinguishing "HTTP 404 (not found)" from every other failure mode)
        - the two providers this session's two real bug reports (BUG-156's
        fabricated "St Leonards, NSW" 404, BUG-157's Mortlake Slipway) both
        involved. Other providers (Chargefox/Evie/BPPulse/EVX/Exploren/Jolt/
        AmpCharge/NRMA) always report False here for now (unchanged, safe
        default - never silently prompts to delete for those until each is
        deliberately wired up the same way).
        """
        src = (station.source or "").lower()
        net = (station.network or "").lower()
        # Route on the record's PRIMARY source only (2026-09-30 review, C1/C8).
        # A merged record like "exploren+plugshare" carries the primary
        # network's own id in source_id; matching with `"plugshare" in src`
        # sent it to PlugShare with that foreign id (live: Exploren 804 ->
        # a Fort Wayne, Indiana home charger; Jolt 169 -> 404 -> "confirmed
        # gone" + removal prompt), and PlugShare-only records whose network
        # merely says "Tesla" went to the Tesla scraper.
        primary_src = src.split("+")[0].strip()

        try:
            if primary_src == "tesla":
                from .chargers_tesla import TeslaChargerExplorer
                explorer = TeslaChargerExplorer()
                c_type = "supercharger" if station.speed_tier == "supercharger" else "destination_charger"
 # this used to call a scrape_station_detail() method
 # (singular) that has never existed on TeslaChargerExplorer -
 # every live Tesla re-verify silently raised AttributeError,
 # caught below, and always reported "Failed to update". The
 # real method is scrape_station_details() (plural), returns
 # (key, record) and does not persist on its own - it must be
 # explicitly saved via update_registry(), matching the exact
 # pattern charger_find_tesla.py's own interactive re-scrape
 # flow already uses.
                tesla_key, tesla_record = explorer.scrape_station_details(station.url, charger_type=c_type, list_title=station.name)
                if not tesla_record:
 # Tesla's own findus page returned an unambiguous HTTP 404 for
 # this exact URL - not a timeout/network/parse failure - so
 # the source of truth has definitively said this station is
 # gone, not just "couldn't check right now".
                    confirmed_not_found = getattr(explorer, "last_http_status", None) == 404
                    return False, None, None, confirmed_not_found
                if explorer.update_registry(tesla_key, tesla_record) == "ERROR":
                    return False, None, None, False  # nothing saved - never report success
                self._refresh_consolidated_export()
                fresh = None
                for p in self.providers:
                    if isinstance(p, TeslaProvider):
                        p._cache = None  # re-read the file we just wrote
                        fresh = p.get_station_details(tesla_key)
                        break
                diffs = _diff_station_fields(station, fresh) if fresh else None
                return True, diffs, fresh, False

            for p in self.providers:
                if isinstance(p, ChargefoxProvider) and primary_src == "chargefox":
                    cf_id = station.source_id or station.key.replace("Chargefox_", "")
                    fresh = p.reverify_station(cf_id)
                    self._refresh_consolidated_export()
                    return fresh is not None, (_diff_station_fields(station, fresh) if fresh else None), fresh, False  # C7: no refresh = not verified
                elif isinstance(p, EvieProvider) and primary_src == "evie":
                    evie_id = station.source_id or station.key.replace("Evie_", "")
                    fresh = p.reverify_station(evie_id)
                    self._refresh_consolidated_export()
                    return fresh is not None, (_diff_station_fields(station, fresh) if fresh else None), fresh, False  # C7: no refresh = not verified
                elif isinstance(p, BPPulseProvider) and primary_src == "bppulse":
                    bp_id = station.source_id or station.key.replace("BPPulse_", "")
                    fresh = p.reverify_station(bp_id)
                    self._refresh_consolidated_export()
                    return fresh is not None, (_diff_station_fields(station, fresh) if fresh else None), fresh, False  # C7: no refresh = not verified
                elif isinstance(p, EVXProvider) and primary_src == "evx":
                    evx_id = station.source_id or station.key.replace("EVX_", "")
                    fresh = p.reverify_station(evx_id)
                    self._refresh_consolidated_export()
                    return fresh is not None, (_diff_station_fields(station, fresh) if fresh else None), fresh, False  # C7: no refresh = not verified
                elif isinstance(p, PlugShareProvider) and primary_src == "plugshare":
                    ps_id = station.source_id or station.metadata.get("plugshare_id") or str(station.views.get("plugshare", {}).get("id", ""))
                    if ps_id:
                        fresh = p.sync_location(ps_id, auto_save=True)
                        if fresh is None:
 # PlugShare's own /locations/{id} API returned an
 # unambiguous HTTP 404 for this exact id - the source of
 # truth has definitively said this station is gone, not
 # just "couldn't check right now" (network error, rate
 # limit, timeout, non-JSON response all set a DIFFERENT
 # last_error string and are correctly NOT treated as
 # confirmed-not-found here).
                            confirmed_not_found = getattr(p.client, "last_error", "") == "HTTP 404 (not found)"
                            return False, None, None, confirmed_not_found
                        self._refresh_consolidated_export()
                        return fresh is not None, (_diff_station_fields(station, fresh) if fresh else None), fresh, False  # C7: no refresh = not verified
                elif isinstance(p, ExplorenProvider) and primary_src == "exploren":
                    exploren_id = station.source_id or station.key.replace("Exploren_", "")
                    fresh = p.reverify_station(exploren_id)
                    self._refresh_consolidated_export()
                    return fresh is not None, (_diff_station_fields(station, fresh) if fresh else None), fresh, False  # C7: no refresh = not verified
                elif isinstance(p, JoltProvider) and primary_src == "jolt":
                    jolt_id = station.source_id or station.key.replace("Jolt_", "")
                    fresh = p.reverify_station(jolt_id)
                    self._refresh_consolidated_export()
                    return fresh is not None, (_diff_station_fields(station, fresh) if fresh else None), fresh, False  # C7: no refresh = not verified
                elif isinstance(p, AmpChargeProvider) and primary_src in ("ampcharge", "ampol"):
                    amp_id = station.source_id or station.key.replace("Ampol_", "")
                    fresh = p.reverify_station(amp_id)
                    self._refresh_consolidated_export()
                    return fresh is not None, (_diff_station_fields(station, fresh) if fresh else None), fresh, False  # C7: no refresh = not verified
                elif isinstance(p, NRMAProvider) and primary_src == "nrma":
                    nrma_id = station.source_id or station.key.replace("NRMA_", "")
                    fresh = p.reverify_station(nrma_id)
                    self._refresh_consolidated_export()
                    return fresh is not None, (_diff_station_fields(station, fresh) if fresh else None), fresh, False  # C7: no refresh = not verified
        except Exception as e:
            print(f"Error reverifying station {station.name}: {e}")
            return False, None, None, False

        return False, None, None, False

    def remove_station(self, station: UnifiedStationRecord) -> Tuple[bool, str]:
        """
        Permanently removes a station from its own provider's live registry
        file, after archiving the pre-removal record (via the same
        archive_station_record() helper add_or_update() already uses when a
        normal update supersedes an existing entry - so a removed station's
        history is still recoverable for invoice-matching, not just gone).

        This is the ONLY code path anywhere in the provider stack that
        deletes a station, and it is NEVER called automatically -
        reverify_station() and batch_reverify_stations() only ever report a
        confirmed_not_found signal, never call this themselves. It exists so
        the interactive [u] flows in charger_find.py can call it after the
        user has been shown the confirmed-not-found finding and explicitly
        typed 'y' to a removal prompt (2026-09-29 user design requirement:
        "If the single source of truth has no entry - then the truth is the
        entry should be noted to user as false and removed with user
        prompt." - prompted by BUG-156/BUG-157, where a fabricated/stale
        entry sat in the registry indefinitely because no code path ever
        removed anything, on any failure, confirmed or not).

        Currently supports Tesla and PlugShare - the two providers this
        session's two real bug reports involved, and the only two currently
        wired with a confirmed_not_found signal in reverify_station(). For
        any other provider this returns (False, <reason>) rather than
        guessing at a registry file/shape it hasn't been taught yet - the
        caller shows the reason and leaves the registry untouched.

        Returns (True, message) on success, (False, reason) otherwise.
        """
        from .chargers_base import atomic_write_json
        from lib.tessie_api_common import resolve_tessie_archive_home

        src = (station.source or "").lower()
        net = (station.network or "").lower()
        # Route on the record's PRIMARY source only (2026-09-30 review, C1/C8).
        # A merged record like "exploren+plugshare" carries the primary
        # network's own id in source_id; matching with `"plugshare" in src`
        # sent it to PlugShare with that foreign id (live: Exploren 804 ->
        # a Fort Wayne, Indiana home charger; Jolt 169 -> 404 -> "confirmed
        # gone" + removal prompt), and PlugShare-only records whose network
        # merely says "Tesla" went to the Tesla scraper.
        primary_src = src.split("+")[0].strip()
        now_utc = datetime.now(timezone.utc).isoformat()

        try:
            if primary_src == "tesla":
                tesla_provider = None
                for p in self.providers:
                    if isinstance(p, TeslaProvider):
                        tesla_provider = p
                        break
                if tesla_provider is None:
                    return False, "Tesla provider not loaded"

                # 2026-09-30 review (C11): match every copy, not just the first.
                # load_cached_registry() renames a clashing key to
                # "X (Destination)" / "X (Supercharger)", so also try the raw
                # key; when both sides carry a findus URL they must agree, so
                # a same-named station of the other type is never removed.
                cand_keys = [station.key]
                for suffix in (" (Destination)", " (Supercharger)"):
                    if station.key.endswith(suffix):
                        cand_keys.append(station.key[: -len(suffix)])
                removed = []
                seen_paths = set()
                for reg_path in tesla_provider.registry_paths:
                    real = os.path.realpath(reg_path)
                    if real in seen_paths or not os.path.isfile(reg_path):
                        continue
                    seen_paths.add(real)
                    try:
                        with open(reg_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except Exception as e:
                        return False, f"{os.path.basename(reg_path)} is unreadable ({e}) - nothing removed"
                    if not isinstance(data, dict):
                        continue
                    hits = []
                    for k in cand_keys:
                        entry = data.get(k)
                        if not isinstance(entry, dict):
                            continue
                        e_url = ((entry.get("tesla_metadata") or {}).get("findus_url") or entry.get("url") or "")
                        if station.url and e_url and e_url.rstrip("/") != station.url.rstrip("/"):
                            continue
                        hits.append(k)
                    if not hits:
                        continue
                    archive_dir = os.path.join(resolve_tessie_archive_home(), "chargers")
                    os.makedirs(archive_dir, exist_ok=True)
                    archive_basename = os.path.splitext(os.path.basename(reg_path))[0] + "_archived.json"
                    archive_path = os.path.join(archive_dir, archive_basename)
                    for k in hits:
                        archive_station_record(archive_path, k, data[k], now_utc)  # raises -> nothing deleted
                    for k in hits:
                        del data[k]
                    atomic_write_json(reg_path, data)
                    removed.append(f"{', '.join(hits)} from {os.path.basename(reg_path)} (archived to {archive_basename})")

                if not removed:
                    return False, f"'{station.key}' not found in any Tesla registry file (already removed?)"
                tesla_provider._cache = None  # force a fresh reload next time
                self._refresh_consolidated_export()
                note = ""
                if "plugshare" in src.split("+")[1:]:
                    note = " - its PlugShare listing is still in the PlugShare registry"
                return True, "Removed " + "; ".join(removed) + note

            for p in self.providers:
                if isinstance(p, PlugShareProvider) and primary_src == "plugshare":
                    registry = p.load_raw_registry()
                    target_key = station.key if station.key in registry else None
                    if target_key is None:
 # Fall back to matching by PlugShare id, in case the display
                        # key drifted (rename migration, REQ- naming fix, etc.)
                        # since this UnifiedStationRecord was built.
                        ps_id = station.source_id or station.metadata.get("plugshare_id") or str(station.views.get("plugshare", {}).get("id", ""))
                        for k, v in registry.items():
                            if str((v.get("plugshare_metadata", {}) or {}).get("id", "")) == str(ps_id) and ps_id:
                                target_key = k
                                break
                    if target_key is None:
                        return False, f"'{station.key}' not found in the PlugShare registry (already removed?)"

                    existing_entry = registry[target_key]
                    archive_dir = os.path.join(resolve_tessie_archive_home(), "chargers")
                    os.makedirs(archive_dir, exist_ok=True)
                    archive_path = os.path.join(archive_dir, "plugshare_chargers_archived.json")
                    archive_station_record(archive_path, target_key, existing_entry, now_utc)

                    del registry[target_key]
                    if not p.save_raw_registry(registry):
                        return False, "Failed to save the PlugShare registry after removal"

                    self._refresh_consolidated_export()
                    return True, f"Removed '{target_key}' from the PlugShare registry (archived to plugshare_chargers_archived.json)"

            return False, f"Removal not yet supported for provider '{station.source or station.network}'"
        except Exception as e:
            return False, f"Error removing station: {e}"

    def batch_reverify_stations(
        self,
        stations: List[UnifiedStationRecord],
        status_filter: Optional[str] = None
    ) -> Dict[str, int]:
        """
        Batch reverifies stations matching the specified status (e.g. 'STALE', 'RENAMED', 'NOT_IN_JSON').
        """
        targets = []
        for s in stations:
            st = s.get_status()
 # REQ-102: status_filter="ALL" is a sentinel meaning "every station in this
 # list, regardless of its current status" - refreshing literally everything
 # from source (Tesla, PlugShare, Chargefox, ...) rather than only whatever
 # our own STALE/RENAMED heuristics happen to flag. get_status() never
 # actually returns the literal string "ALL" for a real station, so without
 # this branch status_filter="ALL" would just match nothing.
            if status_filter and status_filter.upper() == "ALL":
                targets.append(s)
            elif status_filter:
                if status_filter.upper() == st.upper():
                    targets.append(s)
            elif st in ("STALE", "RENAMED", "NOT_IN_JSON"):
                targets.append(s)

        stats = {"TOTAL": len(targets), "SUCCESS": 0, "ERROR": 0, "NOT_FOUND": 0}
        not_found_stations = []
        for idx, s in enumerate(targets, 1):
            print(f"  [{idx}/{len(targets)}] 🔄 Re-verifying '{s.name}' ({s.source.title()})...")
            ok, _diffs, _fresh, confirmed_not_found = self.reverify_station(s)
            if ok:
                stats["SUCCESS"] += 1
            elif confirmed_not_found:
 # (2026-09-29) A batch run has no single user standing by to
 # answer a per-station "remove it?" prompt, so this deliberately
 # does NOT delete anything here - it only counts and lists the
 # confirmed-gone stations separately from ordinary failures, so
 # the caller's summary can tell "78 stations definitively no
 # longer exist at their source" apart from "12 requests failed/
 # timed out, try again later". Removal stays an explicit,
 # single-station, user-confirmed action (see the interactive [u]
 # handlers in charger_find.py).
                stats["NOT_FOUND"] += 1
                not_found_stations.append(s.name)
            else:
                stats["ERROR"] += 1

        stats["_not_found_names"] = not_found_stations
        return stats
