import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools"))
from lib import app_data


class Stub:
    def __init__(self, dr, ch):
        self._dr, self._ch, self.d = dr, ch, "x"
    drives = lambda self: self._dr
    charges = lambda self: self._ch
    idles = lambda self: []
    spec = lambda self: {"vehicle": {"model": "Model Y"}}


class SummaryOdo(unittest.TestCase):
    def test_header_shows_odometer_not_logged_distance(self):
        dr = [dict(t0=1, km=10.0, odo=50.0, odo1=60.1), dict(t0=2, km=20.0, odo=60.1, odo1=80.4)]
        s = app_data.Store.summary(Stub(dr, [dict(t0=3, odo=79.0)]))
        self.assertEqual(s["km"], 30)
        self.assertEqual(s["odo"], 80)

    def test_no_odometer_is_none(self):
        s = app_data.Store.summary(Stub([dict(t0=1, km=5.0)], []))
        self.assertIsNone(s["odo"])


if __name__ == "__main__":
    unittest.main()
