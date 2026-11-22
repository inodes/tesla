"""app_software.py - the Software page of tesla_app.py: what the Teslascope files held in <data>/software say (lib/software/), as the page draws it.

    SoftSync(data_dir)        the fetch run in the background (lib/software/sync.py): start() / stop() / status(), with the step it is on for a progress bar
    overview(data_dir, region, model, hw)   the regions and car types to choose from, the versions of that choice newest first (each with the FSD version it
                                            includes, if it does, for the chip), the version the car is on
    detail(data_dir, version, region, model, hw)   one version: its features and who took it
    first(data_dir, text, region)  when a feature first reached a region
    compare(data_dir, a, b, region, model, hw)     what a jump from a to b brings and what it leaves out

FSD and FSD Lite are two different things: FSD needs an AI4 computer and FSD Lite an AI3 one (fsd_kinds, hidden_for). A car picked as AI3 never sees a version that only
has FSD, one picked as AI4 never sees a version that only has FSD Lite; a version with both is for both.

Nothing here fetches except SoftSync; the questions are answered from the index lib/software/index.py keeps in the local cache folder.
"""
import os
import re
import threading
import time

from lib import car_versions
from lib.fetch_ledger import FetchLedger
from lib.software import index as I
from lib.software import notes as N
from lib.software import settings as S
from lib.software import sync as SY
from lib.software import teslascope as T

_LOCK = threading.Lock()          # one index build at a time; the questions themselves only read


_ANS, _ANS_LOCK, ANS_MAX = {}, threading.Lock(), 200


_CARK = {}


def _car_key(data_dir):
    """What of the car's own versions (car_versions.json) the answers depend on: all of it but the "last seen" times. The car watch rewrites the file at every
    look (20 s after a start, then every few minutes) only to move those, and the file's time in the key had every answer worked out again each time."""
    p = car_versions._path(data_dir)
    try:
        st = os.stat(p)
    except OSError:
        return None
    hit = _CARK.get(p)
    if hit and hit[0] == (st.st_size, st.st_mtime_ns):
        return hit[1]
    d = car_versions.load(data_dir)
    k = repr(dict(d, versions=[{a: b for a, b in v.items() if a != "last_seen"} for v in d.get("versions") or []]))
    _CARK[p] = ((st.st_size, st.st_mtime_ns), k)
    return k


def _remembered(name):
    """The answer of a question kept while what it is worked out from is unchanged: the held Teslascope files (the index's signature), the car's own versions
    and the settings. The page asks the same few questions again and again; worked out each time they took 0.2 s on an idle Mac and 10-20 s while another
    thread was busy (each of ~1,000 rows waits for the interpreter: the charge book reading invoices, say)."""
    def wrap(fn):
        def go(data_dir, *args):
            ix = _index(data_dir)
            key = (name, data_dir, args, I.Index.SIG.get(ix.path), _car_key(data_dir), repr(sorted(ix.cfg.items(), key=lambda kv: kv[0])))
            with _ANS_LOCK:
                hit = _ANS.get(key)
            if hit is not None:
                return hit
            res = fn(data_dir, *args)
            with _ANS_LOCK:
                if len(_ANS) >= ANS_MAX:
                    _ANS.clear()
                _ANS[key] = res
            return res
        go.__wrapped__ = fn
        go.__doc__ = fn.__doc__
        return go
    return wrap


def _index(data_dir):
    with _LOCK:
        ix = I.Index(data_dir, recheck_s=10)
        ix.ensure()
    return ix


def _major(v):
    try:
        return int(str(v).split(".")[0])
    except ValueError:
        return None


