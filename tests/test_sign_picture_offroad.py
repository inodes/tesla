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
        self.assertIn("g.text&&!offRoad(m.stamp,g)", self.js)      # off the map
        self.assertIn("!(o.hide&&o.hide(stamps[si],g))", self.js)      # off the badges beside the video
        self.assertIn("hide:offRoad", self.js)

    def test_picture_overlay(self):
        self.assertIn('id="dsgp"', self.js)
        self.assertIn("signPic(meta[st].off+sec)", self.js)      # follows the video
        self.assertIn("/review/${encodeURIComponent(best.f)}", self.js)      # the sign cut from the frame
        with open(APP_CSS, encoding="utf-8") as f:
            self.assertIn(".sgp{position:absolute;left:10px;bottom:10px", f.read())


if __name__ == "__main__":
    unittest.main()
