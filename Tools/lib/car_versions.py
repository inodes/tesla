"""car_versions.py - the software version the car is on, and the FSD version, over time.

Tessie gives the car's current software (Get Vehicle: vehicle_state.car_version, e.g. "2026.38.5.5 3f9a1c2b7d") and the release notes of a version
(Get Release Notes), but no history. So the history is kept from the first run on: each time the sync sees a version it has not seen, a line is added with
the time it was first seen and that version's release notes. The FSD version is read from those notes where they name it ("FSD (Supervised) v14.3.12");
it can also be entered by hand, and a hand-entered value is never overwritten.

    <data>/car_versions.json     {"current": "2026.38.5.5", "staged": null, "versions": [{version, build, first_seen, last_seen, fsd, source, notes}]}

    parse_car_version(text)          "2026.38.5.5 3f9a1c2b7d" -> ("2026.38.5.5", "3f9a1c2b7d")
    fsd_from_notes(notes)            the FSD version a version's release notes name, or None
    record(data_dir, car_version, notes=None, staged=None, now=None)    note the version the car is on now -> (entry, is_new)
    enter(data_dir, version, fsd=None, first_seen=None)                 add or correct a version by hand
    journey(data_dir)                the versions oldest first, each with when the car went onto it and when it left (None = still on it)
    load(data_dir) / history(data_dir)   what is kept / the versions newest first

Besides the versions, the file keeps `changes` (one line each time the car went from one version to another, with the time it was seen) and `updates` (each version the
car was offered, staged, with the time it was first seen): the updates the car took and the ones on the way. An entry entered by hand may say `between`
[after, before]: the car got the version some time in that window (the first and last evidence), and a `note` saying how that is known.
"""
import json
import os
import re
import tempfile
import time

FILE = "car_versions.json"
_FSD = re.compile(r"(?:FSD|Full\s+Self-Driving)[^\n.]{0,60}?\bv?(\d{1,2}\.\d+(?:\.\d+){0,2})\b", re.I)


def parse_car_version(text):
    parts = (text or "").split()
    return (parts[0], parts[1] if len(parts) > 1 else "") if parts else ("", "")


def _text(notes):
    out = []
    for n in notes or []:
        out += [str(n.get(k) or "") for k in ("title", "subtitle", "description")]
    return " ".join(s for s in out if s)


def fsd_from_notes(notes):
    """The FSD version named in release notes (a list of {title, subtitle, description}), or None. A software version such as 2026.38.5.5 is not taken for it."""
    m = _FSD.search(_text(notes))
    return m.group(1) if m else None


def _path(data_dir):
    return os.path.join(data_dir, FILE)


def load(data_dir):
    try:
        with open(_path(data_dir), encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("versions", [])
        return d
    except (OSError, ValueError):
        return {"current": None, "staged": None, "versions": []}


def _save(data_dir, d):
    os.makedirs(data_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=data_dir, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    os.replace(tmp, _path(data_dir))


def _iso(now):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))


def record(data_dir, car_version, notes=None, staged=None, now=None):
    """Note that the car is on car_version now. notes = its release notes, when they were fetched. Returns (the entry, True when this version was not known)."""
    version, build = parse_car_version(car_version)
    if not version:
        return None, False
    now = time.time() if now is None else now
    d = load(data_dir)
    e = next((v for v in d["versions"] if v["version"] == version), None)
    new = e is None
    if new:
        e = dict(version=version, build=build, first_seen=_iso(now), last_seen=_iso(now), fsd=None, source="tessie", notes=[])
        d["versions"].append(e)
    e["last_seen"] = _iso(now)
    e["build"] = build or e.get("build", "")
    if notes:
        e["notes"] = [dict(title=n.get("title"), subtitle=n.get("subtitle"), description=n.get("description")) for n in notes]
        if not e.get("fsd_by_hand"):
            e["fsd"] = fsd_from_notes(notes) or e.get("fsd")
    was = d.get("current")
    if was and was != version:
        d.setdefault("changes", []).append(dict(at=_iso(now), frm=was, to=version))
    d["current"], d["staged"] = version, (parse_car_version(staged)[0] or None) if staged else None
    if d["staged"] and not any(u["version"] == d["staged"] for u in d.setdefault("updates", [])):
        d["updates"].append(dict(version=d["staged"], seen_at=_iso(now)))
    d["versions"].sort(key=lambda v: v["first_seen"])
    _save(data_dir, d)
    return e, new


def enter(data_dir, version, fsd=None, first_seen=None, between=None, note=None):
    """A version (and its FSD version) entered by hand: for the ones seen before the log began. first_seen is an ISO date or time. between = [after, before]: the
    window the car got it in (when that is only known roughly); note = how it is known."""
    d = load(data_dir)
    e = next((v for v in d["versions"] if v["version"] == version), None)
    if e is None:
        e = dict(version=version, build="", first_seen=first_seen or _iso(time.time()), last_seen=first_seen or _iso(time.time()), fsd=None, source="hand", notes=[])
        d["versions"].append(e)
    elif first_seen:
        e["first_seen"] = first_seen
    if fsd is not None:
        e["fsd"], e["fsd_by_hand"] = fsd, True
    if between:
        e["between"] = list(between)
    if note:
        e["note"] = note
    d["versions"].sort(key=lambda v: v["first_seen"])
    _save(data_dir, d)
    return e


def journey(data_dir):
    """The car's versions oldest first: [{version, fsd, from, to, between, note, source}]. `from` is when the car was first seen on it (or the end of the window it got
    it in), `to` is when it was first seen on the next one, None while it is still on it."""
    d = load(data_dir)
    vs = sorted(d["versions"], key=lambda v: v["first_seen"])
    out = []
    for i, v in enumerate(vs):
        nxt = vs[i + 1] if i + 1 < len(vs) else None
        out.append(dict(version=v["version"], fsd=v.get("fsd"), **{"from": v["first_seen"]}, to=(nxt["first_seen"] if nxt else None), between=v.get("between"), note=v.get("note"),
                        source=v.get("source"), current=v["version"] == d.get("current")))
    return out


def history(data_dir):
    return sorted(load(data_dir)["versions"], key=lambda v: v["first_seen"], reverse=True)
