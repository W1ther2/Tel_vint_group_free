# -*- coding: utf-8 -*-
"""Modeliu pasirinkimas: apie kuriuos iPhone siusti (/modeliai, config MODELS).

Nesekamas modelis atmetamas DAR PRIES skelbimo puslapi ir pries pardavejo salies
uzklausa – ribotos uzklausos (SELLER_COUNTRY_LOOKUPS) atitenka tiems modeliams,
kurie tikrai rupi.
"""
import json
import unittest

from tests.helpers import reset_config, TempDir, item
from tests.test_flow import FakeClient, FakeTelegram, run
from vinted import config
from vinted.commands import handle
from vinted.phone import wanted_models, model_wanted
from vinted.state import State


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class ParsingTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def set_models(self, args):
        state = State()
        reply = handle(f"/modeliai {args}", state)
        return state.overrides.get("MODELS"), reply

    def test_commas_and_spaces(self):
        for args in ("13, 14, 15", "13 14 15", "13 ir 14 ir 15", "13;14;15"):
            models, _ = self.set_models(args)
            self.assertEqual(models, ["13", "14", "15"], args)

    def test_multiword_models(self):
        models, _ = self.set_models("13 Pro Max, xs max, 8 plus, 16e")
        self.assertEqual(models, ["13 Pro Max", "XS Max", "8 Plus", "16e"])

    def test_without_commas_multiword(self):
        models, _ = self.set_models("13 pro max 14 pro 15")
        self.assertEqual(models, ["13 Pro Max", "14 Pro", "15"])

    def test_from_model_onwards(self):
        models, reply = self.set_models("nuo 16")
        self.assertEqual(models, ["16", "16 Plus", "16 Pro", "16 Pro Max", "17e", "17",
                                  "Air", "17 Pro", "17 Pro Max"], reply)
        self.assertNotIn("Galaxy S24", models)

    def test_from_plus_extra(self):
        models, _ = self.set_models("nuo 17, 13 Pro")
        self.assertIn("13 Pro", models)
        self.assertIn("17 Pro Max", models)
        self.assertNotIn("14", models)

    def test_duplicates_removed(self):
        models, _ = self.set_models("13, 13, 13 pro, nuo 17 Pro")
        self.assertEqual(models, ["13", "13 Pro", "17 Pro", "17 Pro Max"])

    def test_from_model_stays_within_brand(self):
        """„nuo 17 Pro“ – tik Apple, o ne „ir visi Samsung, kurie sarase toliau“."""
        models, _ = self.set_models("nuo 17 Pro")
        self.assertEqual(models, ["17 Pro", "17 Pro Max"])
        models, _ = self.set_models("nuo Galaxy S25")
        self.assertTrue(all(m.startswith("Galaxy") for m in models), models)
        self.assertIn("Galaxy S25 Ultra", models)

    def test_whole_brand(self):
        models, _ = self.set_models("samsung")
        self.assertTrue(models and all(m.startswith("Galaxy") for m in models), models)
        models, _ = self.set_models("xiaomi, 13")
        self.assertIn("13", models)
        self.assertIn("Xiaomi 14 Ultra", models)

    def test_unknown_model_explains(self):
        state = State()
        reply = handle("/modeliai nokia 3310", state)
        self.assertIn("Neteisinga reikšmė", reply)
        self.assertIsNone(state.overrides.get("MODELS"))

    def test_show_and_reset(self):
        state = State()
        self.assertIn("visi", handle("/modeliai", state))
        handle("/modeliai nuo 15", state)
        self.assertIn("iPhone 15 Pro Max", handle("/modeliai", state))
        self.assertIn("visų modelių", handle("/modeliai visi", state))
        self.assertEqual(state.overrides["MODELS"], [])
        self.assertIn("visi", handle("/modeliai", state))


class WantedModelsTest(unittest.TestCase):
    def test_empty_means_all(self):
        reset_config(MODELS=[])
        self.assertEqual(wanted_models(), [])
        self.assertTrue(model_wanted("8"))
        self.assertTrue(model_wanted("17 Pro Max"))

    def test_config_values_normalised(self):
        reset_config(MODELS=["13 pro max", "XS MAX", "14"])
        self.assertEqual(wanted_models(), ["13 Pro Max", "XS Max", "14"])
        self.assertTrue(model_wanted("13 Pro Max"))
        self.assertFalse(model_wanted("13"))

    def test_junk_values_ignored(self):
        reset_config(MODELS=["13", "kazkas", ""])
        self.assertEqual(wanted_models(), ["13"])


class FlowTest(unittest.TestCase):
    """Nesekami modeliai neturi nei siuntimo, nei uzklausu."""

    def setUp(self):
        reset_config(SEARCH_QUERIES=["iPhone"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8,
                     MARKET_PERCENTILE=0.5, MIN_DISCOUNT=0.15, MIN_PROFIT_EUR=0, MIN_BATTERY=0)

    def catalog(self):
        # dvi rinkos: po 10 skelbimu kiekvienam modeliui, kad uztektu kainu
        rows = [item(1000 + i, "iPhone 13 128GB", 300 + i * 4, user_id=500 + i) for i in range(10)]
        rows += [item(2000 + i, "iPhone 11 64GB", 150 + i * 3, user_id=600 + i) for i in range(10)]
        rows += [item(1, "iPhone 13 128GB", 190, user_id=1),      # dealas – sekamas modelis
                 item(2, "iPhone 11 64GB", 95, user_id=2)]        # dealas – nesekamas modelis
        return rows

    def test_only_selected_models_sent(self):
        config.cfg["MODELS"] = ["13"]
        with TempDir():
            client = FakeClient({"iPhone": self.catalog()})
            tg = FakeTelegram()
            log = run(client, tg)
            self.assertEqual([d["id"] for d, _ in tg.deals], ["vinted:1"], log)
            self.assertIn("nesekamas modelis", log)

    def test_all_models_when_empty(self):
        config.cfg["MODELS"] = []
        with TempDir():
            client = FakeClient({"iPhone": self.catalog()})
            tg = FakeTelegram()
            run(client, tg)
            self.assertEqual({d["id"] for d, _ in tg.deals}, {"vinted:1", "vinted:2"})

    def test_no_seller_lookups_for_unwanted_models(self):
        from tests.test_country import CountingClient
        config.cfg.update({"MODELS": ["13"], "FILTER_BY_COUNTRY": True,
                           "ALLOWED_COUNTRY_CODES": ["LT"], "SELLER_COUNTRY_LOOKUPS": 40})
        with TempDir():
            client = CountingClient({"iPhone": self.catalog()})
            run(client, FakeTelegram())
            # 11-uju pardaveju (600..609 ir 2) neturi buti klausta
            unwanted = [u for u in client.user_requests if u == 2 or 600 <= u <= 609]
            self.assertEqual(unwanted, [], client.user_requests)
            self.assertTrue([u for u in client.user_requests if 500 <= u <= 509])

    def test_unwanted_model_costs_no_detail_page(self):
        config.cfg["MODELS"] = ["13"]
        with TempDir():
            client = FakeClient({"iPhone": self.catalog()})
            run(client, FakeTelegram())
            self.assertNotIn("2", client.page_requests)
            self.assertIn("1", client.page_requests)


if __name__ == "__main__":
    unittest.main()
