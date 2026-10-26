"""The Software page's backend (lib/app_software.py): the FSD chip, the versions of a region and car, the timeline, the background fetch."""
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))

from lib import app_software as A  # noqa: E402
from lib.software import sync as SY  # noqa: E402
from lib.software import teslascope as T  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "software")


class ChipTest(unittest.TestCase):
    def test_a_version_without_fsd_has_no_chip(self):
        self.assertEqual(A.fsd_chip({"fsd_hw3": None, "fsd_hw4": None}), [])

    def test_each_computer_has_its_own_chip_unless_they_agree(self):
        c = A.fsd_chip({"fsd_hw3": "12.6.4", "fsd_hw4": "13.2.9"})
        self.assertEqual([(x["v"], x["hw"]) for x in c], [("13.2.9", "AI4"), ("12.6.4", "AI3")])
        self.assertEqual(A.fsd_chip({"fsd_hw3": "14.3", "fsd_hw4": "14.3"}), [{"v": "14.3", "kind": "fsd", "hw": ""}])

    def test_a_chosen_computer_shows_only_its_own(self):
        c = A.fsd_chip({"fsd_hw3": "12.6.4", "fsd_hw4": "13.2.9"}, "TeslaAP3")
        self.assertEqual([x["v"] for x in c], ["12.6.4"])

    def test_fsd_lite_is_its_own_chip(self):
        c = A.fsd_chip({"fsd_hw3": "14.3 Lite", "fsd_hw4": "14.3.12"})
        self.assertEqual([(x["v"], x["kind"], x["hw"]) for x in c], [("14.3.12", "fsd", "AI4"), ("14.3", "lite", "AI3")])
        self.assertEqual([x["kind"] for x in A.fsd_chip({"fsd_hw3": "14.3 Lite", "fsd_hw4": "14.3.12"}, "TeslaAP3")], ["lite"])
        self.assertEqual([x["kind"] for x in A.fsd_chip({"fsd_hw3": "14.3 Lite", "fsd_hw4": "14.3.12"}, "TeslaAP4")], ["fsd"])
        self.assertEqual(A.fsd_chip({"fsd_hw3": "14 Lite", "fsd_hw4": "14"}, None)[0]["kind"], "fsd")          # the same number is not the same thing

    def test_what_a_computer_cannot_install(self):
        both = dict(fsd=True, lite=True, legacy=False)
        plain = dict(fsd=True, lite=False, legacy=False)
        lite = dict(fsd=False, lite=True, legacy=False)
        old = dict(fsd=True, lite=False, legacy=True)
        none = dict(fsd=False, lite=False, legacy=False)
        for kinds, ai3, ai4 in ((plain, True, False), (lite, False, True), (both, False, False), (old, False, False), (none, False, False)):
            self.assertEqual((A.hidden_for(kinds, "TeslaAP3"), A.hidden_for(kinds, "TeslaAP4")), (ai3, ai4))
        self.assertFalse(A.hidden_for(plain, None))                                  # no computer chosen: nothing is hidden
        self.assertFalse(A.hidden_for(plain, "MonoCam"))


class PageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.d = cls.tmp.name
        os.makedirs(T.paths(cls.d)[1])
        for f in os.listdir(FIX):
            if f.endswith(".json"):
                shutil.copy(os.path.join(FIX, f), T.version_file(cls.d, f[:-5]))
        cls.old = os.environ.get("TESLA_CACHE_DIR")
        os.environ["TESLA_CACHE_DIR"] = cls.d

    @classmethod
    def tearDownClass(cls):
        if cls.old is None:
            os.environ.pop("TESLA_CACHE_DIR", None)
        else:
            os.environ["TESLA_CACHE_DIR"] = cls.old
        cls.tmp.cleanup()

    def test_nothing_held_says_so(self):
        with tempfile.TemporaryDirectory() as e:
            self.assertTrue(A.overview(e)["empty"])

    def test_the_versions_of_a_region_newest_first_with_their_fsd(self):
        o = A.overview(self.d, "ANZ")
        vs = [v["version"] for v in o["versions"]]
        self.assertEqual(vs[0], "2026.38.5.5")
        self.assertNotIn("2026.38.5.1", vs)                                   # North America only
        top = o["versions"][0]
        self.assertEqual(top["fsd"], [{"v": "14.3.12", "kind": "fsd", "hw": "AI4"}])
        self.assertTrue(any(m["model"] == "modely" for m in o["choices"]["models"]))

    def test_the_fsd_the_notes_name_fills_a_gap_in_the_field(self):
        o = {v["version"]: v for v in A.overview(self.d, "ANZ")["versions"]}
        self.assertEqual([x["v"] for x in o["2026.28.5"]["fsd"]], ["14.3.9"])      # Teslascope's field is empty there; the Down Under note names v14.3.9

    def test_a_car_leaves_out_what_it_cannot_install(self):
        every = A.overview(self.d, "ANZ")["versions"]
        ct = A.overview(self.d, "ANZ", "cybertruck", "TeslaAP4")["versions"]
        self.assertLess(len(ct), len(every))

    def test_the_kinds_of_fsd_each_version_carries(self):
        k = A.fsd_kinds(A._index(self.d))
        self.assertEqual(k["2026.38.5.5"], dict(fsd=True, lite=False, legacy=False))       # FSD v14.3.12 Down Under only
        self.assertEqual(k["2026.38.5.1"], dict(fsd=True, lite=True, legacy=False))        # both
        self.assertNotIn("2026.32.7", k)

    def test_ai3_never_gets_a_version_that_has_only_fsd_and_ai4_never_one_that_has_only_lite(self):
        ix = A._index(self.d)
        self.assertEqual(A.hidden_versions(ix, "modely", "TeslaAP3") & {"2026.38.5.1", "2026.38.5.10"}, set())       # both kinds: for both
        self.assertEqual(A.hidden_versions(ix, "modely", "TeslaAP3") - {"2026.16.6", "2026.20.10", "2026.28.5", "2026.38.5.5"}, set())
        self.assertIn("2026.38.5.5", A.hidden_versions(ix, "modely", "TeslaAP3"))
        self.assertIn("2026.38.5.5", A.hidden_versions(ix, None, "TeslaAP3"))
        self.assertEqual(A.hidden_versions(ix, "modely", "TeslaAP4"), set())
        self.assertEqual(A.hidden_versions(ix, "modely", None), set())

    def test_a_computer_that_did_install_a_version_keeps_it(self):
        ix = A._index(self.d)
        real = A.fsd_kinds
        A.fsd_kinds = lambda i: {"2026.38.5.1": dict(fsd=True, lite=False, legacy=False), "2026.38.5.5": dict(fsd=True, lite=False, legacy=False)}
        try:
            h = A.hidden_versions(ix, "modely", "TeslaAP3")
            self.assertEqual(h, {"2026.38.5.5"})                                           # 104 of 436 modely installs were AI3 on 2026.38.5.1: it was installable
            ix.cfg["fsd_hw_share_pct"] = 50
            self.assertEqual(A.hidden_versions(ix, "modely", "TeslaAP3"), {"2026.38.5.1", "2026.38.5.5"})
        finally:
            A.fsd_kinds = real
            ix.cfg.pop("fsd_hw_share_pct", None)

    def test_a_lite_only_version_is_hidden_from_ai4(self):
        ix = A._index(self.d)
        real = A.fsd_kinds
        A.fsd_kinds = lambda i: {"2026.38.5.1": dict(fsd=False, lite=True, legacy=False)}
        try:
            self.assertEqual(A.hidden_versions(ix, "modely", "TeslaAP4"), set())             # 332 of 436 modely installs were AI4: it was installable
            ix.cfg["fsd_hw_share_pct"] = 99
            self.assertEqual(A.hidden_versions(ix, "modely", "TeslaAP4"), {"2026.38.5.1"})
            self.assertEqual(A.hidden_versions(ix, "modely", "TeslaAP3"), set())
        finally:
            A.fsd_kinds = real
            ix.cfg.pop("fsd_hw_share_pct", None)

    def test_the_overview_of_an_ai3_car_shows_fsd_lite_and_never_plain_fsd(self):
        o = {v["version"]: v for v in A.overview(self.d, "North America", "modely", "TeslaAP3")["versions"]}
        self.assertIn("2026.38.5.1", o)
        self.assertEqual({c["kind"] for c in o["2026.38.5.1"]["fsd"]}, {"lite"})
        a4 = {v["version"]: v for v in A.overview(self.d, "North America", "modely", "TeslaAP4")["versions"]}
        self.assertIn("2026.38.5.1", a4)
        self.assertEqual({c["kind"] for c in a4["2026.38.5.1"]["fsd"]}, {"fsd"})
        self.assertNotIn("2026.38.5.5", {v["version"] for v in A.overview(self.d, "ANZ", "modely", "TeslaAP3")["versions"]})
        self.assertIn("2026.38.5.5", {v["version"] for v in A.overview(self.d, "ANZ", "modely", "TeslaAP4")["versions"]})

    def test_the_timeline_has_days_and_steps_inside_the_region(self):
        t = A.timeline(self.d, "ANZ")
        mine = {v["version"] for v in A.overview(self.d, "ANZ")["versions"]}
        self.assertTrue(t["last"] and t["edges"])
        self.assertTrue(all(e["src"] in mine and e["dst"] in mine for e in t["edges"]))
        self.assertTrue(set(t["last"]) <= mine)

    def test_the_cars_own_journey_is_on_the_page_and_the_timeline_even_for_a_version_the_region_never_had(self):
        from lib import car_versions as CV
        with tempfile.TemporaryDirectory() as e:
            shutil.copytree(os.path.join(self.d, "software"), os.path.join(e, "software"))
            CV.enter(e, "2026.8.200", first_seen="2026-05-31T22:34:00Z")
            CV.enter(e, "2026.20.10", fsd="14.3.5", first_seen="2026-07-29T09:23:00Z", between=["2026-07-28T06:46:29Z", "2026-07-29T09:23:00Z"])
            CV.record(e, "2026.38.5.5 abc", None, now=1792700000)
            o = A.overview(e, "ANZ")
            self.assertEqual([j["version"] for j in o["journey"]], ["2026.8.200", "2026.20.10", "2026.38.5.5"])
            self.assertEqual(o["journey"][1]["fsd_chip"], [{"v": "14.3.5", "kind": "fsd", "hw": ""}])
            self.assertIn("2026.8.200", o["all_versions"])
            self.assertNotIn("2026.8.200", [v["version"] for v in o["versions"]])      # not in the region's own list, yet it can be chosen and compared
            t = A.timeline(e, "ANZ")
            self.assertIn("2026.8.200", t["extra"])
            self.assertEqual(len(t["journey"]), 3)
            self.assertEqual(o["mine"], "2026.38.5.5")

    def test_the_compare_carries_the_text_of_each_note(self):
        c = A.compare(self.d, "2026.20.10", "2026.38.5.5", "ANZ")
        self.assertTrue(all(x["text"] for x in c["listed_new"]))


