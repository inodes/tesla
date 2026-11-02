"""test_distances_places.py - distances and where a point is: the haversine copies (lib/geo_common.py x2,
lib/route_common.py, lib/charging_analyzer.py in metres, lib/dashcam_clips.py) agree with each other and with the
textbook value, point-to-segment distance, state from an address or postcode, state from GPS (with a made-up border
and no network), the station time zone, the route stop score, and lib/car_efficiency.py's grade correction.

Made-up points, names and postcodes only. No network: the reverse geocoder and the geo cache are replaced by fakes.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_distances_places.py
"""
import math
import os
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
from lib import geo_common as GC  # noqa: E402
from lib import route_common as RC  # noqa: E402
from lib import car_efficiency as CE  # noqa: E402
from lib import charging_analyzer as CA  # noqa: E402
from lib import dashcam_clips as DC  # noqa: E402

ONE_DEG_KM = 6371.0 * math.pi / 180.0   # 111.195 km along a meridian


class HaversineTest(unittest.TestCase):
    PAIRS = [
        ((-33.0, 150.0), (-34.0, 150.0)),     # 1 degree of latitude
        ((-33.0, 150.0), (-33.0, 151.0)),
        ((-32.0, 116.0), (-34.0, 151.0)),      # across the continent (made-up points)
        ((-33.0, 150.0), (-33.0, 150.0)),
    ]

    def test_one_degree_of_latitude(self):
        self.assertAlmostEqual(GC.haversine_km(-33, 150, -34, 150), ONE_DEG_KM, places=6)
        self.assertAlmostEqual(CA.haversine_distance_m(-33, 150, -34, 150), ONE_DEG_KM * 1000, places=3)

    def test_longitude_shrinks_with_latitude(self):
        self.assertAlmostEqual(GC.haversine_km(0, 150, 0, 151), ONE_DEG_KM, places=6)
        self.assertAlmostEqual(GC.haversine_km(-60, 150, -60, 151), ONE_DEG_KM * 0.5, delta=0.1)

    def test_all_copies_agree(self):
        for a, b in self.PAIRS:
            with self.subTest(a=a, b=b):
                ref = GC.haversine_km(*a, *b)
                self.assertAlmostEqual(GC.haversine_distance_km(*a, *b), ref, places=9)
                self.assertAlmostEqual(RC.haversine_km(*a, *b), ref, places=9)
                self.assertAlmostEqual(CA.haversine_distance_m(*a, *b) / 1000.0, ref, places=9)
                # dashcam_clips uses a 12742 km diameter (6371 x 2): the same to well under a metre
                self.assertAlmostEqual(DC.hav_km(*a, *b), ref, delta=1e-6)

    def test_symmetric(self):
        a, b = self.PAIRS[2]
        self.assertAlmostEqual(GC.haversine_km(*a, *b), GC.haversine_km(*b, *a), places=9)

    def test_missing_or_bad_inputs_are_infinitely_far(self):
        self.assertEqual(GC.haversine_distance_km(None, 1, 2, 3), float("inf"))
        self.assertEqual(GC.haversine_distance_km("x", 1, 2, 3), float("inf"))
        self.assertEqual(CA.haversine_distance_m(None, 1, 2, 3), float("inf"))
        # strings that are numbers are fine
        self.assertAlmostEqual(GC.haversine_distance_km("-33", "150", "-34", "150"), ONE_DEG_KM, places=6)


class SegmentTest(unittest.TestCase):
    def test_point_beside_the_middle(self):
        # segment along the equator from lon 0 to lon 1; point 0.1 deg north of its middle
        d, t = RC.point_to_segment_distance_km(0.1, 0.5, 0.0, 0.0, 0.0, 1.0)
        self.assertAlmostEqual(t, 0.5, places=6)
        self.assertAlmostEqual(d, 0.1 * ONE_DEG_KM, delta=0.01)

    def test_point_past_the_ends_is_measured_to_the_end(self):
        d, t = RC.point_to_segment_distance_km(0.0, -1.0, 0.0, 0.0, 0.0, 1.0)
        self.assertEqual(t, 0.0)
        self.assertAlmostEqual(d, ONE_DEG_KM, delta=0.05)
        d, t = RC.point_to_segment_distance_km(0.0, 2.0, 0.0, 0.0, 0.0, 1.0)
        self.assertEqual(t, 1.0)
        self.assertAlmostEqual(d, ONE_DEG_KM, delta=0.05)

    def test_zero_length_segment(self):
        d, t = RC.point_to_segment_distance_km(0.1, 0.0, 0.0, 0.0, 0.0, 0.0)
        self.assertEqual(t, 0.0)
        self.assertAlmostEqual(d, 0.1 * ONE_DEG_KM, delta=0.01)

    def test_geo_common_segment_distance(self):
        # (px=lon, py=lat): 0.1 deg north of a segment on the equator, about 11.1 km with its 111 km/deg
        self.assertAlmostEqual(GC._dist_to_segment_km(0.5, 0.1, 0.0, 0.0, 1.0, 0.0), 11.1, places=6)
        self.assertAlmostEqual(GC._dist_to_segment_km(0.0, 0.1, 0.0, 0.0, 0.0, 0.0), 11.1, places=6)


