"""Pranesimu rezultatai: ar praneseti skelbimai nupirkti ir per kiek laiko."""
import contextlib
import io
import json
import time
import unittest

from tests.helpers import reset_config, TempDir, item
from tests.test_flow import FakeClient, FakeTelegram, market_items, read_json, write_json
from vinted import config
from vinted.finder import Run
from vinted.parsing import listing_status
from vinted.sources.pirkpard import listing_state
from vinted.tracker import Tracker, report_text

H = 3600
T0 = 1_750_000_000


def deal(uid, price=200, model="13", profit=40, source="vinted"):
    return {"id": uid, "model": model, "storage": "128 GB", "price": price, "value": price + 60,
            "profit": profit, "source": source, "url": f"https://x/{uid}"}


class TrackerTest(unittest.TestCase):
    def setUp(self):
        reset_config(TRACK_DAYS=7, TRACK_FAST_HOURS=48, TRACK_SLOW_EVERY_HOURS=6,
                     TRACK_CHECKS_PER_RUN=25, TRACK_KEEP_DAYS=90, TRACK_MIN_MINUTES=5)

    def test_bought_within_hour(self):
        t = Tracker()
        t.add(deal("vinted:1"), now=T0)
        t.update("vinted:1", "active", now=T0 + 600)
        t.update("vinted:1", "sold", now=T0 + 40 * 60)
        s = t.stats(now=T0 + 2 * H)
        self.assertEqual((s["sent"], s["taken"], s["within_1h"]), (1, 1, 1))
        self.assertAlmostEqual(s["median_hours"], 40 / 60)

    def test_reserved_counts_as_bought_gone_does_not(self):
        t = Tracker()
        for i, st in enumerate(["reserved", "gone", "sold"]):
            t.add(deal(f"vinted:{i}"), now=T0)
            t.update(f"vinted:{i}", st, now=T0 + 3 * H)
        s = t.stats(now=T0 + 4 * H)
        self.assertEqual((s["taken"], s["reserved"], s["gone"]), (2, 1, 1))

    def test_result_is_final(self):
        """Parduotas ir veliau vel „aktyvus“ (pvz. atsauktas pirkimas) – pirmas rezultatas lieka."""
        t = Tracker()
        t.add(deal("vinted:1"), now=T0)
        t.update("vinted:1", "sold", now=T0 + H)
        t.update("vinted:1", "active", now=T0 + 2 * H)
        self.assertEqual(t.items["vinted:1"]["r"], "sold")

    def test_repeat_alert_keeps_original_time(self):
        """Atpigus pranesam dar karta – bet laikas skaiciuojamas nuo pirmo pranesimo."""
        t = Tracker()
        t.add(deal("vinted:1", price=200), now=T0)
        t.add(deal("vinted:1", price=180), now=T0 + 5 * H)
        self.assertEqual(t.items["vinted:1"]["t"], T0)

    def test_check_schedule(self):
        t = Tracker()
        t.add(deal("vinted:new"), now=T0)
        t.add(deal("vinted:old"), now=T0 - 3 * 86400)
        t.items["vinted:old"]["lc"] = T0 - H             # tikrintas pries valanda
        self.assertEqual(t.due(now=T0 + 60), [])                # ka tik issiustas – dar ne
        self.assertEqual(t.due(now=T0 + 400), ["vinted:new"])   # senas – tik kas 6 val.
        t.items["vinted:old"]["lc"] = T0 - 7 * H
        self.assertIn("vinted:old", t.due(now=T0 + 400))

    def test_unsold_after_a_week_and_pruned_later(self):
        t = Tracker()
        t.add(deal("vinted:1"), now=T0)
        t.expire(now=T0 + 8 * 86400)
        self.assertEqual(t.items["vinted:1"]["r"], "unsold")
        self.assertEqual(t.due(now=T0 + 8 * 86400), [])
        t.expire(now=T0 + 91 * 86400)
        self.assertEqual(t.items, {})

    def test_report_text(self):
        t = Tracker()
        for i, (st, mins) in enumerate([("sold", 25), ("reserved", 180), ("sold", 1500), (None, 0)]):
            t.add(deal(f"vinted:{i}", profit=30 + i), now=T0)
            if st:
                t.update(f"vinted:{i}", st, now=T0 + mins * 60)
        t.add(deal("pirkpard:9", source="pirkpard"), now=T0)
        text = report_text(t, days=7, now=T0 + 26 * H, labels={"vinted": "Vinted", "pirkpard": "Pirkpard"})
        self.assertIn("Pranešta: <b>5</b>", text)
        self.assertIn("Nupirkta: <b>3</b> (60%)", text)
        self.assertIn("per 1 val.: 1 · per 6 val.: 2 · per parą: 2", text)
        self.assertIn("25 min.", text)
        self.assertIn("Vinted 3/4", text)
        self.assertIn("Pirkpard 0/1", text)
        self.assertIn("~93 €", text)                # 30 + 31 + 32

    def test_empty_report(self):
        self.assertIn("pranešimų nebuvo", report_text(Tracker(), now=T0))


