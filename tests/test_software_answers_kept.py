"""app_software keeps its answers while the held files, the car's versions and the settings are unchanged (_remembered)."""
import os
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
from lib import app_software as A  # noqa: E402
from lib.software import index as I  # noqa: E402


class AnswersKeptTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.ix = types.SimpleNamespace(path=os.path.join(self.d, "ix.sqlite"), cfg={"regions": {"ANZ": ["au"]}})
        self.saved = A._index
        A._index = lambda data_dir: self.ix
        A._ANS.clear()
        I.Index.SIG[self.ix.path] = "sig1"
        self.calls = []

        @A._remembered("t")
        def ask(data_dir, region):
            self.calls.append(region)
            return {"region": region, "n": len(self.calls)}
        self.ask = ask

    def tearDown(self):
        A._index = self.saved
        A._ANS.clear()
        I.Index.SIG.pop(self.ix.path, None)

    def test_the_same_question_is_answered_once(self):
        self.assertEqual(self.ask(self.d, "ANZ"), self.ask(self.d, "ANZ"))
        self.ask(self.d, "Europe")
        self.assertEqual(self.calls, ["ANZ", "Europe"])

    def test_new_held_files_or_settings_work_it_out_again(self):
        self.ask(self.d, "ANZ")
        I.Index.SIG[self.ix.path] = "sig2"                  # a sync brought new versions
        self.ask(self.d, "ANZ")
        self.ix.cfg = {"regions": {"ANZ": ["au", "nz"]}}
        self.ask(self.d, "ANZ")
        self.assertEqual(len(self.calls), 3)

    def test_the_car_watch_moving_last_seen_keeps_the_answers(self):
        import json
        import time
        from lib import car_versions
        p = car_versions._path(self.d)

        def save(last, current="2026.38.5.5"):
            with open(p, "w") as f:
                json.dump({"current": current, "versions": [{"version": "2026.38.5.5", "first_seen": "2026-11-01", "last_seen": last}]}, f)
            t = time.time() + len(self.calls) + 1
            os.utime(p, (t, t))
        save("2026-11-22T10:00:00Z")
        self.ask(self.d, "ANZ")
        save("2026-11-22T10:05:00Z")                                   # a look by the car watch
        self.ask(self.d, "ANZ")
        self.assertEqual(len(self.calls), 1)
        save("2026-11-22T10:10:00Z", current="2026.44.1")              # the car took a new version
        self.ask(self.d, "ANZ")
        self.assertEqual(len(self.calls), 2)


if __name__ == "__main__":
    unittest.main()
