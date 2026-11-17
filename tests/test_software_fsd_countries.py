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
    def test_the_default_list_has_no_ukraine_and_the_places_that_have_fsd(self):
        d = S.DEFAULTS["fsd_countries"]
        self.assertNotIn("ua", d)
        for c in ("us", "ca", "au", "nz", "kr", "nl", "fi"):
            self.assertIn(c, d)
        for c in ("gb", "de", "fr", "se", "no", "ch", "jp"):
            self.assertNotIn(c, d)

    def test_a_region_keeps_only_its_approved_countries(self):
        ix = FakeIx({"Europe": ["nl", "ua", "de"], "UK": ["gb", "ie"]}, S.DEFAULTS["fsd_countries"])
        self.assertEqual(A.fsd_countries(ix, "Europe"), ["nl"])
        self.assertEqual(A.fsd_countries(ix, "UK"), [])

    def test_the_field_of_teslascope_counts_only_with_cars_in_an_approved_country(self):
        ix = FakeIx({"Europe": ["nl", "ua"]}, S.DEFAULTS["fsd_countries"])
        row = {"version": "2026.38.5.11", "fsd_hw3": None, "fsd_hw4": "14.3.13"}
        self.assertEqual(A.chips_for(ix, row, {}, None, {}), [])                          # only Ukraine's 2 cars: not an FSD country
        self.assertEqual([c["v"] for c in A.chips_for(ix, row, {}, None, {"2026.38.5.11": 3})], ["14.3.13"])
        self.assertEqual([c["v"] for c in A.chips_for(ix, row, {}, None, None)], ["14.3.13"])   # no region asked: as before


if __name__ == "__main__":
    unittest.main()
