"""stills_thin: which minutes may have smaller stills, and re-encoding a clip's zip."""
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("tests", "Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import clip_clock as CC  # noqa: E402
import stills_thin as TH  # noqa: E402
import teslacam_ledger as TLG  # noqa: E402
import teslacam_stills as ST  # noqa: E402

LIVE = dict(first="2026-10-17T00:00:00Z", last="2026-10-17T00:00:00Z", gone=None)
A, B, C, D, E, F = ["2026-10-16_15-%02d-07" % m for m in (40, 41, 42, 43, 44, 45)]


def fake_ffmpeg(cmd, timeout=0):
    """Halves every picture: reads i000.jpg ... and writes o000.jpg ..."""
    src = cmd[cmd.index("-i") + 1]
    d = os.path.dirname(src)
    for fn in sorted(os.listdir(d)):
        if fn.startswith("i"):
            with open(os.path.join(d, fn), "rb") as f:
                b = f.read()
            with open(os.path.join(d, "o" + fn[1:]), "wb") as f:
                f.write(b[:len(b) // 2])
    return 0


class Store:
    def __init__(self, d):
        self.d = d

    def summary(self, st):
        return self.d.get(st)


class ThinTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.data = os.path.join(self.t, "data")
        CC.configure(self.data)
        self.stills = ST.Stills(self.data)
        for st in (A, B, C, D, E, F):
            os.makedirs(os.path.dirname(self.stills.path(st)), exist_ok=True)
            with zipfile.ZipFile(self.stills.path(st), "w", zipfile.ZIP_STORED) as z:
                for i in range(3):
                    z.writestr("%02d.jpg" % i, b"x" * 1000)
        led = os.path.join(self.data, "teslacam_ledger")
        os.makedirs(led)
        with open(os.path.join(led, "2026-10-16.json"), "w") as f:
            json.dump({"saved|ev1|%s-front" % B: dict(size=1, drives=dict(J=dict(LIVE)))}, f)
        # A: slow, no sign     B: in an event     C: fast, signs found     D: fast, scanned, no sign     E: fast, not scanned yet     F: nothing read
        self.store = Store({A: dict(vmax=10), B: dict(vmax=10), C: dict(vmax=60), D: dict(vmax=60), E: dict(vmax=60)})
        self.scanned = {C: 2, D: 0}
        tc = SimpleNamespace(_tl=lambda r: self.store, _rv=lambda: SimpleNamespace(scanned=lambda: dict(self.scanned)))
        self.th = TH.StillsThin(self.data, self.stills, tc)
        self._run = ST.run_ffmpeg
        ST.run_ffmpeg = fake_ffmpeg
        self.addCleanup(lambda: setattr(ST, "run_ffmpeg", self._run))

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def test_only_minutes_with_nothing_taken_from_them_are_candidates(self):
        self.assertEqual(self.th.candidates(), [D, A])                             # newest first; not the event, the sign, the unread, the unscanned

    def test_the_job_makes_them_smaller_remembers_it_and_leaves_the_others_alone(self):
        self.th.start()
        self.th._thread.join(5)
        j = self.th.status(force=True)
        self.assertEqual((j["done"], j["errors"], j["minutes"]), (2, [], 0))
        self.assertEqual(j["saved"], 3000)
        with zipfile.ZipFile(self.stills.path(A)) as z:
            self.assertEqual((z.namelist(), len(z.read("00.jpg"))), (["00.jpg", "01.jpg", "02.jpg"], 500))
        with zipfile.ZipFile(self.stills.path(B)) as z:
            self.assertEqual(len(z.read("00.jpg")), 1000)
        with open(os.path.join(self.stills.root, "thinned.json")) as f:
            self.assertEqual(sorted(json.load(f)), [A, D])
        self.th.start()                                                            # nothing left to do
        self.th._thread.join(5)
        self.assertEqual(self.th.status(force=True)["total"], 0)

    def test_a_clip_that_would_not_get_clearly_smaller_is_left_as_it_is(self):
        def same(cmd, timeout=0):
            d = os.path.dirname(cmd[cmd.index("-i") + 1])
            for fn in sorted(os.listdir(d)):
                if fn.startswith("i"):
                    shutil.copyfile(os.path.join(d, fn), os.path.join(d, "o" + fn[1:]))
            return 0
        ST.run_ffmpeg = same
        size = os.path.getsize(self.stills.path(A))
        self.assertEqual(TH.thin_zip(self.stills.path(A), 240, 10), (size, size))
        self.assertEqual([f for f in os.listdir(os.path.dirname(self.stills.path(A))) if f.endswith(".thin")], [])
        with zipfile.ZipFile(self.stills.path(A), "w", zipfile.ZIP_STORED):
            pass
        size = os.path.getsize(self.stills.path(A))
        self.assertEqual(TH.thin_zip(self.stills.path(A), 240, 10), (size, size))              # an empty zip is left alone

    def test_a_failing_ffmpeg_is_reported_and_leaves_the_zip_unchanged(self):
        ST.run_ffmpeg = lambda cmd, timeout=0: 1
        self.th.start()
        self.th._thread.join(5)
        j = self.th.status(force=True)
        self.assertEqual(len(j["errors"]), 2)
        with zipfile.ZipFile(self.stills.path(A)) as z:
            self.assertEqual(len(z.read("00.jpg")), 1000)
        self.assertEqual(self.th.candidates(), [D, A])
        self.assertEqual([f for f in os.listdir(os.path.dirname(self.stills.path(A))) if f.endswith(".thin")], [])


if __name__ == "__main__":
    unittest.main()
