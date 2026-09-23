import unittest

from tests.helpers import reset_config
from vinted.phone import (detect_model, is_accessory, find_defects, extract_storage, extract_battery,
                          estimate_value, estimate_profit, normalize_model_name, normalize_storage)


class ModelTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_models(self):
        cases = {
            "iPhone 13 Pro Max 256GB": "13 Pro Max", "Apple iPhone13ProMax": "13 Pro Max",
            "iPhone 8 Plus": "8 Plus", "iPhone 8+": "8 Plus", "iPhone X 64gb": "X",
            "iPhone XS Max": "XS Max", "iphone xr": "XR", "IPHONE 14PRO": "14 Pro",
            "iPhone 12 mini": "12 mini", "iPhone 16e": "16e", "iPhone Air 256": "Air",
            "iPhone 17 Pro Max": "17 Pro Max", "i phone 14": "14", "iPhone 13 max": "13 Pro Max",
        }
        for title, expected in cases.items():
            self.assertEqual(detect_model(title), expected, title)

    def test_not_models(self):
        for title in ["Iphone X 6s XR 13 15pro 8plus 7plus 12pro 11", "iPhone 12 13 14", "iPhone 13 / 13 Pro dėklas",
                      "iPhone 7", "iPhone SE 2020", "iPhone 18 Pro", "Samsung S21", "iPhone 12 / iPhone 13"]:
            self.assertIsNone(detect_model(title), title)

    def test_single_model_with_numbers(self):
        self.assertEqual(detect_model("iPhone 13, naudotas 11 mėn"), "13")
        self.assertEqual(detect_model("iPhone 11 Pro 64GB iOS 17"), "11 Pro")

    def test_description_not_phone(self):
        from vinted.phone import description_not_phone, min_price
        self.assertTrue(description_not_phone("TUŠČIOS DĖŽUTĖS EMPTY BOX KAINA GALUTINĖ"))
        self.assertTrue(description_not_phone("Kaina už visas 50€"))
        self.assertFalse(description_not_phone("Parduodu telefoną su originalia dėžute, 2 vnt. dėklų"))
        # Vinted apgavyste – parduodamas tik lapas su nuotrauka
        for text in ["( parduodama tik A4 lapas su siais vaizdais siuntoje rasite tik tai)",
                     "Parduodama tik A4 lapas su šiais vaizdais, siuntoje rasite tik tai",
                     "gausite tik nuotrauką, ne telefoną",
                     "you are buying a picture, not the phone"]:
            self.assertTrue(description_not_phone(text), text)
        self.assertFalse(description_not_phone("Papildomų nuotraukų galiu atsiųsti, telefonas veikia"))
        self.assertEqual(min_price("13"), 90)

    def test_normalize(self):
        self.assertEqual(normalize_model_name("13 pro max"), "13 Pro Max")
        self.assertEqual(normalize_model_name("xs max"), "XS Max")
        self.assertEqual(normalize_storage("256gb"), "256 GB")
        self.assertEqual(normalize_storage("1tb"), "1 TB")
        self.assertIsNone(normalize_storage("180"))

    def test_accessory(self):
        self.assertTrue(is_accessory("Dėklas iPhone 13"))
        self.assertTrue(is_accessory("Case for iPhone 14 Pro"))
        self.assertTrue(is_accessory("Silikoninis dėklas skirtas iPhone 15"))
        self.assertFalse(is_accessory("iPhone 13 su dėklu ir dėžute"))
        self.assertFalse(is_accessory("iPhone 13 128GB + box"))
        for t in ["Paveikslas iPhone X", "iPhone 12 detalės", "iPhone 11 korpusas", "Framed iPhone art",
                  "iPhone XR dalys", "iPhone 13 muliažas", "iPhone 14 Pro tik dėžutė"]:
            self.assertTrue(is_accessory(t), t)
        for t in ["iPhone 13 Pro 256GB", "Apple iPhone XR juodas", "iPhone 12 mini su dėže ir kroviklis"]:
            self.assertFalse(is_accessory(t), t)


