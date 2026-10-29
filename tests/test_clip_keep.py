"""clip_keep.ClipKeep: what is kept from each clip's video, and deleting / moving the video once it is all kept."""
import json
import os
import time
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import clip_clock as CC  # noqa: E402
import clip_keep as CK  # noqa: E402
import run_cuts as RC  # noqa: E402
import teslacam_scan as TS  # noqa: E402
import teslacam_stills as ST  # noqa: E402
import event_shots as ES  # noqa: E402
import teslacam_deletable as TD  # noqa: E402
import teslacam_ledger as TLG  # noqa: E402

LIVE = dict(first="2026-10-17T00:00:00Z", last="2026-10-17T00:00:00Z", gone=None)
S1, S2, S3 = "2026-10-16_15-40-07", "2026-10-16_15-41-07", "2026-10-16_15-42-07"


class FakeStore:
    def __init__(self, read):
        self.read = read

    def summary(self, st):
        return dict(vmax=60) if st in self.read else None


class FakeTC:
    def __init__(self, read, scanned):
        self.store, self.sc = FakeStore(read), scanned

    def _tl(self, role):
        return self.store

    def _rv(self):
        return SimpleNamespace(scanned=lambda: {s: 0 for s in self.sc}, status=lambda: dict(running=True))      # signs being read elsewhere: a move does not read first


class FakeFG:
    def __init__(self, stills):
        self.stills = SimpleNamespace(stamps=lambda: set(stills))


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.data = os.path.join(self.t, "data")
        self.roots = {r: os.path.join(self.t, r, "TeslaCam") for r in "AJ"}
        for r, root in self.roots.items():
            os.makedirs(os.path.join(root, "SavedClips", "ev1"))
        day = {}
        for st in (S1, S2):                                                      # a Saved clip of two minutes, on JOWUA only
            for cam in ("front", "back"):
                day["saved|ev1|%s-%s" % (st, cam)] = dict(size=10, drives=dict(J=dict(LIVE)))
                with open(os.path.join(self.roots["J"], "SavedClips", "ev1", "%s-%s.mp4" % (st, cam)), "wb") as f:
                    f.write(b"x" * 10)
        led = os.path.join(self.data, "teslacam_ledger")
        os.makedirs(led)
        with open(os.path.join(led, "2026-10-16.json"), "w") as f:
            json.dump(day, f)
        with open(os.path.join(led, "scans.json"), "w") as f:
            json.dump(dict(J=dict(at="x", files=4, bytes=40, ok=True)), f)
        CC.configure(self.data)
        real = TD.drive_windows
        TD.drive_windows = lambda d: [(0, 2 ** 40, True)]                        # one drive window covering everything, snapshots built
        self.addCleanup(lambda: setattr(TD, "drive_windows", real))

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def keep(self, read=(), stills=(), scanned=()):
        return CK.ClipKeep(self.data, FakeTC(set(read), set(scanned)), FakeFG(stills))


