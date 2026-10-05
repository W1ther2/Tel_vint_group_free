"""VintedClient ir Telegram su netikru HTTP."""
import contextlib
import io
import json
import unittest

import requests

from tests.helpers import reset_config, item
from vinted.client import VintedClient, looks_newest_first
from vinted.telegram import Telegram


class Resp:
    def __init__(self, code, data=None, text="", headers=None, url=""):
        self.status_code, self._data, self.text, self.headers, self.url = code, data, text, headers or {}, url

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, handler):
        self.handler, self.calls, self.cookies = handler, [], {}

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers))
        return self.handler(url, params or {}, headers or {})


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


class ClientTest(unittest.TestCase):
    def setUp(self):
        reset_config(SLEEP_SECONDS=0)

    def test_endpoint_fallback_and_headers(self):
        def handler(url, params, headers):
            if url.endswith(".lt/"):
                return Resp(200, text='"CSRF_TOKEN":"12345678-1234-1234-1234-123456789abc"', headers={"x-anon-id": "a1"})
            if "svc-catalogue" in url:
                return Resp(404, text="<title>La page</title>")
            return Resp(200, {"items": [item(3, "iPhone 13", 100)]} if params["page"] == 1 else {"items": []})
        sess = FakeSession(handler)
        c = VintedClient(session_factory=lambda: sess, sleep=lambda s: None)
        quiet(c.start)
        items = quiet(c.fetch_items, "iPhone 13", 3)
        self.assertEqual([i["id"] for i in items], [3])
        self.assertTrue(c.working_endpoint.endswith("/api/v2/catalog/items"))
        _, params, headers = sess.calls[1]
        self.assertEqual((headers["Locale"], headers["X-Csrf-Token"], headers["X-Anon-Id"], params["currency"]),
                         ("lt-LT", "12345678-1234-1234-1234-123456789abc", "a1", "EUR"))

    def test_early_stop_when_all_seen(self):
        page1 = [item(i, "iPhone 13", 100) for i in range(20, 10, -1)]
        sess = FakeSession(lambda url, p, h: Resp(200, {"items": page1 if p.get("page") == 1 else [item(5, "x", 1)]}))
        c = VintedClient(session_factory=lambda: sess, sleep=lambda s: None)
        c.session = sess
        items = quiet(c.fetch_items, "iPhone 13", 3, seen={str(i): 1 for i in range(11, 21)})
        self.assertEqual(len(items), 10)
        self.assertTrue(looks_newest_first(page1))
        self.assertFalse(looks_newest_first(list(reversed(page1))))

    def test_category_filter_and_fallback(self):
        reset_config(SLEEP_SECONDS=0, CATALOG_IDS=[2342], BRAND_IDS=[12])
        seen_params = []

        def handler(url, params, headers):
            seen_params.append(params)
            if url.endswith(".lt/"):
                return Resp(200, text="ok")
            # su filtru – tuscia, be filtro – yra skelbimu
            if "attribute_ids[catalog]" in params:
                return Resp(200, {"items": []})
            return Resp(200, {"items": [item(1, "iPhone 13", 200)]} if params["page"] == 1 else {"items": []})

        sess = FakeSession(handler)
        c = VintedClient(session_factory=lambda: sess, sleep=lambda s: None)
        c.session = sess
        items = quiet(c.fetch_items, "iPhone 13", 2)
        self.assertEqual(seen_params[0]["attribute_ids[catalog]"], "2342")
        self.assertEqual(seen_params[0]["brand_ids"], "12")
        self.assertTrue(c.filters_off)
        self.assertEqual([i["id"] for i in items], [1])

    def test_403_backoff_and_blocked_counter(self):
        reset_config(SLEEP_SECONDS=0, BLOCK_BACKOFF_SECONDS=[0, 0, 0])
        waits = []
        sess = FakeSession(lambda url, p, h: Resp(200, text="ok") if url.endswith(".lt/")
                           else Resp(403, text="<title>Blocked</title>"))
        c = VintedClient(session_factory=lambda: sess, sleep=waits.append)
        c.session = sess
        quiet(c.fetch_items, "iPhone 13", 2)
        self.assertEqual(c.blocked_queries, 1)
        quiet(c.fetch_items, "iPhone 14", 2)
        self.assertEqual(c.blocked_queries, 2)

    def test_all_fail_sets_error(self):
        sess = FakeSession(lambda url, p, h: Resp(404, text="<title>Nerasta</title>"))
        c = VintedClient(session_factory=lambda: sess, sleep=lambda s: None)
        c.session = sess
        self.assertEqual(quiet(c.fetch_items, "iPhone 13", 2), [])
        self.assertIn("svc-catalogue", c.last_error)


