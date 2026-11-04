"""app_drivelist.py - the Drives list: one list of what the car did, from every source that knows.

Tessie is the source of truth for a drive when there is one (its times, distance, energy, battery and ends). The dashcam's drives, which the video and its telemetry
make exact to the second, attach to the Tessie drive they fall in, so a row can show the video and the route. With no Tessie the dashcam alone makes the list, and a drive
the dashcam saw that Tessie has no record of is listed too (src "cam"). A charge is a row of its own; the dashcam's "stopped" spell during a charge is that charge, not a second row.

    merge(tessie, charges, cam_items)   -> items, newest first, the shape the Timeline page draws (kind drive | charge | stopped | parked | sentry | gap | recording)

A row's `src` says where a drive came from: "both" (Tessie and dashcam), "tessie" (no footage kept), "cam" (the dashcam only). `cam` lists the dashcam drive ids inside it and
`cov` is the share of the Tessie drive that has dashcam drive time (0 to 1). Tessie rounds its times to the minute, so a dashcam drive within SLACK_S of a Tessie drive's edges
still belongs to it.
"""
import threading

import teslacam_timeline as TL

SLACK_S = 60            # Tessie's drive times are whole minutes
SHARE = 0.5             # a dashcam drive belongs to a Tessie drive when at least this much of it falls inside (with the slack)


def _ov(a0, a1, b0, b1):
    return min(a1, b1) - max(a0, b0)


def _pos(lat, lon):
    return [lat, lon] if lat is not None and lon is not None else None


def _same_place(a, b, lim=None):
    p, q = a.get("p1") or a.get("p0"), b.get("p0") or b.get("p1")
    if not p or not q or p[0] is None or q[0] is None:
        return False
    return TL.hav(p[0], p[1], q[0], q[1]) <= (lim or TL.OPTS["same_place_m"])


def _coalesce(stops, join_s=120):
    """The dashcam's stopped / parked spells that follow one another at one place (a recording that restarted) are one spell."""
    out = []
    for c in sorted(stops, key=lambda x: x["t0"]):
        p = out[-1] if out else None
        if p and c["t0"] - p["t1"] <= join_s and (_same_place(p, c) or not (p.get("p1") and c.get("p0"))):
            p["t1"] = max(p["t1"], c["t1"])
            p["n"] = (p.get("n") or 1) + (c.get("n") or 1)
            p["p1"] = c.get("p1") or p.get("p1")
            p["kind"] = "parked" if "parked" in (p["kind"], c["kind"]) else "stopped"
            continue
        out.append(dict(c))
    return out


def merge(tessie, charges, cam_items, idles=(), slack=SLACK_S, now=None):
    td = sorted(tessie, key=lambda t: t["t0"])
    cams = sorted((c for c in cam_items if c["kind"] == "drive"), key=lambda c: c["t0"])
    mine = {i: [] for i in range(len(td))}
    lone = []
    for c in cams:
        best, key = None, (0, 0)
        for i, t in enumerate(td):
            wide = _ov(c["t0"], c["t1"], t["t0"] - slack, t["t1"] + slack)
            k = (_ov(c["t0"], c["t1"], t["t0"], t["t1"]), wide)
            if wide >= SHARE * max(1, c["t1"] - c["t0"]) and k > key:
                best, key = i, k
        (mine[best].append(c) if best is not None else lone.append(c))
    out = []
    for i, t in enumerate(td):
        cs = mine[i]
        span = max(1, t["t1"] - t["t0"])
        cov = min(1.0, sum(max(0, _ov(c["t0"], c["t1"], t["t0"], t["t1"])) for c in cs) / float(span))
        it = dict(id=t["id"], kind="drive", src="both" if cs else "tessie", t0=t["t0"], t1=t["t1"], km=t.get("km"), vmax=t.get("max"), kwh=t.get("kwh"), whkm=t.get("whkm"),
                  b0=t.get("b0"), b1=t.get("b1"), ap=t.get("ap"), frm=t.get("frm"), to=t.get("to"), p0=_pos(t.get("lat"), t.get("lon")), p1=_pos(t.get("lat1"), t.get("lon1")),
                  cam=[c["id"] for c in cs], cov=round(cov, 2), short=bool(t.get("repark")), page=bool(t.get("page")), al=t.get("al", 0), min=t.get("min"), avg=t.get("avg"),
                  foot=t.get("foot"))
        if t.get("parts"):
            it["parts"], it["stops"] = t["parts"], t.get("stops") or []             # joined from several of Tessie's drives (a stop of a moment between them)
        out.append(TL.show(it))
    for c in lone:
        it = dict(c, src="cam", cam=[c["id"]], cov=1.0)
        out.append(it)
    ch = sorted(charges, key=lambda c: c["t0"])
    for c in ch:
        it = dict(id=c["id"], kind="charge", t0=c["t0"], t1=c["t1"], place=c.get("place"), pkey=c.get("pkey"), kwh=c.get("kwh"), b0=c.get("b0"), b1=c.get("b1"), ckind=c.get("kind"), cost=c.get("cost"),
                  p0=_pos(c.get("lat"), c.get("lon")), p1=_pos(c.get("lat"), c.get("lon")), min=c.get("min"))
        out.append(TL.show(it))
    # between Tessie's own events: parked where the car was the same place before and after, else a hole in the record
    events = sorted([x for x in out if x["kind"] in ("drive", "charge") and x.get("src") != "cam"], key=lambda x: x["t0"])
    between = []
    for a, b in zip(events, events[1:]):
        if b["t0"] - a["t1"] > TL.OPTS["gap_min_s"]:
            g = TL._gap(a, b)
            g["src"] = "tessie"
            for i in idles:
                if _ov(i["t0"], i["t1"], g["t0"], g["t1"]) > 0:
                    g["kwh"] = round((g.get("kwh") or 0) + (i.get("kwh") or 0), 2)
                    g["sentry"] = max(g.get("sentry") or 0, i.get("sentry") or 0)
            between.append(TL.show(g))
    out += between
    covered = ch + [x for x in between if x["kind"] in ("parked", "gap")]
    stops = []
    for c in cam_items:
        if c["kind"] == "drive":
            continue
        if c["kind"] in ("stopped", "parked", "gap") and any(_ov(c["t0"], c["t1"], h["t0"], h["t1"]) >= SHARE * max(1, c["t1"] - c["t0"]) for h in covered):
            continue                                                    # Tessie says where the car was then: a stop at a charger is the charge, the rest is its parked row
        if c["kind"] in ("stopped", "parked"):
            stops.append(c)
        elif c["kind"] == "recording" and _absorb(c, out):
            continue
        else:
            out.append(dict(c))
    for c in _coalesce(stops):
        out.append(TL.show(c))
    return join_parks(sorted(out, key=lambda x: (-x["t0"], -x["t1"])), now)


