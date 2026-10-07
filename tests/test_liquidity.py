# -*- coding: utf-8 -*-
"""Pardavimo FAKTAS (vinted/liquidity.py) – atskirai nuo kainos.

Sandorio kainos Vinted nerodo, bet „parduota“ puslapyje – patikimas faktas. Svarbiausia:
„dingo“ (404) neturi tapti „parduota“ pro kitas duris – tokia baigtis nezinoma."""
import unittest

from tests.helpers import reset_config
from vinted.liquidity import bucket_of, describe, learn, outcome

DAY = 100


def e(first=90, st="active", sv=None, sd=None, last=99, p=100.0, qa=100.0, **extra):
    out = {"m": "13", "f": first, "l": last, "st": st, "p": p, "qa": qa, "qs": "s"}
    if sv is not None:
        out["sv"] = sv
    if sd is not None:
        out["sd"] = sd
    out.update(extra)
    return out


class OutcomeTest(unittest.TestCase):
    def test_confirmed_sale_within_window(self):
        self.assertEqual(outcome(e(st="sold", sv=1, sd=95), DAY, 7), 1)

    def test_confirmed_sale_after_window_counts_as_not_sold_in_window(self):
        self.assertEqual(outcome(e(first=80, st="sold", sv=1, sd=95), DAY, 7), 0)

    def test_gone_is_unknown_not_sold(self):
        self.assertIsNone(outcome(e(st="sold", sv=0, sd=95), DAY, 7))
        self.assertIsNone(outcome(e(st="sold", sd=95), DAY, 7))        # senas, be sv
        self.assertIsNone(outcome(e(st="gone"), DAY, 7))

    def test_still_listed_is_not_sold(self):
        self.assertEqual(outcome(e(last=99), DAY, 7), 0)

    def test_out_of_sight_is_unknown_unless_page_confirmed_active(self):
        self.assertIsNone(outcome(e(last=92), DAY, 7))
        self.assertEqual(outcome(e(last=92, ca=99), DAY, 7), 0)

    def test_too_young_is_unknown(self):
        self.assertIsNone(outcome(e(first=96, st="sold", sv=1, sd=97), DAY, 7))


class LearnTest(unittest.TestCase):
    def setUp(self):
        # MIN_SALES=1 – sie testai tikrina stebejimu riba; pardavimu ribai yra
        # atskira klase (EnoughNeedsSalesTest).
        reset_config(SALE_FACT_DAYS=7, SALE_FACT_MIN_SAMPLES=3, SALE_FACT_MIN_SALES=1)

    def test_buckets_and_rates(self):
        items = {"a": e(p=80, st="sold", sv=1, sd=95), "b": e(p=82), "c": e(p=84),
                 "d": e(p=130), "x": e(p=80, st="sold", sv=0, sd=95)}
        out = learn(items, DAY)
        self.assertEqual(bucket_of(0.8), "<0,85")
        self.assertEqual(out["<0,85"], {"n": 3, "sold": 1, "rate": 0.3333, "enough": True})
        self.assertEqual(out[">1,20"]["n"], 1)
        self.assertFalse(out[">1,20"]["enough"])
        self.assertEqual(out["unknown"], 1)

    def test_manual_and_excluded_skipped(self):
        items = {"m": e(qs="m"), "x": e(x="kalba"), "ok": e()}
        out = learn(items, DAY)
        self.assertEqual(sum(g["n"] for k, g in out.items() if isinstance(g, dict)), 1)

    def test_old_entries_without_qa_use_derived_scale(self):
        old = {"m": "13", "f": 90, "l": 99, "st": "active", "p": 200.0, "q": 170.0, "qs": "s", "qf": 0.85}
        self.assertEqual(learn({"o": old}, DAY)["0,95–1,05"]["n"], 1)

    def test_describe(self):
        self.assertIn("dar nera duomenu", describe(learn({}, DAY)))
        self.assertIn("<0,85: 0/1*", describe(learn({"b": e(p=80)}, DAY)))



