#!/usr/bin/env python3
"""
school_days.py - Is a date a school day in a state? For the school zone speed signs ("8-9:30 / 2:30-4 SCHOOL DAYS").

A school day is a weekday inside a term of that state's calendar (resources/school_terms.json) that is not a public holiday there. Only calendars
that are in the file are used: an unknown state or year gives None (not True and not False), so the page shows the plain speed sign and never guesses.
Public holidays are known for NSW only (agl_common); a state with a calendar but no holiday rule counts only the weekday and the term.
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_FILE = os.path.join(os.path.dirname(os.path.dirname(HERE)), "resources", "school_terms.json")
_cache = {}


def terms(path=None):
    """The whole calendar file as a dict ({} when it cannot be read)."""
    path = path or _FILE
    if path not in _cache:
        try:
            with open(path, "r", encoding="utf-8") as f:
                _cache[path] = json.load(f)
        except (OSError, ValueError):
            _cache[path] = {}
    return _cache[path]


def _holiday(state, d):
    if state == "NSW":
        import agl_common
        return agl_common.is_nsw_public_holiday(d)
    return False


def school_day(state, d, path=None):
    """True / False for a date (datetime.date or 'YYYY-MM-DD') in a state ('NSW', 'VIC', ...); None when that state or year has no calendar."""
    if isinstance(d, str):
        try:
            d = datetime.date.fromisoformat(d[:10])
        except ValueError:
            return None
    ranges = ((terms(path).get((state or "").upper()) or {}).get("terms") or {}).get(str(d.year))
    if not ranges:
        return None
    if d.weekday() >= 5 or _holiday(state.upper(), d):
        return False
    iso = d.isoformat()
    return any(a <= iso <= b for a, b in ranges)
