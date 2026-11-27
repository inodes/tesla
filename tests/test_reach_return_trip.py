"""Range map with "include return trip": only half of the usable charge is for the way out (lib/app_reach.py)."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
from lib import app_reach as AR  # noqa: E402


class Stop(Exception):
    pass


class FakeGraph:
    fit = object()

    def __init__(self):
        self.charge = None

    def reach(self, pts, basis):
        self.charge = pts[0][2]
        raise Stop()

    def nearest(self, pts, basis, want_who=False):
        raise Stop()


class ReturnTrip(unittest.TestCase):
    def service(self):
        s = AR.ReachService(tempfile.mkdtemp(), tempfile.mkdtemp())
        s.meta = lambda: {"built": True}
        s.car = lambda: {"km": 400.0, "kwh": 70.0}
        g = FakeGraph()
        s._graph = lambda: g
        return s, g

    def charge(self, **extra):
        s, g = self.service()
        body = dict(pts=[[-33.0, 151.0, "a"]], mode="reach", frac=1.0, reserve=0.2, basis="km")
        body.update(extra)
        try:
            s.compute(body)
        except Stop:
            pass
        return g.charge

    def test_one_way_uses_all_the_charge_above_the_reserve(self):
        self.assertAlmostEqual(self.charge(), 320.0)

    def test_a_return_trip_halves_it(self):
        self.assertAlmostEqual(self.charge(round_trip=True), 160.0)

    def test_the_two_are_cached_apart(self):
        s, g = self.service()
        for rt in (False, True):
            try:
                s.compute(dict(pts=[[-33.0, 151.0, "a"]], mode="reach", frac=1.0, reserve=0.2, basis="km", round_trip=rt))
            except Stop:
                pass
        self.assertEqual(g.charge, 160.0)


class Wording(unittest.TestCase):
    def test_the_page_names_the_points_by_the_network_shown(self):
        with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn('"No "+rgThing()+" within reach of the roads here."', js)
        self.assertIn("function rgThing(n)", js)
        self.assertIn('id="rgret"', js)
        self.assertIn("Include return trip", js)
        self.assertIn("> In range</label>", js)
        self.assertIn("> Out of range</label>", js)
        self.assertNotIn("Can drive", js)
        self.assertNotIn("Cannot drive", js)


if __name__ == "__main__":
    unittest.main()
