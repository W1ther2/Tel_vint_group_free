"""Patikimumas: kas nutinka, kai kazkas nepavyksta (2026-09 kodo perziura).

- skelbimo puslapis neatsidaro -> dealas nesiunciamas aklai, bandoma veliau;
- saltinis nustoja veikti -> apie tai pranesama (ir ne kas 30 min.);
- kodas nuluzta -> busena issaugoma, klaida pranesama ne dazniau nei kas valanda.
"""
import json
import unittest

from tests.helpers import reset_config, TempDir, item
from tests.test_flow import FakeClient, FakeTelegram, market_items, run


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def flow_config(**over):
    reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8, MARKET_PERCENTILE=0.5,
                 MIN_DISCOUNT=0.15, LOUD_DISCOUNT=0.30, **over)


class DetailRetryTest(unittest.TestCase):
    def setUp(self):
        flow_config()

    def test_failed_page_not_sent_and_retried(self):
        with TempDir():
            cat = market_items() + [item(8, "iPhone 13 128GB", 170, user_id=908)]
            # aprasyme parasyta, kad uzblokuotas, bet puslapio gauti nepavyksta
            client = FakeClient({"iPhone 13": cat}, pages={"8": "iCloud užblokuotas"},
                                statuses={"8": (403, "<html><title>Forbidden</title></html>")})
            tg = FakeTelegram()
            log = run(client, tg)
            self.assertEqual(tg.deals, [], log)
            self.assertNotIn("vinted:8", read_json("seen.json"))
            self.assertIn("skelbimo atidaryti nepavyko – bandysiu vėliau", log)

            # kitas paleidimas: puslapis atsidaro -> aprasymas patikrinamas ir skelbimas atmetamas
            client.statuses = {}
            tg2 = FakeTelegram()
            log2 = run(client, tg2)
            self.assertEqual(tg2.deals, [], log2)
            self.assertIn("neveikiantis / užrakintas / netestuotas", log2)
            self.assertNotIn("vinted:8", read_json("state.json").get("detail_failures", {}))

    def test_good_deal_sent_after_page_opens(self):
        with TempDir():
            cat = market_items() + [item(9, "iPhone 13 128GB", 170, user_id=909)]
            client = FakeClient({"iPhone 13": cat}, statuses={"9": (0, "")})
            run(client, FakeTelegram())
            client.statuses = {}
            tg = FakeTelegram()
            run(client, tg)
            self.assertEqual([d["id"] for d, _ in tg.deals], ["vinted:9"])

    def test_gives_up_after_retries(self):
        with TempDir():
            cat = market_items() + [item(8, "iPhone 13 128GB", 170, user_id=908)]
            client = FakeClient({"iPhone 13": cat}, statuses={"8": (403, "")})
            for _ in range(3):
                run(client, FakeTelegram())
            self.assertIn("vinted:8", read_json("seen.json"))
            client.page_requests.clear()
            run(client, FakeTelegram())
            self.assertNotIn("8", client.page_requests)

    def test_challenge_page_counts_as_failure(self):
        """200, bet ne skelbimo puslapis (be og zymu) – ne „aktyvus be aprasymo“."""
        with TempDir():
            cat = market_items() + [item(8, "iPhone 13 128GB", 170, user_id=908)]
            client = FakeClient({"iPhone 13": cat},
                                statuses={"8": (200, "<html><body>Checking your browser...</body></html>")})
            tg = FakeTelegram()
            run(client, tg)
            self.assertEqual(tg.deals, [])
            self.assertNotIn("vinted:8", read_json("seen.json"))