class KeepTest(Base):
    def test_a_clip_with_nothing_kept_is_not_ready_and_says_what_is_missing(self):
        r = self.keep().status()["clips"]["saved"]["ev1"]
        self.assertEqual((r["n"], r["meta"], r["stills"], r["signs"], r["ready"]), (2, [0, 2], [0, 2], [0, 0], False))
        self.assertEqual(r["bytes"], {"J": 40})
        self.assertEqual(r["size"], 40)
        self.assertTrue(r["todo"])
        self.assertEqual((r["files"], r["in_archive"]), ({"J": 4}, {}))              # 2 minutes x 2 cameras on JOWUA, none in the Archive yet

    def test_files_already_in_the_archive_are_counted(self):
        led = TLG.Ledger(self.data)
        led.mark("A", "saved|ev1|%s-front" % S1, 10, True)
        r = self.keep().status()["clips"]["saved"]["ev1"]
        self.assertEqual((r["files"]["J"], r["in_archive"]), (4, {"J": 1}))

    def test_everything_kept_makes_it_ready(self):
        r = self.keep(read=[S1, S2], stills=[S1, S2], scanned=[S1, S2]).status()["clips"]["saved"]["ev1"]
        self.assertTrue(r["ready"])
        self.assertFalse(r["todo"])
        self.assertEqual(r["signs"], [2, 2])                                      # both minutes went 60 km/h: both had their signs read

    def test_signs_count_only_minutes_that_were_driven_fast_enough(self):
        r = self.keep(read=[S1, S2], stills=[S1, S2], scanned=[S1]).status()["clips"]["saved"]["ev1"]
        self.assertEqual(r["signs"], [1, 2])
        self.assertFalse(r["ready"])

    def test_delete_is_refused_until_it_is_all_kept_then_removes_only_the_video(self):
        k = self.keep(read=[S1], stills=[S1, S2])
        out = k.start("delete", "J", [dict(kind="saved", id="ev1")], self.roots, confirmed=True)
        self.assertIn("not all kept", out["error"])
        self.assertTrue(os.path.exists(os.path.join(self.roots["J"], "SavedClips", "ev1", "%s-front.mp4" % S1)))
        k = self.keep(read=[S1, S2], stills=[S1, S2], scanned=[S1, S2])
        k.start("delete", "J", [dict(kind="saved", id="ev1")], self.roots, confirmed=True)
        k._thread.join(5)
        self.assertEqual(os.listdir(os.path.join(self.roots["J"], "SavedClips", "ev1")), [])
        j = k.job()
        self.assertEqual((j["done"], j["freed"], j["errors"]), (4, 40, []))
        self.assertEqual(k.status(force=True)["clips"]["saved"]["ev1"]["bytes"], {})      # the log knows at once

    def test_nothing_is_removed_without_a_confirmation(self):
        k = self.keep(read=[S1, S2], stills=[S1, S2], scanned=[S1, S2])
        for act in ("delete", "move"):
            self.assertIn("Not confirmed", k.start(act, "J", [dict(kind="saved", id="ev1")], self.roots)["error"])
        self.assertEqual(len(os.listdir(os.path.join(self.roots["J"], "SavedClips", "ev1"))), 4)

    def test_what_was_removed_is_kept_in_a_log_that_outlives_the_app(self):
        k = self.keep(read=[S1, S2], stills=[S1, S2], scanned=[S1, S2])
        self.assertEqual(k.job(), dict(running=False))                              # nothing done yet, nothing logged
        k.start("delete", "J", [dict(kind="saved", id="ev1")], self.roots, confirmed=True)
        k._thread.join(5)
        h = k.history()
        self.assertEqual((h[0]["action"], h[0]["role"], h[0]["removed"], h[0]["freed"], h[0]["errors"]), ("delete", "J", 4, 40, []))
        self.assertEqual(sorted(h[0]["files"])[0], "%s-back.mp4" % S1)
        self.assertEqual(h[0]["clips"], [dict(kind="saved", id="ev1")])
        again = CK.ClipKeep(self.data, k.tc, k.fg)                                  # the app was restarted
        self.assertEqual(again.job()["last"]["removed"], 4)

    def test_move_copies_to_the_archive_first_and_then_removes_it_from_the_drive(self):
        k = self.keep()
        out = k.start("move", "J", [dict(kind="saved", id="ev1")], self.roots, confirmed=True)                # nothing kept yet: a move is still allowed, nothing is lost
        self.assertNotIn("error", out)
        k._thread.join(5)
        self.assertEqual(sorted(os.listdir(os.path.join(self.roots["A"], "SavedClips", "ev1"))), sorted(["%s-%s.mp4" % (st, c) for st in (S1, S2) for c in ("front", "back")]))
        self.assertEqual(os.listdir(os.path.join(self.roots["J"], "SavedClips", "ev1")), [])
        self.assertEqual(k.status(force=True)["clips"]["saved"]["ev1"]["bytes"], {"A": 40})

    def test_a_copy_that_the_archive_already_holds_may_be_deleted_from_jowua_at_once(self):
        led = TLG.Ledger(self.data)
        for st in (S1, S2):
            for cam in ("front", "back"):
                led.mark("A", "saved|ev1|%s-%s" % (st, cam), 10, True)
        k = self.keep()
        out = k.start("delete", "J", [dict(kind="saved", id="ev1")], self.roots, confirmed=True)
        self.assertNotIn("error", out)
        k._thread.join(5)
        self.assertEqual(os.listdir(os.path.join(self.roots["J"], "SavedClips", "ev1")), [])

    def test_a_drive_that_is_not_connected_or_the_archive_itself_is_refused_for_a_move(self):
        k = self.keep()
        self.assertIn("not connected", k.start("delete", "U", [dict(kind="saved", id="ev1")], self.roots, confirmed=True)["error"])
        self.assertIn("Archive", k.start("move", "A", [dict(kind="saved", id="ev1")], self.roots, confirmed=True)["error"])


