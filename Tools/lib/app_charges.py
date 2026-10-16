#!/usr/bin/env python3
"""app_charges.py - the charge book behind the Charges page (REQ-207).

The Tesla app shows what ./analyse_charging.py shows, in an easy page: every charge reconciled against its
invoice, a plug-in that Tessie split into several records shown ONCE with the records as sub-entries, the invoice
(number, date, kWh, rate, GST, total, file) and whether Tessie already holds the invoice cost, the losses, the
expected tariff, the telemetry summary and the charging curves.

The numbers come from analyse_charging.ChargingAnalyzer itself (same matching, same plug-in grouping), run
quietly and read-only; nothing in the data folder or in Tessie is changed. The reconcile takes ~20 s, so the
result is cached in <data>/charges/app_chargebook.json and rebuilt in the background when its inputs change
(charges_master.csv, deepdive telemetry, invoice files, Tessie caches); until then the previous book is served.

    ChargeBook(tools_dir, data_dir).get()          -> {"status": "ready|building|error", "book": {...}}
    ChargeBook(...).detail(id, part=None)           -> one plug-in with telemetry series and curves
    ChargeBook(...).invoice_file(name)              -> absolute path of an invoice PDF/CSV (only inside the invoice folders)
"""
import contextlib
import csv
import datetime
import glob
import io
import json
import os
import sys
import threading
import time

VERSION = 1