def _absorb(rec, out):
    """A recording (footage whose telemetry is not read yet) over time Tessie knows is not a row of its own: the drives it touches say `tmiss` (minutes of footage with no telemetry
    read, `held` = the TeslaCam drives holding it), and a park or charge it touches already shows its footage. True when Tessie has something there."""
    hit = [x for x in out if x.get("src") != "cam" and x["kind"] in ("drive", "charge", "parked", "gap") and _ov(rec["t0"], rec["t1"], x["t0"], x["t1"]) > 0]
    for x in hit:
        if x["kind"] != "drive":
            continue
        mins = max(1, int(round(_ov(rec["t0"], rec["t1"], x["t0"], x["t1"]) / 60.0)))
        m = x.setdefault("tmiss", dict(min=0, held=[]))
        m["min"] += min(mins, rec.get("n") or mins)
        m["held"] = sorted(set(m["held"]) | set(rec.get("held") or []))
    return bool(hit)


PARKISH = ("parked", "stopped")
JOIN_GAP_S = 300                                    # parks this close together at one place are one park


def _at(a, b, lim=150.0):
    """True when two items are at one place, or when one has no position and so cannot say otherwise."""
    p, q = a.get("p1") or a.get("p0"), b.get("p0") or b.get("p1")
    if not p or not q or p[0] is None or q[0] is None:
        return True
    return TL.hav(p[0], p[1], q[0], q[1]) <= lim


def _is_repark(x):
    """A move that only shuffled the car: Tessie flags it short and it ends where it began."""
    if x["kind"] != "drive" or not x.get("short"):
        return False
    p, q = x.get("p0"), x.get("p1")
    return bool(not p or not q or p[0] is None or q[0] is None or TL.hav(p[0], p[1], q[0], q[1]) <= 150.0)


def _fold_move(d, m, end):
    """A short move (the car shuffled a few metres) that is part of the drive next to it: the drive now ends (`end`) or starts (not `end`) where the move did. It keeps what it was
    made of: `parts` (the records, in time order) and `moves` (the move's id)."""
    parts = d.get("parts") or [dict(id=d["id"], t0=d["t0"], t1=d["t1"], page=d.get("page"))]
    mp = dict(id=m["id"], t0=m["t0"], t1=m["t1"], page=m.get("page"))
    d["parts"] = parts + [mp] if end else [mp] + parts
    d["moves"] = (d.get("moves") or []) + [m["id"]]
    if end:
        d["t1"], d["p1"] = max(d["t1"], m["t1"]), m.get("p1") or d.get("p1")
        d["to"] = m.get("to") or d.get("to")
    else:
        d["t0"], d["p0"] = min(d["t0"], m["t0"]), m.get("p0") or d.get("p0")
        d["frm"] = m.get("frm") or d.get("frm")
    for k in ("km", "kwh"):
        if m.get(k) is not None:
            d[k] = round((d.get(k) or 0) + m[k], 2)
    d["cam"] = (d.get("cam") or []) + [c for c in (m.get("cam") or []) if c not in (d.get("cam") or [])]
    return d


