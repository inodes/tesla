#!/usr/bin/env python3
"""
tessie_api_common.py - Shared Tessie Developer API plumbing
==============================================================
Config/auth loading and the low-level HTTP call, factored out so
test_tessie_api.py and tessie_api_sync.py (and anything else that talks to
the API later) share one implementation instead of drifting apart.

See Tessie/TESSIE_API.md for endpoint/field notes, confirmed parameters,
and known limitations (this module is not a full API client - just enough
plumbing for this repo's own scripts).
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(REPO_ROOT, "Tessie", "config.json")
API_BASE = "https://api.tessie.com"

# Matches this repo's CSV convention (see Tessie/TESSIE_API.md) - the API
# itself defaults to mi/UTC.
DEFAULT_UNIT_PARAMS = {
    "distance_format": "km",
    "temperature_format": "c",
    "timezone": "Australia/Sydney",
}


def load_config():
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def get_token(cli_token, config):
    return cli_token or os.environ.get("TESSIE_ACCESS_TOKEN") or config.get("tessie_access_token")


def get_vin(cli_vin, config):
    return cli_vin or config.get("vin")


def api_get(path, vin, token, params=None, timeout=30):
    """GET https://api.tessie.com/{vin}/{path}?access_token=...&...
    Returns (status_code_or_None, raw_bytes). status is None only for a
    connection-level failure (DNS/timeout/blocked egress), not an HTTP error.

    Sends an Accept header matching the requested format - a fixed
    "Accept: application/json" on a format=csv request was seen causing a
    server-side HTTP 500 (content-negotiation clash) during testing."""
    params = dict(params or {})
    params["access_token"] = token
    url = f"{API_BASE}/{vin}/{path}?{urllib.parse.urlencode(params)}"
    accept = "text/csv, */*;q=0.1" if params.get("format") == "csv" else "application/json"
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "tessie-tools/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return None, str(e).encode("utf-8")


def extract_json_records(body):
    """Parse a /drives or /charges JSON response body into a list of record
    dicts, or raise ValueError with a clear message if the shape is wrong."""
    try:
        data = json.loads(body)
    except Exception as e:
        raise ValueError(f"response was not valid JSON: {e}") from e
    records = data.get("results") if isinstance(data, dict) else data
    if not isinstance(records, list):
        keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
        raise ValueError(f"unexpected response shape - top-level: {keys}")
    return records
