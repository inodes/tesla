"""Choosing the charger of a charge sends its cost to Tessie by itself (ChargeBook.push_tessie): a free charger goes as $0.00, a charge Tessie has no id for is reported, nothing is
called when pushing is off. The push itself is charging_analyzer.sync_tessie_costs(only_ids=...); here it is replaced."""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_charges as AC  # noqa: E402


def book(d, tid=7, pid="10"):
    os.makedirs(os.path.join(d, "charges"), exist_ok=True)
    b = AC.ChargeBook(d, d)
    b.book = dict(plugs=[dict(id=pid, parts=[dict(id=pid, tessie_id=tid)])], sig=["x"])
    b.get = lambda: dict(book=b.book)
    return b


class FakeAnalyzer:
    calls = []

    def __init__(self, tessie_dir=None):
        pass

    def sync_tessie_costs(self, apply=False, limit=None, only_confident=False, only_ids=None, only_epochs=None):
        FakeAnalyzer.calls.append((apply, tuple(only_epochs)))
        return dict(pushed=1, failed=0, skipped=0, rows=[dict(tessie_id=7, action="PUSH", new_cost=0.0)])


class Push(unittest.TestCase):
    def wait(self, b):
        for _ in range(100):
            if not b.push_thread.is_alive():
                return
            time.sleep(0.05)

    def test_choosing_a_charger_sends_the_cost_to_tessie(self):
        with tempfile.TemporaryDirectory() as d:
            b = book(d, pid="1794049943")
            b._analyzer = lambda: (type("M", (), {"ChargingAnalyzer": FakeAnalyzer}), None)
            FakeAnalyzer.calls = []
            r = b.set_charger("1794049943", "Rhodes Waterside")
            self.assertEqual(r["push"], "started")
            self.wait(b)
            self.assertEqual(FakeAnalyzer.calls, [(True, (1794049943,))])   # applied, and only this charge (named by when it began)
            self.assertEqual(b.push_state["state"], "done")
            self.assertIn("$0.00", b.push_state["msg"])
            self.assertTrue(b.dirty)                                        # the book is rebuilt to show the sent figure

    def test_a_charge_our_copy_of_tessies_list_lacks_is_still_sent(self):
        with tempfile.TemporaryDirectory() as d:        # Tessie has an id for every charge: our cache may just not have the newest yet, the sync asks Tessie
            b = book(d, tid=None, pid="1794049943")
            b._analyzer = lambda: (type("M", (), {"ChargingAnalyzer": FakeAnalyzer}), None)
            FakeAnalyzer.calls = []
            self.assertEqual(b.set_charger("1794049943", "Rhodes Waterside")["push"], "started")
            self.wait(b)
            self.assertEqual(len(FakeAnalyzer.calls), 1)

    def test_pushing_off_calls_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            b = book(d)
            b.push_apply = False
            self.assertEqual(b.set_charger("10", "x")["push"], "off")

    def test_forgetting_the_charger_sends_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            b = book(d)
            self.assertNotIn("push", b.set_charger("10", None))

    def test_a_failure_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as d:
            b = book(d)

            def boom():
                raise RuntimeError("no token")
            b._analyzer = boom
            b.set_charger("10", "x")
            self.wait(b)
            self.assertEqual(b.push_state["state"], "failed")
            self.assertIn("no token", b.push_state["msg"])


class FreeIsAllowed(unittest.TestCase):
    def test_the_sync_lets_a_free_charger_send_zero(self):
        src = open(os.path.join(HERE, "..", "Tools", "lib", "charging_analyzer.py"), encoding="utf-8").read()
        i = src.index("def sync_tessie_costs")
        body = src[i:i + 12000]
        self.assertIn('not s.get("free_charger")', body)
        self.assertIn("only_ids", body)
        self.assertIn("only_epochs", body)
        self.assertIn('api_get("charges"', body)           # a charge missing from our copy of Tessie's list is looked up at Tessie


if __name__ == "__main__":
    unittest.main()
