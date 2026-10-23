#!/usr/bin/env python3
"""Tests for lib/software: version order, note identity, the Teslascope fetch, and the questions asked of the index.
The fixtures in tests/fixtures/software are Teslascope's real answers for nine versions (2026-10-23)."""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "software")
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))

from lib.software import index as I  # noqa: E402
from lib.software import notes as N  # noqa: E402
from lib.software import settings as S  # noqa: E402
from lib.software import sync as SY  # noqa: E402
from lib.software import teslascope as T  # noqa: E402
from lib import fetch_ledger as FL  # noqa: E402
from lib.software import versions as V  # noqa: E402

NOW = T.iso("2026-10-23T10:00:00Z")
VERSIONS = sorted((f[:-5] for f in os.listdir(FIX)), key=V.key, reverse=True)


def fixture_bytes(v):
    with open(os.path.join(FIX, v + ".json"), "rb") as fh:
        return fh.read()


class Source:
    """A fake Teslascope serving the fixtures."""

    def __init__(self, listed=None, missing=()):
        self.listed = listed or VERSIONS
        self.calls = []
        self.script = {}                    # url tail -> list of (status, body) handed out first
        self.missing = set(missing)

    def __call__(self, url):
        tail = url.rsplit("/", 1)[-1]
        self.calls.append(tail)
        if self.script.get(tail):
            return self.script[tail].pop(0)
        if tail == "software":
            return 200, json.dumps({"code": 200, "response": self.listed}).encode()
        if tail in self.missing or not os.path.isfile(os.path.join(FIX, tail + ".json")):
            return 404, b'{"code":404}'
        return 200, fixture_bytes(tail)


class DiffTest(unittest.TestCase):
    def test_new_words_are_added_and_dropped_words_are_deleted(self):
        d = N.diff("Set your speed. - Chill mode.", "Set your speed profile. - Chill mode. - Mad Max.")
        self.assertEqual("".join(x["text"] for x in d if x["op"] in ("same", "add")), "Set your speed profile. - Chill mode. - Mad Max.")
        self.assertEqual("".join(x["text"] for x in d if x["op"] in ("same", "del")), "Set your speed. - Chill mode.")
        self.assertEqual([x["text"] for x in d if x["op"] == "add"], ["speed profile.", " - Mad Max."])
        self.assertEqual([x["text"] for x in d if x["op"] == "del"], ["speed."])

    def test_a_changed_word_is_one_struck_piece_and_one_bold_piece(self):
        d = N.diff("Autopilot is now available in v14.3.3 for all cars today.", "Self-Driving is now available in v14.3.5 for all cars today.")
        self.assertEqual([x["text"] for x in d if x["op"] == "del"], ["Autopilot", "v14.3.3"])
        self.assertEqual([x["text"] for x in d if x["op"] == "add"], ["Self-Driving", "v14.3.5"])

    def test_texts_that_share_little_are_replaced_whole(self):
        d = N.diff("Alpha beta gamma delta epsilon zeta", "Completely different words here now")
        self.assertEqual([x["op"] for x in d], ["del", "add"])

    def test_the_same_text_has_no_change_and_empty_old_text_is_all_new(self):
        self.assertEqual(N.diff("a b", "a b"), [{"op": "same", "text": "a b"}])
        self.assertEqual(N.diff("", "a b"), [{"op": "add", "text": "a b"}])
        self.assertEqual(N.diff("a b", ""), [{"op": "del", "text": "a b"}])


class VersionTest(unittest.TestCase):
    def test_order_is_release_order_not_text_order(self):
        vs = ["2026.38.5.5", "2026.38", "2026.8.200", "2026.20.10", "4.10.3050", "2026.38.3.1", "2025.44.25.4"]
        self.assertEqual(sorted(vs, key=V.key), ["4.10.3050", "2025.44.25.4", "2026.8.200", "2026.20.10", "2026.38", "2026.38.3.1", "2026.38.5.5"])
        self.assertEqual(sorted(vs, key=V.sortkey), sorted(vs, key=V.key))

    def test_a_missing_zero_is_the_same_version(self):
        self.assertEqual(V.key("2026.38"), V.key("2026.38.0"))

    def test_line_and_kind(self):
        self.assertEqual(V.line("2026.38.5.5"), "2026.38")
        self.assertTrue(V.is_year("2026.38") and not V.is_year("4.10.3050"))
        self.assertIsNone(V.parse("2026.x"))


