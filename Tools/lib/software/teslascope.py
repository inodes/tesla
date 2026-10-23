"""teslascope.py - the Teslascope software source (https://teslascope.com/api/software), stored exactly as it answers.

    GET /api/software            -> {"code": 200, "response": ["2026.38.5.10", "2026.38.5.5", ...]}      every version it knows, newest first
    GET /api/software/<version>  -> {"code": 200, "response": {version, features, previous_features, countries, metrics, pending, ...}}

No token is needed for these two. A version file is Teslascope's answer, byte for byte:
    <data>/software/teslascope/list.json
    <data>/software/teslascope/versions/<version>.json
and every look at a version's numbers (cars on it, cars waiting, by country and model) is added to <data>/software/uptake.jsonl, because Teslascope
only shows the numbers as they are now, so the history of them can only be kept from the day they were first seen.

What is asked, and when (a version changes slowly, so this is a handful of requests a day once the first run has finished):
    a version never fetched                 -> asked
    a version still moving                  -> asked again after `refetch_hours` (cars still waiting, or taken in the last `settle_days`)
    a version that has settled              -> never asked again (marked complete in fetch_ledger.json)
One request at a time, paced, identified by name, with a growing pause after a rate limit or a dropped connection.
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE = "https://teslascope.com/api/software"
UA = "Tesellate/1 (personal Tesla data tool; software version tracker)"
KIND = "software_version"
DIRNAME = os.path.join("software", "teslascope")
UPTAKE = os.path.join("software", "uptake.jsonl")


class Refused(Exception):
    """The source refused us (403, or a challenge page): stop and say so, never push on."""


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"")


def request(url, get=http_get, sleep=time.sleep, tries=6, log=lambda s: None):
    """-> (status, body). 429 / 5xx / dropped connections are retried with a growing pause; 404 is an answer; 401/403 raise Refused."""
    status, body = 0, b""
    for n in range(1, tries + 1):
        try:
            status, body = get(url)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            status, body = 0, str(e).encode()
        if status == 200 or status == 404:
            return status, body
        if status in (401, 403):
            raise Refused("HTTP %d from %s" % (status, url))
        if 400 <= status < 500 and status != 429:
            return status, body
        if n == tries:
            break
        wait = min(2 * 2 ** (n - 1), 90) * (1 + 0.2 * (n % 3))
        if status == 429:
            wait = max(wait, 15)
        log("  %s -> %s, trying again in %d s" % (url.rsplit("/", 1)[-1], status or "no answer", wait))
        sleep(wait)
    return status, body


def paths(data_dir):
    root = os.path.join(data_dir, DIRNAME)
    return root, os.path.join(root, "versions"), os.path.join(root, "list.json")


def version_file(data_dir, version):
    return os.path.join(paths(data_dir)[1], "%s.json" % version)


def _atomic(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(body)
    os.replace(tmp, path)


def parse_envelope(body):
    """-> the "response" of an answer, or None when it is not a normal Teslascope answer."""
    try:
        d = json.loads(body.decode("utf-8-sig") if isinstance(body, (bytes, bytearray)) else body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(d, dict) or d.get("code") != 200:
        return None
    return d.get("response")


def iso(ts):
    """Teslascope's '2026-10-20T07:52:55.000000Z' / '...+00:00' -> epoch seconds (UTC), or None."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def is_settled(resp, now, settle_days):
    """A version no car has taken for settle_days (3 times as long when a car is still shown waiting: some wait for ever), first seen over a month ago."""
    pend = resp.get("pending") if isinstance(resp.get("pending"), dict) else {}
    last = iso(resp.get("latest_received_at")) or iso(resp.get("updated_at")) or iso(resp.get("firstSpotted"))
    first = iso(resp.get("firstSpotted")) or iso(resp.get("created_at"))
    if last is None or first is None:
        return False
    quiet = settle_days * 86400 * (3 if (pend.get("total") or 0) > 0 else 1)
    return now - last > quiet and now - first > 30 * 86400


def uptake_line(resp, now):
    """The numbers of this look at a version, small enough to keep one per change."""
    models = {}
    for m in (resp.get("metrics") or {}).get("models") or []:
        if isinstance(m, dict):
            models["%s_%s" % (m.get("car_type") or "", m.get("driver_assist") or "")] = m.get("count")
    pend = resp.get("pending") if isinstance(resp.get("pending"), dict) else {}
    return {
        "at": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": resp.get("version"),
        "installs": resp.get("count"),
        "pending": pend.get("total"),
        "countries": {str(c["country"]): c.get("count") for c in resp.get("countries") or [] if isinstance(c, dict) and c.get("country")},
        "models": models,
    }


def _same_numbers(a, b):
    f = lambda x: json.dumps({k: x.get(k) for k in ("installs", "pending", "countries", "models")}, sort_keys=True)
    return f(a) == f(b)


def last_uptake(data_dir):
    """-> {version: its last kept line}. Read once per run."""
    out = {}
    try:
        with open(os.path.join(data_dir, UPTAKE), encoding="utf-8") as fh:
            for ln in fh:
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                out[d.get("version")] = d
    except OSError:
        pass
    return out


def append_uptake(data_dir, line):
    p = os.path.join(data_dir, UPTAKE)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, separators=(",", ":"), sort_keys=True) + "\n")


def body_hash(b):
    return hashlib.sha1(b).hexdigest()[:12]
