"""The cameras are listed Front, Back, Left pillar, Left repeater, Right repeater, Right pillar everywhere (the video player's buttons, the stills viewer, the event pictures)."""
import os, re, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8") as f:
    JS = f.read()


class CameraOrderTest(unittest.TestCase):
    def test_the_order(self):
        keys = re.findall(r"(\w+):\"", re.search(r"const CAMN=\{(.*?)\};", JS).group(1))
        self.assertEqual(keys, ["front", "back", "left_pillar", "left_repeater", "right_repeater", "right_pillar"])

    def test_every_list_of_cameras_is_sorted_by_it(self):
        self.assertIn("const camSort=", JS)
        self.assertIn("camSort((hi&&hi.cams)||[])", JS)
        self.assertIn("camSort(hi.cams)", JS)
        self.assertIn("camSort(sh.cams)", JS)
        self.assertEqual(len(re.findall(r"const CAML=\{", JS)), 0)          # one table, not a second copy in another order


if __name__ == "__main__":
    unittest.main()
