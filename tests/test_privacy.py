# -*- coding: utf-8 -*-
"""v50: kas is busenos NETURI gulti i viesa `busena` saka.

Situacijos esme: botas veikia GitHub Actions, o busena saugoma `busena` sakoje
to pacio VIESO repozitoriumo. Vadinasi viskas, kas atsiduria state.json, yra
vieso git istorijoje – ir ten lieka net tada, kai veliau isimi. Todel:

  pardavejo ID   -> maisos kodas. Kesui reikia tik atpazinti TA PATI pardaveja.
  vartotojo vardas -> visai nerasomas (jis buvo rasomas, bet niekur neskaitomas).
  administratoriaus ID -> is aplinkos (GitHub Secrets), ne is config.json.

Ko sie testai NEGALI pataisyti: privataus pokalbio ID (`chat`). Be jo nera kur
siusti asmeninio pranesimo, tad jis lieka busenoje – ir butent del to DM
funkcijos neverta plesti, kol busena nepersikele i privacia saugykla.
"""

import os
import unittest

from vinted import config
from vinted.state import State


class SellerKeyTest(unittest.TestCase):
    def test_real_id_never_appears_in_state(self):
        s = State()
        s.remember_seller("vinted:6157710734", "LT", day=100)
        blob = repr(s.sellers)
        self.assertNotIn("6157710734", blob)
        self.assertEqual(s.seller_country("vinted:6157710734"), "LT")

    def test_lookup_still_works_after_reload(self):
        """Maisos kodas stabilus – antras paleidimas salį randa be uzklausos."""
        s = State()
        s.remember_seller("vinted:42", "PL", day=7)
        again = State({"sellers": s.sellers})
        self.assertEqual(again.seller_country("vinted:42"), "PL")

    def test_old_plaintext_cache_is_migrated_not_dropped(self):
        """Be perejimo butu kaines visa kesa (gyvai 1285 irasai) ir sukele
        uzklausu banga: salys butu atsistacius tik per ~11 paleidimu."""
        s = State({"sellers": {"vinted:42": ["LT", 7], "vinted:43": ["PL", 7]}})
        self.assertEqual(s.seller_country("vinted:42"), "LT")
        self.assertEqual(s.seller_country("vinted:43"), "PL")
        self.assertNotIn("vinted:42", s.sellers)

    def test_migration_leaves_already_hashed_keys_alone(self):
        """Antras paleidimas neturi maisyti maisos kodo dar karta."""
        key = State.seller_key("vinted:42")
        s = State({"sellers": {key: ["LT", 7]}})
        self.assertEqual(list(s.sellers), [key])
        self.assertEqual(s.seller_country("vinted:42"), "LT")

    def test_hash_is_not_reversible_by_shape(self):
        """16 hex simboliu – trumpas, bet pardavejo ID is jo neatstatysi."""
        key = State.seller_key("vinted:42")
        self.assertEqual(len(key), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in key))


class UserNameTest(unittest.TestCase):
    def test_name_is_not_stored(self):
        s = State()
        s.user(777, "Vardas Pavardenis")
        self.assertNotIn("name", s.users["777"])
        self.assertNotIn("Vardas", repr(s.users))

    def test_old_stored_name_is_cleaned_on_touch(self):
        s = State({"users": {"777": {"name": "Vardas", "watch": [], "hide": []}}})
        s.user(777)
        self.assertNotIn("name", s.users["777"])

    def test_watch_and_hide_survive(self):
        """Vardo atsisakymas neturi nunesti to, kas tikrai naudojama."""
        s = State({"users": {"777": {"name": "Vardas", "watch": ["13"], "hide": ["x"]}}})
        u = s.user(777, "Vardas")
        self.assertEqual(u["watch"], ["13"])
        self.assertEqual(u["hide"], ["x"])


class AdminIdsTest(unittest.TestCase):
    def setUp(self):
        self.before = os.environ.get("ADMIN_IDS")
        self.cfg_before = list(config.cfg.get("ADMIN_IDS") or [])

    def tearDown(self):
        if self.before is None:
            os.environ.pop("ADMIN_IDS", None)
        else:
            os.environ["ADMIN_IDS"] = self.before
        config.cfg["ADMIN_IDS"] = self.cfg_before

    def test_default_is_empty(self):
        self.assertEqual(config.DEFAULTS["ADMIN_IDS"], [])

    def test_tracked_config_has_no_admin_id(self):
        """config.json sekamas git'e – tikro ID jame buti negali."""
        import json
        with open("config.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data.get("ADMIN_IDS", []), [])

    def test_env_supplies_the_real_ids(self):
        config.cfg["ADMIN_IDS"] = []
        os.environ["ADMIN_IDS"] = "6157710734"
        config._admins_from_env()
        self.assertEqual(config.cfg["ADMIN_IDS"], ["6157710734"])

    def test_env_accepts_several(self):
        os.environ["ADMIN_IDS"] = "111, 222;333"
        config._admins_from_env()
        self.assertEqual(config.cfg["ADMIN_IDS"], ["111", "222", "333"])

    def test_empty_env_keeps_config_value(self):
        """Senos konfiguracijos nesulūzta: be aplinkos lieka tai, kas faile."""
        config.cfg["ADMIN_IDS"] = ["999"]
        os.environ["ADMIN_IDS"] = ""
        config._admins_from_env()
        self.assertEqual(config.cfg["ADMIN_IDS"], ["999"])

    def test_setup_message_points_to_secrets_not_config(self):
        from vinted.commands import admin_setup_message
        msg = admin_setup_message(6157710734)
        self.assertIn("Secrets", msg)
        self.assertIn("6157710734", msg)       # savo ID vartotojas turi pamatyti
        self.assertNotIn("pakeisk į", msg)     # senas patarimas redaguoti config.json


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
