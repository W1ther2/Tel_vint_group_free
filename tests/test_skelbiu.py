"""Skelbiu.lt parseris – tikrinamas su tikru svetaines HTML (tests/fixtures.py)."""
import contextlib
import io
import unittest

from tests.fixtures import SKELBIU_LIST, SKELBIU_ITEM, SKELBIU_GONE
from tests.helpers import reset_config
from vinted.listing import Detail
from vinted.phone import detect_model, extract_battery
from vinted.sources.skelbiu import (SkelbiuSource, parse_list, parse_detail, parse_price,
                                    parse_place_and_time, parse_registered, normalize_title)

NOW = 1_758_400_000.0        # 2026-09-20


class FakeHttp:
    """Vietoj tikro tinklo: adresas -> (statusas, HTML)."""

    def __init__(self, pages):
        self.pages = pages
        self.last_error = ""
        self.requested = []

    def start(self):
        pass

    def sleep(self, seconds):
        pass

    def get(self, url, tries=3):
        self.requested.append(url)
        for needle, response in self.pages.items():
            if needle in url:
                return response
        return 404, ""


class ParsePiecesTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_price(self):
        self.assertEqual(parse_price('<div class="price">350 &euro;</div>'), 350.0)
        self.assertEqual(parse_price("1 250,50 &euro;"), 1250.5)
        self.assertIsNone(parse_price("<!-- --> "))
        self.assertIsNone(parse_price("Sutartinė"))

    def test_relative_time(self):
        city, ts = parse_place_and_time("Panevėžys, prieš 7 min.", now=NOW)
        self.assertEqual(city, "Panevėžys")
        self.assertAlmostEqual(ts, NOW - 420)
        self.assertAlmostEqual(parse_place_and_time("Kaunas, prieš 2 val.", now=NOW)[1], NOW - 7200)
        self.assertAlmostEqual(parse_place_and_time("Vilnius, prieš 1 d.", now=NOW)[1], NOW - 86400)

    def test_date_format(self):
        city, ts = parse_place_and_time("Vilnius, rugsėjo 14 d.", now=NOW)
        self.assertEqual(city, "Vilnius")
        self.assertLess(ts, NOW)
        self.assertGreater(ts, NOW - 10 * 86400)

    def test_registered(self):
        days = parse_registered("Užsiregistravo 2024 gegužę. Įdėjo skelbimų: 1")
        self.assertGreater(days, 300)
        self.assertIsNone(parse_registered("nieko"))

    def test_normalize_title(self):
        self.assertEqual(normalize_title("13 pro max 256gb"), "iPhone 13 pro max 256gb")
        self.assertEqual(normalize_title("iPhone 13 Pro"), "iPhone 13 Pro")
        # be sito modelio neatpazintume – Skelbiu daznai raso be "iPhone"
        self.assertIsNone(detect_model("13 pro max 256gb"))
        self.assertEqual(detect_model(normalize_title("13 pro max 256gb")), "13 Pro Max")


class ListPageTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_parses_all_items(self):
        rows = parse_list(SKELBIU_LIST, now=NOW)
        self.assertEqual([r["id"] for r in rows], ["87196074", "78523526", "30555297"])

    def test_first_item_fields(self):
        row = parse_list(SKELBIU_LIST, now=NOW)[0]
        self.assertEqual(row["title"], "iPhone 13 pro max 512gb")
        self.assertEqual(row["price"], 350.0)
        self.assertEqual(row["city"], "Panevėžys")
        self.assertAlmostEqual(row["created_at"], NOW - 420)
        self.assertEqual(row["url"],
                         "https://www.skelbiu.lt/skelbimai/iphone-13-pro-max-512gb-87196074.html")
        self.assertTrue(row["photo"].endswith("iphone-13-pro-max-512gb.jpg"))
        self.assertIsNone(row["condition"])                 # "Naudota" nieko nesako
        # aprasymo pradzia jau sarase – baterija matoma be skelbimo puslapio
        self.assertEqual(extract_battery(row["snippet"]), 83)

    def test_service_ad_without_price(self):
        row = parse_list(SKELBIU_LIST, now=NOW)[2]
        self.assertIsNone(row["price"])                     # ekranu keitimo paslauga
        self.assertIn("greitas", row["title"])

    def test_listing_conversion(self):
        source = SkelbiuSource(client=FakeHttp({}))
        listing = source.to_listing(parse_list(SKELBIU_LIST, now=NOW)[1])
        self.assertEqual(listing.uid, "skelbiu:78523526")
        self.assertEqual(listing.title, "iPhone 13 pro max 128 -256gb su garantija, kaip Nauji!")
        self.assertEqual(detect_model(listing.title), "13 Pro Max")
        self.assertEqual(listing.seller["country"], "LT")
        self.assertEqual(listing.seller["city"], "Vilnius")


class ItemPageTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_parse_detail(self):
        d = parse_detail(SKELBIU_ITEM, city="Kaunas")
        self.assertEqual(d["status"], "active")
        self.assertEqual(d["model"], "iPhone 13")
        self.assertIn("baterijos likutis 74%", d["description"])
        self.assertEqual(extract_battery(d["description"]), 74)
        self.assertEqual(d["seller"]["country"], "LT")
        self.assertEqual(d["seller"]["city"], "Kaunas")
        self.assertEqual(d["seller"]["active_items"], 3)
        self.assertGreater(d["seller"]["account_age_days"], 300)
        self.assertTrue(d["seller"]["verified"])

    def test_gone_page(self):
        self.assertEqual(parse_detail(SKELBIU_GONE)["status"], "gone")
        self.assertEqual(parse_detail("")["status"], "unknown")

    def test_source_detail(self):
        source = SkelbiuSource(client=FakeHttp({"87191092": (200, SKELBIU_ITEM)}))
        listing = source.to_listing(parse_list(SKELBIU_LIST, now=NOW)[0])
        listing.url = "https://www.skelbiu.lt/skelbimai/iphone-13-87191092.html"
        detail = source.detail(listing)
        self.assertIsInstance(detail, Detail)
        self.assertEqual(detail.status, "active")
        self.assertEqual(extract_battery(detail.description), 74)
        self.assertNotIn("verified", detail.seller)         # i korteles duomenis nekeliam

    def test_status_uses_saved_url(self):
        source = SkelbiuSource(client=FakeHttp({"iphone-13-87191092": (200, SKELBIU_ITEM),
                                                "x-87191092": (200, SKELBIU_GONE)}))
        url = "https://www.skelbiu.lt/skelbimai/iphone-13-87191092.html"
        self.assertEqual(source.status("87191092", url), "active")
        self.assertEqual(source.status("87191092"), "gone")  # be adreso – spejimas, todel saugom "u"


