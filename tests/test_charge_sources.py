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
        self.b.pv_fetch = False
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


class FetchTest(unittest.TestCase):
    """A home charge on a day the meter file lacks gets that day's readings fetched, and a second look does not fetch again."""
    class Fake:
        name = "fake"
        calls = 0

        def fetch_intraday_range(self, d0, d1, key, acct):
            FetchTest.Fake.calls += 1
            return [dict(timestamp=datetime.datetime(2026, 10, 25, 10, 0), production_kwh=2.0, consumption_kwh=2.0, feedin_kwh=0.0,
                         purchased_kwh=0.0, self_consumption_kwh=2.0)], None

    def test_fetch(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "PV"))
        con = sqlite3.connect(os.path.join(d, "PV", "pv_history.db"))
        con.execute("CREATE TABLE pv_intraday (timestamp TEXT PRIMARY KEY, production_kwh REAL, consumption_kwh REAL, feedin_kwh REAL, "
                    "purchased_kwh REAL, self_consumption_kwh REAL, provider TEXT NOT NULL, fetched_at TEXT NOT NULL)")
        con.execute("INSERT INTO pv_intraday VALUES ('2026-10-22 12:45:00',1,1,0,0,1,'fake','x')")
        con.commit(); con.close()
        b = ChargeBook(d, d)
        b._pv_provider = lambda: (FetchTest.Fake(), "k", "a")
        t0 = b._tz_epoch("2026-10-25 09:50:00", "Australia/Sydney")
        r = b.sources(dict(home=True, t0=t0, t1=t0 + 1800, tz="Australia/Sydney", parts=[]), [])
        self.assertEqual([x["prod"] for x in r["buckets"]], [2.0])
        self.assertEqual(FetchTest.Fake.calls, 1)


if __name__ == "__main__":
    unittest.main()
