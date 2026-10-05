# -*- coding: utf-8 -*-
"""v50: Pirkpard pardavimu patikra apeina VISUS gamintojus.

Iki siol `states()` darydavo paieska tik su `queries[0]`. Su vienu gamintoju
(„iphone") tai veike. Ijungus penkis, kiekvienas Samsung / Xiaomi / Pixel /
OnePlus skelbimas butu nerastas „iphone" sarase, o `_states_complete` vis tiek
butu True -> `status()` grazintu „gone" -> `GONE_AS_SOLD` paverstu ji PARDUOTU
prasoma kaina.

Tai butu isgalvotas pardavimas, patenkantis i „parduotu" medianas ir i
savikalibracija - t. y. sugadintu butent tuos duomenis, del kuriu Pirkpard
ir ijungiamas (jis vienintelis pasako `sold_out` tiesiogiai).
"""

import unittest

from tests.helpers import reset_config
from vinted import config
from vinted.sources.pirkpard import PirkpardSource


class FakeClient:
    """Grazina tik to gamintojo skelbimus, kurio prasoma."""

    sleep = staticmethod(lambda *a: None)
    last_error = ""
    blocked = ""

    def __init__(self, by_query, last_page=1):
        self.by_query = by_query
        self.last_page = last_page
        self.asked = []

    def start(self):
        pass

    def get_json(self, params, tries=3):
        self.asked.append(params["search"])
        rows = self.by_query.get(params["search"], [])
        return {"data": rows,
                "meta": {"current_page": params["page"], "last_page": self.last_page}}


class PirkpardStatesTest(unittest.TestCase):
    def setUp(self):
        reset_config(BRANDS=["apple", "samsung", "xiaomi", "google", "oneplus"],
                     PIRKPARD_QUERIES=[], PIRKPARD_STATUS_PAGES=3, PIRKPARD_PER_PAGE=100)

    @staticmethod
    def source(by_query, last_page=1):
        return PirkpardSource(client=FakeClient(by_query, last_page))

    def test_every_brand_is_queried(self):
        s = self.source({})
        s.states()
        self.assertEqual(s.client.asked,
                         ["iphone", "samsung galaxy", "xiaomi", "google pixel", "oneplus"])

    def test_samsung_listing_is_not_declared_gone(self):
        """Pagrindinis atvejis: Samsung skelbimas gyvas, bet jo nera „iphone" sarase."""
        s = self.source({
            "iphone": [{"id": 1, "is_active": True}],
            "samsung galaxy": [{"id": 2, "is_active": True}],
        })
        self.assertEqual(s.status("2"), "active")
        self.assertEqual(s.status("1"), "active")

    def test_sold_flag_is_read_from_any_brand(self):
        s = self.source({"google pixel": [{"id": 7, "sold_out": True}]})
        self.assertEqual(s.status("7"), "sold")

    def test_unknown_listing_is_gone_only_when_all_queries_finished(self):
        s = self.source({"iphone": [{"id": 1, "is_active": True}]})
        s.states()
        self.assertTrue(s._states_complete)
        self.assertEqual(s.status("999"), "gone")

    def test_one_failed_query_makes_the_whole_list_incomplete(self):
        """Uztenka vienos nutrukusios paieskos - nerastas skelbimas lieka „unknown",
        o ne „parduotas". Geriau nezinoti, nei issigalvoti pardavima."""
        class Flaky(FakeClient):
            def get_json(self, params, tries=3):
                if params["search"] == "xiaomi":
                    return None             # puslapis nepavyko
                return super().get_json(params, tries)

        s = PirkpardSource(client=Flaky({"iphone": [{"id": 1, "is_active": True}]}))
        s.states()
        self.assertFalse(s._states_complete)
        self.assertEqual(s.status("999"), "unknown")

    def test_paging_still_stops_at_last_page(self):
        s = self.source({"iphone": [{"id": 1, "is_active": True}]}, last_page=2)
        s.states()
        self.assertEqual(s.client.asked.count("iphone"), 2)

    def test_single_brand_behaviour_is_unchanged(self):
        config.cfg["BRANDS"] = ["apple"]
        s = self.source({"iphone": [{"id": 1, "is_active": True}]})
        s.states()
        self.assertEqual(s.client.asked, ["iphone"])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
