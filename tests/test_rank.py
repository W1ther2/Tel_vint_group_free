"""Pigiausiu budas: ar skelbimas tarp pigiausiu SIUO METU parduodamu tokiu pat telefonu.

Esme – jam nereikia zinoti rinkos kainos. Jei mediana isputa (o vartotojas butent
tuo skundesi), nuolaidos budas pradeda meluoti, o sis – ne.
"""
import contextlib
import io
import unittest

from tests.helpers import reset_config, TempDir, item, listing
from tests.test_flow import FakeClient, FakeTelegram, market_items
from vinted import config
from vinted.finder import Run
from vinted.market import Market


def rank_config(**extra):
    reset_config(**extra)
    config.cfg.update(DEAL_MODE="rank", RANK_TOP_PCT=0.15, RANK_MIN_PEERS=8, **extra)


def fill(market, prices, model="iPhone 13 128GB", start=1000, day=100):
    market.observe([listing(start + i, model, p, user_id=start + i)
                    for i, p in enumerate(prices)], day=day)


class RankTest(unittest.TestCase):
    def setUp(self):
        rank_config()

    def test_place_and_range(self):
        m = Market()
        fill(m, [200, 210, 220, 230, 240, 250, 260, 270, 280, 290])
        r = m.rank("13", "128 GB", 205, day=100)
        self.assertEqual((r.place, r.n), (2, 11))          # tik 200 pigesnis
        self.assertEqual((r.low, r.high), (200, 290))
        self.assertAlmostEqual(r.share, 0.1)
        self.assertTrue(r.by_storage)

    def test_cheapest(self):
        m = Market()
        fill(m, [200 + 10 * i for i in range(10)])
        r = m.rank("13", "128 GB", 150, day=100)
        self.assertEqual(r.place, 1)
        self.assertEqual(r.share, 0.0)

    def test_listing_itself_excluded(self):
        m = Market()
        fill(m, [200 + 10 * i for i in range(10)])
        # skelbimas 1000 kainuoja 200 – jis pats neturi buti savo "kaimynas"
        r = m.rank("13", "128 GB", 200, exclude="vinted:1000", day=100)
        self.assertEqual(r.n, 10)
        self.assertEqual(r.place, 1)

    def test_too_few_peers_returns_none(self):
        m = Market()
        fill(m, [200, 210, 220])
        self.assertIsNone(m.rank("13", "128 GB", 150, day=100))

    def test_falls_back_to_whole_model_when_storage_is_rare(self):
        m = Market()
        fill(m, [200 + 10 * i for i in range(10)])               # 128 GB – daug
        fill(m, [300, 310], model="iPhone 13 512GB", start=5000)  # 512 GB – per mazai
        r = m.rank("13", "512 GB", 250, day=100)
        self.assertIsNotNone(r)
        self.assertFalse(r.by_storage)

    def test_stale_listings_ignored(self):
        """Ilgai kabantys skelbimai per brangus – su jais lyginant viskas atrodytu pigu."""
        rank_config(ASKING_MAX_AGE_DAYS=21, PRICE_HISTORY_DAYS=30)
        m = Market()
        fill(m, [400] * 10, start=1000, day=70)                  # kaba 30 dienu
        fill(m, [400] * 10, start=1000, day=100)                 # vis dar kataloge
        fill(m, [200 + 5 * i for i in range(10)], start=2000, day=100)
        r = m.rank("13", "128 GB", 230, day=100)
        self.assertEqual(r.n, 11)                                # seni 400 € neskaiciuojami
        self.assertGreater(r.place, 5)


