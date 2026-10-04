"""Slamstas, kuris neturi patekti i grupe: priedai su telefono pavadinimu, itartinai pigus
retu modeliu skelbimai be papildomu irodymu. Ir zema baterija, kai MIN_BATTERY=0 (2026-10-04)."""
import contextlib
import io
import unittest

from tests.helpers import reset_config, TempDir, item
from tests.test_flow import FakeClient, FakeTelegram, market_items
from vinted import config
from vinted.finder import Run
from vinted.phone import description_not_phone

OK = "Parduodu telefoną, veikia puikiai, siunčiu per Vinted"
# Tik 4 skelbimai – palyginti per mazai (RANK_MIN_PEERS=8), vertinama pagal nuolaida
RARE = [item(2000 + i, "iPhone 12 mini 64GB", p, user_id=600 + i) for i, p in enumerate([190, 210, 230, 250])]
RARE_XR = [item(3000 + i, "iPhone XR 64GB", p, user_id=700 + i) for i, p in enumerate([70, 75, 80, 85])]
NEW_SELLER = {"country_code": "LT", "city": "Vilnius", "feedback_reputation": 0, "feedback_count": 0,
              "given_item_count": 0, "created_at": "2026-10-01T00:00:00Z"}


def run(query, market, title, price, desc, user=None, **extra):
    config.cfg["SEARCH_QUERIES"] = [query]
    with TempDir():
        tg = FakeTelegram()
        client = FakeClient({query: list(market) + [item(9, title, price, user_id=9, **extra)]}, pages={"9": desc},
                            users={9: user} if user else None)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            Run(client, tg, sleep=lambda s: None).run()
        sent = [d for d, _ in tg.deals if d["id"] == "vinted:9"]
        return (sent[0] if sent else None), out.getvalue()


class AccessoryDescriptionTest(unittest.TestCase):
    def test_accessory_descriptions(self):
        for text in ["Dėklas", "Naujas silikoninis dėklas, juodas", "Silikoninis dėklas, juodas, tinka iPhone 13",
                     "Dėklas su kortelių kišenėle", "Parduodu dėklą iPhone 13, naujas, originalus",
                     "Parduodu dėklą. Telefonas neparduodamas", "Naujas apsauginis stiklas iPhone 13",
                     "Kameros apsauga ir galinis stikliukas", "Kroviklis ir laidas, originalūs",
                     "Dėžutė nuo iPhone 13 su dokumentais", "Phone case for iPhone 13, new",
                     "Tinka iPhone 12 mini", "Skirtas iPhone 13", "Parduodu telefono dėžutę",
                     "MagSafe dėklas, originalus Apple", "Dėkliukas iPhone 12 mini, rožinis"]:
            self.assertTrue(description_not_phone(text), text)

    def test_phone_with_accessories_is_phone(self):
        for text in [OK, "Dėklas dovanų", "Dėklas ir stiklas dovanų", "Dėžutė yra", "Dėžutės neturiu",
                     "Dėžutė, kroviklis, dokumentai. Telefonas veikia puikiai, baterija 90%",
                     "Kartu dėklas ir apsauginis stiklas", "Visada laikytas dėkle su apsauginiu stiklu",
                     "Parduodu su dėklu", "Parduodamas iPhone 13, dėklas dovanų", "Pridedu dėklą",
                     "Apsauginis stiklas užklijuotas nuo pirmos dienos", "Dėklas + stiklas",
                     "Parduodu telefoną su originalia dėžute, 2 vnt. dėklų", "Tinka visiems operatoriams",
                     "Kroviklis originalus, ekranas be įbrėžimų", "Case included, battery 88%",
                     "Parduodu, nes nusipirkau naują. Dėklas ir kroviklis kartu.", "Parduodu kartu su dėžute",
                     "Turi minimalius pabraižymus, visada buvo naudotas su dėklais, baterijos likutis 74%"]:
            self.assertFalse(description_not_phone(text), text)

    def test_not_a_sale(self):
        for text in ["Kopija, labai panašus į originalą", "Telefonas yra kopija", "Perku iPhone 13, siūlykit",
                     "Ieškau iPhone 13 su gera baterija", "Kaina už abu telefonus", "Rezervuota",
                     "Rezervuotas iki penktadienio"]:
            self.assertTrue(description_not_phone(text), text)
        for text in ["Ieškau naujo savininko", "Originalus, ne kopija", "Pridedu čekio kopiją",
                     "Yra kopija čekio", "Perku naują, todėl parduodu šį", "Telefonas ne kopija, originalus",
                     "Rezervuoti negaliu, pirmas sumokėjęs gauna", "Kaina už telefoną galutinė"]:
            self.assertFalse(description_not_phone(text), text)


