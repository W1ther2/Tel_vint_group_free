# -*- coding: utf-8 -*-
"""Rezervacija NERA pardavimas, bet ir ne nulis.

Ismatuota 2026-10-05 is archyvo (3017 ivykiu, 163 busenu perejimai):

    -> gone        150
    -> reserved      8      <- Vinted TAI pranesa
    -> relist        4
    -> sold          1      <- ir tas pats is Pirkpard

    vinted „sold" ivykiu: 0  per 19 dienu

Vinted parduota skelbima istrina, tad tikro „parduota" is jo negauni – gauni 404
(„dingo"). Rezervacija jis pranesa, ir trumpam (2026-10-05) buvo pabandyta ja
laikyti patvirtintu pardavimu. Prielaida klaidinga: Vinted pagalba (help/59)
rezervacija aprasO kaip pardavejo pazada palaikyti daikta nariui, kuris PLANUOJA
isigyti – galioja 5 dienas ir bet kada atsaukiama. Mokejimo joje nera. Iš 4
relist ivykiu matosi, kad dalis tikrai isyra.

Todel trys atskiri dalykai, ir butent ju atskirumas yra visa esme:

    sv=1   pardavimo FAKTAS (puslapis sako „parduota"). Tik sis moko kainas.
    sv=0   dingo – netiesioginis spejimas, ivertinamas maziau patikimai.
    rv     kada buvo matytas rezervuotas – faktas, bet ne isvada apie pardavima.
           I kainu statistika neieina niekada.
"""

import unittest

from tests.helpers import listing, reset_config
from vinted import config
from vinted.market import Market


def market_with(price=300.0, day=100):
    m = Market()
    m.observe([listing(1, "iPhone 13 128GB", price, user_id=1)], day=day)
    return m


class ReservedIsNotASaleTest(unittest.TestCase):
    """Numatytasis elgesys: RESERVED_AS_SOLD isjungtas."""

    def setUp(self):
        reset_config(RESERVED_AS_SOLD=False, GONE_AS_SOLD=True)

    def test_reservation_does_not_become_a_sale(self):
        m = market_with()
        m.set_status("vinted:1", "reserved", day=101)
        e = m.items["vinted:1"]
        self.assertEqual(e["st"], "active", "rezervuotas skelbimas tebera gyvas")
        self.assertNotIn("sv", e, "jokio pardavimo patvirtinimo")
        self.assertNotIn("sd", e)

    def test_the_fact_is_still_kept(self):
        """Rezervacija uzrasoma – tik atskirai nuo pardavimu."""
        m = market_with()
        m.set_status("vinted:1", "reserved", day=101)
        self.assertEqual(m.items["vinted:1"]["rv"], 101)

    def test_reservation_price_never_reaches_sold_prices(self):
        """Kodel tai svarbu: „parduotu" mediana maitina kalibracija, t. y. pacia
        svarbiausia produkto dali. Riba nuleista iki 1, kad uztektu vieno iraso –
        jei rezervacija butu laikoma pardavimu, kaina pasidarytu is „parduoti"."""
        reset_config(RESERVED_AS_SOLD=False, GONE_AS_SOLD=True,
                     USE_SOLD_PRICES=True, MIN_SOLD_SAMPLES=1)
        m = market_with(price=275.0)
        m.set_status("vinted:1", "reserved", day=101)
        q = m.quote("13", "128 GB", day=102)
        self.assertNotEqual(getattr(q, "source", None), "parduoti")

    def test_a_real_sale_still_counts(self):
        m = market_with(price=275.0)
        m.set_status("vinted:1", "sold", day=101)
        e = m.items["vinted:1"]
        self.assertEqual(e["sv"], 1)
        self.assertNotIn("rv", e, 'tikras „parduota" nera is rezervacijos')

    def test_gone_is_only_a_guess(self):
        """Dingo != parduota. Vinted parduota istrina, bet istrina ir savininkas."""
        m = market_with()
        m.set_status("vinted:1", "gone", day=101)
        e = m.items["vinted:1"]
        self.assertEqual(e["st"], "sold")
        self.assertEqual(e["sv"], 0, "netiesioginis signalas, maziau patikimas")

    def test_reservation_is_recorded_in_the_archive(self):
        """Archyve lieka tai, ka pasake puslapis, o ne musu isvada."""
        m = market_with()
        m.set_status("vinted:1", "reserved", day=101)
        events = [e for e in m.drain_events() if e.get("e") == "status"]
        self.assertEqual([e["st"] for e in events], ["reserved"])

    def test_reservation_then_relist_keeps_history(self):
        """Gyvas atvejis (4 is 8): rezervacija isiro, skelbimas vel kataloge."""
        m = market_with()
        m.set_status("vinted:1", "reserved", day=101)
        m.observe([listing(1, "iPhone 13 128GB", 300.0, user_id=1)], day=102)
        e = m.items["vinted:1"]
        self.assertEqual(e["st"], "active")
        self.assertNotIn("sv", e)
        self.assertEqual(e["rv"], 101, "kad buvo rezervuotas – vis tiek tiesa")


