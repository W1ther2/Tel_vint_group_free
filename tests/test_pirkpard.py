"""Pirkpard.lt saltinis – tikrinamas su tikrais API atsakymo irasais (tests/fixtures.py)."""
import contextlib
import io
import unittest

from tests.fixtures import PIRKPARD_ITEMS, pirkpard_response
from tests.helpers import reset_config
from vinted.phone import detect_model, extract_battery
from vinted.sources.pirkpard import (PirkpardSource, parse_time, city_of, seller_of,
                                     skip_reason, listing_state, CONDITION_MAP)

NORMAL, SOLD, AUCTION, PARTS = PIRKPARD_ITEMS


class FakeApi:
    """Vietoj tinklo: grazina is anksto paruostus atsakymus pagal puslapi."""

    def __init__(self, pages=None, fail=False):
        self.pages = pages or {1: pirkpard_response()}
        self.fail = fail
        self.last_error = ""
        self.blocked = ""
        self.calls = []

    def start(self):
        pass

    def sleep(self, seconds):
        pass

    def get_json(self, params, tries=3):
        self.calls.append(dict(params))
        if self.fail:
            self.blocked = "Cloudflare apsauga"
            return None
        return self.pages.get(int(params.get("page", 1)))


class PiecesTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_parse_time(self):
        from datetime import datetime, timezone
        laukiama = datetime(2026, 9, 23, 9, 5, 58, tzinfo=timezone.utc).timestamp()
        self.assertAlmostEqual(parse_time("2026-09-23T09:05:58.000000Z"), laukiama, places=0)
        self.assertIsNone(parse_time(None))
        self.assertIsNone(parse_time("nesamone"))

    def test_city(self):
        self.assertEqual(city_of(["Vilnius", "Visa Lietuva"]), "Vilnius")
        self.assertEqual(city_of(["Visa Lietuva"]), "Visa Lietuva")
        self.assertIsNone(city_of([]))

    def test_seller_without_ratings_has_no_rating(self):
        """Be atsiliepimu ivertinimas yra „0.00“ – tai nezinia, o ne prastas pardavejas."""
        info = seller_of(NORMAL)
        self.assertEqual(info["country"], "LT")
        self.assertEqual(info["city"], "Vilnius")
        self.assertEqual(info["reviews"], 0)
        self.assertNotIn("rating", info)
        self.assertEqual(info["id"], "2216")

    def test_seller_with_ratings(self):
        info = seller_of(PARTS)
        self.assertEqual((info["rating"], info["reviews"]), (4.9, 8))

    def test_no_personal_data_kept(self):
        """Pardavejo el. pastas, telefonas ir vardas i musu duomenis nepatenka."""
        raw = dict(NORMAL)
        raw["phone"] = "+370 6 0110724"
        raw["vendor"] = {**raw["vendor"], "user": {"email": "kazkas@example.com",
                                                   "name": "Vardas", "avatar": "x.jpg"}}
        info = seller_of(raw)
        text = repr(info).lower()
        for leaked in ("example.com", "0110724", "vardas", "avatar", "email"):
            self.assertNotIn(leaked, text)

    def test_skip_reasons(self):
        self.assertEqual(skip_reason(NORMAL), "")
        self.assertEqual(skip_reason(PARTS), "dalims / ne telefonas")
        self.assertEqual(skip_reason(AUCTION), "aukcionas")
        self.assertEqual(skip_reason({**NORMAL, "is_reserved": True}), "rezervuotas")
        self.assertEqual(skip_reason({**NORMAL, "is_service": True}),
                         "paslauga / ieskomas skelbimas")

    def test_auctions_can_be_allowed(self):
        reset_config(PIRKPARD_SKIP_AUCTIONS=False)
        self.assertEqual(skip_reason(AUCTION), "")

    def test_listing_state(self):
        self.assertEqual(listing_state(NORMAL), "active")
        self.assertEqual(listing_state(SOLD), "sold")
        self.assertEqual(listing_state({**NORMAL, "expired": True}), "gone")
        self.assertEqual(listing_state({**NORMAL, "is_active": False}), "sold")

    def test_condition_map_covers_api_values(self):
        for value in ("new", "like_new", "excellent", "good", "fair", "for_parts"):
            self.assertIn(value, CONDITION_MAP)


