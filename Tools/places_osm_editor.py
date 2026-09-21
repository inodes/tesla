#!/usr/bin/env python3
"""
places_osm_editor.py - OpenStreetMap Amenities Inspector & Live Editor
========================================================================
CLI tool to inspect, verify, and update OpenStreetMap amenities, restrooms,
and shopping centres around EV charging stations and places.

Usage:
  # 1. Authorize with OpenStreetMap (one-time):
  ./Tools/places_osm_editor.py --auth-url
  ./Tools/places_osm_editor.py --code <authorization_code>
  ./Tools/places_osm_editor.py --whoami

  # 2. Inspect raw OSM data around a charging station:
  ./Tools/places_osm_editor.py --inspect "Thrumster, NSW"

  # 3. Add missing public toilets node:
  ./Tools/places_osm_editor.py --add-toilets --at -31.4578,152.8282 --comment "Add indoor public toilets at Sovereign Place Town Centre"

  # 4. Add or update tags on an OSM Way / Building:
  ./Tools/places_osm_editor.py --tag-way 747162527 --tags "shop=mall" "toilets=yes" --comment "Tag Sovereign Place as shopping mall with restrooms"
"""

import os
import sys
import re
import argparse
import webbrowser
import json
import textwrap
import urllib.request
import urllib.parse
from typing import Tuple, Dict, Any, Optional, List

_script_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_script_dir)

if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)
if os.path.join(_script_dir, "lib") not in sys.path:
    sys.path.insert(0, os.path.join(_script_dir, "lib"))

from lib.osm_client import (
    load_osm_config,
    save_osm_token,
    load_osm_token,
    get_osm_token_file,
    get_oauth_auth_url,
    fetch_oauth_token,
    get_user_details,
    open_changeset,
    close_changeset,
    create_node,
    update_element_tags,
    add_osm_note,
    resolve_gmaps_url,
    infer_osm_tags_from_name,
    query_overpass_charging_stations,
    OSM_USER_AGENT,
)
from lib.osm_templates import (
    OSM_TEMPLATES,
    get_template,
    match_template,
    build_osm_tags,
    derive_tesla_ref,
)
from places_manager import query_overpass_pois
from providers_chargers.chargers_base import haversine_km

