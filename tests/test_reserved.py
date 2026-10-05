# -*- coding: utf-8 -*-
"""v50: „rezervuota" = patvirtintas pardavimas.

Ismatuota 2026-10-05 is archyvo (3017 ivykiu, 163 busenu perejimai):

    -> gone        150
    -> reserved      8      <- Vinted TAI pranesa
    -> relist        4
    -> sold          1      <- ir tas pats is Pirkpard

    vinted „sold" ivykiu: 0  per 19 dienu

Vinted parduota skelbima istrina, tad gaunam 404 = „dingo". Bet rezervacija jis
pranesa, o ji reiskia, kad pirkejas sumokejo. Paskutine prasoma kaina tuo momentu
IR YRA uzsidarymo kaina - butent to reikia confidence.ask_quote.
"""

import unittest

from tests.helpers import listing, reset_config
from vinted import config
from vinted.market import Market


def market_with(price=300.0, day=100):
    m = Market()
    m.observe([listing(1, "iPhone 13 128GB", price, user_id=1)], day=day)
    return m


class ReservedAsSoldTest(unittest.TestCase):
    def setUp(self):
        reset_config(RESERVED_AS_SOLD=True, GONE_AS_SOLD=True)

    def test_reserved_counts_as_a_confirmed_sale(self):
        m = market_with()
        m.set_status("vinted:1", "reserved", day=101)
        e = m.items["vinted:1"]
        self.assertEqual(e["st"], "sold")
        self.assertEqual(e["sv"], 1, "rezervacija yra PATVIRTINIMAS, ne spejimas")
        self.assertEqual(e["sd"], 101)
        self.assertEqual(e["rs"], 1, "pazymim, kad patvirtinta is rezervacijos")

    def test_gone_is_still_only_a_guess(self):
        """Skirtumas, kuris ir yra visa esme: dingo != parduota."""
        m = market_with()
        m.set_status("vinted:1", "gone", day=101)
        e = m.items["vinted:1"]
        self.assertEqual(e["st"], "sold")
        self.assertEqual(e["sv"], 0)
        self.assertNotIn("rs", e)

    def test_page_saying_sold_stays_the_strongest_signal(self):
        m = market_with()
        m.set_status("vinted:1", "sold", day=101)
        e = m.items["vinted:1"]
        self.assertEqual(e["sv"], 1)
        self.assertNotIn("rs", e, 'tikras „parduota" nera is rezervacijos')

    def test_broken_reservation_is_fully_undone(self):
        """Rizika, del kurios tai imanoma daryti saugiai: jei rezervacija isiro ir
        skelbimas vel pasirode kataloge, patvirtinimas turi DINGTI. Kitaip
        kalibracija amzinai mokytusi is kainos, uz kuria niekas nepirko."""
        m = market_with()
        m.set_status("vinted:1", "reserved", day=101)
        self.assertEqual(m.items["vinted:1"]["sv"], 1)

        m.observe([listing(1, "iPhone 13 128GB", 300.0, user_id=1)], day=102)
        e = m.items["vinted:1"]
        self.assertEqual(e["st"], "active")
        self.assertNotIn("sv", e, "patvirtinimas turi buti nuimtas")
        self.assertNotIn("rs", e)
        self.assertNotIn("sd", e)

    def test_switch_off_restores_v49_behaviour(self):
        config.cfg["RESERVED_AS_SOLD"] = False
        m = market_with()
        m.set_status("vinted:1", "reserved", day=101)
        e = m.items["vinted:1"]
        self.assertEqual(e["st"], "active", "lieka aktyvus, kaip iki v50")
        self.assertNotIn("sv", e)

    def test_reservation_is_recorded_in_the_archive_either_way(self):
        """Archyve lieka tai, ka pasake puslapis, o ne musu isvada."""
        m = market_with()
        m.set_status("vinted:1", "reserved", day=101)
        events = [e for e in m.drain_events() if e.get("e") == "status"]
        self.assertEqual([e["st"] for e in events], ["reserved"])

    def test_reserved_price_is_the_closing_price(self):
        """Kaina, uzfiksuota rezervacijos momentu, patenka i „parduotu" kainas."""
        m = market_with(price=275.0)
        m.set_status("vinted:1", "reserved", day=101)
        self.assertEqual(m.items["vinted:1"]["p"], 275.0)
        self.assertEqual(m.items["vinted:1"]["st"], "sold")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
