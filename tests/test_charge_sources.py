"""ChargeBook.sources(): the solar share (self-consumed / consumption, as lib/pv_history_db defines it) of a home charge from the site's 15-minute readings."""
import os, sqlite3, sys, tempfile, unittest, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
from lib.app_charges import ChargeBook


class SourcesTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, "PV"))
        con = sqlite3.connect(os.path.join(self.d, "PV", "pv_history.db"))
        con.execute("CREATE TABLE pv_intraday (timestamp TEXT, production_kwh REAL, consumption_kwh REAL, feedin_kwh REAL, "
                    "purchased_kwh REAL, self_consumption_kwh REAL, provider TEXT)")
        con.executemany("INSERT INTO pv_intraday VALUES (?,?,?,?,?,?,?)", [
            ("2026-10-22 12:45:00", 2.0, 2.0, 0.0, 0.0, 2.0, "SolarEdge"),
            ("2026-10-22 13:00:00", 1.0, 2.0, 0.0, 1.0, 1.0, "SolarEdge"),
            ("2026-10-22 13:15:00", 0.0, 0.0, 0.0, 0.0, 0.0, "SolarEdge")])
        con.commit(); con.close()
        self.b = ChargeBook(self.d, self.d)
        self.t0 = self.b._tz_epoch("2026-10-22 12:50:00", "Australia/Sydney")

    def test_not_home(self):
        self.assertIsNone(self.b.sources(dict(t0=self.t0, t1=self.t0 + 600), []))

    def test_shares(self):
        r = self.b.sources(dict(home=True, t0=self.t0, t1=self.t0 + 1800, tz="Australia/Sydney", parts=[]), [])
        fs = [x["f"] for x in r["buckets"]]
        self.assertEqual(fs, [1.0, 0.5, None])
        self.assertEqual(r["buckets"][0]["t"], self.b._tz_epoch("2026-10-22 12:45:00", "Australia/Sydney"))

    def test_no_meter_file(self):
        os.remove(os.path.join(self.d, "PV", "pv_history.db"))
        self.assertIsNone(self.b.sources(dict(home=True, t0=self.t0, t1=self.t0 + 60), []))


if __name__ == "__main__":
    unittest.main()
