# -*- coding: utf-8 -*-
"""Grupes skiltis (forum topic) ir antras paleidimas tame paciame pokalbyje.

Kai iPhone botas raso vienoje skiltyje, o Android – kitoje, butina, kad:
  - kortele patektu i savo skilti (message_thread_id),
  - asmenines zinutes butu BE skilties (privaciame pokalbyje ju nera),
  - svetimoje skiltyje parasyta komanda nebutu vykdoma.
"""
import json
import unittest

from tests.helpers import reset_config
from vinted.telegram import Telegram


class FakeHTTP:
    def __init__(self, updates=None):
        self.posts = []
        self.updates = updates or []

    def post(self, url, data=None, timeout=None):
        self.posts.append((url.rsplit("/", 1)[-1], data))
        return Reply(200, '{"ok":true}')

    def get(self, url, params=None, timeout=None):
        return Reply(200, json.dumps({"ok": True, "result": self.updates}))


class Reply:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


def deal():
    from vinted.market import Quote
    return {"model": "Galaxy S24 Ultra", "storage": "256 GB", "price": 500.0, "discount": 0.2,
            "value": 620.0, "profit": 60.0, "description": "Tvarkingas", "risk_level": None,
            "risk_reasons": [], "quote": Quote(620, 12, "skelbimai", True), "defects": [],
            "condition": "Labai gera", "battery": None, "seller": {}, "source_label": "Vinted",
            "url": "https://www.vinted.lt/items/1"}


class TopicTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_message_goes_to_topic(self):
        http = FakeHTTP()
        tg = Telegram(token="1:x", chat_id="-100123", http=http, sleep=lambda s: None, topic_id="77")
        tg.send_message("labas")
        method, data = http.posts[0]
        self.assertEqual(method, "sendMessage")
        self.assertEqual(data["message_thread_id"], "77")
        self.assertEqual(data["chat_id"], "-100123")

    def test_card_goes_to_topic(self):
        http = FakeHTTP()
        tg = Telegram(token="1:x", chat_id="-100123", http=http, sleep=lambda s: None, topic_id="77")
        tg.send_deal({**deal(), "photo": "http://x/y.jpg"})
        method, data = http.posts[0]
        self.assertEqual(method, "sendPhoto")
        self.assertEqual(data["message_thread_id"], "77")

    def test_private_message_has_no_topic(self):
        """Asmeniniame pokalbyje skilciu nera – su thread ID Telegram grazintu klaida."""
        http = FakeHTTP()
        tg = Telegram(token="1:x", chat_id="-100123", http=http, sleep=lambda s: None, topic_id="77")
        tg.send_message("labas", chat_id="555")
        _, data = http.posts[0]
        self.assertNotIn("message_thread_id", data)

    def test_no_topic_configured(self):
        http = FakeHTTP()
        tg = Telegram(token="1:x", chat_id="-100123", http=http, sleep=lambda s: None, topic_id="")
        tg.send_message("labas")
        _, data = http.posts[0]
        self.assertNotIn("message_thread_id", data)


class AdminChatTest(unittest.TestCase):
    """Viesoje grupeje „SKRIPTAS UZLUZO“ ar „saltinis nepasiekiamas“ tik gasdina skaitytojus."""

    def setUp(self):
        reset_config()

    def test_diagnostics_go_to_admin_chat(self):
        from unittest import mock
        from vinted import config as cfg
        http = FakeHTTP()
        tg = Telegram(token="1:x", chat_id="-100123", http=http, sleep=lambda s: None, topic_id="77")
        with mock.patch.object(cfg, "ADMIN_CHAT_ID", "555"):
            tg.send_admin("botas nuluzo")
        _, data = http.posts[0]
        self.assertEqual(data["chat_id"], "555")
        self.assertNotIn("message_thread_id", data)       # privaciame pokalbyje skilciu nera

    def test_without_admin_chat_goes_to_group(self):
        from unittest import mock
        from vinted import config as cfg
        http = FakeHTTP()
        tg = Telegram(token="1:x", chat_id="-100123", http=http, sleep=lambda s: None, topic_id="77")
        with mock.patch.object(cfg, "ADMIN_CHAT_ID", ""):
            tg.send_admin("botas nuluzo")
        _, data = http.posts[0]
        self.assertEqual(data["chat_id"], "-100123")
        self.assertEqual(data["message_thread_id"], "77")

    def test_crash_notice_uses_admin_chat(self):
        import contextlib
        import io
        from unittest import mock
        from vinted import config as cfg
        from vinted.finder import notify_crash
        http = FakeHTTP()
        tg = Telegram(token="1:x", chat_id="-100123", http=http, sleep=lambda s: None)
        with mock.patch.object(cfg, "ADMIN_CHAT_ID", "555"), contextlib.redirect_stdout(io.StringIO()):
            notify_crash(tg, None, ValueError("bum"))
        _, data = http.posts[0]
        self.assertEqual(data["chat_id"], "555")
        self.assertIn("SKRIPTAS UZLUZO", data["text"])


class CommandScopeTest(unittest.TestCase):
    """Tame paciame pokalbyje gali suktis du botai – kiekvienas savo skiltyje."""

    def setUp(self):
        reset_config()

    def updates(self):
        def msg(uid, text, thread=None):
            m = {"text": text, "chat": {"id": -100123, "type": "supergroup"}, "from": {"id": 1, "first_name": "A"}}
            if thread is not None:
                m["message_thread_id"] = thread
            return {"update_id": uid, "message": m}
        return [msg(1, "/pauze", 77), msg(2, "/testi", 88), msg(3, "/statistika")]

    def test_only_own_topic_commands(self):
        http = FakeHTTP(self.updates())
        tg = Telegram(token="1:x", chat_id="-100123", http=http, sleep=lambda s: None, topic_id="77")
        messages, _, offset = tg.get_updates(0)
        self.assertEqual([m["text"] for m in messages], ["/pauze"])
        self.assertEqual(offset, 3)              # offset vis tiek pajuda – kitaip kartotusi

    def test_without_topic_all_group_commands(self):
        http = FakeHTTP(self.updates())
        tg = Telegram(token="1:x", chat_id="-100123", http=http, sleep=lambda s: None, topic_id="")
        messages, _, _ = tg.get_updates(0)
        self.assertEqual([m["text"] for m in messages], ["/pauze", "/testi", "/statistika"])


# AndroidConfigTest isimtas v50. Jis tikrino `config.android.json` – antrojo,
# atskirai paleidziamo boto konfiga. To budo nebera: Android skelbimai dabar eina
# per TA PATI paleidima i kita grupes skilti (TOPIC_BY_BRAND, zr. test_topics.py).
# Failo nebuvo, tad klase buvo visiskai praleidziama – ir tylėdama tvirtino
# `BRANDS == ["apple"]`, nors ju dabar penki. Praleidziamas testas, saugantis
# neteisinga tiesa, yra blogiau nei jokio testo.
#
# Vienas jo testas buvo vertingas ir tikrino gyva koda, tad liko – tik nebe uz
# praleidimo salygos (del jos jis irgi niekada nebuvo paleistas).
class BrandQueriesTest(unittest.TestCase):
    def test_brands_shape_queries(self):
        from vinted import config
        reset_config(BRANDS=["samsung", "xiaomi", "google", "oneplus"])
        self.assertEqual(config.brand_queries([]),
                         ["samsung galaxy", "xiaomi", "google pixel", "oneplus"])


if __name__ == "__main__":
    unittest.main()
