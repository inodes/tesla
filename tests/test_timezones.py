"""test_timezones.py - lib/timezones.py: UTC epoch to local time with the right abbreviation and offset, Tessie's
fixed-Sydney CSV times turned back into true UTC, and the GPS-to-zone guess used when timezonefinder is missing.

Made-up points only (no real places). Runs without config or data:
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_timezones.py
"""
import os
import sys
import unittest
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
from lib import timezones as TZ  # noqa: E402

# 2026-01-15 01:00:00 UTC (Sydney summer) and 2026-07-15 01:00:00 UTC (Sydney winter)
SUMMER_EPOCH = 1768438800
WINTER_EPOCH = 1784077200


class LocalizeTest(unittest.TestCase):
    def test_sydney_summer_is_aedt_plus_11(self):
        dt, abbr, off = TZ.localize(SUMMER_EPOCH, "Australia/Sydney")
        self.assertEqual((dt.strftime("%Y-%m-%d %H:%M"), abbr, off), ("2026-01-15 12:00", "AEDT", "+11:00"))

    def test_sydney_winter_is_aest_plus_10(self):
        dt, abbr, off = TZ.localize(WINTER_EPOCH, "Australia/Sydney")
        self.assertEqual((dt.strftime("%H:%M"), abbr, off), ("11:00", "AEST", "+10:00"))

    def test_adelaide_half_hour_offset(self):
        _dt, abbr, off = TZ.localize(SUMMER_EPOCH, "Australia/Adelaide")
        self.assertEqual((abbr, off), ("ACDT", "+10:30"))
        _dt, abbr, off = TZ.localize(WINTER_EPOCH, "Australia/Adelaide")
        self.assertEqual((abbr, off), ("ACST", "+09:30"))

    def test_brisbane_and_perth_have_no_daylight_saving(self):
        for epoch in (SUMMER_EPOCH, WINTER_EPOCH):
            self.assertEqual(TZ.localize(epoch, "Australia/Brisbane")[1:], ("AEST", "+10:00"))
            self.assertEqual(TZ.localize(epoch, "Australia/Perth")[1:], ("AWST", "+08:00"))

    def test_missing_epoch_and_bad_zone(self):
        self.assertEqual(TZ.localize(None), (None, None, None))
        self.assertEqual(TZ.localize(""), (None, None, None))
        # an unknown zone name falls back to Sydney rather than raising
        self.assertEqual(TZ.localize(WINTER_EPOCH, "Not/AZone")[1], "AEST")

    def test_epoch_as_string_and_float(self):
        self.assertEqual(TZ.localize(str(WINTER_EPOCH))[0], TZ.localize(WINTER_EPOCH)[0])
        self.assertEqual(TZ.localize(float(WINTER_EPOCH) + 0.7)[0], TZ.localize(WINTER_EPOCH)[0])

    def test_epoch_to_utc_iso(self):
        self.assertEqual(TZ.epoch_to_utc_iso(SUMMER_EPOCH), "2026-01-15T01:00:00Z")
        self.assertEqual(TZ.epoch_to_utc_iso(None), "")
        self.assertEqual(TZ.epoch_to_utc_iso(""), "")


class NaiveLocalTest(unittest.TestCase):
    def test_round_trip_epoch_local(self):
        for epoch in (SUMMER_EPOCH, WINTER_EPOCH):
            dt, _a, _o = TZ.localize(epoch, "Australia/Sydney")
            self.assertEqual(TZ.epoch_for_naive_local(dt.replace(tzinfo=None), "Australia/Sydney"), epoch)

    def test_abbr_for_naive_local(self):
        self.assertEqual(TZ.abbr_for_naive_local(datetime(2026, 1, 15, 12, 0)), "AEDT")
        self.assertEqual(TZ.abbr_for_naive_local(datetime(2026, 7, 15, 12, 0)), "AEST")
        self.assertEqual(TZ.abbr_for_naive_local(datetime(2026, 7, 15, 12, 0), "Australia/Adelaide"), "ACST")

    def test_sydney_offset_around_the_changeovers(self):
        # 2026: daylight saving ends Sun 5 Apr 03:00 AEDT, starts Sun 4 Oct 02:00 AEST
        self.assertEqual(TZ.sydney_offset_seconds(datetime(2026, 4, 4, 12, 0)), 11 * 3600)
        self.assertEqual(TZ.sydney_offset_seconds(datetime(2026, 4, 5, 12, 0)), 10 * 3600)
        self.assertEqual(TZ.sydney_offset_seconds(datetime(2026, 10, 3, 12, 0)), 10 * 3600)
        self.assertEqual(TZ.sydney_offset_seconds(datetime(2026, 10, 4, 12, 0)), 11 * 3600)