def _num(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _r(v, n=2):
    return None if v is None else round(v, n)


class ChargeBook:
    def __init__(self, tools_dir, data_dir):
        self.tools = tools_dir
        self.data = data_dir
        self.cache_path = os.path.join(data_dir, "charges", "app_chargebook.json")
        self.lock = threading.Lock()
        self.thread = None
        self.err = ""
        self.book = None
        self.pool = None
        self.pool_sig = None
        self.curve_memo = {}

    # ---- inputs signature -------------------------------------------------------------------------------------------
    def _repo(self):
        return os.path.dirname(self.tools)

    def _inputs(self):
        p = lambda *a: os.path.join(self.data, *a)
        files = [p("charges", "charges_master.csv"), os.path.join(self._repo(), "resources", "api_cache", "charges.json"),
                 os.path.join(self._repo(), "resources", "api_cache", "tessie_cost_push_ledger.json")]
        sig = []
        for f in files:
            try:
                st = os.stat(f)
                sig.append("%s:%d:%d" % (os.path.basename(f), st.st_size, int(st.st_mtime)))
            except OSError:
                sig.append(os.path.basename(f) + ":-")
        dd = p("charges", "deepdive")
        try:
            names = [n for n in os.listdir(dd) if n.startswith("charge_deepdive_") and n.endswith(".csv")]
            sig.append("dd:%d:%d" % (len(names), int(os.stat(dd).st_mtime)))
        except OSError:
            sig.append("dd:-")
        for d in self._invoice_dirs_cfg():
            try:
                names = os.listdir(d)
                sig.append("inv:%d:%d" % (len(names), int(os.stat(d).st_mtime)))
            except OSError:
                sig.append("inv:-")
        return "|".join(sig)

    def _invoice_dirs_cfg(self):
        try:
            if self.book and self.book.get("invoice_dirs"):
                return self.book["invoice_dirs"]
        except Exception:
            pass
        try:
            with open(os.path.join(self._repo(), "config.json"), encoding="utf-8") as f:
                d = (json.load(f).get("directories") or {}).get("invoices")
            return [os.path.expanduser(d)] if d else []
        except Exception:
            return []

    # ---- cache --------------------------------------------------------------------------------------------------------
    def _load_cache(self):
        if self.book is not None:
            return
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                b = json.load(f)
            if b.get("version") == VERSION:
                self.book = b
        except (OSError, ValueError):
            pass

    def get(self):
        """Serves the cached book at once and starts a background rebuild when it is missing or out of date."""
        with self.lock:
            self._load_cache()
            sig = self._inputs()
            stale = self.book is None or self.book.get("sig") != sig
            running = self.thread is not None and self.thread.is_alive()
            if stale and not running and self.tools and time.time() - getattr(self, "fail_t", 0) > 120:
                self.err = ""
                self.thread = threading.Thread(target=self._build_bg, args=(sig,), daemon=True)
                self.thread.start()
                running = True
            if self.book is None:
                return dict(status="error" if self.err and not running else "building", error=self.err, book=None)
            return dict(status="building" if running else "ready", error=self.err, book=self.book)

    def _build_bg(self, sig):
        try:
            book = self.build(sig)
            tmp = self.cache_path + ".tmp"
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(book, f)
            os.replace(tmp, self.cache_path)
            with self.lock:
                self.book = book
        except Exception as e:                          # keep serving the older book; try again in two minutes
            self.err = "%s: %s" % (type(e).__name__, e)
            self.fail_t = time.time()

    # ---- the build (analyse_charging, quiet and read-only) -----------------------------------------------------
    def _analyzer(self):
        if self.tools not in sys.path:
            sys.path.insert(0, self.tools)
        cwd = os.getcwd()
        os.chdir(self.tools)
        try:
            import analyse_charging as ac
            from lib import charge_cost_model as ccm
        finally:
            os.chdir(cwd)
        return ac, ccm

    def build(self, sig=None):
        ac, ccm = self._analyzer()
        buf = io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.tools)
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                a = ac.ChargingAnalyzer(tessie_dir=self.data)
                # The app only reads. analyse_charging's load_charges() normally moves new Tessie CSVs out of the Downloads
                # (landing) folder into the data folder; the app must not do that, nor touch Downloads at all.
                a.landing_dir = ""
                a.auto_ingest_from_landing = lambda: 0
                a.load_charges()
                a.load_invoices()
                a.load_detailed_charges()
                S = a.reconcile(interactive=False) or a.reconciled_sessions
                S = a.reconciled_sessions
                groups = {}
                for s in S:
                    if s.get("plugin_group_role") == "leader":
                        groups[id(s)] = [s] + a.plugin_followers(s, S)
                members_of = {id(m) for g in groups.values() for m in g[1:]}
                records, by_epoch = ccm.load_raw_tessie_cache(a.repo_root)
        finally:
            os.chdir(cwd)
        ledger = {}
        try:
            with open(os.path.join(a.repo_root, "resources", "api_cache", "tessie_cost_push_ledger.json"), encoding="utf-8") as f:
                ledger = json.load(f)
        except (OSError, ValueError):
            pass

        def part(s, multi=False):
            raw = s.get("raw_charge") or {}
            t0 = int(s.get("start_utc_epoch") or 0)
            rec = ccm.find_cache_record_by_epoch(by_epoch, t0)
            tid = rec.get("id") if rec else None
            held = ledger.get(str(tid)) if tid is not None else None
            target = s.get("invoice_cost")
            if target is None:
                st, why = "na", "no invoice"
            elif held is not None and abs(float(held) - target) < 0.005:
                st, why = "synced", "Tessie was sent $%.2f" % held
            elif rec and rec.get("cost") is not None and abs(float(rec["cost"]) - target) < 0.005:
                st, why = "synced", "Tessie already shows $%.2f" % rec["cost"]
            else:
                st, why = "pending", "Tessie shows %s, invoice share is $%.2f" % ("$%.2f" % rec["cost"] if rec and rec.get("cost") is not None else "no cost", target)
            tel = s.get("detailed_telemetry") or {}
            dt = s.get("datetime")
            return dict(
                id=str(t0), t0=t0, t1=int(s.get("end_utc_epoch") or t0), tz=s.get("timezone") or s.get("start_tz") or "", tzabbr=s.get("start_tz_abbr") or "",
                day=dt.strftime("%Y-%m-%d") if dt else "", hm=dt.strftime("%H:%M") if dt else "", min=s.get("duration_mins"),
                b0=s.get("start_soc"), b1=s.get("end_soc"), kwh=_r(s.get("battery_kwh")), disp=_r(s.get("tessie_car_kwh") if multi and s.get("tessie_car_kwh") else s.get("dispenser_kwh")), used=_r(s.get("tessie_car_kwh")),
                range=_r(s.get("range_added_km")), tcost=_r(s.get("tessie_cost")), trate=_r(s.get("tessie_rate"), 3), icost=_r(s.get("invoice_cost")),
                irate=_r(s.get("invoice_rate"), 3), peak=_r(ac.ChargingAnalyzer.session_peak_kw(s), 1), kmh=_r(ac.ChargingAnalyzer.session_range_kmh(s), 0),
                cable_loss=_r(s.get("cable_loss_kwh")), car_loss=_r(s.get("car_loss_kwh")), tessie_id=tid, tessie_cost_now=_r(rec.get("cost")) if rec else None,
                ledger=_r(float(held)) if held is not None else None, sync=st, sync_why=why,
                tel=dict(file=tel.get("source_file"), path=tel.get("source_path"), samples=tel.get("samples_count"), min=_r(_num(tel.get("duration_mins")), 1),
                         soc0=tel.get("soc_start"), soc1=tel.get("soc_end"), peak=_r(_num(tel.get("peak_power_kw")), 1), avg=_r(_num(tel.get("avg_power_kw")), 1),
                         t_start=_num(tel.get("initial_batt_temp_c")), t_end=_num(tel.get("final_batt_temp_c")), t_max=_num(tel.get("max_batt_temp_c")),
                         heater=tel.get("battery_heater"), outside=_num(tel.get("outside_temp_c")), inside=_num(tel.get("inside_temp_c")),
                         int_disp=_r(_num(tel.get("integrated_dispenser_kwh"))), int_pack=_r(_num(tel.get("integrated_pack_kwh"))),
                         d_energy=_r(_num(tel.get("delta_energy_remaining_kwh"))), ) if tel else None,
                has=bool(tel.get("source_path")) if tel else False)

        plugs = []
        for s in S:
            if id(s) in members_of:
                continue
            ms = groups.get(id(s), [s])
            parts = [part(m, len(ms) > 1) for m in ms]
            inv = s.get("matched_invoice") or {}
            icost = [p["icost"] for p in parts if p["icost"] is not None]
            tcost = sum((p["tcost"] or 0) for p in parts)
            bat = sum((p["kwh"] or 0) for p in parts)
            disp = s.get("invoice_disp_kwh") if (s.get("invoice_disp_kwh") and inv) else sum((p["disp"] or 0) for p in parts)
            syncs = {p["sync"] for p in parts}
            sync = "na" if syncs == {"na"} else "synced" if syncs <= {"synced", "na"} and "synced" in syncs else "pending"
            if sync == "pending" and "synced" in syncs:
                sync = "partial"
            first, last = parts[0], parts[-1]
            peaks = [p["peak"] for p in parts if p["peak"]]
            plug = dict(
                id=first["id"], t0=first["t0"], t1=last["t1"], tz=first["tz"], tzabbr=first["tzabbr"], day=first["day"], hm=first["hm"],
                span=round((last["t1"] - first["t0"]) / 60.0, 1), min=sum((p["min"] or 0) for p in parts), b0=first["b0"], b1=last["b1"],
                kwh=_r(bat), disp=_r(disp), eff=_r(100.0 * bat / disp, 1) if disp else None, loss=_r(disp - bat) if disp else None,
                range=_r(sum((p["range"] or 0) for p in parts), 1), peak=max(peaks) if peaks else None,
                kmh=_r((sum((p["range"] or 0) for p in parts) / (sum((p["min"] or 0) for p in parts) / 60.0)), 0) if sum((p["min"] or 0) for p in parts) > 0 else None,
                place=s.get("place_name") or "", network=s.get("network") or "", emoji=s.get("emoji") or "", status=s.get("status") or "",
                kind="supercharger" if s.get("is_supercharger") else "dc" if s.get("is_fast_charger") else "ac",
                home=("HOME" in (s.get("status") or "")), lat=(s.get("raw_charge") or {}).get("latitude"), lon=(s.get("raw_charge") or {}).get("longitude"),
                addr=(s.get("raw_charge") or {}).get("location_raw") or "",
                tcost=_r(tcost), icost=_r(sum(icost)) if icost else None, cost=_r(sum(icost)) if icost else _r(tcost),
                rate=s.get("invoice_rate") if s.get("invoice_rate") is not None else s.get("tessie_rate"),
                expected_rate=s.get("expected_rate"), schedule=s.get("expected_schedule_name"), theory=_r(s.get("theoretical_cost")), gst=_r(s.get("theoretical_gst")),
                sync=sync, invoice_only=bool(s.get("is_invoice_only_entry")), parts=parts,
                invoice=dict(number=inv.get("invoice_number"), file=inv.get("source_file"), date=str(inv.get("date") or "")[:10], network=inv.get("network"),
                             location=inv.get("location_raw"), kwh=_num(inv.get("energy_kwh")), rate=_num(inv.get("unit_rate")), gross_rate=_num(inv.get("gross_rate")),
                             total=_num(inv.get("total_cost")), gst=_num(inv.get("gst")), vin=inv.get("vin")) if inv else None)
            plug["rate"] = _r(plug["rate"], 3)
            plugs.append(plug)
        plugs.sort(key=lambda p: -p["t0"])
        by_num = {p["invoice"]["number"]: p["id"] for p in plugs if p["invoice"] and p["invoice"]["number"]}
        invs = []
        for i in a.invoices:
            invs.append(dict(number=i.get("invoice_number"), file=i.get("source_file"), date=str(i.get("date") or "")[:10], network=i.get("network"), location=i.get("location_raw"),
                             kwh=_num(i.get("energy_kwh")), rate=_num(i.get("unit_rate")), gross_rate=_num(i.get("gross_rate")), total=_num(i.get("total_cost")), gst=_num(i.get("gst")),
                             plug=by_num.get(i.get("invoice_number")), t=int(i["date"].timestamp()) if hasattr(i.get("date"), "timestamp") else 0))
        invs.sort(key=lambda i: -i["t"])
        dirs = []
        for d in getattr(a, "invoice_dirs", []) or []:
            if d not in dirs:
                dirs.append(d)
        return dict(version=VERSION, sig=sig or self._inputs(), built=int(time.time()), invoice_dirs=dirs, plugs=plugs, invoices=invs,
                    sync_ledger=len(ledger), tolerance_mins=getattr(a, "tolerance_mins", None))

    # ---- one plug-in with its telemetry ---------------------------------------------------------------------------------
    def invoice_file(self, name):
        name = os.path.basename(name or "")
        if not name:
            return None
        for d in self._invoice_dirs_cfg() + ((self.book or {}).get("invoice_dirs") or []):
            f = os.path.join(d, name)
            if os.path.isfile(f):
                return f
        return None

    @staticmethod
    def _tz_epoch(text, zone="Australia/Sydney"):
        """Deep-dive clock text is on Sydney's clock (the file header says AEDT / AEST)."""
        from zoneinfo import ZoneInfo
        try:
            d = datetime.datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo(zone))
            return int(d.timestamp())
        except (ValueError, TypeError):
            return None

    def _series(self, path, maxn=900):
        """One telemetry CSV as parallel lists (UTC epoch seconds). Current A: the AC charger current is 0 on a DC
        charger, so the pack current is used there (the pack current is what a DC charger actually pushes)."""
        out = dict(t=[], kw=[], pct=[], kwh=[], amps=[], pack_a=[], pack_v=[], volt=[], tmax=[], tmin=[], out=[], state=[], rate=[])
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        except OSError:
            return out
        if len(rows) > maxn:
            rows = [rows[int(i * (len(rows) - 1) / (maxn - 1))] for i in range(maxn)]
        ac_any = False
        for r in rows:
            ts = next((v for k, v in r.items() if k.startswith("Timestamp")), "")
            e = self._tz_epoch(ts)
            if e is None:
                continue
            out["t"].append(e)
            out["kw"].append(_num(r.get("Charger Power (kW)")))
            out["pct"].append(_num(r.get("Battery Level (%)")))
            out["kwh"].append(_num(r.get("Energy Added (kWh)")))
            ac = _num(r.get("Current (A)"))
            ac_any = ac_any or bool(ac)
            out["amps"].append(ac)
            out["pack_a"].append(_num(r.get("Pack Current (A)")))
            out["pack_v"].append(_num(r.get("Pack Voltage (V)")))
            out["volt"].append(_num(r.get("Charger Voltage (V)")))
            out["tmax"].append(_num(r.get("Max Battery Module Temp (°C)")))
            out["tmin"].append(_num(r.get("Min Battery Module Temp (°C)")))
            out["out"].append(_num(r.get("Outside Temp (°C)")))
            out["state"].append((r.get("Charging State") or "").strip())
            out["rate"].append(_num(r.get("Charge Rate (km/h)")))
        if not ac_any:
            out["amps"] = [abs(p) if (p is not None and (k or 0) > 0.5) else None for p, k in zip(out["pack_a"], out["kw"])]
            out["amps_src"] = "pack"
        else:
            out["amps_src"] = "charger"
        return out

    def _find(self, cid):
        b = (self.get() or {}).get("book")
        for p in (b or {}).get("plugs", []):
            if p["id"] == cid or any(x["id"] == cid for x in p["parts"]):
                return p
        return None

    def detail(self, cid, part=None):
        p = self._find(cid)
        if not p:
            return None
        if part is None and cid != p["id"]:
            part = cid
        segs = []
        for i, x in enumerate(p["parts"]):
            path = (x.get("tel") or {}).get("path")
            f = (x.get("tel") or {}).get("file")
            fp = os.path.join(path, f) if path and f and os.path.isdir(path) else (path if path and os.path.isfile(path) else None)
            if not fp or not os.path.isfile(fp):
                fp = os.path.join(self.data, "charges", "deepdive", f) if f else None
            s = self._series(fp) if fp and os.path.isfile(fp) else None
            if s is not None and s["t"]:
                s["part"] = x["id"]
                s["i"] = i
                s["file"] = f
                segs.append(s)
        return dict(plug=p, part=part, segments=segs, curves=self.curves(p, part))

    # ---- curves (lib/charge_curves: power vs SoC and the electrical profile, with dashed history) -------------------
    def _pool(self):
        dd = os.path.join(self.data, "charges", "deepdive")
        try:
            sig = (len(os.listdir(dd)), int(os.stat(dd).st_mtime))
        except OSError:
            return []
        if self.pool_sig != sig:
            if self.tools not in sys.path:
                sys.path.insert(0, self.tools)
            from lib import charge_curves as cc
            self.pool = cc.discover_deepdive_sessions(dd)
            self.pool_sig = sig
            self.curve_memo = {}
        return self.pool or []

    def curves(self, p, part=None):
        key = (p["id"], part)
        if key in self.curve_memo:
            return self.curve_memo[key]
        try:
            res = self._curves(p, part)
        except Exception as e:
            res = dict(error="%s: %s" % (type(e).__name__, e))
        self.curve_memo[key] = res
        return res

    def _curves(self, p, part):
        from lib import charge_curves as cc
        pool = self._pool()
        dd = os.path.join(self.data, "charges", "deepdive")
        files = [(x["id"], (x.get("tel") or {}).get("file")) for x in p["parts"]]
        paths = {}
        for pid, f in files:
            if f and os.path.join(dd, f) in {s["path"] for s in pool}:
                paths[pid] = os.path.join(dd, f)
        if not paths:
            return None
        by_path = {s["path"]: s for s in pool}
        chosen = [pid for pid in paths if part is None or pid == part] or list(paths)
        others = [pid for pid in paths if pid not in chosen]

        def merge(ids):
            pts = []
            for pid in ids:
                pts += by_path[paths[pid]]["points"]
            pts = sorted(pts, key=lambda q: q["t"])
            if not pts:
                return []
            t0 = datetime.datetime.strptime(pts[0]["t"][:19], "%Y-%m-%d %H:%M:%S")
            out = []
            for q in pts:
                q = dict(q)
                q["elapsed_min"] = (datetime.datetime.strptime(q["t"][:19], "%Y-%m-%d %H:%M:%S") - t0).total_seconds() / 60.0
                out.append(q)
            return out

        first = by_path[paths[chosen[0]]]
        cur = dict(path=first["path"], slug=first["slug"], points=merge(chosen))
        used = {paths[i] for i in chosen}
        reg = cc.load_charger_registries()
        hw_name, hw = cc.match_hardware(cur["slug"], reg)
        history_pool = [s for s in pool if s["path"] not in {paths[i] for i in paths}]
        hist = cc.historical_extension(history_pool + [cur], cur, reg)
        wr = cc.estimate_window_minutes(history_pool + [cur], cur, reg)
        win = cc.window_line(wr)
        winfo = dict(measured=wr["measured"], minutes=wr["minutes"], text=wr["text"], **(wr.get("info") or {}))
        pts = [q for q in cur["points"] if q["soc"] is not None]
        pw = [q for q in pts if q["power_kw"] is not None]
        curve = [dict(name="Charger power", unit="kW", axis="power", dash=False, x=[q["soc"] for q in pw], y=[q["power_kw"] for q in pw])]
        if others:
            op = merge(others)
            op = [q for q in op if q["soc"] is not None and q["power_kw"] is not None]
            if op:
                curve.append(dict(name="Rest of this charge", unit="kW", axis="power", dash=True, x=[q["soc"] for q in op], y=[q["power_kw"] for q in op]))
        for seg in ("before", "after"):
            sg = ((hist or {}).get("power_kw") or {}).get(seg) or []
            if sg:
                curve.append(dict(name="Estimated from other sessions", unit="kW", axis="power", dash=True, x=[a for a, _ in sg], y=[b for _, b in sg]))
        tp = [q for q in pts if q.get("temp_c") is not None]
        if len(tp) >= 2:
            curve.append(dict(name="Pack temperature", unit="°C", axis="temp", dash=False, x=[q["soc"] for q in tp], y=[q["temp_c"] for q in tp]))
        prof = []
        ps = sorted(pts, key=lambda q: q["elapsed_min"])
        prof.append(dict(name="Pack voltage", unit="V", axis="volt", dash=False, x=[q["elapsed_min"] for q in ps], y=[q["voltage_v"] for q in ps]))
        prof.append(dict(name="Pack current", unit="A", axis="amp", dash=False, x=[q["elapsed_min"] for q in ps], y=[abs(q["current_a"]) if q["current_a"] is not None else None for q in ps]))
        peak = max((q["power_kw"] for q in pw), default=None)
        return dict(curve=curve, profile=prof, caption=cc._history_caption(hist), window=win, winfo=winfo, hardware=hw_name, tier=(hw or {}).get("tier"), cap=(hw or {}).get("max_power_kw"),
                    stalls=(hw or {}).get("stalls"), peak=peak, slug=cur["slug"], home=cur["slug"] == "Evnex_Home")
