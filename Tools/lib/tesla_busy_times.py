"""
tesla_busy_times.py - Tesla's "Busy Times" chart for each Supercharger
(REQ-174, 2026-09-30).

Tesla's Find Us page draws a 24-bar "Congestion Level" chart under
"Pricing for Tesla". The numbers behind it are in the page's own
__NEXT_DATA__ (which the Tesla scraper already captures):

    props.pageProps.formattedData.chargerDetails.availabilityProfile = {
        "createdTimestamp": "2026-09-29T00:00:00Z",
        "utcOffset": "36000s",
        "availabilityProfile": {"sunday": {"congestionValue": [24 x 0..1]}, ...}
    }

Each weekday array is indexed by UTC hour of that UTC day, not local hour.
Checked against Gosford's own chart on 2026-09-30: the page's Wednesday
chart (local 4am..3am) is Tuesday[18..23] followed by Wednesday[0..17] at
utcOffset +10h. local_hourly() does that mapping.

The profile changes every day, so it lives in its own file
(<data>/chargers/tesla_busy_times.json, keyed by Find Us id) - never in the
versioned Supercharger registry, where it would look like a tariff change
on every scrape.
"""
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DAYS_SUN_FIRST = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
DAYS_MON_FIRST = DAYS_SUN_FIRST[1:] + DAYS_SUN_FIRST[:1]   # Python weekday() order
BUSY_FILE_NAME = "tesla_busy_times.json"
SPARK = "▁▂▃▄▅▆▇█"


def busy_times_path() -> str:
    try:
        from lib.tessie_api_common import resolve_tessie_data_home
    except ImportError:
        from tessie_api_common import resolve_tessie_data_home
    return os.path.join(resolve_tessie_data_home(), "chargers", BUSY_FILE_NAME)


def extract_busy_profile(next_data_payload: Optional[dict]) -> Optional[Dict[str, Any]]:
    """The availability profile from a Find Us page's __NEXT_DATA__, or None."""
    try:
        ap = next_data_payload["props"]["pageProps"]["formattedData"]["chargerDetails"]["availabilityProfile"]
        days_raw = ap["availabilityProfile"]
    except (KeyError, TypeError):
        return None
    days = {}
    for d in DAYS_SUN_FIRST:
        vals = ((days_raw.get(d) or {}).get("congestionValue")) or []
        if len(vals) != 24:
            return None
        days[d] = [round(float(v), 3) for v in vals]
    off = str(ap.get("utcOffset") or "0s").rstrip("s")
    try:
        off_s = int(float(off))
    except ValueError:
        off_s = 0
    return {"utc_offset_s": off_s, "profile_date": ap.get("createdTimestamp"), "days": days}


def load_busy_times(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or busy_times_path()
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_busy_profile(findus_id: str, station_name: str, profile: Dict[str, Any], path: Optional[str] = None) -> bool:
    """Store (overwrite) one station's profile. Atomic; an unreadable existing
    file is left alone rather than replaced with just this station."""
    if not findus_id or not profile:
        return False
    path = path or busy_times_path()
    try:
        data = load_busy_times(path)
    except Exception:
        return False
    data[str(findus_id)] = {"station_name": station_name, **profile,
                            "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp-write"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    return True


_CACHE: Optional[Dict[str, Any]] = None


def station_busy_profile(st) -> Optional[Dict[str, Any]]:
    """Cached profile for a station record (matched on its Find Us id), or None."""
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = load_busy_times()
        except Exception:
            _CACHE = {}
    ids = [getattr(st, "source_id", None), (getattr(st, "metadata", None) or {}).get("findus_id")]
    ids += list((getattr(st, "metadata", None) or {}).get("secondary_source_ids") or [])
    for i in ids:
        if i and str(i) in _CACHE:
            return _CACHE[str(i)]
    return None


def local_hourly(profile: Dict[str, Any], weekday=None) -> List[float]:
    """24 congestion values for local hours 0..23 of a local weekday
    (0=Mon..6=Sun, Python's weekday()), the average of a list of weekdays,
    or of all 7 days when weekday is None."""
    days = profile["days"]
    off_h = int(round(profile.get("utc_offset_s", 0) / 3600))
    def one(wd: int) -> List[float]:
        out = []
        for h in range(24):
            u = h - off_h
            d = wd
            while u < 0:
                u += 24; d -= 1
            while u >= 24:
                u -= 24; d += 1
            out.append(days[DAYS_MON_FIRST[d % 7]][u])
        return out
    if isinstance(weekday, int):
        return one(weekday)
    wds = list(weekday) if weekday is not None else list(range(7))
    per = [one(w) for w in wds]
    return [sum(p[h] for p in per) / len(per) for h in range(24)]


WEEKDAYS = [0, 1, 2, 3, 4]
WEEKENDS = [5, 6]


def _hour_label(h: int) -> str:
    h %= 24
    return "12am" if h == 0 else ("12pm" if h == 12 else (f"{h}am" if h < 12 else f"{h - 12}pm"))


# Tesla's own gauge on the Find Us chart has two wordings (page
# translations): "Most Chargers Available" and "Some Chargers Available".
# The chart's y-axis is absolute (0 = empty, 1 = every stall in use) - on
# Gosford's page a 0.41 hour draws ~40% of the chart height and is labelled
# "Most Chargers Available". Tesla's exact cut-over between the two wordings
# isn't in the page data; 0.5 (half the stalls in use) is our assumption.
SOME_AVAILABLE_FROM = 0.5
LEVEL_TEXT = {"most": "Most chargers available", "some": "Some chargers available"}


def level(v: float) -> str:
    return "some" if v >= SOME_AVAILABLE_FROM else "most"


def sparkline(vals: List[float], start_hour: int = 4) -> str:
    """24 block characters from start_hour (Tesla's chart starts at 4am) on
    Tesla's absolute 0..1 scale - a near-empty line means plenty free."""
    seq = vals[start_hour:] + vals[:start_hour]
    return "".join(SPARK[min(len(SPARK) - 1, max(0, int(round(v * (len(SPARK) - 1)))))] for v in seq)


def busy_summary(profile: Dict[str, Any], weekday=None) -> Dict[str, Any]:
    """How busy, in Tesla's own gauge wording:
    'Most chargers available all day (busiest 2pm, 43% in use)' or
    'Some chargers available 11am–4pm, most at other times (busiest 1pm, 56% in use)'."""
    vals = local_hourly(profile, weekday)
    top = max(vals)
    peak_h = vals.index(top)
    some_hours = [h for h in range(24) if level(vals[h]) == "some"]
    peak = f"busiest {_hour_label(peak_h)}, {round(top * 100)}% in use"
    if not some_hours:
        text, lvl, win = f"{LEVEL_TEXT['most']} all day ({peak})", "most", None
    else:
        # contiguous run of "some" hours around the peak (wraps midnight)
        s0 = s1 = peak_h
        while (s0 - 1) % 24 in some_hours and (s0 - 1) % 24 != peak_h:
            s0 = (s0 - 1) % 24
        while (s1 + 1) % 24 in some_hours and (s1 + 1) % 24 != s0:
            s1 = (s1 + 1) % 24
        win = (s0, (s1 + 1) % 24)
        lvl = "some"
        text = (f"{LEVEL_TEXT['some']} {_hour_label(win[0])}–{_hour_label(win[1])}, "
                f"most at other times ({peak})")
    return {"level": lvl, "peak_hour": peak_h, "peak_pct": round(top * 100), "some_window": win,
            "spark": sparkline(vals), "text": text, "values": vals}
