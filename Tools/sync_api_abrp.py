#!/usr/bin/env python3
"""
sync_api_abrp.py - ABRP / Iternio Telemetry Sync & Vehicle Profile Inspector
=============================================================================
NOTE (BUG-123, 2026-09-26): despite the "sync_" prefix, this is a MANUAL,
one-time OAuth setup / vehicle-profile inspection tool - it is not chained
into sync_all.sh or any other sync_*_all.sh pipeline, and nothing else in
the repo calls it automatically. Run it by hand whenever you need to
(re)authorize ABRP or inspect the live vehicle profile it sees. Its
underlying library, lib/abrp_common.py, IS genuinely used by charger_find.py
for live range/SoC estimation - that part is unaffected by this note.

Communicates with the ABRP (A Better Routeplanner / Iternio) Telemetry API:
- Validates client_id, api_key, and user_token configured in config.json
- Queries live battery level, health, calibrated consumption, and vehicle profile
- Generates OAuth authorization links and handles code exchanges

Usage:
  ./Tools/sync_api_abrp.py                 # Query vehicle telemetry & profile
  ./Tools/sync_api_abrp.py --auth-url      # Generate 1-click ABRP login link
  ./Tools/sync_api_abrp.py --code <auth_code>   # Exchange code and inspect vehicle profile
  ./Tools/sync_api_abrp.py --token <token>      # Query user info & custom vehicle directly (this run only)
  ./Tools/sync_api_abrp.py --token <token> --save-token  # ...and cache it for future runs

Token storage (BUG-141-style safety, applied here too):
  The one-time, manually-generated ABRP telemetry token (from
  https://abetterrouteplanner.com/home/app/api-keys/telemetry) never expires
  and never needs regenerating - the recommended place for it is directly in
  config.json's "abrp": {"user_token": "..."} block, which load_abrp_config()
  always checks FIRST. In that setup, .abrp_token.json is never touched.
  .abrp_token.json exists only to cache the OAuth access_token produced by
  --code - that one genuinely cannot be re-derived from client_id/api_key
  alone (it requires a one-time browser consent step), so it has to persist
  somewhere between runs. --token alone NEVER writes to that cache (or to
  config.json) - it only affects the current invocation, so inspecting or
  testing a token can't silently overwrite your real one; pass --save-token
  to actually persist it.
"""

import sys
import os
import json
import argparse
import webbrowser

_tools_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_tools_dir)
_lib_dir = os.path.join(_tools_dir, "lib")
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from lib.abrp_common import (
    load_abrp_config,
    fetch_oauth_token,
    fetch_user_info,
    get_oauth_auth_url,
    save_abrp_token,
    load_abrp_token,
    get_abrp_token_file,
)
from lib.tessie_api_common import load_config as _load_raw_config
from lib.table_formatter import pad_display

