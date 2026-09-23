#!/usr/bin/env python3
"""
abrp_planner.py - ABRP (A Better Routeplanner) & EV Route Intelligence
========================================================================
Combines local vehicle intelligence (Tessie live SoC, VIN/model specs) with
route corridor analysis and ABRP (Iternio) trip planning.

Key Capabilities:
- Dynamic Departure SoC: Queries live battery level from Tessie API (or uses config default / CLI override).
- Physics-Based Arrival SoC: Projects battery % at every highway corridor station using vehicle consumption profile.
- 1-Click ABRP Deep Linking: Generates pre-populated ABRP road trip URLs with vehicle typecode, departure SoC, and charger stops.
- Iternio Planning & Telemetry API Readiness:
    - API keys: https://abetterrouteplanner.com/home/app/api-keys/telemetry
    - Rate Limit: 2 requests / sec per key (pacing enforced at 0.5s minimum interval)
    - Token Exchange: POST https://api.iternio.com/1/oauth/token?client_id=<id>&client_secret=<api_key>&code=<code>
"""

import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Tuple, Any

_lib_dir = os.path.dirname(os.path.abspath(__file__))
_tools_dir = os.path.dirname(_lib_dir)
_repo_root = os.path.dirname(_tools_dir)

if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from tessie_api_common import (
    load_config,
    load_vehicle_spec,
    api_get,
    get_token,
    get_vin,
)

DEFAULT_VEHICLE_TYPECODE = "tesla:myl:25:88:dual:ccs"  # Tesla Model YL AWD (LG 5N NMC 88.2 kWh gross)
DEFAULT_USABLE_KWH = 85.0  # ~85.0 kWh usable capacity (LG pack)
DEFAULT_CONSUMPTION_WH_KM = 171.1  # ~17.1 kWh/100km calibrated reference consumption from ABRP
ABRP_MIN_INTERVAL_SEC = 0.5  # 2 requests / sec rate limit per key

_last_abrp_request_time = 0.0


def _pace_abrp_request():
    """Enforces the 2 requests / sec rate limit (>= 0.5s spacing) for Iternio/ABRP API."""
    global _last_abrp_request_time
    now = time.time()
    elapsed = now - _last_abrp_request_time
    if elapsed < ABRP_MIN_INTERVAL_SEC:
        time.sleep(ABRP_MIN_INTERVAL_SEC - elapsed)
    _last_abrp_request_time = time.time()


_cached_telemetry_data = None
_cached_telemetry_time = 0.0


