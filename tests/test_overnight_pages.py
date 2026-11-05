#!/usr/bin/env python3
"""test_overnight_pages.py - a night is never a page: a park past midnight opens one day at a time, and no black minutes fill a hole longer than app.teslacam.park_end_s.
Checked as text (the page needs a browser).

    python3 -m unittest tests/test_overnight_pages.py
"""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP_JS = os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js")


class OvernightPagesTest(unittest.TestCase):
    def setUp(self):
        with open(APP_JS, encoding="utf-8") as f:
            self.js = f.read()

    def test_day_rows_open_their_own_day(self):
        self.assertIn("const dayPart=(v,d)=>", self.js)
        self.assertIn("byWin(dayPart(v,d))", self.js)
        self.assertIn("o.go(v,d)", self.js)      # the row knows its day
        self.assertIn(".map(v=>open(v))", self.js)      # Previous / Next walk whole items (map would pass an index as the day)

    def test_no_black_for_a_night(self):
        self.assertIn('const HOLE=(+cfg("teslacam.park_end_s")||21600)', self.js)
        self.assertIn("if(m.off-c>HOLE&&out.length)c=m.off;", self.js)


if __name__ == "__main__":
    unittest.main()
