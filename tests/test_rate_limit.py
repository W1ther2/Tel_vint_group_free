# -*- coding: utf-8 -*-
"""HTTP 429 ir salies uzklausu taupymas.

Gyvai ismatuota: www.vinted.lt riboja uzklausu DAZNI (~55 per minute), o riba laikina.
Kol blokas galiojo visam paleidimui, vienas 429 palikdavo be salies patikros visus
likusius gamintojus – i Telegram krisdavo tik pirmojo telefonai. GitHub Actions tai
ypac tikeтina, nes runner'iu IP bendri.
"""
import time
import unittest

from tests.helpers import reset_config, TempDir, item
from tests.test_flow import FakeTelegram
from vinted import config
from vinted.client import VintedClient


class Reply:
    def __init__(self, status_code, text="{}", headers=None, url=""):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.url = url

    def json(self):
        import json
        return json.loads(self.text)


class FakeSession:
    """Grazina 429 po `ok_count` sekmingu uzklausu."""

    def __init__(self, ok_count=2, retry_after=None):
        self.ok_count = ok_count
        self.retry_after = retry_after
        self.calls = []
        self.cookies = {}

    def get(self, url, **kw):
        self.calls.append(url)
        if len(self.calls) <= self.ok_count:
            return Reply(200, '{"user": {"country_code": "LT"}}', url=url)
        headers = {"retry-after": str(self.retry_after)} if self.retry_after else {}
        return Reply(429, "slow down", headers=headers, url=url)


class CooldownTest(unittest.TestCase):
    def setUp(self):
        reset_config(VINTED_RATE_COOLDOWN=90)

    def client(self, session):
        c = VintedClient(session_factory=lambda: session, sleep=lambda s: None)
        c.session = session
        return c

    def test_429_blocks_only_temporarily(self):
        session = FakeSession(ok_count=2)
        c = self.client(session)
        self.assertEqual(c.fetch_user(1).get("country_code"), "LT")
        self.assertEqual(c.fetch_user(2).get("country_code"), "LT")
        self.assertEqual(c.fetch_user(3), {})                 # 429
        self.assertIn("429", c.users_blocked)
        self.assertEqual(c.fetch_user(4), {})                 # atvesinimas – be uzklausos
        before = len(session.calls)
        c.fetch_user(5)
        self.assertEqual(len(session.calls), before, "atvesinimo metu uzklausu buti neturi")
        # kai atvesinimas baigiasi, klausiam toliau (kitaip likę gamintojai lieka be nieko)
        c.limiter._blocked_until = time.time() - 1
        self.assertEqual(c.users_blocked, "")
        session.ok_count = 99
        self.assertEqual(c.fetch_user(6).get("country_code"), "LT")

    def test_retry_after_header_respected(self):
        session = FakeSession(ok_count=0, retry_after=12)
        c = self.client(session)
        c.fetch_user(1)
        self.assertAlmostEqual(c.cooldown_left(), 12, delta=2)

    def test_item_page_429_counts_to_same_limit(self):
        """Skelbimu puslapiai ir pardaveju uzklausos dalijasi ta pacia riba."""
        session = FakeSession(ok_count=0)
        c = self.client(session)
        status, _, _ = c.fetch_item_page("/items/1")
        self.assertEqual(status, 429)
        self.assertIn("429", c.users_blocked)


class Clock:
    """Virtualus laikas: `sleep` ne laukia, o pastumia laikrodi (testai lieka greiti)."""

    def __init__(self, start=1000.0):
        self.t = start
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