def fsd_chip(row, hw=None, labels=None):
    """The FSD versions a version includes, for the chip on its row: [{v: "14.3.12", kind: "fsd", hw: "AI4"}], one per computer that has its own; FSD Lite (the AI3
    one, Teslascope writes "14.3 Lite") has kind "lite". [] when it includes none. With a computer chosen, only that one."""
    labels = labels or S.load()["hardware_labels"]
    out = []
    for key, code in (("fsd_hw4", "TeslaAP4"), ("fsd_hw3", "TeslaAP3")):
        v = row.get(key)
        if v and (not hw or hw == code):
            s = str(v).strip()
            lite = bool(re.search(r"\blite\b", s, re.I))
            out.append(dict(v=re.sub(r"\s*\blite\b\s*", "", s, flags=re.I).strip(), kind="lite" if lite else "fsd", hw=labels.get(code, code)))
    if len(out) == 2 and out[0]["v"] == out[1]["v"] and out[0]["kind"] == out[1]["kind"]:
        out = [dict(v=out[0]["v"], kind=out[0]["kind"], hw="")]
    return out


def fsd_table(ix):
    """{country: {hw4: bool, hw3: "lite" | False}} from settings "fsd_countries" (a plain list of codes means FSD on AI4 only)."""
    v = ix.cfg.get("fsd_countries") or {}
    if not isinstance(v, dict):
        v = {c: {"hw4": True, "hw3": False} for c in v}
    return {str(c).lower(): dict(hw4=bool((x or {}).get("hw4")), hw3=(x or {}).get("hw3") or False) for c, x in v.items()}


def fsd_countries(ix, region, kind="any"):
    """The countries of the region where FSD (Supervised) is approved (settings "fsd_countries"); kind "hw4": those with FSD for AI4 cars, "lite": those with FSD v14 Lite for
    AI3 cars. Only cars and notes of these count for FSD. [] when the region has none."""
    t = fsd_table(ix)
    ok = {"any": lambda e: True, "hw4": lambda e: e["hw4"], "lite": lambda e: bool(e["hw3"])}[kind]
    return [c for c in ix.resolve(region) if c in t and ok(t[c])]


def fsd_cars(ix, region):
    """{version: {any, hw4, lite}}: cars seen on it in the region's FSD countries (any approved one, one with FSD for AI4, one with Lite for AI3), the AI4 / AI3 ones no more than
    the AI4 / AI3 cars the version has."""
    out = {}
    for kind in ("any", "hw4", "lite"):
        fcs = fsd_countries(ix, region, kind)
        if not fcs:
            continue
        for r in ix.q("SELECT version, SUM(installs) n FROM version_countries WHERE country IN (%s) GROUP BY version" % ",".join("?" * len(fcs)), list(fcs)):
            out.setdefault(r["version"], dict(any=0, hw4=0, lite=0))[kind] = r["n"] or 0
    # the computer decides too: FSD (v13+) runs only on AI4 and Lite only on AI3, so an AI3 car in Australia that signs up for FSD still gets a non-FSD version. Teslascope gives
    # cars by country and by computer separately, so the cars that can be on the version are at most the AI4 (or AI3) cars it has seen, whichever countries they are in.
    hw = {}
    for r in ix.q("SELECT version, hw, SUM(installs) n FROM version_models GROUP BY version, hw"):
        hw.setdefault(r["version"], {})[r["hw"]] = r["n"] or 0
    for v, e in out.items():
        h = hw.get(v)
        if h:
            e["hw4"] = min(e["hw4"], h.get("TeslaAP4", 0))
            e["lite"] = min(e["lite"], h.get("TeslaAP3", 0))
    return out


def notes_chips(ix, region, model=None, hw=None):
    """{version: [chip]}: the FSD the notes first listed on that version name (titles like "FSD (Supervised) v14.3.9 Down Under", "FSD (Supervised) v14.3 Lite") for this
    region and car. Teslascope fills its own FSD field only for some versions (2026.28.5 names v14.3.9 in its notes, the field is empty), so the notes fill the gaps."""
    out = {}
    for kind, want in (("hw4", "fsd"), ("lite", "lite")):       # a Lite note counts for the countries that have Lite, an FSD note for those that have it on AI4
        fcs = fsd_countries(ix, region, kind)
        if not fcs:
            continue
        sql, args = ix._scope_sql(fcs, model, hw)
        rows = ix.q("SELECT DISTINCT o.version, o.title FROM occurrences o JOIN scope s ON s.version=o.version AND s.fid=o.fid WHERE o.tag=o.version AND " + sql, args)
        for r in rows:
            k = N.fsd_note(r["title"])
            if k and k[1] and k[0] == want:
                c = dict(v=k[1], kind=k[0], hw="")
                if c not in out.setdefault(r["version"], []):
                    out[r["version"]].append(c)
    return {v: sorted(cs, key=lambda c: c["kind"]) for v, cs in out.items()}