C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_CYAN = "\033[96m"
C_RED = "\033[91m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_RESET = "\033[0m"


def main():
    parser = argparse.ArgumentParser(description="ABRP / Iternio API Connectivity & Vehicle Inspector")
    parser.add_argument("--auth-url", action="store_true", help="Generate ABRP OAuth login URL")
    parser.add_argument("--code", type=str, help="Authorization code received from ABRP OAuth redirect")
    parser.add_argument("--token", type=str, help="User access token to inspect (this run only, unless --save-token is also passed)")
    parser.add_argument("--save-token", action="store_true", help="Persist --token to the local ABRP token cache (.abrp_token.json). Without this, --token only affects the current run.")
    parser.add_argument("--redirect-uri", type=str, default="http://localhost:8080", help="OAuth redirect URI (default: http://localhost:8080)")
    parser.add_argument("--open", action="store_true", help="Open OAuth login link directly in browser")

    args = parser.parse_args()
    abrp_cfg = load_abrp_config()

    client_id = abrp_cfg.get("client_id")
    api_key = abrp_cfg.get("api_key")

    print(f"\n⚡ {C_BOLD}ABRP / Iternio API Configuration:{C_RESET}")
    print(f"  • Client ID: {C_CYAN}{client_id or 'Auto-resolved'}{C_RESET}")
    print(f"  • API Key:   {C_CYAN}{api_key[:8] + '...' if api_key else 'Not Configured'}{C_RESET}")
    print(f"  • Local Spec:{C_GREEN} {abrp_cfg.get('vehicle_name')} ({abrp_cfg.get('usable_kwh')} kWh usable){C_RESET}")
    print()

    if not api_key:
        print(f"{C_RED}❌ Error: 'api_key' must be configured under 'abrp' in config.json.{C_RESET}", file=sys.stderr)
        print(f"   Manage API keys at: {C_CYAN}https://abetterrouteplanner.com/home/app/api-keys/telemetry{C_RESET}\n")
        sys.exit(1)

    if args.token:
        cli_token = args.token.strip()
        if args.save_token:
            # BUG-141 (OSM) taught us --token used for a one-off inspection/test
            # must never silently clobber the real cached/config token - only
            # persist when the user explicitly opts in.
            save_abrp_token(cli_token, {"source": "cli_token"})
            abrp_cfg = load_abrp_config()
        else:
            abrp_cfg["user_token"] = cli_token

    active_token = abrp_cfg.get("user_token")

    if active_token and not args.auth_url and not args.code:
        token_file = get_abrp_token_file()
        if args.token:
            token_src = "CLI argument (saved to cache)" if args.save_token else "CLI argument (not persisted - pass --save-token to cache it)"
        elif (_load_raw_config().get("abrp", {}) or {}).get("user_token") or (_load_raw_config().get("abrp", {}) or {}).get("user_api_key") or (_load_raw_config().get("abrp", {}) or {}).get("token"):
            token_src = "config.json (abrp.user_token)"
        else:
            token_src = f"Generated Cache ({os.path.basename(token_file)})"
        print(f"🔍 {C_CYAN}Querying ABRP Live Telemetry & Vehicle Profile using token from {token_src}...{C_RESET}")
        from lib.abrp_common import fetch_abrp_telemetry
        tlm_res, raw = fetch_abrp_telemetry(api_key, active_token)
        if tlm_res and not tlm_res.get("error"):
            _display_vehicle_telemetry(tlm_res)
        else:
            user_info, u_raw = fetch_user_info(active_token, api_key)
            if user_info and not user_info.get("error"):
                _display_user_profile(user_info)
            else:
                print(f"{C_RED}❌ Failed to fetch telemetry: {raw.get('error') or raw}{C_RESET}")
        return

    if args.code:
        code_val = args.code.strip()
        if "code=" in code_val:
            try:
                import urllib.parse
                parsed = urllib.parse.urlparse(code_val)
                query = urllib.parse.parse_qs(parsed.query)
                if "code" in query:
                    code_val = query["code"][0]
            except Exception:
                pass

        if not client_id:
            client_id = api_key

        print(f"🔄 {C_CYAN}Exchanging OAuth authorization code for access token...{C_RESET}")
        token, token_data = fetch_oauth_token(client_id, api_key, code_val)
        if token:
            cached_path = save_abrp_token(token, {"source": "oauth_code", "data": token_data})
            print(f"{C_GREEN}✔ Access Token successfully retrieved and cached to {cached_path}!{C_RESET}")
            print(f"🔍 {C_CYAN}Fetching user & custom vehicle profile from ABRP...{C_RESET}")
            from lib.abrp_common import fetch_abrp_telemetry
            tlm_res, raw = fetch_abrp_telemetry(api_key, token)
            if tlm_res and not tlm_res.get("error"):
                _display_vehicle_telemetry(tlm_res)
            else:
                user_info, raw = fetch_user_info(token, api_key)
                if user_info and not user_info.get("error"):
                    _display_user_profile(user_info)
                else:
                    print(f"{C_YELLOW}⚠ Token valid, but query returned: {raw}{C_RESET}")
        else:
            print(f"{C_RED}❌ Code exchange failed: {token_data.get('error') or token_data}{C_RESET}")
        return

    # Default / --auth-url flow
    auth_client = client_id or api_key
    auth_url = get_oauth_auth_url(auth_client, redirect_uri=args.redirect_uri)
    print(f"🔑 {C_BOLD}ABRP OAuth2 Authorization URL:{C_RESET}")
    print(f"   {C_CYAN}{auth_url}{C_RESET}\n")
    print(f"👉 {C_BOLD}Instructions to connect your custom ABRP vehicle:{C_RESET}")
    print(f"   1. Open the URL above in your browser (or run with {C_GREEN}--open{C_RESET})")
    print(f"   2. Log into ABRP and click 'Approve'")
    print(f"   3. Copy the {C_YELLOW}code=...{C_RESET} parameter from the redirect address bar")
    print(f"   4. Run: {C_GREEN}./Tools/sync_api_abrp.py --code <your_code>{C_RESET}\n")

    if args.open:
        webbrowser.open(auth_url)


def _display_vehicle_telemetry(data: dict):
    tlm = data.get("telemetry") or {}
    print(f"\n┌─────────────────────────────────────────────────────────────┐")
    print(f"│ 🚗 {C_BOLD}ABRP Custom Vehicle Profile & Live Telemetry{C_RESET}             │")
    print(f"├─────────────────────────────────────────────────────────────┤")
    print(f"│ • Vehicle Name:     {C_GREEN}{str(data.get('name')):<39}{C_RESET} │")
    print(f"│ • Vehicle Typecode: {C_CYAN}{str(data.get('typecode')):<39}{C_RESET} │")
    print(f"│ • Vehicle ID:       {str(data.get('vehicle_id')):<39} │")
    print(f"│ • User ID:          {str(data.get('user_id')):<39} │")
    print(f"│ • Connected Status: {C_GREEN + '🟢 Online / Connected' if data.get('is_connected') else C_YELLOW + '🟡 Offline'}{C_RESET:<39} │")
    if tlm:
        soc_val = tlm.get("soc")
        soh_val = tlm.get("soh")
        ref_cons = tlm.get("calib_ref_cons")
        ext_temp = tlm.get("ext_temp")
        odo = tlm.get("odometer")
        if soc_val is not None:
            print(f"│ • Battery SoC:      {C_GREEN}{soc_val:.1f}%{C_RESET:<39} │")
        if soh_val is not None:
            print(f"│ • Battery Health:   {soh_val:.1f}% SoH{'':<33} │")
        if ref_cons is not None:
            print(f"│ • Calibrated Cons:  {C_YELLOW}{ref_cons:.1f} Wh/km (ABRP Reference){C_RESET:<27} │")
        if ext_temp is not None:
            print(f"│ • Outside Temp:     {ext_temp:.1f}°C{'':<37} │")
        if odo is not None:
            print(f"│ • Odometer:         {odo:,.1f} km{'':<33} │")
    print(f"└─────────────────────────────────────────────────────────────┘\n")


def _display_user_profile(info: dict):
    print(f"\n┌─────────────────────────────────────────────────────────────┐")
    print(f"│ {C_BOLD}ABRP User & Custom Vehicle Profile{C_RESET}                          │")
    print(f"├─────────────────────────────────────────────────────────────┤")
    if "user_id" in info:
        print(f"│ • User ID:          {str(info.get('user_id')):<39} │")
    if "full_name" in info:
        print(f"│ • Full Name:        {str(info.get('full_name')):<39} │")
    if "email" in info:
        print(f"│ • Email:            {str(info.get('email')):<39} │")
    if "vehicle_name" in info:
        print(f"│ • Vehicle Name:     {C_GREEN}{str(info.get('vehicle_name')):<39}{C_RESET} │")
    if "vehicle_typecode" in info:
        print(f"│ • Vehicle Typecode: {C_CYAN}{str(info.get('vehicle_typecode')):<39}{C_RESET} │")
    if "vehicle_id" in info:
        print(f"│ • Vehicle ID:       {str(info.get('vehicle_id')):<39} │")
    print(f"└─────────────────────────────────────────────────────────────┘\n")


if __name__ == "__main__":
    main()
