"""Itartinai pigus skelbimas be palyginimo (retas modelis, Android su spejama verte): neatmetamas
aklai, bet siunciamas tik jei aprasyme parasyta, kad veikia, ir nera kitu rizikos pozymiu.
2026-10-08: i grupe pateko Huawei P40 Pro uz 65 € (48 % spejamos vertes)."""
import contextlib
import io
import unittest

from tests.helpers import reset_config, TempDir, item
from tests.test_flow import FakeClient, FakeTelegram
from vinted import config
from vinted.finder import Run
from vinted.risk import cheap_listing_problems

# Tik 4 skelbimai – palyginti per mazai (RANK_MIN_PEERS=8), vertinama pagal rinkos verte
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


class CheapNoRankTest(unittest.TestCase):
    def setUp(self):
        reset_config(HEARTBEAT_HOURS=0, MIN_SAMPLES=8, DEAL_MODE="rank", MIN_BATTERY=0)

    def test_working_and_clean_is_sent(self):
        """Pasitaiko ir veikiantis XR uz ~35 € (rinka ~70 €) – tokio neatmetam."""
        deal, log = run("iPhone XR", RARE_XR, "iPhone XR", 33, "Pilnai veikiantis, baterija 85%, siunčiu per Vinted")
        self.assertIsNotNone(deal, log)

    def test_not_said_working_is_rejected(self):
        deal, log = run("iPhone XR", RARE_XR, "iPhone XR", 33, "Parduodu, nes nebereikia, siunčiu per Vinted")
        self.assertIsNone(deal)
        self.assertIn("įtartinai pigu (nepraėjo papildomos patikros)", log)
        self.assertIn("neparašyta, kad veikia", log)

    def test_other_risk_signs_reject(self):
        works = "Telefonas veikia puikiai, siunčiu per Vinted"
        self.assertIsNotNone(run("iPhone 12 mini", RARE, "iPhone 12 mini", 70, works)[0])
        for desc, kwargs in [(works, {"user": NEW_SELLER}),             # nauja paskyra, be atsiliepimu
                             (works, {"photo_count": 1}),               # tik 1 nuotrauka
                             ("Veikia, rašykite WhatsApp", {}),         # ne per Vinted
                             ("Ne viskas veikia, siunčiu per Vinted", {})]:
            deal, log = run("iPhone 12 mini", RARE, "iPhone 12 mini", 70, desc, **kwargs)
            self.assertIsNone(deal, (desc, kwargs))
            self.assertIn("nepraėjo papildomos patikros", log)

    def test_guessed_value_checked_from_60_percent(self):
        """Kai verte tik spejama (patikimumas „l“), patikra nuo 60 %, ne 50 % (Xiaomi Mi 11 uz 60 €
        prie spejamos 110 €)."""
        deal, log = run("iPhone 12 mini", RARE, "iPhone 12 mini", 80, "Parduodu, nes nebereikia, siunčiu per Vinted")
        self.assertIsNone(deal, log)
        self.assertIn("nepraėjo papildomos patikros", log)
        deal, log = run("iPhone 12 mini", RARE, "iPhone 12 mini", 80, "Veikia puikiai, siunčiu per Vinted")
        self.assertIsNotNone(deal, log)
        self.assertEqual(deal["confidence"].code, "l")

    def test_normal_price_has_no_extra_check(self):
        deal, _ = run("iPhone 12 mini", RARE, "iPhone 12 mini", 100, "Parduodu, nes nebereikia, siunčiu per Vinted")
        self.assertIsNotNone(deal)

    def test_problems_helper(self):
        self.assertEqual(cheap_listing_problems("Veikia puikiai", []), [])
        self.assertEqual(cheap_listing_problems("Veikia", ["48% pigiau nei rinka – per gerai, kad būtų tiesa?",
                                                           "skubus pardavimas"]), [])
        self.assertEqual(cheap_listing_problems("Neveikia ekranas", []), ["aprašyme neparašyta, kad veikia"])
        self.assertEqual(cheap_listing_problems("Veikia", ["nauja paskyra (6 d.)"]), ["nauja paskyra (6 d.)"])


if __name__ == "__main__":
    unittest.main()
