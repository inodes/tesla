#!/usr/bin/env python3
"""
station_facilities_common.py - Persisted Station Facilities Registry & Quality Scoring Engine
=============================================================================================
Maintains a local JSON cache of verified nearby amenities, food, restrooms, and shops
around EV charging stations (primarily Superchargers), with a 0-10 quality scoring
algorithm that allows drivers to instantly see which chargers have good vs bad facilities.

Stored in Tessie data home (station_facilities.json) for instant offline pullup.
"""

import os
import json
import time
from datetime import datetime, timezone
from typing import Optional

try:
    from tessie_api_common import resolve_tessie_data_home
except ImportError:
    try:
        from lib.tessie_api_common import resolve_tessie_data_home
    except ImportError:
        try:
            from Tools.lib.tessie_api_common import resolve_tessie_data_home
        except ImportError:
            def resolve_tessie_data_home():
                return os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie")

try:
    from geo_common import haversine_distance_km
except ImportError:
    try:
        from lib.geo_common import haversine_distance_km
    except ImportError:
        try:
            from Tools.lib.geo_common import haversine_distance_km
        except ImportError:
            import math
            def haversine_distance_km(lat1, lon1, lat2, lon2):
                r = 6371.0
                dlat = math.radians(lat2 - lat1)
                dlon = math.radians(lon2 - lon1)
                a = (math.sin(dlat / 2.0) ** 2 +
                     math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
                     math.sin(dlon / 2.0) ** 2)
                return r * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def resolve_station_physical_coords(station: any) -> tuple:
    """
    Resolves the authoritative physical stall coordinates for a station according to
    the hierarchy of truth:
    a) supercharge.info GPS coords (for Tesla Superchargers) -> highest fidelity physical stall location
    b) OpenStreetMap (OSM) GPS coords (if available in metadata) -> community mapped stall node
    c) Source / Tesla GPS coords (station.lat, station.lon) -> compulsory baseline (driveway / entrance)

    Returns:
        (lat, lon, source_name)
    """
    if station is None:
        return None, None, "Unknown"

    # 1. Base coordinates
    base_lat = getattr(station, "lat", None)
    base_lon = getattr(station, "lon", None)
    if isinstance(station, dict):
        base_lat = station.get("lat") if station.get("lat") is not None else station.get("latitude")
        base_lon = station.get("lon") if station.get("lon") is not None else station.get("longitude")
        if (base_lat is None or base_lon is None) and "location" in station and isinstance(station["location"], dict):
            base_lat = station["location"].get("lat")
            base_lon = station["location"].get("lon")

    # 2. Extract station attributes
    st_source = getattr(station, "source", "") if not isinstance(station, dict) else station.get("source", "")
    st_tier = getattr(station, "speed_tier", "") if not isinstance(station, dict) else (station.get("speed_tier") or (station.get("hardware") or {}).get("speed_tier", ""))
    st_name = getattr(station, "name", "") if not isinstance(station, dict) else station.get("name", "")
    st_meta = getattr(station, "metadata", {}) if not isinstance(station, dict) else station.get("metadata", {})
    if not isinstance(st_meta, dict):
        st_meta = {}

    st_network = getattr(station, "network", "") if not isinstance(station, dict) else station.get("network", "")
    st_views = getattr(station, "views", {}) if not isinstance(station, dict) else station.get("views", {})

    # a) Check supercharge.info if Tesla Supercharger
    is_supercharger = (
        st_tier == "supercharger"
        or "supercharger" in str(st_name).lower()
        or ("tesla" in str(st_network).lower() and st_tier == "supercharger")
        or ("tesla" in str(st_source).lower() and st_tier == "supercharger")
        or (isinstance(st_views, dict) and "tesla" in st_views and st_tier == "supercharger")
    )
    if is_supercharger:
        try:
            try:
                from lib.supercharge_common import find_supercharge_site
            except ImportError:
                try:
                    from supercharge_common import find_supercharge_site
                except ImportError:
                    from Tools.lib.supercharge_common import find_supercharge_site
            loc_id = st_meta.get("location_id") or st_meta.get("short_name") or getattr(station, "source_id", None) or (station.get("source_id") if isinstance(station, dict) else None)
            sc_site = find_supercharge_site(str(st_name), base_lat, base_lon, location_id=loc_id)
            if sc_site and sc_site.get("gps"):
                sc_lat = sc_site["gps"].get("latitude")
                sc_lon = sc_site["gps"].get("longitude")
                if sc_lat is not None and sc_lon is not None:
                    return float(sc_lat), float(sc_lon), "supercharge.info"
        except Exception:
            pass

    # b) Check OSM coordinates
    osm_lat = st_meta.get("osm_lat")
    osm_lon = st_meta.get("osm_lon")
    if osm_lat is not None and osm_lon is not None:
        try:
            return float(osm_lat), float(osm_lon), "OpenStreetMap"
        except (ValueError, TypeError):
            pass

    # c) Base / Source coordinates
    src_label = f"{st_source.capitalize()} GPS" if st_source else "Source GPS"
    return (float(base_lat) if base_lat is not None else None,
            float(base_lon) if base_lon is not None else None,
            src_label)


