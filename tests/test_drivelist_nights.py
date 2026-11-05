"""A night at one place in the Drives list: Tessie keeps its park for the night, and the footage of the morning is a park of its own, not joined into it."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_drivelist as DL  # noqa: E402
import home_ignore as IGN  # noqa: E402
from test_drivelist import T, tess, cam, by  # noqa: E402

H = 3600
HOME = (-33.9, 151.3)


def drive(i, a, b, home_end=True, home_start=False):
    return tess(i, a, b, lat1=HOME[0] if home_end else -33.7, lon1=HOME[1] if home_end else 151.0, lat=HOME[0] if home_start else -33.7, lon=HOME[1] if home_start else 151.0)


class Nights(unittest.TestCase):
    def setUp(self):
        self.real = IGN.at
        IGN.at = lambda lat, lon: lat is not None and abs(lat - HOME[0]) < 0.001
        self.addCleanup(lambda: setattr(IGN, "at", self.real))

    def night(self, cams):
        return DL.merge([drive(1, 0, 600), drive(2, 15 * H, 15 * H + 600, home_end=False, home_start=True)], [], cams)

    def test_tessie_keeps_its_park_for_the_night_and_the_mornings_footage_is_a_second_row(self):
        r = self.night([cam(9, 14 * H + 600, 14 * H + 1260, kind="stopped", n=11, p0=list(HOME), p1=list(HOME))])
        self.assertEqual(sorted((x["t0"] - T, x["t1"] - T) for x in r if x["kind"] in ("parked", "stopped")), [(600, 15 * H), (14 * H + 600, 14 * H + 1260)])

    def test_a_home_park_whose_footage_was_deleted_is_still_in_the_list(self):
        r = self.night([])
        self.assertEqual(len([x for x in r if x["kind"] == "parked"]), 1)

    def test_a_park_elsewhere_is_listed_as_before(self):
        r = DL.merge([drive(1, 0, 600, home_end=False), drive(2, 3 * H, 3 * H + 600, home_end=False)], [], [])
        self.assertEqual(len(by(r, "parked")), 1)

    def test_the_newest_park_is_kept_while_the_car_is_there(self):
        r = DL.merge([drive(1, 0, 600)], [], [])
        self.assertEqual(len([x for x in r if x["kind"] == "drive"]), 1)


if __name__ == "__main__":
    unittest.main()
