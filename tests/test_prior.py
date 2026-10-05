# -*- coding: utf-8 -*-
"""v50: APSAUGOS riba atskirta nuo KAINOS prioro.

Iki v49.1 `typical_price` buvo isvedamas is ribos (`min / 0.45`), tad vienas
skaicius dirbo du priesingus darbus. Sie testai saugo, kad jie nebesusilietu
atgal - tai lengva padaryti netycia, nes abu gyvena catalog.py.
"""

import unittest

from tests.helpers import reset_config
from vinted import catalog, phone


class FloorVsPriorTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_measurement_does_not_move_the_floor(self):
        """Esme. Prioras ateina is MEASURED, riba - is MIN_PRICES, ir jie nesusieti."""
        for model in ("11", "13", "17 Pro"):
            prior = catalog.prior(model)
            floor = catalog.min_price(model)
            self.assertIsNotNone(prior)
            self.assertGreater(prior.price, floor,
                               f"{model}: prioras turi buti virs ribos")
            # Riba NEISVEDAMA is prioro jokiu daugikliu
            self.assertNotAlmostEqual(floor, prior.price * catalog.GUESS_RATIO, delta=0.01,
                                      msg=f"{model}: riba vel isvesta is kainos")

    def test_measured_models_carry_their_sample_size(self):
        """v49.1 is matavimo likdavo tik komentaras. Dabar imtis yra duomuo."""
        p = catalog.prior("13")
        self.assertEqual(p.method, "live-median")
        self.assertGreater(p.samples, 100)
        self.assertFalse(p.is_guess)
        self.assertEqual(p.measured, catalog.MEASURED_ON)

    def test_unmeasured_models_say_plainly_that_they_are_a_guess(self):
        """Retiems modeliams duomenu nera - ir tai turi matytis, o ne atrodyti
        taip pat tvirtai kaip 1417 skelbimu mediana."""
        rare = next(m for m in catalog.order() if m not in catalog.MEASURED)
        p = catalog.prior(rare)
        self.assertEqual(p.samples, 0)
        self.assertTrue(p.is_guess)
        self.assertEqual(p.method, "guess")

    def test_typical_price_equals_the_measured_median(self):
        """Buvo: „11" pervertintas 53 %, „17" nuvertintas 18 %, nes 0.45 nera
        konstanta. Ismatuotiems modeliams klaidos nebeturi likti visai."""
        for model, (median, _n) in list(catalog.MEASURED.items())[:12]:
            self.assertAlmostEqual(phone.typical_price(model), median, delta=0.5,
                                   msg=f"{model}: typical nesutampa su matavimu")

    def test_ratio_really_is_not_constant(self):
        """Kodel vieno daugiklio negali but: tikrasis santykis riba/mediana
        pigiems telefonams yra beveik dvigubai didesnis nei brangiems."""
        ratios = {m: catalog.min_price(m) / med
                  for m, (med, _n) in catalog.MEASURED.items() if catalog.min_price(m)}
        cheap = ratios["11"]          # mediana ~88
        dear = ratios["17 Pro"]       # mediana ~993
        self.assertGreater(cheap, dear * 1.5,
                           "jei santykis taptu pastovus, 0.45 vel butu pateisinamas")

    def test_floors_stay_below_every_measurement(self):
        """Sveikatos patikra visai lentelei: riba, pakilusi virs rinkos kainos,
        atmestu visus to modelio skelbimus ir to niekas nepastebetu."""
        for model, (median, _n) in catalog.MEASURED.items():
            floor = catalog.min_price(model)
            self.assertLess(floor, median,
                            f"{model}: riba {floor} >= rinkos mediana {median}")


class SkipIsRecordedTest(unittest.TestCase):
    """v50: po ribos krentancios kainos nebedingsta tyliai.

    Kol filtruodavom RASANT, klausimo „ar riba teisinga" nebuvo kaip uzduoti -
    irodymai buvo ismetami prie duru."""

    def setUp(self):
        reset_config()

    def test_below_floor_listing_is_archived_with_reason(self):
        from tests.helpers import listing
        from vinted.market import Market

        m = Market()
        floor = catalog.min_price("13")
        m.observe([listing(1, "iPhone 13 128GB", floor - 10, user_id=1)], day=100)

        self.assertNotIn("vinted:1", m.items, "i rinkos statistika patekti neturi")
        skips = [e for e in m.drain_events()
                 if e.get("e") == "skip" and e.get("why") == "below_floor"]
        self.assertEqual(len(skips), 1, "turi likti pedsakas archyve")
        self.assertEqual(skips[0]["m"], "13")
        self.assertAlmostEqual(skips[0]["fl"], floor, delta=0.01)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
