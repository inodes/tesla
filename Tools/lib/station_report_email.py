"""
station_report_email.py - email-ready version of charger_find.py's station
list (REQ-168, 2026-09-30). User: "I would love to be able to send this in an
email appropriate format" - chose "Copy to clipboard" and "Trip summary +
full table".

build_station_report() turns the SAME stations/route_info the terminal table
uses into (html, text): a trip summary plus the station table, as plain,
inline-styled HTML (no external CSS, no scripts - survives Mail, Gmail and
Outlook) and a plain-text fallback. copy_report_to_clipboard() puts both on
the macOS clipboard (HTML + plain-text flavours) so a paste into any email
keeps the formatting.

Row values come from the same station methods the terminal table uses
(get_effective_pricing, tier_badge, access_label, stop_score_data) and the
shared station_arrival_soc() below, so the email and the terminal agree.
"""
import html as _html
import os
import re
import platform
import subprocess
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


def station_arrival_soc(st, route_info: Optional[Dict[str, Any]]) -> Optional[float]:
    """Predicted arrival battery % at this station, leg-aware when
    --charger-stops re-planned the trip. None when it can't be estimated.
    Shared by the terminal table and the email report."""
    from lib.abrp_common import DEFAULT_CONSUMPTION_WH_KM, DEFAULT_USABLE_KWH, estimate_arrival_soc
    prog_val = getattr(st, "route_progress_km", st.distance_km)
    ri = route_info or {}
    dep_soc = ri.get("departure_soc", 90)
    c_wh_km = ri.get("consumption_wh_km", DEFAULT_CONSUMPTION_WH_KM)
    u_kwh = ri.get("usable_kwh", DEFAULT_USABLE_KWH)
    leg_dep = getattr(st, "leg_departure_soc", None)
    leg_dist = getattr(st, "leg_distance_km", None)
    eff_dep = leg_dep if leg_dep is not None else dep_soc
    eff_dist = leg_dist if leg_dist is not None else prog_val
    if eff_dist is None:
        return None
    try:
        arr, _ = estimate_arrival_soc(eff_dep, eff_dist, getattr(st, "detour_km", None) or 0.0, c_wh_km, u_kwh)
        return arr
    except Exception:
        return None


FACILITIES_ICON = "🏪"


def station_facilities_info(st) -> Optional[Dict[str, Any]]:
    """The station's cached OSM facilities record (score + POIs), or None
    when nothing fresh is cached. Looked up once per station."""
    if hasattr(st, "_facilities_info_cache"):
        return st._facilities_info_cache
    info = None
    try:
        from lib.station_facilities_common import get_facilities_registry, resolve_station_physical_coords
        reg = get_facilities_registry()
        if reg:
            lat, lon, _ = resolve_station_physical_coords(st)
            info = reg.get_station_facilities(station_key=getattr(st, "key", None),
                                              station_name=getattr(st, "name", None), lat=lat, lon=lon) or None
    except Exception:
        info = None
    try:
        st._facilities_info_cache = info
    except Exception:
        pass
    return info


def station_facilities_score(st) -> Optional[float]:
    """The station's real cached OSM facilities score (0-10), or None when
    nothing (fresh) is cached - never the 5.0 "neutral" stand-in the stop
    score uses, so an unknown shows as unknown."""
    info = station_facilities_info(st)
    try:
        return float(info["score"]) if info and info.get("score") is not None else None
    except (TypeError, ValueError):
        return None


# --- Short per-station "inspect" card (REQ-170) ---------------------------
# Big-name places worth calling out on a charging stop. Matched on whole
# words in the POI name, so "Coles" matches "Coles Central" but not "Colesberg".
_MAJOR_SUPERMARKETS = ("woolworths", "coles", "aldi", "iga", "harris farm", "foodworks",
                       "ritchies", "costco")
_DEPT_STORES = ("kmart", "target", "big w", "myer", "david jones")


def _is_charging_poi(p: Dict[str, Any]) -> bool:
    name = (p.get("name") or "").lower()
    return ((p.get("category") or "").lower().startswith("charging")
            or "supercharger" in name or name.startswith("tesla super"))


def _name_has(name: str, words) -> bool:
    n = (name or "").lower()
    return any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", n) for w in words)


def _poi_str(p: Dict[str, Any], name_len: int = 26) -> str:
    name = (p.get("name") or p.get("category") or "?").strip()
    if len(name) > name_len:
        name = name[:name_len - 1].rstrip() + "…"
    return f"{name} {int(round(p.get('dist_m', 0)))} m"


