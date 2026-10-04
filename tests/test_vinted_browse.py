"""Vinted narsymo veiksena: viena paieska „iphone“ vietoj 34 atskiru.

Tikrinta gyvai su api.vinted.lt: `search_text=iphone&order=newest_first` grazina
visus modelius (11 Pro Max, X, 15 Pro, 6 plus, 13 ...) viename sarase.
"""
import contextlib
import io
import unittest

from tests.helpers import reset_config, TempDir, item
from tests.test_flow import FakeClient, FakeTelegram
from vinted import config
from vinted.finder import Run
from vinted.sources.vinted_source import VintedSource


def vinted_defaults(**extra):
    reset_config(**extra)
    config.cfg["VINTED_BROWSE_ALL"] = True       # gyva numatytoji veiksena
    config.cfg.update(extra)


class QueriesTest(unittest.TestCase):
    def test_browse_uses_single_query(self):
        vinted_defaults(SEARCH_QUERIES=[f"iPhone {i}" for i in range(34)])
        source = VintedSource(client=FakeClient({}))
        self.assertEqual(source.queries(), ["iphone"])
        self.assertIn("visi modeliai", source.describe("iphone"))

    def test_keyword_mode_still_available(self):
        reset_config(SEARCH_QUERIES=["iPhone 13", "iPhone 14"])
        config.cfg["VINTED_BROWSE_ALL"] = False
        source = VintedSource(client=FakeClient({}))
        self.assertEqual(source.queries(), ["iPhone 13", "iPhone 14"])

    def test_page_count(self):
        vinted_defaults(PAGES=2, FULL_SCAN_PAGES=10, VINTED_BROWSE_PAGES=5,
                        VINTED_FULL_SCAN_PAGES=10, VINTED_MAX_PAGES=10)
        source = VintedSource(client=FakeClient({}))
        self.assertEqual(source.page_count(2), 5)         # iprastas paleidimas
        self.assertEqual(source.page_count(10), 10)       # seen.json tuscias – giliau
        config.cfg["VINTED_BROWSE_ALL"] = False
        self.assertEqual(source.page_count(2), 2)         # raktazodziu veiksena – kaip buvo

    def test_never_asks_beyond_vinted_limit(self):
        """Gyvas log'as: 'iphone' p.11 -> HTTP 400 INVALID_REQUEST. Vinted giliau neleidzia."""
        vinted_defaults(FULL_SCAN_PAGES=10, VINTED_FULL_SCAN_PAGES=25, VINTED_MAX_PAGES=10)
        source = VintedSource(client=FakeClient({}))
        self.assertEqual(source.page_count(10), 10)


class PageLimitTest(unittest.TestCase):
    """HTTP 400 po 10-o puslapio – saraso pabaiga, ne klaida."""

    def test_400_after_first_page_ends_quietly(self):
        from vinted.client import VintedClient
        reset_config(SLEEP_SECONDS=0)

        class Resp:
            def __init__(self, code, data=None):
                self.status_code, self._d = code, data
                self.text = '{"code":"INVALID_REQUEST"}' if code == 400 else "{}"
                self.headers, self.cookies, self.url = {}, {}, ""
            def json(self):
                return self._d

        class Session:
            def get(self, url, params=None, headers=None, timeout=None):
                page = int((params or {}).get("page", 1))
                if "catalog" not in url:
                    return Resp(200)
                if page >= 3:
                    return Resp(400)
                return Resp(200, {"items": [{"id": 900 - page * 10 - i, "title": "iPhone 13"}
                                            for i in range(3)]})

        client = VintedClient(session_factory=Session, sleep=lambda s: None)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            client.start()
            items = client.fetch_items("iphone", pages=10)
        self.assertEqual(len(items), 6)                   # 2 puslapiai, tada pabaiga
        self.assertNotIn("INVALID_REQUEST", out.getvalue())   # jokio gasdinancio pranesimo
        self.assertNotIn("400", client.last_error)


class RequestCountTest(unittest.TestCase):
    """Svarbiausias matas: kiek uzklausu padaroma per paleidima."""

    def setUp(self):
        vinted_defaults(HEARTBEAT_HOURS=0, MIN_SAMPLES=100, SLEEP_SECONDS=0,
                        USE_TYPICAL_FALLBACK=False, USE_SOLD_PRICES=False)

    def run_once(self, browse, queries):
        config.cfg["VINTED_BROWSE_ALL"] = browse
        config.cfg["SEARCH_QUERIES"] = queries
        catalog = {q: [item(1000 + i, "iPhone 13 128GB", 250) for i in range(3)]
                   for q in queries + ["iphone"]}
        client = FakeClient(catalog)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            Run([VintedSource(client=client)], FakeTelegram(), sleep=lambda s: None).run()
        return out.getvalue().count("Tikrinama [Vinted]")

    def test_browse_makes_one_search_instead_of_34(self):
        queries = [f"iPhone {i}" for i in range(34)]
        with TempDir():
            self.assertEqual(self.run_once(browse=True, queries=queries), 1)
        with TempDir():
            self.assertEqual(self.run_once(browse=False, queries=queries), 34)


class TotalPriceTest(unittest.TestCase):
    """Vinted pasako tikra suma su pirkejo apsaugos mokesciu – jos spelioti nebereikia."""

    def setUp(self):
        reset_config()

    def test_total_price_taken_from_api(self):
        source = VintedSource(client=FakeClient({}))
        raw = item(1, "iPhone 13 128GB", 200)
        raw["total_item_price"] = {"amount": "210.70", "currency_code": "EUR"}
        self.assertEqual(source.to_listing(raw).total_price, 210.70)

    def test_missing_total_price_falls_back_to_estimate(self):
        from vinted.phone import estimate_profit
        source = VintedSource(client=FakeClient({}))
        self.assertIsNone(source.to_listing(item(1, "iPhone 13", 200)).total_price)
        # be tikros sumos – skaiciuojam pagal nustatymus (0.70 + 5%)
        spetas = estimate_profit(200, 300, pickup_only=True, buyer_fee=True)
        self.assertAlmostEqual(spetas, 300 - (200 + 0.70 + 10))

    def test_real_total_price_wins(self):
        from vinted.phone import estimate_profit
        tikras = estimate_profit(200, 300, pickup_only=True, buyer_fee=True, total_price=210.70)
        self.assertAlmostEqual(tikras, 300 - 210.70)

    def test_other_currency_ignored(self):
        source = VintedSource(client=FakeClient({}))
        raw = item(1, "iPhone 13", 200)
        raw["total_item_price"] = {"amount": "250", "currency_code": "PLN"}
        self.assertIsNone(source.to_listing(raw).total_price)


if __name__ == "__main__":
    unittest.main()
