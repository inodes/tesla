"""footage_todo.Todo and drive_eject.eject."""
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Tools", "lib"))
import drive_eject as DE  # noqa: E402
import footage_todo as FT  # noqa: E402
import teslacam_ledger as TLG  # noqa: E402


class FakeTC:
    def __init__(self):
        self.calls = 0

    def _tl(self, role):
        return SimpleNamespace(stamps=lambda: {"a", "b"}, summary=lambda st: dict(vmax=50) if st.endswith("16-12-14") else dict(vmax=2) if st.endswith("16-13-14") else None)

    def _rv(self):
        return SimpleNamespace(base=os.path.join(tempfile.mkdtemp(), "drive_review"), scanned=lambda: self.scanned, unrefined=lambda: self.unrefined)

    scanned, unrefined = {}, set()

    def scan_report(self, roots):
        self.calls += 1
        return dict(drives=[dict(role=r, to_read=3) for r in roots])


class FakeFG:
    stills = SimpleNamespace(stamps=lambda: {"a"})

    def stills_report(self, roots, walked=None):
        return dict(drives=[dict(role=r, to_make=5) for r in roots])


class TodoTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.cam = os.path.join(self.t, "J", "TeslaCam")
        for n in ("2026-10-11_16-12-14", "2026-10-11_16-13-14"):
            os.makedirs(os.path.join(self.cam, "RecentClips"), exist_ok=True)
            open(os.path.join(self.cam, "RecentClips", n + "-front.mp4"), "wb").write(b"x" * 10)
        self.data = os.path.join(self.t, "data")
        os.makedirs(self.data)
        self.tc, self.fg = FakeTC(), FakeFG()
        self.todo = FT.Todo(os.path.join(self.t, "cache", "todo.json"), self.data, self.tc, self.fg, lambda: {"J": self.cam})

    def wait(self):
        for _ in range(100):
            if not (self.todo._thread and self.todo._thread.is_alive()):
                return
            time.sleep(0.05)

    def test_files_not_in_the_log_telemetry_and_stills_are_counted_per_drive(self):
        s = self.todo.snapshot()
        self.assertTrue(s["checking"])                                   # the first look starts the work
        self.wait()
        s = self.todo.snapshot()
        self.assertFalse(s["checking"])
        d = s["drives"]["J"]
        self.assertEqual((d["log_new"], d["log_gone"], d["telemetry"], d["stills"], d["signs"]), (2, 0, 3, 5, 1))     # one minute driven fast enough, signs not read
        self.assertEqual(sum(d["new_days"].values()), 2)                 # which days the new files are from, so a number that looks too big can be checked

    def test_signs_are_counted_until_read_and_until_given_the_second_look(self):
        self.tc.scanned, self.tc.unrefined = {"2026-10-11_16-12-14": 2}, set()
        self.todo.snapshot()
        self.wait()
        self.assertEqual(self.todo.snapshot()["drives"]["J"]["signs"], 0)                       # read and nothing to look at again
        self.tc.unrefined = {"2026-10-11_16-12-14"}
        self.todo.forget()
        self.todo.snapshot()
        self.wait()
        self.assertEqual(self.todo.snapshot()["drives"]["J"]["signs"], 1)                       # read, but the second look is still to do
        self.tc.scanned, self.tc.unrefined = {}, set()

    def test_minutes_parked_at_home_or_taken_off_the_log_are_not_counted_for_signs(self):
        import home_ignore as IGN
        IGN.set_stamps({"2026-10-11_16-12-14"})
        self.addCleanup(lambda: IGN.set_stamps(()))
        self.todo.snapshot()
        self.wait()
        self.assertEqual(self.todo.snapshot()["drives"]["J"]["signs"], 0)

    def test_the_minutes_left_alone_at_home_are_kept_between_runs_so_the_first_count_after_a_start_leaves_them_out(self):
        import home_ignore as IGN
        f = os.path.join(self.t, "home_ignore.json")
        self.addCleanup(lambda: (IGN.set_file(None), IGN.set_stamps(())))
        IGN.set_file(f)
        IGN.set_stamps({"2026-10-11_16-12-14"})
        IGN.set_stamps(())
        IGN.set_stamps({"2026-10-11_16-12-14", "2026-10-11_16-13-14"})
        IGN._STAMPS = frozenset()                                       # a new start: nothing in memory
        IGN.set_file(f)
        self.assertTrue(IGN.has("2026-10-11_16-13-14"))
        self.assertEqual(IGN.version(), 2)

    def test_a_listed_drive_has_nothing_to_list_and_a_gone_file_is_counted(self):
        led = TLG.Ledger(self.data)
        led.scan("J", self.cam)
        os.remove(os.path.join(self.cam, "RecentClips", "2026-10-11_16-13-14-front.mp4"))
        self.todo.snapshot()
        self.wait()
        d = self.todo.snapshot()["drives"]["J"]
        self.assertEqual((d["log_new"], d["log_gone"]), (0, 1))

    def test_a_name_the_folder_still_lists_but_whose_file_is_not_there_is_not_a_new_file(self):
        led = TLG.Ledger(self.data)
        led.scan("J", self.cam)
        gone = os.path.join(self.cam, "RecentClips", "2026-10-11_16-13-14-front.mp4")
        os.remove(gone)
        led.scan("J", self.cam)                                          # the log has it as gone
        real = FT.TF.walk
        FT.TF.walk = lambda p, stat=True, cached=False: ([dict(src="recent", ev="", stamp="2026-10-11_16-13-14", cam="front", size=0, mtime=0),
                                                           dict(src="recent", ev="", stamp="2026-10-11_16-12-14", cam="front", size=0, mtime=0)], True)      # the drive still lists both names
        try:
            self.todo.snapshot()
            self.wait()
        finally:
            FT.TF.walk = real
        d = self.todo.snapshot()["drives"]["J"]
        self.assertEqual((d["log_new"], d["ghosts"]), (0, 1))

    def test_the_answer_is_kept_until_the_drive_or_what_is_kept_from_it_changes(self):
        self.todo.snapshot()
        self.wait()
        self.todo.snapshot()
        self.assertEqual(self.tc.calls, 1)
        again = FT.Todo(os.path.join(self.t, "cache", "todo.json"), self.data, self.tc, self.fg, lambda: {"J": self.cam})
        self.assertFalse(again.snapshot()["checking"])                  # after a restart too
        self.assertEqual(self.tc.calls, 1)

    def test_an_answer_worked_out_by_older_code_is_looked_at_again(self):
        self.todo.snapshot()
        self.wait()
        self.assertEqual(self.todo._sig({"J": self.cam})[-1], FT.CODE_VER)
        self.todo._mem = dict(self.todo._mem, sig=self.todo._sig({"J": self.cam})[:-1])      # as saved before the version was part of it
        self.assertTrue(self.todo.snapshot()["checking"])
        self.wait()

    def test_progress_is_reported_while_it_works_and_a_failure_is_not_retried_at_once(self):
        seen = []
        orig = self.tc.scan_report
        self.tc.scan_report = lambda roots: (seen.append(self.todo.snapshot()["progress"]), orig(roots))[1]
        self.todo.snapshot()
        self.wait()
        self.assertEqual(seen[0]["step"], "telemetry")
        self.assertEqual((seen[0]["done"], seen[0]["total"]), (1, 4))
        self.assertIsNone(self.todo.snapshot()["progress"])
        bad = FT.Todo(os.path.join(self.t, "c3.json"), self.data, SimpleNamespace(_tl=None), self.fg, lambda: {"J": self.cam})
        bad.snapshot()
        for _ in range(100):
            if not bad._thread.is_alive():
                break
            time.sleep(0.05)
        s = bad.snapshot()
        self.assertTrue(s["error"])
        self.assertFalse(s["checking"])                                  # not asked again for a minute

    def test_what_a_job_did_is_taken_off_at_once(self):
        self.todo.snapshot()
        self.wait()
        self.todo.subtract("stills", {"J": 2})
        self.todo.subtract("telemetry", {"J": 10})                       # more than was left: never below nothing
        d = self.todo.snapshot()["drives"]["J"]
        self.assertEqual((d["stills"], d["telemetry"]), (3, 0))
        self.todo.subtract("log", {"J": 1})
        self.assertEqual(self.todo.snapshot()["drives"]["J"]["log_new"], 0)

    def test_nothing_is_worked_out_while_a_job_reads_a_drive(self):
        busy = FT.Todo(os.path.join(self.t, "c2.json"), self.data, self.tc, self.fg, lambda: {"J": self.cam}, lambda: False)
        s = busy.snapshot()
        self.assertTrue(s["checking"])
        self.assertEqual(self.tc.calls, 0)

    def test_an_unchanged_drive_is_not_walked_again_when_only_telemetry_or_stills_change(self):
        import teslacam_files as TF
        self.assertEqual([TF.key(f) for f in TF.walk(self.cam, stat=False)[0]], [TF.key(f) for f in TF.walk(self.cam, stat=True)[0]])
        walks, got_walked = [], []
        real_walk = TF.walk
        TF.walk = lambda p, stat=True: (walks.append((p, stat)), real_walk(p, stat))[1]
        self.fg.stills_report = lambda roots, walked=None: (got_walked.append(walked), dict(drives=[dict(role=r, to_make=5) for r in roots]))[1]
        try:
            self.todo.snapshot()
            self.wait()
            self.assertEqual(walks, [(self.cam, False)])             # listed once, without per-file stat
            self.assertIn("J", got_walked[-1])                       # stills_report reused the same listing
            self.fg.stills = SimpleNamespace(stamps=lambda: {"a", "b"})
            self.todo.snapshot()                                     # stills count changed, the drive did not
            self.wait()
            self.assertEqual(len(walks), 1)                          # the drive was not walked again
        finally:
            TF.walk = real_walk