class NoteTest(unittest.TestCase):
    def test_a_note_is_the_same_note_whatever_fsd_version_it_names(self):
        self.assertEqual(N.key("FSD (Supervised) v14.3.12 Down Under"), N.key("FSD (Supervised) v14.3.5 Down Under"))
        self.assertNotEqual(N.key("FSD (Supervised) v14.3.12 Down Under"), N.key("Full Self-Driving (Supervised) Down Under"))
        self.assertEqual(N.fsd_version("FSD (Supervised) v14.3.12 Down Under"), "14.3.12")
        self.assertIsNone(N.fsd_version("Speed Profiles"))

    def test_quotes_and_spacing_do_not_make_a_new_note(self):
        self.assertEqual(N.key('"Hey Grok"'), N.key("Hey  Grok"))
        self.assertEqual(N.text_hash("a  b\n c"), N.text_hash("a b c"))
        self.assertNotEqual(N.text_hash("a b c"), N.text_hash("a b d"))


class FetchTest(unittest.TestCase):
    def test_retries_grow_and_a_refusal_stops(self):
        seq = [(429, b""), (503, b""), (200, b"ok")]
        waits = []
        st, body = T.request("u/x", get=lambda u: seq.pop(0), sleep=waits.append)
        self.assertEqual((st, body), (200, b"ok"))
        self.assertEqual(len(waits), 2)
        self.assertGreaterEqual(waits[0], 15)                       # a rate limit waits at least 15 s
        self.assertGreater(waits[1], 0)
        with self.assertRaises(T.Refused):
            T.request("u/x", get=lambda u: (403, b"challenge"), sleep=lambda s: None)
        self.assertEqual(T.request("u/x", get=lambda u: (404, b""), sleep=lambda s: None)[0], 404)

    def test_an_answer_that_is_not_a_version_answer_is_not_taken(self):
        self.assertIsNone(T.parse_envelope(b"<html>"))
        self.assertIsNone(T.parse_envelope(b'{"code": 500}'))
        self.assertEqual(T.parse_envelope(b'{"code": 200, "response": [1]}'), [1])

    def test_settled_means_nobody_waiting_and_nobody_taking_it(self):
        old = T.parse_envelope(fixture_bytes("2026.16.6"))
        self.assertTrue(T.is_settled(old, NOW, 14))
        live = T.parse_envelope(fixture_bytes("2026.38.5.10"))
        self.assertFalse(T.is_settled(live, NOW, 14))
        self.assertTrue(T.is_settled(dict(old, pending={"total": 1}), NOW, 14))                 # one car waiting for ever does not keep it moving
        recent = dict(old, pending={"total": 3}, latest_received_at="2026-10-01T00:00:00Z")      # 22 days ago, cars still waiting: not yet
        self.assertFalse(T.is_settled(recent, NOW, 14))
        self.assertTrue(T.is_settled(dict(recent, pending={"total": 0}), NOW, 14))


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.src = Source()

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self, now=NOW, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch.object(FL, "time", SimpleNamespace(time=lambda: now)):         # the ledger stamps its marks with the sync's own clock
            r = SY.sync(self.d, get=self.src, sleep=lambda s: None, now=now, pace=0, bar=False, say=lambda t: None, **kw)
        return r

    def test_a_first_run_keeps_every_version_exactly_as_it_came_newest_first(self):
        r = self.run_sync()
        self.assertEqual(r["asked"], len(VERSIONS))
        self.assertEqual([c for c in self.src.calls if c != "software"], VERSIONS)
        for v in VERSIONS:
            with open(T.version_file(self.d, v), "rb") as fh:
                self.assertEqual(fh.read(), fixture_bytes(v))
        self.assertEqual(r["failed"], 0)

    def test_settled_versions_are_not_asked_again_but_moving_ones_are_after_the_wait(self):
        self.run_sync()
        live = {v for v in VERSIONS if not T.is_settled(T.parse_envelope(fixture_bytes(v)), NOW, 14)}
        self.assertIn("2026.38.5.5", live)
        self.assertNotIn("2026.16.6", live)
        self.src.calls.clear()
        self.run_sync(now=NOW + 3600)                                         # one hour later: inside the 6 hour wait
        self.assertEqual(self.src.calls, ["software"])
        self.src.calls.clear()
        self.run_sync(now=NOW + 7 * 3600)
        self.assertEqual({c for c in self.src.calls if c != "software"}, live)

    def test_numbers_are_kept_over_time_only_when_they_change(self):
        self.run_sync()
        n = len(T.last_uptake(self.d))
        self.assertEqual(n, len(VERSIONS))
        lines = open(os.path.join(self.d, T.UPTAKE)).read().splitlines()
        self.run_sync(now=NOW + 7 * 3600)                                     # same answers again
        self.assertEqual(open(os.path.join(self.d, T.UPTAKE)).read().splitlines(), lines)
        body = json.loads(fixture_bytes("2026.38.5.5"))
        body["response"]["count"] += 5
        self.src.script["2026.38.5.5"] = [(200, json.dumps(body).encode())]
        self.run_sync(now=NOW + 14 * 3600)
        again = open(os.path.join(self.d, T.UPTAKE)).read().splitlines()
        self.assertEqual(len(again), len(lines) + 1)
        self.assertEqual(json.loads(again[-1])["installs"], json.loads(lines[[json.loads(x)["version"] for x in lines].index("2026.38.5.5")])["installs"] + 5)

    def test_odd_entries_in_an_answer_do_not_stop_the_run(self):
        body = json.loads(fixture_bytes("2026.28.5"))
        body["response"]["countries"].append({"country": None, "count": 1})
        body["response"]["metrics"]["models"] = [{"car_type": None, "driver_assist": None, "count": 2}]
        self.src.script["2026.28.5"] = [(200, json.dumps(body).encode())]
        r = self.run_sync()
        self.assertEqual(r["failed"], 0)
        self.assertEqual(r["asked"], len(VERSIONS))
        line = T.last_uptake(self.d)["2026.28.5"]
        self.assertNotIn(None, line["countries"])

    def test_a_crash_inside_one_version_marks_it_failed_and_carries_on(self):
        orig = SY.fetch_one

        def boom(data_dir, v, *a, **k):
            if v == "2026.28.5":
                raise ValueError("boom")
            return orig(data_dir, v, *a, **k)
        SY.fetch_one = boom
        try:
            r = self.run_sync()
        finally:
            SY.fetch_one = orig
        self.assertEqual(r["failed"], 1)
        self.assertEqual(r["asked"], len(VERSIONS))

    def test_a_failed_version_is_asked_again_next_time(self):
        self.src.script["2026.28.5"] = [(500, b"")] * 6
        r = self.run_sync()
        self.assertEqual(r["failed"], 1)
        self.assertFalse(os.path.isfile(T.version_file(self.d, "2026.28.5")))
        self.src.calls.clear()
        r = self.run_sync()
        self.assertIn("2026.28.5", self.src.calls)
        self.assertEqual(r["failed"], 0)

    def test_a_refusal_changes_nothing_and_says_so(self):
        self.src.script["software"] = [(403, b"challenge")]
        r = self.run_sync()
        self.assertTrue(r["refused"])
        self.assertFalse(os.path.exists(T.paths(self.d)[1]))

    def test_limit_and_since(self):
        r = self.run_sync(limit=2)
        self.assertEqual(r["asked"], 2)
        self.assertEqual(r["left"], len(VERSIONS) - 2)
        self.assertEqual(sorted(os.listdir(T.paths(self.d)[1])), sorted(["%s.json" % v for v in VERSIONS[:2]]))
        r = self.run_sync(since=2027)
        self.assertEqual(r["asked"], 0)

    def test_a_version_teslascope_does_not_have_is_settled_not_failed(self):
        self.src.listed = VERSIONS + ["2020.1.1"]
        r = self.run_sync()
        self.assertEqual(r["empty"], 1)
        self.src.calls.clear()
        self.run_sync()
        self.assertNotIn("2020.1.1", self.src.calls)


class IndexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.d = cls.tmp.name
        os.makedirs(T.paths(cls.d)[1])
        for v in VERSIONS:
            shutil.copy(os.path.join(FIX, v + ".json"), T.version_file(cls.d, v))
        cls.ix = I.Index(cls.d, cache_dir=cls.d)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def titles(self, items):
        return {x["title"] for x in items}

    def test_what_versions_a_region_has(self):
        vs = [v["version"] for v in self.ix.versions_for("ANZ")]
        self.assertEqual(vs[0], "2026.38.5.5")
        self.assertIn("2026.20.10", vs)
        self.assertIn("2026.28.5", vs)
        self.assertNotIn("2026.38.5.1", vs)                                   # North America only
        self.assertEqual(vs, sorted(vs, key=V.key, reverse=True))
        na = [v["version"] for v in self.ix.versions_for("North America")]
        self.assertIn("2026.38.5.1", na)
        self.assertNotIn("2026.38.5.5", na)

    def test_the_path_of_a_region_stays_on_its_own_versions(self):
        self.assertTrue(self.ix._path("2026.28.5", "2026.38.5.5"))
        self.assertTrue(self.ix._path("2026.28.5", "2026.38.5.5", only={"2026.28.5", "2026.38.5.5"}))
        self.assertFalse(self.ix._path("2026.28.5", "2026.38.5.5", only={"2026.28.5"}))         # the step is to a version this region has not had

    def test_upgrade_steps_are_marked_by_the_region_that_has_them(self):
        from lib import app_software
        old = os.environ.get("TESLA_CACHE_DIR")
        os.environ["TESLA_CACHE_DIR"] = self.d
        try:
            au = app_software.detail(self.d, "2026.38.5.5", "ANZ")["uptake"]
            na = app_software.detail(self.d, "2026.38.5.5", "North America")["uptake"]
        finally:
            os.environ.pop("TESLA_CACHE_DIR") if old is None else os.environ.update(TESLA_CACHE_DIR=old)
        self.assertTrue(au["from"] and all(e["region"] for e in au["from"]))
        self.assertEqual(au["from_all"], len(au["from"]))
        self.assertTrue(na["from"] and not any(e["region"] for e in na["from"]))                # Australian steps are not North America's path
        self.assertEqual(na["from_region"], [])

    def test_every_country_in_the_data_belongs_to_a_region(self):
        placed = {c for r in self.ix.regions() for c in r["countries"]}
        seen = {x["country"] for x in self.ix.countries_seen()}
        self.assertTrue(seen)
        self.assertEqual(seen - placed, set())
        self.assertNotIn("Other", [r["name"] for r in self.ix.regions()])        # the defaults name them all (Japan is Asia Pacific)
        self.assertIn("jp", [r for r in self.ix.regions() if r["name"] == "Asia Pacific"][0]["countries"])

    def test_a_country_no_group_holds_is_kept_in_other(self):
        cfg = S.load({})
        cfg["regions"] = {"ANZ": ["au", "nz"]}
        ix = I.Index(self.d, cache_dir=self.d, settings=cfg)
        other = [r for r in ix.regions() if r["name"] == "Other"][0]["countries"]
        self.assertIn("jp", other)
        self.assertNotIn("au", other)
        self.assertEqual(ix.resolve("Other"), other)

    def test_a_revised_note_shows_what_changed_from_the_text_before_it_in_the_chain(self):
        c = self.ix.compare("2026.20.10", "2026.38.5.5", "ANZ")
        sp = next(x for x in c["listed_revised"] if x["title"] == "Speed Profiles")
        added = " ".join(g["text"] for g in sp["diff"] if g["op"] == "add")
        self.assertIn("MAD MAX", added)                                       # the new speed profile is the new text
        self.assertEqual(sp["prev_tag"], "2026.16.6")
        self.assertEqual([x["tag"] for x in sp["chain"]][-1], "2026.38.5.5")
        self.assertIsNone(sp["chain"][0]["diff"])
        self.assertTrue(all(x["diff"] for x in sp["chain"][1:]))

    def test_the_first_text_of_a_note_has_no_diff_and_a_later_one_has(self):
        rows = self.ix.first_available("speed profiles", "ANZ")
        self.assertNotIn("diff", rows[0])
        self.assertTrue(rows[-1]["diff"] and rows[-1]["prev_tag"])

    def test_a_version_the_region_never_had_can_still_be_compared(self):
        c = self.ix.compare("2026.8.200", "2026.20.10", "ANZ")
        self.assertNotIn("error", c)
        self.assertTrue(c["a_page_lists_nothing_here"])
        self.assertTrue(c["listed_new"])                                      # judged against what ANZ had listed before 2026.8.200
        self.assertIn("2026.8.200", self.ix.held())

    def test_a_single_country_is_a_region_too(self):
        nz = {v["version"]: v["region_installs"] for v in self.ix.versions_for("nz")}
        self.assertEqual(nz["2026.38.5.5"], 6)

    def test_when_the_welcome_animation_first_reached_australia(self):
        rows = self.ix.first_available("welcome animation", "ANZ")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["first_tag"], "2026.26.6")
        self.assertEqual(rows[0]["first_page"], "2026.28.5")                  # the first Australian page that lists it, a version the jump skipped
        self.assertEqual(rows[0]["countries"], ["au"])
        self.assertEqual(self.ix.first_available("welcome animation", "nz"), [])

    def test_fsd_down_under_goes_through_each_version_in_order(self):
        rows = self.ix.first_available("fsd (supervised) down under", "au")
        self.assertEqual([r["fsd" if "fsd" in r else "title"] for r in rows],
                         ["FSD (Supervised) v14.3.3 Down Under", "FSD (Supervised) v14.3.5 Down Under", "FSD (Supervised) v14.3.9 Down Under", "FSD (Supervised) v14.3.12 Down Under"])
        self.assertEqual([r["first_tag"] for r in rows], ["2026.16.6", "2026.20.10", "2026.28.5", "2026.38.5.5"])
        self.assertEqual([r["change"] for r in rows], ["new", "revised", "revised", "revised"])

    def test_a_note_listed_again_with_the_same_text_is_not_a_new_introduction(self):
        rows = self.ix.first_available("arrival options", "au")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["first_tag"], "2026.16.6")
        self.assertIn("2026.20.10", rows[0]["repeated_in"])
        self.assertIn("2026.38.5.5", rows[0]["repeated_in"])

    def test_speed_profiles_is_revised_when_its_text_changes(self):
        rows = self.ix.first_available("speed profiles", "au")
        self.assertEqual([r["change"] for r in rows], ["new", "revised"])
        self.assertEqual(rows[1]["first_tag"], "2026.38.5.5")
        self.assertIn("MAD MAX", rows[1]["description"].upper())
        self.assertNotIn("MAD MAX", rows[0]["description"].upper())

    def test_the_big_jump_lists_what_the_end_pages_leave_out(self):
        c = self.ix.compare("2026.20.10", "2026.38.5.5", "ANZ")
        self.assertEqual(c["between"], ["2026.26.6.1", "2026.28.5", "2026.32.7", "2026.38.3.1", "2026.38.5.1"])
        self.assertEqual(c["between_region"], ["2026.26.6.1", "2026.28.5", "2026.32.7", "2026.38.3.1"])        # the versions Australia has had (not the North America build)
        self.assertEqual(self.titles(c["listed_new"]), {"Set Arrival Energy", "Halloween Mode", "Wheel Selection", "Apple Music"})
        self.assertEqual(self.titles(c["listed_revised"]), {"Speed Profiles", "FSD (Supervised) v14.3.12 Down Under"})
        fsd = next(x for x in c["listed_revised"] if x["title"].startswith("FSD"))
        self.assertEqual(fsd["was_title"], "FSD (Supervised) v14.3.5 Down Under")
        self.assertEqual([x["title"] for x in fsd["via"]], ["FSD (Supervised) v14.3.9 Down Under"])
        self.assertTrue({"Arrival Options", "Brake Confirm", "UI Improvements"} <= self.titles(c["listed_again"]))
        skipped = {x["title"]: x for x in c["skipped"]}
        wa = skipped["Welcome Animation"]
        self.assertEqual(wa["tag"], "2026.26.6")
        self.assertIn("2026.28.5", wa["versions"])
        self.assertTrue(wa["reaches_b"])                                      # cars were seen going 2026.28.5 -> 2026.38.5.5
        self.assertNotIn("Welcome Animation", self.titles(c["listed_new"]))

    def test_other_regions_notes_are_kept_apart_from_the_chosen_ones(self):
        c = self.ix.compare("2026.20.10", "2026.38.5.5", "ANZ")
        self.assertIn("Dynamic Headlight Leveling", self.titles(c["others"]))   # United States and Ukraine
        self.assertNotIn("Dynamic Headlight Leveling", self.titles(c["skipped"]))
        self.assertIn("Powershare with Powerwall", self.titles(c["others"]))

    def test_a_note_the_region_has_for_other_car_types_says_which(self):
        c = self.ix.compare("2026.20.10", "2026.38.5.5", "ANZ", "model3", "TeslaAP4")
        o = {x["title"]: x for x in c["others"]}
        self.assertIn("Model S AI3", o["Speed Cameras on Route"]["region_cars"])          # listed in Australia, but not for a Model 3
        self.assertNotIn("region_cars", o["Dynamic Headlight Leveling"])                  # no ANZ car type has it at all

    def test_every_note_says_which_car_types_and_countries_list_it(self):
        c = self.ix.compare("2026.20.10", "2026.38.5.5", "ANZ", "model3", "TeslaAP4")
        o = {x["title"]: x for x in c["others"]}
        sc = o["Speed Cameras on Route"]["scope"]
        self.assertIn("au", sc["countries"])
        self.assertFalse(sc["all_countries"])
        self.assertIn("Model X AI3", [x["label"] for x in sc["cars"]])
        self.assertTrue(all("scope" in x for x in c["listed_new"] + c["skipped"]))

    def test_a_newer_text_in_b_supersedes_what_was_in_between(self):
        c = self.ix.compare("2026.20.10", "2026.38.5.5", "ANZ")
        self.assertNotIn("FSD (Supervised) v14.3.9 Down Under", self.titles(c["skipped"]))      # b lists 14.3.12 instead

    def test_comparing_to_a_version_that_is_not_held_says_so(self):
        self.assertIn("error", self.ix.compare("2026.20.10", "2030.1.1", "au"))

    def test_what_a_region_and_model_has_by_a_version(self):
        r = self.ix.features_for("au", model="model3", hw="TeslaAP4")
        self.assertEqual(r["version"], "2026.38.5.5")
        titles = {f["title"]: f for f in r["features"]}
        self.assertIn("Welcome Animation", titles)                            # from the 2026.28.5 page
        self.assertFalse(titles["Welcome Animation"]["listed_on_page"])
        self.assertTrue(titles["Brake Confirm"]["listed_on_page"])
        self.assertNotIn("Dynamic Headlight Leveling", titles)
        self.assertNotIn("Wheel Selection", {f["title"] for f in self.ix.features_for("au", model="model3", hw="TeslaAP4", version="2026.20.10")["features"]})
        self.assertTrue(any(f["title"] == "Visual Updates" for f in self.ix.features_for("au", model="modely", hw="TeslaAP4")["features"]))
        self.assertFalse(any(f["title"] == "Wheel Selection" for f in self.ix.features_for("au", model="model3", hw="TeslaAP3")["features"]))

    def test_uptake(self):
        u = self.ix.uptake("2026.38.5.5", "au")
        self.assertEqual(u["region_installs"], 104)
        self.assertEqual({c["country"] for c in u["countries"]}, {"au", "nz"})
        self.assertEqual(u["fsd_hw4"], "14.3.12")
        self.assertTrue(any(e["version"] == "2026.20.10" for e in u["from"]))
        self.assertIn("error", self.ix.uptake("1.1.1"))

    def test_the_cache_folder_is_made_when_it_is_not_there(self):
        d = tempfile.mkdtemp()
        os.makedirs(T.paths(d)[1])
        shutil.copy(os.path.join(FIX, "2026.20.10.json"), T.version_file(d, "2026.20.10"))
        ix = I.Index(d, cache_dir=os.path.join(d, "no", "such", "cache"))
        self.assertEqual(len(ix.versions_for("au")), 1)
        shutil.rmtree(d)

    def test_the_index_rebuilds_itself_when_a_file_changes(self):
        d = tempfile.mkdtemp()
        os.makedirs(T.paths(d)[1])
        shutil.copy(os.path.join(FIX, "2026.20.10.json"), T.version_file(d, "2026.20.10"))
        ix = I.Index(d, cache_dir=d)
        self.assertEqual(len(ix.versions_for("au")), 1)
        shutil.copy(os.path.join(FIX, "2026.38.5.5.json"), T.version_file(d, "2026.38.5.5"))
        self.assertEqual(len(I.Index(d, cache_dir=d).versions_for("au")), 2)
        shutil.rmtree(d)


