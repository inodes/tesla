import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
import teslacam_stills as ST  # noqa: E402

S = "2026-09-11_09-45-35"


def clip(root, ev, size, stamp=S):
    d = os.path.join(root, "SentryClips", ev)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "%s-front.mp4" % stamp), "wb") as f:
        f.write(b"x" * size)


class UnreadableTest(unittest.TestCase):
    def setUp(self):
        self._run = ST.run_ffmpeg

        def run(cmd, timeout=0):
            if os.path.getsize(cmd[cmd.index("-i") + 1]) > 1000:                # a whole clip gives a picture a second; a cut-short one gives none
                for i in range(60):
                    with open(cmd[-1] % (i + 1), "wb") as f:
                        f.write(b"jpg")
            return 0
        ST.run_ffmpeg = run
        self.addCleanup(lambda: setattr(ST, "run_ffmpeg", self._run))
        self.d = tempfile.mkdtemp()
        self.root = os.path.join(self.d, "vol", "TeslaCam")
        self.stills = ST.Stills(os.path.join(self.d, "data"))

    def test_a_minute_kept_in_two_events_is_made_from_the_file_that_works(self):
        clip(self.root, "2026-09-11_09-45-36", 500)                              # cut short: the first one met
        clip(self.root, "2026-09-11_09-46-36", 5000)
        rep = ST.plan(self.stills, {"A": self.root})
        self.assertEqual((rep["drives"][0]["to_make"], len(rep["todo"][0]["alts"])), (1, 1))
        self.assertEqual(ST.make_many(self.stills, rep["todo"], workers=1), 1)
        self.assertTrue(self.stills.has(S))
        rep = ST.plan(self.stills, {"A": self.root})
        self.assertEqual((rep["drives"][0]["to_make"], rep["drives"][0]["unreadable"], rep["drives"][0]["done"]), (0, 0, 1))
        self.assertEqual(self.stills.unreadable(), {})

    def test_a_minute_of_an_event_taken_off_the_list_is_not_counted(self):
        clip(self.root, "2026-09-11_09-45-36", 5000)
        clip(self.root, "2026-09-11_09-46-36", 5000, "2026-09-11_09-46-35")
        rep = ST.plan(self.stills, {"A": self.root}, skip_ev=lambda src, ev: ev == "2026-09-11_09-45-36")
        self.assertEqual((rep["drives"][0]["to_make"], [t["stamp"] for t in rep["todo"]]), (1, ["2026-09-11_09-46-35"]))

    def test_a_minute_no_file_of_gives_a_picture_is_counted_as_unreadable_and_not_tried_again(self):
        clip(self.root, "2026-09-11_13-51-21", 500, "2026-09-11_13-51-20")
        rep = ST.plan(self.stills, {"A": self.root})
        self.assertEqual(rep["drives"][0]["to_make"], 1)
        self.assertEqual(ST.make_many(self.stills, rep["todo"], workers=1), 0)
        self.assertEqual(list(self.stills.unreadable().values()), [500])
        rep = ST.plan(self.stills, {"A": self.root})
        self.assertEqual((rep["drives"][0]["to_make"], rep["drives"][0]["unreadable"], rep["todo"]), (0, 1, []))


    def test_a_minute_that_is_left_alone_is_not_planned(self):
        clip(self.root, "2026-09-11_09-46-36", 5000)
        rep = ST.plan(self.stills, {"A": self.root}, skip=lambda st: st == S)
        self.assertEqual((rep["drives"][0]["to_make"], rep["todo"]), (0, []))
        self.assertEqual(len(ST.plan(self.stills, {"A": self.root})["todo"]), 1)

    def test_stills_are_removed_by_minute(self):
        os.makedirs(os.path.dirname(self.stills.path(S)), exist_ok=True)
        with open(self.stills.path(S), "wb") as f:
            f.write(b"12345")
        self.assertEqual(self.stills.remove([S, "2026-09-11_09-46-35"]), (1, 5))
        self.assertFalse(self.stills.has(S))


if __name__ == "__main__":
    unittest.main()
