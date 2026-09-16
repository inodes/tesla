#!/usr/bin/env python3
"""
Base Charging Provider and Unified Data Schema
==============================================
Standardized abstract interface and data structures for EV charging providers.
"""

import os
import re
import json
import math
import tempfile
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple


def atomic_write_json(path: str, payload: Any, indent: int = 2) -> None:
    """Writes JSON to `path` atomically: serializes to a temp file in the
    SAME directory, flushes+fsyncs it, then os.replace()s it into place -
    so a process killed mid-write (Ctrl-C, laptop sleep, OOM - all
    plausible on a multi-minute/hour provider scrape) can never leave a
    truncated/corrupt registry at `path`. A partial write only ever
    touches the abandoned temp file; `path` itself is either the old
    complete file or the new complete file, never something in between.
    Raises on failure (permission, disk full, missing parent, ...) - the
    caller decides whether to fall back to another path or propagate.

    AGENTS.md BUG-067: every provider's own registry save used to write
    directly to its target path with plain `open(path, "w")`, with no
    atomicity at all - this is the fix, factored out once here so every
    provider (and manager.py's consolidated export) shares the same
    guarantee instead of re-implementing (or forgetting to re-implement)
    it per file."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=indent, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def save_json_with_fallback(target_paths: List[str], payload: Any, indent: int = 2) -> str:
    """Tries each path in `target_paths` in order via atomic_write_json(),
    stopping at the first success and returning that path. Only a
    per-path environmental failure (PermissionError/OSError - e.g. a
    read-only mount, or a parent directory that can't be created) falls
    through to the next candidate; any other exception propagates
    immediately, since that's a real bug, not "try the next path".

    AGENTS.md BUG-067: this replaces a pattern that used to be
    copy-pasted per provider (chargefox_provider.py, manager.py) as a
    bare `for p in target_paths: try: ... except (PermissionError,
    OSError): continue` loop with NOTHING checking whether any write
    actually succeeded - a total failure (every candidate path
    unwritable) silently fell out the bottom having saved nothing, while
    the caller went on to log "successfully saved N stations" anyway.
    Raises PermissionError naming every failed candidate if none
    succeed - a save can no longer look like it worked when it didn't."""
    last_exc = None
    for p in target_paths:
        try:
            atomic_write_json(p, payload, indent=indent)
            return p
        except (PermissionError, OSError) as e:
            last_exc = e
            continue
    detail = f" (last error: {last_exc})" if last_exc else ""
    raise PermissionError(f"Could not save registry to any of: {target_paths}{detail}")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Computes great-circle distance between two GPS coordinates in kilometers."""
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return float("inf")
    r = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2.0) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(d_lon / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


COUNTRY_TAX_CONFIG: Dict[str, Dict[str, Any]] = {
    "AU": {"currency": "AUD", "tax_name": "GST", "tax_rate": 0.10, "tax_inclusive": True},
    "AUSTRALIA": {"currency": "AUD", "tax_name": "GST", "tax_rate": 0.10, "tax_inclusive": True},
    "JP": {"currency": "JPY", "tax_name": "Consumption Tax", "tax_rate": 0.10, "tax_inclusive": True},
    "JAPAN": {"currency": "JPY", "tax_name": "Consumption Tax", "tax_rate": 0.10, "tax_inclusive": True},
    "NZ": {"currency": "NZD", "tax_name": "GST", "tax_rate": 0.15, "tax_inclusive": True},
    "NEW ZEALAND": {"currency": "NZD", "tax_name": "GST", "tax_rate": 0.15, "tax_inclusive": True},
    "GB": {"currency": "GBP", "tax_name": "VAT", "tax_rate": 0.20, "tax_inclusive": True},
    "UNITED KINGDOM": {"currency": "GBP", "tax_name": "VAT", "tax_rate": 0.20, "tax_inclusive": True},
    "DE": {"currency": "EUR", "tax_name": "VAT", "tax_rate": 0.19, "tax_inclusive": True},
    "GERMANY": {"currency": "EUR", "tax_name": "VAT", "tax_rate": 0.19, "tax_inclusive": True},
    "US": {"currency": "USD", "tax_name": "Sales Tax", "tax_rate": 0.0, "tax_inclusive": False},
    "UNITED STATES": {"currency": "USD", "tax_name": "Sales Tax", "tax_rate": 0.0, "tax_inclusive": False},
    "CA": {"currency": "CAD", "tax_name": "GST/HST", "tax_rate": 0.05, "tax_inclusive": False},
    "CANADA": {"currency": "CAD", "tax_name": "GST/HST", "tax_rate": 0.05, "tax_inclusive": False},
}

DEFAULT_TAX_CONFIG = {"currency": "AUD", "tax_name": "GST", "tax_rate": 0.10, "tax_inclusive": True}


def compute_tax_breakdown(
    listed_rate: Optional[float],
    country: str = "Australia",
    is_tax_inclusive: Optional[bool] = None
) -> Tuple[Optional[float], Optional[float], Optional[float], str, float]:
    """
    Computes (gross_rate, net_rate, tax_amount, tax_name, tax_rate).
    """
    if listed_rate is None:
        return None, None, None, "GST", 0.10
        
    cfg = COUNTRY_TAX_CONFIG.get(str(country).upper(), DEFAULT_TAX_CONFIG)
    tax_rate = cfg["tax_rate"]
    tax_name = cfg["tax_name"]
    inc = cfg["tax_inclusive"] if is_tax_inclusive is None else is_tax_inclusive
    
    val = float(listed_rate)
    if inc:
        gross = round(val, 4)
        net = round(gross / (1.0 + tax_rate), 4) if tax_rate > 0 else gross
        tax = round(gross - net, 4)
    else:
        net = round(val, 4)
        gross = round(net * (1.0 + tax_rate), 4) if tax_rate > 0 else net
        tax = round(gross - net, 4)
        
    return gross, net, tax, tax_name, tax_rate


# Known first-party network name-tokens and each one's expected `source`
# provenance tag - shared by type_icon() (BUG-107, badge selection) and
# filter_station_records() (BUG-108, default exclusion of unverified brand
# self-claims). NRMA has no separate source tag of its own - it's sourced
# via Chargefox's own GraphQL feed, per AGENTS.md's "chargefox (covers
# NRMA...)" convention. AmpCharge's real source tag is "ampol", not
# "ampcharge" - see chargers_ampcharge.py's own UnifiedStationRecord
# construction.
_FIRST_PARTY_NETWORK_SOURCE_TAGS: Dict[str, Tuple[str, ...]] = {
    "tesla": ("tesla",),
    "chargefox": ("chargefox",),
    # NRMA now has its own Tier 1 provider (source="nrma", REQ-088,
    # AGENTS.local.md 2026-09-16) as well as being reachable through
    # Chargefox's own feed for the ~28 overlapping sites - either
    # provenance tag is a verified NRMA claim, so both are accepted.
    "nrma": ("chargefox", "nrma"),
    "evx": ("evx",),
    "evie": ("evie",),
    "bp": ("bp",),
    "ampcharge": ("ampol",),
    "ampol": ("ampol",),
    "exploren": ("exploren",),
    "jolt": ("jolt",),
}


def _network_claims_unverified_brand(network: Optional[str], source: Optional[str]) -> bool:
    """
    True if `network`'s free text claims one of our known first-party
    brands but `source` doesn't actually carry that brand's own provenance
    tag - i.e. the claim was never confirmed against that network's real
    live registry.

    BUG-107/BUG-108 (AGENTS.local.md, 2026-09-16): concretely caught via a
    real PlugShare-only listing named "Jolt - Meadowbank" ($0.52/kWh, 1
    bay) that doesn't correspond to any of JOLT's own 133 real live
    stations - `source` for it is `"plugshare"` only, `network` self-
    reports `"Jolt"` with nothing behind that claim. A live-registry check
    finding zero match makes "stale / mislabeled / decommissioned" far
    more likely than "real charger we just haven't verified yet".
    """
    net = (network or "").lower()
    src = (source or "").lower()
    for token, tags in _FIRST_PARTY_NETWORK_SOURCE_TAGS.items():
        if token in net and not any(tag in src for tag in tags):
            return True
    return False


def filter_station_records(
    records: List['UnifiedStationRecord'],
    filters: Optional[Dict[str, Any]] = None
) -> List['UnifiedStationRecord']:
    """
    Applies standard speed_tier, min_kw, state, and network filters to a list of records.
    """
    if not filters:
        return records

    speed_tier = filters.get("speed_tier")
    min_kw = filters.get("min_kw", 0.0) or 0.0
    network = (filters.get("network") or "").lower().strip()
    state = (filters.get("state") or "").upper().strip()

    filtered = []
    for st in records:
        # Speed tier filter
        if speed_tier:
            if speed_tier == "supercharger":
                # "Supercharger" is exclusively a Tesla network/hardware term -
                # --sc means "look at tesla_superchargers", full stop. Source
                # is checked here as defense-in-depth (a misclassification bug
                # in any provider - e.g. PlugShare tagging a non-Tesla station
                # as speed_tier="supercharger" - must not leak through this
                # filter regardless). Fixed 2026-09-14.
                # source can be a merged "tesla+plugshare" string after
                # multi-source dedup, so check membership, not equality.
                if st.speed_tier != "supercharger" or "tesla" not in (st.source or "").lower():
                    continue
            elif speed_tier == "dc_fast":
                if st.speed_tier not in ("dc_fast", "supercharger") and (st.max_power_kw or 0.0) < 25.0:
                    continue
            elif speed_tier == "ac_destination":
                if st.speed_tier != "ac_destination" and (st.max_power_kw or 0.0) >= 25.0:
                    continue

        # Minimum power filter
        if min_kw and (st.max_power_kw or 0.0) < min_kw:
            continue

        # State filter
        if state and (st.state or "").upper() != state:
            continue

        # Network filter
        if network:
            net_search = f"{st.network} {st.operator_name or ''} {st.name} {st.source}".lower()
            if network not in net_search:
                continue

        # Exclude Coming Soon / Under Construction / In Development / Planned stations by default
        if not filters.get("include_coming_soon", False):
            name_low = st.name.lower()
            if any(phrase in name_low for phrase in ("coming soon", "under construction", "in development", "planned", "permit")):
                continue
            meta = st.metadata or {}
            if meta.get("under_construction") or meta.get("coming_soon"):
                continue
            st_status = str(meta.get("status") or meta.get("site_status") or "").lower()
            if st_status in ("in_development", "permit", "construction", "under_construction", "coming_soon", "planned"):
                continue

        # Exclude records whose `network` free text claims a known first-
        # party brand we have a live authoritative registry for, but whose
        # actual `source` provenance never matched that registry - BUG-108.
        # BUG-107 already stops these getting a confident brand badge; this
        # goes further and excludes them from results by default too (opt
        # back in with include_unverified_brand=True).
        if not filters.get("include_unverified_brand", False):
            if _network_claims_unverified_brand(st.network, st.source):
                continue

        # Exclude Private Residential stations by default
        if not filters.get("include_private", False):
            meta_acc = str(st.metadata.get("access_type", "")).strip().lower()
            if meta_acc.startswith("private"):
                continue

        # Minimum facilities quality rating filter
        min_fac = (filters.get("min_facilities") or "").lower().strip()
        if min_fac:
            min_score = 8.0 if min_fac.startswith("exc") else (5.5 if min_fac.startswith("goo") else (3.0 if min_fac.startswith("bas") else 0.0))
            try:
                from station_facilities import get_facilities_registry
                _f_reg = get_facilities_registry()
            except ImportError:
                try:
                    from lib.station_facilities import get_facilities_registry
                    _f_reg = get_facilities_registry()
                except ImportError:
                    _f_reg = None
            if _f_reg:
                fac = _f_reg.get_station_facilities(station_key=st.key, station_name=st.name, lat=st.lat, lon=st.lon)
                if not fac or (fac.get("score", 0.0) < min_score):
                    continue

        filtered.append(st)

    return filtered


def strip_invisible_chars(s: Optional[str]) -> Optional[str]:
    """
    Strips zero-width and other invisible Unicode formatting characters
    (zero-width space/joiner/non-joiner, word joiner, BOM, soft hyphen,
    bidi control marks, etc. - Unicode general category 'Cf') from
    upstream text, then collapses any resulting double space.

    These are pure noise in a charger name/address - width-aware display
    code already scores them as 0 columns (see table_formatter.py's
    char_width()) - but they're an easy way for a visual "this should be
    self-healing" alignment, or a plain string comparison, to go wrong
    depending on how forgiving a given terminal/font/JSON diff is of stray
    invisible characters riding through otherwise-plain text.
    Root-caused via a real Chargefox station
    ("Bondi Beach - 157 \u200bCurlewis St" - see AGENTS.local.md BUG-093),
    then generalized here to every text field a provider can populate, not
    just name, since upstream catalogs can embed this junk into an address
    or suburb just as easily (REQ-077).
    """
    if not s:
        return s
    cleaned = "".join(c for c in s if unicodedata.category(c) != "Cf")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return cleaned


@dataclass
class UnifiedStationRecord:
    key: str
    name: str
    network: str
    operator_name: str = ""
    address: str = ""
    suburb: str = ""
    state: str = ""
    postcode: str = ""
    country: str = "Australia"
    lat: float = 0.0
    lon: float = 0.0
    timezone: str = "Australia/Sydney"
    
    # Hardware
    speed_tier: str = "dc_fast"  # 'supercharger', 'dc_fast', 'ac_destination'
    max_power_kw: float = 0.0
    stalls_total: int = 0
    stalls_available: Optional[int] = None
    plugs: List[str] = field(default_factory=list)
    tethered: bool = True
    hardware_model: str = ""
    
    # Tariffs / Pricing
    pricing_model: str = "unknown"  # 'flat', 'time_of_use', 'free', 'unknown'
    current_rate_per_kwh: Optional[float] = None
    rate_range: str = ""
    schedule: List[Dict[str, Any]] = field(default_factory=list)
    # Separate non-Tesla-vehicle rate schedule (Superchargers open to non-Tesla
    # CCS2 EVs commonly charge a different rate than Tesla members/vehicles).
    # Added 2026-09-14 - this dimension existed in charger_find_tesla.py's
    # raw registry (tariffs.non_tesla.rate_schedules) the whole time but was
    # never carried into UnifiedStationRecord, so it silently vanished from
    # charger_find.py's display when this schema became the shared one.
    non_tesla_schedule: List[Dict[str, Any]] = field(default_factory=list)
    idle_fee_per_min: Optional[float] = None
    congestion_fee_per_min: Optional[float] = None
    parking_fee: Optional[str] = None
    currency: str = "AUD"
    
    # Source & Provenance
    tier: int = 1  # 1 = First-Party Authoritative, 2 = Aggregator
    source: str = "unknown"
    source_id: str = ""
    url: str = ""
    last_verified_utc: str = ""
    # When this record's pricing/hardware became effective (charger_find_tesla.py's
    # "valid_from"/"first_seen") - distinct from last_verified_utc, which is
    # just "when this was last checked against the live page". Added
    # 2026-09-14: tessie_charging_analyzer.py's invoice reconciliation needs
    # the EFFECTIVE date to pick the right historical (active or archived)
    # tariff record for a charge from long ago, not merely the most recent
    # verification timestamp - see AGENTS.local.md.
    effective_date_utc: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    amenities: Dict[str, Any] = field(default_factory=dict)
    keywords: List[str] = field(default_factory=list)
    views: Dict[str, Any] = field(default_factory=dict)
    
    # Calculated / Runtime properties
    distance_km: Optional[float] = None

    def __post_init__(self):
        """
        Strips invisible/zero-width characters (strip_invisible_chars(),
        above) from every free-text field a provider can populate - not
        just `name` - per the user's own generalization of BUG-093: "It
        would be good to remove any padding invisible characters on all
        fields that have values."

        Deliberately excludes `key` (the registry's own persistence/
        matching identifier - silently rewriting it here would look like
        every affected station "renamed itself" on the next diff, which
        is a call for the RENAMED-status reconciliation flow to surface,
        not something to do silently in a constructor) and non-prose
        fields (url, source, source_id, currency, timezone, country)
        where stripping could alter a machine-meaningful value.
        """
        self.name = strip_invisible_chars(self.name)
        self.operator_name = strip_invisible_chars(self.operator_name)
        self.address = strip_invisible_chars(self.address)
        self.suburb = strip_invisible_chars(self.suburb)
        self.state = strip_invisible_chars(self.state)
        self.postcode = strip_invisible_chars(self.postcode)
        self.hardware_model = strip_invisible_chars(self.hardware_model)
        self.rate_range = strip_invisible_chars(self.rate_range)
        if self.parking_fee:
            self.parking_fee = strip_invisible_chars(self.parking_fee)
        self.plugs = [strip_invisible_chars(p) for p in self.plugs]
        self.keywords = [strip_invisible_chars(k) for k in self.keywords]
        for meta_dict in (self.metadata, self.amenities):
            for mk, mv in list(meta_dict.items()):
                if isinstance(mv, str):
                    meta_dict[mk] = strip_invisible_chars(mv)

        # BUG-109 (AGENTS.local.md, 2026-09-16): "supercharger" is a Tesla-
        # exclusive hardware/network term, but `speed_tier` is populated by
        # each PROVIDER's own parsing/classification logic - and a Tier-2
        # aggregator (PlugShare) can self-classify a crowd-sourced listing
        # as "supercharger" from its own (unverified) network-name text,
        # with no actual Tesla registry match behind that claim. Concretely
        # caught via a real PlugShare-only ("Macquarie Centre (2)",
        # source="plugshare") listing carrying speed_tier="supercharger" -
        # every consumer of `self.speed_tier` (type_icon(), badge(),
        # access_label(), speed_tier_label(), and the --dc filter's
        # dc_fast branch, which treats "supercharger" as passing) trusted
        # it completely unconditionally, so this one bad field silently
        # produced a confidently-wrong 🔴 SC badge and "CCS2 All"/V2-tier
        # display everywhere downstream - fixing every call site
        # individually is exactly the kind of whack-a-mole that already
        # missed spots once (BUG-107 covered every OTHER network branch in
        # type_icon() but not this one, its very first line). Enforcing the
        # invariant once, here, at construction time, means every consumer
        # inherits the fix for free: a record claiming "supercharger" but
        # whose `source` doesn't actually carry "tesla" (a merged record's
        # source is a "+"-joined string like "tesla+plugshare", so this is
        # a substring check, not equality) gets downgraded to "dc_fast" -
        # still an honest, plausible classification for a real DC charger,
        # just not a Tesla-verified Supercharger claim - with the original
        # claim preserved in metadata for anyone auditing the correction.
        if self.speed_tier == "supercharger" and "tesla" not in (self.source or "").lower():
            self.metadata["speed_tier_corrected_from"] = "supercharger"
            self.speed_tier = "dc_fast"

    def to_dict(self) -> Dict[str, Any]:
        """Converts UnifiedStationRecord to the full multi-view canonical dictionary format."""
        sources = [s.strip() for s in self.source.split("+") if s.strip()]
        source_ids = [self.source_id] if self.source_id else []
        if self.metadata.get("secondary_source_ids"):
            for sid in self.metadata["secondary_source_ids"]:
                if sid and sid not in source_ids:
                    source_ids.append(sid)
        hw_tier = self.metadata.get("hardware_tier")
        open_nt = self.metadata.get("open_to_non_tesla", "Tesla" not in self.network or "CCS2" in self.plugs)
        t_only = self.metadata.get("tesla_only", "Tesla" in self.network and not open_nt)
        
        # Compute rate tax breakdown
        gross_r, net_r, tax_r, tax_n, tax_pct = compute_tax_breakdown(self.current_rate_per_kwh, self.country)
        gross_idle, net_idle, _, _, _ = compute_tax_breakdown(self.idle_fee_per_min, self.country)
        gross_cong, net_cong, _, _, _ = compute_tax_breakdown(self.congestion_fee_per_min, self.country)

        enriched_schedules = []
        for s in self.schedule:
            raw_r = s.get("rate") or s.get("rate_per_kwh")
            s_gross, s_net, s_tax, _, _ = compute_tax_breakdown(raw_r, self.country)
            enriched_schedules.append({
                "label": s.get("label") or s.get("name") or "Rate",
                "start_time": s.get("start") or s.get("start_time") or "00:00",
                "end_time": s.get("end") or s.get("end_time") or "24:00",
                "rate_per_kwh": s_gross,
                "rate_per_kwh_gross": s_gross,
                "rate_per_kwh_net": s_net,
                "tax_amount": s_tax,
                "days": s.get("days", ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]),
                "months": s.get("months", ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
            })

        # Build tessie cost config
        tessie_cost_config = {
            "currency": self.currency,
            "pricing_model": self.pricing_model,
            "per_kwh_flat": gross_r if self.pricing_model == "flat" else 0.0,
            "per_minute": 0.0,
            "per_session": 0.0,
            "idle_fee_per_min": gross_idle or 0.0,
            "rate_schedules": enriched_schedules
        }

        return {
            "id": self.key,
            "name": self.name,
            "network": self.network,
            "operator": self.operator_name or self.network,
            "location": {
                "address": self.address,
                "street": self.metadata.get("street") or (self.address.split(",")[0].strip() if self.address else ""),
                "suburb": self.suburb,
                "state": self.state,
                "postcode": self.postcode,
                "country": self.country,
                "country_code": self.metadata.get("country_code", "AU" if "australia" in self.country.lower() else "JP" if "japan" in self.country.lower() else "US"),
                "lat": self.lat,
                "lon": self.lon,
                "radius_m": 250 if self.speed_tier == "supercharger" else 150,
                "timezone": self.timezone,
            },
            "hardware": {
                "speed_tier": self.speed_tier,
                "stalls": self.stalls_total,
                "stalls_available": self.stalls_available,
                "max_power_kw": self.max_power_kw,
                "tier": hw_tier,
                "connector_types": self.plugs,
                "tethered": self.tethered,
                "pedestals": self.metadata.get("pedestals", []),
                "hardware_models": [self.hardware_model] if self.hardware_model else [],
            },
            "compatibility": {
                "open_to_non_tesla": open_nt,
                "tesla_only": t_only,
                "access": self.metadata.get("access_type", "Public 24/7"),
                "connector_types": self.plugs,
            },
            "access": {
                "hours": self.metadata.get("access_hours", "Available 24/7"),
                "description": self.metadata.get("access_description", "")
            },
            "tariffs": {
                "currency": self.currency,
                "tax_name": tax_n,
                "tax_rate": tax_pct,
                "tax_included_in_listed_price": COUNTRY_TAX_CONFIG.get(str(self.country).upper(), DEFAULT_TAX_CONFIG)["tax_inclusive"],
                "pricing_model": self.pricing_model,
                "has_tou_pricing": len(self.schedule) > 1,
                "per_kwh_gross": gross_r,
                "per_kwh_net": net_r,
                "tax_per_kwh": tax_r,
                "idle_fee_per_min_gross": gross_idle,
                "idle_fee_per_min_net": net_idle,
                "congestion_fee_per_min_gross": gross_cong,
                "congestion_fee_per_min_net": net_cong,
                "congestion_fee_per_min": gross_cong or 0.0,
                "parking_fee": self.parking_fee,
                "rate_range": self.rate_range,
                "schedule": enriched_schedules,
                "tesla_members": {
                    "pricing_model": self.pricing_model,
                    "rate_schedules": enriched_schedules
                },
                "non_tesla": {
                    "supported": open_nt,
                    "pricing_model": self.pricing_model if open_nt else "none",
                    "rate_schedules": enriched_schedules if open_nt else []
                }
            },
            "tessie_cost_config": tessie_cost_config,
            "amenities": self.amenities,
            "keywords": self.keywords,
            "aliases": self.metadata.get("aliases", []),
            "provenance": {
                "tier": self.tier,
                "sources": sources,
                "source_ids": source_ids,
                "url": self.url,
                "last_verified_utc": self.last_verified_utc,
            },
            "views": self.views
        }

    def get_effective_pricing(self, eval_time: Optional[str] = None) -> Tuple[Optional[float], str, str]:
        """
        Calculates (rate_per_kwh, period_label, time_window) evaluated at eval_time or local station time.
        """
        if self.pricing_model == "free" or (self.current_rate_per_kwh == 0.0 and not self.schedule):
            return 0.0, "Free", "24/7"

        # Resolve local time in station timezone
        from zoneinfo import ZoneInfo
        from datetime import datetime
        try:
            tz = ZoneInfo(self.timezone or "Australia/Sydney")
        except Exception:
            tz = ZoneInfo("Australia/Sydney")

        if eval_time:
            clean_t = eval_time.replace(":", "").strip()
            if len(clean_t) == 4 and clean_t.isdigit():
                eval_val = int(clean_t)
            else:
                now_dt = datetime.now(tz)
                eval_val = int(now_dt.strftime("%H%M"))
        else:
            now_dt = datetime.now(tz)
            eval_val = int(now_dt.strftime("%H%M"))

        if self.schedule:
            for item in self.schedule:
                st = item.get("start") or item.get("start_time") or "00:00"
                et = item.get("end") or item.get("end_time") or "24:00"
                st_clean = st.replace(":", "") if st.replace(":", "").isdigit() else "0000"
                et_clean = et.replace(":", "") if et.replace(":", "").isdigit() else "2400"
                s_num = int(st_clean)
                e_num = int(et_clean)
                if s_num <= e_num:
                    in_window = s_num <= eval_val < e_num
                else:
                    in_window = eval_val >= s_num or eval_val < e_num

                if in_window:
                    r = item.get("rate") if item.get("rate") is not None else item.get("rate_per_kwh")
                    lbl = item.get("name") or item.get("label") or "Rate"
                    win = f"{st}–{et}"
                    r_f = float(r) if r is not None else None
                    return r_f, lbl, win

            first = self.schedule[0]
            r = first.get("rate") if first.get("rate") is not None else first.get("rate_per_kwh")
            lbl = first.get("name") or first.get("label") or "Rate"
            st = first.get("start") or first.get("start_time") or "00:00"
            et = first.get("end") or first.get("end_time") or "24:00"
            r_f = float(r) if r is not None else None
            return r_f, lbl, f"{st}–{et}"

        if self.current_rate_per_kwh is not None:
            if self.current_rate_per_kwh == 0.0:
                return 0.0, "Free", "24/7"
            return self.current_rate_per_kwh, "Base Rate", "00:00–24:00"

        return None, "-", "-"

    def evaluate_rate(self, eval_time: Optional[str] = None) -> Optional[float]:
        """Evaluates active $/kWh rate at eval_time or local station time."""
        rate, _, _ = self.get_effective_pricing(eval_time)
        return rate

    def formatted_rate(self, eval_time: Optional[str] = None) -> str:
        """Returns clean formatted rate string (e.g. '$0.59/kWh', 'Free', '$0.30 - $0.65')."""
        rate, _, _ = self.get_effective_pricing(eval_time)
        if rate is not None:
            if rate == 0.0:
                return "Free"
            return f"${rate:.2f}/kWh"
        if self.rate_range:
            return self.rate_range
        return "-"

    def formatted_power(self) -> str:
        """Returns formatted power with unit (e.g. '250 kW', '22 kW', '7 kW')."""
        if self.max_power_kw and self.max_power_kw > 0:
            if self.max_power_kw.is_integer():
                return f"{int(self.max_power_kw)} kW"
            return f"{self.max_power_kw:.1f} kW"
        return "-"

    def speed_tier_label(self) -> str:
        """Returns human-friendly charging category label."""
        if self.speed_tier == "supercharger":
            return "Tesla Supercharger"
        if self.speed_tier == "dc_fast":
            return "DC Fast Charger"
        net = (self.network or "").lower()
        src = (self.source or "").lower()
        if "tesla" in net or "tesla" in src:
            return "Tesla Destination"
        return "AC Public"

    def type_icon(self) -> str:
        """Returns short type icon (🔴 SC, 🔌 Dest, 🦊 CF, ⚡ NRMA, 🕊️ Evie, 🟢 BP, ⛽ Amp, 🟣 Exp, 🔵 Jolt, 🔌 3rd)."""
        if self.speed_tier == "supercharger":
            return "🔴 SC"
        net = (self.network or "").lower()
        src = (self.source or "").lower()
        if self.source == "tesla" or ("tesla" in net and "tesla" in src):
            return "🔌 Dest"
        # BUG-107 (AGENTS.local.md, 2026-09-16): a network-specific badge is
        # only trustworthy when this record has ACTUAL Tier-1 provenance for
        # that network (src - `source`, a "+"-joined provenance tag like
        # "jolt" or a merged "jolt+plugshare" - contains that network's own
        # tag), never just because `network` TEXT says so. `network` is a
        # free-text label a PURE PlugShare ("Tier 2 Aggregator") record can
        # self-report as anything at all, unverified against the real
        # network's own registry - caught concretely via the user's own
        # report of a real PlugShare listing named "Jolt - Meadowbank" that
        # doesn't match any station in JOLT's actual live feed, plus (found
        # while fixing it) a "Sydney Olympic Park" listing self-tagged
        # "NRMA" that's equally unverified (`sources: ["plugshare"]` only,
        # no `chargefox` provenance at all). A name-only match with no
        # matching source now falls through to the generic "3rd" badge - an
        # honest "unverified 3rd-party listing" signal instead of a
        # confident, possibly-wrong brand badge. (NRMA is verified via EITHER
        # `chargefox` in src - the Chargefox-fed subset of NRMA sites, see
        # AGENTS.md's "chargefox (covers NRMA...)" convention - OR `nrma`
        # in src, its own dedicated Tier 1 provider added in REQ-088.)
        if "chargefox" in net and "chargefox" in src:
            return "🦊 CF"
        if "nrma" in net and ("chargefox" in src or "nrma" in src):
            return "⚡ NRMA"
        if "evx" in net and "evx" in src:
            return "🟠 EVX"
        if "evie" in net and "evie" in src:
            return "🕊️ Evie"
        if "bp" in net and "bp" in src:
            return "🟢 BP"
        if ("ampcharge" in net or "ampol" in net) and "ampol" in src:
            return "⛽ Amp"
        if "exploren" in net and "exploren" in src:
            return "🟣 Exp"
        if "jolt" in net and "jolt" in src:
            return "🔵 Jolt"
        return "🔌 3rd"

    def tier_badge(self) -> str:
        """Returns concise hardware tier string (V2, V3, V4, AC, 350kW, 150kW, etc.)."""
        meta_tier = str(self.metadata.get("hardware_tier", "")).strip().upper()
        if meta_tier in ("V2", "V3", "V4", "AC", "DC"):
            return meta_tier
        if self.speed_tier == "ac_destination" or (self.max_power_kw and self.max_power_kw < 25.0):
            return "AC"
        if self.max_power_kw:
            if self.source == "tesla" or "tesla" in (self.network or "").lower():
                if self.max_power_kw >= 300:
                    return "V4"
                if self.max_power_kw >= 250:
                    return "V3"
                if self.max_power_kw >= 120:
                    return "V2"
            if self.max_power_kw.is_integer():
                return f"{int(self.max_power_kw)}kW"
            return f"{self.max_power_kw:.0f}kW"
        return "AC" if self.speed_tier == "ac_destination" else "-"

    def access_label(self) -> str:
        """Returns clean access label (Tesla Only, Type 2 All, CCS2 All, Public, Customer, Private)."""
        meta_acc = str(self.metadata.get("access_type", "")).strip().lower()
        if "private" in meta_acc:
            return "Private"
        if "customer" in meta_acc or "patron" in meta_acc:
            return "Customer"
        open_nt = self.metadata.get("open_to_non_tesla")
        t_only = self.metadata.get("tesla_only")
        if self.speed_tier == "supercharger":
            if open_nt:
                return "CCS2 All"
            return "Tesla Only"
        if "tesla" in (self.network or "").lower() or "tesla" in (self.source or "").lower():
            if open_nt:
                return "Type 2 All"
            if t_only:
                return "Tesla Only"
            return "Type 2 All"
        if open_nt is False or t_only:
            return "Private"
        return "Public"

    def badge(self) -> str:
        """Returns standard visual network badge emoji."""
        net = (self.network or "").lower()
        if "tesla" in net or self.speed_tier == "supercharger":
            if self.speed_tier == "ac_destination":
                return "🏢 Tesla Dest"
            return "⚡ Tesla"
        if "chargefox" in net:
            return "🦊 Chargefox"
        if "nrma" in net:
            return "⚡ NRMA"
        if "evx" in net or self.source == "evx":
            return "🟠 EVX"
        if "evie" in net:
            return "🕊️ Evie"
        if "bp" in net or "pulse" in net:
            return "🟢 BP Pulse"
        if "ampcharge" in net or "ampol" in net:
            return "🔴 AmpCharge"
        if "exploren" in net:
            return "⚡ Exploren"
        if self.speed_tier == "ac_destination":
            return f"🔌 {self.network[:10]}" if self.network else "🔌 AC Public"
        return f"🔌 {self.network[:10]}" if self.network else "🔌 Public"

    def get_status(self, threshold_days: int = 90) -> str:
        """
        Calculates the registry freshness and discrepancy status of the station record:
        - 'RENAMED' (Magenta): Upstream discrepancy, name mismatch, or explicitly flagged as renamed.
        - 'UP_TO_DATE' (Green): Present and verified within threshold_days (<= 90 days / 3 months).
        - 'STALE' (Blue): Present but last verification is older than threshold_days (> 90 days) or unverified.
        - 'NOT_IN_JSON' (Orange): Missing from JSON registry.
        """
        meta = self.metadata or {}
        if meta.get("is_renamed") or meta.get("renamed") or meta.get("status") == "RENAMED":
            return "RENAMED"

        # Check views for title discrepancy
        views = self.views or {}
        if len(views) > 1:
            for vname, vdata in views.items():
                if isinstance(vdata, dict):
                    v_title = vdata.get("name") or vdata.get("title") or (vdata.get("tesla_metadata", {}).get("name") if "tesla" in vname else None)
                    if v_title and self.name:
                        t1 = re.sub(r"[^\w\s]", "", self.name.lower()).strip()
                        t2 = re.sub(r"[^\w\s]", "", v_title.lower()).strip()
                        if t1 and t2 and t1 != t2 and not (t1 in t2 or t2 in t1):
                            # Name discrepancy detected across views
                            return "RENAMED"

        # Check verification date
        ver_str = self.last_verified_utc or meta.get("last_verified") or meta.get("last_updated") or meta.get("first_seen")
        if not ver_str:
            for vdata in views.values():
                if isinstance(vdata, dict):
                    v_ver = vdata.get("last_verified") or vdata.get("last_updated") or vdata.get("created_at") or vdata.get("provenance", {}).get("last_verified_utc")
                    if v_ver:
                        ver_str = v_ver
                        break

        if not ver_str:
            return "STALE"

        try:
            clean_iso = ver_str.replace("Z", "+00:00")
            if "+" not in clean_iso and "-" not in clean_iso[10:]:
                clean_iso += "+00:00"
            dt = datetime.fromisoformat(clean_iso)
            now = datetime.now(timezone.utc)
            if (now - dt).days <= threshold_days:
                return "UP_TO_DATE"
            else:
                return "STALE"
        except Exception:
            return "STALE"

    def get_source_urls(self) -> List[Dict[str, str]]:
        """
        Extracts all distinct source URLs across multi-source views and base records.
        Returns list of dicts: [{'name': 'Tesla', 'icon': '⚡', 'label': 'Tesla Find Us', 'url': '...'}, ...]
        """
        results = []
        seen_urls = set()

        def _add(name: str, icon: str, label: str, u: Optional[str]):
            if u and isinstance(u, str) and u.startswith("http") and u not in seen_urls:
                seen_urls.add(u)
                results.append({"name": name, "icon": icon, "label": label, "url": u})

        views = self.views or {}

        # 1. Tesla View
        t_view = views.get("tesla")
        if t_view and isinstance(t_view, dict):
            t_meta = t_view.get("tesla_metadata", {})
            t_url = t_meta.get("findus_url") or t_view.get("location", {}).get("url") or t_view.get("url")
            _add("Tesla", "⚡", "Tesla Find Us", t_url)

        # 2. Chargefox View
        cf_view = views.get("chargefox")
        if cf_view and isinstance(cf_view, dict):
            cf_id = cf_view.get("id")
            cf_url = cf_view.get("url") or (f"https://app.chargefox.com/charge-stations/{cf_id}" if cf_id else None)
            _add("Chargefox", "🦊", "Chargefox App", cf_url)

        # 3. EVX View
        evx_view = views.get("evx")
        if evx_view and isinstance(evx_view, dict):
            evx_url = evx_view.get("url") or evx_view.get("storepoint_url")
            _add("EVX", "🔌", "EVX Portal", evx_url)

        # 4. Evie View
        evie_view = views.get("evie")
        if evie_view and isinstance(evie_view, dict):
            evie_url = evie_view.get("url") or "https://evie.com.au/find-a-charger/"
            _add("Evie", "🕊️", "Evie Networks", evie_url)

        # 5. bp pulse View
        bp_view = views.get("bppulse")
        if bp_view and isinstance(bp_view, dict):
            bp_url = bp_view.get("url")
            _add("BP Pulse", "🟢", "bp pulse Storefinder", bp_url)

        # 6. PlugShare View
        ps_view = views.get("plugshare")
        if ps_view and isinstance(ps_view, dict):
            ps_meta = ps_view.get("plugshare_metadata", {})
            ps_id = ps_meta.get("id") or ps_view.get("id")
            ps_url = ps_meta.get("url") or ps_view.get("url") or (f"https://www.plugshare.com/location/{ps_id}" if ps_id else None)
            _add("PlugShare", "🔌", "PlugShare", ps_url)

        # 7. Base station URL fallback
        if self.url and self.url not in seen_urls:
            label = "Web Link"
            icon = "🔗"
            if "tesla.com" in self.url:
                label = "Tesla Find Us"
                icon = "⚡"
            elif "chargefox.com" in self.url:
                label = "Chargefox App"
                icon = "🦊"
            elif "evie" in self.url:
                label = "Evie Networks"
                icon = "🔌"
            elif "bppulse" in self.url or "bp.com" in self.url:
                label = "bp pulse Storefinder"
                icon = "🟢"
            elif "plugshare.com" in self.url:
                label = "PlugShare"
                icon = "🔌"
            elif "evx" in self.url:
                label = "EVX Portal"
                icon = "🔌"
            _add(self.source.title(), icon, label, self.url)

        return results


class BaseChargerProvider(ABC):
    """Abstract base class for all EV charging data providers."""
    name: str = "base"
    tier: int = 2  # 1 = First-Party Authoritative, 2 = Aggregator

    # BUG-079: the set of speed_tier values this provider's
    # load_cached_registry() can ever produce, or None if unknown/unrestricted.
    # get_all_stations() (chargers_manager.py) uses this to skip providers that
    # provably cannot contribute a record matching an active speed_tier filter -
    # e.g. a --sc query only wants speed_tier="supercharger", and only
    # TeslaProvider and PlugShareProvider (a narrow cross-listing case - see its
    # own POSSIBLE_SPEED_TIERS comment) ever set that tier, so ChargefoxProvider
    # (whose load_cached_registry() can trigger a live, rate-limited Nominatim
    # reverse-geocode call per uncached station), EVXProvider, EvieProvider, and
    # BPPulseProvider can be skipped entirely for that query. Declared
    # per-subclass and verified against each provider's actual speed_tier
    # assignments (grep "speed_tier\s*=" in each *_provider file) - if a
    # provider is ever changed to assign a new speed_tier, this set MUST be
    # updated too, or get_all_stations() will silently drop its matching
    # records under a speed_tier filter. Leave as None (the default) for any
    # provider whose possible tiers aren't confidently known - that disables
    # the skip for it (always loaded), which is always correctness-safe, just
    # not optimized.
    POSSIBLE_SPEED_TIERS: Optional[set] = None

    @abstractmethod
    def search_nearby(self, lat: float, lon: float, radius_km: float = 15.0, filters: Optional[Dict[str, Any]] = None) -> List[UnifiedStationRecord]:
        """Finds stations within radius of coordinates."""
        pass

    @abstractmethod
    def search_by_text(self, query: str, filters: Optional[Dict[str, Any]] = None) -> List[UnifiedStationRecord]:
        """Searches stations by text query (name, suburb, network)."""
        pass

    @abstractmethod
    def get_station_details(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Fetches detailed record for a specific station."""
        pass

    @abstractmethod
    def load_cached_registry(self) -> List[UnifiedStationRecord]:
        """Loads locally cached station records from JSON registry."""
        pass


def detect_station_changes(existing_entry: Dict[str, Any], new_entry: Dict[str, Any]) -> bool:
    """
    Compares existing station entry with new entry to detect significant tariff,
    hardware, or naming changes that warrant archiving the superseded version.
    """
    if not existing_entry or not new_entry:
        return False

    # 1. Tariffs comparison
    ex_tar = existing_entry.get("tariffs", {})
    new_tar = new_entry.get("tariffs", {})
    
    # Flat rate check
    ex_flat = ex_tar.get("per_kwh_gross") if ex_tar.get("per_kwh_gross") is not None else ex_tar.get("per_kwh_flat")
    new_flat = new_tar.get("per_kwh_gross") if new_tar.get("per_kwh_gross") is not None else new_tar.get("per_kwh_flat")
    if ex_flat is not None and new_flat is not None:
        try:
            if round(float(ex_flat), 2) != round(float(new_flat), 2):
                return True
        except Exception:
            pass

    # TOU schedules comparison
    ex_scheds = ex_tar.get("schedule") or ex_tar.get("tesla_members", {}).get("rate_schedules", []) or existing_entry.get("tessie_cost_config", {}).get("rate_schedules", [])
    new_scheds = new_tar.get("schedule") or new_tar.get("tesla_members", {}).get("rate_schedules", []) or new_entry.get("tessie_cost_config", {}).get("rate_schedules", [])
    
    if len(ex_scheds) != len(new_scheds) and len(ex_scheds) > 0:
        return True

    if ex_scheds and new_scheds:
        for s1, s2 in zip(ex_scheds, new_scheds):
            r1 = s1.get("rate_per_kwh") or s1.get("rate_per_kwh_gross") or s1.get("rate")
            r2 = s2.get("rate_per_kwh") or s2.get("rate_per_kwh_gross") or s2.get("rate")
            if r1 is not None and r2 is not None:
                try:
                    if round(float(r1), 2) != round(float(r2), 2):
                        return True
                except Exception:
                    pass
            if (s1.get("start_time") or s1.get("start")) != (s2.get("start_time") or s2.get("start")):
                return True

    # 2. Hardware comparison (stalls, max_power_kw, tier)
    ex_hw = existing_entry.get("hardware", {})
    new_hw = new_entry.get("hardware", {})
    if ex_hw.get("stalls") and new_hw.get("stalls") and ex_hw.get("stalls") != new_hw.get("stalls"):
        return True
    if ex_hw.get("max_power_kw") and new_hw.get("max_power_kw") and ex_hw.get("max_power_kw") != new_hw.get("max_power_kw"):
        return True

    return False


def archive_station_record(
    archive_path: str,
    station_key: str,
    existing_entry: Dict[str, Any],
    now_utc: Optional[str] = None
) -> None:
    """
    Archives a superseded station record to the specified archive JSON registry
    with valid_from, valid_to, and archived_at timestamps for historical invoice matching.
    """
    if not existing_entry:
        return

    import json
    now_utc = now_utc or datetime.now(timezone.utc).isoformat()
    archive_reg: Dict[str, Any] = {}
    if os.path.isfile(archive_path):
        try:
            with open(archive_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    archive_reg = loaded
        except Exception:
            archive_reg = {}

    orig_first_seen = (
        existing_entry.get("valid_from")
        or existing_entry.get("first_seen")
        or existing_entry.get("created_at")
        or existing_entry.get("last_verified")
        or existing_entry.get("last_updated")
        or now_utc
    )

    archived_record = dict(existing_entry)
    archived_record["archived_at"] = now_utc
    archived_record["valid_to"] = now_utc
    archived_record["valid_from"] = orig_first_seen

    if station_key not in archive_reg:
        archive_reg[station_key] = [archived_record]
    elif isinstance(archive_reg[station_key], list):
        archive_reg[station_key].append(archived_record)
    elif isinstance(archive_reg[station_key], dict):
        archive_reg[station_key] = [archive_reg[station_key], archived_record]

    try:
        os.makedirs(os.path.dirname(archive_path), exist_ok=True)
        with open(archive_path, "w", encoding="utf-8") as f:
            json.dump(archive_reg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Warning: Failed to write archive record to {archive_path}: {e}")

