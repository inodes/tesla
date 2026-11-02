#!/usr/bin/env python3
"""test_energy_history.py - energy and money history kept on disk: the battery health history (lib/battery_health.py), the
power usage database (lib/power_usage_db.py), PVOutput's parsers and solar split (lib/pvoutput_common.py) and its daily cache
(lib/pvoutput_history_db.py), SolarEdge's daily fetch (lib/solaredge_common.py) and cache (lib/solaredge_history_db.py), and the
Evnex "was this a real charge" check (lib/evnex_common.py).

Made-up readings only, in temporary folders. No network: every API call is replaced by a fake.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_energy_history.py
"""
import csv
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
from lib import battery_health as BH  # noqa: E402
from lib import power_usage_db as PU  # noqa: E402
from lib import pvoutput_common as PV  # noqa: E402
from lib import pvoutput_history_db as PVDB  # noqa: E402
from lib import solaredge_common as SE  # noqa: E402
from lib import solaredge_history_db as SEDB  # noqa: E402
from lib import evnex_common as EV  # noqa: E402


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_energy_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path


# ------------------------------------------------------------------------------------------------------------ battery health
BATTERY_HEADER = "Timestamp ({zone}),Max Range (km),Max Ideal Range (km),Usable Capacity (kWh),Odometer (km)\n"


class BatteryHealthTest(TempDirTest):
    def test_nothing_on_disk(self):
        self.assertIsNone(BH.load_battery_health_history(self.tmp))
        self.assertIsNone(BH.load_battery_health_history(None))

    def test_csv_with_either_zone_label(self):
        self.write("battery_a.csv", BATTERY_HEADER.format(zone="AEST") + "2026-06-01 10:00:00,500,510,75.5,1000\n")
        self.write("battery_b.csv", BATTERY_HEADER.format(zone="AEDT") + "2026-11-01 10:00:00,495,505,75.0,2000\n")
        hist = BH.load_battery_health_history(self.tmp)
        self.assertEqual([e["synced_at"] for e in hist], ["2026-06-01T10:00:00", "2026-11-01T10:00:00"])
        self.assertEqual(hist[1]["capacity_kwh"], 75.0)
        self.assertEqual(hist[1]["odometer_km"], 2000.0)
        self.assertEqual(hist[0]["source"], "csv:battery_a.csv")

    def test_bad_rows_and_other_csvs_are_skipped(self):
        self.write("battery.csv", BATTERY_HEADER.format(zone="AEST")
                   + "not a date,500,510,75.5,1\n"
                   + "2026-06-02 10:00:00,500,510,,1\n"          # no capacity
                   + ",500,510,75.5,1\n"
                   + "2026-06-03 10:00:00,,,74.9,\n")
        self.write("drives.csv", "Started At,Distance (km)\n2026-06-01 10:00:00,12\n")   # not a battery export
        hist = BH.load_battery_health_history(self.tmp)
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["capacity_kwh"], 74.9)
        self.assertIsNone(hist[0]["max_range_km"])
        self.assertIsNone(hist[0]["odometer_km"])

    def test_log_and_csv_merge_log_wins_outliers_dropped(self):
        log = [
            {"synced_at": "2026-06-01T10:00:00", "capacity_kwh": 76.0},
            {"synced_at": "2026-07-01T10:00:00", "capacity_kwh": 60.0, "is_outlier": True},
            {"synced_at": "2026-05-01T10:00:00", "capacity_kwh": None},      # no reading: skipped
            "junk",
        ]
        self.write("battery_health_log.json", json.dumps(log))
        self.write("battery.csv", BATTERY_HEADER.format(zone="AEST")
                   + "2026-06-01 10:00:00,500,510,75.5,1\n2026-04-01 10:00:00,505,515,76.5,1\n")
        hist = BH.load_battery_health_history(self.tmp)
        self.assertEqual([(e["synced_at"], e["capacity_kwh"]) for e in hist],
                         [("2026-04-01T10:00:00", 76.5), ("2026-06-01T10:00:00", 76.0)])
        self.assertEqual(hist[1]["source"], "battery_health_log.json")
        with_outliers = BH.load_battery_health_history(self.tmp, include_outliers=True)
        self.assertIn(60.0, [e["capacity_kwh"] for e in with_outliers])

    def test_unreadable_log_is_ignored(self):
        self.write("battery_health_log.json", "{not json")
        self.assertIsNone(BH.load_battery_health_history(self.tmp))


