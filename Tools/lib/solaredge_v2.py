#!/usr/bin/env python3
"""
solaredge_v2.py - the SolarEdge Monitoring API V2 client (V1 is retired 2026-11-01/03; docs: api-docs.solaredge.com, "Migrating from V1 to V2").

Two ways in, both sent as one header:
  Site Access (a homeowner's own site): OAuth 2.0 authorization code. config.json "solaredge": {"client_id", "client_secret"} (an application made at
      developer.solaredge.com with access type Site Access, scopes SITE_DATA + DEVICE_DATA, redirect URL http://127.0.0.1:8765/solaredge/callback).
      The site owner approves once at authorize_url(); the code is exchanged for a 2-hour access token and a rotating refresh token (the old one stops working at
      once, so the pair is saved every time; unused for 14 days it lapses). Tokens live in resources/api_cache/solaredge_oauth.json (gitignored, mode 600).
  Fleet Access (an installer's account): config.json "solaredge": {"fleet_api_key"} -> X-API-Key.

Everything else (the PV provider, the app) calls get() / meter_rows() here and never sees which of the two is in use.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from lib.tessie_api_common import REPO_ROOT, load_config

BASE = "https://monitoringapi.solaredge.com/v2"
CONNECT = "https://connect.solaredge.com/authorize"
TOKEN_URL = BASE + "/oauth2/token"
TOKEN_FILE = os.path.join(REPO_ROOT, "resources", "api_cache", "solaredge_oauth.json")
SENTINEL = "solaredge-v2"                       # what resolve_credentials hands the callers in place of a V1 api_key: "use this module's auth"
_LOCK = threading.Lock()
SKEW = 120                                      # refresh when the access token has under this many seconds left

# the meter series that make up the five numbers the PV cache keeps (SolarEdge names, case-insensitive); energy where the API gives it, else mean power x interval
FIELD_OF = {"productionenergy": "production", "consumptionenergy": "consumption", "exportenergy": "export", "importenergy": "import",
            "productionpower": "production", "consumptionpower": "consumption", "exportpower": "export", "importpower": "import"}


class V2Error(Exception):
    def __init__(self, status, detail):
        super().__init__("HTTP %s: %s" % (status, detail))
        self.status, self.detail = status, detail


# ---- configuration and tokens -------------------------------------------------------------------------------------------------------------------------------

def _cfg(config=None):
    se = (config if config is not None else load_config()).get("solaredge") or {}
    real = lambda v: v if v and not str(v).startswith(("PASTE_", "YOUR_")) else None
    return dict(site_id=real(se.get("site_id")), client_id=real(se.get("client_id")), client_secret=real(se.get("client_secret")),
                fleet_key=real(se.get("fleet_api_key")), v1_key=real(se.get("api_key")))


def load_tokens():
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            t = json.load(f)
        return t if isinstance(t, dict) else {}
    except (OSError, ValueError):
        return {}


def save_tokens(t):
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    tmp = TOKEN_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(t, f, indent=1)
    os.replace(tmp, TOKEN_FILE)


def mode(config=None):
    """-> 'fleet' | 'oauth' (connected) | 'oauth-pending' (app configured, site not yet approved) | 'v1' (only the old key) | None"""
    c = _cfg(config)
    if c["fleet_key"]:
        return "fleet"
    if c["client_id"] and c["client_secret"]:
        return "oauth" if load_tokens().get("refresh_token") else "oauth-pending"
    return "v1" if c["v1_key"] else None


def active(config=None):
    """True when requests go to V2 (a Fleet key, or an approved site)."""
    return mode(config) in ("fleet", "oauth")


def status(config=None):
    """-> dict for the app: mode, site_id, configured, connected, expires_at, error"""
    c, t, m = _cfg(config), load_tokens(), mode(config)
    return dict(mode=m, site_id=c["site_id"], configured=bool(c["client_id"] and c["client_secret"]), connected=m in ("fleet", "oauth"), granted_site=t.get("site_id"),
                access_expires_at=t.get("expires_at"), refreshed_at=t.get("refreshed_at"), error=t.get("error"))


def authorize_url(config=None, external_id=None):
    c = _cfg(config)
    if not c["client_id"]:
        raise V2Error(0, "solaredge.client_id is not set in config.json")
    q = {"client_id": c["client_id"]}
    if external_id:
        q["external_id"] = external_id
    return CONNECT + "?" + urllib.parse.urlencode(q)


def _post_token(body, timeout=30):
    req = urllib.request.Request(TOKEN_URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "tesla-tools/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except Exception as e:
        return None, {"error": "connection", "error_description": str(e)}


def _store(resp, site_id=None, prev=None):
    t = dict(prev or {})
    t.update(access_token=resp["access_token"], refresh_token=resp.get("refresh_token") or t.get("refresh_token"),
             expires_at=int(time.time()) + int(resp.get("expires_in") or 7200), refreshed_at=int(time.time()), error=None)
    if site_id:
        t["site_id"] = str(site_id)
    save_tokens(t)
    return t


def exchange_code(code, site_id=None, config=None):
    """The one-use code from the callback -> tokens saved. Raises V2Error with SolarEdge's own error_description."""
    c = _cfg(config)
    if not (c["client_id"] and c["client_secret"]):
        raise V2Error(0, "solaredge.client_id / client_secret are not set in config.json")
    with _LOCK:
        st, r = _post_token(dict(grant_type="authorization_code", code=code, client_id=c["client_id"], client_secret=c["client_secret"]))
        if st != 200 or "access_token" not in r:
            raise V2Error(st, r.get("error_description") or r.get("error") or "token exchange failed")
        return _store(r, site_id)


