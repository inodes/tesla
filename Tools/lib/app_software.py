"""app_software.py - the Software page of tesla_app.py: what the Teslascope files held in <data>/software say (lib/software/), as the page draws it.

    SoftSync(data_dir)        the fetch run in the background (lib/software/sync.py): start() / stop() / status(), with the step it is on for a progress bar
    overview(data_dir, region, model, hw)   the regions and car types to choose from, the versions of that choice newest first (each with the FSD version it
                                            includes, if it does, for the chip), the version the car is on
    detail(data_dir, version, region, model, hw)   one version: its features and who took it
    first(data_dir, text, region)  when a feature first reached a region
    compare(data_dir, a, b, region, model, hw)     what a jump from a to b brings and what it leaves out

Nothing here fetches except SoftSync; the questions are answered from the index lib/software/index.py keeps in the local cache folder.
"""
import os
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


def _index(data_dir):
    with _LOCK:
        ix = I.Index(data_dir, recheck_s=10)
        ix.ensure()
    return ix


def fsd_chip(row, hw=None, labels=None):
    """The FSD versions a version includes, for the chip on its row: [{v: "14.3.12", hw: "AI4"}], one per computer that has its own. [] when it includes none.
    With a computer chosen, only that one."""
    labels = labels or S.load()["hardware_labels"]
    out = []
    for key, code in (("fsd_hw4", "TeslaAP4"), ("fsd_hw3", "TeslaAP3")):
        v = row.get(key)
        if v and (not hw or hw == code):
            out.append(dict(v=str(v), hw=labels.get(code, code)))
    if len(out) == 2 and out[0]["v"] == out[1]["v"]:
        out = [dict(v=out[0]["v"], hw="")]
    return out


def notes_fsd(ix, region, model=None, hw=None):
    """{version: "14.3.9"}: the FSD version a note first listed on that version names (title like "FSD (Supervised) v14.3.9 Down Under") for this region and car.
    Teslascope fills its own FSD field only for some versions (2026.28.5 names v14.3.9 in its notes, the field is empty), so the notes fill the gaps."""
    sql, args = ix._scope_sql(ix.resolve(region), model, hw)
    rows = ix.q("SELECT DISTINCT o.version, o.title FROM occurrences o JOIN scope s ON s.version=o.version AND s.fid=o.fid WHERE o.tag=o.version AND " + sql, args)
    out = {}
    for r in rows:
        v = N.fsd_version(r["title"])
        if v:
            out.setdefault(r["version"], v)
    return out


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
        fsd = [dict(v=j["fsd"], hw="")] if j.get("fsd") else fsd_chip(sp, None, ix.cfg["hardware_labels"])
        out.append(dict(j, fsd_chip=fsd, spotted=sp.get("first_spotted"), held=j["version"] in held))
    return out


def overview(data_dir, region="ANZ", model=None, hw=None):
    if not held(data_dir)["n"]:
        return dict(empty=True, held=held(data_dir))
    ix = _index(data_dir)
    car = car_versions.load(data_dir)
    mine = car.get("current")
    rows = ix.versions_for(region, model, hw)
    nf = notes_fsd(ix, region, model, hw)
    out = []
    for r in rows:
        chip = fsd_chip(r, hw, ix.cfg["hardware_labels"]) or ([dict(v=nf[r["version"]], hw="")] if r["version"] in nf else [])
        out.append(dict(version=r["version"], first=r["first_spotted"], installs=r["installs"], region_installs=r["region_installs"], has_notes=bool(r["has_notes"]),
                        via=r["via"], fsd=chip, mine=r["version"] == mine))
    st = SY.status_text(data_dir, FetchLedger(data_dir), time.time())
    car = car_versions.load(data_dir)
    return dict(choices=choices(ix), region=region, model=model, hw=hw, versions=out, mine=mine, held=held(data_dir), left=st[1], total_installs=rows[0]["total_installs"] if rows else 0,
                all_versions=ix.held(), journey=car_journey(ix, data_dir), car_changes=car.get("changes") or [], car_updates=car.get("updates") or [], staged=car.get("staged"))


def detail(data_dir, version, region, model=None, hw=None):
    ix = _index(data_dir)
    f = ix.features_for(region, model, hw, version)
    u = ix.uptake(version, region)
    if u.get("error"):
        return dict(error=u["error"])
    nf = notes_fsd(ix, region, model, hw)
    u["fsd"] = fsd_chip(u, hw, ix.cfg["hardware_labels"]) or ([dict(v=nf[version], hw="")] if version in nf else [])
    # Teslascope keeps the upgrade steps for every country together; this region's path is the steps from and to versions it has had itself
    mine = {v["version"] for v in ix.versions_for(region)}
    for k in ("from", "to"):
        u[k + "_all"] = len(u.get(k) or [])
        u[k] = [dict(e, region=e["version"] in mine) for e in (u.get(k) or [])]
        u[k + "_region"] = [e for e in u[k] if e["region"]]
    return dict(version=version, features=f["features"], uptake=u, region_countries=ix.resolve(region), my_car=choices(ix)["my_car"])


def timeline(data_dir, region, model=None, hw=None):
    """What the timeline chart draws besides the versions list: the last day each version still got installs, and the upgrade steps between the versions this
    region + car has had (cars seen going from one to the other). The days are Teslascope's for all regions together; the region decides which versions and steps
    are here, and `region_installs` (in overview) says how many of the cars were its own."""
    ix = _index(data_dir)
    mine = {v["version"] for v in ix.versions_for(region, model, hw)}
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


def first(data_dir, text, region):
    ix = _index(data_dir)
    return dict(found=ix.search_notes(text), rows=ix.first_available(text, region) if text else [])


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