def compute_facility_score(pois: list) -> dict:
    """
    Computes an objective, EV-driver focused facility quality score (0.0 to 10.0)
    and categorical rating based on nearby amenities.

    Key considerations for EV drivers during a 15-45 minute charging session:
    1. 🚻 Restrooms & Services (Essential): up to 3.0 pts
    2. 🍔 Dining & Food Options: up to 2.5 pts
    3. ☕ Cafes & Coffee: up to 2.0 pts
    4. 🛒 Supermarkets & Groceries: up to 1.5 pts
    5. 🛍️ Retail & Leisure: up to 1.0 pts

    Closer venues (<= 100m) receive full weight; 100m-250m receive 0.8x weight;
    venues > 250m receive 0.5x weight.
    """
 # Exclude charging station self-matches
    filtered_pois = [
        p for p in (pois or [])
        if not (p.get("category", "").lower().startswith("charging") or "supercharger" in p.get("name", "").lower() or p.get("name", "").lower().startswith("tesla super"))
    ]
    pois = filtered_pois
    if not pois:
        return {
            "score": 0.0,
            "rating": "Sparse",
            "tier_color": "🔴",
            "badge": "🔴 Sparse (< 3.0/10) · No amenities detected",
            "categories": {},
            "chips": "—",
            "total_count": 0,
            "doorstep_count": 0,
            "doorstep_100m": [],
            "immediate_doorstep": [],
            "closest_poi": None,
        }

 # Group POIs by category group
    grouped = {}
    for p in pois:
        grp = p.get("group", "Other Facilities")
        if grp not in grouped:
            grouped[grp] = []
        grouped[grp].append(p)

    def group_pts(grp_name, max_pts, per_item_base):
        items = grouped.get(grp_name, [])
        if not items:
            return 0.0
        tot = 0.0
        for item in items:
            dist = item.get("dist_m", 200.0)
            weight = 1.0 if dist <= 100 else (0.8 if dist <= 250 else 0.5)
            tot += per_item_base * weight
        return min(max_pts, tot)

    pts_restroom = group_pts("Amenities & Services", 3.0, 1.5)
    pts_dining = group_pts("Dining & Fast Food", 2.5, 1.0)
    pts_coffee = group_pts("Cafes & Coffee", 2.0, 1.0)
    pts_groceries = group_pts("Supermarkets & Groceries", 1.5, 1.0)
    pts_retail = group_pts("Retail & Shopping", 0.7, 0.4)
    pts_leisure = group_pts("Leisure & Entertainment", 0.3, 0.3)

    # REQ-099 (2026-09-22): the 6 groups above are the only ones this
    # score has ever counted. That was fine while classify_poi_group()
    # only ever produced those 6 (plus a catch-all "Other Facilities"
    # that was never scored either, by design). REQ-099 gave it several
    # new, real groups (Wineries & Breweries, Attractions & Culture,
    # Sports & Fitness, Healthcare, Lodging & Hotels, Boating, Historic
    # Sites, Automotive & Parking, Health & Beauty, Offices, Trades &
    # Craft Services) - without this, a station could correctly DETECT
    # and DISPLAY a real, nearby venue (e.g. Cassegrain Winery, 85m) in
    # its POI list and still badge itself "Sparse (0.0/10) · —" right
    # above it, which is a confusing, self-contradicting result. These
    # don't get the same dedicated per-group weight as the original 6
    # (they're generally less relevant to a 15-45 minute charging stop
    # than food/coffee/groceries/restrooms), just a modest shared pool
    # so a real amenity here is never worth a flat, misleading zero.
    other_scored_groups = (
        "Wineries & Breweries", "Attractions & Culture", "Lodging & Hotels",
        "Sports & Fitness", "Healthcare", "Boating, Wharves & Ramps",
        "Historic Sites", "Automotive & Parking", "Health & Beauty",
        "Offices & Professional Services", "Trades & Craft Services",
    )
    pts_other = 0.0
    for grp in other_scored_groups:
        pts_other += group_pts(grp, 1.5, 0.5)
    pts_other = min(1.5, pts_other)

    # Universal detection: Shopping centres, town centres, supermarkets, service plazas, and multi-category retail hubs
    commercial_categories = {"Dining & Fast Food", "Cafes & Coffee", "Supermarkets & Groceries", "Retail & Shopping"}
    active_commercial_groups = set(p.get("group") for p in pois if p.get("group") in commercial_categories)

    has_commercial_hub = (
        len(pois) >= 5 and len(active_commercial_groups) >= 3
    ) or any(
        (p.get("tags") or {}).get("landuse") in ("retail", "commercial")
        or (p.get("tags") or {}).get("shop") in ("mall", "supermarket")
        or p.get("group") == "Supermarkets & Groceries"
        or "supermarket" in (p.get("category") or "").lower()
        or any(s in (p.get("name") or "").lower() for s in (
            "town centre", "town center", "shopping centre", "shopping center",
            "shopping plaza", "shopping mall", "homemaker centre", "marketplace",
            "service centre", "service center", "roadhouse", "travel centre", "travel center"
        ))
        for p in pois
    )
    if pts_restroom < 2.0 and has_commercial_hub:
        pts_restroom = max(pts_restroom, 2.4)

    raw_score = pts_restroom + pts_dining + pts_coffee + pts_groceries + pts_retail + pts_leisure + pts_other
    score = round(min(10.0, raw_score), 1)

 # Determine Rating Tier
    if score >= 8.0:
        rating = "Excellent"
        tier_color = "🟢"
    elif score >= 5.5:
        rating = "Good"
        tier_color = "🟡"
    elif score >= 3.0:
        rating = "Basic"
        tier_color = "🟠"
    else:
        rating = "Sparse"
        tier_color = "🔴"

 # Emoji summary chips for available categories
    chips_list = []
    if "Amenities & Services" in grouped or (has_commercial_hub and pts_restroom > 0):
        chips_list.append("🚻")
    if "Cafes & Coffee" in grouped:
        chips_list.append("☕")
    if "Dining & Fast Food" in grouped:
        chips_list.append("🍔")
    if "Supermarkets & Groceries" in grouped:
        chips_list.append("🛒")
    if "Retail & Shopping" in grouped:
        chips_list.append("🛍️")
    if "Lodging & Hotels" in grouped:
        chips_list.append("🏨")
    if "Leisure & Entertainment" in grouped:
        chips_list.append("🎯")
    if "Wineries & Breweries" in grouped:
        chips_list.append("🍷")
    if "Attractions & Culture" in grouped:
        chips_list.append("📸")
    if "Sports & Fitness" in grouped:
        chips_list.append("🏋️")

    chips_str = "".join(chips_list) if chips_list else "—"
    badge = f"{tier_color} {rating} ({score:.1f}/10) · {chips_str}"

    cat_summary = {}
    for grp_name, items in grouped.items():
        cat_summary[grp_name] = [f"{it['name']} ({it['dist_m']:.0f}m)" for it in items[:6]]

 # Immediate Doorstep amenities (<= 100m, ~1-2 min walk right by the stalls)
    doorstep_pois = [p for p in pois if p.get("dist_m", 999.0) <= 100.0]
    doorstep_summary = [f"{p['name']} ({p.get('dist_m', 0):.0f}m)" for p in doorstep_pois]
    immediate_doorstep = [
        {
            "name": p["name"],
            "category": p.get("category", "amenity"),
            "group": p.get("group", "Other Facilities"),
            "emoji": p.get("emoji", "📍"),
            "dist_m": p.get("dist_m", 0.0)
        }
        for p in doorstep_pois
    ]
    closest_poi = pois[0] if pois else None

    return {
        "score": score,
        "rating": rating,
        "tier_color": tier_color,
        "badge": badge,
        "categories": cat_summary,
        "chips": chips_str,
        "total_count": len(pois),
        "doorstep_count": len(doorstep_pois),
        "doorstep_100m": doorstep_summary,
        "immediate_doorstep": immediate_doorstep,
        "closest_poi": f"{closest_poi['name']} ({closest_poi['dist_m']:.0f}m)" if closest_poi else None,
    }


