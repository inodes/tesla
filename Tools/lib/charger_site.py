"""charger_site.py - everything the charger registry holds about one site, shaped for the site page (Places -> a Supercharger or charger).

site_info(rec, busy, facilities) -> dict: the place, hardware, access, tariffs and fees, amenities, a busy-time profile (Tesla), what is around it (the facilities
score) and, per source (tesla.com, PlugShare, BP Pulse, Chargefox ...), every field that source gave, with the link and when it was last checked.
rec is one record of <data>/charging_stations.json; busy is <data>/chargers/tesla_busy_times.json; facilities is <data>/station_facilities.json.
"""
import re

try:
    import tesla_busy_times as BT
except ImportError:                                           # run as a package
    from lib import tesla_busy_times as BT

SKIP = {"lat", "lon", "lng", "latitude", "longitude", "keywords", "first_seen", "last_seen", "seen_count", "valid_from", "photos", "location", "addresses",
        "_resolved_postcode", "_resolved_suburb", "_migrated_from_legacy_schema", "hardware", "compatibility", "tariffs", "tessie_cost_config", "access", "aliases",
        "amenities", "plugshare_metadata", "tesla_metadata"}
SRC = {"tesla": "tesla.com", "plugshare": "PlugShare", "bppulse": "BP Pulse", "chargefox": "Chargefox", "evie": "Evie", "evx": "EVX", "ampol": "Ampol",
       "nrma": "NRMA", "exploren": "Exploren", "jolt": "Jolt", "ampcharge": "AmpCharge"}


def _label(k):
    k = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(k)).replace("_", " ").strip()
    return k[:1].upper() + k[1:]


def _flat(d, pre=""):
    """[(label, text)] of the scalars of a source's own record (lists of plain values joined; long or nested records left out)."""
    out = []
    for k, v in d.items():
        if k in SKIP or v in (None, "", [], {}):
            continue
        if isinstance(v, dict):
            out += _flat(v, _label(k) + " ")
        elif isinstance(v, list):
            if all(not isinstance(x, (dict, list)) for x in v):
                out.append((pre + _label(k), ", ".join(str(x) for x in v)))
        elif isinstance(v, bool):
            out.append((pre + _label(k), "Yes" if v else "No"))
        else:
            out.append((pre + _label(k), str(v)))
    return out


def _sched(rows):
    out = []
    for r in rows or []:
        out.append(dict(label=r.get("label") or r.get("name") or "", start=r.get("start_time"), end=r.get("end_time"), days=r.get("days") or [], months=r.get("months") or [],
                        rate=r.get("rate_per_kwh_gross", r.get("rate_per_kwh")), net=r.get("rate_per_kwh_net"), tax=r.get("tax_amount")))
    return out


def busy_of(rec, busy):
    """Tesla's busy-time chart for a Supercharger: the hours (local) of each weekday, Monday first."""
    ids = [rec.get("id"), ((rec.get("views") or {}).get("tesla") or {}).get("id"), (((rec.get("views") or {}).get("tesla") or {}).get("tesla_metadata") or {}).get("findus_id")]
    prof = next((busy[str(i)] for i in ids if i and str(i) in (busy or {})), None)
    if not prof:
        return None
    days = [dict(day=n, hours=[round(x, 3) for x in BT.local_hourly(prof, i)]) for i, n in enumerate(("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"))]
    s = BT.busy_summary(prof)
    return dict(date=prof.get("profile_date") or prof.get("fetched") or "", days=days, text=s["text"], peak_hour=s["peak_hour"], peak_pct=s["peak_pct"])


def facilities_of(rec, facilities):
    nm = rec.get("name") or ""
    for k in ("%s_%s" % (rec.get("network") or "", nm), "Tesla_" + nm, nm):
        f = (facilities or {}).get(k)
        if f:
            return dict(score=f.get("score"), rating=f.get("rating"), badge=f.get("badge"), total=f.get("total_count"), categories=f.get("categories") or {}, updated=f.get("last_updated") or "")
    return None


