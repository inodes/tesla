#!/usr/bin/env python3
"""test_alerts_snapshots.py - firmware alerts and drive snapshots: local clock to UTC, the two alert files merged, repeats
joined into episodes, an alert tied to the drive / charge / park it happened in, and which dashcam events get a still every second.

Covers lib/app_alerts.py, lib/firmware_alert_catalog.py and lib/drive_snapshots.py (the pure parts: no video, no ffmpeg).
Made-up alerts, drives and points only, in temp folders. Runs without config or data:
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_alerts_snapshots.py
"""
import calendar
import csv
import json
import os
import sys
import tempfile
import unittest
import zoneinfo
from datetime import datetime
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import clip_clock as CC  # noqa: E402
import app_alerts as AA  # noqa: E402
import firmware_alert_catalog as CAT  # noqa: E402
import drive_snapshots as SN  # noqa: E402

SYD = zoneinfo.ZoneInfo("Australia/Sydney")


def utc(y, mo, d, h, mi, s=0):
    return calendar.timegm((y, mo, d, h, mi, s))


def write_csv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow(r)


class SydneyClock(unittest.TestCase):
    """Every test here reads local clocks as Sydney, whatever config.json says."""

    def setUp(self):
        p = mock.patch.object(CC, "zone", lambda: SYD)
        p.start()
        self.addCleanup(p.stop)


class LocalEpochTest(SydneyClock):
    def test_summer_reading_is_aedt(self):
        # 12:00 on 15 Jan in Sydney is 01:00 UTC (AEDT, +11)
        self.assertEqual(AA._local_epoch("2026-01-15 12:00:00"), utc(2026, 1, 15, 1, 0))

    def test_winter_reading_is_aest(self):
        # 12:00 on 15 Jul in Sydney is 02:00 UTC (AEST, +10)
        self.assertEqual(AA._local_epoch("2026-07-15 12:00:00"), utc(2026, 7, 15, 2, 0))

    def test_minutes_only_reading(self):
        self.assertEqual(AA._local_epoch("2026-07-15 12:00"), utc(2026, 7, 15, 2, 0))

    def test_text_after_the_seconds_is_ignored(self):
        self.assertEqual(AA._local_epoch("2026-07-15 12:00:00.123 AEST"), utc(2026, 7, 15, 2, 0))

    def test_unreadable_is_none(self):
        for s in ("", "yesterday", "15/07/2026 12:00"):
            self.assertIsNone(AA._local_epoch(s), s)


class CatalogTest(unittest.TestCase):
    def test_words_splits_camel_case(self):
        self.assertEqual(CAT.words("proxLatchedIdlePilot"), "prox latched idle pilot")
        self.assertEqual(CAT.words("door_OpenWhileDriving"), "door open while driving")

    def test_known_alert_uses_the_catalog(self):
        name = next(iter(CAT.C))
        c, s, t, w, a = CAT.C[name]
        x = CAT.explain(name, {"Description": "ignored"})
        self.assertEqual((x["category"], x["severity"], x["title"], x["what"], x["advice"], x["known"]), (c, s, t, w, a, True))

    def test_every_catalog_severity_has_an_order(self):
        for name, (_c, s, *_rest) in CAT.C.items():
            self.assertIn(s, CAT.SEVERITY_ORDER, name)

    def test_unknown_warning_decoded_from_its_name(self):
        x = CAT.explain("DI_w999_madeUpThing")
        self.assertEqual((x["category"], x["severity"], x["title"], x["known"]), ("Driving controls", "notice", "Made up thing", False))
        self.assertEqual(x["what"], 'The drive unit reported "made up thing".')
        self.assertEqual(x["advice"], "")

    def test_unknown_alert_kind_a_needs_attention_and_uses_tessie_text(self):
        x = CAT.explain("CP_a999_madeUpFault", {"Description": " Tessie says this. ", "Impact": " Charging may stop. "})
        self.assertEqual((x["category"], x["severity"]), ("Charging", "attention"))
        self.assertEqual((x["what"], x["advice"]), ("Tessie says this.", "Charging may stop."))

    def test_unknown_prefix_is_other(self):
        x = CAT.explain("ZZZ_a001_somethingOdd")
        self.assertEqual((x["category"], x["title"]), ("Other", "Something odd"))

    def test_name_without_the_pattern_keeps_the_name(self):
        x = CAT.explain("plainname")
        self.assertEqual((x["category"], x["severity"], x["title"]), ("Other", "attention", "Plainname"))


