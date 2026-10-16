"""app_footage.py - the footage log and the one-second stills, for the app (the same work as teslacam_ledger.py and teslacam_stills.py).

    GET  footage/log                the log as it stands (per drive, deleted, to import, purgeable) and the running job
    GET  footage/stills?roles=A,J   the front clips on those drives against the stills kept (nothing is made)
    POST footage {action: log|stills|stop, roles: [...]}   update the log from the drives / make the missing stills (a background job)
    GET  /still/<clip stamp>/<n>.jpg                        one kept still
    GET  clipstatus?stamps=a,b                              for each clip: kept on a drive / deleted (and when) / never logged

Only the drives picked are read (AGENTS.md 3.5.1). Nothing on a drive is changed.
"""
import threading
import time

import app_config
import teslacam_ledger as TLG
import teslacam_stills as ST


class Footage:
    def __init__(self, data_dir):
        self.dir = data_dir
        self.ledger = TLG.Ledger(data_dir)
        self.stills = ST.Stills(data_dir, app_config.get("stills.width", 400), app_config.get("stills.quality", 6))
        self.t = None
        self.stop_flag = False
        self.job = dict(running=False, kind="", done=0, total=0, current="", error="", t0=0, note="")
        self._todo = []

    # ---- reading ---------------------------------------------------------------------------------------------------
    def status(self):
        s = dict(self.job)
        s["running"] = bool(self.t and self.t.is_alive())
        return s

    def log(self):
        self.ledger = TLG.Ledger(self.dir)              # fresh: the files may have changed since (another run)
        s = self.ledger.summary()
        s["job"] = self.status()
        return s

    def stills_report(self, roots):
        self.stills.width = app_config.get("stills.width", 400)
        self.stills.quality = app_config.get("stills.quality", 6)
        rep = ST.plan(self.stills, {r: p for r, p in roots.items() if p})
        self._todo = rep.pop("todo")
        rep["to_make"] = len(self._todo)
        rep["job"] = self.status()
        rep["ffmpeg"] = ST.have_ffmpeg()
        return rep

    def clip_status(self, stamps):
        led = TLG.Ledger(self.dir)
        out = led.status(stamps)
        for st, d in out.items():
            d["stills"] = self.stills.has(st)
        return out

    def still(self, stamp, n):
        return self.stills.get(stamp, n)

    # ---- jobs ------------------------------------------------------------------------------------------------------
    def start(self, action, roots):
        if self.t and self.t.is_alive():
            return self.status()
        self.stop_flag = False
        if action == "stills":
            if not self._todo:
                self.stills_report(roots)
            self.job = dict(running=True, kind="stills", done=0, total=len(self._todo), current="", error="", t0=time.time(), note="")
            self.t = threading.Thread(target=self._run_stills, args=(list(self._todo),), daemon=True)
        else:
            self.job = dict(running=True, kind="log", done=0, total=len([p for p in roots.values() if p]), current="", error="", t0=time.time(), note="")
            self.t = threading.Thread(target=self._run_log, args=({r: p for r, p in roots.items() if p},), daemon=True)
        self.t.start()
        return self.status()

    def stop(self):
        self.stop_flag = True
        return self.status()

    def _run_log(self, roots):
        notes = []
        try:
            for role in sorted(roots, key=lambda r: "AJU?".index(r) if r in "AJU?" else 9):
                if self.stop_flag:
                    break
                self.job["current"] = role
                led = TLG.Ledger(self.dir)
                res = led.scan(role, roots[role])
                if res["note"]:
                    notes.append(res["note"])
                self.job["done"] += 1
        except Exception as e:                           # a drive pulled out: say so
            self.job["error"] = "%s: %s" % (type(e).__name__, e)
        self.job["note"] = " ".join(notes)

    def _run_stills(self, todo):
        def prog(n, stamp):
            self.job["done"], self.job["current"] = n, stamp
        try:
            ST.make_many(self.stills, todo, app_config.get("stills.workers", 3), stop=lambda: self.stop_flag, progress=prog)
        except Exception as e:
            self.job["error"] = "%s: %s" % (type(e).__name__, e)
        self._todo = []