def fetch_abrp_telemetry(api_key: str, user_token: str, timeout: int = 10, force: bool = False) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Queries latest vehicle telemetry and configuration from ABRP Telemetry API.
    Endpoint: https://api.iternio.com/1/tlm/get_telemetry?api_key=<api_key>&token=<token>
    Returns: (result_dict_or_none, full_response_dict)
    """
    global _cached_telemetry_data, _cached_telemetry_time
    now = time.time()
    if not force and _cached_telemetry_data is not None and (now - _cached_telemetry_time) < 30.0:
        return _cached_telemetry_data, {"status": "ok", "result": _cached_telemetry_data, "cached": True}

    _pace_abrp_request()
    params = {
        "api_key": api_key,
        "token": user_token,
    }
    url = f"https://api.iternio.com/1/tlm/get_telemetry?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "TeslaTools/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
            if data.get("status") == "ok" and "result" in data:
                _cached_telemetry_data = data["result"]
                _cached_telemetry_time = now
                return data["result"], data
            return None, data
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return None, {"error": f"HTTP {e.code}: {err_body}"}
    except Exception as e:
        return None, {"error": str(e)}


def get_abrp_token_file() -> str:
    """Returns the preferred path for storing the generated ABRP user token."""
    cfg = load_config()
    tessie_dir = cfg.get("directories", {}).get("data")
    if tessie_dir:
        expanded = os.path.expanduser(tessie_dir)
        if os.path.isdir(expanded):
            return os.path.join(expanded, ".abrp_token.json")
    return os.path.join(_repo_root, ".abrp_token.json")


def save_abrp_token(token: str, metadata: Optional[Dict[str, Any]] = None) -> str:
    """Saves generated ABRP user token to local secure cache outside config.json."""
    payload = {
        "token": token,
        "saved_at": time.time(),
        "metadata": metadata or {}
    }
    candidate_paths = [
        get_abrp_token_file(),
        os.path.join(_repo_root, ".abrp_token.json"),
        os.path.expanduser("~/.abrp_token.json"),
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
    raise RuntimeError(f"Could not save ABRP token to any path: {last_err}")


def load_abrp_token() -> Optional[str]:
    """Loads generated ABRP user token from local cache or environment."""
    if os.environ.get("ABRP_USER_TOKEN"):
        return os.environ["ABRP_USER_TOKEN"].strip()

    candidate_paths = [
        get_abrp_token_file(),
        os.path.expanduser("~/.abrp_token.json"),
        os.path.join(_repo_root, ".abrp_token.json"),
    ]

    for p in candidate_paths:
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "token" in data:
                        return str(data["token"]).strip()
                    elif isinstance(data, str) and data.strip():
                        return data.strip()
            except Exception:
                continue
    return None


def load_abrp_config() -> Dict[str, Any]:
    """Loads ABRP configuration block from config.json with robust fallbacks and vehicle spec resolution."""
    cfg = load_config()
    abrp_cfg = cfg.get("abrp", {})
    if not isinstance(abrp_cfg, dict):
        abrp_cfg = {}

    api_key = abrp_cfg.get("api_key") or abrp_cfg.get("app_api_key") or os.environ.get("ABRP_API_KEY")
    user_token = (
        abrp_cfg.get("user_token")
        or abrp_cfg.get("user_api_key")
        or abrp_cfg.get("token")
        or load_abrp_token()
    )

    # Check vehicle_spec.json (in ~/iCloud/Tesla/Tessie/) for exact vehicle specs
    spec = load_vehicle_spec() or {}
    spec_veh = spec.get("vehicle", {})
    spec_battery = spec.get("battery", {})
    spec_wltp = spec.get("wltp", {})
    spec_gvg = spec.get("gvg_reference", {})

    usable_kwh = (
        float(abrp_cfg["usable_kwh"]) if "usable_kwh" in abrp_cfg
        else (float(spec_battery.get("usable_kwh")) if spec_battery.get("usable_kwh") else DEFAULT_USABLE_KWH)
    )

    # Dynamic telemetry pull from ABRP if credentials exist
    tlm_result = None
    if api_key and user_token:
        tlm_result, _ = fetch_abrp_telemetry(api_key, user_token, timeout=4)

    # Auto-resolve vehicle typecode from ABRP or fallback
    v_typecode = (
        abrp_cfg.get("vehicle_typecode")
        or (tlm_result.get("typecode") if tlm_result else None)
        or DEFAULT_VEHICLE_TYPECODE
    )

    # Local vehicle name, derived from vehicle_spec.json's actual model/trim.
    # Fix (2026-09-21): this used to hardcode the literal "Tesla Model YL AWD"
    # whenever the trim happened to contain "YL", discarding whatever the
    # real spec said and never adapting if the car or its trim ever changes.
    # vehicle_spec.json's trim strings look like "YL (AWD Long Range - L
    # Premium AWD)" - the short code before the parenthetical - so build a
    # compact name from model + that code, falling back to the full trim
    # text (or just the model) when there's no code to split out.
    v_name = "Tesla Model Y"
    if spec_veh:
        model = str(spec_veh.get("model") or "Tesla Model Y").strip()
        trim = str(spec_veh.get("trim") or "").strip()
        if trim:
            trim_code = trim.split("(", 1)[0].strip()
            v_name = f"{model} {trim_code}".strip() if trim_code else f"{model} {trim}".strip()
        else:
            v_name = model

    # Reference consumption (Wh/km): prefer ABRP calibrated live reference, then config, then GVG/spec
    if "consumption_wh_km" in abrp_cfg:
        consumption_wh_km = float(abrp_cfg["consumption_wh_km"])
    elif tlm_result and (tlm_result.get("telemetry") or {}).get("calib_ref_cons"):
        consumption_wh_km = round(float(tlm_result["telemetry"]["calib_ref_cons"]), 1)
    elif spec_gvg.get("real_world_efficiency_kwh_per_100km"):
        base_kwh_100 = float(spec_gvg["real_world_efficiency_kwh_per_100km"])
        consumption_wh_km = round(base_kwh_100 * 10.0 * 1.15, 1)
    elif spec_wltp.get("efficiency_kwh_per_100km"):
        base_kwh_100 = float(spec_wltp["efficiency_kwh_per_100km"])
        consumption_wh_km = round(base_kwh_100 * 10.0 * 1.25, 1)
    else:
        consumption_wh_km = DEFAULT_CONSUMPTION_WH_KM

    client_id = (
        abrp_cfg.get("client_id")
        or os.environ.get("ABRP_CLIENT_ID")
        or (str(tlm_result.get("user_id")) if tlm_result and tlm_result.get("user_id") else None)
    )

    return {
        "client_id": client_id,
        "api_key": api_key,
        "user_token": user_token,
        "vehicle_typecode": v_typecode,
        "vehicle_name": v_name,
        "default_departure_soc": abrp_cfg.get("default_departure_soc", "live"),
        "target_arrival_soc": float(abrp_cfg.get("target_arrival_soc", 10)),
        "max_charge_soc": float(abrp_cfg.get("max_charge_soc", 85)),
        "corridor_radius_km": float(abrp_cfg.get("corridor_radius_km", 5.0)),
        "consumption_wh_km": consumption_wh_km,
        "usable_kwh": usable_kwh,
        "live_telemetry": tlm_result.get("telemetry") if tlm_result else None,
    }


def fetch_oauth_token(client_id: str, api_key: str, auth_code: str, state: Optional[str] = None) -> Tuple[Optional[str], Dict[str, Any]]:
    """
    Exchanges an ABRP OAuth authorization code for a reusable access_token.
    Endpoint: https://api.iternio.com/1/oauth/token?client_id=<id>&client_secret=<api_key>&code=<code>
    """
    _pace_abrp_request()
    params = {
        "client_id": client_id,
        "client_secret": api_key,
        "code": auth_code,
    }
    if state:
        params["state"] = state

    url = f"https://api.iternio.com/1/oauth/token?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="POST", headers={"User-Agent": "TeslaTools/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
            access_token = data.get("access_token")
            return access_token, data
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return None, {"error": f"HTTP {e.code}: {err_body}"}
    except Exception as e:
        return None, {"error": str(e)}


def get_oauth_auth_url(client_id: str, redirect_uri: str = "http://localhost:8080", scope: str = "get_telemetry set_telemetry", state: Optional[str] = None) -> str:
    """Generates the ABRP OAuth user authorization URL."""
    params = {
        "client_id": str(client_id),
        "scope": scope,
        "response_type": "code",
        "redirect_uri": redirect_uri,
    }
    if state:
        params["state"] = state
    return f"https://abetterrouteplanner.com/oauth/auth?{urllib.parse.urlencode(params)}"


def fetch_user_info(access_token: str, api_key: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Fetches user and vehicle information from ABRP /1/oauth/me.
    Endpoint: https://api.iternio.com/1/oauth/me?access_token=<token>&api_key=<api_key>
    Returns: (user_info_dict_or_none, response_data_dict)
    """
    _pace_abrp_request()
    params = {
        "access_token": access_token,
        "api_key": api_key,
    }
    url = f"https://api.iternio.com/1/oauth/me?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "TeslaTools/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
            return data, data
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return None, {"error": f"HTTP {e.code}: {err_body}"}
    except Exception as e:
        return None, {"error": str(e)}


