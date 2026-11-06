#!/usr/bin/env python3
"""test_sign_picture_offroad.py - the drive page: a faster speed sign seen off our road is ignored (map, badges), and the picture of the sign being passed shows at the map's bottom left.
Checked as text (the page needs a browser).

    python3 -m unittest tests/test_sign_picture_offroad.py
"""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP_JS = os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js")
APP_CSS = os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.css")


class SignPictureTest(unittest.TestCase):
    def setUp(self):
        with open(APP_JS, encoding="utf-8") as f:
            self.js = f.read()

    def test_off_road_faster_sign_is_ignored(self):
        self.assertIn('off:dbt&&TR.get(g).why==="lat"', self.js)
        self.assertIn("+g.text>(pr!=null?+pr:sp)", self.js)      # only a higher speed than the one in force
        self.assertIn("g.text&&!asideSign(m.stamp,g)", self.js)      # off the map
        self.assertIn("!(o.hide&&o.hide(stamps[si],g))", self.js)      # off the badges beside the video
        self.assertIn("hide:asideSign", self.js)

    def test_overridden_signs_are_left_out_unless_asked_for(self):
        i = self.js.index("const asideSign=")
        self.assertIn('c.why==="ovr"||c.why==="spk")&&!SASIDE', self.js[i:i + 260])      # an overridden sign, and a spike, is set aside by default
        self.assertIn('let SASIDE=lsGet("tc.signaside","0")==="1"', self.js)      # and shown only when asked, remembered
        j = self.js.index("const sgRedraw=")
        blk = self.js[j:j + 3200]
        self.assertIn("!asideSign(x[0].stamp,x[1])", blk)      # not in the list ...
        self.assertIn("LV.length", blk)                         # ... nor in its count
        self.assertIn('id="dsas"', blk)                         # a button shows the set-aside ones
        self.assertIn("${qSign(SASIDE)}", blk)                  # a speed sign with a ? in it, crossed through while they are hidden (no words)
        self.assertNotIn("set aside</button>", blk)
        self.assertIn("${qSign(SASIDE)}</button>", blk)                 # no count beside it
        self.assertIn('stroke="#d62828" stroke-width="2.4"', self.js)    # the bar through it is red, like the ring
        self.assertNotIn("offRoad", self.js)

    def test_each_sign_is_shown_as_a_picture_with_what_was_worked_out(self):
        j = self.js.index("const gc=$(\"#dsgc\")")
        blk = self.js[j:j + 2400]
        self.assertIn('class="evc sgk', blk)
        self.assertIn("/review/${esc(g.f)}", blk)                      # the sign cut from the frame
        self.assertIn("m ${g.fx<.5?\"left\":\"right\"} of the car's line", blk)      # how far off the car's line it stood
        self.assertIn("no picture kept", blk)                          # said, not left blank, when the picture is gone
        self.assertIn('style="width:${MW}ch;max-width:100%"', self.js)    # the dropdown is always as wide as its longest label

    def test_picture_overlay(self):
        self.assertIn('id="dsgp"', self.js)
        self.assertIn("signPic(meta[st].off+sec)", self.js)      # follows the video
        self.assertIn("/review/${encodeURIComponent(best.f)}", self.js)      # the sign cut from the frame
        with open(APP_CSS, encoding="utf-8") as f:
            self.assertIn(".sgp{position:absolute;left:10px;bottom:10px", f.read())


if __name__ == "__main__":
    unittest.main()


class SignCamerasTest(unittest.TestCase):
    def test_speed_signs_are_read_from_the_front_camera_only(self):
        import re
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Tools", "lib")
        src = open(os.path.join(root, "app_review.py"), encoding="utf-8").read()
        self.assertIn('SIGN_CAMS = ("front",)', src)
        self.assertNotRegex(src, r"left_repeater|right_repeater|\bback\b.*\.mp4")


class SignWindowUiTest(unittest.TestCase):
    def js(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8") as f:
            return f.read()

    def test_the_camera_that_read_a_sign_is_ringed_red(self):
        js = self.js()
        self.assertIn("speed sign read here", js)
        self.assertIn('(!!sgCam&&b.dataset.c===sgCam)', js)            # the camera's button in the bar, as for a Sentry event
        self.assertIn('||"front"', js)                                  # a reading that does not name its camera was read on the front one

    def test_the_sign_window_hides_and_shows_like_the_telemetry(self):
        js = self.js()
        self.assertIn('data-gs="${k}"', js)
        self.assertIn('lsSet("tc.sgsz",SGSZ)', js)
        self.assertIn('TSZ.map(([k,t])=>`<button class="chip" data-gs', js)
