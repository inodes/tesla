#!/usr/bin/env python3
"""test_sentry_beside_park_ui.py - a Sentry event with no park round it, but a park of the same place a few minutes away (no drive or charge between), is part of that stay: the
Drives list shows one park, not two side by side (checked as text; the page needs a browser).

    python3 -m unittest tests/test_sentry_beside_park_ui.py
"""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class SentryBesidePark(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8") as f:
            self.js = f.read()

    def test_a_loose_event_joins_the_park_beside_it(self):
        i = self.js.index("an event just before or after a park at the same place")
        blk = self.js[i:i + 1800]
        self.assertIn("nearPark(v)", blk)
        self.assertIn("gap<=3600", blk)                                   # an hour, as for parks joined to each other
        self.assertIn("<=300", blk)                                       # one place
        self.assertIn("hard2.some(h=>h.t1>lo&&h.t0<hi)", blk)             # a drive or charge between: the car left, not one stay
        self.assertIn("(k._sent=k._sent||[]).push(v);v._in=k.id", blk)    # counted on that park
        self.assertIn("k.t0=v.t0", blk)                                   # and the stay now starts at the event
        # the synthetic row is only made when no park is near
        self.assertLess(blk.index("nearPark(v)"), self.js.index("g={id:\"sp:\"+v.id"))


if __name__ == "__main__":
    unittest.main()
