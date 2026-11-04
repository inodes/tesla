import calendar
import os
import sys
import tempfile
import time
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
import clip_clock as CC  # noqa: E402
import event_stills as EV  # noqa: E402
from lib import teslacam_stills as ST  # noqa: E402

S1, S2, S3 = "2026-10-16_15-40-07", "2026-10-16_15-41-07", "2026-10-16_15-42-07"


def _utc(text):
    t = text.replace("T", "_")
    return calendar.timegm(time.strptime(t[:10] + "_" + t[11:19].replace(":", "-"), "%Y-%m-%d_%H-%M-%S"))


class EventStillsTest(unittest.TestCase):
    def setUp(self):
        self._utc, self._run = CC.utc, ST.run_ffmpeg
        CC.utc = _utc
        self.calls = []

        def run(cmd, timeout=0):
            self.calls.append(cmd)
            last = cmd[-1]
            if "%03d" in last and "-t" in cmd:                       # a run from a clip: one picture a second
                for i in range(int(cmd[cmd.index("-t") + 1])):
                    with open(last % i, "wb") as f:
                        f.write(b"J" * 300)
            elif "%03d" in last:                                     # re-encoding every picture of a zip smaller
                n = 0
                while os.path.isfile(cmd[cmd.index("-i") + 1] % n):
                    with open(last % n, "wb") as f:
                        f.write(b"j" * 40)
                    n += 1
            return 0
        ST.run_ffmpeg = run
        self.d = tempfile.mkdtemp()
        self.st = EV.EventStills(self.d)
        self.ev = dict(id="ev1", ts="2026-10-16T15:41:37", cam="7")

    def tearDown(self):
        CC.utc, ST.run_ffmpeg = self._utc, self._run

    def test_the_camera_is_the_one_that_saw_a_sentry_event_and_the_front_for_a_saved_clip(self):
        self.assertEqual(EV.camera("sentry", dict(cam="7")), "back")
        self.assertEqual(EV.camera("sentry", dict(cam="5")), "left_repeater")
        self.assertEqual(EV.camera("sentry", dict(cam="")), "front")
        self.assertEqual(EV.camera("saved", dict(cam="7")), "front")

    def test_the_window_is_thirty_seconds_either_side_across_the_minutes_that_have_the_camera(self):
        cams = {S1: {"front", "back"}, S2: {"front", "back"}, S3: {"front"}}
        self.assertEqual(EV.window(self.ev, cams, "back"), [(S2, 0, 59)])
        self.assertEqual(EV.window(self.ev, cams, "front"), [(S2, 0, 59), (S3, 0, 0)])
        self.assertEqual(EV.window(dict(ts="2026-10-16T15:40:00"), cams, "front"), [])

    def test_the_pictures_are_cut_once_kept_by_second_and_read_back(self):
        cams = {S2: {"front", "back"}, S3: {"back"}}
        win = EV.window(self.ev, cams, "back")
        n = self.st.make("sentry", self.ev, win, {S2: "/v/%s-back.mp4" % S2, S3: "/v/%s-back.mp4" % S3})
        self.assertEqual(n, 61)
        i = self.st.info("sentry", "ev1")
        self.assertEqual((i["cam"], i["tier"], i["width"], i["n"], i["trigger"]), ("back", "high", 1280, 61, [S2, 30]))
        self.assertEqual(self.st.seconds("sentry", "ev1")[S2][:2], [0, 1])
        self.assertEqual(self.st.seconds("sentry", "ev1")[S3], [0])
        self.assertEqual(len(self.st.get("sentry", "ev1", S2, 30)), 300)
        self.assertIsNone(self.st.get("sentry", "ev1", S2, 99))
        self.assertIn("scale=1280:-2", self.calls[0][self.calls[0].index("-vf") + 1])
        self.assertEqual(self.st.make("sentry", self.ev, win, {S2: "/v/x.mp4"}), 0)             # already made
        self.assertEqual(len(self.calls), 2)

    def test_an_event_is_shrunk_one_step_at_a_time_and_never_back_up(self):
        win = EV.window(self.ev, {S2: {"front"}}, "front")
        self.st.make("saved", self.ev, win, {S2: "/v/a.mp4"})
        old, new = self.st.shrink("saved", "ev1", "normal")
        self.assertLess(new, old)
        self.assertEqual((self.st.info("saved", "ev1")["tier"], self.st.info("saved", "ev1")["width"]), ("normal", 400))
        self.assertIsNone(self.st.shrink("saved", "ev1", "normal"))
        self.assertIsNone(self.st.shrink("saved", "ev1", "high"))
        self.assertIsNotNone(self.st.shrink("saved", "ev1", "shrunk"))
        self.assertEqual(self.st.info("saved", "ev1")["tier"], "shrunk")
        self.assertEqual(len(self.st.seconds("saved", "ev1")[S2]), 60)
        self.assertEqual([(k, e) for k, e, _ in self.st.events()], [("saved", "ev1")])

    def _all(self, cams=EV.CAMS, stamps=(S1, S2, S3)):
        allp = {st: {c: "/v/%s-%s.mp4" % (st, c) for c in cams} for st in stamps}
        paths = EV.Paths({st: allp[st]["front"] for st in stamps})
        paths.all = allp
        return paths

    def test_a_half_second_picture_the_video_did_not_give_does_not_stop_the_event(self):
        """The last half-second frame of a minute can be past the end of the video: ffmpeg makes one picture fewer. That must leave it out, not abort the event (a None in the zip's list)."""
        base = ST.run_ffmpeg

        def run(cmd, timeout=0):
            r = base(cmd, timeout)
            if cmd[cmd.index("-ss") + 1].endswith(".5") and "%03d" in cmd[-1]:       # a half-second run: drop its last picture
                n = int(cmd[cmd.index("-t") + 1])
                os.remove(cmd[-1] % (n - 1))
            return r
        ST.run_ffmpeg = run
        paths = self._all()
        win = EV.window(self.ev, {s: set(EV.CAMS) for s in paths}, "front")
        self.st.make("sentry", dict(self.ev, cam="0"), win, paths)                        # no TypeError from a missing picture
        i = self.st.info("sentry", "ev1")
        self.assertTrue(i.get("more"))
        self.assertTrue(self.st.seconds("sentry", "ev1", half=True)[S2])                  # the half-second pictures that were made are kept

    def test_the_other_cameras_and_the_half_second_pictures_are_cut_once_after_the_main_ones(self):
        paths = self._all()
        win = EV.window(self.ev, {s: set(EV.CAMS) for s in paths}, "front")
        ev = dict(self.ev, cam="0")
        n = self.st.make("sentry", ev, win, paths)
        i = self.st.info("sentry", "ev1")
        self.assertEqual(i["cam"], "front")
        self.assertEqual(sorted(i["cams"]), sorted(EV.CAMS))
        self.assertEqual(i["dense"], EV.DENSE_S)
        for c in EV.CAMS[1:]:
            self.assertEqual(len(self.st.seconds("sentry", "ev1", c)[S2]), 60)
        half = self.st.seconds("sentry", "ev1", half=True)
        self.assertEqual(half[S2], list(range(20, 40)))                                   # 10 s either side of second 30
        self.assertEqual(len(self.st.get("sentry", "ev1", S2, 30, half=True)), 300)
        self.assertIsNotNone(self.st.get("sentry", "ev1", S2, 30, cam="back"))
        self.assertEqual(self.st.halves("sentry", "ev1"), "front")
        side = [c for c in self.calls if "scale=800:-2" in c[c.index("-vf") + 1]]
        self.assertEqual(len(side), 5 * 2)                                                # five cameras, two minutes each (S2, S3)
        self.assertFalse(self.st.wants_more("sentry", "ev1"))
        before = len(self.calls)
        self.assertEqual(self.st.make("sentry", ev, win, paths), 0)
        self.assertEqual(len(self.calls), before)
        self.assertTrue(n > 61)

    def test_an_event_made_with_the_main_camera_only_is_upgraded_later(self):
        win = EV.window(self.ev, {S2: {"front"}}, "front")
        ev = dict(self.ev, cam="0")
        self.st.make("sentry", ev, win, EV.Paths({S2: "/v/a.mp4"}))
        self.assertTrue(self.st.wants_more("sentry", "ev1"))
        paths = self._all(stamps=(S2,))
        self.assertGreater(self.st.make("sentry", ev, win, paths), 0)
        self.assertEqual(len(self.st.cams("sentry", "ev1")), 6)

    def test_reducing_keeps_only_the_main_camera_and_the_event_is_not_upgraded_again(self):
        paths = self._all(stamps=(S2,))
        ev = dict(self.ev, cam="0")
        win = EV.window(ev, {S2: set(EV.CAMS)}, "front")
        self.st.make("sentry", ev, win, paths)
        total = self.st.size("sentry", "ev1")
        freed = self.st.reduce("sentry", "ev1")
        self.assertGreater(freed, 0)
        self.assertEqual(self.st.size("sentry", "ev1"), total - freed)
        self.assertEqual(self.st.cams("sentry", "ev1"), ["front"])
        self.assertEqual(self.st.halves("sentry", "ev1"), "")
        self.assertFalse(self.st.wants_more("sentry", "ev1"))
        self.assertEqual(self.st.make("sentry", ev, win, paths), 0)
        self.assertIsNone(self.st.reduce("sentry", "ev1"))

    def test_shrinking_an_event_drops_the_other_cameras_first(self):
        paths = self._all(stamps=(S2,))
        ev = dict(self.ev, cam="0")
        self.st.make("sentry", ev, EV.window(ev, {S2: set(EV.CAMS)}, "front"), paths)
        old, new = self.st.shrink("sentry", "ev1", "normal")
        self.assertLess(new, old)
        self.assertEqual(self.st.cams("sentry", "ev1"), ["front"])
        self.assertEqual(len(self.st.zips("sentry", "ev1")), 1)

    def test_an_event_missing_the_other_cameras_in_its_video_is_not_asked_again(self):
        paths = self._all(cams=("front",), stamps=(S2,))
        ev = dict(self.ev, cam="0")
        self.st.make("sentry", ev, EV.window(ev, {S2: {"front"}}, "front"), paths)
        self.assertEqual(self.st.cams("sentry", "ev1"), ["front"])
        self.assertFalse(self.st.wants_more("sentry", "ev1"))
        self.assertTrue(self.st.info("sentry", "ev1").get("more"))                          # the half-second pictures of the front camera were made


if __name__ == "__main__":
    unittest.main()
