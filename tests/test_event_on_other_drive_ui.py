"""An event page looks for the event on every connected drive, LaCie (role L) and other registered folder drives too, not only A, J and U."""
import os, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
JS = open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8").read()


class EventDriveTest(unittest.TestCase):
    def test_the_sentry_page_asks_the_connected_drives(self):
        i = JS.index("async function pageTeslaCamSentry")
        blk = JS[i:i + 1800]
        self.assertIn('fetch("/api/status")', blk)
        self.assertIn('up.filter(x=>!"AJU".includes(x))', blk)           # the drives beyond A, J, U that are connected
        self.assertNotIn('["A","J","U"].filter(x=>x!==role)', blk)


if __name__ == "__main__":
    unittest.main()
