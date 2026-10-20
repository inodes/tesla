"""Small shared helpers for the drives code: text and number formatting, home-place tests, distances and date parsing. Moved unchanged out of analyse_drives.py."""

from typing import Optional
from datetime import datetime
import math
import os
from datetime import timedelta
import lib.timezones as tztools

EMOJI_MAP = {
    "Recent": "🔄 Recent",
    "Saved": "💾 Saved",
    "Sentry": "🔴 Sentry"
}




# TODO-009 (2026-09-26): normalize_row_timezone() moved to the shared
# lib/timezones.py (as tztools.normalize_row_timezone) so
# tessie_charging_analyzer.py's own consolidate_charges_master() can
# reuse the exact same logic instead of a drifting copy - see that
# module's own docstring for the full explanation. Nothing below this
# comment changes: every call site in this file still says
# `normalize_row_timezone(...)`, now resolved via the module-level
# alias just below.
normalize_row_timezone = tztools.normalize_row_timezone


def format_duration_short(mins):
    if not mins or mins == 0:
        return "-"
    if mins < 60:
        return f"{mins}m"
    h, m = divmod(mins, 60)
    if m == 0:
        return f"{h}h"
    return f"{h}h {m}m"


def format_drive_efficiency(t):
    """kWh/100km average efficiency for a whole trip (drives-table column).
    Prefers Tessie's own 'Average Energy Used (Wh/km)' summary column (divide by 10 for kWh/100km),
    falling back to Total Energy Used (kWh) / Distance (km) * 100.
    Returns '-' when distance is zero/near-zero (<0.1 km) or when efficiency is out of physical bounds (e.g. >100 kWh/100km or <0)."""
    dist_km = t.get("dist_km")
    try:
        if dist_km is not None and float(dist_km) < 0.1:
            return "-"
    except (TypeError, ValueError):
        pass
    wh_km = t.get("wh_km")
    try:
        if wh_km not in (None, "", "-"):
            val = float(wh_km) / 10.0
            if 0.0 <= val <= 100.0:
                return f"{val:.1f} kWh/100km"
            return "-"
    except (TypeError, ValueError):
        pass
    kwh_used = t.get("kwh_used")
    try:
        if kwh_used not in (None, "", "-") and dist_km and float(dist_km) >= 0.1:
            val = (float(kwh_used) / float(dist_km)) * 100.0
            if 0.0 <= val <= 100.0:
                return f"{val:.1f} kWh/100km"
            return "-"
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return "-"


def clean_event_reason(reason):
    if not reason:
        return "event"
    r = reason.strip()
    prefixes = [
        "sentry_aware_",
        "user_interaction_dashcam_launcher_",
        "user_interaction_dashcam_",
        "user_interaction_",
        "vehicle_"
    ]
    for p in prefixes:
        if r.startswith(p):
            r = r[len(p):]
    return r.replace("_", " ")


def format_footage_tag(cats):
    if not cats:
        return "No local footage"
    icons = {
        "Saved": "📹 Saved",
        "Sentry": "🛡️ Sentry",
        "Recent": "🕒 Recent",
        "Other": "📁 Clips"
    }
    order = ["Saved", "Sentry", "Recent", "Other"]
    tags = [icons.get(c, c) for c in order if c in cats]
    return ", ".join(tags) if tags else "No local footage"


def is_home_place(place_str, places_dict=None):
    if not place_str:
        return False
    p_lower = str(place_str).lower().replace("🏠", "").replace("📍", "").strip()
    if places_dict and isinstance(places_dict, dict):
 # BUG-102 follow-up (2026-09-21): this used to key off `type == "home" OR
 # name == "home"` - but nothing ever actually set a "type" field on any
 # place, so in practice this only ever matched by literal name, silently.
 # That meant Home-detection would break the moment the place was ever
 # renamed (no other signal ties it to "home"), and offered no protection
 # beyond "the dict key happens to be the string home" - places_manager.py
 # now sets an explicit `"type": "home"` field on the real Home entry
 # (the one authoritative marker of "this is the user's home", independent
 # of what it's named), so this now keys off *only* that field.
        for p_name, p_info in places_dict.items():
            if isinstance(p_info, dict) and p_info.get("type") == "home":
                meta_short = str((p_info.get("tesla_metadata") or {}).get("short_name", "")).lower()
                loc_name = str(p_info.get("location_name", "")).lower()
                nick_name = str(p_info.get("nickname", "")).lower()
                if p_lower in (str(p_name).lower(), meta_short, loc_name, nick_name) and p_lower:
                    return True
 # 2026-09-28: found via a real cross-check on charges_master.csv's
 # raw "Saved Location" field (Tessie's own, never run through this
 # repo's resolve_place() pipeline) - the Home place's own `keywords`
 # list (e.g. "1108 Victoria" for an address Tessie itself used to
 # label this charger before the user renamed it to "Home" in the
 # Tessie app) was never consulted here, only exact name/short_name/
 # location_name/nickname matches - so any RAW, unresolved string
 # still carrying an old address label silently failed this check,
 # even though resolve_place() elsewhere in this same file already
 # keyword-matches the identical way (kw.strip().lower() in
 # s_clean.lower()) for exactly this reason. Mirrors that same
 # substring semantics here so a second, less complete copy of "is
 # this home" logic doesn't quietly diverge from the first.
                kws = p_info.get("keywords") or []
                for kw in kws:
                    if kw and isinstance(kw, str) and kw.strip() and kw.strip().lower() in p_lower:
                        return True
 # BUG-102 (2026-09-21): require the *entire* cleaned string to reduce to
 # "home", not merely contain it as one token. Real place strings reaching
 # here (Tessie's own reverse-geocoded/manually-entered "Saved Location"
 # field, or this module's own resolve_place() address-fallback branch) can
 # be arbitrary business names or street addresses that happen to contain
 # the word "home" - e.g. "Home Depot", "At Home Furniture", "Home Plate
 # Grill" - which \bhome\b matched too. That was low-stakes while every
 # consumer was an interactive, manually-confirmed flow; it is materially
 # higher-stakes now that REQ-084 wires this into sync_dashcam.sh's
 # unattended --yes purge path, where a false-positive match here stages
 # real footage from an unrelated business stop for permanent deletion.
 # A places_dict registry match (exact, above) is unaffected by this change.
    return p_lower == "home"


