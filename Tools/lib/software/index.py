"""index.py - the local SQLite index of the stored software versions, and the questions asked of it.

The stored files (teslascope.py) are the truth; this index is a copy built from them, in the local cache folder (never iCloud), rebuilt on its own
when the files change. Delete it any time.

    ix = Index(data_dir)                                   builds or refreshes itself on first use
    ix.regions()                                           [{name, countries}]            the groups from config.json
    ix.versions_for(countries, model=None, hw=None)        what versions a region has     (newest first)
    ix.search_notes(text)                                  [{key, title, ...}]            notes by name
    ix.first_available(text, countries)                    when a note first reached a region (and every revision after it)
    ix.features_for(countries, model=None, hw=None, version=None)   what a region + model has by that version
    ix.compare(a, b, countries, model=None, hw=None)       what the jump a -> b lists, what it brought without listing (skipped versions), what only others got
    ix.uptake(version, countries=None)                     cars on a version now, by country and model, and the numbers kept over time

How a note is told apart (notes.py): the KEY is its name without version numbers, the HASH is its text. A page of Teslascope lists a note with a TAG, the
version Tesla first put that text in its release notes for that group of cars; a page repeats earlier notes only back to about the previous major build,
so what a jump installed is read from every version in between (compare), never from its two end pages alone.
Scope: a note names the countries and the car types it is for; none named means all.
"""
import json
import os
import sqlite3
import time

from lib.software import notes as N
from lib.software import settings as S
from lib.software import teslascope as T
from lib.software import versions as V

SCHEMA = 3
ALL = "*"


def default_cache():
    d = os.environ.get("TESLA_CACHE_DIR")
    if not d:
        home = os.path.expanduser("~")
        d = os.path.join(home, "Library", "Caches", "Tesellate") if os.path.isdir(os.path.join(home, "Library")) else os.path.join(home, ".cache", "tesellate")
    os.makedirs(d, exist_ok=True)
    return d


def _ts(s):
    e = T.iso(s)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(e)) if e else None


def _split_type(t):
    """'modely_TeslaAP4' -> ('modely', 'TeslaAP4'); a type with no hardware -> ('cybertruck', '*')."""
    m, _, h = (t or "").partition("_")
    return (m or ALL), (h or ALL)


SQL = """
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE versions(version TEXT PRIMARY KEY, k TEXT, line TEXT, first_spotted TEXT, latest TEXT, fsd_hw3 TEXT, fsd_hw4 TEXT,
                      installs INTEGER, total_installs INTEGER, pending INTEGER, early INTEGER);
CREATE TABLE version_countries(version TEXT, country TEXT, installs INTEGER);
CREATE TABLE version_models(version TEXT, model TEXT, hw TEXT, installs INTEGER);
CREATE TABLE upgrades(src TEXT, dst TEXT, installs INTEGER, PRIMARY KEY(src, dst));
CREATE TABLE daily(version TEXT, day TEXT, installs INTEGER);
CREATE TABLE occurrences(version TEXT, fid INTEGER, prior INTEGER, tag TEXT, tag_k TEXT, title TEXT, note_key TEXT, hash TEXT, fsd TEXT,
                         PRIMARY KEY(version, fid));
CREATE TABLE scope(version TEXT, fid INTEGER, country TEXT, model TEXT, hw TEXT);
CREATE TABLE note_text(note_key TEXT, hash TEXT, title TEXT, subtitle TEXT, description TEXT, PRIMARY KEY(note_key, hash));
CREATE TABLE introductions(note_key TEXT, country TEXT, hash TEXT, seq INTEGER, change TEXT, first_tag TEXT, first_tag_k TEXT, first_page TEXT,
                           first_page_k TEXT, tags TEXT, pages INTEGER, PRIMARY KEY(note_key, country, hash));
CREATE INDEX occ_k ON occurrences(note_key, hash);
CREATE INDEX scope_v ON scope(version, fid);
CREATE INDEX scope_c ON scope(country);
CREATE INDEX vc_c ON version_countries(country);
"""