# ------------------------------------------------------------------------------------------------------------ power usage db
class FakePowerProvider:
    """Day-first dates like a real retailer's export: '25/07/2026 12:00:00 AM'."""
    name = "fake"

    def __init__(self, root):
        self.root = root

    def parse_datetime(self, s):
        return datetime.strptime(s, "%d/%m/%Y %I:%M:%S %p")

    def resolve_data_home(self):
        return os.path.join(self.root, "home")

    def resolve_invoices_dir(self):
        return os.path.join(self.root, "invoices")


def usage_row(start, end, code="E1", role="import", kwh="0.5", src="a.csv"):
    return (start, end, code), dict(StartDate=start, EndDate=end, RegisterCode=code, Role=role, kWh=kwh, SourceFile=src)


class PowerUsageDbTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.provider = FakePowerProvider(self.tmp)
        self.conn = PU.get_usage_db_connection(self.provider)
        self.addCleanup(self.conn.close)
        rows = dict([
            usage_row("25/07/2026 11:30:00 PM", "26/07/2026 12:00:00 AM", kwh="0.25"),
            usage_row("02/08/2026 12:00:00 AM", "02/08/2026 12:30:00 AM", kwh="1"),
            usage_row("02/08/2026 12:00:00 AM", "02/08/2026 12:30:00 AM", code="B1", role="export", kwh="2"),
            usage_row("31/07/2026 11:30:00 PM", "01/08/2026 12:00:00 AM", kwh="0.75"),
        ])
        PU.upsert_rows(self.conn, rows, self.provider)
        self.conn.commit()

    def test_paths_and_names(self):
        self.assertEqual(PU.resolve_usage_db_path(self.provider), os.path.join(self.tmp, "home", "usage", "fake_usage_history.db"))
        self.assertEqual(PU.monthly_csv_filename(self.provider, "202608"), "FAKE_202608.csv")
        self.assertEqual(PU.resolve_monthly_dir(self.provider), os.path.join(self.tmp, "invoices"))
        self.assertEqual(PU.resolve_monthly_dir(self.provider, "/elsewhere"), "/elsewhere")

    def test_rows_come_back_in_time_order_not_text_order(self):
        starts = [r["StartDate"] for r in PU.iter_rows(self.conn, role="import")]
        self.assertEqual(starts, ["25/07/2026 11:30:00 PM", "31/07/2026 11:30:00 PM", "02/08/2026 12:00:00 AM"])
        r = next(PU.iter_rows(self.conn, role="import"))
        self.assertEqual(r["kWh"], "0.25")          # text, as the old CSV gave it
        self.assertEqual(set(r), {"StartDate", "EndDate", "RegisterCode", "Role", "kWh", "SourceFile"})

    def test_date_filter_is_inclusive_of_whole_days(self):
        rows = list(PU.iter_rows(self.conn, role="import", start_date=date(2026, 7, 26), end_date=date(2026, 7, 31)))
        self.assertEqual([r["StartDate"] for r in rows], ["31/07/2026 11:30:00 PM"])   # the 11:30 pm reading of the last day is kept
        rows = list(PU.iter_rows(self.conn, start_date=date(2026, 8, 2), end_date=date(2026, 8, 2)))
        self.assertEqual(sorted(r["Role"] for r in rows), ["export", "import"])

    def test_upsert_replaces_by_key(self):
        self.assertEqual(len(PU.existing_keys(self.conn)), 4)
        PU.upsert_rows(self.conn, dict([usage_row("02/08/2026 12:00:00 AM", "02/08/2026 12:30:00 AM", kwh="9", src="b.csv")]), self.provider)
        self.assertEqual(len(PU.existing_keys(self.conn)), 4)
        r = [r for r in PU.iter_rows(self.conn, role="import") if r["StartDate"].startswith("02/08")][0]
        self.assertEqual((r["kWh"], r["SourceFile"]), ("9.0", "b.csv"))

    def test_unparseable_date_is_kept_but_not_in_any_month(self):
        PU.upsert_rows(self.conn, dict([usage_row("someday", "later")]), self.provider)
        self.assertEqual(PU.year_months_present(self.conn), ["202607", "202608"])
        self.assertEqual(PU.year_months_present(self.conn, only=["202608", None, "202608"]), ["202608"])
        self.assertNotIn("someday", [r["StartDate"] for r in PU.iter_rows(self.conn, start_date=date(2000, 1, 1))])

    def test_monthly_csv(self):
        dest, n = PU.write_monthly_csv(self.conn, self.provider, "202607", log=lambda *a: None)
        self.assertEqual((os.path.basename(dest), n), ("FAKE_202607.csv", 2))
        with open(dest, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r["StartDate"] for r in rows], ["25/07/2026 11:30:00 PM", "31/07/2026 11:30:00 PM"])
        self.assertFalse(os.path.exists(dest + ".tmp"))

    def test_sync_log_and_legacy_master(self):
        PU.record_sync(self.conn, "a.csv", 4, 4)
        PU.record_sync(self.conn, "a.csv", 5, 1)
        self.assertEqual(list(self.conn.execute("SELECT row_count, new_row_count FROM usage_sync_log")), [(5, 1)])
        self.assertEqual(PU.migrate_legacy_master_csv(self.conn, self.provider, os.path.join(self.tmp, "none.csv")), 0)
        master = self.write("master.csv", "StartDate,EndDate,RegisterCode,Role,kWh\n"
                                          "01/09/2026 12:00:00 AM,01/09/2026 12:30:00 AM,E1,import,0.4\n")
        self.assertEqual(PU.migrate_legacy_master_csv(self.conn, self.provider, master, log=lambda *a: None), 1)
        self.assertIn("202609", PU.year_months_present(self.conn))