def fsd_kinds(ix):
    """{version: {fsd, lite, legacy}}: what FSD each version carries whichever region it is for (the computer a version needs does not depend on the country).
    fsd = FSD from v13 on (AI4 only), lite = FSD Lite (AI3 only), legacy = FSD before v13, which AI3 cars had too. From the notes' titles and Teslascope's fields."""
    first = int(ix.cfg.get("fsd_ai4_from") or 13)
    out, bare = {}, set()

    def put(v, kind, ver):
        e = out.setdefault(v, dict(fsd=False, lite=False, legacy=False))
        m = _major(ver)
        if kind == "lite":
            e["lite"] = True
        elif m is not None and m < first:
            e["legacy"] = True
        else:
            e["fsd"] = True
    for r in ix.q("SELECT DISTINCT version, title FROM occurrences WHERE tag=version AND (title LIKE 'FSD%' OR title LIKE 'Tesla Assisted Driving%')"):
        k = N.fsd_note(r["title"])
        if not k:
            continue
        if k[1]:
            put(r["version"], k[0], k[1])
        else:
            bare.add(r["version"])
    for r in ix.q("SELECT version, fsd_hw3, fsd_hw4 FROM versions WHERE fsd_hw3 IS NOT NULL OR fsd_hw4 IS NOT NULL"):
        if r["fsd_hw4"]:
            put(r["version"], "fsd", r["fsd_hw4"])
        if r["fsd_hw3"]:
            h3 = str(r["fsd_hw3"])
            put(r["version"], "lite" if re.search(r"\blite\b", h3, re.I) else "fsd", re.sub(r"[^0-9.]", "", h3) if re.search(r"\d", h3) else "")
    for v in bare:                                                  # "FSD (Supervised)" with no version says only that it is there: FSD
        if v not in out:
            out[v] = dict(fsd=True, lite=False, legacy=False)
    return out


def branch_cars(kinds, have):
    """A version carrying FSD is an FSD branch: only cars of countries that have that FSD can be on it (cars that do not subscribe, and cars of countries without FSD, are given
    the non-FSD versions, which are other versions). -> the cars of the region that can be, or None when the version is not an FSD branch. `have`: this version's entry of fsd_cars."""
    ks = [k for k, on in (("hw4", (kinds or {}).get("fsd")), ("lite", (kinds or {}).get("lite")), ("any", (kinds or {}).get("legacy"))) if on]
    return max(have.get(k, 0) for k in ks) if ks else None


def hidden_for(kinds, hw):
    """True when a version with these kinds cannot be installed on that computer: FSD only (no Lite, no older FSD) on AI3, FSD Lite only on AI4."""
    if not kinds:
        return False
    if hw == "TeslaAP3":
        return kinds["fsd"] and not kinds["lite"] and not kinds["legacy"]
    if hw == "TeslaAP4":
        return kinds["lite"] and not kinds["fsd"]
    return False


def hidden_versions(ix, model, hw):
    """The versions the chosen computer cannot install because of the FSD they carry (hidden_for), as a set; empty when no computer is chosen. Cars of that
    computer that did install a version (at least `fsd_hw_share_pct` of its installs) keep it on the list: the notes only say what FSD it carries."""
    if hw not in ("TeslaAP3", "TeslaAP4"):
        return set()
    sql, args = "SELECT version, SUM(CASE WHEN hw=? THEN installs ELSE 0 END) h, SUM(installs) n FROM version_models" + (" WHERE model=?" if model else "") + " GROUP BY version", [hw] + ([model] if model else [])
    share = {r["version"]: (100.0 * r["h"] / r["n"] if r["n"] else 0.0) for r in ix.q(sql, args)}
    need = float(ix.cfg.get("fsd_hw_share_pct") if ix.cfg.get("fsd_hw_share_pct") is not None else 5)
    return {v for v, k in fsd_kinds(ix).items() if hidden_for(k, hw) and share.get(v, 0.0) < need}


