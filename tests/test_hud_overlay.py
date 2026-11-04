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

    # a sign the car did not answer (a side road's or an exit's limit seen from the main road): a 60 read while the car holds 99-109 km/h
    def test_a_60_read_at_motorway_speed_that_the_car_ignores_is_doubtful(self):
        self.assertTrue(run("hudDoubt(60,k=>99)"))                                  # 13:12 on 11 Oct: 60 read, 99 before and after
        self.assertTrue(run("hudDoubt(60,k=>k<0?109:108)"))                         # 13:24: 109 before, 106-109 after
        self.assertTrue(run("hudDoubt(60,k=>k<0?98:93)"))                           # eases a little, not by a tenth

    def test_a_sign_the_car_slows_for_is_not_doubtful(self):
        self.assertFalse(run("hudDoubt(40,k=>k<0?71:(k>5?26:50))"))                 # 16:32: 71 before, down to 26 after
        self.assertFalse(run("hudDoubt(60,k=>k<0?78:65)"))                          # 78 to 65: slowing for it
        self.assertFalse(run("hudDoubt(60,k=>60)"))                                 # the car is at the limit already
        self.assertFalse(run("hudDoubt(80,k=>99)"))                                 # 80 against 99 is not a quarter below
        self.assertFalse(run("hudDoubt(30,k=>40)"))                                 # too slow a car to say

    def test_not_enough_speed_known_is_no_answer(self):
        self.assertIsNone(run("hudDoubt(60,k=>null)"))
        self.assertIsNone(run("hudDoubt(60,k=>k<0?99:null)"))

    def test_the_limit_held_skips_a_sign_the_car_did_not_answer(self):
        sg = '{"a":[{"text":"100","kind":"","s0":10},{"text":"60","kind":"","s0":20}]}'
        self.assertEqual(run('hudLimit(%s,["a"],0,30,(st,g)=>g.text==="60")' % sg), 100)    # the 60 was a side road: still 100
        self.assertEqual(run('hudLimit(%s,["a"],0,30,(st,g)=>false)' % sg), 60)
        self.assertEqual(run('hudLimit(%s,["a"],0,30)' % sg), 60)                          # no way to tell: the sign is taken
        self.assertEqual(run('hudLimit(%s,["a"],0,30,(st,g)=>null)' % sg), 60)             # not enough speed read yet

    # school zones: the plate (kind school, hours in g.times), the zone's limit sign read within 8 s of it, and whether it is a school day and inside the hours
    SZ = ('{"m1":[{"text":"60","kind":"","s0":10},{"text":"School zone","kind":"school","s0":30,"times":["08:00-09:30","14:30-16:00"]},{"text":"40","kind":"","s0":32}],'
          '"m2":[{"text":"60","kind":"","s0":20}]}')
    CALL = '(i,g)=>i*60+g.s0'

    def school(self, t, minute, sd, si=0, sz=None):
        return run('hudSchool(%s,["m1","m2"],%d,%s,%s,{min:%d},()=>%s)' % (sz or self.SZ, si, t, self.CALL, minute, sd))

    def test_in_a_school_zone_on_a_school_day_inside_the_hours_the_state_is_on(self):
        z = self.school(40, 8 * 60 + 54, "true")
        self.assertEqual((z["state"], z["lim"]), ("on", 40))

    def test_outside_the_hours_or_on_a_day_off_the_zone_is_off(self):
        self.assertEqual(self.school(40, 11 * 60, "true")["state"], "off")                  # 11:00: between the two ranges
        self.assertEqual(self.school(40, 9 * 60 + 30, "true")["state"], "off")              # the range ends at 09:30
        self.assertEqual(self.school(40, 8 * 60, "true")["state"], "on")                    # and starts at 08:00
        self.assertEqual(self.school(40, 8 * 60 + 54, "false")["state"], "off")             # a weekend, a holiday or the school holidays
        self.assertEqual(self.school(40, 15 * 60, "true")["state"], "on")                   # the afternoon range

    def test_unknown_hours_or_an_unknown_calendar_is_not_a_guess(self):
        self.assertEqual(self.school(40, 8 * 60 + 54, "null")["state"], "unknown")          # no calendar for that state or year
        self.assertEqual(self.school(40, 8 * 60 + 54, "undefined")["state"], "unknown")     # the answer is not in yet
        nohrs = self.SZ.replace('"times":["08:00-09:30","14:30-16:00"]', '"times":[]')
        self.assertEqual(self.school(40, 8 * 60 + 54, "true", sz=nohrs)["state"], "unknown")

    def test_not_in_a_zone_before_the_plate_after_it_ends_or_a_new_limit(self):
        self.assertIsNone(self.school(20, 8 * 60 + 54, "true"))                              # the plate (30 s) is ahead
        self.assertIsNone(run('hudSchool(%s,["m1","m2"],1,40,%s,{min:534},()=>true)' % (self.SZ, self.CALL)))   # 60+40 s: a 60 sign (80 s) ended the zone
        self.assertEqual(self.school(10, 534, "true", si=1)["state"], "on")                    # the 60 of m2 (at 20 s) is not read yet: still in the zone

    def test_a_zone_lasts_150_seconds(self):
        sz = '{"m1":[{"text":"School zone","kind":"school","s0":30,"times":["08:00-09:30"]},{"text":"40","kind":"","s0":32}],"m2":[]}'
        self.assertEqual(self.school(0, 534, "true", si=1, sz=sz)["state"], "on")             # 30 s after the plate's minute ended
        late = '{"m1":[{"text":"School zone","kind":"school","s0":30,"times":["08:00-09:30"]}],"m2":[],"m3":[],"m4":[]}'
        self.assertIsNone(run('hudSchool(%s,["m1","m2","m3","m4"],3,5,%s,{min:534},()=>true)' % (late, self.CALL)))   # 3 minutes on

    def test_outside_the_hours_the_zones_own_limit_is_skipped(self):
        st = '["m1","m2"]'
        self.assertEqual(run('hudLimit(%s,%s,0,35,null,g=>g.text==="40")' % (self.SZ, st)), 60)    # the plate's 40 is not held: the 60 before it
        self.assertEqual(run('hudLimit(%s,%s,0,35)' % (self.SZ, st)), 40)

    def test_the_sign_stays_hidden_until_one_is_seen(self):
        with open(APP_CSS, encoding="utf-8") as f:
            css = f.read()
        self.assertIn(".hud [hidden]{display:none}", css)                           # the display of the sign would otherwise win over hidden: an empty red ring
        self.assertIn(".hud .hsg.sch", css)                                          # the yellow ring of an active school zone

    def test_style_for_every_part(self):
        with open(APP_CSS, encoding="utf-8") as f:
            css = f.read()
        for sel in (".hud .hsg", ".hud .hsp", ".hud .hbk.on", "@keyframes hudfl"):
            self.assertIn(sel, css)


if __name__ == "__main__":
    unittest.main()
