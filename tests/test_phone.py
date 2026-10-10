import unittest

from tests.helpers import reset_config
from vinted.phone import (detect_model, is_accessory, find_defects, extract_storage, extract_battery,
                          estimate_value, estimate_profit, normalize_model_name, normalize_storage,
                          description_not_phone)


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
                      "iPhone 7", "iPhone SE 2020", "iPhone 18 Pro", "iPhone 12 / iPhone 13",
                      "Samsung A54", "Redmi Note 13 Pro", "iPhone ar Samsung dėklai"]:
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

    def test_cracked_word_forms(self):
        """2026-10-07: „suskilęs“ Huawei P40 praslydo – atpazinta tik „skilęs“ / „įskilęs“."""
        for text in ["Suskilęs ekranas", "Truputį suskilęs kampas", "Galinis dangtelis suskilęs",
                     "suskilinėjęs ekranas", "sutrūkęs ekranas"]:
            self.assertIn("skilęs", self.labels(text), text)
        for text in ["Ekranas sudužęs", "dužęs stiklas"]:
            self.assertIn("sudaužtas", self.labels(text), text)
        for text in ["Nesuskilęs, be defektų", "Ekranas nesudužęs", "trūksta dėžutės",
                     "Ekrano apsauga suskilusi, ekranas sveikas"]:
            self.assertEqual(self.labels(text), [], text)

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

    def test_sim_lock(self):
        """2026-10-10: iPhone su SIM užraktu nusiųstas kaip deal'as (-50 %)."""
        for text in ["SIM užraktas", "Telefonas turi sim užraktą", "parduodu su SIM užraktu", "Sim uzraktas",
                     "simlock", "sim lock", "operatoriaus užraktas", "tinka tik Tele2 kortelė",
                     "veikia tik su Telia", "tik Bitės kortelei", "tinklo užraktas"]:
            self.assertIn("SIM užraktas", self.labels(text), text)
            self.assertTrue(any(f == 0 for _, f in find_defects(text)), text)
        for text in ["be SIM užrakto", "neturi sim užrakto", "SIM užrakto nėra", "jokio SIM užrakto",
                     "no sim lock", "sim lock free", "SIM užraktas nuimtas", "SIM užraktas atrištas",
                     "pirktas Telia salone", "Tele2 garantija iki 2027", "veikia su visomis kortelėmis",
                     "simpatiškas telefonas", "simbolinė kaina"]:
            self.assertNotIn("SIM užraktas", self.labels(text), text)

    def test_accessory_anywhere_in_title(self):
        """Gyvas log'as: „Iphone 17 pro case 2€“ buvo skaiciuojamas kaip „per pigus telefonas“."""
        from vinted.phone import is_accessory
        for t in ["Iphone 17 pro case", "MagSafe case iPhone 15", "iPhone 13 Pro Max dėklas",
                  "Apple iPhone 14 Pro silikoninis dekliukas", "iPhone 15 kroviklis",
                  "iphone 15 pro max clear case magsafe"]:
            self.assertTrue(is_accessory(t), t)
        # telefonas su priedu – vis dar telefonas
        for t in ["iPhone 13 + dėklas", "iPhone 12 su dėkliuku", "iPhone 11, dėklas dovanų",
                  "iPhone 13 128GB case", "iPhone 12 mini with case", "iPhone 13, stiklas įskilęs",
                  "iPhone 12 Pro MagSafe", "iPhone 16 Pro Max 256GB", "iPhone XR case free"]:
            self.assertFalse(is_accessory(t), t)

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


