# -*- coding: utf-8 -*-
"""Paleidimo diagnostika: kodel si karta nieko neatejo.

Problema, kuria tai sprendzia, yra ne kosmetine. Bendroje suvestineje trys
visiskai skirtingos situacijos atrodydavo vienodai:

    saltinis sulūzo       gauta 0, yra klaida   -> taisyti koda ar keisti IP
    filtrai atmeta        gauta daug, 0 issiusta -> ziureti, KURI priezastis
    nieko naujo nebuvo    gauta daug, nauju 0    -> viskas gerai

Pirma ir trecia tylejo lygiai taip pat. 2026-10-05 butent del to „61 % be
salies" buvo palaikyta uzklausu badejimu, nors filtras veike teisingai – tie
skelbimai buvo lenkiski ir juos TEISINGAI nustume i gala.

Antra bėda: Telegram klaidos buvo tik spausdinamos. „0 pranesimu" galejo
reiksti ir „nebuvo ka siusti", ir „Telegram atmete viska" – o reaguoti i tai
reikia priesingai.
"""

import contextlib
import io
import unittest

from tests.helpers import reset_config, TempDir, item
from tests.test_flow import FakeClient, FakeTelegram, market_items
from vinted.finder import Run


def diagnose(client, tg, **cfg):
    """Paleidzia ir grazina (visas logas, DIAGNOSTIKA dalis)."""
    reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8,
                 MARKET_PERCENTILE=0.5, MIN_DISCOUNT=0.15, **cfg)
    with TempDir():
        run = Run(client, tg, sleep=lambda s: None)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            run.run()
        log = out.getvalue()
    block = log.split("DIAGNOSTIKA", 1)
    return run, log, ("DIAGNOSTIKA" + block[1]) if len(block) > 1 else ""


class TellsTheThreeCasesApartTest(unittest.TestCase):
    def test_source_broken_says_so(self):
        """Gauta 0 ir yra klaida – tai gedimas, ne tyli diena."""
        class Broken(FakeClient):
            def fetch_items(self, query, pages, seen=None):
                raise RuntimeError("403 Forbidden")

        _, _, diag = diagnose(Broken({}), FakeTelegram())
        self.assertIn("gauta 0", diag)
        self.assertIn("403", diag)

    def test_nothing_new_is_not_reported_as_a_fault(self):
        """Gauta daug, nauju 0 – viskas gerai, nieko daryti nereikia."""
        cat = market_items()
        tg = FakeTelegram()
        diagnose(FakeClient({"iPhone 13": cat}), tg)          # pirmas – viska pamato
        _, _, diag = diagnose(FakeClient({"iPhone 13": cat}), FakeTelegram())
        self.assertIn("gauta 20", diag)
        self.assertNotIn("negauta nė vieno", diag)

    def test_filters_rejecting_shows_which_reason(self):
        """Gauta daug, issiusta 0 – turi matytis, KURI priezastis didziausia."""
        cat = market_items() + [item(1, "Dėklas iPhone 13", 15, user_id=1),
                                item(2, "Dėklas iPhone 13", 16, user_id=2)]
        _, _, diag = diagnose(FakeClient({"iPhone 13": cat}), FakeTelegram())
        self.assertIn("atmesta", diag)
        self.assertIn("ne telefonas", diag)


class PerSourceTest(unittest.TestCase):
    def test_counters_are_not_mixed_between_sources(self):
        """Visa esme: vieno saltinio atmetimai neturi slėptis kito skaiciuose."""
        reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8,
                     MARKET_PERCENTILE=0.5, MIN_DISCOUNT=0.15)
        with TempDir():
            run = Run(FakeClient({"iPhone 13": market_items()}), FakeTelegram(),
                      sleep=lambda s: None)
            with contextlib.redirect_stdout(io.StringIO()):
                run.run()
            self.assertEqual(len(run.diag), 1)
            d = next(iter(run.diag.values()))
            self.assertEqual(d["fetched"], 20)
            self.assertEqual(sum(d["rejects"].values()) > 0, True)

    def test_totals_still_match_the_per_source_sum(self):
        """Bendra suvestine nedingo – ji turi sutapti su dalimis."""
        reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8,
                     MARKET_PERCENTILE=0.5, MIN_DISCOUNT=0.15)
        with TempDir():
            run = Run(FakeClient({"iPhone 13": market_items()}), FakeTelegram(),
                      sleep=lambda s: None)
            with contextlib.redirect_stdout(io.StringIO()):
                run.run()
            per_source = {}
            for d in run.diag.values():
                for why, n in d["rejects"].items():
                    per_source[why] = per_source.get(why, 0) + n
            for why, n in per_source.items():
                self.assertEqual(run.totals.get(why), n, why)

    def test_diag_is_saved_for_the_next_run(self):
        """Irasoma i last_run – kad butu su kuo palyginti kita karta."""
        reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8,
                     MARKET_PERCENTILE=0.5, MIN_DISCOUNT=0.15)
        with TempDir():
            run = Run(FakeClient({"iPhone 13": market_items()}), FakeTelegram(),
                      sleep=lambda s: None)
            with contextlib.redirect_stdout(io.StringIO()):
                run.run()
            self.assertIn("diag", run.state.last_run)
            self.assertIn("telegram", run.state.last_run)


class TelegramErrorsTest(unittest.TestCase):
    def test_silence_because_of_telegram_is_visible(self):
        """Skirtumas, del kurio tai daroma: tylu NE todel, kad nebuvo pasiulymu."""
        cat = market_items() + [item(1, "iPhone 13 128GB", 150, user_id=1)]
        tg = FakeTelegram(fail_sends=True)
        _, _, diag = diagnose(FakeClient({"iPhone 13": cat}), tg)
        self.assertIn("Telegram", diag)
        self.assertIn("atmete (403)", diag)

    def test_quiet_run_says_there_were_no_errors(self):
        _, _, diag = diagnose(FakeClient({"iPhone 13": market_items()}), FakeTelegram())
        self.assertIn("klaidų nėra", diag)

    def test_failed_send_is_not_counted_as_sent(self):
        cat = market_items() + [item(1, "iPhone 13 128GB", 150, user_id=1)]
        tg = FakeTelegram(fail_sends=True)
        run, _, _ = diagnose(FakeClient({"iPhone 13": cat}), tg)
        self.assertEqual(sum(d["sent"] for d in run.diag.values()), 0)


class MoneyFormatTest(unittest.TestCase):
    """Absurdiska kaina neturi isstumti is eilutes to, del ko ji rasoma."""

    def test_short_for_normal_prices(self):
        from vinted.util import money
        self.assertEqual(money(349), "349€")
        self.assertEqual(money(349.6), "350€")

    def test_absurd_prices_are_compressed(self):
        from vinted.util import money
        self.assertEqual(money(99_999_999), "100.0 mln. €")
        self.assertEqual(money(45_000), "45 tūkst. €")

    def test_broken_numbers_do_not_crash_the_log(self):
        from vinted.util import money
        self.assertEqual(money(float("nan")), "?")
        self.assertEqual(money(float("inf")), "?")
        self.assertEqual(money(None), "?")
        self.assertEqual(money("abc"), "?")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