class EnoughNeedsSalesTest(unittest.TestCase):
    """„enough" turi reiksti, kad rodikliu galima tiketi.

    Gyvai (2026-10-07) pirma versija rode:

        "<0,85":    n=37, sold=0, rate=0.0,    enough=true
        "0,85-0,95": n=31, sold=0, rate=0.0,    enough=true
        "0,95-1,05": n=24, sold=2, rate=0.0833, enough=false

    Trys ketvirciai grupiu skelbesi „pakanka", nors is viso visose grupese buvo
    3 pardavimai. 0 is 37 nereiskia „neparsiduoda" – reiskia „dar neturim ko
    matuoti". Santykiu besiremiantis rodiklis negali buti laikomas ismatuotu,
    kol nera pakankamai PACIU IVYKIU, kuriu santykis imamas. Pavojus realus:
    jei sprendimas butu priimtas pagal rate=0.0, visa pigiausiu zona butu
    paskelbta neparduodama remiantis nuliu irodymu."""

    def setUp(self):
        reset_config(SALE_FACT_DAYS=7, SALE_FACT_MIN_SAMPLES=3, SALE_FACT_MIN_SALES=2)

    def test_many_observations_but_no_sales_is_not_enough(self):
        items = {str(i): e(p=80) for i in range(10)}          # 10 stebejimu, 0 pardavimu
        out = learn(items, DAY)
        self.assertEqual(out["<0,85"]["n"], 10)
        self.assertEqual(out["<0,85"]["sold"], 0)
        self.assertFalse(out["<0,85"]["enough"], "0 pardavimu nera matavimas")

    def test_enough_once_there_are_real_sales(self):
        items = {"a": e(p=80, st="sold", sv=1, sd=95), "b": e(p=81, st="sold", sv=1, sd=95),
                 "c": e(p=82), "d": e(p=83)}
        out = learn(items, DAY)
        self.assertEqual((out["<0,85"]["n"], out["<0,85"]["sold"]), (4, 2))
        self.assertTrue(out["<0,85"]["enough"])

    def test_sales_alone_are_not_enough_either(self):
        """Riba abipuse: 2 pardavimai is 2 stebejimu nera 100 % parduodamumas."""
        items = {"a": e(p=80, st="sold", sv=1, sd=95), "b": e(p=81, st="sold", sv=1, sd=95)}
        out = learn(items, DAY)
        self.assertEqual(out["<0,85"]["rate"], 1.0)
        self.assertFalse(out["<0,85"]["enough"], "per mazai stebejimu")

    def test_describe_shows_the_total_number_of_confirmations(self):
        """Log'e turi matytis, kiek IS VISO patvirtintu – tai skaicius, pagal kuri
        sprendziama, ar visa lentele dar yra triuksmas."""
        items = {"a": e(p=80, st="sold", sv=1, sd=95), "b": e(p=81)}
        text = describe(learn(items, DAY))
        self.assertIn("is viso 1 patvirtintu", text)


class RunComputesSaleFactTest(unittest.TestCase):
    """v49 klaida: faktas skaiciuotas tik uz patikimumo „jau siandien“ patikros. Gyvoje
    busenoje ta zyma siandienai jau buvo (is v47/v48), tad sale_fact liko {}."""

    def setUp(self):
        reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8,
                     MARKET_PERCENTILE=0.5, MIN_DISCOUNT=0.15, MIN_PROFIT_EUR=0)

    def test_computed_even_when_confidence_already_learned_today(self):
        import json
        from tests.helpers import TempDir
        from tests.test_flow import FakeClient, FakeTelegram, market_items, run
        from vinted.util import today
        with TempDir():
            client = FakeClient({"iPhone 13": market_items()})
            run(client, FakeTelegram())
            with open("state.json", encoding="utf-8") as f:
                state = json.load(f)
            # kaip gyvai: patikimumas siandien jau skaiciuotas, faktas – dar ne
            state["market"]["confidence_bands"] = {"day": today()}
            state["market"]["sale_fact"] = {}
            with open("state.json", "w", encoding="utf-8") as f:
                json.dump(state, f)
            log = run(client, FakeTelegram())
            with open("state.json", encoding="utf-8") as f:
                fact = json.load(f)["market"]["sale_fact"]
            self.assertEqual(fact.get("day"), today(), log)
            self.assertIn("window", fact)
            self.assertIn("Pardavimo faktas", log)
            # ta pacia diena antra karta neskaiciuojama
            log = run(client, FakeTelegram())
            self.assertNotIn("Pardavimo faktas", log)

if __name__ == "__main__":
    unittest.main()
