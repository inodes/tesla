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
        self.assertEqual(A.fsd_chip({"fsd_hw3": "14.3", "fsd_hw4": "14.3"}), [{"v": "14.3", "hw": ""}])

    def test_a_chosen_computer_shows_only_its_own(self):
        c = A.fsd_chip({"fsd_hw3": "12.6.4", "fsd_hw4": "13.2.9"}, "TeslaAP3")
        self.assertEqual([x["v"] for x in c], ["12.6.4"])


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
        self.assertEqual(top["fsd"], [{"v": "14.3.12", "hw": "AI4"}])
        self.assertTrue(any(m["model"] == "modely" for m in o["choices"]["models"]))

    def test_the_fsd_the_notes_name_fills_a_gap_in_the_field(self):
        o = {v["version"]: v for v in A.overview(self.d, "ANZ")["versions"]}
        self.assertEqual([x["v"] for x in o["2026.28.5"]["fsd"]], ["14.3.9"])      # Teslascope's field is empty there; the Down Under note names v14.3.9

    def test_a_car_leaves_out_what_it_cannot_install(self):
        every = A.overview(self.d, "ANZ")["versions"]
        ct = A.overview(self.d, "ANZ", "cybertruck", "TeslaAP4")["versions"]
        self.assertLess(len(ct), len(every))

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
            self.assertEqual(o["journey"][1]["fsd_chip"], [{"v": "14.3.5", "hw": ""}])
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
