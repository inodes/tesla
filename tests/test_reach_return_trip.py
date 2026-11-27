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


class AtSpot(unittest.TestCase):
    """The popup on the range map: the nearest charger is found among every charger shown, the charge left from the ones picked."""

    def setUp(self):
        s = AR.ReachService(tempfile.mkdtemp(), tempfile.mkdtemp())
        s.meta = lambda: {"built": True}
        s.car = lambda: {"km": 400.0, "kwh": 70.0}
        self.seen = []

        class G:
            fit = object()

            def prepare(g, pts, basis):
                self.seen.append([p for p in pts])
                return {"n": len(pts)}

            def at(g, lat, lon, prep, basis, limit=None):
                return dict(lat=lat, lon=lon, found=[(0, 0.3, 0)], back=None)

            def snap(g, lat, lon):
                return 0, 0.0
        s._graph = lambda: G()
        self.s = s

    def test_the_nearest_is_searched_among_all_chargers_when_some_are_picked(self):
        r = self.s.at(dict(pts=[[-34.18, 142.16, "nrma"], [-34.2, 142.14, "sc"]], reach_pts=[[-34.2, 142.14, "sc"]], lat=-34.18, lon=142.16, basis="km"))
        self.assertEqual(len(self.seen[0]), 2)
        self.assertEqual(r["near"][0]["key"], "nrma")

    def test_charge_left_is_read_from_the_picked_chargers_range(self):
        import numpy as np
        sc = [(-34.2, 142.14)]
        both = [(-34.18, 142.16), (-34.2, 142.14)]
        import hashlib
        import json

        def key(pts):
            return hashlib.sha1(json.dumps(["reach", "km", 0.9, 0.2, 1.0, [(round(a, 5), round(b, 5)) for a, b in pts]]).encode()).hexdigest()
        self.s.cache[key(sc)] = {"val": np.array([123.0])}
        self.s.cache[key(both)] = {"val": np.array([999.0])}
        body = dict(pts=[[-34.18, 142.16, "nrma"], [-34.2, 142.14, "sc"]], lat=-34.18, lon=142.16, basis="km", frac=0.9, reserve=0.2)
        self.assertEqual(self.s.at(dict(body, reach_pts=[[-34.2, 142.14, "sc"]]))["left"], 123.0)
        self.assertEqual(self.s.at(body)["left"], 999.0)
