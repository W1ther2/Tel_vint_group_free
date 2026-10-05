"""Rinkos duomenu kokybe (2026-09 kodo perziura).

- laipsniskas atpigimas (300 -> 290 -> 280) pastebimas;
- uzrakinti / sugede / ne telefonai nebeiskreipia vietos tarp pigiausiu;
- is naujo ikeltas skelbimas nelaikomas pardavimu;
- patvirtinti pardavimai svarbesni uz dingusius;
- kalibruojama karta per diena;
- Pirkpard: nepilnas sarasas != parduota.
"""
import contextlib
import io
import unittest

from tests.fixtures import pirkpard_response
from tests.helpers import reset_config, TempDir, item, listing
from tests.test_flow import FakeClient, FakeTelegram, market_items
from vinted import config
from vinted.finder import Run
from vinted.market import Market


def run_obj(client, tg=None):
    r = Run(client, tg or FakeTelegram(), sleep=lambda s: None)
    with contextlib.redirect_stdout(io.StringIO()):
        r.run()
    return r


class PriceDropTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_gradual_drop_measured_from_last_evaluation(self):
        m = Market()
        m.observe([listing(1, "iPhone 13 128GB", 300)], day=20000)
        m.mark_evaluated("vinted:1", 300)
        self.assertEqual(m.observe([listing(1, "iPhone 13 128GB", 290)], day=20001), {"vinted:1": 300})
        # evaluate() lygina su 300: 290 > 285 – dar ne; 280 <= 285 – taip
        self.assertEqual(m.observe([listing(1, "iPhone 13 128GB", 280)], day=20002), {"vinted:1": 300})

    def test_gradual_drop_reevaluated_in_flow(self):
        reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, DEAL_MODE="rank", MIN_PROFIT_EUR=0)
        with TempDir():
            peers = market_items()
            client = FakeClient({"iPhone 13": peers + [item(1, "iPhone 13 128GB", 300, user_id=1)]})
            first = run_obj(client)
            self.assertIn("ne tarp pigiausių", first.totals)
            for price, reevaluated in ((290, False), (280, True), (250, True)):
                client.catalog["iPhone 13"] = peers + [item(1, "iPhone 13 128GB", price, user_id=1)]
                tg = FakeTelegram()
                r = run_obj(client, tg)
                self.assertEqual("jau matyti" not in r.totals or r.totals["jau matyti"] < len(peers) + 1,
                                 reevaluated, (price, r.totals))
            self.assertEqual(len(tg.deals), 1)
            self.assertEqual(tg.deals[0][0]["drop_from"], 280)


class ExcludedListingsTest(unittest.TestCase):
    def setUp(self):
        reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8, MARKET_PERCENTILE=0.5,
                     MIN_DISCOUNT=0.15)

    def test_locked_phone_leaves_market(self):
        with TempDir():
            client = FakeClient({"iPhone 13": market_items() + [item(5, "iPhone 13 128GB", 200, user_id=5)]},
                                pages={"5": "iCloud užblokuotas, parduodu kaip yra"})
            r = run_obj(client)
            e = r.state.market.get("vinted:5")
            self.assertEqual(e["x"], "neveikia")
            rank = r.state.market.rank("13", "128 GB", 250, exclude="vinted:new")
            self.assertEqual(rank.place, 1)                         # uzrakintas 200 € nebeskaiciuojamas
            self.assertGreater(rank.peer_low, 200)

    def test_rank_and_quote_ignore_excluded(self):
        m = Market()
        m.observe([listing(i, "iPhone 13 128GB", 300 + i, user_id=i) for i in range(10)], day=20000)
        m.observe([listing(99, "iPhone 13 128GB", 180, user_id=99)], day=20000)
        before = m.rank("13", "128 GB", 290, day=20000)
        m.exclude("vinted:99", "neveikia")
        after = m.rank("13", "128 GB", 290, day=20000)
        self.assertEqual(before.place, 2)
        self.assertEqual(after.place, 1)
        self.assertNotIn(180, [e["p"] for e in m.items.values() if not e.get("x")])


