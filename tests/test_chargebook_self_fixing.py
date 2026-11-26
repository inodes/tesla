"""The charge book fixes itself: an invoice PDF that still has the name it was downloaded with gets its name (never over another file, only when it is not named yet), each rename is
logged and the book carries the new name; a COST MISMATCH that Tessie has since corrected (it holds the invoice's cost) reads MATCHED."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_charges as AC  # noqa: E402

RAW = "tesla_invoice8deaa2ed-91c3-44dd-a56a-fe44e59dda7e.pdf"
NEW = "20261126_2240_Tesla_2010P0005495478_Leichhardt_NSW.pdf"


def touch(path, text="x"):
    with open(path, "w") as f:
        f.write(text)


class RenameUnnamedTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp()
        self.inv = tempfile.mkdtemp()
        self.cb = AC.ChargeBook(self.data, self.data)

    def book(self, renames):
        return dict(plugs=[dict(invoice=dict(file=RAW, number="2010P0005495478"))], invoices=[dict(file=RAW)], renames=renames)

    def plan(self, old=RAW, new=NEW):
        return [dict(dir=self.inv, old=old, new=new)]

    def test_an_unnamed_invoice_is_renamed_and_the_book_and_log_follow(self):
        touch(os.path.join(self.inv, RAW))
        b = self.book(self.plan())
        done = self.cb.rename_unnamed(b)
        self.assertEqual([x["new"] for x in done], [NEW])
        self.assertEqual(os.listdir(self.inv), [NEW])
        self.assertEqual(b["plugs"][0]["invoice"]["file"], NEW)
        self.assertEqual(b["invoices"][0]["file"], NEW)
        self.assertNotIn("renames", b)
        with open(os.path.join(self.data, "invoice_rename_log.jsonl")) as f:
            rows = [json.loads(x) for x in f]
        self.assertEqual([(r["old"], r["new"]) for r in rows], [(RAW, NEW)])

    def test_nothing_is_renamed_over_a_file_that_is_there(self):
        touch(os.path.join(self.inv, RAW), "raw")
        touch(os.path.join(self.inv, NEW), "other")
        b = self.book(self.plan())
        self.assertEqual(self.cb.rename_unnamed(b), [])
        self.assertEqual(sorted(os.listdir(self.inv)), sorted([RAW, NEW]))
        with open(os.path.join(self.inv, NEW)) as f:
            self.assertEqual(f.read(), "other")
        self.assertEqual(b["plugs"][0]["invoice"]["file"], RAW)

    def test_a_file_that_has_its_name_is_left_alone(self):
        named = "20261022_2251_Tesla_2010P0005205224_Leichhardt_NSW.pdf"
        touch(os.path.join(self.inv, named))
        self.assertEqual(self.cb.rename_unnamed(self.book(self.plan(named, "20261022_2251_Tesla_2010P0005205224_Leichhardt.pdf"))), [])
        self.assertEqual(os.listdir(self.inv), [named])

    def test_names_that_leave_the_folder_or_are_not_pdfs_are_refused(self):
        touch(os.path.join(self.inv, RAW))
        for new in ("../x.pdf", "sub/x.pdf", "x.txt"):
            self.assertEqual(self.cb.rename_unnamed(self.book(self.plan(new=new))), [])
        self.assertEqual(os.listdir(self.inv), [RAW])

    def test_a_missing_file_is_skipped(self):
        self.assertEqual(self.cb.rename_unnamed(self.book(self.plan())), [])

    def test_the_name_pattern(self):
        self.assertTrue(AC.INVOICE_NAMED.match(NEW))
        self.assertTrue(AC.INVOICE_NAMED.match("20260530_1614_Exploren_0000533887_Bunnings_Rydalmere_NSW.pdf"))
        self.assertFalse(AC.INVOICE_NAMED.match(RAW))
        self.assertFalse(AC.INVOICE_NAMED.match("invoice.pdf"))


class SelfFixingCostMismatchTest(unittest.TestCase):
    def test_the_status_logic_is_in_the_build(self):
        src = open(os.path.join(HERE, "..", "Tools", "lib", "app_charges.py"), encoding="utf-8").read()
        self.assertIn('"COST MISMATCH" in plug["status"] and plug["sync"] == "synced" and plug["icost"] is not None', src)
        self.assertIn('plug["status"] = "MATCHED ✅"', src)


if __name__ == "__main__":
    unittest.main()
