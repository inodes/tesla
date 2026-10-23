"""test_ingest.py - a minute's metadata is read once, kept locally, and the drive is not asked again.

    python3 -m unittest tests/test_ingest.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import teslacam_ingest as IN  # noqa: E402
import clip_clock as CC  # noqa: E402
import teslacam_ledger as L  # noqa: E402
import teslacam_locate as LOC  # noqa: E402
import teslacam_timeline as TL  # noqa: E402

LIVE = dict(first="2026-10-17T00:00:00Z", last="2026-10-17T00:00:00Z", gone=None)
GONE = dict(first="2026-10-17T00:00:00Z", last="2026-10-17T00:00:00Z", gone="2026-10-18T00:00:00Z")
S1, S2, S3 = "2026-10-16_15-44-07", "2026-10-16_15-45-07", "2026-10-16_15-46-07"


def clip(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30", "-t", "3", "-pix_fmt", "yuv420p", "-g", "15", path],
                   check=True, stdin=subprocess.DEVNULL)


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class Ingest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        self.root = os.path.join(self.tmp, "drive", "TeslaCam")
        for s in (S1, S2, S3):
            clip(os.path.join(self.root, "RecentClips", s + "-front.mp4"))
        os.makedirs(os.path.join(self.data, "teslacam_ledger"))
        day = {"recent||%s-front" % S1: dict(drives=dict(A=LIVE), size=1),
               "recent||%s-front" % S2: dict(drives=dict(A=LIVE, J=LIVE), size=1),
               "recent||%s-front" % S3: dict(drives=dict(A=LIVE), size=1),
               "recent||%s-back" % S1: dict(drives=dict(A=LIVE), size=1),                       # not a front clip
               "sentry|2026-10-16_16-00-00|2026-10-16_15-59-07-front": dict(drives=dict(A=LIVE), size=1),   # Sentry is not read
               "recent||2026-10-16_15-30-00-front": dict(drives=dict(A=GONE), size=1),            # no drive holds it any more
               "saved|2026-10-16_15-44-30|%s-front" % S1: dict(drives=dict(J=LIVE), size=1)}     # the same minute again, as a Saved clip
        with open(os.path.join(self.data, "teslacam_ledger", "2026-10-16.json"), "w") as f:
            json.dump(day, f)
        self.led = L.Ledger(self.data)
        self.store = TL.Store(os.path.join(self.data, "teslacam_timeline"))
        self.th = IN.Thumbs(self.data)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_day_minutes_from_the_log(self):
        m = LOC.day_minutes(self.led, "2026-10-16")
        self.assertEqual([x["stamp"] for x in m], [S1, S2, S3])
        self.assertEqual(m[0]["src"], "recent")                                  # Recent wins over the Saved copy of the same minute
        self.assertEqual(m[1]["roles"], ["A", "J"])                              # the order is Archive first
        self.assertEqual(LOC.day_minutes(self.led, "2026-10-17"), [])

    def test_front_path(self):
        self.assertEqual(LOC.front_path("/v/TeslaCam", "recent||a-front"), "/v/TeslaCam/RecentClips/a-front.mp4")
        self.assertEqual(LOC.front_path("/v/TeslaCam", "saved|ev|a-front"), "/v/TeslaCam/SavedClips/ev/a-front.mp4")

    def test_thumbs_keep_what_they_hold(self):
        self.th.put_many({S1: b"\xff\xd8one"})
        self.th.put_many({S2: b"\xff\xd8two", S1 + "x": b""})                    # an empty one is not kept
        self.assertEqual(self.th.get(S1), b"\xff\xd8one")
        self.assertEqual(self.th.get(S2), b"\xff\xd8two")
        self.assertTrue(self.th.has(S1) and not self.th.has(S3))
        self.assertEqual(self.th.count("2026-10-16"), 2)
        self.assertEqual([n for n in os.listdir(self.th.base) if n.endswith(".part")], [])

    def test_make_thumb_is_a_small_jpeg(self):
        b = IN.make_thumb(os.path.join(self.root, "RecentClips", S1 + "-front.mp4"))
        self.assertTrue(b[:2] == b"\xff\xd8" and 500 < len(b) < 60000, len(b or b""))

    def test_pull_reads_once_then_nothing(self):
        m = LOC.day_minutes(self.led, "2026-10-16")
        st = IN.day_status(self.store, self.th, m, LOC.Locator(self.led, {"A": self.root}).minute)
        self.assertEqual((st["minutes"], st["telemetry"], st["thumbs"], len(st["pullable"])), (3, 0, 0, 3))
        seen = []
        r = IN.pull(self.store, self.th, m, LOC.Locator(self.led, {"A": self.root}).minute, progress=lambda n, t, s: seen.append((n, t)), workers=2)
        self.assertEqual((r["read"], r["thumbs"], r["failed"], r["skipped"], r["stalled"]), (3, 3, 0, 0, ""))
        self.assertEqual(seen[-1], (3, 3))
        self.assertTrue(all(self.store.summary(s) is not None for s in (S1, S2, S3)))     # 0 = read, nothing in it (a test pattern has no telemetry)
        self.assertTrue(all(self.th.has(s) for s in (S1, S2, S3)))
        st = IN.day_status(self.store, self.th, m, LOC.Locator(self.led, {}).minute)
        self.assertEqual((st["telemetry"], st["thumbs"], st["missing"], st["away"]), (3, 3, [], []))   # complete: the drive is not needed any more
        again = IN.pull(self.store, self.th, m, LOC.Locator(self.led, {"A": self.root}).minute, workers=2)
        self.assertEqual((again["read"], again["thumbs"]), (0, 0))
        reopened = TL.Store(os.path.join(self.data, "teslacam_timeline"))                  # it is on disk, not just in memory
        self.assertIsNotNone(reopened.summary(S2))

    def test_drive_away_leaves_it_for_later(self):
        m = LOC.day_minutes(self.led, "2026-10-16")
        away = LOC.Locator(self.led, {}).minute
        st = IN.day_status(self.store, self.th, m, away)
        self.assertEqual((len(st["missing"]), st["pullable"], st["away"]), (3, [], ["A", "J"]))
        r = IN.pull(self.store, self.th, m, away, workers=1)
        self.assertEqual((r["read"], r["thumbs"], r["skipped"]), (0, 0, 3))
        self.assertIsNone(self.store.summary(S1))                                    # nothing is recorded for a minute that was not read

    def test_another_drive_supplies_what_the_first_lacks(self):
        m = LOC.day_minutes(self.led, "2026-10-16")
        r = IN.pull(self.store, self.th, [x for x in m if x["stamp"] == S2], LOC.Locator(self.led, {"J": self.root}).minute, workers=1)   # S2 is on A and J; only J is mounted
        self.assertEqual((r["read"], r["thumbs"]), (1, 1))

    def test_a_clip_that_cannot_be_read_is_not_recorded_as_empty(self):
        m = LOC.day_minutes(self.led, "2026-10-16")
        os.remove(os.path.join(self.root, "RecentClips", S3 + "-front.mp4"))
        r = IN.pull(self.store, self.th, m, LOC.Locator(self.led, {"A": self.root}).minute, workers=2)
        self.assertEqual(r["read"] + r["failed"], 3)
        self.assertIsNone(self.store.summary(S3))                                    # tried again next time
        self.assertEqual(r["failed"], 1)

    def test_stop_leaves_the_rest(self):
        m = LOC.day_minutes(self.led, "2026-10-16")
        r = IN.pull(self.store, self.th, m, LOC.Locator(self.led, {"A": self.root}).minute, stop=lambda: True, workers=1)
        self.assertEqual((r["read"], r["thumbs"]), (0, 0))


class Events(unittest.TestCase):
    """The picture of what triggered an event: the camera that saw it, at the second it was detected."""

    def setUp(self):
        self.ctmp = tempfile.mkdtemp()
        CC.configure(self.ctmp)

    def tearDown(self):
        CC._set_runs([], bump=False)
        shutil.rmtree(self.ctmp, ignore_errors=True)

    def test_trigger_point_and_camera(self):
        ev = dict(id="2026-10-16_16-00-00", ts="2026-10-16T15-59-30".replace("T", "T"), cam="5",
                  clips={"2026-10-16_15-57-07": ["front", "left_repeater"], "2026-10-16_15-58-07": ["front", "left_repeater"], "2026-10-16_15-59-07": ["front", "left_repeater"],
                         "2026-10-16_16-00-07": ["front", "left_repeater"]})
        ev["ts"] = "2026-10-16T15:59:37"
        self.assertEqual(IN.trigger_point(ev), ("2026-10-16_15-59-07", 30))              # the minute that holds it, 30 s in
        self.assertEqual(IN.trigger_camera(ev, "2026-10-16_15-59-07"), "left_repeater")  # camera 5 = left repeater
        ev["cam"] = "7"
        self.assertEqual(IN.trigger_camera(ev, "2026-10-16_15-59-07"), "front")          # the back camera has no clip in that minute: the front one
        self.assertEqual(IN.trigger_point(dict(id="x", ts="2026-10-16T10:00:00", clips={})), (None, 2))
        self.assertEqual(IN.trigger_point(dict(ev, ts="2026-10-16T15:50:00"))[0], "2026-10-16_15-57-07")   # before every clip: the first
        self.assertEqual(IN.trigger_point(dict(ev, ts="2026-10-16T16:30:00"))[1], 58)                       # far after the last minute starts: capped

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_pull_events_keeps_one_picture_and_does_not_need_the_drive_again(self):
        tmp = tempfile.mkdtemp()
        try:
            root = os.path.join(tmp, "drive", "TeslaCam")
            eid, k0 = "2026-10-16_16-00-00", "2026-10-16_15-59-07"
            clip(os.path.join(root, "SentryClips", eid, k0 + "-left_repeater.mp4"))
            os.makedirs(os.path.join(tmp, "data", "teslacam_ledger"))
            with open(os.path.join(tmp, "data", "teslacam_ledger", "2026-10-16.json"), "w") as f:
                json.dump({"sentry|%s|%s-left_repeater" % (eid, k0): dict(drives=dict(J=LIVE), size=1)}, f)
            led = L.Ledger(os.path.join(tmp, "data"))
            ev = dict(id=eid, ts="2026-10-16T15:59:08", cam="5", clips={k0: ["front", "left_repeater"]})
            img = IN.EventImages(os.path.join(tmp, "data"))
            away = LOC.Locator(led, {}).event
            self.assertEqual(IN.pull_events(img, "sentry", [ev], away)["skipped"], 1)  # drive away: left for later, nothing made
            self.assertFalse(img.has("sentry", eid))
            here = LOC.Locator(led, {"J": root}).event
            r = IN.pull_events(img, "sentry", [ev], here)
            self.assertEqual((r["made"], r["failed"], r["skipped"]), (1, 0, 0))
            b = img.get("sentry", eid)
            self.assertTrue(b[:2] == b"\xff\xd8" and len(b) > 1000)
            self.assertEqual(IN.pull_events(img, "sentry", [ev], away), dict(made=0, skipped=0, failed=0, stalled=""))   # already kept: the drive is not asked
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_a_clip_cut_short_gives_its_last_frame(self):
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "short.mp4")
            clip(p)                                                                       # 3 s long
            b = IN.make_event_image(p, 20)                                                # the trigger was asked 20 s in
            self.assertTrue(b and b[:2] == b"\xff\xd8")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_event_images_keep_what_they_hold(self):
        tmp = tempfile.mkdtemp()
        try:
            im = IN.EventImages(tmp)
            im.put_many("sentry", {"a": b"\xff\xd8A"})
            im.put_many("sentry", {"b": b"\xff\xd8B", "c": b""})
            self.assertEqual((im.get("sentry", "a"), im.get("sentry", "b")), (b"\xff\xd8A", b"\xff\xd8B"))
            self.assertFalse(im.has("sentry", "c") or im.has("saved", "a"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class EventClock(unittest.TestCase):
    """Event times and clip names are the CAR's clock; every comparison is on UTC, whatever the car's clock was doing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        CC.configure(self.tmp)

    def tearDown(self):
        CC._set_runs([], bump=False)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def runs(self, *rs):
        CC._set_runs([[CC.naive(a), CC.naive(b), off] for a, b, off in rs], bump=False)

    def test_the_car_clock_jumps_back_inside_one_event(self):
        # NSW (daylight, +11 h) into Queensland (+10 h): the car's names go back an hour while real time moves on
        pre, post = ["2026-10-11_11-30-07", "2026-10-11_11-31-07"], ["2026-10-11_11-00-07", "2026-10-11_11-01-07"]
        self.runs((pre[0], pre[1], 39600.0), (post[0], post[1], 36000.0))
        clips = {k: ["front", "left_repeater"] for k in pre + post}
        self.assertLess(CC.utc(pre[1]), CC.utc(post[0]))                               # in real time the 11:31 minute came first
        ev = dict(id="e", cam="5", clips=clips)
        self.assertEqual(IN.trigger_point(dict(ev, ts="2026-10-11T11:31:30")), (pre[1], 23))      # detected before the border
        self.assertEqual(IN.trigger_point(dict(ev, ts="2026-10-11T11:01:30")), (post[1], 23))     # detected after it, though the name reads earlier
        self.assertEqual(IN.trigger_point(dict(ev, ts="2026-10-11T11:00:20")), (post[0], 13))

    def test_the_cars_reading_is_used_not_the_display_time(self):
        k = "2026-10-11_11-31-07"
        self.runs((k, k, 36000.0))
        ev = dict(id="e", cam="0", clips={k: ["front"]}, ts="2026-10-11T12:31:30", cts="2026-10-11T11:31:30")   # a shown event: ts = local display, cts = the car's
        self.assertEqual(IN.trigger_point(ev), (k, 23))

    def test_a_car_clock_that_stayed_on_standard_time(self):
        a, b = "2026-10-07_09-00-07", "2026-10-07_09-01-07"                          # after the clocks changed the car kept standard time (+10 h)
        self.runs((a, b, 36000.0))
        self.assertEqual(IN.trigger_point(dict(id="e", cam="0", clips={a: ["front"], b: ["front"]}, ts="2026-10-07T09:00:45")), (a, 38))
        self.assertEqual(CC.local_of(a), "2026-10-07T10:00:07")                       # shown on the local clock (+11 h), an hour later than its name

    def test_nonsense_time_does_not_stop_the_ingest(self):
        self.assertEqual(IN.trigger_point(dict(id="e", ts="not a time", clips={"2026-10-11_11-31-07": ["front"]})), ("2026-10-11_11-31-07", 2))


if __name__ == "__main__":
    unittest.main()
