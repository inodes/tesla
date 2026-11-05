#!/usr/bin/env python3
"""perf_run.py - times the app's start-up and every /api list against a fixture folder (make_perf_fixture.py).

    perf_run.py FIXTURE [--timeout S] [--no-background] [--json OUT]

Two starts of tesla_app.py --no-window, both with HOME=FIXTURE/home, TESLA_CONFIG=FIXTURE/config.json and the data folder FIXTURE/data, so nothing
outside FIXTURE is read or written:

    first start   with the computer's cache folder (FIXTURE/home/.cache) emptied: what a first launch pays
    restart       the same again, keeping that cache: what every later launch pays

For each: seconds from the process start to the first answered request; with Playwright installed, each route in --pages opened in headless
Chromium (first contentful paint, and when the page has rendered); then every endpoint asked twice (cold, then warm), with its size.
A request that takes longer than --timeout is shown as '>N'. The fixture is changed by the run (the app keeps its lists and logs there), so
make a fresh one for numbers to compare.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "tesla_app.py")
ENDPOINTS = ["status", "settings", "summary", "activity", "drivelist", "places", "alerts", "chargers", "battery", "analytics",
             "teslacam", "clips", "clips/keep", "keeper", "footage/todo", "review/scanned", "drivereview/all", "clipclock", "sentry", "tessie"]
PAGES = ["/", "/app.js", "/app.css"]


def fetch(base, path, timeout):
    t = time.time()
    try:
        with urllib.request.urlopen(base + path, timeout=timeout) as r:
            body = r.read()
            return time.time() - t, len(body), r.status
    except urllib.error.HTTPError as e:
        return time.time() - t, 0, e.code
    except Exception:
        return None, 0, None


def one_start(fx, timeout, background, pages=()):
    env = dict(os.environ, HOME=os.path.join(fx, "home"), TESLA_CONFIG=os.path.join(fx, "config.json"), TESLA_DATA_DIR=os.path.join(fx, "data"), PYTHONUNBUFFERED="1")
    cmd = [sys.executable, APP, "--no-window", "--port", "0", "--data-dir", os.path.join(fx, "data")] + ([] if background else ["--no-background"])
    t0 = time.time()
    p = subprocess.Popen(cmd, cwd=os.path.dirname(APP), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base, out = None, {}
    try:
        while time.time() - t0 < 120:
            line = p.stdout.readline()
            if not line:
                break
            m = re.search(r"(http://127\.0\.0\.1:\d+)/", line)
            if m:
                base = m.group(1)
                out["bound_s"] = time.time() - t0
                break
        if not base:
            raise RuntimeError("the server did not start")
        while time.time() - t0 < 120:
            dt, _n, code = fetch(base, "/api/status", 5)
            if code == 200:
                out["first_answer_s"] = time.time() - t0
                break
            time.sleep(0.05)
        if pages:
            out["pages"] = page_times(base, pages)
        rows = []
        for path in PAGES + ["/api/" + e for e in ENDPOINTS]:
            cold = fetch(base, path, timeout)
            warm = fetch(base, path, timeout) if cold[0] is not None else (None, 0, None)
            rows.append(dict(path=path, cold_s=cold[0], warm_s=warm[0], bytes=cold[1] or warm[1], code=cold[2]))
        out["rows"] = rows
    finally:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
    return out


def page_times(base, routes):
    """{route: dict(fcp_ms, rendered_ms)} in headless Chromium (Playwright): first contentful paint, and when #main no longer shows 'Loading…'.
    Each route in a new page with an empty cache (a first look after launch)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {}
    out = {}
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception:                       # no headless shell for this Playwright: the full browser, where it is kept
            exe = next((x for x in (os.environ.get("PERF_CHROMIUM"), "/opt/pw-browsers/chromium") if x and os.path.exists(x)), None)
            if not exe:
                return {}
            b = pw.chromium.launch(executable_path=exe)
        for r in routes:
            ctx = b.new_context()
            pg = ctx.new_page()
            t = time.time()
            try:
                pg.goto(base + "/" + r, wait_until="commit", timeout=60000)
                pg.wait_for_function("() => { const m = document.querySelector('#main'); return m && !/Loading/.test(m.textContent) && m.children.length > 0 }", timeout=60000)
                done = (time.time() - t) * 1000
                fcp = pg.evaluate("() => { const e = performance.getEntriesByName('first-contentful-paint')[0]; return e ? e.startTime : null }")
                out[r] = dict(fcp_ms=fcp, rendered_ms=done)
            except Exception as e:
                out[r] = dict(fcp_ms=None, rendered_ms=None, error=str(e)[:80])
            ctx.close()
        b.close()
    return out


def fmt(v, timeout):
    return ">%d" % timeout if v is None else "%.3f" % v


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fixture")
    ap.add_argument("--timeout", type=float, default=60, help="seconds to wait for one request (default 60)")
    ap.add_argument("--no-background", action="store_true", help="start the app without its background reading of the drives")
    ap.add_argument("--json", help="also write the numbers to this file")
    ap.add_argument("--pages", default="#/activity,#/drives,#/places,#/teslacam", help="routes to open in headless Chromium after each start (empty: none)")
    a = ap.parse_args()
    fx = os.path.realpath(a.fixture)
    shutil.rmtree(os.path.join(fx, "home", ".cache"), ignore_errors=True)
    res = {}
    for name in ("first start", "restart"):
        res[name] = one_start(fx, a.timeout, not a.no_background, [x for x in a.pages.split(",") if x])
    for name, r in res.items():
        print("\n## %s: bound %.2f s, first answer %.2f s\n" % (name, r.get("bound_s", -1), r.get("first_answer_s", -1)))
        for route, v in (r.get("pages") or {}).items():
            print("page %s: first paint %s ms, rendered %s ms%s" % (route, "%.0f" % v["fcp_ms"] if v.get("fcp_ms") is not None else "-",
                                                                  "%.0f" % v["rendered_ms"] if v.get("rendered_ms") is not None else "-", (" (" + v["error"] + ")") if v.get("error") else ""))
        print("| path | cold s | warm s | bytes |\n|---|---|---|---|")
        for x in r["rows"]:
            print("| %s | %s | %s | %d |" % (x["path"], fmt(x["cold_s"], a.timeout), fmt(x["warm_s"], a.timeout), x["bytes"]))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
