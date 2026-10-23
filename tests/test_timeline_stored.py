"""test_timeline_stored.py - the Timeline built from what is kept on this computer needs no drive.

    python3 -m unittest tests/test_timeline_stored.py
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
import teslacam_timeline as TL  # noqa: E402

LIVE = dict(first="2026-10-17T00:00:00Z", last="2026-10-17T00:00:00Z", gone=None)
GONE = dict(first="2026-10-17T00:00:00Z", last="2026-10-17T00:00:00Z", gone="2026-10-18T00:00:00Z")
CAMS = ["front", "back"]
MINUTES = ["2026-10-16_15-%02d-07" % m for m in (40, 41, 42, 43)]


def dump(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f)


def load(path):
    with open(path) as f:
        return json.load(f)


def series(speed, lat0, n=60):
    z = [0] * n
    return dict(n=n, dur=n, v=[speed] * n, lat=[lat0 + i * 1e-5 for i in range(n)], lon=[151.0 + i * 1e-5 for i in range(n)], hd=z, ap=z, steer=z, accel=z, br=z, blk=z, gear=[1 if speed else 0] * n)   # in Drive when moving, Park when not


class Stored(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        led = os.path.join(self.tmp, "teslacam_ledger")
        os.makedirs(led)
        day = {}
        for st in MINUTES:
            for c in CAMS:
                day["recent||%s-%s" % (st, c)] = dict(drives=dict(A=LIVE), size=1)
        day["recent||2026-10-16_14-00-07-front"] = dict(drives=dict(A=GONE), size=1)      # deleted from the drive: not on the timeline any more
        dump(day, os.path.join(led, "2026-10-16.json"))
        dump(dict(A=dict(at="x", files=1, bytes=1, ok=True), U=dict(at="x", files=0, bytes=0, ok=True)), os.path.join(led, "scans.json"))
        ev = {"2026-10-16_16-00-00": dict(id="2026-10-16_16-00-00", ts="2026-10-16T16:00:00", city="Ryde", street="", lat=-33.8, lon=151.1, reason="sentry_aware_object_detection",
                                         cam="5", clips={"2026-10-16_15-58-07": CAMS, "2026-10-16_15-59-07": CAMS}, thumb=False)}
        dump(ev, os.path.join(self.tmp, "teslacam_index_A_sentry.json"))
        store = TL.Store(os.path.join(self.tmp, "teslacam_timeline"))
        for i, st in enumerate(MINUTES):
            store.put(st, series(50.0, -33.8 + i * 0.001))
        store.flush()
        self.tc = AT.TeslaCam(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_every_drive_away(self):
        tl = self.tc.timeline_stored()
        kinds = sorted(it["kind"] for it in tl["items"])
        self.assertIn("drive", kinds)
        self.assertIn("sentry", kinds)
        self.assertEqual((tl["minutes"], tl["read"]), (4, 4))                        # the gone clip is not counted
        drive = next(it for it in tl["items"] if it["kind"] == "drive")
        self.assertEqual(drive["n"], 4)
        self.assertGreater(drive["km"], 2.0)                                         # 4 minutes at 50 km/h from the kept telemetry

    def test_a_recording_says_which_drives_hold_its_video(self):
        led = os.path.join(self.tmp, "teslacam_ledger")
        dump(dict(A=dict(at="x", files=1, bytes=1, ok=True), J=dict(at="x", files=1, bytes=1, ok=True)), os.path.join(led, "scans.json"))
        here, away = "2026-10-16_17-10-07", "2026-10-16_18-10-07"
        dump({"e1": dict(id="e1", ts="2026-10-16T17:10:30", reason="user_interaction_honk", cam="0", clips={here: CAMS}, thumb=False),
              "e2": dict(id="e2", ts="2026-10-16T18:10:30", reason="user_interaction_honk", cam="0", clips={away: CAMS}, thumb=False, gone="2026-10-18T00:00:00Z")},
             os.path.join(self.tmp, "teslacam_index_J_saved.json"))
        rec = {it["id"]: it for it in self.tc.timeline_stored()["items"] if it["kind"] == "recording"}
        self.assertEqual(rec[here]["held"], ["J"])                                     # on JOWUA: readable when JOWUA is connected
        self.assertEqual(rec[away]["held"], [])                                        # gone from every drive: nothing can read it

    def test_limited_to_one_drive(self):
        self.assertEqual(self.tc.timeline_stored(["U"])["items"], [])                # the USB holds nothing
        self.assertEqual(self.tc.timeline_stored(["A"])["roles"], ["A"])
        self.assertEqual(self.tc.timeline_stored(["Z"])["roles"], [])                # a drive never seen is ignored

    def test_same_event_on_two_drives_is_one_with_both_minutes(self):
        ev = load(os.path.join(self.tmp, "teslacam_index_A_sentry.json"))
        e = dict(ev["2026-10-16_16-00-00"], clips={"2026-10-16_15-59-07": ["front", "back", "left_pillar"], "2026-10-16_16-00-07": CAMS})
        dump({e["id"]: e}, os.path.join(self.tmp, "teslacam_index_U_sentry.json"))
        tl = AT.TeslaCam(self.tmp).timeline_stored()
        sen = [it for it in tl["items"] if it["kind"] == "sentry"]
        self.assertEqual(len(sen), 1)
        self.assertEqual(sen[0]["n"], 3)                                             # 15-58, 15-59 and 16-00

    def test_old_path_still_answers_not_connected(self):
        self.assertEqual(self.tc.events("A", "sentry", None), ([], False))



class SentryWindow(unittest.TestCase):
    """A Sentry event is footage with telemetry too: asked for with `sentry=<folder>`, its minutes are in the window, and found under SentryClips."""

    def test_the_minutes_of_a_sentry_event_make_a_window_with_telemetry(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        root = os.path.join(tmp, "TeslaCam")
        ev = "2026-10-16_16-00-00"
        stamps = ["2026-10-16_15-58-07", "2026-10-16_15-59-07"]
        for st in stamps:
            d = os.path.join(root, "SentryClips", ev)
            os.makedirs(d, exist_ok=True)
            for c in ("front", "back"):
                open(os.path.join(d, "%s-%s.mp4" % (st, c)), "wb").close()
        store = TL.Store(os.path.join(tmp, "teslacam_timeline"))
        for i, st in enumerate(stamps):
            store.put(st, series(0.0, -33.8 + i * 1e-4))
        store.flush()
        tc = AT.TeslaCam(tmp)
        t0 = TL.secs(stamps[0])
        d = tc.window("A", root, t0 - 30, t0 + 150, 0, None, ev)
        self.assertIsNotNone(d)
        self.assertEqual([(m["stamp"], m["src"], m["ref"]) for m in d["minutes"]], [(st, "sentry", ev) for st in stamps])
        self.assertTrue(any(x is not None for x in d["series"]["lat"]))
        self.assertTrue(AT.TeslaCam._front(root, d["minutes"][0] | dict(tcam="front")).endswith(os.path.join("SentryClips", ev, "%s-front.mp4" % stamps[0])))
        self.assertIsNone(tc.window("A", root, t0 - 30, t0 + 150, 0, None, None))              # without it there is nothing recorded in that window



if __name__ == "__main__":
    unittest.main()
