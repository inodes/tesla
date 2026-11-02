"""Drives of Tessie's split by a stop of a moment (Park for a pick-up, a drop-off, a gate) are one drive."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_data  # noqa: E402
import app_drivelist as DL  # noqa: E402

T = 1793000000


def drv(i, a, b, lat=-33.80, lon=151.0, lat1=None, lon1=None, **kw):
    r = dict(k="drive", id="d%d" % i, page=True, t0=T + a, t1=T + b, min=(b - a) / 60.0, km=5.0, kwh=1.0, whkm=200, avg=30.0, max=60, b0=80, b1=78, ap=1.0, lat=lon and lat, lon=lon,
             lat1=lat1 if lat1 is not None else lat + 0.01, lon1=lon1 if lon1 is not None else lon, frm="A", to="B", addr1="x", pkey1="k", odo=100.0, odo1=105.0, tin=20.0, tout=25.0,
             clips=2, saved=0, flips=0, foot=50, repark=False)
    r.update(kw)
    return r


def chain():
    a = drv(1, 0, 600)
    b = drv(2, 600, 720, lat=a["lat1"], lon=a["lon1"], km=0.2, kwh=0.0, to="C", b1=77, max=24, odo=105.0, odo1=105.2)
    return a, b


class Join(unittest.TestCase):
    def test_two_drives_with_no_gap_at_one_spot_are_one(self):
        out = app_data.join_short_stops(list(chain()), 120, 50)
        self.assertEqual(len(out), 1)
        j = out[0]
        self.assertEqual((j["id"], j["t0"], j["t1"], j["to"], j["b0"], j["b1"], j["odo"], j["odo1"]), ("d1", T, T + 720, "C", 80, 77, 100.0, 105.2))
        self.assertAlmostEqual(j["km"], 5.2)
        self.assertEqual(j["max"], 60)
        self.assertEqual([p["id"] for p in j["parts"]], ["d1", "d2"])
        self.assertEqual(j["stops"][0]["secs"], 0)
        self.assertAlmostEqual(j["min"], 12.0)

    def test_a_gap_over_the_limit_keeps_two(self):
        a, b = chain()
        b["t0"] += 121
        b["t1"] += 121
        self.assertEqual(len(app_data.join_short_stops([a, b], 120, 50)), 2)
        self.assertEqual(len(app_data.join_short_stops([a, b], 150, 50)), 1)

    def test_far_apart_keeps_two(self):
        a, b = chain()
        b["lat"] += 0.01
        self.assertEqual(len(app_data.join_short_stops([a, b], 120, 50)), 2)

    def test_a_repark_is_not_joined(self):
        a, b = chain()
        b["repark"] = True
        self.assertEqual(len(app_data.join_short_stops([a, b], 120, 50)), 2)

    def test_zero_turns_it_off(self):
        self.assertEqual(len(app_data.join_short_stops(list(chain()), 0, 50)), 2)

    def test_three_parts_and_the_order_of_the_list_is_kept(self):
        a, b = chain()
        c = drv(3, 720, 900, lat=b["lat1"], lon=b["lon1"], to="D")
        out = app_data.join_short_stops([c, b, a], 120, 50)                  # newest first stays newest first
        self.assertEqual(len(out), 1)
        self.assertEqual([p["id"] for p in out[0]["parts"]], ["d1", "d2", "d3"])
        self.assertEqual(len(out[0]["stops"]), 2)
        d = drv(4, 5000, 5600)
        out = app_data.join_short_stops([d, c, b, a], 120, 50)
        self.assertEqual([x["id"] for x in out], ["d4", "d1"])

    def test_the_drives_list_carries_the_parts(self):
        j = app_data.join_short_stops(list(chain()), 120, 50)
        t = dict(j[0], id="d1", k="drive")
        items = DL.merge([dict(t, **{"day": "2026-10-12", "hm": "10:00", "tz": "AEDT", "max": 60})], [], [])
        d = [i for i in items if i["kind"] == "drive"][0]
        self.assertEqual([p["id"] for p in d["parts"]], ["d1", "d2"])
        self.assertEqual(len(d["stops"]), 1)


if __name__ == "__main__":
    unittest.main()
