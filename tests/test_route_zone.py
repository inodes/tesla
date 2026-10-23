import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
import app_route  # noqa: E402


class ZoneTest(unittest.TestCase):
    def test_times_are_read_on_the_drives_own_clock_not_the_zone_in_the_heading(self):
        csv_text = '"Timestamp (AEDT)","Latitude","Longitude","Speed (km/h)"\n"2026-06-01 08:44:08","-33.8","151.0","33"\n'
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "drive_deepdive_2026-06-01_08-46_AEST.csv")
            open(p, "w").write(csv_text)
            self.assertEqual(app_route.points(p, 36000)[0][0], 1780267448)      # 08:44:08 AEST = 22:44:08 UTC the day before
            self.assertEqual(app_route.points(p)[0][0], 1780267448)             # no clock given: the zone in the file's name


if __name__ == "__main__":
    unittest.main()
