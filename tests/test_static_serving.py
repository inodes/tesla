#!/usr/bin/env python3
"""test_static_serving.py - the page's own files: MapLibre from lib/app_ui/vendor with a long cache (the CDN only when the copy is missing), and nothing
in index.html's <head> that holds the first paint.

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
