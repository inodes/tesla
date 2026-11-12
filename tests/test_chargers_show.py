"""The place page offers 'Chargers here' only when a charge was made there and the charger is not already known (Places.chargers_show)."""
import os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("Tools", os.path.join("Tools", "lib")):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import app_places  # noqa: E402

LAT, LON = -33.80, 151.08


def near(kw, d=0.0):
    return dict(lat=LAT + d, lon=LON, kw=kw)


def show(stations=(), chargers=(), visits=None, **info):
    P = app_places.Places.__new__(app_places.Places)
    P.stations = lambda: list(stations)
    P.chargers = lambda: dict(rows=list(chargers))
    base = dict(kind="saved", lat=LAT, lon=LON, radius=100, chargers=None, home=False)
    base.update(info)
    v = [dict(k="charge", kwh=20, min=30)] if visits is None else visits
    return P.chargers_show(base, v)


class ShowTest(unittest.TestCase):
    def test_no_charge_here_nothing_to_ask(self):
        self.assertFalse(show(visits=[dict(k="idle", min=60)]))

    def test_a_charge_and_no_charger_on_record_asks(self):
        self.assertTrue(show())

    def test_a_charge_at_a_known_charger_does_not(self):
        self.assertFalse(show(chargers=[near(22)]))
        self.assertFalse(show(stations=[near(250)]))

    def test_a_charger_far_away_is_not_this_place(self):
        self.assertTrue(show(chargers=[near(22, d=0.01)]))

    def test_home_never(self):
        self.assertFalse(show(home=True))

    def test_dc_and_ac_side_by_side_speed_tells_them_apart(self):
        v = [dict(k="charge", kwh=20, min=30)]               # 40 kW average: not the 22 kW one
        self.assertFalse(show(chargers=[near(22), near(60)], visits=v))

    def test_two_that_the_speed_cannot_tell_apart_asks(self):
        v = [dict(k="charge", kwh=3, min=30)]                # 6 kW average: either could be it
        self.assertTrue(show(chargers=[near(22), near(60)], visits=v))

    def test_two_with_no_power_listed_asks(self):
        self.assertTrue(show(chargers=[near(None), near(None)]))


if __name__ == "__main__":
    unittest.main()