class FakeHttp:
    def __init__(self, post_resp=None, get_resp=None, raise_timeout=False):
        self.posts, self.post_resp, self.get_resp, self.raise_timeout = [], post_resp, get_resp, raise_timeout

    def post(self, url, data=None, timeout=None):
        self.posts.append((url.rsplit("/", 1)[1], data))
        if self.raise_timeout:
            raise requests.Timeout()
        return self.post_resp or Resp(200)

    def get(self, url, params=None, timeout=None):
        return self.get_resp


class TelegramTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def deal(self):
        from vinted.market import Quote
        return {"model": "13", "storage": None, "price": 150.0, "discount": 0.2, "value": 190.0, "profit": 20,
                "quote": Quote(190, 10, "skelbimai", False), "url": "https://www.vinted.lt/items/1",
                "photo": "https://img/1.jpg", "seller": {}}

    def test_silent_photo(self):
        http = FakeHttp()
        Telegram("t", "42", http).send_deal(self.deal(), silent=True)
        method, data = http.posts[0]
        self.assertEqual((method, data["disable_notification"], data["chat_id"]), ("sendPhoto", True, "42"))

    def test_timeout_no_duplicate(self):
        http = FakeHttp(raise_timeout=True)
        quiet(Telegram("t", "42", http).send_deal, self.deal())
        self.assertEqual(len(http.posts), 1)

    def test_photo_error_falls_back_to_text(self):
        http = FakeHttp(post_resp=Resp(400, text="bad photo"))
        quiet(Telegram("t", "42", http).send_deal, self.deal())
        self.assertEqual([m for m, _ in http.posts], ["sendPhoto", "sendMessage"])

    def test_updates_messages_callbacks_and_private(self):
        data = {"ok": True, "result": [
            {"update_id": 10, "message": {"chat": {"id": 42}, "from": {"id": 7, "first_name": "Vy"},
                                          "text": "/kaina 13 180"}},
            {"update_id": 11, "message": {"chat": {"id": 99, "type": "supergroup"}, "text": "/pauze"}},
            {"update_id": 12, "message": {"chat": {"id": 42}, "text": "labas"}},
            {"update_id": 13, "message": {"chat": {"id": 555, "type": "private"},
                                          "from": {"id": 7, "first_name": "Vy"}, "text": "/start"}},
            {"update_id": 14, "callback_query": {"id": "abc", "data": "w|13 Pro",
                                                 "from": {"id": 7, "first_name": "Vy"}}},
        ]}
        msgs, cbs, offset = Telegram("t", "42", FakeHttp(get_resp=Resp(200, data))).get_updates(0)
        self.assertEqual([m["text"] for m in msgs], ["/kaina 13 180", "/start"])
        self.assertTrue(msgs[1]["private"])
        self.assertEqual((cbs[0]["data"], cbs[0]["user"], cbs[0]["id"]), ("w|13 Pro", "7", "abc"))
        self.assertEqual(offset, 14)

    def test_deal_keyboard_has_personal_buttons(self):
        from vinted.telegram import Telegram as T
        kb = json.loads(T.deal_keyboard({"model": "13 Pro", "url": "https://x", "seller_id": "99"}))
        rows = kb["inline_keyboard"]
        self.assertEqual(rows[0][0]["url"], "https://x")
        self.assertEqual([b["callback_data"] for b in rows[1]], ["w|13 Pro"])



