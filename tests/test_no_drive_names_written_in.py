#!/usr/bin/env python3
"""test_no_drive_names_written_in.py - a drive is named only by the label it was registered with (config.json teslacam.drives): no drive name is written into the page or the
keeper, so "the Archive" / "the Tesla USB" cannot appear beside the registered "Archive" / "TeslaUSB" / "LaCie".

    python3 -m unittest tests/test_no_drive_names_written_in.py
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def src(*p):
    with open(os.path.join(HERE, "..", *p), encoding="utf-8") as f:
        return f.read()


class NoDriveNamesWrittenIn(unittest.TestCase):
    def test_the_page_has_no_drive_name_table(self):
        js = src("Tools", "lib", "app_ui", "app.js")
        self.assertNotRegex(js, r'\{\s*A\s*:\s*"[^"]*"\s*,\s*J\s*:\s*"')                  # {A:"...",J:"...",U:"..."}
        self.assertNotIn('"the Archive"', js)
        self.assertNotIn('"the Tesla USB"', js)
        self.assertNotIn('"Tesla USB"', js)
        self.assertNotIn('if(!"AJU".includes(r)){if(DRX', js)                              # every registered drive's label is taken, the first three too

    def test_the_names_come_from_the_status(self):
        js = src("Tools", "lib", "app_ui", "app.js")
        self.assertIn("const drName=r=>DRX[r]||r;", js)
        self.assertIn("Object.entries(s.names||{}).forEach(([r,l])=>{{if(DRX[r]!==l)ch=true;DRX[r]=l;NM[r]=l}})", js)

    def test_the_keeper_and_the_roles_have_no_names_of_their_own(self):
        self.assertNotIn("the Archive", src("Tools", "lib", "app_keeper.py").split('"""', 2)[2].replace("whether or not the Archive", ""))
        dr = src("Tools", "lib", "drive_roles.py")
        self.assertNotRegex(dr, r'NAMES\s*=\s*\{"A"')
        self.assertIn("REG.drives()", dr)


if __name__ == "__main__":
    unittest.main()