class Store2:
    """A telemetry store that can be filled: summary() is None until put()."""
    def __init__(self, vmax=60):
        self.d, self.vmax = {}, vmax

    def put(self, st):
        self.d[st] = dict(vmax=self.vmax)

    def summary(self, st):
        return self.d.get(st)

    def series(self, st):
        return dict(v=[self.vmax] * 60) if st in self.d else None


class Rv:
    def __init__(self):
        self.done, self.started, self.run = {}, None, False

    def scanned(self):
        return dict(self.done)

    def status(self):
        return dict(running=self.run, done=len(self.started or []), current="", error="")

    def start(self, items):
        self.started = items
        self.done.update({i[0]: 0 for i in items})

    def stop(self):
        pass


class ProcBase(Base):
    def setUp(self):
        super().setUp()
        self.store, self.rv, self.stills = Store2(), Rv(), set()
        self.calls = []
        tc = SimpleNamespace(_tl=lambda r: self.store, _rv=lambda: self.rv)
        st = SimpleNamespace(stamps=lambda: set(self.stills), has=lambda s: s in self.stills, width=0, quality=0)
        import teslacam_deletable as TD
        self._dw = TD.drive_windows
        TD.drive_windows = lambda d: [(0, 2 ** 40, True)]
        self.addCleanup(lambda: setattr(TD, "drive_windows", self._dw))
        self.k = CK.ClipKeep(self.data, tc, SimpleNamespace(stills=st))
        self._read, self._make = TS.read, ST.make_many

        def read(store, todo, data_dir=None, stop=None, progress=None, limit=None):
            self.calls.append(("read", [x["stamp"] for x in todo], [os.path.basename(x["path"]) for x in todo]))
            for n, x in enumerate(todo, 1):
                store.put(x["stamp"])
                progress(n, x["stamp"])

        def make(stills, todo, workers=3, stop=None, progress=None, limit=None, by="command"):
            self.calls.append(("stills", [x["stamp"] for x in todo], by))
            for n, x in enumerate(todo, 1):
                self.stills.add(x["stamp"])
                progress(n, x["stamp"])
        TS.read, ST.make_many = read, make
        self.addCleanup(lambda: (setattr(TS, "read", self._read), setattr(ST, "make_many", self._make)))