class JunkFlowTest(unittest.TestCase):
    def setUp(self):
        reset_config(HEARTBEAT_HOURS=0, MIN_SAMPLES=8, DEAL_MODE="rank", MIN_BATTERY=0)

    def test_user_examples_rejected(self):
        """„iPhone 13 už 20 €“ ir „iPhone 12 mini“ už 7 € su aprasymu „Dėklas“."""
        self.assertIsNone(run("iPhone 13", market_items(), "iPhone 13", 20, OK)[0])
        self.assertIsNone(run("iPhone 12 mini", RARE, "iPhone 12 mini", 7, "Dėklas")[0])

    def test_accessory_description_above_price_floor(self):
        deal, _ = run("iPhone 13", market_items(), "iPhone 13 128GB", 200, OK)
        self.assertIsNotNone(deal)
        deal, log = run("iPhone 13", market_items(), "iPhone 13 128GB", 200, "Silikoninis dėklas, juodas")
        self.assertIsNone(deal)
        self.assertIn("ne telefonas (pagal aprašymą)", log)

    def test_working_xr_for_35_is_sent(self):
        """Pasitaiko ir pilnai veikiantis XR uz 35 €: < NO_RANK_STRICT_RATIO rinkos neatmetam,
        jei aprasyme parasyta, kad veikia, ir nera apgavystes pozymiu."""
        deal, _ = run("iPhone XR", RARE_XR, "iPhone XR", 35, "Pilnai veikiantis, baterija 85%, siunčiu per Vinted")
        self.assertIsNotNone(deal)
        self.assertTrue(any("pigiau nei rinka" in r for r in deal["risk_reasons"]), deal["risk_reasons"])
        deal, log = run("iPhone XR", RARE_XR, "iPhone XR", 35, "Parduodu, nes nebereikia, siunčiu per Vinted")
        self.assertIsNone(deal)
        self.assertIn("nepraėjo papildomos patikros", log)
        self.assertIn("neparašyta, kad veikia", log)

    def test_suspiciously_cheap_needs_clean_listing(self):
        works = "Telefonas veikia puikiai, siunčiu per Vinted"
        self.assertIsNotNone(run("iPhone 12 mini", RARE, "iPhone 12 mini", 75, works)[0])
        for desc, kwargs in [(works, {"user": NEW_SELLER}),             # nauja paskyra, be atsiliepimu
                             (works, {"photo_count": 1}),               # tik 1 nuotrauka
                             ("Veikia, rašykite WhatsApp", {}),         # ne per Vinted
                             ("Veikia", {}),                            # beveik tuscias aprasymas
                             ("Ne viskas veikia, siunčiu per Vinted", {})]:
            deal, log = run("iPhone 12 mini", RARE, "iPhone 12 mini", 75, desc, **kwargs)
            self.assertIsNone(deal, (desc, kwargs))
            self.assertIn("nepraėjo papildomos patikros", log)

    def test_normal_price_has_no_extra_checks(self):
        deal, _ = run("iPhone 12 mini", RARE, "iPhone 12 mini", 100, "Parduodu, nes nebereikia, siunčiu per Vinted")
        self.assertIsNotNone(deal)
        deal, _ = run("iPhone 12 mini", RARE, "iPhone 12 mini", 100, "Dėklas iPhone 12 mini")
        self.assertIsNone(deal)

    def test_low_battery_sent_when_limit_off(self):
        # Zema baterija vis tiek mazina verte (x0.85), tad pelnas skaiciuojamas teisingai
        deal, log = run("iPhone 13", market_items(), "iPhone 13 128GB", 200,
                        "Tvarkingas telefonas, baterija 72%, siunčiu per Vinted")
        self.assertIsNone(deal)
        self.assertIn("per mažas pelnas", log)
        deal, _ = run("iPhone 13", market_items(), "iPhone 13 128GB", 180,
                      "Tvarkingas telefonas, baterija 72%, siunčiu per Vinted")
        self.assertIsNotNone(deal)
        self.assertEqual(deal["battery"], 72)
        self.assertFalse(deal["battery_low"])


if __name__ == "__main__":
    unittest.main()
