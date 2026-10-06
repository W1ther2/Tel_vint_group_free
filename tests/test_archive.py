# -*- coding: utf-8 -*-
"""Ilgalaikis rinkos archyvas (vinted/archive.py).

state.json isvalo aktyvius po 30 d., parduotus po 60 d. – patikrinti, ar rinkos kaina ir jos
patikimumas pasiteisina, po dvieju menesiu nebebutu su kuo. Archyve ivykiai niekada
neperrasomi."""
import gzip
import os
import time
import unittest

from tests.helpers import TempDir, item, listing, reset_config
from tests.test_flow import FakeClient, FakeTelegram, market_items, run
from vinted import archive, config


class ArchiveFileTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_appends_without_rewriting(self):
        """Kiekvienas paleidimas – naujas gzip narys gale; skaitomi visi."""
        with TempDir():
            now = time.mktime((2026, 10, 1, 12, 0, 0, 0, 0, 0))
            archive.append([{"e": "obs", "id": "vinted:1"}], now=now)
            size = os.path.getsize("archive/market-2026-10-01.jsonl.gz")
            archive.append([{"e": "price", "id": "vinted:1"}, {"e": "status", "id": "vinted:1"}], now=now)
            self.assertGreater(os.path.getsize("archive/market-2026-10-01.jsonl.gz"), size)
            self.assertEqual([e["e"] for e in archive.read()], ["obs", "price", "status"])

    def test_one_file_per_day_read_in_order(self):
        with TempDir():
            archive.append([{"e": "b"}], now=time.mktime((2026, 11, 2, 12, 0, 0, 0, 0, 0)))
            archive.append([{"e": "a"}], now=time.mktime((2026, 10, 2, 12, 0, 0, 0, 0, 0)))
            self.assertEqual(sorted(os.listdir("archive")),
                             ["market-2026-10-02.jsonl.gz", "market-2026-11-02.jsonl.gz"])
            self.assertEqual([e["e"] for e in archive.read()], ["a", "b"])

    def test_broken_tail_does_not_lose_the_rest(self):
        """Nutrauktas irasymas (nukirsta failo pabaiga) – perskaitom, kiek galima."""
        with TempDir():
            now = time.mktime((2026, 10, 1, 12, 0, 0, 0, 0, 0))
            archive.append([{"e": "obs", "id": str(i)} for i in range(50)], now=now)
            path = "archive/market-2026-10-01.jsonl.gz"
            with open(path, "ab") as f:
                f.write(gzip.compress(b'{"e":"price"}\n' * 200)[:30])   # nebaigtas narys
            events = list(archive.read())
            self.assertGreaterEqual(len(events), 50)
            self.assertEqual([e["id"] for e in events[:50]], [str(i) for i in range(50)])

    def test_can_be_turned_off(self):
        config.cfg["ARCHIVE_ENABLED"] = False
        with TempDir():
            self.assertEqual(archive.append([{"e": "obs"}]), 0)
            self.assertFalse(os.path.exists("archive"))