def _access_token(config=None, force=False):
    c = _cfg(config)
    with _LOCK:
        t = load_tokens()
        if not t.get("refresh_token"):
            raise V2Error(0, "SolarEdge is not connected")
        if not force and t.get("access_token") and t.get("expires_at", 0) - time.time() > SKEW:
            return t["access_token"]
        st, r = _post_token(dict(grant_type="refresh_token", refresh_token=t["refresh_token"], client_id=c["client_id"], client_secret=c["client_secret"]))
        if st != 200 or "access_token" not in r:
            msg = r.get("error_description") or r.get("error") or "token refresh failed"
            if st in (400, 401):                                         # the pair is dead (lapsed after 14 days, or the secret was regenerated): connect again
                t["error"] = msg
                save_tokens(t)
            raise V2Error(st, msg)
        return _store(r, prev=t)["access_token"]


def _headers(config=None, force=False):
    c = _cfg(config)
    h = {"Accept": "application/json", "User-Agent": "tesla-tools/1.0"}
    if c["fleet_key"]:
        h["X-API-Key"] = c["fleet_key"]
    else:
        h["Authorization"] = "Bearer " + _access_token(config, force)
    return h


# ---- requests -----------------------------------------------------------------------------------------------------------------------------------------------

_CALLS = []                                      # when this process made its recent calls (the free tier allows 10 a minute)


def _pace(config=None):
    cpm = int((load_config().get("solaredge") or {}).get("calls_per_minute") or 10) if config is None else int(((config.get("solaredge") or {}).get("calls_per_minute")) or 10)
    now = time.time()
    _CALLS[:] = [t for t in _CALLS if now - t < 60]
    if len(_CALLS) >= cpm:
        time.sleep(max(0.0, 60.5 - (now - _CALLS[0])))
        now = time.time()
        _CALLS[:] = [t for t in _CALLS if now - t < 60]
    _CALLS.append(time.time())


