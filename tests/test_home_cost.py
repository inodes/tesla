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
        from unittest import mock
        with mock.patch("lib.power_demand.load_rate_periods", return_value=[]):      # never the real tariff file: the test runs the same with no data folder (cloud, CI)
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


class SendState(unittest.TestCase):
    def test_what_there_is_nothing_to_send_for_is_not_pending(self):
        self.assertEqual(AC.send_state(None, None, None, False)[0], "na")                                   # no invoice
        self.assertEqual(AC.send_state(7.19, None, None, True), ("na", "not in Tessie"))                    # an invoice for a charge Tessie has no record of
        self.assertEqual(AC.send_state(7.19, None, dict(cost=3.0), True)[0], "pending")                     # the same invoice once Tessie has the record
        self.assertEqual(AC.send_state(7.19, None, None, False)[0], "pending")                              # a normal charge not (yet) in the cache

    def test_what_tessie_holds_is_synced(self):
        self.assertEqual(AC.send_state(0.32, 0.32, dict(cost=0.94), False)[0], "synced")                    # sent by us
        self.assertEqual(AC.send_state(0.32, None, dict(cost=0.32), False)[0], "synced")                    # Tessie's own figure already is it


class Placeholders(unittest.TestCase):
    def peer(self, kwh, mn, b0, b1, rng):
        return dict(kind="dc", kwh=kwh, min=mn, b0=b0, b1=b1, range=rng, invoice_only=False)

    def test_an_invoice_with_no_tessie_record_gets_labelled_averages_and_nothing_real_changes(self):
        lone = dict(kind="dc", kwh=12.0, min=0, b0=0, b1=0, range=0, invoice_only=True)
        real = self.peer(10.0, 10, 20, 40, 60)                              # 60 kW, 50 kWh pack, 6 km per kWh
        done = self.peer(20.0, 20, 40, 80, 120)
        AC.placeholders([lone, real, done])
        self.assertEqual(lone["est"], dict(min=12, pct=24, range=72, n=2))
        self.assertEqual((lone["min"], lone["b1"], lone["range"]), (0, 0, 0))
        self.assertNotIn("est", real)

    def test_nothing_is_made_up_without_charges_of_that_kind_or_for_a_real_charge(self):
        lone = dict(kind="supercharger", kwh=12.0, min=0, b0=0, b1=0, range=0, invoice_only=True)
        AC.placeholders([lone, self.peer(10.0, 10, 20, 40, 60)])
        self.assertNotIn("est", lone)
        timed = dict(kind="dc", kwh=12.0, min=9, b0=10, b1=30, range=50, invoice_only=True)
        AC.placeholders([timed, self.peer(10.0, 10, 20, 40, 60)])
        self.assertNotIn("est", timed)


if __name__ == "__main__":
    unittest.main()
