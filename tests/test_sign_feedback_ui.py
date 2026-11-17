"""The sign cards carry the answer box (static checks on app.js / app.css: the browser itself is not run here)."""
import os, re, shutil, subprocess, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "..", "Tools", "lib", "app_ui")
JS = open(os.path.join(UI, "app.js"), encoding="utf-8").read()
CSS = open(os.path.join(UI, "app.css"), encoding="utf-8").read()


class UiTest(unittest.TestCase):
    def test_reasons_and_endpoint_are_there(self):
        for t in ("/api/signfeedback", "Wrong speed", "Right speed, it is a school zone", "Right speed, not a school zone", "Not a speed sign", "&dismissed=1", "removed as not speed signs", "Put back"):
            self.assertIn(t, JS)

    def test_the_list_collapses_from_its_heading(self):
        self.assertIn('id="dsgt"', JS)
        self.assertIn("setGs(SGSZ===\"h\"?\"n\":\"h\")", JS)

    def test_a_school_zone_card_states_hours_and_school_day(self):
        for t in ("standard hours assumed", "not a school day", "/api/schoolday?date=", "SGSTD"):
            self.assertIn(t, JS)

    def test_the_box_is_styled_and_does_not_open_the_video(self):
        self.assertIn(".evc span.sgx", CSS)
        self.assertIn("e.stopPropagation()", JS[JS.index("data-sgx"):JS.index("data-sgx") + 4000] if "data-sgx" in JS else "")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_app_js_parses(self):
        r = subprocess.run(["node", "--check", os.path.join(UI, "app.js")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])


if __name__ == "__main__":
    unittest.main()
