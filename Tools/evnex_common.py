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
  - Source of truth is Tessie/config.json ("evnex_username"/"evnex_password"),
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

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tessie_api_common import load_config  # noqa: E402  (reuse Tessie/config.json loader)

ENV_USERNAME = "EVNEX_CLIENT_USERNAME"
ENV_PASSWORD = "EVNEX_CLIENT_PASSWORD"


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
            "Missing " + " and ".join(missing) + ". Set in Tessie/config.json "
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
