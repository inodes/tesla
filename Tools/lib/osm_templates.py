#!/usr/bin/env python3
"""
osm_templates.py - Standard OpenStreetMap Tagging Templates & Presets
=======================================================================
Provides rich, OSM-compliant tagging presets, Australian brand matching,
and tag inference for converting Google Maps locations into valid OSM objects.

Templates and brand catalogs are stored in osm_templates.json and conform
strictly to official OpenStreetMap Wiki tagging standards:
https://wiki.openstreetmap.org/wiki/Tesla
https://wiki.openstreetmap.org/wiki/Tag:amenity=charging_station/Tesla_Motors
"""

import os
import json
import re
from typing import Dict, List, Any, Optional, Tuple

_DIR = os.path.dirname(os.path.abspath(__file__))
_JSON_PATH = os.path.join(_DIR, "osm_templates.json")


def load_templates_data() -> Tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, Any]]]:
    """Loads brand catalog and templates from JSON file."""
    if os.path.isfile(_JSON_PATH):
        try:
            with open(_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("brand_catalog", {}), data.get("templates", {})
        except Exception:
            pass
    return {}, {}


BRAND_CATALOG, OSM_TEMPLATES = load_templates_data()


def get_template(template_id: str) -> Optional[Dict[str, Any]]:
    """Returns template definition by ID."""
    return OSM_TEMPLATES.get(template_id.lower().replace("-", "_").replace(" ", "_"))


def match_template(name: str, hint: str = "") -> Optional[Tuple[str, Dict[str, Any]]]:
    """Matches the best template for a given place name and optional category hint."""
    combined = f"{name.lower()} {hint.lower()}".strip()
    
    # 1. Check direct brand catalog first
    for brand_key, brand_tags in BRAND_CATALOG.items():
        if brand_key in combined:
            if "supercharger" in brand_key:
                return "tesla_supercharger", OSM_TEMPLATES.get("tesla_supercharger", {})
            elif "destination" in brand_key:
                return "tesla_destination", OSM_TEMPLATES.get("tesla_destination", {})
            elif "service" in brand_key:
                return "tesla_service", OSM_TEMPLATES.get("tesla_service", {})
            elif "store" in brand_key or "showroom" in brand_key:
                return "tesla_store", OSM_TEMPLATES.get("tesla_store", {})
            elif brand_tags.get("shop") == "supermarket":
                return "supermarket", OSM_TEMPLATES.get("supermarket", {})
            elif brand_tags.get("amenity") == "pharmacy":
                return "pharmacy", OSM_TEMPLATES.get("pharmacy", {})
            elif brand_tags.get("amenity") == "fast_food":
                return "fast_food", OSM_TEMPLATES.get("fast_food", {})
            elif brand_tags.get("amenity") == "charging_station":
                return "ev_charging_station", OSM_TEMPLATES.get("ev_charging_station", {})

    # 2. Check template keywords
    for t_id, t_info in OSM_TEMPLATES.items():
        for kw in t_info.get("keywords", []):
            if re.search(r"\b" + re.escape(kw) + r"\b", combined, re.IGNORECASE):
                return t_id, t_info

    return None


def build_osm_tags(
    name: str,
    template_id: Optional[str] = None,
    extra_tags: Optional[Dict[str, str]] = None,
    hint: str = ""
) -> Tuple[Dict[str, str], Optional[str], List[str]]:
    """
    Builds a complete, valid set of OSM tags using templates, brand knowledge, and user overrides.
    Returns: (final_tags, matched_template_id, suggested_optional_keys)
    """
    tags = {"name": name.strip()}
    matched_id = None
    suggested_keys = []

    # 1. Resolve template
    if template_id:
        t = get_template(template_id)
        if t:
            matched_id = template_id
            tags.update(t.get("primary_tags", {}))
            suggested_keys = list(t.get("suggested_tags", {}).keys())
    else:
        m = match_template(name, hint)
        if m:
            matched_id, t = m
            tags.update(t.get("primary_tags", {}))
            suggested_keys = list(t.get("suggested_tags", {}).keys())

    # 2. Apply Australian brand metadata if recognized
    n_lower = name.lower()
    for brand_key, brand_tags in BRAND_CATALOG.items():
        if brand_key in n_lower:
            tags.update(brand_tags)
            break

    # 3. Apply user extra tags
    if extra_tags:
        tags.update(extra_tags)

    # 4. Enforce Australian Plug Standards (CCS2 / Type 2, NOT NACS/CCS1)
    if "socket:nacs" in tags or "socket:type1_combo" in tags or "socket:type1" in tags:
        tags.pop("socket:nacs", None)
        tags.pop("socket:type1_combo", None)
        tags.pop("socket:type1", None)
        if tags.get("amenity") == "charging_station" and "socket:type2_combo" not in tags and "socket:type2" not in tags:
            tags["socket:type2_combo"] = "yes"

    return tags, matched_id, suggested_keys


def derive_tesla_ref(
    name: str = "",
    short_id: str = "",
    url: str = "",
    location_id: str = ""
) -> str:
    """
    Derives standard OSM `ref:tesla` tag value.
    Format: lowercase, alphanumeric only, with 'supercharger' suffix.
    E.g.:
      'Macquarie, NSW' / 'Macquarie_NSW' -> 'macquarienswsupercharger'
      'Central Coast, NSW' -> 'centralcoastnswsupercharger'
      location_id 'tuggerahsupercharger' -> 'tuggerahsupercharger'
    """
    if location_id:
        slug = re.sub(r"[^a-zA-Z0-9]", "", str(location_id)).lower()
        if slug:
            return slug
    if url:
        m = re.search(r"/location/(?:supercharger/)?([a-zA-Z0-9_\-]+)", str(url))
        if m:
            slug = re.sub(r"[^a-zA-Z0-9]", "", m.group(1)).lower()
            if slug:
                return slug
    base = short_id or name or ""
    clean = re.sub(r"[^a-zA-Z0-9]", "", str(base)).lower()
    if clean:
        if not clean.endswith("supercharger"):
            clean += "supercharger"
        return clean
    return ""

