"""drive_points.py - the ONE reader of a drive's telemetry file (Tessie /states), whatever form it was stored in.

Stored forms (a drive folder holds exactly one of them):

    drive_deepdive_<stamp>.json   what the app fetches now: Tessie's own answer, unchanged - {"results": [{"timestamp": <UTC epoch s>, ...}]}
                                  (or Tessie's condensed form, {"results": {"timestamps": [...], "latitudes": [...]}}; both are read).
                                  UTC is the only clock inside it, so a point can never be stored twice (one car, one instant).
    drive_deepdive_<stamp>.csv    the older form (manual exports and earlier syncs): local wall-clock text, written in Sydney time.

The time zone is never a column or a key. It is a VALUE that only converts a UTC instant to the clock on the wall where the drive began, worked out from the
first position (lib/timezones.resolve_timezone), else Sydney. It is never read from drive_summary.json: that file is a copy made from this data.

Every consumer reads through this module, so none of them cares which form it got:

    rows(path)        -> [dict]   one dict per point, keyed by the CSV headings ("Timestamp", "Latitude", "Speed (km/h)", ...), values as text
    table(path)       -> [list]   the same as csv.reader would give: a heading row, then one list per point (column 0 = local clock text)
    points_epoch(path)-> [(epoch, row)]  the same points with their UTC epoch (CSV rows are converted once, with the Sydney clock of their date)
    zone_of(path)     -> IANA name of the drive's own zone
"""
import csv
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    import timezones as tz
except ImportError:           # imported as lib.drive_points
    from lib import timezones as tz

TS = "Timestamp"
FMT = "%Y-%m-%d %H:%M:%S"

# CSV heading <- Tessie JSON field. The order is the CSV's own order (Tessie's export), so a table built from JSON reads like the old file.
FIELDS = [
    (TS, "timestamp"), ("State", "state"), ("Charging State", "charging_state"), ("Shift State", "shift_state"),
    ("Battery Level (%)", "battery_level"), ("Usable Battery Level (%)", "usable_battery_level"),
    ("Battery Range (km)", "battery_range"), ("Est. Battery Range (km)", "est_battery_range"), ("Ideal Battery Range (km)", "ideal_battery_range"),
    ("Latitude", "latitude"), ("Longitude", "longitude"), ("Elevation (m)", "elevation"), ("Speed (km/h)", "speed"), ("Power (kW)", "power"),
    ("Odometer (km)", "odometer"), ("Charge Rate (km/h)", "charge_rate"), ("Current (A)", "charger_actual_current"),
    ("Charger Power (kW)", "charger_power"), ("Charger Voltage (V)", "charger_voltage"), ("Charger Phases", "charger_phases"),
    ("Energy Added (kWh)", "charge_energy_added"), ("Rated Range Added (km)", "charge_miles_added_rated"),
    ("Ideal Range Added (km)", "charge_miles_added_ideal"), ("Climate", "is_climate_on"), ("Battery Heater", "battery_heater_on"),
    ("Inside Temp (°C)", "inside_temp"), ("Outside Temp (°C)", "outside_temp"), ("Locked", "locked"), ("Sentry Mode", "sentry_mode"),
    ("Lifetime Energy Used (kWh)", "lifetime_energy_used"), ("Energy Remaining (kWh)", "energy_remaining"),
    ("Pack Current (A)", "pack_current"), ("Pack Voltage (V)", "pack_voltage"),
    ("Min Battery Module Temp (°C)", "module_temp_min"), ("Max Battery Module Temp (°C)", "module_temp_max"),
]
HEADINGS = [h for h, _ in FIELDS]

_CACHE = {}          # path -> (mtime, size, parsed); the app reads the same file many times per page


def is_json(path):
    return str(path).lower().endswith(".json")