# ------------------------------------------------------------------------------------------------------------ PVOutput
def status_body(rows):
    """A made-up getstatus.jsp?h=1 body: date,time,gen_wh,...,cons_wh at index 7."""
    return ";".join(f"{d},{t},{g},0,0,0,0,{c}" for d, t, g, c in rows).encode()


class PVOutputParseTest(unittest.TestCase):
    def test_status_rows(self):
        self.assertEqual(PV.parse_status_rows(b""), [])
        self.assertEqual(PV.parse_status_rows(b"a,b;c,d;"), [["a", "b"], ["c", "d"]])

    def test_history_readings_sorted_and_bad_rows_skipped(self):
        rows = PV.parse_status_rows(status_body([("20260601", "10:05", 200, 300), ("20260601", "10:00", 100, 150)]))
        rows += [["20260601", "10:10", "x", "0", "0", "0", "0", "1"], ["short"]]
        got = PV.parse_history_readings(rows)
        self.assertEqual(got, [(datetime(2026, 6, 1, 10, 0), 100.0, 150.0), (datetime(2026, 6, 1, 10, 5), 200.0, 300.0)])

    def test_day_intervals(self):
        r = [(datetime(2026, 6, 1, 6, 0), 0.0, 1000.0),
             (datetime(2026, 6, 1, 6, 5), float("nan"), 1100.0),    # a NaN reading is skipped, not a zero
             (datetime(2026, 6, 1, 6, 10), 50.0, 1200.0),
             (datetime(2026, 6, 1, 6, 15), 40.0, 1300.0)]           # a lower counter never makes negative energy
        got = PV.day_intervals(r)
        self.assertEqual(got[0], (datetime(2026, 6, 1, 0, 0), datetime(2026, 6, 1, 6, 0), 0.0, 1000.0))   # from midnight
        self.assertEqual(got[1], (datetime(2026, 6, 1, 6, 0), datetime(2026, 6, 1, 6, 10), 50.0, 200.0))
        self.assertEqual(got[2][2:], (0.0, 100.0))
        self.assertEqual(PV.day_intervals([]), [])

    def test_window_split_is_prorated_and_self_use_is_per_interval(self):
        t = lambda h, m=0: datetime(2026, 6, 1, h, m)
        # interval 1: lots of sun, little use; interval 2: no sun, lots of use
        ivals = [(t(10), t(11), 2000.0, 500.0), (t(11), t(12), 0.0, 1500.0)]
        s = PV.window_energy_split(ivals, t(10), t(12))
        self.assertAlmostEqual(s["generation_kwh"], 2.0)
        self.assertAlmostEqual(s["consumption_kwh"], 2.0)
        self.assertAlmostEqual(s["self_consumed_kwh"], 0.5)          # not min(2.0, 2.0) of the totals
        self.assertAlmostEqual(s["export_kwh"], 1.5)
        self.assertAlmostEqual(s["import_kwh"], 1.5)
        self.assertAlmostEqual(s["coverage"], 1.0)
        half = PV.window_energy_split(ivals, t(10, 30), t(11, 30))
        self.assertAlmostEqual(half["generation_kwh"], 1.0)
        self.assertAlmostEqual(half["consumption_kwh"], 1.0)
        part = PV.window_energy_split(ivals, t(11), t(13))
        self.assertAlmostEqual(part["coverage"], 0.5)
        self.assertEqual(PV.window_energy_split(ivals, t(13), t(13))["coverage"], 0.0)

    def test_output_day_rows(self):
        rows = [["20260602", "5000", "x", "2000", "3000"], ["20260601", "4000", "x", "1000", "NaN"],
                ["20260603", "1"], ["bad", "1", "1", "1", "1"]]
        self.assertEqual(PV.parse_output_day_rows(rows), [(date(2026, 6, 2), 5.0, 2.0, 3.0)])

    def test_pv_agl_conflict(self):
        self.assertTrue(PV.pv_agl_import_conflict(10.0, 1.0, 6.0, False))       # 40% apart
        self.assertFalse(PV.pv_agl_import_conflict(10.0, 1.0, 8.0, False))      # 20% apart
        self.assertFalse(PV.pv_agl_import_conflict(10.0, 0.5, 1.0, False))      # too little PV coverage
        self.assertFalse(PV.pv_agl_import_conflict(10.0, 1.0, 1.0, True))       # AGL missing half-hours
        self.assertFalse(PV.pv_agl_import_conflict(0.4, 1.0, 0.1, False))       # both tiny
        self.assertFalse(PV.pv_agl_import_conflict(None, 1.0, 1.0, False))
        self.assertFalse(PV.pv_agl_import_conflict(1.0, None, 1.0, False))


