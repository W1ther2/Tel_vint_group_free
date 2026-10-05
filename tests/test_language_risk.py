import unittest

from tests.helpers import reset_config
from vinted.language import detect_foreign_language
from vinted.risk import assess_risk, _PHONE_RE


class LanguageTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_lithuanian_with_and_without_diacritics(self):
        for text in ["Būklė ideali, ekranas be įbrėžimų", "bukle ideali, ekranas be ibrezimu",
                     "parduodu telefona, viskas veikia, battery health 88", "nesideru, atsiimti vilniuje", ""]:
            self.assertIsNone(detect_foreign_language("iPhone 13", text), text)

    def test_foreign(self):
        self.assertEqual(detect_foreign_language("iPhone", "Sprzedam telefon, stan bardzo dobry"), "PL")
        self.assertEqual(detect_foreign_language("iPhone", "Très bon état, vendu avec boite"), "FR")
        self.assertEqual(detect_foreign_language("iPhone", "Продаю телефон"), "RU")
        self.assertEqual(detect_foreign_language("iPhone", "Selling my phone, works perfectly"), "EN")


class RiskTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_clean_listing(self):
        level, _ = assess_risk("iPhone 13", "Parduodu, baterija 90%, puiki būklė, siunčiu per Vinted",
                               250, 290, {"rating": 4.9, "reviews": 20, "sold": 30, "account_age_days": 900})
        self.assertIsNone(level)

    def test_scam_listing(self):
        level, reasons = assess_risk("iPhone 13", "Atsiėmimas tik iš rankų. Rašykite WhatsApp +370 612 34567",
                                     120, 290, {"reviews": 0, "sold": 0, "account_age_days": 3}, photo_count=1)
        self.assertEqual(level, "didelė")
        for r in ["prašo rašyti ne per Vinted", "tik atsiėmimas iš rankų", "aprašyme telefono nr. / el. paštas",
                  "nauja paskyra (3 d.)", "pardavėjas dar nieko nepardavė", "tik 1 nuotrauka"]:
            self.assertIn(r, reasons)

    def test_seller_profile(self):
        _, reasons = assess_risk("iPhone 13", "Parduodu telefoną, veikia puikiai, baterija 90%", 250, 290,
                                 {"rating": 3.5, "reviews": 20, "negative": 5, "active_items": 80})
        self.assertIn("žemas pardavėjo įvertinimas (3.5/5)", reasons)
        self.assertIn("daug neigiamų atsiliepimų (5 iš 20)", reasons)
        self.assertTrue(any("perpardavėjas" in r for r in reasons))

    def test_phone_regex_no_false_positive(self):
        self.assertFalse(_PHONE_RE.search("128 256 512 GB"))
        self.assertTrue(_PHONE_RE.search("8 612 34 567"))


    def test_copy_only_when_really_a_copy(self):
        def reasons(desc):
            return assess_risk("iPhone 13", desc, 250, 290, {"rating": 4.9, "reviews": 20, "sold": 30})[1]
        self.assertIn("gali būti kopija", reasons("Tai kopija, bet veikia gerai ir greitai"))
        for desc in ["Telefonas originalus, ne kopija, viskas veikia", "Pridedu pirkimo čekio kopiją ir dėžutę"]:
            self.assertNotIn("gali būti kopija", reasons(desc), desc)

if __name__ == "__main__":
    unittest.main()
