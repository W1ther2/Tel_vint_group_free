"""Viso paleidimo testai su netikru Vinted ir netikru Telegram."""
import contextlib
import io
import json
import time
import unittest

from tests.helpers import reset_config, TempDir, item
from vinted.finder import Run


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


class FakeClient:
    blocked_queries = 0

    def __init__(self, catalog, pages=None, users=None, statuses=None):
        self.catalog = catalog            # query -> [items]
        self.pages = pages or {}          # item_id -> aprasymas
        self.users = users or {}
        self.statuses = statuses or {}    # item_id -> (http, html)
        self.last_error = ""
        self.page_requests = []

    def start(self):
        pass

    def fetch_items(self, query, pages, seen=None):
        return list(self.catalog.get(query, []))

    def fetch_item_page(self, url):
        iid = url.rstrip("/").split("/")[-1].split("-")[0]
        self.page_requests.append(iid)
        if iid in self.statuses:
            code, page = self.statuses[iid]
            return code, page, f"https://www.vinted.lt/items/{iid}"
        desc = self.pages.get(iid, "Parduodu telefoną, veikia puikiai, siunčiu per Vinted")
        return 200, f'<meta property="og:description" content="{desc}">', f"https://www.vinted.lt/items/{iid}"

    def fetch_user(self, uid):
        return self.users.get(uid, {"country_code": "LT", "city": "Vilnius", "feedback_reputation": 0.98,
                                    "feedback_count": 25, "given_item_count": 30,
                                    "created_at": "2021-01-01T00:00:00Z"})


class FakeTelegram:
    def __init__(self, updates=None, callbacks=None, private=None):
        self.deals, self.messages, self.answers = [], [], []
        self.updates = updates or []            # [(update_id, tekstas)] – grupeje
        self.callbacks = callbacks or []        # [(update_id, data, user, name)]
        self.private = private or []            # [(update_id, tekstas, user, chat, name)]

    def send_deal(self, deal, silent=False, chat_id=None):
        self.deals.append((deal, silent) if chat_id is None else (deal, chat_id))
        return True

    def send_message(self, text, silent=False, chat_id=None):
        self.messages.append(text if chat_id is None else f"[{chat_id}] {text}")
        return True

    def answer_callback(self, callback_id, text, alert=False):
        self.answers.append(text)
        return True

    def get_updates(self, offset):
        msgs = [{"text": t, "chat": "42", "user": "1", "name": "Testas", "private": False}
                for i, t in self.updates if i > offset]
        msgs += [{"text": t, "chat": chat, "user": u, "name": n, "private": True}
                 for i, t, u, chat, n in self.private if i > offset]
        cbs = [{"data": d, "user": u, "name": n, "id": f"cb{i}"}
               for i, d, u, n in self.callbacks if i > offset]
        ids = ([i for i, _ in self.updates] + [i for i, *_ in self.callbacks]
               + [i for i, *_ in self.private])
        return msgs, cbs, max([offset] + ids)


