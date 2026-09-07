#!/usr/bin/env python3
"""
pvoutput_common.py - Shared PVOutput API plumbing
====================================================
Config/auth loading and the low-level HTTP call, factored out the same
way tessie_api_common.py serves test_tessie_api.py/tessie_api_sync.py -
so test_pvoutput_api.py and any later real sync script share one
implementation instead of drifting apart.

See Tessie/PVOUTPUT_API.md for endpoint/field notes, confirmed
parameters, rate limits, and known limitations (this module is not a
full API client - just enough plumbing for this repo's own scripts).
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(REPO_ROOT, "Tessie", "config.json")
API_BASE = "https://pvoutput.org/service/r2"

# Response headers PVOutput returns when the request itself sends
# "X-Rate-Limit: 1" - see Tessie/PVOUTPUT_API.md's Rate Limits section.
# Names confirmed against the real spec the user pasted (an earlier
# secondary-sourced pass of this doc guessed "X-Rate-Limit-Total" for the
# second one, which was wrong).
RATE_LIMIT_HEADERS = ("X-Rate-Limit-Remaining", "X-Rate-Limit-Limit", "X-Rate-Limit-Reset")


def load_config():
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def get_api_key(cli_value, config):
    return cli_value or os.environ.get("PVOUTPUT_API_KEY") or config.get("pvoutput_api_key")


def get_system_id(cli_value, config):
    return cli_value or os.environ.get("PVOUTPUT_SYSTEM_ID") or config.get("pvoutput_system_id")


def api_get(endpoint, api_key, system_id, params=None, timeout=30):
    """GET https://pvoutput.org/service/r2/{endpoint}.jsp?... using the
    confirmed header-based auth (X-Pvoutput-Apikey/X-Pvoutput-SystemId -
    NOT the X-Pid/X-Apikey names an earlier secondary-sourced pass of
    this repo's docs guessed, before the user supplied the real spec -
    see AGENTS.md REQ-039). Always sends "X-Rate-Limit: 1" so the
    response carries the account's real remaining-request headers.

    Returns (status_code_or_None, raw_bytes, response_headers_dict).
    status is None only for a connection-level failure (DNS/timeout/
    blocked egress), not an HTTP error - PVOutput returns plain-text
    error bodies with 4xx/5xx statuses, not JSON, so the caller decides
    how to surface those."""
    url = f"{API_BASE}/{endpoint}.jsp"
    if params:
        url += f"?{urllib.parse.urlencode(params)}"
    headers = {
        "X-Pvoutput-Apikey": api_key,
        "X-Pvoutput-SystemId": system_id,
        "X-Rate-Limit": "1",
        "User-Agent": "tesla-tools/1.0",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})
    except Exception as e:
        return None, str(e).encode("utf-8"), {}


def print_rate_limit(headers, log=print):
    """HTTP headers are case-insensitive, but api_get() returns a plain
    dict() of whatever casing the server actually sent - a real response
    seen from the user'"'"'s own account came back with different casing
    than assumed here, so this does a case-insensitive lookup instead of
    a direct dict .get() on the documented header names."""
    lowered = {k.lower(): v for k, v in headers.items()}
    found = {h: lowered.get(h.lower()) for h in RATE_LIMIT_HEADERS if lowered.get(h.lower()) is not None}
    if found:
        parts = [f"{k.replace('X-Rate-Limit-', '')}={v}" for k, v in found.items()]
        log(f"   rate limit: {', '.join(parts)}")
    else:
        log("   rate limit: (no X-Rate-Limit-* headers in response)")


def parse_status_rows(body):
    """Splits a getstatus.jsp response body into rows - PVOutput
    separates multiple readings (h=1 history) with ';', and each
    reading's own fields with ','. A single non-history reading is just
    one such comma-separated row with no semicolon."""
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    return [row.split(",") for row in text.split(";") if row]