class PacingTest(unittest.TestCase):
    """Riba neperzengiama IS VISO: uzklausos istempiamos, o ne pataikoma i 429.

    Kiekvienas 429 kainuoja VINTED_RATE_COOLDOWN (60 s) tylos, o sulaikyta uzklausa –
    sekunde. Todel pigiau rikiuotis, nei po to vesti serveri."""

    def setUp(self):
        reset_config(VINTED_MAX_PER_MINUTE=3, VINTED_RATE_COOLDOWN=60)

    def limiter(self):
        from vinted.limiter import HostLimiter
        clock = Clock()
        return HostLimiter(sleep=clock.sleep, now=clock.now), clock

    def test_requests_within_limit_are_not_delayed(self):
        lim, clock = self.limiter()
        for _ in range(3):
            self.assertTrue(lim.take("katalogas"))
        self.assertEqual(clock.slept, [])
        self.assertEqual(lim.peak, 3)

    def test_over_limit_waits_for_window_to_free_up(self):
        lim, clock = self.limiter()
        for _ in range(3):
            lim.take("katalogas")
        self.assertTrue(lim.take("pardavejas"))
        self.assertEqual(len(clock.slept), 1)
        self.assertAlmostEqual(clock.slept[0], 60, delta=1)
        self.assertAlmostEqual(lim.paced, clock.slept[0], places=3)
        self.assertEqual(lim.hits, 0, "ribos perzengti neturejom, tad 429 neturi buti")

    def test_window_slides(self):
        """Praejus minutei senos uzklausos nebeskaiciuojamos."""
        lim, clock = self.limiter()
        for _ in range(3):
            lim.take("katalogas")
        clock.t += 61
        self.assertTrue(lim.take("katalogas"))
        self.assertEqual(clock.slept, [])

    def test_zero_means_no_pacing(self):
        config.cfg["VINTED_MAX_PER_MINUTE"] = 0
        lim, clock = self.limiter()
        for _ in range(50):
            lim.take("katalogas")
        self.assertEqual(clock.slept, [])

    def test_counts_every_kind_and_reports(self):
        lim, _ = self.limiter()
        lim.take("katalogas")
        lim.take("skelbimas")
        lim.take("pardavejas")
        stats = lim.stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["by_kind"],
                         {"katalogas": 1, "skelbimas": 1, "pardavejas": 1})
        self.assertIn("3 (", lim.report())

    def test_cooldown_is_only_extended_never_shortened(self):
        """Atvesinimas tik pratesiamas.

        Anksciau terminas buvo persistatomas is naujo, tad trumpas `Retry-After: 5`,
        atejes ilgo atvesinimo metu, ji sutrumpindavo iki 5 s – ir mes vel leksdavom i riba.
        Kad atvesinimo metu uzklausu nebutu visai, rupinasi `take()`."""
        lim, clock = self.limiter()
        lim.note_rate_limit(Reply(429, headers={"retry-after": "120"}))
        self.assertAlmostEqual(lim.cooldown_left(), 120, delta=1)
        clock.t += 10
        lim.note_rate_limit(Reply(429, headers={"retry-after": "5"}))
        self.assertAlmostEqual(lim.cooldown_left(), 110, delta=1)
        clock.t += 111
        self.assertEqual(lim.blocked, "")

    def test_retry_after_may_be_a_date(self):
        """RFC leidzia ir data. Anksciau float() mesdavo klaida ir prasymas buvo ignoruotas."""
        from email.utils import formatdate
        from vinted.limiter import retry_after_seconds
        now = 1_700_000_000.0
        when = formatdate(now + 120, usegmt=True)
        self.assertAlmostEqual(retry_after_seconds(Reply(429, headers={"retry-after": when}),
                                                   60, now=now), 120, delta=2)
        self.assertEqual(retry_after_seconds(Reply(429, headers={"retry-after": "nesamone"}),
                                             60, now=now), 60)
        self.assertEqual(retry_after_seconds(Reply(429), 60, now=now), 60)


class CooldownAppliesEverywhereTest(unittest.TestCase):
    """Atvesinimas galioja VISOMS uzklausoms. Anksciau jo klause tik `fetch_user`:
    katalogas ir skelbimu puslapiai belsdavosi toliau – kiekviena uzklausa tuscia ir
    kiekviena dar prailginanti ta pati atvesinima."""

    def setUp(self):
        reset_config(VINTED_RATE_COOLDOWN=60, VINTED_MAX_PER_MINUTE=0, SLEEP_SECONDS=0)

    def client(self, session):
        c = VintedClient(session_factory=lambda: session, sleep=lambda s: None)
        c.session = session
        return c

    def test_catalog_429_registers_the_cooldown(self):
        session = FakeSession(ok_count=0)
        c = self.client(session)
        self.assertIsNone(c.fetch_page("iphone", 1))
        self.assertIn("429", c.users_blocked, "katalogo 429 anksciau nebuvo uzrasomas")

    def test_nothing_is_asked_while_cooling_down(self):
        session = FakeSession(ok_count=0)
        c = self.client(session)
        c.fetch_page("iphone", 1)
        before = len(session.calls)
        self.assertIsNone(c.fetch_page("samsung", 1))
        self.assertEqual(c.fetch_item_page("/items/1"), (0, "", "https://www.vinted.lt/items/1"))
        self.assertEqual(c.fetch_user(7), {})
        self.assertEqual(len(session.calls), before,
                         "atvesinimo metu nei viena uzklausa neturi iseiti i tinkla")
        self.assertEqual(c.limiter.skipped, 3)


