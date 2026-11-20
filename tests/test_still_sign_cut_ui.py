"""The signs shown over a still in the stills viewer are cut out of the whole frame with sgCut (a picture kept as a whole frame, ..._f02.jpg, is 2896 px wide): shown
as a plain <img> it filled the card with the whole street instead of the sign."""
import os, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8") as f:
    JS = f.read()
with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.css"), encoding="utf-8") as f:
    CSS = f.read()


class StillSignCutTest(unittest.TestCase):
    def test_the_big_sign_is_a_cut(self):
        i = JS.index("function stillViewer")
        blk = JS[i:i + 12000]
        self.assertIn('${sgCut(g.f,g.pos,"Speed limit "+g.text)}<figcaption>', blk)
        self.assertNotIn('<img src="/review/${encodeURIComponent(g.f)}" alt="Speed limit', blk)

    def test_the_cut_has_a_size_inside_the_card(self):
        self.assertIn(".sgbig .sgcut{width:min(260px,26vw)", CSS)


if __name__ == "__main__":
    unittest.main()