def site_info(rec, busy=None, facilities=None):
    loc, hw, cp, ac, tf = (rec.get(k) or {} for k in ("location", "hardware", "compatibility", "access", "tariffs"))
    tm = tf.get("tesla_members") or {}
    nt = tf.get("non_tesla") or {}
    views = rec.get("views") or {}
    prov = rec.get("provenance") or {}
    sources = []
    for k, v in views.items():
        if not isinstance(v, dict):
            continue
        meta = v.get("tesla_metadata") or v.get("plugshare_metadata") or {}
        url = meta.get("findus_url") or meta.get("url") or v.get("url") or ""
        rows = _flat(meta) + _flat({a: b for a, b in v.items() if a not in ("tesla_metadata", "plugshare_metadata")})
        seen, rows2 = set(), []
        for a, b in rows:                                     # the same fact only once
            if (a, b) not in seen:
                seen.add((a, b))
                rows2.append([a, b])
        sources.append(dict(name=SRC.get(k, _label(k)), url=url, verified=v.get("last_verified") or v.get("last_updated") or "", rows=rows2))
    return dict(
        name=rec.get("name"), network=rec.get("network"), operator=rec.get("operator"), aliases=rec.get("aliases") or [],
        place=dict(address=loc.get("address"), street=loc.get("street"), suburb=loc.get("suburb"), state=loc.get("state"), postcode=loc.get("postcode"), country=loc.get("country"),
                   timezone=loc.get("timezone"), radius_m=loc.get("radius_m"), lat=loc.get("lat"), lon=loc.get("lon")),
        hardware=dict(stalls=hw.get("stalls"), available=hw.get("stalls_available"), kw=hw.get("max_power_kw"), tier=hw.get("tier"), speed=hw.get("speed_tier"),
                      connectors=hw.get("connector_types") or [], tethered=hw.get("tethered"), pedestals=hw.get("pedestals") or [], models=hw.get("hardware_models") or []),
        access=dict(tesla_only=cp.get("tesla_only"), open_to_all=cp.get("open_to_non_tesla"), kind=cp.get("access"), hours=ac.get("hours"), description=ac.get("description")),
        tariffs=dict(currency=tf.get("currency"), model=tf.get("pricing_model"), tou=tf.get("has_tou_pricing"), tax_name=tf.get("tax_name"), tax_rate=tf.get("tax_rate"),
                     tax_included=tf.get("tax_included_in_listed_price"), range=tf.get("rate_range"), per_kwh=tf.get("per_kwh_gross"), per_kwh_net=tf.get("per_kwh_net"),
                     idle=tf.get("idle_fee_per_min_gross"), congestion=tf.get("congestion_fee_per_min_gross", tf.get("congestion_fee_per_min")), parking=tf.get("parking_fee"),
                     schedule=_sched(tf.get("schedule")), members=_sched(tm.get("rate_schedules")), members_model=tm.get("pricing_model"),
                     non_tesla=_sched(nt.get("rate_schedules")), non_tesla_supported=nt.get("supported"), non_tesla_model=nt.get("pricing_model")),
        amenities=[_label(k) for k, v in (rec.get("amenities") or {}).items() if v],
        busy=busy_of(rec, busy), facilities=facilities_of(rec, facilities),
        checked=prov.get("last_verified_utc") or "", sources=sources, source_names=[SRC.get(s, _label(s)) for s in prov.get("sources") or []])


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _hm(h):
    return "%02d:00" % h


def _row(label, start, end, days, months, ex, gst):
    return dict(label=label, start=start, end=end, days=days, months=months, rate=round(ex * gst, 4), net=round(ex, 4), tax=round(ex * (gst - 1), 4))