class EjectTest(unittest.TestCase):
    def fake(self, code, out="", err=""):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return SimpleNamespace(returncode=code if cmd[0] == "diskutil" else 0, stdout=out if cmd[0] == "lsof" else "", stderr=err)
        return run, calls

    def test_a_drive_under_volumes_is_ejected_with_diskutil(self):
        run, calls = self.fake(0)
        import drive_registry as REG
        real = REG._mount_point
        REG._mount_point = lambda p: "/Volumes/JOWUA"
        try:
            r = DE.eject("/Volumes/JOWUA/TeslaCam", platform="darwin", run=run, which=lambda x: "/usr/bin/" + x)
        finally:
            REG._mount_point = real
        self.assertTrue(r["ok"])
        self.assertEqual(calls[0], ["diskutil", "eject", "/Volumes/JOWUA"])
        self.assertIn("safe to unplug", r["text"])

    def test_a_busy_drive_names_what_holds_it(self):
        run, calls = self.fake(1, out="COMMAND PID\nPreview 1\nPython 2\nPreview 3\n", err="Volume failed to eject: dissented by PID 55")
        import drive_registry as REG
        real = REG._mount_point
        REG._mount_point = lambda p: "/Volumes/JOWUA"
        try:
            r = DE.eject("/Volumes/JOWUA/TeslaCam", platform="darwin", run=run, which=lambda x: "/usr/bin/" + x)
        finally:
            REG._mount_point = real
        self.assertFalse(r["ok"])
        self.assertIn("Open in: Preview, Python", r["text"])

    def test_only_volumes_are_touched_and_only_on_a_mac(self):
        import drive_registry as REG
        real = REG._mount_point
        REG._mount_point = lambda p: "/"
        try:
            self.assertFalse(DE.eject("/x/TeslaCam", platform="darwin", run=self.fake(0)[0], which=lambda x: "/usr/bin/x")["ok"])
        finally:
            REG._mount_point = real
        self.assertFalse(DE.eject("/Volumes/A/TeslaCam", platform="linux")["ok"])


if __name__ == "__main__":
    unittest.main()
