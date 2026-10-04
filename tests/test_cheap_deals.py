# -*- coding: utf-8 -*-
"""Itin pigus skelbimai (2026-10-04): slamsto riba, rizikos patikra, Pirkpard isimtis, baterija."""
import contextlib
import io
import json
import os
import unittest

from tests.helpers import TempDir, item, reset_config
from tests.test_flow import FakeClient, FakeTelegram, market_items
from vinted import config
from vinted.finder import Run
from vinted.phone import battery_factor, description_is_accessory
from vinted.risk import assess_risk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def verdict(source, description, seller=None, photos=3):
    _level, reasons = assess_risk("iPhone 13 128GB", description, 140, 250, seller or {}, photos)
    return Run.extreme_price_verdict(source, reasons, seller or {"reviews": 25, "account_age_days": 900},
                                     description, photos)


class ExtremeVerdictTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_clean_listing_passes(self):
        self.assertIsNone(verdict("vinted", "Parduodu telefoną, veikia puikiai, siunčiu per Vinted"))

    def test_vinted_off_platform_contact_rejected(self):
        for text in ("Veikia, rašyk į WhatsApp", "Veikia, skambink 861234567",
                     "Veikia, susitarsim pavedimu", "Veikia, mano nr. +370 612 34567"):
            with self.subTest(text=text):
                self.assertIsNotNone(verdict("vinted", text))

    def test_pirkpard_phone_number_is_normal(self):
        """Pirkpard'e telefono nr. ir „skambink“ iprasti – neatmetam."""
        for text in ("Veikia puikiai, skambinkite 861234567", "Veikia, tel. +370 612 34567"):
            with self.subTest(text=text):
                self.assertIsNone(verdict("pirkpard", text))

    def test_pirkpard_messenger_and_off_platform_payment_rejected(self):
        """Pirkpard'e atsiskaitoma platformoje – WhatsApp ir pavedimas vis tiek pavojinga."""
        for text in ("Veikia, rašyk į WhatsApp", "Veikia, atsiskaitymas pavedimu į sąskaitą"):
            with self.subTest(text=text):
                self.assertIsNotNone(verdict("pirkpard", text))

    def test_stolen_and_copy_rejected_everywhere(self):
        for source in ("vinted", "pirkpard"):
            for text in ("Rastas telefonas, veikia", "Veikia, kopija, labai panaši"):
                with self.subTest(source=source, text=text):
                    self.assertIsNotNone(verdict(source, text))

    def test_without_documents_only_marked(self):
        self.assertIsNone(verdict("vinted", "Veikia puikiai, be dėžutės ir be dokumentų"))

    def test_many_negative_reviews_rejected(self):
        seller = {"reviews": 10, "negative": 3, "account_age_days": 900}
        self.assertIsNotNone(verdict("vinted", "Parduodu telefoną, veikia puikiai", seller))

    def test_weak_profile_needs_empty_listing_too(self):
        new = {"reviews": 0, "account_age_days": 3}
        self.assertIsNotNone(verdict("vinted", "Veikia", new, photos=1))
        self.assertIsNone(verdict("vinted", "Parduodu telefoną, veikia puikiai, siunčiu", new, photos=1))
        self.assertIsNone(verdict("vinted", "Veikia", new, photos=4))


class AccessoryDescriptionTest(unittest.TestCase):
    def test_accessory_descriptions(self):
        for text in ("Naujas silikoninis dėklas, tinka iPhone 13", "Ekrano apsauginis stiklas 2 vnt, naujas",
                     "Etui na iPhone 13, nowe", "Case iPhone 13 clear", "Dėkliukas su blizgučiais"):
            with self.subTest(text=text):
                self.assertTrue(description_is_accessory(text))

    def test_phone_descriptions(self):
        for text in ("Ekranas be įbrėžimų, viskas veikia", "Baterija 88%, siunčiu per Vinted",
                     "Dėklas dovanų, telefonas veikia puikiai", "Parduodu su dėklu ir stiklu", "",
                     "Veikia", "128GB, juodas"):
            with self.subTest(text=text):
                self.assertFalse(description_is_accessory(text))


class BatteryTest(unittest.TestCase):
    def test_factor_steeper_below_75(self):
        self.assertEqual(battery_factor(80), 0.93)
        self.assertEqual(battery_factor(76), 0.85)
        self.assertEqual(battery_factor(72), 0.80)
        self.assertEqual(battery_factor(60), 0.75)
        self.assertEqual(battery_factor(None), 0.98)

    def test_low_battery_not_rejected_but_warned(self):
        """MIN_BATTERY 0: 72 % baterija nebeatmeta, bet kortelėje ⚠️ ir mazesne verte."""
        reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8,
                     MARKET_PERCENTILE=0.5, MIN_DISCOUNT=0.15, MIN_BATTERY=0, MIN_PROFIT_EUR=0)
        with TempDir():
            cat = market_items() + [item(1, "iPhone 13 128GB", 140, user_id=1),
                                    item(2, "iPhone 13 128GB", 141, user_id=2)]
            client = FakeClient({"iPhone 13": cat}, pages={"1": "Tvarkingas, baterija 72%, siunčiu per Vinted",
                                                           "2": "Tvarkingas, baterija 95%, siunčiu per Vinted"})
            tg = FakeTelegram()
            with contextlib.redirect_stdout(io.StringIO()):
                Run(client, tg, sleep=lambda s: None).run()
            sent = {d["id"]: d for d, _ in tg.deals}
        low, good = sent["vinted:1"], sent["vinted:2"]
        self.assertTrue(low["battery_low"])
        self.assertFalse(good["battery_low"])
        self.assertLess(low["value"], good["value"] * 0.85)
        from vinted.telegram import format_card
        self.assertIn("tikėtinas keitimas", format_card(low))


class ConfigConsistencyTest(unittest.TestCase):
    KEYS = ("MIN_PROFIT_EUR", "MIN_BATTERY", "BATTERY_WARN_BELOW", "JUNK_PRICE_RATIO", "JUNK_MIN_EUR",
            "HARD_MIN_PRICE_RATIO", "SUSPICIOUS_REJECT_RATIO", "SUSPICIOUS_WARN_RATIO")

    def test_config_json_matches_defaults(self):
        with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
            live = json.load(f)
        for key in self.KEYS:
            with self.subTest(key=key):
                self.assertIn(key, config.DEFAULTS)
                self.assertEqual(live.get(key), config.DEFAULTS[key])


if __name__ == "__main__":
    unittest.main()
