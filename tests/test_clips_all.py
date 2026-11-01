"""test_clips_all.py - the Clips list is the same whatever drives are connected: every logged run and event, each saying which drive holds it and if it can be played.

    python3 -m unittest tests/test_clips_all.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_teslacam as AT  # noqa: E402
import clips_purged as PG  # noqa: E402
import home_ignore as IGN  # noqa: E402
import run_cuts as RC  # noqa: E402

LIVE = dict(first="2026-10-17T00:00:00Z", last="2026-10-17T00:00:00Z", gone=None)
GONE = dict(first="2026-10-17T00:00:00Z", last="2026-10-17T00:00:00Z", gone="2026-10-18T00:00:00Z")
CAMS = ["front", "back"]


def dump(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f)


class ClipsAll(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        led = os.path.join(self.tmp, "teslacam_ledger")
        os.makedirs(led)
        day = {}
        for st in ("2026-10-16_15-40-07", "2026-10-16_15-41-07"):                 # one run, on JOWUA
            for c in CAMS:
                day["recent||%s-%s" % (st, c)] = dict(drives=dict(J=LIVE), size=1)
        for c in CAMS:                                                           # a later run, deleted from its drive
            day["recent||2026-10-16_18-00-07-%s" % c] = dict(drives=dict(A=GONE), size=1)
        dump(day, os.path.join(led, "2026-10-16.json"))
        dump(dict(A=dict(at="x", files=1, bytes=1, ok=True), J=dict(at="x", files=1, bytes=1, ok=True)), os.path.join(led, "scans.json"))
        ev = dict(id="2026-10-16_16-00-00", ts="2026-10-16T16:00:00", city="Ryde", street="", lat=-33.8, lon=151.1, reason="sentry_aware_object_detection", cam="5",
                  clips={"2026-10-16_15-59-07": CAMS}, thumb=False)
        dump({ev["id"]: ev}, os.path.join(self.tmp, "teslacam_index_A_sentry.json"))
        self.tc = AT.TeslaCam(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_nothing_connected_lists_everything(self):
        d = self.tc.clips_all({})
        self.assertEqual(d["connected"], [])
        self.assertEqual(len(d["recent"]), 2)
        by = {r["id"]: r for r in d["recent"]}
        self.assertEqual(by["2026-10-16_15-40-07"]["roles"], ["J"])
        self.assertIsNone(by["2026-10-16_15-40-07"]["play"])
        self.assertEqual(by["2026-10-16_15-40-07"]["n"], 2)
        self.assertEqual(by["2026-10-16_18-00-07"]["roles"], [])                  # gone from every drive: still listed, only stills are left
        self.assertEqual(d["sentry"][0]["roles"], ["A"])
        self.assertIsNone(d["sentry"][0]["play"])

    def test_connected_drive_makes_it_playable(self):
        d = self.tc.clips_all({"J": self.tmp})
        by = {r["id"]: r for r in d["recent"]}
        self.assertEqual(by["2026-10-16_15-40-07"]["play"], "J")
        self.assertEqual(by["2026-10-16_15-40-07"]["playable"], 2)
        self.assertIsNone(by["2026-10-16_18-00-07"]["play"])
        self.assertEqual(d["connected"], ["J"])

    def test_a_saved_clip_says_whether_the_car_moved_in_it_and_sentry_is_always_parked(self):
        ev = dict(id="2026-10-16_19-37-44", ts="2026-10-16T19:37:43", city="West Ryde", street="Victoria Rd", lat=-33.8, lon=151.1, reason="user_interaction_dashcam_icon_tapped",
                  cam="5", clips={"2026-10-16_19-33-54": CAMS}, thumb=False)
        dump({ev["id"]: ev}, os.path.join(self.tmp, "teslacam_index_A_saved.json"))
        self.tc = AT.TeslaCam(self.tmp)
        store = self.tc._tl("A")
        d = self.tc.clips_all({})
        self.assertFalse(d["saved"][0]["moved"])                                 # no telemetry read: not known to have moved
        moving = dict(n=60, vmax=45.0, km=0.3, mov=40, fsd=0, aut=0, p0=None, p1=None)
        store._load_sum()["2026-10-16_19-33-54"] = moving
        store._load_sum()["2026-10-16_15-59-07"] = moving
        d = self.tc.clips_all({})
        self.assertTrue(d["saved"][0]["moved"])                                  # the drive into Home: a drive, not parked at Home
        self.assertFalse(d["sentry"][0]["moved"])                                # Sentry only records parked

    def test_one_run_for_the_player(self):
        r = self.tc.run_all("2026-10-16_15-40-07", {"J": self.tmp})
        self.assertEqual(sorted(r["clips"]), ["2026-10-16_15-40-07", "2026-10-16_15-41-07"])
        self.assertEqual(set(r["play"].values()), {"J"})
        self.assertEqual(self.tc.run_all("2026-10-16_15-40-07", {})["play"]["2026-10-16_15-40-07"], None)
        self.assertIsNone(self.tc.run_all("1999-01-01_00-00-00", {}))

    def test_minutes_after_an_event_come_from_the_continuous_recording(self):
        r = self.tc.after_all("2026-10-16_15-40-07", 5, {"J": self.tmp})
        self.assertEqual(list(r["clips"]), ["2026-10-16_15-41-07"])               # the next minute only: the later run is a gap away (and on no connected drive)
        self.assertEqual(r["play"], {"2026-10-16_15-41-07": "J"})
        self.assertEqual(self.tc.after_all("2026-10-16_15-40-07", 0, {"J": self.tmp})["clips"], {})
        self.assertEqual(self.tc.after_all("2026-10-16_15-40-07", 5, {})["clips"], {})        # not playable: nothing to add
        self.assertEqual(self.tc.after_all("2026-10-16_15-40-07", 5, {"J": self.tmp}, skip=["2026-10-16_15-41-07"])["clips"], {})



class ParkedAtHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        led = os.path.join(self.tmp, "teslacam_ledger")
        os.makedirs(led)
        day = {}
        for st in ("2026-10-29_10-47-02", "2026-10-29_10-48-02"):
            for c in CAMS:
                day["recent||%s-%s" % (st, c)] = dict(drives=dict(A=GONE), size=1)
        for c in CAMS:
            day["recent||2026-10-29_15-00-02-%s" % c] = dict(drives=dict(A=GONE), size=1)          # parked elsewhere, also gone
        dump(day, os.path.join(led, "2026-10-29.json"))
        dump(dict(A=dict(at="x", files=1, bytes=1, ok=True)), os.path.join(led, "scans.json"))
        self.saved = (dict(RC._S), IGN._PLACES)
        RC._S.clear()
        RC._S["2026-10-29_10-47-02"] = ["park", -33.8, 151.1, False, 0]
        RC._S["2026-10-29_15-00-02"] = ["park", -33.0, 150.0, False, 0]
        IGN.set_places(lambda: [dict(name="Home", lat=-33.8, lon=151.1, radius=100)])
        PG.set_file(os.path.join(self.tmp, "purged.json"))
        self.tc = AT.TeslaCam(self.tmp)

    def tearDown(self):
        RC._S.clear()
        RC._S.update(self.saved[0])
        IGN.set_places(self.saved[1])
        PG.set_file(None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_run_parked_at_home_and_gone_from_every_drive_leaves_the_list_for_good(self):
        ids = [r["id"] for r in self.tc.clips_all({})["recent"]]
        self.assertEqual(ids, ["2026-10-29_15-00-02"])                           # parked elsewhere: still listed as video deleted
        self.assertTrue(PG.has("2026-10-29_10-47-02") and PG.has("2026-10-29_10-48-02"))
        self.assertFalse(PG.has("2026-10-29_15-00-02"))
        self.assertEqual([r["id"] for r in self.tc.clips_all({})["recent"]], ["2026-10-29_15-00-02"])


if __name__ == "__main__":
    unittest.main()
