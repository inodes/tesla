#!/usr/bin/env python3
"""test_suburb_lookup.py - the offline suburb lookup (lib/suburb_lookup.py): boundaries stitched from OpenStreetMap ways, a point-in-polygon answer, one request per suburb ever,
holes, overlaps, points in no suburb, a server that does not answer, and the background queue.

    python3 -m unittest tests/test_suburb_lookup.py
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import suburb_lookup as SL  # noqa: E402


def way(*pts):
    return [dict(lat=a, lon=b) for a, b in pts]


def rel(rid, name, ways, level="9"):
    return dict(type="relation", id=rid, tags=dict(name=name, admin_level=level, boundary="administrative"),
                members=[dict(type="way", role=r, geometry=g) for r, g in ways])


# a square suburb 0.00..0.02 south, 0.00..0.02 east, drawn as four ways (one reversed) so the rings must be stitched
SQ = rel(1, "Squareville", [("outer", way((-33.80, 151.00), (-33.80, 151.02))), ("outer", way((-33.80, 151.02), (-33.82, 151.02))),
                            ("outer", way((-33.80, 151.00), (-33.82, 151.00))),                       # reversed: ends at the start of the first way
                            ("outer", way((-33.82, 151.00), (-33.82, 151.02)))])
# a hole in it (an enclave) and a smaller locality beside it
HOLED = rel(2, "Holey", [("outer", way((-33.90, 151.00), (-33.90, 151.04), (-33.94, 151.04), (-33.94, 151.00), (-33.90, 151.00))),
                          ("inner", way((-33.91, 151.01), (-33.91, 151.02), (-33.92, 151.02), (-33.92, 151.01), (-33.91, 151.01)))])
BIG = rel(3, "Bigger", [("outer", way((-34.0, 151.0), (-34.0, 151.1), (-34.1, 151.1), (-34.1, 151.0), (-34.0, 151.0)))], level="9")
SMALL = rel(4, "Inner Locality", [("outer", way((-34.02, 151.02), (-34.02, 151.04), (-34.04, 151.04), (-34.04, 151.02), (-34.02, 151.02)))], level="10")


class Fake:
    def __init__(self, rels):
        self.rels, self.calls = rels, []

    def __call__(self, q):
        self.calls.append(q)
        import re
        m = re.search(r"is_in\(([-\d.]+),([-\d.]+)\)", q)
        la, lo = float(m.group(1)), float(m.group(2))
        out = []
        for r in self.rels:
            pts = [(p["lat"], p["lon"]) for mem in r["members"] for p in mem["geometry"]]
            if min(p[0] for p in pts) <= la <= max(p[0] for p in pts) and min(p[1] for p in pts) <= lo <= max(p[1] for p in pts):
                out.append(r)
        return dict(elements=out)


class Lookup(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.f = Fake([SQ, HOLED, BIG, SMALL])
        self.S = SL.Suburbs(self.d, fetcher=self.f)

    def test_ways_are_stitched_into_one_ring_and_the_point_is_found(self):
        self.assertEqual(self.S.at(-33.81, 151.01, wait=True), "Squareville")
        self.assertIsNone(self.S.at(-33.79, 151.01, wait=True))

    def test_one_request_per_suburb_then_everything_in_it_is_answered_from_memory(self):
        self.S.at(-33.81, 151.01, wait=True)
        for la, lo in ((-33.805, 151.005), (-33.815, 151.015), (-33.8101, 151.0101)):
            self.assertEqual(self.S.at(la, lo), "Squareville")                      # no wait, no new request
        self.assertEqual(len(self.f.calls), 1)

    def test_kept_on_disk_so_a_new_run_asks_nothing(self):
        self.S.at(-33.81, 151.01, wait=True)
        again = SL.Suburbs(self.d, fetcher=self.f)
        self.assertEqual(again.at(-33.805, 151.012), "Squareville")
        self.assertEqual(len(self.f.calls), 1)

    def test_a_hole_is_not_the_suburb(self):
        self.assertEqual(self.S.at(-33.92, 151.03, wait=True), "Holey")
        self.assertIsNone(self.S.at(-33.915, 151.015))             # in the enclave: not Holey (nothing else holds it, so it is asked and found in none)

    def test_the_smallest_suburb_wins_where_they_overlap(self):
        self.assertEqual(self.S.at(-34.03, 151.03, wait=True), "Inner Locality")
        self.assertEqual(self.S.at(-34.08, 151.08, wait=True), "Bigger")

    def test_a_point_in_no_suburb_is_asked_once_and_remembered(self):
        self.assertIsNone(self.S.at(-35.0, 150.0, wait=True))
        self.assertIsNone(self.S.at(-35.0, 150.0, wait=True))
        self.assertEqual(len(self.f.calls), 1)
        self.assertIsNone(SL.Suburbs(self.d, fetcher=self.f).at(-35.0, 150.0, wait=True))
        self.assertEqual(len(self.f.calls), 1)

    def test_a_server_that_does_not_answer_is_left_alone_for_a_while(self):
        down = []
        S = SL.Suburbs(self.d + "/x", fetcher=lambda q: down.append(q), retry_s=3600)
        self.assertIsNone(S.at(-33.81, 151.01, wait=True))
        self.assertIsNone(S.at(-33.82, 151.02, wait=True))
        self.assertEqual(len(down), 1)
        self.assertIsNone(S.at(-33.81, 151.01))                      # nothing was kept or guessed

    def test_without_wait_the_page_never_blocks_and_the_answer_arrives_by_itself(self):
        t = time.time()
        self.assertIsNone(self.S.at(-33.81, 151.01))
        self.assertLess(time.time() - t, 0.5)
        for _ in range(100):
            if self.S.at(-33.81, 151.01):
                break
            time.sleep(0.05)
        self.assertEqual(self.S.at(-33.81, 151.01), "Squareville")

    def test_prefetch_queues_only_what_is_not_held_one_per_cell(self):
        self.S.at(-33.81, 151.01, wait=True)
        n = self.S.prefetch([(-33.805, 151.005), (-33.905, 151.0101), (-33.9051, 151.0102), (None, 1)])
        self.assertEqual(n, 1)
        for _ in range(100):
            if self.S.at(-33.905, 151.0101):
                break
            time.sleep(0.05)
        self.assertEqual(self.S.at(-33.905, 151.0101), "Holey")

    def test_fetch_off_answers_only_from_what_is_kept(self):
        self.S.at(-33.81, 151.01, wait=True)
        off = SL.Suburbs(self.d, fetch=False, fetcher=self.f)
        self.assertEqual(off.at(-33.81, 151.01), "Squareville")
        self.assertIsNone(off.at(-33.9, 151.01, wait=True))
        self.assertEqual(len(self.f.calls), 1)

    def test_lookups_are_fast(self):
        self.S.at(-33.81, 151.01, wait=True)
        t = time.time()
        for i in range(20000):
            self.S.at(-33.80 - (i % 200) * 0.0001, 151.0 + (i // 200) * 0.0001)
        self.assertLess(time.time() - t, 3.0)

    def test_simplify_keeps_the_shape_and_the_ends(self):
        line = [(0.0, 0.0), (0.0, 0.5e-5), (0.0, 1.0e-5), (0.001, 0.0011), (0.0, 0.002)]
        s = SL.simplify(line)
        self.assertEqual((s[0], s[-1]), (line[0], line[-1]))
        self.assertIn((0.001, 0.0011), s)
        self.assertLess(len(s), len(line))


if __name__ == "__main__":
    unittest.main()