class DefectTest(unittest.TestCase):
    def labels(self, text):
        return [label for label, _ in find_defects(text)]

    def test_negations_ignored(self):
        for text in ["be įbrėžimų ir įskilimų", "jokių defektų ar įbrėžimų, viskas veikia", "iCloud atrištas",
                     "Telefonas iššilaikęs gerai, nesudaužytas ir nesubraižytas", "nėra jokių įbrėžimų ar įskilimų",
                     "no cracks, works perfectly"]:
            self.assertEqual(self.labels(text), [], text)

    def test_defects_found(self):
        self.assertEqual(self.labels("ekranas įskilęs, bet veikia"), ["skilęs"])
        self.assertEqual(self.labels("nėra įbrėžimų, bet yra įskilimas kampe"), ["skilęs"])
        self.assertEqual(self.labels("Face ID neveikia"), ["neveikia Face ID"])
        self.assertIn("iCloud užraktas", self.labels("for parts, icloud locked"))

    def test_untested_locked(self):
        for text in ["Telefonas įsijungia ir prašo kodo kurio mes nežinome.Toliau netestuotas.",
                     "pamiršau slaptažodį", "neįsijungia", "nesikrauna", "untested, sold as is",
                     "activation lock", "juodas ekranas"]:
            self.assertTrue(any(f == 0 for _, f in find_defects(text)), text)
        self.assertEqual(self.labels("ekrane žalia linija"), ["ekrano gedimas"])
        self.assertEqual(self.labels("kodas atrištas, veikia puikiai, išbandytas"), [])

    def test_fatal_defects_zero(self):
        self.assertTrue(any(f == 0 for _, f in find_defects("iCloud užblokuotas")))

    def test_real_listing_locked_without_battery(self):
        """Tikras Vinted skelbimas, atejes kaip „pigiausias iš 154“: rasybos klaida ir „be akumo“."""
        labels = self.labels("iPhone 14 - Užbluokuotas be akumo")
        self.assertIn("užblokuotas", labels)
        self.assertIn("be baterijos", labels)

    def test_lock_typos(self):
        for text in ["užbluokuotas", "Užblokuotas", "užlockintas", "blukuotas", "uzblokuotas operatoriui"]:
            self.assertIn("užblokuotas", self.labels(text), text)
        for text in ["neužblokuotas", "nėra užblokuotas", "iCloud atrištas, neblokuotas",
                     "bloknotas dovanų", "lokalus pardavimas", "su blokeliu"]:
            self.assertNotIn("užblokuotas", self.labels(text), text)

    def test_missing_battery(self):
        for text in ["be akumo", "nėra baterijos", "trūksta akumo", "neturi baterijos",
                     "be dėžutės be akumo", "no battery", "without battery"]:
            self.assertIn("be baterijos", self.labels(text), text)
        # „be“ cia reiskia „be problemu“ – telefonas tvarkingas
        for text in ["be baterijos keitimo", "be baterijos problemų, veikia", "be akumuliatoriaus pakeitimo",
                     "no battery issues", "be originalios baterijos", "baterija 90%, be jokių defektų",
                     "be įbrėžimų, baterija 88%"]:
            self.assertNotIn("be baterijos", self.labels(text), text)


class SpecsTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_storage_battery(self):
        self.assertEqual(extract_storage("iPhone 13 128GB"), "128 GB")
        self.assertEqual(extract_storage("1TB"), "1 TB")
        self.assertEqual(extract_battery("baterija 87%"), 87)
        self.assertEqual(extract_battery("Baterijos talpa: 88 %"), 88)
        self.assertEqual(extract_battery("85% battery"), 85)
        self.assertIsNone(extract_battery("nuolaida 20%"))

    def test_value_and_profit(self):
        self.assertAlmostEqual(estimate_value(200, "Labai gera", 92, []), 200)
        self.assertAlmostEqual(estimate_value(200, "Gera", 92, [("skilęs", 0.7)]), 200 * 0.92 * 0.7)
        # 150 + 0.70 + 7.50 + 3.50 = 161.70
        self.assertAlmostEqual(estimate_profit(150, 200), 200 - 161.70)
        self.assertAlmostEqual(estimate_profit(150, 200, pickup_only=True), 200 - 158.20)


if __name__ == "__main__":
    unittest.main()


class ICloudNegationTest(unittest.TestCase):
    """Tikras Pirkpard skelbimas: „iCloud paskyra bus atsieta pries pardavima“.
    Anksciau toks tvarkingas telefonas budavo atmetamas kaip uzrakintas."""

    def setUp(self):
        reset_config()

    def blokuojantys(self, tekstas):
        return [d for d, f in find_defects(tekstas) if f == 0]

    def test_promise_to_unlink_is_not_a_defect(self):
        self.assertEqual(self.blokuojantys("iCloud paskyra bus atsieta prieš pardavimą"), [])
        self.assertEqual(self.blokuojantys("Parduodu tvarkingą, iCloud bus atrišta"), [])
        self.assertEqual(self.blokuojantys("iCloud atrištas, viskas veikia"), [])

    def test_real_lock_still_caught(self):
        self.assertIn("iCloud užraktas",
                      self.blokuojantys("iCloud užraktas, nežinau slaptažodžio"))
        self.assertIn("iCloud užraktas",
                      self.blokuojantys("Telefonas užrakintas, iCloud pririštas prie senos paskyros"))