class RelistTest(unittest.TestCase):
    def setUp(self):
        reset_config(GONE_AS_SOLD=True)

    def test_relisted_phone_is_not_a_sale(self):
        m = Market()
        m.observe([listing(1, "iPhone 13 128GB", 250, user_id=7)], day=20000)
        m.observe([listing(2, "iPhone 13 128GB", 240, user_id=7)], day=20001)     # tas pats pardavejas
        m.set_status("vinted:1", "gone", day=20002)
        e = m.get("vinted:1")
        self.assertEqual((e["st"], e.get("rl")), ("gone", 1))
        self.assertNotIn("sd", e)

    def test_other_seller_gone_still_counts(self):
        m = Market()
        m.observe([listing(1, "iPhone 13 128GB", 250, user_id=7)], day=20000)
        m.observe([listing(2, "iPhone 13 128GB", 240, user_id=8)], day=20001)
        m.set_status("vinted:1", "gone", day=20002)
        self.assertEqual(m.get("vinted:1")["st"], "sold")

    def test_seller_id_not_stored_in_plain(self):
        m = Market()
        m.observe([listing(1, "iPhone 13 128GB", 250, user_id=123456)], day=20000)
        self.assertNotIn("123456", str(m.to_dict()))


class ConfirmedSalesFirstTest(unittest.TestCase):
    def setUp(self):
        reset_config(MIN_SOLD_SAMPLES=5, USE_SOLD_PRICES=True)

    def market(self, confirmed_n):
        m = Market()
        day = 20000
        for i in range(confirmed_n):
            m.items[f"vinted:c{i}"] = {"m": "13", "s": "128 GB", "p": 200, "st": "sold", "sd": day, "sv": 1,
                                       "f": day, "l": day, "c": day}
        for i in range(10):
            m.items[f"vinted:g{i}"] = {"m": "13", "s": "128 GB", "p": 300, "st": "sold", "sd": day, "sv": 0,
                                       "f": day, "l": day, "c": day}
        return m

    def test_confirmed_preferred(self):
        q = self.market(5).quote("13", "128 GB", day=20000)
        self.assertEqual((q.price, q.ask), (200, 200))     # gyvai – kaip iki v49

    def test_gone_used_when_confirmed_too_few(self):
        q = self.market(3).quote("13", "128 GB", day=20000)
        self.assertEqual((q.price, q.ask), (300, 300))


class DailyCalibrationTest(unittest.TestCase):
    def test_once_per_day(self):
        from tests.test_calibration import market_with_sold, base_config
        base_config()
        m = market_with_sold(sold_price=170, asking=200)
        self.assertIsNotNone(m.calibrate(day=100))
        self.assertIsNone(m.calibrate(day=100))              # tas pats paleidimu srautas – ne
        self.assertIsNotNone(m.calibrate(day=101))           # kita diena – dar vienas zingsnis
        self.assertEqual(Market(m.to_dict()).calibrated_day, 101)


class PirkpardStatusTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_incomplete_list_means_unknown(self):
        from tests.test_pirkpard import FakeApi
        from vinted.sources.pirkpard import PirkpardSource
        page1 = pirkpard_response(page=1, last_page=3)
        api = FakeApi(pages={1: page1})                       # 2-as puslapis negrazinamas (None)
        source = PirkpardSource(client=api)
        self.assertEqual(source.status("3748"), "active")
        self.assertEqual(source.status("999999"), "unknown")

    def test_limit_reached_means_unknown(self):
        from tests.test_pirkpard import FakeApi
        from vinted.sources.pirkpard import PirkpardSource
        config.cfg["PIRKPARD_STATUS_PAGES"] = 1
        api = FakeApi(pages={1: pirkpard_response(page=1, last_page=5)})
        self.assertEqual(PirkpardSource(client=api).status("999999"), "unknown")


if __name__ == "__main__":
    unittest.main()
