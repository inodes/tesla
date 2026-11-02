"""A drive that starts and ends in one saved place (a car park with two street addresses) is a re-park, not a drive."""
import csv
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import app_data  # noqa: E402

COLS = ["Started At (UTC)", "Started At TZ", "Started At (Local)", "Ended At (UTC)", "Ended At TZ", "Ended At (Local)", "Duration (Minutes)", "Starting Location", "Starting Saved Location",
        "Starting Latitude", "Starting Longitude", "Ending Location", "Ending Saved Location", "Ending Latitude", "Ending Longitude", "Distance (km)"]


def row(t0, t1, mins, a, la, lo, b, lb, lob, km):
    return {"Started At (UTC)": t0, "Started At TZ": "Australia/Sydney", "Started At (Local)": "2026-10-31 14:00 AEDT", "Ended At (UTC)": t1, "Ended At TZ": "Australia/Sydney",
            "Ended At (Local)": "2026-10-31 14:02 AEDT", "Duration (Minutes)": mins, "Starting Location": a, "Starting Latitude": la, "Starting Longitude": lo,
            "Ending Location": b, "Ending Latitude": lb, "Ending Longitude": lob, "Distance (km)": km}


class Repark(unittest.TestCase):
    def drives(self, saved):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "drives"))
        with open(os.path.join(d, "drives", "drives_master.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, COLS, restval="")
            w.writeheader()
            w.writerow(row(1793415630, 1793415761, 2, "43-49 Goulburn Street, Sydney NSW", -33.877758, 151.206040, "397 Pitt Street, Sydney NSW", -33.876995, 151.207080, 0.18))
        if saved:
            with open(os.path.join(d, "places.json"), "w") as fh:
                json.dump({"World Square": {"lat": -33.87729, "lon": 151.20726, "radius_m": 150}}, fh)
        return app_data.Store(d).drives()

    def test_two_addresses_in_one_saved_place_is_a_repark(self):
        self.assertTrue(self.drives(True)[0]["repark"])

    def test_without_the_saved_place_it_is_a_drive(self):
        self.assertFalse(self.drives(False)[0]["repark"])


if __name__ == "__main__":
    unittest.main()