class ProcessTest(ProcBase):
    def test_a_move_reads_what_is_missing_first_and_then_moves(self):
        out = self.k.start("move", "J", [dict(kind="saved", id="ev1")], self.roots, confirmed=True)
        self.assertNotIn("error", out)
        self.k._thread.join(5)
        j = self.k.job()
        self.assertEqual([c[0] for c in self.calls], ["read", "stills"])               # read first ...
        self.assertEqual(j["errors"], [])
        self.assertEqual(os.listdir(os.path.join(self.roots["J"], "SavedClips", "ev1")), [])      # ... then the video was moved off JOWUA
        self.assertTrue(os.path.exists(os.path.join(self.roots["A"], "SavedClips", "ev1", "%s-front.mp4" % S1)))

    def test_a_move_does_not_start_when_what_is_missing_could_not_be_read(self):
        def bad(store, todo, data_dir=None, stop=None, progress=None, limit=None):
            raise OSError("drive pulled out")
        TS.read = bad
        self.k.start("move", "J", [dict(kind="saved", id="ev1")], self.roots, confirmed=True)
        self.k._thread.join(5)
        self.assertIn("drive pulled out", self.k.job()["errors"][0])
        self.assertTrue(os.listdir(os.path.join(self.roots["J"], "SavedClips", "ev1")))           # nothing was removed

    def test_it_reads_the_metadata_then_the_stills_then_the_signs_of_the_selected_clip_and_nothing_else(self):
        out = self.k.start_process([dict(kind="saved", id="ev1")], self.roots)
        self.assertNotIn("error", out)
        self.k._thread.join(5)
        j = self.k.job()
        self.assertEqual([x["step"] for x in j["steps"]], ["Reading metadata", "Making stills", "Building drive snapshots", "Saving event pictures", "Reading signs"])
        self.assertEqual([c[0] for c in self.calls], ["read", "stills"])
        self.assertEqual(self.calls[0][1], [S2, S1])                                   # newest first
        self.assertEqual(self.calls[0][2], ["%s-front.mp4" % S2, "%s-front.mp4" % S1])
        self.assertEqual(sorted(i[0] for i in self.rv.started), [S1, S2])
        self.assertEqual(j["errors"], [])
        self.assertTrue(self.k.status(force=True)["clips"]["saved"]["ev1"]["ready"])

    def test_what_is_already_kept_is_not_read_again(self):
        self.store.put(S1)
        self.stills.add(S1)
        self.rv.done[S1] = 0
        self.k.start_process([dict(kind="saved", id="ev1")], self.roots)
        self.k._thread.join(5)
        self.assertEqual(self.calls[0][1], [S2])
        self.assertEqual(self.calls[1][1], [S2])
        self.assertEqual([i[0] for i in self.rv.started], [S2])

    def test_a_single_step_may_be_run_on_its_own(self):
        self.rv.run = True                                                              # sign scanner busy elsewhere: stills alone still run
        out = self.k.start_process([dict(kind="saved", id="ev1")], self.roots, steps=["stills"])
        self.assertNotIn("error", out)
        self.k._thread.join(5)
        self.assertEqual([x["step"] for x in self.k.job()["steps"]], ["Making stills"])
        self.assertEqual([c[0] for c in self.calls], ["stills"])
        self.assertIsNone(self.rv.started)

    def test_it_is_refused_while_something_else_is_done_to_the_drives_or_there_is_no_video_to_read(self):
        self.rv.run = True
        self.assertIn("Signs are being read", self.k.start_process([dict(kind="saved", id="ev1")], self.roots)["error"])
        self.rv.run = False
        self.assertIn("no connected drive", self.k.start_process([dict(kind="saved", id="ev1")], dict(A=self.roots["A"]))["error"])
        self.assertIn("not in the footage log", self.k.start_process([dict(kind="saved", id="nope")], self.roots)["error"])

    def test_a_failing_tool_is_reported_and_the_job_ends(self):
        def bad(*a, **k):
            raise OSError("drive gone")
        TS.read = bad
        self.k.start_process([dict(kind="saved", id="ev1")], self.roots)
        self.k._thread.join(5)
        self.assertFalse(self.k.job()["running"])
        self.assertIn("drive gone", self.k.job()["errors"][0])


