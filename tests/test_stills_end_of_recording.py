"""A clip the car stopped recording part-way (Sentry stops when the owner unlocks the car) has fewer than 60 stills: the status says how many and the stills viewer
stops there with a note, instead of stepping on to pictures that do not exist (the blue ?)."""
import os, sys, tempfile, unittest, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Tools"))
sys.path.insert(0, os.path.join(HERE, "..", "Tools", "lib"))
JS = open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8").read()


class EndOfRecordingTest(unittest.TestCase):
    def test_status_counts_the_stills_of_a_short_clip(self):
        import app_footage
        d = tempfile.mkdtemp()
        st = "2026-06-16_15-45-00"
        p = os.path.join(d, "teslacam_stills", st[:10], st + ".zip")
        os.makedirs(os.path.dirname(p))
        with zipfile.ZipFile(p, "w") as z:
            for n in range(23):
                z.writestr("%02d.jpg" % n, b"x")
        out = app_footage.Footage(d).clip_status([st])
        self.assertTrue(out[st]["stills"])
        self.assertEqual(out[st]["stills_n"], 23)

    def test_viewer_stops_at_the_last_still_and_says_so(self):
        i = JS.index("function stillViewer")
        blk = JS[i:i + 12000]
        self.assertIn("stills_n", blk)
        self.assertIn("t<LEN(m)", blk)                       # no frame beyond the last picture of a minute
        self.assertNotIn("for(let t=0;t<60;t+=sp)", blk)
        self.assertIn('id="stend"', blk)
        self.assertIn("The recording ends here", blk)
        self.assertIn("not an error", blk)


if __name__ == "__main__":
    unittest.main()