class SearchTest(unittest.TestCase):
    def setUp(self):
        reset_config(SLEEP_SECONDS=0)

    def test_search_builds_url_and_stops_on_seen(self):
        http = FakeHttp({"skelbimai/": (200, SKELBIU_LIST)})
        source = SkelbiuSource(client=http)
        with contextlib.redirect_stdout(io.StringIO()):
            listings = source.search("iPhone 13", pages=3)
        self.assertEqual(len(listings), 3)                  # antras puslapis – tie patys ID
        first = http.requested[0]
        self.assertIn("keywords=iPhone%2013", first)
        self.assertIn("category_id=480", first)
        self.assertIn("orderBy=1", first)

        seen = {"skelbiu:87196074", "skelbiu:78523526", "skelbiu:30555297"}
        http.requested.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            source.search("iPhone 13", pages=3, seen=seen)
        self.assertEqual(len(http.requested), 1)            # visi matyti – toliau nebeeina

    def test_browse_mode_uses_single_category_listing(self):
        """Kategorija 480 jau yra „Apple telefonai“ – raktazodziu nereikia."""
        from vinted import config
        source = SkelbiuSource(client=FakeHttp({}))
        config.cfg["SKELBIU_BROWSE_ALL"] = True
        self.assertEqual(source.queries(), [""])
        self.assertEqual(source.describe(""), "visi Apple telefonai")
        url = source.search_url("", 1)
        self.assertNotIn("keywords", url)
        self.assertIn("category_id=480", url)
        self.assertIn("orderBy=1", url)
        self.assertEqual(source.search_url("", 3),
                         "https://www.skelbiu.lt/skelbimai/3?category_id=480&orderBy=1"
                         "&user_type=0&type=0")

    def test_keyword_mode_still_available(self):
        from vinted import config
        source = SkelbiuSource(client=FakeHttp({}))
        config.cfg.update(SKELBIU_BROWSE_ALL=False, SEARCH_QUERIES=["iPhone 13", "iPhone 14"])
        self.assertEqual(source.queries(), ["iPhone 13", "iPhone 14"])
        self.assertIn("keywords=iPhone%2013", source.search_url("iPhone 13", 1))

    def test_browse_mode_makes_one_query_not_34(self):
        from tests.helpers import TempDir
        from tests.test_flow import FakeTelegram
        from vinted.finder import Run
        from vinted import config

        with TempDir():
            config.cfg.update(SKELBIU_BROWSE_ALL=True, SEARCH_QUERIES=["iPhone %d" % i for i in range(34)],
                              HEARTBEAT_HOURS=0, SLEEP_SECONDS=0)
            http = FakeHttp({"skelbimai/": (200, SKELBIU_LIST)})
            source = SkelbiuSource(client=http)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                Run([source], FakeTelegram(), sleep=lambda s: None).run()
            log = out.getvalue()
            self.assertEqual(log.count("Tikrinama [Skelbiu]"), 1, log)
            self.assertIn("visi Apple telefonai", log)
            self.assertTrue(all("keywords" not in u for u in http.requested if "?" in u))

    def test_user_agent_not_sent_with_curl_cffi(self):
        """curl_cffi pats prideda savo TLS parasa atitinkanti User-Agent.
        Irase savaji, parasas sakytu viena, antraste kita – Cloudflare tai mato."""
        from vinted.sources import skelbiu as mod
        original = mod.USING_CFFI
        try:
            mod.USING_CFFI = True
            self.assertNotIn("User-Agent", mod.headers())
            mod.USING_CFFI = False
            self.assertIn("User-Agent", mod.headers())
        finally:
            mod.USING_CFFI = original
        self.assertIn("Sec-Fetch-Mode", mod.headers())

    def test_recognises_cloudflare_block(self):
        from vinted.sources.skelbiu import why_blocked
        self.assertEqual(why_blocked("<title>Attention Required! | Cloudflare</title>"),
                         "Cloudflare apsauga")
        self.assertEqual(why_blocked("Just a moment... Ray ID: abc"), "Cloudflare apsauga")
        self.assertEqual(why_blocked("", {"CF-RAY": "abc123"}), "Cloudflare apsauga")
        self.assertEqual(why_blocked("Too many requests"), "uzklausu ribojimas")

    def test_unknown_block_is_not_guessed(self):
        """Nezinia nevadinam „uzklausu ribojimu“ – tai butu klaidinga diagnoze."""
        from vinted.sources.skelbiu import why_blocked
        self.assertEqual(why_blocked("Forbidden"), "neaiski priezastis")
        self.assertEqual(why_blocked("Forbidden", {"Server": "nginx"}),
                         "neaiski priezastis (serveris: nginx)")

    def test_block_details_shows_what_answered(self):
        from vinted.sources.skelbiu import block_details
        r = type("R", (), {})()
        r.headers = {"Server": "nginx", "CF-RAY": "8ab", "Set-Cookie": "x=1"}
        r.text = "  Forbidden\n\n  by policy "
        details = block_details(r)
        self.assertIn("nginx", details)
        self.assertIn("8ab", details)
        self.assertIn("Forbidden by policy", details)
        self.assertNotIn("Set-Cookie", details)          # slaptu dalyku i log'a nededam

    def test_tries_several_browser_signatures(self):
        """„chrome“ gali rodyti i sena versija – bandom kelis parasus is eiles."""
        from vinted.sources import skelbiu as mod
        made = []

        class Session:
            def __init__(self, profile): self.profile = profile
            def get(self, url, headers=None, timeout=None):
                r = type("R", (), {})()
                ok = self.profile == "chrome124"
                r.status_code = 200 if ok else 403
                r.text, r.headers = ("<html>gerai</html>" if ok else "Forbidden"), {}
                return r

        client = mod.SkelbiuClient(sleep=lambda s: None)
        original, mod.USING_CFFI = mod.USING_CFFI, True
        client._new_session = lambda p: (made.append(p), Session(p))[1]
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                client.start()
        finally:
            mod.USING_CFFI = original
        self.assertEqual(client.profile, "chrome124")
        self.assertFalse(client.blocked)
        self.assertEqual(made[:3], ["chrome", "chrome131", "chrome124"])
        self.assertIn("parasas: chrome124", out.getvalue())

    def test_reports_details_when_all_signatures_rejected(self):
        from vinted.sources import skelbiu as mod

        class Session:
            def __init__(self, profile): pass
            def get(self, url, headers=None, timeout=None):
                r = type("R", (), {})()
                r.status_code, r.text, r.headers = 403, "Forbidden", {"Server": "nginx"}
                return r

        client = mod.SkelbiuClient(sleep=lambda s: None)
        original, mod.USING_CFFI = mod.USING_CFFI, True
        client._new_session = Session
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                client.start()
        finally:
            mod.USING_CFFI = original
        log = out.getvalue()
        self.assertEqual(client.blocked, "neaiski priezastis (serveris: nginx)")
        self.assertIn("Skelbiu atsakymas:", log)
        self.assertIn("nginx", log)

    def test_block_from_first_request_stops_immediately(self):
        """Blokas nuo pirmos uzklausos = mus neileidzia. Laukti 30+60+120s beprasmiska."""
        from vinted.sources.skelbiu import SkelbiuClient
        slept = []
        client = SkelbiuClient(sleep=slept.append)

        class FakeResp:
            status_code = 403
            text = "<title>Attention Required! | Cloudflare</title>"

        class FakeSession:
            def __init__(self): self.calls = 0
            def get(self, url, headers=None, timeout=None):
                self.calls += 1
                return FakeResp()

        client.session = FakeSession()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            status, body = client.get("https://www.skelbiu.lt/skelbimai/?x=1")
        self.assertEqual((status, body), (0, ""))
        self.assertEqual(client.session.calls, 1)        # nebandoma kelis kartus
        self.assertEqual(slept, [])                      # ir nelaukiama nei sekundes
        self.assertEqual(client.blocked, "Cloudflare apsauga")
        self.assertIn("praleidziu Skelbiu", out.getvalue())

    def test_rate_limit_after_success_does_retry(self):
        """O jei anksciau pavyko – tai tik greitis, tad verta palaukti ir pakartoti."""
        from vinted.sources.skelbiu import SkelbiuClient
        slept = []
        client = SkelbiuClient(sleep=slept.append)
        client.ok_count = 5

        class FlakySession:
            def __init__(self): self.calls = 0
            def get(self, url, headers=None, timeout=None):
                self.calls += 1
                r = type("R", (), {})()
                r.status_code = 429 if self.calls == 1 else 200
                r.text = "Too many requests" if self.calls == 1 else "<html>gerai</html>"
                return r

        client.session = FlakySession()
        with contextlib.redirect_stdout(io.StringIO()):
            status, body = client.get("https://www.skelbiu.lt/skelbimai/?x=1")
        self.assertEqual(status, 200)
        self.assertEqual(len(slept), 1)
        self.assertFalse(client.blocked)

    def test_blocked_source_skipped_and_reported_once(self):
        from tests.helpers import TempDir
        from tests.test_flow import FakeClient, FakeTelegram, market_items, item
        from vinted.sources.vinted_source import VintedSource
        from vinted.finder import Run
        from vinted import config

        class BlockedSkelbiu(SkelbiuSource):
            def start(self):
                self.unavailable = "Cloudflare apsauga"

        with TempDir():
            config.cfg.update(SOURCES=["vinted", "skelbiu"], SEARCH_QUERIES=["iPhone 13"],
                              HEARTBEAT_HOURS=0, MIN_SAMPLES=8, MARKET_PERCENTILE=0.5,
                              SOURCE_ALERT_HOURS=12)
            catalog = market_items() + [item(1, "iPhone 13 128GB", 150, user_id=77)]
            for run_no in range(2):
                vinted = VintedSource(client=FakeClient({"iPhone 13": catalog}))
                tg = FakeTelegram()
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    Run([vinted, BlockedSkelbiu(client=FakeHttp({}))], tg,
                        sleep=lambda s: None).run()
                log = out.getvalue()
                self.assertIn("Skelbiu (Cloudflare apsauga)", log)
                warnings = [m for m in tg.messages if "nepasiekiamas" in m]
                # pirma karta pranesam, antra – jau ne (Telegram neuzkimsam)
                self.assertEqual(len(warnings), 1 if run_no == 0 else 0, log)

    def test_blocked_counts(self):
        source = SkelbiuSource(client=FakeHttp({}))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(source.search("iPhone 13", pages=2), [])
        self.assertEqual(source.blocked_queries, 1)


