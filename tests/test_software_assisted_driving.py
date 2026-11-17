"""In Europe FSD (Supervised) is called "Tesla Assisted Driving" (listed from 2026.39.100): the same note, the same FSD."""
import os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))

from lib.software import notes as N  # noqa: E402


class AssistedDrivingTest(unittest.TestCase):
    def test_same_note_key(self):
        self.assertEqual(N.key("Tesla Assisted Driving"), N.key("FSD (Supervised)"))
        self.assertEqual(N.key("Tesla Assisted Driving (Supervised)"), N.key("FSD (Supervised)"))
        self.assertNotEqual(N.key("FSD (Supervised) Naming Update"), N.key("Tesla Assisted Driving"))      # the note about the rename is its own note

    def test_it_is_an_fsd_note(self):
        self.assertEqual(N.fsd_note("Tesla Assisted Driving"), ("fsd", ""))
        self.assertEqual(N.fsd_note("Tesla Assisted Driving v14.3.13"), ("fsd", "14.3.13"))
        self.assertEqual(N.fsd_note("FSD (Supervised) Naming Update"), ("fsd", ""))
        self.assertEqual(N.fsd_version("Tesla Assisted Driving v14.3.13"), "14.3.13")
        self.assertIsNone(N.fsd_note("Speed Profiles"))

    def test_the_version_kinds_look_for_both_names(self):
        src = open(os.path.join(HERE, "..", "Tools", "lib", "app_software.py"), encoding="utf-8").read()
        self.assertIn("title LIKE 'Tesla Assisted Driving%'", src)


if __name__ == "__main__":
    unittest.main()