class NormalizeRowTest(unittest.TestCase):
    def test_summer_label_column_is_read_and_converted(self):
        # Tessie names the column after the exporter's zone of the day: "(AEDT)" in summer
        row = {"Started At (AEDT)": "2026-01-15 12:00", "Latitude": "-33.5", "Longitude": "150.5"}
        TZ.normalize_row_timezone(row, "Latitude", "Longitude", "Started At")
        self.assertEqual(row["Started At (UTC)"], str(SUMMER_EPOCH))
        self.assertTrue(row["Started At (Local)"].startswith("2026-01-15 12:00"))
        self.assertTrue(row["Started At TZ"])

    def test_true_utc_does_not_depend_on_the_event_zone(self):
        # Tessie always writes Sydney wall-clock time; a row in Perth still decodes to the same UTC instant
        row = {"Started At (AEST)": "2026-07-15 11:00", "Latitude": "-31.0", "Longitude": "116.0"}
        TZ.normalize_row_timezone(row, "Latitude", "Longitude", "Started At")
        self.assertEqual(row["Started At (UTC)"], str(WINTER_EPOCH))

    def test_already_migrated_row_is_left_alone(self):
        row = {"Started At (UTC)": "123", "Started At TZ": "X", "Started At (Local)": "Y", "Started At (AEST)": "2026-07-15 11:00"}
        TZ.normalize_row_timezone(row, "Latitude", "Longitude", "Started At")
        self.assertEqual((row["Started At (UTC)"], row["Started At TZ"], row["Started At (Local)"]), ("123", "X", "Y"))

    def test_empty_and_unparsable(self):
        row = {}
        TZ.normalize_row_timezone(row, "Latitude", "Longitude", "Started At")
        self.assertEqual((row["Started At (UTC)"], row["Started At TZ"], row["Started At (Local)"]), ("", "", ""))
        row = {"Started At (AEST)": "not a date"}
        TZ.normalize_row_timezone(row, "Latitude", "Longitude", "Started At")
        self.assertEqual((row["Started At (UTC)"], row["Started At (Local)"]), ("", "not a date"))

    def test_legacy_get(self):
        self.assertEqual(TZ.legacy_get({"Ended At (AEST)": "a"}, "Ended At"), "a")
        self.assertEqual(TZ.legacy_get({"Ended At (AEDT)": "b"}, "Ended At"), "b")
        self.assertEqual(TZ.legacy_get({"Ended At (ACDT)": "c"}, "Ended At"), "c")
        # our own migrated columns are never mistaken for Tessie's
        self.assertIsNone(TZ.legacy_get({"Ended At (UTC)": "1", "Ended At (Local)": "2"}, "Ended At"))
        self.assertIsNone(TZ.legacy_get({}, "Ended At"))
        self.assertIsNone(TZ.legacy_get(None, "Ended At"))


class ZoneGuessTest(unittest.TestCase):
    """The fallback used when timezonefinder is not installed (_heuristic_timezone)."""

    def test_missing_or_bad_coordinates_give_the_default(self):
        self.assertEqual(TZ.resolve_timezone(None, 150.0), TZ.DEFAULT_TIMEZONE)
        self.assertEqual(TZ.resolve_timezone("x", "y"), TZ.DEFAULT_TIMEZONE)

    def test_states(self):
        cases = [
            ((-31.0, 120.0), "Australia/Perth"),       # inland WA
            ((-20.0, 133.0), "Australia/Darwin"),      # central NT
            ((-31.0, 136.0), "Australia/Adelaide"),    # inland SA
            ((-24.0, 148.0), "Australia/Brisbane"),    # inland QLD
            ((-42.0, 146.5), "Australia/Hobart"),      # central TAS
            ((-33.5, 148.0), "Australia/Sydney"),      # inland NSW
            ((-37.0, 145.0), "Australia/Sydney"),      # VIC (same offsets as Sydney)
        ]
        for (lat, lon), zone in cases:
            with self.subTest(lat=lat, lon=lon):
                self.assertEqual(TZ._heuristic_timezone(lat, lon), zone)

    def test_far_north_nsw_is_not_queensland(self):
        """Was a bug (fixed in the second test-gap pass): every point north of 29 S east of 141 E was called
        Brisbane, but the coastal NSW/QLD border is near 28.2 S. The fallback now follows the same border line
        as resources/state_boundaries.json."""
        self.assertEqual(TZ._heuristic_timezone(-28.8, 153.3), "Australia/Sydney")   # far-north NSW coast
        self.assertEqual(TZ._heuristic_timezone(-29.2, 151.5), "Australia/Sydney")   # NSW tablelands
        self.assertEqual(TZ._heuristic_timezone(-28.0, 153.3), "Australia/Brisbane") # QLD side of the coast
        self.assertEqual(TZ._heuristic_timezone(-28.8, 145.0), "Australia/Brisbane") # inland QLD, north of 29 S
        self.assertEqual(TZ._heuristic_timezone(-29.2, 145.0), "Australia/Sydney")   # inland NSW, south of 29 S


if __name__ == "__main__":
    unittest.main()