class StateFromTextTest(unittest.TestCase):
    def test_state_code_in_title(self):
        self.assertEqual(GC.extract_au_state_from_text("Example Charger, QLD"), "QLD")
        self.assertEqual(GC.extract_au_state_from_text("Example Charger, Vic 3999"), "VIC")
        self.assertEqual(GC.extract_au_state_from_text("Example Charger WA"), "WA")

    def test_full_state_name(self):
        self.assertEqual(GC.extract_au_state_from_text("Example Charger", "Testville, South Australia"), "SA")
        self.assertEqual(GC.extract_au_state_from_text("Example Charger", "Testville, Tasmania"), "TAS")

    def test_postcodes(self):
        cases = {"2999": "NSW", "2600": "ACT", "2618": "ACT", "2619": "NSW", "2910": "ACT", "3999": "VIC",
                 "4999": "QLD", "5999": "SA", "6999": "WA", "7999": "TAS", "0870": "NT"}
        for pc, st in cases.items():
            with self.subTest(pc=pc):
                self.assertEqual(GC.extract_au_state_from_text("Example Charger", f"1 Example Lane, Testville {pc}"), st)

    def test_nothing_known(self):
        self.assertEqual(GC.extract_au_state_from_text("Example Charger", "no clue here"), "Other")

    def test_street_named_after_a_state(self):
        """Was a bug (fixed in the second test-gap pass): full state names were matched as bare substrings
        before the address's own state code and postcode, so a street named Victoria won over 'NSW 2999'. Now
        whichever state signal comes last in the address wins."""
        self.assertEqual(GC.extract_au_state_from_text("Example Charger", "1 Victoria Street, Testville NSW 2999"), "NSW")
        self.assertEqual(GC.extract_au_state_from_text("Example Charger", "1 Queensland Road, Testville 2999"), "NSW")
        self.assertEqual(GC.extract_au_state_from_text("Example Charger", "1 Tasmania Lane, Testville, Victoria"), "VIC")
        # a four-digit street number is not taken for the postcode when the state name comes after it
        self.assertEqual(GC.extract_au_state_from_text("Example Charger", "3000 Example Hwy, Testville, Western Australia"), "WA")

    def test_names(self):
        self.assertEqual(GC.clean_station_short_name("  Example -- Plaza (Level 2) "), "Example_Plaza_Level_2")
        self.assertEqual(GC.normalize_country_slug("aus"), "Australia")
        self.assertEqual(GC.normalize_country_slug("hong_kong"), "Hong+Kong")
        self.assertEqual(GC.normalize_country_slug("new zealand"), "New+Zealand")
        self.assertEqual(GC.normalize_country_slug("south africa"), "South+Africa")


# A made-up NSW/VIC border running straight from (141 E, 34 S) to (150 E, 37.5 S).
BOUNDS = {"bbox": [-44.5, 112.0, -10.0, 154.5], "border_threshold_km": 15.0,
          "states": {"ACT": {"bbox": [-35.92, 148.76, -35.12, 149.40]}},
          "borders": {"NSW_VIC": [[141.0, -34.0], [150.0, -37.5]]}}


REAL_LOAD_BOUNDARIES = GC.load_state_boundaries   # reads the committed resources/state_boundaries.json


