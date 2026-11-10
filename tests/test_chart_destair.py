"""destair (app.js): a staircase from Tessie's coarse readings is drawn as the straight line it was, with the same area under it; real holds and smooth series are left alone."""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import node_usable  # noqa: E402

APP_JS = os.path.join(HERE, "..", "Tools", "lib", "app_ui", "app.js")


def run(expr):
    with open(APP_JS, encoding="utf-8") as f:
        src = f.read()
    h = src[src.index("// DESTAIR begin"):src.index("// DESTAIR end")].replace("let SMS=true;try{SMS=localStorage.getItem(\"smoothSteps\")!==\"0\"}catch(_){}", "")
    out = subprocess.run(["node", "-e", h + "\nconsole.log(JSON.stringify(" + expr + "))"], capture_output=True, text=True)
    return json.loads(out.stdout)


def area(t, a):
    return sum((a[i] + a[i + 1]) / 2.0 * (t[i + 1] - t[i]) for i in range(len(a) - 1))


@unittest.skipUnless(node_usable.usable(), "node is not installed or does not start")
class DestairTest(unittest.TestCase):
    def stair(self, hold=10, steps=40, start=20):
        t, a = [], []
        for v in range(steps):
            for k in range(hold):
                t.append((v * hold + k) * 3)           # a sample every 3 s
                a.append(start + v)
        return t, a

    def test_a_staircase_becomes_a_line_with_the_same_area(self):
        t, a = self.stair()
        b = run("destair(%s,%s)" % (json.dumps(t), json.dumps(a)))
        self.assertNotEqual(a, b)
        # the true line rises 1 per 10 samples: centred on each step, i.e. value v at the middle of its run
        true = [20 + (i - 4.5) / 10.0 for i in range(len(t))]
        mid = range(30, len(t) - 30)
        self.assertLess(max(abs(b[i] - true[i]) for i in mid), 0.15)
        self.assertLess(abs(area(t, b) - area(t, a)) / area(t, a), 0.01)
        self.assertEqual(sorted(b[5:-5]), b[5:-5])      # still rising, never wobbling

    def test_the_readings_given_are_not_changed(self):
        t, a = self.stair()
        keep = list(a)
        run("destair(%s,%s)" % (json.dumps(t), json.dumps(a)))
        self.assertEqual(a, keep)

    def test_a_series_that_is_not_a_staircase_is_left_alone(self):
        t = list(range(60))
        a = [round(50 + 10 * ((i * 7) % 13) / 13.0, 3) for i in range(60)]
        self.assertEqual(run("destair(%s,%s)" % (json.dumps(t), json.dumps(a))), a)

    def test_a_real_hold_stays_flat(self):
        t, a = self.stair(hold=10, steps=20, start=50)           # climbing 50..69
        n = len(t)
        t2 = t + [t[-1] + 3 * (k + 1) for k in range(300)]
        a2 = a + [a[-1]] * 300                                    # then sitting at 69 for 15 minutes
        b = run("destair(%s,%s)" % (json.dumps(t2), json.dumps(a2)))
        self.assertTrue(all(abs(v - 69) < 1e-9 for v in b[n + 10:]))

    def test_few_steps_are_left_alone(self):
        t = list(range(40))
        a = [11.0] * 20 + [0.0] * 20                              # a charger that stopped: a real step
        self.assertEqual(run("destair(%s,%s)" % (json.dumps(t), json.dumps(a))), a)

    def test_gaps_and_nulls_are_kept(self):
        t, a = self.stair(steps=12)
        a[3] = None
        b = run("destair(%s,%s)" % (json.dumps(t), json.dumps(a)))
        self.assertIsNone(b[3])
        self.assertEqual(len(b), len(a))


@unittest.skipUnless(node_usable.usable(), "node is not installed or does not start")
class SoftenTest(unittest.TestCase):
    def volts(self):
        t = [i * 3 for i in range(300)]
        a = [round(380 + i * 0.06 + (1 if i % 7 in (0, 1) else 0)) for i in range(300)]       # whole volts, wobbling by one between neighbours
        return t, a

    def test_a_one_unit_jump_between_neighbours_becomes_a_slope(self):
        t, a = self.volts()
        b = run("soften(%s,%s)" % (json.dumps(t), json.dumps(a)))
        self.assertLess(max(abs(b[i + 1] - b[i]) for i in range(5, 290)), 0.6 * max(abs(a[i + 1] - a[i]) for i in range(5, 290)))
        self.assertLess(abs(area(t, b) - area(t, a)) / area(t, a), 0.001)

    def test_a_flat_stretch_stays_flat_and_nulls_stay(self):
        t = list(range(100))
        a = [5.0] * 100
        a[40] = None
        b = run("soften(%s,%s)" % (json.dumps(t), json.dumps(a)))
        self.assertIsNone(b[40])
        self.assertTrue(all(abs(v - 5.0) < 1e-9 for i, v in enumerate(b) if i != 40))

    def test_the_ends_of_a_series_stay_put(self):
        t = [i * 3 for i in range(120)]
        a = [0.0] + [4.0 if i % 9 else 3.0 for i in range(1, 119)] + [0.0]       # begins and ends at 0 kW
        b = run("soften(%s,%s)" % (json.dumps(t), json.dumps(a)))
        self.assertEqual(b[0], 0.0)
        self.assertEqual(b[-1], 0.0)

    def test_a_slow_climb_in_few_steps_is_a_ramp(self):
        t = [i * 36 for i in range(145)]                 # a slow home charge: the battery in whole percent, five readings in 95 minutes
        a = [62] * 20 + [63] * 30 + [64] * 30 + [65] * 35 + [66] * 30
        b = run("destair(%s,%s)" % (json.dumps(t), json.dumps(a)))
        self.assertLess(max(abs(b[i + 1] - b[i]) for i in range(144)), 0.1)         # no step left
        self.assertAlmostEqual(area(t, b), area(t, a), delta=0.01 * area(t, a))

    def test_the_readings_given_are_not_changed(self):
        t, a = self.volts()
        keep = list(a)
        run("soften(%s,%s)" % (json.dumps(t), json.dumps(a)))
        self.assertEqual(a, keep)

    def test_a_short_series_is_left_alone(self):
        self.assertEqual(run("soften([0,1,2],[1,2,3])"), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
