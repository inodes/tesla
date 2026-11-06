#!/usr/bin/env python3
"""test_drive_dot_busy_ui.py - the drive's dot in the corner is orange while a Move, Delete or Update of clips works on it (not only while the Keeper reads it). Checked as text.

    python3 -m unittest tests/test_drive_dot_busy_ui.py
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def read(name):
    with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", name), encoding="utf-8") as f:
        return f.read()


class DriveDot(unittest.TestCase):
    def test_a_clip_job_makes_the_drives_it_works_on_orange(self):
        js = read("app.js")
        self.assertIn('fetch("/api/clips/job")', js[js.index("const kp=async"):])
        self.assertIn("[cj.role,cj.to].filter(Boolean)", js)                      # the drive it takes from and the one it moves to
        self.assertIn('"Moving clips"', js)

    def test_busy_is_orange(self):
        css = read("app.css")
        self.assertRegex(css, r"\.dot\.rd\{background:#f97316\}")


if __name__ == "__main__":
    unittest.main()