class ListingTest(unittest.TestCase):
    def setUp(self):
        reset_config()
        self.source = PirkpardSource(client=FakeApi())

    def test_normal_listing(self):
        l = self.source.to_listing(NORMAL)
        self.assertEqual(l.uid, "pirkpard:3748")
        self.assertEqual(l.price, 430.0)
        self.assertEqual(l.condition, "Labai gera")
        self.assertEqual(detect_model(l.title), "14 Pro Max")
        self.assertEqual(l.photo_count, 4)
        self.assertEqual(l.url,
                         "https://pirkpard.lt/lt/product/iphone-14-pro-max-256-gb-vendor-2216")
        self.assertEqual(l.seller_id, "2216")
        self.assertFalse(l.skip_reason)
        self.assertIsNotNone(l.created_at)

    def test_description_arrives_with_the_list(self):
        """Svarbiausias Pirkpard privalumas: aprasymo atskirai traukti nereikia."""
        l = self.source.to_listing(NORMAL)
        detail = self.source.detail(l)
        self.assertEqual(detail.status, "active")
        self.assertEqual(extract_battery(detail.description), 78)
        self.assertFalse(self.source.client.calls)        # nei vienos uzklausos
        self.assertFalse(self.source.detail_needs_request)

    def test_sold_listing(self):
        detail = self.source.detail(self.source.to_listing(SOLD))
        self.assertEqual(detail.status, "sold")

    def test_non_eur_price_ignored(self):
        l = self.source.to_listing({**NORMAL, "currency_code": "USD"})
        self.assertIsNone(l.price)

    def test_seller_flag_not_leaked_to_card(self):
        detail = self.source.detail(self.source.to_listing(NORMAL))
        self.assertNotIn("verified", detail.seller)


class SearchTest(unittest.TestCase):
    def setUp(self):
        reset_config(SLEEP_SECONDS=0)

    def test_one_request_returns_everything(self):
        api = FakeApi()
        source = PirkpardSource(client=api)
        with contextlib.redirect_stdout(io.StringIO()):
            listings = source.search("iphone", pages=3)
        self.assertEqual(len(listings), 4)
        self.assertEqual(len(api.calls), 1)               # last_page=1, toliau neina
        self.assertEqual(api.calls[0]["sort"], "newest")
        self.assertEqual(api.calls[0]["search"], "iphone")

    def test_stops_when_all_seen(self):
        api = FakeApi(pages={1: pirkpard_response(last_page=5),
                             2: pirkpard_response(last_page=5, page=2)})
        source = PirkpardSource(client=api)
        seen = {f"pirkpard:{i['id']}" for i in PIRKPARD_ITEMS}
        with contextlib.redirect_stdout(io.StringIO()):
            source.search("iphone", pages=5, seen=seen)
        self.assertEqual(len(api.calls), 1)

    def test_blocked_marks_source_unavailable(self):
        source = PirkpardSource(client=FakeApi(fail=True))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(source.search("iphone", pages=2), [])
        self.assertEqual(source.unavailable, "Cloudflare apsauga")

    def test_status_from_single_full_listing(self):
        """Busena visiems gaunama viena uzklausa – atskirai tikrinti nereikia."""
        api = FakeApi()
        source = PirkpardSource(client=api)
        self.assertEqual(source.status("3748"), "active")
        self.assertEqual(source.status("3645"), "sold")
        self.assertEqual(source.status("999999"), "gone")   # nebera sarase
        self.assertEqual(len(api.calls), 1)                 # ir tik viena uzklausa visiems
        self.assertEqual(api.calls[0]["include_sold"], 1)


