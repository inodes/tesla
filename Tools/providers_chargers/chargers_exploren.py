#!/usr/bin/env python3
"""
Exploren Charger Provider (Tier 1 First-Party)
================================================
First-party provider for the Exploren AC/DC public charging network across Australia.
Connects directly to the official Exploren "Find a Charger" page and its WordPress
AJAX backend:
- Stage 1: Scrapes the base station list (id, name, GPS, current_type, max_power)
  embedded as a JSON array inside the page's own inline Google Maps initMap() script.
- Stage 2: Batches per-station detail queries (full address, individual charger unit
  breakdown, optional pricing, site-specific access notes) against the site's
  wp-admin/admin-ajax.php `exploren_get_location_detail` action, in parallel.
- Caches locally in Tessie/exploren_chargers.json with historical archiving in
  Tessie/exploren_chargers_archived.json.

REQ-086 (AGENTS.local.md): user asked to add Exploren as a first-party network -
charger_find.py's own --network help text already referenced Exploren as an example
of a PlugShare-aggregated sub-brand, but Exploren was never a dedicated provider of
its own; this closes that gap the same way EVX/Evie/bp pulse/AmpCharge did.
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

EXPLOREN_FIND_A_CHARGER_URL = "https://exploren.com.au/find-a-charger/"
EXPLOREN_AJAX_URL = "https://exploren.com.au/wp-admin/admin-ajax.php"

EXPLOREN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Referer": EXPLOREN_FIND_A_CHARGER_URL,
    "Origin": "https://exploren.com.au",
    "Accept": "*/*",
}

AU_STATE_CODES = ("NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT")
_ADDRESS_RE = re.compile(
    r"^(?P<street>.*?),\s*(?P<suburb>[A-Za-z][A-Za-z .'\-]*?)\s+"
    r"(?P<state>" + "|".join(AU_STATE_CODES) + r")\s+(?P<postcode>\d{4})"
    r"(?:,\s*Australia)?\s*$"
)


def _resolve_exploren_registry_path() -> str:
    """Finds path to exploren_chargers.json in Tessie data directory or local fallback."""
    candidates = []
    tessie_home = None
    try:
        from lib.tessie_api_common import resolve_tessie_data_home
        tessie_home = resolve_tessie_data_home()
    except Exception:
        pass
    if tessie_home:
        candidates.append(os.path.join(tessie_home, "exploren_chargers.json"))

    candidates.append(os.path.join(_repo_root, "Tessie", "exploren_chargers.json"))
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
                    candidates.insert(0, os.path.join(td_exp, "exploren_chargers.json"))
        except Exception:
            pass

    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0] if candidates else os.path.join(_repo_root, "Tessie", "exploren_chargers.json")


def _extract_locations_and_nonce(html: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Extracts the base station list and AJAX nonce embedded in the find-a-charger
    page's own markup:
    - `var locations = [...]` (inside the page's inline initMap() script) is a
      plain JSON array of {id, name, lat, lng, current_type, max_power} - extracted
      by brace-counting from the `[` immediately after `var locations = ` to its
      matching `]`, since the array is far too long for a simple non-greedy regex
      to safely bound.
    - The nonce is scraped from the page's own `formData.append('nonce', '...')`
      call - it's a WordPress AJAX nonce (short-lived by WP convention, but
      observed stable across multiple fetches in practice), so it must be
      re-scraped per sync rather than hardcoded.
    """
    locations: List[Dict[str, Any]] = []
    marker = "var locations = "
    idx = html.find(marker)
    if idx != -1:
        start = html.find("[", idx)
        if start != -1:
            depth = 0
            end = -1
            for k in range(start, len(html)):
                c = html[k]
                if c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
                    if depth == 0:
                        end = k + 1
                        break
            if end != -1:
                try:
                    locations = json.loads(html[start:end])
                except Exception:
                    locations = []

    nonce = None
    m = re.search(r"nonce['\"]\s*,\s*['\"]([a-f0-9]+)['\"]", html)
    if m:
        nonce = m.group(1)

    return locations, nonce


