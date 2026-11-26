"""Things the code scanner flagged: files only inside their folder (safe_join), no line break in a response header (_hv), a site judged by its host name (url_host)."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import tesla_app as A  # noqa: E402
import url_host as U  # noqa: E402


class SafeJoinTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.out = os.path.realpath(tempfile.mkdtemp())
        os.makedirs(os.path.join(self.root, "a"))
        open(os.path.join(self.root, "a", "f.txt"), "w").close()
        open(os.path.join(self.out, "secret.txt"), "w").close()
        os.symlink(self.out, os.path.join(self.root, "link"))

    def test_a_file_inside_is_found(self):
        self.assertEqual(A.safe_join(self.root, "a/f.txt"), os.path.join(self.root, "a", "f.txt"))
        self.assertEqual(A.safe_join(self.root, ""), self.root)

    def test_nothing_outside_is_reached(self):
        for rel in ("../x", "a/../../x", "/etc/passwd", "link/secret.txt", "a/../../" + os.path.basename(self.out) + "/secret.txt"):
            self.assertIsNone(A.safe_join(self.root, rel), rel)

    def test_a_sibling_folder_with_the_same_start_is_outside(self):
        sib = self.root + "-other"
        os.makedirs(sib, exist_ok=True)
        self.assertIsNone(A.safe_join(self.root, "../" + os.path.basename(sib)))


class HeaderTest(unittest.TestCase):
    def test_no_line_break_survives(self):
        self.assertEqual(A._hv("text/plain\r\nSet-Cookie: x=1"), "text/plainSet-Cookie: x=1")
        self.assertEqual(A._hv("a\nb"), "ab")
        self.assertEqual(A._hv(5), "5")


class UrlHostTest(unittest.TestCase):
    def test_the_host_decides(self):
        self.assertTrue(U.on_host("https://www.tesla.com/findus?id=1", "tesla.com"))
        self.assertTrue(U.on_host("tesla.com/findus", "tesla.com"))
        self.assertTrue(U.on_host("https://bppulse.com/x", "bp.com", "bppulse.com"))
        self.assertFalse(U.on_host("https://evil.example/tesla.com", "tesla.com"))
        self.assertFalse(U.on_host("https://tesla.com.evil.example/", "tesla.com"))
        self.assertFalse(U.on_host("https://nottesla.com/", "tesla.com"))
        self.assertFalse(U.on_host("", "tesla.com"))
        self.assertFalse(U.on_host(None, "tesla.com"))
        self.assertEqual(U.host_of("HTTPS://User@WWW.Tesla.com:8443/x"), "www.tesla.com")


if __name__ == "__main__":
    unittest.main()
