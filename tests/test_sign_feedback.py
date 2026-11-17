"""The answers on the sign cards: kept, applied to the signs the page gets, and handed to the trainer (lib/sign_feedback.py, app_review.minute_signs); school terms of every state."""
import json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import sign_feedback as SF  # noqa: E402
import app_review  # noqa: E402
import school_days  # noqa: E402

ST = "2026-11-08_10-56-00"


def groups():
    return [dict(text="60", side="L", kind="", s0=5.0, s1=7.0, n=3, conf=90, f="a.jpg"),
            dict(text="40", side="L", kind="", s0=20.0, s1=21.0, n=2, conf=80, f="b.jpg")]


class FeedbackTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.F = SF.Feedback(self.d)

    def test_not_a_sign_takes_it_out_and_lists_it_apart(self):
        a, err = self.F.set(ST, 5.0, "60", "none")
        self.assertIsNone(err)
        self.assertEqual([g["text"] for g in self.F.apply(ST, groups())], ["40"])
        gone = self.F.apply(ST, groups(), dismissed=True)
        self.assertEqual([(g["text"], g["fb"]["r"]) for g in gone], [("60", "none")])

    def test_wrong_speed_changes_the_number_and_remembers_what_was_read(self):
        self.F.set(ST, 20.0, "40", "speed", 60)
        g = [x for x in self.F.apply(ST, groups()) if x["s0"] == 20.0][0]
        self.assertEqual((g["text"], g["conf"], g["fb"]["was"], g["fb"]["v"]), ("60", 100, "40", 60))

    def test_school_zone_answers_set_zone_and_keep_the_speed(self):
        self.F.set(ST, 5.0, "60", "school")
        self.F.set(ST, 20.0, "40", "notschool")
        out = {g["s0"]: g for g in self.F.apply(ST, groups())}
        self.assertTrue(out[5.0]["zone"])
        self.assertFalse(out[20.0]["zone"])
        self.assertEqual((out[5.0]["text"], out[20.0]["text"]), ("60", "40"))

    def test_the_trainer_gets_what_the_sign_really_is(self):
        self.F.set(ST, 5.0, "60", "none")
        self.F.set(ST, 20.0, "40", "speed", 60)
        over = json.load(open(os.path.join(self.d, "sign_training", "overrides.json")))
        self.assertEqual(over, {"%s|5.0|60" % ST: "none", "%s|20.0|40" % ST: "60"})
        self.F.set(ST, 20.0, "40", "school")          # a school answer does not say what the number is
        over = json.load(open(os.path.join(self.d, "sign_training", "overrides.json")))
        self.assertNotIn("%s|20.0|40" % ST, over)

    def test_clear_takes_an_answer_back_everywhere(self):
        self.F.set(ST, 5.0, "60", "none")
        self.F.clear(ST, 5.0, "60")
        self.assertEqual(len(self.F.apply(ST, groups())), 2)
        self.assertEqual(json.load(open(os.path.join(self.d, "sign_training", "overrides.json"))), {})

    def test_the_page_sends_whole_seconds_as_ints(self):
        self.F.set(ST, 5, "60", "none")                      # JSON from the page: 5, not 5.0
        self.assertEqual([g["text"] for g in self.F.apply(ST, groups())], ["40"])
        self.F.clear(ST, 5, "60")
        self.assertEqual(len(self.F.apply(ST, groups())), 2)

    def test_bad_answers_are_refused(self):
        self.assertIsNotNone(self.F.set(ST, 5.0, "60", "maybe")[1])
        self.assertIsNotNone(self.F.set(ST, 5.0, "60", "speed")[1])
        self.assertIsNotNone(self.F.set(ST, 5.0, "60", "speed", 63)[1])
        self.assertIsNotNone(self.F.set(ST, 5.0, "60", "speed", 500)[1])
        self.assertEqual(self.F.all(), {})


class MinuteSignsTest(unittest.TestCase):
    def test_the_page_gets_corrected_signs(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "drive_review"))
        rec = {"d": [dict(s=5, text="60", side="L", c=90, f="a.jpg", x=100, y=100, r=30, w=2896, h=1876),
                     dict(s=6, text="60", side="L", c=92, f="a2.jpg", x=110, y=100, r=32, w=2896, h=1876)]}
        json.dump({ST: rec}, open(os.path.join(d, "drive_review", "2026-11-08.json"), "w"))
        R = app_review.Review(d)
        got = R.minute_signs([ST])[ST]
        self.assertEqual((len(got), got[0]["text"], got[0]["s0"]), (1, "60", 5))
        R._fb().set(ST, got[0]["s0"], "60", "speed", 70)
        self.assertEqual(R.minute_signs([ST])[ST][0]["text"], "70")
        R._fb().set(ST, got[0]["s0"], "60", "none")
        self.assertNotIn(ST, R.minute_signs([ST]))
        self.assertEqual(R.minute_signs([ST], dismissed=True)[ST][0]["fb"]["was"], "60")


class SchoolTermsTest(unittest.TestCase):
    def test_every_state_has_both_years_four_terms(self):
        t = school_days.terms()
        for st in ("NSW", "VIC", "QLD", "ACT", "SA", "WA", "TAS", "NT"):
            for y in ("2026", "2027"):
                r = t[st]["terms"][y]
                self.assertEqual(len(r), 4, (st, y))
                self.assertTrue(all(a < b for a, b in r), (st, y))
                self.assertTrue(all(r[i][1] < r[i + 1][0] for i in range(3)), (st, y))
                self.assertTrue(all(x[0].startswith(y) and x[1].startswith(y) for x in r), (st, y))

    def test_a_school_day_in_each_state(self):
        for st in ("NSW", "VIC", "QLD", "ACT", "SA", "WA", "TAS", "NT"):
            self.assertTrue(school_days.school_day(st, "2026-11-17") if st in ("NSW", "VIC", "QLD", "ACT", "SA", "WA", "TAS") else school_days.school_day(st, "2026-10-14"), st)
            self.assertFalse(school_days.school_day(st, "2026-11-14"), st)       # a Saturday
            self.assertFalse(school_days.school_day(st, "2026-12-28"), st)       # the summer holidays

    def test_a_state_public_holiday_inside_a_term_is_not_a_school_day(self):
        self.assertFalse(school_days.school_day("VIC", "2026-11-03"))      # Melbourne Cup
        self.assertFalse(school_days.school_day("QLD", "2026-05-04"))      # Labour Day
        self.assertFalse(school_days.school_day("WA", "2026-06-01"))       # WA Day
        self.assertFalse(school_days.school_day("NSW", "2026-06-08"))      # King's Birthday
        self.assertTrue(school_days.school_day("NSW", "2026-06-09"))

    def test_an_unknown_state_or_year_is_unknown(self):
        self.assertIsNone(school_days.school_day("XX", "2026-11-17"))
        self.assertIsNone(school_days.school_day("NSW", "2030-03-03"))


if __name__ == "__main__":
    unittest.main()
