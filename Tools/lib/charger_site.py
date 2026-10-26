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


def personal_info(name, rec):
    """The same shape as site_info for one of your own chargers (<data>/chargers/personal_chargers.json: a home or workplace charger)."""
    hw, cp, co = (rec.get(k) or {} for k in ("hardware", "compatibility", "costs"))
    sched = _sched([dict(label=r.get("name"), start_time=r.get("start_time"), end_time=r.get("end_time"), days=r.get("days"), months=r.get("months"), rate_per_kwh=r.get("rate_per_kwh"))
                    for r in co.get("rate_schedules") or []])
    rates = [r["rate"] for r in sched if r["rate"] is not None]
    t = dict(currency=co.get("currency"), model=co.get("pricing_model"), tou=co.get("pricing_model") == "time_of_use", tax_name=None, tax_rate=None, tax_included=None,
             range=("$%.2f - $%.2f/kWh" % (min(rates), max(rates))) if len(set(rates)) > 1 else None, per_kwh=(rates[0] if len(set(rates)) == 1 else (co.get("flat_per_kwh") or None) if not rates else None),
             per_kwh_net=None, idle=None, congestion=None, parking=None, schedule=sched, members=[], members_model=None, non_tesla=[], non_tesla_supported=None, non_tesla_model=None)
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