def _text(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def _stat(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size, st.st_ino, st.st_ctime_ns)       # ctime/inode too: the migration gives a new JSON its CSV's date
    except OSError:
        return None


_PLURAL = {}                                  # condensed key -> the single point's field name
for _h, _k in FIELDS:
    _PLURAL[_k] = _k
    _PLURAL[_k + "s"] = _k                    # Tessie pluralises by adding "s" ("charger_phases" -> "charger_phasess")


def _uncondense(res):
    """Tessie's default (condense=true) answer is one object of parallel arrays: {"timestamps": [...], "latitudes": [...], ...}.
    -> the same points as a list of dicts with the single names ("timestamp", "latitude"), the shape condense=false gives."""
    ts = res.get("timestamps")
    if not isinstance(ts, list):
        return None
    cols = {}
    for key, arr in res.items():
        k = _PLURAL.get(key)
        if k and isinstance(arr, list) and k not in cols:
            cols[k] = arr
    out = []
    for i, t in enumerate(ts):
        p = {k: (a[i] if i < len(a) else None) for k, a in cols.items()}
        p["timestamp"] = t
        out.append(p)
    return out


def parse_json_bytes(body):
    """Tessie's /states answer -> its point list (each a dict), duplicates by UTC second dropped (the first wins), sorted by time.
    [] when the body is not a points answer."""
    try:
        d = json.loads(body.decode("utf-8-sig") if isinstance(body, (bytes, bytearray)) else body)
    except (ValueError, UnicodeDecodeError):
        return []
    res = d.get("results") if isinstance(d, dict) else d
    if isinstance(res, dict):
        res = _uncondense(res)
    if not isinstance(res, list):
        return []
    seen, out = set(), []
    for p in res:
        if not isinstance(p, dict):
            continue
        t = p.get("timestamp")
        if not isinstance(t, (int, float)) or t in seen:
            continue
        seen.add(t)
        out.append(p)
    out.sort(key=lambda p: p["timestamp"])
    return out


def _load_json(path):
    with open(path, "rb") as fh:
        return parse_json_bytes(fh.read())


def zone_of(path, first_lat=None, first_lon=None):
    """The drive's own zone (a value, never stored beside the data): worked out from where the drive began, else the default. It comes from the
    source data alone - drive_summary.json is a copy made from that data and is never asked."""
    if first_lat is None or first_lon is None:
        try:
            if is_json(path):
                for p in _load_json(path)[:1]:
                    first_lat, first_lon = p.get("latitude"), p.get("longitude")
            else:
                head, rs, _ = _get(path)
                if rs and "Latitude" in head and "Longitude" in head:
                    first_lat, first_lon = float(rs[0][head.index("Latitude")] or "nan"), float(rs[0][head.index("Longitude")] or "nan")
                    if first_lat != first_lat or first_lon != first_lon:
                        first_lat = first_lon = None
        except (OSError, ValueError):
            pass
    if first_lat is not None and first_lon is not None:
        return tz.resolve_timezone(first_lat, first_lon)
    return tz.DEFAULT_TIMEZONE


def text_zone(path):
    """The zone the Timestamp TEXT of this file is written in: the drive's own for JSON (made here from UTC), Sydney for an older CSV (requested in Sydney time)."""
    return zone_of(path) if is_json(path) else tz.DEFAULT_TIMEZONE


def local_text(epoch, zone):
    """The wall clock text of a UTC instant in `zone`."""
    return datetime.fromtimestamp(epoch, ZoneInfo(zone)).strftime(FMT)


def _parse_csv(path):
    with open(path, "r", encoding="utf-8-sig", errors="ignore", newline="") as fh:
        rd = csv.reader(fh)
        head = next(rd, None)
        if not head:
            return [], []
        return head, [r for r in rd if r]


def _norm_head(h):
    """'Timestamp (AEDT)' -> 'Timestamp': a zone in a heading is a label the exporter chose, never data."""
    return TS if h.startswith(TS) else h


def _build(path):
    """-> (headings, rows as lists, epochs). One parse per file version."""
    if is_json(path):
        pts = _load_json(path)
        if not pts:
            return list(HEADINGS), [], []
        z = zone_of(path, pts[0].get("latitude"), pts[0].get("longitude"))
        zi = ZoneInfo(z)
        rows, eps = [], []
        for p in pts:
            e = p["timestamp"]
            r = [datetime.fromtimestamp(e, zi).strftime(FMT)] + [_text(p.get(k)) for _, k in FIELDS[1:]]
            rows.append(r)
            eps.append(float(e))
        return list(HEADINGS), rows, eps
    head, raw = _parse_csv(path)
    head = [_norm_head(h) for h in head]
    rows, eps = [], []
    for r in raw:
        try:
            x = r[0].strip()
            e = tz.epoch_for_naive_local(datetime(int(x[0:4]), int(x[5:7]), int(x[8:10]), int(x[11:13]), int(x[14:16]), int(x[17:19])))     # strptime is 20x slower
        except (ValueError, IndexError):
            continue
        rows.append(r)
        eps.append(e)
    return head, rows, eps


def _get(path):
    k = _stat(path)
    if k is None:
        raise OSError("no such file: %s" % path)
    hit = _CACHE.get(path)
    if hit and hit[0] == k:
        return hit[1]
    val = _build(path)
    if len(_CACHE) > 8:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[path] = (k, val)
    return val


def table(path):
    """A heading row, then one list per point - what csv.reader gave for the old file."""
    head, rows, _ = _get(path)
    return [list(head)] + [list(r) for r in rows]


def rows(path):
    """One dict per point, keyed by the (zone-free) CSV headings."""
    head, rs, _ = _get(path)
    return [dict(zip(head, r)) for r in rs]


def points_epoch(path):
    """[(UTC epoch, row dict)] by time."""
    head, rs, eps = _get(path)
    return [(e, dict(zip(head, r))) for e, r in zip(eps, rs)]


def epochs(path):
    return list(_get(path)[2])


def count(path):
    return len(_get(path)[1])