def is_home_event(ev, analyzer=None):
    if not ev:
        return False
    loc = ev.get("location", "")
    act = ev.get("activity", "")
    places_dict = getattr(analyzer, "places", None) if analyzer else None
    return is_home_place(loc, places_dict=places_dict) or is_home_place(act, places_dict=places_dict)


def clean_destination_display(p):
    if not p:
        return ""
    p_clean = p.replace("🏠", "").replace("📍", "").strip()
    p_lower = p_clean.lower()
    if p_lower in ("home",):
        return ""
    if p_lower in ("home",):
        return ""
    if "echo point" in p_lower:
        return "Echo Point"
    if "scenic world" in p_lower:
        return "Scenic World"
    if "railway parade" in p_lower:
        return "Leura (Railway Pde)"
    if "cliff drive" in p_lower:
        return "Katoomba (Cliff Dr)"
    if "hancott" in p_lower or "goulding" in p_lower:
        return "Preschool"
    if "," in p_clean:
        return p_clean.split(",")[0].strip()
    return p_clean


def extract_notable_destinations(trips, max_places=3):
    seen = set()
    places = []
    for t in trips:
        for p in [t.get("end_place"), t.get("start_place")]:
            if not p:
                continue
            cp = clean_destination_display(p)
            if cp and cp not in seen:
                seen.add(cp)
                places.append(cp)
    if not places:
        return "Local (West Ryde)"
    return ", ".join(places[:max_places])


def shorten_display_path(p, max_len=40):
    if not p:
        return ""
    p_str = str(p)
    home = os.path.expanduser("~")
    if p_str.startswith(home):
        p_str = "~" + p_str[len(home):]
    p_str = p_str.replace("~/Library/Mobile Documents/com~apple~CloudDocs/", "iCloud/")
    p_str = p_str.replace("~/Library/Mobile Documents/com~apple~CloudDocs", "iCloud")
    if max_len and len(p_str) > max_len:
        p_str = "…" + p_str[-(max_len - 1):]
    return p_str


def haversine_distance_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0)**2
    return R * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def parse_relative_date(date_str):
    if not date_str:
        return None
    d_clean = date_str.strip().lower()
    now = datetime.now()
    
    if d_clean == "today":
        return datetime(now.year, now.month, now.day)
    elif d_clean == "yesterday":
        y = now - timedelta(days=1)
        return datetime(y.year, y.month, y.day)
    
    days_of_week = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if d_clean in days_of_week:
        target_weekday = days_of_week.index(d_clean)
        curr_weekday = now.weekday()
        days_ago = (curr_weekday - target_weekday) % 7
        if days_ago == 0:
            days_ago = 7
        target_date = now - timedelta(days=days_ago)
        return datetime(target_date.year, target_date.month, target_date.day)

    for fmt in ["%Y%m%d", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%d %H:%M"]:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            pass
    return None


def compute_adaptive_rolling_window(distance_km: Optional[float]) -> float:
    """
    Computes an optimal adaptive rolling distance window (km) based on total drive distance.
    Formula: Window (W) = Total Distance * 0.05 (Option 1: Adaptive Window Formula)

    Bounds:
    - Minimum floor of 0.35 km: Prevents 0.01 kWh battery quantization noise artifacts on short trips.
    - Maximum ceiling of 5.0 km: Keeps long highway road trips responsive to major terrain trends.

    Examples:
    - 6 km trip:  max(0.35, 6 * 0.05)   = 0.35 km window (high-res city traffic detail)
    - 10 km trip: max(0.35, 10 * 0.05)  = 0.50 km window (traffic lights & local accelerations)
    - 20 km trip: 20 * 0.05             = 1.00 km window (mixed commute)
    - 50 km trip: 50 * 0.05             = 2.50 km window (suburban & freeway)
    - 100+ km:    min(5.0, 100 * 0.05)  = 5.00 km window (smooth highway trends)
    """
    if not distance_km or distance_km <= 0:
        return 0.8
    w = distance_km * 0.05
    return max(0.35, min(5.0, round(w, 2)))
