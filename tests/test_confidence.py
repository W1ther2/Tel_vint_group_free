# -*- coding: utf-8 -*-
"""Rinkos kainos patikimumas (vinted/confidence.py).

v48: atsargi verte = rinkos kaina x p20(pardavimo kaina / musu kaina) – VIENPUSE riba is
santykio SU ZENKLU. Pradines reiksmes (PRIOR) apskaicuotos tuo paciu budu
(vinted/dataset.py); ismokta reiksme – tik is PATVIRTINTU pardavimu su tuometiniu lygiu.
"""
import unittest

from tests.helpers import TempDir, item, reset_config
from tests.test_flow import FakeClient, FakeTelegram, market_items, run
from vinted import config
from vinted.confidence import PRIOR, assess, classify, learn_levels, pessimistic, progress, quantile
from vinted.dataset import rows, summarize
from vinted.market import Quote, spread_of


def q(source="skelbimai", n=40, spread=0.10, price=300):
    return Quote(price, n, source, True, spread)


class ClassifyTest(unittest.TestCase):
    def setUp(self):
        reset_config(CONFIDENCE_STRICT=True)

    def test_levels_follow_measured_thresholds(self):
        self.assertEqual(classify(q(n=40, spread=0.10))[0], "h")
        self.assertEqual(classify(q(n=40, spread=0.20))[0], "m")
        self.assertEqual(classify(q(n=15, spread=0.10))[0], "m", "imtis per maza aukstam")
        self.assertEqual(classify(q(n=40, spread=0.30))[0], "l")
        self.assertEqual(classify(q(n=5, spread=0.05))[0], "l")
        self.assertEqual(classify(q(spread=None))[0], "l")

    def test_sources(self):
        self.assertEqual(classify(q(source="apytikslė", n=2))[0], "l")
        self.assertEqual(classify(q(source="rankinė", n=-1, spread=None))[0], "m")
        self.assertEqual(classify(q(source="parduoti", n=35, spread=0.12))[0], "h")
        self.assertIn("parduotų", classify(q(source="parduoti", n=35, spread=0.12))[1])

    def test_prior_until_learned(self):
        c = assess(q(n=40, spread=0.20))
        self.assertEqual((c.code, c.factor, c.band, c.learned), ("m", PRIOR["m"]["factor"],
                                                                 PRIOR["m"]["band"], False))
        learned = assess(q(n=40, spread=0.20), {"m": {"factor": 0.85, "band": 0.12, "n": 25}})
        self.assertEqual((learned.factor, learned.band, learned.learned, learned.samples),
                         (0.85, 0.12, True, 25))
        old_format = assess(q(n=40, spread=0.20), {"m": {"band": 0.12}, "day": 5})
        self.assertFalse(old_format.learned, "v47 irasas be factor – naudojam pradini")

    def test_pessimistic_is_value_times_factor(self):
        self.assertAlmostEqual(pessimistic(300, assess(q(n=40, spread=0.20))),
                               300 * PRIOR["m"]["factor"])
        self.assertEqual(pessimistic(300, assess(q(n=40, spread=0.10))), 300 * PRIOR["h"]["factor"])
        above = assess(q(), {"h": {"factor": 1.0, "band": 0.1, "n": 30}})
        self.assertEqual(pessimistic(300, above), 300)
        config.cfg["CONFIDENCE_STRICT"] = False
        self.assertEqual(pessimistic(300, assess(q(n=5))), 300)

    def test_spread(self):
        self.assertIsNone(spread_of([100, 200]))
        self.assertEqual(spread_of([100, 100, 100]), 0)
        self.assertAlmostEqual(spread_of([90, 100, 110]), 0.0816, places=3)


def sold(i, ratio, code="m", confirmed=1):
    # v49 irasas: `qa` – musu kaina prasomu masteliu (q – verte, x0,85).
    return (f"vinted:{i}", {"m": "13", "st": "sold", "q": 85.0, "qa": 100.0, "p": 100.0 * ratio,
                            "qc": code, "sv": confirmed})


