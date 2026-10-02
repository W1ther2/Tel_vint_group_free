# -*- coding: utf-8 -*-
"""Vinted uzklausu greicio sargas – viena riba visam serveriui.

Gyvai ismatuota: www.vinted.lt riboja uzklausu DAZNI (~55 per minute; virsijus – HTTP 429),
ir riba yra BENDRA: ja valgo ir katalogo puslapiai, ir skelbimu puslapiai, ir pardaveju
profiliai. v44 sutvarke *atgaline* puse – po 429 palaukiama ir klausiama toliau. Bet kiekvienas
429 kainuoja VINTED_RATE_COOLDOWN (60 s) tylos, o viena uzklausa, sulaikyta ~1,3 s, nekainuoja
beveik nieko. Todel cia riba neperzengiama is viso: slankiuoju minutes langu uzklausos
istempiamos iki VINTED_MAX_PER_MINUTE, o 429 telieka avarine israsa (pvz. kai ta pati
GitHub runner'io IP tuo paciu metu naudoja ir kitas darbas).

Du dalykai, isaiskinti gyvai ir sutvarkyti butent cia:
- atvesinimas tik PRATESIAMAS, niekada nepersistatomas is naujo. Anksciau kas antras
  skelbimo puslapis, klausiamas atvesinimo metu, gaudavo 429 ir nustatydavo nauja termina
  „nuo dabar + 60 s“, tad atvesinimas niekada nepasibaigdavo, o salies patikra visam
  paleidimui likdavo be uzklausu;
- atvesinimo metu uzklausu NEDAROMA (`take()` grazina False). Anksciau to klause tik
  `fetch_user`, o katalogo ir skelbimu puslapiai belsdavosi toliau – kiekvienas tusciai
  ir kiekvienas prailgindamas ta pati atvesinima.
"""

import threading
import time
from collections import deque
from email.utils import parsedate_to_datetime

from . import config

WINDOW = 60.0            # slankiojo lango ilgis (sekundes)
MAX_COOLDOWN = 300.0     # ilgiausias atvesinimas, kurio praso serveris (Retry-After)

# Uzklausu rusys – tik log'ui, kad butu matyti, kas valgo riba.
LABELS = {"sesija": "sesija", "katalogas": "katalogas", "skelbimas": "skelbimai",
          "pardavejas": "pardavejai"}


def retry_after_seconds(response, default, now=None):
    """Kiek sekundziu praso palaukti serveris (`Retry-After`), arba `default`.

    Antraste gali buti ir skaicius („90“), ir HTTP data („Wed, 01 Oct 2026 18:00:00 GMT“) –
    RFC leidzia abu. Anksciau datos atveju `float()` mesdavo klaida ir buvo imamas
    numatytasis laikas, t. y. serverio praseno buvo nepaklausyta."""
    headers = getattr(response, "headers", None) or {}
    raw = str(headers.get("retry-after") or headers.get("Retry-After") or "").strip()
    if not raw:
        return default
    now = time.time() if now is None else now
    try:
        seconds = float(raw)
    except ValueError:
        try:
            stamp = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return default
        if stamp is None:
            return default
        seconds = stamp.timestamp() - now
    if seconds <= 0:
        return default
    return min(MAX_COOLDOWN, seconds)


def rate_floor():
    """VINTED_RATE_MIN – zemiausias greitis, kurio niekada nenusileidziam (bent 1)."""
    return max(1.0, float(config.cfg.get("VINTED_RATE_MIN") or 1))


