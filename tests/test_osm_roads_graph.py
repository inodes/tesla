"""osm_roads_graph: a graph made from a few ways gives the same distances as walking them, one search serves several chargers with their own charge, one-way roads are respected,
a charger part way along a stretch of road counts for the points either side of it, and the search for the nearest charger runs the roads the other way."""
import os, sys, tempfile, unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
from lib import osm_roads_graph as G

LAT0, LON0, STEP = -33.0, 151.0, 0.01


def make(ways, nodes, fit=(10.0, 0.0)):
    """ways: [(node ids, oneway, kmh)]; nodes: {id: (lat, lon)}. A RoadGraph over them (saved and read back like the real one)."""
    refs = np.array([n for w in ways for n in w[0]], np.int64)
    way_of = np.array([i for i, w in enumerate(ways) for _ in w[0]], np.int32)
    ids = np.array(sorted(nodes), np.int64)
    g = G.assemble(refs, way_of, np.array([w[1] for w in ways], np.int8), np.array([w[2] for w in ways], np.uint8), ids,
                   np.array([nodes[i][0] for i in ids]), np.array([nodes[i][1] for i in ids]))
    d = tempfile.mkdtemp()
    for k in G.FILES:
        np.save(os.path.join(d, k + ".npy"), g[k])
    return G.RoadGraph(d, fit), ids


def line(n, lat0=LAT0, lon0=LON0):
    return {i + 1: (lat0 + i * STEP, lon0) for i in range(n)}


def km(a, b):
    return float(G._hav(a[0], a[1], b[0], b[1])) / 1000.0


