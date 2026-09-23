"""Saltiniu sandura: uid raktai, senu failu perkelimas, saltiniu registras."""
import contextlib
import io
import json
import unittest

from tests.helpers import reset_config, TempDir, listing
from vinted.listing import split_uid
from vinted.market import Market
from vinted.sources import build_sources
from vinted.sources.vinted_source import VintedSource
from vinted.state import load_seen, seen_key
from vinted.util import human_age


class UidTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_listing_uid(self):
        l = listing(123, "iPhone 13 128GB", 200)
        self.assertEqual((l.source, l.id, l.uid), ("vinted", "123", "vinted:123"))
        self.assertEqual(l.url, "https://www.vinted.lt/items/123")
        self.assertEqual(l.seller_id, "1")
        self.assertEqual(l.condition, "Labai gera")

    def test_split_uid(self):
        self.assertEqual(split_uid("vinted:123"), ("vinted", "123"))
        self.assertEqual(split_uid("skelbiu:abc"), ("skelbiu", "abc"))
        self.assertEqual(split_uid("123"), ("vinted", "123"))      # senas formatas

    def test_local_ids_filtered_per_source(self):
        source = VintedSource(client=object())
        self.assertEqual(source.local_ids({"vinted:1", "skelbiu:2", "fp:abc"}), {"1"})


class MigrationTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_old_seen_file_gets_source_prefix(self):
        with TempDir():
            with open("seen.json", "w", encoding="utf-8") as f:
                json.dump({"123": 1.0, "fp:abc": 2.0, "vinted:9": 3.0}, f)
            seen = load_seen("seen.json")
            self.assertEqual(set(seen), {"vinted:123", "fp:abc", "vinted:9"})

    def test_seen_key(self):
        self.assertEqual(seen_key(55), "vinted:55")
        self.assertEqual(seen_key("fp:x"), "fp:x")
        self.assertEqual(seen_key("__heartbeat__"), "__heartbeat__")

    def test_old_market_keys_migrated(self):
        m = Market({"items": {"7": {"m": "13", "s": "", "p": 200, "l": 1, "st": "active"}}})
        self.assertEqual(list(m.items), ["vinted:7"])
        self.assertEqual(m.get(7)["p"], 200)          # senas ID vis tiek randamas


class RegistryTest(unittest.TestCase):
    def test_build_sources_defaults_to_vinted(self):
        reset_config(SOURCES=["vinted"])
        sources = build_sources(sleep=lambda s: None)
        self.assertEqual([s.name for s in sources], ["vinted"])

    def test_unknown_source_ignored(self):
        reset_config(SOURCES=["nera_tokio"])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            sources = build_sources(sleep=lambda s: None)
        self.assertEqual([s.name for s in sources], ["vinted"])
        self.assertIn("Nezinomi saltiniai", out.getvalue())


class AgeTest(unittest.TestCase):
    def test_human_age(self):
        now = 1_000_000
        self.assertEqual(human_age(now - 30, now), "ką tik")
        self.assertEqual(human_age(now - 4 * 60, now), "prieš 4 min.")
        self.assertEqual(human_age(now - 3 * 3600, now), "prieš 3 val.")
        self.assertEqual(human_age(now - 2 * 86400, now), "prieš 2 d.")
        self.assertEqual(human_age(now - 70 * 86400, now), "prieš 2 mėn.")
        self.assertIsNone(human_age(None, now))

    def test_created_at_from_photo_timestamp(self):
        from vinted.parsing import get_created_at
        self.assertEqual(get_created_at({"photo": {"high_resolution": {"timestamp": 1700000000}}}),
                         1700000000)
        self.assertIsNone(get_created_at({"photo": {"high_resolution": {"timestamp": 0}}}))
        self.assertEqual(get_created_at({"created_at_ts": "2024-01-02T03:04:05Z"}),
                         1704164645.0)

    def test_card_shows_age_and_source(self):
        reset_config(SOURCES=["vinted", "skelbiu"])
        from vinted.telegram import format_card
        from vinted.market import Quote
        card = format_card({
            "model": "13", "storage": "128 GB", "price": 150.0, "discount": 0.25, "value": 200.0,
            "description": "Parduodu", "quote": Quote(205, 12, "parduoti", True), "defects": [],
            "condition": "Labai gera", "battery": 75, "battery_low": True, "seller": {},
            "source": "vinted", "source_label": "Vinted", "age": "prieš 4 min.",
            "url": "https://www.vinted.lt/items/1"})
        self.assertIn("⏱ <b>Įkelta:</b> prieš 4 min. · Vinted", card)
        self.assertIn("🔋 <b>Baterija:</b> 75% ⚠️ žema", card)
        self.assertIn("Atidaryti Vinted</a>", card)


if __name__ == "__main__":
    unittest.main()