class HostLimiter:
    """Vieno serverio uzklausu greitis: slankusis minutes langas + 429 atvesinimas."""

    def __init__(self, sleep=time.sleep, now=time.time, rate=None):
        self.sleep = sleep
        self.now = now
        # Issimoktas greitis (uzklausu per minute) arba None = imam is nustatymu.
        # Jei runner'io IP dalijamasi, tikroji riba zemesne nei nustatymuose – zr.
        # Run.learn_rate_limit: po 429 greitis mazinamas, po tyliu paleidimu grazinamas.
        self.rate = rate
        self._lock = threading.RLock()
        self._window = deque()          # uzklausu laikai, telpantys i paskutine minute
        self._blocked_until = 0.0
        self._blocked_reason = ""
        self.counts = {}                # {rusis: kiek uzklausu}
        self.hits = 0                   # kiek 429 atsakymu gavom
        self.paced = 0.0                # kiek sekundziu sulaikem, kad ribos neperzengtume
        self.skipped = 0                # kiek uzklausu nedarem, nes galiojo atvesinimas
        self.peak = 0                   # didziausias uzklausu kiekis minutes lange
        self.refused_at = None          # koks greitis buvo, kai serveris atsake 429

    # --- 429 -------------------------------------------------------------
    @property
    def blocked(self):
        """Netuscia, KOL galioja 429 atvesinimas."""
        with self._lock:
            if self._blocked_until and self.now() < self._blocked_until:
                return self._blocked_reason
            return ""

    def cooldown_left(self):
        """Kiek sekundziu liko iki atvesinimo pabaigos (0 – galima klausti)."""
        with self._lock:
            if not self._blocked_until:
                return 0.0
            return max(0.0, self._blocked_until - self.now())

    def note_rate_limit(self, response=None):
        """429: pazymim, kiek laiko nebeklausti. Gerbiam Retry-After, jei ji atsiuncia."""
        with self._lock:
            seconds = retry_after_seconds(response, config.cfg["VINTED_RATE_COOLDOWN"],
                                          now=self.now())
            self.hits += 1
            # TIK pratesiam. Persistatymas „nuo dabar“ reiktu, kad kiekviena atvesinimo metu
            # gauta 429 atidetu pabaiga dar minutei – ir taip be galo (zr. modulio aprasa).
            self._blocked_until = max(self._blocked_until, self.now() + seconds)
            left = self._blocked_until - self.now()
            self._slow_down()
            self._blocked_reason = f"HTTP 429 – per daug uzklausu, laukiu {left:.0f}s"
            return left

    # --- greitis ---------------------------------------------------------
    def take(self, kind="kita"):
        """Leidimas vienai uzklausai; sauktis PRIES kiekviena uzklausa i ta serveri.

        Grazina False, kai klausti negalima (galioja 429 atvesinimas) – tada uzklausos
        nedarom visai. Kitaip pasizymi vieta lange ir, jei ta vieta dar ne laisva,
        uzmiega iki jos.

        Vieta REZERVUOJAMA is karto (i langa rasomas busimas laikas), o miegama jau be
        spynos. Taip dvi gijos negali abi nuspresti, kad vieta laisva, ir vienos ilgas
        miegas nestabdo kitos, kuri tik pasiziuri, ar galioja atvesinimas.

        Pabudus atvesinimas tikrinamas DAR KARTA: kol si gija miegojo, kita galejo gauti
        429. Tada rezervacija atsaukiama ir uzklausa nedaroma – kitaip pabudusi gija
        isiustu uzklausa i jau galiojanti atvesinima ir ji tik prailgintu."""
        with self._lock:
            if self.blocked:
                self.skipped += 1
                return False
            now = self.now()
            self._trim(now)
            limit = self.limit()
            at = now
            if 0 < limit <= len(self._window):
                # Riba pilna: si uzklausa gali iseiti tik po WINDOW nuo tos, kuri uzima
                # paskutine vieta (0 = greicio neriboti, kaip iki v46).
                at = self._window[-limit] + WINDOW
            wait = max(0.0, at - now)
            slot = max(at, now)
            self._window.append(slot)
            self.paced += wait
        if wait > 0:
            self.sleep(wait)
        with self._lock:
            if wait > 0 and self.blocked:
                # Kol miegojom, atejo 429 (zr. docstring'a) – vieta atiduodama atgal.
                try:
                    self._window.remove(slot)
                except ValueError:
                    pass                # jau isvalyta _trim() – nieko neuzima
                self.skipped += 1
                return False
            self.counts[kind] = self.counts.get(kind, 0) + 1
            now = self.now()
            self.peak = max(self.peak, sum(1 for t in self._window if t <= now))
        return True

    def _slow_down(self):
        """Po 429 greitis mazinamas IS KARTO – likusiai paleidimo daliai, ne tik kitam.

        Kiekvienas GitHub Actions paleidimas gauna nauja runner'i (ir daznai kita IP), tad
        vien tarp paleidimu issimoktas greitis vėluotu: sio paleidimo riba jau perzengta,
        o atvesinimui pasibaigus vel leksim tuo paciu greiciu i ta pacia siena."""
        if not config.cfg.get("VINTED_RATE_ADAPT"):
            return
        current = self.limit()
        if current <= 0:
            return
        self.refused_at = current
        self.rate = max(rate_floor(), current * 0.7)

    def limit(self):
        """Siuo metu galiojantis greitis: issimoktas, o jei jo nera – is nustatymu.

        Nustatymuose nurodytas VINTED_MAX_PER_MINUTE yra LUBOS: mokomasi tik zemiau ju,
        tad issimoktas skaicius niekada neperzengia to, ka leido zmogus."""
        ceiling = int(config.cfg["VINTED_MAX_PER_MINUTE"] or 0)
        if ceiling <= 0:
            return 0                    # 0 = greicio neriboti (kaip iki v46)
        if self.rate:
            # Grindys taikomos ir cia, ne tik mokantis: state.json gali buti likes skaicius
            # is laiku, kai VINTED_RATE_MIN buvo mazesnis. Lubos svarbesnes uz grindis.
            return min(ceiling, max(int(rate_floor()), int(self.rate)))
        return ceiling

    def _trim(self, now):
        cutoff = now - WINDOW
        while self._window and self._window[0] <= cutoff:
            self._window.popleft()

    # --- statistika ------------------------------------------------------
    def stats(self):
        """Suvestine state.json („last_run“) – pagal ja matuojamos ribos."""
        with self._lock:
            return {"total": sum(self.counts.values()), "by_kind": dict(self.counts),
                    "peak_per_minute": self.peak, "paced_seconds": round(self.paced, 1),
                    "rate_limits": self.hits, "skipped": self.skipped, "limit": self.limit()}

    def report(self):
        """Viena eilute i log'a (tuscia, jei uzklausu nebuvo)."""
        with self._lock:
            total = sum(self.counts.values())
            if not total:
                return ""
            limit = self.limit()
            kinds = ", ".join(f"{LABELS.get(k, k)} {n}"
                              for k, n in sorted(self.counts.items(), key=lambda kv: -kv[1]))
            parts = [f"{total} ({kinds})",
                     f"piko greitis {self.peak}/min" + (f" is {limit}" if limit > 0 else "")]
            if self.paced >= 0.5:
                parts.append(f"sulaikyta {self.paced:.0f}s")
            if self.hits:
                parts.append(f"429: {self.hits}")
            if self.skipped:
                parts.append(f"atideta del ribos: {self.skipped}")
            return "Vinted uzklausos: " + ", ".join(parts)
