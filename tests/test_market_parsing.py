import unittest

from tests.helpers import reset_config, item, listing
from vinted import config
from vinted.market import Market, migrate_old_prices
from vinted.parsing import get_price, get_condition, listing_status, seller_from_dict


class MarketTest(unittest.TestCase):
    def setUp(self):
        reset_config(MIN_SAMPLES=3, MIN_SOLD_SAMPLES=2, MARKET_PERCENTILE=0.5)

    def test_observe_and_drop(self):
        m = Market()
        self.assertEqual(m.observe([listing(1, "iPhone 13 128GB", 200)], day=100), {})
        self.assertEqual(m.observe([listing(1, "iPhone 13 128GB", 170)], day=101), {"vinted:1": 200})
        self.assertEqual(m.get(1)["p"], 170)
        # priedai ir sugede neirasomi
        m.observe([listing(2, "Dėklas iPhone 13", 10), listing(3, "iPhone 13 įskilęs", 100)], day=101)
        self.assertIsNone(m.get(2))
        self.assertIsNone(m.get(3))

    def test_skipped_listings_never_enter_market(self):
        """Aukciono pasiulymas ar dalies kaina nera rinkos kaina."""
        m = Market()
        geras = listing(1, "iPhone 13 128GB", 200)
        aukcionas = listing(2, "iPhone 13 128GB", 90)
        aukcionas.skip_reason = "aukcionas"
        m.observe([geras, aukcionas], day=100)
        self.assertIsNotNone(m.get("vinted:1"))
        self.assertIsNone(m.get("vinted:2"))

    def test_quote_priority(self):
        m = Market()
        m.observe([listing(i, "iPhone 13 128GB", p) for i, p in enumerate([200, 220, 240], 1)], day=100)
        q = m.quote("13", "128 GB", day=100)
        self.assertEqual((q.source, q.by_storage), ("skelbimai", True))
        self.assertAlmostEqual(q.price, 220 * config.cfg["ASKING_SALE_FACTOR"])
        m.set_status(1, "sold", day=100)
        m.set_status(2, "sold", day=100)
        q = m.quote("13", "128 GB", day=100)
        self.assertEqual((q.source, q.price), ("parduoti", 210))
        config.cfg["MARKET_PRICES"] = {"13": 180}
        self.assertEqual(m.quote("13", "128 GB", day=100).source, "rankinė")
        config.cfg["MARKET_PRICES"] = {"13|128 GB": 190}
        self.assertEqual(m.quote("13", "128 GB", day=100).price, 190)
        self.assertEqual(m.quote("15", None, day=100).source, "apytikslė")
        config.cfg["USE_TYPICAL_FALLBACK"] = False
        self.assertIsNone(m.quote("15", None, day=100))

    def test_stale_listings_excluded_from_market(self):
        reset_config(MIN_SAMPLES=3, MARKET_PERCENTILE=0.5, ASKING_MAX_AGE_DAYS=21, PRICE_HISTORY_DAYS=30)
        m = Market()
        # trys sviezi skelbimai po 200 ir trys seni, kabantys 40 dienu, po 400
        m.observe([listing(i, "iPhone 13 128GB", 200) for i in range(1, 4)], day=100)
        m.observe([listing(i, "iPhone 13 128GB", 400) for i in range(10, 13)], day=60)
        m.observe([listing(i, "iPhone 13 128GB", 400) for i in range(10, 13)], day=100)
        self.assertAlmostEqual(m.quote("13", "128 GB", day=100).price, 200 * config.cfg["ASKING_SALE_FACTOR"])

    def test_gone_counts_as_sold(self):
        reset_config(GONE_AS_SOLD=True)
        m = Market()
        m.observe([listing(1, "iPhone 13 128GB", 200)], day=100)
        m.set_status(1, "gone", day=101)
        self.assertEqual(m.get(1)["st"], "sold")
        reset_config(GONE_AS_SOLD=False)
        m.observe([listing(2, "iPhone 13 128GB", 200)], day=100)
        m.set_status(2, "gone", day=101)
        self.assertEqual(m.get(2)["st"], "gone")

    def test_sold_candidates(self):
        reset_config(SOLD_CHECK_AFTER_DAYS=2, SOLD_CHECKS_PER_RUN=10)
        m = Market()
        m.observe([listing(1, "iPhone 13", 200)], day=100)
        m.observe([listing(2, "iPhone 13", 200)], day=103)
        self.assertEqual(m.sold_check_candidates(day=103), ["vinted:1"])
        m.set_status(1, "active", day=103)
        self.assertEqual(m.sold_check_candidates(day=103), [])

    def test_alerted(self):
        reset_config(PRICE_DROP_MIN=0.05)
        m = Market()
        m.observe([listing(1, "iPhone 13", 200)], day=1)
        m.mark_alerted(1, 200)
        self.assertTrue(m.already_alerted_at(1, 195))
        self.assertFalse(m.already_alerted_at(1, 185))

    def test_prune_and_migrate(self):
        reset_config(PRICE_HISTORY_DAYS=30)
        m = migrate_old_prices({"13|*": {"5": [200, 10]}, "13|128 GB": {"5": [200, 10]}})
        self.assertEqual(m.get(5)["s"], "128 GB")
        m.prune(day=100)
        self.assertEqual(m.items, {})