class EventShotsTest(ProcBase):
    """A horn (Saved) or a Sentry event keeps full-size pictures of its moment; the video may not be deleted before they exist."""
    def setUp(self):
        super().setUp()
        self.ffmpeg = []

        def run(cmd, timeout=0):
            self.ffmpeg.append(cmd)
            if "%03d" in cmd[-1]:                                   # a run of pictures: one a second for -t seconds
                for i in range(int(cmd[cmd.index("-t") + 1])):
                    with open(cmd[-1] % i, "wb") as f:
                        f.write(b"jpeg")
                return 0
            with open(cmd[-1], "wb") as f:
                f.write(b"jpeg")
            return 0
        self._run = ST.run_ffmpeg
        ST.run_ffmpeg = run
        self.addCleanup(lambda: setattr(ST, "run_ffmpeg", self._run))

    def index(self, reason, cam="0"):
        with open(os.path.join(self.data, "teslacam_index_J_saved.json"), "w") as f:
            json.dump({"ev1": dict(id="ev1", ts="2026-10-16T15:41:37", reason=reason, cam=cam)}, f)

    def kept(self):
        self.store.put(S1)
        self.store.put(S2)
        self.stills.update({S1, S2})
        self.rv.done.update({S1: 0, S2: 0})

    def test_the_moment_is_the_second_inside_the_clip_that_holds_the_event(self):
        self.assertEqual(ES.moment(dict(ts="2026-10-16T15:41:37"), [S1, S2, S3]), (S2, 30))
        self.assertEqual(ES.moment(dict(ts="2026-10-16T15:40:00"), [S1, S2]), None)

    def test_which_cameras_a_horn_and_a_sentry_event_want(self):
        self.assertEqual(ES.cams_for("saved", dict(reason="user_interaction_honk", cam="0")), list(ES.CAMS))
        self.assertEqual(ES.cams_for("saved", dict(reason="user_interaction_dashcam_icon_tapped")), [])
        self.assertEqual(ES.cams_for("sentry", dict(reason="sentry_aware_object_detection", cam="5")), ["left_repeater"])
        self.assertEqual(ES.cams_for("sentry", dict(reason="sentry_accelerometer", cam="")), list(ES.CAMS))

    def test_the_pictures_are_saved_on_their_own_for_the_keeper_and_only_where_they_are_missing(self):
        self.index("user_interaction_honk")
        self.kept()
        k = self.k
        self.assertEqual(len(k.pictures_plan(self.roots)), 1)
        self.assertTrue(k.pictures_start(self.roots))
        k._thread.join(5)
        j = k.job()
        self.assertEqual((j["action"], j["step_done"], j["step_total"], j["errors"]), ("pictures", 3, 3, []))                  # two cameras of the moment and the seconds around it
        self.assertEqual(k.shots.have("saved", "ev1"), {"front", "back"})
        self.assertTrue(k.evst.info("saved", "ev1")["n"])
        self.assertEqual(k.pictures_plan(self.roots), [])
        self.assertFalse(k.pictures_start(self.roots))                                 # nothing left to save: no job

    def test_a_horn_is_not_ready_until_its_pictures_are_kept_and_then_the_video_may_go(self):
        self.index("user_interaction_honk")
        self.kept()
        k = self.k
        r = k.status(force=True)["clips"]["saved"]["ev1"]
        self.assertEqual(r["shots"], [0, 2])                                          # front and back exist in this fixture, so those two
        self.assertFalse(r["ready"])
        self.assertIn("not all kept", k.start("delete", "J", [dict(kind="saved", id="ev1")], self.roots, confirmed=True)["error"])
        k.start_process([dict(kind="saved", id="ev1")], self.roots)
        k._thread.join(5)
        self.assertEqual(k.job()["errors"], [])
        self.assertEqual(sorted(k.shots.have("saved", "ev1")), ["back", "front"])
        self.assertEqual(self.ffmpeg[0][self.ffmpeg[0].index("-ss") + 1], "30")
        self.assertIn("%s-front.mp4" % S2, self.ffmpeg[0][self.ffmpeg[0].index("-i") + 1])
        r = k.status(force=True)["clips"]["saved"]["ev1"]
        self.assertEqual((r["shots"], r["window"], r["ready"], r["shot_cams"]), ([2, 2], [1, 1], True, ["back", "front"]))

    def test_an_event_that_asks_for_no_camera_pictures_still_wants_the_seconds_around_it(self):
        self.index("user_interaction_dashcam_icon_tapped")
        self.kept()
        r = self.k.status(force=True)["clips"]["saved"]["ev1"]
        self.assertEqual((r["shots"], r["window"], r["ready"]), ([0, 0], [0, 1], False))
        self.k.start_process([dict(kind="saved", id="ev1")], self.roots)
        self.k._thread.join(5)
        r = self.k.status(force=True)["clips"]["saved"]["ev1"]
        self.assertEqual((r["window"], r["ready"], r["tier"]), ([1, 1], True, "high"))

    def test_pictures_already_kept_are_not_cut_again(self):
        self.index("user_interaction_honk")
        self.kept()
        self.k.start_process([dict(kind="saved", id="ev1")], self.roots)
        self.k._thread.join(5)
        n = len(self.ffmpeg)
        self.k.start_process([dict(kind="saved", id="ev1")], self.roots)
        self.k._thread.join(5)
        self.assertEqual(len(self.ffmpeg), n)


class StepClockTest(ProcBase):
    def test_a_step_records_when_it_started_so_the_page_can_say_how_long_is_left(self):
        before = time.time()
        self.k._job["steps"] = []
        self.k._step("Reading signs", 230)
        j = self.k.job()
        self.assertEqual((j["step"], j["step_done"], j["step_total"]), ("Reading signs", 0, 230))
        self.assertGreaterEqual(j["step_t0"], before)
        self.assertLessEqual(j["step_t0"], time.time())