class RateLimitedDetailTest(unittest.TestCase):
    """Neatsidares skelbimo puslapis del MUSU ribos nevalgo DETAIL_RETRIES.

    Salies patikrai tai jau buvo istaisyta (`country_limited`), o skelbimo puslapiui – ne:
    ilgesnis atvesinimas per tris paleidimus nurasydavo tvarkingus skelbimus su
    „skelbimo atidaryti nepavyko“."""

    def setUp(self):
        reset_config(DETAIL_RETRIES=3)

    def runner(self):
        import contextlib
        import io
        from vinted.finder import Run
        from vinted.state import State
        runner = Run([], FakeTelegram(), sleep=lambda s: None)
        with contextlib.redirect_stdout(io.StringIO()):
            runner.state = State.load()
        runner.new_seen = {"vinted:1": time.time()}
        return runner

    class Limited:
        rate_limited = True

    class Healthy:
        rate_limited = False

    def test_rate_limited_failure_is_not_counted(self):
        with TempDir():
            runner = self.runner()
            for _ in range(5):
                self.assertIsNone(runner.detail_failed("vinted:1", "iPhone 13", self.Limited()))
            self.assertEqual(runner.state.detail_failures, {})
            self.assertNotIn("vinted:1", runner.new_seen, "skelbimas turi likti nematytas")

    def test_ordinary_failure_still_runs_out_of_retries(self):
        with TempDir():
            runner = self.runner()
            for _ in range(3):
                self.assertIsNone(runner.detail_failed("vinted:1", "iPhone 13", self.Healthy()))
            self.assertEqual(runner.state.detail_failures["vinted:1"]["n"], 3)
            self.assertFalse(runner.detail_failed("vinted:1", "iPhone 13", self.Healthy()))


