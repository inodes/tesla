"""The note under a clip job does not claim videos cannot play: the job stops the videos that were playing, but the server still serves a video asked for during it."""
import os, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8") as f:
    JS = f.read()


class JobNoteTest(unittest.TestCase):
    def test_wording(self):
        self.assertNotIn("Videos cannot play while this reads", JS)
        self.assertNotIn("videos cannot play until it has finished", JS)
        self.assertNotIn("Videos were stopped to give", JS)           # the old wording read as a fault
        self.assertEqual(JS.count("You can still play one, but it may stutter"), 2)      # the bar over a clip and the note under the job


if __name__ == "__main__":
    unittest.main()