class SyncRunTest(unittest.TestCase):
    def test_progress_and_result_are_what_the_page_polls(self):
        real = SY.sync

        def fake(data, **kw):
            kw["progress"](0, 3, "")
            for i, v in enumerate(["2026.1", "2026.2", "2026.3"]):
                if kw["stop"]():
                    break
                kw["progress"](i, 3, v)
                time.sleep(0.05)
            kw["progress"](3, 3, "")
            return dict(asked=3, failed=0, left=0, refused=False, list_ok=True)
        SY.sync = fake
        try:
            s = A.SoftSync("/nowhere")
            s.start()
            for _ in range(100):
                if not s.running():
                    break
                time.sleep(0.05)
            st = s.status()
            self.assertFalse(st["running"])
            self.assertEqual((st["done"], st["total"]), (3, 3))
            self.assertEqual(st["result"]["asked"], 3)
            self.assertEqual(st["error"], "")
        finally:
            SY.sync = real

    def test_stop_ends_it_between_two_versions(self):
        real = SY.sync

        def fake(data, **kw):
            n = 0
            for i in range(200):
                if kw["stop"]():
                    break
                kw["progress"](i, 200, "v%d" % i)
                n += 1
                time.sleep(0.01)
            return dict(asked=n, refused=False)
        SY.sync = fake
        try:
            s = A.SoftSync("/nowhere")
            s.start()
            time.sleep(0.1)
            s.stop()
            for _ in range(100):
                if not s.running():
                    break
                time.sleep(0.02)
            self.assertLess(s.status()["result"]["asked"], 200)
        finally:
            SY.sync = real

    def test_a_crash_is_shown_not_lost(self):
        real = SY.sync

        def boom(data, **kw):
            raise RuntimeError("no network")
        SY.sync = boom
        try:
            s = A.SoftSync("/nowhere")
            s.start()
            for _ in range(100):
                if not s.running():
                    break
                time.sleep(0.02)
            self.assertIn("no network", s.status()["error"])
        finally:
            SY.sync = real


if __name__ == "__main__":
    unittest.main()