class TelegramRobustnessTest(unittest.TestCase):
    """2026-09: ilga kortele nebepjauna HTML, 429 kartojamas, sugadintas HTML – paprastu tekstu."""

    def setUp(self):
        reset_config(SOURCES=["vinted", "pirkpard"], SHOW_RANK=True)

    def long_deal(self, desc_words=60):
        from vinted.market import Quote, Rank
        from vinted.risk import assess_risk
        desc = " ".join(["Parduodu skubiai, rašykite WhatsApp +37061234567, galima pavedimu."] * 4)
        desc = " ".join(desc.split()[:desc_words])
        seller = {"country": "LT", "city": "Klaipėda", "rating": 3.5, "reviews": 4, "sold": 0, "negative": 2,
                  "account_age_days": 5, "active_items": 60}
        level, reasons = assess_risk("iPhone 13 Pro Max", desc, 330, 700, seller, 1)
        return {"model": "13 Pro Max", "storage": "256 GB", "price": 330.0, "drop_from": 380.0, "discount": 0.48,
                "value": 640.0, "profit": 300, "description": desc, "risk_level": level, "risk_reasons": reasons,
                "quote": Quote(700, 25, "skelbimai", True), "defects": ["įbrėžimai"], "condition": "Labai gera",
                "battery": 84, "seller": seller, "age": "prieš 3 min.", "source_label": "Vinted",
                "url": "https://www.vinted.lt/items/7123456789-apple-iphone-13-pro-max-256gb-sierra-blue-idealios",
                "rank": Rank(1, 20, 330, 900, 0.0, True, 520), "suspicious": {"ratio": 0.63, "peer_low": 520}}

    def test_long_card_fits_without_breaking_html(self):
        import re
        from vinted.telegram import format_card
        for n in range(1, 50, 3):
            card = format_card(self.long_deal(n))
            self.assertLessEqual(len(card), 1024)
            for tag in ("b", "a", "i", "code"):
                self.assertEqual(len(re.findall(f"<{tag}[ >]", card)), card.count(f"</{tag}>"), (n, card[-80:]))
            self.assertNotRegex(card, r"<[^>]*$")

    def test_parse_error_resent_as_plain_text(self):
        calls = []

        class Http:
            def post(self, url, data=None, timeout=None):
                calls.append(dict(data))
                if "parse_mode" in data:
                    return Resp(400, text='{"description":"Bad Request: can\'t parse entities"}')
                return Resp(200)

        ok = quiet(Telegram("t", "42", Http(), sleep=lambda s: None).send_message, "<b>Sveiki</b> &amp; <a href=")
        self.assertTrue(ok)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("parse_mode", calls[1])
        self.assertNotIn("<b>", calls[1]["text"])

    def test_429_waits_and_retries(self):
        slept, answers = [], [Resp(429, {"ok": False, "parameters": {"retry_after": 7}}), Resp(200)]

        class Http:
            def post(self, url, data=None, timeout=None):
                return answers.pop(0)

        ok = quiet(Telegram("t", "42", Http(), sleep=slept.append).send_message, "labas")
        self.assertTrue(ok)
        self.assertEqual(slept, [7.5])

    def test_group_messages_spaced_out(self):
        slept, clock = [], [100.0]
        http = FakeHttp()
        tg = Telegram("t", "-100123", http, sleep=slept.append, clock=lambda: clock[0])
        tg.send_message("vienas")
        clock[0] += 1.0
        tg.send_message("du")
        self.assertEqual(len(slept), 1)
        self.assertAlmostEqual(slept[0], 2.1)
        private = Telegram("t", "42", FakeHttp(), sleep=slept.append, clock=lambda: clock[0])
        private.send_message("a")
        private.send_message("b")
        self.assertEqual(len(slept), 1)                    # asmeniniams – be pauziu

if __name__ == "__main__":
    unittest.main()
