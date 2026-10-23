"""test_route_fill.py - a drive's route: the dashcam's seconds where it has them, Tessie's points joined in between.

    python3 -m unittest tests/test_route_fill.py
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_route as R  # noqa: E402

T = 1792536000


def cam(t0, n, lat=-33.8):
    s = {k: [None] * n for k in R.KEYS}
    s["lat"], s["lon"], s["v"] = [lat] * n, [151.1] * n, [50.0] * n
    s["t0"], s["pending"] = t0, 0
    return s


class FillTests(unittest.TestCase):
    pts = [(T, -33.80, 151.10, 0.0), (T + 100, -33.81, 151.12, 60.0), (T + 200, -33.82, 151.14, 40.0)]

    def test_tessie_alone_draws_the_whole_drive(self):
        s = R.fill(None, T, T + 200, self.pts)
        self.assertEqual((s["t0"], len(s["lat"])), (T, 200))
        self.assertAlmostEqual(s["lat"][50], -33.805, 5)
        self.assertEqual(s["v"][100], 60.0)
        self.assertEqual(sum(1 for x in s["lat"] if x is None), 0)

    def test_the_dashcams_seconds_are_kept_and_tessie_fills_the_rest(self):
        s = R.fill(cam(T + 60, 40), T, T + 200, self.pts)
        self.assertEqual(s["lat"][70], -33.8)                       # the dashcam's second
        self.assertNotEqual(s["lat"][10], -33.8)                    # Tessie's
        self.assertIsNotNone(s["lat"][150])
        self.assertEqual(len(s["v"]), 200)

    def test_a_one_second_hole_between_clips_is_bridged_from_the_dashcams_points(self):
        a, b = cam(T, 60), cam(T + 61, 60)
        b["lat"] = [-33.8 - 0.0001] * 60                        # the next clip's first point is 11 m on
        s = R.fill({k: (a[k] + [None]) + b[k] if isinstance(a[k], list) else a[k] for k in a}, T, T + 121, self.pts)
        self.assertAlmostEqual(s["lat"][60], -33.80005, 5)       # halfway, not Tessie's place

    def test_tessies_route_is_moved_onto_the_dashcams_at_the_join(self):
        c = cam(T + 100, 50)
        c["lat"], c["lon"] = [-33.8105] * 50, [151.1205] * 50      # 50 m off Tessie's line at T + 100 (-33.81, 151.12)
        s = R.fill(c, T, T + 200, self.pts)
        self.assertAlmostEqual(s["lat"][99], -33.8105, 3)         # next to the join Tessie's point sits on the dashcam's
        self.assertAlmostEqual(s["lat"][0], -33.80, 4)            # far from it Tessie's own

    def test_a_hole_in_tessies_points_is_not_joined(self):
        s = R.fill(None, T, T + 700, [(T, -33.8, 151.1, 0.0), (T + 600, -33.9, 151.2, 0.0)])
        self.assertTrue(all(x is None for x in s["lat"]))

    def test_the_dashcam_may_start_before_the_drive(self):
        s = R.fill(cam(T - 30, 100), T, T + 200, self.pts)
        self.assertEqual(s["t0"], T - 30)
        self.assertEqual(s["lat"][0], -33.8)

    def test_points_are_read_from_the_file_on_the_zone_in_its_header(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write('"Timestamp (AEDT)","Latitude","Longitude","Speed (km/h)"\n2026-10-21 09:41:44,-33.8,151.1,0\n2026-10-21 09:42:10,-33.81,151.11,12\n')
        pts = R.points(f.name)
        os.unlink(f.name)
        self.assertEqual(len(pts), 2)
        self.assertEqual(pts[0][0], 1792536104)                     # 2026-10-20 22:41:44 UTC
        self.assertEqual(pts[1][3], 12.0)


if __name__ == "__main__":
    unittest.main()
