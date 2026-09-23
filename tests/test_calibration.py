"""Vertinimo tikslumo matavimas ir savikalibracija."""
import unittest

from tests.helpers import reset_config, listing
from vinted import config
from vinted.market import Market


def base_config(**extra):
    opts = dict(MIN_SAMPLES=3, MARKET_PERCENTILE=0.5, ASKING_SALE_FACTOR=1.0,
                USE_TYPICAL_FALLBACK=False, USE_SOLD_PRICES=False, MARKET_PRICES={},
                MIN_CALIBRATION_SAMPLES=20, CALIBRATION_MAX_STEP=0.05, AUTO_CALIBRATE=True)
    opts.update(extra)
    reset_config(**opts)


def market_with_sold(sold_price, n=20, asking=200, confirmed=True, day=100):
    """Rinka, kurioje musu vertinimas buvo `asking`, o parduota uz `sold_price`."""
    m = Market()
    m.observe([listing(i, "iPhone 13 128GB", asking, user_id=i) for i in range(1, 4)], day=day)
    watched = [listing(i, "iPhone 13 128GB", asking, user_id=i) for i in range(10, 10 + n)]
    m.observe(watched, day=day)                       # cia irasomas musu vertinimas "q"
    m.observe([listing(l.id, "iPhone 13 128GB", sold_price, user_id=1) for l in watched], day=day)
    for l in watched:
        m.set_status(l.uid, "sold" if confirmed else "gone", day=day)
    return m


class QuoteMemoryTest(unittest.TestCase):
    def setUp(self):
        base_config()

    def test_quote_saved_with_new_listing(self):
        m = Market()
        m.observe([listing(i, "iPhone 13 128GB", 200, user_id=i) for i in range(1, 4)], day=100)
        self.assertNotIn("q", m.get("vinted:1"))          # pirmiems duomenu dar nebuvo
        m.observe([listing(9, "iPhone 13 128GB", 200, user_id=9)], day=100)
        e = m.get("vinted:9")
        self.assertEqual(e["q"], 200)
        self.assertEqual(e["qs"], "s")                    # vertinta pagal skelbimu kainas

    def test_manual_price_not_used_for_calibration(self):
        config.cfg["MARKET_PRICES"] = {"13": 180}
        m = Market()
        m.observe([listing(1, "iPhone 13 128GB", 200)], day=100)
        self.assertEqual(m.get("vinted:1")["qs"], "m")
        m.set_status("vinted:1", "sold", day=100)
        self.assertEqual(m.accuracy(day=100)["n"], 0)     # rankines kainos netaisom


class AccuracyTest(unittest.TestCase):
    def setUp(self):
        base_config()

    def test_overestimate_detected(self):
        m = market_with_sold(sold_price=170, asking=200)
        acc = m.accuracy(day=100)
        self.assertEqual(acc["n"], 20)
        self.assertAlmostEqual(acc["ratio"], 0.85)
        self.assertTrue(acc["confirmed"])
        self.assertEqual(acc["rows"][0][0], "13")

    def test_confirmed_sales_preferred_over_disappeared(self):
        m = market_with_sold(sold_price=170, n=20, asking=200, confirmed=True)
        # ta pati rinka papildoma dingusiais skelbimais, kuriu kaina kitokia
        extra = [listing(i, "iPhone 13 128GB", 200, user_id=i) for i in range(100, 130)]
        m.observe(extra, day=100)
        m.observe([listing(l.id, "iPhone 13 128GB", 100, user_id=1) for l in extra], day=100)
        for l in extra:
            m.set_status(l.uid, "gone", day=100)
        acc = m.accuracy(day=100)
        self.assertTrue(acc["confirmed"])
        self.assertEqual(acc["n"], 20)                    # tik patvirtinti pardavimai
        self.assertAlmostEqual(acc["ratio"], 0.85)

    def test_falls_back_to_disappeared_when_too_few_confirmed(self):
        m = market_with_sold(sold_price=170, n=25, asking=200, confirmed=False)
        acc = m.accuracy(day=100)
        self.assertFalse(acc["confirmed"])
        self.assertEqual(acc["n"], 25)


