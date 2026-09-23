"""Ar saltiniai tikrai dirba vienu metu – matuojant sieninio laikrodzio laika.

Testai tyciomis naudoja tikras (trumpas) pauzes: be ju lygiagretumo patikrinti
neimanoma, o su suklastotu miegu testas praeitu ir tada, kai viskas eina paeiliui.
"""
import contextlib
import io
import threading
import time
import unittest

from tests.fixtures import pirkpard_response
from tests.helpers import reset_config, TempDir
from tests.test_pirkpard import FakeApi
from vinted import config
from vinted.finder import Run
from vinted.sources.pirkpard import PirkpardSource
from vinted.sources.vinted_source import VintedSource

DELSA = 0.15        # kiek "trunka" viena uzklausa


class SlowVinted:
    """Netikras Vinted, kurio kiekviena uzklausa uztrunka."""
    last_error = ""
    blocked_queries = 0

    def __init__(self, pages=3, items=6):
        self.pages, self.items = pages, items
        self.gijos = set()
        self.tikrinti = []

    def start(self):
        pass

    def fetch_items(self, query, pages, seen=None):
        for _ in range(self.pages):
            time.sleep(DELSA)
            self.gijos.add(threading.current_thread().name)
        return [{"id": 5000 + i, "title": "iPhone 13 128GB",
                 "price": {"amount": "255", "currency_code": "EUR"},
                 "url": f"/items/{5000 + i}", "user": {"id": i}} for i in range(self.items)]

    def fetch_item_page(self, url):
        time.sleep(DELSA)
        self.gijos.add(threading.current_thread().name)
        self.tikrinti.append(url)
        return 200, '<meta property="og:description" content="Tvarkingas telefonas"/>', \
            f"https://www.vinted.lt{url}"

    def fetch_user(self, uid):
        return {"country_code": "LT"}


class SlowPirkpard(FakeApi):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.gijos = set()

    def get_json(self, params, tries=3):
        time.sleep(DELSA)
        self.gijos.add(threading.current_thread().name)
        return super().get_json(params)


class NoTelegram:
    def send_deal(self, *a, **k):
        return True

    def send_message(self, *a, **k):
        return True

    def answer_callback(self, *a, **k):
        return True

    def get_updates(self, offset):
        return [], [], offset


def paleisk(lygiagreciai, **over):
    reset_config(HEARTBEAT_HOURS=0, TELEGRAM_COMMANDS=False, DRY_RUN=True,
                 SLEEP_SECONDS=0, DETAIL_SLEEP_SECONDS=0, MIN_SAMPLES=100,
                 USE_TYPICAL_FALLBACK=False, USE_SOLD_PRICES=False,
                 SEARCH_QUERIES=["iPhone 13"], PIRKPARD_QUERIES=["iphone"],
                 PARALLEL_SOURCES=lygiagreciai, **over)
    vinted, pirkpard = SlowVinted(), SlowPirkpard()
    started = time.time()
    with contextlib.redirect_stdout(io.StringIO()) as out:
        Run([VintedSource(client=vinted), PirkpardSource(client=pirkpard)],
            NoTelegram(), sleep=lambda s: None).run()
    return time.time() - started, out.getvalue(), vinted, pirkpard


class ParallelScanTest(unittest.TestCase):
    def test_sources_overlap_in_time(self):
        with TempDir():
            paeiliui, _, _, _ = paleisk(False)
        with TempDir():
            lygiagreciai, log, vinted, pirkpard = paleisk(True)
        self.assertIn("Tikrinami lygiagreciai", log)
        # Lygiagreciai turi buti pastebimai greiciau nei paeiliui
        self.assertLess(lygiagreciai, paeiliui * 0.9,
                        f"paeiliui {paeiliui:.2f}s, lygiagreciai {lygiagreciai:.2f}s")

    def test_each_source_runs_in_its_own_thread(self):
        with TempDir():
            _, _, vinted, pirkpard = paleisk(True)
        self.assertTrue(vinted.gijos and pirkpard.gijos)
        self.assertFalse(vinted.gijos & pirkpard.gijos,
                         f"Vinted {vinted.gijos}, Pirkpard {pirkpard.gijos}")

    def test_sequential_mode_uses_one_thread(self):
        with TempDir():
            _, _, vinted, pirkpard = paleisk(False)
        self.assertEqual(vinted.gijos, pirkpard.gijos)


class ParallelSoldCheckTest(unittest.TestCase):
    """Pardavimu patikra taip pat neturi laukti vieno saltinio."""

    def prepare_state(self, market):
        from vinted.util import today
        diena = today() - 5
        for uid, model in market:
            market_entry = {"m": model, "s": "128 GB", "p": 200, "f": diena, "l": diena,
                            "c": diena, "st": "active"}
            if uid.startswith("pirkpard"):
                market_entry["u"] = "https://pirkpard.lt/lt/product/x"
            yield uid, market_entry

    def run_check(self, lygiagreciai):
        reset_config(HEARTBEAT_HOURS=0, TELEGRAM_COMMANDS=False, DRY_RUN=True,
                     SLEEP_SECONDS=0, DETAIL_SLEEP_SECONDS=0, MIN_SAMPLES=100,
                     USE_TYPICAL_FALLBACK=False, USE_SOLD_PRICES=True,
                     SOLD_CHECK_AFTER_DAYS=1, SOLD_CHECKS_PER_RUN=8,
                     SEARCH_QUERIES=["iPhone 13"], PIRKPARD_QUERIES=["iphone"],
                     PARALLEL_SOURCES=lygiagreciai)
        vinted_client = SlowVinted(pages=1, items=0)
        pirkpard_client = SlowPirkpard()
        vinted = VintedSource(client=vinted_client)
        pirkpard = PirkpardSource(client=pirkpard_client)
        run = Run([vinted, pirkpard], NoTelegram(), sleep=lambda s: None)
        from vinted.state import State
        run.state = State()
        for uid, entry in self.prepare_state(
                [(f"vinted:{i}", "13") for i in range(4)] +
                [(f"pirkpard:{3748 + i}", "13") for i in range(4)]):
            run.state.market.items[uid] = entry
        started = time.time()
        with contextlib.redirect_stdout(io.StringIO()):
            run.check_sold()
        return time.time() - started, vinted_client, pirkpard_client

    def test_sold_check_runs_in_parallel(self):
        with TempDir():
            paeiliui, _, _ = self.run_check(False)
        with TempDir():
            lygiagreciai, vinted_client, pirkpard_client = self.run_check(True)
        self.assertTrue(vinted_client.tikrinti)          # Vinted tikrino skelbimus
        self.assertTrue(pirkpard_client.calls)           # Pirkpard irgi
        self.assertLess(lygiagreciai, paeiliui * 0.9,
                        f"paeiliui {paeiliui:.2f}s, lygiagreciai {lygiagreciai:.2f}s")

    def test_pirkpard_needs_one_request_for_all(self):
        """Pirkpard busena visiems gaunama viena uzklausa – jis neturi lukuriuoti."""
        with TempDir():
            _, _, pirkpard_client = self.run_check(True)
        self.assertEqual(len(pirkpard_client.calls), 1)


if __name__ == "__main__":
    unittest.main()