def get(path, params=None, config=None, timeout=30):
    """GET {BASE}{path}?params -> (status, parsed_json_or_None, raw_bytes). Same shape as lib.solaredge_common.api_get. Refreshes the token once on a 401; waits out a
    per-minute 429 (SolarEdge says when) and tries again; a monthly-credit 429 (minute allowance not used up) is returned as it is."""
    url = BASE + path + (("?" + urllib.parse.urlencode(params)) if params else "")
    refreshed, waits = False, 0
    while True:
        try:
            req = urllib.request.Request(url, headers=_headers(config, force=refreshed))
        except V2Error as e:
            return e.status or None, {"detail": e.detail}, e.detail.encode()
        _pace(config)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body, st = r.read(), r.status
        except urllib.error.HTTPError as e:
            body, st = e.read(), e.code
            if st == 401 and not refreshed and not _cfg(config)["fleet_key"]:
                refreshed = True
                continue
            if st == 429 and waits < 2 and (e.headers.get("x-ratelimit-remaining-minute") == "0" or e.headers.get("retry-after")):
                waits += 1
                ra = e.headers.get("retry-after")
                time.sleep(min(65.0, float(ra) if ra and ra.replace(".", "", 1).isdigit() else 30.0))
                continue
        except Exception as e:
            return None, None, str(e).encode()
        try:
            return st, json.loads(body), body
        except ValueError:
            return st, None, body


_TZ = {}


def site_tz(site_id, config=None):
    """the site's own time zone (GET /sites/{id} location.timezone, asked once per run), else this machine's"""
    if site_id not in _TZ:
        tz = None
        st, parsed, _ = get("/sites/%s" % site_id, None, config)
        name = ((parsed or {}).get("location") or {}).get("timezone") if st == 200 and isinstance(parsed, dict) else None
        if name:
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(name)
            except Exception:
                tz = None
        _TZ[site_id] = tz
    return _TZ[site_id]


def _stamp(dt, tz=None):
    """ISO 8601 with a UTC offset: V2 answers 400 to a time with no zone. A naive datetime is a wall-clock time at the site (tz), else on this machine."""
    if dt.tzinfo:
        return dt.isoformat(timespec="seconds")
    return (dt.replace(tzinfo=tz) if tz else dt.astimezone()).isoformat(timespec="seconds")


def _when(s):
    """a V2 timestamp (site-local with its offset) -> naive site-local datetime"""
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _series(block):
    """one metric block -> (unit, [(naive datetime, float|None)]). Accepts {"unit", "values": [{"timestamp"|"date", "value"}]}."""
    if not isinstance(block, dict):
        return None, []
    out = []
    for v in block.get("values") or []:
        if isinstance(v, dict):
            ts = _when(v.get("timestamp") or v.get("date") or v.get("time"))
            val = v.get("value")
            if ts is not None:
                out.append((ts, float(val) if isinstance(val, (int, float)) else None))
    return block.get("unit"), out


def _wh(value, unit):
    """a value in the unit it came with -> Wh (energy) or W (power) is the caller's job; here only the energy units are scaled"""
    u = str(unit or "WH").upper()
    return value * {"WH": 1.0, "KWH": 1000.0, "MWH": 1e6}.get(u, 1.0)


def _w(value, unit):
    u = str(unit or "W").upper()
    return value * {"W": 1.0, "KW": 1000.0, "MW": 1e6}.get(u, 1.0)


def parse_meter_telemetry(body, step_hours):
    """GET /sites/{id}/meters/telemetry body -> {naive start: {"production": kWh, "consumption": kWh, "export": kWh, "import": kWh}}, summed over the meters.
    Body shape: {"meters": {serial: {metricName: {"unit", "values": [{"timestamp", "value"}]}}}}. A metric given as energy is taken as it is; one given only as power
    is the bucket's mean power x step_hours."""
    meters = body.get("meters") if isinstance(body, dict) else None
    got, power_only = {}, {}
    if not isinstance(meters, dict):
        return {}
    for serial, metrics in meters.items():
        if not isinstance(metrics, dict):
            continue
        for name, block in metrics.items():
            fld = FIELD_OF.get(str(name).lower())
            if not fld:
                continue
            is_energy = str(name).lower().endswith("energy")
            unit, vals = _series(block)
            for ts, v in vals:
                if v is None:
                    continue
                kwh = _wh(v, unit) / 1000.0 if is_energy else _w(v, unit) / 1000.0 * step_hours
                (got if is_energy else power_only).setdefault(ts, {}).setdefault(fld, {}).setdefault(serial + ":" + name, kwh)
    out = {}
    for src in (power_only, got):                                        # energy wins over power for the same field
        for ts, flds in src.items():
            for fld, per in flds.items():
                out.setdefault(ts, {})[fld] = sum(per.values())
    return out