class PVWindowSplitTest(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def fake_day(d, key, system):
            self.calls.append(d)
            return [(datetime(d.year, d.month, d.day, 12, 0), 1200.0, 2400.0)], None    # 1.2 kWh made, 2.4 used, by noon
        p = mock.patch.object(PV, "fetch_day_readings", side_effect=fake_day)
        p.start()
        self.addCleanup(p.stop)

    def test_one_day_and_the_shared_cache(self):
        cache = {}
        s = PV.pv_window_split(datetime(2026, 6, 1, 0, 0), datetime(2026, 6, 1, 12, 0), "k", "s", day_cache=cache)
        self.assertAlmostEqual(s["generation_kwh"], 1.2)
        self.assertAlmostEqual(s["solar_fraction"], 0.5)
        self.assertIsNone(s["error"])
        PV.pv_window_split(datetime(2026, 6, 1, 6, 0), datetime(2026, 6, 1, 9, 0), "k", "s", day_cache=cache)
        self.assertEqual(self.calls, [date(2026, 6, 1)])                 # fetched once

    def test_nothing_fetched(self):
        with mock.patch.object(PV, "fetch_day_readings", return_value=([], "HTTP 401: no")):
            s = PV.pv_window_split(datetime(2026, 6, 1, 0, 0), datetime(2026, 6, 1, 12, 0), "k", "s")
        self.assertIsNone(s["generation_kwh"])
        self.assertIn("HTTP 401", s["error"])

    @unittest.expectedFailure
    def test_a_window_over_three_days_reads_the_middle_day(self):
        """BUG (found by this test): only the first and last calendar day of the window are fetched, so a window from
        one evening to the morning two days later leaves out the whole middle day (coverage drops, energy is missing).
        No current caller (lib/pv_history_db.py's own pv_window_split replaced this one for charging sessions)."""
        PV.pv_window_split(datetime(2026, 6, 1, 20, 0), datetime(2026, 6, 3, 8, 0), "k", "s")
        self.assertEqual(sorted(self.calls), [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)])


class PVOutputDailyCacheTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.conn = PVDB.get_pvoutput_db_connection(data_home=self.tmp)
        self.addCleanup(self.conn.close)

    def test_upsert_and_query(self):
        PVDB.upsert_days(self.conn, [(date(2020, 1, 1), 10.0, 4.0, 8.0), (date(2020, 1, 3), 12.0, 5.0, 9.0)])
        PVDB.upsert_days(self.conn, [(date(2020, 1, 1), 11.0, 4.5, 8.5)])
        got = PVDB.query_range(self.conn, date(2020, 1, 1), date(2020, 1, 2))
        self.assertEqual(got, {date(2020, 1, 1): {"generation_kwh": 11.0, "exported_kwh": 4.5, "used_kwh": 8.5}})
        self.assertEqual(PVDB.days_present(self.conn), {"2020-01-01", "2020-01-03"})

    def test_sync_fetches_only_the_missing_runs(self):
        PVDB.upsert_days(self.conn, [(date(2020, 1, 3), 12.0, 5.0, 9.0)])
        calls = []

        def fake_range(a, b, key, system):
            calls.append((a, b))
            return [(a, 1.0, 0.5, 0.8)], None
        with mock.patch.object(PV, "fetch_output_range", side_effect=fake_range):
            n = PVDB.sync_range(self.conn, date(2020, 1, 1), date(2020, 1, 5), "k", "s", log=lambda *a: None)
            self.assertEqual(calls, [(date(2020, 1, 1), date(2020, 1, 2)), (date(2020, 1, 4), date(2020, 1, 5))])
            self.assertEqual(n, 2)
            calls.clear()
            self.assertEqual(PVDB.sync_range(self.conn, date(2020, 1, 3), date(2020, 1, 4), "k", "s", log=lambda *a: None), 0)
            self.assertEqual(calls, [])

    def test_today_is_always_fetched_again(self):
        today = date.today()
        PVDB.upsert_days(self.conn, [(today, 1.0, 0.0, 1.0)])
        with mock.patch.object(PV, "fetch_output_range", return_value=([], None)) as f:
            PVDB.sync_range(self.conn, today, today, "k", "s", log=lambda *a: None)
        f.assert_called_once_with(today, today, "k", "s")


# ------------------------------------------------------------------------------------------------------------ SolarEdge
def energy_details(values_by_meter):
    return {"energyDetails": {"meters": [{"type": t, "values": v} for t, v in values_by_meter.items()]}}