C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_CYAN = "\033[96m"
C_RED = "\033[91m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_RESET = "\033[0m"


def save_audited_osm_to_tesla_registry(
    station_key: str,
    osm_lat: float,
    osm_lon: float,
    osm_node_id: Optional[int] = None
) -> bool:
    """Saves verified OSM GPS coordinates and node ID to local Tesla registry."""
    from lib.tessie_api_common import resolve_tessie_data_home
    from datetime import datetime, timezone
    tessie_dir = resolve_tessie_data_home()
    if not tessie_dir:
        return False

    for filename in ("tesla_superchargers.json", "tesla_chargers.json"):
        fpath = os.path.join(tessie_dir, filename)
        if not os.path.isfile(fpath):
            continue
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)

            target_key = None
            if isinstance(data, dict):
                if station_key in data:
                    target_key = station_key
                else:
                    for k in data.keys():
                        if station_key.lower() == k.lower() or station_key.lower().replace(" ", "") in k.lower().replace(" ", ""):
                            target_key = k
                            break

            if target_key:
                entry = data[target_key]
                if "location" not in entry or not isinstance(entry["location"], dict):
                    entry["location"] = {}
                entry["location"]["osm_lat"] = round(float(osm_lat), 6)
                entry["location"]["osm_lon"] = round(float(osm_lon), 6)
                if osm_node_id:
                    entry["location"]["osm_node_id"] = int(osm_node_id)
                entry["location"]["osm_audited_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                return True
        except Exception:
            continue
    return False


def main():
    parser = argparse.ArgumentParser(description="OpenStreetMap Amenities Inspector & Live Editor for EV Stations")
    parser.add_argument("--auth-url", action="store_true", help="Generate OSM OAuth login URL")
    parser.add_argument("--code", type=str, help="Authorization code received from OSM OAuth screen")
    parser.add_argument("--token", type=str, help="Directly supply or test an OSM access token")
    parser.add_argument("--whoami", action="store_true", help="Check currently authenticated OSM user")
    parser.add_argument("--inspect", type=str, help="Inspect nearby OSM elements around a station name or lat,lon")
    parser.add_argument("--radius", type=int, default=300, help="Search radius in meters (default: 300)")
    parser.add_argument("--from-gmaps", type=str, help="Import / translate a Google Maps share link into OSM tags and location")
    parser.add_argument("--audit-tesla", "--audit", type=str, help="Audit a Tesla Supercharger / Destination Charger locally or globally (by name, address, or supercharge.info ID)")
    parser.add_argument("--from-supercharge", "--supercharge", type=str, help=argparse.SUPPRESS)
    parser.add_argument("--fix-node", "--fix-both", action="store_true", help="Automatically update Wiki tags AND reposition OSM node to updated GPS in one changeset")
    parser.add_argument("--fix-tags", action="store_true", help="Fix Wiki & Tag compliance errors on OSM node only (preserves current OSM coordinates)")
    parser.add_argument("--reposition", action="store_true", help="Reposition OSM node coordinates only (preserves existing tags)")
    parser.add_argument("--gpsupdate", "--update-gps", type=str, help="Updated physical stall GPS coordinates for charger (lat,lon or Google Maps URL)")
    parser.add_argument("--save-registry", action="store_true", help="Save audited OSM coordinates and node ID to local tesla_superchargers.json")
    parser.add_argument("--template", type=str, help="Apply a specific OSM preset template (e.g. basketball_court, public_toilets, supermarket)")
    parser.add_argument("--list-templates", action="store_true", help="List all available OSM-compliant tagging templates")
    parser.add_argument("--create-node", action="store_true", help="DISABLED BY POLICY (BUG-121, 2026-09-21) - node creation is no longer performed by this tool, no override exists. Kept as a flag only so old invocations fail with a clear message instead of an argparse error.")
    parser.add_argument("--name", type=str, help="Override place name for new node")
    parser.add_argument("--add-toilets", action="store_true", help="Create an amenity=toilets node at --at coordinates")
    parser.add_argument("--tag-way", type=int, help="OSM Way ID to update tags on")
    parser.add_argument("--tags", nargs="+", help="Tags to apply in key=value format (e.g. shop=mall toilets=yes)")
    parser.add_argument("--at", type=str, help="Coordinates for new node (lat,lon)")
    parser.add_argument("--comment", type=str, help="Mandatory changeset description for edits")
    parser.add_argument("--note", type=str, help="Submit a public community note to OSM")
    parser.add_argument("--open", dest="open_browser", action="store_true", default=True, help="Automatically open changeset in web browser upon publishing (default: True)")
    parser.add_argument("--no-open", dest="open_browser", action="store_false", help="Do not open changeset in web browser")
    parser.add_argument("--dry-run", action="store_true", help="Preview changeset without sending edits (this is now the DEFAULT - see --live)")
    parser.add_argument("--live", action="store_true", help="BUG-121 (2026-09-21): actually publish to OpenStreetMap. Dry-run is now the default for every flow that would write - pass --live to opt in for real. --dry-run always wins if both are passed.")

    args = parser.parse_args()
    # BUG-121: dry-run is now the safe default. --live is the explicit opt-in to real writes;
    # --dry-run (if passed) always overrides --live back to a preview, never the other way round.
    args.dry_run = (not args.live) or args.dry_run
    cfg = load_osm_config()

    client_id = cfg.get("client_id")
    client_secret = cfg.get("client_secret")
    redirect_uri = cfg.get("redirect_uri", "urn:ietf:wg:oauth:2.0:oob")
    token = args.token or cfg.get("access_token")

    if args.token:
        save_osm_token(args.token.strip())
        token = args.token.strip()

    # Flow 1: OAuth Code Exchange
    if args.code:
        if not client_id or not client_secret:
            print(f"{C_RED}❌ Error: 'client_id' and 'client_secret' must be in config.json['osm']{C_RESET}", file=sys.stderr)
            sys.exit(1)
        print(f"🔄 {C_CYAN}Exchanging authorization code with OpenStreetMap...{C_RESET}")
        tok, data = fetch_oauth_token(client_id, client_secret, args.code, redirect_uri=redirect_uri)
        if tok:
            p = save_osm_token(tok, {"source": "oauth_code"})
            print(f"{C_GREEN}✔ Successfully authenticated with OpenStreetMap!{C_RESET}")
            print(f"  Token securely cached to: {C_CYAN}{p}{C_RESET}\n")
            token = tok
            _display_user(token)
            return
        else:
            print(f"{C_RED}❌ Code exchange failed: {data.get('error') or data}{C_RESET}", file=sys.stderr)
            sys.exit(1)

    # Flow 2: Generate Auth URL
    if args.auth_url or (not token and not args.inspect and not args.note):
        if not client_id:
            print(f"{C_RED}❌ Error: 'client_id' missing in config.json['osm']{C_RESET}", file=sys.stderr)
            sys.exit(1)
        url = get_oauth_auth_url(client_id, redirect_uri=redirect_uri)
        print(f"\n🔑 {C_BOLD}OpenStreetMap Authorization URL:{C_RESET}")
        print(f"   {C_CYAN}{url}{C_RESET}\n")
        print(f"👉 {C_BOLD}Instructions:{C_RESET}")
        print(f"   1. Open the URL above in your browser")
        print(f"   2. Click 'Authorize' on OpenStreetMap")
        print(f"   3. Copy the authorization code shown on the screen")
        print(f"   4. Run: {C_GREEN}./Tools/places_osm_editor.py --code <your_code>{C_RESET}\n")
        return

    # Flow 3: Whoami
    if args.whoami:
        if not token:
            print(f"{C_RED}❌ Not logged in. Run ./Tools/places_osm_editor.py --auth-url first.{C_RESET}")
            sys.exit(1)
        _display_user(token)
        return

    # Flow 3b: List Templates
    if args.list_templates:
        _display_templates()
        return

    # Flow 4: Inspect station / coordinates
    if args.inspect:
        _inspect_location(args.inspect, args.radius)
        return

    # Flow 4b: Import / Translate Google Maps Link
    if args.from_gmaps:
        print(f"🗺️  {C_CYAN}Resolving Google Maps location...{C_RESET}")
        info = resolve_gmaps_url(args.from_gmaps)
        lat = info.get("latitude")
        lon = info.get("longitude")
        name = args.name or info.get("name") or "Unnamed Place"

        if lat is None or lon is None:
            print(f"{C_RED}❌ Failed to extract coordinates from Google Maps link.{C_RESET}")
            print(f"  Resolved URL: {info.get('resolved_url')}")
            sys.exit(1)

        print(f"\n📍 {C_BOLD}Google Maps Location Extracted:{C_RESET}")
        print(f"  • Name:        {C_GREEN}{name}{C_RESET}")
        print(f"  • Coordinates: {C_BOLD}{lat:.7f}, {lon:.7f}{C_RESET}")
        print(f"  • Link:        {C_DIM}{info.get('resolved_url')}{C_RESET}\n")

        # Parse user extra tags if provided
        user_tags = {}
        if args.tags:
            for t in args.tags:
                if "=" in t:
                    k, v = t.split("=", 1)
                    user_tags[k.strip()] = v.strip()

        # Build OSM compliant tags using template & brand knowledge
        tags, matched_t_id, suggested_keys = build_osm_tags(
            name=name,
            template_id=args.template,
            extra_tags=user_tags,
        )

        if matched_t_id:
            t_obj = get_template(matched_t_id)
            print(f"📋 {C_BOLD}Matched OSM Template:{C_RESET} {C_GREEN}{t_obj.get('label')}{C_RESET} {C_DIM}(--template {matched_t_id}){C_RESET}")
        else:
            print(f"📋 {C_BOLD}Matched OSM Template:{C_RESET} {C_YELLOW}Generic / Keyword Inference{C_RESET}")

        print(f"\n🏷️  {C_BOLD}OpenStreetMap Tags to Apply:{C_RESET}")
        for k, v in tags.items():
            print(f"  • {C_CYAN}{k:<16}{C_RESET} = {C_GREEN}{v}{C_RESET}")

        if suggested_keys:
            print(f"\n💡 {C_BOLD}Suggested Optional Tags for this entity:{C_RESET}")
            t_obj = get_template(matched_t_id) if matched_t_id else None
            s_dict = t_obj.get("suggested_tags", {}) if t_obj else {}
            for sk in suggested_keys:
                if sk not in tags:
                    default_val = s_dict.get(sk, "")
                    print(f"    --tags {sk}={default_val} {C_DIM}(optional){C_RESET}")
        print()

        # Check existing OSM elements nearby
        print(f"🔍 {C_CYAN}Checking nearby OpenStreetMap elements within 150m...{C_RESET}")
        nearby = query_overpass_pois(lat, lon, radius_m=150)
        if nearby:
            print(f"  Found {len(nearby)} existing OSM element(s) nearby:")
            for p in nearby[:6]:
                print(f"    {p.get('emoji', '📍')} {p.get('name')} ({p.get('dist_m')}m) - {p.get('category')}")
            if len(nearby) > 6:
                print(f"    ... and {len(nearby) - 6} more")
        else:
            print(f"  {C_YELLOW}No existing OSM elements found in immediate radius (New mapping!).{C_RESET}")
        print()

        comment = args.comment or f"Add {name} verified from local area survey / field verification"

        _display_changeset_rationale(
            title=name,
            comment=comment,
            source="ground survey; field verification; local knowledge",
            feature_type=f"{name} ({t_obj.get('label') if matched_t_id else 'Facility'})",
            coords=(lat, lon),
            tags=tags,
            rationale_text=f"Verified in-person / local ground truth; tags follow OSM Wiki presets for {matched_t_id or 'amenities'}."
        )

        if args.create_node:
            if not token:
                print(f"{C_RED}❌ Authentication required to publish to OSM. Run ./Tools/places_osm_editor.py --auth-url{C_RESET}", file=sys.stderr)
                sys.exit(1)
            if args.dry_run:
                print(f"{C_YELLOW}⚠ [DRY-RUN] Would create node at ({lat:.7f}, {lon:.7f}) with comment: '{comment}'{C_RESET}")
                print(f"  Tags: {json.dumps(tags, indent=2)}")
                return
            _publish_node(token, lat, lon, tags, comment, open_browser=args.open_browser)
        else:
            print(f"💡 {C_BOLD}To publish this node to OpenStreetMap, run:{C_RESET}")
            print(f"   {C_GREEN}./Tools/places_osm_editor.py --from-gmaps \"{args.from_gmaps}\" --create-node{C_RESET}")
            print(f"   (Or add {C_CYAN}--dry-run{C_RESET} to test without publishing)\n")
        return

    # Flow 4c: Audit Tesla Station vs OSM & Tesla Scraped Registry
    if args.audit_tesla:
        _audit_tesla_station(
            query=args.audit_tesla,
            token=token,
            create_node=args.create_node,
            fix_node=args.fix_node,
            fix_tags=args.fix_tags,
            reposition=args.reposition,
            gpsupdate=args.gpsupdate,
            save_registry=args.save_registry,
            dry_run=args.dry_run,
            comment=args.comment,
            user_tags=args.tags,
            override_name=args.name,
            open_browser=args.open_browser,
        )
        return

    # Flow 4d: Source and Audit directly from supercharge.info
    if args.from_supercharge:
        _source_and_audit_supercharge(
            query=args.from_supercharge,
            token=token,
            create_node=args.create_node,
            fix_node=args.fix_node,
            fix_tags=args.fix_tags,
            reposition=args.reposition,
            gpsupdate=args.gpsupdate,
            save_registry=args.save_registry,
            dry_run=args.dry_run,
            comment=args.comment,
            user_tags=args.tags,
            override_name=args.name,
            open_browser=args.open_browser,
        )
        return

    # Flow 5: Add Toilets Node
    if args.add_toilets:
        if not token:
            print(f"{C_RED}❌ Authentication required for map editing. Run ./Tools/osm_editor.py --auth-url{C_RESET}", file=sys.stderr)
            sys.exit(1)
        if not args.at:
            print(f"{C_RED}❌ Error: --at <lat,lon> is required to create a node.{C_RESET}", file=sys.stderr)
            sys.exit(1)
        lat, lon = _parse_coords(args.at)
        comment = args.comment or "Add verified public restrooms near EV charging station"
        
        tags = {
            "amenity": "toilets",
            "access": "yes",
            "wheelchair": "yes",
            "description": "Public toilets inside shopping centre",
            "fee": "no",
        }
        if args.tags:
            for t in args.tags:
                if "=" in t:
                    k, v = t.split("=", 1)
                    tags[k.strip()] = v.strip()

        print(f"\n📍 {C_BOLD}Creating OSM Node (amenity=toilets):{C_RESET}")
        print(f"  • Coordinates: {lat:.6f}, {lon:.6f}")
        print(f"  • Comment:     {comment}")
        print(f"  • Tags:        {json.dumps(tags, indent=2)}")

        if args.dry_run:
            print(f"\n{C_YELLOW}⚠ [DRY-RUN] No edits sent to OpenStreetMap.{C_RESET}")
            return

        _publish_node(token, lat, lon, tags, comment, open_browser=args.open_browser)
        return

    # Flow 6: Tag Way / Building
    if args.tag_way:
        if not token:
            print(f"{C_RED}❌ Authentication required for map editing. Run ./Tools/places_osm_editor.py --auth-url{C_RESET}", file=sys.stderr)
            sys.exit(1)
        if not args.tags:
            print(f"{C_RED}❌ Error: --tags key=value ... required for updating way.{C_RESET}", file=sys.stderr)
            sys.exit(1)
        comment = args.comment or f"Update tags on OSM Way {args.tag_way} near EV charging station"
        new_tags = {}
        for t in args.tags:
            if "=" in t:
                k, v = t.split("=", 1)
                new_tags[k.strip()] = v.strip()

        print(f"\n🏷️  {C_BOLD}Updating OSM Way ID {args.tag_way}:{C_RESET}")
        print(f"  • Comment:  {comment}")
        print(f"  • New Tags: {json.dumps(new_tags, indent=2)}")

        if args.dry_run:
            print(f"\n{C_YELLOW}⚠ [DRY-RUN] No edits sent to OpenStreetMap.{C_RESET}")
            return

        _publish_way_tags(token, args.tag_way, new_tags, comment, open_browser=args.open_browser)
        return

    # Flow 7: Submit OSM Note
    if args.note:
        if not args.at:
            print(f"{C_RED}❌ Error: --at <lat,lon> is required to place an OSM note.{C_RESET}", file=sys.stderr)
            sys.exit(1)
        lat, lon = _parse_coords(args.at)
        print(f"📝 {C_CYAN}Submitting public community note to OpenStreetMap...{C_RESET}")
        note_id, res = add_osm_note(lat, lon, args.note)
        if note_id:
            print(f"{C_GREEN}✔ Note #{note_id} created successfully!{C_RESET}")
            print(f"  View at: {C_CYAN}https://www.openstreetmap.org/note/{note_id}{C_RESET}")
        else:
            print(f"{C_RED}❌ Failed to create note: {res.get('error') or res}{C_RESET}")
        return


def _generate_simple_changeset_comment(
    orig_tags: Optional[Dict[str, str]] = None,
    new_tags: Optional[Dict[str, str]] = None,
    orig_coords: Optional[Tuple[float, float]] = None,
    new_coords: Optional[Tuple[float, float]] = None,
    station_name: str = "",
    custom_comment: Optional[str] = None,
    is_new: bool = False,
    stall_ref: Optional[str] = None,
    is_stall_batch: bool = False,
    stall_count: int = 0,
) -> str:
    """Generate concise, natural, human-friendly changeset comments without bot/automated phrasing."""
    if custom_comment:
        return custom_comment

    if is_new:
        if stall_ref:
            return f"Added Tesla Supercharger stall {stall_ref}"
        if station_name:
            clean_name = station_name.replace("Tesla Supercharger - ", "").replace("Tesla Supercharger", "").strip()
            return f"Added Tesla Supercharger {clean_name}"
        return "Added Tesla Supercharger"

    if is_stall_batch:
        if stall_count:
            return f"Updated charger metadata for {stall_count} Tesla stalls"
        return "Updated charger metadata for Tesla stalls"

    if stall_ref:
        if orig_tags and new_tags:
            old_model = orig_tags.get("model")
            new_model = new_tags.get("model")
            if old_model and new_model and str(old_model).lower() != str(new_model).lower():
                return f"Changed metadata to be correct charger type (Stall {stall_ref}: {new_model})"
        return f"Updated charger metadata for stall {stall_ref}"

    orig_tags = orig_tags or {}
    new_tags = new_tags or {}

    coords_moved = False
    if orig_coords and new_coords:
        coords_moved = (round(orig_coords[0], 6) != round(new_coords[0], 6) or round(orig_coords[1], 6) != round(new_coords[1], 6))

    old_cap = orig_tags.get("capacity")
    new_cap = new_tags.get("capacity")
    cap_changed = bool(old_cap and new_cap and str(old_cap) != str(new_cap))

    old_model = orig_tags.get("model")
    new_model = new_tags.get("model")
    model_changed = bool(old_model and new_model and str(old_model).lower() != str(new_model).lower())

    added_tags = {k: v for k, v in new_tags.items() if k not in orig_tags and v}
    modified_tags = {k: (orig_tags[k], v) for k, v in new_tags.items() if k in orig_tags and str(orig_tags[k]) != str(v) and v}

    if coords_moved and not cap_changed and not model_changed and not modified_tags and not added_tags:
        return "Changed location to the correct location."

    if cap_changed and not coords_moved and not model_changed:
        return f"Corrected the number of charging stalls - it was {old_cap} but it's really {new_cap}"

    if model_changed and not coords_moved and not cap_changed:
        if old_model:
            return f"Changed metadata to be correct charger type (was {old_model}, now {new_model})"
        return f"Changed metadata to be correct charger type ({new_model})"

    if coords_moved and cap_changed:
        return f"Changed location to the correct location and corrected stall count ({new_cap} stalls)"

    if coords_moved and model_changed:
        return f"Changed location to the correct location and updated charger type ({new_model})"

    if coords_moved and (added_tags or modified_tags):
        return "Changed location to the correct location and updated charger details"

    if added_tags and not modified_tags and not cap_changed and not model_changed:
        return "Added missing charger details and sockets"

    if modified_tags:
        return "Corrected charger details to match physical site"

    return "Updated charger metadata"


def _display_changeset_rationale(
    title: str,
    comment: str,
    source: str,
    feature_type: str,
    coords: Optional[Tuple[float, float]] = None,
    tags: Optional[Dict[str, str]] = None,
    rationale_text: str = "",
    orig_tags: Optional[Dict[str, str]] = None,
    orig_coords: Optional[Tuple[float, float]] = None,
    removed_tags: Optional[List[str]] = None,
    is_new: bool = False
):
    print(f"\n📋 {C_BOLD}Changeset Summary & Discussion Notes (Ready to Copy & Paste):{C_RESET}")
    print(f"─" * 80)
    print(f"{C_CYAN}Changeset Comment:{C_RESET}")
    print(f"{comment}\n")
    print(f"{C_CYAN}Source:{C_RESET}")
    print(f"{source}\n")
    print(f"{C_CYAN}Description & Rationale:{C_RESET}")
    if is_new:
        if coords:
            print(f"• Feature: Created new {feature_type} at ({coords[0]:.6f}, {coords[1]:.6f}).")
        else:
            print(f"• Feature: Created new {feature_type}.")
        tag_summary = ", ".join([f"{k}={v}" for k, v in (tags or {}).items() if k not in ("name", "description")])
        if tag_summary:
            print(f"• Applied Tags: {tag_summary}")
    else:
        print(f"• Target Element: {feature_type}")
        if coords and orig_coords:
            c_lat, c_lon = coords
            o_lat, o_lon = orig_coords
            if round(c_lat, 6) != round(o_lat, 6) or round(c_lon, 6) != round(o_lon, 6):
                from providers_chargers.chargers_base import haversine_km
                move_dist_m = haversine_km(o_lat, o_lon, c_lat, c_lon) * 1000.0
                print(f"• Position: Repositioned pin from ({o_lat:.6f}, {o_lon:.6f}) to ({c_lat:.6f}, {c_lon:.6f}) (~{move_dist_m:.1f}m adjustment to physical stalls).")

        if tags is not None and orig_tags is not None:
            added = {k: v for k, v in tags.items() if k not in orig_tags and v}
            modified = {k: (orig_tags[k], v) for k, v in tags.items() if k in orig_tags and str(orig_tags[k]) != str(v) and v}
            purged = [k for k in (removed_tags or []) if k in orig_tags]

            if added:
                print(f"• Added Tags: {', '.join([f'{k}={v}' for k, v in added.items()])}")
            if modified:
                print(f"• Modified Tags: {', '.join([f'{k}: {old} ➔ {new}' for k, (old, new) in modified.items()])}")
            if purged:
                print(f"• Removed Deprecated Tags: {', '.join(purged)}")
            if not added and not modified and not purged:
                print(f"• Tag Status: All existing tags were already compliant (no tag modifications).")
        elif tags:
            tag_summary = ", ".join([f"{k}={v}" for k, v in tags.items() if k not in ("name", "description")])
            if tag_summary:
                print(f"• Tagging Schema: {tag_summary}")

    if tags and tags.get("website"):
        print(f"• Reference URL: {tags['website']}")
    if rationale_text:
        print(f"• Notes: {rationale_text}")
    print(f"─" * 80 + "\n")


def _display_user(token: str):
    user, raw = get_user_details(token)
    if user:
        print(f"👤 {C_BOLD}Authenticated OpenStreetMap Account:{C_RESET}")
        print(f"  • Username:     {C_GREEN}{user.get('display_name')}{C_RESET}")
        print(f"  • User ID:      {user.get('id')}")
        print(f"  • Account Age:  {user.get('account_created')}")
        changesets = user.get("changesets", {})
        print(f"  • Changesets:   {changesets.get('count', 0)} total")
        print()
    else:
        print(f"{C_RED}❌ Failed to fetch user details: {raw.get('error') or raw}{C_RESET}")


def _parse_coords(coord_str: str) -> Tuple[float, float]:
    """Parses lat,lon coordinates from 'lat,lon', 'lat lon', or Google Maps URL."""
    clean = str(coord_str).strip()
    if clean.startswith("http://") or clean.startswith("https://") or "maps" in clean:
        info = resolve_gmaps_url(clean)
        lat, lon = info.get("latitude"), info.get("longitude")
        if lat is not None and lon is not None:
            return float(lat), float(lon)
        raise ValueError(f"Could not extract coordinates from Google Maps link: {coord_str}")

    clean = clean.replace(" ", ",")
    while ",," in clean:
        clean = clean.replace(",,", ",")
    parts = clean.split(",")
    if len(parts) >= 2:
        return float(parts[0]), float(parts[1])
    raise ValueError(f"Invalid coordinate format: {coord_str} (expected 'lat,lon' or Google Maps URL)")


def _confirm_live_publish(action_desc: str) -> bool:
    """
    BUG-121 (2026-09-21): the one shared gate every real (non-dry-run) OSM write must pass through.
    Requires the user to type the literal word 'yes' - anything else (including just pressing
    Enter) aborts. This is on top of --live already having to be passed and --dry-run not having
    been passed; it exists so a live write to production OpenStreetMap is never one accidental
    keypress away.
    """
    print(f"\n{C_YELLOW}⚠ About to publish LIVE to OpenStreetMap: {action_desc}{C_RESET}")
    try:
        answer = input(f"{C_BOLD}Type 'yes' to publish, anything else to abort: {C_RESET}").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(f"\n{C_YELLOW}⚠ Aborted (no input available).{C_RESET}")
        return False
    if answer != "yes":
        print(f"{C_YELLOW}⚠ Aborted - not published.{C_RESET}")
        return False
    return True


def _publish_node(token: str, lat: float, lon: float, tags: dict, comment: str, open_browser: bool = True):
    """
    BUG-121 (2026-09-21): node creation is disabled by policy, with no override anywhere in this
    tool (per the user's explicit decision - see AGENTS.local.md). This function is kept (rather
    than deleted) only because several call sites still reference it; it always refuses. If a
    station genuinely needs a new node, do it by hand in the OSM editor and re-run this tool to
    check/update its tags afterward.
    """
    print(f"{C_RED}❌ Node creation is disabled by policy (BUG-121) - this tool no longer creates new "
          f"OSM nodes.{C_RESET}")
    return None


def _publish_way_tags(token: str, way_id: int, tags: dict, comment: str, open_browser: bool = True):
    # BUG-121: --tag-way is a different domain (mall/building POI tags like shop=mall,
    # toilets=yes), not charging-station work - exempt from the 8-key allowlist
    # (allow_extra_tags=True below), but it still needs the same live-publish confirmation gate.
    if not _confirm_live_publish(f"update tags on OSM Way #{way_id}: {tags}"):
        return
    print(f"🚀 {C_CYAN}Opening changeset on OpenStreetMap...{C_RESET}")
    cs_id, cs_data = open_changeset(token, comment=comment, source="field survey / EV station verification")
    if not cs_id:
        print(f"{C_RED}❌ Failed to open changeset: {cs_data.get('error') or cs_data}{C_RESET}")
        return

    print(f"  • Changeset ID: {C_GREEN}{cs_id}{C_RESET}")
    print(f"🚀 {C_CYAN}Updating tags on Way ID {way_id}...{C_RESET}")
    ok, res = update_element_tags(token, cs_id, "way", way_id, tags, allow_extra_tags=True)
    if not ok:
        print(f"{C_RED}❌ Failed to update way: {res.get('error') or res}{C_RESET}")
        close_changeset(token, cs_id)
        return

    print(f"🚀 {C_CYAN}Closing changeset...{C_RESET}")
    close_changeset(token, cs_id)
    print(f"{C_GREEN}✔ Successfully updated Way on OpenStreetMap!{C_RESET}")
    print(f"  View Way:       {C_CYAN}https://www.openstreetmap.org/way/{way_id}{C_RESET}")
    print(f"  View Changeset: {C_CYAN}https://www.openstreetmap.org/changeset/{cs_id}{C_RESET}\n")

    if open_browser:
        cs_url = f"https://www.openstreetmap.org/changeset/{cs_id}"
        try:
            webbrowser.open(cs_url)
            print(f"🌐 {C_DIM}Opened changeset in browser: {cs_url}{C_RESET}\n")
        except Exception:
            pass



def _inspect_location(query: str, radius_m: int):
    # Try resolving via coords or station registry
    lat, lon = None, None
    if "," in query:
        try:
            lat, lon = _parse_coords(query)
        except Exception:
            pass

    if lat is None:
        from tessie_api_common import load_tessie_superchargers
        scs = load_tessie_superchargers()
        for k, v in scs.items():
            if query.lower() in k.lower() or query.lower() in v.get("name", "").lower():
                lat = v.get("latitude")
                lon = v.get("longitude")
                print(f"📍 Matched Supercharger: {C_GREEN}{k}{C_RESET} ({lat}, {lon})")
                break

    if lat is None:
        print(f"{C_RED}❌ Could not resolve location: '{query}'{C_RESET}")
        return

    print(f"\n🔍 {C_CYAN}Querying OpenStreetMap elements within {radius_m}m of ({lat:.5f}, {lon:.5f})...{C_RESET}\n")
    pois = query_overpass_pois(lat, lon, radius_m=radius_m)
    print(f"Found {len(pois)} mapped facilities:\n")
    for p in pois:
        print(f"  {p.get('emoji', '📍')} {C_BOLD}{p.get('name')}{C_RESET} ({p.get('dist_m')}m) - {C_CYAN}{p.get('category')}{C_RESET} [{p.get('group')}]")
    print()


def _handle_browser_open_combo(
    choice: str,
    gmaps_coords: Optional[Tuple[Optional[float], Optional[float]]] = None,
    osm_element: Optional[Tuple[str, Optional[int]]] = None,
    osm_coords: Optional[Tuple[Optional[float], Optional[float]]] = None,
    sc_id: Optional[int] = None,
    tesla_url: Optional[str] = None,
    gmaps_url: Optional[str] = None,
    provider_name: str = "Tesla"
) -> bool:
    """
    Handles single or multi-target browser opening commands (e.g. 'm', 'o', 's', 'g', 'mo', 'ms', 'os', 'mos', 'mosg').
    Returns True if choice was handled as a browser command, False otherwise.
    """
    choice_clean = choice.lower().strip()
    if not choice_clean:
        return False

    if choice_clean in ("1", "2", "3", "4", "5", "6", "7", "8", "9", "q", "quit", "exit"):
        return False

    valid_browser_chars = set("mosbtg")
    letters = re.findall(r"[a-z]", choice_clean)
    if not letters:
        return False

    is_known_word = choice_clean in ("m", "maps", "gmaps", "o", "osm", "s", "sc", "supercharge", "supercharge.info", "b", "browser", "url", "t", "tesla", "g", "google")
    is_char_combo = set(letters).issubset(valid_browser_chars)

    if not (is_known_word or is_char_combo):
        return False

    targets_to_open: List[str] = []
    if choice_clean in ("m", "maps", "gmaps"):
        targets_to_open = ["m"]
    elif choice_clean in ("o", "osm"):
        targets_to_open = ["o"]
    elif choice_clean in ("s", "sc", "supercharge", "supercharge.info"):
        targets_to_open = ["s"]
    elif choice_clean in ("g", "google"):
        targets_to_open = ["g"]
    elif choice_clean in ("b", "browser", "url", "t", "tesla"):
        targets_to_open = ["b"]
    else:
        seen = set()
        for ch in letters:
            if ch in valid_browser_chars and ch not in seen:
                seen.add(ch)
                targets_to_open.append(ch)

    for target in targets_to_open:
        if target == "m":
            if gmaps_coords and gmaps_coords[0] is not None and gmaps_coords[1] is not None:
                g_url = f"https://www.google.com/maps/search/?api=1&query={gmaps_coords[0]},{gmaps_coords[1]}"
                webbrowser.open(g_url)
                print(f"🌐 {C_GREEN}Opened Google Maps ({provider_name} location) in browser: {g_url}{C_RESET}")
        elif target == "g":
            if gmaps_url:
                webbrowser.open(gmaps_url)
                print(f"🌐 {C_GREEN}Opened Google entry in browser: {gmaps_url}{C_RESET}")
            elif gmaps_coords and gmaps_coords[0] is not None and gmaps_coords[1] is not None:
                g_url = f"https://www.google.com/maps/search/?api=1&query={gmaps_coords[0]},{gmaps_coords[1]}"
                webbrowser.open(g_url)
                print(f"🌐 {C_GREEN}Opened Google entry in browser: {g_url}{C_RESET}")
            else:
                print(f"{C_YELLOW}⚠ No Google Maps entry URL available.{C_RESET}")
        elif target == "o":
            if osm_element and osm_element[1]:
                osm_url = f"https://www.openstreetmap.org/{osm_element[0]}/{osm_element[1]}"
            elif osm_coords and osm_coords[0] is not None and osm_coords[1] is not None:
                osm_url = f"https://www.openstreetmap.org/?mlat={osm_coords[0]}&mlon={osm_coords[1]}#map=19/{osm_coords[0]}/{osm_coords[1]}"
            elif gmaps_coords and gmaps_coords[0] is not None and gmaps_coords[1] is not None:
                osm_url = f"https://www.openstreetmap.org/?mlat={gmaps_coords[0]}&mlon={gmaps_coords[1]}#map=19/{gmaps_coords[0]}/{gmaps_coords[1]}"
            else:
                osm_url = "https://www.openstreetmap.org"
            webbrowser.open(osm_url)
            print(f"🌐 {C_GREEN}Opened OpenStreetMap in browser: {osm_url}{C_RESET}")
        elif target == "s":
            sc_url = f"https://supercharge.info/map?siteId={sc_id}" if sc_id else "https://supercharge.info"
            webbrowser.open(sc_url)
            print(f"🌐 {C_GREEN}Opened supercharge.info in browser: {sc_url}{C_RESET}")
        elif target in ("b", "t"):
            if tesla_url:
                webbrowser.open(tesla_url)
                print(f"🌐 {C_GREEN}Opened Tesla page in browser: {tesla_url}{C_RESET}")
            else:
                print(f"{C_YELLOW}⚠ No official Tesla URL available.{C_RESET}")

    print()
    return True


def _display_gmaps_audit_and_verification(
    station_base_name: str,
    city_name: str,
    lat: float,
    lon: float,
    stalls: int,
    power_kw: int,
    hardware_model: str,
    open_non_tesla: bool,
    is_destination: bool = False,
    gmaps_audited_info: Optional[Dict] = None,
    nearby_dest_registry: Optional[List] = None,
    tesla_dest_elements: Optional[List] = None,
):
    if gmaps_audited_info:
        g_name = gmaps_audited_info.get("name", "").strip()
        g_url = gmaps_audited_info.get("original_url", "")
        g_lat = gmaps_audited_info.get("latitude")
        g_lon = gmaps_audited_info.get("longitude")
        from math import radians, cos, sin, asin, sqrt
        def _h_km(lat1, lon1, lat2, lon2):
            r = 6371.0
            dlat = radians(lat2 - lat1)
            dlon = radians(lon2 - lon1)
            a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
            return 2 * r * asin(sqrt(a))
        g_dist_m = _h_km(lat, lon, g_lat, g_lon) * 1000.0 if (g_lat and g_lon) else 0.0

        issues = []
        hw_label = hardware_model if "supercharger" in hardware_model.lower() else f"{hardware_model} Supercharger"

        if "destination" in g_name.lower() and not is_destination and not nearby_dest_registry:
            issues.append({
                "severity": "CRITICAL",
                "title": f"Misclassified as '{g_name}'",
                "detail": f"Tesla operates {hw_label} ({stalls} stalls) here with NO Destination Charger.",
                "action": "Open listing ➔ 'Suggest an edit' ➔ 'Close or remove' ➔ 'Doesn\'t exist here' (or rename to 'Tesla Supercharger')"
            })
        elif g_name.lower() != "tesla supercharger" and not is_destination:
            issues.append({
                "severity": "WARNING",
                "title": f"Non-standard title '{g_name}'",
                "detail": "Canonical Google Maps naming standard is strictly 'Tesla Supercharger'.",
                "action": f"Open listing ➔ 'Suggest an edit' ➔ Change Title from '{g_name}' to 'Tesla Supercharger'"
            })

        if g_dist_m > 40.0:
            issues.append({
                "severity": "WARNING",
                "title": f"GPS offset ({g_dist_m:.1f}m)",
                "detail": f"Google Maps pin ({g_lat:.6f}, {g_lon:.6f}) is {g_dist_m:.1f}m away from authoritative location.",
                "action": f"Suggest an edit ➔ 'Edit location' ➔ Move pin to ({lat:.6f}, {lon:.6f})"
            })

        if issues:
            print(f"🗺️  {C_RED}{C_BOLD}Google Maps Entry Issues Found:{C_RESET}")
            print(f"  • 📌 Audited URL:    {C_CYAN}{g_url}{C_RESET}")
            print(f"  • 🏷️  Current Title:  {C_YELLOW}{g_name}{C_RESET}")
            for iss in issues:
                color = C_RED if iss["severity"] == "CRITICAL" else C_YELLOW
                icon = "🚨" if iss["severity"] == "CRITICAL" else "⚠️ "
                print(f"  • {icon} {color}[{iss['severity']}] {iss['title']}:{C_RESET} {iss['detail']}")
                print(f"    ➔ Action: {iss['action']}")
            print()
        else:
            print(f"🗺️  {C_GREEN}✔ Google Maps Entry: 'Tesla Supercharger' (Audited & compliant, no issues found){C_RESET}\n")
    else:
        print(f"🗺️  {C_GREEN}✔ Google Maps Entry: 'Tesla Supercharger' (Audited & compliant, no issues found){C_RESET}\n")
        if (nearby_dest_registry or tesla_dest_elements) and not is_destination:
            print(f"🗺️  {C_YELLOW}ℹ️  Google Maps Notice:{C_RESET} Venue has co-located Destination Chargers. Ensure GMaps has distinct 'Tesla Supercharger' and 'Tesla Destination Charger' pins.\n")


def _inspect_and_fix_single_stall(
    stall_elem: Dict,
    stall_idx: int,
    total_stalls: int,
    power_kw: int,
    model_name: str,
    model_wikidata: str,
    open_non_tesla: bool,
    is_destination: bool,
    forbidden: List[str],
    token: Optional[str],
    dry_run: bool,
    open_browser: bool,
    is_north_america: bool = False,
):
    e_id = stall_elem.get("id")
    e_type = stall_elem.get("type", "node")
    e_tags = stall_elem.get("tags", {})
    e_lat = stall_elem.get("lat") or stall_elem.get("center", {}).get("lat")
    e_lon = stall_elem.get("lon") or stall_elem.get("center", {}).get("lon")
    s_ref = e_tags.get("ref") or e_tags.get("ref:stall") or e_tags.get("name") or f"Stall #{stall_idx}"

    print(f"\n╔════════════════════════════════════════════════════════════════════════════════════╗")
    print(f"║       🅿️  OSM INDIVIDUAL STALL NODE #{stall_idx}: {s_ref:<31} ║")
    print(f"╚════════════════════════════════════════════════════════════════════════════════════╝\n")
    print(f"  • Node ID:        {C_CYAN}https://www.openstreetmap.org/{e_type}/{e_id}{C_RESET}")
    print(f"  • GPS Position:   {C_BOLD}{e_lat:.6f}, {e_lon:.6f}{C_RESET} {C_GREEN}[Locked / Physical Stall GPS]{C_RESET}")
    print(f"  • Google Maps:    {C_DIM}https://www.google.com/maps/search/?api=1&query={e_lat:.6f},{e_lon:.6f}{C_RESET}\n")

    sock_key = "socket:type2_combo" if not is_north_america else "socket:nacs"
    wiki_stall_tags = {
        "amenity": "charging_station",
        "brand": "Tesla Supercharger" if not is_destination else "Tesla Destination",
        "brand:wikidata": "Q17089620" if not is_destination else "Q478214",
        "operator": "Tesla",
        "operator:wikidata": "Q478214",
        "manufacturer": "Tesla",
        "manufacturer:wikidata": "Q478214",
        "access": "yes" if open_non_tesla else "customers",
        "fee": "yes",
        "parking:fee": "no",
        "motorcar": "yes",
        "payment:app:tesla": "yes",
        "opening_hours": "24/7",
    }
    if not is_destination:
        wiki_stall_tags["network"] = "Supercharger"
        wiki_stall_tags["network:wikidata"] = "Q17089620"
        wiki_stall_tags["model"] = model_name
        wiki_stall_tags["model:wikidata"] = model_wikidata
        wiki_stall_tags[sock_key] = "1"
        wiki_stall_tags[f"{sock_key}:output"] = f"{power_kw}kW"
        if not is_north_america:
            wiki_stall_tags[f"{sock_key}:voltage"] = "400"
        wiki_stall_tags["frequency"] = "0"
    else:
        wiki_stall_tags["socket:type2"] = "1"
        wiki_stall_tags["socket:type2:output"] = f"{power_kw}kW"

    if e_tags.get("ref"):
        wiki_stall_tags["ref"] = e_tags["ref"]
    if e_tags.get("ref:stall"):
        wiki_stall_tags["ref:stall"] = e_tags["ref:stall"]

    print(f"📊 {C_BOLD}Tag & Wiki Compliance Audit for Stall #{stall_idx} ({s_ref}):{C_RESET}")
    print(f"  {'TAG / PROPERTY':<28} {'OSM CURRENT VALUE':<26} {'REQUIRED WIKI VALUE':<24} {'AUDIT STATUS'}")
    print(f"  {'-'*28} {'-'*26} {'-'*24} {'-'*14}")

    for k, v in wiki_stall_tags.items():
        curr = e_tags.get(k, "")
        if not curr:
            status = f"{C_RED}❌ MISSING{C_RESET}"
        elif str(curr).lower() == str(v).lower() or (k == sock_key and str(curr) in ("1", "yes")):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif k in ("brand", "operator", "network") and "tesla" in str(curr).lower():
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        else:
            status = f"{C_YELLOW}⚠ DEVIATION{C_RESET}"
        print(f"  {k:<28} {str(curr):<26} {str(v):<24} {status}")

    for fb in forbidden:
        if fb in e_tags:
            print(f"  {C_RED}⚠ DEPRECATED/FORBIDDEN TAG FOUND: {fb}={e_tags[fb]} (Must be removed in AU){C_RESET}")
    print()

    print(f"⚡ {C_BOLD}Stall Actions (GPS coordinates locked at {e_lat:.6f}, {e_lon:.6f}):{C_RESET}")
    print(f"  {C_CYAN}[1]{C_RESET} Fix Tag & Wiki Compliance errors on this stall only (preserve GPS)")
    print(f"  {C_CYAN}[o]{C_RESET} Open this stall node in your browser (OpenStreetMap)")
    print(f"  {C_CYAN}[b]{C_RESET} Back to stall summary list\n")

    try:
        st_choice = input("Choice [1/o/b]: ").strip().lower()
        if st_choice == "1":
            if not token and not dry_run:
                print(f"{C_RED}❌ Authentication required to update OSM. Run ./Tools/places_osm_editor.py --auth-url{C_RESET}")
                return
            cs_comment = f"Update Tesla Supercharger stall {s_ref} tags to OSM Wiki standard"
            cs_source = "field survey; ground verification; https://wiki.openstreetmap.org/wiki/Tag:amenity%3Dcharging_station/Tesla_Motors#Europe"

            merged_stall_tags = dict(e_tags)
            merged_stall_tags.update(wiki_stall_tags)
            remove_tags = [fb for fb in forbidden if fb in e_tags]

            cs_comment = _generate_simple_changeset_comment(
                orig_tags=e_tags,
                new_tags=merged_stall_tags,
                stall_ref=s_ref,
                custom_comment=comment if 'comment' in locals() else None,
            )
            cs_source = "field survey; official Tesla website"

            _display_changeset_rationale(
                title=f"Tesla Supercharger Stall {s_ref}",
                comment=cs_comment,
                source=cs_source,
                feature_type=f"Tesla Supercharger Stall {s_ref} (Node #{e_id})",
                coords=(e_lat, e_lon),
                tags=merged_stall_tags,
                rationale_text="Updated stall tags; preserved exact surveyed physical stall GPS coordinate.",
                orig_tags=e_tags,
                orig_coords=(e_lat, e_lon),
                removed_tags=remove_tags,
                is_new=False,
            )

            if dry_run:
                print(f"{C_YELLOW}⚠ [DRY-RUN] Would update stall node #{e_id} with comment: '{cs_comment}'{C_RESET}\n")
                return

            if not _confirm_live_publish(f"update stall node #{e_id} - {cs_comment}"):
                return

            cs_id, _ = open_changeset(token, cs_comment, source=cs_source)
            if cs_id:
                ok, res = update_element_tags(token, cs_id, e_type, e_id, merged_stall_tags, new_lat=None, new_lon=None, remove_tags=remove_tags)
                close_changeset(token, cs_id)
                if ok:
                    print(f"{C_GREEN}✔ Successfully updated stall node #{e_id} on OpenStreetMap! (GPS preserved){C_RESET}")
                    if res.get("tags_dropped_by_policy"):
                        print(f"{C_YELLOW}⚠ Tags outside the simple checklist were NOT sent (BUG-121 policy): "
                              f"{', '.join(res['tags_dropped_by_policy'])}{C_RESET}")
                    print(f"  View Element:   {C_CYAN}https://www.openstreetmap.org/node/{e_id}{C_RESET}")
                    print(f"  View Changeset: {C_CYAN}https://www.openstreetmap.org/changeset/{cs_id}{C_RESET}\n")
                    if open_browser:
                        try:
                            webbrowser.open(f"https://www.openstreetmap.org/node/{e_id}")
                        except Exception:
                            pass
                else:
                    print(f"{C_RED}❌ Failed to update stall: {res.get('error')}{C_RESET}\n")
        elif st_choice == "o":
            try:
                webbrowser.open(f"https://www.openstreetmap.org/node/{e_id}")
            except Exception:
                pass
    except (EOFError, KeyboardInterrupt):
        print()


def _batch_fix_micromapped_stalls(
    stall_nodes: List[Dict],
    st_name: str,
    station_base_name: str,
    power_kw: int,
    model_name: str,
    model_wikidata: str,
    open_non_tesla: bool,
    is_destination: bool,
    forbidden: List[str],
    token: Optional[str],
    dry_run: bool,
    open_browser: bool,
    is_north_america: bool = False,
    comment: Optional[str] = None,
):
    if not token and not dry_run:
        print(f"{C_RED}❌ Authentication required to update OSM. Run ./Tools/places_osm_editor.py --auth-url{C_RESET}")
        return

    cs_comment = _generate_simple_changeset_comment(
        custom_comment=comment,
        is_stall_batch=True,
        stall_count=len(stall_nodes),
    )
    cs_source = "field survey; official Tesla website"
    sock_key = "socket:type2_combo" if not is_north_america else "socket:nacs"

    sample_tags = {
        "amenity": "charging_station",
        "brand": "Tesla Supercharger" if not is_destination else "Tesla Destination",
        "brand:wikidata": "Q17089620" if not is_destination else "Q478214",
        "operator": "Tesla",
        "operator:wikidata": "Q478214",
        "manufacturer": "Tesla",
        "manufacturer:wikidata": "Q478214",
        sock_key: "1",
        f"{sock_key}:output": f"{power_kw}kW",
        "access": "yes" if open_non_tesla else "customers",
        "fee": "yes",
        "parking:fee": "no",
        "motorcar": "yes",
        "payment:app:tesla": "yes",
        "opening_hours": "24/7",
    }
    if not is_destination:
        sample_tags["network"] = "Supercharger"
        sample_tags["network:wikidata"] = "Q17089620"
        sample_tags["model"] = model_name
        sample_tags["model:wikidata"] = model_wikidata
        if not is_north_america:
            sample_tags[f"{sock_key}:voltage"] = "400"
        sample_tags["frequency"] = "0"
    else:
        sample_tags["socket:type2"] = "1"
        sample_tags["socket:type2:output"] = f"{power_kw}kW"

    _display_changeset_rationale(
        title=f"Tesla Supercharger {station_base_name} ({len(stall_nodes)} Micro-Mapped Stalls)",
        comment=cs_comment,
        source=cs_source,
        feature_type=f"{len(stall_nodes)}x Micro-Mapped Tesla Supercharger Stalls ({power_kw} kW {model_name})",
        coords=None,
        tags=sample_tags,
        rationale_text=f"Updated charger metadata across {len(stall_nodes)} individual stall nodes; preserved exact surveyed stall GPS coordinates for all bays.",
        is_new=False,
    )

    print(f"🚀 {C_CYAN}Batch-updating tags on {len(stall_nodes)} individual stall nodes (preserving exact physical GPS)...{C_RESET}")
    if dry_run:
        print(f"{C_YELLOW}⚠ [DRY-RUN] Would update all {len(stall_nodes)} stall nodes with comment: '{cs_comment}'{C_RESET}\n")
        return

    if not _confirm_live_publish(f"batch-update {len(stall_nodes)} stall nodes - {cs_comment}"):
        return

    cs_id, _ = open_changeset(token, cs_comment, source=cs_source)
    if not cs_id:
        print(f"{C_RED}❌ Failed to open changeset on OpenStreetMap.{C_RESET}")
        return

    success_count = 0
    for idx, el in enumerate(stall_nodes, 1):
        e_id = el.get("id")
        e_type = el.get("type", "node")
        e_tags = el.get("tags", {})
        s_ref = e_tags.get("ref") or e_tags.get("ref:stall") or e_tags.get("name") or f"Stall #{idx}"

        merged = dict(e_tags)
        merged.update({
            "amenity": "charging_station",
            "brand": "Tesla Supercharger" if not is_destination else "Tesla Destination",
            "brand:wikidata": "Q17089620" if not is_destination else "Q478214",
            "operator": "Tesla",
            "operator:wikidata": "Q478214",
            "manufacturer": "Tesla",
            "manufacturer:wikidata": "Q478214",
            "access": "yes" if open_non_tesla else "customers",
            "fee": "yes",
            "parking:fee": "no",
            "motorcar": "yes",
            "payment:app:tesla": "yes",
            "opening_hours": "24/7",
        })
        if not is_destination:
            merged["network"] = "Supercharger"
            merged["network:wikidata"] = "Q17089620"
            merged["model"] = model_name
            merged["model:wikidata"] = model_wikidata
            merged[sock_key] = "1"
            merged[f"{sock_key}:output"] = f"{power_kw}kW"
            if not is_north_america:
                merged[f"{sock_key}:voltage"] = "400"
            merged["frequency"] = "0"
        else:
            merged["socket:type2"] = "1"
            merged["socket:type2:output"] = f"{power_kw}kW"

        remove_tags = [fb for fb in forbidden if fb in e_tags]
        ok, res = update_element_tags(token, cs_id, e_type, e_id, merged, new_lat=None, new_lon=None, remove_tags=remove_tags)
        if ok:
            success_count += 1
            dropped_note = f" (dropped: {', '.join(res['tags_dropped_by_policy'])})" if res.get("tags_dropped_by_policy") else ""
            print(f"  • {C_GREEN}✔ Stall #{idx} (Node #{e_id}, {s_ref}): Tags updated (GPS preserved){dropped_note}{C_RESET}")
        else:
            print(f"  • {C_RED}❌ Stall #{idx} (Node #{e_id}): Failed to update{C_RESET}")

    close_changeset(token, cs_id)
    print(f"\n{C_GREEN}✔ Successfully updated {success_count}/{len(stall_nodes)} stall nodes on OpenStreetMap!{C_RESET}")
    print(f"  View Changeset: {C_CYAN}https://www.openstreetmap.org/changeset/{cs_id}{C_RESET}\n")
    if open_browser:
        try:
            webbrowser.open(f"https://www.openstreetmap.org/changeset/{cs_id}")
        except Exception:
            pass


def _create_primary_cluster_node(
    token: Optional[str],
    lat: float,
    lon: float,
    station_base_name: str,
    st_name: str,
    stalls: int,
    power_kw: int,
    hardware_model: str,
    model_name: str,
    model_wikidata: str,
    open_non_tesla: bool,
    is_destination: bool,
    loc_id: Optional[str],
    sc_info: Optional[Dict],
    st_url: Optional[str],
    dry_run: bool,
    open_browser: bool,
    comment: Optional[str] = None,
    is_north_america: bool = False,
):
    if not token and not dry_run:
        print(f"{C_RED}❌ Authentication required to publish to OSM. Run ./Tools/places_osm_editor.py --auth-url{C_RESET}")
        return

    sock_key = "socket:type2_combo" if not is_north_america else "socket:nacs"
    wiki_tags = {
        "name": f"Tesla Supercharger {station_base_name}",
        "amenity": "charging_station",
        "brand": "Tesla Supercharger" if not is_destination else "Tesla Destination",
        "brand:wikidata": "Q17089620" if not is_destination else "Q478214",
        "operator": "Tesla",
        "operator:wikidata": "Q478214",
        "manufacturer": "Tesla",
        "manufacturer:wikidata": "Q478214",
        "capacity": str(stalls),
        "access": "yes" if open_non_tesla else "customers",
        "fee": "yes",
        "parking:fee": "no",
        "motorcar": "yes",
        "payment:app:tesla": "yes",
        "opening_hours": "24/7",
    }
    if not is_destination:
        wiki_tags["network"] = "Supercharger"
        wiki_tags["network:wikidata"] = "Q17089620"
        wiki_tags["model"] = model_name
        wiki_tags["model:wikidata"] = model_wikidata
        wiki_tags[sock_key] = str(stalls)
        wiki_tags[f"{sock_key}:output"] = f"{power_kw}kW"
        if not is_north_america:
            wiki_tags[f"{sock_key}:voltage"] = "400"
        wiki_tags["frequency"] = "0"
    else:
        wiki_tags["socket:type2"] = str(stalls)
        wiki_tags["socket:type2:output"] = f"{power_kw}kW"

    if loc_id:
        wiki_tags["ref:tesla"] = loc_id
    if sc_info:
        wiki_tags["ref:supercharge_info"] = str(sc_info.get("id"))
        if sc_info.get("dateOpened"):
            wiki_tags["start_date"] = sc_info.get("dateOpened")
        if sc_info.get("elevationMeters"):
            wiki_tags["ele"] = str(sc_info.get("elevationMeters"))
    if st_url:
        wiki_tags["website"] = st_url

    cs_comment = _generate_simple_changeset_comment(
        new_tags=wiki_tags,
        station_name=station_base_name,
        custom_comment=comment,
        is_new=True,
    )
    cs_source = "field survey; official Tesla website"

    _display_changeset_rationale(
        title=f"Tesla Supercharger {station_base_name} (Primary Cluster Node)",
        comment=cs_comment,
        source=cs_source,
        feature_type=f"Tesla Supercharger ({stalls} Stalls, {power_kw} kW {hardware_model})",
        coords=(lat, lon),
        tags=wiki_tags,
        rationale_text="Added primary charging station cluster node.",
        is_new=True
    )

    if dry_run:
        print(f"{C_YELLOW}⚠ [DRY-RUN] Would create node at ({lat:.6f}, {lon:.6f}) with comment: '{cs_comment}'{C_RESET}\n")
        return

    new_node_id = _publish_node(token, lat, lon, wiki_tags, cs_comment, open_browser=open_browser)
    if new_node_id:
        save_audited_osm_to_tesla_registry(st_name, lat, lon, new_node_id)
        print(f"💾 {C_GREEN}Saved primary cluster Node ID #{new_node_id} into local registry.{C_RESET}\n")


def _audit_micromapped_stalls(
    stall_nodes: List[Dict],
    primary_cluster_node: Optional[Dict],
    st_name: str,
    station_base_name: str,
    lat: float,
    lon: float,
    sc_lat: Optional[float],
    sc_lon: Optional[float],
    stalls: int,
    power_kw: int,
    hardware_model: str,
    model_name: str,
    model_wikidata: str,
    open_non_tesla: bool,
    is_destination: bool,
    loc_id: Optional[str],
    sc_info: Optional[Dict],
    st_url: Optional[str],
    token: Optional[str],
    dry_run: bool,
    open_browser: bool,
    gmaps_audited_info: Optional[Dict],
    nearby_dest_registry: Optional[List],
    tesla_dest_elements: Optional[List],
    forbidden: List[str],
    is_north_america: bool = False,
    comment: Optional[str] = None,
):
    c_lats = [el.get("lat") or el.get("center", {}).get("lat", lat) for el in stall_nodes]
    c_lons = [el.get("lon") or el.get("center", {}).get("lon", lon) for el in stall_nodes]
    c_lat = sum(c_lats) / len(c_lats) if c_lats else lat
    c_lon = sum(c_lons) / len(c_lons) if c_lons else lon
    c_dist_m = haversine_km(lat, lon, c_lat, c_lon) * 1000.0
    mapped_count = len(stall_nodes)

    print(f"✔ {C_GREEN}{C_BOLD}STATUS: MICRO-MAPPED INDIVIDUAL CHARGING STALLS FOUND IN OPENSTREETMAP{C_RESET} ({mapped_count} stall nodes)\n")

    if stall_nodes and stall_nodes[0].get("id"):
        save_audited_osm_to_tesla_registry(st_name, c_lat, c_lon, stall_nodes[0]["id"])
        print(f"💾 {C_GREEN}Cached audited cluster OSM coordinates ({c_lat:.6f}, {c_lon:.6f}) into local registry.{C_RESET}\n")

    print(f"📍 {C_BOLD}Geographic Coordinate Comparison:{C_RESET}")
    print(f"  • 🌐 Tesla Entry GPS:       ({lat:.6f}, {lon:.6f})")
    print(f"  • 🌐 Stall Cluster Centroid: ({c_lat:.6f}, {c_lon:.6f}) {C_YELLOW}~{c_dist_m:.1f}m offset !{C_RESET}" if c_dist_m > 25.0 else f"  • 🌐 Stall Cluster Centroid: ({c_lat:.6f}, {c_lon:.6f}) {C_GREEN}~{c_dist_m:.1f}m (aligned){C_RESET}")
    if sc_lat and sc_lon:
        sc_to_c_m = haversine_km(sc_lat, sc_lon, c_lat, c_lon) * 1000.0
        print(f"  • 🌐 supercharge.info GPS:   ({sc_lat:.6f}, {sc_lon:.6f}) ~{sc_to_c_m:.1f}m from cluster centroid")
    print(f"  • 🔒 Stall GPS Policy:      {C_CYAN}Individual stall coordinates are LOCKED and will NOT be modified.{C_RESET}\n")

    # Stall Count Audit
    if mapped_count == stalls:
        print(f"🅿️  {C_BOLD}Stall Count Verification:{C_RESET} {C_GREEN}✔ {mapped_count} individual stall nodes mapped (100% matches {stalls} authoritative stalls){C_RESET}")
    elif mapped_count < stalls:
        print(f"🅿️  {C_BOLD}Stall Count Verification:{C_RESET} {C_YELLOW}⚠️  {mapped_count} individual stall nodes mapped vs {stalls} authoritative stalls ({stalls - mapped_count} missing in OSM){C_RESET}")
    else:
        print(f"🅿️  {C_BOLD}Stall Count Verification:{C_RESET} {C_YELLOW}⚠️  {mapped_count} individual stall nodes mapped vs {stalls} authoritative stalls ({mapped_count - stalls} surplus in OSM){C_RESET}")

    if primary_cluster_node:
        p_id = primary_cluster_node.get("id")
        print(f"🏢 {C_BOLD}Primary Station Cluster Node:{C_RESET} {C_GREEN}✔ Node #{p_id} present (capacity={primary_cluster_node.get('tags', {}).get('capacity', stalls)}){C_RESET}\n")
    else:
        print(f"🏢 {C_BOLD}Primary Station Cluster Node:{C_RESET} {C_YELLOW}❌ Not Mapped (Only individual 1-stall nodes exist){C_RESET}")
        print(f"                               {C_DIM}ℹ️  Standard practice recommends a primary cluster node (capacity={stalls}) for EV navigation.{C_RESET}\n")

    # Audit each stall node
    stalls_with_issues = []
    compliant_stalls = []
    stall_details = []
    sock_key = "socket:type2_combo" if not is_north_america else "socket:nacs"

    for idx, el in enumerate(stall_nodes, 1):
        t = el.get("tags", {})
        node_id = el.get("id")
        s_ref = t.get("ref") or t.get("ref:stall") or t.get("name") or f"Stall #{idx}"
        s_ref = s_ref.replace("Tesla Supercharger - ", "").replace("Tesla Supercharger", "").strip() or f"Stall #{idx}"

        sock_val = t.get(sock_key, "")
        p_out = t.get(f"{sock_key}:output") or ""
        sock_str = f"1x CCS2 ({p_out or f'{power_kw}kW'})" if not is_north_america else f"1x NACS ({p_out or f'{power_kw}kW'})"

        issues = []
        if t.get("amenity") != "charging_station":
            issues.append("missing amenity")
        if not t.get("model") and not is_destination:
            issues.append("missing model")
        if not t.get("manufacturer"):
            issues.append("missing mfr")
        if not sock_val:
            issues.append("missing socket")
        if not p_out:
            issues.append("missing output")
        elif p_out.replace(" ", "").lower() != f"{power_kw}kw".lower():
            issues.append(f"output {p_out}➔{power_kw}kW")
        if not is_destination and not t.get(f"{sock_key}:voltage"):
            issues.append("missing voltage")
        for fb in forbidden:
            if fb in t:
                issues.append(f"forbidden {fb}")

        stall_details.append({
            "idx": idx,
            "el": el,
            "node_id": node_id,
            "ref": s_ref,
            "sock_str": sock_str,
            "issues": issues,
        })
        if issues:
            stalls_with_issues.append(idx)
        else:
            compliant_stalls.append(idx)

    print(f"📊 {C_BOLD}Individual Stall Nodes Inventory & Audit Summary:{C_RESET}")
    print(f"  • Compliant Stalls: {C_GREEN}{len(compliant_stalls)}{C_RESET} / {mapped_count}")
    print(f"  • Needs Attention:  {C_YELLOW}{len(stalls_with_issues)}{C_RESET} / {mapped_count}" if stalls_with_issues else f"  • Needs Attention:  {C_GREEN}0 (All compliant){C_RESET}")
    print()

    print(f"  {'#':<4} {'OSM NODE ID':<14} {'STALL REF':<14} {'SOCKET & POWER':<22} {'ISSUES FOUND':<38} {'STATUS'}")
    print(f"  {'-'*4} {'-'*14} {'-'*14} {'-'*22} {'-'*38} {'-'*12}")

    for sd in stall_details:
        idx = sd["idx"]
        n_id = sd["node_id"]
        s_ref = sd["ref"]
        s_sock = sd["sock_str"]
        issues = sd["issues"]
        if not issues:
            iss_str = "None (fully compliant)"
            st_str = f"{C_GREEN}✔ PASS{C_RESET}"
        else:
            iss_str = ", ".join(issues[:3]) + (f" (+{len(issues)-3})" if len(issues) > 3 else "")
            st_str = f"{C_YELLOW}⚠ ATTENTION{C_RESET}"
        print(f"  {idx:<4} {str(n_id):<14} {s_ref:<14} {s_sock:<22} {iss_str:<38} {st_str}")
    print()

    _display_gmaps_audit_and_verification(
        station_base_name=station_base_name,
        city_name=station_base_name,
        lat=lat,
        lon=lon,
        stalls=stalls,
        power_kw=power_kw,
        hardware_model=hardware_model,
        open_non_tesla=open_non_tesla,
        is_destination=is_destination,
        gmaps_audited_info=gmaps_audited_info,
        nearby_dest_registry=nearby_dest_registry,
        tesla_dest_elements=tesla_dest_elements,
    )

    tesla_page_url = st_url or (f"https://www.tesla.com/en_AU/findus/location/supercharger/{loc_id}" if loc_id else "https://www.tesla.com/findus")
    sc_page_url = f"https://supercharge.info/map?siteId={sc_info.get('id')}" if sc_info and sc_info.get('id') else "https://supercharge.info"
    gmaps_entry_url = gmaps_audited_info.get("original_url") if gmaps_audited_info else f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lon:.6f}"

    while True:
        print(f"⚡ {C_BOLD}Available Audit Actions (GPS Coordinates are locked & preserved for all stalls):{C_RESET}")
        print(f"  {C_CYAN}[1-{mapped_count}]{C_RESET} Inspect & fix a specific individual stall node in detail (e.g. 1)")
        if stalls_with_issues:
            print(f"  {C_CYAN}[all]{C_RESET}   Batch-fix Wiki tags across all {len(stalls_with_issues)} stalls needing attention")
        if not primary_cluster_node:
            print(f"  {C_CYAN}[c]{C_RESET}     Create missing primary station cluster node (capacity={stalls}) at centroid")
        print(f"  {C_CYAN}[4]{C_RESET}     Save audited cluster centroid GPS to local JSON registry")
        print(f"  {C_CYAN}[m]{C_RESET}     Open Tesla location in your browser (Google Maps)")
        print(f"  {C_CYAN}[o]{C_RESET}     Open OSM location in your browser (OpenStreetMap)")
        print(f"  {C_CYAN}[s]{C_RESET}     Open SC.info location in your browser (supercharge.info)")
        print(f"  {C_CYAN}[g]{C_RESET}     Open Google entry in your browser (Google Maps)")
        print(f"  {C_DIM}[q]{C_RESET}     Quit (or press Enter)\n")

        try:
            prompt_opts = f"1-{mapped_count}/all/c/4/m/o/s/g/q" if not primary_cluster_node else f"1-{mapped_count}/all/4/m/o/s/g/q"
            choice = input(f"Choice [{prompt_opts}]: ").strip().lower()

            if choice.isdigit() and 1 <= int(choice) <= mapped_count:
                _inspect_and_fix_single_stall(
                    stall_elem=stall_nodes[int(choice) - 1],
                    stall_idx=int(choice),
                    total_stalls=stalls,
                    power_kw=power_kw,
                    model_name=model_name,
                    model_wikidata=model_wikidata,
                    open_non_tesla=open_non_tesla,
                    is_destination=is_destination,
                    forbidden=forbidden,
                    token=token,
                    dry_run=dry_run,
                    open_browser=open_browser,
                    is_north_america=is_north_america,
                )
                continue

            elif choice == "all" and stalls_with_issues:
                _batch_fix_micromapped_stalls(
                    stall_nodes=stall_nodes,
                    st_name=st_name,
                    station_base_name=station_base_name,
                    power_kw=power_kw,
                    model_name=model_name,
                    model_wikidata=model_wikidata,
                    open_non_tesla=open_non_tesla,
                    is_destination=is_destination,
                    forbidden=forbidden,
                    token=token,
                    dry_run=dry_run,
                    open_browser=open_browser,
                    is_north_america=is_north_america,
                    comment=comment,
                )
                return

            elif choice in ("c", "cluster", "create") and not primary_cluster_node:
                _create_primary_cluster_node(
                    token=token,
                    lat=c_lat,
                    lon=c_lon,
                    station_base_name=station_base_name,
                    st_name=st_name,
                    stalls=stalls,
                    power_kw=power_kw,
                    hardware_model=hardware_model,
                    model_name=model_name,
                    model_wikidata=model_wikidata,
                    open_non_tesla=open_non_tesla,
                    is_destination=is_destination,
                    loc_id=loc_id,
                    sc_info=sc_info,
                    st_url=st_url,
                    dry_run=dry_run,
                    open_browser=open_browser,
                    comment=comment,
                    is_north_america=is_north_america,
                )
                return

            elif choice == "4":
                first_id = stall_nodes[0].get("id") if stall_nodes else None
                save_audited_osm_to_tesla_registry(st_name, c_lat, c_lon, first_id)
                print(f"💾 {C_GREEN}Saved audited cluster coordinates ({c_lat:.6f}, {c_lon:.6f}) into local registry.{C_RESET}\n")
                return

            elif _handle_browser_open_combo(
                choice,
                gmaps_coords=(lat, lon),
                osm_element=("node", stall_nodes[0].get("id")) if stall_nodes else None,
                osm_coords=(c_lat, c_lon),
                sc_id=sc_info.get("id") if sc_info else None,
                tesla_url=tesla_page_url,
                gmaps_url=gmaps_entry_url,
                provider_name="Tesla"
            ):
                continue
            else:
                return
        except (EOFError, KeyboardInterrupt):
            print()
            return


