#!/usr/bin/env python3
"""test_drivelist_one_request.py - the Drives page needs one request: /api/drivelist carries Tessie's drives and charges (as /api/activity gives them), so the
page no longer asks for /api/activity as well. Built the old way (the page filters /api/activity) and the new way (the list's own rows) from the same made-up
activity, the rows the page draws from are the same.

    python3 -m unittest tests/test_drivelist_one_request.py
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

import sys  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import node_usable  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_drivelist as DL  # noqa: E402

ROOT = os.path.join(HERE, "..")
APP_JS = os.path.join(ROOT, "Tools", "lib", "app_ui", "app.js")
T = 1792000000


def activity():
    """A made-up /api/activity: drives (one with no ends named), charges (one with no position) and idle spells, in the mixed order the store gives."""
    out = []
    for i in range(6):
        d = dict(k="drive", id="d%d" % i, t0=T + i * 7200, t1=T + i * 7200 + 1500, min=25.0, km=12.5, frm="Home", to="Shops %d" % i, lat=-33.8, lon=151.2, lat1=-33.9,
                 lon1=151.3, repark=False, page=True, al=0, kwh=2.1, b0=80, b1=78)
        if i == 3:
            d.pop("frm")
            d["to"] = None
        out.append(d)
        out.append(dict(k="idle", id="i%d" % i, t0=T + i * 7200 + 1500, t1=T + i * 7200 + 3600, kwh=0.2, sentry=0))
        if i % 2:
            c = dict(k="charge", id="c%d" % i, t0=T + i * 7200 + 1600, t1=T + i * 7200 + 3400, min=30.0, place="Charger %d" % i, kind="supercharger" if i == 1 else "ac",
                     kwh=20.5, b0=40, b1=70, cost=7.5, lat=-33.9, lon=151.3)
            if i == 5:
                c.pop("lat")
                c.pop("lon")
            out.append(c)
    return out


def page_line():
    """The line of pageDriveList that picks the rows (new way, with the old way kept for a list kept by an older version)."""
    with open(APP_JS, encoding="utf-8") as f:
        src = f.read()
    body = src[src.index("async function pageDriveList(){"):]
    body = body[:body.index("\nasync function ", 10)]
    return body, next(ln.strip() for ln in body.splitlines() if ln.strip().startswith("const act=dl.tess"))


class OneRequest(unittest.TestCase):
    def test_rows_are_the_activity_rows_the_page_filtered(self):
        act = activity()
        old = dict(tess=[x for x in act if x["k"] == "drive"], chg=[x for x in act if x["k"] == "charge"])       # the old page: act.filter(x=>x.k==="drive"|"charge")
        new = DL.page_rows(act)
        self.assertEqual(new, old)
        self.assertEqual(json.loads(json.dumps(new)), json.loads(json.dumps(old)))                              # and as the page receives them
        self.assertEqual(len(new["tess"]), 6)
        self.assertEqual(len(new["chg"]), 3)

    def test_page_asks_for_the_list_only(self):
        body, line = page_line()
        first = body[:body.index(line)]
        self.assertIn('tcGet("drivelist")', first)
        self.assertNotIn('api("activity")', first)                           # not asked with the list
        self.assertIn("tess,chg:chgs,", body)                                 # what the day lists draw from
        self.assertNotIn("act.filter(x=>x.k===\"drive\"),chg:act.filter", body)

    def test_server_puts_the_rows_in_the_list(self):
        with open(os.path.join(ROOT, "Tools", "tesla_app.py"), encoding="utf-8") as f:
            src = f.read()
        build = src[src.index("def drivelist(wait=False):"):src.index("def tessie_auto():")]
        self.assertRegex(build, r"\*\*app_drivelist\.page_rows\(act\)")
        self.assertGreaterEqual(DL.VERSION, 5)                               # a list kept by the older code (without the rows) is built again

    @unittest.skipUnless(node_usable.usable(), "node is not installed or does not start")
    def test_page_line_old_and_new_way_in_node(self):
        """Runs the page's own line in node: with the rows in the list (new way, no /api/activity request) and without them (a list kept by an older
        version: falls back to /api/activity), against the old way's filter."""
        _, line = page_line()
        act = activity()
        dl_new = dict(items=[], tessie=True, **DL.page_rows(act))
        dl_old = dict(items=[], tessie=True)
        js = """
const ACT=%s;let calls=0;const api=async p=>{if(p!=="activity")throw new Error(p);calls++;return ACT};
(async()=>{const out=[];for(const dl of [%s,%s]){calls=0;%s
  out.push({tess,chgs,calls})}
  const old={tess:ACT.filter(x=>x.k==="drive"),chgs:ACT.filter(x=>x.k==="charge")};
  console.log(JSON.stringify({out,old}))})();
""" % (json.dumps(act), json.dumps(dl_new), json.dumps(dl_old), line)
        r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        new, fallback = got["out"]
        self.assertEqual(new["calls"], 0)                                     # one request: the list only
        self.assertEqual(fallback["calls"], 1)
        for way in (new, fallback):
            self.assertEqual(way["tess"], got["old"]["tess"])
            self.assertEqual(way["chgs"], got["old"]["chgs"])
        self.assertEqual(len(new["tess"]), 6)


if __name__ == "__main__":
    unittest.main()