class StatusParsingTest(unittest.TestCase):
    def test_vinted_reserved(self):
        page = '{"item":{"id":5,"is_closed":false,"is_reserved":true}}'
        self.assertEqual(listing_status(200, page, "https://www.vinted.lt/items/5", 5), "reserved")
        page = '{"item":{"id":5,"is_closed":false,"is_reserved":false}}'
        self.assertEqual(listing_status(200, page, "https://www.vinted.lt/items/5", 5), "active")

    def test_pirkpard_reserved(self):
        self.assertEqual(listing_state({"is_reserved": True}), "reserved")
        self.assertEqual(listing_state({"sold_out": True, "is_reserved": True}), "sold")


class FlowTest(unittest.TestCase):
    """Visas kelias: pranesimas -> kitame paleidime parduotas -> ataskaita."""

    def setUp(self):
        reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8,
                     MARKET_PERCENTILE=0.5, MIN_DISCOUNT=0.15, LOUD_DISCOUNT=0.30,
                     TRACK_RESULTS=True, REPORT_EVERY_DAYS=7, DETAIL_SLEEP_SECONDS=0,
                     TRACK_MIN_MINUTES=0)

    def run_once(self, client, tg=None):
        tg = tg or FakeTelegram()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            Run(client, tg, sleep=lambda s: None).run()
        return tg, out.getvalue()

    def test_sold_deal_recorded_and_reported(self):
        with TempDir():
            cat = market_items() + [item(1, "iPhone 13 128GB", 150, user_id=1)]
            client = FakeClient({"iPhone 13": cat})
            tg, _ = self.run_once(client)
            self.assertEqual([d["id"] for d, _ in tg.deals], ["vinted:1"])
            state = read_json("state.json")
            self.assertIn("vinted:1", state["tracked"])
            self.assertNotIn("r", state["tracked"]["vinted:1"])

            # kitame paleidime skelbimas jau parduotas
            client.statuses = {"1": (200, '{"is_closed":true}')}
            _, log = self.run_once(client)
            state = read_json("state.json")
            self.assertEqual(state["tracked"]["vinted:1"].get("r"), "sold", log)
            self.assertIn("Rezultatas: iPhone 13", log)

            # pirma paleidima ataskaitos laikrodis pradetas, ataskaitos dar nebuvo
            self.assertTrue(state["last_report"])
            # „praejo savaite“
            state["last_report"] -= 7 * 86400 + 1
            write_json("state.json", state)
            client.statuses = {}
            tg3, _ = self.run_once(client)
            reports = [m for m in tg3.messages if "Rezultatai per 7 d." in m]
            self.assertEqual(len(reports), 1)
            self.assertIn("Nupirkta: <b>1</b>", reports[0])

    def test_command(self):
        with TempDir():
            cat = market_items() + [item(1, "iPhone 13 128GB", 150, user_id=1)]
            self.run_once(FakeClient({"iPhone 13": cat}))
            config.cfg["ADMIN_IDS"] = ["1"]
            tg = FakeTelegram(updates=[(5, "/rezultatai 30")])
            self.run_once(FakeClient({"iPhone 13": cat}), tg)
            self.assertTrue(any("Rezultatai per 30 d." in m and "Pranešta: <b>1</b>" in m
                                for m in tg.messages), tg.messages)

    def test_tracking_off(self):
        config.cfg["TRACK_RESULTS"] = False
        with TempDir():
            cat = market_items() + [item(1, "iPhone 13 128GB", 150, user_id=1)]
            self.run_once(FakeClient({"iPhone 13": cat}))
            state = read_json("state.json")
            self.assertEqual(state["tracked"], {})


if __name__ == "__main__":
    unittest.main()