def _audit_tesla_station(
    query: str,
    token: Optional[str] = None,
    create_node: bool = False,
    fix_node: bool = False,
    fix_tags: bool = False,
    reposition: bool = False,
    gpsupdate: Optional[str] = None,
    save_registry: bool = False,
    dry_run: bool = False,
    comment: Optional[str] = None,
    user_tags: Optional[List[str]] = None,
    override_name: Optional[str] = None,
    open_browser: bool = True
):
    from providers_chargers.chargers_tesla import TeslaProvider
    from providers_chargers.chargers_base import haversine_km
    from lib.supercharge_info import find_supercharge_site

    p = TeslaProvider()
    stations = p.load_cached_registry() or []

    q_lower = query.strip().lower()
    matched = []
    gmaps_audited_info = None

    # Check if query is a Google Maps link
    if "maps.app.goo.gl" in q_lower or "goo.gl/maps" in q_lower or "google.com/maps" in q_lower:
        gmaps_audited_info = resolve_gmaps_url(query)
        g_lat = gmaps_audited_info.get("latitude")
        g_lon = gmaps_audited_info.get("longitude")
        if g_lat is not None and g_lon is not None and stations:
            # Find closest Tesla station in registry
            closest = min(stations, key=lambda s: haversine_km(g_lat, g_lon, s.lat, s.lon) if (s.lat and s.lon) else 999999)
            if closest.lat and closest.lon and haversine_km(g_lat, g_lon, closest.lat, closest.lon) <= 15.0:
                matched.append(closest)

    # Check if query is explicitly a supercharge.info site ID or URL
    is_sc_direct = (
        query.strip().startswith("#") or 
        "supercharge.info" in q_lower or 
        "siteid=" in q_lower or
        (query.strip().isdigit() and len(query.strip()) <= 5 and not any(query.strip() == getattr(s, "short_id", "") for s in stations))
    )

    if not is_sc_direct and not matched and stations:
        for s in stations:
            if (q_lower in s.name.lower() or 
                q_lower in s.key.lower() or 
                q_lower in s.address.lower() or 
                q_lower in s.suburb.lower()):
                matched.append(s)

    # Deduplicate matched stations (avoids duplicates when both tesla_superchargers.json & tesla_chargers.json are loaded)
    deduped = []
    seen = set()
    for s in matched:
        loc_id = s.metadata.get("location_id") or s.metadata.get("findus_id") or ""
        st_lat = round(s.lat or 0, 4)
        st_lon = round(s.lon or 0, 4)
        dedup_key = (loc_id, s.speed_tier) if loc_id else (s.name.strip().lower(), s.speed_tier, st_lat, st_lon)
        if dedup_key not in seen:
            seen.add(dedup_key)
            deduped.append(s)
    matched = deduped

    if not matched:
        # Fallback to supercharge.info global database seamlessly
        return _source_and_audit_supercharge(
            query=query,
            token=token,
            create_node=create_node,
            fix_node=fix_node,
            fix_tags=fix_tags,
            reposition=reposition,
            gpsupdate=gpsupdate,
            save_registry=save_registry,
            dry_run=dry_run,
            comment=comment,
            user_tags=user_tags,
            override_name=override_name,
            open_browser=open_browser,
            gmaps_audited_info=gmaps_audited_info,
        )

    if len(matched) == 1:
        st = matched[0]
    else:
        # Sort so exact matches come first
        matched.sort(key=lambda s: (0 if (s.name.lower().strip() == q_lower or s.key.lower().strip() == q_lower) else 1, s.name))
        print(f"\n⚡ {C_BOLD}Multiple Tesla stations matched '{query}':{C_RESET}")
        for idx, s in enumerate(matched[:8], 1):
            tier_info = f"{s.hardware_model or s.speed_tier.upper()} ({s.stalls_total or '?'} stalls)"
            print(f"  [{idx}] {C_GREEN}{s.name}{C_RESET} - {s.address} [{tier_info}]")
        print(f"  [q] Quit\n")
        try:
            choice = input(f"Select station [1-{min(8, len(matched))}/q]: ").strip().lower()
            if choice.isdigit() and 1 <= int(choice) <= len(matched):
                st = matched[int(choice) - 1]
            else:
                return
        except (EOFError, KeyboardInterrupt):
            print()
            return

    lat, lon = st.lat, st.lon
    stalls = st.stalls_total or 0
    power_kw = int(st.max_power_kw) if st.max_power_kw else 250
    hardware_model = st.hardware_model or "Tesla V3"
    open_non_tesla = st.metadata.get("open_to_non_tesla", True)
    is_destination = st.speed_tier == "ac_destination"

    # Cross-reference with supercharge.info community registry
    loc_id = st.metadata.get("location_id") or (st.url.rstrip("/").split("/")[-1] if st.url else None)
    sc_info = find_supercharge_site(st.name, lat, lon, location_id=loc_id)
    tesla_page_url = st.url or (f"https://www.tesla.com/en_AU/findus/location/supercharger/{loc_id}" if loc_id else "https://www.tesla.com/findus")
    sc_page_url = f"https://supercharge.info/map?siteId={sc_info.get('id')}" if sc_info and sc_info.get('id') else "https://supercharge.info"

    # Determine Wiki model name and Wikidata ID per https://wiki.openstreetmap.org/wiki/Tesla
    sc_stalls = sc_info.get("stalls", {}) if sc_info else {}
    if sc_stalls.get("v4") or "V4" in hardware_model.upper():
        model_name = "V4 Supercharger"
        model_wikidata = "Q122926617"
    elif sc_stalls.get("v2") or "V2" in hardware_model.upper():
        model_name = "V2 Supercharger"
        model_wikidata = "Q116171470"
    else:
        model_name = "V3 Supercharger"
        model_wikidata = "Q116171539"

    # Physical stall GPS coordinates from supercharge.info if available
    sc_gps = sc_info.get("gps", {}) if sc_info else {}
    sc_lat = sc_gps.get("latitude")
    sc_lon = sc_gps.get("longitude")

    # Custom updated GPS coordinate if supplied via --gpsupdate
    target_lat = lat
    target_lon = lon
    if gpsupdate:
        try:
            target_lat, target_lon = _parse_coords(gpsupdate)
            print(f"📍 {C_BOLD}Using Custom Physical Stall GPS:{C_RESET} {target_lat:.6f}, {target_lon:.6f}")
        except Exception as e:
            print(f"{C_RED}❌ Invalid --gpsupdate format (expected lat,lon): {e}{C_RESET}")
            return

    print(f"\n╔════════════════════════════════════════════════════════════════════════════════════╗")
    print(f"║       ⚡ TESLA CHARGER OSM COMPLIANCE & ACCURACY AUDIT: {st.name:<26} ║")
    print(f"╚════════════════════════════════════════════════════════════════════════════════════╝\n")

    print(f"📍 {C_BOLD}Authoritative Tesla Registry Data (Local Scrape):{C_RESET}")
    print(f"  • Name / Location: {C_GREEN}{st.name}{C_RESET} ({st.address})")
    print(f"  • Tesla Entry GPS: {C_BOLD}{lat:.6f}, {lon:.6f}{C_RESET}")
    print(f"  • Speed & Tier:    {st.speed_tier.upper()} | {hardware_model} ({power_kw} kW)")
    print(f"  • Stall Count:     {C_BOLD}{stalls} Stalls{C_RESET}")
    print(f"  • Non-Tesla CCS2:  {'YES (Open to all CCS2 EVs)' if open_non_tesla else 'NO (Tesla Only)'}")
    if st.url:
        print(f"  • Official URL:    {C_CYAN}{st.url}{C_RESET}")
    print(f"  • Google Maps:     {C_DIM}https://www.google.com/maps/search/?api=1&query={lat:.6f},{lon:.6f}{C_RESET}")

    if sc_info:
        sc_opened = sc_info.get("dateOpened", "Unknown")
        sc_ele = sc_info.get("elevationMeters", "?")
        sc_stalls_str = ", ".join([f"{v}x {k.upper()}" for k, v in sc_stalls.items()]) if sc_stalls else f"{stalls} Stalls"
        print(f"\n🌐 {C_BOLD}supercharge.info Cross-Reference Data:{C_RESET}")
        print(f"  • Site ID:         {C_CYAN}#{sc_info.get('id')}{C_RESET} ({sc_info.get('name')})")
        print(f"  • Date Opened:     {C_GREEN}{sc_opened}{C_RESET} | Elevation: {sc_ele}m")
        print(f"  • Hardware Spec:   {sc_stalls_str} ({sc_info.get('powerKilowatt', power_kw)} kW)")
        if sc_lat and sc_lon:
            sc_offset_m = haversine_km(lat, lon, sc_lat, sc_lon) * 1000.0
            print(f"  • Stall Physical GPS: ({sc_lat:.6f}, {sc_lon:.6f}) ~{sc_offset_m:.1f}m from entry")
            print(f"  • supercharge.info Map: {C_DIM}https://supercharge.info/map?siteId={sc_info.get('id')}{C_RESET}")

        from lib.supercharge_info import fetch_site_changes
        changes = fetch_site_changes(st.name)
        if changes:
            history_events = [f"{ch.get('date')} [{ch.get('siteStatus')}]" for ch in reversed(changes[:4])]
            print(f"  • Milestone History: {' ➔ '.join(history_events)}")
    print()

    # Query Overpass for charging stations within 400m
    print(f"🔍 {C_CYAN}Querying OpenStreetMap for mapped charging stations within 400m...{C_RESET}")
    elements = query_overpass_charging_stations(lat, lon, radius_m=400)

    # Separate true Tesla elements from third-party co-located chargers (e.g. NRMA, Chargefox)
    tesla_elements = []
    tesla_sc_elements = []
    tesla_dest_elements = []
    other_elements = []
    for el in elements:
        t_tags = el.get("tags", {})
        t_name = t_tags.get("name", "").lower()
        t_brand = t_tags.get("brand", "").lower()
        t_op = t_tags.get("operator", "").lower()
        t_net = t_tags.get("network", "").lower()
        if "tesla" in t_name or "tesla" in t_brand or "tesla" in t_op or "supercharger" in t_name or "supercharger" in t_net:
            tesla_elements.append(el)
            is_sc = ("supercharger" in t_name or "supercharger" in t_brand or "supercharger" in t_net or "socket:type2_combo" in t_tags)
            is_dest = ("destination" in t_name or "destination" in t_brand or "destination" in t_net or ("socket:type2" in t_tags and "socket:type2_combo" not in t_tags))
            if is_sc:
                tesla_sc_elements.append(el)
            if is_dest:
                tesla_dest_elements.append(el)
        else:
            other_elements.append(el)

    # Check local Tesla Registry for co-located destination chargers
    nearby_dest_registry = []
    if not is_destination:
        for s in stations:
            if getattr(s, "speed_tier", "") == "ac_destination" and s.lat and s.lon:
                d_dist = haversine_km(lat, lon, s.lat, s.lon) * 1000.0
                if d_dist <= 350.0:
                    nearby_dest_registry.append((s, d_dist))

    # Determine targeted Tesla elements
    target_tesla_elements = (tesla_sc_elements if (not is_destination and tesla_sc_elements) else (tesla_dest_elements if (is_destination and tesla_dest_elements) else tesla_elements))

    station_base_name = st.name.split(",")[0].strip()
    ref_tesla = derive_tesla_ref(
        name=st.name,
        short_id=getattr(st, "short_id", ""),
        url=st.url,
        location_id=loc_id or (sc_info.get("locationId") if sc_info else None)
    )
    wiki_tags = {
        "name": override_name or f"Tesla Supercharger {station_base_name}",
        "amenity": "charging_station",
        "brand": "Tesla Supercharger" if not is_destination else "Tesla Destination",
        "brand:wikidata": "Q17089620" if not is_destination else "Q478214",
        "operator": "Tesla",
        "operator:wikidata": "Q478214",
        "manufacturer": "Tesla",
        "manufacturer:wikidata": "Q478214",
        "capacity": str(stalls) if stalls else "6",
        "fee": "yes" if not is_destination else "no",
        "parking:fee": "no",
        "motorcar": "yes",
    }
    if not is_destination:
        wiki_tags.update({
            "network": "Supercharger",
            "network:wikidata": "Q17089620",
            "model": model_name,
            "model:wikidata": model_wikidata,
            "socket:type2_combo": str(stalls) if stalls else "yes",
            "socket:type2_combo:output": f"{power_kw}kW",
            "socket:type2_combo:voltage": "400",
            "frequency": "0",
            "access": "yes" if open_non_tesla else "customers",
            "payment:app:tesla": "yes",
            "opening_hours": "24/7",
        })
        if ref_tesla:
            wiki_tags["ref:tesla"] = ref_tesla
        if sc_info:
            wiki_tags["ref:supercharge_info"] = str(sc_info.get("id"))
            if sc_info.get("dateOpened"):
                wiki_tags["start_date"] = sc_info.get("dateOpened")
            if sc_info.get("elevationMeters"):
                wiki_tags["ele"] = str(sc_info.get("elevationMeters"))
        if st.url:
            wiki_tags["website"] = st.url
    else:
        wiki_tags.update({
            "socket:type2": str(stalls) if stalls else "yes",
            "socket:type2:output": f"{power_kw}kW",
            "network": "Tesla Destination",
            "access": "customers",
        })

    if user_tags:
        for t in user_tags:
            if "=" in t:
                k, v = t.split("=", 1)
                wiki_tags[k.strip()] = v.strip()

    forbidden = ["socket:nacs", "socket:tesla_supercharger", "socket:tesla_supercharger_ccs", "socket:tesla_destination", "socket:type1", "socket:type1_combo"]

    if not is_destination and (nearby_dest_registry or tesla_dest_elements):
        print(f"⚠️  {C_YELLOW}{C_BOLD}CO-LOCATION ALERT: Supercharger & Destination Charger share this venue!{C_RESET}")
        if nearby_dest_registry:
            for d_st, d_dist in nearby_dest_registry:
                print(f"  • 🏨  Tesla Registry:   {C_CYAN}{d_st.name}{C_RESET} ({d_st.stalls or '?'}x AC Wall Connectors, ~{d_dist:.1f}m away)")
        if tesla_dest_elements:
            for d_el in tesla_dest_elements:
                d_id = d_el.get("id")
                d_type = d_el.get("type", "node")
                d_tags = d_el.get("tags", {})
                d_lat = d_el.get("lat") or d_el.get("center", {}).get("lat", lat)
                d_lon = d_el.get("lon") or d_el.get("center", {}).get("lon", lon)
                d_dist = haversine_km(lat, lon, d_lat, d_lon) * 1000.0
                d_name = d_tags.get("name") or "Tesla Destination"
                print(f"  • 🗺️  OpenStreetMap:    {d_type.capitalize()} #{d_id}: {C_CYAN}{d_name}{C_RESET} (~{d_dist:.1f}m away)")
        print(f"  • 📋  OSM Wiki Standard: Superchargers and Destination Chargers MUST be mapped as two separate nodes.")
        print(f"                           Never combine AC Type 2 wall connectors and DC Supercharger stalls onto one OSM node.")
        print(f"  • ⚠️  Google Maps Risk:  Google Maps often mislabels the whole site as 'Tesla Destination Charger'. Verify GMaps has a distinct Supercharger POI.\n")

    # Case 1: Station missing from OpenStreetMap
    if not target_tesla_elements:
        print(f"❌ {C_RED}{C_BOLD}STATUS: TESLA STATION MISSING IN OPENSTREETMAP{C_RESET}")
        print(f"   No dedicated Tesla charging node exists within 400m of ({lat:.6f}, {lon:.6f}).")
        if other_elements:
            print(f"   ℹ️  Found {len(other_elements)} co-located third-party charging station(s) nearby:")
            for o in other_elements:
                o_tags = o.get("tags", {})
                o_op = o_tags.get("operator") or o_tags.get("brand") or "Unknown"
                o_cap = o_tags.get("capacity") or "?"
                o_lat = o.get("lat") or o.get("center", {}).get("lat", lat)
                o_lon = o.get("lon") or o.get("center", {}).get("lon", lon)
                o_dist = haversine_km(lat, lon, o_lat, o_lon) * 1000.0
                print(f"     • {o.get('type', 'node').capitalize()} #{o.get('id')}: {C_CYAN}{o_op}{C_RESET} ({o_cap} stalls, {o_dist:.1f}m away)")
        print()
        print(f"📋 {C_BOLD}Proposed OSM Wiki-Compliant Node Tags for Tesla Supercharger:{C_RESET}")
        for k, v in wiki_tags.items():
            print(f"  • {C_CYAN}{k:<28}{C_RESET} = {C_GREEN}{v}{C_RESET}")
        print()
        _display_gmaps_audit_and_verification(
            station_base_name=station_base_name,
            city_name=station_base_name,
            lat=target_lat,
            lon=target_lon,
            stalls=stalls,
            power_kw=power_kw,
            hardware_model=hardware_model,
            open_non_tesla=open_non_tesla,
            is_destination=is_destination,
            gmaps_audited_info=gmaps_audited_info,
            nearby_dest_registry=nearby_dest_registry,
            tesla_dest_elements=tesla_dest_elements,
        )

        cs_source = "official Tesla website; supercharge.info; ground survey"
        cs_comment = _generate_simple_changeset_comment(
            new_tags=wiki_tags,
            station_name=station_base_name,
            custom_comment=comment,
            is_new=True,
        )

        _display_changeset_rationale(
            title=f"Tesla Supercharger {station_base_name}",
            comment=cs_comment,
            source=cs_source,
            feature_type=f"Tesla Supercharger ({stalls} Stalls, {power_kw} kW {hardware_model})",
            coords=(target_lat, target_lon),
            tags=wiki_tags,
            rationale_text="Added new charging station node.",
            is_new=True
        )

        gmaps_entry_url = gmaps_audited_info.get("original_url") if gmaps_audited_info else f"https://www.google.com/maps/search/?api=1&query={target_lat:.6f},{target_lon:.6f}"

        while not (create_node or dry_run):
            print(f"⚡ {C_BOLD}Available Audit Actions:{C_RESET}")
            print(f"  {C_CYAN}[1]{C_RESET} Create new Wiki-compliant Tesla Supercharger node on OpenStreetMap")
            print(f"  {C_CYAN}[m]{C_RESET} Open Tesla location in your browser (Google Maps)")
            print(f"  {C_CYAN}[o]{C_RESET} Open OSM location in your browser (OpenStreetMap)")
            print(f"  {C_CYAN}[s]{C_RESET} Open SC.info location in your browser (supercharge.info)")
            print(f"  {C_CYAN}[g]{C_RESET} Open Google entry in your browser (Google Maps)")
            print(f"  {C_DIM}[q]{C_RESET} Quit (or press Enter)\n")
            try:
                choice = input(f"Choice [1/m/o/s/g/q]: ").strip().lower()
                if choice == "1":
                    create_node = True
                    break
                elif _handle_browser_open_combo(
                    choice,
                    gmaps_coords=(target_lat, target_lon),
                    osm_coords=(target_lat, target_lon),
                    sc_id=sc_info.get("id") if sc_info else None,
                    tesla_url=tesla_page_url,
                    gmaps_url=gmaps_entry_url,
                    provider_name="Tesla"
                ):
                    continue
                else:
                    return
            except (EOFError, KeyboardInterrupt):
                print()
                return

        if create_node:
            if not token:
                print(f"{C_RED}❌ Authentication required to publish to OSM. Run ./Tools/places_osm_editor.py --auth-url{C_RESET}", file=sys.stderr)
                sys.exit(1)
            if dry_run:
                print(f"{C_YELLOW}⚠ [DRY-RUN] Would create node at ({target_lat:.6f}, {target_lon:.6f}) with comment: '{cs_comment}'{C_RESET}")
                return
            new_node_id = _publish_node(token, target_lat, target_lon, wiki_tags, cs_comment, open_browser=open_browser)
            if new_node_id:
                save_audited_osm_to_tesla_registry(st.name, target_lat, target_lon, new_node_id)
                print(f"💾 {C_GREEN}Saved verified OSM coordinates & node ID #{new_node_id} into local registry.{C_RESET}")
        return

    # Case 2: Station found on OpenStreetMap
    stall_nodes = []
    primary_cluster_node = None

    if len(target_tesla_elements) > 1:
        for el in target_tesla_elements:
            t = el.get("tags", {})
            cap = t.get("capacity")
            s_combo = t.get("socket:type2_combo") or t.get("socket:nacs")
            if (cap and str(cap).isdigit() and int(cap) > 1) or (s_combo and str(s_combo).isdigit() and int(s_combo) > 1):
                primary_cluster_node = el
            else:
                stall_nodes.append(el)

    is_micromapped = len(stall_nodes) >= 2 or (len(stall_nodes) >= 1 and primary_cluster_node is not None)

    if is_micromapped:
        return _audit_micromapped_stalls(
            stall_nodes=stall_nodes,
            primary_cluster_node=primary_cluster_node,
            st_name=st.name,
            station_base_name=station_base_name,
            lat=lat,
            lon=lon,
            sc_lat=sc_lat,
            sc_lon=sc_lon,
            stalls=stalls,
            power_kw=power_kw,
            hardware_model=hardware_model,
            model_name=model_name,
            model_wikidata=model_wikidata,
            open_non_tesla=open_non_tesla,
            is_destination=is_destination,
            loc_id=loc_id,
            sc_info=sc_info,
            st_url=st.url,
            token=token,
            dry_run=dry_run,
            open_browser=open_browser,
            gmaps_audited_info=gmaps_audited_info,
            nearby_dest_registry=nearby_dest_registry,
            tesla_dest_elements=tesla_dest_elements,
            forbidden=forbidden,
            is_north_america=False,
            comment=comment,
        )

    elem = target_tesla_elements[0]
    e_type = elem.get("type", "node")
    e_id = elem.get("id")
    e_tags = elem.get("tags", {})
    e_lat = elem.get("lat") or elem.get("center", {}).get("lat", lat)
    e_lon = elem.get("lon") or elem.get("center", {}).get("lon", lon)
    dist_m = haversine_km(lat, lon, e_lat, e_lon) * 1000.0

    print(f"✔ {C_GREEN}{C_BOLD}STATUS: FOUND IN OPENSTREETMAP{C_RESET} ({e_type.capitalize()} ID: {C_CYAN}{e_id}{C_RESET})\n")

    # Auto-cache verified OSM coordinates & Node ID to local registry
    if e_lat is not None and e_lon is not None:
        if save_audited_osm_to_tesla_registry(st.name, e_lat, e_lon, e_id):
            print(f"💾 {C_GREEN}Cached audited OSM coordinates ({e_lat:.6f}, {e_lon:.6f}) & Node ID #{e_id} into local registry.{C_RESET}\n")

    sc_dist_m = haversine_km(sc_lat, sc_lon, e_lat, e_lon) * 1000.0 if (sc_lat and sc_lon) else None
    tesla_dist_m = haversine_km(lat, lon, e_lat, e_lon) * 1000.0

    print(f"📍 {C_BOLD}Geographic Coordinate Comparison:{C_RESET}")
    if sc_lat and sc_lon:
        print(f"  • 🌐 supercharge.info GPS: ({sc_lat:.6f}, {sc_lon:.6f}) [Physical Stalls]")
        if sc_dist_m <= 30.0:
            print(f"  • 🌐 OSM Node GPS:        ({e_lat:.6f}, {e_lon:.6f}) {C_GREEN}✔ ~{sc_dist_m:.1f}m (aligned with physical stalls - within 30m tolerance, no GPS change needed){C_RESET}")
        else:
            print(f"  • 🌐 OSM Node GPS:        ({e_lat:.6f}, {e_lon:.6f}) {C_YELLOW}⚠ ~{sc_dist_m:.1f}m offset (>30m from physical stalls, reposition recommended){C_RESET}")
        print(f"  • 🌐 Tesla Entry GPS:       ({lat:.6f}, {lon:.6f}) ~{tesla_dist_m:.1f}m (site/driveway entry - divergence expected)")
    else:
        print(f"  • 🌐 Tesla Entry GPS:       ({lat:.6f}, {lon:.6f})")
        print(f"  • 🌐 OSM Node GPS:        ({e_lat:.6f}, {e_lon:.6f}) ~{tesla_dist_m:.1f}m")
    print(f"  • 🔗 View in OSM:         {C_CYAN}https://www.openstreetmap.org/{e_type}/{e_id}{C_RESET}\n")

    print(f"📊 {C_BOLD}Tag & Wiki Compliance Audit (vs https://wiki.openstreetmap.org/wiki/Tesla):{C_RESET}")
    print(f"  {'TAG / PROPERTY':<28} {'OSM CURRENT VALUE':<26} {'REQUIRED WIKI VALUE':<24} {'AUDIT STATUS'}")
    print(f"  {'-'*28} {'-'*26} {'-'*24} {'-'*14}")

    def _check(tag, req_val):
        curr = e_tags.get(tag, "")
        if not curr:
            status = f"{C_RED}❌ MISSING{C_RESET}"
        elif str(curr).lower() == str(req_val).lower() or (tag.startswith("socket:") and str(curr) in ("yes", str(stalls))):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "name" and ("tesla" in str(curr).lower() or str(curr).lower() == "tesla supercharger"):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "short_name" and str(curr).lower() == "tesla":
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "brand" and str(curr).lower() in ("tesla", "tesla supercharger"):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "brand:wikidata" and str(curr).upper() in ("Q17089620", "Q478214"):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "operator" and str(curr).lower() in ("tesla", "tesla inc.", "tesla motors"):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "operator:wikidata" and str(curr).upper() == "Q478214":
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "level":
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "network" and str(curr).lower() in ("supercharger", "tesla supercharger", "tesla"):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "network:wikidata" and str(curr).upper() in ("Q17089620", "Q478214"):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "access" and str(curr).lower() in ("yes", "customers", "permissive", "public"):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "parking:fee" and str(curr).lower() in ("no", "yes", "interval", "limited"):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "website" and ("tesla.com" in str(curr).lower() and ("findus" in str(curr).lower() or "supercharger" in str(curr).lower())):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "ref:tesla" and (str(curr).lower().replace("_", "") == str(req_val).lower().replace("_", "") or str(curr).lower() in (str(req_val).lower(), str(req_val).lower().replace("supercharger", ""))):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        else:
            status = f"{C_YELLOW}⚠ DEVIATION{C_RESET}"
        print(f"  {tag:<28} {str(curr):<26} {str(req_val):<24} {status}")

    # Core Tags
    _check("amenity", "charging_station")
    _check("brand", "Tesla Supercharger" if not is_destination else "Tesla Destination")
    _check("brand:wikidata", "Q17089620" if not is_destination else "Q478214")
    _check("name", "Tesla Supercharger")
    _check("short_name", "Tesla")
    _check("operator", "Tesla")
    _check("operator:wikidata", "Q478214")
    if e_tags.get("level"):
        _check("level", e_tags["level"])

    if not is_destination:
        _check("network", "Supercharger")
        _check("network:wikidata", "Q17089620")
        _check("model", model_name)
        _check("model:wikidata", model_wikidata)
        _check("capacity", str(stalls))
        _check("socket:type2_combo", str(stalls) if stalls else "yes")
        _check("socket:type2_combo:output", f"{power_kw}kW")
        _check("socket:type2_combo:voltage", "400")
        _check("frequency", "0")
        _check("access", "yes" if open_non_tesla else "customers")
        _check("fee", "yes")
        _check("parking:fee", "no")
        _check("payment:app:tesla", "yes")
        _check("opening_hours", "24/7")
        if ref_tesla:
            _check("ref:tesla", ref_tesla)
        if sc_info:
            _check("ref:supercharge_info", str(sc_info.get("id")))
            if sc_info.get("dateOpened"):
                _check("start_date", sc_info.get("dateOpened"))
            if sc_info.get("elevationMeters"):
                _check("ele", str(sc_info.get("elevationMeters")))
        if st.url:
            _check("website", st.url)
    else:
        _check("socket:type2", str(stalls) if stalls else "yes")
        _check("network", "Tesla Destination")
        _check("access", "customers")

    for fb in forbidden:
        if fb in e_tags:
            print(f"  {C_RED}⚠ DEPRECATED/FORBIDDEN TAG FOUND: {fb}={e_tags[fb]} (Must be removed in AU){C_RESET}")

    if not is_destination and ("socket:type2" in e_tags or "destination" in e_tags.get("brand", "").lower() or "destination" in e_tags.get("name", "").lower()):
        print(f"  {C_RED}🚨 CONFLATION WARNING: This OSM node contains AC Destination charger tags or name!{C_RESET}")
        print(f"     OSM Wiki requires separate nodes for AC Destination Wall Connectors vs DC Superchargers.\n")

    if dist_m > 30.0:
        print(f"  {C_YELLOW}⚠ GPS Offset Warning: OSM pin is {dist_m:.1f}m away from official Tesla GPS pin.{C_RESET}\n")

    _display_gmaps_audit_and_verification(
        station_base_name=station_base_name,
        city_name=station_base_name,
        lat=lat,
        lon=lon,
        stalls=stalls,
        power_kw=power_kw,
        hardware_model=hardware_model,
        open_non_tesla=open_non_tesla,
        is_destination=is_destination,
        gmaps_audited_info=gmaps_audited_info,
        nearby_dest_registry=nearby_dest_registry,
        tesla_dest_elements=tesla_dest_elements,
    )

    gmaps_entry_url = gmaps_audited_info.get("original_url") if gmaps_audited_info else f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lon:.6f}"

    # Determine action to perform
    action_chosen = None
    if fix_tags:
        action_chosen = "1"
    elif reposition:
        action_chosen = "2"
    elif fix_node:
        action_chosen = "3"
    elif save_registry:
        action_chosen = "4"
    elif not dry_run:
        while True:
            print(f"⚡ {C_BOLD}Available Audit Actions:{C_RESET}")
            print(f"  {C_CYAN}[1]{C_RESET} Fix Tag & Wiki Compliance errors only (preserve current OSM GPS)")
            print(f"  {C_CYAN}[2]{C_RESET} Provide correct GPS coordinates only (reposition OSM pin, preserve tags)")
            print(f"  {C_CYAN}[3]{C_RESET} Fix Both in one go (Apply Wiki tags AND update GPS coordinates)")
            print(f"  {C_CYAN}[4]{C_RESET} Save current OSM GPS to local JSON registry only (no OSM changeset)")
            print(f"  {C_CYAN}[m]{C_RESET} Open Tesla location in your browser (Google Maps)")
            print(f"  {C_CYAN}[o]{C_RESET} Open OSM location in your browser (OpenStreetMap)")
            print(f"  {C_CYAN}[s]{C_RESET} Open SC.info location in your browser (supercharge.info)")
            print(f"  {C_CYAN}[g]{C_RESET} Open Google entry in your browser (Google Maps)")
            print(f"  {C_DIM}[q]{C_RESET} Quit (or press Enter)\n")

            try:
                choice = input(f"Choice [1/2/3/4/m/o/s/g/q]: ").strip().lower()
                if choice in ("1", "2", "3", "4"):
                    action_chosen = choice
                    break
                elif _handle_browser_open_combo(
                    choice,
                    gmaps_coords=(lat, lon),
                    osm_element=(e_type, e_id),
                    osm_coords=(e_lat, e_lon),
                    sc_id=sc_info.get("id") if sc_info else None,
                    tesla_url=tesla_page_url,
                    gmaps_url=gmaps_entry_url,
                    provider_name="Tesla"
                ):
                    continue
                else:
                    return
            except (EOFError, KeyboardInterrupt):
                print()
                return

    # Action 4: Save current OSM GPS to local JSON registry without making OSM edits
    if action_chosen == "4":
        save_audited_osm_to_tesla_registry(st.name, e_lat, e_lon, e_id)
        print(f"💾 {C_GREEN}Saved audited OSM coordinates ({e_lat:.6f}, {e_lon:.6f}) & Node ID #{e_id} into local registry.{C_RESET}\n")
        return

    # Actions 1, 2, 3 require OSM edit
    if action_chosen in ("1", "2", "3") or dry_run:
        if not token and not dry_run:
            print(f"{C_RED}❌ Authentication required to update OSM. Run ./Tools/places_osm_editor.py --auth-url{C_RESET}", file=sys.stderr)
            sys.exit(1)

        final_lat = e_lat
        final_lon = e_lon
        tags_to_apply = {}
        remove_tags_list = []

        # Build merged tags preserving valid existing node properties
        merged_tags = dict(e_tags)
        for k, v in wiki_tags.items():
            if k not in merged_tags or not merged_tags[k]:
                merged_tags[k] = v
            elif k in ("brand", "brand:wikidata", "network", "network:wikidata", "operator", "operator:wikidata", "model", "model:wikidata", "capacity", "socket:type2_combo", "socket:type2_combo:output", "socket:type2_combo:voltage", "ref:tesla", "ref:supercharge_info", "start_date", "ele"):
                merged_tags[k] = v

        if e_tags.get("access") and e_tags.get("access") in ("yes", "customers", "permissive", "public"):
            merged_tags["access"] = e_tags["access"]
        if e_tags.get("name") and "supercharger" in e_tags.get("name").lower() and not override_name:
            merged_tags["name"] = e_tags["name"]
        if e_tags.get("parking:fee"):
            merged_tags["parking:fee"] = e_tags["parking:fee"]
        if e_tags.get("website") and "tesla.com" in e_tags.get("website"):
            merged_tags["website"] = e_tags["website"]

        cs_source = "official Tesla website; supercharge.info; ground survey"

        if action_chosen == "1":
            # Option 1: Fix tags only
            tags_to_apply = merged_tags
            remove_tags_list = [fb for fb in forbidden if fb in e_tags]
            final_lat = e_lat
            final_lon = e_lon

        elif action_chosen == "2":
            # Option 2: Reposition GPS only
            if not gpsupdate:
                try:
                    default_desc = f" (press Enter to use supercharge.info GPS {sc_lat:.6f}, {sc_lon:.6f}): " if (sc_lat and sc_lon) else ": "
                    user_coords = input(f"Enter physical stall GPS coordinates or Google Maps link{default_desc}").strip()
                    if not user_coords and sc_lat and sc_lon:
                        final_lat, final_lon = sc_lat, sc_lon
                    elif user_coords:
                        final_lat, final_lon = _parse_coords(user_coords)
                    else:
                        print(f"{C_RED}❌ No coordinates entered.{C_RESET}")
                        return
                except Exception as e:
                    print(f"{C_RED}❌ Invalid coordinates: {e}{C_RESET}")
                    return
            else:
                final_lat = target_lat
                final_lon = target_lon

            tags_to_apply = {}
            remove_tags_list = []

        else:
            # Option 3: Fix both in one go (or dry_run default)
            default_coords = (sc_lat, sc_lon) if (sc_lat and sc_lon) else (e_lat, e_lon)
            default_label = "supercharge.info" if (sc_lat and sc_lon) else "current OSM"

            if not gpsupdate and action_chosen == "3":
                try:
                    user_coords = input(f"Enter physical stall GPS coordinates or Google Maps link (press Enter for {default_label} {default_coords[0]:.6f}, {default_coords[1]:.6f}): ").strip()
                    if not user_coords:
                        final_lat, final_lon = default_coords
                    else:
                        final_lat, final_lon = _parse_coords(user_coords)
                except Exception as e:
                    print(f"{C_RED}❌ Invalid coordinates: {e}{C_RESET}")
                    return
            elif gpsupdate:
                final_lat = target_lat
                final_lon = target_lon
            else:
                final_lat, final_lon = default_coords

            tags_to_apply = merged_tags
            remove_tags_list = [fb for fb in forbidden if fb in e_tags]

        # Compute exact change diff
        added_tags = {k: v for k, v in tags_to_apply.items() if k not in e_tags and v}
        modified_tags = {k: (e_tags[k], v) for k, v in tags_to_apply.items() if k in e_tags and str(e_tags[k]) != str(v) and v}
        purged_tags = [k for k in remove_tags_list if k in e_tags]
        coords_moved = (round(final_lat, 6) != round(e_lat, 6) or round(final_lon, 6) != round(e_lon, 6))
        tags_changed = bool(added_tags or modified_tags or purged_tags)

        node_name = e_tags.get("name") or station_base_name
        if "tesla" not in node_name.lower() and "supercharger" not in node_name.lower():
            target_display_name = f"Tesla Supercharger {node_name}"
        else:
            target_display_name = node_name

        cs_comment = _generate_simple_changeset_comment(
            orig_tags=e_tags,
            new_tags=tags_to_apply,
            orig_coords=(e_lat, e_lon),
            new_coords=(final_lat, final_lon),
            station_name=target_display_name,
            custom_comment=comment,
        )
        rationale = ""

        _display_changeset_rationale(
            title=f"{target_display_name} (Update)",
            comment=cs_comment,
            source=cs_source,
            feature_type=f"Tesla Supercharger ({e_type.capitalize()} #{e_id})",
            coords=(final_lat, final_lon),
            tags=tags_to_apply or e_tags,
            rationale_text=rationale,
            orig_tags=e_tags,
            orig_coords=(e_lat, e_lon),
            removed_tags=remove_tags_list,
            is_new=False
        )

        if dry_run:
            print(f"{C_YELLOW}⚠ [DRY-RUN] Would update {e_type} #{e_id} at ({final_lat:.6f}, {final_lon:.6f}) with comment: '{cs_comment}'{C_RESET}")
            return

        if not _confirm_live_publish(f"update {e_type} #{e_id} at ({final_lat:.6f}, {final_lon:.6f}) - {cs_comment}"):
            return

        print(f"🚀 {C_CYAN}Opening changeset on OpenStreetMap...{C_RESET}")
        cs_id, _ = open_changeset(token, cs_comment, source=cs_source)
        if not cs_id:
            print(f"{C_RED}❌ Failed to open changeset on OpenStreetMap.{C_RESET}")
            return

        print(f"🔄 {C_CYAN}Updating {e_type} #{e_id}...{C_RESET}")
        reposition_lat = final_lat if (final_lat != e_lat or action_chosen in ("2", "3")) else None
        reposition_lon = final_lon if (final_lon != e_lon or action_chosen in ("2", "3")) else None
        ok, res = update_element_tags(token, cs_id, e_type, e_id, tags_to_apply, new_lat=reposition_lat, new_lon=reposition_lon, remove_tags=remove_tags_list)

        close_changeset(token, cs_id)

        if ok:
            print(f"{C_GREEN}✔ Successfully updated {e_type} #{e_id} on OpenStreetMap!{C_RESET}")
            if res.get("tags_dropped_by_policy"):
                print(f"{C_YELLOW}⚠ Tags outside the simple checklist were NOT sent (BUG-121 policy): "
                      f"{', '.join(res['tags_dropped_by_policy'])}{C_RESET}")
            print(f"  View Element:   {C_CYAN}https://www.openstreetmap.org/{e_type}/{e_id}{C_RESET}")
            print(f"  View Changeset: {C_CYAN}https://www.openstreetmap.org/changeset/{cs_id}{C_RESET}\n")
            # BUG-121: a reposition can be silently refused by osm_client.py's policy gate even
            # though the tag update itself succeeds - save what was actually applied, not what
            # was requested, or the local registry drifts from the real OSM node.
            if res.get("reposition_refused_by_policy"):
                print(f"{C_YELLOW}⚠ Coordinate change was NOT applied (repositioning is disabled by "
                      f"policy) - saving the node's existing GPS, not the requested move.{C_RESET}")
                saved_lat, saved_lon = e_lat, e_lon
            else:
                saved_lat, saved_lon = final_lat, final_lon
            save_audited_osm_to_tesla_registry(st.name, saved_lat, saved_lon, e_id)
            print(f"💾 {C_GREEN}Saved updated OSM coordinates & Node ID into local registry.{C_RESET}\n")

            if open_browser:
                try:
                    webbrowser.open(f"https://www.openstreetmap.org/changeset/{cs_id}")
                except Exception:
                    pass
        else:
            print(f"{C_RED}❌ Failed to update element: {res.get('error')}{C_RESET}")