API_HDR = ["Timestamp (UTC)", "Name", "Description", "Customer Facing Message 1"]
EXP_HDR = ["Timestamp (AEDT)", "Name", "Description", "Customer Facing Message 1", "Incidence Rate"]
T0 = utc(2026, 1, 15, 1, 0)                         # 12:00 AEDT


class AlertsLoadBuildTest(SydneyClock):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = self.tmp.name

    def _files(self, api, export):
        write_csv(AA.deps(self.d)[0], API_HDR, api)
        write_csv(AA.deps(self.d)[1], EXP_HDR, export)

    def test_no_files_is_empty(self):
        self.assertEqual(AA.load(self.d), [])
        b = AA.build(self.d, [], [], [])
        self.assertEqual((b["episodes"], b["types"], b["first"], b["last"], b["events"]), ([], [], None, None, 0))

    def test_export_row_within_90s_of_api_row_is_merged_not_doubled(self):
        self._files(
            [{"Timestamp (UTC)": str(T0), "Name": "X_a001_one", "Description": "", "Customer Facing Message 1": ""}],
            [{"Timestamp (AEDT)": "2026-01-15 12:01:00", "Name": "X_a001_one", "Description": "Richer text",
              "Customer Facing Message 1": "Shown on screen", "Incidence Rate": "1%"}])
        ev = AA.load(self.d)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["t"], T0)                                     # the API's time is kept
        self.assertEqual(ev[0]["row"]["Description"], "Richer text")         # the export's text is borrowed
        self.assertEqual(ev[0]["row"]["Customer Facing Message 1"], "Shown on screen")

    def test_api_text_wins_over_export_text(self):
        self._files(
            [{"Timestamp (UTC)": str(T0), "Name": "X_a001_one", "Description": "API text", "Customer Facing Message 1": ""}],
            [{"Timestamp (AEDT)": "2026-01-15 12:00:30", "Name": "X_a001_one", "Description": "Export text",
              "Customer Facing Message 1": "", "Incidence Rate": ""}])
        self.assertEqual(AA.load(self.d)[0]["row"]["Description"], "API text")

    def test_export_row_far_from_api_row_or_other_name_is_kept(self):
        self._files(
            [{"Timestamp (UTC)": str(T0), "Name": "X_a001_one", "Description": "", "Customer Facing Message 1": ""},
             {"Timestamp (UTC)": "not a number", "Name": "X_a001_one", "Description": "", "Customer Facing Message 1": ""}],
            [{"Timestamp (AEDT)": "2026-01-15 12:05:00", "Name": "X_a001_one", "Description": "", "Customer Facing Message 1": "", "Incidence Rate": ""},
             {"Timestamp (AEDT)": "2026-01-15 12:00:00", "Name": "X_a002_two", "Description": "", "Customer Facing Message 1": "", "Incidence Rate": ""},
             {"Timestamp (AEDT)": "garbage", "Name": "X_a003_three", "Description": "", "Customer Facing Message 1": "", "Incidence Rate": ""}])
        ev = AA.load(self.d)
        self.assertEqual([(e["name"], e["t"], e["src"]) for e in ev],
                         [("X_a001_one", T0, "api"), ("X_a002_two", T0, "export"), ("X_a001_one", T0 + 300, "export")])

    def test_repeats_within_ten_minutes_are_one_episode(self):
        rows = [{"Timestamp (UTC)": str(T0 + dt), "Name": "X_w001_rep", "Description": "", "Customer Facing Message 1": ""}
                for dt in (0, 300, 900, 900 + AA.GAP_S + 1)]
        self._files(rows, [])
        b = AA.build(self.d, [], [], [])
        eps = sorted(b["episodes"], key=lambda e: e["t0"])
        # 0 and 300 and 900 chain (each within 600 s of the last), the fourth is 601 s after the third
        self.assertEqual([(e["t0"] - T0, e["t1"] - T0, e["n"]) for e in eps], [(0, 900, 3), (1501, 1501, 1)])
        self.assertEqual(b["events"], 4)
        self.assertEqual((b["first"], b["last"]), (T0, T0 + 1501))
        self.assertEqual([e["t0"] for e in b["episodes"]], [T0 + 1501, T0])          # newest first
        self.assertEqual(b["types"][0]["episodes"], 2)
        self.assertEqual(b["types"][0]["events"], 4)

    def test_day_and_time_shown_on_the_local_clock(self):
        self._files([{"Timestamp (UTC)": str(utc(2026, 1, 15, 14, 30)), "Name": "X_a001_one", "Description": "", "Customer Facing Message 1": ""}], [])
        e = AA.build(self.d, [], [], [])["episodes"][0]
        self.assertEqual((e["day"], e["hm"]), ("2026-01-16", "01:30"))                 # 14:30 UTC is 01:30 next day AEDT
        self.assertEqual(e["id"], "X_a001_one:%d" % utc(2026, 1, 15, 14, 30))

    def test_context_prefers_drive_then_charge_then_park(self):
        self._files([{"Timestamp (UTC)": str(T0), "Name": "X_a001_one", "Description": "", "Customer Facing Message 1": " Hello "}], [])
        drive = dict(id="d1", t0=T0 + 30, t1=T0 + 600, frm="Place A", to="Place B")       # starts 30 s after: within the 60 s slack
        charge = dict(id="c1", t0=T0 - 3600, t1=T0 + 3600, place="Charger C")
        idle = dict(id="i1", t0=T0 - 7200, t1=T0 + 7200, place="Park P")
        e = AA.build(self.d, [drive], [charge], [idle])["episodes"][0]
        self.assertEqual(e["ctx"], dict(k="drive", id="d1", label="Place A → Place B"))
        self.assertEqual(e["screen"], "Hello")
        e = AA.build(self.d, [], [charge], [idle])["episodes"][0]
        self.assertEqual(e["ctx"], dict(k="charge", id="c1", label="Charger C"))
        e = AA.build(self.d, [], [], [idle])["episodes"][0]
        self.assertEqual(e["ctx"]["k"], "idle")
        late = dict(id="d2", t0=T0 + 61, t1=T0 + 600, frm="A", to="B")                    # 61 s after: outside
        self.assertIsNone(AA.build(self.d, [late], [], [])["episodes"][0]["ctx"])

    def test_types_sorted_by_severity_then_count(self):
        known_safety = next((n for n, v in CAT.C.items() if v[1] == "safety"), None)
        if known_safety is None:
            self.skipTest("no safety alert in the catalog")
        rows = [{"Timestamp (UTC)": str(T0 + k * 3600), "Name": "X_w001_minor", "Description": "", "Customer Facing Message 1": ""} for k in range(3)]
        rows.append({"Timestamp (UTC)": str(T0), "Name": known_safety, "Description": "", "Customer Facing Message 1": ""})
        self._files(rows, [])
        types = AA.build(self.d, [], [], [])["types"]
        self.assertEqual([t["name"] for t in types], [known_safety, "X_w001_minor"])