# BUG-143: cache staleness TTLs (days) for get_station_facilities(). A
# zero-POI ("nothing nearby") result gets a much shorter TTL than a
# populated one, since a new place appearing nearby is exactly what
# silently invalidates a "sparse" answer, and is the failure mode this
# bug was found from (a 6-day-old empty result blocked a fresh check
# indefinitely, with no time-based expiry at all before this).
FACILITIES_CACHE_TTL_DAYS = 30
FACILITIES_EMPTY_CACHE_TTL_DAYS = 7

# REQ-099: any cached record older than this is stale regardless of the
# TTLs above - it predates the classify_poi_group()/query_overpass_pois()
# rewrite and may be holding an old, now-wrong group/emoji/score. Bump
# this again if classify_poi_group()'s category logic changes further.
CATEGORY_SCHEMA_CUTOFF = datetime(2026, 9, 22, 8, 35, 11, tzinfo=timezone.utc)


class FacilitiesRegistry:
    """
    Manages local JSON storage and query resolution for charging station facilities.
    """

    def __init__(self, data_home: str = None):
        self.data_home = data_home or resolve_tessie_data_home()
        tools_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        repo_root = os.path.dirname(tools_dir)
        self.candidate_paths = [
            os.path.join(self.data_home, "station_facilities.json"),
            os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie/station_facilities.json"),
            os.path.join(repo_root, "resources", "station_facilities.json")
        ]
 # Remove duplicate paths while preserving order
        seen = set()
        self.candidate_paths = [p for p in self.candidate_paths if not (p in seen or seen.add(p))]
        self.file_path = self.candidate_paths[0]
        self._cache = None

    def load(self) -> dict:
        if self._cache is not None:
            return self._cache
        for p in self.candidate_paths:
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        self._cache = json.load(f)
                        self.file_path = p
                        return self._cache
                except Exception:
                    continue
        self._cache = {}
        return self._cache

    def save(self) -> bool:
        if self._cache is None:
            return False
        for p in self.candidate_paths:
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                tmp = f"{p}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._cache, f, indent=2, ensure_ascii=False)
                os.replace(tmp, p)
                self.file_path = p
                return True
            except Exception:
                continue
        return False

    def _normalize_key(self, key_or_name: str) -> str:
        if not key_or_name:
            return ""
        s = key_or_name.strip().lower()
        for prefix in ("tesla_", "chargefox_", "plugshare_", "evie_", "evx_", "bppulse_", "ampcharge_", "supercharger_"):
            if s.startswith(prefix):
                s = s[len(prefix):]
        import re
        s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
        return s

    def _find_existing_key(self, station_key: str = None, station_name: str = None) -> Optional[str]:
        """Finds the dict key an existing cached record for this station is stored
        under, if any - exact key match first, then normalized key/name match
        (so a legacy provider-prefixed key like "Tesla_X" is still found for an
        unprefixed lookup key "X", and vice versa). Returns None if no record
        exists yet. Shared by get_station_facilities() (read) and
        store_station_facilities() (write, BUG-144) so a refresh always updates
        the one existing record instead of creating a differently-keyed duplicate.
        """
        data = self.load()
        if station_key and station_key in data:
            return station_key
        norm_key = self._normalize_key(station_key)
        norm_name = self._normalize_key(station_name)
        for k, v in data.items():
            nk = self._normalize_key(k)
            nv_name = self._normalize_key(v.get("station_name", ""))
            if norm_key and nk == norm_key:
                return k
            if norm_name and (nk == norm_name or nv_name == norm_name):
                return k
        return None

    def get_station_facilities(self, station_key: str = None, station_name: str = None, lat: float = None, lon: float = None) -> dict:
        """Looks up cached facilities record for a station by key, name, or proximity."""
        data = self.load()
        res = None
        if station_key and station_key in data:
            res = data[station_key]

        if not res:
            norm_key = self._normalize_key(station_key)
            norm_name = self._normalize_key(station_name)

            for k, v in data.items():
                if k == station_key:
                    res = v
                    break
                nk = self._normalize_key(k)
                nv_name = self._normalize_key(v.get("station_name", ""))
 # BUG-136: this used to also accept `nk in norm_key or norm_key in nk`
 # (and the same for names) - a raw substring test, meant to catch
 # provider-prefix variants of the SAME station (e.g. "Tesla_X" vs "X",
 # which _normalize_key() already unifies to the same string above by
 # stripping the prefix - so those need only exact equality, not a
 # substring test at all). What it actually did in practice: "Macquarie,
 # NSW" (a real, different station in Macquarie Park, Sydney,
 # ~500km away) normalizes to "macquarie_nsw", which IS a literal
 # substring of "Port Macquarie, NSW"'s "port_macquarie_nsw" - so this
 # unrelated station's cached facilities (Macquarie University, Macquarie
 # Centre, real distances ~305km) were being served for the Port
 # Macquarie Supercharger's lookup, badged "Excellent 10.0/10 within
 # 250m" as if they were genuinely nearby. A shared English place-name
 # fragment ("Macquarie") is not evidence of being the same place, or
 # even a shortened/prefixed spelling of it - dropped entirely. Exact
 # normalized-key/name equality (below) plus the coordinate-proximity
 # fallback (~200m, further down) are the only matches now - both are
 # actually safe: one is a real identity match, the other is grounded in
 # real physical distance instead of text similarity.
                if norm_key and nk == norm_key:
                    res = v
                    break
                if norm_name and (nk == norm_name or nv_name == norm_name):
                    res = v
                    break

 # Coordinate matching (within ~200m)
        if not res and lat is not None and lon is not None:
            for k, v in data.items():
                v_lat, v_lon = v.get("lat"), v.get("lon")
                if v_lat is not None and v_lon is not None:
                    d_lat = abs(v_lat - lat)
                    d_lon = abs(v_lon - lon)
                    if d_lat < 0.002 and d_lon < 0.002:
                        res = v
                        break

        if not res:
            return None

        # BUG-143 (2026-09-22): a cached record used to be trusted forever,
        # no matter how old - discovered live: Port Macquarie's cached
        # "Sparse / 0 POIs" record was from 2026-09-16, six days before
        # Cassegrain Wines was added to OSM on 2026-09-22, and this function
        # kept serving that stale zero-result answer indefinitely (both
        # charger_find.py's --inspect and its interactive Nearby Facilities
        # view) because nothing here ever checked the record's own age -
        # a fresh live Overpass query at the exact same coordinates found
        # the winery immediately once actually attempted. A zero-POI result
        # is the riskiest to trust indefinitely (it's exactly the case where
        # a new place appearing nearby silently invalidates it), so it gets
        # a shorter TTL than a populated one. Past the TTL, treat this as a
        # cache miss (None) - same "fall through to a fresh live query"
        # pattern as the REFINEMENT_SANITY_CAP_M case just below - rather
        # than deleting or overwriting anything here.
        last_updated_str = res.get("last_updated")
        if last_updated_str:
            try:
                last_updated_dt = datetime.strptime(last_updated_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

                # REQ-099 (2026-09-22): the day-based TTL above is about the
                # PLACE DATA going stale (a new venue appearing nearby). It
                # does nothing for a record whose *classification* is stale
                # instead - discovered live, minutes after REQ-099 shipped:
                # Knockrow's cached record (1 POI, "Byron Bay Wildlife
                # Sanctuary") was fetched before today's classify_poi_group()
                # rewrite, so it was still holding the OLD "Other Facilities"
                # group/emoji/score - and because it has a real POI (not
                # empty), it qualified for the 30-day TTL, not the 7-day one,
                # so it would have kept showing the wrong group for weeks.
                # A schema/logic change invalidates a record regardless of
                # how "fresh" its data otherwise looks by the day-based TTL,
                # since raw per-POI OSM tags aren't persisted in the cache -
                # only the already-computed name/category/group/emoji - so
                # there's no way to recompute a record's grouping in place;
                # it has to be refetched. Rather than batch-refetching all
                # ~161 cached stations right now (slow, and hammers the free
                # public Overpass mirrors we already saw rate-limit/timeout
                # today), this cutoff makes the fix lazy: any record older
                # than the moment classify_poi_group() was rewritten is
                # treated as a cache miss on its NEXT actual lookup, which is
                # exactly when a stale grouping would otherwise be seen.
                if last_updated_dt < CATEGORY_SCHEMA_CUTOFF and res.get("pois"):
 # BUG-151: a record with ZERO cached POIs is immune to any classify_poi_group()
 # rewrite by definition - empty stays empty no matter how groups/emoji are
 # assigned, since there's nothing in it to (mis)classify. Only a record that
 # actually holds POIs needs the refetch this cutoff forces.
                    return None

                age_days = (datetime.now(timezone.utc) - last_updated_dt).total_seconds() / 86400.0
                ttl_days = FACILITIES_EMPTY_CACHE_TTL_DAYS if not res.get("pois") else FACILITIES_CACHE_TTL_DAYS
                if age_days > ttl_days:
                    return None
            except Exception:
                pass

        # If refined coordinates are provided and differ from cached coords (>15m),
        # dynamically recompute POI distances and quality score from the refined coordinates.
        #
        # BUG-137 (2026-09-22): this used to fire for ANY offset > 15m, no
        # matter how large - fine for its intended case (a station's GPS
        # gets refined by a few tens of metres, e.g. an OSM audit fixing a
        # ~26m Tesla-vs-OSM discrepancy - see the "~26m offset !" note
        # elsewhere in this file's caller). But when BUG-136's now-removed
        # substring-matching bug served the WRONG station's cached record
        # (the real "Macquarie, NSW" Supercharger in Sydney, ~500km from a
        # "Port Macquarie, NSW" query it was wrongly matched against), this
        # same block "helpfully" treated that ~500km discrepancy as if it
        # were just a GPS refinement: it recomputed the (Sydney) POIs'
        # distances against the (Port Macquarie) query coordinates -
        # producing ~305km "nearby" facilities - then overwrote the
        # record's own lat/lon to the query coordinates and PERMANENTLY
        # SAVED that corruption back to disk under the original
        # (unrelated) station's key, so it kept poisoning every subsequent
        # lookup independently of the matching bug itself. A real GPS
        # refinement is tens of metres, never hundreds of kilometres - a
        # huge offset is actual, strong evidence this is the WRONG
        # station, not evidence its coordinates need refining. Capped at
        # REFINEMENT_SANITY_CAP_M: beyond that, this is treated as no
        # match at all (falls through to a fresh live query upstream)
        # rather than silently rewriting a station's cached identity.
        REFINEMENT_SANITY_CAP_M = 2000.0
        if lat is not None and lon is not None and res.get("pois"):
            r_lat = res.get("lat")
            r_lon = res.get("lon")
            if r_lat is not None and r_lon is not None:
                try:
                    offset_m = haversine_distance_km(float(r_lat), float(r_lon), float(lat), float(lon)) * 1000.0
                except Exception:
                    offset_m = 0.0
                if offset_m > REFINEMENT_SANITY_CAP_M:
                    return None
                if offset_m > 15.0:
                    for p in res["pois"]:
                        if p.get("lat") is not None and p.get("lon") is not None:
                            p["dist_m"] = round(haversine_distance_km(float(lat), float(lon), float(p["lat"]), float(p["lon"])) * 1000.0, 1)
                    res["pois"].sort(key=lambda p: p.get("dist_m", 999.0))
                    score_info = compute_facility_score(res["pois"])
                    res.update({
                        "lat": float(lat),
                        "lon": float(lon),
                        "score": score_info["score"],
                        "rating": score_info["rating"],
                        "tier_color": score_info["tier_color"],
                        "badge": score_info["badge"],
                        "chips": score_info["chips"],
                        "total_count": score_info["total_count"],
                        "doorstep_count": score_info["doorstep_count"],
                        "doorstep_100m": score_info["doorstep_100m"],
                        "immediate_doorstep": score_info["immediate_doorstep"],
                        "closest_poi": score_info["closest_poi"],
                        "categories": score_info["categories"],
                    })
                    self.save()
                    return res

        # Backwards compatibility: dynamically attach doorstep_100m & immediate_doorstep if missing from older json
        if ("doorstep_100m" not in res or "immediate_doorstep" not in res) and "pois" in res:
            doorstep_pois = [p for p in res["pois"] if p.get("dist_m", 999.0) <= 100.0]
            res["doorstep_100m"] = [f"{p['name']} ({p.get('dist_m', 0):.0f}m)" for p in doorstep_pois]
            res["immediate_doorstep"] = [
                {
                    "name": p["name"],
                    "category": p.get("category", "amenity"),
                    "group": p.get("group", "Other Facilities"),
                    "emoji": p.get("emoji", "📍"),
                    "dist_m": p.get("dist_m", 0.0)
                }
                for p in doorstep_pois
            ]
            res["doorstep_count"] = len(doorstep_pois)
            closest_poi = res["pois"][0] if res["pois"] else None
            res["closest_poi"] = f"{closest_poi['name']} ({closest_poi['dist_m']:.0f}m)" if closest_poi else None
        return res

    def store_station_facilities(self, station_key: str, station_name: str, network: str, lat: float, lon: float, pois: list, radius_m: int = 500, notes: str = "") -> dict:
        """Computes score and stores facility record in JSON registry.

        BUG-144 (2026-09-22): used to always write to the literal `station_key`
        dict key, even when an existing record for the SAME station already
        lived under a different (typically legacy provider-prefixed, e.g.
        "Tesla_X" vs "X") key - discovered live: force-refreshing Port
        Macquarie's facilities (after Cassegrain Wines was added to OSM)
        correctly fetched fresh data but wrote it to a brand-new "Port
        Macquarie, NSW" key while the stale "Tesla_Port Macquarie, NSW" record
        (0 POIs, six days old) was left behind untouched - two records for one
        physical station, only the newer of which any future lookup would
        actually find via get_station_facilities()'s exact-match-first check.
        Now reuses _find_existing_key() (the same exact/normalized matching
        get_station_facilities() itself uses) so a refresh updates the ONE
        existing record in place - no orphaned duplicates, whatever key
        convention it happened to be stored under originally.
        """
        self.load()
        score_info = compute_facility_score(pois)
        record = {
            "station_name": station_name,
            "network": network,
            "lat": lat,
            "lon": lon,
            "radius_m": radius_m,
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "score": score_info["score"],
            "rating": score_info["rating"],
            "tier_color": score_info["tier_color"],
            "badge": score_info["badge"],
            "chips": score_info["chips"],
            "total_count": score_info["total_count"],
            "doorstep_count": score_info["doorstep_count"],
            "doorstep_100m": score_info["doorstep_100m"],
            "immediate_doorstep": score_info["immediate_doorstep"],
            "closest_poi": score_info["closest_poi"],
            "categories": score_info["categories"],
            "pois": pois,
            "notes": notes,
        }
        existing_key = self._find_existing_key(station_key=station_key, station_name=station_name)
        write_key = existing_key or station_key
        self._cache[write_key] = record
        self.save()
        return record

    def reclassify_cached_facilities(self, dry_run: bool = False) -> dict:
        """BUG-151: recomputes group/emoji/score for every cached record whose
        POIs carry the `raw_tags` snapshot (added alongside this method), with
        NO network call at all - the whole point of saving raw_tags was to make
        a future classify_poi_group() change (like REQ-099's rewrite, the
        reason BUG-146's CATEGORY_SCHEMA_CUTOFF exists) something that can be
        applied to the entire cache instantly and offline, instead of costing
        one live Overpass refetch per station the way today's backlog did.

        A record with an empty POI list needs nothing here - see BUG-151's
        change to get_station_facilities(), which already exempts it from the
        schema cutoff entirely (empty stays empty regardless of grouping
        rules). A non-empty record where even one POI is missing `raw_tags`
        (i.e. it predates this method existing) genuinely cannot be
        recomputed from the cache alone and is reported separately so the
        caller knows a live refetch is still the only option for it.

        Returns {"reclassified": int, "unchanged": int, "needs_live_refetch": int,
        "needs_live_refetch_names": [str, ...]}.
        """
        try:
            from places_manager import classify_poi_group
        except ImportError:
            import sys as _sys
            import os as _os
            _tools_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            if _tools_dir not in _sys.path:
                _sys.path.insert(0, _tools_dir)
            from places_manager import classify_poi_group

        self.load()
        reclassified = 0
        unchanged = 0
        needs_refetch = []

        for key, record in self._cache.items():
            pois = record.get("pois") or []
            if not pois:
                continue  # nothing to (mis)classify - already exempt, see get_station_facilities()

            if any("raw_tags" not in p for p in pois):
                needs_refetch.append(record.get("station_name", key))
                continue

            changed = False
            new_pois = []
            for p in pois:
                new_group, new_emoji = classify_poi_group(p["raw_tags"])
                if new_group != p.get("group") or new_emoji != p.get("emoji"):
                    changed = True
                new_pois.append({**p, "group": new_group, "emoji": new_emoji})

            if not changed:
                unchanged += 1
                continue

            reclassified += 1
            if dry_run:
                continue

            score_info = compute_facility_score(new_pois)
            record["pois"] = new_pois
            record["score"] = score_info["score"]
            record["rating"] = score_info["rating"]
            record["tier_color"] = score_info["tier_color"]
            record["badge"] = score_info["badge"]
            record["chips"] = score_info["chips"]
            record["total_count"] = score_info["total_count"]
            record["doorstep_count"] = score_info["doorstep_count"]
            record["doorstep_100m"] = score_info["doorstep_100m"]
            record["immediate_doorstep"] = score_info["immediate_doorstep"]
            record["closest_poi"] = score_info["closest_poi"]
            record["categories"] = score_info["categories"]
            record["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if reclassified and not dry_run:
            self.save()

        return {
            "reclassified": reclassified,
            "unchanged": unchanged,
            "needs_live_refetch": len(needs_refetch),
            "needs_live_refetch_names": needs_refetch,
        }


_FACILITIES_REGISTRY = None

def get_facilities_registry() -> FacilitiesRegistry:
    global _FACILITIES_REGISTRY
    if _FACILITIES_REGISTRY is None:
        _FACILITIES_REGISTRY = FacilitiesRegistry()
    return _FACILITIES_REGISTRY


SUPERMARKET_KEYWORDS = {
    "woolworths", "coles", "aldi", "iga", "harris farm", "costco",
    "foodland", "drakes", "spudshed", "kmart", "target", "big w",
    "supermarket", "grocery", "groceries"
}

def station_has_supermarket(cached_fac: dict, max_dist_m: int = 500) -> bool:
    """True if cached facilities record contains a confirmed supermarket within max_dist_m."""
    if not cached_fac:
        return False
    pois = cached_fac.get("pois", [])
    for p in pois:
        if p.get("dist_m", 999.0) <= max_dist_m:
            if p.get("group") == "Supermarkets & Groceries":
                return True
            cat = (p.get("category") or "").lower()
            name = (p.get("name") or "").lower()
            if any(kw in cat or kw in name for kw in SUPERMARKET_KEYWORDS):
                return True
    return False


import shutil

try:
    from table_formatter import display_len, truncate_display
except ImportError:
    try:
        from lib.table_formatter import display_len, truncate_display
    except ImportError:
        try:
            from Tools.lib.table_formatter import display_len, truncate_display
        except ImportError:
            import re
            _ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\033\[[0-9;]*[a-zA-Z]")
            def display_len(s: str) -> int:
                return len(_ANSI_RE.sub("", str(s)))
            def truncate_display(s: str, max_w: int, ellipsis: str = "…") -> str:
                d = display_len(s)
                if d <= max_w:
                    return s
                return str(s)[:max(1, max_w - 1)] + ellipsis


def format_facilities_summary_card(cached_fac: dict, indent: str = "  ", max_width: int = None) -> list:
    """
    Renders an executive, beautifully formatted terminal block for nearby facilities
    on station inspection and preview cards, strictly constrained to terminal width.
    """
    if not cached_fac:
        return []

    if max_width is None:
        try:
            cols = shutil.get_terminal_size((100, 24)).columns
            max_width = max(60, cols)
        except Exception:
            max_width = 100

    lines = []
    radius_m = cached_fac.get("radius_m", 500)
    badge = cached_fac.get("badge", "")
    header_raw = f"{indent}🏙️  \033[1mNearby Facilities:\033[0m {badge} \033[90m(within {radius_m}m)\033[0m"
    if display_len(header_raw) > max_width:
        header_raw = truncate_display(header_raw, max_width)
    lines.append(header_raw)

 # 1. Immediate Doorstep (<= 100m)
    pois = cached_fac.get("pois", [])
    doorstep = [
        p for p in pois
        if p.get("dist_m", 999.0) <= 100.0
        and not (p.get("category", "").lower().startswith("charging") or "supercharger" in p.get("name", "").lower() or p.get("name", "").lower().startswith("tesla super"))
    ]

    if doorstep:
        lines.append(f"{indent}   🚪 \033[1mImmediate Doorstep (≤ 100m — 1-2 min walk):\033[0m")
        for p in doorstep[:8]:
            emoji = p.get("emoji", "📍")
            name = p.get("name", "Unknown")
            cat = p.get("category", "")
            dist = f"{p.get('dist_m', 0):.0f}m"
            
            prefix = f"{indent}      • {emoji} "
            suffix = f" ({dist})"
            avail = max_width - display_len(prefix) - display_len(suffix)
            
            if cat:
                full_content = f"{name} — {cat}"
                if display_len(full_content) <= avail:
                    line = f"{prefix}\033[36m{name}\033[0m — \033[90m{cat}\033[0m{suffix}"
                else:
                    cat_short = truncate_display(cat, 16)
                    rem_for_name = max(8, avail - display_len(f" — {cat_short}"))
                    name_short = truncate_display(name, rem_for_name)
                    line = f"{prefix}\033[36m{name_short}\033[0m — \033[90m{cat_short}\033[0m{suffix}"
            else:
                name_short = truncate_display(name, avail)
                line = f"{prefix}\033[36m{name_short}\033[0m{suffix}"
            lines.append(line)

        if len(doorstep) > 8:
            lines.append(f"{indent}      \033[90m... and {len(doorstep) - 8} more within 100m\033[0m")
    else:
        closest = None
        for p in pois:
            if not (p.get("category", "").lower().startswith("charging") or "supercharger" in p.get("name", "").lower() or p.get("name", "").lower().startswith("tesla super")):
                closest = p
                break
        if closest:
            emoji = closest.get("emoji", "📍")
            lines.append(f"{indent}   🚪 \033[1mWithin 100m:\033[0m \033[90mNone (closest is {emoji} {closest['name']} at {closest['dist_m']:.0f}m)\033[0m")
        else:
            lines.append(f"{indent}   🚪 \033[1mWithin 100m:\033[0m \033[90mNo amenities detected\033[0m")

 # 2. Detailed Per-Line Category Summary with emojis, counts, and top venues
    groups = {}
    for p in pois:
        if p.get("category", "").lower().startswith("charging") or "supercharger" in p.get("name", "").lower() or p.get("name", "").lower().startswith("tesla super"):
            continue
        grp = p.get("group", "Other Facilities")
        emoji = p.get("emoji", "📍")
        key = (grp, emoji)
        if key not in groups:
            groups[key] = []
        groups[key].append(p)

    if groups:
        lines.append(f"{indent}   📋 \033[1mCategory Summary (within {radius_m}m):\033[0m")
        def group_sort_order(g_key):
            g_name = g_key[0]
            prio = {
                "Supermarkets & Groceries": 1,
                "Dining & Fast Food": 2,
                "Cafes & Coffee": 3,
                "Amenities & Services": 4,
                "Retail & Shopping": 5,
                "Leisure & Entertainment": 6,
                "Lodging & Hotels": 7,
                "Healthcare": 8,
                "Automotive & Parking": 9,
                "Other Facilities": 10
            }
            return prio.get(g_name, 99)

        for (grp_name, grp_emoji), grp_pois in sorted(groups.items(), key=group_sort_order):
            prefix = f"{indent}      • {grp_emoji} \033[1m{grp_name}\033[0m ({len(grp_pois)}): "
            avail = max(15, max_width - display_len(prefix))
            
 # Dynamically pack as many venues as fit within line width
            chosen_venues = []
            for p in grp_pois:
                v_str = f"{p['name']} ({p.get('dist_m', 0):.0f}m)"
                rem_count = len(grp_pois) - (len(chosen_venues) + 1)
                suffix_check = f", +{rem_count} more" if rem_count > 0 else ""
                candidate = ", ".join(chosen_venues + [v_str]) + suffix_check
                if display_len(candidate) <= avail:
                    chosen_venues.append(v_str)
                else:
                    break

            if chosen_venues:
                rem_count = len(grp_pois) - len(chosen_venues)
                if rem_count > 0:
                    venues_str = ", ".join(chosen_venues) + f", +{rem_count} more"
                else:
                    venues_str = ", ".join(chosen_venues)
            else:
                first_v = f"{grp_pois[0]['name']} ({grp_pois[0].get('dist_m', 0):.0f}m)"
                rem_count = len(grp_pois) - 1
                suffix = f", +{rem_count} more" if rem_count > 0 else ""
                first_avail = max(8, avail - display_len(suffix))
                venues_str = truncate_display(first_v, first_avail) + suffix

            lines.append(f"{prefix}\033[90m{venues_str}\033[0m")

    return lines
