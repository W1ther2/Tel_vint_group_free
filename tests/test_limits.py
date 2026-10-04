"""Ribos ir atsparumas (2026-09 antroji kodo perziura).

- busena issaugoma taip, kad nutrauktas paleidimas jos nesugadintu;
- nepavykes Telegram siuntimas nesunaikina dealo – jis siunciamas kitame paleidime;
- MAX_ALERTS_PER_RUN neleidzia lavinos, o likusieji nedingsta;
- ta pati skelbimo busena per paleidima uzklausiama viena karta;
- sugedusi komanda nesustabdo offset'o (kitaip ji vykdoma amzinai);
- bot'o token'as nepatenka i log'a.
"""
import json
import os
import unittest

from tests.helpers import reset_config, TempDir, item
from tests.test_flow import FakeClient, FakeTelegram, market_items, run


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def flow_config(**over):
    reset_config(SEARCH_QUERIES=["iPhone 13"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8, MARKET_PERCENTILE=0.5,
                 MIN_DISCOUNT=0.15, LOUD_DISCOUNT=0.30, MIN_PROFIT_EUR=0, **over)


class AtomicSaveTest(unittest.TestCase):
    """Anksciau buvo rasoma tiesiai i state.json: nutrauktas paleidimas palikdavo
    pusiau irasyta faila, o kitas paleidimas pradedavo nuo tuscio – dingdavo visa
    rinkos istorija ir kalibracija."""

    def setUp(self):
        flow_config()

    def test_write_is_atomic_and_leaves_backup(self):
        from vinted import state as state_mod
        with TempDir():
            state_mod.write_json("x.json", {"a": 1})
            state_mod.write_json("x.json", {"a": 2})
            self.assertEqual(read_json("x.json"), {"a": 2})
            self.assertEqual(read_json("x.json.bak"), {"a": 1})
            self.assertFalse(os.path.exists("x.json.tmp"))

    def test_broken_state_falls_back_to_backup(self):
        from vinted.state import State
        with TempDir():
            client = FakeClient({"iPhone 13": market_items()})
            run(client, FakeTelegram())
            run(client, FakeTelegram())                 # kad butu ir .bak
            good = read_json("state.json")
            self.assertTrue(good["market"]["items"])
            # nutrauktas irasymas: failas nukirptas viduryje
            with open("state.json", "w", encoding="utf-8") as f:
                f.write(json.dumps(good)[:200])
            restored = State.load()
            self.assertEqual(len(restored.market.items), len(good["market"]["items"]))

    def test_broken_seen_falls_back_to_backup(self):
        from vinted.state import load_seen, save_seen
        with TempDir():
            save_seen({"vinted:1": 1_800_000_000.0})
            save_seen({"vinted:1": 1_800_000_000.0, "vinted:2": 1_800_000_000.0})
            with open("seen.json", "w", encoding="utf-8") as f:
                f.write('{"vinted:1": 18000')
            self.assertEqual(set(load_seen()), {"vinted:1"})


class SendFailureTest(unittest.TestCase):
    """Telegram neatsake – anksciau skelbimas likdavo pazymetas matytu ir „pranestu“,
    tad geras pasiulymas dingdavo visam laikui."""

    def setUp(self):
        flow_config()

    def test_failed_send_is_retried_next_run(self):
        class Silent(FakeTelegram):
            ok = False

            def send_deal(self, deal, silent=False, chat_id=None):
                if not self.ok:
                    return False
                return super().send_deal(deal, silent=silent, chat_id=chat_id)

        with TempDir():
            cat = market_items() + [item(1, "iPhone 13 128GB", 190, user_id=1)]
            client = FakeClient({"iPhone 13": cat})
            tg = Silent()
            log = run(client, tg)
            self.assertEqual(tg.deals, [], log)
            self.assertIn("nepavyko išsiųsti – bandysiu vėliau", log)
            self.assertNotIn("vinted:1", read_json("seen.json"))
            self.assertIsNone(read_json("state.json")["market"]["items"]["vinted:1"].get("a"))
            self.assertNotIn("vinted:1", read_json("state.json").get("tracked", {}))

            tg2 = Silent()
            tg2.ok = True
            run(client, tg2)
            self.assertEqual([d["id"] for d, _ in tg2.deals], ["vinted:1"])


class AlertLimitTest(unittest.TestCase):
    def setUp(self):
        flow_config(MAX_ALERTS_PER_RUN=3)

    def test_limit_stops_flood_and_rest_come_later(self):
        with TempDir():
            cat = market_items() + [item(i, "iPhone 13 128GB", 190, user_id=i) for i in range(1, 8)]
            client = FakeClient({"iPhone 13": cat})
            tg = FakeTelegram()
            log = run(client, tg)
            self.assertEqual(len(tg.deals), 3, log)
            self.assertIn("MAX_ALERTS_PER_RUN", log)
            # nepranesti skelbimai NEZYMIMI matytais – kitaip jie butu dinge
            seen = read_json("seen.json")
            sent = {d["id"] for d, _ in tg.deals}
            left = [f"vinted:{i}" for i in range(1, 8) if f"vinted:{i}" not in sent]
            self.assertTrue(left)
            self.assertFalse([uid for uid in left if uid in seen], seen)

            tg2 = FakeTelegram()
            run(client, tg2)
            self.assertEqual(len(tg2.deals), 3)
            self.assertFalse(sent & {d["id"] for d, _ in tg2.deals})

    def test_owner_is_told_how_many_were_left(self):
        """Anksciau riba buvo tik log'o eilute: atkeliaudavo 3 korteles, o kad eileje liko
        dar tiek pat, savininkas nesuzinodavo."""
        with TempDir():
            cat = market_items() + [item(i, "iPhone 13 128GB", 190, user_id=i) for i in range(1, 8)]
            client = FakeClient({"iPhone 13": cat})
            tg = FakeTelegram()
            run(client, tg)
            notes = [m for m in tg.messages if "pranešimų riba" in m]
            self.assertEqual(len(notes), 1, tg.messages)
            self.assertIn("/riba", notes[0])
            self.assertRegex(notes[0], r"liko <b>\d+</b> neįvertintų")
            # ne dazniau nei kas valanda – kitaip kas 10 min. ta pati zinute
            tg2 = FakeTelegram()
            run(client, tg2)
            self.assertFalse([m for m in tg2.messages if "pranešimų riba" in m], tg2.messages)

    def test_no_note_when_limit_not_reached(self):
        flow_config(MAX_ALERTS_PER_RUN=50)
        with TempDir():
            cat = market_items() + [item(i, "iPhone 13 128GB", 190, user_id=i) for i in range(1, 4)]
            tg = FakeTelegram()
            run(FakeClient({"iPhone 13": cat}), tg)
            self.assertFalse([m for m in tg.messages if "pranešimų riba" in m], tg.messages)


class StatusCacheTest(unittest.TestCase):
    """Ta pati skelbima tikrina ir „pardavimu patikra“, ir „pranesimu rezultatai“ –
    Vinted tai buvo dvi uzklausos ir dvi pauzes tam paciam puslapiui."""

    def test_status_requested_once_per_run(self):
        flow_config(SOLD_CHECK_AFTER_DAYS=0, SOLD_CHECKS_PER_RUN=50, TRACK_RESULTS=True,
                    TRACK_MIN_MINUTES=0, MIN_SOLD_SAMPLES=3, GONE_AS_SOLD=False)
        from vinted.util import today
        with TempDir():
            cat = market_items() + [item(1, "iPhone 13 128GB", 190, user_id=1)]
            client = FakeClient({"iPhone 13": cat})
            run(client, FakeTelegram())                       # 1 issiunciamas ir sekamas
            state = read_json("state.json")
            self.assertIn("vinted:1", state.get("tracked", {}))
            # skelbimo kataloge nebera ir jis parduotas
            client.catalog["iPhone 13"] = [i for i in cat if i["id"] != 1]
            client.statuses = {"1": (200, '{"is_closed":true}')}
            state["market"]["items"]["vinted:1"]["l"] = today() - 3
            state["market"]["items"]["vinted:1"]["c"] = today() - 3
            with open("state.json", "w", encoding="utf-8") as f:
                json.dump(state, f)
            client.page_requests.clear()
            run(client, FakeTelegram())
            self.assertEqual(client.page_requests.count("1"), 1, client.page_requests)
            after = read_json("state.json")
            self.assertEqual(after["tracked"]["vinted:1"]["r"], "sold")
            self.assertEqual(after["market"]["items"]["vinted:1"]["st"], "sold")


class CommandResilienceTest(unittest.TestCase):
    def setUp(self):
        flow_config(ADMIN_IDS=["1"])

    def test_broken_command_does_not_repeat_forever(self):
        """Anksciau klaida komandoje nutraukdavo visa zingsni, offset nebudavo irasytas,
        ir ta pati komanda budavo vykdoma kas 10 min. be galo."""
        from unittest import mock
        from vinted import commands
        with TempDir():
            client = FakeClient({"iPhone 13": market_items()})
            tg = FakeTelegram(updates=[(5, "/kaina 13 200")])
            with mock.patch.object(commands, "handle", side_effect=RuntimeError("bum")):
                log = run(client, tg)
            self.assertEqual(read_json("state.json")["telegram_offset"], 5)
            self.assertIn("bum", log)
            self.assertTrue(any("Dalis darbų nepavyko" in m for m in tg.messages))

    def test_private_start_with_extra_lines(self):
        """„/start“ su antra eilute anksciau nebuvo atpazintas, ir zmogus negaudavo
        asmeniniu zinuciu, nors ir parase botui."""
        from vinted.commands import handle_private
        from vinted.state import State
        state = State()
        reply = handle_private({"text": "/start\nlabas", "user": "77", "chat": "77", "name": "A"}, state)
        self.assertIn("Tavo ID: 77", reply)
        self.assertEqual(state.users["77"]["chat"], "77")


class TokenLeakTest(unittest.TestCase):
    """requests klaidos tekste yra visas adresas, o jame – bot'o token'as."""

    def test_token_not_printed(self):
        import contextlib
        import io
        from vinted.telegram import Telegram
        reset_config()

        class Boom:
            @staticmethod
            def post(url, **kwargs):
                raise RuntimeError(f"Max retries exceeded with url: {url}")

        tg = Telegram(token="123456789:AAE-SECRET", chat_id="42", http=Boom(), sleep=lambda s: None)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertFalse(tg.send_message("labas"))
        log = out.getvalue()
        self.assertNotIn("AAE-SECRET", log)
        self.assertNotIn("123456789", log)
        self.assertIn("***", log)


if __name__ == "__main__":
    unittest.main()