class SyntheticTest(unittest.TestCase):
    """Small made-up versions, so the rules are checked exactly."""

    def make(self, spec):
        d = tempfile.mkdtemp()
        os.makedirs(T.paths(d)[1])
        for ver, feats in spec.items():
            ft = [{"id": i + 1, "title": t, "description": txt, "subtitle": "", "customer_version": tag, "car_types": ["modely_TeslaAP4"], "countries": cs}
                  for i, (t, txt, tag, cs) in enumerate(feats)]
            r = {"version": ver, "features": ft, "previous_features": [], "countries": [], "metrics": {}, "pending": {"total": 0}, "count": 1,
                 "firstSpotted": "2026-01-01T00:00:00Z"}
            with open(T.version_file(d, ver), "w") as fh:
                json.dump({"code": 200, "response": r}, fh)
        return d

    def test_first_in_a_region_is_not_first_in_the_world(self):
        d = self.make({"2026.10.1": [("Glow", "it glows", "2026.10.1", ["us"])], "2026.12.1": [("Glow", "it glows", "2026.12.1", ["au"])]})
        ix = I.Index(d, cache_dir=d)
        au = ix.first_available("glow", "au")
        self.assertEqual([r["first_tag"] for r in au], ["2026.12.1"])
        us = ix.first_available("glow", "us")
        self.assertEqual([r["first_tag"] for r in us], ["2026.10.1"])
        both = ix.first_available("glow", ["us", "au"])
        self.assertEqual([r["first_tag"] for r in both], ["2026.10.1"])
        shutil.rmtree(d)

    def test_same_text_is_repeated_and_a_change_is_a_revision(self):
        d = self.make({"2026.10.1": [("Glow", "it glows", "2026.10.1", ["au"])],
                       "2026.11.1": [("Glow", "it glows", "2026.11.1", ["au"])],
                       "2026.12.1": [("Glow", "it glows brighter", "2026.12.1", ["au"])]})
        rows = I.Index(d, cache_dir=d).first_available("glow", "au")
        self.assertEqual([(r["first_tag"], r["change"]) for r in rows], [("2026.10.1", "new"), ("2026.12.1", "revised")])
        self.assertEqual(rows[0]["repeated_in"], ["2026.11.1"])
        shutil.rmtree(d)

    def test_a_note_with_no_countries_is_for_everyone(self):
        d = self.make({"2026.10.1": [("Glow", "it glows", "2026.10.1", [])]})
        ix = I.Index(d, cache_dir=d)
        self.assertEqual(len(ix.first_available("glow", "nz")), 1)
        shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