def chip_fits(c, hw, first=13):
    """A chip belongs to the computer chosen: FSD v13+ to AI4, FSD Lite and older FSD to AI3 (hw "" = no computer chosen: all)."""
    if hw == "TeslaAP4":
        return c["kind"] == "fsd" and (_major(c["v"]) is None or _major(c["v"]) >= first)
    if hw == "TeslaAP3":
        return c["kind"] == "lite" or (_major(c["v"]) is not None and _major(c["v"]) < first)
    return True


def chip_counts(c, cars, first=13):
    """Cars of the region that can have this chip: FSD Lite needs a country with Lite, FSD from v13 one with FSD for AI4, older FSD any approved country."""
    if c["kind"] == "lite":
        return cars.get("lite", 0)
    if _major(c["v"]) is None or _major(c["v"]) >= first:
        return cars.get("hw4", 0)
    return cars.get("any", 0)


def chips_for(ix, row, nf, hw, cars=None):
    """The FSD chips of a version for the region: Teslascope's FSD field counts only when cars of a country that has that FSD are on the version (`cars`: fsd_cars;
    None = no region asked), the notes' FSD only from the countries that have it (notes_chips)."""
    first = int(ix.cfg.get("fsd_ai4_from") or 13)
    chips = fsd_chip(row, hw, ix.cfg["hardware_labels"])
    if cars is not None:
        have = cars.get(row["version"]) or {}
        chips = [c for c in chips if chip_counts(c, have, first)]
    if not chips:
        chips = [c for c in nf.get(row["version"], []) if chip_fits(c, hw, first)]
    return chips


def choices(ix):
    cfg = ix.cfg
    seen = ix.q("SELECT model, hw, SUM(installs) n FROM version_models GROUP BY model, hw ORDER BY n DESC")
    models = []
    for r in seen:
        hw = r["hw"] if r["hw"] not in (None, "None", "") else None
        models.append(dict(model=r["model"], hw=hw, label=ix.label(r["model"], hw), installs=r["n"]))
    mc = cfg.get("my_car") or {}
    my = dict(model=mc.get("model"), hw=mc.get("hw") or None, label=ix.label(mc.get("model"), mc.get("hw"))) if mc.get("model") else None
    return dict(regions=ix.regions(), countries=ix.countries_seen(), models=models, model_names=cfg["model_labels"], my_car=my)


def held(data_dir):
    """-> how many versions are held, the newest and oldest, when the last one was fetched."""
    d = T.paths(data_dir)[1]
    try:
        names = [f[:-5] for f in os.listdir(d) if f.endswith(".json")]
    except OSError:
        names = []
    return dict(n=len(names), newest=max(names, key=SY.V.key) if names else None, oldest=min(names, key=SY.V.key) if names else None)


def car_journey(ix, data_dir):
    """The car's own versions oldest first (lib/car_versions.py), each with what Teslascope says about it, the FSD chip, when it was first spotted anywhere and
    whether it is held (so it can be compared)."""
    held = set(ix.held())
    out = []
    for j in car_versions.journey(data_dir):
        r = ix.q("SELECT first_spotted, fsd_hw3, fsd_hw4 FROM versions WHERE version=?", (j["version"],))
        sp = r[0] if r else {}
        fsd = [dict(v=j["fsd"], kind="fsd", hw="")] if j.get("fsd") else fsd_chip(sp, (ix.cfg.get("my_car") or {}).get("hw") or None, ix.cfg["hardware_labels"])
        out.append(dict(j, fsd_chip=fsd, spotted=sp.get("first_spotted"), held=j["version"] in held))
    return out


def overview(data_dir, region="ANZ", model=None, hw=None):
    if not held(data_dir)["n"]:
        return dict(empty=True, held=held(data_dir))
    res = _overview(data_dir, region, model, hw)
    if res.get("empty"):
        return res
    return dict(res, left=SY.status_text(data_dir, FetchLedger(data_dir), time.time())[1])      # what is left to fetch changes while a sync runs: never kept


