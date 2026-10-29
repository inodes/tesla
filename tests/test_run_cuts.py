"""run_cuts: a Recent recording is cut where the timeline goes from a parked spell to a drive (or back)."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import clip_clock as CC  # noqa: E402
import clip_keep as CK  # noqa: E402
import run_cuts as RC  # noqa: E402


def stamps(first_min, n, day="2026-10-29", hour=8):
    return ["%s_%02d-%02d-07" % (day, hour + (first_min + k) // 60, (first_min + k) % 60) for k in range(n)]


def item(kind, a, b, **kw):
    """Timeline item from the minute named a to the end of the minute named b."""
    return dict(id=a, kind=kind, t0=CC.utc(a), t1=CC.utc(b) + 60, **kw)


class CutsTest(unittest.TestCase):
    def setUp(self):
        RC.set_file(os.path.join(tempfile.mkdtemp(), "run_cuts.json"))
        self.sts = stamps(0, 30)                                   # 08:00 .. 08:29, one run
        self.items = [item("stopped", self.sts[0], self.sts[9]),
                      item("drive", self.sts[10], self.sts[19], p0=[-33.80, 151.10], p1=[-33.81, 151.11]),
                      item("stopped", self.sts[20], self.sts[29])]

    def test_a_park_a_drive_and_a_park_are_three_sections_of_one_run(self):
        c = RC.compute(self.sts, self.items)
        self.assertEqual([(k, v[0], v[3]) for k, v in sorted(c.items())],
                         [(self.sts[0], "park", False), (self.sts[10], "drive", True), (self.sts[20], "park", True)])

    def test_a_parked_section_is_where_the_drive_beside_it_began_or_ended(self):
        c = RC.compute(self.sts, self.items)
        self.assertEqual(c[self.sts[0]][1:3], [-33.80, 151.10])    # before the drive: where it began
        self.assertEqual(c[self.sts[20]][1:3], [-33.81, 151.11])   # after it: where it ended

    def test_minutes_not_read_never_make_a_cut(self):
        items = [item("drive", self.sts[0], self.sts[9], p0=[1.0, 2.0], p1=[1.1, 2.1]), item("recording", self.sts[10], self.sts[14]),
                 item("stopped", self.sts[15], self.sts[29])]
        c = RC.compute(self.sts, items)
        self.assertEqual([(k, v[0], v[3]) for k, v in sorted(c.items())], [(self.sts[0], "drive", False), (self.sts[15], "park", True)])   # the unread minutes stay with the drive

    def test_a_drive_after_another_drive_is_cut_between_them(self):
        items = [item("drive", self.sts[0], self.sts[9]), item("drive", self.sts[10], self.sts[29])]
        c = RC.compute(self.sts, items)
        self.assertEqual([(k, v[3]) for k, v in sorted(c.items())], [(self.sts[0], False), (self.sts[10], True)])

    def test_a_gap_starts_a_run_without_a_cut(self):
        sts = stamps(0, 5) + stamps(20, 5)
        c = RC.compute(sts, [item("stopped", sts[0], sts[-1])])
        self.assertEqual([(k, v[3]) for k, v in sorted(c.items())], [(sts[0], False), (sts[5], False)])

    def test_the_cuts_are_kept_and_say_when_they_change(self):
        seen = []
        RC.on_change(lambda: seen.append(1))
        v0 = RC.version()
        self.assertTrue(RC.update(self.sts, self.items))
        self.assertGreater(RC.version(), v0)
        self.assertTrue(RC.is_cut(self.sts[10]) and not RC.is_cut(self.sts[11]))
        self.assertEqual((RC.kind(self.sts[10]), RC.kind(self.sts[0])), ("drive", "park"))
        self.assertFalse(RC.update(self.sts, self.items))           # nothing changed: no new version, no news
        self.assertEqual(len(seen), 1)
        RC.set_file(RC._PATH)                                       # a new run of the app reads them back
        self.assertTrue(RC.is_cut(self.sts[20]))

    def test_the_keep_list_groups_minutes_by_the_same_runs(self):
        RC.update(self.sts, self.items)
        self.assertEqual([len(r) for r in CK._runs(self.sts)], [10, 10, 10])
        RC.update(self.sts, [])
        self.assertEqual([len(r) for r in CK._runs(self.sts)], [30])

    def test_any_minute_knows_the_section_it_lies_in(self):
        RC.update(self.sts, self.items)
        self.assertEqual(RC.section(self.sts[4])[0], "park")
        self.assertEqual(RC.section(self.sts[12])[0], "drive")
        self.assertEqual(RC.section(self.sts[25])[:1], ("park",))
        self.assertIsNone(RC.section(stamps(200, 1)[0]))                      # a minute after the last section: no section known


if __name__ == "__main__":
    unittest.main()
