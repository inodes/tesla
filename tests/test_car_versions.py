"""test_car_versions.py - the version log: what the car is on, when it was first seen, and the FSD version.

    python3 -m unittest tests/test_car_versions.py
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import car_versions as CV  # noqa: E402

NOTES = [dict(title="Full Self-Driving (Supervised)", subtitle="v14.3.12", description="Improved merging."), dict(title="Minor Fixes", subtitle=None, description="x")]


class CarVersionTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_the_version_and_build_are_split(self):
        self.assertEqual(CV.parse_car_version("2026.38.5.5 3f9a1c2b7d"), ("2026.38.5.5", "3f9a1c2b7d"))
        self.assertEqual(CV.parse_car_version(""), ("", ""))

    def test_fsd_is_read_from_the_notes_and_not_confused_with_the_software_version(self):
        self.assertEqual(CV.fsd_from_notes(NOTES), "14.3.12")
        self.assertEqual(CV.fsd_from_notes([dict(title="FSD (Supervised) v14.3.5", description="")]), "14.3.5")
        self.assertIsNone(CV.fsd_from_notes([dict(title="Software 2026.38.5.5", description="Minor fixes")]))
        self.assertIsNone(CV.fsd_from_notes(None))

    def test_a_new_version_is_logged_once_with_the_time_first_seen(self):
        e, new = CV.record(self.d, "2026.38.5.5 abc", NOTES, now=1792700000)
        self.assertTrue(new)
        e2, new2 = CV.record(self.d, "2026.38.5.5 abc", None, now=1792800000)
        self.assertFalse(new2)
        self.assertEqual(e2["first_seen"], e["first_seen"])
        self.assertNotEqual(e2["last_seen"], e["first_seen"])
        self.assertEqual(len(CV.history(self.d)), 1)
        self.assertEqual(CV.load(self.d)["current"], "2026.38.5.5")

    def test_a_staged_update_is_noted_and_the_history_is_newest_first(self):
        CV.record(self.d, "2026.30.1 a", now=1790000000)
        CV.record(self.d, "2026.38.5.5 b", NOTES, staged="2026.40.1 c", now=1792700000)
        self.assertEqual([v["version"] for v in CV.history(self.d)], ["2026.38.5.5", "2026.30.1"])
        self.assertEqual(CV.load(self.d)["staged"], "2026.40.1")

    def test_an_fsd_version_entered_by_hand_is_kept(self):
        CV.enter(self.d, "2026.38.5.5", fsd="14.3.99", first_seen="2026-10-22")
        CV.record(self.d, "2026.38.5.5 abc", NOTES, now=1792700000)
        self.assertEqual(CV.history(self.d)[0]["fsd"], "14.3.99")

    def test_nothing_is_written_without_a_version(self):
        self.assertEqual(CV.record(self.d, "", None), (None, False))


if __name__ == "__main__":
    unittest.main()


class JourneyTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_a_change_of_version_is_logged_and_the_journey_has_both_ends(self):
        CV.enter(self.d, "2026.8.200", first_seen="2026-05-31T22:34:00Z")
        CV.enter(self.d, "2026.20.10", fsd="14.3.5", first_seen="2026-07-28T06:46:29Z", between=["2026-07-28T06:46:29Z", "2026-07-29T09:23:00Z"], note="first FSD drive 2026-07-29")
        CV.record(self.d, "2026.20.10 aaa", None, now=1790000000)
        CV.record(self.d, "2026.38.5.5 bbb", NOTES, staged="2026.38.9 ccc", now=1792700000)
        j = CV.journey(self.d)
        self.assertEqual([x["version"] for x in j], ["2026.8.200", "2026.20.10", "2026.38.5.5"])
        self.assertEqual(j[0]["to"], j[1]["from"])
        self.assertIsNone(j[2]["to"])
        self.assertTrue(j[2]["current"])
        self.assertEqual(j[1]["between"][1], "2026-07-29T09:23:00Z")
        d = CV.load(self.d)
        self.assertEqual([(c["frm"], c["to"]) for c in d["changes"]], [("2026.20.10", "2026.38.5.5")])
        self.assertEqual([u["version"] for u in d["updates"]], ["2026.38.9"])

    def test_a_staged_update_is_logged_once(self):
        CV.record(self.d, "2026.38.5.5", None, staged="2026.38.9", now=1792700000)
        CV.record(self.d, "2026.38.5.5", None, staged="2026.38.9", now=1792800000)
        self.assertEqual(len(CV.load(self.d)["updates"]), 1)


class WatchTests(unittest.TestCase):
    def test_a_look_records_the_version_and_asks_for_the_notes_once(self):
        import json
        import car_watch as W
        d = tempfile.mkdtemp()
        asked = []

        def api(path, vin, token, params=None):
            asked.append(path)
            if path == "state":
                return 200, json.dumps({"vehicle_state": {"car_version": "2026.38.5.5 abc", "software_update": {"version": "2026.38.9 x"}}})
            return 200, json.dumps({"deployed_version": "2026.38.5.5 abc", "release_notes": NOTES})
        r = W.check(d, token="t", vin="v", api=api)
        self.assertEqual((r["version"], r["new"], r["fsd"], r["staged"]), ("2026.38.5.5", True, "14.3.12", "2026.38.9"))
        W.check(d, token="t", vin="v", api=api)
        self.assertEqual(asked.count("release_notes/"), 1)
        self.assertEqual(asked.count("state"), 2)

    def test_an_error_is_returned_not_raised(self):
        import car_watch as W
        self.assertIn("error", W.check(tempfile.mkdtemp(), token="t", vin="v", api=lambda *a, **k: (500, "")))
        self.assertIn("error", W.check(tempfile.mkdtemp(), token="t", vin="v", api=lambda *a, **k: (200, "{}")))