@_remembered("overview")
def _overview(data_dir, region="ANZ", model=None, hw=None):
    if not held(data_dir)["n"]:
        return dict(empty=True, held=held(data_dir))
    ix = _index(data_dir)
    car = car_versions.load(data_dir)
    mine = car.get("current")
    nope = hidden_versions(ix, model, hw)
    rows = [r for r in ix.versions_for(region, model, hw) if r["version"] not in nope]
    nf = notes_chips(ix, region, model, hw)
    cars = fsd_cars(ix, region)
    fk = fsd_kinds(ix)
    keep = []
    for r in rows:                                  # an FSD version has only FSD-country cars on it: those of other countries (2 of Ukraine on 2026.38.5.11) are not counted, and a version with only those is not the region's
        n = branch_cars(fk.get(r["version"]), cars.get(r["version"]) or {})
        if n is None:
            keep.append(r)
        elif n or r["via"] != "cars":
            keep.append(dict(r, region_installs=n, region_installs_all=r["region_installs"]))
    rows = keep
    out = []
    for r in rows:
        chip = chips_for(ix, r, nf, hw, cars)
        out.append(dict(version=r["version"], first=r["first_spotted"], installs=r["installs"], region_installs=r["region_installs"], has_notes=bool(r["has_notes"]),
                        via=r["via"], fsd=chip, mine=r["version"] == mine))
    st = SY.status_text(data_dir, FetchLedger(data_dir), time.time())
    car = car_versions.load(data_dir)
    return dict(choices=choices(ix), region=region, model=model, hw=hw, versions=out, mine=mine, held=held(data_dir), left=st[1], total_installs=rows[0]["total_installs"] if rows else 0,
                all_versions=ix.held(), journey=car_journey(ix, data_dir), car_changes=car.get("changes") or [], car_updates=car.get("updates") or [], staged=car.get("staged"))


@_remembered("detail")
def detail(data_dir, version, region, model=None, hw=None):
    ix = _index(data_dir)
    f = ix.features_for(region, model, hw, version)
    u = ix.uptake(version, region)
    if u.get("error"):
        v2 = ix.version_of_tag(version)               # a release-note tag that is not a version (widevine-4.10.3050): the version whose page carried it
        if v2 and v2 != version:
            return dict(redirect=v2, tag=version)
        return dict(error="%s is not a version this tool holds, and no held version's notes carry it as a tag." % version)
    nf = notes_chips(ix, region, model, hw)
    fcs = fsd_countries(ix, region)
    cars = fsd_cars(ix, region)
    u["fsd"] = chips_for(ix, u, nf, hw, cars)
    u["fsd_cars"] = (cars.get(version) or {}).get("any", 0)
    n = branch_cars(fsd_kinds(ix).get(version), cars.get(version) or {})
    if n is not None and u.get("region_installs") is not None:
        u["region_installs_ignored"] = max(0, u["region_installs"] - n)       # cars of the region's countries without that FSD: it is an FSD branch, they cannot be on it
        u["region_installs"] = n
    if not u["fsd"]:                                # what the version carries for FSD in the world, which this region's cars do not have (the page says so)
        u["fsd_elsewhere"] = fsd_chip(u, hw, ix.cfg["hardware_labels"])
    u["fsd_countries"] = fcs
    # Teslascope keeps the upgrade steps for every country together; this region's path is the steps from and to versions it has had itself
    mine = {v["version"] for v in ix.versions_for(region)}
    for k in ("from", "to"):
        u[k + "_all"] = len(u.get(k) or [])
        u[k] = [dict(e, region=e["version"] in mine) for e in (u.get(k) or [])]
        u[k + "_region"] = [e for e in u[k] if e["region"]]
    return dict(version=version, features=f["features"], own=ix.own_notes(version, region, model, hw), car_label=ix.label(model, hw) if model else "", uptake=u, region_countries=ix.resolve(region), my_car=choices(ix)["my_car"])


