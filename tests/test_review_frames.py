"""test_review_frames.py - the stills the sign reading takes from a clip: key frames only (a fifth of the work), frame by frame when the clip has too few of them.

    python3 -m unittest tests/test_review_frames.py
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import app_review as AR  # noqa: E402


class Frames(unittest.TestCase):
    def fake(self, key_sizes, all_sizes):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            sizes = key_sizes if "-skip_frame" in cmd else all_sizes
            out = cmd[-1]
            for k, n in enumerate(sizes, 1):
                with open(out % k, "wb") as f:
                    f.write(b"x" * n)

        real = AR.subprocess.run
        AR.subprocess.run = run
        self.addCleanup(setattr, AR.subprocess, "run", real)
        return calls

    def test_key_frames_are_enough_when_every_second_has_its_own_picture(self):
        calls = self.fake([100 + k for k in range(60)], [900 + k for k in range(60)])
        with tempfile.TemporaryDirectory() as tmp:
            fr = AR._frames("clip.mp4", 1.0, tmp)
            self.assertEqual(len(fr), 60)
            self.assertEqual([s for s, _f in fr][:3], [0.0, 1.0, 2.0])
        self.assertEqual(len(calls), 1)
        self.assertIn("-skip_frame", calls[0])

    def test_a_clip_with_few_key_frames_is_taken_frame_by_frame(self):
        calls = self.fake([100] * 60, [900 + k for k in range(60)])               # the same picture over and over
        with tempfile.TemporaryDirectory() as tmp:
            fr = AR._frames("clip.mp4", 1.0, tmp)
            self.assertEqual(len(fr), 60)
            self.assertEqual(os.path.getsize(fr[5][1]), 905)                          # the second run's stills are the ones kept
        self.assertEqual(len(calls), 2)
        self.assertNotIn("-skip_frame", calls[1])


if __name__ == "__main__":
    unittest.main()
