#!/usr/bin/env python3
"""
Unified Charger Discovery Manager & Spatial Deduplication Engine
================================================================
Orchestrates multi-network provider queries, applies spatial deduplication (R <= 60m),
and enforces the First-Party Truth Hierarchy (Tier 1 Operator > Tier 2 Aggregator).
"""

import os
import re
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
from concurrent.futures import ThreadPoolExecutor

from .chargers_base import (
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
from .chargers_nrma import NrmaProvider


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
    (AC destination vs DC-capable/supercharger) - see BUG-115 in
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
    1. Distance <= max_dist_m (default 250 meters for carparks / shopping centres).
    2. Same network + token similarity >= 0.20 or identical suburb/postcode.
    3. Co-located if distance <= 35m, UNLESS the two are on different
       networks AND report fundamentally different charging classes (AC
       vs DC) - see BUG-115, 2026-09-16.
    """
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

    if dist_m <= 35.0:
        # BUG-115 (AGENTS.local.md, 2026-09-16): this used to be an
        # UNCONDITIONAL "same charger" verdict at this range, on the
        # assumption two different operators never install that close
        # together - false in practice: "NRMA Fast Charger Cowra" (NRMA
        # KML, DC, 2x CCS2) and "Cowra Art Gallery EV Chargers" (Exploren,
        # AC, 2x Type 2 22kW) sit ~27m apart at the same real address and
        # got silently merged into one record, hiding one of two genuinely
        # separate, independently-operated chargers. The user's own
        # diagnosis: "With the different charging speed it's obvious" -
        # a single physical charger can't report both AC-only and DC-
        # capable specs across sources, so a fundamental AC-vs-DC mismatch
        # between two DIFFERENT networks this close together is now treated
        # as two real installs, not one misreported one. Same-network pairs
        # (same_net True) are unaffected - a spec disagreement there is far
        # more likely a data-quality issue on one side than proof of two
        # chargers, and demanding a match would risk splitting genuinely
        # identical stations.
        if same_net or not _hardware_class_mismatch(s1, s2):
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
        # merge until 2026-09-14 (see AGENTS.local.md) - same class of bug as
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
                NrmaProvider()
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

            # BUG-079: get_all_stations() used to unconditionally load every
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
                # BUG-110 (AGENTS.local.md, 2026-09-16): filter_station_records()'s
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

    def sort_records(
        self,
        records: List[UnifiedStationRecord],
        sort_by: str = "distance",
        eval_time: Optional[str] = None
    ) -> List[UnifiedStationRecord]:
        """
        Sorts unified records by distance, price, power, stalls, or name.
        """
        if sort_by == "price":
            def price_key(r: UnifiedStationRecord):
                p = r.evaluate_rate(eval_time)
                if p is None:
                    return (3, 999.0, r.name)
                elif p == 0.0:
                    return (0, 0.0, r.name)
                else:
                    return (1, p, r.name)
            return sorted(records, key=price_key)

        elif sort_by == "power":
            return sorted(records, key=lambda r: (- (r.max_power_kw or 0.0), r.distance_km or 999.0))

        elif sort_by == "stalls":
            return sorted(records, key=lambda r: (- (r.stalls_total or 0), r.distance_km or 999.0))

        elif sort_by in ["facilities", "facility", "rating"]:
            try:
                from station_facilities import get_facilities_registry
                _f_reg = get_facilities_registry()
            except ImportError:
                try:
                    from lib.station_facilities import get_facilities_registry
                    _f_reg = get_facilities_registry()
                except ImportError:
                    _f_reg = None

            def fac_key(r: UnifiedStationRecord):
                if _f_reg:
                    f = _f_reg.get_station_facilities(station_key=r.key, station_name=r.name, lat=r.lat, lon=r.lon)
                    if f:
                        score = f.get("score", 0.0)
                        return (-score, r.distance_km if r.distance_km is not None else 999.0, r.name.lower())
                return (1.0, r.distance_km if r.distance_km is not None else 999.0, r.name.lower())

            return sorted(records, key=fac_key)

        elif sort_by in ["name", "state"]:
            return sorted(records, key=lambda r: (r.state or "ZZ", r.name.lower()))

        else:  # default "distance"
            has_dist = any(r.distance_km is not None for r in records)
            if has_dist:
                return sorted(records, key=lambda r: (r.distance_km if r.distance_km is not None else 999999.0, r.name.lower()))
            else:
                tier_order = {"supercharger": 0, "dc_fast": 1, "ac_destination": 2}
                return sorted(records, key=lambda r: (tier_order.get(r.speed_tier, 3), r.state or "ZZ", r.name.lower()))

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
                tessie_home = os.path.join(repo_root, "Tessie")
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
            except Exception:
                existing_stations = {}

        all_records: List[UnifiedStationRecord] = []
        for p in self.providers:
            try:
                cached = p.load_cached_registry()
                all_records.extend(cached)
            except Exception:
                pass

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
        local_fallback = os.path.join(repo_root, "Tessie", "charging_stations.json")
        if local_fallback not in target_candidates:
            target_candidates.append(local_fallback)

        # AGENTS.md BUG-067: save_json_with_fallback() (base.py) now
        # carries this exact "try each candidate, raise if all fail"
        # behavior AND writes atomically (temp file + os.replace) - this
        # used to be a bare open(path, "w") per candidate, so a killed
        # process mid-write on this potentially large consolidated export
        # could corrupt charging_stations.json.
        final_path = save_json_with_fallback(target_candidates, payload)

        return final_path, len(stations_dict)

    def reverify_station(self, station: UnifiedStationRecord) -> bool:
        """
        Re-verifies a single station live against its primary provider,
        updating local cache and archiving historical state if changes occur.
        """
        src = (station.source or "").lower()
        net = (station.network or "").lower()

        try:
            if "tesla" in src or "tesla" in net:
                from charger_find_tesla import TeslaChargerExplorer
                explorer = TeslaChargerExplorer()
                c_type = "supercharger" if station.speed_tier == "supercharger" else "destination_charger"
                explorer.scrape_station_detail(station.url, c_type, auto_save=True)
                self.export_consolidated_registry()
                return True

            for p in self.providers:
                if isinstance(p, ChargefoxProvider) and "chargefox" in src:
                    cf_id = station.source_id or station.key.replace("Chargefox_", "")
                    p.reverify_station(cf_id)
                    self.export_consolidated_registry()
                    return True
                elif isinstance(p, EvieProvider) and "evie" in src:
                    evie_id = station.source_id or station.key.replace("Evie_", "")
                    p.reverify_station(evie_id)
                    self.export_consolidated_registry()
                    return True
                elif isinstance(p, BPPulseProvider) and ("bppulse" in src or "bp pulse" in net):
                    bp_id = station.source_id or station.key.replace("BPPulse_", "")
                    p.reverify_station(bp_id)
                    self.export_consolidated_registry()
                    return True
                elif isinstance(p, EVXProvider) and "evx" in src:
                    evx_id = station.source_id or station.key.replace("EVX_", "")
                    p.reverify_station(evx_id)
                    self.export_consolidated_registry()
                    return True
                elif isinstance(p, PlugShareProvider) and "plugshare" in src:
                    ps_id = station.source_id or station.metadata.get("plugshare_id") or str(station.views.get("plugshare", {}).get("id", ""))
                    if ps_id:
                        p.sync_location(ps_id, auto_save=True)
                        self.export_consolidated_registry()
                        return True
                elif isinstance(p, ExplorenProvider) and "exploren" in src:
                    exploren_id = station.source_id or station.key.replace("Exploren_", "")
                    p.reverify_station(exploren_id)
                    self.export_consolidated_registry()
                    return True
                elif isinstance(p, JoltProvider) and "jolt" in src:
                    jolt_id = station.source_id or station.key.replace("Jolt_", "")
                    p.reverify_station(jolt_id)
                    self.export_consolidated_registry()
                    return True
                elif isinstance(p, NrmaProvider) and "nrma" in src:
                    nrma_id = station.source_id or station.key.replace("NRMA_", "")
                    p.reverify_station(nrma_id)
                    self.export_consolidated_registry()
                    return True
        except Exception as e:
            print(f"Error reverifying station {station.name}: {e}")
            return False

        return False

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
            if status_filter:
                if status_filter.upper() == st.upper():
                    targets.append(s)
            elif st in ("STALE", "RENAMED", "NOT_IN_JSON"):
                targets.append(s)

        stats = {"TOTAL": len(targets), "SUCCESS": 0, "ERROR": 0}
        for idx, s in enumerate(targets, 1):
            print(f"  [{idx}/{len(targets)}] 🔄 Re-verifying '{s.name}' ({s.source.title()})...")
            ok = self.reverify_station(s)
            if ok:
                stats["SUCCESS"] += 1
            else:
                stats["ERROR"] += 1

        return stats