class ParkedStillsTest(Base):
    def _recent(self):
        f = os.path.join(self.data, "teslacam_ledger", "2026-10-16.json")
        day = json.load(open(f))
        for st in (S1, S2):
            day["recent||%s-front" % st] = dict(size=10, drives=dict(J=dict(LIVE)))
        json.dump(day, open(f, "w"))

    def test_stills_of_a_parked_section_do_not_wait_for_a_drive_from_tessie(self):
        self._recent()
        TD.drive_windows = lambda d: [(0, 5, True)]                              # the last drive Tessie gave ended long before these minutes
        old = RC._S
        self.addCleanup(lambda: setattr(RC, "_S", old))
        RC._S = {}
        r = self.keep(read=[S1, S2], stills=[S1, S2]).status()["clips"]["recent"][S1]
        self.assertEqual((r["stills"], r["wait_n"]), ([0, 2], 2))     # driving or unread: held until the drive arrives
        RC._S = {S1: ["park", None, None, False]}
        r = self.keep(read=[S1, S2], stills=[S1, S2]).status()["clips"]["recent"][S1]
        self.assertEqual((r["stills"], r["wait_n"]), ([2, 2], 0))                       # parked: the stills count as done


    def test_stills_do_not_wait_once_a_sync_ran_after_the_minutes(self):
        self._recent()
        TD.drive_windows = lambda d: [(0, 5, True)]
        old = RC._S
        self.addCleanup(lambda: setattr(RC, "_S", old))
        RC._S = {}
        with open(os.path.join(self.data, "tessie_sync_state.json"), "w") as f:
            json.dump(dict(at=CC.utc(S2) + 3600), f)                            # Tessie was synced an hour after the clip: no drive of it is still to come
        r = self.keep(read=[S1, S2], stills=[S1, S2]).status()["clips"]["recent"][S1]
        self.assertEqual((r["stills"], r["wait_n"]), ([2, 2], 0))
        with open(os.path.join(self.data, "tessie_sync_state.json"), "w") as f:
            json.dump(dict(at=CC.utc(S2) + 60), f)                              # a minute later: the drive may not be published yet
        r = self.keep(read=[S1, S2], stills=[S1, S2]).status()["clips"]["recent"][S1]
        self.assertEqual(r["wait_n"], 2)


class PurgeFakeTC(FakeTC):
    def __init__(self, read, scanned):
        super().__init__(read, scanned)
        self.forgot = {"meta": [], "signs": []}
        self.store.forget = lambda stamps: self.forgot["meta"].extend(stamps)
        self._r = SimpleNamespace(scanned=lambda: {s: 0 for s in self.sc}, status=lambda: dict(running=False), forget=lambda stamps: self.forgot["signs"].extend(stamps),
                                  signs=lambda stamps: [], unrefined=lambda: set())

    def _rv(self):
        return self._r