# --inspect's Category Summary order (format_facilities_summary_card); groups
# it doesn't list come after these.
_INSPECT_GROUP_ORDER = {"Supermarkets & Groceries": 1, "Dining & Fast Food": 2, "Cafes & Coffee": 3,
                        "Amenities & Services": 4, "Retail & Shopping": 5, "Leisure & Entertainment": 6,
                        "Lodging & Hotels": 7, "Healthcare": 8, "Automotive & Parking": 9}


def facilities_brief(info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Short summary of a cached facilities record for the email card:
    best      - shopping centres, big-name supermarkets and department stores
                [(kind, poi)], closest first within each kind, at most 4
    groups    - [(emoji, group, [pois], more)] per --inspect category, same order."""
    out = {"best": [], "groups": []}
    if not info:
        return out
    pois = sorted((p for p in info.get("pois") or [] if not _is_charging_poi(p)),
                  key=lambda p: p.get("dist_m", 9999.0))
    cat = lambda p: (p.get("category") or "").lower()
    # Same anchor rules as the facilities score (REQ-176), so the stars and the score agree.
    from lib.station_facilities_common import is_shopping_centre, is_major_supermarket
    malls = [p for p in pois if is_shopping_centre(p)]
    supers = [p for p in pois if is_major_supermarket(p)]
    depts = [p for p in pois if cat(p).startswith("department store") and _name_has(p.get("name"), _DEPT_STORES)]
    best = [("Shopping centre", p) for p in malls[:2]] + [("Supermarket", p) for p in supers[:2]] \
        + [("Dept store", p) for p in depts[:2]]
    out["best"] = best[:4]
    used = {id(p) for _, p in out["best"]}
    # Same groups, order and content as --inspect's "Category Summary", minus
    # what's already starred and minus "Other Facilities" (unnamed buildings,
    # houses). Groceries and food list 3 names, everything else 2.
    groups: Dict[str, List[Dict[str, Any]]] = {}
    emojis: Dict[str, str] = {}
    for p in pois:
        g = p.get("group") or "Other Facilities"
        if g == "Other Facilities" or id(p) in used:
            continue
        groups.setdefault(g, []).append(p)
        emojis.setdefault(g, p.get("emoji") or "📍")
    for g in sorted(groups, key=lambda g: _INSPECT_GROUP_ORDER.get(g, 50)):
        n = 3 if g in ("Supermarkets & Groceries", "Dining & Fast Food") else 2
        items = groups[g]
        out["groups"].append((emojis[g], g, items[:n], max(0, len(items) - n)))
    return out


def _is_tesla(st) -> bool:
    return "tesla" in (st.source or "").lower() or "tesla" in (st.network or "").lower()


_STATE_NAMES = {"NSW": "New South Wales", "VIC": "Victoria", "QLD": "Queensland", "SA": "South Australia",
                "WA": "Western Australia", "TAS": "Tasmania", "ACT": "Australian Capital Territory",
                "NT": "Northern Territory"}


def station_address(st) -> str:
    """Street address plus suburb/state/postcode where the address lacks them
    (same rule as charger_find.format_station_address)."""
    addr = (st.address or "").strip()
    if not addr:
        return ", ".join(p for p in (st.suburb, st.state, st.postcode) if p)
    parts, low = [addr], addr.lower()
    if st.suburb and st.suburb.lower() not in low:
        parts.append(st.suburb)
    state = (st.state or "").strip()
    full = _STATE_NAMES.get(state.upper(), "")
    if full and full.lower() in low:
        # "..., New South Wales" -> "..., NSW" rather than "..., New South Wales, NSW"
        i = low.rfind(full.lower())
        parts[0] = addr[:i] + state.upper() + addr[i + len(full):]
    elif state and state.lower() not in low:
        parts.append(state)
    if st.postcode and st.postcode not in addr:
        parts.append(st.postcode)
    return ", ".join(parts)


def _sched_items(sched) -> List[Tuple[str, str, str, float]]:
    """[(label, start, end, rate)] with a period that runs past midnight joined
    up again (Off-Peak 23:00–24:00 + Off-Peak 00:00–08:00 -> 23:00–08:00)."""
    items = []
    for it in sched or []:
        rate = it.get("rate") if it.get("rate") is not None else it.get("rate_per_kwh")
        if rate is None:
            continue
        items.append((it.get("name") or it.get("label") or "Rate", it.get("start") or it.get("start_time") or "00:00",
                      it.get("end") or it.get("end_time") or "24:00", float(rate)))
    if len(items) > 1:
        first, last = items[0], items[-1]
        if first[1] == "00:00" and last[2] == "24:00" and first[0] == last[0] and abs(first[3] - last[3]) < 0.005:
            items = [(last[0], last[1], first[2], last[3])] + items[1:-1]
    return items


def _money(r: float) -> str:
    return "free" if r == 0 else f"${r:.2f}"


def sched_range(sched) -> str:
    """"$0.44" flat or "$0.34–0.48" time-of-use; "" when there's no schedule."""
    items = _sched_items(sched)
    if not items:
        return ""
    lo, hi = min(i[3] for i in items), max(i[3] for i in items)
    return _money(lo) if hi - lo < 0.005 else f"{_money(lo)}–{hi:.2f}"


def sched_full(sched) -> str:
    """Every pricing period: "Off-Peak Night 23:00–08:00 $0.34 · Peak Day 08:00–23:00 $0.48",
    or "$0.44/kWh all day"."""
    items = _sched_items(sched)
    if not items:
        return ""
    if len(items) == 1 and items[0][1] == "00:00" and items[0][2] == "24:00":
        return f"{_money(items[0][3])}/kWh all day"
    return " · ".join(f"{lbl} {st_}–{en} {_money(r)}" for lbl, st_, en, r in items) + " /kWh"


def station_card_lines(st, route_info, eval_time, share: bool = False) -> Dict[str, Any]:
    """The facts for one station's short card, as plain strings."""
    meta = st.metadata or {}
    label, model = st.speed_tier_label(), (st.hardware_model or "").strip()
    kind = model if model.lower().startswith(label.lower()) else (f"{label} {model}".strip())
    general = (meta.get("general_location") or "").strip()
    addr = station_address(st)
    where = f"{general} · {addr}" if general and general.lower() not in addr.lower() \
        and general.lower() != (st.suburb or "").lower() else addr

    acc = st.access_label()
    acc_s = {"CCS2 All": "open to all EVs", "Type 2 All": "open to all EVs", "Tesla Only": "Tesla only",
             "Public": "public", "Private": "private", "Customer": "customers only"}.get(acc, acc)
    hw = [f"{st.stalls_total} stalls" if st.stalls_total > 0 else "",
          f"up to {st.formatted_power()}" if st.max_power_kw else "",
          ", ".join(sorted(set(st.plugs))) if st.plugs else "", acc_s,
          meta.get("hours") or meta.get("access_hours") or ""]

    # Every pricing period, not just the one in force when the script runs.
    main = sched_full(st.schedule)
    if not main:
        if st.current_rate_per_kwh is not None and st.current_rate_per_kwh > 0:
            main = f"${st.current_rate_per_kwh:.2f}/kWh"
        else:
            main = st.rate_range or "not listed"
    on_route = bool(route_info) and getattr(st, "route_progress_km", None) is not None
    if not share and (eval_time or not on_route):
        now_s, now_lbl = _rate(st, eval_time)
        if now_s != "-" and len(_sched_items(st.schedule)) > 1:
            main += f" (now {now_s.replace('/kWh', '')})" if not eval_time else f" ({eval_time}: {now_s.replace('/kWh', '')})"
    nt = sched_full(st.non_tesla_schedule)
    price_lines = [f"Tesla: {main}", f"Non-Tesla: {nt}"] if (nt and _is_tesla(st)) else [main]
    if share:
        # For someone driving another EV: only the price they'd pay.
        if acc == "Tesla Only":
            price_lines = ["Tesla vehicles only"]
        elif nt and _is_tesla(st):
            # Non-Tesla EVs get the Tesla rate with a Supercharging membership.
            price_lines = [f"Tesla & members: {main}", f"Non-members: {nt}"]
    fees = []
    if st.congestion_fee_per_min and st.congestion_fee_per_min > 0:
        fees.append(f"congestion ${st.congestion_fee_per_min:.2f}/min")
    elif st.idle_fee_per_min and st.idle_fee_per_min > 0:
        fees.append(f"idle ${st.idle_fee_per_min:.2f}/min")
    if st.parking_fee:
        fees.append(f"parking {st.parking_fee}")

    trip, arr_level = [], ""
    prog = getattr(st, "route_progress_km", None)
    if route_info and prog is not None:
        trip.append(f"{prog:.0f} km along the route")
        det = getattr(st, "detour_km", None)
        if det is not None and det >= 0.5:
            trip.append(f"{det:.1f} km detour")
        arr = None if (share or route_info.get("no_soc")) else station_arrival_soc(st, route_info)
        if arr is not None:
            tgt = route_info.get("target_arrival_soc", 10.0)
            if arr < 0:
                trip.append("needs a stop before"); arr_level = "bad"
            else:
                trip.append(f"arrive with {round(arr)}%")
                arr_level = "good" if arr >= 25 else ("warn" if arr >= tgt else "bad")
    elif st.distance_km is not None:
        trip.append(f"{st.distance_km:.1f} km away")

    busy = None
    try:
        from lib.tesla_busy_times import station_busy_profile, busy_summary, local_hourly
        prof = station_busy_profile(st)
        if prof:
            sm = busy_summary(prof)
            vals = local_hourly(prof)
            busy = {"spark": sm["spark"], "text": sm["text"], "level": sm["level"],
                    "bars": vals[4:] + vals[:4]}
    except Exception:
        busy = None

    info = station_facilities_info(st)
    return {"busy": busy, "kind": kind, "where": where, "warn": (meta.get("operational_status") or "").strip(),
            "tesla_only": share and acc == "Tesla Only",
            "hw": " · ".join(x for x in hw if x), "price_lines": price_lines, "fees": " · ".join(fees)[:1].upper() + " · ".join(fees)[1:],
            "trip": " · ".join(trip), "arr_level": arr_level,
            "fac_score": station_facilities_score(st), "fac_rating": (info or {}).get("rating") or "",
            "fac": facilities_brief(info), "fac_checked": info is not None}


def facilities_level(score: Optional[float]) -> str:
    """'good' / 'warn' / 'low' / 'bad' band for colouring, '' when unknown."""
    if score is None:
        return ""
    return "good" if score >= 8.0 else ("warn" if score >= 6.0 else ("low" if score >= 4.0 else "bad"))


def station_maps_url(st) -> Optional[str]:
    """Google Maps link to the station's GPS position (best-known stall
    coordinates, falling back to the listed position)."""
    try:
        from lib.station_facilities_common import resolve_station_physical_coords
        lat, lon, _ = resolve_station_physical_coords(st)
    except Exception:
        lat, lon = getattr(st, "lat", None), getattr(st, "lon", None)
    if lat is None or lon is None or (not lat and not lon):
        return None
    return f"https://www.google.com/maps/search/{lat},{lon}"


def _dur(mins: float) -> str:
    mins = mins or 0
    return f"{int(mins // 60)}h {int(mins % 60):02d}m" if mins >= 60 else f"{int(mins)}m"


def _rate(st, eval_time) -> Tuple[str, str]:
    rate, lbl, win = st.get_effective_pricing(eval_time)
    if rate is None:
        return "-", "-"
    rate_s = "Free" if rate == 0.0 else f"${rate:.2f}/kWh"
    period = f"{lbl} ({win})" if (win and win not in ("24/7", "00:00–24:00")) else (lbl or "-")
    return rate_s, period


def _summary_rows(title, stations, route_info, eval_time, near_info=None, share: bool = False) -> List[Tuple[str, str]]:
    ri = route_info or {}
    rows = []
    hide_soc = share or bool(ri.get("no_soc"))
    if not ri and near_info and near_info.get("label"):
        rad = near_info.get("radius_km")
        rows.append(("Near", str(near_info["label"]) + (f" (within {rad:g} km)" if rad else "")))
        if near_info.get("sort"):
            rows.append(("Sorted by", str(near_info["sort"])))
    if ri:
        vias = ri.get("via_labels") or []
        via = f" via {', '.join(vias)}" if vias else ""
        rows.append(("Route", f"{ri.get('origin_label', 'Origin')} → {ri.get('destination_label', 'Destination')}{via}"))
        rows.append(("Distance", f"{ri.get('distance_km', 0.0):.0f} km, about {_dur(ri.get('duration_mins', 0.0))} driving"))
        if ri.get("vehicle_name") and not hide_soc:
            rows.append(("Vehicle", str(ri["vehicle_name"])))
        if ri.get("departure_soc") is not None and not hide_soc:
            live = ri.get("live_detail") or {}
            rng = f", ~{live['battery_range_km']:.0f} km range" if live.get("battery_range_km") else ""
            rows.append(("Battery at departure", f"{ri['departure_soc']}% ({ri.get('departure_soc_src', 'config')}{rng})"))
        if not hide_soc:
            rows.append(("Arrive with at least", f"{ri.get('target_arrival_soc', 10):.0f}%"))
            rows.append(("Charge up to", f"{ri.get('max_charge_soc', 85):.0f}%"))
        if ri.get("charger_stops") and not hide_soc:
            rows.append(("Planned charging stops", ", ".join(ri["charger_stops"])))
        rows.append(("Chargers listed", f"within {ri.get('corridor_radius_km', 5.0):.0f} km of the route ({len(stations)} found)"))
    else:
        rows.append(("Chargers listed", str(len(stations))))
    if share:
        pass  # generic: prices are shown as full ranges, not for a time
    elif eval_time:
        rows.append(("Prices shown for", f"{eval_time} local time"))
    elif not ri:
        rows.append(("Prices shown for", datetime.now().strftime("%a %d %b %Y, %H:%M")))
    return rows


def _table(stations, route_info, eval_time, type_icon_fn, share: bool = False) -> Tuple[List[str], List[List[str]], List[Dict[str, str]]]:
    """Headers, plain cell text per row, and per-cell style hints."""
    has_route = route_info is not None and any(getattr(s, "route_progress_km", None) is not None for s in stations)
    hide_soc = share or bool((route_info or {}).get("no_soc"))
    multi_stop = has_route and bool((route_info or {}).get("charger_stops")) and not hide_soc
    has_dist = (not has_route) and any(s.distance_km is not None for s in stations)
    tgt = (route_info or {}).get("target_arrival_soc", 10.0)

    # On a route the time the script runs is irrelevant: show each station's
    # price range instead of "rate now", unless --time was given.
    timeless = share or (has_route and not eval_time)
    headers = ["#", "Station", "Suburb", "Type", "Tier", "Bays", "Access"] + \
        ([RATE_SHARE_HDR if share else "Rate /kWh"] if timeless else ["Rate now", "Pricing period"])
    if has_route:
        headers += ["Along route", "Detour"] + ([] if hide_soc else ["Arrive with"] + (["Leave with"] if multi_stop else [])) \
            + [FACILITIES_ICON, "Stop score"]
    elif has_dist:
        headers += ["Distance", FACILITIES_ICON]
    else:
        headers += [FACILITIES_ICON]

    rows, styles = [], []
    for i, st in enumerate(stations, 1):
        rate_s, period = _rate(st, eval_time)
        acc = st.access_label()
        warn = ((st.metadata or {}).get("operational_status") or "").strip()
        name = st.name + (f"  ⚠ {warn}" if warn else "")
        rng = sched_range(st.schedule) or rate_s
        if share:
            if acc == "Tesla Only":
                rng = "-"
            elif _is_tesla(st) and st.non_tesla_schedule and sched_range(st.non_tesla_schedule):
                rng = f"{rng} / {sched_range(st.non_tesla_schedule)}"
        cells = [str(i), name, st.suburb or st.state or "-", (type_icon_fn(st) or "").strip(), st.tier_badge(),
                 str(st.stalls_total) if st.stalls_total > 0 else "-",
                 "⛔ Tesla only" if (share and acc == "Tesla Only") else acc] + \
            ([rng] if timeless else [rate_s, period])
        style = {"Access": ("bad" if share else "tesla") if acc == "Tesla Only" else ("bad" if acc == "Private" else ""),
                 "_url": station_maps_url(st) or "", "_row_bad": share and acc == "Tesla Only"}
        fac = station_facilities_score(st)
        fac_s = f"{fac:.1f}" if fac is not None else "-"
        style[FACILITIES_ICON] = facilities_level(fac)
        if has_route:
            prog = getattr(st, "route_progress_km", st.distance_km)
            det = getattr(st, "detour_km", None)
            arr = station_arrival_soc(st, route_info)
            if arr is None:
                arr_s, arr_style = "-", ""
            elif arr < 0:
                arr_s, arr_style = "Needs a stop before", "bad"
            else:
                arr_s = f"{round(arr)}%"
                arr_style = "good" if arr >= 25 else ("warn" if arr >= tgt else "bad")
            cells += [f"{prog:.0f} km" if prog is not None else "-",
                      f"{det:.1f} km" if det is not None else "-"] + ([] if hide_soc else [arr_s])
            style["Arrive with"] = arr_style
            if multi_stop:
                dep = getattr(st, "stop_dep_soc", None)
                cells.append(f"{dep:.0f}%" if dep is not None else "")
            cells.append(fac_s)
            sc = getattr(st, "stop_score_data", None) or {}
            score = sc.get("score")
            cells.append(f"{score:.1f}/10" if isinstance(score, (int, float)) else "-")
        elif has_dist:
            cells.append(f"{st.distance_km:.1f} km" if st.distance_km is not None else "-")
            cells.append(fac_s)
        else:
            cells.append(fac_s)
        rows.append(cells)
        styles.append(style)
    return headers, rows, styles


RATE_SHARE_HDR = "Rate /kWh (member / non-member)"
_COLORS = {"good": "#1a7f37", "warn": "#9a6700", "low": "#bc4c00", "bad": "#cf222e", "tesla": "#0969da"}


def _group_lines(brief) -> List[str]:
    """One line per --inspect category, e.g. '🍔 Subway 164 m, Domino's 190 m, KFC 229 m +1'."""
    return [f"{emoji} " + ", ".join(_poi_str(p) for p in items) + (f" +{more}" if more else "")
            for emoji, _g, items, more in brief["groups"]]


def _busy_bars_html(bars: List[float]) -> str:
    """Tesla's Busy Times as a tiny 24-bar chart (4am..3am) built from a
    table - survives Gmail/Outlook, unlike block characters or SVG. Same
    absolute 0..1 scale as Tesla's chart (full height = every stall in use);
    bars green where most chargers are free, amber where only some are."""
    from lib.tesla_busy_times import level
    cells = "".join(
        f'<td style="vertical-align:bottom;padding:0 1px 0 0;height:24px;background:#f6f8fa;">'
        f'<div style="width:5px;height:{max(1, round(v * 24))}px;'
        f'background:{"#bf8700" if level(v) == "some" else "#2da44e"};font-size:0;line-height:0;">&nbsp;</div></td>'
        for v in bars)
    return ('<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;display:inline-table;vertical-align:bottom;">'
            f'<tr>{cells}</tr></table> <span style="font-size:11px;color:#59636e;">4am → 3am</span>')


def _cards_html(stations, route_info, eval_time, share: bool = False) -> List[str]:
    """Short inspect card per station, under the table."""
    esc = _html.escape
    grey = "color:#59636e;"
    h = ['<h3 style="font-size:15px;margin:24px 0 4px 0;">Station details</h3>']
    for i, st in enumerate(stations, 1):
        c = station_card_lines(st, route_info, eval_time, share)
        url = station_maps_url(st)
        name = esc(st.name)
        if url:
            name = f'<a href="{esc(url)}" style="color:#0969da;text-decoration:none;">{name}</a>'
        rows = [f'<div style="font-size:14px;"><b>{i}. {name}</b> <span style="{grey}">{esc(c["kind"])}</span>'
                + (f' <span style="color:#cf222e;font-weight:600;">⚠ {esc(c["warn"])}</span>' if c["warn"] else "")
                + (' <span style="color:#cf222e;font-weight:700;">⛔ Tesla only - other EVs can\'t charge here</span>'
                   if c["tesla_only"] else "")
                + '</div>']
        line = lambda icon, body: rows.append(f'<div><span style="display:inline-block;width:22px;">{icon}</span>{body}</div>')
        if c["where"]:
            line("📍", esc(c["where"]))
        if c["tesla_only"]:
            # Only there so it can be recognised and skipped - keep it short.
            if c["trip"]:
                line("🧭", esc(c["trip"]))
            h.append('<div style="font-size:13px;line-height:1.5;margin:10px 0 0 0;background:#fff1f0;'
                     'padding:8px 8px 6px 8px;border-top:1px solid #e8ebef;">' + "".join(rows) + '</div>')
            continue
        if c["hw"]:
            line("⚡", esc(c["hw"]))
        for n_, pl in enumerate(c["price_lines"] + ([c["fees"]] if c["fees"] else [])):
            is_fee = c["fees"] and n_ == len(c["price_lines"])
            line("💰" if n_ == 0 else "", f'<span style="{grey}">{esc(pl)}</span>' if is_fee else esc(pl))
        if c["trip"]:
            col = _COLORS.get(c["arr_level"], "")
            line("🧭", f'<span style="color:{col};">{esc(c["trip"])}</span>' if col else esc(c["trip"]))
        if c["busy"]:
            b_col = "#9a6700" if c["busy"]["level"] == "some" else "#1a7f37"
            line("🚦", f'<span style="color:{b_col};">{esc(c["busy"]["text"])}</span>')
            line("", _busy_bars_html(c["busy"]["bars"]))
        fs = c["fac_score"]
        if fs is None:
            line(FACILITIES_ICON, f'<span style="{grey}">Facilities not checked yet</span>')
        else:
            col = _COLORS.get(facilities_level(fs), "#59636e")
            line(FACILITIES_ICON, f'<b style="color:{col};">{fs:.1f}</b> {esc(c["fac_rating"])}')
            b = c["fac"]
            if b["best"]:
                best = " · ".join(f'<b>{esc(p.get("name") or "")}</b> {int(round(p.get("dist_m", 0)))} m '
                                  f'<span style="{grey}">({esc(kind.lower())})</span>' for kind, p in b["best"])
                line("⭐", f'<span style="color:#1a7f37;">{best}</span>')
            for g_line in _group_lines(b):
                line("", f'<span style="{grey}">{esc(g_line)}</span>')
            if not b["groups"] and not b["best"]:
                line("", f'<span style="{grey}">Nothing named within 500 m</span>')
        tint = "background:#fff1f0;padding:8px 8px 6px 8px;" if c["tesla_only"] else "padding:8px 0 0 0;"
        h.append(f'<div style="font-size:13px;line-height:1.5;margin:10px 0 0 0;{tint}'
                 'border-top:1px solid #e8ebef;">' + "".join(rows) + '</div>')
    return h


def _cards_text(stations, route_info, eval_time, share: bool = False) -> List[str]:
    t = ["", "Station details", "---------------"]
    for i, st in enumerate(stations, 1):
        c = station_card_lines(st, route_info, eval_time, share)
        pad = "   "
        t.append("")
        t.append(f"{i}. {st.name} - {c['kind']}" + (f"  ⚠ {c['warn']}" if c["warn"] else "")
                 + ("  ⛔ TESLA ONLY - other EVs can't charge here" if c["tesla_only"] else ""))
        if c["tesla_only"]:
            for icon, body in (("📍", c["where"]), ("🧭", c["trip"])):
                if body:
                    t.append(f"{pad}{icon} {body}")
            continue
        price_rows = [("💰" if n_ == 0 else "  ", pl) for n_, pl in
                      enumerate(c["price_lines"] + ([c["fees"]] if c["fees"] else []))]
        busy_row = [("🚦", f"{c['busy']['text']}  {c['busy']['spark']} (4am→3am)")] if c["busy"] else []
        for icon, body in [("📍", c["where"]), ("⚡", c["hw"])] + price_rows + [("🧭", c["trip"])] + busy_row:
            if body:
                t.append(f"{pad}{icon} {body}")
        fs = c["fac_score"]
        if fs is None:
            t.append(f"{pad}{FACILITIES_ICON} Facilities not checked yet")
        else:
            t.append(f"{pad}{FACILITIES_ICON} {fs:.1f} {c['fac_rating']}".rstrip())
            b = c["fac"]
            if b["best"]:
                t.append(f"{pad}⭐ " + " · ".join(f"{p.get('name')} {int(round(p.get('dist_m', 0)))} m ({k.lower()})"
                                                 for k, p in b["best"]))
            for g_line in _group_lines(b):
                t.append(f"{pad}   {g_line}")
            if not b["groups"] and not b["best"]:
                t.append(f"{pad}   Nothing named within 500 m")
        url = station_maps_url(st)
        if url:
            t.append(f"{pad}🗺️ {url}")
    return t


def build_station_report(stations, title: str, route_info: Optional[Dict[str, Any]] = None,
                         eval_time: Optional[str] = None, type_icon_fn=None,
                         near_info: Optional[Dict[str, Any]] = None, share: bool = False) -> Tuple[str, str]:
    """(html, text) email body for a charger_find.py result.

    share=True makes it generic, for sending to someone else: no battery /
    arrival figures, planned stops, vehicle or trip-planner link (which
    carries your start coordinates), prices as full non-Tesla ranges, and
    Tesla-only stations marked in red so they're avoided."""
    type_icon_fn = type_icon_fn or (lambda s: s.type_icon())
    ri = route_info or {}
    if ri.get("origin_label") and " ALONG ROUTE" in title.upper():
        # Keep place names as written ("Brisbane, QLD"), not the terminal's all-caps title.
        kind = title.upper().split(" ALONG ROUTE")[0].strip().capitalize()
        heading = f"{kind} from {ri['origin_label']} to {ri.get('destination_label', '')}"
    elif near_info and near_info.get("label") and " NEAR " in title.upper():
        # Keep the place as written ("Brisbane, QLD"), not "brisbane, qld".
        kind = title.upper().split(" NEAR ")[0].strip().capitalize()
        heading = f"{kind} near {near_info['label']}"
    else:
        heading = title[:1].upper() + title[1:].lower() if title.isupper() else title
    summary = _summary_rows(title, stations, route_info, eval_time, near_info, share)
    headers, rows, styles = _table(stations, route_info, eval_time, type_icon_fn, share)
    abrp = None if share else (route_info or {}).get("abrp_link")
    esc = _html.escape

    font = "font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"
    h = [f'<div style="{font}font-size:14px;color:#1f2328;">',
         f'<h2 style="font-size:18px;margin:0 0 12px 0;">{esc(heading)}</h2>',
         '<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin:0 0 16px 0;">']
    for k, v in summary:
        h.append(f'<tr><td style="padding:2px 16px 2px 0;color:#59636e;white-space:nowrap;">{esc(k)}</td>'
                 f'<td style="padding:2px 0;">{esc(v)}</td></tr>')
    if abrp:
        h.append('<tr><td style="padding:2px 16px 2px 0;color:#59636e;">Trip plan</td>'
                 f'<td style="padding:2px 0;"><a href="{esc(abrp)}" style="color:#0969da;">Open in A Better Routeplanner</a></td></tr>')
    h.append('</table>')
    num_cols = {"#", "Bays", "Rate now", "Rate /kWh", RATE_SHARE_HDR, "Along route", "Detour", "Distance", "Stop score", FACILITIES_ICON}
    h.append('<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:13px;">')
    h.append('<tr>' + "".join(
        f'<th style="text-align:{"right" if c in num_cols else "left"};padding:6px 10px;border-bottom:2px solid #d1d9e0;'
        f'background:#f6f8fa;white-space:nowrap;">{esc(c)}</th>' for c in headers) + '</tr>')
    for cells, style in zip(rows, styles):
        tds = []
        for c, val in zip(headers, cells):
            color = _COLORS.get(style.get(c, ""), "")
            css = f'text-align:{"right" if c in num_cols else "left"};padding:5px 10px;border-bottom:1px solid #e8ebef;'
            if c in ("Station", "Pricing period"):
                css += "white-space:normal;"
            else:
                css += "white-space:nowrap;"
            if color:
                css += f"color:{color};font-weight:600;"
            cell = esc(val)
            if c == "Station" and style.get("_url"):
                cell = f'<a href="{esc(style["_url"])}" style="color:#0969da;text-decoration:none;">{cell}</a>'
            tds.append(f'<td style="{css}">{cell}</td>')
        row_bg = ' style="background:#fff1f0;"' if style.get("_row_bad") else ""
        h.append(f"<tr{row_bg}>" + "".join(tds) + "</tr>")
    h.append('</table>')
    h += _cards_html(stations, route_info, eval_time, share)
    h.append(f'<p style="font-size:12px;color:#59636e;margin:12px 0 0 0;">{FACILITIES_ICON} = nearby facilities '
             f'score out of 10 (shops, food, toilets within 500 m, from OpenStreetMap; - = not checked yet). '
             f'⭐ = shopping centres, big-name supermarkets and department stores within 500 m; +N = more of the same nearby. '
             + ('⛔ Tesla only = only Tesla vehicles can charge there. Other Superchargers are open to all EVs through the '
                'Tesla app; rates show the Tesla/member price first, which other EVs also get with a Tesla Supercharging '
                'membership, then the non-member price. ' if share else
                ('' if ri.get("no_soc") else 'Arrival battery is an estimate from distance and typical consumption. '))
             + '🚦 = Tesla\'s own Busy Times for the site, averaged over the week; bar height = share of stalls in use '
             + '(full height = all in use), green = most chargers available, amber = some. '
             + 'Station names link to Google Maps.</p>')
    h.append('</div>')
    html_out = "\n".join(h)

    # Plain-text fallback: aligned columns, no box drawing.
    t = [heading, "=" * len(heading), ""]
    w = max(len(k) for k, _ in summary)
    t += [f"{k:<{w}}  {v}" for k, v in summary]
    if abrp:
        t.append(f"{'Trip plan':<{w}}  {abrp}")
    t.append("")
    widths = [max(len(h_), *(len(r[i]) for r in rows)) if rows else len(h_) for i, h_ in enumerate(headers)]
    widths = [min(x, 40) for x in widths]
    fmt = lambda cells: "  ".join((c[:wd] if len(c) > wd else c).rjust(wd) if hd in num_cols else (c[:wd]).ljust(wd)
                                   for c, wd, hd in zip(cells, widths, headers)).rstrip()
    t.append(fmt(headers))
    t.append("  ".join("-" * wd for wd in widths))
    t += [fmt(r) for r in rows]
    t += _cards_text(stations, route_info, eval_time, share)
    t += ["", f"{FACILITIES_ICON} = nearby facilities score out of 10 (- = not checked yet). "
              "⭐ = shopping centres, big-name supermarkets and department stores within 500 m; +N = more of the same nearby. "
              + ("⛔ Tesla only = only Tesla vehicles can charge there. Other Superchargers are open to all EVs "
                 "through the Tesla app; rates show the Tesla/member price first, which other EVs also get with a "
                 "Tesla Supercharging membership, then the non-member price." if share else
                 ("" if ri.get("no_soc") else "Arrival battery is an estimate from distance and typical consumption."))]
    return html_out, "\n".join(t) + "\n"


def copy_report_to_clipboard(html_body: str, text_body: str) -> Tuple[bool, str]:
    """Put the report on the macOS clipboard as HTML + plain text so a paste
    into Mail/Gmail/Outlook keeps its formatting. Elsewhere (or if that
    fails) writes the HTML to a temp file and says where."""
    doc = f'<html><head><meta charset="utf-8"></head><body>{html_body}</body></html>'
    if platform.system() == "Darwin":
        hex_html = doc.encode("utf-8").hex()
        hex_text = text_body.encode("utf-8").hex()
        for script in (f"set the clipboard to {{«class HTML»:«data HTML{hex_html}», «class utf8»:«data utf8{hex_text}»}}",
                       f"set the clipboard to «data HTML{hex_html}»"):
            try:
                r = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True, timeout=20)
                if r.returncode == 0:
                    return True, "Copied to the clipboard - paste it into an email."
            except Exception:
                pass
    fd, path = tempfile.mkstemp(prefix="charger_report_", suffix=".html")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(doc)
    return False, f"Couldn't use the clipboard here - saved the email version to {path}"
