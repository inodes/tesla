"""A save that fails says so in red: the Places form and the charge page's charger picker both colour their 'Not saved' text."""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class ErrorColour(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js"), encoding="utf-8") as f:
            self.js = f.read()

    def test_the_places_form_paints_its_errors_red(self):
        i = self.js.index('$("#sv").onclick=async()=>{const m=$("#msg")')
        block = self.js[i:i + 1400]
        self.assertIn('m.style.color="var(--bad)"', block)
        self.assertIn('bad("Not saved: "+j.error)', block)
        self.assertNotIn('m.textContent="Not saved: "', block)

    def test_the_charger_picker_paints_its_errors_red(self):
        self.assertIn('say("Not saved: "+(r.error||"?"),1)', self.js)
        self.assertIn('m.style.color=bad?"var(--bad)":""', self.js)


if __name__ == "__main__":
    unittest.main()