@_remembered("timeline")
def timeline(data_dir, region, model=None, hw=None):
    """What the timeline chart draws besides the versions list: the last day each version still got installs, and the upgrade steps between the versions this
    region + car has had (cars seen going from one to the other). The days are Teslascope's for all regions together; the region decides which versions and steps
    are here, and `region_installs` (in overview) says how many of the cars were its own."""
    ix = _index(data_dir)
    nope = hidden_versions(ix, model, hw)
    mine = {v["version"] for v in ix.versions_for(region, model, hw) if v["version"] not in nope}
    last = {r["version"]: r["last"] for r in ix.q("SELECT version, MAX(day) AS last FROM daily WHERE installs > 0 GROUP BY version") if r["version"] in mine}
    edges = [dict(src=r["src"], dst=r["dst"], installs=r["installs"]) for r in ix.q("SELECT src, dst, installs FROM upgrades") if r["src"] in mine and r["dst"] in mine]
    journey = car_journey(ix, data_dir)
    extra = {}                                                  # the car's own versions that the region's list does not have (2026.8.200): still drawn
    for j in journey:
        v = j["version"]
        if v in mine or not j["held"]:
            continue
        r = ix.q("SELECT first_spotted, installs FROM versions WHERE version=?", (v,))[0]
        d = ix.q("SELECT MAX(day) AS last FROM daily WHERE version=? AND installs > 0", (v,))
        extra[v] = dict(version=v, first=r["first_spotted"], installs=r["installs"], last=d[0]["last"], fsd=j["fsd_chip"], region_installs=0)
        mine.add(v)
    last.update({v: e["last"] for v, e in extra.items() if e["last"]})
    edges = [dict(src=r["src"], dst=r["dst"], installs=r["installs"]) for r in ix.q("SELECT src, dst, installs FROM upgrades") if r["src"] in mine and r["dst"] in mine]
    return dict(last=last, edges=edges, journey=journey, extra=extra)


@_remembered("first")
def first(data_dir, text, region):
    ix = _index(data_dir)
    return dict(found=ix.search_notes(text), rows=ix.first_available(text, region) if text else [])


@_remembered("compare")
def compare(data_dir, a, b, region, model=None, hw=None):
    ix = _index(data_dir)
    r = ix.compare(a, b, region, model, hw)
    if r.get("error"):
        return r
    for k in ("listed_new", "listed_revised", "listed_again", "skipped", "others"):          # the text of each note, for the page to open
        for it in r.get(k, []):
            t = ix.q("SELECT description FROM note_text WHERE note_key=? AND hash=?", (it["note_key"], it["hash"]))
            it["text"] = t[0]["description"] if t else ""
    return r


class SoftSync:
    """The fetch in a thread of this program. status() is what the page polls: running, how far, what it is on, how it ended."""

    def __init__(self, data_dir):
        self.data = data_dir
        self.th = None
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.done = self.total = 0
        self.what = ""
        self.result = None
        self.error = ""
        self.started = self.ended = None
        self.halt = False
        self.lines = []

    def running(self):
        return self.th is not None and self.th.is_alive()

    def _say(self, s):
        with self.lock:
            self.lines = (self.lines + [str(s)])[-40:]

    def _progress(self, done, total, what):
        with self.lock:
            self.done, self.total, self.what = done, total, what

    def _run(self, since):
        try:
            res = SY.sync(self.data, since=since, say=self._say, bar=False, progress=self._progress, stop=lambda: self.halt)
            with self.lock:
                self.result = res
        except Exception as e:                      # shown on the page, never lost
            with self.lock:
                self.error = "%s: %s" % (type(e).__name__, e)
        finally:
            with self.lock:
                self.ended = time.time()

    def start(self, since=None):
        if self.running():
            return self.status()
        self.reset()
        self.started = time.time()
        self.th = threading.Thread(target=self._run, args=(since,), daemon=True)
        self.th.start()
        return self.status()

    def stop(self):
        self.halt = True
        return self.status()

    def status(self):
        with self.lock:
            return dict(ok=True, running=self.running(), done=self.done, total=self.total, what=self.what, result=self.result, error=self.error, started=self.started,
                        ended=self.ended, lines=list(self.lines))
