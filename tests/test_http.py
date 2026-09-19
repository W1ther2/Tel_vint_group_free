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

    def test_updates_only_from_chat(self):
        data = {"ok": True, "result": [
            {"update_id": 10, "message": {"chat": {"id": 42}, "text": "/kaina 13 180"}},
            {"update_id": 11, "message": {"chat": {"id": 99}, "text": "/pauze"}},
            {"update_id": 12, "message": {"chat": {"id": 42}, "text": "labas"}},
        ]}
        ups, offset = Telegram("t", "42", FakeHttp(get_resp=Resp(200, data))).get_updates(0)
        self.assertEqual((ups, offset), ([(10, "/kaina 13 180")], 12))


if __name__ == "__main__":
    unittest.main()