class SolarEdgeFetchTest(unittest.TestCase):
    def test_wh_to_kwh_nulls_and_unknown_meters(self):
        body = energy_details({
            "Production": [{"date": "2026-06-01 00:00:00", "value": 12500.0}, {"date": "2026-06-02 00:00:00", "value": None}],
            "Purchased": [{"date": "2026-06-01 00:00:00", "value": 3000.0}],
            "Mystery": [{"date": "2026-06-01 00:00:00", "value": 1.0}],
        })
        with mock.patch.object(SE, "api_get", return_value=(200, body, b"")):
            rows, err = SE.fetch_energy_details_range(date(2026, 6, 1), date(2026, 6, 2), "k", "1")
        self.assertIsNone(err)
        self.assertEqual(rows, [{"day": date(2026, 6, 1), "production_kwh": 12.5, "consumption_kwh": None, "feedin_kwh": None,
                                 "purchased_kwh": 3.0, "self_consumption_kwh": None}])

    def test_long_range_is_split_at_365_days(self):
        seen = []

        def fake(endpoint, key, params=None, timeout=30):
            seen.append((params["startTime"], params["endTime"]))
            return 200, energy_details({}), b""
        with mock.patch.object(SE, "api_get", side_effect=fake):
            SE.fetch_energy_details_range(date(2025, 1, 1), date(2026, 2, 1), "k", "1")
        self.assertEqual(seen, [("2025-01-01 00:00:00", "2025-12-31 23:59:59"), ("2026-01-01 00:00:00", "2026-02-01 23:59:59")])

    def test_errors_are_reported_and_other_chunks_kept(self):
        good = energy_details({"Production": [{"date": "2026-01-01 00:00:00", "value": 1000.0}]})
        answers = iter([(400, {"detail": "Data range interval exceeds one year"}, b""), (200, good, b"")])
        with mock.patch.object(SE, "api_get", side_effect=lambda *a, **k: next(answers)):
            rows, err = SE.fetch_energy_details_range(date(2025, 1, 1), date(2026, 1, 1), "k", "1")
        self.assertIn("HTTP 400: Data range interval exceeds one year", err)
        self.assertEqual([r["day"] for r in rows], [date(2026, 1, 1)])

    def test_credentials_order(self):
        cfg = {"solaredge": {"api_key": "from-config", "site_id": "9"}}
        with mock.patch.dict(os.environ, {"SOLAREDGE_API_KEY": "from-env"}, clear=False):
            self.assertEqual(SE.get_api_key("from-cli", cfg), "from-cli")
            self.assertEqual(SE.get_api_key(None, cfg), "from-env")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(SE.get_api_key(None, cfg), "from-config")
            self.assertEqual(SE.get_site_id(None, cfg), "9")
            self.assertIsNone(SE.get_site_id(None, {}))


class SolarEdgeDailyCacheTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.conn = SEDB.get_solaredge_db_connection(data_home=self.tmp)
        self.addCleanup(self.conn.close)

    @staticmethod
    def day(d, prod=10.0):
        return {"day": d, "production_kwh": prod, "consumption_kwh": 8.0, "feedin_kwh": 4.0, "purchased_kwh": 2.0, "self_consumption_kwh": 6.0}

    def test_upsert_query_and_missing_runs(self):
        SEDB.upsert_days(self.conn, [self.day(date(2020, 3, 2)), self.day(date(2020, 3, 2), prod=11.0)])
        self.assertEqual(SEDB.query_range(self.conn, date(2020, 3, 1), date(2020, 3, 31))[date(2020, 3, 2)]["production_kwh"], 11.0)
        calls = []

        def fake_range(a, b, key, site):
            calls.append((a, b))
            return [self.day(a)], None
        with mock.patch.object(SE, "fetch_energy_details_range", side_effect=fake_range):
            n = SEDB.sync_range(self.conn, date(2020, 3, 1), date(2020, 3, 3), "k", "1", log=lambda *a: None)
        self.assertEqual(calls, [(date(2020, 3, 1), date(2020, 3, 1)), (date(2020, 3, 3), date(2020, 3, 3))])
        self.assertEqual(n, 2)
        self.assertEqual(SEDB.days_present(self.conn), {"2020-03-01", "2020-03-02", "2020-03-03"})


# ------------------------------------------------------------------------------------------------------------ Evnex
class EvnexSessionTest(unittest.TestCase):
    def test_energy_and_real_charge(self):
        self.assertEqual(EV.session_energy_wh({"totalEnergyUsage": {"total": 5400.0}}), 5400.0)
        self.assertIsNone(EV.session_energy_wh({}))
        self.assertIsNone(EV.session_energy_wh({"totalEnergyUsage": 5}))
        self.assertTrue(EV.is_real_charging_session({"totalEnergyUsage": {"total": 5400.0}}))
        self.assertFalse(EV.is_real_charging_session({"totalEnergyUsage": {"total": 0.0}}))    # a plug-in that never drew power
        self.assertFalse(EV.is_real_charging_session({"totalEnergyUsage": {"total": 0.5}}))
        self.assertTrue(EV.is_real_charging_session({}))                                       # no figure is not "no charge"
        self.assertTrue(EV.is_real_charging_session({"totalEnergyUsage": {"total": 0.5}}, min_wh=0.1))


if __name__ == "__main__":
    unittest.main()
