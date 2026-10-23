import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import drive_paths as DP  # noqa: E402
import drive_points as P  # noqa: E402
import app_route  # noqa: E402


def pt(t, lat=-33.8, lon=151.0, v=40):
    return {"id": t, "timestamp": t, "state": "online", "shift_state": "D", "latitude": lat, "longitude": lon, "speed": v, "elevation": 14, "locked": 1, "power": None}


def write_json(folder, stamp, pts):
    os.makedirs(folder, exist_ok=True)
    p = os.path.join(folder, "drive_deepdive_%s.json" % stamp)
    json.dump({"results": pts}, open(p, "w"))
    return p


class LoaderTest(unittest.TestCase):
    def test_json_is_utc_and_a_point_is_never_stored_twice(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_json(d, "2026-06-01_08-46_AEST", [pt(1780267478), pt(1780267418), pt(1780267418, v=99)])
            self.assertEqual(P.epochs(p), [1780267418.0, 1780267478.0])                    # sorted, the duplicate second dropped (first wins)
            t = P.table(p)
            self.assertEqual(t[0][0], "Timestamp")                                          # no zone in any heading
            self.assertEqual(t[1][0], "2026-06-01 08:43:38")                                # Sydney in June = UTC+10
            self.assertEqual(t[1][P.HEADINGS.index("Speed (km/h)")], "40")
            self.assertEqual(t[1][P.HEADINGS.index("Power (kW)")], "")                      # null -> empty cell

    def test_the_zone_is_a_value_worked_out_from_the_source_data_alone(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_json(d, "2026-01-10_09-00_AEST", [pt(1768000000, lat=-27.47, lon=153.03)])      # Brisbane
            self.assertEqual(P.zone_of(p), "Australia/Brisbane")
            self.assertEqual(P.table(p)[1][0], "2026-01-10 09:06:40")                       # UTC+10 (Brisbane has no daylight saving)
            json.dump({"tz": "Australia/Perth"}, open(os.path.join(d, "drive_summary.json"), "w"))      # a copy made from the data: never the source of the zone
            self.assertEqual(P.zone_of(p), "Australia/Brisbane")
            q = write_json(os.path.join(d, "w"), "2026-01-10_09-00_AWST", [pt(1768000000, lat=-31.95, lon=115.86)])   # Perth
            self.assertEqual(P.table(q)[1][0], "2026-01-10 07:06:40")                       # UTC+8

    def test_old_csv_is_converted_with_the_zone_rules_of_its_date_not_its_heading(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "drive_deepdive_2026-06-01_08-46_AEST.csv")
            open(p, "w").write('"Timestamp (AEDT)","Latitude","Longitude"\n"2026-06-01 08:44:08","-33.8","151.0"\n')
            self.assertEqual(P.epochs(p), [1780267448.0])
            self.assertEqual(P.table(p)[0][0], "Timestamp")
            self.assertEqual(P.rows(p)[0]["Latitude"], "-33.8")

    def test_an_old_csv_is_read_on_sydney_text_but_its_zone_is_where_it_began(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "drive_deepdive_2026-01-10_09-00_AEST.csv")
            open(p, "w").write('"Timestamp (AEDT)","Latitude","Longitude"\n"2026-01-10 10:06:40","-27.47","153.03"\n')
            self.assertEqual(P.text_zone(p), "Australia/Sydney")               # the text was asked for in Sydney time
            self.assertEqual(P.zone_of(p), "Australia/Brisbane")               # the drive itself began in Queensland
            self.assertEqual(P.epochs(p), [1768000000.0])

    def test_json_wins_over_csv_for_the_same_drive(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "2026-06-01", "2026-06-01_08-46_AEST")
            os.makedirs(f)
            open(os.path.join(f, "drive_deepdive_2026-06-01_08-46_AEST.csv"), "w").write("Timestamp\n")
            j = write_json(f, "2026-06-01_08-46_AEST", [pt(1780267418)])
            self.assertEqual(DP.list_data(d), [j])
            self.assertEqual(DP.find_data(d, "2026-06-01_08-46_AEST"), j)
            self.assertEqual(DP.stamp_of(j), "2026-06-01_08-46_AEST")

    def test_route_points_are_the_same_instants_from_either_form(self):
        with tempfile.TemporaryDirectory() as d:
            j = write_json(d, "2026-06-01_08-46_AEST", [pt(1780267448)])
            c = os.path.join(d, "drive_deepdive_2026-06-01_08-47_AEST.csv")
            open(c, "w").write('"Timestamp (AEST)","Latitude","Longitude","Speed (km/h)"\n"2026-06-01 08:44:08","-33.8","151.0","40"\n')
            self.assertEqual(app_route.points(j), app_route.points(c))


class FetchTest(unittest.TestCase):
    def test_the_fetch_asks_for_json_in_utc_and_stores_the_answer_unchanged(self):
        import lib.drives.analyzer as A
        import lib.tessie_api_common as T
        body = json.dumps({"results": [pt(1780267418 + i) for i in range(5)]}).encode()
        seen = {}

        def fake_api_get(path, vin, token, params):
            seen.update(params)
            return 200, body
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "drives"))
            an = A.DriveAnalyzer.__new__(A.DriveAnalyzer)
            an.drives_dirs = [os.path.join(d, "drives")]
            an.quiet_fetch = True
            drive = {"start_utc_epoch": 1780267418, "end_utc_epoch": 1780267422, "start_tz_abbr": "AEST"}
            with mock.patch.object(T, "load_config", return_value={}), mock.patch.object(T, "get_token", return_value="x"), \
                    mock.patch.object(T, "get_vin", return_value="v"), mock.patch.object(T, "api_get", fake_api_get):
                path = A.DriveAnalyzer.fetch_drive_deepdive_from_api(an, drive)
            self.assertTrue(path and path.endswith(".json"))
            self.assertEqual(open(path, "rb").read(), body)                                  # Tessie's answer, byte for byte
            self.assertEqual((seen["format"], seen["timezone"]), ("json", "UTC"))
            parsed = an.parse_detailed_drive_csv(path)
            self.assertEqual((parsed["start_epoch"], parsed["end_epoch"]), (1780267418.0, 1780267422.0))