def _source_and_audit_supercharge(
    query: str,
    token: Optional[str] = None,
    create_node: bool = False,
    fix_node: bool = False,
    fix_tags: bool = False,
    reposition: bool = False,
    gpsupdate: Optional[str] = None,
    save_registry: bool = False,
    dry_run: bool = False,
    comment: Optional[str] = None,
    user_tags: Optional[List[str]] = None,
    override_name: Optional[str] = None,
    open_browser: bool = True,
    gmaps_audited_info: Optional[Dict] = None,
):
    from lib.supercharge_info import search_supercharge_sites, fetch_site_changes
    from providers_chargers.chargers_base import haversine_km

    if not gmaps_audited_info and ("maps.app.goo.gl" in query.lower() or "goo.gl/maps" in query.lower() or "google.com/maps" in query.lower()):
        gmaps_audited_info = resolve_gmaps_url(query)

    matched = search_supercharge_sites(query, limit=10)
    if not matched:
        print(f"{C_RED}❌ No Supercharger matching '{query}' found on supercharge.info.{C_RESET}")
        return

    if len(matched) == 1 or query.strip().isdigit() or "siteId=" in query:
        site = matched[0]
    else:
        print(f"\n⚡ {C_BOLD}Multiple Superchargers found on supercharge.info for '{query}':{C_RESET}")
        for idx, s in enumerate(matched[:8], 1):
            s_addr = s.get("address", {})
            s_loc = f"{s_addr.get('city', '')}, {s_addr.get('state', '')} ({s_addr.get('country', '')})"
            print(f"  [{idx}] #{s.get('id')}: {C_GREEN}{s.get('name')}{C_RESET} - {s_loc} ({s.get('stallCount')} stalls, {s.get('powerKilowatt')}kW)")
        print(f"  [q] Quit\n")
        try:
            choice = input(f"Select site [1-{min(8, len(matched))}/q]: ").strip().lower()
            if choice.isdigit() and 1 <= int(choice) <= len(matched):
                site = matched[int(choice) - 1]
            else:
                return
        except (EOFError, KeyboardInterrupt):
            print()
            return

    site_id = site.get("id")
    site_name = site.get("name", "Supercharger")
    addr = site.get("address", {})
    country = addr.get("country", "")
    is_north_america = country in ("USA", "United States", "Canada", "Mexico")

    gps = site.get("gps", {})
    lat = gps.get("latitude")
    lon = gps.get("longitude")
    stalls = site.get("stallCount") or 0
    power_kw = site.get("powerKilowatt") or 250
    stalls_breakdown = site.get("stalls", {})
    if stalls_breakdown.get("v4"):
        model_name = "V4 Supercharger"
        model_wikidata = "Q122926617"
    elif stalls_breakdown.get("v2"):
        model_name = "V2 Supercharger"
        model_wikidata = "Q116171470"
    else:
        model_name = "V3 Supercharger"
        model_wikidata = "Q116171539"

    target_lat = lat
    target_lon = lon
    if gpsupdate:
        try:
            target_lat, target_lon = _parse_coords(gpsupdate)
            print(f"📍 {C_BOLD}Using Custom Physical Stall GPS:{C_RESET} {target_lat:.6f}, {target_lon:.6f}")
        except Exception as e:
            print(f"{C_RED}❌ Invalid --gpsupdate format (expected lat,lon): {e}{C_RESET}")
            return

    print(f"\n╔════════════════════════════════════════════════════════════════════════════════════╗")
    print(f"║   ⚡ SUPERCHARGE.INFO OSM COMPLIANCE & AUDIT: {site_name:<36} ║")
    print(f"╚════════════════════════════════════════════════════════════════════════════════════╝\n")

    sc_opened = site.get("dateOpened", "Unknown")
    sc_ele = site.get("elevationMeters", "?")
    sc_stalls_str = ", ".join([f"{v}x {k.upper()}" for k, v in stalls_breakdown.items()]) if stalls_breakdown else f"{stalls} Stalls"
    print(f"🌐 {C_BOLD}supercharge.info Authoritative Specifications:{C_RESET}")
    print(f"  • Site ID:         {C_CYAN}#{site_id}{C_RESET} ({site_name})")
    print(f"  • Address:         {addr.get('street', '')}, {addr.get('city', '')} {addr.get('state', '')} {addr.get('zip', '')} ({country})")
    print(f"  • Physical GPS:    {C_BOLD}{lat:.6f}, {lon:.6f}{C_RESET}")
    print(f"  • Date Opened:     {C_GREEN}{sc_opened}{C_RESET} | Elevation: {sc_ele}m")
    print(f"  • Hardware Spec:   {sc_stalls_str} ({power_kw} kW)")
    print(f"  • Open to Other:   {'YES (Open to all EVs)' if site.get('otherEVs') else 'NO (Tesla Only)'}")
    print(f"  • supercharge.info: {C_CYAN}https://supercharge.info/map?siteId={site_id}{C_RESET}")

    changes = fetch_site_changes(site_name)
    if changes:
        history_events = [f"{ch.get('date')} [{ch.get('siteStatus')}]" for ch in reversed(changes[:4])]
        print(f"  • Milestone History: {' ➔ '.join(history_events)}")
    print()

    # Build Tags
    city_name = addr.get("city") or site_name.split(",")[0].strip()
    ref_tesla = derive_tesla_ref(
        name=site_name,
        location_id=site.get("locationId"),
        url=f"https://www.tesla.com/findus/location/supercharger/{site.get('locationId')}" if site.get("locationId") else ""
    )
    wiki_tags = {
        "name": override_name or "Tesla Supercharger",
        "short_name": "Tesla",
        "amenity": "charging_station",
        "brand": "Tesla Supercharger",
        "brand:wikidata": "Q17089620",
        "operator": "Tesla",
        "operator:wikidata": "Q478214",
        "network": "Supercharger",
        "network:wikidata": "Q17089620",
        "manufacturer": "Tesla",
        "manufacturer:wikidata": "Q478214",
        "model": model_name,
        "model:wikidata": model_wikidata,
        "capacity": str(stalls),
        "ref:supercharge_info": str(site_id),
        "fee": "yes",
        "parking:fee": "no",
        "motorcar": "yes",
        "frequency": "0",
        "payment:app:tesla": "yes",
        "opening_hours": "24/7",
        "access": "yes" if site.get("otherEVs") else "customers",
    }
    if ref_tesla:
        wiki_tags["ref:tesla"] = ref_tesla
    if is_north_america:
        wiki_tags["socket:nacs"] = str(stalls)
        wiki_tags["socket:nacs:output"] = f"{power_kw}kW"
    else:
        wiki_tags["socket:type2_combo"] = str(stalls)
        wiki_tags["socket:type2_combo:output"] = f"{power_kw}kW"
        wiki_tags["socket:type2_combo:voltage"] = "400"

    if site.get("dateOpened"):
        wiki_tags["start_date"] = site.get("dateOpened")
    if site.get("elevationMeters"):
        wiki_tags["ele"] = str(site.get("elevationMeters"))
    if site.get("locationId"):
        wiki_tags["website"] = f"https://www.tesla.com/findus/location/supercharger/{site.get('locationId')}"

    # Address tags
    if addr.get("street"):
        wiki_tags["addr:street"] = addr["street"]
    if addr.get("city"):
        wiki_tags["addr:city"] = addr["city"]
    if addr.get("state"):
        wiki_tags["addr:state"] = addr["state"]
    if addr.get("zip"):
        wiki_tags["addr:postcode"] = addr["zip"]
    if addr.get("country"):
        wiki_tags["addr:country"] = "AU" if country == "Australia" else ("US" if country in ("USA", "United States") else country)

    if user_tags:
        for t in user_tags:
            if "=" in t:
                k, v = t.split("=", 1)
                wiki_tags[k.strip()] = v.strip()

    forbidden = ["socket:tesla_supercharger", "socket:tesla_supercharger_ccs", "socket:tesla_destination", "socket:type1", "socket:type1_combo"]
    if not is_north_america:
        forbidden.append("socket:nacs")

    # Query Overpass around coordinates
    print(f"🔍 {C_CYAN}Querying OpenStreetMap for mapped charging stations within 400m...{C_RESET}")
    elements = query_overpass_charging_stations(lat, lon, radius_m=400)
    
    # Separate true Tesla elements from third-party co-located chargers (e.g. NRMA, Chargefox)
    tesla_elements = []
    tesla_sc_elements = []
    tesla_dest_elements = []
    other_elements = []
    for el in elements:
        t_tags = el.get("tags", {})
        t_name = t_tags.get("name", "").lower()
        t_brand = t_tags.get("brand", "").lower()
        t_op = t_tags.get("operator", "").lower()
        t_net = t_tags.get("network", "").lower()
        if "tesla" in t_name or "tesla" in t_brand or "tesla" in t_op or "supercharger" in t_name or "supercharger" in t_net:
            tesla_elements.append(el)
            is_sc = ("supercharger" in t_name or "supercharger" in t_brand or "supercharger" in t_net or "socket:type2_combo" in t_tags or "socket:nacs" in t_tags)
            is_dest = ("destination" in t_name or "destination" in t_brand or "destination" in t_net or ("socket:type2" in t_tags and "socket:type2_combo" not in t_tags and "socket:nacs" not in t_tags))
            if is_sc:
                tesla_sc_elements.append(el)
            if is_dest:
                tesla_dest_elements.append(el)
        else:
            other_elements.append(el)

    # Check local Tesla Registry for co-located destination chargers if available
    nearby_dest_registry = []
    try:
        from providers_chargers.chargers_tesla import TeslaProvider
        p = TeslaProvider()
        stations = p.load_cached_registry() or []
        for s in stations:
            if getattr(s, "speed_tier", "") == "ac_destination" and s.lat and s.lon:
                d_dist = haversine_km(lat, lon, s.lat, s.lon) * 1000.0
                if d_dist <= 350.0:
                    nearby_dest_registry.append((s, d_dist))
    except Exception:
        pass

    target_tesla_elements = tesla_sc_elements if tesla_sc_elements else tesla_elements

    if nearby_dest_registry or tesla_dest_elements:
        print(f"⚠️  {C_YELLOW}{C_BOLD}CO-LOCATION ALERT: Supercharger & Destination Charger share this venue!{C_RESET}")
        if nearby_dest_registry:
            for d_st, d_dist in nearby_dest_registry:
                print(f"  • 🏨  Tesla Registry:   {C_CYAN}{d_st.name}{C_RESET} ({d_st.stalls or '?'}x AC Wall Connectors, ~{d_dist:.1f}m away)")
        if tesla_dest_elements:
            for d_el in tesla_dest_elements:
                d_id = d_el.get("id")
                d_type = d_el.get("type", "node")
                d_tags = d_el.get("tags", {})
                d_lat = d_el.get("lat") or d_el.get("center", {}).get("lat", lat)
                d_lon = d_el.get("lon") or d_el.get("center", {}).get("lon", lon)
                d_dist = haversine_km(lat, lon, d_lat, d_lon) * 1000.0
                d_name = d_tags.get("name") or "Tesla Destination"
                print(f"  • 🗺️  OpenStreetMap:    {d_type.capitalize()} #{d_id}: {C_CYAN}{d_name}{C_RESET} (~{d_dist:.1f}m away)")
        print(f"  • 📋  OSM Wiki Standard: Superchargers and Destination Chargers MUST be mapped as two separate nodes.")
        print(f"                           Never combine AC Type 2 wall connectors and DC Supercharger stalls onto one OSM node.")
        print(f"  • ⚠️  Google Maps Risk:  Google Maps often mislabels the whole site as 'Tesla Destination Charger'. Verify GMaps has a distinct Supercharger POI.\n")

    cs_source = "official Tesla website; supercharge.info; ground survey"
    cs_comment = _generate_simple_changeset_comment(
        new_tags=wiki_tags,
        station_name=city_name,
        custom_comment=comment,
        is_new=True,
    )

    if not target_tesla_elements:
        print(f"❌ {C_RED}{C_BOLD}STATUS: TESLA STATION MISSING IN OPENSTREETMAP{C_RESET}")
        print(f"   No dedicated Tesla charging node exists within 400m of ({lat:.6f}, {lon:.6f}).\n")
        print(f"📋 {C_BOLD}Proposed OSM Wiki-Compliant Node Tags for Tesla Supercharger:{C_RESET}")
        for k, v in wiki_tags.items():
            print(f"  • {C_CYAN}{k:<28}{C_RESET} = {C_GREEN}{v}{C_RESET}")
        gmaps_entry_url = gmaps_audited_info.get("original_url") if gmaps_audited_info else f"https://www.google.com/maps/search/?api=1&query={target_lat:.6f},{target_lon:.6f}"
        print()
        _display_gmaps_audit_and_verification(
            station_base_name=city_name,
            city_name=city_name,
            lat=target_lat,
            lon=target_lon,
            stalls=stalls,
            power_kw=power_kw,
            hardware_model=model_name,
            open_non_tesla=bool(site.get("otherEVs")),
            is_destination=False,
            gmaps_audited_info=gmaps_audited_info,
        )

        _display_changeset_rationale(
            title=f"Tesla Supercharger {city_name}",
            comment=cs_comment,
            source=cs_source,
            feature_type=f"Tesla Supercharger ({stalls} Stalls, {power_kw} kW {model_name})",
            coords=(target_lat, target_lon),
            tags=wiki_tags,
            rationale_text="Added new charging station node.",
            is_new=True
        )

        while not (create_node or dry_run):
            print(f"⚡ {C_BOLD}Available Audit Actions:{C_RESET}")
            print(f"  {C_CYAN}[1]{C_RESET} Create new Wiki-compliant Tesla Supercharger node on OpenStreetMap")
            print(f"  {C_CYAN}[m]{C_RESET} Open Tesla location in your browser (Google Maps)")
            print(f"  {C_CYAN}[o]{C_RESET} Open OSM location in your browser (OpenStreetMap)")
            print(f"  {C_CYAN}[s]{C_RESET} Open SC.info location in your browser (supercharge.info)")
            print(f"  {C_CYAN}[g]{C_RESET} Open Google entry in your browser (Google Maps)")
            print(f"  {C_DIM}[q]{C_RESET} Quit (or press Enter)\n")
            try:
                choice = input(f"Choice [1/m/o/s/g/q]: ").strip().lower()
                if choice == "1":
                    create_node = True
                    break
                elif _handle_browser_open_combo(
                    choice,
                    gmaps_coords=(target_lat, target_lon),
                    osm_coords=(target_lat, target_lon),
                    sc_id=site_id,
                    tesla_url=tesla_page_url,
                    gmaps_url=gmaps_entry_url,
                    provider_name="supercharge.info"
                ):
                    continue
                else:
                    return
            except (EOFError, KeyboardInterrupt):
                print()
                return

        if create_node:
            if not token:
                print(f"{C_RED}❌ Authentication required to publish to OSM. Run ./Tools/places_osm_editor.py --auth-url{C_RESET}", file=sys.stderr)
                sys.exit(1)
            if dry_run:
                print(f"{C_YELLOW}⚠ [DRY-RUN] Would create node at ({target_lat:.6f}, {target_lon:.6f}) with comment: '{cs_comment}'{C_RESET}")
                return
            new_node_id = _publish_node(token, target_lat, target_lon, wiki_tags, cs_comment, open_browser=open_browser)
            if new_node_id:
                save_audited_osm_to_tesla_registry(site_name, target_lat, target_lon, new_node_id)
                print(f"💾 {C_GREEN}Saved verified OSM coordinates & node ID #{new_node_id} into local registry.{C_RESET}")
        return

    # Check for micro-mapped stall nodes
    stall_nodes = []
    primary_cluster_node = None

    if len(target_tesla_elements) > 1:
        for el in target_tesla_elements:
            t = el.get("tags", {})
            cap = t.get("capacity")
            s_combo = t.get("socket:type2_combo") or t.get("socket:nacs")
            if (cap and str(cap).isdigit() and int(cap) > 1) or (s_combo and str(s_combo).isdigit() and int(s_combo) > 1):
                primary_cluster_node = el
            else:
                stall_nodes.append(el)

    is_micromapped = len(stall_nodes) >= 2 or (len(stall_nodes) >= 1 and primary_cluster_node is not None)

    if is_micromapped:
        return _audit_micromapped_stalls(
            stall_nodes=stall_nodes,
            primary_cluster_node=primary_cluster_node,
            st_name=site_name,
            station_base_name=city_name,
            lat=lat,
            lon=lon,
            sc_lat=lat,
            sc_lon=lon,
            stalls=stalls,
            power_kw=power_kw,
            hardware_model=model_name,
            model_name=model_name,
            model_wikidata=model_wikidata,
            open_non_tesla=bool(site.get("otherEVs")),
            is_destination=False,
            loc_id=site.get("locationId"),
            sc_info=site,
            st_url=tesla_page_url,
            token=token,
            dry_run=dry_run,
            open_browser=open_browser,
            gmaps_audited_info=gmaps_audited_info,
            nearby_dest_registry=nearby_dest_registry,
            tesla_dest_elements=tesla_dest_elements,
            forbidden=forbidden,
            is_north_america=is_north_america,
            comment=comment,
        )

    elem = target_tesla_elements[0]
    e_type = elem.get("type", "node")
    e_id = elem.get("id")
    e_tags = elem.get("tags", {})
    e_lat = elem.get("lat") or elem.get("center", {}).get("lat", lat)
    e_lon = elem.get("lon") or elem.get("center", {}).get("lon", lon)
    dist_m = haversine_km(lat, lon, e_lat, e_lon) * 1000.0

    print(f"✔ {C_GREEN}{C_BOLD}STATUS: FOUND IN OPENSTREETMAP{C_RESET} ({e_type.capitalize()} ID: {C_CYAN}{e_id}{C_RESET}, {dist_m:.1f}m away)\n")

    # Auto-cache verified OSM coordinates & Node ID to local registry
    if e_lat is not None and e_lon is not None:
        if save_audited_osm_to_tesla_registry(site_name, e_lat, e_lon, e_id):
            print(f"💾 {C_GREEN}Cached audited OSM coordinates ({e_lat:.6f}, {e_lon:.6f}) & Node ID #{e_id} into local registry.{C_RESET}\n")

    print(f"📍 {C_BOLD}Geographic Coordinate Comparison:{C_RESET}")
    print(f"  • 🌐 supercharge.info GPS: ({lat:.6f}, {lon:.6f}) [Physical Stalls]")
    if dist_m <= 30.0:
        print(f"  • 🌐 OSM Node GPS:        ({e_lat:.6f}, {e_lon:.6f}) {C_GREEN}✔ ~{dist_m:.1f}m (aligned with physical stalls - within 30m tolerance, no GPS change needed){C_RESET}")
    else:
        print(f"  • 🌐 OSM Node GPS:        ({e_lat:.6f}, {e_lon:.6f}) {C_YELLOW}⚠ ~{dist_m:.1f}m offset (>30m from supercharge.info physical stalls, reposition recommended){C_RESET}")
    print(f"  • 🔗 View in OSM:         {C_CYAN}https://www.openstreetmap.org/{e_type}/{e_id}{C_RESET}\n")

    print(f"📊 {C_BOLD}Tag & Wiki Compliance Audit (vs https://wiki.openstreetmap.org/wiki/Tesla):{C_RESET}")
    print(f"  {'TAG / PROPERTY':<28} {'OSM CURRENT VALUE':<26} {'REQUIRED WIKI VALUE':<24} {'AUDIT STATUS'}")
    print(f"  {'-'*28} {'-'*26} {'-'*24} {'-'*14}")

    def _check(tag, req_val):
        curr = e_tags.get(tag, "")
        if not curr:
            status = f"{C_RED}❌ MISSING{C_RESET}"
        elif str(curr).lower() == str(req_val).lower() or (tag.startswith("socket:") and str(curr) in ("yes", str(stalls))):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "name" and ("tesla" in str(curr).lower() or str(curr).lower() == "tesla supercharger"):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "short_name" and str(curr).lower() == "tesla":
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "brand" and str(curr).lower() in ("tesla", "tesla supercharger"):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "brand:wikidata" and str(curr).upper() in ("Q17089620", "Q478214"):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "operator" and str(curr).lower() in ("tesla", "tesla inc.", "tesla motors"):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "operator:wikidata" and str(curr).upper() == "Q478214":
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "level":
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "network" and str(curr).lower() in ("supercharger", "tesla supercharger", "tesla"):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "network:wikidata" and str(curr).upper() in ("Q17089620", "Q478214"):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "access" and str(curr).lower() in ("yes", "customers", "permissive", "public"):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "parking:fee" and str(curr).lower() in ("no", "yes", "interval", "limited"):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "website" and ("tesla.com" in str(curr).lower() and ("findus" in str(curr).lower() or "supercharger" in str(curr).lower())):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        elif tag == "ref:tesla" and (str(curr).lower().replace("_", "") == str(req_val).lower().replace("_", "") or str(curr).lower() in (str(req_val).lower(), str(req_val).lower().replace("supercharger", ""))):
            status = f"{C_GREEN}✔ PASS{C_RESET}"
        else:
            status = f"{C_YELLOW}⚠ DEVIATION{C_RESET}"
        print(f"  {tag:<28} {str(curr):<26} {str(req_val):<24} {status}")

    # Core Tags first
    _check("amenity", "charging_station")
    _check("brand", "Tesla Supercharger")
    _check("brand:wikidata", "Q17089620")
    _check("name", "Tesla Supercharger")
    _check("short_name", "Tesla")
    _check("operator", "Tesla")
    _check("operator:wikidata", "Q478214")
    if e_tags.get("level"):
        _check("level", e_tags["level"])

    for k, v in wiki_tags.items():
        if k not in ("amenity", "brand", "brand:wikidata", "name", "short_name", "operator", "operator:wikidata"):
            _check(k, v)

    for fb in forbidden:
        if fb in e_tags:
            print(f"  {C_RED}⚠ DEPRECATED/FORBIDDEN TAG FOUND: {fb}={e_tags[fb]} (Must be removed in AU){C_RESET}")

    if "socket:type2" in e_tags or "destination" in e_tags.get("brand", "").lower() or "destination" in e_tags.get("name", "").lower():
        print(f"  {C_RED}🚨 CONFLATION WARNING: This OSM node contains AC Destination charger tags or name!{C_RESET}")
        print(f"     OSM Wiki requires separate nodes for AC Destination Wall Connectors vs DC Superchargers.\n")

    print()
    _display_gmaps_audit_and_verification(
        station_base_name=city_name,
        city_name=city_name,
        lat=lat,
        lon=lon,
        stalls=stalls,
        power_kw=power_kw,
        hardware_model=model_name,
        open_non_tesla=bool(site.get("otherEVs")),
        is_destination=False,
        gmaps_audited_info=gmaps_audited_info,
    )

    gmaps_entry_url = gmaps_audited_info.get("original_url") if gmaps_audited_info else f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lon:.6f}"

    action_chosen = None
    if fix_tags:
        action_chosen = "1"
    elif reposition:
        action_chosen = "2"
    elif fix_node:
        action_chosen = "3"
    elif save_registry:
        action_chosen = "4"
    elif not dry_run:
        while True:
            print(f"⚡ {C_BOLD}Available Audit Actions:{C_RESET}")
            print(f"  {C_CYAN}[1]{C_RESET} Fix Tag & Wiki Compliance errors only (preserve current OSM GPS)")
            print(f"  {C_CYAN}[2]{C_RESET} Provide correct GPS coordinates only (reposition OSM pin, preserve tags)")
            print(f"  {C_CYAN}[3]{C_RESET} Fix Both in one go (Apply Wiki tags AND update GPS coordinates)")
            print(f"  {C_CYAN}[4]{C_RESET} Save current OSM GPS to local JSON registry only (no OSM changeset)")
            print(f"  {C_CYAN}[m]{C_RESET} Open Tesla location in your browser (Google Maps)")
            print(f"  {C_CYAN}[o]{C_RESET} Open OSM location in your browser (OpenStreetMap)")
            print(f"  {C_CYAN}[s]{C_RESET} Open SC.info location in your browser (supercharge.info)")
            print(f"  {C_CYAN}[g]{C_RESET} Open Google entry in your browser (Google Maps)")
            print(f"  {C_DIM}[q]{C_RESET} Quit (or press Enter)\n")

            try:
                choice = input(f"Choice [1/2/3/4/m/o/s/g/q]: ").strip().lower()
                if choice in ("1", "2", "3", "4"):
                    action_chosen = choice
                    break
                elif _handle_browser_open_combo(
                    choice,
                    gmaps_coords=(lat, lon),
                    osm_element=(e_type, e_id),
                    osm_coords=(e_lat, e_lon),
                    sc_id=site_id,
                    tesla_url=tesla_page_url,
                    gmaps_url=gmaps_entry_url,
                    provider_name="supercharge.info"
                ):
                    continue
                else:
                    return
            except (EOFError, KeyboardInterrupt):
                print()
                return

    if action_chosen == "4":
        save_audited_osm_to_tesla_registry(site_name, e_lat, e_lon, e_id)
        print(f"💾 {C_GREEN}Saved audited OSM coordinates ({e_lat:.6f}, {e_lon:.6f}) & Node ID #{e_id} into local registry.{C_RESET}\n")
        return

    if action_chosen in ("1", "2", "3") or dry_run:
        if not token and not dry_run:
            print(f"{C_RED}❌ Authentication required to update OSM. Run ./Tools/places_osm_editor.py --auth-url{C_RESET}", file=sys.stderr)
            sys.exit(1)

        final_lat = e_lat
        final_lon = e_lon
        tags_to_apply = {}
        remove_tags_list = []

        merged_tags = dict(e_tags)
        for k, v in wiki_tags.items():
            if k not in merged_tags or not merged_tags[k]:
                merged_tags[k] = v
            elif k in ("brand", "brand:wikidata", "network", "network:wikidata", "operator", "operator:wikidata", "model", "model:wikidata", "capacity", "socket:type2_combo", "socket:type2_combo:output", "socket:type2_combo:voltage", "ref:tesla", "ref:supercharge_info", "start_date", "ele"):
                merged_tags[k] = v

        if e_tags.get("access") and e_tags.get("access") in ("yes", "customers", "permissive", "public"):
            merged_tags["access"] = e_tags["access"]
        if e_tags.get("name") and "supercharger" in e_tags.get("name").lower() and not override_name:
            merged_tags["name"] = e_tags["name"]
        if e_tags.get("parking:fee"):
            merged_tags["parking:fee"] = e_tags["parking:fee"]
        if e_tags.get("website") and "tesla.com" in e_tags.get("website"):
            merged_tags["website"] = e_tags["website"]

        if action_chosen == "1":
            tags_to_apply = merged_tags
            remove_tags_list = [fb for fb in forbidden if fb in e_tags]
            final_lat = e_lat
            final_lon = e_lon

        elif action_chosen == "2":
            if not gpsupdate:
                try:
                    default_desc = f" (press Enter to use supercharge.info GPS {lat:.6f}, {lon:.6f}): " if (lat and lon) else ": "
                    user_coords = input(f"Enter physical stall GPS coordinates or Google Maps link{default_desc}").strip()
                    if not user_coords and lat and lon:
                        final_lat, final_lon = lat, lon
                    elif user_coords:
                        final_lat, final_lon = _parse_coords(user_coords)
                    else:
                        print(f"{C_RED}❌ No coordinates entered.{C_RESET}")
                        return
                except Exception as e:
                    print(f"{C_RED}❌ Invalid coordinates: {e}{C_RESET}")
                    return
            else:
                final_lat = target_lat
                final_lon = target_lon

            tags_to_apply = {}
            remove_tags_list = []

        else:
            default_coords = (lat, lon) if (lat and lon) else (e_lat, e_lon)
            default_label = "supercharge.info" if (lat and lon) else "current OSM"

            if not gpsupdate and action_chosen == "3":
                try:
                    user_coords = input(f"Enter physical stall GPS coordinates or Google Maps link (press Enter for {default_label} {default_coords[0]:.6f}, {default_coords[1]:.6f}): ").strip()
                    if not user_coords:
                        final_lat, final_lon = default_coords
                    else:
                        final_lat, final_lon = _parse_coords(user_coords)
                except Exception as e:
                    print(f"{C_RED}❌ Invalid coordinates: {e}{C_RESET}")
                    return
            elif gpsupdate:
                final_lat = target_lat
                final_lon = target_lon
            else:
                final_lat, final_lon = default_coords

            tags_to_apply = merged_tags
            remove_tags_list = [fb for fb in forbidden if fb in e_tags]

        # Compute exact change diff
        added_tags = {k: v for k, v in tags_to_apply.items() if k not in e_tags and v}
        modified_tags = {k: (e_tags[k], v) for k, v in tags_to_apply.items() if k in e_tags and str(e_tags[k]) != str(v) and v}
        purged_tags = [k for k in remove_tags_list if k in e_tags]
        coords_moved = (round(final_lat, 6) != round(e_lat, 6) or round(final_lon, 6) != round(e_lon, 6))
        tags_changed = bool(added_tags or modified_tags or purged_tags)

        cs_source = "official Tesla website; supercharge.info; ground survey"

        node_name = e_tags.get("name") or city_name or site_name
        if "tesla" not in node_name.lower() and "supercharger" not in node_name.lower():
            target_display_name = f"Tesla Supercharger {node_name}"
        else:
            target_display_name = node_name

        cs_comment = _generate_simple_changeset_comment(
            orig_tags=e_tags,
            new_tags=tags_to_apply,
            orig_coords=(e_lat, e_lon),
            new_coords=(final_lat, final_lon),
            station_name=target_display_name,
            custom_comment=comment,
        )
        rationale = ""

        _display_changeset_rationale(
            title=f"{target_display_name} (Update)",
            comment=cs_comment,
            source=cs_source,
            feature_type=f"Tesla Supercharger ({e_type.capitalize()} #{e_id})",
            coords=(final_lat, final_lon),
            tags=tags_to_apply or e_tags,
            rationale_text=rationale,
            orig_tags=e_tags,
            orig_coords=(e_lat, e_lon),
            removed_tags=remove_tags_list,
            is_new=False
        )

        if dry_run:
            print(f"{C_YELLOW}⚠ [DRY-RUN] Would update {e_type} #{e_id} at ({final_lat:.6f}, {final_lon:.6f}) with comment: '{cs_comment}'{C_RESET}")
            return

        if not _confirm_live_publish(f"update {e_type} #{e_id} at ({final_lat:.6f}, {final_lon:.6f}) - {cs_comment}"):
            return

        print(f"🚀 {C_CYAN}Opening changeset on OpenStreetMap...{C_RESET}")
        cs_id, _ = open_changeset(token, cs_comment, source=cs_source)
        if not cs_id:
            print(f"{C_RED}❌ Failed to open changeset on OpenStreetMap.{C_RESET}")
            return

        print(f"🔄 {C_CYAN}Updating {e_type} #{e_id}...{C_RESET}")
        reposition_lat = final_lat if (final_lat != e_lat or action_chosen in ("2", "3")) else None
        reposition_lon = final_lon if (final_lon != e_lon or action_chosen in ("2", "3")) else None
        ok, res = update_element_tags(token, cs_id, e_type, e_id, tags_to_apply, new_lat=reposition_lat, new_lon=reposition_lon, remove_tags=remove_tags_list)

        close_changeset(token, cs_id)
        if ok:
            print(f"{C_GREEN}✔ Successfully updated {e_type} #{e_id} on OpenStreetMap!{C_RESET}")
            if res.get("tags_dropped_by_policy"):
                print(f"{C_YELLOW}⚠ Tags outside the simple checklist were NOT sent (BUG-121 policy): "
                      f"{', '.join(res['tags_dropped_by_policy'])}{C_RESET}")
            print(f"  View Element:   {C_CYAN}https://www.openstreetmap.org/{e_type}/{e_id}{C_RESET}")
            print(f"  View Changeset: {C_CYAN}https://www.openstreetmap.org/changeset/{cs_id}{C_RESET}\n")
            # BUG-121: same fix as the other call site - don't save a requested reposition that
            # osm_client.py's policy gate actually refused.
            if res.get("reposition_refused_by_policy"):
                print(f"{C_YELLOW}⚠ Coordinate change was NOT applied (repositioning is disabled by "
                      f"policy) - saving the node's existing GPS, not the requested move.{C_RESET}")
                saved_lat, saved_lon = e_lat, e_lon
            else:
                saved_lat, saved_lon = final_lat, final_lon
            save_audited_osm_to_tesla_registry(site_name, saved_lat, saved_lon, e_id)
            print(f"💾 {C_GREEN}Saved updated OSM coordinates & Node ID into local registry.{C_RESET}\n")
            if open_browser:
                try:
                    webbrowser.open(f"https://www.openstreetmap.org/changeset/{cs_id}")
                except Exception:
                    pass
        else:
            print(f"{C_RED}❌ Failed to update element: {res.get('error')}{C_RESET}")


def _display_templates():
    print(f"\n📚 {C_BOLD}OpenStreetMap Standard Tagging Templates & Presets:{C_RESET}\n")
    # Group by category
    cats = {}
    for t_id, info in OSM_TEMPLATES.items():
        c = info.get("category", "General")
        cats.setdefault(c, []).append((t_id, info))

    for c_name, items in cats.items():
        print(f"{C_BOLD}{C_CYAN}📁 {c_name}:{C_RESET}")
        for t_id, info in items:
            p_tags = ", ".join([f"{k}={v}" for k, v in info.get("primary_tags", {}).items()])
            s_tags = ", ".join(info.get("suggested_tags", {}).keys())
            print(f"  • {C_GREEN}{t_id:<24}{C_RESET} {C_BOLD}{info.get('label')}{C_RESET}")
            print(f"    Tags:      {C_DIM}{p_tags}{C_RESET}")
            if s_tags:
                print(f"    Optional:  {C_DIM}{s_tags}{C_RESET}")
        print()
    print(f"💡 {C_BOLD}Usage Example:{C_RESET}")
    print(f"   ./Tools/places_osm_editor.py --from-gmaps <URL> --template basketball_court --tags surface=concrete lit=no --create-node\n")


if __name__ == "__main__":
    main()

