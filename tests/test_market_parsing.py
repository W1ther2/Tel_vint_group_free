import unittest

from tests.helpers import reset_config, item, listing
from vinted import config
from vinted.market import Market
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
        # Gyva verte – kaip iki v49 (MARKET_SCALE_UNIFIED=false); ask – visada prasomu masteliu.
        self.assertEqual((q.source, q.price, q.ask), ("parduoti", 210, 210))
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

    def test_prune(self):
        reset_config(PRICE_HISTORY_DAYS=30)
        m = Market()
        m.items["vinted:5"] = {"m": "13", "s": "128 GB", "p": 200, "f": 10, "l": 10, "c": 10, "st": "active"}
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



class SoldCheckPriorityTest(unittest.TestCase):
    """v49.1: pardavimu tiesa. Gyvai (2026-10-04) is ~2300 „parduotu“ tik 4 Vinted tikrai
    parode „parduota“ – eile eidavo nuo seniausiu, tad puslapio jau nebuvo (404 = „dingo“).

    Nuo `efad5c5` tvarka trijuose sluoksniuose:
      0) NESENIAI dinge (nematyti <= SOLD_CHECK_FRESH_DAYS) – ju puslapis dar gali
         rodyti „parduota“; tarp ju pirmiau `qc` (is ju mokosi confidence), tada
         neseniausiai dinge, tada ilgiausiai netikrinti;
      1) seni – tik jei lieka vietos;
      o „vis dar parduodamas“ (`ca`) nekartojamas anksciau nei SOLD_RECHECK_ACTIVE_DAYS."""

    def setUp(self):
        reset_config(SOLD_CHECK_AFTER_DAYS=1, SOLD_CHECKS_PER_RUN=10)

    @staticmethod
    def market(**rows):
        from vinted.market import Market
        m = Market()
        for iid, row in rows.items():
            last_seen, checked, qc = row[0], row[1], row[2]
            e = {"m": "13", "s": "", "p": 200, "f": 90, "l": last_seen, "c": checked, "st": "active"}
            if qc:
                e["qc"] = qc
            if len(row) > 3 and row[3] is not None:
                e["ca"] = row[3]          # kada puslapis sake „vis dar parduodamas“
            m.items["vinted:" + iid] = e
        return m

    def test_qc_first_then_most_recently_gone_then_longest_unchecked(self):
        """Visi penki – „svieziai“ dinge, tad rusiuoja `qc`, paskui `unseen`, paskui `c`."""
        m = self.market(a=(99, 95, None), b=(99, 99, "m"), c=(97, 95, "l"),
                        d=(99, 95, "h"), e=(98, 92, None))
        self.assertEqual(m.sold_check_candidates(day=100),
                         ["vinted:d", "vinted:b", "vinted:c", "vinted:a", "vinted:e"])

    def test_fresh_beats_old_even_without_qc(self):
        """Svarbiausias `efad5c5` pakeitimas: seniai dinges su `qc` NEBEAPLENKIA
        neseniai dingusio be jo. Butent del senu pirmumo is 2300 „parduotu“
        patvirtinti buvo 4."""
        m = self.market(senas=(80, 70, "h"), sviezias=(99, 95, None))
        self.assertEqual(m.sold_check_candidates(day=100), ["vinted:sviezias", "vinted:senas"])

    def test_still_listed_is_not_rechecked_daily(self):
        """`ca` – puslapis sake „vis dar parduodamas“. Kartoti kita diena beprasmiska."""
        m = self.market(vakar=(99, 99, "h", 99), seniau=(99, 99, "h", 95))
        self.assertEqual(m.sold_check_candidates(day=100), ["vinted:seniau"])

    def test_same_day_disappearance_is_not_checked(self):
        """Ta pacia diena dingimas nieko nereiskia – galejo nepatekti i perziuretus puslapius."""
        config.cfg["SOLD_CHECK_AFTER_DAYS"] = 0
        m = self.market(a=(100, 90, "h"), b=(99, 90, None))
        self.assertEqual(m.sold_check_candidates(day=100), ["vinted:b"])

    def test_limit_keeps_qc_items(self):
        config.cfg["SOLD_CHECKS_PER_RUN"] = 2
        m = self.market(**{f"x{i}": (95, 90, None) for i in range(5)}, q1=(99, 99, "m"), q2=(99, 99, "l"))
        self.assertEqual(sorted(m.sold_check_candidates(day=100)), ["vinted:q1", "vinted:q2"])


