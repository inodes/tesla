#!/usr/bin/env python3
"""test_pdf_text_cache.py - an invoice PDF is parsed once (lib/pdf_text_cache.py), so a Charges sync only reads the new or changed ones.
    TESLA_CONFIG=config.example.json TESLA_DATA_DIR=/tmp/nodata python3 -m unittest tests/test_pdf_text_cache.py
"""
import importlib
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["TESLA_DATA_DIR"] = self.tmp.name
        import pdf_text_cache as PC
        self.PC = importlib.reload(PC)
        self.pdf = os.path.join(self.tmp.name, "a.pdf")
        with open(self.pdf, "wb") as f:
            f.write(b"%PDF-1")
        self.calls = []

    def reader(self, p):
        self.calls.append(p)
        return "Total Amount 23.35"

    def test_read_once_then_from_the_cache(self):
        self.assertEqual(self.PC.text(self.pdf, self.reader), "Total Amount 23.35")
        self.assertEqual(self.PC.text(self.pdf, self.reader), "Total Amount 23.35")
        self.assertEqual(len(self.calls), 1)

    def test_kept_on_disk_for_the_next_run(self):
        self.PC.text(self.pdf, self.reader)
        self.PC.save()
        self.PC = importlib.reload(self.PC)
        self.PC.text(self.pdf, self.reader)
        self.assertEqual(len(self.calls), 1)

    def test_a_changed_file_is_read_again(self):
        self.PC.text(self.pdf, self.reader)
        with open(self.pdf, "wb") as f:
            f.write(b"%PDF-1 changed")
        self.PC.text(self.pdf, self.reader)
        self.assertEqual(len(self.calls), 2)

    def test_an_empty_read_is_not_kept_and_forget_rereads(self):
        self.assertEqual(self.PC.text(self.pdf, lambda p: ""), "")
        self.PC.text(self.pdf, self.reader)
        self.PC.text(self.pdf, self.reader)
        self.assertEqual(len(self.calls), 1)
        self.PC.forget(self.pdf)                         # a session re-checked by hand
        self.PC.text(self.pdf, self.reader)
        self.assertEqual(len(self.calls), 2)


if __name__ == "__main__":
    unittest.main()