class CondensedTest(unittest.TestCase):
    """Tessie's default answer: one object of parallel arrays (seen in the real log on 2026-10-23)."""

    def body(self):
        n = 4
        return json.dumps({"results": {
            "ids": list(range(n)), "timestamps": [1792724002 + 30 * i for i in range(n)], "states": ["online"] * n,
            "shift_states": ["D", "D", "R", "P"], "battery_levels": [46] * n, "latitudes": [-33.8 + i / 1000 for i in range(n)],
            "longitudes": [151.2] * n, "speeds": [0, 20, 5, None], "charger_phasess": [None] * n, "is_climate_ons": [True, True, False, False],
            "lifetime_energy_used": [100.5] * n}}).encode()

    def test_condensed_reads_as_the_same_points_as_the_list_form(self):
        pts = P.parse_json_bytes(self.body())
        self.assertEqual(len(pts), 4)
        self.assertEqual(pts[2]["timestamp"], 1792724062)
        self.assertEqual(pts[2]["shift_state"], "R")
        self.assertEqual(pts[1]["speed"], 20)
        self.assertAlmostEqual(pts[3]["latitude"], -33.797)
        self.assertEqual(pts[0]["lifetime_energy_used"], 100.5)
        self.assertIn("charger_phases", pts[0])
        self.assertNotIn("ids", pts[0])

    def test_a_stored_condensed_file_reads_like_any_other(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "drive_deepdive_x.json")
        open(p, "wb").write(self.body())
        t = P.table(p)
        self.assertEqual(len(t), 5)
        i = t[0].index("Shift State")
        self.assertEqual([r[i] for r in t[1:]], ["D", "D", "R", "P"])
        self.assertEqual(P.epochs(p)[0], 1792724002.0)
        self.assertEqual(P.rows(p)[0]["Climate"], "1")

    def test_other_shapes_are_still_no_points(self):
        for b in (b'{"results": {}}', b'{"results": {"timestamps": 5}}', b'{"results": "x"}', b"nope"):
            self.assertEqual(P.parse_json_bytes(b), [], b)

    def test_the_app_asks_for_the_list_form(self):
        import tessie_api_common as T
        self.assertEqual(T.STATES_PARAMS["condense"], "false")

    def test_a_replaced_file_of_the_same_size_and_date_is_read_again(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "drive_deepdive_x.json")
        def put(speed):
            tmp = p + ".tmp"
            open(tmp, "w").write(json.dumps({"results": [{"timestamp": 1792724002, "speed": speed}]}))
            os.utime(tmp, (1780000000, 1780000000))
            os.replace(tmp, p)
        put(42)
        self.assertEqual(P.rows(p)[0]["Speed (km/h)"], "42")
        put(49)
        self.assertEqual(P.rows(p)[0]["Speed (km/h)"], "49")


if __name__ == "__main__":
    unittest.main()
