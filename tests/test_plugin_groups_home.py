"""One plug-in is one charge, whatever Tessie recorded: Home/AC records too (12 Nov 2026: 12:36-13:28 and 13:30-14:55 at Home, same odometer, 2 minutes apart)."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
from lib.charging_analyzer import ChargingAnalyzer


def ch(t0, t1, odo=12555.23, loc="1106 Victoria Road", sc=False, fast=False):
    return dict(start_utc_epoch=t0, end_utc_epoch=t1, odometer_km=odo, location_raw=loc, latitude=-33.8000, longitude=151.0000, is_supercharger=sc, is_fast_charger=fast)


def groups(charges, disconnected=None):
    a = ChargingAnalyzer.__new__(ChargingAnalyzer)
    a.charges = charges
    a._telemetry_disconnected_between = lambda x, y: disconnected
    return a.build_plugin_groups()


class HomeGroupTest(unittest.TestCase):
    def test_home_records_two_minutes_apart_are_one_plug_in(self):
        g = groups([ch(1794447391, 1794450535), ch(1794450654, 1794455736)], disconnected=False)
        self.assertEqual(g[0], (0, [0, 1]))
        self.assertEqual(g[1], (0, [0, 1]))

    def test_no_telemetry_within_ten_minutes_still_joins(self):
        g = groups([ch(0, 3000), ch(3119, 6000)])
        self.assertEqual(g[1][0], 0)

    def test_a_drive_between_is_two_charges(self):
        self.assertEqual(groups([ch(0, 3000, odo=12543.16), ch(3119, 6000, odo=12555.23)], disconnected=False), {})

    def test_unplugged_between_is_two_charges(self):
        self.assertEqual(groups([ch(0, 3000), ch(3119, 6000)], disconnected=True), {})

    def test_a_long_pause_is_two_charges(self):
        self.assertEqual(groups([ch(0, 3000), ch(3000 + 2160, 9000)], disconnected=False), {})

    def test_another_place_is_two_charges(self):
        b = ch(3119, 6000, loc="elsewhere")
        b["latitude"], b["longitude"] = -33.9, 151.2
        self.assertEqual(groups([ch(0, 3000), b], disconnected=False), {})

    def test_a_home_record_never_joins_a_public_one(self):
        self.assertEqual(groups([ch(0, 3000), ch(3119, 6000, sc=True)], disconnected=False), {})

    def test_superchargers_as_before(self):
        g = groups([ch(0, 3000, odo=1, sc=True), ch(3300, 6000, odo=1, sc=True)], disconnected=False)
        self.assertEqual(g[1][0], 0)


if __name__ == "__main__":
    unittest.main()
