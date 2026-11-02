"""A drive is right whichever source arrives first: Tessie's record or the dashcam's telemetry. The list and the route are worked out again from both each time,
so the later one joins the earlier one and improves it (the dashcam's seconds for the line, Tessie's names, energy and battery for the record)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_drivelist as DL  # noqa: E402
import app_route as AR  # noqa: E402

T0 = 1793000000
TESSIE = dict(id="2026-10-30_17-00_AEDT", t0=T0, t1=T0 + 720, km=3.8, max=61, kwh=0.4, frm="Top Ryde", to="Home", lat=-33.8125, lon=151.106, lat1=-33.7000, lon1=151.0500, min=12)
CAM = dict(id="2026-10-30_17-00-43", kind="drive", t0=T0 + 20, t1=T0 + 700, km=3.7, vmax=60, p0=[-33.8125, 151.106], p1=[-33.7000, 151.0500], lo=39600)


def drives(items):
    return [x for x in items if x["kind"] == "drive"]


class OrderTest(unittest.TestCase):
    def test_dashcam_first_then_tessie_joins_it(self):
        only_cam = drives(DL.merge([], [], [dict(CAM)]))
        self.assertEqual([(x["src"], x["cam"]) for x in only_cam], [("cam", [CAM["id"]])])           # listed from the dashcam alone
        both = drives(DL.merge([dict(TESSIE)], [], [dict(CAM)]))
        self.assertEqual(len(both), 1)                                                               # one drive, not two
        self.assertEqual((both[0]["src"], both[0]["cam"], both[0]["to"]), ("both", [CAM["id"]], "Home"))

    def test_tessie_first_then_the_dashcam_joins_it(self):
        only_t = drives(DL.merge([dict(TESSIE)], [], []))
        self.assertEqual([(x["src"], x["cam"]) for x in only_t], [("tessie", [])])
        both = drives(DL.merge([dict(TESSIE)], [], [dict(CAM)]))
        self.assertEqual([(x["src"], x["cam"]) for x in both], [("both", [CAM["id"]])])
        self.assertEqual(both, drives(DL.merge([dict(TESSIE)], [], [dict(CAM)])))                   # the same answer whichever came first

    def test_the_route_uses_the_dashcams_seconds_and_tessie_only_in_between(self):
        pts = [(T0 + i * 25, -33.8125 + i * 0.0002, 151.106 - i * 0.0009, 40.0) for i in range(30)]       # Tessie: a point every 25 s
        tessie_only = AR.fill(None, T0, T0 + 720, pts)
        self.assertTrue(all(x is not None for x in tessie_only["lat"][10:700]))                   # a whole line from Tessie alone
        cam = dict(t0=T0 + 100, v=[50.0] * 60, lat=[-33.81] * 60, lon=[151.1] * 60, hd=[0] * 60, ap=[0] * 60, steer=[0] * 60, accel=[0] * 60, br=[0] * 60, blk=[0] * 60, gear=[1] * 60)
        both = AR.fill(cam, T0, T0 + 720, pts)
        i = 100 + 30                                                                                 # a second the dashcam has
        self.assertEqual((both["lat"][i], both["lon"][i]), (-33.81, 151.1))                        # its own point, not Tessie's
        self.assertIsNotNone(both["lat"][400])                                                      # and Tessie still fills the rest of the drive


if __name__ == "__main__":
    unittest.main()
