"""test_sentry_kept.py - the kept list of Sentry / Saved events never loses an event, whatever happens to the drive.

    python3 -m unittest tests/test_sentry_kept.py
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
import app_sentry as S  # noqa: E402

E1, E2 = "2026-10-16_16-00-00", "2026-10-17_09-30-00"


class Kept(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.drive = os.path.join(self.tmp, "TeslaCam")
        for e in (E1, E2):
            self.add(e)
        self.s = S.Sentry(self.tmp, "idx.json", "SentryClips")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, e):
        d = os.path.join(self.drive, "SentryClips", e)
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, e + "-front.mp4"), "wb").close()
        with open(os.path.join(d, "event.json"), "w") as f:
            json.dump(dict(timestamp=e.replace("_", "T", 1)[:11] + e[11:].replace("-", ":"), city="Ryde", reason="sentry_aware_object_detection", camera="5"), f)

    def kept(self):
        with open(os.path.join(self.tmp, "teslacam_events", "idx.json")) as f:
            return json.load(f)

    def ids(self, root):
        return sorted(e["id"] for e in self.s.events(root)[0])

    def test_a_deleted_folder_is_marked_not_dropped(self):
        self.assertEqual(self.ids(self.drive), [E1, E2])
        shutil.rmtree(os.path.join(self.drive, "SentryClips", E1))
        self.assertEqual(self.ids(self.drive), [E2])                                 # while the drive is connected, only what is on it
        k = self.kept()
        self.assertEqual(sorted(k), [E1, E2])                                        # but the kept file still holds both, with the city and reason
        self.assertTrue(k[E1]["gone"])
        self.assertEqual((k[E1]["city"], k[E1]["reason"]), ("Ryde", "sentry_aware_object_detection"))
        self.assertNotIn("gone", k[E2])
        self.assertEqual(self.ids(None), [E1, E2])                                   # drive away: everything kept is answered

    def test_it_survives_a_restart_and_a_new_event(self):
        self.ids(self.drive)
        shutil.rmtree(os.path.join(self.drive, "SentryClips", E1))
        self.ids(self.drive)
        self.add("2026-10-18_10-00-00")                                              # something new makes the file be written again
        s2 = S.Sentry(self.tmp, "idx.json", "SentryClips")
        self.assertEqual(sorted(e["id"] for e in s2.events(self.drive)[0]), [E2, "2026-10-18_10-00-00"])
        self.assertEqual(sorted(self.kept()), [E1, E2, "2026-10-18_10-00-00"])       # the gone one was not lost by that write
        self.assertEqual(sorted(e["id"] for e in S.Sentry(self.tmp, "idx.json", "SentryClips").events(None)[0]), [E1, E2, "2026-10-18_10-00-00"])

    def test_a_folder_that_comes_back_is_unmarked(self):
        self.ids(self.drive)
        shutil.move(os.path.join(self.drive, "SentryClips", E1), os.path.join(self.tmp, "away"))
        self.ids(self.drive)
        shutil.move(os.path.join(self.tmp, "away"), os.path.join(self.drive, "SentryClips", E1))
        self.assertEqual(self.ids(self.drive), [E1, E2])
        self.assertNotIn("gone", self.kept()[E1])

    def test_an_empty_listing_marks_nothing(self):
        self.ids(self.drive)
        for e in (E1, E2):
            shutil.rmtree(os.path.join(self.drive, "SentryClips", e))
        before = self.kept()
        self.assertEqual(self.ids(self.drive), [E1, E2])                             # a drive that shows nothing is not taken as "all deleted"
        self.assertEqual(self.kept(), before)

    def test_drive_not_connected_changes_nothing(self):
        self.ids(self.drive)
        before = self.kept()
        self.assertEqual(self.s.events(None)[1], False)
        self.assertEqual(self.kept(), before)

    def test_a_list_at_the_top_of_the_data_folder_is_moved_into_teslacam_events(self):
        import app_sentry as AS
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "teslacam_index_A_saved.json"), "w") as f:
            f.write("{}")
        p = AS.index_path(d, "teslacam_index_A_saved.json")
        self.assertEqual(p, os.path.join(d, "teslacam_events", "teslacam_index_A_saved.json"))
        self.assertTrue(os.path.isfile(p))
        self.assertFalse(os.path.exists(os.path.join(d, "teslacam_index_A_saved.json")))


if __name__ == "__main__":
    unittest.main()