class LearnLevelsTest(unittest.TestCase):
    """Mokomasi tik is tiesos: patvirtinti pardavimai su TUO METU uzrasytu lygiu."""

    def setUp(self):
        reset_config(CONFIDENCE_MIN_SAMPLES=5)

    def test_signed_p20_not_absolute_error(self):
        """Kryptis svarbi. Dvi imtys su IDENTISKOMIS |r - 1| reiksmemis (veidrodines):
        - kai uzsidaro PIGIAU (0.70, 0.72, 0.74...) – atsargi verte x0.72 (2-asis is 10);
        - kai BRANGIAU (1.30, 1.28, 1.26...) – x0.97: mazinti beveik nera ko.
        Simetrine |klaida| siu imciu neatskirtu. Reiksmes SKIRTINGOS – kitaip kvantilio
        indekso klaida per viena pasislepia (taip buvo iki v48.1)."""
        cheaper = [0.70, 0.72, 0.74, 0.98, 0.99, 1.00, 1.01, 1.02, 1.03, 1.04]
        dearer = [round(2 - x, 2) for x in cheaper]
        down = learn_levels(dict(sold(i, r) for i, r in enumerate(cheaper)))["m"]
        up = learn_levels(dict(sold(i, r) for i, r in enumerate(dearer)))["m"]
        self.assertAlmostEqual(down["factor"], 0.72, places=3)
        self.assertAlmostEqual(up["factor"], 0.97, places=3)
        self.assertAlmostEqual(down["band"], up["band"], places=3, msg="|klaida| identiska")

    def test_factor_capped_at_one_and_floor(self):
        up = dict(sold(i, 1.5) for i in range(6))
        self.assertEqual(learn_levels(up)["m"]["factor"], 1.0)
        crash = dict(sold(i, 0.1) for i in range(6))
        self.assertEqual(learn_levels(crash)["m"]["factor"], 0.40)

    def test_only_confirmed_sales_teach(self):
        """Dinge (404, sv=0) galejo buti istrinti – mokymuisi netinka."""
        items = dict(sold(i, 0.5, confirmed=0) for i in range(30))
        self.assertNotIn("m", learn_levels(items))
        items.update(sold(100 + i, 0.95) for i in range(5))
        out = learn_levels(items)
        self.assertEqual((out["m"]["n"], out["m"]["factor"]), (5, 0.95))

    def test_needs_qc_from_that_time_and_enough_samples(self):
        items = dict(sold(i, 0.9) for i in range(4))
        self.assertNotIn("m", learn_levels(items))
        no_qc = {k: {**v, "qc": None} for k, v in dict(sold(i, 0.9) for i in range(10)).items()}
        self.assertEqual(learn_levels(no_qc, day=7), {"day": 7})
        excluded = {k: {**v, "x": "kalba"} for k, v in dict(sold(i, 0.9) for i in range(10)).items()}
        self.assertNotIn("m", learn_levels(excluded))

    def test_progress_counts_confirmed_with_level(self):
        items = dict([sold(1, 1.0, "h"), sold(2, 1.0, "m"), sold(3, 1.0, "m"), sold(4, 1.0, "l", 0)])
        self.assertEqual(progress(items), {"h": 1, "m": 2, "l": 0})


class QuantileTest(unittest.TestCase):
    """Nearest-rank apibrezimas, tikrinamas su SKIRTINGOMIS reiksmemis – su pasikartojanciomis
    (0.70, 0.70, 0.70...) ir neteisingas indeksas duoda ta pati atsakyma (taip buvo iki v48.1)."""

    def test_nearest_rank_definition(self):
        cases = [  # (n, p, laukiamas: ceil(p*n)-asis elementas is 1..n)
            (10, 0.2, 2), (10, 0.8, 8), (5, 0.2, 1), (5, 0.8, 4), (15, 0.2, 3), (34, 0.2, 7),
            (34, 0.8, 28), (54, 0.2, 11), (1, 0.2, 1), (1, 0.8, 1), (3, 0.5, 2), (4, 0.5, 2)]
        for n, p, want in cases:
            with self.subTest(n=n, p=p):
                self.assertEqual(quantile(list(range(1, n + 1)), p), want)

    def test_float_traps(self):
        for n, p in ((15, 0.2), (10, 0.7), (20, 0.35), (100, 0.29)):
            with self.subTest(n=n, p=p):
                self.assertEqual(quantile(list(range(1, n + 1)), p), round(p * n))

    def test_unsorted_input(self):
        self.assertEqual(quantile([5, 1, 4, 2, 3], 0.4), 2)