def market_items(n=20, base=1000, low=260, high=340):
    return [item(base + i, "iPhone 13 128GB", low + (high - low) * i // (n - 1), user_id=500 + i) for i in range(n)]


def run(client, tg):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        Run(client, tg, sleep=lambda s: None).run()
    return out.getvalue()


class FlowTest(unittest.TestCase):
    def setUp(self):
        reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8, MARKET_PERCENTILE=0.5,
                     MIN_DISCOUNT=0.15, LOUD_DISCOUNT=0.30)

    def test_deal_rejects_and_silent(self):
        with TempDir():
            cat = market_items() + [
                item(1, "iPhone 13 128GB", 190, user_id=1),                 # ~20% -> tyliai
                item(2, "iPhone 13 128GB", 150, user_id=2),                 # ~40% -> garsiai
                item(3, "iPhone 13 128GB", 150, user_id=3),                 # iCloud -> atmesta
                item(4, "Dėklas iPhone 13", 15, user_id=4),                 # priedas
                item(5, "iPhone 13 128GB", 200, user_id=5),                 # lenkiskai
                item(6, "iPhone 13 128GB", 150, user_id=6),                 # uzsienio pardavejas
                item(7, "iPhone 13 128GB", 30, user_id=7),                  # per pigu telefonui
            ]
            client = FakeClient({"iPhone 13": cat},
                                pages={"3": "iCloud užblokuotas", "5": "Sprzedam, stan bardzo dobry",
                                       "2": "Baterija 91%, siunčiu per Vinted, be įbrėžimų"},
                                users={6: {"country_code": "PL", "feedback_reputation": 1, "feedback_count": 9}})
            tg = FakeTelegram()
            log = run(client, tg)
            sent = {d["id"]: (d, silent) for d, silent in tg.deals}
            self.assertEqual(set(sent), {"1", "2"}, log)
            self.assertTrue(sent["1"][1])
            self.assertFalse(sent["2"][1])
            self.assertEqual(sent["2"][0]["battery"], 91)
            self.assertGreater(sent["2"][0]["profit"], 0)
            for reason in ["neveikiantis / užrakintas / netestuotas", "ne telefonas / kitas modelis", "kalba", "salis",
                           "per pigu (sugedęs / dalims / ne telefonas?)"]:
                self.assertIn(reason, log)
            state = read_json("state.json")
            self.assertEqual(state["market"]["items"]["2"]["a"], 150)

            # antras paleidimas – niekas nesiunciama pakartotinai
            tg2 = FakeTelegram()
            run(client, tg2)
            self.assertEqual(tg2.deals, [])

    def test_only_tidy_phones(self):
        """Tikri pavyzdziai is Telegram: uzrakintas XR ir paveikslas is iPhone X detaliu."""
        reset_config(SEARCH_QUERIES=["iPhone XR"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8, MARKET_PERCENTILE=0.5,
                     MIN_DISCOUNT=0.15, TIDY_ONLY=True, HARD_MIN_PRICE_RATIO=0.4)
        with TempDir():
            market = [item(1000 + i, "iPhone XR 64GB", 125 + i * 2, user_id=600 + i) for i in range(20)]
            junk = [item(2000 + i, "iPhone XR", 20 + i, user_id=700 + i, status="Patenkinama") for i in range(15)]
            cat = market + junk + [
                item(1, "Apple iphone XR", 80, user_id=1, status="Labai gera"),       # uzrakintas kodu
                item(2, "Paveikslas iPhone XR", 60, user_id=2, status="Nauja be etikečių"),
                item(3, "iPhone XR 64GB", 85, user_id=3, status="Patenkinama"),       # prasta bukle
                item(4, "iPhone XR 64GB", 80, user_id=4, status="Labai gera"),        # linijos ekrane
                item(5, "iPhone XR 64GB", 90, user_id=5, status="Labai gera"),        # tvarkingas
                item(6, "iPhone XR 64GB", 88, user_id=6, status="Labai gera"),        # smulkus ibrezimai – ok
                item(7, "Iphone X 6s XR 13 15pro 8plus 7plus 12pro 11", 50, user_id=7),  # dezutes
                item(8, "iPhone XR 64GB", 85, user_id=8, status="Labai gera"),        # dezutes (aprasymas)
            ]
            client = FakeClient({"iPhone XR": cat}, pages={
                "1": "Telefonas įsijungia ir prašo kodo kurio mes nežinome. Toliau netestuotas.",
                "4": "Veikia, bet ekrane yra žalia linija",
                "5": "Parduodu tvarkingą telefoną, baterija 88%, siunčiu per Vinted",
                "6": "Veikia puikiai, yra smulkių įbrėžimų, baterija 90%",
                "8": "TUŠČIOS DĖŽUTĖS EMPTY BOX. Tuščios orginalios dėžutės, kaina už visas",
            })
            tg = FakeTelegram()
            log = run(client, tg)
            self.assertEqual(sorted(d["id"] for d, _ in tg.deals), ["5", "6"], log)
            quote = tg.deals[0][0]["quote"]
            self.assertGreater(quote.price, 100)                     # sugede/pigus nesugadino rinkos kainos

    def test_collects_catalog_and_brand_ids(self):
        reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=100)
        with TempDir():
            cat = [item(1, "iPhone 13 128GB", 200, catalog_id=2342, brand={"id": 12, "title": "Apple"}),
                   item(2, "Dėklas iPhone 13", 10, catalog_id=9999, brand_id=77)]
            log = run(FakeClient({"iPhone 13": cat}), FakeTelegram())
            self.assertIn('"CATALOG_IDS": [2342]', log)
            self.assertIn('"BRAND_IDS": [12]', log)

    def test_personal_buttons_and_dm(self):
        reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8, MARKET_PERCENTILE=0.5)
        with TempDir():
            cat = market_items() + [item(1, "iPhone 13 128GB", 150, user_id=7)]
            client = FakeClient({"iPhone 13": cat})
            # 1) vartotojas paspaudzia "Sekti si modeli" ir parasoma botui privaciai
            tg = FakeTelegram(callbacks=[(5, "w|13", "77", "Vy")],
                              private=[(6, "/start", "77", "555", "Vy")])
            run(client, tg)
            self.assertTrue(any("Įsiminta" in a or "Siųsiu" in a for a in tg.answers))
            users = read_json("state.json")["users"]
            self.assertEqual(users["77"]["watch"], ["13"])
            self.assertEqual(users["77"]["chat"], "555")

            # 2) naujas sandoris – ateina ir i grupe, ir asmeniskai
            client.catalog["iPhone 13"] = cat + [item(2, "iPhone 13 128GB", 145, user_id=8)]
            tg2 = FakeTelegram()
            run(client, tg2)
            targets = [t for _, t in tg2.deals]
            self.assertIn("555", targets)                      # asmenine zinute
            self.assertIn(False, targets)                      # grupes zinute (silent=False)

            # 3) sekimą galima išjungti tuo pačiu mygtuku
            tg3 = FakeTelegram(callbacks=[(9, "w|13", "77", "Vy")])
            client.catalog["iPhone 13"] = cat + [item(3, "iPhone 13 128GB", 140, user_id=9)]
            run(client, tg3)
            self.assertNotIn("555", [t for _, t in tg3.deals])

    def test_group_commands_only_for_admin(self):
        reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, ADMIN_IDS=["999"])
        with TempDir():
            tg = FakeTelegram(updates=[(5, "/pagalba")])       # rasys ne adminas (user "1")
            run(FakeClient({"iPhone 13": []}), tg)
            self.assertEqual([m for m in tg.messages if "/kaina" in m], [])

    def test_time_limit_stops_run(self):
        reset_config(SEARCH_QUERIES=["A", "B", "C"], HEARTBEAT_HOURS=0, MAX_RUN_MINUTES=1e-9)
        with TempDir():
            client = FakeClient({"A": [], "B": [], "C": []})
            log = run(client, FakeTelegram())
            self.assertIn("laiko limitas", log)

    def test_query_rotation(self):
        reset_config(SEARCH_QUERIES=["A", "B", "C"], HEARTBEAT_HOURS=0, ROTATE_QUERIES=True)
        with TempDir():
            client = FakeClient({})
            first = []
            for _ in range(3):
                log = run(client, FakeTelegram())
                first.append(log.split("Tikrinama: '")[1].split("'")[0])
            self.assertEqual(first, ["A", "B", "C"])

    def test_price_drop(self):
        with TempDir():
            client = FakeClient({"iPhone 13": market_items() + [item(1, "iPhone 13 128GB", 290)]})
            tg = FakeTelegram()
            run(client, tg)
            self.assertEqual(tg.deals, [])
            client.catalog["iPhone 13"][-1] = item(1, "iPhone 13 128GB", 200)
            tg2 = FakeTelegram()
            run(client, tg2)
            self.assertEqual(len(tg2.deals), 1)
            self.assertEqual(tg2.deals[0][0]["drop_from"], 290)
            # nedidelis papildomas atpigimas – nesiunciam dar karta
            client.catalog["iPhone 13"][-1] = item(1, "iPhone 13 128GB", 195)
            tg3 = FakeTelegram()
            run(client, tg3)
            self.assertEqual(tg3.deals, [])

    def test_not_enough_data_retried_later(self):
        reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8, USE_TYPICAL_FALLBACK=False)
        with TempDir():
            client = FakeClient({"iPhone 13": [item(1, "iPhone 13", 150)]})
            log = run(client, FakeTelegram())
            self.assertIn("per mazai kainu duomenu", log)
            self.assertNotIn("1", read_json("seen.json"))

    def test_rare_model_uses_typical_price(self):
        reset_config(SEARCH_QUERIES=["iPhone 16e"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8)
        with TempDir():
            from vinted.phone import typical_price
            price = round(typical_price("16e") * 0.6)
            client = FakeClient({"iPhone 16e": [item(1, "iPhone 16e 128GB", price)]},
                                pages={"1": "Parduodu tvarkingą telefoną, baterija 95%, siunčiu per Vinted"})
            tg = FakeTelegram()
            run(client, tg)
            self.assertEqual(len(tg.deals), 1)
            self.assertEqual(tg.deals[0][0]["quote"].source, "apytikslė")

    def test_commands_and_pause(self):
        with TempDir():
            client = FakeClient({"iPhone 13": [item(1, "iPhone 13 128GB", 140)]})
            reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8, ADMIN_IDS=["1"])
            tg = FakeTelegram(updates=[(5, "/kaina 13 200"), (6, "/pauze")])
            log = run(client, tg)
            self.assertEqual(tg.deals, [])
            self.assertIn("pauzė", log)
            self.assertTrue(any("rinkos kaina 200" in m for m in tg.messages))
            state = read_json("state.json")
            self.assertEqual(state["telegram_offset"], 6)
            self.assertEqual(state["overrides"]["MARKET_PRICES"], {"13": 200})

            tg2 = FakeTelegram(updates=[(5, "/kaina 13 200"), (6, "/pauze"), (7, "/testi")])
            client.catalog["iPhone 13"] = [item(2, "iPhone 13 128GB", 140)]
            run(client, tg2)
            self.assertEqual(len(tg2.messages), 1)                  # senos komandos nevykdomos antra karta
            self.assertEqual(len(tg2.deals), 1)
            self.assertEqual(tg2.deals[0][0]["quote"].source, "rankinė")

    def test_sold_check_and_sold_prices(self):
        reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=100, MIN_SOLD_SAMPLES=3,
                     SOLD_CHECK_AFTER_DAYS=0, SOLD_CHECKS_PER_RUN=50,
                     USE_TYPICAL_FALLBACK=False, GONE_AS_SOLD=False)
        with TempDir():
            from vinted.util import today
            d = today() - 3
            state = {"market_version": 3, "market": {"items": {str(i): {"m": "13", "s": "128 GB", "p": 190 + i, "f": d, "l": d,
                                                   "c": d, "st": "active"} for i in range(1, 5)}}}
            write_json("state.json", state)
            client = FakeClient({"iPhone 13": [item(9, "iPhone 13 128GB", 150)]},
                                statuses={"1": (200, '{"is_closed":true}'), "2": (200, '{"is_closed":true}'),
                                          "3": (200, '{"is_closed":true}'), "4": (404, "")})
            run(client, FakeTelegram())
            items = read_json("state.json")["market"]["items"]
            self.assertEqual([items[k]["st"] for k in "1234"], ["sold", "sold", "sold", "gone"])
            tg = FakeTelegram()
            client.catalog["iPhone 13"] = [item(10, "iPhone 13 128GB", 150)]
            run(client, tg)
            self.assertEqual(len(tg.deals), 1)
            self.assertEqual(tg.deals[0][0]["quote"].source, "parduoti")

    def test_heartbeat_and_zero_warning(self):
        reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=24, FAIL_ALERT_RUNS=3)
        with TempDir():
            tg = FakeTelegram()
            run(FakeClient({}), tg)
            self.assertTrue(any("skriptas veikia" in m for m in tg.messages))
            # vienkartinis nesekmingas paleidimas (pvz. Vinted 403) – dar nepranesam
            self.assertFalse(any("ISPEJIMAS" in m for m in tg.messages))
            for _ in range(2):
                tg = FakeTelegram()
                run(FakeClient({}), tg)
            self.assertTrue(any("ISPEJIMAS" in m for m in tg.messages))
            self.assertEqual(read_json("state.json")["fail_streak"], 0)
            tg = FakeTelegram()
            run(FakeClient({}), tg)
            self.assertFalse(any("ISPEJIMAS" in m for m in tg.messages))
            tg2 = FakeTelegram()
            run(FakeClient({}), tg2)
            self.assertFalse(any("skriptas veikia" in m for m in tg2.messages))