class StateVersionTest(unittest.TestCase):
    def test_old_market_cleared(self):
        import contextlib, io
        from vinted.state import State
        with contextlib.redirect_stdout(io.StringIO()):
            old = State({"market": {"items": {"1": {"m": "XR", "s": "", "p": 20, "l": 1, "st": "active"}}},
                         "overrides": {"MIN_DISCOUNT": 0.2}, "telegram_offset": 7})
        self.assertEqual(old.market.items, {})
        self.assertEqual((old.overrides, old.telegram_offset), ({"MIN_DISCOUNT": 0.2}, 7))
        new = State({"market_version": 3, "market": {"items": {"1": {"m": "XR", "p": 100}}}})
        self.assertIn("vinted:1", new.market.items)


class ParsingTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_price(self):
        self.assertEqual(get_price({"price": {"amount": "150.0", "currency_code": "EUR"}}), 150)
        self.assertIsNone(get_price({"price": {"amount": "150", "currency_code": "USD"}}))
        self.assertEqual(get_price({"price": "1 200,50 €"}), 1200.5)

    def test_condition(self):
        self.assertEqual(get_condition({"status": "Très bon état"}), "Labai gera")
        self.assertEqual(get_condition({"status_id": 3}), "Gera")
        self.assertEqual(get_condition({}, '{"x":"Patenkinama"}'), "Patenkinama")

    def test_listing_status(self):
        self.assertEqual(listing_status(404, "", "", 1), "gone")
        self.assertEqual(listing_status(200, '{\\"is_closed\\":true}', "https://www.vinted.lt/items/1-x", 1), "sold")
        self.assertEqual(listing_status(200, '{"is_closed":false}', "https://www.vinted.lt/items/1-x", 1), "active")
        self.assertEqual(listing_status(200, "<html>", "https://www.vinted.lt/catalog", 1), "gone")
        self.assertEqual(listing_status(500, "", "", 1), "unknown")

    def test_photo_count_not_from_catalog_list(self):
        from vinted.parsing import get_photo_count
        self.assertIsNone(get_photo_count({"photos": [{"url": "a"}]}))
        self.assertEqual(get_photo_count({"photos_count": 6}), 6)

    def test_seller(self):
        info = seller_from_dict({"country_code": "LT", "feedback_reputation": 0.9, "feedback_count": 10,
                                 "given_item_count": 4, "created_at": "2020-01-01T00:00:00Z", "city": "Vilnius"})
        self.assertEqual((info["country"], info["rating"], info["reviews"], info["sold"], info["city"]),
                         ("LT", 4.5, 10, 4, "Vilnius"))
        self.assertGreater(info["account_age_days"], 1000)


if __name__ == "__main__":
    unittest.main()
