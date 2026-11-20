"""The list of files already on the destination (a move that finds them) scrolls inside its own box: the bar is sticky, so a list taller than the window left no way to scroll the page."""
import os, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "..", "Tools", "lib", "app_ui")
JS = open(os.path.join(UI, "app.js"), encoding="utf-8").read()
CSS = open(os.path.join(UI, "app.css"), encoding="utf-8").read()


class ConflictScrollTest(unittest.TestCase):
    def test_the_table_is_in_a_box_with_a_height_limit(self):
        self.assertIn('<div class="cfscroll"><table class="kv"', JS)
        self.assertIn("</table></div></details>", JS)
        self.assertRegex(CSS, r"\.cfscroll\{[^}]*max-height:\d+vh[^}]*overflow:auto")

    def test_the_bar_itself_does_not_clip_its_menus(self):
        import re
        rule = re.search(r"#selbar\{([^}]*)\}", CSS).group(1)
        self.assertNotIn("overflow", rule)


if __name__ == "__main__":
    unittest.main()