def fetch_live_battery_soc(timeout: int = 5) -> Tuple[Optional[int], Dict[str, Any]]:
    """
    Queries live vehicle battery state from Tessie API (/state).
    Returns (soc_pct, details_dict). If offline or unreachable, returns (None, {}).
    """
    cfg = load_config()
    token = get_token(None, cfg)
    vin = get_vin(None, cfg)

    if not token or not vin:
        return None, {"error": "Missing Tessie API token or VIN in config.json"}

    status_code, raw_bytes = api_get("state", vin, token, timeout=timeout)
    if status_code != 200 or not raw_bytes:
        return None, {"error": f"Tessie API returned status {status_code}"}

    try:
        data = json.loads(raw_bytes)
        charge_state = data.get("charge_state", {})
        battery_level = charge_state.get("battery_level")
        if battery_level is not None:
            details = {
                "battery_level": int(battery_level),
                "battery_range_km": round(float(charge_state.get("battery_range", 0.0)) * 1.60934, 1) if charge_state.get("battery_range") else None,
                "charging_state": charge_state.get("charging_state"),
                "time_to_full_charge": charge_state.get("time_to_full_charge"),
                "timestamp": data.get("timestamp"),
            }
            return int(battery_level), details
    except Exception as e:
        return None, {"error": f"Failed to parse Tessie state: {e}"}

    return None, {"error": "No battery_level field in response"}


