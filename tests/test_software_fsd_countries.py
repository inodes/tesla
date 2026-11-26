"""FSD counts only for the countries where it is approved (settings "fsd_countries"): a car Teslascope puts in Ukraine does not make FSD v14.3.13 a Europe version."""
import os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))

from lib import app_software as A  # noqa: E402
from lib.software import settings as S  # noqa: E402


class FakeIx:
    def __init__(self, groups, fsd):
        self.cfg = dict(S.DEFAULTS, fsd_countries=fsd, hardware_labels=S.DEFAULTS["hardware_labels"])
        self.groups = groups

    def resolve(self, region):
        return list(self.groups.get(region, [region]))


class FsdCountriesTest(unittest.TestCase):
    def test_the_default_table_follows_the_ticks(self):
        d = S.DEFAULTS["fsd_countries"]
        self.assertEqual(len(d), 19)
        self.assertNotIn("ua", d)
        for c in ("gb", "de", "fr", "se", "no", "ch", "jp"):
            self.assertNotIn(c, d)
        for c in ("us", "ca", "kr"):                                   # HW3 column "v14 Lite"
            self.assertEqual(d[c], {"hw4": True, "hw3": "lite"})
        for c in ("au", "nz", "nl", "fi"):                              # HW3 "No"
            self.assertEqual(d[c], {"hw4": True, "hw3": False})
        self.assertEqual(d["lv"], {"hw4": False, "hw3": False})        # approved, HW4 "-": no FSD yet

    def test_a_region_keeps_only_its_approved_countries(self):
        ix = FakeIx({"Europe": ["nl", "ua", "de", "lv"], "UK": ["gb", "ie"], "NA": ["us", "mx"]}, S.DEFAULTS["fsd_countries"])
        self.assertEqual(A.fsd_countries(ix, "Europe"), ["nl", "lv"])
        self.assertEqual(A.fsd_countries(ix, "Europe", "hw4"), ["nl"])
        self.assertEqual(A.fsd_countries(ix, "UK"), [])
        self.assertEqual(A.fsd_countries(ix, "NA", "lite"), ["us"])

    def test_a_plain_list_of_codes_means_fsd_on_ai4_only(self):
        ix = FakeIx({"X": ["nl", "us"]}, ["nl"])
        self.assertEqual(A.fsd_countries(ix, "X", "hw4"), ["nl"])
        self.assertEqual(A.fsd_countries(ix, "X", "lite"), [])

    def test_the_field_of_teslascope_counts_only_with_cars_in_a_country_that_has_it(self):
        ix = FakeIx({"Europe": ["nl", "ua"]}, S.DEFAULTS["fsd_countries"])
        row = {"version": "2026.38.5.11", "fsd_hw3": None, "fsd_hw4": "14.3.13"}
        self.assertEqual(A.chips_for(ix, row, {}, None, {}), [])                                                    # no car of an FSD country
        self.assertEqual(A.chips_for(ix, row, {}, None, {"2026.38.5.11": dict(any=2, hw4=0, lite=0)}), [])          # cars only where HW4 has no FSD yet (Latvia)
        self.assertEqual([c["v"] for c in A.chips_for(ix, row, {}, None, {"2026.38.5.11": dict(any=3, hw4=3, lite=0)})], ["14.3.13"])
        self.assertEqual([c["v"] for c in A.chips_for(ix, row, {}, None, None)], ["14.3.13"])                       # no region asked: as before

    def test_lite_needs_a_country_with_lite(self):
        ix = FakeIx({}, S.DEFAULTS["fsd_countries"])
        row = {"version": "2026.38.5.5", "fsd_hw3": "14.3 Lite", "fsd_hw4": "14.3.12"}
        au = {"2026.38.5.5": dict(any=100, hw4=100, lite=0)}
        self.assertEqual([c["kind"] for c in A.chips_for(ix, row, {}, None, au)], ["fsd"])                          # Australia: HW3 "No"
        us = {"2026.38.5.5": dict(any=100, hw4=100, lite=100)}
        self.assertEqual(sorted(c["kind"] for c in A.chips_for(ix, row, {}, None, us)), ["fsd", "lite"])

    def test_the_ai3_v12_chip_needs_a_country_where_ai3_has_fsd(self):
        row = {"version": "2026.8.3", "fsd_hw3": "12.6.4", "fsd_hw4": None}
        ix = FakeIx({}, S.DEFAULTS["fsd_countries"])
        anz = {"2026.8.3": dict(any=5, hw4=3, lite=0)}                       # 5 cars of Australia and New Zealand are on the build, none of them AI3 with FSD
        self.assertEqual(A.chips_for(ix, row, {}, None, anz), [])
        na = {"2026.8.3": dict(any=50, hw4=20, lite=30)}
        self.assertEqual([(c["v"], c["hw"]) for c in A.chips_for(ix, row, {}, None, na)], [("12.6.4", "AI3")])
        self.assertEqual([c["v"] for c in A.chips_for(ix, row, {}, None, None)], ["12.6.4"])     # no region asked: Teslascope's field as it is

    def test_an_fsd_version_has_only_cars_of_fsd_countries(self):
        fsd = dict(fsd=True, lite=False, legacy=False)
        lite = dict(fsd=False, lite=True, legacy=False)
        none = dict(fsd=False, lite=False, legacy=False)
        self.assertIsNone(A.branch_cars(none, dict(any=5, hw4=5, lite=5)))               # not an FSD branch: every car counts
        self.assertIsNone(A.branch_cars(None, {}))
        self.assertEqual(A.branch_cars(fsd, dict(any=2, hw4=0, lite=0)), 0)              # 2 cars of Ukraine: none can be on it
        self.assertEqual(A.branch_cars(fsd, dict(any=70, hw4=60, lite=10)), 60)
        self.assertEqual(A.branch_cars(lite, dict(any=70, hw4=60, lite=10)), 10)
        self.assertEqual(A.branch_cars(dict(fsd=True, lite=True, legacy=False), dict(any=70, hw4=60, lite=10)), 60)

    def test_the_page_says_which_cars_are_not_counted(self):
        js = open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8").read()
        self.assertIn("region_installs_ignored", js)
        self.assertIn("get the non-FSD ones", js)
        self.assertIn("country or on a computer without that FSD", js)

    def test_an_ai3_car_in_an_fsd_country_is_not_on_the_ai4_fsd_version(self):
        import sqlite3
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE version_countries(version TEXT, country TEXT, installs INTEGER)")
        db.execute("CREATE TABLE version_models(version TEXT, model TEXT, hw TEXT, installs INTEGER)")
        db.executemany("INSERT INTO version_countries VALUES (?,?,?)", [("v", "au", 100), ("v", "nz", 6)])
        db.executemany("INSERT INTO version_models VALUES (?,?,?,?)", [("v", "modely", "TeslaAP4", 60), ("v", "model3", "TeslaAP3", 46)])   # 46 AI3 cars in Australia, not on FSD
        ix = FakeIx({"ANZ": ["au", "nz"]}, S.DEFAULTS["fsd_countries"])
        ix.q = lambda sql, args=(): [dict(r) for r in db.execute(sql, args)]
        e = A.fsd_cars(ix, "ANZ")["v"]
        self.assertEqual((e["any"], e["hw4"], e["lite"]), (106, 60, 0))             # AU / NZ have no Lite; only the 60 AI4 cars can be on the FSD version


if __name__ == "__main__":
    unittest.main()
