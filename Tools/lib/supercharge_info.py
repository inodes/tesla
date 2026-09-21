#!/usr/bin/env python3
"""
supercharge_info.py - Supercharge.info Integration & Metadata Enricher
=======================================================================
Provides integration with supercharge.info community API for authoritative
stall versions (v2/v3/v4), opening dates, elevation, plug types, and ref IDs.
"""

import os
import json
import time
import urllib.request
from typing import Dict, List, Any, Optional, Tuple
from lib.geo_common import haversine_distance_km

SUPERCHARGE_INFO_API = "https://supercharge.info/service/supercharge/allSites"
CACHE_FILE = os.path.expanduser("~/.supercharge_info_cache.json")
CACHE_TTL_SECONDS = 86400  # 24 hours


def fetch_all_sites(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Loads all superchargers from supercharge.info with 24h local caching."""
    if not force_refresh and os.path.isfile(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
                if time.time() - cached.get("timestamp", 0) < CACHE_TTL_SECONDS:
                    return cached.get("sites", [])
        except Exception:
            pass

    # Fetch from API
    try:
        req = urllib.request.Request(
            SUPERCHARGE_INFO_API,
            headers={
                "User-Agent": "TeslaTools/1.0 (https://github.com/inodes/tesla)",
                "Accept": "application/json",
            }
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            sites = json.loads(resp.read().decode("utf-8"))
            if sites and isinstance(sites, list):
                with open(CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump({"timestamp": time.time(), "sites": sites}, f)
                return sites
    except Exception:
        # Fallback to expired cache if available
        if os.path.isfile(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f).get("sites", [])
            except Exception:
                pass
    return []


def find_supercharge_site(
    name: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    location_id: Optional[str] = None,
    radius_km: float = 15.0
) -> Optional[Dict[str, Any]]:
    """
    Finds matching supercharge.info site by locationId, name, or GPS proximity.
    """
    sites = fetch_all_sites()
    if not sites:
        return None

    # 1. Match by locationId (Tesla location number or slug)
    if location_id:
        loc_str = str(location_id).strip().lower()
        for s in sites:
            if str(s.get("locationId", "")).strip().lower() == loc_str:
                return s

    # 2. Match by GPS proximity (< 15km) and name similarity
    name_clean = name.lower().replace("supercharger", "").replace(",", "").strip()
    candidates = []

    for s in sites:
        s_gps = s.get("gps", {})
        s_lat = s_gps.get("latitude")
        s_lon = s_gps.get("longitude")
        s_name = s.get("name", "").lower().replace(",", "").strip()

        dist_km = 9999.0
        if lat is not None and lon is not None and s_lat is not None and s_lon is not None:
            dist_km = haversine_distance_km(lat, lon, s_lat, s_lon)

        # Direct proximity match
        if dist_km < 1.0:
            return s

        # Name match within reasonable radius
        if (name_clean in s_name or s_name in name_clean) and dist_km < radius_km:
            candidates.append((dist_km, s))

    if candidates:
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    # 3. Fallback name-only search if no coordinates
    for s in sites:
        s_name = s.get("name", "").lower().replace(",", "").strip()
        if name_clean and (name_clean == s_name or name_clean in s_name):
            return s

    return None


def search_supercharge_sites(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Searches supercharge.info database globally by:
    - Site ID (e.g. 7255, 7384)
    - Supercharge.info URL (with siteId=... or center=lat,lon)
    - Name, city, state, or street tokens
    """
    import re
    query_str = str(query).strip()
    sites = fetch_all_sites()
    if not sites:
        return []

    # 1. URL pattern matching (siteId=...)
    m_id = re.search(r"siteId=(\d+)", query_str)
    if m_id:
        target_id = int(m_id.group(1))
        return [s for s in sites if s.get("id") == target_id]

    # 2. URL pattern matching (center=lat,lon)
    m_center = re.search(r"center=(-?\d+\.\d+),(-?\d+\.\d+)", query_str)
    if m_center:
        lat, lon = float(m_center.group(1)), float(m_center.group(2))
        by_dist = []
        for s in sites:
            s_gps = s.get("gps", {})
            if s_gps.get("latitude") is not None and s_gps.get("longitude") is not None:
                d = haversine_distance_km(lat, lon, s_gps["latitude"], s_gps["longitude"])
                by_dist.append((d, s))
        by_dist.sort(key=lambda x: x[0])
        return [s for d, s in by_dist[:limit]]

    # 3. Integer ID exact match
    if query_str.isdigit():
        target_id = int(query_str)
        exact = [s for s in sites if s.get("id") == target_id]
        if exact:
            return exact

    # 4. Token-based multi-field matching
    tokens = [t.lower() for t in re.split(r"[\s,]+", query_str) if t]
    scored = []
    for s in sites:
        text = f"{s.get('name', '')} {s.get('locationId', '')} {s.get('address', {}).get('street', '')} {s.get('address', {}).get('city', '')} {s.get('address', {}).get('state', '')} {s.get('address', {}).get('country', '')}".lower()
        match_count = sum(1 for t in tokens if t in text)
        if match_count == len(tokens):
            score = 100
            if query_str.lower() in s.get("name", "").lower():
                score += 50
            scored.append((score, s))
        elif match_count > 0 and len(tokens) > 1:
            scored.append((match_count * 10, s))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for score, s in scored[:limit]]


def fetch_site_changes(search_name: str) -> List[Dict[str, Any]]:
    """
    Fetches construction, opening, and status change history for a station from supercharge.info.
    """
    import urllib.parse
    clean = search_name.replace("Supercharger", "").replace(",", "").strip()
    url = f"https://supercharge.info/service/supercharge/changes?search={urllib.parse.quote(clean)}"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "TeslaTools/1.0 (https://github.com/inodes/tesla)",
                "Accept": "application/json",
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("results", [])
    except Exception:
        return []


