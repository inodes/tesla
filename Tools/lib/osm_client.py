#!/usr/bin/env python3
"""
osm_client.py - OpenStreetMap API v0.6 & OAuth2 Client
======================================================
Provides complete authentication and editing capabilities for OpenStreetMap:
- OAuth2 Authorization & Token Exchange
- Changeset lifecycle management (open, update, close)
- Node creation & element tagging (amenities, toilets, shopping centres)
- Public Notes API submission
"""

import os
import sys
import re
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple, Any

_lib_dir = os.path.dirname(os.path.abspath(__file__))
_tools_dir = os.path.dirname(_lib_dir)
_repo_root = os.path.dirname(_tools_dir)

if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from tessie_api_common import load_config

OSM_BASE_URL = "https://api.openstreetmap.org"
OSM_AUTH_URL = "https://www.openstreetmap.org/oauth2/authorize"
OSM_TOKEN_URL = "https://www.openstreetmap.org/oauth2/token"
OSM_USER_AGENT = "TeslaPlacesManager/1.0 (https://github.com/inodes/tesla)"

# BUG-121 (2026-09-21): per the user's explicit "no heavy OSM edits" directive, live writes to
# OpenStreetMap are restricted to this short list of simple, low-risk tags by default. Anything
# outside it (geometry/tag sprawl - manufacturer, socket wiring details, network refs, etc.) requires
# an explicit opt-in from the caller (allow_extra_tags=True on update_element_tags()), and creating a
# brand new node requires its own explicit opt-in (allow_node_creation=True on create_node()) since the
# policy is "no node creation" at all, not "node creation with fewer tags."
SIMPLE_TAG_ALLOWLIST = {
    "amenity",
    "brand",
    "brand:wikidata",
    "name",
    "short_name",
    "operator",
    "operator:wikidata",
    "level",
}


OVERPASS_ENDPOINTS = [
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]


def query_overpass_charging_stations(lat: float, lon: float, radius_m: int = 400) -> List[Dict[str, Any]]:
    """
    Queries Overpass API across fallback endpoints to find charging stations and Tesla-branded infrastructure.
    """
    import subprocess
    query = f"""[out:json][timeout:12];
(
  nwr(around:{radius_m},{lat},{lon})["amenity"="charging_station"];
  nwr(around:{radius_m},{lat},{lon})["brand"~"Tesla",i];
);
out center tags;"""

    for ep in OVERPASS_ENDPOINTS:
        # Fast path: curl with data-urlencode
        try:
            cmd = ["curl", "-s", "--max-time", "6", "-H", f"User-Agent: {OSM_USER_AGENT}", "-H", "Accept: application/json", "--data-urlencode", f"data={query}", ep]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            if proc.returncode == 0 and proc.stdout:
                res_data = json.loads(proc.stdout)
                if "elements" in res_data:
                    return res_data["elements"]
        except Exception:
            pass

        # Fallback path: urllib.request
        try:
            data = urllib.parse.urlencode({"data": query}).encode("utf-8")
            headers = {"User-Agent": OSM_USER_AGENT, "Accept": "application/json"}
            req = urllib.request.Request(ep, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                if "elements" in res_data:
                    return res_data["elements"]
        except Exception:
            continue
    return []



def get_osm_token_file() -> str:
    """Returns preferred path for storing local OSM OAuth token."""
    return os.path.join(_repo_root, ".osm_token.json")


def save_osm_token(token: str, metadata: Optional[Dict[str, Any]] = None) -> str:
    """Saves generated OSM OAuth access token to local secure cache outside config.json."""
    path = get_osm_token_file()
    payload = {
        "access_token": token,
        "saved_at": time.time(),
        "metadata": metadata or {}
    }
    candidate_paths = [
        path,
        os.path.expanduser("~/.osm_token.json"),
    ]
    last_err = None
    for p in candidate_paths:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            return p
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"Could not save OSM token to any path: {last_err}")


def load_osm_token() -> Optional[str]:
    """Loads OSM access token from environment or local cache."""
    if os.environ.get("OSM_ACCESS_TOKEN"):
        return os.environ["OSM_ACCESS_TOKEN"].strip()

    candidate_paths = [
        get_osm_token_file(),
        os.path.expanduser("~/.osm_token.json"),
    ]

    for p in candidate_paths:
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "access_token" in data:
                        return str(data["access_token"]).strip()
                    elif isinstance(data, str) and data.strip():
                        return data.strip()
            except Exception:
                continue
    return None