class StateFromCoordsTest(unittest.TestCase):
    def setUp(self):
        self.geocode = mock.MagicMock(return_value=None)
        self.saved = mock.MagicMock()
        for target, value in (("load_state_boundaries", mock.MagicMock(return_value=BOUNDS)),
                              ("load_geo_cache", mock.MagicMock(return_value={})),
                              ("save_geo_cache", self.saved),
                              ("reverse_geocode_osm", self.geocode)):
            p = mock.patch.object(GC, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(GC.time, "sleep")
        p.start()
        self.addCleanup(p.stop)

    def test_far_from_any_border(self):
        cases = [((-31.0, 120.0), "WA"), ((-20.0, 133.0), "NT"), ((-31.0, 136.0), "SA"), ((-24.0, 139.0), "QLD"),
                 ((-35.3, 149.1), "ACT"), ((-35.0, 145.5), "NSW"), ((-36.5, 145.5), "VIC")]
        for (lat, lon), st in cases:
            with self.subTest(lat=lat, lon=lon):
                self.assertEqual(GC.state_from_coords(lat, lon), (st, None))
        self.geocode.assert_not_called()

    def test_outside_the_country_and_bad_input(self):
        self.assertEqual(GC.state_from_coords(0.0, 0.0), (None, None))
        self.assertEqual(GC.state_from_coords(None, 150.0), (None, None))
        self.assertEqual(GC.state_from_coords("x", 150.0), (None, None))

    def test_near_the_border_uses_the_cache_then_the_geocoder(self):
        # on the made-up line at lon 145.5: lat = -34 - 3.5 * 4.5 / 9 = -35.75
        with mock.patch.object(GC, "load_geo_cache", return_value={"-35.7500,145.5000": {"state": "VIC", "suburb": "Testville"}}):
            self.assertEqual(GC.state_from_coords(-35.75, 145.5), ("VIC", "Testville"))
        self.geocode.assert_not_called()
        self.geocode.return_value = {"state": "NSW", "suburb": "Exampleton"}
        self.assertEqual(GC.state_from_coords(-35.75, 145.5), ("NSW", "Exampleton"))
        self.saved.assert_called_once()

    def test_tasmania_with_the_committed_boundaries(self):
        with mock.patch.object(GC, "load_state_boundaries", REAL_LOAD_BOUNDARIES):
            self.assertEqual(GC.state_from_coords(-42.0, 146.5), ("TAS", None))   # inside its safe box
        self.geocode.assert_not_called()

    def test_north_coast_of_tasmania(self):
        """Was a bug (fixed in the second test-gap pass): a point on Tasmania's north coast (made-up point
        40.75 S, 145.2 E, north of the TAS safe box) reached the geometric fallback, where the NSW/VIC line was
        checked first and called everything south of it VIC. South of 39.2 S is now TAS before that line."""
        with mock.patch.object(GC, "load_state_boundaries", REAL_LOAD_BOUNDARIES):
            self.assertEqual(GC.state_from_coords(-40.75, 145.2), ("TAS", None))
            self.assertEqual(GC.state_from_coords(-39.9, 144.0), ("TAS", None))    # King Island area
            self.assertEqual(GC.state_from_coords(-40.0, 148.0), ("TAS", None))    # Flinders Island area
            self.assertEqual(GC.state_from_coords(-38.3, 144.5), ("VIC", None))    # south-coast VIC stays VIC

    def test_station_time_zone(self):
        self.assertEqual(CA.resolve_location_timezone("NSW"), "Australia/Sydney")
        self.assertEqual(CA.resolve_location_timezone("act"), "Australia/Sydney")
        self.assertEqual(CA.resolve_location_timezone("SA"), "Australia/Adelaide")
        self.assertEqual(CA.resolve_location_timezone("NT"), "Australia/Darwin")
        self.assertEqual(CA.resolve_location_timezone(lat=-31.0, lon=120.0), "Australia/Perth")
        self.assertEqual(CA.resolve_location_timezone(lat=-36.5, lon=145.5), "Australia/Melbourne")
        self.assertEqual(CA.resolve_location_timezone(country="nz"), "Pacific/Auckland")
        self.assertEqual(CA.resolve_location_timezone("CA", "United States"), "America/Los_Angeles")
        self.assertEqual(CA.resolve_location_timezone(country="Atlantis"), "UTC")


class RouteScoreTest(unittest.TestCase):
    def setUp(self):
        # an empty facilities registry: the score must not read the data folder
        stub = types.ModuleType("lib.station_facilities_common")
        self.registry = types.SimpleNamespace(get_station_facilities=lambda **kw: None)
        stub.get_facilities_registry = lambda: self.registry
        stub.resolve_station_physical_coords = lambda s: (None, None, None)
        p = mock.patch.dict(sys.modules, {"lib.station_facilities_common": stub})
        p.start()
        self.addCleanup(p.stop)

    def station(self, **kw):
        base = dict(key="k", name="Example", max_power_kw=None, stalls_total=None, metadata=None, amenities=None, detour_km=0.0)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_best_station(self):
        r = RC.compute_route_stop_score(self.station(max_power_kw=300, stalls_total=12), detour_km=0.0)
        # facilities 5 (unknown), detour 10, speed 10 -> 8.3
        self.assertEqual((r["facilities_score"], r["detour_score"], r["speed_score"], r["score"]), (5.0, 10.0, 10.0, 8.3))
        self.assertEqual(r["emoji"], "🟢")
        self.assertIsNone(r["plugshare_score"])

    def test_detour_decays_to_zero(self):
        s = self.station(max_power_kw=150, stalls_total=6)
        self.assertEqual(RC.compute_route_stop_score(s, detour_km=2.5)["detour_score"], 5.0)
        self.assertEqual(RC.compute_route_stop_score(s, detour_km=9.0)["detour_score"], 0.0)

    def test_plugshare_rating_joins_the_average_only_when_known(self):
        s = self.station(max_power_kw=150, stalls_total=6, metadata={"score": 12})
        r = RC.compute_route_stop_score(s, detour_km=0.0)
        self.assertEqual(r["plugshare_score"], 10.0)   # clamped
        self.assertEqual(r["score"], round((5.0 + 10.0 + 5.0 + 10.0) / 4, 1))

    def test_amenities_list(self):
        r = RC.compute_route_stop_score(self.station(amenities=["cafe", "toilets"]), detour_km=0.0)
        self.assertEqual(r["facilities_score"], 3.0)
        r = RC.compute_route_stop_score(self.station(amenities={"items": []}), detour_km=0.0)
        self.assertEqual(r["facilities_score"], 5.0)   # an empty dict carries no signal
        r = RC.compute_route_stop_score(self.station(amenities={"food": ["a", "b"], "toilets": ["c"], "wifi": True}), detour_km=0.0)
        self.assertEqual(r["facilities_score"], 4.8)   # 4 listed x 1.2

    def test_registry_score_wins(self):
        self.registry.get_station_facilities = lambda **kw: {"score": 9.0, "badge": "lots"}
        r = RC.compute_route_stop_score(self.station(amenities=["cafe"]), detour_km=0.0)
        self.assertEqual((r["facilities_score"], r["facilities_summary"]), (9.0, "lots"))


class CarEfficiencyTest(unittest.TestCase):
    def test_mass(self):
        self.assertIsNone(CE.vehicle_mass_kg_for_profile(None))           # no assumed mass: unknown
        self.assertEqual(CE.vehicle_mass_kg_for_profile({"mass": {"assumed_driving_mass_kg": 2100}}), 2100)
        self.assertEqual(CE.vehicle_mass_kg_for_profile({"mass": {"assumed_driving_mass_kg": 2100}}, 1900), 1900)

    def test_confidence(self):
        self.assertEqual([CE.confidence_for_distance(d) for d in (0, 4.99, 5.0, 19.99, 20.0, 500)],
                         ["low", "low", "medium", "medium", "high", "high"])

    def test_potential_energy(self):
        # 2000 kg up 100 m: 2000 * 9.81 * 100 J = 0.545 kWh
        self.assertAlmostEqual(CE.potential_energy_kwh(2000, 100), 0.545, places=6)
        self.assertAlmostEqual(CE.potential_energy_kwh(2000, -100), -0.545, places=6)

    def test_grade_correction(self):
        flat = {"energy_kwh": 1.5, "distance_km": 10.0, "elevation_change_m": 0}
        self.assertAlmostEqual(CE.grade_corrected_efficiency_kwh_per_100km(flat, 2000), 15.0)
        up = {"energy_kwh": 1.5 + 0.545, "distance_km": 10.0, "elevation_change_m": 100}
        self.assertAlmostEqual(CE.grade_corrected_efficiency_kwh_per_100km(up, 2000), 15.0, places=6)
        self.assertIsNone(CE.grade_corrected_efficiency_kwh_per_100km({"energy_kwh": None, "distance_km": 5}, 2000))
        self.assertIsNone(CE.grade_corrected_efficiency_kwh_per_100km({"energy_kwh": 0.1, "distance_km": 0.1}, 2000))


if __name__ == "__main__":
    unittest.main()
