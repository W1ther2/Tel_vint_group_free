# -*- coding: utf-8 -*-
"""Kitu gamintoju atpazinimas: Samsung, Xiaomi, Pixel, OnePlus.

Pavadinimai – tokio stiliaus, kokie is tikruju buna Vinted/Skelbiu sarasuose
(su „5G“, be tarpu, lietuviskai, lenkiskai). Svarbiausia – kad neatsirastu klaidingu
atpazinimu: pigios serijos (Redmi, Poco, Galaxy A) nera flagmanai, o „dekliukas
Samsung S23“ nera telefonas.
"""
import unittest

from tests.helpers import reset_config
from vinted import catalog
from vinted.phone import detect_model, display, is_accessory, min_price, normalize_model_name, typical_price


class SamsungTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_galaxy_s(self):
        cases = {
            "Samsung Galaxy S24 Ultra 256GB": "Galaxy S24 Ultra",
            "Galaxy S23": "Galaxy S23",
            "SAMSUNG GALAXY S21 ULTRA 5G": "Galaxy S21 Ultra",
            "Samsung s22+ 128gb": "Galaxy S22+",
            "Samsung Galaxy S23 Plus": "Galaxy S23+",
            "Parduodu Samsung S25 Ultra, idealus": "Galaxy S25 Ultra",
            "Samsung Galaxy S24 FE": "Galaxy S24 FE",
            "samsung galaxy s26 ultra": "Galaxy S26 Ultra",
        }
        for title, expected in cases.items():
            self.assertEqual(detect_model(title), expected, title)

    def test_fold_flip_note(self):
        cases = {
            "Samsung Galaxy Z Flip 5": "Galaxy Z Flip 5",
            "Samsung Z Fold4 512GB": "Galaxy Z Fold 4",
            "Galaxy Z Fold 6": "Galaxy Z Fold 6",
            "Samsung Galaxy Note 20 Ultra": "Galaxy Note 20 Ultra",
            "Samsung Note 20": "Galaxy Note 20",
        }
        for title, expected in cases.items():
            self.assertEqual(detect_model(title), expected, title)

    def test_not_samsung_flagships(self):
        for title in ["Samsung Galaxy A54 5G", "Samsung Galaxy S10", "Galaxy Tab S9",
                      "Samsung Galaxy Watch 6", "Samsung Galaxy S23 ir S24 dėklai"]:
            self.assertIsNone(detect_model(title), title)

    def test_brand_word_required(self):
        """Be „samsung“/„galaxy“ neatpazistam – „S24“ gali buti bet kas."""
        self.assertIsNone(detect_model("S24 Ultra 256GB"))
        self.assertEqual(detect_model("Galaxy S24 Ultra"), "Galaxy S24 Ultra")


class XiaomiTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_flagships(self):
        cases = {
            "Xiaomi 14 Ultra 512GB": "Xiaomi 14 Ultra",
            "Xiaomi 13 Pro": "Xiaomi 13 Pro",
            "xiaomi 14t pro 5g": "Xiaomi 14T Pro",
            "Xiaomi 15": "Xiaomi 15",
            "Xiaomi Mi 11 Ultra": "Xiaomi Mi 11 Ultra",
        }
        for title, expected in cases.items():
            self.assertEqual(detect_model(title), expected, title)

    def test_cheap_lines_ignored(self):
        """Redmi ir Poco – ne flagmanai, ju kainos iskreiptu rinka."""
        for title in ["Xiaomi Redmi Note 13 Pro", "Redmi Note 12", "Xiaomi Poco F5",
                      "Xiaomi Redmi 12C"]:
            self.assertIsNone(detect_model(title), title)


class PixelOnePlusTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_pixel(self):
        cases = {
            "Google Pixel 8 Pro": "Pixel 8 Pro",
            "Pixel 9 Pro XL 256GB": "Pixel 9 Pro XL",
            "Pixel 7": "Pixel 7",
        }
        for title, expected in cases.items():
            self.assertEqual(detect_model(title), expected, title)
        self.assertIsNone(detect_model("Google Pixel 5"))       # per senas, nera lenteleje

    def test_oneplus(self):
        self.assertEqual(detect_model("OnePlus 12 256GB"), "OnePlus 12")
        self.assertEqual(detect_model("One Plus 13"), "OnePlus 13")


class MixedTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_two_brands_is_not_a_phone(self):
        for title in ["Dėklai iPhone 13 ir Samsung Galaxy S23", "iPhone 13 / Xiaomi 14 stiklai"]:
            self.assertIsNone(detect_model(title), title)

    def test_accessories_for_any_brand(self):
        for title in ["Dėklas Samsung Galaxy S23", "Case for Samsung Galaxy S24",
                      "Stiklas skirtas Xiaomi 14", "Kroviklis Samsung Galaxy S22"]:
            self.assertTrue(is_accessory(title), title)

    def test_iphone_unchanged(self):
        """Apple ID nesikeicia – sena state.json istorija lieka galioti."""
        self.assertEqual(detect_model("Apple iPhone 13 Pro Max 256GB"), "13 Pro Max")
        self.assertEqual(detect_model("iPhone 16e"), "16e")


class DisplayAndPriceTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_display(self):
        self.assertEqual(display("13 Pro"), "iPhone 13 Pro")
        self.assertEqual(display("Galaxy S24 Ultra"), "Galaxy S24 Ultra")
        self.assertEqual(display("Xiaomi 14"), "Xiaomi 14")
        self.assertEqual(display("Pixel 9 Pro"), "Pixel 9 Pro")

    def test_prices_exist_for_every_model(self):
        for brand in catalog.BRANDS:
            for model in brand.order:
                self.assertIn(model, brand.prices, model)
                self.assertGreater(min_price(model), 0, model)
                self.assertGreater(typical_price(model), min_price(model), model)
            self.assertEqual(sorted(brand.order), sorted(brand.prices), brand.key)

    def test_normalize_user_input(self):
        cases = {"s24 ultra": "Galaxy S24 Ultra", "galaxy s23": "Galaxy S23",
                 "fold 5": "Galaxy Z Fold 5", "xiaomi 14 pro": "Xiaomi 14 Pro",
                 "pixel 9 pro": "Pixel 9 Pro", "13 pro max": "13 Pro Max", "xs max": "XS Max"}
        for text, expected in cases.items():
            self.assertEqual(normalize_model_name(text), expected, text)

    def test_bare_number_stays_apple(self):
        """Iki siol „14“ visada reiske iPhone – taip ir lieka."""
        self.assertEqual(normalize_model_name("14"), "14")
        self.assertEqual(normalize_model_name("15 pro"), "15 Pro")
        self.assertIsNone(normalize_model_name("7"))        # iPhone 7 nesekam, Pixel 7 – su vardu


class BrandSwitchTest(unittest.TestCase):
    def test_disabled_brand_not_detected(self):
        reset_config(BRANDS=["apple"])
        self.assertIsNone(detect_model("Samsung Galaxy S24 Ultra"))
        self.assertEqual(detect_model("iPhone 13"), "13")
        reset_config(BRANDS=["samsung"])
        self.assertEqual(detect_model("Samsung Galaxy S24 Ultra"), "Galaxy S24 Ultra")
        self.assertIsNone(detect_model("iPhone 13"))

    def test_unknown_brand_falls_back_to_all(self):
        reset_config(BRANDS=["nokia"])
        self.assertEqual(detect_model("iPhone 13"), "13")


if __name__ == "__main__":
    unittest.main()