class SourceHealthTest(unittest.TestCase):
    """Vinted neveikia, o Pirkpard veikia – anksciau apie tai nebuvo pranesama visai."""

    def setUp(self):
        reset_config(SOURCES=["vinted", "pirkpard"], HEARTBEAT_HOURS=0, VINTED_BROWSE_ALL=True,
                     FAIL_ALERT_RUNS=3, SOURCE_ALERT_HOURS=12)

    def sources(self, vinted_ok):
        from tests.fixtures import pirkpard_response
        from vinted.sources.pirkpard import PirkpardSource
        from vinted.sources.vinted_source import VintedSource

        class Pirk:
            last_error, blocked = "", ""

            def start(self):
                pass

            def sleep(self, s):
                pass

            def get_json(self, params):
                return pirkpard_response()

        catalog = {"iphone": market_items()} if vinted_ok else {}
        vinted = FakeClient(catalog)
        if not vinted_ok:
            vinted.last_error = "HTTP 403 (api.vinted.lt): Forbidden"
        return [VintedSource(client=vinted), PirkpardSource(client=Pirk())]

    def one_run(self, vinted_ok):
        import contextlib
        import io
        from vinted.finder import Run
        tg = FakeTelegram()
        with contextlib.redirect_stdout(io.StringIO()):
            Run(self.sources(vinted_ok), tg, sleep=lambda s: None).run()
        return tg.messages

    def test_blocked_vinted_reported_once_and_recovery(self):
        with TempDir():
            sent = [self.one_run(vinted_ok=False) for _ in range(6)]
            warnings = [i for i, msgs in enumerate(sent) if any("Vinted" in m and "ISPEJIMAS" in m for m in msgs)]
            self.assertEqual(warnings, [2])                       # po 3 paleidimu ir tik viena karta
            self.assertIn("403", next(m for m in sent[2] if "ISPEJIMAS" in m))
            back = self.one_run(vinted_ok=True)
            self.assertTrue(any("Vinted vėl veikia" in m for m in back), back)
            self.assertEqual(read_json("state.json")["source_zero"], {})


class CrashTest(unittest.TestCase):
    def setUp(self):
        flow_config()

    def test_source_crash_does_not_lose_state(self):
        """Vieno saltinio rezime klaida anksciau nutraukdavo visa paleidima be issaugojimo."""
        class Broken(FakeClient):
            def fetch_items(self, query, pages, seen=None):
                raise ValueError("netiketas API formatas")

        with TempDir():
            tg = FakeTelegram()
            run(Broken({}), tg)                                  # neiskrenta
            state = read_json("state.json")
            self.assertEqual(state["source_zero"], {"vinted": 1})
            self.assertTrue(any("nepasiekiamas" in m and "ValueError" in m for m in tg.messages))

    def test_side_step_error_reported_hourly(self):
        from vinted.finder import Run

        class Breaks(Run):
            def calibrate(self):
                raise RuntimeError("kalibravimas sulūžo")

        import contextlib
        import io
        with TempDir():
            client = FakeClient({"iPhone 13": market_items()})
            for i in range(2):
                tg = FakeTelegram()
                with contextlib.redirect_stdout(io.StringIO()):
                    Breaks(client, tg, sleep=lambda s: None).run()
                msgs = [m for m in tg.messages if "Dalis darbų nepavyko" in m]
                self.assertEqual(len(msgs), 1 if i == 0 else 0)
            self.assertTrue(read_json("seen.json"))               # busena issaugota

    def test_main_exit_codes_and_crash_throttle(self):
        import contextlib
        import io
        from unittest import mock
        from vinted import config, finder

        with TempDir():
            tg = FakeTelegram()
            with mock.patch.object(config, "BOT_TOKEN", ""), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(finder.main(), 2)
            with mock.patch.object(config, "BOT_TOKEN", "x"), mock.patch.object(config, "CHAT_ID", "1"), \
                    mock.patch.object(finder, "Telegram", lambda: tg), \
                    mock.patch.object(finder.Run, "scan_all", side_effect=KeyError("bloga")), \
                    mock.patch.object(config, "load", lambda *a, **k: config.cfg), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(finder.main(), 1)
                self.assertEqual(finder.main(), 1)
            crash = [m for m in tg.messages if "SKRIPTAS UZLUZO" in m]
            self.assertEqual(len(crash), 1)                         # antras – ne anksciau nei po valandos
            self.assertIn("KeyError", crash[0])


class ManualPriceTest(unittest.TestCase):
    def test_stale_manual_price_warned_daily(self):
        flow_config(MARKET_PRICES={"13": 180})
        with TempDir():
            client = FakeClient({"iPhone 13": market_items()})     # rinka ~260–340 €
            tg = FakeTelegram()
            run(client, tg)
            warn = [m for m in tg.messages if "Rankinė kaina gal pasenusi" in m]
            self.assertEqual(len(warn), 1)
            self.assertIn("/kaina 13 trinti", warn[0])
            tg2 = FakeTelegram()
            run(client, tg2)
            self.assertFalse(any("Rankinė kaina" in m for m in tg2.messages))

    def test_config_has_no_example_price(self):
        from pathlib import Path
        with open(Path(__file__).resolve().parent.parent / "config.json", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["MARKET_PRICES"], {})


if __name__ == "__main__":
    unittest.main()
