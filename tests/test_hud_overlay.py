#!/usr/bin/env python3
"""test_hud_overlay.py - the overlay on a drive's front camera (speed limit sign, speed, indicators): the page's own helpers are run in node with made-up readings.

    python3 -m unittest tests/test_hud_overlay.py
"""
import json
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP_JS = os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js")
APP_CSS = os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.css")


def helpers():
    with open(APP_JS, encoding="utf-8") as f:
        src = f.read()
    return src[src.index("// HUD begin"):src.index("// HUD end")]


def run(expr):
    js = helpers() + "\nconsole.log(JSON.stringify(%s))" % expr
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


SGV = ('{"m1":[{"text":"60","kind":"","s0":10,"s1":12},{"text":"80","kind":"","s0":40,"s1":42}],'
       '"m2":[{"text":"40","kind":"school","s0":5,"s1":6},{"text":"100","kind":"","s0":30,"s1":31}],"m3":[]}')


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class HudTest(unittest.TestCase):
    def test_limit_is_the_latest_sign_read_so_far(self):
        st = '["m1","m2","m3"]'
        self.assertIsNone(run("hudLimit(%s,%s,0,5)" % (SGV, st)))                  # nothing read yet
        self.assertEqual(run("hudLimit(%s,%s,0,15)" % (SGV, st)), 60)
        self.assertEqual(run("hudLimit(%s,%s,0,50)" % (SGV, st)), 80)              # the later sign of that minute
        self.assertEqual(run("hudLimit(%s,%s,2,0)" % (SGV, st)), 100)             # held into the next minutes, until a new sign

    def test_a_school_zone_sign_is_not_a_limit_to_hold(self):
        self.assertEqual(run('hudLimit(%s,["m1","m2"],1,10)' % SGV), 80)           # not the 40 of the school sign read at 5 s
        self.assertEqual(run('hudLimit(%s,["m1","m2"],1,35)' % SGV), 100)

    def test_a_sign_a_moment_ahead_is_not_shown_early_but_the_half_second_slack_is(self):
        self.assertIsNone(run('hudLimit({"a":[{"text":"50","kind":"","s0":10}]},["a"],0,9)'))
        self.assertEqual(run('hudLimit({"a":[{"text":"50","kind":"","s0":10}]},["a"],0,9.6)'), 50)

    def test_indicator_bits(self):
        self.assertEqual(run("[hudBlink(0),hudBlink(1),hudBlink(2),hudBlink(3)]"), [dict(l=False, r=False), dict(l=True, r=False), dict(l=False, r=True), dict(l=True, r=True)])

    def test_overlay_sits_on_the_picture_not_on_the_bars(self):
        wide = run("hudBox(1100,800,2896,1876)")                                  # a tall box: bars above and below
        self.assertAlmostEqual(wide["w"], 1100)
        self.assertAlmostEqual(wide["h"], 1100 * 1876 / 2896)
        self.assertAlmostEqual(wide["y"], (800 - wide["h"]) / 2)
        narrow = run("hudBox(2000,600,2896,1876)")                                # a short box: bars at the sides
        self.assertAlmostEqual(narrow["h"], 600)
        self.assertAlmostEqual(narrow["x"], (2000 - narrow["w"]) / 2)
        self.assertEqual(run("hudBox(1000,800,0,0)")["w"], 1000)                  # video size not known yet: the usual shape

    def test_style_for_every_part(self):
        with open(APP_CSS, encoding="utf-8") as f:
            css = f.read()
        for sel in (".hud .hsg", ".hud .hsp", ".hud .hbk.on", "@keyframes hudfl"):
            self.assertIn(sel, css)


if __name__ == "__main__":
    unittest.main()