class FlowTest(unittest.TestCase):
    """Visas paleidimas su Pirkpard salia Vinted."""

    def test_deal_reaches_telegram(self):
        from tests.helpers import TempDir
        from tests.test_flow import FakeClient, FakeTelegram, market_items
        from vinted.sources.vinted_source import VintedSource
        from vinted.finder import Run

        reset_config(SOURCES=["vinted", "pirkpard"], SEARCH_QUERIES=["iPhone 14 Pro Max"],
                     PIRKPARD_QUERIES=["iphone"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8,
                     MARKET_PERCENTILE=0.5, MIN_DISCOUNT=0.15, ASKING_SALE_FACTOR=1.0,
                     SLEEP_SECONDS=0, MIN_BATTERY=0, AUTO_CALIBRATE=False,
                     MARKET_PRICES={"14 Pro Max": 600})
        with TempDir():
            vinted = VintedSource(client=FakeClient({"iPhone 14 Pro Max": market_items()}))
            pirkpard = PirkpardSource(client=FakeApi())
            tg = FakeTelegram()
            with contextlib.redirect_stdout(io.StringIO()) as out:
                Run([vinted, pirkpard], tg, sleep=lambda s: None).run()
            log = out.getvalue()
            deals = {d["id"]: d for d, _ in tg.deals}
            self.assertIn("pirkpard:3748", deals, log)
            deal = deals["pirkpard:3748"]
            self.assertEqual(deal["source_label"], "Pirkpard")
            self.assertEqual(deal["battery"], 78)
            self.assertEqual(deal["model"], "14 Pro Max")
            self.assertTrue(deal["url"].startswith("https://pirkpard.lt/"))
            # aukcionas ir dalys – atmesti su aiskia priezastimi
            for reason in ("aukcionas", "dalims / ne telefonas"):
                self.assertIn(reason, log)
            # ir – svarbiausia – aukciono kaina nepateko i rinkos kainu istorija
            self.assertNotIn("iPhone 16 Pro ", log.split("Rinkos kainos")[1])


class SeenTest(unittest.TestCase):
    """Gyvas skundas: „Pirkpard nekaupia seen – meta vel tuos pacius“."""

    def setUp(self):
        reset_config(SOURCES=["pirkpard"], PIRKPARD_QUERIES=["iphone"], HEARTBEAT_HOURS=0,
                     MIN_SAMPLES=8, MARKET_PERCENTILE=0.5, MIN_DISCOUNT=0.15,
                     ASKING_SALE_FACTOR=1.0, SLEEP_SECONDS=0, MIN_BATTERY=0,
                     AUTO_CALIBRATE=False, MARKET_PRICES={"14 Pro Max": 600}, SEEN_MAX_AGE_DAYS=7)

    def run_once(self):
        from tests.test_flow import FakeTelegram
        from vinted.finder import Run
        tg = FakeTelegram()
        with contextlib.redirect_stdout(io.StringIO()):
            Run([PirkpardSource(client=FakeApi())], tg, sleep=lambda s: None).run()
        return [d["id"] for d, _ in tg.deals]

    def test_still_listed_after_a_week_not_resent(self):
        """Pirkpard skelbimai sarase isbuna savaites. Anksciau po 7 d. jie buvo
        „pamirstami“ ir issiunciami is naujo."""
        import json, time
        from tests.helpers import TempDir
        with TempDir():
            self.assertEqual(self.run_once(), ["pirkpard:3748"])
            for day in range(1, 15):              # dvi savaites, kasdien po paleidima
                with open("seen.json", encoding="utf-8") as f:
                    seen = json.load(f)
                with open("seen.json", "w", encoding="utf-8") as f:
                    json.dump({k: v - 86400 for k, v in seen.items()}, f)   # „praejo diena“
                self.assertEqual(self.run_once(), [], f"issiusta is naujo {day}-a diena")

    def test_save_while_other_source_adds(self):
        """Lygiagreciai: vienas saltinis saugo seen, kitas tuo metu prideda."""
        import os, tempfile, threading, time
        from vinted.state import save_seen
        seen = {f"vinted:{i}": time.time() for i in range(20000)}
        stop, errors = [False], []

        def writer():
            i = 0
            while not stop[0] and i < 200000:
                seen[f"pirkpard:{i}"] = time.time()
                i += 1

        t = threading.Thread(target=writer)
        t.start()
        path = os.path.join(tempfile.mkdtemp(), "seen.json")
        try:
            for _ in range(5):
                try:
                    save_seen(seen, path)
                except RuntimeError as e:
                    errors.append(e)
        finally:
            stop[0] = True
            t.join()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
