"""The keep bar offers one button that deletes every clip parked at Home, those shown or not (the Select menu only ticks the ones shown)."""
import os, re, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8") as f:
    JS = f.read()


class HomeDeleteAllTest(unittest.TestCase):
    def test_the_button_counts_every_ignored_clip_not_only_the_shown(self):
        m = re.search(r"const homeEvery=(.*?);", JS)
        self.assertIn("KPC", m.group(1))
        self.assertNotIn("vis", m.group(1))
        self.assertIn('id="hdelall"', JS)
        self.assertIn("Delete all Home parks", JS)

    def test_the_click_ticks_them_all_and_asks_what_to_delete(self):
        m = re.search(r'on\("hdelall",\(\)=>\{(.*?)\}\);', JS)
        self.assertIn("KPC[k][id].ignored", m.group(1))
        self.assertIn('pend={action:"purge"}', m.group(1))


if __name__ == "__main__":
    unittest.main()