class UndoBadSalesTest(unittest.TestCase):
    """Vienkartinis valymas irasams, kuriuos paliko trumpai galiojusi versija."""

    def setUp(self):
        reset_config(RESERVED_AS_SOLD=False, GONE_AS_SOLD=True)

    def old_state(self):
        """Taip atrode state.json su RESERVED_AS_SOLD=True."""
        reset_config(RESERVED_AS_SOLD=True, GONE_AS_SOLD=True)
        m = market_with(price=275.0)
        m.set_status("vinted:1", "reserved", day=101)
        self.assertEqual(m.items["vinted:1"]["sv"], 1)   # tikrai sugedes
        data = m.to_dict()
        reset_config(RESERVED_AS_SOLD=False, GONE_AS_SOLD=True)
        return data

    def test_bad_confirmation_is_removed(self):
        m = Market(self.old_state())
        e = m.items["vinted:1"]
        self.assertEqual(e["st"], "active")
        self.assertNotIn("sv", e)
        self.assertNotIn("rs", e)

    def test_the_reservation_day_is_preserved_not_deleted(self):
        """Pirma valymo versija kartu su isvada sunaikindavo ir stebejima.
        Taisom isvada, o ne duomenis."""
        m = Market(self.old_state())
        self.assertEqual(m.items["vinted:1"]["rv"], 101)

    def test_cleaned_price_is_out_of_sold_prices(self):
        """Isvalius irasa, jo kaina nebemaitina „parduotu" medianos."""
        data = self.old_state()
        reset_config(RESERVED_AS_SOLD=False, GONE_AS_SOLD=True,
                     USE_SOLD_PRICES=True, MIN_SOLD_SAMPLES=1)
        m = Market(data)
        q = m.quote("13", "128 GB", day=102)
        self.assertNotEqual(getattr(q, "source", None), "parduoti")

    def test_cleanup_is_idempotent(self):
        """Valymas vyksta kiekviename paleidime – antras neturi nieko daryti."""
        once = Market(self.old_state())
        twice = Market(once.to_dict())
        self.assertEqual(twice.items["vinted:1"]["rv"], 101)
        self.assertEqual(twice.items["vinted:1"]["st"], "active")

    def test_real_sales_are_not_touched_by_the_cleanup(self):
        """Valymas atpazista irasus pagal `rs`, tad tikru pardavimu neliecia."""
        reset_config(RESERVED_AS_SOLD=True, GONE_AS_SOLD=True)
        m = market_with(price=275.0)
        m.set_status("vinted:1", "sold", day=101)
        data = m.to_dict()
        reset_config(RESERVED_AS_SOLD=False, GONE_AS_SOLD=True)
        healed = Market(data)
        self.assertEqual(healed.items["vinted:1"]["sv"], 1)
        self.assertEqual(healed.items["vinted:1"]["st"], "sold")


class SwitchStillWorksTest(unittest.TestCase):
    """Jungiklis paliktas, jei kada atsirastu irodymu, kad rezervacijos
    virsta pardavimais pakankamai patikimai."""

    def setUp(self):
        reset_config(RESERVED_AS_SOLD=True, GONE_AS_SOLD=True)

    def test_on_it_counts_as_a_confirmed_sale(self):
        m = market_with()
        m.set_status("vinted:1", "reserved", day=101)
        e = m.items["vinted:1"]
        self.assertEqual(e["st"], "sold")
        self.assertEqual(e["sv"], 1)
        self.assertEqual(e["rs"], 1, "pazymim, kad patvirtinta is rezervacijos")

    def test_even_then_the_fact_is_recorded_separately(self):
        m = market_with()
        m.set_status("vinted:1", "reserved", day=101)
        self.assertEqual(m.items["vinted:1"]["rv"], 101)

    def test_broken_reservation_is_undone_on_relist(self):
        m = market_with()
        m.set_status("vinted:1", "reserved", day=101)
        m.observe([listing(1, "iPhone 13 128GB", 300.0, user_id=1)], day=102)
        e = m.items["vinted:1"]
        self.assertEqual(e["st"], "active")
        self.assertNotIn("sv", e, "patvirtinimas turi buti nuimtas")
        self.assertNotIn("rs", e)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