class SingleScaleTest(unittest.TestCase):
    """v49: vienas mastelis. Iki tol „parduoti“ grazindavo prasomas kainas, „skelbimai“ – x0,85:
    ta pati rinka gaudavo ~x1,17 skirtinga verte (gyvai: iPhone 13 174 € vs 157 €)."""

    def setUp(self):
        reset_config(MIN_SAMPLES=3, MIN_SOLD_SAMPLES=3, MARKET_PERCENTILE=0.5, USE_SOLD_PRICES=True,
                     MARKET_SCALE_UNIFIED=True)

    def test_live_value_unchanged_by_default(self):
        """Gyvai (numatyta) – „parduoti“ verte kaip iki v49; vienas mastelis tik ask/mokymui."""
        from vinted.market import Market
        reset_config(MIN_SOLD_SAMPLES=3, USE_SOLD_PRICES=True)
        self.assertFalse(config.cfg["MARKET_SCALE_UNIFIED"])
        m = Market()
        m.observe([listing(i, "iPhone 13 128GB", p) for i, p in enumerate([200, 220, 240], 1)], day=100)
        for i in range(1, 4):
            m.set_status(i, "sold", day=100)
        q = m.quote("13", "128 GB", day=100)
        self.assertEqual((q.source, q.price, q.ask), ("parduoti", 220, 220))

    def test_same_prices_same_value_whichever_source(self):
        from vinted.market import Market
        prices = [200, 220, 240]
        active, sold = Market(), Market()
        active.observe([listing(i, "iPhone 13 128GB", p) for i, p in enumerate(prices, 1)], day=100)
        sold.observe([listing(i, "iPhone 13 128GB", p) for i, p in enumerate(prices, 1)], day=100)
        for i in range(1, 4):
            sold.set_status(i, "sold", day=100)
        a, b = active.quote("13", "128 GB", day=100), sold.quote("13", "128 GB", day=100)
        self.assertEqual((a.source, b.source), ("skelbimai", "parduoti"))
        self.assertAlmostEqual(a.price, b.price)
        self.assertAlmostEqual(a.ask, b.ask)
        self.assertAlmostEqual(a.price, a.ask * config.cfg["ASKING_SALE_FACTOR"])

    def test_manual_and_table_are_values(self):
        from vinted.market import Market
        config.cfg["MARKET_PRICES"] = {"13": 170}
        q = Market().quote("13", None, day=100)
        self.assertEqual(q.price, 170)
        self.assertAlmostEqual(q.ask, 170 / config.cfg["ASKING_SALE_FACTOR"])
        t = Market().quote("15", None, day=100)
        self.assertEqual(t.source, "apytikslė")
        self.assertAlmostEqual(t.ask, t.price / config.cfg["ASKING_SALE_FACTOR"])

    def test_observe_stores_ask_scale_quote(self):
        from vinted.market import Market
        m = Market()
        m.observe([listing(i, "iPhone 13 128GB", p) for i, p in enumerate([200, 220, 240], 1)], day=100)
        m.observe([listing(9, "iPhone 13 128GB", 210)], day=100)
        e = m.get(9)
        self.assertEqual(e["qs"], "s")
        self.assertAlmostEqual(e["qa"], 220)
        self.assertAlmostEqual(e["q"], 220 * config.cfg["ASKING_SALE_FACTOR"], places=2)

    def test_ask_quote_for_old_entries(self):
        """Seni irasai (be qa): mastelis priklausė nuo saltinio."""
        from vinted.confidence import ask_quote
        f = config.cfg["ASKING_SALE_FACTOR"]
        self.assertEqual(ask_quote({"q": 200, "qs": "d"}), 200)              # parduoti – prasomos
        self.assertAlmostEqual(ask_quote({"q": 170, "qs": "s", "qf": 0.85}), 200)
        self.assertAlmostEqual(ask_quote({"q": 170, "qs": "t"}), 170 / f)    # lentele – verte
        self.assertAlmostEqual(ask_quote({"q": 170, "qs": "m"}), 170 / f)    # rankine – verte
        self.assertEqual(ask_quote({"q": 170, "qs": "s", "qa": 199}), 199)   # v49 – tiesiogiai
        self.assertIsNone(ask_quote({"qs": "s"}))

    def test_calibration_off_by_default(self):
        """Prielaida nekalibruojama is uzsidarymo kainu – jos sandorio kainos nematuoja."""
        reset_config()
        self.assertFalse(config.cfg["AUTO_CALIBRATE"])

if __name__ == "__main__":
    unittest.main()