def agl_tariff(periods, gst, today, provider="AGL", plan=""):
    """What a home charge costs is the household electricity tariff (the scripts price home charges with it too, not the charger's own rate card): the period in
    force on `today` as rate rows (gross = ex-GST x gst, as the charge pages show), its fixed charges and feed-in, and every earlier period as a history."""
    cur = None
    for p in periods:
        if (p.get("effective_from") or "") <= today and (not p.get("effective_to") or p["effective_to"] >= today):
            cur = p
    if cur is None:
        return None
    all_m, wk, every = list(MONTHS), ["Mon", "Tue", "Wed", "Thu", "Fri"], list(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
    rows, tt = [], cur.get("tariff_type")
    if tt == "tou":
        pm = [MONTHS[m - 1] for m in sorted(cur.get("peak_months") or [])]
        om = [m for m in all_m if m not in pm]
        a, b = cur["peak_hour_start"], cur["peak_hour_end"]
        pk, op = cur["peak_rate_per_kwh"], cur["offpeak_rate_per_kwh"]
        rows.append(_row("Peak", _hm(a), _hm(b), every, pm, pk, gst))
        rows.append(_row("Off-peak", _hm(0), _hm(a), every, pm, op, gst))
        rows.append(_row("Off-peak", _hm(b), "24:00", every, pm, op, gst))
        if om:
            rows.append(_row("Off-peak (all day)", _hm(0), "24:00", every, om, op, gst))
        model = "time_of_use"
    elif tt == "tou3" and len({cur.get("peak_rate_per_kwh"), cur.get("shoulder_rate_per_kwh"), cur.get("offpeak_rate_per_kwh")}) > 1:
        a, b = cur["peak_hour_start"], cur["peak_hour_end"]
        rows.append(_row("Peak", _hm(a), _hm(b), wk, [], cur["peak_rate_per_kwh"], gst))
        for w0, w1 in cur.get("shoulder_weekday_windows") or []:
            rows.append(_row("Shoulder", _hm(w0), _hm(w1), wk, [], cur["shoulder_rate_per_kwh"], gst))
        rows.append(_row("Shoulder", _hm(cur.get("shoulder_weekend_hour_start", 7)), _hm(cur.get("shoulder_weekend_hour_end", 22)), ["Sat", "Sun"], [], cur["shoulder_rate_per_kwh"], gst))
        rows.append(_row("Off-peak", "", "", every, [], cur["offpeak_rate_per_kwh"], gst))
        model = "time_of_use"
    else:
        ex = cur.get("usage_rate_per_kwh") if cur.get("usage_rate_per_kwh") is not None else cur.get("offpeak_rate_per_kwh")
        rows.append(_row("All day", _hm(0), "24:00", every, [], ex, gst) if ex is not None else None)
        rows = [r for r in rows if r]
        model = "flat"
    rates = [r["rate"] for r in rows]
    fixed = []
    if cur.get("supply_charge_per_day") is not None:
        fixed.append(["Supply charge", "$%.4f/day" % (cur["supply_charge_per_day"] * gst)])
    if cur.get("controlled_load_2_rate_per_kwh") is not None:
        fixed.append(["Controlled load 2", "$%.4f/kWh" % (cur["controlled_load_2_rate_per_kwh"] * gst)])
    if cur.get("demand_rate_per_kw_day"):
        fixed.append(["Demand charge", "$%.4f/kW/day" % (cur["demand_rate_per_kw_day"] * gst)])
    if cur.get("solar_feed_in_tier1_rate_per_kwh") is not None:
        fixed.append(["Solar feed-in", "$%.2f/kWh first tier, $%.2f/kWh after" % (cur["solar_feed_in_tier1_rate_per_kwh"], cur.get("solar_feed_in_tier2_rate_per_kwh") or 0)])
    hist = []
    for p in sorted(periods, key=lambda x: x.get("effective_from") or "", reverse=True):
        pk = p.get("peak_rate_per_kwh") if p.get("peak_rate_per_kwh") is not None else p.get("usage_rate_per_kwh")
        op = p.get("offpeak_rate_per_kwh") if p.get("offpeak_rate_per_kwh") is not None else p.get("usage_rate_per_kwh")
        if pk is None and p.get("supply_charge_per_day") is None:
            continue
        hist.append(dict(frm=p.get("effective_from"), to=p.get("effective_to") or "", peak=None if pk is None else round(pk * gst, 4), offpeak=None if op is None else round(op * gst, 4),
                         supply=None if p.get("supply_charge_per_day") is None else round(p["supply_charge_per_day"] * gst, 4), feed_in=p.get("solar_feed_in_tier1_rate_per_kwh"),
                         kind=p.get("tariff_type") or "flat"))
    return dict(currency="AUD", model=model, tou=model == "time_of_use", tax_name="GST", tax_rate=round(gst - 1, 4), tax_included=True,
                range=("$%.4f - $%.4f/kWh" % (min(rates), max(rates))) if len(set(rates)) > 1 else None, per_kwh=rates[0] if len(set(rates)) == 1 else None,
                per_kwh_net=None, idle=None, congestion=None, parking=None, schedule=rows, members=[], members_model=None, non_tesla=[], non_tesla_supported=None, non_tesla_model=None,
                retailer="%s%s" % (provider, " " + plan if plan else ""), since=cur.get("effective_from"), fixed=fixed, history=hist)


def personal_info(name, rec, agl=None):
    """The same shape as site_info for one of your own chargers (<data>/chargers/personal_chargers.json: a home or workplace charger)."""
    hw, cp, co = (rec.get(k) or {} for k in ("hardware", "compatibility", "costs"))
    sched = _sched([dict(label=r.get("name"), start_time=r.get("start_time"), end_time=r.get("end_time"), days=r.get("days"), months=r.get("months"), rate_per_kwh=r.get("rate_per_kwh"))
                    for r in co.get("rate_schedules") or []])
    rates = [r["rate"] for r in sched if r["rate"] is not None]
    t = dict(currency=co.get("currency"), model=co.get("pricing_model"), tou=co.get("pricing_model") == "time_of_use", tax_name=None, tax_rate=None, tax_included=None,
             range=("$%.2f - $%.2f/kWh" % (min(rates), max(rates))) if len(set(rates)) > 1 else None, per_kwh=(rates[0] if len(set(rates)) == 1 else (co.get("flat_per_kwh") or None) if not rates else None),
             per_kwh_net=None, idle=None, congestion=None, parking=None, schedule=sched, members=[], members_model=None, non_tesla=[], non_tesla_supported=None, non_tesla_model=None)
    if agl:
        t = agl
    if co.get("per_minute"):
        t["parking"] = "$%.2f/min" % co["per_minute"]
    if co.get("per_session"):
        t["parking"] = ((t["parking"] + " · ") if t["parking"] else "") + "$%.2f/session" % co["per_session"]
    return dict(name=rec.get("name") or name, network=rec.get("network"), operator=rec.get("operator"), aliases=[],
                place=dict(address=rec.get("address"), street=None, suburb=None, state=None, postcode=None, country=None, timezone=co.get("timezone"), radius_m=rec.get("radius_m"),
                           lat=rec.get("lat"), lon=rec.get("lon")),
                hardware=dict(stalls=None, available=None, kw=hw.get("max_power_kw"), tier=hw.get("charger_type"), speed=rec.get("type"), connectors=hw.get("connector_types") or [],
                              tethered=None, pedestals=[], models=[hw.get("brand")] if hw.get("brand") else []),
                access=dict(tesla_only=bool(cp.get("tesla_only")), open_to_all=bool(cp.get("open_to_non_tesla")), kind=cp.get("access"), hours=None, description=""),
                tariffs=t, amenities=[], busy=None, facilities=None, checked="", sources=[], source_names=[])