class RequestMeterTest(unittest.TestCase):
    """Paleidimas pats pasako, kiek uzklausu isleido ir ar riba dar pasiekiama.

    Siame projekte ribos nustatomos is gyvu matavimu (zr. README), tad skaitliukai
    patenka i log'a ir i state.json – kitaip VINTED_MAX_PER_MINUTE ir
    SELLER_COUNTRY_LOOKUPS tenka spelioti."""

    def setUp(self):
        reset_config(VINTED_MAX_PER_MINUTE=45, SLEEP_SECONDS=0)

    def test_report_names_every_kind(self):
        import contextlib
        import io
        from vinted.finder import Run
        from vinted.sources.vinted_source import VintedSource
        session = FakeSession(ok_count=99)
        client = VintedClient(session_factory=lambda: session, sleep=lambda s: None)
        client.session = session
        source = VintedSource(client=client)
        client.fetch_user(1)
        client.fetch_item_page("/items/2")
        runner = Run([source], FakeTelegram(), sleep=lambda s: None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            stats = runner.request_report()
        self.assertEqual(stats["vinted"]["total"], 2)
        self.assertEqual(stats["vinted"]["by_kind"], {"pardavejas": 1, "skelbimas": 1})
        self.assertEqual(stats["vinted"]["rate_limits"], 0)
        self.assertIn("Vinted uzklausos", out.getvalue())

    def test_sources_without_a_limiter_are_skipped(self):
        from vinted.finder import Run

        class Plain:
            name = "skelbiu"
            label = "Skelbiu"
        self.assertEqual(Run([Plain()], FakeTelegram()).request_report(), {})


class LearnedRateTest(unittest.TestCase):
    """AIMD: greitis mokomas pagal 429, nes GitHub Actions runner'iu IP yra bendri.

    45/min yra spejimas vienam runner'iui. Kai tuo paciu IP dirba kitas darbas, tikroji
    riba zemesne, ir jokia staline reiksme to nenumatys."""

    def setUp(self):
        reset_config(VINTED_MAX_PER_MINUTE=45, VINTED_RATE_ADAPT=True, VINTED_RATE_MIN=15)

    def runner(self, hits=0, total=40, learned=None):
        import contextlib
        import io
        from vinted.finder import Run
        from vinted.limiter import HostLimiter
        from vinted.state import State

        limiter = HostLimiter(sleep=lambda s: None)
        limiter.counts = {"katalogas": total}
        if learned is not None:
            limiter.rate = float(learned)          # kaip apply_learned_rate paleidimo pradzioje
        for _ in range(hits):
            limiter.note_rate_limit(Reply(429))    # tikras kelias: greitis mazinamas is karto

        class Src:
            name, label = "vinted", "Vinted"

        src = Src()
        src.limiter = limiter
        runner = Run([src], FakeTelegram(), sleep=lambda s: None)
        with contextlib.redirect_stdout(io.StringIO()):
            runner.state = State.load()
        if learned is not None:
            runner.state.rate_limit = {"vinted": {"per_minute": learned}}
        return runner, limiter

    def learn(self, runner):
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            runner.learn_rate_limit()
        return runner.state.rate_limit["vinted"]["per_minute"]

    def test_429_cuts_rate_during_the_run(self):
        """Mazinama IS KARTO, ne tik kitam paleidimui: kiekvienas paleidimas gauna nauja
        runner'i, tad vien tarp paleidimu issimoktas greitis vėluotu."""
        with TempDir():
            runner, limiter = self.runner(hits=1)
            self.assertEqual(limiter.limit(), 31)          # likusiai paleidimo daliai
            self.assertAlmostEqual(self.learn(runner), 31.5)
            self.assertEqual(runner.state.rate_limit["vinted"]["bad"], 45)

    def test_remembers_refused_rate(self):
        """Be atminties greitis kas kelis paleidimus vel atsitrenkdavo i ta pacia siena."""
        import time as t
        with TempDir():
            runner, _ = self.runner(learned=27)
            runner.state.rate_limit = {"vinted": {"per_minute": 27, "bad": 31, "bad_at": t.time()}}
            self.assertEqual(self.learn(runner), 28)       # 31 - 3
            runner.state.rate_limit["vinted"]["per_minute"] = 28
            self.assertEqual(self.learn(runner), 28)       # toliau nekyla

    def test_old_memory_is_forgotten(self):
        import time as t
        with TempDir():
            runner, _ = self.runner(learned=28)
            runner.state.rate_limit = {"vinted": {"per_minute": 28, "bad": 31,
                                                  "bad_at": t.time() - 7 * 3600}}
            self.assertEqual(self.learn(runner), 31)
            self.assertIsNone(runner.state.rate_limit["vinted"]["bad"])

    def test_clean_run_recovers_slowly(self):
        with TempDir():
            runner, _ = self.runner(learned=30)
            self.assertEqual(self.learn(runner), 33)

    def test_never_above_configured_ceiling(self):
        """Lubos – is nustatymu: issimoktas skaicius neperzengia to, ka leido zmogus."""
        with TempDir():
            runner, _ = self.runner(learned=44)
            self.assertEqual(self.learn(runner), 45)
            runner, _ = self.runner(learned=45)
            self.assertEqual(self.learn(runner), 45)

    def test_never_below_floor(self):
        with TempDir():
            runner, limiter = self.runner(hits=3, learned=20)
            self.assertEqual(limiter.limit(), 15)
            self.assertEqual(self.learn(runner), 15)

    def test_small_sample_teaches_nothing(self):
        with TempDir():
            runner, _ = self.runner(hits=1, total=5, learned=40)
            runner.state.rate_limit = {"vinted": {"per_minute": 40}}
            self.learn(runner)
            self.assertEqual(runner.state.rate_limit["vinted"]["per_minute"], 40)

    def test_disabled_keeps_configured_rate(self):
        config.cfg["VINTED_RATE_ADAPT"] = False
        with TempDir():
            runner, limiter = self.runner(hits=3)
            self.assertEqual(limiter.limit(), 45, "isjungus – ir paleidimo metu nemazinam")
            runner.learn_rate_limit()
            self.assertEqual(runner.state.rate_limit, {})
            runner.state.rate_limit = {"vinted": {"per_minute": 20}}
            runner.apply_learned_rate()
            self.assertEqual(limiter.limit(), 45)

    def test_learned_rate_is_applied_and_survives_save(self):
        import contextlib
        import io
        from vinted.state import State
        with TempDir():
            runner, _ = self.runner(hits=1)
            self.learn(runner)
            runner.state.save()
            again, limiter = self.runner()
            with contextlib.redirect_stdout(io.StringIO()):
                again.state = State.load()
                again.apply_learned_rate()
            self.assertEqual(limiter.limit(), 31)

    def test_learned_rate_actually_paces(self):
        """Issimoktas greitis ne tik irasomas – limiter'is pagal ji ir stabdo."""
        from vinted.limiter import HostLimiter
        config.cfg["VINTED_RATE_MIN"] = 1          # kitaip grindys (15) uzgozti rate=3
        clock = Clock()
        lim = HostLimiter(sleep=clock.sleep, now=clock.now, rate=3)
        for _ in range(3):
            lim.take("katalogas")
        self.assertEqual(clock.slept, [])
        lim.take("katalogas")
        self.assertAlmostEqual(clock.slept[0], 60, delta=1)


class FloorAfter429Test(unittest.TestCase):
    """VINTED_RATE_MIN – grindys, kurios galioja VISADA, ne tik paleidimui su 429."""

    def setUp(self):
        reset_config(VINTED_MAX_PER_MINUTE=45, VINTED_RATE_ADAPT=True, VINTED_RATE_MIN=15)

    def learn(self, entry, hits=0):
        import contextlib
        import io
        from vinted.finder import Run
        from vinted.limiter import HostLimiter
        from vinted.state import State
        lim = HostLimiter(sleep=lambda s: None)
        lim.counts = {"katalogas": 40}
        if entry:
            lim.rate = entry["per_minute"]
        for _ in range(hits):
            lim.note_rate_limit(Reply(429))

        class Src:
            name, label = "vinted", "Vinted"
        src = Src()
        src.limiter = lim
        runner = Run([src], FakeTelegram(), sleep=lambda s: None)
        with contextlib.redirect_stdout(io.StringIO()):
            runner.state = State.load()
            runner.state.rate_limit = {"vinted": dict(entry)} if entry else {}
            runner.learn_rate_limit()
        return runner.state.rate_limit["vinted"], lim

    def test_429_at_floor_then_clean_run_stays_at_floor(self):
        """429 prie 15/min -> bad=15, cap=12. Kitas tylus paleidimas neturi eiti 12/min."""
        with TempDir():
            entry, lim = self.learn({"per_minute": 15}, hits=1)
            self.assertEqual((entry["per_minute"], entry["bad"]), (15, 15))
            self.assertEqual(lim.limit(), 15)
            for _ in range(3):
                entry, lim = self.learn(entry)
                self.assertGreaterEqual(entry["per_minute"], 15)
                self.assertGreaterEqual(lim.limit(), 15)

    def test_old_state_below_raised_floor_is_lifted(self):
        """VINTED_RATE_MIN pakeltas, o state.json liko senas 12 (ir bad=16 -> cap 13)."""
        import time as t
        with TempDir():
            entry, _ = self.learn({"per_minute": 12, "bad": 16, "bad_at": t.time()})
            self.assertEqual(entry["per_minute"], 15)

    def test_limiter_itself_respects_floor(self):
        from vinted.limiter import HostLimiter
        self.assertEqual(HostLimiter(rate=12).limit(), 15)

    def test_ceiling_beats_floor(self):
        """Jei zmogus lubas nustate zemiau grindu – lubos svarbesnes."""
        from vinted.limiter import HostLimiter
        config.cfg["VINTED_MAX_PER_MINUTE"] = 10
        self.assertEqual(HostLimiter(rate=12).limit(), 10)
        with TempDir():
            entry, _ = self.learn({"per_minute": 12}, hits=1)
            self.assertEqual(entry["per_minute"], 10)


class ConcurrentTakeTest(unittest.TestCase):
    """Rezervuota, bet dar miegojusi uzklausa neturi iseiti, jei per ta laika atejo 429."""

    def setUp(self):
        reset_config(VINTED_MAX_PER_MINUTE=2, VINTED_RATE_COOLDOWN=60, VINTED_RATE_ADAPT=False)

    def test_sleeper_cancels_when_other_request_got_429(self):
        """A laukia vietos; tuo metu B gauna 429; A pabudusi nebesiuncia."""
        from vinted.limiter import HostLimiter
        clock = Clock()
        lim = None

        def sleep(seconds):
            clock.sleep(seconds)
            lim.note_rate_limit(Reply(429))      # „kita gija“ gavo 429, kol A miegojo

        lim = HostLimiter(sleep=sleep, now=clock.now)
        self.assertTrue(lim.take("katalogas"))
        self.assertTrue(lim.take("katalogas"))
        self.assertFalse(lim.take("skelbimas"), "pabudusi i atvesinima – neklausti")
        self.assertEqual(lim.counts, {"katalogas": 2}, "atsaukta uzklausa neskaiciuojama")
        self.assertEqual(lim.skipped, 1)
        self.assertEqual(len(lim._window), 2, "rezervacija grazinta")

    def test_real_threads_never_exceed_the_limit(self):
        """Tikros gijos: per langa niekada daugiau nei riba, ir po 429 nei vienos uzklausos."""
        import threading
        from vinted import limiter as limiter_mod
        old_window = limiter_mod.WINDOW
        limiter_mod.WINDOW = 0.3                  # trumpas langas – testas trunka <2 s
        try:
            config.cfg["VINTED_MAX_PER_MINUTE"] = 3
            lim = limiter_mod.HostLimiter()
            sent, lock = [], threading.Lock()
            blocked_at = []

            def worker(n):
                for i in range(4):
                    if lim.take("katalogas"):
                        with lock:
                            sent.append(time.time())
                            if len(sent) == 6 and not blocked_at:
                                blocked_at.append(time.time())
                                lim.note_rate_limit(Reply(429, headers={"retry-after": "30"}))

            threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
            for th in threads:
                th.start()
            for th in threads:
                th.join(10)
            self.assertFalse(any(th.is_alive() for th in threads))
            self.assertEqual(len(sent), 6, f"po 429 uzklausu buti neturejo: {len(sent)}")
            for t0 in sent:                       # bet kuriame lange – ne daugiau nei 3
                in_window = [x for x in sent if t0 <= x < t0 + limiter_mod.WINDOW - 0.02]
                self.assertLessEqual(len(in_window), 3, sent)
            self.assertEqual(lim.skipped, 16 - 6)
        finally:
            limiter_mod.WINDOW = old_window


class SearchWaitsOutCooldownTest(unittest.TestCase):
    """Atvesinimas neturi praleisti viso gamintojo katalogo – tik atideti paieska.

    Nuo v46 atvesinimo metu uzklausu nedarom visai, tad be laukimo `source.search()`
    grazintu nuli skelbimu ir gamintojas tyliai liktu neperziuretas."""

    def setUp(self):
        reset_config(MAX_RUN_MINUTES=25, FILTER_BY_COUNTRY=False, MAX_ALERTS_PER_RUN=0)

    def test_waits_before_searching(self):
        import contextlib
        import io
        from vinted.finder import Run
        from vinted.state import State

        events = []

        class StubSource:
            name, label = "vinted", "Vinted"
            blocked_queries, unavailable, last_error = 0, "", ""
            country_needs_request = False
            cooldown = 50.0

            @property
            def country_cooldown_left(self):
                return self.cooldown

            def start(self):
                pass

            def finish(self, run):
                pass

            def page_count(self, pages):
                return pages

            def queries(self):
                return ["iphone"]

            def describe(self, q):
                return q

            def search(self, q, pages, seen=None):
                events.append(("paieska", self.cooldown))
                return []

        source = StubSource()

        def sleep(seconds):
            events.append(("laukta", round(seconds)))
            source.cooldown = 0.0

        runner = Run([source], FakeTelegram(), sleep=sleep)
        with contextlib.redirect_stdout(io.StringIO()):
            runner.state = State.load()
            runner.new_seen = {}
            runner._scan(source, set(), 1)
        self.assertEqual(events[0][0], "laukta")
        self.assertTrue(45 <= events[0][1] <= 55, events)
        self.assertIn(("paieska", 0.0), events, "po laukimo paieska turi ivykti")


class CooldownWaitTest(unittest.TestCase):
    """Uzuot nurasius likusius gamintojus, palaukiam, kol riba atsileis."""

    def setUp(self):
        reset_config(FILTER_BY_COUNTRY=True, ALLOWED_COUNTRY_CODES=["LT"],
                     SELLER_COUNTRY_LOOKUPS=10, MAX_RUN_MINUTES=25, BRANDS=["google"])

    def runner_with(self, cooldown):
        import contextlib
        import io
        from vinted.finder import Run
        from vinted.state import State

        class StubSource:
            name = "vinted"
            label = "Vinted"
            country_needs_request = True

            def __init__(self):
                self.cooldown = cooldown
                self.asked = []

            @property
            def country_cooldown_left(self):
                return self.cooldown

            @property
            def country_lookups_blocked(self):
                return self.cooldown > 0

            def seller_country(self, listing):
                self.asked.append(listing.seller_id)
                return "LT"

        source = StubSource()
        slept = []
        runner = Run([source], FakeTelegram(), sleep=lambda s: (slept.append(s),
                                                               setattr(source, "cooldown", 0)))
        with contextlib.redirect_stdout(io.StringIO()):
            runner.state = State.load()
        runner.new_seen = {}
        return runner, source, slept

    def rows(self):
        from tests.helpers import listings
        return listings(*[item(20 + i, "Google Pixel 8 128GB", 250, user_id=20 + i) for i in range(3)])

    def test_waits_then_asks(self):
        import contextlib
        import io
        with TempDir():
            runner, source, slept = self.runner_with(cooldown=40)
            with contextlib.redirect_stdout(io.StringIO()):
                runner.resolve_countries(source, self.rows(), queries_left=2)
            self.assertTrue(slept and 35 <= slept[0] <= 45, slept)
            self.assertEqual(len(source.asked), 3, source.asked)

    def test_does_not_wait_when_no_time_left(self):
        import contextlib
        import io
        with TempDir():
            runner, source, slept = self.runner_with(cooldown=40)
            runner.started -= 25 * 60 - 30        # liko 30 s – laukti nebegalima
            with contextlib.redirect_stdout(io.StringIO()):
                runner.resolve_countries(source, self.rows(), queries_left=2)
            self.assertEqual(slept, [])
            self.assertEqual(source.asked, [])


class CheapListingsTest(unittest.TestCase):
    """Salies uzklausos yra ribotos – priedams ju gaisti negalima."""

    def setUp(self):
        reset_config(SEARCH_QUERIES=["google pixel"], HEARTBEAT_HOURS=0, MIN_SAMPLES=8,
                     FILTER_BY_COUNTRY=True, ALLOWED_COUNTRY_CODES=["LT"],
                     SELLER_COUNTRY_LOOKUPS=10, BRANDS=["google"], VINTED_BROWSE_ALL=False)

    def test_below_price_floor_costs_no_lookup(self):
        import contextlib
        import io
        from tests.helpers import listings
        from tests.test_country import CountingClient
        from vinted.finder import Run
        from vinted.sources.vinted_source import VintedSource
        from vinted.state import State
        with TempDir():
            client = CountingClient({})
            source = VintedSource(client=client)
            runner = Run([source], FakeTelegram(), sleep=lambda s: None)
            with contextlib.redirect_stdout(io.StringIO()):
                runner.state = State.load()
            runner.new_seen = {}
            rows = listings(
                *[item(10 + i, "Google Pixel 8 dėklas su apsauga", 6, user_id=10 + i) for i in range(5)],
                *[item(20 + i, "Google Pixel 8 128GB", 250, user_id=20 + i) for i in range(2)])
            with contextlib.redirect_stdout(io.StringIO()):
                runner.resolve_countries(source, rows, queries_left=1)
            self.assertEqual(sorted(client.user_requests), [20, 21], client.user_requests)


if __name__ == "__main__":
    unittest.main()
