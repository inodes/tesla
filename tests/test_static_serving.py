#!/usr/bin/env python3
"""test_static_serving.py - the page's own files: MapLibre from lib/app_ui/vendor with a long cache (the CDN only when the copy is missing), nothing
in index.html's <head> that holds the first paint, and ETag / 304 / gzip for index.html, app.js, app.css (a changed file is sent again).

    python3 -m unittest tests/test_static_serving.py
"""
import http.client
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import tesla_app  # noqa: E402


class Served(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = tesla_app.Server(("127.0.0.1", 0), tesla_app.H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def get(self, path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        c.request("GET", path, headers=headers or {})
        r = c.getresponse()
        body = r.read()
        c.close()
        return r, body


class Vendor(Served):
    def test_maplibre_is_served_from_the_local_copy_with_a_long_cache(self):
        for name in ("maplibre-gl.js", "maplibre-gl.css"):
            r, body = self.get("/vendor/" + name)
            self.assertEqual(r.status, 200)
            self.assertIn("immutable", r.getheader("Cache-Control"))
            with open(os.path.join(tesla_app.UI, "vendor", name), "rb") as f:
                self.assertEqual(body, f.read())

    def test_a_missing_copy_goes_to_the_pinned_cdn_file(self):
        ui, tmp = tesla_app.UI, tempfile.mkdtemp()
        try:
            tesla_app.UI = tmp
            r, _ = self.get("/vendor/maplibre-gl.js")
            self.assertEqual(r.status, 302)
            self.assertEqual(r.getheader("Location"), "https://cdnjs.cloudflare.com/ajax/libs/maplibre-gl/4.7.1/maplibre-gl.js")
        finally:
            tesla_app.UI = ui
            shutil.rmtree(tmp)

    def test_other_names_are_not_served(self):
        self.assertEqual(self.get("/vendor/../tesla_app.py")[0].status, 404)
        self.assertEqual(self.get("/vendor/other.js")[0].status, 404)


class Revalidate(Served):
    def test_app_files_carry_an_etag_and_a_304_when_unchanged(self):
        for path in ("/", "/app.js", "/app.css", "/vendor/maplibre-gl.js"):
            r, body = self.get(path)
            self.assertEqual(r.status, 200)
            tag = r.getheader("ETag")
            self.assertTrue(tag)
            r2, body2 = self.get(path, {"If-None-Match": tag})
            self.assertEqual((r2.status, body2), (304, b""))

    def test_app_js_is_asked_again_every_time(self):
        self.assertEqual(self.get("/app.js")[0].getheader("Cache-Control"), "no-cache")

    def test_gzip_when_accepted_and_the_same_bytes_unzipped(self):
        import gzip
        raw = self.get("/app.js")[1]
        r, z = self.get("/app.js", {"Accept-Encoding": "gzip, deflate"})
        self.assertEqual(r.getheader("Content-Encoding"), "gzip")
        self.assertEqual(int(r.getheader("Content-Length")), len(z))
        self.assertLess(len(z), len(raw) / 2)
        self.assertEqual(gzip.decompress(z), raw)
        self.assertIsNone(self.get("/app.js")[0].getheader("Content-Encoding"))

    def test_a_changed_file_gets_a_new_etag_and_its_new_content(self):
        ui, tmp = tesla_app.UI, tempfile.mkdtemp()
        try:
            tesla_app.UI = tmp
            p = os.path.join(tmp, "app.css")
            with open(p, "w") as f:
                f.write("a{}")
            r, b = self.get("/app.css")
            tag = r.getheader("ETag")
            with open(p, "w") as f:
                f.write("b{color:red}")
            os.utime(p, ns=(os.stat(p).st_atime_ns, os.stat(p).st_mtime_ns + 10 ** 9))
            r2, b2 = self.get("/app.css", {"If-None-Match": tag})
            self.assertEqual((r2.status, b2), (200, b"b{color:red}"))
            self.assertNotEqual(r2.getheader("ETag"), tag)
        finally:
            tesla_app.UI = ui
            shutil.rmtree(tmp)


class Json(Served):
    """The /api answers: ETag, 304 when unchanged, gzip when accepted; a stale kept list never matches a fresh one."""

    def setUp(self):
        self.kept, self.lists = tesla_app.KEPT, dict(tesla_app.KEPT_LISTS)
        self.state = {"stale": False, "body": {"rows": list(range(2000))}}
        st = self.state

        class K:
            def get(self, name, build, fresh=False):
                import json
                return json.dumps(st["body"]).encode(), st["stale"]
        tesla_app.KEPT = K()
        tesla_app.KEPT_LISTS["activity"] = lambda: None

    def tearDown(self):
        tesla_app.KEPT = self.kept
        tesla_app.KEPT_LISTS.clear()
        tesla_app.KEPT_LISTS.update(self.lists)

    def test_unchanged_list_is_a_304_and_a_changed_one_is_sent(self):
        r, b = self.get("/api/activity")
        tag = r.getheader("ETag")
        self.assertEqual(self.get("/api/activity", {"If-None-Match": tag})[0].status, 304)
        self.state["body"] = {"rows": [1]}
        r2, b2 = self.get("/api/activity", {"If-None-Match": tag})
        self.assertEqual((r2.status, b2), (200, b'{"rows": [1]}'))

    def test_stale_and_fresh_never_share_a_tag(self):
        self.state["stale"] = True
        r, _ = self.get("/api/activity")
        self.assertEqual(r.getheader("X-Stale"), "1")
        self.state["stale"] = False
        r2, _ = self.get("/api/activity", {"If-None-Match": r.getheader("ETag")})
        self.assertEqual(r2.status, 200)
        self.assertIsNone(r2.getheader("X-Stale"))

    def test_gzip_for_a_big_list(self):
        import gzip
        import json
        r, z = self.get("/api/activity", {"Accept-Encoding": "gzip"})
        self.assertEqual(r.getheader("Content-Encoding"), "gzip")
        self.assertEqual(json.loads(gzip.decompress(z)), self.state["body"])

    def test_errors_are_not_kept(self):
        r, _ = self.get("/api/nothing-here")
        self.assertEqual(r.status, 404)
        self.assertIsNone(r.getheader("ETag"))


class Head(unittest.TestCase):
    def test_nothing_in_head_holds_the_first_paint_but_the_app_css(self):
        with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "index.html"), encoding="utf-8") as f:
            head = f.read().split("</head>")[0]
        self.assertNotIn("cdnjs", head)
        for tag in re.findall(r"<script[^>]*>", head):
            self.assertRegex(tag, r"\b(defer|async)\b")
        blocking = [t for t in re.findall(r"<link[^>]*rel=\"stylesheet\"[^>]*>", head) if "media=\"print\"" not in t]
        self.assertEqual(len(blocking), 1)
        self.assertIn("/app.css", blocking[0])

    def test_maplibre_runs_before_the_app(self):
        with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "index.html"), encoding="utf-8") as f:
            s = f.read()
        self.assertLess(s.index("/vendor/maplibre-gl.js"), s.index("/app.js"))


if __name__ == "__main__":
    unittest.main()
