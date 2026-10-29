"""test_home_cost.py - a Home charge costs what the scripts say: charge_cost_model.home_charge_cost is the one place, and the app reads its figure (not Tessie's flat rate).

    python3 -m unittest tests/test_home_cost.py
"""
import datetime
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_charges as AC  # noqa: E402
import charge_cost_model as CCM  # noqa: E402

RAW = dict(started_at=datetime.datetime(2026, 10, 29, 10, 48), ended_at=datetime.datetime(2026, 10, 29, 12, 4), energy_used_kwh=7.85, energy_added_kwh=7.5, cost=0.94, cost_per_kwh=0.12)
TIERED = dict(kwh_total=7.85, usage_rate=0.1593, tier1_rate=0.10, tier2_rate=0.04, solar_kwh=7.784286, grid_kwh=0.065714, tier1_kwh=0.0, tier2_kwh=7.784286, cumulative_export_before_kwh=13.8452,
              pv_solar_fraction=0.9916, pv_coverage=1.0, real_cost_incl_gst=0.3229, base_cost_incl_gst=1.375, used_flat_tier1_fallback=False)


def stub(result, seen=None):
    m = types.ModuleType("lib.session_cost")

    def ev(row, periods):
        if seen is not None:
            seen.append(row)
        return dict(result)
    m.evaluate_tessie_charge = ev
    return m


class HomeCostTest(unittest.TestCase):
    def setUp(self):
        self.old = sys.modules.get("lib.session_cost")
        AC._HOME_MEMO.clear()

    def tearDown(self):
        if self.old is None:
            sys.modules.pop("lib.session_cost", None)
        else:
            sys.modules["lib.session_cost"] = self.old

    def test_tiered_model_is_the_cost(self):
        seen = []
        sys.modules["lib.session_cost"] = stub(TIERED, seen)
        r = CCM.home_charge_cost(RAW, periods=[])
        self.assertEqual(r["source"], "tiered_solar_model")
        self.assertAlmostEqual(r["cost"], 0.3229, 4)
        self.assertEqual(seen[0]["kwh"], 7.85)

    def test_no_solar_data_is_flagged_not_hidden(self):
        sys.modules["lib.session_cost"] = stub(dict(TIERED, real_cost_incl_gst=None, pv_solar_fraction=None))
        r = CCM.home_charge_cost(RAW, periods=[])
        self.assertEqual(r["source"], "flat_rate_fallback(no_pv_data)")
        self.assertAlmostEqual(r["cost"], 1.375, 3)

    def test_energy_used_missing_prices_energy_added(self):
        seen = []
        sys.modules["lib.session_cost"] = stub(TIERED, seen)
        r = CCM.home_charge_cost(dict(RAW, energy_used_kwh=0), periods=[])
        self.assertEqual(seen[0]["kwh"], 7.5)
        self.assertTrue(any("Energy Used missing" in n for n in r["notes"]))

    def test_app_shows_model_and_remembers_it(self):
        seen = []
        sys.modules["lib.session_cost"] = stub(TIERED, seen)
        m = AC.home_model(CCM, RAW)
        self.assertAlmostEqual(m["cost"], 0.3229, 4)
        self.assertAlmostEqual(m["rate"], 0.3229 / 7.85, 4)
        self.assertEqual((m["tier2_rate"], m["before_kwh"], m["usage_rate"]), (0.04, 13.8452, 0.1593))
        AC.home_model(CCM, RAW)
        self.assertEqual(len(seen), 1)

    def test_app_has_nothing_when_unpriced(self):
        sys.modules["lib.session_cost"] = stub(dict(TIERED, real_cost_incl_gst=None, pv_solar_fraction=None))
        self.assertIsNone(AC.home_model(CCM, RAW))
        self.assertIsNone(AC.home_model(CCM, dict(RAW, ended_at=None)))
        self.assertIsNone(AC.home_model(CCM, {}))


if __name__ == "__main__":
    unittest.main()