class GraphTest(unittest.TestCase):
    def setUp(self):
        G.MIN_NET = 1

    def val(self, g, ids, arr, node_id):
        return float(arr[int(np.searchsorted(ids, node_id))])

    def test_reach_matches_walking(self):
        nodes = line(6)
        g, ids = make([([1, 2, 3, 4, 5, 6], 0, 60)], nodes)
        r = g.reach([(nodes[1][0], nodes[1][1], 4.0)], "km")
        for n in range(1, 7):
            want = 4.0 - km(nodes[1], nodes[n])
            got = self.val(g, ids, r["left"], n)
            if want >= 0:
                self.assertAlmostEqual(got, want, places=2)
            else:
                self.assertEqual(got, -1.0)

    def test_two_chargers_own_charge(self):
        nodes = line(7)
        g, ids = make([(list(range(1, 8)), 0, 60)], nodes)
        r = g.reach([(nodes[1][0], nodes[1][1], 3.0), (nodes[7][0], nodes[7][1], 5.0)], "km")
        for n in range(1, 8):
            want = max(3.0 - km(nodes[1], nodes[n]), 5.0 - km(nodes[7], nodes[n]), -1.0)
            self.assertAlmostEqual(self.val(g, ids, r["left"], n), want if want >= 0 else -1.0, places=2)
        self.assertEqual(r["full"], 5.0)

    def test_one_way(self):
        nodes = line(5)
        g, ids = make([([1, 2, 3, 4, 5], 1, 60)], nodes)                   # runs 1 -> 5 only
        up = g.reach([(nodes[1][0], nodes[1][1], 10.0)], "km")["left"]
        down = g.reach([(nodes[5][0], nodes[5][1], 10.0)], "km")["left"]
        self.assertGreater(self.val(g, ids, up, 5), 0)
        self.assertEqual(self.val(g, ids, down, 1), -1.0)
        d = g.nearest([(nodes[5][0], nodes[5][1])], "km")                  # a car at 1 can drive to a charger at 5, a car at 5 cannot get to one at 1
        self.assertAlmostEqual(self.val(g, ids, d, 1), km(nodes[1], nodes[5]), places=2)
        d = g.nearest([(nodes[1][0], nodes[1][1])], "km")
        self.assertTrue(np.isinf(self.val(g, ids, d, 5)))

    def test_charger_between_ends(self):
        nodes = line(5)
        g, ids = make([([1, 2, 3, 4, 5], 0, 60)], nodes)                   # only 1 and 5 are junctions, 2-4 are bends of one chain
        r = g.reach([(nodes[3][0], nodes[3][1], 3.0)], "km")["left"]
        for n in (2, 3, 4):
            self.assertAlmostEqual(self.val(g, ids, r, n), 3.0 - km(nodes[3], nodes[n]), places=2)
        d = g.nearest([(nodes[3][0], nodes[3][1])], "km")
        for n in (1, 2, 4, 5):
            self.assertAlmostEqual(self.val(g, ids, d, n), km(nodes[3], nodes[n]), places=2)

    def test_junction_and_branch(self):
        nodes = line(5)
        nodes.update({6: (LAT0 + 2 * STEP, LON0 + STEP), 7: (LAT0 + 2 * STEP, LON0 + 2 * STEP)})
        g, ids = make([([1, 2, 3, 4, 5], 0, 60), ([3, 6, 7], 0, 60)], nodes)
        r = g.reach([(nodes[1][0], nodes[1][1], 20.0)], "km")["left"]
        want = 20.0 - km(nodes[1], nodes[3]) - km(nodes[3], nodes[6]) - km(nodes[6], nodes[7])
        self.assertAlmostEqual(self.val(g, ids, r, 7), want, places=2)

    def test_energy_basis(self):
        nodes = line(4)
        g, ids = make([([1, 2, 3, 4], 0, 100)], nodes, fit=(10.0, 0.001))      # 10 + 0.001 * 100^2 = 20 kWh / 100 km at 100 km/h
        r = g.reach([(nodes[1][0], nodes[1][1], 5.0)], "kwh")["left"]
        self.assertAlmostEqual(self.val(g, ids, r, 4), 5.0 - km(nodes[1], nodes[4]) * 0.20, places=2)

    def test_outline_and_who(self):
        nodes = line(9)
        g, ids = make([(list(range(1, 10)), 0, 60)], nodes)
        r = g.reach([(nodes[1][0], nodes[1][1], 3.4), (nodes[5][0], nodes[5][1], 0.5), (nodes[9][0], nodes[9][1], 3.4)], "km")
        self.assertEqual(r["outline"], [0, 2])                              # the small circle in the middle lies inside the other two: it shapes nothing at the edge
        self.assertEqual(int(r["who"][int(g.kid[0])]), 0)

    def test_nearest_says_which_charger_and_stretches_run_between_neighbours(self):
        nodes = line(9)
        g, ids = make([(list(range(1, 10)), 0, 60)], nodes)
        pts = [(nodes[n][0], nodes[n][1]) for n in (1, 4, 9)]
        d, who = g.nearest(pts, "km", want_who=True)
        for n, w in ((2, 0), (3, 1), (5, 1), (7, 2), (4, 1)):
            self.assertEqual(int(who[int(np.searchsorted(ids, n))]), w, n)
        st = g.stretches(d, who, "km")
        self.assertEqual([(a, b) for a, b, _c, _p in st], [(1, 2), (0, 1)])           # longest first: 4 to 9 then 1 to 4
        self.assertAlmostEqual(st[0][2], km(nodes[4], nodes[9]), places=2)
        self.assertAlmostEqual(st[1][2], km(nodes[1], nodes[4]), places=2)

    def test_a_spot_sees_the_nearest_charger_each_way(self):
        nodes = line(9)
        g, ids = make([(list(range(1, 10)), 0, 60)], nodes)
        pts = [(nodes[n][0], nodes[n][1]) for n in (1, 4, 9)]
        prep = g.prepare(pts, "km")
        r = g.at(nodes[6][0], nodes[6][1], prep, "km")
        self.assertEqual([f[0] for f in r["found"]], [1, 2, 0])
        self.assertAlmostEqual(r["found"][0][1], km(nodes[6], nodes[4]), places=2)
        self.assertEqual(r["back"][0], 2)                                              # the other way from the nearest is the one at 9
        self.assertAlmostEqual(r["back"][1], km(nodes[6], nodes[9]), places=2)
        self.assertNotEqual(r["found"][0][2], r["back"][2])
        self.assertIsNone(g.at(10.0, 10.0, prep, "km"))

    def test_one_way_spot_only_sees_chargers_ahead(self):
        nodes = line(5)
        g, ids = make([([1, 2, 3, 4, 5], 1, 60)], nodes)
        prep = g.prepare([(nodes[1][0], nodes[1][1]), (nodes[5][0], nodes[5][1])], "km")
        r = g.at(nodes[3][0], nodes[3][1], prep, "km")
        self.assertEqual([f[0] for f in r["found"]], [1])
        self.assertIsNone(r["back"])


if __name__ == "__main__":
    unittest.main()