def load_osm_config() -> Dict[str, Any]:
    """Loads OSM OAuth configuration from config.json with env fallbacks."""
    cfg = load_config()
    osm_cfg = cfg.get("osm", {})
    if not isinstance(osm_cfg, dict):
        osm_cfg = {}

    client_id = osm_cfg.get("client_id") or os.environ.get("OSM_CLIENT_ID")
    client_secret = osm_cfg.get("client_secret") or os.environ.get("OSM_CLIENT_SECRET")
    redirect_uri = osm_cfg.get("redirect_uri", "urn:ietf:wg:oauth:2.0:oob")
    token = load_osm_token()

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "access_token": token,
    }


def get_oauth_auth_url(client_id: str, redirect_uri: str = "urn:ietf:wg:oauth:2.0:oob", scope: str = "read_prefs write_api") -> str:
    """Generates the OpenStreetMap user authorization URL."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
    }
    return f"{OSM_AUTH_URL}?{urllib.parse.urlencode(params)}"


def fetch_oauth_token(client_id: str, client_secret: str, auth_code: str, redirect_uri: str = "urn:ietf:wg:oauth:2.0:oob") -> Tuple[Optional[str], Dict[str, Any]]:
    """
    Exchanges an OSM OAuth authorization code for a bearer access_token.
    Endpoint: POST https://www.openstreetmap.org/oauth2/token
    """
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": auth_code.strip(),
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        OSM_TOKEN_URL,
        data=data,
        headers={
            "User-Agent": OSM_USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            token = resp_data.get("access_token")
            return token, resp_data
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return None, {"error": f"HTTP {e.code}: {err_body}"}
    except Exception as e:
        return None, {"error": str(e)}


def get_user_details(token: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Fetches user account details from OSM API.
    Endpoint: GET https://api.openstreetmap.org/api/0.6/user/details.json
    """
    url = f"{OSM_BASE_URL}/api/0.6/user/details.json"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": OSM_USER_AGENT,
            "Authorization": f"Bearer {token}",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("user", {}), data
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return None, {"error": f"HTTP {e.code}: {err_body}"}
    except Exception as e:
        return None, {"error": str(e)}