def _derive(rec):
    """the five PV numbers from what the meters gave. Self-consumption = what was produced and not exported."""
    prod, cons, exp, imp = rec.get("production"), rec.get("consumption"), rec.get("export"), rec.get("import")
    if prod is None and cons is not None and exp is not None and imp is not None:
        prod = cons - imp + exp
    if cons is None and prod is not None and exp is not None and imp is not None:
        cons = prod - exp + imp
    self_c = (prod - exp) if prod is not None and exp is not None else None
    return dict(production_kwh=prod, consumption_kwh=cons, feedin_kwh=exp, purchased_kwh=imp, self_consumption_kwh=self_c)


def meter_rows(site_id, date_from, date_to, resolution="QUARTER_HOUR", config=None, days_per_call=7, log=None):
    """[{ts (naive local start of bucket), production_kwh, consumption_kwh, feedin_kwh, purchased_kwh, self_consumption_kwh}] for the days date_from..date_to (inclusive
    date()s), and an error string or None. Chunked; a chunk the API calls too long (400) is halved and tried again."""
    step = {"QUARTER_HOUR": 0.25, "HOUR": 1.0, "DAY": 24.0}[resolution]
    merged, errors = {}, []
    todo = []
    d = date_from
    while d <= date_to:
        e = min(date_to, d + timedelta(days=days_per_call - 1))
        todo.append((d, e))
        d = e + timedelta(days=1)
    while todo:
        a, b = todo.pop(0)
        tz = site_tz(site_id, config)
        win = {"from": _stamp(datetime.combine(a, datetime.min.time()), tz), "to": _stamp(datetime.combine(b + timedelta(days=1), datetime.min.time()), tz)}
        st, parsed, raw = get("/sites/%s/meters/telemetry" % site_id, dict(resolution=resolution, **win), config)
        st2, prod, raw2 = get("/sites/%s/energy" % site_id, dict(resolution=resolution, unit="WH", **win), config)       # production is the site's own series: this site has no production meter
        if (st == 400 or st2 == 400) and b > a:
            mid = a + (b - a) // 2
            todo[:0] = [(a, mid), (mid + timedelta(days=1), b)]
            continue
        bad = [(x, y, z) for x, y, z in ((st, parsed, raw), (st2, prod, raw2)) if x != 200 or not isinstance(y, dict)]
        if bad:
            x, y, z = bad[0]
            errors.append("%s..%s: HTTP %s: %s" % (a, b, x, (y or {}).get("detail") or (y or {}).get("message") or z[:200]))
            if x == 429:
                break
            continue
        for ts, rec in parse_meter_telemetry(parsed, step).items():
            merged.setdefault(ts, {}).update(rec)
        unit, vals = _series(prod)
        for ts, v in vals:
            if v is not None:
                merged.setdefault(ts, {})["production"] = _wh(v, unit) / 1000.0
    rows = [dict(ts=ts, **_derive(rec)) for ts, rec in sorted(merged.items())]
    return rows, ("; ".join(errors) if errors else None)


def installation_date(site_id, config=None):
    st, parsed, _ = get("/sites/%s" % site_id, None, config)
    if st == 200 and isinstance(parsed, dict):
        d = parsed.get("installationDate")
        if d:
            return _when(d)
    return None
