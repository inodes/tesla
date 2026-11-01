"""TODO-068: the drives' Tasks and TeslaCam's "N updates needed" count the same thing (clip_keep.ClipKeep.needs_by_drive)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import clip_keep as CK  # noqa: E402
import drive_roles as DR  # noqa: E402


def clip(bytes_, meta=(0, 0), stills=(0, 0), signs=(0, 0), shots=(0, 0), window=(0, 0), **kw):
    return dict(bytes=bytes_, meta=list(meta), stills=list(stills), signs=list(signs), shots=list(shots), window=list(window), **kw)


class NeedsByDriveTest(unittest.TestCase):
    def setUp(self):
        self.real = DR.read_roles
        DR.read_roles = lambda: "AJUL"

    def tearDown(self):
        DR.read_roles = self.real

    def needs(self, clips, roots):
        return CK.ClipKeep.needs_by_drive(CK.ClipKeep.__new__(CK.ClipKeep), roots, clips)

    def test_a_clip_counts_once_on_the_first_connected_drive_that_holds_it(self):
        clips = dict(recent={"r1": clip({"J": 5, "L": 5}, meta=(1, 3), stills=(0, 3))})
        n = self.needs(clips, {"J": "/j", "L": "/l"})
        self.assertEqual((n["J"]["clips"], n["J"]["telemetry"], n["J"]["stills"]), (1, 2, 3))
        self.assertEqual(n["L"]["clips"], 0)
        n = self.needs(clips, {"L": "/l"})                       # JOWUA away: LaCie has it
        self.assertEqual(n["L"]["clips"], 1)

    def test_ignored_done_and_unreachable_clips_count_nowhere(self):
        clips = dict(recent={"home": clip({"A": 1}, meta=(0, 4), ignored=True), "done": clip({"A": 1}, meta=(4, 4), stills=(4, 4)),
                             "away": clip({"J": 1}, meta=(0, 4))},
                     saved={"e": clip({"A": 1}, window=(0, 1), signs=(1, 2), refine_n=1)})
        n = self.needs(clips, {"A": "/a"})
        self.assertEqual(n, {"A": dict(clips=1, telemetry=0, stills=0, snapshots=0, signs=2, pictures=1)})

    def test_stills_waiting_for_a_drive_page_are_not_counted_as_stills_but_as_snapshots(self):
        clips = dict(recent={"r": clip({"A": 1}, stills=(0, 5), wait_n=2, snap_n=1)})
        n = self.needs(clips, {"A": "/a"})["A"]
        self.assertEqual((n["stills"], n["snapshots"]), (2, 1))


if __name__ == "__main__":
    unittest.main()