def skelbiu_page(rows):
    """Saraso puslapis is (id, antraste, kaina, aprasymas)."""
    items = []
    for iid, title, price, desc in rows:
        items.append(f"""
<a href="/skelbimai/x-{iid}.html" class="js-cfuser-link standard-list-item   " data-item-id="{iid}">
  <div class="collapsed-info"><div class="title">{title}</div></div>
  <div class="extended-info">
    <img src="https://skelbiu-img.dgn.lt/{iid}.jpg"/>
    <div class="content-block">
      <div class="title">{title}</div>
      <div class="first-dataline">{desc}</div>
      <div class="second-dataline">Vilnius, prie&#353; 10 min.</div>
      <div class="item-params"><div class='param'>B&#363;kl&#279;: Naudota</div></div>
      <div class="price-line"><div class="price">{price} &euro;</div></div>
    </div>
  </div>
</a>""")
    return "<div>" + "".join(items) + "</div>"


def skelbiu_item(description, seller="pardavejas"):
    return (f'<meta property="og:title" content="iPhone 13"/>'
            f'<meta property="og:description" content="{description}"/>'
            f'<div class="description">{description}</div>'
            f'<a href="/pasiulymai/{seller}/" class="all-sellers-items">Visi <span>4</span></a>'
            f'<div class="profile-info-line">U&#382;siregistravo 2019 kovo.</div>')