def resolve_departure_soc(cli_soc_arg: Optional[str] = None) -> Tuple[int, str, Optional[Dict[str, Any]]]:
    """
    Resolves effective departure SoC % based on CLI override and config defaults.
    Returns: (soc_pct, source_label, live_details_or_none)
    """
    abrp_cfg = load_abrp_config()
    target_spec = cli_soc_arg if cli_soc_arg is not None else abrp_cfg["default_departure_soc"]

    # Check if string requests "live"
    if str(target_spec).strip().lower() == "live":
        live_soc, details = fetch_live_battery_soc(timeout=4)
        if live_soc is not None:
            return live_soc, "Tessie Live", details
        else:
            # Fallback when offline
            fallback_soc = 90
            return fallback_soc, "Default (Tessie Offline)", None

    # Parse numeric value
    try:
        val = int(float(target_spec))
        clamped = max(1, min(100, val))
        return clamped, "CLI / Config Override" if cli_soc_arg is not None else "Config Default", None
    except (ValueError, TypeError):
        return 90, "Fallback", None


def estimate_arrival_soc(
    departure_soc: float,
    distance_km: float,
    detour_km: float = 0.0,
    consumption_wh_km: float = DEFAULT_CONSUMPTION_WH_KM,
    usable_kwh: float = DEFAULT_USABLE_KWH
) -> Tuple[float, float]:
    """
    Calculates arrival SoC % and total energy consumed (kWh) for a given distance and detour.
    Returns: (arrival_soc_pct, energy_consumed_kwh)
    """
    total_km = max(0.0, distance_km + detour_km)
    energy_consumed_kwh = (total_km * consumption_wh_km) / 1000.0
    initial_kwh = (departure_soc / 100.0) * usable_kwh
    remaining_kwh = initial_kwh - energy_consumed_kwh
    arrival_soc = (remaining_kwh / usable_kwh) * 100.0
    return arrival_soc, energy_consumed_kwh


def format_soc_badge(soc_pct: float, target_min_soc: float = 10.0) -> Tuple[str, str]:
    """
    Formats the predicted arrival SoC into a color-coded string and status category.
    Categories: 'ok', 'warning', 'critical', 'unreachable'
    """
    C_GREEN = "\033[92m"
    C_YELLOW = "\033[93m"
    C_RED = "\033[91m"
    C_CYAN = "\033[96m"
    C_RESET = "\033[0m"

    rounded_soc = int(round(soc_pct))

    if rounded_soc >= 25:
        text = f"{rounded_soc}%"
        styled = f"{C_GREEN}🔋 {text:>3}{C_RESET}"
        return styled, "ok"
    elif rounded_soc >= target_min_soc:
        text = f"{rounded_soc}%"
        styled = f"{C_YELLOW}🟡 {text:>3}{C_RESET}"
        return styled, "warning"
    elif rounded_soc >= 0:
        text = f"{rounded_soc}%"
        styled = f"{C_RED}⚠️  {text:>3}{C_RESET}"
        return styled, "critical"
    else:
        styled = f"{C_RED}🔴 Req Stop{C_RESET}"
        return styled, "unreachable"


def generate_abrp_deep_link(
    origin_coords: Tuple[float, float],
    dest_coords: Tuple[float, float],
    via_coords: Optional[List[Tuple[float, float]]] = None,
    vehicle_typecode: Optional[str] = None,
    departure_soc: Optional[int] = None,
    target_soc: Optional[int] = None,
    max_soc: Optional[int] = None,
) -> str:
    """
    Generates a pre-populated 1-Click ABRP (A Better Routeplanner) interactive URL.
    Opens directly in ABRP web or mobile app with vehicle model and route stops configured.
    """
    abrp_cfg = load_abrp_config()
    v_type = vehicle_typecode or abrp_cfg.get("vehicle_typecode", DEFAULT_VEHICLE_TYPECODE)
    dep_soc = departure_soc if departure_soc is not None else 90
    arr_soc = target_soc if target_soc is not None else int(abrp_cfg.get("target_arrival_soc", 10))

    params = {
        "origin": f"{origin_coords[0]:.6f},{origin_coords[1]:.6f}",
        "destination": f"{dest_coords[0]:.6f},{dest_coords[1]:.6f}",
        "origin_soc": str(dep_soc),
        "target_soc": str(arr_soc),
        "vehicle_model": v_type,
    }

    if via_coords:
        via_str = "|".join(f"{lat:.6f},{lon:.6f}" for lat, lon in via_coords)
        params["via"] = via_str

    if max_soc is not None:
        params["max_soc"] = str(max_soc)

    query_string = urllib.parse.urlencode(params)
    return f"https://abetterrouteplanner.com/?{query_string}"
