#!/usr/bin/env python3
"""test_osm_parsing.py - map lookups with every network call faked: OSM speed limits by point and heading, Google Maps links read
for a name and coordinates, OSM tags guessed from a place name, the OSM edit policy (no new nodes, only simple tags), and
supercharge.info sites matched by id, distance and name with its day-long cache.

Covers lib/osm_roads.py, lib/osm_client_common.py, lib/osm_templates_common.py and lib/supercharge_common.py.
Made-up roads, places and sites only, in temp folders. Runs without config or data:
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_osm_parsing.py
"""
import email.message
import io
import json
import math
import os
import sys
import tempfile
import time
import unittest
import urllib.error
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import osm_roads as OR  # noqa: E402
from lib import osm_client_common as OC  # noqa: E402
from lib import osm_templates_common as OT  # noqa: E402
from lib import supercharge_common as SC  # noqa: E402


class FakeResp:
    """What urllib.request.urlopen hands back, enough for these modules."""

    def __init__(self, body=b"", url="", status=200):
        self.body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.url, self.status = url, status

    def read(self, n=-1):
        b, self.body = (self.body, b"") if n is None or n < 0 else (self.body[:n], self.body[n:])
        return b

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def no_network(*a, **k):
    raise AssertionError("network used")


# ------------------------------------------------------------------------------------------------------------- osm_roads
class LimitAndGeometryTest(unittest.TestCase):
    def test_limit_text(self):
        self.assertEqual(OR._limit("50"), 50)
        self.assertEqual(OR._limit(" 110 "), 110)
        self.assertEqual(OR._limit("30 mph"), 48)          # 30 * 1.609 = 48.27
        self.assertEqual(OR._limit("65 mph"), 105)
        for v in (None, "", "none", "AU:urban", "signals", "50;60", "50 km/h"):
            self.assertIsNone(OR._limit(v), v)

    def test_bearing(self):
        self.assertAlmostEqual(OR._bearing(-30.0, 140.0, -29.9, 140.0), 0.0, places=6)
        self.assertAlmostEqual(OR._bearing(-30.0, 140.0, -30.1, 140.0), 180.0, places=6)
        self.assertAlmostEqual(OR._bearing(0.0, 140.0, 0.0, 140.1), 90.0, places=6)
        self.assertAlmostEqual(OR._bearing(0.0, 140.1, 0.0, 140.0), 270.0, places=6)

    def test_distance_to_segment(self):
        a, b = (-30.0, 140.0), (-30.0, 140.01)                    # an east-west piece of road
        self.assertAlmostEqual(OR._seg_dist_m(-30.0, 140.005, a, b), 0.0, places=6)
        self.assertAlmostEqual(OR._seg_dist_m(-30.001, 140.005, a, b), 110.54, places=2)      # 0.001 deg of latitude due south
        kx = 111320.0 * math.cos(math.radians(-30.0))
        self.assertAlmostEqual(OR._seg_dist_m(-30.0, 140.011, a, b), 0.001 * kx, places=3)   # past the end: to the end point
        self.assertAlmostEqual(OR._seg_dist_m(-30.0, 140.0, a, a), 0.0, places=6)            # a zero-length piece


LAT, LON = -30.0125, 140.0125                                        # a made-up point in the middle of nowhere


def way(i, pts, **tags):
    return {"type": "way", "id": i, "tags": tags, "geometry": [{"lat": a, "lon": b} for a, b in pts]}


class RoadsAtTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ti, self.tj = int(math.floor(LAT / OR.TILE)), int(math.floor(LON / OR.TILE))

    def _tile(self, elements, old=False):
        name = ("hw_%d_%d.json" % (self.ti // 2, self.tj // 2)) if old else ("hw05_%d_%d.json" % (self.ti, self.tj))
        with open(os.path.join(self.tmp.name, name), "w") as fh:
            json.dump({"elements": elements}, fh)

    def _roads(self):
        return OR.Roads(self.tmp.name, fetch=False)

    def test_two_way_road_limit_by_direction(self):
        self._tile([way(1, [(LAT - 0.01, LON), (LAT + 0.01, LON)], highway="primary", **{"maxspeed:forward": "80", "maxspeed:backward": "60"},
                        name="Test Road", ref="A1")])
        r = self._roads()
        north = r.at(LAT, LON + 0.0001, 5)
        self.assertEqual((north["limit"], north["name"], north["ref"], north["hw"]), (80, "Test Road", "A1", "primary"))
        self.assertEqual(r.at(LAT, LON + 0.0001, 185)["limit"], 60)
        self.assertIsNone(r.at(LAT, LON + 0.0001, 90))                       # across the road
        self.assertIsNone(r.at(LAT, LON + 0.001, 0))                         # ~96 m away
        self.assertIsNone(r.at(None, LON, 0))
        self.assertIsNone(r.at(LAT, LON, None))

    def test_one_way_only_matches_its_direction(self):
        self._tile([way(2, [(LAT - 0.01, LON), (LAT + 0.01, LON)], highway="trunk", oneway="yes", maxspeed="100"),
                    way(3, [(LAT - 0.01, LON + 0.0002), (LAT + 0.01, LON + 0.0002)], highway="trunk", oneway="-1", maxspeed="90")])
        r = self._roads()
        self.assertEqual(r.at(LAT, LON, 0)["limit"], 100)
        self.assertEqual(r.at(LAT, LON + 0.0002, 180)["limit"], 90)          # the -1 way runs against its drawn direction
        self.assertIsNone(r.at(LAT, LON - 0.0002, 180))                      # against the one-way, the -1 way ~38 m off

    def test_motorway_is_one_way_by_default_but_not_a_roundabout(self):
        self._tile([way(4, [(LAT - 0.01, LON), (LAT + 0.01, LON)], highway="motorway", maxspeed="110")])
        self.assertIsNone(self._roads().at(LAT, LON, 180))
        self._tile([way(5, [(LAT - 0.01, LON), (LAT + 0.01, LON)], highway="motorway", junction="roundabout", maxspeed="40")])
        self.assertEqual(self._roads().at(LAT, LON, 180)["limit"], 40)

    def test_major_road_preferred_over_a_slightly_nearer_secondary(self):
        kx = 111320.0 * math.cos(math.radians(LAT))
        self._tile([way(6, [(LAT - 0.01, LON), (LAT + 0.01, LON)], highway="secondary", maxspeed="60"),
                    way(7, [(LAT - 0.01, LON + 4 / kx), (LAT + 0.01, LON + 4 / kx)], highway="primary", maxspeed="80")])
        self.assertEqual(self._roads().at(LAT, LON, 0)["limit"], 80)           # 4 m vs 0 m + 6 m penalty

    def test_variable_and_conditional_limits_reported(self):
        self._tile([way(8, [(LAT - 0.01, LON), (LAT + 0.01, LON)], highway="primary", maxspeed="80", **{"maxspeed:variable": "yes",
                                                                                                      "maxspeed:conditional": "40 @ (07:00-09:00)"})])
        a = self._roads().at(LAT, LON, 0)
        self.assertEqual((a["limit"], a["var"], a["cond"]), (80, True, "40 @ (07:00-09:00)"))

    def test_old_tile_file_is_read(self):
        self._tile([way(9, [(LAT - 0.01, LON), (LAT + 0.01, LON)], highway="primary", maxspeed="70")], old=True)
        self.assertEqual(self._roads().at(LAT, LON, 0)["limit"], 70)

    def test_broken_or_missing_tile_is_no_answer(self):
        with open(os.path.join(self.tmp.name, "hw05_%d_%d.json" % (self.ti, self.tj)), "w") as fh:
            fh.write("{not json")
        self.assertIsNone(self._roads().at(LAT, LON, 0))
        self.assertIsNone(OR.Roads(os.path.join(self.tmp.name, "nothing"), fetch=False).at(LAT, LON, 0))

    def test_fetch_writes_the_tile_once(self):
        payload = json.dumps({"elements": [way(10, [(LAT - 0.01, LON), (LAT + 0.01, LON)], highway="primary", maxspeed="90")]})
        calls = []

        def fake_run(cmd, **k):
            calls.append(cmd[-1])
            return SimpleNamespace(stdout=payload, returncode=0)
        cache = os.path.join(self.tmp.name, "cache")
        with mock.patch.object(OR.subprocess, "run", side_effect=fake_run):
            r = OR.Roads(cache, fetch=True)
            self.assertEqual(r.at(LAT, LON, 0)["limit"], 90)
            self.assertEqual(r.at(LAT, LON, 0)["limit"], 90)
        self.assertEqual(calls, [OR.MIRRORS[0]])
        with open(os.path.join(cache, "hw05_%d_%d.json" % (self.ti, self.tj))) as fh:
            self.assertEqual(json.load(fh)["tile"], [self.ti, self.tj])

    def test_unreachable_server_leaves_no_answer_and_no_file(self):
        cache = os.path.join(self.tmp.name, "cache")
        with mock.patch.object(OR.subprocess, "run", side_effect=OSError("offline")) as run:
            self.assertIsNone(OR.Roads(cache, fetch=True).at(LAT, LON, 0))
        self.assertEqual(run.call_count, len(OR.MIRRORS))
        self.assertFalse(os.path.exists(cache))


# ----------------------------------------------------------------------------------------------------- osm_client_common
class GmapsUrlTest(unittest.TestCase):
    def _resolve(self, url, urlopen=None):
        with mock.patch.object(OC.urllib.request, "urlopen", urlopen or (lambda req, timeout=0: FakeResp(b"", req.full_url))):
            return OC.resolve_gmaps_url(url)

    def test_exact_poi_coordinates_beat_the_viewport(self):
        r = self._resolve("https://www.google.com/maps/place/Test+Cafe/@-33.1000,151.2000,17z/data=!3d-33.1010!4d151.2020")
        self.assertEqual((r["name"], r["latitude"], r["longitude"]), ("Test Cafe", -33.101, 151.202))

    def test_viewport_coordinates(self):
        r = self._resolve("https://www.google.com/maps/place/Made-Up%20Bakery/@-33.5,151.25,15z")
        self.assertEqual((r["name"], r["latitude"], r["longitude"]), ("Made-Up Bakery", -33.5, 151.25))

    def test_query_coordinates(self):
        r = self._resolve("https://maps.google.com/?q=-33.25,151.75")
        self.assertEqual((r["latitude"], r["longitude"], r["name"]), (-33.25, 151.75, "Unnamed Location"))

    def test_plain_text_is_not_fetched(self):
        r = self._resolve("  just some words  ", urlopen=no_network)
        self.assertEqual((r["original_url"], r["resolved_url"], r["latitude"]), ("just some words", "just some words", None))

    def test_redirect_and_meta_refresh_are_followed(self):
        hdrs = email.message.Message()
        hdrs["Location"] = "https://example.invalid/meta"
        final = "https://www.google.com/maps/place/Follow+Me/@-34.0,150.5,15z"

        def fake(req, timeout=0):
            if req.full_url.startswith("https://maps.app.goo.gl/"):
                raise urllib.error.HTTPError(req.full_url, 302, "Found", hdrs, io.BytesIO(b""))
            if req.full_url == "https://example.invalid/meta":
                return FakeResp('<meta http-equiv="refresh" content="0;url=%s">' % final, req.full_url)
            return FakeResp(b"", req.full_url)
        r = self._resolve("https://maps.app.goo.gl/abc123", urlopen=fake)
        self.assertEqual((r["resolved_url"], r["name"], r["latitude"]), (final, "Follow Me", -34.0))

    def test_network_error_keeps_the_link(self):
        def boom(req, timeout=0):
            raise OSError("offline")
        r = self._resolve("https://maps.app.goo.gl/xyz", urlopen=boom)
        self.assertEqual((r["resolved_url"], r["name"]), ("https://maps.app.goo.gl/xyz", "Unnamed Location"))

    @unittest.expectedFailure
    def test_query_name_is_read(self):
        # BUG: the check is `"[?&]q=" in final_url`, a regex written as a plain substring test, so it is never true and a
        # "?q=<name>" link always comes back "Unnamed Location".
        r = self._resolve("https://www.google.com/maps?q=Testville+Bakery")
        self.assertEqual(r["name"], "Testville Bakery")


class InferTagsTest(unittest.TestCase):
    def test_categories(self):
        cases = {"Testville Basketball Court": ("sport", "basketball"), "Testville Reserve": ("leisure", "park"),
                 "Testville Dog Park": ("leisure", "dog_park"), "Testville Public Toilets": ("amenity", "toilets"),
                 "Testville Supermarket": ("shop", "supermarket"), "Testville Espresso": ("amenity", "cafe"),
                 "Testville Bakehouse": ("shop", "bakery"), "Testville Pizzeria": ("amenity", "restaurant"),
                 "Testville Pharmacy": ("amenity", "pharmacy"), "Testville Supercharger": ("amenity", "charging_station")}
        for name, (k, v) in cases.items():
            t = OC.infer_osm_tags_from_name("  %s " % name)
            self.assertEqual((t["name"], t.get(k)), (name, v), name)

    def test_car_park_is_not_a_park(self):
        self.assertNotEqual(OC.infer_osm_tags_from_name("Testville Car Park").get("leisure"), "park")

    def test_hint_is_used(self):
        self.assertEqual(OC.infer_osm_tags_from_name("Blue Door", "cafe").get("amenity"), "cafe")

    def test_unknown_gets_a_description_only(self):
        self.assertEqual(OC.infer_osm_tags_from_name("Something"), {"name": "Something", "description": "Something"})

    @unittest.expectedFailure
    def test_words_are_matched_whole(self):
        # BUG: keywords are matched as bare substrings, so "wc" inside "NeWCastle" makes a coffee roaster public toilets.
        self.assertEqual(OC.infer_osm_tags_from_name("Newcastle Coffee Roasters").get("amenity"), "cafe")


class OsmTokenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = os.path.join(self.tmp.name, "repo", ".osm_token.json")
        env = {k: v for k, v in os.environ.items() if k != "OSM_ACCESS_TOKEN"}
        env["HOME"] = os.path.join(self.tmp.name, "home")
        for p in (mock.patch.object(OC, "get_osm_token_file", return_value=self.file), mock.patch.dict(os.environ, env, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    def test_none_when_nothing_saved(self):
        self.assertIsNone(OC.load_osm_token())

    def test_save_then_load(self):
        self.assertEqual(OC.save_osm_token(" tok123 ", {"user": "x"}), self.file)
        self.assertEqual(OC.load_osm_token(), "tok123")
        with open(self.file) as fh:
            self.assertEqual(json.load(fh)["metadata"], {"user": "x"})

    def test_environment_wins(self):
        OC.save_osm_token("from-file")
        with mock.patch.dict(os.environ, {"OSM_ACCESS_TOKEN": " from-env "}):
            self.assertEqual(OC.load_osm_token(), "from-env")

    def test_bare_string_and_broken_files(self):
        os.makedirs(os.path.dirname(self.file))
        with open(self.file, "w") as fh:
            json.dump("  plain  ", fh)
        self.assertEqual(OC.load_osm_token(), "plain")
        with open(self.file, "w") as fh:
            fh.write("{broken")
        self.assertIsNone(OC.load_osm_token())

    def test_home_copy_used_when_repo_copy_missing(self):
        home = os.path.join(self.tmp.name, "home")
        os.makedirs(home)
        with open(os.path.join(home, ".osm_token.json"), "w") as fh:
            json.dump({"access_token": "home-tok"}, fh)
        self.assertEqual(OC.load_osm_token(), "home-tok")


class OverpassAndAuthTest(unittest.TestCase):
    def test_first_endpoint_that_answers_is_used(self):
        good = SimpleNamespace(returncode=0, stdout=json.dumps({"elements": [{"id": 1}]}))
        bad = SimpleNamespace(returncode=0, stdout="<html>busy</html>")
        with mock.patch("subprocess.run", side_effect=[bad, good]) as run, \
                mock.patch.object(OC.urllib.request, "urlopen", side_effect=OSError("offline")):
            self.assertEqual(OC.query_overpass_charging_stations(-30.0, 140.0, 250), [{"id": 1}])
        self.assertEqual(run.call_count, 2)
        self.assertIn("around:250,-30.0,140.0", run.call_args_list[0][0][0][-2])

    def test_urllib_fallback_and_all_down(self):
        with mock.patch("subprocess.run", side_effect=OSError("no curl")), \
                mock.patch.object(OC.urllib.request, "urlopen", return_value=FakeResp(json.dumps({"elements": [{"id": 2}]}))):
            self.assertEqual(OC.query_overpass_charging_stations(-30.0, 140.0), [{"id": 2}])
        with mock.patch("subprocess.run", side_effect=OSError("no curl")), \
                mock.patch.object(OC.urllib.request, "urlopen", side_effect=OSError("offline")):
            self.assertEqual(OC.query_overpass_charging_stations(-30.0, 140.0), [])

    def test_auth_url(self):
        u = OC.get_oauth_auth_url("abc", "http://127.0.0.1:8080/cb")
        self.assertTrue(u.startswith(OC.OSM_AUTH_URL + "?"))
        self.assertIn("client_id=abc", u)
        self.assertIn("redirect_uri=http%3A%2F%2F127.0.0.1%3A8080%2Fcb", u)
        self.assertIn("scope=read_prefs+write_api", u)

    def test_token_exchange(self):
        with mock.patch.object(OC.urllib.request, "urlopen", return_value=FakeResp(json.dumps({"access_token": "T"}))) as uo:
            tok, data = OC.fetch_oauth_token("id", "secret", "  code  ")
        self.assertEqual(tok, "T")
        self.assertIn(b"code=code&", uo.call_args[0][0].data)
        err = urllib.error.HTTPError(OC.OSM_TOKEN_URL, 400, "Bad", email.message.Message(), io.BytesIO(b"invalid_grant"))
        with mock.patch.object(OC.urllib.request, "urlopen", side_effect=err):
            self.assertEqual(OC.fetch_oauth_token("id", "secret", "x"), (None, {"error": "HTTP 400: invalid_grant"}))


class OsmEditPolicyTest(unittest.TestCase):
    """No new nodes and only simple tags unless the caller opts in; positions are not moved unless asked."""

    def test_node_creation_refused_by_default(self):
        with mock.patch.object(OC.urllib.request, "urlopen", side_effect=no_network):
            nid, info = OC.create_node("t", 1, -30.0, 140.0, {"name": "X"})
        self.assertIsNone(nid)
        self.assertTrue(info["refused_by_policy"])

    def test_node_creation_drops_extra_tags(self):
        with mock.patch.object(OC.urllib.request, "urlopen", return_value=FakeResp("42")) as uo:
            nid, info = OC.create_node("t", 7, -30.0, 140.0, {"name": "X", "socket:type2": "2", "amenity": "charging_station"},
                                       allow_node_creation=True)
        self.assertEqual((nid, info["tags_dropped_by_policy"]), (42, ["socket:type2"]))
        node = ET.fromstring(uo.call_args[0][0].data).find("node")
        self.assertEqual((node.get("lat"), node.get("lon"), node.get("changeset")), ("-30.0000000", "140.0000000", "7"))
        self.assertEqual(sorted(t.get("k") for t in node.findall("tag")), ["amenity", "name"])

    def _update(self, **kw):
        existing = ('<osm><node id="5" lat="-30.0" lon="140.0" version="3" changeset="1">'
                    '<tag k="name" v="Old"/><tag k="fee" v="yes"/></node></osm>')
        sent = []

        def fake(req, timeout=0):
            if req.get_method() == "PUT":
                sent.append(ET.fromstring(req.data))
                return FakeResp("4")
            return FakeResp(existing)
        with mock.patch.object(OC.urllib.request, "urlopen", side_effect=fake):
            ok, info = OC.update_element_tags("t", 9, "node", 5, **kw)
        return ok, info, (sent[0].find("node") if sent else None)

    def test_update_keeps_to_simple_tags_and_position(self):
        ok, info, node = self._update(new_tags={"name": "New", "socket:type2": "2"}, new_lat=-31.0, new_lon=141.0, remove_tags=["fee"])
        self.assertTrue(ok)
        self.assertEqual((info["version"], info["tags_dropped_by_policy"], info["reposition_refused_by_policy"]), ("4", ["socket:type2"], True))
        self.assertEqual({t.get("k"): t.get("v") for t in node.findall("tag")}, {"name": "New"})
        self.assertEqual((node.get("lat"), node.get("lon"), node.get("changeset")), ("-30.0", "140.0", "9"))

    def test_update_with_opt_ins(self):
        ok, info, node = self._update(new_tags={"socket:type2": "2"}, new_lat=-31.0, new_lon=141.0, allow_extra_tags=True, allow_reposition=True)
        self.assertTrue(ok)
        self.assertNotIn("tags_dropped_by_policy", info)
        self.assertEqual((node.get("lat"), node.get("lon")), ("-31.0000000", "141.0000000"))
        self.assertEqual({t.get("k"): t.get("v") for t in node.findall("tag")}, {"name": "Old", "fee": "yes", "socket:type2": "2"})

    def test_update_fails_cleanly_when_element_cannot_be_read(self):
        with mock.patch.object(OC.urllib.request, "urlopen", side_effect=OSError("offline")):
            ok, info = OC.update_element_tags("t", 9, "node", 5, {"name": "x"})
        self.assertFalse(ok)
        self.assertIn("Could not fetch", info["error"])

    def test_note_id_read(self):
        with mock.patch.object(OC.urllib.request, "urlopen", return_value=FakeResp("<osm><note><id>77</id></note></osm>")):
            self.assertEqual(OC.add_osm_note(-30.0, 140.0, "hi")[0], 77)


# -------------------------------------------------------------------------------------------------- osm_templates_common
class TemplatesTest(unittest.TestCase):
    def test_brand_beats_keywords(self):
        self.assertEqual(OT.match_template("Testville Woolworths")[0], "supermarket")
        self.assertEqual(OT.match_template("Tesla Supercharger Testville")[0], "tesla_supercharger")

    def test_keyword_whole_word(self):
        self.assertEqual(OT.match_template("Testville Tennis Club")[0], "tennis_court")
        self.assertIsNone(OT.match_template("Tapestry House"))              # 'tap' is a keyword but not a whole word here

    def test_named_template_and_extra_tags(self):
        tags, tid, keys = OT.build_osm_tags(" Test Court ", template_id="Basketball-Court", extra_tags={"surface": "asphalt"})
        self.assertEqual(tid, "Basketball-Court")
        self.assertEqual((tags["name"], tags["sport"], tags["surface"]), ("Test Court", "basketball", "asphalt"))
        self.assertIsInstance(keys, list)

    def test_us_plugs_replaced_with_ccs2(self):
        tags, _tid, _k = OT.build_osm_tags("Test Charger", template_id="ev_charging_station",
                                           extra_tags={"socket:nacs": "4", "socket:type1": "1", "socket:type2_combo": None})
        self.assertNotIn("socket:nacs", tags)
        self.assertNotIn("socket:type1", tags)
        tags, _tid, _k = OT.build_osm_tags("Plain", extra_tags={"amenity": "charging_station", "socket:type1_combo": "2"})
        self.assertEqual((tags.get("socket:type2_combo"), "socket:type1_combo" in tags), ("yes", False))

    def test_tesla_ref(self):
        self.assertEqual(OT.derive_tesla_ref(name="Testville, NSW"), "testvillenswsupercharger")
        self.assertEqual(OT.derive_tesla_ref(short_id="Test_Ville"), "testvillesupercharger")
        self.assertEqual(OT.derive_tesla_ref(name="Testville Supercharger"), "testvillesupercharger")
        self.assertEqual(OT.derive_tesla_ref(location_id="Test-Ville supercharger"), "testvillesupercharger")
        self.assertEqual(OT.derive_tesla_ref(url="https://www.tesla.com/en_AU/findus/location/supercharger/testville123"), "testville123")
        self.assertEqual(OT.derive_tesla_ref(name="!!!"), "")

    @unittest.expectedFailure
    def test_brand_matched_on_whole_words(self):
        # BUG: brands are matched as bare substrings, so "iga" inside "RIGAtoni" makes a restaurant an IGA supermarket.
        tags, tid, _k = OT.build_osm_tags("Rigatoni House")
        self.assertNotEqual(tid, "supermarket")
        self.assertNotEqual(tags.get("brand"), "IGA")


# ------------------------------------------------------------------------------------------------------ supercharge_common
SITES = [
    {"id": 101, "locationId": "testvillesupercharger", "name": "Testville, NSW", "gps": {"latitude": -30.0, "longitude": 140.0},
     "address": {"street": "1 Test St", "city": "Testville", "state": "NSW", "country": "Australia"}},
    {"id": 102, "locationId": "testvillenorth", "name": "Testville North, NSW", "gps": {"latitude": -29.993, "longitude": 140.0},
     "address": {"street": "9 Other Rd", "city": "Testville", "state": "NSW", "country": "Australia"}},
    {"id": 103, "locationId": "faraway", "name": "Faraway, WA", "gps": {"latitude": -31.0, "longitude": 117.0},
     "address": {"street": "2 Far Rd", "city": "Faraway", "state": "WA", "country": "Australia"}},
]


class SuperchargeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = os.path.join(self.tmp.name, "sc_cache.json")
        p = mock.patch.object(SC, "CACHE_FILE", self.cache)
        p.start()
        self.addCleanup(p.stop)

    def _cache(self, sites, age_s):
        with open(self.cache, "w") as fh:
            json.dump({"timestamp": time.time() - age_s, "sites": sites}, fh)

    def test_fresh_cache_is_used_without_network(self):
        self._cache(SITES, 60)
        with mock.patch.object(SC.urllib.request, "urlopen", side_effect=no_network):
            self.assertEqual(len(SC.fetch_all_sites()), 3)

    def test_old_cache_refetched_and_saved(self):
        self._cache(SITES[:1], SC.CACHE_TTL_SECONDS + 10)
        with mock.patch.object(SC.urllib.request, "urlopen", return_value=FakeResp(json.dumps(SITES))):
            self.assertEqual(len(SC.fetch_all_sites()), 3)
        with open(self.cache) as fh:
            self.assertEqual(len(json.load(fh)["sites"]), 3)

    def test_old_cache_used_when_offline(self):
        self._cache(SITES[:1], SC.CACHE_TTL_SECONDS + 10)
        with mock.patch.object(SC.urllib.request, "urlopen", side_effect=OSError("offline")):
            self.assertEqual([s["id"] for s in SC.fetch_all_sites()], [101])

    def test_nothing_when_offline_without_cache(self):
        with mock.patch.object(SC.urllib.request, "urlopen", side_effect=OSError("offline")):
            self.assertEqual(SC.fetch_all_sites(), [])
            self.assertIsNone(SC.find_supercharge_site("Testville"))
            self.assertEqual(SC.search_supercharge_sites("Testville"), [])

    def test_find(self):
        self._cache(SITES, 60)
        with mock.patch.object(SC.urllib.request, "urlopen", side_effect=no_network):
            self.assertEqual(SC.find_supercharge_site("x", location_id=" TestvilleNorth ")["id"], 102)
            self.assertEqual(SC.find_supercharge_site("anything", -30.0003, 140.0)["id"], 101)          # ~33 m: same site
            # 780 m from Testville North but named Testville: the name decides, not the nearer site
            self.assertEqual(SC.find_supercharge_site("Testville Supercharger", -30.0, 140.0003)["id"], 101)
            self.assertIsNone(SC.find_supercharge_site("Nowhere", -30.0, 140.005))                     # close, wrong name
            self.assertEqual(SC.find_supercharge_site("Faraway, WA")["id"], 103)                       # no position: name only

    @unittest.expectedFailure
    def test_find_with_position_does_not_fall_back_to_a_far_name_match(self):
        # BUG: the name-only fallback ("if no coordinates") runs even when a position is given, so a site with the right
        # name 2,600 km away is returned - the wrong-site match BUG-152 tightened the distance rule to avoid.
        self._cache(SITES, 60)
        with mock.patch.object(SC.urllib.request, "urlopen", side_effect=no_network):
            self.assertIsNone(SC.find_supercharge_site("Faraway", -30.0, 140.005))

    def test_search(self):
        self._cache(SITES, 60)
        with mock.patch.object(SC.urllib.request, "urlopen", side_effect=no_network):
            self.assertEqual([s["id"] for s in SC.search_supercharge_sites("https://supercharge.info/map?siteId=102")], [102])
            self.assertEqual([s["id"] for s in SC.search_supercharge_sites("https://supercharge.info/map?center=-31.1,117.1", limit=2)], [103, 101])
            self.assertEqual([s["id"] for s in SC.search_supercharge_sites("103")], [103])
            self.assertEqual([s["id"] for s in SC.search_supercharge_sites("Testville North")], [102, 101])
            # every word matched ranks above some words matched ("rd" is also in Faraway's street)
            self.assertEqual([s["id"] for s in SC.search_supercharge_sites("Other Rd")], [102, 103])

    def test_site_changes(self):
        with mock.patch.object(SC.urllib.request, "urlopen", return_value=FakeResp(json.dumps({"results": [{"a": 1}]}))) as uo:
            self.assertEqual(SC.fetch_site_changes("Testville, NSW Supercharger"), [{"a": 1}])
        self.assertTrue(uo.call_args[0][0].full_url.endswith("search=Testville%20NSW"))
        with mock.patch.object(SC.urllib.request, "urlopen", side_effect=OSError("offline")):
            self.assertEqual(SC.fetch_site_changes("x"), [])


if __name__ == "__main__":
    unittest.main()