class PurgeTest(Base):
    def setUp(self):
        super().setUp()
        self.stills = ST.Stills(self.data)
        for st in (S1, S2):
            os.makedirs(os.path.dirname(self.stills.path(st)), exist_ok=True)
            with open(self.stills.path(st), "wb") as f:
                f.write(b"z" * 7)
        self.fg = SimpleNamespace(stills=self.stills)
        self.fg.stills.stamps = self.stills.stamps

    def keeper(self, read=(), scanned=()):
        return CK.ClipKeep(self.data, PurgeTest.tc(read, scanned), self.fg)

    @staticmethod
    def tc(read, scanned):
        return PurgeFakeTC(set(read), set(scanned))

    def wait(self, k):
        for _ in range(200):
            if not k.job().get("running"):
                break
            time.sleep(0.02)
        return k.job()

    def test_chosen_kinds_only_are_deleted(self):
        k = self.keeper(read=[S1, S2], scanned=[S1, S2])
        out = k.purge([dict(kind="saved", id="ev1")], ["stills", "metadata"], self.roots, confirmed=True)
        self.assertNotIn("error", out)
        j = self.wait(k)
        self.assertEqual(sorted(k.tc.forgot["meta"]), [S1, S2])
        self.assertEqual(k.tc.forgot["signs"], [])
        self.assertFalse(self.stills.has(S1) or self.stills.has(S2))
        self.assertEqual(j["data_freed"], 14)
        self.assertTrue(os.path.isfile(os.path.join(self.roots["J"], "SavedClips", "ev1", "%s-front.mp4" % S1)))      # the video stays

    def test_nothing_is_deleted_without_a_confirmation(self):
        k = self.keeper()
        self.assertIn("Not confirmed", k.purge([dict(kind="saved", id="ev1")], ["stills"], self.roots)["error"])
        self.assertTrue(self.stills.has(S1))

    def test_video_that_is_not_all_kept_needs_force(self):
        k = self.keeper()
        out = k.purge([dict(kind="saved", id="ev1")], ["video"], self.roots, confirmed=True)
        self.assertTrue(out.get("needs_force"))
        self.assertTrue(os.path.isfile(os.path.join(self.roots["J"], "SavedClips", "ev1", "%s-front.mp4" % S1)))
        out = k.purge([dict(kind="saved", id="ev1")], ["video"], self.roots, confirmed=True, force=True)
        self.assertNotIn("error", out)
        self.wait(k)
        self.assertFalse(os.path.exists(os.path.join(self.roots["J"], "SavedClips", "ev1", "%s-front.mp4" % S1)))


    def test_a_clip_deleted_with_everything_leaves_the_list(self):
        import clips_purged as PG
        PG.set_file(os.path.join(self.data, "teslacam_purged.json"))
        self.addCleanup(lambda: PG.set_file(None))
        k = self.keeper()
        self.assertIn("ev1", k.status(force=True)["clips"]["saved"])
        k.purge([dict(kind="saved", id="ev1")], list(k.TYPES), self.roots, confirmed=True, force=True)
        self.wait(k)
        self.assertTrue(PG.has_event("saved", "ev1"))
        self.assertNotIn("ev1", k.status(force=True)["clips"]["saved"])
        PG.set_file(os.path.join(self.data, "teslacam_purged.json"))              # kept across a restart
        self.assertTrue(PG.has_event("saved", "ev1"))

    def test_deleting_some_kinds_keeps_the_clip_listed(self):
        import clips_purged as PG
        PG.set_file(os.path.join(self.data, "teslacam_purged.json"))
        self.addCleanup(lambda: PG.set_file(None))
        k = self.keeper()
        k.purge([dict(kind="saved", id="ev1")], ["stills", "metadata", "signs", "pictures"], self.roots, confirmed=True)
        self.wait(k)
        self.assertIn("ev1", k.status(force=True)["clips"]["saved"])

    def test_everything_at_home_is_deleted_without_force(self):
        real = CK.IGN.at
        CK.IGN.at = lambda lat, lon: True                                        # every Sentry event is at Home
        self.addCleanup(lambda: setattr(CK.IGN, "at", real))
        led = os.path.join(self.data, "teslacam_ledger", "2026-10-16.json")
        with open(led) as f:
            day = json.load(f)
        with open(led, "w") as f:
            json.dump({k.replace("saved|ev1", "sentry|ev1"): v for k, v in day.items()}, f)
        os.rename(os.path.join(self.roots["J"], "SavedClips"), os.path.join(self.roots["J"], "SentryClips"))
        k = self.keeper()
        k._events = lambda: {("sentry", "ev1"): dict(id="ev1", lat=-33.8, lon=151.1)}
        r = k.status(force=True)["clips"]["sentry"]["ev1"]
        self.assertTrue(r["ignored"])
        self.assertEqual((r["meta"], r["stills"], r["signs"], r["ready"], r["todo"]), ([0, 0], [0, 0], [0, 0], True, False))
        self.assertTrue(CK.IGN.has(S1))
        out = k.purge([dict(kind="sentry", id="ev1")], list(k.TYPES), self.roots, confirmed=True)
        self.assertNotIn("error", out)
        self.wait(k)
        self.assertFalse(os.path.exists(os.path.join(self.roots["J"], "SentryClips", "ev1", "%s-front.mp4" % S1)))
        self.assertFalse(self.stills.has(S1))


class StaleStatusTest(Base):
    def test_an_old_answer_is_returned_at_once_and_rebuilt_behind(self):
        k = self.keep()
        first = k.status()
        calls = []
        real = k._build
        k._build = lambda: (calls.append(1), real())[1]
        k._cache = (time.time() - 60, first)                                    # older than the time an answer is reused
        self.assertIs(k.status(), first)                                        # no waiting for the build
        for _ in range(100):
            if k._cache[0] > time.time() - 5 and not k._building:
                break
            time.sleep(0.05)
        self.assertEqual(len(calls), 1)
        self.assertIsNot(k.status(), first)                                     # the rebuilt one
        k._cache = None
        self.assertIsNot(k.status(), first)                                     # after a job the caller waits for a fresh one


    def test_a_build_that_began_before_a_change_is_not_kept(self):
        k = self.keep()
        real = k._build
        calls = []

        def slow():
            calls.append(1)
            res = real()
            if len(calls) == 1:
                k.invalidate()                                                  # a delete finished while this build was reading
            return res
        k._build = slow
        k.status(force=True)
        self.assertEqual(len(calls), 2)                                         # it was built again, so what is kept is not the state before the change


if __name__ == "__main__":
    unittest.main()