def _parse_address(raw_address: str, lat: float, lon: float) -> Dict[str, str]:
    """Splits an Exploren detail-fragment address ('316 Victoria Rd, Rydalmere NSW 2116, Australia')
    into street/suburb/state/postcode, falling back to GPS-based state resolution
    when the address is missing or doesn't match the expected 'STREET, SUBURB STATE POSTCODE'
    shape (e.g. a station with no detail response yet)."""
    if raw_address:
        m = _ADDRESS_RE.match(raw_address.strip())
        if m:
            return {
                "street": m.group("street").strip(),
                "suburb": m.group("suburb").strip(),
                "state": m.group("state"),
                "postcode": m.group("postcode"),
            }
    return {
        "street": raw_address or "",
        "suburb": "",
        "state": resolve_au_state(lat, lon) if lat and lon else "",
        "postcode": "",
    }


def _strip_tags(s: str) -> str:
    """Strips HTML tags and collapses whitespace/entities from an Exploren detail
    fragment's free-text blocks (access notes, etc.) down to plain text."""
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&mdash;", "-").replace("&nbsp;", " ").replace("&#038;", "&")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def parse_exploren_detail_html(html: str) -> Dict[str, Any]:
    """
    Parses the HTML fragment returned by the `exploren_get_location_detail` AJAX
    action into a plain dict: name, address, a list of individual charger units
    (id + type + power_kw each), and, when present, a $/kWh rate and a flat
    connection fee (both optional - observed present for some stations, e.g.
    Toronto Commercial Centre Carpark, and entirely absent for others, e.g.
    Bunnings Rydalmere, which lists no pricing block at all).
    """
    name_m = re.search(r'<h2[^>]*>(.*?)</h2>', html, re.S)
    addr_m = re.search(r'<p class="mb-2">(.*?)</p>', html, re.S)
    name = _strip_tags(name_m.group(1)) if name_m else ""
    address = _strip_tags(addr_m.group(1)) if addr_m else ""

    units = []
    for cm in re.finditer(r'Charger #(\d+)\s*&mdash;\s*(AC|DC)\s*([\d.]+)\s*kW', html):
        units.append({
            "unit_id": cm.group(1),
            "current_type": cm.group(2).lower(),
            "power_kw": float(cm.group(3)),
        })

    # The last "py-4 border-top border-light-gray" block is either a $/kWh
    # pricing block ("Paid Charger: ... $X per kWh ... $Y Connection Fee") or a
    # free-text site-specific access note - the "Operational Instructions" /
    # "How to Charge Your Car" block that always precedes it is skipped by
    # simply taking the LAST split segment rather than trying to bound each
    # block with a nested-div-safe regex.
    segments = html.split('<div class="py-4 border-top border-light-gray">')
    per_kwh = None
    connection_fee = None
    access_note = ""
    if len(segments) >= 3:
        tail = segments[-1]
        if "Paid Charger" in tail:
            pm = re.search(r"\$([\d.]+)\s*per\s*kWh", tail)
            fm = re.search(r"\$([\d.]+)\s*Connection Fee", tail)
            if pm:
                per_kwh = float(pm.group(1))
            if fm:
                connection_fee = float(fm.group(1))
        else:
            access_note = _strip_tags(tail)

    return {
        "name": name,
        "address": address,
        "units": units,
        "per_kwh": per_kwh,
        "connection_fee": connection_fee,
        "access_note": access_note,
    }