class BenchmarkTest(unittest.TestCase):
    """PRIOR ir ismokta reiksme skaiciuojamos tuo paciu budu – kitaip nepalyginami."""

    def test_summarize_matches_learning(self):
        reset_config(CONFIDENCE_MIN_SAMPLES=5)
        items = dict(sold(i, r) for i, r in enumerate([0.7, 0.8, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2]))
        bench = summarize(rows(items))["m"]
        learned = learn_levels(items)["m"]
        self.assertEqual((bench["factor"], bench["band"], bench["n"]),
                         (learned["factor"], learned["band"], learned["n"]))
        self.assertEqual(bench["factor"], quantile([0.7, 0.8, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2], 0.2))

    def test_rows_prefer_qc_and_fall_back_to_given_level(self):
        items = dict([sold(1, 1.0, "h"), sold(2, 0.9, None)])
        self.assertEqual(sorted(c for c, *_ in rows(items)), ["h"])
        self.assertEqual(sorted(c for c, *_ in rows(items, level_of=lambda e: "l")), ["h", "l"])
        self.assertEqual(len(rows(items, level_of=lambda e: "l", confirmed_only=True)), 2)

    def test_prior_table_is_consistent(self):
        """Prior'ai – is benchmarko (2026-10-01). „Aukštas“ neturi nei vieno pavyzdzio, tad
        BENDRAS: kol nera irodymu, kad jis tikslesnis, jam nesuteikiama nuolaida."""
        from vinted.dataset import PRIOR_MIN_SAMPLES
        self.assertTrue(all(0 < p["factor"] <= 1 for p in PRIOR.values()))
        for code, p in PRIOR.items():
            self.assertTrue(p["n"] >= PRIOR_MIN_SAMPLES or p.get("pooled"), code)

    def test_small_levels_fall_back_to_pooled(self):
        from vinted.dataset import priors
        samples = [("m", r, False) for r in (0.7, 0.8, 0.9, 1.0, 1.1) * 3] + [("h", 1.0, False)] * 3
        out = priors(samples)
        self.assertFalse(out["m"]["pooled"])
        self.assertTrue(out["h"]["pooled"])
        self.assertEqual((out["h"]["own_n"], out["h"]["n"]), (3, 18))
        self.assertTrue(out["l"]["pooled"])

    def test_manual_prices_never_teach(self):
        """Rankine kaina – ne musu ivertis, mastelis nezinomas (iki v49 – visas „aukštas“)."""
        reset_config(CONFIDENCE_MIN_SAMPLES=5)
        items = {k: {**v, "qs": "m"} for k, v in dict(sold(i, 0.85) for i in range(10)).items()}
        self.assertNotIn("m", learn_levels(items))
        self.assertEqual(rows(items, level_of=lambda e: "h"), [])


class StrictSelectionTest(unittest.TestCase):
    """Nuolaidos budu sprendima lemia tik rinkos kaina – atsargi verte = verte x p20."""

    def config(self, **over):
        reset_config(**{"SEARCH_QUERIES": ["iPhone 13"], "HEARTBEAT_HOURS": 0, "MIN_SAMPLES": 8,
                        "MARKET_PERCENTILE": 0.5, "MIN_DISCOUNT": 0.15, "MIN_PROFIT_EUR": 0,
                        "CONFIDENCE_STRICT": True, **over})

    def deal_run(self, market):
        with TempDir():
            cat = market + [item(1, "iPhone 13 128GB", 190, user_id=1)]
            tg = FakeTelegram()
            log = run(FakeClient({"iPhone 13": cat}), tg)
            return tg, log

    def test_unreliable_price_needs_bigger_discount(self):
        """12 skelbimu (vidutinis lygis, x0.79): ~20 % nuolaida nebetenkina."""
        self.config()
        tg, log = self.deal_run(market_items(n=12))
        self.assertEqual(tg.deals, [], log)
        self.assertIn("nepatikima rinkos kaina", log)
        self.assertIn("pradinė", log)

    def test_high_level_without_evidence_is_not_exempt(self):
        """40 panasiu kainu – aukštas lygis, bet jo tikslumo irodymu dar nera (prior bendras):
        ~25 % nuolaidos nepakanka; ~45 % – pakanka."""
        self.config()
        tg, log = self.deal_run(market_items(n=40, low=285, high=315))
        self.assertEqual(tg.deals, [], log)
        with TempDir():
            cat = market_items(n=40, low=285, high=315) + [item(1, "iPhone 13 128GB", 140, user_id=1)]
            tg = FakeTelegram()
            log = run(FakeClient({"iPhone 13": cat}), tg)
        self.assertEqual(len(tg.deals), 1, log)
        self.assertEqual(tg.deals[0][0]["confidence"].code, "h")

    def test_can_be_turned_off(self):
        self.config(CONFIDENCE_STRICT=False)
        tg, log = self.deal_run(market_items(n=12))
        self.assertEqual(len(tg.deals), 1, log)

    def test_rank_mode_is_not_affected(self):
        """v48 „pigiausiu“ (rank) logikos nekeicia: tas pats skelbimas su ta pacia rinka."""
        results = []
        for strict in (True, False):
            self.config(DEAL_MODE="rank", RANK_MIN_PEERS=8, RANK_TOP_PCT=0.15,
                        CONFIDENCE_STRICT=strict)
            tg, log = self.deal_run(market_items(n=12))
            results.append(len(tg.deals))
        self.assertEqual(results[0], results[1])
        self.assertGreater(results[0], 0)

    def test_card_shows_band_and_level(self):
        from vinted.telegram import format_card
        self.config(CONFIDENCE_STRICT=False)
        tg, _ = self.deal_run(market_items(n=12))
        text = format_card(tg.deals[0][0])
        self.assertIn("±30%", text)
        self.assertIn("Patikimumas:</b> vidutinis", text)


if __name__ == "__main__":
    unittest.main()
