"""sign_speed: signs are read from 30 km/h on the road, and from 10 km/h only in the last minutes of a drive."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import clip_clock as CC  # noqa: E402
import run_cuts as RC  # noqa: E402
import sign_speed as SS  # noqa: E402
from test_run_cuts import item, stamps  # noqa: E402


class SignSpeedTest(unittest.TestCase):
    def setUp(self):
        RC.set_file(os.path.join(tempfile.mkdtemp(), "run_cuts.json"))
        self.sts = stamps(0, 30)                                   # 08:00 park, 08:10-08:19 drive, 08:20 park
        items = [item("stopped", self.sts[0], self.sts[9]),
                 item("drive", self.sts[10], self.sts[19], p0=[-33.80, 151.10], p1=[-33.81, 151.11]),
                 item("stopped", self.sts[20], self.sts[29])]
        RC.update(self.sts, items)

    def test_on_the_road_slow_minutes_are_not_read(self):
        self.assertEqual(SS.floor(self.sts[12]), 30.0)
        self.assertFalse(SS.wanted(self.sts[12], {"vmax": 20}))
        self.assertTrue(SS.wanted(self.sts[12], {"vmax": 45}))

    def test_the_last_minutes_of_a_drive_are_read_from_10(self):
        for st in self.sts[17:20]:                                # 08:17-08:19: within 3 minutes of the end at 08:20
            self.assertEqual(SS.floor(st), 10.0, st)
            self.assertTrue(SS.wanted(st, {"vmax": 20}))
        self.assertFalse(SS.wanted(self.sts[19], {"vmax": 5}))

    def test_seconds_before_the_last_three_minutes_stay_at_30(self):
        self.assertEqual(SS.floor(self.sts[16]), 30.0)             # 08:16:07 .. 08:17:07; the drive ends 08:20:07
        self.assertEqual(SS.floor_at(self.sts[16], 59), 30.0)
        self.assertEqual(SS.floor_at(self.sts[17], 0), 10.0)

    def test_parked_and_unknown_minutes_use_30(self):
        self.assertEqual(SS.floor(self.sts[25]), 30.0)
        self.assertEqual(SS.floor("2026-10-30_09-00-07"), 30.0)


if __name__ == "__main__":
    unittest.main()
