#!/usr/bin/env python3
"""
evnex_common.py - Shared Evnex credential loading + client bootstrap
======================================================================
Evnex (the maker of this car's home charger) doesn't issue API credentials
to residential/individual accounts - real API keys ("Client ID"/"Client
Secret") are only provisioned to Enterprise/CP-Link customers. Instead this
uses the community-maintained `evnex` python library
(https://github.com/hardbyte/python-evnex), which talks to the same
consumer-app API the Evnex phone app itself uses, authenticating with your
normal Evnex account email + password.

Credential handling rules (deliberate, not incidental):
  - Source of truth is config.json ("evnex_username"/"evnex_password"),
    the same file/pattern tessie_access_token already uses - see
    tessie_api_common.py.
  - EVNEX_CLIENT_USERNAME / EVNEX_CLIENT_PASSWORD environment variables
    override config.json if set. These are the `evnex` library's own env
    var names (see its README), reused here rather than inventing new ones.
  - No CLI flag for either value. A password passed as a command-line
    argument ends up in shell history and any other user's `ps` output on
    a shared machine - config.json/env vars don't have that problem.
  - Nothing in this module ever prints, logs, or includes the username or
    password in an exception message. `require_evnex_credentials()` reports
    which config KEYS are missing, never what (if anything) it did find.
    `get_authenticated_client()` scrubs the password out of the auth
    library's own exception text before re-raising, in case a future
    version of that library ever echoes request details on failure.
"""

from datetime import datetime
import os

# REQ-067: now a sibling module inside Tools/lib/ - a package-relative
# import replaces the old sys.path.insert()+flat-import trick.
from .tessie_api_common import load_config  # (reuse config.json loader)

ENV_USERNAME = "EVNEX_CLIENT_USERNAME"
ENV_PASSWORD = "EVNEX_CLIENT_PASSWORD"


def to_jsonable(obj):
    """Recursively convert pydantic models (and containers of them) into
    plain JSON-safe data. Falls back to str() for anything else exotic
    (e.g. datetime objects some fields might carry)."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        try:
            return to_jsonable(model_dump(mode="json"))
        except TypeError:
            return to_jsonable(model_dump())
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


class EvnexCredentialsError(RuntimeError):
    """Missing evnex_username/evnex_password. The message names which
    config keys/env vars are missing - never a credential value, even a
    partially-typed or wrong one."""


def get_evnex_username(config=None):
    config = load_config() if config is None else config
    return os.environ.get(ENV_USERNAME) or config.get("evnex_username") or None


def get_evnex_password(config=None):
    config = load_config() if config is None else config
    return os.environ.get(ENV_PASSWORD) or config.get("evnex_password") or None


def require_evnex_credentials(config=None):
    """(username, password), or raises EvnexCredentialsError."""
    config = load_config() if config is None else config
    username = get_evnex_username(config)
    password = get_evnex_password(config)
    missing = []
    if not username:
        missing.append("evnex_username")
    if not password:
        missing.append("evnex_password")
    if missing:
        raise EvnexCredentialsError(
            "Missing " + " and ".join(missing) + ". Set in config.json "
            "(see Tessie/config.example.json) or via the "
            f"{ENV_USERNAME}/{ENV_PASSWORD} environment variables."
        )
    return username, password


async def get_authenticated_client(config=None):
    """Returns an authenticated evnex.api.Evnex client. Raises
    EvnexCredentialsError if nothing usable is configured, or a plain
    RuntimeError (password-scrubbed) if Evnex itself rejects the login."""
    try:
        from evnex.api import Evnex
        from evnex.auth import EvnexAuth
    except ImportError as e:
        raise RuntimeError(
            "The `evnex` package isn't installed. It's in requirements.txt - "
            "run `direnv allow` (or `pip install -r requirements.txt`) to pick it up."
        ) from e

    username, password = require_evnex_credentials(config)
    auth = EvnexAuth()
    try:
        await auth.start_authentication(username, password)
    except Exception as e:
        # Scrub the password out of whatever the auth library's exception
        # says, on the off chance it ever echoes request details.
        safe_msg = str(e).replace(password, "***REDACTED***") if password else str(e)
        raise RuntimeError(f"Evnex authentication failed: {safe_msg}") from e
    return Evnex(auth=auth)
def session_energy_wh(attrs):
    """Real energy delivered (Wh) for one Evnex session's own `attributes`
    dict (the same dict sync_api_evnex.py writes as record["session"]
    ["attributes"], and analyse_session_cost.py reads back as session["session"]
    ["attributes"]), or None if Evnex didn't report a totalEnergyUsage at
    all for this session. Kept in one shared place so "did this session
    actually deliver power" is judged identically everywhere that asks -
    see is_real_charging_session() below."""
    teu = attrs.get("totalEnergyUsage")
    return teu.get("total") if isinstance(teu, dict) else None


def is_real_charging_session(attrs, min_wh=1.0):
    """False for a session that delivered ~zero energy - a connection
    blip (pairing/testing a charger, a momentary plug/unplug, a start
    that never actually drew power) rather than a real charge. Found via
    a real user report: swapping the home Evnex charger produced 5 new
    "sessions" within about an hour of setup, all totalEnergyUsage.total
    == 0.0 and (where present) transaction.reason == "EVDisconnected" -
    and the SAME pattern already existed in 23 of the 144 real Evnex
    session files on disk at the time, well predating this charger swap
    (one "reason": "Remote", the rest "EVDisconnected"; durations ranged
    from 0 seconds to several minutes - a real duration with zero energy
    is still not a charge, e.g. a plug-in that never actually started).

    True whenever energy is unknown/missing (Evnex just didn't report a
    figure) - absence of data is never treated as "not a real session",
    only an explicit ~zero reading is. min_wh is a floor rather than an
    exact-zero check only as a hedge against float noise; every real
    example seen so far reported exactly 0.0, never a small-but-nonzero
    residual."""
    wh = session_energy_wh(attrs)
    return wh is None or wh >= min_wh
