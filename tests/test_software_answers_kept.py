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


if __name__ == "__main__":
    unittest.main()
