#!/usr/bin/env python3
"""test_clip_clock_incremental.py - the clips' own recording times are read from the provenance files one file at a time: a file not changed since it was
last read is not read again, a changed, new or removed file is, and the result is the same as reading every file.

    python3 -m unittest tests/test_clip_clock_incremental.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
import clip_clock as CC  # noqa: E402


def rec(created, name_stamp, street="", suburb=""):
    return [dict(created=created, modified=created + 60, name_stamp=name_stamp, street=street, suburb=suburb)]


class Incremental(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "teslacam_provenance")
        os.makedirs(self.p)
        CC._PF.clear()
        self.reads = []
        self.real = CC._read_one
        CC._read_one = lambda f, key: (self.reads.append(os.path.basename(f)), self.real(f, key))[1]

    def tearDown(self):
        CC._read_one = self.real
        CC._PF.clear()
        shutil.rmtree(self.d)

    def write(self, day, j):
        f = os.path.join(self.p, day + ".json")
        with open(f, "w") as fh:
            json.dump(j, fh)
        st = os.stat(f)
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + len(self.reads) * 1000 + 1))

    def keys(self):
        return {f: (os.stat(f).st_mtime_ns, os.stat(f).st_size) for f in CC._prov_files(self.d)}

    def full(self):
        CC._read_one = self.real
        CC._PF.clear()
        durs, where = {}, {}
        out = CC._read_skews(self.d, durs, where)
        CC._PF.clear()
        return out, durs, where

    def read(self):
        durs, where = {}, {}
        return CC._read_skews(self.d, durs, where, self.keys()), durs, where

    def test_only_a_changed_file_is_read_again_and_the_result_is_a_full_read(self):
        self.write("2026-10-01", {"recent||2026-10-01_10-00-00-front": rec(1000.0, 1000.0 + 36000 + 30, "A St", "Ryde")})
        self.write("2026-10-02", {"recent||2026-10-02_10-00-00-front": rec(2000.0, 2000.0 + 36000)})
        first = self.read()
        self.assertEqual(sorted(self.reads), ["2026-10-01.json", "2026-10-02.json"])
        self.reads.clear()
        self.assertEqual(self.read(), first)
        self.assertEqual(self.reads, [])                                         # nothing changed: nothing read
        self.write("2026-10-02", {"recent||2026-10-02_10-00-00-front": rec(2000.0, 2000.0 + 36000 + 45)})
        self.reads.clear()
        got = self.read()
        self.assertEqual(self.reads, ["2026-10-02.json"])
        self.assertEqual(got, self.full())
        self.assertIn("2026-10-02_10-00-00", got[0])

    def test_new_and_removed_files(self):
        self.write("2026-10-01", {"recent||2026-10-01_10-00-00-front": rec(1000.0, 1000.0 + 36000 + 30)})
        self.read()
        self.write("2026-10-03", {"recent||2026-10-03_10-00-00-front": rec(3000.0, 3000.0 + 36000 + 30)})
        self.assertIn("2026-10-03_10-00-00", self.read()[0])
        os.remove(os.path.join(self.p, "2026-10-01.json"))
        got = self.read()
        self.assertNotIn("2026-10-01_10-00-00", got[0])
        self.assertEqual(got, self.full())

    def test_the_first_file_naming_a_place_wins_as_before(self):
        self.write("2026-10-01", {"saved|e|2026-10-02_10-00-00-front": rec(2000.0, 2000.0 + 36000, "First St", "")})
        self.write("2026-10-02", {"recent||2026-10-02_10-00-00-front": rec(2000.0, 2000.0 + 36000, "Second St", "")})
        self.assertEqual(self.read()[2]["2026-10-02_10-00-00"], ("First St", ""))


if __name__ == "__main__":
    unittest.main()
