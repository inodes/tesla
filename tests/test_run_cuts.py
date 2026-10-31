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

    def test_minutes_not_read_after_a_drive_are_the_car_parked_when_there_are_a_few_of_them(self):
        items = [item("drive", self.sts[0], self.sts[9], p0=[1.0, 2.0], p1=[1.1, 2.1]), item("recording", self.sts[10], self.sts[14]),
                 item("stopped", self.sts[15], self.sts[29])]
        c = RC.compute(self.sts, items)
        self.assertEqual([(k, v[0], v[3]) for k, v in sorted(c.items())], [(self.sts[0], "drive", False), (self.sts[10], "park", True)])    # parked at Home: never read, so no telemetry of its own

    def test_between_two_drives_the_unread_minutes_are_a_parked_section_and_a_short_hole_is_not(self):
        items = [item("drive", self.sts[0], self.sts[9], p0=[1.0, 2.0], p1=[1.1, 2.1]), item("recording", self.sts[10], self.sts[19]), item("drive", self.sts[20], self.sts[29])]
        c = RC.compute(self.sts, items)
        self.assertEqual([(k, v[0], v[3]) for k, v in sorted(c.items())], [(self.sts[0], "drive", False), (self.sts[10], "park", True), (self.sts[20], "drive", True)])
        items = [item("drive", self.sts[0], self.sts[9]), item("recording", self.sts[10], self.sts[11]), item("drive", self.sts[12], self.sts[29])]
        c = RC.compute(self.sts, items)
        self.assertEqual([k for k in sorted(c)], [self.sts[0], self.sts[12]])                         # two minutes between: a hole; the drives are cut as before

    def test_a_drive_after_another_drive_is_cut_between_them(self):
        items = [item("drive", self.sts[0], self.sts[9]), item("drive", self.sts[10], self.sts[29])]
        c = RC.compute(self.sts, items)
        self.assertEqual([(k, v[3]) for k, v in sorted(c.items())], [(self.sts[0], False), (self.sts[10], True)])

    def test_a_lone_parked_minute_is_where_the_section_before_it_ended(self):
        later = stamps(0, 1, hour=19)[0]                            # 19:00, hours after the last section: a clip of its own
        far = "2026-10-31_10-15-43"                                 # the next morning
        items = self.items + [item("parked", later, later), item("parked", far, far)]
        c = RC.compute(self.sts + [later, far], items)
        self.assertEqual(c[later][1:3], [-33.81, 151.11])           # the park before it ended there
        self.assertEqual(c[far][1:3], [None, None])                 # more than a day after the section before it: not guessed

    def test_a_carried_position_is_marked_as_an_estimate_unless_another_record_confirms_it(self):
        later = stamps(0, 1, hour=19)[0]
        items = self.items + [item("parked", later, later)]
        t = CC.utc(later)
        c = RC.compute(self.sts + [later], items)
        self.assertEqual((c[later][1:3], c[later][5:]), ([-33.81, 151.11], [True]))        # dashcam alone: an estimate
        c = RC.compute(self.sts + [later], items, [dict(t0=1, t1=2, p0=[0, 0], p1=[1, 1])], t + 60)
        self.assertEqual((c[later][1:3], c[later][5:]), ([-33.81, 151.11], []))            # Tessie is complete to here and has no drive since: confirmed
        moved = dict(t0=CC.utc(self.sts[29]) + 600, t1=CC.utc(self.sts[29]) + 1200, p0=[-33.81, 151.11], p1=[-33.5, 151.0])
        c = RC.compute(self.sts + [later], items, [moved], t + 60)
        self.assertEqual((c[later][1:3], c[later][5:]), ([-33.5, 151.0], []))              # it moved without a clip showing it: where that drive ended
        c = RC.compute(self.sts + [later], items, [dict(t0=1, t1=2, p0=[0, 0], p1=[1, 1])], t - 3600)
        self.assertEqual(c[later][5:], [True])                                             # Tessie not complete to here: still an estimate

    def test_a_parked_section_next_to_a_drive_in_another_run_is_where_the_drive_ended_or_began(self):
        drive = stamps(0, 5, hour=9)                                # 09:00 .. 09:04, a drive
        park_after = stamps(7, 3, hour=9)                           # 09:07 .. 09:09: two minutes after it, a run of its own
        park_before = stamps(57, 2, hour=8)                        # 08:57, 08:58: just before it
        far = stamps(50, 1, hour=9)                                 # 09:50: long after
        items = [item("drive", drive[0], drive[-1], p0=[-33.80, 151.10], p1=[-33.81, 151.11]),
                 item("parked", park_after[0], park_after[-1]), item("parked", park_before[0], park_before[-1]), item("parked", far[0], far[0])]
        c = RC.compute(drive + park_after + park_before + far, items)
        self.assertEqual((c[park_after[0]][1:3], c[park_after[0]][5:]), ([-33.81, 151.11], []))      # next to the end of the drive: assumed, not an estimate
        self.assertEqual((c[park_before[0]][1:3], c[park_before[0]][5:]), ([-33.80, 151.10], []))    # next to the start of the drive
        self.assertEqual(c[far[0]][5:], [True])                                                       # far from it: carried, an estimate

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
