#!/usr/bin/env python3
"""test_save_minutes_ui.py - Shift-click on the blocks of a drive page's strip selects minutes and saves them as a Saved clip (checked as text; the page needs a browser).

    python3 -m unittest tests/test_save_minutes_ui.py
"""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class SaveMinutesUiTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8") as f:
            self.js = f.read()
        with open(os.path.join(HERE, "..", "Tools", "tesla_app.py"), encoding="utf-8") as f:
            self.py = f.read()

    def test_shift_click_selects_a_range_and_the_bar_saves_it(self):
        self.assertIn("if(e.shiftKey){covSel=", self.js)
        self.assertIn('fetch("/api/clips/save_minutes"', self.js)
        self.assertIn("Move them from Recent into a new Saved clip?", self.js)       # asked before anything is moved
        self.assertIn("confirm:true", self.js)

    def test_the_server_route_needs_a_confirmation(self):
        self.assertIn("/api/clips/save_minutes", self.py)
        self.assertIn('CKEEP.save_minutes(body.get("stamps") or [], footage_roots(), confirmed=body.get("confirm") is True)', self.py)


if __name__ == "__main__":
    unittest.main()