class Index:
    _CHECKED = {}                    # index file -> when its held files were last compared with it (so a page that asks five times in a second stats the files once)

    def __init__(self, data_dir, cache_dir=None, settings=None, recheck_s=0):
        self.recheck_s = recheck_s
        self.data = data_dir
        self.cfg = settings or S.load()
        self.path = os.path.join(cache_dir or default_cache(), "software_index.sqlite")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._con = None

    # ------------------------------------------------------------------ build
    def _files(self):
        d = T.paths(self.data)[1]
        try:
            return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json"))
        except OSError:
            return []

    def _signature(self, files):
        n, newest, size = len(files), 0, 0
        for f in files:
            st = os.stat(f)
            newest, size = max(newest, st.st_mtime_ns), size + st.st_size
        return "%d|%d|%d|%d" % (SCHEMA, n, newest, size)

    def con(self):
        if self._con is None:
            self.ensure()
            self._con = sqlite3.connect(self.path)
            self._con.row_factory = sqlite3.Row
        return self._con

    def ensure(self):
        if self.recheck_s and time.time() - Index._CHECKED.get(self.path, 0) < self.recheck_s:
            return
        files = self._files()
        sig = self._signature(files)
        try:
            c = sqlite3.connect(self.path)
            have = c.execute("SELECT v FROM meta WHERE k='sig'").fetchone()
            c.close()
            if have and have[0] == sig:
                Index._CHECKED[self.path] = time.time()
                return
        except sqlite3.Error:
            pass
        self.build(files, sig)
        Index._CHECKED[self.path] = time.time()

    def build(self, files=None, sig=None):
        files = self._files() if files is None else files
        sig = sig or self._signature(files)
        tmp = self.path + ".new"
        if os.path.exists(tmp):
            os.remove(tmp)
        c = sqlite3.connect(tmp)
        c.executescript(SQL)
        text_seen = {}
        pages = {}
        for f in files:
            try:
                with open(f, "rb") as fh:
                    resp = T.parse_envelope(fh.read())
            except OSError:
                continue
            if not isinstance(resp, dict) or not resp.get("version"):
                continue
            self._add_version(c, resp, text_seen, pages)
        c.executemany("INSERT OR REPLACE INTO note_text VALUES (?,?,?,?,?)", [(k[0], k[1]) + v for k, v in text_seen.items()])
        self._derive_introductions(c)
        c.execute("INSERT INTO meta VALUES('sig',?)", (sig,))
        c.commit()
        c.close()
        os.replace(tmp, self.path)
        if self._con is not None:
            self._con.close()
            self._con = None

    def _add_version(self, c, r, text_seen, pages):
        v = r["version"]
        pend = r.get("pending") if isinstance(r.get("pending"), dict) else {}
        c.execute("INSERT OR REPLACE INTO versions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (v, V.sortkey(v), V.line(v), _ts(r.get("firstSpotted") or r.get("created_at")), _ts(r.get("latest_received_at")),
                   r.get("fsd_hw3"), r.get("fsd_hw4"), r.get("count"), r.get("totalCount"), pend.get("total"), r.get("early_access") or 0))
        for ct in r.get("countries") or []:
            if isinstance(ct, dict) and ct.get("country"):
                c.execute("INSERT INTO version_countries VALUES (?,?,?)", (v, ct["country"], ct.get("count") or 0))
        m = r.get("metrics") if isinstance(r.get("metrics"), dict) else {}
        for x in m.get("models") or []:
            if isinstance(x, dict):
                c.execute("INSERT INTO version_models VALUES (?,?,?,?)", (v, x.get("car_type") or ALL, x.get("driver_assist") or ALL, x.get("count") or 0))
        for x in m.get("days") or []:
            if isinstance(x, dict) and x.get("date"):
                c.execute("INSERT INTO daily VALUES (?,?,?)", (v, x["date"], x.get("count") or 0))
        for x in m.get("previousVersions") or []:
            if isinstance(x, dict) and x.get("version"):
                self._edge(c, x["version"], v, x.get("total") or 0)
        for x in m.get("nextVersions") or []:
            if isinstance(x, dict) and x.get("version"):
                self._edge(c, v, x["version"], x.get("total") or 0)
        for prior, lst in ((0, r.get("features")), (1, r.get("previous_features"))):
            for f in lst or []:
                if not isinstance(f, dict) or f.get("id") is None:
                    continue
                tag = f.get("customer_version") or v
                title = (f.get("title") or "").strip()
                key, h = N.key(title), N.text_hash(f.get("description"), f.get("subtitle") or "")
                c.execute("INSERT OR REPLACE INTO occurrences VALUES (?,?,?,?,?,?,?,?,?)",
                          (v, f["id"], prior, tag, V.sortkey(tag), title, key, h, N.fsd_version(title, f.get("description") or "")))
                for country in (f.get("countries") or [ALL]):
                    for ct in (f.get("car_types") or [ALL]):
                        model, hw = _split_type(ct)
                        c.execute("INSERT INTO scope VALUES (?,?,?,?,?)", (v, f["id"], country, model, hw))
                text_seen[(key, h)] = (title, f.get("subtitle") or "", f.get("description") or "")

    @staticmethod
    def _edge(c, a, b, n):
        row = c.execute("SELECT installs FROM upgrades WHERE src=? AND dst=?", (a, b)).fetchone()
        if row is None:
            c.execute("INSERT INTO upgrades VALUES (?,?,?)", (a, b, n))
        elif n > row[0]:
            c.execute("UPDATE upgrades SET installs=? WHERE src=? AND dst=?", (n, a, b))

    def _derive_introductions(self, c):
        """Per note and country: each distinct text in the order it first got a tag, as new / revised, with the tags that carried it."""
        rows = c.execute("""SELECT DISTINCT o.note_key, o.hash, o.tag, o.tag_k, o.version, v.k AS page_k, s.country
                            FROM occurrences o JOIN scope s ON s.version=o.version AND s.fid=o.fid JOIN versions v ON v.version=o.version""").fetchall()
        groups = {}
        for key, h, tag, tag_k, page, page_k, country in rows:
            g = groups.setdefault((key, country), {}).setdefault(h, {"tags": {}, "pages": set(), "page_k": {}})
            g["tags"][tag] = tag_k
            g["pages"].add(page)
            g["page_k"][page] = page_k
        out = []
        for (key, country), hs in groups.items():
            ordered = sorted(hs.items(), key=lambda kv: (min(kv[1]["tags"].values()), min(kv[1]["page_k"].values())))
            for seq, (h, g) in enumerate(ordered, 1):
                first_tag = min(g["tags"], key=lambda t: g["tags"][t])
                first_page = min(g["page_k"], key=lambda p: g["page_k"][p])
                out.append((key, country, h, seq, "new" if seq == 1 else "revised", first_tag, g["tags"][first_tag], first_page, g["page_k"][first_page],
                            json.dumps(sorted(g["tags"], key=V.key)), len(g["pages"])))
        c.executemany("INSERT INTO introductions VALUES (?,?,?,?,?,?,?,?,?,?,?)", out)

    # ------------------------------------------------------------------ helpers
    def q(self, sql, args=()):
        return [dict(r) for r in self.con().execute(sql, args).fetchall()]

    def countries_seen(self):
        """Every country code the held answers name (cars or notes), with the cars seen there: -> [{country, installs}], most cars first."""
        rows = self.q("SELECT country, SUM(installs) AS installs FROM version_countries GROUP BY country")
        have = {r["country"]: r["installs"] for r in rows}
        for r in self.q("SELECT DISTINCT country FROM scope"):
            have.setdefault(r["country"], 0)
        return sorted(({"country": c, "installs": n} for c, n in have.items() if c not in (ALL, "unknown", "", None)), key=lambda x: (-x["installs"], x["country"]))

    def _groups(self):
        """The region groups of the settings, and an "Other" group for any country in the data that none of them holds: no country is left out."""
        groups = {n: list(cs) for n, cs in self.cfg["regions"].items()}
        placed = {c for cs in groups.values() for c in cs}
        rest = [x["country"] for x in self.countries_seen() if x["country"] not in placed]
        if rest:
            groups["Other"] = sorted(rest)
        return groups

    def regions(self):
        return [{"name": n, "countries": cs} for n, cs in self._groups().items()]

    def resolve(self, region):
        """A group name, a country code, or a list of either -> a list of lower-case country codes."""
        out = []
        for r in ([region] if isinstance(region, str) else list(region or [])):
            g = next((cs for n, cs in self._groups().items() if n.lower() == str(r).lower()), None)
            out += list(g) if g is not None else [str(r).lower()]
        return out

    @staticmethod
    def _scope_sql(countries, model, hw, alias="s"):
        """-> (sql, args): a scope row matches when its country is one of countries (or unspecified), and its model / hardware match."""
        sql, args = [], []
        if countries:
            sql.append("%s.country IN (%s)" % (alias, ",".join("?" * (len(countries) + 1))))
            args += list(countries) + [ALL]
        if model:
            sql.append("%s.model IN (?,?)" % alias)
            args += [model, ALL]
        if hw:
            sql.append("%s.hw IN (?,?)" % alias)
            args += [hw, ALL]
        return (" AND ".join(sql) or "1=1"), args

    def label(self, model, hw=None):
        m = self.cfg["model_labels"].get(model, model)
        h = self.cfg["hardware_labels"].get(hw, hw) if hw and hw != ALL else ""
        return ("%s %s" % (m, h)).strip()

    # ------------------------------------------------------------------ questions
    def versions_for(self, region, model=None, hw=None):
        """What versions a region has had cars on or release notes for, newest first."""
        cs = self.resolve(region)
        ph = ",".join("?" * len(cs)) or "''"
        sql = """SELECT v.version, v.first_spotted, v.fsd_hw3, v.fsd_hw4, v.installs, v.total_installs,
                        COALESCE((SELECT SUM(installs) FROM version_countries c WHERE c.version=v.version AND c.country IN (%s)), 0) AS region_installs,
                        EXISTS(SELECT 1 FROM scope s WHERE s.version=v.version AND s.country IN (%s)) AS has_notes
                 FROM versions v ORDER BY v.k DESC""" % (ph, ph)
        rows = self.q(sql, cs + cs)
        out = []
        for r in rows:
            if not (r["region_installs"] or r["has_notes"]):
                continue
            if model or hw:
                n = self.q("SELECT COALESCE(SUM(installs),0) n FROM version_models WHERE version=? AND (?='' OR model=?) AND (?='' OR hw=?)",
                           (r["version"], model or "", model or "", hw or "", hw or ""))[0]["n"]
                if not n:
                    continue
            r["via"] = "cars and notes" if r["region_installs"] and r["has_notes"] else ("cars" if r["region_installs"] else "notes")
            out.append(r)
        return out

    def search_notes(self, text, limit=40):
        t = "%" + N.key(text) + "%" if N.key(text) else "%" + (text or "").lower() + "%"
        return self.q("""SELECT note_key, MIN(title) AS title, COUNT(DISTINCT hash) AS texts, MIN(tag_k) AS first_k
                         FROM occurrences WHERE note_key LIKE ? OR LOWER(title) LIKE ? GROUP BY note_key ORDER BY MIN(tag_k) DESC LIMIT ?""",
                      (t, "%" + (text or "").lower() + "%", limit))

    def first_available(self, text, region):
        """For every note matching text: each distinct text it has had in the region, with the tag and page it first came in and the tags that repeated it."""
        cs = self.resolve(region)
        keys = [r["note_key"] for r in self.search_notes(text, 200)]
        out = []
        for key in keys:
            ph = ",".join("?" * len(cs)) if cs else "''"
            rows = self.q("""SELECT i.hash, i.country, i.seq, i.change, i.first_tag, i.first_tag_k, i.first_page, i.first_page_k, i.tags, i.pages
                             FROM introductions i WHERE i.note_key=? AND (i.country IN (%s) OR i.country=?) ORDER BY i.first_tag_k, i.first_page_k""" % ph,
                          [key] + cs + [ALL])
            by = {}
            for r in rows:
                b = by.setdefault(r["hash"], {"hash": r["hash"], "first_tag": r["first_tag"], "first_tag_k": r["first_tag_k"], "first_page": r["first_page"],
                                              "first_page_k": r["first_page_k"], "countries": [], "tags": set(), "change": r["change"]})
                if r["first_tag_k"] < b["first_tag_k"]:
                    b.update(first_tag=r["first_tag"], first_tag_k=r["first_tag_k"], first_page=r["first_page"], first_page_k=r["first_page_k"])
                b["countries"].append(r["country"])
                b["tags"].update(json.loads(r["tags"]))
            for b in sorted(by.values(), key=lambda x: (x["first_tag_k"], x["first_page_k"])):
                t = self.q("SELECT title, subtitle, description FROM note_text WHERE note_key=? AND hash=?", (key, b["hash"]))[0]
                sp = self.q("SELECT first_spotted FROM versions WHERE version=?", (b["first_page"],))
                out.append(dict(note_key=key, title=t["title"], description=t["description"], hash=b["hash"], first_tag=b["first_tag"], first_page=b["first_page"],
                                first_seen=sp[0]["first_spotted"] if sp else None, change=b["change"], countries=sorted(set(b["countries"])),
                                repeated_in=sorted(b["tags"] - {b["first_tag"]}, key=V.key)))
            prev = None
            for i, row in enumerate([o for o in out if o["note_key"] == key]):
                row["change"] = "new" if i == 0 else "revised"
                if prev is not None:
                    row["prev_tag"], row["diff"] = prev["first_tag"], N.diff(prev["description"], row["description"])
                prev = row
        return out

    def _chain(self, key, countries):
        """The distinct texts a note has had for the countries, oldest first: [{hash, tag, text}] (all countries when none of these ever had it)."""
        for cs in (countries, None):
            ph = ",".join("?" * len(cs)) if cs else None
            where = "(i.country IN (%s) OR i.country=?)" % ph if cs else "1=1"
            rows = self.q("SELECT i.hash, MIN(i.first_tag_k) AS k, MIN(i.first_tag) AS tag FROM introductions i WHERE i.note_key=? AND %s GROUP BY i.hash ORDER BY k" % where,
                          [key] + (list(cs) + [ALL] if cs else []))
            if rows:
                break
        out = []
        for r in rows:
            t = self.q("SELECT description FROM note_text WHERE note_key=? AND hash=?", (key, r["hash"]))
            out.append(dict(hash=r["hash"], tag=r["tag"], text=t[0]["description"] if t else ""))
        return out

    def changed_from(self, key, h, countries):
        """How the text with hash h differs from the text the note had just before it in the countries' chain: {prev_tag, prev_hash, diff}, or None when it is the first."""
        ch = self._chain(key, countries)
        i = next((n for n, c in enumerate(ch) if c["hash"] == h), None)
        if not i:
            return None
        return dict(prev_tag=ch[i - 1]["tag"], prev_hash=ch[i - 1]["hash"], diff=N.diff(ch[i - 1]["text"], ch[i]["text"]))

    def steps(self, key, h_from, h_to, countries):
        """The texts a note went through from h_from to h_to, each compared with the one before it: [{tag, hash, diff}] (the first has no diff)."""
        ch = self._chain(key, countries)
        ia = next((n for n, c in enumerate(ch) if c["hash"] == h_from), None)
        ib = next((n for n, c in enumerate(ch) if c["hash"] == h_to), None)
        if ia is None or ib is None or ib <= ia:
            return []
        return [dict(tag=c["tag"], hash=c["hash"], diff=(N.diff(ch[n - 1]["text"], c["text"]) if n > ia else None)) for n, c in enumerate(ch) if ia <= n <= ib]

    def _scoped_notes(self, version, countries, model, hw):
        sql, args = self._scope_sql(countries, model, hw)
        return self.q("""SELECT DISTINCT o.note_key, o.hash, o.title, o.tag, o.tag_k, o.version FROM occurrences o
                         JOIN scope s ON s.version=o.version AND s.fid=o.fid WHERE o.version=? AND %s""" % sql, [version] + args)

    def _cumulative(self, version, countries, model, hw):
        """What a car on `version` has had listed for it: the newest text of each note listed for the region on that version or any before it, in the shape of
        _scoped_notes. Used for a version whose own page lists nothing for the region (2026.8.200: no car or note of Australia was ever seen on it)."""
        sql, args = self._scope_sql(countries, model, hw)
        rows = self.q("""SELECT o.note_key, o.hash, o.title, o.tag, o.tag_k, o.version, v.k AS page_k FROM occurrences o
                         JOIN scope s ON s.version=o.version AND s.fid=o.fid JOIN versions v ON v.version=o.version
                         WHERE v.k <= ? AND %s""" % sql, [V.sortkey(version)] + args)
        best = {}
        for r in rows:
            b = best.get(r["note_key"])
            if b is None or (r["tag_k"], r["page_k"]) > (b["tag_k"], b["page_k"]):
                best[r["note_key"]] = r
        return list(best.values())

    def held(self):
        """Every version held, newest first (all regions): a jump can be asked about between any two of them, not only the region's own."""
        return [r["version"] for r in self.q("SELECT version FROM versions ORDER BY k DESC")]

    def features_for(self, region, model=None, hw=None, version=None):
        """What the region + model has by `version` (default: the newest version with cars in the region): the newest text of each note listed for it on
        that version or any before it. listed_on_page says whether that version's own page lists it."""
        cs = self.resolve(region)
        if version is None:
            vs = self.versions_for(cs, model, hw)
            vs = [x for x in vs if x["region_installs"]] or vs
            if not vs:
                return {"version": None, "features": []}
            version = vs[0]["version"]
        kx = V.sortkey(version)
        sql, args = self._scope_sql(cs, model, hw)
        rows = self.q("""SELECT o.note_key, o.hash, o.title, o.tag, o.tag_k, o.version, v.k AS page_k FROM occurrences o
                         JOIN scope s ON s.version=o.version AND s.fid=o.fid JOIN versions v ON v.version=o.version
                         WHERE v.k <= ? AND %s""" % sql, [kx] + args)
        best = {}
        for r in rows:
            b = best.get(r["note_key"])
            if b is None or (r["tag_k"], r["page_k"]) > (b["tag_k"], b["page_k"]):
                best[r["note_key"]] = r
        page = {(r["note_key"], r["hash"]) for r in self._scoped_notes(version, cs, model, hw)}
        out = []
        for r in sorted(best.values(), key=lambda x: (x["tag_k"], x["note_key"]), reverse=True):
            t = self.q("SELECT description FROM note_text WHERE note_key=? AND hash=?", (r["note_key"], r["hash"]))
            out.append(dict(note_key=r["note_key"], title=r["title"], tag=r["tag"], hash=r["hash"], listed_on_page=(r["note_key"], r["hash"]) in page,
                            seen_on=r["version"], description=t[0]["description"] if t else "", **(self.changed_from(r["note_key"], r["hash"], cs) or {})))
        return {"version": version, "features": out}

    def _path(self, src, dst, depth=3, only=None):
        """True when cars were seen going src -> dst, directly or through up to `depth` steps. With `only` (a set of versions) the steps stay on those versions:
        Teslascope keeps the upgrade steps for all countries together, so a region's path is the steps between versions that region has had."""
        edges = {}
        for r in self.q("SELECT src, dst FROM upgrades"):
            if only is None or (r["src"] in only and r["dst"] in only):
                edges.setdefault(r["src"], set()).add(r["dst"])
        frontier, seen = {src}, {src}
        for _ in range(depth):
            frontier = {d for s in frontier for d in edges.get(s, ()) if d not in seen}
            if dst in frontier:
                return True
            seen |= frontier
        return False

    def compare(self, a, b, region, model=None, hw=None):
        """What the jump a -> b brings. -> dict with these groups (each a list of notes, newest tag first):
           listed_new       on b's page for the region and not on a's (no note of that name there)
           listed_revised   on both pages with a different text (`via` names the texts it went through on the versions in between)
           listed_again     on both pages with the same text, tagged again on b: nothing new to a car on a, listed for cars coming from older versions
           skipped          listed for the region on a version between a and b (or tagged between them) and not on b's page: installed all the same
                            when b's build contains it, but b's notes do not say so. `reaches_b`: cars were seen going from that version to b
           others           listed between a and b only for countries / car types outside the choice (`region_cars`: the car types the chosen region does have it for)
           between / between_region   every version held between a and b / those the region has had cars or notes on
           Every note carries first_tag / first_seen: the first version that listed that text for the region."""
        cs = self.resolve(region)
        for v in (a, b):
            if not self.q("SELECT 1 FROM versions WHERE version=?", (v,)):
                return {"error": "Version %s is not held. Run the software sync." % v}
        ka, kb = V.sortkey(a), V.sortkey(b)
        ea, eb = self._scoped_notes(a, cs, model, hw), self._scoped_notes(b, cs, model, hw)
        a_own = bool(ea)
        if not ea:
            ea = self._cumulative(a, cs, model, hw)
        newest = lambda rows: {k: max((r for r in rows if r["note_key"] == k), key=lambda r: r["tag_k"]) for k in {r["note_key"] for r in rows}}
        a_by = newest(ea)
        a_pairs = {(r["note_key"], r["hash"]) for r in ea}
        b_by = newest(eb)
        b_pairs = {(r["note_key"], r["hash"]) for r in eb}
        sql_all = """SELECT o.note_key, o.hash, o.title, o.tag, o.tag_k, o.version, s.country, s.model, s.hw FROM occurrences o
                     JOIN scope s ON s.version=o.version AND s.fid=o.fid JOIN versions v ON v.version=o.version
                     WHERE v.k > ? AND v.k < ? AND o.tag_k > ? AND o.tag_k <= ?"""
        mid = self.q(sql_all, [ka, kb, ka, kb])
        in_region = lambda r: (r["country"] in cs or r["country"] == ALL) and (not model or r["model"] in (model, ALL)) and (not hw or r["hw"] in (hw, ALL))
        groups = {}
        for r in mid:
            g = groups.setdefault((r["note_key"], r["hash"]), {"title": r["title"], "tag": r["tag"], "tag_k": r["tag_k"], "versions": set(), "countries": set(),
                                                              "applies": set(), "applies_v": set(), "region_cars": set()})
            g["versions"].add(r["version"])
            g["countries"].add(r["country"])
            if r["country"] in cs or r["country"] == ALL:
                g["region_cars"].add((r["model"], r["hw"]))
            if in_region(r):
                g["applies"].add(r["country"])
                g["applies_v"].add(r["version"])
        by_key = {}
        for (key, h), g in groups.items():
            by_key.setdefault(key, []).append((g["tag_k"], h, g))
        first = {}

        def first_for(key, h):
            if (key, h) not in first:
                ph = ",".join("?" * len(cs)) if cs else "''"
                rows = self.q("""SELECT i.first_tag, i.first_tag_k, i.first_page FROM introductions i WHERE i.note_key=? AND i.hash=? AND (i.country IN (%s) OR i.country=?)
                                 ORDER BY i.first_tag_k LIMIT 1""" % ph, [key, h] + cs + [ALL])
                sp = self.q("SELECT first_spotted FROM versions WHERE version=?", (rows[0]["first_page"],)) if rows else []
                first[(key, h)] = (rows[0]["first_tag"] if rows else None, sp[0]["first_spotted"] if sp else None)
            return first[(key, h)]

        def desc(r, **more):
            ft, fs = first_for(r["note_key"], r["hash"])
            return dict(note_key=r["note_key"], title=r["title"], tag=r["tag"], hash=r["hash"], first_tag=ft, first_seen=fs, **more)

        new, revised, again = [], [], []
        for r in sorted(eb, key=lambda x: (x["tag_k"], x["note_key"]), reverse=True):
            k = r["note_key"]
            if (k, r["hash"]) in a_pairs:
                if r["tag_k"] > a_by[k]["tag_k"]:
                    again.append(desc(r))
                continue
            if k in a_by:
                via = [dict(tag=g["tag"], title=g["title"]) for tk, h, g in sorted(by_key.get(k, []), key=lambda x: x[0]) if h not in (r["hash"], a_by[k]["hash"])]
                revised.append(desc(r, was_tag=a_by[k]["tag"], was_title=a_by[k]["title"], was_hash=a_by[k]["hash"], via=via))
            else:
                new.append(desc(r))
        reach = {}
        mine = {v["version"] for v in self.versions_for(cs, model, hw)} | {a, b}            # this region's own versions: its path is not the world's
        reaches = lambda v: reach.setdefault(v, self._path(v, b, only=mine))
        skipped, others = [], []
        for key, lst in by_key.items():
            tk, h, g = max(lst, key=lambda x: x[0])                       # the newest text of the note in the range
            if (key, h) in b_pairs or (key, h) in a_pairs:
                continue
            if key in b_by and b_by[key]["tag_k"] >= tk:
                continue                                                  # b's page lists a newer text of this note: that supersedes it
            if key in a_by and a_by[key]["tag_k"] >= tk:
                continue
            item = desc(dict(note_key=key, hash=h, title=g["title"], tag=g["tag"]), versions=sorted(g["applies_v"] or g["versions"], key=V.key),
                        countries=sorted(c for c in g["countries"] if c != ALL) or [ALL], earlier_texts=len(lst) - 1)
            if g["applies"]:
                item["applies_to"] = sorted(c for c in g["applies"] if c != ALL) or [ALL]
                item["reaches_b"] = any(reaches(v) for v in item["versions"])
                skipped.append(item)
            else:
                if g["region_cars"]:        # the region does get it, but for other car types than the one chosen
                    item["region_cars"] = sorted({self.label(m, h) if m != ALL else "all cars" for m, h in g["region_cars"]})
                others.append(item)
        key_f = lambda x: V.sortkey(x["tag"])
        for it in revised:                                  # compared with the text just before it in the chain a -> b, and each step on the way
            st = self.steps(it["note_key"], it["was_hash"], it["hash"], cs)
            it["chain"] = st
            it.update(self.changed_from(it["note_key"], it["hash"], cs) or {})
        for it in skipped + others:
            it.update(self.changed_from(it["note_key"], it["hash"], cs) or {})
        return {"a": a, "b": b, "countries": cs, "model": model, "hw": hw, "a_page_lists_nothing_here": not a_own,
                "listed_new": new, "listed_revised": revised, "listed_again": again,
                "skipped": sorted(skipped, key=key_f, reverse=True), "others": sorted(others, key=key_f, reverse=True),
                "between": [r["version"] for r in self.q("SELECT version FROM versions WHERE k > ? AND k < ? ORDER BY k", (ka, kb))],
                "between_region": sorted([v["version"] for v in self.versions_for(cs, model, hw) if ka < V.sortkey(v["version"]) < kb], key=V.key)}

    @staticmethod
    def _desc(r):
        return {"note_key": r["note_key"], "title": r["title"], "tag": r["tag"], "hash": r["hash"]}

    def uptake(self, version, region=None):
        """Cars that took the version (Teslascope's count: every car seen on it, not the cars on it today), by country and model, the numbers kept
        over time (one line per change since this tool first saw them), and where the cars came from and went to."""
        rows = self.q("SELECT * FROM versions WHERE version=?", (version,))
        if not rows:
            return {"error": "Version %s is not held." % version}
        v = rows[0]
        cs = self.resolve(region) if region else []
        countries = self.q("SELECT country, installs FROM version_countries WHERE version=? ORDER BY installs DESC", (version,))
        models = self.q("SELECT model, hw, installs FROM version_models WHERE version=? ORDER BY installs DESC", (version,))
        for m in models:
            m["label"] = self.label(m["model"], m["hw"])
        total_c = sum(c["installs"] for c in countries)
        region_n = sum(c["installs"] for c in countries if c["country"] in cs) if cs else None
        series = []
        try:
            with open(os.path.join(self.data, T.UPTAKE), encoding="utf-8") as fh:
                for ln in fh:
                    d = json.loads(ln)
                    if d.get("version") == version:
                        series.append(d)
        except (OSError, ValueError):
            pass
        return {"version": version, "first_spotted": v["first_spotted"], "installs": v["installs"], "pending": v["pending"], "fsd_hw4": v["fsd_hw4"], "fsd_hw3": v["fsd_hw3"],
                "countries": countries, "models": models, "region_installs": region_n, "sample_note": "installs by country add up to %d" % total_c,
                "daily": self.q("SELECT day, installs FROM daily WHERE version=? ORDER BY day DESC", (version,)),
                "from": self.q("SELECT src AS version, installs FROM upgrades WHERE dst=? ORDER BY installs DESC", (version,)),
                "to": self.q("SELECT dst AS version, installs FROM upgrades WHERE src=? ORDER BY installs DESC", (version,)),
                "series": series}
