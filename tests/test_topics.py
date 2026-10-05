# -*- coding: utf-8 -*-
"""v50: skiltis parenkama pagal gamintoja.

Iki tol Android reiske ATSKIRA paleidima - savas botas, sava busena, savas
workflow - tik todel, kad `CHAT_TOPIC_ID` buvo vienas visam procesui. Tai
kainavo antra token'a (Telegram `getUpdates` ta pacia zinute atiduoda tik
vienam) ir, svarbiau, ATSKIRA rinkos istorija bei kalibracija.
"""

import unittest

from tests.helpers import reset_config
from vinted import config
from vinted.telegram import Telegram


class TopicRoutingTest(unittest.TestCase):
    def setUp(self):
        reset_config()
        self.tg = Telegram(token="t", chat_id="-100500", topic_id="9")

    def test_brand_gets_its_own_topic(self):
        config.cfg["TOPIC_BY_BRAND"] = {"apple": "11", "samsung": "22"}
        self.assertEqual(self.tg.topic_for("apple"), "11")
        self.assertEqual(self.tg.topic_for("samsung"), "22")

    def test_unmapped_brand_falls_back_to_the_default_topic(self):
        """Pridejus nauja gamintoja niekas nenutyla – jis tiesiog eina i bendra skilti."""
        config.cfg["TOPIC_BY_BRAND"] = {"apple": "11"}
        self.assertEqual(self.tg.topic_for("oneplus"), "9")
        self.assertEqual(self.tg.topic_for(None), "9")

    def test_without_any_mapping_behaviour_is_unchanged(self):
        config.cfg["TOPIC_BY_BRAND"] = {}
        self.assertEqual(self.tg.topic_for("apple"), "9")

    def test_thread_id_reaches_the_telegram_payload(self):
        config.cfg["TOPIC_BY_BRAND"] = {"samsung": "22"}
        self.assertEqual(self.tg._thread("-100500", "samsung"),
                         {"message_thread_id": "22"})

    def test_private_chats_never_get_a_thread_id(self):
        """Asmeninese zinutese skilciu nera – su svetimu thread ID Telegram atsako klaida."""
        config.cfg["TOPIC_BY_BRAND"] = {"samsung": "22"}
        self.assertEqual(self.tg._thread("777", "samsung"), {})


class DealCarriesBrandTest(unittest.TestCase):
    def test_brand_key_is_derived_from_the_model(self):
        from vinted.catalog import brand_of
        self.assertEqual(getattr(brand_of("13 Pro"), "key", None), "apple")
        self.assertEqual(getattr(brand_of("Galaxy S24 Ultra"), "key", None), "samsung")
        self.assertEqual(getattr(brand_of("Pixel 8"), "key", None), "google")
        self.assertIsNone(getattr(brand_of("nera tokio"), "key", None))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
