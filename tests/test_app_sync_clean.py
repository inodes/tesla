"""app_sync.clean: a script's colour codes and redrawn progress lines never reach the page."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import app_sync as A  # noqa: E402


class CleanTest(unittest.TestCase):
    def test_colour_codes_go(self):
        self.assertEqual(A.clean("\x1b[96m\x1b[1mSTARTING BATCH\x1b[0m\n"), "STARTING BATCH")
        self.assertEqual(A.clean("[ 1/ 5] Berry [\x1b[92mUP_TO_DATE\x1b[0m]..."), "[ 1/ 5] Berry [UP_TO_DATE]...")

    def test_a_redrawn_line_keeps_what_it_ended_as(self):
        self.assertEqual(A.clean("10%\r50%\r100% done\n"), "100% done")

    def test_plain_text_is_left_alone(self):
        self.assertEqual(A.clean("==> Tesla Superchargers\n"), "==> Tesla Superchargers")


if __name__ == "__main__":
    unittest.main()