class CardTest(unittest.TestCase):
    def test_card(self):
        reset_config()
        from vinted.telegram import format_card
        from vinted.market import Quote
        card = format_card({
            "model": "13", "storage": "128 GB", "price": 150.0, "drop_from": 190.0, "discount": 0.25,
            "value": 200.0, "profit": 38.3, "description": "Parduodu", "risk_level": "vidutinė",
            "risk_reasons": ["tik atsiėmimas iš rankų", "pardavėjas be atsiliepimų"],
            "quote": Quote(205, 12, "parduoti", True), "defects": [], "condition": "Labai gera",
            "battery": 88, "seller": {"rating": 4.9, "reviews": 20, "sold": 31, "country": "LT", "city": "Kaunas"},
            "url": "https://www.vinted.lt/items/1?a=1&b=2"})
        for part in ["📉 ⚠️ <b>iPhone 13 128 GB | 150 €</b>", "Atpigo:</b> 190 € → 150 €", "25% pigiau",
                     "Galimas pelnas:</b> ~38 €", "Rizika: vidutinė", "205 € (128 GB, 12 parduotų)",
                     "pardavė 31", "Lietuva, Kaunas", "&amp;b=2"]:
            self.assertIn(part, card)
        self.assertLessEqual(len(card), 1024)


if __name__ == "__main__":
    unittest.main()
