# -*- coding: utf-8 -*-
"""v50: salies uzklausu eileje rezervuojama dalis laukiantiems.

Gyvai matuota 2026-10-05, praejus kelioms valandoms po Android prijungimo:
is 100 Android irasu 61 (61 %) stovejo „salis?" busenoje, o Apple, kurio
pardaveju 1146 jau atmintyje, tik 2 %. Priezastis buvo `todo + backfill`:
nauji visada pirmi, tad prie nuolatinio nauju srauto laukiantys negaudavo
nieko NIEKADA. Tie 61 nedalyvavo nei rinkos kainoje, nei „pigiausiu" palyginime.
"""

import unittest

from tests.helpers import reset_config
from vinted import config
from vinted.finder import Run


def q(todo, backfill, budget):
    """Tik eiles tvarka – be tinklo ir be busenos."""
    return Run._country_queue(list(todo), list(backfill), budget)


class CountryQueueTest(unittest.TestCase):
    def setUp(self):
        reset_config()
        config.cfg["COUNTRY_BACKFILL_SHARE"] = 0.30

    def test_waiting_listings_are_not_starved_by_a_steady_stream_of_new_ones(self):
        """Esme. 100 nauju ir 61 laukiantis, biudzetas 20 – laukiantys TURI gauti dali."""
        todo = [f"n{i}" for i in range(100)]
        backfill = [f"b{i}" for i in range(61)]
        served = q(todo, backfill, 20)[:20]
        from_backfill = [x for x in served if x.startswith("b")]
        self.assertEqual(len(from_backfill), 6, "30 % is 20 = 6 vietos laukiantiems")
        self.assertEqual(len(served) - len(from_backfill), 14)

    def test_new_listings_still_come_first(self):
        """Rezervas nedidelis samoningai: naujas skelbimas gali but dealas SIANDIEN."""
        served = q(["n1", "n2", "n3", "n4", "n5"], ["b1", "b2"], 5)[:5]
        self.assertEqual(served[:3], ["n1", "n2", "n3"])

    def test_no_backfill_means_everything_goes_to_new(self):
        served = q(["n1", "n2", "n3"], [], 3)[:3]
        self.assertEqual(served, ["n1", "n2", "n3"])

    def test_no_new_means_everything_goes_to_waiting(self):
        served = q([], ["b1", "b2", "b3"], 3)[:3]
        self.assertEqual(served, ["b1", "b2", "b3"])

    def test_reserve_never_exceeds_what_is_waiting(self):
        """Vienas laukiantis prie 20 biudzeto neturi uzimti sesiu vietu."""
        served = q([f"n{i}" for i in range(50)], ["b1"], 20)[:20]
        self.assertEqual(len([x for x in served if x.startswith("b")]), 1)

    def test_share_zero_restores_the_old_behaviour(self):
        """Jungiklis atgal i v49 elgesi – jei rezervas kada nors pasirodytu klaida."""
        config.cfg["COUNTRY_BACKFILL_SHARE"] = 0
        served = q(["n1", "n2", "n3"], ["b1", "b2"], 3)[:3]
        self.assertEqual(served, ["n1", "n2", "n3"])

    def test_nothing_is_lost_from_the_queue(self):
        """Eile tik PERRIKIUOJAMA – nei vienas skelbimas negali dingti."""
        todo, backfill = [f"n{i}" for i in range(7)], [f"b{i}" for i in range(5)]
        out = q(todo, backfill, 4)
        self.assertEqual(sorted(out), sorted(todo + backfill))
        self.assertEqual(len(out), len(set(out)), "ir nesidubliuoja")

    def test_zero_budget_keeps_the_plain_order(self):
        out = q(["n1"], ["b1"], 0)
        self.assertEqual(out, ["n1", "b1"])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