def join_parks(items, now=None):
    """One park per stay: parks that follow one another at one place are a single park, and a re-park (the car shuffled a few metres) inside a stay does not split it.
    The newest park has no known end (nothing came after it), so it is marked `ongoing` and runs to now. Items come and go newest first."""
    asc = sorted(items, key=lambda x: (x["t0"], x["t1"]))
    out = []
    for x in asc:
        prev = out[-1] if out else None
        if prev and prev["kind"] in PARKISH and x["kind"] in PARKISH and x["t0"] - prev["t1"] <= JOIN_GAP_S and _at(prev, x):
            prev["t1"] = max(prev["t1"], x["t1"])
            prev["p1"] = x.get("p1") or prev.get("p1")
            prev["p0"] = prev.get("p0") or x.get("p0")
            prev["kind"] = "parked" if "parked" in (prev["kind"], x["kind"]) else "stopped"
            for k in ("kwh", "sentry"):
                if x.get(k) is not None:
                    prev[k] = round((prev.get(k) or 0) + x[k], 2) if k == "kwh" else max(prev.get(k) or 0, x[k])
            for k in ("n", "recent", "saved", "read"):
                if x.get(k) is not None:
                    prev[k] = (prev.get(k) or 0) + x[k]
            if x.get("src") == "tessie" or prev.get("src") == "tessie":
                prev["src"] = "tessie"
            continue
        out.append(dict(x))
    # a re-park between two parks at one place (or after one) belongs to the stay
    res, carry = [], None
    for i, x in enumerate(out):
        prev = res[-1] if res else None
        nxt = out[i + 1] if i + 1 < len(out) else None
        if carry:                                       # the re-park just before this park (the car shuffled on arriving) is where this stay began
            x["t0"] = min(x["t0"], carry["t0"])
            x["p0"] = carry.get("p0") or x.get("p0")
            x["moves"] = [carry["id"]] + (x.get("moves") or [])
            carry = None
        elif (not (prev and prev["kind"] in PARKISH) and _is_repark(x) and nxt and nxt["kind"] in PARKISH and nxt["t0"] - x["t1"] <= JOIN_GAP_S
              and _at(x, nxt)):
            carry = x
            continue
        if prev and prev["kind"] in PARKISH and _is_repark(x) and _at(prev, x) and (nxt is None or nxt["kind"] in PARKISH or x["t1"] - prev["t1"] <= JOIN_GAP_S):
            prev["t1"] = max(prev["t1"], x["t1"])
            prev["moves"] = (prev.get("moves") or []) + [x["id"]]
            continue
        res.append(x)
    # a short move right after a drive, at the place that drive ended, is the end of that drive; right before one, at the place it began, the start of it
    # (a few metres to the charger's bay): not a drive of its own
    mv = []
    for i, x in enumerate(res):
        prev = mv[-1] if mv else None
        nxt = res[i + 1] if i + 1 < len(res) else None
        if _is_repark(x):
            if prev and prev["kind"] == "drive" and not prev.get("short") and 0 <= x["t0"] - prev["t1"] <= JOIN_GAP_S and _at(prev, x, 150.0):
                _fold_move(prev, x, True)
                continue
            if nxt and nxt["kind"] == "drive" and not nxt.get("short") and 0 <= nxt["t0"] - x["t1"] <= JOIN_GAP_S and _at(x, nxt, 150.0):
                _fold_move(nxt, x, False)
                continue
        mv.append(x)
    res = mv
    # the absorbed re-park may now sit between two parks: join those as well
    fin = []
    for x in res:
        prev = fin[-1] if fin else None
        if prev and prev["kind"] in PARKISH and x["kind"] in PARKISH and x["t0"] - prev["t1"] <= JOIN_GAP_S and _at(prev, x):
            prev["t1"] = max(prev["t1"], x["t1"])
            prev["p1"] = x.get("p1") or prev.get("p1")
            prev["kind"] = "parked" if "parked" in (prev["kind"], x["kind"]) else "stopped"
            for k in ("n", "recent", "saved", "read"):
                if x.get(k) is not None:
                    prev[k] = (prev.get(k) or 0) + x[k]
            continue
        fin.append(x)
    for x in fin:
        TL.show(x)
    if fin and fin[-1]["kind"] in PARKISH:
        fin[-1]["ongoing"] = True                       # nothing is known after it: it is still going
    return sorted(fin, key=lambda x: (-x["t0"], -x["t1"]))


