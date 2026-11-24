"""clip_move.put moves one file whole or not at all."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
import clip_move as CM  # noqa: E402


class AtomicTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.src = os.path.join(self.d, "a", "2026-11-20_10-00-00-front.mp4")
        self.dst = os.path.join(self.d, "b", "x", "2026-11-20_10-00-00-front.mp4")
        os.makedirs(os.path.dirname(self.src))
        with open(self.src, "wb") as f:
            f.write(os.urandom(9 * 1024 ** 2))
        os.utime(self.src, (1700000000, 1700000000))

    def left(self):
        return [n for dp, _d, fn in os.walk(self.d) for n in fn]

    def test_a_move_is_checked_and_leaves_no_part(self):
        self.assertEqual(CM.put(self.src, self.dst, None), "moved")
        self.assertFalse(os.path.exists(self.src))
        self.assertEqual(os.path.getsize(self.dst), 9 * 1024 ** 2)
        self.assertEqual(int(os.path.getmtime(self.dst)), 1700000000)
        self.assertEqual(self.left(), [os.path.basename(self.dst)])

    def test_stopped_in_the_middle_of_a_copy_leaves_the_video_where_it_was(self):
        calls = []

        def stop():
            calls.append(1)
            return len(calls) > 1
        with self.assertRaises(CM.Cancelled):
            CM.put(self.src, self.dst, None, stop=stop)
        self.assertTrue(os.path.exists(self.src))
        self.assertEqual(self.left(), [os.path.basename(self.src)])      # no half copy, no destination file

    def test_a_copy_that_reads_back_differently_is_never_put_in_place(self):
        real = CM._crc_from_drive
        CM._crc_from_drive = lambda p: "00000000"
        try:
            with self.assertRaises(OSError):
                CM.put(self.src, self.dst, None)
        finally:
            CM._crc_from_drive = real
        self.assertTrue(os.path.exists(self.src))
        self.assertEqual(self.left(), [os.path.basename(self.src)])

    def test_the_destinations_own_file_is_not_touched_when_a_copy_fails(self):
        os.makedirs(os.path.dirname(self.dst))
        with open(self.dst, "wb") as f:
            f.write(b"old")
        real = CM._crc_from_drive
        CM._crc_from_drive = lambda p: "00000000"
        try:
            with self.assertRaises(OSError):
                CM.put(self.src, self.dst, "overwrite")
        finally:
            CM._crc_from_drive = real
        with open(self.dst, "rb") as f:
            self.assertEqual(f.read(), b"old")

    def test_sweep_parts_removes_only_half_copies(self):
        os.makedirs(os.path.dirname(self.dst))
        for n in ("v.mp4.part", "v.mp4", "event.json"):
            open(os.path.join(os.path.dirname(self.dst), n), "wb").close()
        self.assertEqual(CM.sweep_parts(self.d), 1)
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.dst))), ["event.json", "v.mp4"])


if __name__ == "__main__":
    unittest.main()
