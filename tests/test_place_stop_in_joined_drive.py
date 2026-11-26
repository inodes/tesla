"""A short stop at a saved place between two of Tessie's drives (they are joined into one) still counts as an arrival at that place."""
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


def row(t0, t1, a, la, lo, b, lb, lob, km):
    return {"Started At (UTC)": t0, "Started At TZ": "Australia/Sydney", "Started At (Local)": "2026-08-25 22:20 AEST", "Ended At (UTC)": t1, "Ended At TZ": "Australia/Sydney",
            "Ended At (Local)": "2026-08-25 22:26 AEST", "Duration (Minutes)": (t1 - t0) // 60, "Starting Location": a, "Starting Latitude": la, "Starting Longitude": lo,
            "Ending Location": b, "Ending Latitude": lb, "Ending Longitude": lob, "Distance (km)": km}


class StopInJoinedDrive(unittest.TestCase):
    def store(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "drives"))
        t = 1787656800
        with open(os.path.join(d, "drives", "drives_master.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, COLS, restval="")
            w.writeheader()
            w.writerow(row(t, t + 360, "1 Start Street", -33.85, 151.00, "2 Gym Street", -33.83145, 151.05022, 4.86))
            w.writerow(row(t + 400, t + 1300, "2 Gym Street", -33.83145, 151.05022, "3 End Street", -33.79, 151.10, 9.8))
        with open(os.path.join(d, "places.json"), "w") as fh:
            json.dump({"Gym": {"lat": -33.83145, "lon": 151.05022, "radius_m": 150}}, fh)
        return app_data.Store(d)

    def test_joined_drive_has_the_stop(self):
        D = self.store().drives()
        self.assertEqual(len(D), 1)
        self.assertEqual(len(D[0]["stops"]), 1)

    def test_the_stop_is_an_arrival_at_the_saved_place(self):
        S = self.store()
        ev = [e for e in S.pl._events(S) if e["key"] == "p:Gym"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["k"], "drive")
        self.assertTrue(ev[0]["stop"])
        self.assertEqual(S.pl.listing(S)["saved"][0]["drives"], 1)


if __name__ == "__main__":
    unittest.main()
