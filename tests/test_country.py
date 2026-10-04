# -*- coding: utf-8 -*-
"""Tik Lietuvos skelbimai.

Gyvai patikrinta 2026-09-25 (`api.vinted.lt`, paieska „iphone“, 2 puslapiai, 45 telefonai):
PL 31, FI 6, LT 5, LV 2, EE 1. T. y. tik ~11 % saraso yra Lietuvos. Naujasis katalogo
API pardavejo objekte grazina TIK `business`, `id`, `login` – salies nebeduoda, tad
anksciau ji paaiskedavo tik atidarius skelbima, jau PO `market.observe()`. Uzsienio
skelbimai, atmesti ankstesniame etape, taip ir likdavo Lietuvos rinkos istorijoje.
"""
import json
import unittest

from tests.helpers import reset_config, TempDir, item
from tests.test_flow import FakeClient, FakeTelegram, run


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class CountingClient(FakeClient):
    """Skaiciuoja, kiek kartu uzklausta pardavejo salies.

    Kesuoja taip pat, kaip tikras VintedClient (`_user_cache`): tas pats pardavejas
    per paleidima uzklausiamas viena karta, nesvarbu, kiek turi skelbimu."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_requests = []
        self._cache = {}

    def fetch_user(self, uid):
        if uid not in self._cache:
            self.user_requests.append(uid)
            self._cache[uid] = super().fetch_user(uid)
        return self._cache[uid]


def lt_config(**over):
    reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8,
                 MARKET_PERCENTILE=0.5, MIN_DISCOUNT=0.15, MIN_PROFIT_EUR=0, MIN_BATTERY=0,
                 FILTER_BY_COUNTRY=True, ALLOWED_COUNTRY_CODES=["LT"], **over)


def seller(country, city="Vilnius"):
    return {"country_code": country, "city": city, "feedback_reputation": 0.98,
            "feedback_count": 25, "given_item_count": 30, "created_at": "2021-01-01T00:00:00Z"}


class MarketStaysLithuanianTest(unittest.TestCase):
    """Pagrindinis dalykas: uzsienio kainos neturi patekti i Lietuvos rinkos kaina."""

    def setUp(self):
        lt_config()

    def test_foreign_prices_never_enter_market_history(self):
        with TempDir():
            # 16 lenkisku po ~150 € (PLN perskaiciuota – todel su centais) ir 9 lietuvisku po ~300 €
            pl = [item(100 + i, "iPhone 13 128GB", 148.37 + i, user_id=700 + i) for i in range(16)]
            lt = [item(200 + i, "iPhone 13 128GB", 295 + i * 5, user_id=800 + i) for i in range(9)]
            users = {700 + i: seller("PL", "Warszawa") for i in range(16)}
            users.update({800 + i: seller("LT") for i in range(9)})
            client = CountingClient({"iPhone 13": pl + lt}, users=users)
            log = run(client, FakeTelegram())
            items = read_json("state.json")["market"]["items"]
            self.assertEqual({k for k in items if k.startswith("vinted:1")}, set(), log)
            self.assertEqual(len([k for k in items if k.startswith("vinted:2")]), 9)
            self.assertIn("salis (PL)", log)

    def test_lt_deal_not_buried_by_cheaper_foreign_listings(self):
        """Butent del to kentejo atranka: 280 € yra pigiausias LT skelbimas, bet tarp
        lenkisku 150 € jis atrodo vidutinis ir buvo atmetamas kaip „ne tarp pigiausiu“."""
        lt_config(DEAL_MODE="rank", RANK_TOP_PCT=0.15, RANK_MIN_PEERS=8)
        with TempDir():
            pl = [item(100 + i, "iPhone 13 128GB", 148.37 + i, user_id=700 + i) for i in range(16)]
            lt = [item(200 + i, "iPhone 13 128GB", 300 + i * 5, user_id=800 + i) for i in range(9)]
            deal = item(5, "iPhone 13 128GB", 280, user_id=850)
            users = {700 + i: seller("PL") for i in range(16)}
            users.update({800 + i: seller("LT") for i in range(9)})
            users[850] = seller("LT")
            client = CountingClient({"iPhone 13": pl + lt + [deal]}, users=users)
            tg = FakeTelegram()
            log = run(client, tg)
            sent = {d["id"]: d for d, _ in tg.deals}
            self.assertIn("vinted:5", sent, log)
            self.assertEqual(sent["vinted:5"]["rank"].place, 1)      # pats pigiausias
            self.assertEqual(sent["vinted:5"]["rank"].n, 10)         # lyginta tik su lietuviskais
            self.assertFalse([k for k in sent if k.startswith("vinted:1")], sent)
            # su lenkiskomis kainomis 280 € butu buves 17-as is 26 – ne tarp pigiausiu
            self.assertGreater(sent["vinted:5"]["quote"].price, 250, sent["vinted:5"]["quote"])


class LookupBudgetTest(unittest.TestCase):
    def setUp(self):
        lt_config()

    def test_country_remembered_between_runs(self):
        with TempDir():
            cat = [item(100 + i, "iPhone 13 128GB", 300 + i, user_id=42) for i in range(10)]
            client = CountingClient({"iPhone 13": cat}, users={42: seller("LT")})
            run(client, FakeTelegram())
            first = len(set(client.user_requests))
            self.assertEqual(first, 1, client.user_requests)         # 10 skelbimu, 1 pardavejas
            self.assertEqual(read_json("state.json")["sellers"]["vinted:42"][0], "LT")
            client.user_requests.clear()
            client.catalog["iPhone 13"] = cat + [item(300, "iPhone 13 128GB", 250, user_id=42)]
            run(client, FakeTelegram())
            self.assertEqual(client.user_requests, [], "salis turejo buti paimta is state.json")

    def test_budget_checks_round_prices_first(self):
        """Riba pasiekiama – tikrinami tie, kurie labiau panasus i lietuviskus:
        perskaiciuota PLN kaina beveik visada su centais (692.64 €), LT – apvali."""
        lt_config(SELLER_COUNTRY_LOOKUPS=2)
        with TempDir():
            cat = [item(1, "iPhone 13 128GB", 148.37, user_id=701),
                   item(2, "iPhone 13 128GB", 152.11, user_id=702),
                   item(3, "iPhone 13 128GB", 300.0, user_id=801),
                   item(4, "iPhone 13 128GB", 310.0, user_id=802)]
            users = {701: seller("PL"), 702: seller("PL"), 801: seller("LT"), 802: seller("LT")}
            client = CountingClient({"iPhone 13": cat}, users=users)
            run(client, FakeTelegram())
            # Pirmos dvi uzklausos (tiek leidzia riba) – apvalios kainos, t. y. lietuviskos.
            # Toliau lenkiskus pardavejus uzklausia jau tik atidaromas skelbimas.
            self.assertEqual(client.user_requests[:2], [801, 802], client.user_requests)

    def test_no_lookups_for_sources_that_know_country(self):
        """Pirkpard sarase salis jau yra – uzklausu nereikia."""
        from tests.fixtures import pirkpard_response
        from vinted.sources.pirkpard import PirkpardSource
        reset_config(SOURCES=["pirkpard"], HEARTBEAT_HOURS=0, SELLER_COUNTRY_LOOKUPS=0,
                     MAX_ALERTS_PER_RUN=0)
        with TempDir():
            calls = []

            class Client:
                last_error, blocked = "", ""

                def start(self):
                    pass

                def sleep(self, s):
                    pass

                def get_json(self, params):
                    calls.append(params)
                    return pirkpard_response()

            import contextlib
            import io
            from vinted.finder import Run
            with contextlib.redirect_stdout(io.StringIO()) as out:
                Run([PirkpardSource(client=Client())], FakeTelegram(), sleep=lambda s: None).run()
            self.assertNotIn("nepatikrinta", out.getvalue())
            self.assertIn("vinted:", "vinted:")                      # sanity
            self.assertTrue(calls)


class UnknownCountryTest(unittest.TestCase):
    """Kai salies nustatyti nepavyksta (uzklausa neatsake), sprendziama pagal teksta."""

    def setUp(self):
        lt_config(SELLER_COUNTRY_LOOKUPS=0)     # salis niekada nezinoma

    def market(self):
        return [item(1000 + i, "iPhone 13 128GB", 290 + i, user_id=500 + i) for i in range(12)]

    def run_one(self, listing, description):
        client = FakeClient({"iPhone 13": self.market() + [listing]},
                            pages={str(listing["id"]): description},
                            users={listing["user"]["id"]: {}})      # pardavejo API tyli
        tg = FakeTelegram()
        log = run(client, tg)
        return [d["id"] for d, _ in tg.deals], log

    def test_neutral_text_rejected(self):
        with TempDir():
            sent, log = self.run_one(item(7, "iPhone 13 128GB", 200, user_id=907),
                                     "iPhone 13 128GB battery 89")
            self.assertEqual(sent, [], log)
            self.assertIn("šalis nežinoma, tekstas ne lietuviškas", log)

    def test_lithuanian_text_allowed(self):
        with TempDir():
            sent, log = self.run_one(item(8, "iPhone 13 128GB", 200, user_id=908),
                                     "Parduodu tvarkingą telefoną, baterija 89%, siunčiu per Vinted")
            self.assertEqual(sent, ["vinted:8"], log)

    def test_can_be_turned_off(self):
        lt_config(SELLER_COUNTRY_LOOKUPS=0, UNKNOWN_COUNTRY_NEEDS_LT_TEXT=False)
        with TempDir():
            sent, log = self.run_one(item(9, "iPhone 13 128GB", 200, user_id=909),
                                     "iPhone 13 128GB battery 89")
            self.assertEqual(sent, ["vinted:9"], log)


class SharedBudgetTest(unittest.TestCase):
    """Riba yra VISO PALEIDIMO, ne vienos paieskos.

    Su keliais gamintojais (`BRANDS`) paieskos yra kelios, o Vinted 429 atsako po ~45
    uzklausu is eiles – nesvarbu, kiek paiesku jas isbarste. Kol riba galiojo kiekvienai
    paieskai atskirai, pirmasis gamintojas suvalgydavo visa kvota ir i Telegram krisdavo
    tik jo telefonai."""

    def setUp(self):
        reset_config(SEARCH_QUERIES=["samsung galaxy", "xiaomi", "google pixel"],
                     HEARTBEAT_HOURS=0, MIN_SAMPLES=8, FILTER_BY_COUNTRY=True,
                     ALLOWED_COUNTRY_CODES=["LT"], SELLER_COUNTRY_LOOKUPS=6,
                     BRANDS=["samsung", "xiaomi", "google"], VINTED_BROWSE_ALL=False)

    def brand_listings(self, first, title, n=5):
        from tests.helpers import listings
        return listings(*[item(first + i, title, 300 + i, user_id=first + i) for i in range(n)])

    def resolver(self, client):
        """Paruostas Run be pilno paleidimo – kad matytusi TIK salies uzklausos
        (skelbimo atidarymas ta pacia pardaveju API kvieciа atskirai)."""
        import contextlib
        import io
        from vinted.finder import Run
        from vinted.sources.vinted_source import VintedSource
        from vinted.state import State
        source = VintedSource(client=client)
        runner = Run([source], FakeTelegram(), sleep=lambda s: None)
        with contextlib.redirect_stdout(io.StringIO()):
            runner.state = State.load()
        runner.new_seen = {}
        return runner, source, contextlib.redirect_stdout(io.StringIO())

    def test_share_is_split_across_queries(self):
        import contextlib
        import io
        with TempDir():
            client = CountingClient({})
            runner, source, _ = self.resolver(client)
            with contextlib.redirect_stdout(io.StringIO()):
                for first, title, left in ((100, "Samsung Galaxy S23 256GB", 3),
                                           (200, "Xiaomi 14 Pro 512GB", 2),
                                           (300, "Google Pixel 8 Pro 128GB", 1)):
                    runner.resolve_countries(source, self.brand_listings(first, title), queries_left=left)
            # Po 2 kiekvienam, o ne 6 pirmajam (riba – visam paleidimui)
            self.assertEqual(client.user_requests, [100, 101, 200, 201, 300, 301])
            self.assertEqual(runner.country_used["vinted"], 6)

    def test_last_query_gets_the_leftovers(self):
        """Jei pirmieji gamintojai savo dalies nesunaudoja, ji atitenka paskutiniam."""
        import contextlib
        import io
        with TempDir():
            client = CountingClient({})
            runner, source, _ = self.resolver(client)
            with contextlib.redirect_stdout(io.StringIO()):
                runner.resolve_countries(source, [], queries_left=3)
                runner.resolve_countries(source, [], queries_left=2)
                runner.resolve_countries(source, self.brand_listings(300, "Google Pixel 8 Pro 128GB"),
                                         queries_left=1)
            self.assertEqual(runner.country_used["vinted"], 5)      # visi 5, ne 2


class RateLimitedTest(unittest.TestCase):
    """Gyvai matyta: po ~45 pardaveju uzklausu Vinted atsako HTTP 429 (o api.vinted.lt sio
    adreso neturi visai – visada 404). Tada salis nezinoma LAIKINAI, tad skelbimo negalima
    nei itraukti i Lietuvos rinkos kaina, nei pazymeti matytu."""

    def setUp(self):
        lt_config()

    def seeded_state(self, day):
        """Rinkos pardavejai jau isiminti, tad jiems uzklausu nereikia."""
        return {"market_version": 3, "sellers": {f"vinted:{500 + i}": ["LT", day] for i in range(12)}}

    def test_unverified_not_in_market_and_retried(self):
        from vinted.util import today
        from tests.test_flow import write_json

        class Blocked(FakeClient):
            users_blocked = "HTTP 429 – per daug pardaveju uzklausu"

            def fetch_user(self, uid):
                return {}

        with TempDir():
            write_json("state.json", self.seeded_state(today()))
            market = [item(1000 + i, "iPhone 13 128GB", 290 + i * 4, user_id=500 + i) for i in range(12)]
            cand = item(1, "iPhone 13 128GB", 200, user_id=901)
            client = Blocked({"iPhone 13": market + [cand]},
                             pages={"1": "iPhone 13 128GB battery 89"})
            tg = FakeTelegram()
            log = run(client, tg)
            self.assertEqual(tg.deals, [], log)
            self.assertIn("šalies nustatyti nepavyko – bandysiu vėliau", log)
            self.assertNotIn("vinted:1", read_json("seen.json"))           # bus bandoma dar karta
            items = read_json("state.json")["market"]["items"]
            self.assertEqual(items["vinted:1"]["x"], "salis?")             # i rinkos kaina neitraukiamas
            self.assertNotIn("x", items["vinted:1000"])                    # isiminti pardavejai – itraukiami

    def test_gives_up_after_retries(self):
        from vinted.util import today
        from tests.test_flow import write_json

        class Blocked(FakeClient):
            users_blocked = "HTTP 429"

            def fetch_user(self, uid):
                return {}

        with TempDir():
            write_json("state.json", self.seeded_state(today()))
            market = [item(1000 + i, "iPhone 13 128GB", 290 + i * 4, user_id=500 + i) for i in range(12)]
            cand = item(1, "iPhone 13 128GB", 200, user_id=901)
            client = Blocked({"iPhone 13": market + [cand]},
                             pages={"1": "iPhone 13 128GB battery 89"})
            logs = [run(client, FakeTelegram()) for _ in range(3)]
            self.assertIn("šalies nustatyti nepavyko – bandysiu vėliau", logs[0])
            # treciasis (DETAIL_RETRIES) – nurasom galutinai
            self.assertIn("šalis nežinoma, tekstas ne lietuviškas", logs[2])
            self.assertIn("vinted:1", read_json("seen.json"))

    def test_seen_listings_backfilled_when_budget_allows(self):
        """Ribotos uzklausos pirma eina naujiems, o kas liko – jau matytiems, kuriu salis
        dar nezinoma. Kiekvienas patikrintas grazina i rinkos kaina dar viena tikra
        lietuviska kaina (kitaip jie liktu uzbraukti amzinai)."""
        with TempDir():
            market = [item(1000 + i, "iPhone 13 128GB", 290 + i * 4, user_id=600 + i) for i in range(10)]
            users = {600 + i: seller("LT") for i in range(10)}
            # pirmas paleidimas: uzklausu tik 3, tad 7 lieka nepatikrinti
            lt_config(SELLER_COUNTRY_LOOKUPS=3)
            client = CountingClient({"iPhone 13": market}, users=users)
            run(client, FakeTelegram())
            items = read_json("state.json")["market"]["items"]
            self.assertEqual(sum(1 for e in items.values() if e.get("x") == "salis?"), 7, items)
            # antras paleidimas: nauju nera, tad uzklausos eina jau matytiems
            lt_config(SELLER_COUNTRY_LOOKUPS=10)
            run(client, FakeTelegram())
            items = read_json("state.json")["market"]["items"]
            self.assertEqual(sum(1 for e in items.values() if e.get("x") == "salis?"), 0, items)
            self.assertEqual(len([e for e in items.values() if not e.get("x")]), 10)

    def test_country_learned_later_returns_listing_to_market(self):
        """Kai salis paaiskeja (pvz. kito paleidimo uzklausa), irasas vel skaiciuojamas."""
        from vinted.util import today
        from tests.test_flow import write_json

        class Blocked(FakeClient):
            users_blocked = "HTTP 429"

            def fetch_user(self, uid):
                return {}

        with TempDir():
            write_json("state.json", self.seeded_state(today()))
            market = [item(1000 + i, "iPhone 13 128GB", 290 + i * 4, user_id=500 + i) for i in range(12)]
            cand = item(1, "iPhone 13 128GB", 200, user_id=901)
            run(Blocked({"iPhone 13": market + [cand]},
                        pages={"1": "iPhone 13 128GB battery 89"}), FakeTelegram())
            self.assertEqual(read_json("state.json")["market"]["items"]["vinted:1"]["x"], "salis?")
            # kitas paleidimas: pardaveju API vel atsako
            ok = CountingClient({"iPhone 13": market + [cand]},
                                pages={"1": "iPhone 13 128GB battery 89"},
                                users={901: seller("LT")})
            tg = FakeTelegram()
            log = run(ok, tg)
            items = read_json("state.json")["market"]["items"]
            self.assertNotIn("x", items["vinted:1"], log)
            self.assertEqual([d["id"] for d, _ in tg.deals], ["vinted:1"], log)


class LithuanianScoreTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_positive_proof_required(self):
        from vinted.language import looks_lithuanian
        for text in ["Parduodu tvarkingą telefoną", "Telefonas veikia puikiai, be ibrezimu",
                     "geros būklės, su dėklu"]:
            self.assertTrue(looks_lithuanian(text), text)
        for text in ["iPhone 13 128GB", "iPhone 12 64g", "Apple iPhone 16 pro 512gb czarny tytan",
                     "iPhone 13 mini", ""]:
            self.assertFalse(looks_lithuanian(text), text)


if __name__ == "__main__":
    unittest.main()
