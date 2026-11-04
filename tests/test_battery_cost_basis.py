import csv
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools"))
from lib import battery_cost_basis as B  # noqa: E402
from lib import fuel_common as FC  # noqa: E402

HDR = ["Started At (UTC)", "Energy Added (kWh)", "Starting Battery (%)", "Ending Battery (%)", "Cost"]


def make_dir(rows):
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "charges"))
    with open(os.path.join(d, "charges", "charges_master.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HDR)
        w.writerows(rows)
    return d


# a 100 kWh pack (1 kWh per %): charge 1 at $0.10/kWh from 10% to 90%, charge 2 at $0.50/kWh from 50% to 90%, ...
ROWS = [(1000, 80, 10, 90, 8.0), (2000, 40, 50, 90, 20.0), (3000, 40, 50, 90, 20.0), (4000, 40, 50, 90, 20.0)]


class BasisTest(unittest.TestCase):
    def test_pack_size_is_read_from_the_charges(self):
        b = B.BatteryCostBasis(make_dir(ROWS))
        self.assertAlmostEqual(b.pack, 100.0)

    def test_pack_size_follows_the_battery_health_readings_over_time(self):
        import json
        d = make_dir(ROWS)
        with open(os.path.join(d, "battery_health_log.json"), "w") as f:
            json.dump([dict(synced_at="2026-01-01T00:00:00", capacity_kwh=80.0), dict(synced_at="2026-06-01T00:00:00", capacity_kwh=78.0)], f)
        b = B.BatteryCostBasis(d)
        s = B.capacity_series(d)
        self.assertEqual([k for _, k in s], [80.0, 78.0])
        self.assertEqual(B.pack_at(s, s[0][0] + 1), 80.0)
        self.assertEqual(B.pack_at(s, s[1][0] + 1), 78.0)
        self.assertEqual(B.pack_at(s, s[0][0] - 10 ** 6), 80.0)         # before the first reading: the first one
        self.assertEqual(b.pack, 78.0)                                  # the latest
        self.assertEqual(B.pack_at([], 5, 100.0), 100.0)                # no reading at all: the charges' own estimate

    def test_no_price_while_the_energy_from_before_is_still_most_of_the_pack(self):
        b = B.BatteryCostBasis(make_dir(ROWS))
        self.assertIsNone(b.rate_at(500))           # before any charge
        self.assertIsNone(b.rate_at(1000))          # 10 kWh of the pack was there before charge 1: 11% unknown

    def test_price_moves_with_the_charges(self):
        b = B.BatteryCostBasis(make_dir(ROWS))
        r2, r3, r4 = b.rate_at(2000), b.rate_at(3000), b.rate_at(4000)
        self.assertIsNotNone(r2)
        self.assertTrue(0.10 < r2 < 0.50)            # charge 2 mixes $0.50/kWh energy into $0.10/kWh energy
        self.assertTrue(r2 < r3 < r4 < 0.50)         # and keeps moving towards it as more is bought
        self.assertEqual(b.rate_at(2999), r2)        # a step holds until the next charge

    def test_drive_cost_is_kwh_times_the_rate_then(self):
        b = B.BatteryCostBasis(make_dir(ROWS))
        self.assertAlmostEqual(b.drive_cost(3500, 10.0), 10.0 * b.rate_at(3500))
        self.assertIsNone(b.drive_cost(500, 10.0))
        self.assertIsNone(b.drive_cost(3500, None))

    def test_no_charges_no_price(self):
        b = B.BatteryCostBasis(make_dir([]))
        self.assertIsNone(b.rate_at(10 ** 9))

    def test_chargebook_cost_replaces_the_records_cost(self):
        d = make_dir(ROWS)
        import json
        with open(os.path.join(d, "charges", "app_chargebook.json"), "w") as f:
            json.dump(dict(plugs=[dict(cost=80.0, parts=[dict(id="2000", kwh=40)])]), f)   # the invoice says charge 2 cost $2/kWh
        b = B.BatteryCostBasis(d)
        self.assertGreater(b.rate_at(2000), 0.5)


class DriveFuelTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(FC, "get_fuel_price_dated",                     # a price held, so the block is drawn; never the real fuel database
                              return_value=dict(price=2.0, price_date="2026-06-01", basis="day"))
        p.start()
        self.addCleanup(p.stop)

    def test_ev_cost_uses_the_batterys_price_at_that_time_not_a_fixed_rate(self):
        d = make_dir(ROWS)
        rate = B.for_dir(d).rate_at(3500)
        lines = "\n".join(FC.format_drive_fuel_comparison(20.0, "2026-06-01", kwh_used=10.0, tessie_dir=d, start_epoch=3500))
        self.assertIn("%.2f" % (10.0 * rate), lines)

    def test_no_ev_cost_without_a_known_price(self):
        d = make_dir(ROWS)
        for ep in (None, 500):
            lines = "\n".join(FC.format_drive_fuel_comparison(20.0, "2026-06-01", kwh_used=10.0, tessie_dir=d, start_epoch=ep))
            self.assertNotIn("EV Energy", lines)


if __name__ == "__main__":
    unittest.main()
