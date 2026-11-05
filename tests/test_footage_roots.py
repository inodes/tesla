#!/usr/bin/env python3
"""test_footage_roots.py - which drive is connected is found by one look at the volumes for every role (not one per role), and two first askers
share that look.

    python3 -m unittest tests/test_footage_roots.py
"""
import os
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import tesla_app  # noqa: E402


class Roots(unittest.TestCase):
    VOLS = [("/v/A1/TeslaCam", "A"), ("/v/J/TeslaCam", "J"), ("/v/A2/TeslaCam", "A"), ("/v/X/TeslaCam", "?")]

    def setUp(self):
        self.saved = (tesla_app.DR.discover, tesla_app.DR.read_roles, dict(tesla_app._ROOTS))
        self.calls = [0]

        def discover():
            self.calls[0] += 1
            time.sleep(0.05)
            return list(self.VOLS)
        tesla_app.DR.discover = discover
        tesla_app.DR.read_roles = lambda: "AJU"
        tesla_app._ROOTS.update(t=0, v=None, busy=False)

    def tearDown(self):
        tesla_app.DR.discover, tesla_app.DR.read_roles, roots = self.saved
        tesla_app._ROOTS.clear()
        tesla_app._ROOTS.update(roots)

    def test_the_same_answer_as_one_find_root_per_role(self):
        want = {r: next((p for p, x in self.VOLS if x == r), None) for r in "AJU"}
        self.assertEqual(tesla_app._look_roots(), want)
        self.assertEqual(self.calls[0], 1)

    def test_two_first_askers_share_one_look(self):
        out = []
        ts = [threading.Thread(target=lambda: out.append(tesla_app.footage_root("A"))) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual((out, self.calls[0]), (["/v/A1/TeslaCam"] * 2, 1))


if __name__ == "__main__":
    unittest.main()