class InflatedMedianTest(unittest.TestCase):
    """Butent ta, del ko vartotojas skundesi: rinkos kaina per didele."""

    def test_rank_ignores_inflated_manual_price(self):
        """Rinkos kaina nustatyta 400 €, nors realiai telefonai parduodami uz ~200 €.
        Nuolaidos budu 300 € telefonas atrodo 25% pigus. Pigiausiu budu – ne."""
        with TempDir():
            rank_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8,
                        MARKET_PRICES={"13": 400}, MIN_BATTERY=0)
            katalogas = [item(1000 + i, "iPhone 13 128GB", 190 + 3 * i, user_id=500 + i)
                         for i in range(20)]
            katalogas.append(item(1, "iPhone 13 128GB", 300, user_id=1))
            client = FakeClient({"iPhone 13": katalogas})

            tg = FakeTelegram()
            with contextlib.redirect_stdout(io.StringIO()) as out:
                Run(client, tg, sleep=lambda s: None).run()
            self.assertNotIn("vinted:1", {d["id"] for d, _ in tg.deals}, out.getvalue())
            self.assertIn("ne tarp pigiausių", out.getvalue())

        with TempDir():
            config.cfg["DEAL_MODE"] = "discount"
            tg = FakeTelegram()
            with contextlib.redirect_stdout(io.StringIO()):
                Run(FakeClient({"iPhone 13": katalogas}), tg, sleep=lambda s: None).run()
            # senasis budas sita "deal'a" praleidzia – nes pasitiki isputa kaina
            self.assertIn("vinted:1", {d["id"] for d, _ in tg.deals})


