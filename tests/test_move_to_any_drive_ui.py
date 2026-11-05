#!/usr/bin/env python3
"""test_move_to_any_drive_ui.py - the TeslaCam bar offers "Move to <drive>" for every other connected drive, and sends it (checked as text; the page needs a browser).

    python3 -m unittest tests/test_move_to_any_drive_ui.py
"""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class MoveToAnyDriveUiTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8") as f:
            self.js = f.read()
        with open(os.path.join(HERE, "..", "Tools", "tesla_app.py"), encoding="utf-8") as f:
            self.py = f.read()

    def test_a_button_per_target_drive(self):
        self.assertIn('data-ka="move|${r}|${t}"', self.js)
        self.assertIn('Move to ${t==="A"?"Archive":NMD[t]}', self.js)
        self.assertIn('pend={action:a,role:r,to:t||"A"}', self.js)

    def test_the_target_is_sent_and_read(self):
        self.assertIn('to:p.to||"A"', self.js)
        self.assertIn('to=body.get("to") or "A"', self.py)


if __name__ == "__main__":
    unittest.main()
