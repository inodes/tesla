"""
Shared timezone resolution/localization helpers (TODO-009).

Design (per the user's own scoping):
  - code:    canonical UTC everywhere - a Unix epoch integer, exactly
             what the Tessie API itself already hands us in
             started_at/ended_at. Sorting/uniqueness/comparisons are
             done on this and only this - no naive-datetime ambiguity,
             no DST fall-back repeated-hour bug.
  - csv:     UTC epoch + the IANA timezone name the event actually
             happened in, stored side by side. Enough to re-derive
             correct local wall-clock time and the right abbreviation
             (AEST/AEDT/ACST/ACDT/AWST/...) for that exact date, later,
             without a separate abbreviations table - Python's stdlib
             zoneinfo already produces the right abbreviation per date
             for every Australian zone (verified: AEDT/AEST for Sydney,
             ACDT/ACST for Adelaide including the half-hour offset,
             AEST/ACST/AWST for the no-DST states).
  - display: sort by UTC, but render local time + that row's own
             timezone abbreviation - never one static, half-the-year-
             wrong header label like the old "(AEST)" columns.

resolve_timezone(lat, lon) turns GPS coordinates into an IANA zone
name. Tries `timezonefinder` first if it's installed (accurate
everywhere, including real exceptions like Broken Hill in far-west
NSW actually observing SA time, or Lord Howe Island's own unique
half-hour-DST zone); otherwise falls back to a small Australia
state-boundary heuristic below, which is exactly correct for every
trip this repo's data has actually covered so far (all NSW to date).
The fallback upgrades to full accuracy automatically the moment
timezonefinder is installed - no code change needed.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Australia/Sydney"

_tf_instance = None
_tf_unavailable = False


def _get_timezonefinder():
    global _tf_instance, _tf_unavailable
    if _tf_unavailable:
        return None
    if _tf_instance is None:
        try:
            from timezonefinder import TimezoneFinder
            _tf_instance = TimezoneFinder()
        except Exception:
            _tf_unavailable = True
            return None
    return _tf_instance


def timezonefinder_available():
    """Whether the accurate resolver is active (vs. the heuristic
    fallback) - surfaced so callers/docs can tell the user which mode
    is in effect."""
    return _get_timezonefinder() is not None


def _heuristic_timezone(lat, lon):
    """Approximate Australia state/territory lookup by lat/lon. Good
    enough for real mainland road-trip driving; does NOT know about
    small real-world exceptions (Broken Hill, Lord Howe Island) -
    install timezonefinder for those. Checked in this specific order
    so overlapping bounding boxes resolve to the more specific zone
    first, falling through to the NSW/VIC/ACT/TAS catch-all last."""
    if lon < 129.0:
        return "Australia/Perth"          # WA
    if lon < 138.0 and lat > -26.0:
        return "Australia/Darwin"         # NT
    if lon < 141.0 and lat <= -26.0:
        return "Australia/Adelaide"       # SA
    if lat > -29.0:
        return "Australia/Brisbane"       # QLD
    if lon < 149.5 and lat < -39.2:
        return "Australia/Hobart"         # TAS
    return DEFAULT_TIMEZONE               # NSW / VIC / ACT catch-all


def resolve_timezone(lat, lon):
    """Best-effort IANA zone name for a GPS point. Falls back to
    DEFAULT_TIMEZONE if lat/lon are missing entirely."""
    if lat is None or lon is None:
        return DEFAULT_TIMEZONE
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return DEFAULT_TIMEZONE
    tf = _get_timezonefinder()
    if tf is not None:
        try:
            zone = tf.timezone_at(lat=lat_f, lng=lon_f)
            if zone:
                return zone
        except Exception:
            pass
    return _heuristic_timezone(lat_f, lon_f)


def localize(epoch_utc, iana_zone=None):
    """(local_datetime, abbreviation, offset_str) for a Unix epoch
    second, localized into iana_zone (defaults to DEFAULT_TIMEZONE if
    not given or not a real zone name). offset_str is e.g. "+10:00" /
    "+09:30". Returns (None, None, None) if epoch_utc is missing."""
    if epoch_utc is None or epoch_utc == "":
        return None, None, None
    zone_name = iana_zone or DEFAULT_TIMEZONE
    try:
        tz = ZoneInfo(zone_name)
    except Exception:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    utc_dt = datetime.fromtimestamp(int(float(epoch_utc)), tz=timezone.utc)
    local_dt = utc_dt.astimezone(tz)
    abbr = local_dt.tzname()
    raw_offset = local_dt.strftime("%z")  # e.g. "+1000", "+1030", "-0000"
    if len(raw_offset) == 5:
        offset_str = raw_offset[:3] + ":" + raw_offset[3:]
    else:
        offset_str = raw_offset
    return local_dt, abbr, offset_str


def sydney_offset_seconds(dt_naive):
    """The UTC offset (seconds) Australia/Sydney actually observed at
    this naive datetime - correctly toggling AEST(+10h)/AEDT(+11h) per
    the real daylight-saving calendar for that date. Used only by the
    one-time historical backfill migration, since every row in this
    repo's data to date is confirmed to have actually happened in
    Sydney's timezone (user-confirmed: the car has never left it)."""
    tz = ZoneInfo(DEFAULT_TIMEZONE)
    aware = dt_naive.replace(tzinfo=tz)
    return aware.utcoffset().total_seconds()


def epoch_to_utc_iso(epoch_utc):
    """Epoch seconds -> 'YYYY-MM-DDTHH:MM:SSZ', for human-readable
    debugging/logging only - stored CSV columns use the raw epoch
    integer itself (sorts correctly as a plain number, no parsing
    needed), per the epoch-is-just-as-good-as-UTC design above."""
    if epoch_utc is None or epoch_utc == "":
        return ""
    dt = datetime.fromtimestamp(int(float(epoch_utc)), tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