class RunWritesArchiveTest(unittest.TestCase):
    def setUp(self):
        reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8,
                     MARKET_PERCENTILE=0.5, MIN_DISCOUNT=0.15, MIN_PROFIT_EUR=0)

    def test_observations_with_quote_and_confidence_at_the_time(self):
        with TempDir():
            cat = market_items() + [item(1, "iPhone 13 128GB", 190, user_id=1)]
            client = FakeClient({"iPhone 13": cat})
            run(client, FakeTelegram())
            events = list(archive.read())
            obs = [e for e in events if e["e"] == "obs"]
            self.assertEqual(len(obs), 21)
            # Pirmame paleidime visi pamatomi vienu metu – rinka dar nezinoma (apytiksle).
            self.assertEqual({e.get("qs") for e in obs}, {"t"})
            self.assertIn("alert", [e["e"] for e in events])
            # antras paleidimas: atpigo -> price ivykis; naujas skelbimas jau su rinka
            cat[-1] = item(1, "iPhone 13 128GB", 170, user_id=1)
            cat.append(item(2, "iPhone 13 128GB", 280, user_id=2))
            run(client, FakeTelegram())
            events = list(archive.read())
            self.assertEqual(sum(1 for e in events if e["e"] == "obs"), 22)
            new = [e for e in events if e["e"] == "obs" and e["id"] == "vinted:2"][0]
            for key in ("p", "q", "qs", "qn", "qsp", "qc", "qb", "m", "s"):
                self.assertIn(key, new, new)
            self.assertEqual((new["qs"], new["qn"]), ("s", 21))
            price = [e for e in events if e["e"] == "price"]
            self.assertEqual((price[0]["p"], price[0]["pp"]), (170, 190))

    def test_alert_event_records_the_model(self):
        """Be `m` pranesimai butu vienintelis ivykio tipas, kurio pagal modeli
        nesugrupuosi – o archyvas rasomas butent tam, kad veliau butu galima
        atsakyti „kurie modeliai realiai pasiteisina". Gyvai (2026-10-06) visi
        8 archyve buve pranesimai buvo be modelio."""
        with TempDir():
            cat = market_items() + [item(1, "iPhone 13 128GB", 190, user_id=1)]
            run(FakeClient({"iPhone 13": cat}), FakeTelegram())
            alerts = [e for e in archive.read() if e["e"] == "alert"]
            self.assertTrue(alerts)
            for a in alerts:
                self.assertEqual(a.get("m"), "13", a)
                self.assertIn("p", a)

    def test_below_floor_records_the_floor_that_was_applied(self):
        """Riba yra max(40, min_price) – ja ir reikia uzrasyti.

        Pirma versija rase `min_price(model)`, tad archyve atsidurdavo nesamone:
        gyvai „iPhone 8 uz 37 EUR, riba 30 – below_floor" (3 atvejai). Skelbimas
        atmestas teisingai (37 < 40), bet irasas tvirtino, kad kaina VIRS ribos.
        Archyvas skirtas ribas tikrinti matuojant, tad klaidinga `fl` gadina
        butent ta, del ko jis rasomas."""
        from vinted.market import Market
        from vinted.phone import min_price
        with TempDir():
            reset_config(BRANDS=["apple"])
            self.assertLess(min_price("8"), 40, "testas turi prasme tik jei modelio riba < 40")
            m = Market()
            m.observe([listing(1, "iPhone 8 64GB", 37.0, user_id=1)], day=100)
            skips = [e for e in m.drain_events() if e.get("why") == "below_floor"]
            self.assertEqual(len(skips), 1)
            self.assertEqual(skips[0]["fl"], 40.0)
            self.assertLess(skips[0]["p"], skips[0]["fl"],
                            "uzrasyta kaina turi buti ZEMIAU uzrasytos ribos")

    def test_status_keeps_what_the_page_said(self):
        """„Dingo“ ir „tikrai parduota“ archyve atskirti – GONE_AS_SOLD to nesulieja."""
        from vinted.market import Market
        with TempDir():
            m = Market()
            m.items = {"vinted:1": {"m": "13", "s": "", "p": 200, "f": 1, "l": 1, "c": 1, "st": "active"},
                       "vinted:2": {"m": "13", "s": "", "p": 210, "f": 1, "l": 1, "c": 1, "st": "active"}}
            m.set_status("vinted:1", "sold", day=5)
            m.set_status("vinted:2", "gone", day=5)
            st = {e["id"]: e["st"] for e in m.drain_events()}
            self.assertEqual(st, {"vinted:1": "sold", "vinted:2": "gone"})
            self.assertEqual(m.items["vinted:2"]["st"], "sold", "busenoje – kaip ir anksciau")
            self.assertEqual(m.drain_events(), [])


if __name__ == "__main__":
    unittest.main()