def parse_exploren_location_record(loc: Dict[str, Any], detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Transforms a base list entry (+ optional parsed detail fragment) into the
    canonical multi-view JSON dictionary shared by every provider."""
    loc_id = str(loc.get("id", ""))
    lat = float(loc.get("lat") or 0.0)
    lon = float(loc.get("lng") or 0.0)
    raw_type = str(loc.get("current_type", "")).lower()
    max_power_kw = round(float(loc.get("max_power") or 0.0) / 1000.0, 1)

    name = (detail or {}).get("name") or loc.get("name") or f"Exploren Station #{loc_id}"
    raw_address = (detail or {}).get("address", "")
    addr_parts = _parse_address(raw_address, lat, lon)

    units = (detail or {}).get("units") or []
    stalls_total = len(units) if units else 2
    unit_powers = [u["power_kw"] for u in units if u.get("power_kw")]
    if unit_powers:
        max_power_kw = max(unit_powers)

    is_dc = "dc" in raw_type
    is_ac = "ac" in raw_type
    speed_tier = "dc_fast" if (is_dc and max_power_kw >= 25.0) else "ac_destination"

    plugs = []
    if any(u.get("current_type") == "dc" for u in units) or (is_dc and not units):
        plugs.append("CCS2")
    if any(u.get("current_type") == "ac" for u in units) or (is_ac and not units):
        plugs.append("Type 2")
    if not plugs:
        plugs = ["CCS2"] if is_dc else ["Type 2"]

    per_kwh = (detail or {}).get("per_kwh")
    connection_fee = (detail or {}).get("connection_fee")
    pricing_model = "flat" if per_kwh is not None else "unknown"
    gross_r, net_r, tax_r, tax_n, tax_pct = compute_tax_breakdown(per_kwh, "Australia")
    rate_range = ""
    if gross_r is not None:
        rate_range = f"${gross_r:.2f}/kWh"
        if connection_fee:
            rate_range += f" + ${connection_fee:.2f} connection fee"

    access_note = (detail or {}).get("access_note", "")

    full_address = raw_address or ", ".join(p for p in [addr_parts["street"], addr_parts["suburb"]] if p)

    now_utc = datetime.now(timezone.utc).isoformat()

    return {
        "id": f"Exploren_{loc_id}",
        "name": name,
        "keywords": [name, "Exploren", addr_parts["suburb"]],
        "location": {
            "address": full_address,
            "suburb": addr_parts["suburb"],
            "state": addr_parts["state"],
            "postcode": addr_parts["postcode"],
            "country": "Australia",
            "lat": lat,
            "lon": lon,
            "timezone": "Australia/Sydney",
        },
        "hardware": {
            "max_power_kw": max_power_kw,
            "stalls": stalls_total,
            "connector_types": plugs,
            "hardware_models": ["Exploren Charger"],
            "units": units,
        },
        "tariffs": {
            "pricing_model": pricing_model,
            "per_kwh_gross": gross_r,
            "per_kwh_net": net_r,
            "rate_range": rate_range,
            "connection_fee_gross": connection_fee,
            "currency": "AUD",
        },
        "provenance": {
            "source_ids": [loc_id],
            "url": f"https://exploren.com.au/find-a-charger/?charger_location={loc_id}",
            "last_verified_utc": now_utc,
        },
        "metadata": {
            "current_type": raw_type,
            "access_note": access_note,
        },
        "views": {},
        "_speed_tier": speed_tier,
    }


class ExplorenProvider(BaseChargerProvider):
    """
    Tier 1 First-Party Authoritative Provider for the Exploren AC/DC charging
    network across Australia (Bunnings, shopping centres, carparks, etc.).
    """
    name: str = "Exploren"
    tier: int = 1
    # Verified via the base station list's own `current_type` field, which is
    # exclusively "ac", "dc", or "ac/dc" - never a Tesla-specific value.
    POSSIBLE_SPEED_TIERS = {"dc_fast", "ac_destination"}

    def __init__(self, registry_path: Optional[str] = None):
        self.registry_path = registry_path or _resolve_exploren_registry_path()
        self._cache: Optional[List[UnifiedStationRecord]] = None

    def fetch_all_remote(self, max_workers: int = 8, timeout: int = 15) -> List[UnifiedStationRecord]:
        """
        Fetches all live Exploren stations across Australia:
        1. Scrape the find-a-charger page for the base station list (~640
           stations: id, name, GPS, current_type, max_power) and a fresh AJAX
           nonce.
        2. Query per-station detail (address, individual charger units,
           optional pricing, access note) in parallel via the
           `exploren_get_location_detail` admin-ajax action.
        """
        req_page = urllib.request.Request(EXPLOREN_FIND_A_CHARGER_URL, headers=EXPLOREN_HEADERS)
        with urllib.request.urlopen(req_page, timeout=timeout) as resp:
            page_html = resp.read().decode("utf-8", errors="replace")

        locations, nonce = _extract_locations_and_nonce(page_html)
        if not locations:
            return []

        detail_map: Dict[Any, Dict[str, Any]] = {}

        def _fetch_single_detail(loc_id: Any) -> Tuple[Any, Optional[Dict[str, Any]]]:
            # BUG-113 (AGENTS.local.md, 2026-09-16): a failed/timed-out detail
            # fetch here used to silently return None on the FIRST try, no
            # retry - and every downstream consumer of `units=[]` falls back
            # to a hardcoded "2 stalls, no connector breakdown" guess
            # (parse_exploren_location_record's `len(units) if units else 2`).
            # Caught concretely via the user's own report: Bankstown Central
            # showed "2 Stalls | Up to 160 kW" in the app, but Exploren's own
            # page lists 4 independent "Chargers (4)" - #10968/10969/9098/9099,
            # all confirmed DC 160kW on a fresh direct fetch, proving the
            # parser itself is correct and this was a transient fetch failure
            # baked into the cache, not a parsing bug. Across the full cached
            # registry, 499/637 stations (78%) show exactly stalls==2 - far
            # more than plausible as all being genuinely single-pedestal
            # sites, meaning this silent fallback was hit at scale. A couple
            # of retries here, plus surfacing the still-failed count from
            # sync(), turns "quietly wrong for hundreds of stations" into
            # "actually retried, and here's what's still unknown."
            if not nonce:
                return loc_id, None
            last_err = None
            for attempt in range(3):
                try:
                    form = urllib.parse.urlencode({
                        "action": "exploren_get_location_detail",
                        "location_id": loc_id,
                        "nonce": nonce,
                    }).encode("utf-8")
                    req = urllib.request.Request(EXPLOREN_AJAX_URL, data=form, headers=EXPLOREN_HEADERS)
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        payload = json.load(r)
                    if payload.get("success"):
                        return loc_id, parse_exploren_detail_html(payload.get("data", ""))
                    last_err = ValueError(f"success=False for location {loc_id}")
                except Exception as e:
                    last_err = e
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
            return loc_id, None

        all_ids = [l.get("id") for l in locations if l.get("id") is not None]
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for loc_id, d_obj in executor.map(_fetch_single_detail, all_ids):
                if d_obj:
                    detail_map[loc_id] = d_obj

        # BUG-113: surface how many detail fetches never succeeded even after
        # 3 attempts, instead of letting them vanish into a silent "2 stalls,
        # no breakdown" fallback - those specific stations are named so a
        # re-run or a targeted --sync-exploren retry can be judged on real
        # information rather than guessing at the scale of the gap.
        failed_ids = [str(lid) for lid in all_ids if lid not in detail_map]
        if failed_ids:
            print(f"⚠ Exploren: detail fetch failed for {len(failed_ids)}/{len(all_ids)} station(s) after 3 attempts each - "
                  f"these fall back to a generic 2-stall estimate: {', '.join(failed_ids[:15])}"
                  f"{' ...' if len(failed_ids) > 15 else ''}")

        records: List[UnifiedStationRecord] = []
        for loc in locations:
            detail = detail_map.get(loc.get("id"))
            parsed = parse_exploren_location_record(loc, detail)
            meta = parsed.get("metadata", {})
            loc_data = parsed.get("location", {})
            hw_data = parsed.get("hardware", {})
            tariffs = parsed.get("tariffs", {})
            prov = parsed.get("provenance", {})

            rec = UnifiedStationRecord(
                key=parsed["id"],
                name=parsed["name"],
                network="Exploren",
                operator_name="Exploren",
                address=loc_data.get("address", ""),
                suburb=loc_data.get("suburb", ""),
                state=loc_data.get("state", ""),
                postcode=loc_data.get("postcode", ""),
                country=loc_data.get("country", "Australia"),
                lat=loc_data.get("lat", 0.0),
                lon=loc_data.get("lon", 0.0),
                timezone=loc_data.get("timezone", "Australia/Sydney"),
                speed_tier=parsed.get("_speed_tier", "ac_destination"),
                max_power_kw=hw_data.get("max_power_kw", 0.0),
                stalls_total=hw_data.get("stalls", 2),
                stalls_available=None,
                plugs=hw_data.get("connector_types", ["Type 2"]),
                tethered=False,
                hardware_model=hw_data.get("hardware_models", ["Exploren Charger"])[0] if hw_data.get("hardware_models") else "Exploren Charger",
                pricing_model=tariffs.get("pricing_model", "unknown"),
                current_rate_per_kwh=tariffs.get("per_kwh_gross"),
                rate_range=tariffs.get("rate_range", ""),
                schedule=[],
                idle_fee_per_min=None,
                currency=tariffs.get("currency", "AUD"),
                parking_fee=(f"${tariffs['connection_fee_gross']:.2f} connection fee" if tariffs.get("connection_fee_gross") else None),
                tier=1,
                source="exploren",
                source_id=str(prov.get("source_ids", [""])[0]),
                url=prov.get("url", EXPLOREN_FIND_A_CHARGER_URL),
                last_verified_utc=prov.get("last_verified_utc", ""),
                metadata=meta,
                amenities={},
                keywords=parsed.get("keywords", []),
                views={}
            )
            records.append(rec)

        return records

    def sync(self, save_path: Optional[str] = None) -> List[UnifiedStationRecord]:
        """Fetches remote Exploren stations and saves them to the local JSON registry, archiving changes."""
        target_path = save_path or self.registry_path
        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
        archive_path = os.path.join(os.path.dirname(target_path), "exploren_chargers_archived.json")
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
            "source": "exploren_wordpress_ajax",
            "stations": stations_dict
        }

        atomic_write_json(target_path, payload)

        self._cache = records
        return records

    def reverify_station(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Re-fetches and updates a single Exploren station, archiving changes."""
        clean_id = str(station_id).replace("Exploren_", "").strip()
        all_recs = self.fetch_all_remote()
        matched = None
        for r in all_recs:
            if str(r.source_id) == clean_id or r.key == f"Exploren_{clean_id}" or r.key == station_id:
                matched = r
                break
        if matched:
            self.sync()
            return matched
        return None

    def load_cached_registry(self) -> List[UnifiedStationRecord]:
        """Loads locally cached Exploren stations from JSON registry."""
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

                max_pwr = float(hw_data.get("max_power_kw") or 0.0)
                # BUG-112 (AGENTS.local.md, 2026-09-16): this used to re-derive
                # speed_tier from meta.get("current_type", "") - a metadata key
                # that sync() never actually writes (there is no top-level
                # "metadata" object in the cached JSON at all), so is_dc was
                # unconditionally False and EVERY cached Exploren station loaded
                # as "ac_destination", discarding the correct value sync()
                # already computed and stored at hardware.speed_tier (e.g.
                # Bunnings Gladesville: 2x CCS2, 120kW, on-disk speed_tier
                # "dc_fast" - but every load showed it, and 148 other real DC
                # stations, as AC). Trust the stored field directly instead of
                # re-deriving it from data that was never there.
                speed_tier = hw_data.get("speed_tier") or ("dc_fast" if max_pwr >= 25.0 else "ac_destination")

                rec = UnifiedStationRecord(
                    key=s_id,
                    name=s_data.get("name", s_id),
                    network=s_data.get("network", "Exploren"),
                    operator_name=s_data.get("operator", "Exploren"),
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
                    stalls_total=int(hw_data.get("stalls") or 2),
                    stalls_available=hw_data.get("stalls_available"),
                    plugs=hw_data.get("connector_types", ["Type 2"]),
                    tethered=hw_data.get("tethered", False),
                    hardware_model=hw_data.get("hardware_models", ["Exploren Charger"])[0] if hw_data.get("hardware_models") else "Exploren Charger",
                    pricing_model=tariffs.get("pricing_model", "unknown"),
                    current_rate_per_kwh=tariffs.get("per_kwh_gross"),
                    rate_range=tariffs.get("rate_range", ""),
                    schedule=tariffs.get("schedule") or [],
                    idle_fee_per_min=tariffs.get("idle_fee_per_min_gross"),
                    parking_fee=(f"${tariffs['connection_fee_gross']:.2f} connection fee" if tariffs.get("connection_fee_gross") else None),
                    currency=tariffs.get("currency", "AUD"),
                    tier=1,
                    source="exploren",
                    source_id=prov.get("source_ids", [""])[0] if prov.get("source_ids") else s_id,
                    url=prov.get("url", EXPLOREN_FIND_A_CHARGER_URL),
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
        """Finds Exploren stations within radius_km of coordinates."""
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
        """Searches Exploren stations by keyword match on name, suburb, address."""
        if not query or not query.strip():
            return []

        q_clean = query.strip().lower()
        all_stations = self.load_cached_registry()
        matched = []

        for st in all_stations:
            searchable = f"{st.name} {st.suburb} {st.address} {st.state} {st.postcode} {st.network} {' '.join(st.keywords)}".lower()
            if q_clean in searchable:
                matched.append(st)

        return filter_station_records(matched, filters)

    def get_station_details(self, station_id: str) -> Optional[UnifiedStationRecord]:
        """Finds specific station by key or Exploren location ID."""
        all_stations = self.load_cached_registry()
        s_clean = str(station_id).strip().lower()

        for st in all_stations:
            if st.key.lower() == s_clean or st.source_id.lower() == s_clean:
                return st

        return None
