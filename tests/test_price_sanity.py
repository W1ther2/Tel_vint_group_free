# -*- coding: utf-8 -*-
"""v50: kainos galiojimo ir virsutines ribos patikra.

Apatine riba (min_price) buvo nuo pat pradziu, bet virsutines ir skaiciaus
galiojimo – ne. Praktines pasekmes, patikrintos gyvai:

  float("nan")  yra TEISETAS skaicius. Kiekvienas jo palyginimas yra False,
                tad NaN praeidavo net `price < 40`, o viena tokia reiksme
                paverčia viso modelio mediana NaN - tyliai, be klaidos.
  float("inf")  ir "1e400" tas pats kelias.
  lotas         „parduodu 5 telefonus" arba netiksliai atpazintas modelis
                patekdavo i istorija ir tempdavo mediana auksyn (gyvai:
                OnePlus 11 uz 899 EUR, kai to modelio kaina ~165).
"""

import math
import unittest

from tests.helpers import listing, reset_config
from vinted import config
from vinted.market import Market, price_ceiling
from vinted.parsing import get_price


def price_of(raw):
    return get_price({"price": {"amount": raw, "currency_code": "EUR"}})


class NumberValidityTest(unittest.TestCase):
    def test_nan_and_infinity_are_not_prices(self):
        for raw in ("nan", "NaN", "inf", "-inf", "Infinity", "1e400"):
            self.assertIsNone(price_of(raw), f"{raw!r} neturi tapti kaina")

    def test_normal_prices_still_parse(self):
        self.assertEqual(price_of("349.99"), 349.99)
        self.assertEqual(price_of("1 299,00"), 1299.0)

    def test_nan_would_have_passed_the_floor_check(self):
        """Kodel to reikejo: NaN apeina apatine riba, nes palyginimas visada False."""
        self.assertFalse(float("nan") < 40)


class CeilingTest(unittest.TestCase):
    def setUp(self):
        reset_config(MAX_PRICE_RATIO=4.0)

    def test_ceiling_follows_the_model_prior(self):
        """Riba skaiciuojama nuo prioro, ne nuo apatines ribos – patikslinus
        kaina ji pasislenka kartu."""
        from vinted.phone import typical_price
        self.assertAlmostEqual(price_ceiling("13"), typical_price("13") * 4.0, places=2)

    def test_absurd_price_never_reaches_market_history(self):
        m = Market()
        m.observe([listing(1, "iPhone 13 128GB", 99_999_999.0, user_id=1)], day=100)
        self.assertNotIn("vinted:1", m.items)
        skips = [e for e in m.drain_events() if e.get("why") == "above_ceiling"]
        self.assertEqual(len(skips), 1, "atmetimas turi likti archyve")

    def test_the_real_case_from_live_data(self):
        """OnePlus 11 uz 899 EUR salia 165 EUR – santykis 5.4."""
        reset_config(MAX_PRICE_RATIO=4.0, BRANDS=["oneplus"])
        m = Market()
        m.observe([listing(1, "OnePlus 11 256GB", 899.0, user_id=1),
                   listing(2, "OnePlus 11 128GB", 165.0, user_id=2)], day=100)
        self.assertNotIn("vinted:1", m.items)
        self.assertIn("vinted:2", m.items)

    def test_expensive_but_plausible_still_gets_in(self):
        """Riba dosni tycia: naujas, dar neatidarytas telefonas realiai buna
        2-3 kartus brangesnis uz naudotu mediana."""
        from vinted.phone import typical_price
        m = Market()
        ok = typical_price("13") * 2.5
        m.observe([listing(1, "iPhone 13 128GB", ok, user_id=1)], day=100)
        self.assertIn("vinted:1", m.items)

    def test_switch_off_removes_the_ceiling(self):
        config.cfg["MAX_PRICE_RATIO"] = 0
        self.assertIsNone(price_ceiling("13"))
        m = Market()
        m.observe([listing(1, "iPhone 13 128GB", 99_999.0, user_id=1)], day=100)
        self.assertIn("vinted:1", m.items)

    def test_median_stays_a_real_number(self):
        """Visos sios patikros egzistuoja del vieno dalyko: kad rinkos kaina
        liktu skaicius, o ne NaN ar tukstantis is loto."""
        m = Market()
        rows = [listing(i, "iPhone 13 128GB", 180.0 + i, user_id=i) for i in range(1, 11)]
        rows.append(listing(99, "iPhone 13 128GB", 99_999_999.0, user_id=99))
        m.observe(rows, day=100)
        q = m.quote("13", "128 GB", day=100)
        self.assertIsNotNone(q)
        self.assertTrue(math.isfinite(q.price))
        self.assertLess(q.price, 1000)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
