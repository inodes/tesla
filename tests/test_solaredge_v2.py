"""test_solaredge_v2.py - the SolarEdge V2 client: OAuth tokens (saved on every refresh, since the old refresh token dies), which mode is active, and the meter telemetry
read into the five numbers the PV cache keeps.

    python3 -m unittest tests/test_solaredge_v2.py
"""
import os
import sys
import tempfile
import time
import unittest
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
from lib import solaredge_v2 as SE  # noqa: E402

CFG = {"solaredge": {"api_key": "old", "site_id": "1393211", "client_id": "cid", "client_secret": "sec"}}


def series(unit, vals):
    return {"unit": unit, "values": [{"timestamp": "2026-10-29T10:%02d:00+11:00" % (15 * i), "value": v} for i, v in enumerate(vals)]}


class V2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = SE.TOKEN_FILE
        SE.TOKEN_FILE = os.path.join(self.tmp, "t.json")

    def tearDown(self):
        import importlib
        importlib.reload(SE)                                                  # puts back anything a test replaced

    def test_modes(self):
        self.assertEqual(SE.mode({"solaredge": {"api_key": "k"}}), "v1")
        self.assertEqual(SE.mode({"solaredge": {"client_id": "PASTE_CLIENT_ID_HERE", "client_secret": "PASTE_CLIENT_SECRET_HERE", "api_key": "k"}}), "v1")
        self.assertEqual(SE.mode(CFG), "oauth-pending")
        SE.save_tokens({"refresh_token": "r"})
        self.assertEqual(SE.mode(CFG), "oauth")
        self.assertTrue(SE.active(CFG))
        self.assertEqual(SE.mode({"solaredge": {"fleet_api_key": "f"}}), "fleet")
        self.assertEqual(oct(os.stat(SE.TOKEN_FILE).st_mode & 0o777), "0o600")

    def test_exchange_then_refresh_keeps_the_new_pair(self):
        calls = []

        def post(body, timeout=30):
            calls.append(body)
            n = len(calls)
            return 200, dict(access_token="a%d" % n, refresh_token="r%d" % n, expires_in=7200)
        SE._post_token = post
        SE.exchange_code("code1", "1393211", CFG)
        self.assertEqual(SE.load_tokens()["site_id"], "1393211")
        self.assertEqual(SE._access_token(CFG), "a1")                       # still valid: no call
        self.assertEqual(len(calls), 1)
        t = SE.load_tokens()
        t["expires_at"] = time.time() + 5
        SE.save_tokens(t)
        self.assertEqual(SE._access_token(CFG), "a2")
        self.assertEqual(calls[1]["grant_type"], "refresh_token")
        self.assertEqual(calls[1]["refresh_token"], "r1")
        self.assertEqual(SE.load_tokens()["refresh_token"], "r2")             # the rotated one is the one kept

    def test_dead_refresh_token_is_recorded(self):
        SE.save_tokens({"refresh_token": "r", "expires_at": 0})
        SE._post_token = lambda body, timeout=30: (400, {"error": "invalid_request", "error_description": "Invalid refresh token"})
        with self.assertRaises(SE.V2Error):
            SE._access_token(CFG)
        self.assertIn("Invalid refresh", SE.load_tokens()["error"])

    def test_energy_series_are_summed_over_meters(self):
        body = {"meters": {"A": {"productionEnergy": series("WH", [1000, 2000]), "exportEnergy": series("WH", [400, 500])},
                           "B": {"importEnergy": series("WH", [100, 0]), "consumptionEnergy": series("WH", [700, 1500])}}}
        r = SE.parse_meter_telemetry(body, 0.25)
        ts = sorted(r)
        self.assertEqual(len(ts), 2)
        self.assertAlmostEqual(r[ts[0]]["production"], 1.0)
        self.assertAlmostEqual(r[ts[1]]["export"], 0.5)
        d = SE._derive(r[ts[0]])
        self.assertAlmostEqual(d["self_consumption_kwh"], 0.6)
        self.assertAlmostEqual(d["purchased_kwh"], 0.1)

    def test_power_only_becomes_energy_by_the_interval(self):
        body = {"meters": {"A": {"productionPower": series("W", [4000]), "exportPower": series("W", [2000])}}}
        r = SE.parse_meter_telemetry(body, 0.25)
        (rec,) = r.values()
        self.assertAlmostEqual(rec["production"], 1.0)                       # 4 kW for a quarter hour
        self.assertAlmostEqual(rec["export"], 0.5)

    def test_energy_wins_over_power_for_the_same_bucket(self):
        body = {"meters": {"A": {"productionPower": series("W", [4000]), "productionEnergy": series("WH", [900])}}}
        (rec,) = SE.parse_meter_telemetry(body, 0.25).values()
        self.assertAlmostEqual(rec["production"], 0.9)

    def test_consumption_is_worked_out_when_not_given(self):
        d = SE._derive(dict(production=2.0, export=0.5, **{"import": 0.25}))
        self.assertAlmostEqual(d["consumption_kwh"], 1.75)

    def test_too_long_a_span_is_halved_and_retried(self):
        seen = []

        def get(path, params=None, config=None, timeout=30):
            seen.append((params["from"][:10], params["to"][:10]))
            if len(seen) == 1:
                return 400, {"detail": "span too long"}, b""
            return 200, {"meters": {"A": {"productionEnergy": series("WH", [1000])}}}, b""
        SE.get = get
        try:
            rows, err = SE.meter_rows("1", date(2026, 10, 20), date(2026, 10, 25), "QUARTER_HOUR", days_per_call=6)
        finally:
            pass
        self.assertIsNone(err)
        self.assertEqual(len(seen), 3)

    def test_timestamps_carry_a_zone(self):
        self.assertRegex(SE._stamp(datetime(2026, 10, 29, 0, 0)), r"T00:00:00[+-]\d\d:\d\d$")
        self.assertEqual(SE._when("2026-10-29T10:15:00+11:00"), datetime(2026, 10, 29, 10, 15))


if __name__ == "__main__":
    unittest.main()