class SnapshotRulesTest(unittest.TestCase):
    KINDS = {"intervention", "accelerator", "speed", "saved"}
    ZONES = [40, 50, 100, 110]

    def test_burst_for_listed_kinds_only(self):
        self.assertTrue(SN._burst({"kind": "intervention"}, self.KINDS, self.ZONES, 3))
        self.assertTrue(SN._burst({"kind": "saved"}, self.KINDS, self.ZONES, 3))
        self.assertFalse(SN._burst({"kind": "honk"}, self.KINDS, self.ZONES, 3))

    def test_speed_change_bursts_only_near_a_zone_speed(self):
        b = lambda v, v1: SN._burst({"kind": "speed", "v": v, "v1": v1}, self.KINDS, self.ZONES, 3)
        self.assertTrue(b(60, 50))                 # into a 50 zone
        self.assertTrue(b(41, 70))                 # out of a 40 zone, within the tolerance
        self.assertTrue(b(97, 80))                 # 97 is within 3 of 100
        self.assertFalse(b(70, 80))
        self.assertFalse(b(60, 54))                # 54 is 4 from 50
        self.assertTrue(b(None, 110))              # a missing speed is skipped, not an error
        self.assertFalse(b(None, None))

    def test_speed_change_bursts_when_no_zones_are_set(self):
        self.assertTrue(SN._burst({"kind": "speed", "v": 70, "v1": 80}, self.KINDS, [], 3))

    def test_event_text(self):
        self.assertEqual(SN._text({"kind": "intervention", "to_mode": "Manual", "flags": ["brake", "wheel"]}), "FSD off -> Manual (brake/wheel)")
        self.assertEqual(SN._text({"kind": "intervention", "to_mode": "Manual"}), "FSD off -> Manual")
        self.assertEqual(SN._text({"kind": "accelerator", "v": 61.6}), "Accelerator pressed at 62 km/h")
        self.assertEqual(SN._text({"kind": "speed", "v": 100.2, "v1": 79.6}), "Speed 100 -> 80 km/h")
        self.assertEqual(SN._text({"kind": "speed"}), "Speed 0 -> 0 km/h")