class CalibrationTest(unittest.TestCase):
    def setUp(self):
        base_config()

    def test_step_limited(self):
        m = market_with_sold(sold_price=170, asking=200)       # paklaida 15%
        change = m.calibrate(day=100)
        self.assertEqual(change["old"], 1.0)
        self.assertAlmostEqual(change["new"], 0.95)            # daugiausiai 5% per karta
        self.assertAlmostEqual(m.sale_factor(), 0.95)

    def test_needs_enough_samples(self):
        m = market_with_sold(sold_price=170, n=5, asking=200)
        self.assertIsNone(m.calibrate(day=100))
        self.assertEqual(m.calibration, 1.0)

    def test_disabled(self):
        base_config(AUTO_CALIBRATE=False)
        m = market_with_sold(sold_price=170, asking=200)
        self.assertIsNone(m.calibrate(day=100))

    def test_converges_to_truth(self):
        """Kartojant, pataisymas nusistovi ties realia paklaida, o ne nueina i begalybe."""
        base_config(CALIBRATION_MAX_STEP=0.5)
        real, asking = 170.0, 200.0
        calibration = 1.0
        for _ in range(12):
            m = Market()
            m.calibration = calibration
            factor = m.sale_factor()
            quote = asking * factor
            # visi parduoti uz realia kaina, nors spejom `quote`
            for i in range(20):
                m.items[f"vinted:{i}"] = {"m": "13", "s": "128 GB", "p": real, "q": round(quote, 2),
                                          "qs": "s", "qf": round(factor, 4), "st": "sold", "sd": 100,
                                          "sv": 1, "f": 100, "l": 100, "c": 100}
            m.calibrate(day=100)
            calibration = m.calibration
        self.assertAlmostEqual(asking * calibration, real, delta=1.0)

    def test_stale_samples_do_not_overshoot(self):
        """Seni pardavimai, ivertinti su senu pataisymu, negali nustumti kainos zemiau tiesos.

        Butent cia buvo klaida: dauginant „senas pataisymas x paklaida“, tas pats
        pardavimas veike kelis kartus ir vertinimas persisversdavo i kita puse."""
        base_config(CALIBRATION_MAX_STEP=0.5)
        real, asking = 170.0, 200.0
        m = Market()
        # 20 pardavimu, vertintu dar be pataisymo (senas irasas)
        for i in range(20):
            m.items[f"vinted:{i}"] = {"m": "13", "s": "128 GB", "p": real, "q": asking, "qs": "s",
                                      "qf": 1.0, "st": "sold", "sd": 100, "sv": 1,
                                      "f": 100, "l": 100, "c": 100}
        for _ in range(5):                       # tie patys duomenys tikrinami kelis kartus
            m.calibrate(day=100)
        self.assertAlmostEqual(asking * m.calibration, real, delta=0.5)

    def test_bounds_respected(self):
        base_config(CALIBRATION_MAX_STEP=0.9, CALIBRATION_MIN=0.7)
        m = market_with_sold(sold_price=50, asking=200)        # absurdiski duomenys
        m.calibrate(day=100)
        self.assertGreaterEqual(m.calibration, 0.7)

    def test_calibration_survives_save(self):
        m = market_with_sold(sold_price=170, asking=200)
        m.calibrate(day=100)
        again = Market(m.to_dict())
        self.assertAlmostEqual(again.calibration, m.calibration)


class AccuracyCommandTest(unittest.TestCase):
    def setUp(self):
        base_config(ADMIN_IDS=["1"])

    def test_tikslumas_command(self):
        from vinted import commands
        from vinted.state import State
        state = State()
        from vinted.util import today
        state.market = market_with_sold(sold_price=170, asking=200, day=today())
        text = commands.handle("/tikslumas", state)
        self.assertIn("pervertiname 15%", text)
        self.assertIn("iPhone 13", text)

    def test_tikslumas_without_data(self):
        from vinted import commands
        from vinted.state import State
        text = commands.handle("/tikslumas", State())
        self.assertIn("dar nėra", text)

    def test_kalibruoti_off(self):
        from vinted import commands
        from vinted.state import State
        state = State()
        self.assertIn("išjungta", commands.handle("/kalibruoti ne", state))
        self.assertFalse(config.cfg["AUTO_CALIBRATE"])
        self.assertEqual(state.overrides["AUTO_CALIBRATE"], False)


if __name__ == "__main__":
    unittest.main()
