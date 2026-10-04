"""Slamstas, kuris neturi patekti i grupe: priedai su telefono pavadinimu, itartinai pigus
retu modeliu skelbimai. Ir zema baterija, kai MIN_BATTERY=0 (2026-10-04 riba nuimta)."""
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


def run(query, market, title, price, desc):
    config.cfg["SEARCH_QUERIES"] = [query]
    with TempDir():
        tg = FakeTelegram()
        client = FakeClient({query: list(market) + [item(9, title, price, user_id=9)]}, pages={"9": desc})
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

    def test_no_rank_suspiciously_cheap(self):
        """Be palyginimo: < NO_RANK_REJECT_RATIO rinkos kainos – atmetama, net jei virs min. kainos."""
        deal, log = run("iPhone 12 mini", RARE, "iPhone 12 mini", 75, OK)
        self.assertIsNone(deal)
        self.assertIn("įtartinai pigu", log)
        self.assertIn("palyginimo nėra", log)
        deal, _ = run("iPhone 12 mini", RARE, "iPhone 12 mini", 100, OK)
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