class BothSourcesFlowTest(unittest.TestCase):
    """Vienas paleidimas per abu saltinius: Vinted ir Skelbiu."""

    def setUp(self):
        reset_config(SOURCES=["vinted", "skelbiu"], SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0,
                     MIN_SAMPLES=8, MARKET_PERCENTILE=0.5, MIN_DISCOUNT=0.15, ASKING_SALE_FACTOR=1.0,
                     SLEEP_SECONDS=0, MIN_BATTERY=0, AUTO_CALIBRATE=False)

    def run_both(self, skelbiu_rows, sellers=None):
        from tests.test_flow import FakeClient, FakeTelegram, market_items
        from vinted.sources.vinted_source import VintedSource
        from vinted.finder import Run

        vinted = VintedSource(client=FakeClient({"iPhone 13": market_items()}))
        desc = "Tvarkingas telefonas, baterija 92%, visi dokumentai"
        pages = {"skelbimai/?": (200, skelbiu_page(skelbiu_rows))}
        for iid, *_ in skelbiu_rows:
            seller = (sellers or {}).get(iid, "pardavejas")
            pages[f"/skelbimai/x-{iid}."] = (200, skelbiu_item(desc, seller))
        http = FakeHttp(pages)
        skelbiu = SkelbiuSource(client=http)
        tg = FakeTelegram()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            Run([vinted, skelbiu], tg, sleep=lambda s: None).run()
        return tg, out.getvalue()

    def test_skelbiu_deal_reaches_telegram(self):
        from tests.helpers import TempDir
        with TempDir():
            tg, log = self.run_both([("900001", "iPhone 13 128GB", 150, "Tvarkingas")])
            deals = {d["id"]: d for d, _ in tg.deals}
            self.assertIn("skelbiu:900001", deals, log)
            deal = deals["skelbiu:900001"]
            self.assertEqual(deal["source_label"], "Skelbiu")
            self.assertTrue(deal["url"].startswith("https://www.skelbiu.lt/"))
            self.assertEqual(deal["seller"]["country"], "LT")
            self.assertEqual(deal["age"], "prieš 10 min.")
            self.assertIn("Tikrinama [Skelbiu]", log)

    def test_market_price_shared_between_sources(self):
        """Rinkos kaina sukaupta is Vinted – Skelbiu skelbimas vertinamas ta pacia kaina."""
        from tests.helpers import TempDir
        with TempDir():
            tg, log = self.run_both([("900002", "iPhone 13 128GB", 150, "Tvarkingas")])
            deal = next(d for d, _ in tg.deals if d["id"] == "skelbiu:900002")
            self.assertEqual(deal["quote"].source, "skelbimai")
            self.assertGreater(deal["quote"].samples, 8)

    def test_different_sellers_same_price_are_not_merged(self):
        """Du skirtingi zmones gali parduoti ta pati modeli uz ta pacia kaina – abu turi ateiti."""
        from tests.helpers import TempDir
        with TempDir():
            rows = [("900010", "iPhone 13 128GB", 150, "Tvarkingas"),
                    ("900011", "iPhone 13 128GB", 150, "Tvarkingas")]
            tg, log = self.run_both(rows, sellers={"900010": "jonas", "900011": "petras"})
            ids = {d["id"] for d, _ in tg.deals}
            self.assertEqual({"skelbiu:900010", "skelbiu:900011"}, ids, log)

    def test_same_seller_relisting_is_merged(self):
        from tests.helpers import TempDir
        with TempDir():
            rows = [("900020", "iPhone 13 128GB", 150, "Tvarkingas"),
                    ("900021", "iPhone 13 128GB", 150, "Tvarkingas")]
            tg, log = self.run_both(rows, sellers={"900020": "jonas", "900021": "jonas"})
            self.assertEqual(len([d for d, _ in tg.deals if d["source"] == "skelbiu"]), 1, log)
            self.assertIn("dublikatai", log)

    def test_each_source_gets_time_and_order_rotates(self):
        """Antrasis saltinis turi gauti savo laiko dali, o ne likti uz borto."""
        from tests.helpers import TempDir
        from tests.test_flow import FakeClient, FakeTelegram, read_json
        from vinted.sources.vinted_source import VintedSource
        from vinted.finder import Run
        from vinted import config

        with TempDir():
            config.cfg.update(MAX_RUN_MINUTES=30, ROTATE_QUERIES=True, SEARCH_QUERIES=["A", "B", "C"],
                              PARALLEL_SOURCES=False)
            order = []
            for _ in range(2):
                vinted = VintedSource(client=FakeClient({}))
                skelbiu = SkelbiuSource(client=FakeHttp({}))
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    Run([vinted, skelbiu], FakeTelegram(), sleep=lambda s: None).run()
                log = out.getvalue()
                order.append(log.index("[Vinted]") < log.index("[Skelbiu]"))
                self.assertIn("[Skelbiu]", log)
            self.assertNotEqual(order[0], order[1])          # eiliskumas kaitaliojasi

    def test_parallel_sources_both_deliver(self):
        """Lygiagreciai: abieju saltiniu dealai ateina, busena nesugadinama."""
        from tests.helpers import TempDir
        from tests.test_flow import FakeClient, FakeTelegram, market_items, read_json, item
        from vinted.sources.vinted_source import VintedSource
        from vinted.finder import Run
        from vinted import config

        with TempDir():
            config.cfg["PARALLEL_SOURCES"] = True
            vinted_catalog = market_items() + [item(1, "iPhone 13 128GB", 150, user_id=77)]
            vinted = VintedSource(client=FakeClient({"iPhone 13": vinted_catalog}))
            desc = "Tvarkingas telefonas, baterija 92%, visi dokumentai"
            skelbiu = SkelbiuSource(client=FakeHttp({
                "skelbimai/?": (200, skelbiu_page([("900100", "iPhone 13 128GB", 150, "Tvarkingas")])),
                "/skelbimai/x-900100.": (200, skelbiu_item(desc, "jonas")),
            }))
            tg = FakeTelegram()
            with contextlib.redirect_stdout(io.StringIO()) as out:
                Run([vinted, skelbiu], tg, sleep=lambda s: None).run()
            log = out.getvalue()
            ids = {d["id"] for d, _ in tg.deals}
            self.assertIn("vinted:1", ids, log)
            self.assertIn("skelbiu:900100", ids, log)
            self.assertIn("Tikrinami lygiagreciai", log)
            items = read_json("state.json")["market"]["items"]
            self.assertIn("skelbiu:900100", items)
            self.assertIn("vinted:1000", items)

    def test_one_source_failing_does_not_stop_the_other(self):
        from tests.helpers import TempDir
        from tests.test_flow import FakeClient, FakeTelegram, market_items, item
        from vinted.sources.vinted_source import VintedSource
        from vinted.finder import Run
        from vinted import config

        class BrokenSource(SkelbiuSource):
            def search(self, query, pages, seen=None):
                raise RuntimeError("svetainė nulūžo")

        with TempDir():
            config.cfg["PARALLEL_SOURCES"] = True
            catalog = market_items() + [item(1, "iPhone 13 128GB", 150, user_id=77)]
            vinted = VintedSource(client=FakeClient({"iPhone 13": catalog}))
            broken = BrokenSource(client=FakeHttp({}))
            tg = FakeTelegram()
            with contextlib.redirect_stdout(io.StringIO()) as out:
                Run([vinted, broken], tg, sleep=lambda s: None).run()
            log = out.getvalue()
            self.assertIn("vinted:1", {d["id"] for d, _ in tg.deals}, log)
            self.assertIn("Skelbiu nutruko", log)

    def test_shared_state_survives_concurrent_sources(self):
        """Du saltiniai vienu metu raso i ta pacia rinkos istorija ir saugo state.json.

        Be spynos cia luztu „dictionary changed size during iteration“ – testas
        patikrintas ja laikinai isjungus."""
        import json
        import threading
        from tests.helpers import TempDir, listing as make_listing
        from vinted.state import State
        from vinted import config

        with TempDir():
            config.cfg["PRICE_HISTORY_MAX_ITEMS"] = 100000
            state = State()
            errors = []

            def worker(source):
                try:
                    for i in range(40):
                        batch = [make_listing(f"{source}{i}_{k}", "iPhone 13 128GB", 200 + k, user_id=k)
                                 for k in range(20)]
                        for l in batch:
                            l.source = source
                        state.market.observe(batch)
                        state.market.quote("13", "128 GB")
                        state.market.summary()
                        if i % 5 == 0:
                            state.save()
                except Exception as e:
                    errors.append(f"{source}: {type(e).__name__}: {e}")

            threads = [threading.Thread(target=worker, args=(s,)) for s in ("vinted", "skelbiu")]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            state.save()
            with open("state.json", encoding="utf-8") as f:
                saved = json.load(f)                     # failas nesugadintas
            self.assertEqual(len(saved["market"]["items"]), 2 * 40 * 20)

    def test_state_keeps_source_prefix_and_url(self):
        from tests.helpers import TempDir
        from tests.test_flow import read_json
        with TempDir():
            self.run_both([("900003", "iPhone 13 128GB", 150, "Tvarkingas")])
            items = read_json("state.json")["market"]["items"]
            self.assertIn("skelbiu:900003", items)
            self.assertTrue(items["skelbiu:900003"]["u"].startswith("https://www.skelbiu.lt/"))
            self.assertNotIn("u", items["vinted:1000"])      # Vinted adresas atkuriamas is ID


if __name__ == "__main__":
    unittest.main()