class SnapshotLocalTimeTest(unittest.TestCase):
    """A still carries the local time of the place where it was taken (a drive can cross a time zone)."""

    def test_unknown_place_is_sydney(self):
        dt, z = SN._local(utc(2026, 1, 15, 1, 0), None, None)
        self.assertEqual((dt, z), (datetime(2026, 1, 15, 12, 0), "AEDT"))
        dt, z = SN._local(utc(2026, 7, 15, 1, 0), None, None)
        self.assertEqual((dt, z), (datetime(2026, 7, 15, 11, 0), "AEST"))

    def test_place_zone_is_used(self):
        with mock.patch("timezones.resolve_timezone", return_value="Australia/Perth"):
            dt, z = SN._local(utc(2026, 1, 15, 1, 0), -31.0, 117.0)
        self.assertEqual((dt, z), (datetime(2026, 1, 15, 9, 0), "AWST"))
        with mock.patch("timezones.resolve_timezone", return_value="Australia/Brisbane"):
            dt, z = SN._local(utc(2026, 1, 15, 1, 0), -26.0, 151.0)
        self.assertEqual((dt, z), (datetime(2026, 1, 15, 11, 0), "AEST"))          # no daylight saving in Queensland

    def test_zone_lookup_failure_falls_back_to_sydney(self):
        with mock.patch("timezones.resolve_timezone", side_effect=RuntimeError("boom")):
            self.assertEqual(SN._local(utc(2026, 1, 15, 1, 0), -31.0, 117.0)[1], "AEDT")

    def test_index_written_whole(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "snapshots")
            items = [dict(id="e1", kind="speed", text="Speed 60 -> 50 km/h", utc=1768438800.7), dict(kind="saved", text="Saved", utc=1768438900)]
            SN._write_index(out, "2026-01-15_12-00-00", 30, items, [{"file": "timeline/a.jpg"}])
            self.assertEqual(sorted(os.listdir(out)), ["index.json"])                 # no .tmp left behind
            with open(os.path.join(out, "index.json")) as fh:
                ix = json.load(fh)
        self.assertEqual((ix["stamp"], ix["every_s"], ix["snapshots"]), ("2026-01-15_12-00-00", 30, [{"file": "timeline/a.jpg"}]))
        self.assertEqual(ix["events"], [dict(id="e1", kind="speed", text="Speed 60 -> 50 km/h", utc=1768438800),
                                        dict(id="", kind="saved", text="Saved", utc=1768438900)])
        self.assertRegex(ix["made"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_no_archive_means_no_front_clips(self):
        self.assertEqual(SN.front_clips(object(), None), {})
        self.assertEqual(SN.front_clips(object(), ""), {})


if __name__ == "__main__":
    unittest.main()