def open_changeset(token: str, comment: str, source: str = "survey") -> Tuple[Optional[int], Dict[str, Any]]:
    """
    Opens an editing changeset on OpenStreetMap.
    Endpoint: PUT https://api.openstreetmap.org/api/0.6/changeset/create
    """
    url = f"{OSM_BASE_URL}/api/0.6/changeset/create"
    
    osm_elem = ET.Element("osm")
    cs_elem = ET.SubElement(osm_elem, "changeset")
    
    tags = {
        "created_by": "TeslaTools/1.0",
        "comment": comment,
        "source": source,
    }
    for k, v in tags.items():
        ET.SubElement(cs_elem, "tag", {"k": k, "v": str(v)})

    xml_data = ET.tostring(osm_elem, encoding="utf-8")
    req = urllib.request.Request(
        url,
        data=xml_data,
        method="PUT",
        headers={
            "User-Agent": OSM_USER_AGENT,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/xml",
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            cs_id_str = resp.read().decode("utf-8").strip()
            return int(cs_id_str), {"status": "ok", "changeset_id": int(cs_id_str)}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return None, {"error": f"HTTP {e.code}: {err_body}"}
    except Exception as e:
        return None, {"error": str(e)}


def close_changeset(token: str, changeset_id: int) -> bool:
    """
    Closes an open changeset on OpenStreetMap.
    Endpoint: PUT https://api.openstreetmap.org/api/0.6/changeset/<id>/close
    """
    url = f"{OSM_BASE_URL}/api/0.6/changeset/{changeset_id}/close"
    req = urllib.request.Request(
        url,
        method="PUT",
        headers={
            "User-Agent": OSM_USER_AGENT,
            "Authorization": f"Bearer {token}",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def create_node(
    token: str,
    changeset_id: int,
    lat: float,
    lon: float,
    tags: Dict[str, str],
    allow_node_creation: bool = False,
    allow_extra_tags: bool = False,
) -> Tuple[Optional[int], Dict[str, Any]]:
    """
    Creates a new node element in OpenStreetMap within an open changeset.
    Endpoint: PUT https://api.openstreetmap.org/api/0.6/node/create

    BUG-121: refuses to run at all unless allow_node_creation=True is passed explicitly - the
    user's stated policy is "no node creation," so the safe default is refuse, not "create with
    fewer tags." Tags outside SIMPLE_TAG_ALLOWLIST are dropped unless allow_extra_tags=True.
    """
    if not allow_node_creation:
        return None, {
            "error": "node creation is disabled by policy (BUG-121) - pass allow_node_creation=True "
                     "to override for this one call",
            "refused_by_policy": True,
        }

    dropped = [k for k in tags if k not in SIMPLE_TAG_ALLOWLIST]
    if dropped and not allow_extra_tags:
        tags = {k: v for k, v in tags.items() if k in SIMPLE_TAG_ALLOWLIST}

    url = f"{OSM_BASE_URL}/api/0.6/node/create"
    
    osm_elem = ET.Element("osm")
    node_elem = ET.SubElement(osm_elem, "node", {
        "lat": f"{lat:.7f}",
        "lon": f"{lon:.7f}",
        "changeset": str(changeset_id),
    })
    
    for k, v in tags.items():
        if v is not None:
            ET.SubElement(node_elem, "tag", {"k": str(k), "v": str(v)})

    xml_data = ET.tostring(osm_elem, encoding="utf-8")
    req = urllib.request.Request(
        url,
        data=xml_data,
        method="PUT",
        headers={
            "User-Agent": OSM_USER_AGENT,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/xml",
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            node_id_str = resp.read().decode("utf-8").strip()
            result = {"status": "ok", "node_id": int(node_id_str)}
            if dropped and not allow_extra_tags:
                result["tags_dropped_by_policy"] = dropped
            return int(node_id_str), result
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return None, {"error": f"HTTP {e.code}: {err_body}"}
    except Exception as e:
        return None, {"error": str(e)}


def fetch_element_xml(elem_type: str, elem_id: int) -> Optional[ET.Element]:
    """Fetches raw XML for an OSM node/way/relation."""
    url = f"{OSM_BASE_URL}/api/0.6/{elem_type}/{elem_id}"
    req = urllib.request.Request(url, headers={"User-Agent": OSM_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            tree = ET.fromstring(resp.read().decode("utf-8"))
            return tree
    except Exception:
        return None


def update_element_tags(
    token: str,
    changeset_id: int,
    elem_type: str,
    elem_id: int,
    new_tags: Dict[str, str],
    new_lat: Optional[float] = None,
    new_lon: Optional[float] = None,
    remove_tags: Optional[List[str]] = None,
    allow_extra_tags: bool = False,
    allow_reposition: bool = False,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Updates or appends tags (and optionally coordinates for nodes) on an existing OSM element within an open changeset.
    Endpoint: PUT https://api.openstreetmap.org/api/0.6/<elem_type>/<elem_id>

    BUG-121: by default, only tags in SIMPLE_TAG_ALLOWLIST are written - anything else in `new_tags`
    is silently dropped (reported back in the result dict as `tags_dropped_by_policy`) unless the
    caller explicitly passes allow_extra_tags=True. Likewise, a coordinate change (new_lat/new_lon)
    is refused unless allow_reposition=True is passed explicitly - existing stall-node call sites that
    already pass None/None for both are unaffected either way.
    """
    dropped = [k for k in new_tags if k not in SIMPLE_TAG_ALLOWLIST]
    if dropped and not allow_extra_tags:
        new_tags = {k: v for k, v in new_tags.items() if k in SIMPLE_TAG_ALLOWLIST}

    reposition_refused = False
    if new_lat is not None or new_lon is not None:
        if not allow_reposition:
            reposition_refused = True
            new_lat, new_lon = None, None

    tree = fetch_element_xml(elem_type, elem_id)
    if tree is None:
        return False, {"error": f"Could not fetch existing {elem_type} ID {elem_id}"}

    elem = tree.find(elem_type)
    if elem is None:
        return False, {"error": f"Invalid XML payload for {elem_type} ID {elem_id}"}

    elem.set("changeset", str(changeset_id))

    if elem_type == "node" and new_lat is not None and new_lon is not None:
        elem.set("lat", f"{new_lat:.7f}")
        elem.set("lon", f"{new_lon:.7f}")

    # Existing tags
    tag_elems = {t.get("k"): t for t in elem.findall("tag")}
    if remove_tags:
        for rt in remove_tags:
            if rt in tag_elems:
                elem.remove(tag_elems[rt])
                del tag_elems[rt]

    for k, v in new_tags.items():
        if k in tag_elems:
            tag_elems[k].set("v", str(v))
        else:
            ET.SubElement(elem, "tag", {"k": str(k), "v": str(v)})

    xml_data = ET.tostring(tree, encoding="utf-8")
    url = f"{OSM_BASE_URL}/api/0.6/{elem_type}/{elem_id}"
    req = urllib.request.Request(
        url,
        data=xml_data,
        method="PUT",
        headers={
            "User-Agent": OSM_USER_AGENT,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/xml",
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            new_version = resp.read().decode("utf-8").strip()
            result = {"status": "ok", "version": new_version}
            if dropped and not allow_extra_tags:
                result["tags_dropped_by_policy"] = dropped
            if reposition_refused:
                result["reposition_refused_by_policy"] = True
            return True, result
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return False, {"error": f"HTTP {e.code}: {err_body}"}
    except Exception as e:
        return False, {"error": str(e)}


def add_osm_note(lat: float, lon: float, text: str) -> Tuple[Optional[int], Dict[str, Any]]:
    """
    Creates a public community map note on OpenStreetMap.
    Endpoint: POST https://api.openstreetmap.org/api/0.6/notes
    """
    url = f"{OSM_BASE_URL}/api/0.6/notes"
    payload = {
        "lat": f"{lat:.7f}",
        "lon": f"{lon:.7f}",
        "text": text,
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "User-Agent": OSM_USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read().decode("utf-8")
            tree = ET.fromstring(data)
            note_elem = tree.find("note")
            note_id = int(note_elem.find("id").text) if note_elem is not None and note_elem.find("id") is not None else None
            return note_id, {"status": "ok", "note_id": note_id}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return None, {"error": f"HTTP {e.code}: {err_body}"}
    except Exception as e:
        return None, {"error": str(e)}


def resolve_gmaps_url(url_or_query: str) -> Dict[str, Any]:
    """
    Resolves Google Maps shortlinks or canonical URLs and extracts:
    - Target place name
    - Exact POI coordinates (!3d/!4d or viewport /@lat,lon)
    - Full resolved URL
    """
    url = url_or_query.strip()
    current = url
    for _ in range(6):
        if not (current.startswith("http://") or current.startswith("https://")):
            break
        req = urllib.request.Request(current, headers={"User-Agent": "curl/7.88.1"})
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = resp.read().decode("utf-8", errors="ignore")
                m_meta = re.search(r'content=["\']0;url=([^"\']+)["\']', data, re.IGNORECASE)
                if m_meta:
                    current = m_meta.group(1)
                    continue
                current = resp.geturl()
                break
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                current = e.headers.get("Location")
            else:
                break
        except Exception:
            break

    final_url = current
    lat, lon, name = None, None, None

    # 1. Exact POI coordinates in data block: !3d<lat>!4d<lon>
    m_exact = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", final_url)
    if m_exact:
        lat = float(m_exact.group(1))
        lon = float(m_exact.group(2))

    # 2. Viewport coordinates /@<lat>,<lon>
    if lat is None:
        m_vp = re.search(r"/@(-?\d+\.\d+),(-?\d+\.\d+)", final_url)
        if m_vp:
            lat = float(m_vp.group(1))
            lon = float(m_vp.group(2))

    # 3. Query string coordinates ?q=<lat>,<lon> or &ll=<lat>,<lon>
    if lat is None:
        m_q = re.search(r"[?&](?:q|ll|query)=(-?\d+\.\d+),(-?\d+\.\d+)", final_url)
        if m_q:
            lat = float(m_q.group(1))
            lon = float(m_q.group(2))

    # 4. Place name from /place/<Name>/
    m_name = re.search(r"/place/([^/@?]+)", final_url)
    if m_name:
        raw_name = urllib.parse.unquote_plus(m_name.group(1))
        name = raw_name.replace("+", " ").strip()
    elif "[?&]q=" in final_url and not lat:
        m_qn = re.search(r"[?&]q=([^&]+)", final_url)
        if m_qn:
            name = urllib.parse.unquote_plus(m_qn.group(1)).replace("+", " ").strip()

    return {
        "original_url": url,
        "resolved_url": final_url,
        "name": name or "Unnamed Location",
        "latitude": lat,
        "longitude": lon,
    }


def infer_osm_tags_from_name(name: str, hint: str = "") -> Dict[str, str]:
    """
    Infers appropriate standard OpenStreetMap tags based on place name and category hints.
    """
    clean_name = name.strip()
    n_lower = clean_name.lower()
    h_lower = hint.lower()
    combined = f"{n_lower} {h_lower}"

    tags = {
        "name": clean_name
    }

    # Sports & Recreation
    if any(k in combined for k in ["basket ball", "basketball", "hoop", "half court"]):
        tags.update({
            "leisure": "pitch",
            "sport": "basketball",
            "access": "yes",
        })
    elif any(k in combined for k in ["tennis", "tennis court"]):
        tags.update({
            "leisure": "pitch",
            "sport": "tennis",
            "access": "yes",
        })
    elif any(k in combined for k in ["pickleball"]):
        tags.update({
            "leisure": "pitch",
            "sport": "pickleball",
            "access": "yes",
        })
    elif any(k in combined for k in ["skate park", "skatepark"]):
        tags.update({
            "leisure": "pitch",
            "sport": "skateboard",
            "access": "yes",
        })
    elif any(k in combined for k in ["playground", "play area", "play space", "kids play"]):
        tags.update({
            "leisure": "playground",
            "access": "yes",
        })
    elif any(k in combined for k in ["dog park", "off leash", "off-leash"]):
        tags.update({
            "leisure": "dog_park",
            "access": "yes",
        })
    elif any(k in combined for k in ["park", "reserve", "gardens"]) and not any(k in combined for k in ["car park", "business park"]):
        tags.update({
            "leisure": "park",
        })
    # Public Facilities & Amenities
    elif any(k in combined for k in ["toilet", "toilets", "restroom", "restrooms", "bathroom", "amenities block", "wc"]):
        tags.update({
            "amenity": "toilets",
            "access": "yes",
            "fee": "no",
        })
    elif any(k in combined for k in ["drinking water", "water bubbler", "water fountain", "water refill"]):
        tags.update({
            "amenity": "drinking_water",
        })
    elif any(k in combined for k in ["picnic table", "picnic area", "picnic shelter"]):
        tags.update({
            "leisure": "picnic_table",
        })
    elif any(k in combined for k in ["barbecue", "bbq"]):
        tags.update({
            "amenity": "bbq",
        })
    # Supermarkets & Groceries
    elif any(k in combined for k in ["iga", "supa iga", "coles", "woolworths", "woolies", "aldi", "supermarket", "grocer"]):
        tags.update({
            "shop": "supermarket",
        })
    # Dining & Drinks
    elif any(k in combined for k in ["cafe", "coffee", "espresso", "roastery"]):
        tags.update({
            "amenity": "cafe",
        })
    elif any(k in combined for k in ["bakery", "bakehouse", "bakers delight"]):
        tags.update({
            "shop": "bakery",
        })
    elif any(k in combined for k in ["pizzeria", "pizza", "burger", "grill", "bistro", "restaurant", "dining"]):
        tags.update({
            "amenity": "restaurant",
        })
    elif any(k in combined for k in ["mcdonald's", "maccas", "kfc", "hungry jack", "subway", "red rooster"]):
        tags.update({
            "amenity": "fast_food",
        })
    # Health & Retail
    elif any(k in combined for k in ["chemist", "pharmacy", "priceline", "terrywhite"]):
        tags.update({
            "amenity": "pharmacy",
        })
    # EV Charging
    elif any(k in combined for k in ["supercharger", "ev charging", "tesla charger", "chargefox", "evie"]):
        tags.update({
            "amenity": "charging_station",
            "fee": "yes",
        })
    else:
        # Generic fallback
        tags.update({
            "description": clean_name
        })

    return tags