class FlowTest(unittest.TestCase):
    def setUp(self):
        rank_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8,
                    MARKET_PERCENTILE=0.5, MIN_BATTERY=0)

    def test_cheapest_sent_loud_others_silent(self):
        with TempDir():
            katalogas = market_items() + [
                item(1, "iPhone 13 128GB", 150, user_id=1),   # pigiausias
                item(2, "iPhone 13 128GB", 258, user_id=2),   # tarp pigiausiu, bet ne 1-as
                item(3, "iPhone 13 128GB", 330, user_id=3),   # brangus
            ]
            tg = FakeTelegram()
            with contextlib.redirect_stdout(io.StringIO()) as out:
                Run(FakeClient({"iPhone 13": katalogas}), tg, sleep=lambda s: None).run()
            log = out.getvalue()
            sent = {d["id"]: (d, silent) for d, silent in tg.deals}
            self.assertIn("vinted:1", sent, log)
            self.assertFalse(sent["vinted:1"][1])                # pigiausias – su garsu
            self.assertEqual(sent["vinted:1"][0]["rank"].place, 1)
            self.assertNotIn("vinted:3", sent)
            self.assertIn("ne tarp pigiausių", log)
            # 258 € – 2-as pigiausias is 23: ateina, bet tyliai
            self.assertIn("vinted:2", sent, log)
            self.assertEqual(sent["vinted:2"][0]["rank"].place, 2)
            self.assertTrue(sent["vinted:2"][1])

    def test_rare_model_falls_back_to_discount(self):
        with TempDir():
            rank_config(SEARCH_QUERIES=["iPhone 16e"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8,
                        MIN_BATTERY=0)
            from vinted.phone import typical_price
            kaina = round(typical_price("16e") * 0.6)
            client = FakeClient({"iPhone 16e": [item(1, "iPhone 16e 128GB", kaina)]},
                                pages={"1": "Tvarkingas telefonas, siunciu per Vinted"})
            tg = FakeTelegram()
            with contextlib.redirect_stdout(io.StringIO()) as out:
                Run(client, tg, sleep=lambda s: None).run()
            self.assertEqual(len(tg.deals), 1, out.getvalue())
            self.assertIsNone(tg.deals[0][0]["rank"])
            self.assertIn("retas modelis", out.getvalue())


class CardTest(unittest.TestCase):
    def test_rank_line_hidden_by_default(self):
        """Prekiautojui vieta sarase nereikalinga – kortelej jos nerodom (atrankai naudojama)."""
        rank_config()
        from vinted.market import Quote, Rank
        from vinted.telegram import format_card
        card = format_card({
            "model": "8", "storage": None, "price": 45.0, "discount": 0.11,
            "value": 50.0, "description": "", "quote": Quote(56, 6, "parduoti", False),
            "defects": [], "condition": "Gera", "battery": None, "seller": {},
            "url": "https://www.vinted.lt/items/1",
            "rank": Rank(place=12, n=64, low=40, high=99, share=0.17, by_storage=False)})
        self.assertNotIn("pigiausias iš", card)
        self.assertNotIn("dabar parduodamų", card)
        self.assertIn("iPhone 8 | 45 €", card)
        self.assertIn("~11% pigiau nei vertinta", card)

    def test_card_explains_why(self):
        rank_config()
        config.cfg["SHOW_RANK"] = True
        from vinted.market import Quote, Rank
        from vinted.telegram import format_card
        card = format_card({
            "model": "13", "storage": "128 GB", "price": 150.0, "discount": 0.25,
            "value": 200.0, "description": "", "quote": Quote(205, 12, "skelbimai", True),
            "defects": [], "condition": "Labai gera", "battery": None, "seller": {},
            "url": "https://www.vinted.lt/items/1",
            "rank": Rank(place=2, n=23, low=140, high=280, share=0.05, by_storage=True)})
        self.assertIn("2-as pigiausias iš 23", card)
        self.assertIn("tokių pat (128 GB)", card)
        self.assertIn("140–280 €", card)

    def test_card_without_positive_discount_hides_it(self):
        """Isputa mediana gali sakyti „brangiau nei verte“ – tokio melo nerodome."""
        rank_config()
        config.cfg["SHOW_RANK"] = True
        from vinted.market import Quote, Rank
        from vinted.telegram import format_card
        card = format_card({
            "model": "13", "storage": "128 GB", "price": 150.0, "discount": -0.1,
            "value": 136.0, "description": "", "quote": Quote(140, 12, "skelbimai", True),
            "defects": [], "condition": "Labai gera", "battery": None, "seller": {},
            "url": "https://www.vinted.lt/items/1",
            "rank": Rank(place=1, n=15, low=150, high=260, share=0.0, by_storage=True)})
        self.assertIn("Pigiausias iš 15", card)
        self.assertNotIn("pigiau nei vert", card)


class CommandTest(unittest.TestCase):
    def test_pigiausi_command(self):
        reset_config(ADMIN_IDS=["1"])
        from vinted import commands
        from vinted.state import State
        state = State()
        reply = commands.handle("/pigiausi 20", state)
        self.assertIn("20% pigiausių", reply)
        self.assertEqual(config.cfg["DEAL_MODE"], "rank")
        self.assertAlmostEqual(config.cfg["RANK_TOP_PCT"], 0.20)

    def test_rezimas_command(self):
        reset_config(ADMIN_IDS=["1"])
        from vinted import commands
        from vinted.state import State
        state = State()
        self.assertIn("pigiau nei įvertinta", commands.handle("/rezimas nuolaida", state))
        self.assertEqual(config.cfg["DEAL_MODE"], "discount")
        self.assertIn("pigiausių", commands.handle("/rezimas pigiausi", state))
        self.assertEqual(config.cfg["DEAL_MODE"], "rank")


if __name__ == "__main__":
    unittest.main()


class CurrentMarketTest(unittest.TestCase):
    """„Dabar parduodami“ turi reiksti dabar, o ne per paskutinias 30 dienu.

    Gyvas log'as rode „3-as pigiausias is 231“ – i palyginima pateko jau parduoti
    telefonai. Ju nupirkti nebegalima, o pigus dealas atrodo vidutiniskas."""

    def test_sold_long_ago_do_not_count(self):
        rank_config(RANK_RECENT_DAYS=2, ASKING_MAX_AGE_DAYS=21, PRICE_HISTORY_DAYS=30)
        m = Market()
        # pries 10 dienu buvo daug pigiu skelbimu – jie seniai parduoti (kataloge nebematyti)
        fill(m, [120 + i for i in range(40)], start=1000, day=90)
        # dabar parduodami – brangesni
        fill(m, [200 + 5 * i for i in range(12)], start=5000, day=100)
        r = m.rank("13", "128 GB", 190, day=100)
        self.assertEqual(r.n, 13)                     # tik dabartiniai 12 + jis pats
        self.assertEqual(r.place, 1)                  # tarp dabar parduodamu – pigiausias

    def test_same_data_with_old_window_would_bury_the_deal(self):
        """Tas pats su senuoju 30 d. langu: dealas atsiduria 41-oje vietoje."""
        rank_config(RANK_RECENT_DAYS=30, ASKING_MAX_AGE_DAYS=21, PRICE_HISTORY_DAYS=30)
        m = Market()
        fill(m, [120 + i for i in range(40)], start=1000, day=90)
        fill(m, [200 + 5 * i for i in range(12)], start=5000, day=100)
        r = m.rank("13", "128 GB", 190, day=100)
        self.assertEqual(r.place, 41)
        self.assertGreater(r.share, 0.15)             # -> butu atmestas


class ForeignTitleTest(unittest.TestCase):
    """Vinted rodo ir Lenkijos skelbimus. Ju kainos neturi lemti Lietuvos rinkos."""

    def setUp(self):
        rank_config(ONLY_LITHUANIAN_TEXT=True, ALLOWED_LANGUAGES=["LT", "EN"])

    def test_foreign_titles_marked_before_detail_fetch(self):
        from vinted.finder import Run
        ls = [listing(1, "Iphone 12 | Iphone 12 - Sprzedam IP 12 , stan idealny wizualny", 150),
              listing(2, "iphone 13 Stan bardzo dobry", 200),
              listing(3, "iPhone 13 128GB", 200),
              listing(4, "Parduodu iPhone 13 geros bukles", 210),
              listing(5, "iPhone 14 pro 128gb black", 400)]
        Run.mark_foreign(ls)
        self.assertTrue(ls[0].skip_reason.startswith("kalba (PL"))
        self.assertTrue(ls[1].skip_reason.startswith("kalba (PL"))
        self.assertEqual([l.skip_reason for l in ls[2:]], ["", "", ""])

    def test_foreign_prices_do_not_enter_market(self):
        from vinted.finder import Run
        m = Market()
        ls = [listing(1, "iphone 13 Stan bardzo dobry", 90),
              listing(2, "iPhone 13 128GB", 200)]
        Run.mark_foreign(ls)
        m.observe(ls, day=100)
        self.assertIsNone(m.get("vinted:1"))
        self.assertIsNotNone(m.get("vinted:2"))

    def test_disabled_when_language_filter_off(self):
        from vinted.finder import Run
        rank_config(ONLY_LITHUANIAN_TEXT=False)
        ls = [listing(1, "iphone 13 Stan bardzo dobry", 90)]
        Run.mark_foreign(ls)
        self.assertEqual(ls[0].skip_reason, "")


class MarketTableTest(unittest.TestCase):
    """Log'o lentele turi rodyti, ka kodas IS TIKRUJU naudoja."""

    def test_used_column_matches_quote(self):
        from vinted.finder import Run
        from vinted.state import State
        rank_config(MIN_SAMPLES=3, MIN_SOLD_SAMPLES=5, MARKET_PERCENTILE=0.5,
                    ASKING_SALE_FACTOR=1.0, USE_TYPICAL_FALLBACK=False)
        run = Run([], FakeTelegram())
        run.state = State()
        from vinted.util import today
        fill(run.state.market, [50, 55, 60, 65], model="iPhone X 64GB", day=today())
        # 2 pardavimai po 130 € – per mazai (reikia 5), kodas ju NENAUDOJA
        fill(run.state.market, [130, 132], model="iPhone X 64GB", start=9000, day=today())
        for uid in ("vinted:9000", "vinted:9001"):
            run.state.market.set_status(uid, "sold", day=today())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            run.print_market()
        eilute = next(l for l in out.getvalue().splitlines() if "iPhone X " in l)
        naudojama = float(eilute.split("naudojama:")[1].split("(")[0])
        self.assertAlmostEqual(naudojama, run.state.market.quote("X", None).price, delta=1)
        self.assertLess(naudojama, 100)                  # ne 131 kaip rode anksciau
        self.assertIn("skelb.", eilute)
