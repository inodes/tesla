"""The note under a clip job does not claim videos cannot play: the job stops the videos that were playing, but the server still serves a video asked for during it."""
import os, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8") as f:
    JS = f.read()


class JobNoteTest(unittest.TestCase):
    def test_wording(self):
        self.assertNotIn("Videos cannot play while this reads", JS)
        self.assertNotIn("videos cannot play until it has finished", JS)
        self.assertIn("not blocked, but may stutter", JS)


if __name__ == "__main__":
    unittest.main()
