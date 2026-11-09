"""The Charges page must never wait on "Reconciling the charges with their invoices..." when a book was kept before: any kept book is served at once (whatever version wrote it) and a
different version only makes it stale, to be rebuilt behind it."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_charges as AC  # noqa: E402


class KeptBookTest(unittest.TestCase):
    def kept(self, version):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "charges"))
        with open(os.path.join(d, "charges", "app_chargebook.json"), "w") as f:
            json.dump(dict(version=version, sig="old", built=1, invoice_dirs=[], plugs=[{"id": "1", "parts": []}], invoices=[]), f)
        return AC.ChargeBook(d, d)

    def test_a_book_of_another_version_is_shown_at_once(self):
        for v in (AC.VERSION, AC.VERSION + 1, 0):
            b = self.kept(v)
            b.fail_t = 1e12                                 # no rebuild thread started: only what is served
            r = b.get()
            self.assertIsNotNone(r["book"], "version %s" % v)
            self.assertEqual(len(r["book"]["plugs"]), 1)
            self.assertNotEqual(r["status"], "error")

    def test_no_kept_book_is_the_only_wait(self):
        d = tempfile.mkdtemp()
        b = AC.ChargeBook(d, d)
        b.fail_t = 1e12
        self.assertIsNone(b.get()["book"])


if __name__ == "__main__":
    unittest.main()
