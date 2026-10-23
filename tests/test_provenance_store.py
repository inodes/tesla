"""Provenance store: one record per recording, a reused name with a different creation time is kept as a collision, the car's clock changes are listed."""
import calendar, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
sys.path.insert(0, os.path.dirname(__file__))
import teslacam_provenance_store as PS
from test_provenance import clip, utc

KEY = "recent||2026-10-04_12-00-00-front"


class FakeClock:
    def utc(self, stamp):
        return calendar.timegm((int(stamp[:4]), int(stamp[5:7]), int(stamp[8:10]), int(stamp[11:13]), int(stamp[14:16]), int(stamp[17:19]))) - 11 * 3600
    def version(self):
        return 7


class FakeLedger:
    def __init__(self, days):
        self.d = days
    def days(self):
        return sorted(self.d)
    def day(self, day):
        return self.d[day]


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.data = os.path.join(self.t, "data")
        os.makedirs(self.data)
        self.roots = {}
        for r in "AJ":
            self.roots[r] = os.path.join(self.t, r, "TeslaCam")
            os.makedirs(os.path.join(self.roots[r], "RecentClips"))

    def put(self, role, name, created, **kw):
        p = os.path.join(self.roots[role], "RecentClips", name + ".mp4")
        clip(p, created, **kw)
        return p


class Observe(Base):
    def test_same_recording_on_two_drives_is_one_record(self):
        st = PS.Store(self.data, clock=FakeClock())
        c = utc(2026, 10, 4, 1, 0, 0)                       # name 12-00-00 = +11 h
        a = self.put("A", "2026-10-04_12-00-00-front", c)
        j = self.put("J", "2026-10-04_12-00-00-front", c + 1)   # the name truncates a second
        st.observe(KEY, a, "A"); r = st.observe(KEY, j, "J")
        recs = st.records(KEY)
        self.assertEqual(len(recs), 1)
        self.assertEqual(sorted(r["sources"]), ["A", "J"])
        self.assertEqual((r["offset"], r["agree"], r["flags"]), (11 * 3600, True, []))
        self.assertEqual(r["sources"]["A"]["rel"], os.path.join("RecentClips", "2026-10-04_12-00-00-front.mp4"))

    def test_reused_name_is_a_collision_and_both_are_kept(self):
        st = PS.Store(self.data, clock=FakeClock())
        a = self.put("A", "2026-10-04_12-00-00-front", utc(2026, 10, 4, 1, 0, 0))     # NSW +11
        j = self.put("J", "2026-10-04_12-00-00-front", utc(2026, 10, 4, 2, 0, 0))     # back in QLD +10: the same name an hour later
        st.observe(KEY, a, "A"); st.observe(KEY, j, "J")
        recs = st.records(KEY)
        self.assertEqual(len(recs), 2)
        self.assertTrue(all("collision" in r["flags"] for r in recs))
        self.assertEqual(sorted(r["offset"] for r in recs), [10 * 3600, 11 * 3600])
        self.assertEqual([sorted(r["sources"]) for r in sorted(recs, key=lambda r: r["created"])], [["A"], ["J"]])

    def test_size_differs_is_flagged(self):
        st = PS.Store(self.data)
        c = utc(2026, 10, 4, 1, 0, 0)
        a = self.put("A", "2026-10-04_12-00-00-front", c)
        j = self.put("J", "2026-10-04_12-00-00-front", c, street="Longer street name here")
        st.observe(KEY, a, "A"); st.observe(KEY, j, "J")
        self.assertIn("size_differs", st.records(KEY)[0]["flags"])

    def test_learned_clock_disagreement_is_recorded(self):
        st = PS.Store(self.data, clock=FakeClock())
        p = self.put("A", "2026-10-04_12-00-00-front", utc(2026, 10, 4, 2, 0, 0))     # header says +10, the learned clock says +11
        r = st.observe(KEY, p, "A")
        self.assertFalse(r["agree"])
        self.assertEqual(PS.summary(st)["disagree_with_learned_clock"], 1)

    def test_persists_and_reloads(self):
        st = PS.Store(self.data)
        p = self.put("A", "2026-10-04_12-00-00-front", utc(2026, 10, 4, 1, 0, 0))
        st.observe(KEY, p, "A"); st.flush()
        st2 = PS.Store(self.data)
        self.assertTrue(st2.has(KEY, "A")); self.assertFalse(st2.has(KEY, "J"))
        self.assertEqual(st2.days(), ["2026-10-04"])

    def test_unreadable_header_is_not_recorded(self):
        st = PS.Store(self.data)
        p = os.path.join(self.roots["A"], "RecentClips", "2026-10-04_12-00-00-front.mp4")
        open(p, "wb").write(b"junk")
        self.assertIsNone(st.observe(KEY, p, "A"))
        self.assertEqual(st.records(KEY), [])


class Backfill(Base):
    def ledger(self, names, roles="AJ"):
        d = {}
        for n in names:
            d["recent||%s-front" % n] = dict(size=1, drives={r: dict(first="x", last="y", gone=None) for r in roles})
        return FakeLedger({names[0][:10]: d})

    def test_reads_only_what_is_missing_and_counts_failures(self):
        names = ["2026-10-04_12-00-00", "2026-10-04_12-01-00", "2026-10-04_12-02-00"]
        for i, n in enumerate(names[:2]):
            self.put("A", n + "-front", utc(2026, 10, 4, 1, i, 0))
        open(os.path.join(self.roots["A"], "RecentClips", names[2] + "-front.mp4"), "wb").write(b"junk")
        st = PS.Store(self.data)
        led = self.ledger(names, roles="A")
        r = PS.backfill(st, led, self.roots, workers=2)
        self.assertEqual((r["total"], r["read"], r["failed"]), (3, 2, 1))
        r2 = PS.backfill(PS.Store(self.data), led, self.roots)          # recorded ones are not read again, the failed one is retried
        self.assertEqual((r2["total"], r2["read"], r2["failed"]), (1, 0, 1))

    def test_drive_not_connected_is_skipped_and_gone_files_ignored(self):
        n = "2026-10-04_12-00-00"
        self.put("A", n + "-front", utc(2026, 10, 4, 1, 0, 0))
        led = FakeLedger({"2026-10-04": {"recent||%s-front" % n: dict(size=1, drives={"A": dict(first="x", last="y", gone="z"), "J": dict(first="x", last="y", gone=None)})}})
        r = PS.backfill(PS.Store(self.data), led, {"A": self.roots["A"]})   # A's copy is gone and J is not connected
        self.assertEqual(r["total"], 0)


class ClockEvents(Base):
    def test_back_jump_and_forward_jump_are_listed(self):
        st = PS.Store(self.data)
        seq = [(utc(2026, 10, 4, 0, 0, 0), 10), (utc(2026, 10, 4, 0, 1, 0), 10), (utc(2026, 10, 4, 3, 0, 0), 11), (utc(2026, 10, 4, 3, 1, 0), 11), (utc(2026, 10, 4, 5, 0, 0), 10)]
        for c, off in seq:
            n = c + off * 3600
            stamp = "%04d-%02d-%02d_%02d-%02d-%02d" % tuple(__import__("time").gmtime(n)[:6])
            p = self.put("A", stamp + "-front", c)
            st.observe("recent||%s-front" % stamp, p, "A")
        ev = PS.clock_events(st)
        self.assertEqual([(e["from_h"], e["to_h"]) for e in ev], [(10, 11), (11, 10)])
        self.assertEqual(ev[1]["utc"], utc(2026, 10, 4, 5, 0, 0))


if __name__ == "__main__":
    unittest.main()
