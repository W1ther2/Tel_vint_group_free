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
        for desc in ["Telefonas originalus, ne kopija, viskas veikia",
                     "Pridedu pirkimo čekio kopiją ir dėžutę",
                     # Atvirkstine zodziu tvarka reiskia TA PATI, ka „ne kopija".
                     # Lookbehind jos nepagaudavo – gyvai pazymedavo tvarkinga skelbima.
                     "Tvarkingas, pirktas Lietuvoje, yra čekis. Kopijos nėra."]:
            self.assertNotIn("gali būti kopija", reasons(desc), desc)


class OffPlatformScamTest(unittest.TestCase):
    """Lenkiska schema: issivilioti pirkeja uz Vinted apsaugos ribu.

    Visos frazes cia – is gyvu 2026-10-06/07 skelbimu, kuriuos pagavo apatine kainos
    riba. Riba yra ATSITIKTINE apsauga: ji veikia tik todel, kad sukciai praso per
    mazai. Jei toks skelbimas kada nors paprasys tikrosios kainos, jis praeis filtra
    ir vartotojas gaus kortele be jokio ispejimo. Butent todel pozymiai cia, o ne
    pasikliaujant riba."""

    def setUp(self):
        reset_config()

    def flags(self, desc):
        return assess_risk("iPhone 15 Pro", desc, 161, 700,
                           {"rating": 4.9, "reviews": 20, "sold": 30})[1]

    def test_blik_payment(self):
        """BLIK – lenkiskas momentinis pervedimas: neatsaukiamas, be jokios apsaugos."""
        self.assertIn("mokėjimas ne per Vinted",
                      self.flags("Tylko płatność blik. Wysylam do 3 dni."))

    def test_asks_not_to_use_buy_now(self):
        self.assertIn("prašo NEnaudoti „Pirkti dabar“",
                      self.flags("❗Prosze nie kupywac przez kup teraz❗"))

    def test_redirect_to_another_marketplace(self):
        for desc in ["Sprzedaż wyłącznie przez Allegro albo OLX",
                     "Sprzedam na OLX, tutaj tylko oglądanie",
                     "Tylko przez Allegro, prosze o kontakt"]:
            self.assertIn("prašo pirkti kitoje svetainėje", self.flags(desc), desc)

    def test_bank_transfer(self):
        self.assertIn("mokėjimas ne per Vinted",
                      self.flags("Zapłata przelewem na konto, nie przez Vinted"))

    def test_the_real_listing_is_high_risk(self):
        level, _ = assess_risk(
            "Iphone 15 pro",
            "❗Prosze nie kupywac przez kup teraz❗ Tylko płatność blik. Wysylam do 3 dni.",
            161.0, 700.0, {"rating": 4.9, "reviews": 20, "sold": 30})
        self.assertEqual(level, "didelė")

    def test_courier_names_are_not_a_redirect(self):
        """Riba, be kurios pozymis butu nenaudingas: „OLX WeDo" ir „Allegro One Box"
        yra PAKETOMATAI, kuriuos mini visiskai tvarkingi lenku skelbimai. Pirma
        versija juos pazymedavo kaip „didele rizika"."""
        for desc in ["Sprzedam iPhone 12, stan bardzo dobry. Wysyłka OLX WeDo, paczkomat.",
                     "Wysyłka Allegro One Box, polecam.",
                     "Wysylka kurierem InPost, paczkomaty OLX."]:
            self.assertNotIn("prašo pirkti kitoje svetainėje", self.flags(desc), desc)

    def test_clean_lithuanian_listing_stays_clean(self):
        level, reasons = assess_risk(
            "iPhone 13 128GB", "Puikios būklės, su dėžute. Siunčiu per Vinted, baterija 91%.",
            184, 268, {"rating": 4.9, "reviews": 25, "sold": 30, "account_age_days": 900})
        self.assertIsNone(level, reasons)

if __name__ == "__main__":
    unittest.main()
