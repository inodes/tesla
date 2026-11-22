"""The app builds the charge book in a process of its own (ChargeBook._build_apart): the request threads never wait behind it."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_charges as AC  # noqa: E402


class BuiltApartTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.b = AC.ChargeBook(os.path.join(HERE, "..", "Tools"), self.d)
        self.b.separate = True
        self.saved = subprocess.run

    def tearDown(self):
        subprocess.run = self.saved

    def test_the_childs_book_is_kept_and_served(self):
        seen = []

        def fake(cmd, **kw):
            seen.append(cmd)
            with open(cmd[-1], "w") as f:
                json.dump({"version": AC.VERSION, "sig": cmd[-2], "plugs": [{"id": "1"}], "invoices": []}, f)
            return SimpleNamespace(returncode=0, stderr=b"")
        subprocess.run = fake
        self.b._build_bg("sig-1")
        self.assertEqual(self.b.book["plugs"], [{"id": "1"}])
        cmd = seen[0][2:] if seen[0][:2] == ["taskpolicy", "-b"] else seen[0]
        self.assertEqual(cmd[:3], [sys.executable, os.path.abspath(AC.__file__), "--build"])
        with open(self.b.cache_path) as f:
            self.assertEqual(json.load(f)["sig"], "sig-1")
        self.assertFalse(os.path.exists(seen[0][-1]))            # the hand-over file is removed

    def test_a_failed_child_keeps_the_older_book(self):
        self.b.book = {"plugs": [{"id": "old"}]}
        subprocess.run = lambda cmd, **kw: SimpleNamespace(returncode=1, stderr=b"Traceback\nValueError: bad pdf")
        self.b._build_bg("sig-2")
        self.assertEqual(self.b.book["plugs"], [{"id": "old"}])
        self.assertIn("bad pdf", self.b.err)

    def test_the_command_line_needs_its_arguments(self):
        self.assertEqual(AC._main(["--build"]), 2)


if __name__ == "__main__":
    unittest.main()


class PvRetryTest(unittest.TestCase):
    """A charge of today: its solar readings are fetched once in PV_RETRY_S, not at every look at its page."""
    def test_fetched_once_in_a_while(self):
        import datetime
        import sqlite3
        d = tempfile.mkdtemp()
        db = os.path.join(d, "pv_history.db")
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE pv_intraday (timestamp TEXT PRIMARY KEY, production_kwh REAL)")
        c.commit()
        c.close()
        b = AC.ChargeBook(os.path.join(HERE, "..", "Tools"), d)
        asked = []
        b._pv_provider = lambda: asked.append(1)                 # None: nothing fetched, but asked
        today = datetime.date.today()
        b._refresh_pv(db, today, today)
        b._refresh_pv(db, today, today)
        self.assertEqual(len(asked), 1)
        b._pv_tried[(today, today)] = 0                          # the wait is over
        b._refresh_pv(db, today, today)
        self.assertEqual(len(asked), 2)
