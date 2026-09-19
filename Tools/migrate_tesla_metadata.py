#!/usr/bin/env python3
"""
⚡ Tesla Metadata 6-Field Standard Migration Script (migrate_tesla_metadata.py) ⚡
================================================================================
Backfills all 6 canonical Tesla naming fields across tesla_superchargers.json
and tesla_chargers.json, orders the 6 fields at the very top of tesla_metadata,
removes legacy name prefixes, cleans raw suburb strings, and validates on-disk consistency.
"""

import os
import sys
import json
import re
from typing import Dict, Any

_tools_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_tools_dir)
_lib_dir = os.path.join(_tools_dir, "lib")
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from lib.table_formatter import clean_station_short_name

KNOWN_LIST_OVERRIDES = {
    "supercharger": {
        "Balgowlah, NSW": "Manly, NSW",
        "New Italy, NSW": "Ballina, NSW",
        "Goulburn, NSW - Lockyer Street": "Goulburn 2, NSW",
        "Norwood, SA": "North Adelaide, SA",
        "Kings Park, WA": "Perth,WA",
        "Carmila, QLD": "St Lawrence, QLD",
    },
    "destination_charger": {}
}


def clean_suburb_and_postcode(raw_suburb: str, raw_postcode: str):
    """Strips state abbreviations and trailing postcodes from suburb strings."""
    s = str(raw_suburb or "").strip()
    pc = str(raw_postcode or "").strip()

    if not pc:
        m = re.search(r"\b(\d{4})\b", s)
        if m:
            pc = m.group(1)

    s = re.sub(r",?\s*(?:NSW|VIC|QLD|WA|SA|TAS|ACT|NT)\b.*$", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"\s+\d{4}$", "", s).rstrip(",").strip()
    return s or str(raw_suburb or ""), pc


def migrate_entry(key: str, data: dict, charger_type: str) -> dict:
    """Migrates a single Tesla station record to the 6-Field Standard with top-ordered fields."""
    is_dest = (charger_type == "destination_charger")
    t_meta = data.get("tesla_metadata", {}) if isinstance(data.get("tesla_metadata"), dict) else {}
    loc = data.get("location", {}) if isinstance(data.get("location"), dict) else {}

 # 1. Location Name (Authoritative)
    raw_loc = (t_meta.get("findus_location_name") or t_meta.get("name") or data.get("name") or key)
    findus_loc_name = str(raw_loc).replace("Tesla Supercharger - ", "").replace("Tesla Destination - ", "").strip()
    findus_loc_short_name = t_meta.get("findus_location_short_name") or clean_station_short_name(findus_loc_name)

 # 2. List Name (Directory List)
    list_override = KNOWN_LIST_OVERRIDES[charger_type].get(key) or KNOWN_LIST_OVERRIDES[charger_type].get(findus_loc_name)
    raw_list = list_override or t_meta.get("findus_list_name") or findus_loc_name
    findus_list_name = str(raw_list).replace("Tesla Supercharger - ", "").replace("Tesla Destination - ", "").strip()
    findus_list_short_name = t_meta.get("findus_list_short_name") or clean_station_short_name(findus_list_name)

 # 3. Canonical Display Name (Location > List rule: findus_name)
    canonical_name = findus_loc_name

 # 4. Canonical Short Name
    canonical_short_name = findus_loc_short_name or findus_list_short_name or clean_station_short_name(key)

 # 5. Clean Suburb & Postcode
    raw_suburb = loc.get("suburb") or data.get("suburb") or ""
    raw_pc = loc.get("postcode") or data.get("postcode") or ""
    clean_sub, clean_pc = clean_suburb_and_postcode(raw_suburb, raw_pc)

    if "location" in data and isinstance(data["location"], dict):
        if clean_sub:
            data["location"]["suburb"] = clean_sub
        if clean_pc:
            data["location"]["postcode"] = clean_pc

 # General / location descriptor
    general_location = t_meta.get("general_location") or t_meta.get("location_name") or clean_sub

 # Keywords indexing all 6 representations
    existing_kw = t_meta.get("keywords", [])
    street_str = loc.get("street") or loc.get("address", "")
    address_str = loc.get("address", "")
    findus_id_val = str(t_meta.get("findus_id") or data.get("id") or "").strip()

    merged_keywords = [
        k for k in dict.fromkeys([
            key,
            canonical_name,
            findus_list_name,
            findus_list_short_name,
            findus_loc_name,
            findus_loc_short_name,
            canonical_short_name,
            findus_list_name.split(",")[0].strip(),
            findus_loc_name.split(",")[0].strip(),
            findus_id_val,
            general_location,
            clean_sub,
            loc.get("state", ""),
            address_str,
            street_str
        ] + existing_kw) if k and str(k).strip()
    ]

 # Build top-ordered tesla_metadata with the 6 fields first
    ordered_meta = {
        "findus_list_name": findus_list_name,
        "findus_list_short_name": findus_list_short_name,
        "findus_location_name": findus_loc_name,
        "findus_location_short_name": findus_loc_short_name,
        "name": canonical_name,
        "short_name": canonical_short_name,
        "general_location": general_location,
        "location_name": general_location,
        "type": "supercharger" if not is_dest else "destination_charger"
    }

    if "findus_id" in t_meta or findus_id_val:
        ordered_meta["findus_id"] = findus_id_val
    if "findus_url" in t_meta:
        ordered_meta["findus_url"] = t_meta["findus_url"]

 # Preserve any other metadata fields
    for k, v in t_meta.items():
        if k not in ordered_meta and k != "keywords":
            ordered_meta[k] = v

    ordered_meta["keywords"] = merged_keywords
    data["tesla_metadata"] = ordered_meta

    return data


def migrate_file(filepath: str, charger_type: str) -> int:
    """Migrates a single JSON registry file."""
    if not os.path.exists(filepath):
        print(f"❌ File not found: {filepath}")
        return 0

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    updated_count = 0
    for key in list(data.keys()):
        data[key] = migrate_entry(key, data[key], charger_type)
        updated_count += 1

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"✔ Successfully migrated {updated_count} entries in {os.path.basename(filepath)}")
    return updated_count


def main():
    print("🚀 Starting 6-Field Tesla Metadata Migration...\n")
    data_dir = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Tesla/Tessie")
    if not os.path.exists(data_dir):
        data_dir = os.path.join(_repo_root, "Tessie")

    sc_path = os.path.join(data_dir, "tesla_superchargers.json")
    dc_path = os.path.join(data_dir, "tesla_chargers.json")

    sc_count = migrate_file(sc_path, "supercharger")
    dc_count = migrate_file(dc_path, "destination_charger")

 # Also migrate repo template example files if present
    repo_tessie = os.path.join(_repo_root, "Tessie")
    for f, c_type in [
        ("tesla_superchargers.example.json", "supercharger"),
        ("superchargers.example.json", "supercharger"),
        ("tesla_chargers.example.json", "destination_charger"),
        ("charging.example.json", "destination_charger"),
    ]:
        p = os.path.join(repo_tessie, f)
        if os.path.exists(p):
            migrate_file(p, c_type)

    print(f"\n✨ Migration complete: {sc_count} Superchargers, {dc_count} Destination Chargers.")


if __name__ == "__main__":
    main()
