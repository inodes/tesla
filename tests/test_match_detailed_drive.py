"""A drive's telemetry file must not be taken for the telemetry of the next drive just because it runs a minute or two past its end."""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
from lib.drives.analyzer import DriveAnalyzer  # noqa: E402


def drive(start, minutes):
    s = datetime(2026, 6, 1, 8, 0) + timedelta(minutes=start)
    e = s + timedelta(minutes=minutes)
    return dict(start_dt=s, end_dt=e)


def rec(start, minutes):
    s = datetime(2026, 6, 1, 8, 0) + timedelta(minutes=start)
    return dict(start_datetime=s, end_datetime=s + timedelta(minutes=minutes))


class MatchTest(unittest.TestCase):
    def an(self, recs):
        a = DriveAnalyzer.__new__(DriveAnalyzer)
        a.detailed_drives = recs
        a._drive_start_collision_epochs = lambda: set()
        return a

    def test_a_file_that_spills_into_the_next_drive_is_not_its_telemetry(self):
        a = self.an([rec(0, 8.7)])                       # the first drive's file runs 98 s past its end
        self.assertIsNotNone(a.match_detailed_drive(drive(0, 7)))
        self.assertIsNone(a.match_detailed_drive(drive(7, 35)))

    def test_a_partial_file_inside_a_longer_drive_still_matches(self):
        a = self.an([rec(5, 6)])
        self.assertIsNotNone(a.match_detailed_drive(drive(2, 30)))

    def test_a_file_starting_with_the_drive_matches(self):
        a = self.an([rec(0, 3)])
        self.assertIsNotNone(a.match_detailed_drive(drive(0, 30)))


class JsonMatchTest(unittest.TestCase):
    """A JSON file carries UTC; the matcher compares those instants, so a drive in any zone matches its own file and only that."""

    def parsed(self, d, lat, lon, t0, n):
        import json
        import tempfile
        p = os.path.join(d, "drive_deepdive_x.json")
        json.dump({"results": [{"timestamp": t0 + i * 10, "latitude": lat, "longitude": lon, "speed": 40, "shift_state": "D"} for i in range(n)]}, open(p, "w"))
        a = DriveAnalyzer.__new__(DriveAnalyzer)
        a._drive_start_collision_epochs = lambda: set()
        rec_ = a.parse_detailed_drive_csv(p)
        a.detailed_drives = [rec_]
        return a, rec_

    def test_a_drive_is_matched_by_utc_in_sydney_and_in_brisbane(self):
        import tempfile
        for lat, lon, zone in ((-33.8, 151.0, "Australia/Sydney"), (-27.47, 153.03, "Australia/Brisbane"), (-31.95, 115.86, "Australia/Perth")):
            with tempfile.TemporaryDirectory() as d:
                t0 = 1768000000                                       # January: Sydney is on daylight saving, Brisbane is not
                a, r = self.parsed(d, lat, lon, t0, 60)               # ten minutes of points
                self.assertEqual((r["start_epoch"], r["end_epoch"]), (t0, t0 + 590))
                from datetime import timezone
                dr = dict(start_dt=datetime.fromtimestamp(t0, timezone.utc).replace(tzinfo=None), end_dt=datetime.fromtimestamp(t0 + 600, timezone.utc).replace(tzinfo=None),
                          start_utc_epoch=t0, end_utc_epoch=t0 + 600)
                self.assertIsNotNone(a.match_detailed_drive(dr), zone)
                later = dict(dr, start_utc_epoch=t0 + 3600, end_utc_epoch=t0 + 4200)
                self.assertIsNone(a.match_detailed_drive(later), zone)


class OwnTelemetryTest(unittest.TestCase):
    """The sync decides what to fetch with has_own_telemetry(): a file filed under another drive's folder is not this drive's telemetry."""

    def an(self, folder, drives, rec_epochs):
        a = DriveAnalyzer.__new__(DriveAnalyzer)
        a.drives_dirs = ["/nonexistent"]
        a.drives = [dict(start_utc_epoch=e) for e in drives]
        a._drive_stamp = staticmethod(lambda d, e: "S%d" % e)
        a.has_local_deepdive = lambda d: False
        a.match_detailed_drive = lambda d: dict(start_epoch=rec_epochs, source_path="/x/%s/f.json" % folder)
        return a

    def test_a_file_in_the_folder_of_the_drive_before_is_not_its_telemetry(self):
        a = self.an("S1000", [1000, 1120], 1070)           # the file starts between the two, but its folder is the first drive's
        self.assertTrue(a.has_own_telemetry(dict(start_utc_epoch=1000)))
        self.assertFalse(a.has_own_telemetry(dict(start_utc_epoch=1120)))

    def test_a_file_in_no_drive_folder_still_counts(self):
        a = self.an("other", [1000], 1000)
        self.assertTrue(a.has_own_telemetry(dict(start_utc_epoch=1000)))


if __name__ == "__main__":
    unittest.main()
