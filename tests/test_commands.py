import unittest

from tests.helpers import reset_config
from vinted import config, commands
from vinted.state import State


class CommandsTest(unittest.TestCase):
    def setUp(self):
        reset_config(MARKET_PRICES={"14": 300})
        self.state = State()

    def test_price(self):
        self.assertIn("iPhone 13: rinkos kaina 180", commands.handle("/kaina 13 180", self.state))
        self.assertEqual(config.market_prices()["13"], 180)
        self.assertIn("iPhone 13 Pro Max 256 GB", commands.handle("/kaina@VintBot 13 pro max 256 390", self.state))
        self.assertEqual(config.market_prices()["13 Pro Max|256 GB"], 390)
        self.assertEqual(config.market_prices()["14"], 300)          # config.json kaina islieka
        commands.handle("/kaina 13 trinti", self.state)
        self.assertNotIn("13", config.market_prices())
        self.assertIn("Nežinomas modelis", commands.handle("/kaina 7 100", self.state))

    def test_overrides_survive_reload(self):
        commands.handle("/kaina 13 180", self.state)
        commands.handle("/nuolaida 20", self.state)
        reset_config()
        config.apply_overrides(State(self.state.__dict__ | {"market": None}).overrides)
        self.assertEqual(config.cfg["MIN_DISCOUNT"], 0.20)
        self.assertEqual(config.market_prices(), {"13": 180})

    def test_settings(self):
        commands.handle("/baterija 80", self.state)
        commands.handle("/garsas 35", self.state)
        commands.handle("/pauze", self.state)
        self.assertEqual((config.cfg["MIN_BATTERY"], config.cfg["LOUD_DISCOUNT"], config.cfg["PAUSED"]),
                         (80, 0.35, True))
        self.assertIn("Pauzė: taip", commands.handle("/nustatymai", self.state))
        commands.handle("/testi", self.state)
        self.assertFalse(config.cfg["PAUSED"])

    def test_stats(self):
        self.assertIn("dar nėra", commands.handle("/statistika", self.state))
        import time
        self.state.last_run = {"time": time.time(), "fetched": 3000, "new": 500, "sent": 15,
                               "totals": {"per brangu": 400, "defektai": 30}}
        text = commands.handle("/statistika", self.state)
        self.assertIn("per brangu: <b>400</b>", text)
        self.assertIn("/nuolaida 10", text)

    def test_profit_toggle(self):
        self.assertIn("nebebus", commands.handle("/pelnas ne", self.state))
        self.assertFalse(config.cfg["SHOW_PROFIT"])
        commands.handle("/pelnas taip", self.state)
        self.assertTrue(config.cfg["SHOW_PROFIT"])

    def test_market_percentile(self):
        self.assertIn("mediana", commands.handle("/rinka 50", self.state))
        self.assertEqual(config.cfg["MARKET_PERCENTILE"], 0.5)
        self.assertIn("Neteisinga", commands.handle("/rinka 99", self.state))

    def test_admin_only_settings_in_private(self):
        config.cfg["ADMIN_IDS"] = ["77"]
        start = {"text": "/start", "user": "77", "chat": "999", "name": "Vy", "private": True}
        self.assertIn("Tavo ID: 77", commands.handle_private(start, self.state))
        self.assertIn("rinkos kaina 180", commands.handle_private({**start, "text": "/kaina 13 180"}, self.state))
        kitas = {"text": "/kaina 13 500", "user": "5", "chat": "5", "name": "X", "private": True}
        self.assertNotIn("500", commands.handle_private(kitas, self.state))
        self.assertEqual(config.market_prices()["13"], 180)

    def test_callbacks_and_private(self):
        cb = {"data": "w|13 Pro", "user": "77", "name": "Vy", "id": "x"}
        self.assertIn("13 Pro", commands.handle_callback(cb, self.state))
        self.assertEqual(self.state.users["77"]["watch"], ["13 Pro"])
        self.assertIn("Nebesiųsiu", commands.handle_callback(cb, self.state))   # perjungia atgal
        hide = {"data": "h|555", "user": "77", "name": "Vy", "id": "x"}
        commands.handle_callback(hide, self.state)
        self.assertEqual(self.state.users["77"]["hide"], ["555"])

        start = {"text": "/start", "user": "77", "chat": "999", "name": "Vy", "private": True}
        self.assertIn("Sveikas", commands.handle_private(start, self.state))
        self.assertEqual(self.state.users["77"]["chat"], "999")
        self.assertIn("Seki:", commands.handle_private({**start, "text": "/mano"}, self.state))
        commands.handle_private({**start, "text": "/stop"}, self.state)
        self.assertIsNone(self.state.users["77"]["chat"])

    def test_bad_input(self):
        self.assertIn("Neteisinga", commands.handle("/nuolaida daug", self.state))
        self.assertIsNone(commands.handle("/nezinoma", self.state))
        self.assertIn("/kaina", commands.handle("/pagalba", self.state))


if __name__ == "__main__":
    unittest.main()