class RealPhrasesTest(unittest.TestCase):
    """Iprasti skelbimu sakiniai, kuriuos v36 klaidingai atmesdavo (2026-09 kodo perziura).
    Kiekvienas atvejis – tvarkingas telefonas, kuris nebuvo issiustas."""

    def setUp(self):
        reset_config()

    def test_tidy_descriptions_have_no_defects(self):
        for text in ["Telefonas nebuvo taisytas, nebuvo daužtas, nebuvo sulytas", "Niekada nebuvo daužtas",
                     "Nebuvo remontuotas", "Atrištas nuo iCloud", "Atsietas nuo iCloud, paruoštas naujam savininkui",
                     "iCloud švarus", "Parduodu, nes reikia pinigų", "Parduodu skubiai, reikia greitai pinigų",
                     "Defektų neturi", "Apsauginis stikliukas įskilęs, ekranas sveikas",
                     "Būklė tokia, kaip yra nuotraukose", "Nebuvo keistas ekranas", "iCloud nėra užrakintas",
                     "Jei reikia, galiu paaiškinti kodėl parduodu"]:
            self.assertEqual(find_defects(text), [], text)

    def test_real_defects_still_found(self):
        cases = {
            "Ekranas įskilęs": "skilęs", "Galinis stiklas įskilęs": "skilęs", "Ekrano stiklas įskilęs": "skilęs",
            "iCloud užrakintas": "iCloud užraktas", "iCloud užrakintas bus atrištas": "iCloud užraktas",
            "Svarbu: iCloud užrakintas": "iCloud užraktas", "Svarbu – iCloud prašo slaptažodžio": "užrakintas kodu",
            "Nežinau PIN kodo": "užrakintas kodu", "Parduodu kaip yra": "netestuotas", "Sold as is": "netestuotas",
            "Buvo keistas ekranas": "keistas ekranas", "Buvo sulytas": "pažeistas",
            "Nebuvo naudotas, bet ekranas įskilęs": "skilęs",
            "Niekada nebuvo taisytas, tačiau ekranas įskilęs": "skilęs",
            "Telefonas nebuvo taisytas, bet neveikia Face ID": "neveikia Face ID",
        }
        for text, label in cases.items():
            self.assertIn(label, [lbl for lbl, _ in find_defects(text)], text)

    def test_phone_descriptions_are_phones(self):
        for text in ["Parduodu iPhone 13 telefoną, būklė labai gera", "Apple iPhone 12 telefonas, 128GB",
                     "Pridedu pirkimo čekio kopiją", "Tik korpuse keli smulkūs įbrėžimai",
                     "Visada laikytas tik dėkle, būklė ideali", "Tik ekrane vienas nežymus įbrėžimas",
                     "Telefonas originalus, ne kopija", "Originalus, ne replika", "Daugiau paveiksliukų galiu atsiųsti",
                     "Parduodu nes turiu 2 telefonus"]:
            self.assertFalse(description_not_phone(text), text)

    def test_not_phone_descriptions_still_caught(self):
        for text in ["Parduodu 3 telefonus, kaina už visus", "5 vnt. telefonų lotas", "Parduodamas tik dėklas",
                     "Tik korpusas, be ekrano", "Parduodu tik ekraną", "Tai replika", "Paveikslas su iPhone",
                     "Kaina už visas 50€"]:
            self.assertTrue(description_not_phone(text), text)

    def test_titles(self):
        for t in ["iPhone 13, korpusas be įbrėžimų", "iPhone 14 Pro originalus, ne kopija", "Baterija 100% iPhone 13",
                  "iPhone 12 Pro 128GB, ekranas ir korpusas idealūs", "iPhone 15 Pro Max titanium, all parts original"]:
            self.assertFalse(is_accessory(t), t)
        for t in ["iPhone 11 korpusas", "iPhone 12 parts only", "iPhone 14 Pro Max kopija",
                  "iPhone 11 korpusas su kamera", "Baterija iPhone 12"]:
            self.assertTrue(is_accessory(t), t)

    def test_model_edge_cases(self):
        self.assertEqual(detect_model("iPhone 17 Air 256GB"), "Air")
        self.assertIsNone(detect_model("iPhone 128GB juodas"))
        self.assertIsNone(detect_model("iPhone Xiaomi Samsung ekranų keitimas"))
        self.assertEqual(detect_model("iPhone XsMax 64GB"), "XS Max")

    def test_battery_in_words(self):
        self.assertEqual(extract_battery("baterija 89 proc."), 89)
        self.assertEqual(extract_battery("Baterijos būklė 100 procentų"), 100)
        self.assertIsNone(extract_battery("procesorius greitas, 90 kadrų"))


if __name__ == "__main__":
    unittest.main()
