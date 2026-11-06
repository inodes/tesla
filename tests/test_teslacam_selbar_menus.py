#!/usr/bin/env python3
"""test_teslacam_selbar_menus.py - the bar over the TeslaCam list is three menus (Select, Move, Delete), not a row of buttons (checked as text; the page needs a browser).

    python3 -m unittest tests/test_teslacam_selbar_menus.py
"""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class SelbarMenusTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8") as f:
            self.js = f.read()

    def test_three_menus(self):
        for m in ('mnu("sel","Select"', 'mnu("mv","Move"', 'mnu("del","Delete"'):
            self.assertIn(m, self.js)

    def test_no_loose_move_and_delete_buttons(self):
        self.assertNotIn('class="btn sm bad" data-ka="delete|', self.js)
        self.assertNotIn('class="btn sm" data-ka="move|', self.js)
        self.assertNotIn('<span class="sub" style="margin-left:6px">Select:</span>', self.js)

    def test_the_actions_still_carry_their_data(self):
        for a in ('data-ka="move|${r}|${t}"', 'data-ka="delete|${r}"', 'data-ka="purge|"', 'data-sset="all"', 'data-sset="done"'):
            self.assertIn(a, self.js)

    def test_move_and_delete_only_with_a_selection(self):
        self.assertIn("if(items.length){\n        const mv=", self.js)


if __name__ == "__main__":
    unittest.main()