def missing(items, now, recent_days=None, tried=()):
    """What Tessie has not given yet: drives the dashcam saw that Tessie has no record of (a real move: a minute or 300 m), and Tessie drives with no detailed page (path file), of any age (`recent_days` limits it to the last few days).
    A Tessie drive that ended less than ten minutes ago is left out: `sync_api_tessie.py --backfill-deepdive` waits ten minutes (`DRIVE_FINALIZE_GRACE_SECONDS`) before asking Tessie for its path so a drive still settling is not saved half-done.
    -> dict(cam_only=n, no_page=n, ids=[...], since=earliest start or None). Short moves and re-parks do not count."""
    cam, nop, gone, gone_cam, gone_nop = [], [], 0, 0, 0
    for v in items:
        if v.get("kind") != "drive" or v.get("short"):
            continue
        want = None
        if v.get("src") == "cam":
            if (v["t1"] - v["t0"]) >= 60 or (v.get("km") or 0) >= 0.3:
                want = cam
        elif not v.get("page") and v["t1"] <= now - 600 and (recent_days is None or v["t0"] >= now - recent_days * 86400):
            want = nop
        if want is None:
            continue
        if v["id"] in tried:
            gone += 1                           # a sync already ran with this one missing: Tessie has nothing more for it
            if want is cam:
                gone_cam += 1                   # the dashcam saw it and Tessie has no drive for it
            else:
                gone_nop += 1                   # Tessie has the drive but gave no route for it
        else:
            want.append(v)
    both = cam + nop
    return dict(cam_only=len(cam), no_page=len(nop), tried=gone, tried_cam=gone_cam, tried_no_page=gone_nop, ids=[v["id"] for v in both[:500]], since=min((v["t0"] for v in both), default=None))


class Cached:
    """The merged list, kept until one of the files it is built from changes (the build takes seconds). When something changed the page does not wait for the rebuild: it gets
    the previous list (marked stale) at once and the rebuild runs in the background. A copy of the last list is kept in `path` (a folder on this computer, not in the data
    folder), so even the first page after the app starts has something to show."""

    def __init__(self, path=None):
        self._sig, self._val, self._stale, self._busy, self._path = None, None, False, False, path
        self._lock = threading.Lock()                       # the fields
        self._build_lock = threading.Lock()                 # one build at a time
        if path:
            try:
                import json
                with open(path, encoding="utf-8") as f:
                    self._val = json.load(f)
                self._stale = True                          # from an earlier run: shown until the rebuild replaces it
            except (OSError, ValueError):
                self._val = None

    def _save(self, val):
        if not self._path:
            return
        try:
            import json
            import os
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(val, f)
            os.replace(tmp, self._path)
        except (OSError, TypeError, ValueError):
            pass

    def _build(self, sig, build):
        try:
            with self._build_lock:
                with self._lock:
                    if sig == self._sig and not self._stale and self._val is not None:
                        return self._val
                val = build()
                with self._lock:
                    self._val, self._sig, self._stale = val, sig, False
                self._save(val)
                return val
        finally:
            with self._lock:
                self._busy = False

    def has(self):
        """True when a list is kept (built, or from an earlier run)."""
        with self._lock:
            return self._val is not None

    def get(self, sig, build, wait=False):
        """A current list. Without wait, a stale one (marked) is returned at once while the rebuild runs; with nothing kept yet, or wait=True, it waits for the build."""
        with self._lock:
            val = self._val
            if val is not None and not self._stale and sig == self._sig:
                return val
            start = val is not None and not wait and not self._busy
            if start:
                self._busy = True
        if val is None or wait:
            return self._build(sig, build)
        if start:
            threading.Thread(target=self._build, args=(sig, build), daemon=True).start()
        return dict(val, stale=True)


def page_rows(act):
    """The rows of the activity list the Drives page also draws from (Tessie's drives and charges, as /api/activity gives them), so the page needs one request:
    dict(tess=[drive rows], chg=[charge rows])."""
    return dict(tess=[x for x in act if x.get("k") == "drive"], chg=[x for x in act if x.get("k") == "charge"])


VERSION = 8                                         # bump when merge() or what the list carries changes: a list kept from the older code is then built again


def signature(paths):
    """What changed: the size and modification time of each file or folder (a missing one counts as nothing), and the version of the merge."""
    import os
    out = [("version", VERSION, None)]
    for p in paths:
        try:
            st = os.stat(p)
            out.append((p, st.st_mtime, st.st_size))
        except OSError:
            out.append((p, None, None))
    return tuple(out)
