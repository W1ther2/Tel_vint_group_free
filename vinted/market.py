# -*- coding: utf-8 -*-
"""Rinkos kainos: prasomu kainu istorija, tikros pardavimo kainos, kainos sumazejimai.

Kiekvienas matytas telefonas saugomas state.json:
  {"m": modelis, "s": talpa, "p": dabartine kaina, "pp": ankstesne kaina,
   "f": pirma diena, "l": paskutine diena kataloge, "c": paskutinio patikrinimo diena,
   "st": "active"/"sold"/"gone", "sd": pardavimo diena, "a": kaina, uz kuria jau pranesta,
   "ev": kaina, uz kuria paskutini karta vertinom, "sh": pardavejo maisos kodas,
   "x": kodel netinka rinkos kainai (uzrakintas, ne telefonas...), "rl": 1 = dingo, nes ikeltas is naujo}
Dienos – sveikas skaicius (dienos nuo 1970-01-01).
"""

import functools
import hashlib
import statistics
import threading
import time
from dataclasses import dataclass

from . import config
from .phone import detect_model, extract_storage, is_accessory, find_defects, condition_ok, min_price, typical_price
from . import liquidity
from .confidence import ask_quote, assess, learn_levels
from .util import today, median, percentile


def trimmed(values):
    """Atmeta isskirtis: pigiau nei pusė medianos (sugede/dalims) ar brangiau nei dviguba."""
    if len(values) < 3:
        return list(values)
    med = median(values)
    return [v for v in values if 0.5 * med <= v <= 2 * med]


@dataclass
class Quote:
    """Rinkos kaina. DU masteliai (v49):

    ask   – PRASOMU kainu masteliu: tiek, kiek panasus telefonai prasomi / uz kiek ju
            skelbimai uzsidaro. Tai, ka is tikruju matom Vinted.
    price – VERTE = ask x sale_factor (ASKING_SALE_FACTOR). Sandorio kainos Vinted nerodo,
            tad sis daugiklis yra NEPATIKRINTA PRIELAIDA, o ne ismatuotas dydis.

    Iki v49 „parduoti“ saltinis grazindavo prasomu masteli (dingusiu skelbimu paskutines
    prasomos), o „skelbimai“ – jau padaugintą is 0,85: ta pati rinka gaudavo ~x1,17
    skirtinga kaina priklausomai nuo saltinio (gyvai: iPhone 13 174 € vs 157 €)."""
    price: float
    samples: int          # -1 = rankine kaina (config / Telegram)
    source: str           # "rankinė" / "parduoti" / "skelbimai" / "apytikslė"
    by_storage: bool
    # Kainu sklaida imtyje (standartinis nuokrypis / mediana). Gyvai matuota: kai sklaida
    # < 0,15, 80 % rinkos kainos klaidu neviršija 7 %, o kai > 0,25 – siekia 33 %.
    # None – sklaidos nezinom (rankine kaina, per maza imtis). Zr. vinted/confidence.py.
    spread: float = None
    ask: float = None     # prasomu masteliu (zr. aukščiau); None – senas objektas

    def __post_init__(self):
        if self.ask is None and self.price:
            self.ask = self.price / sale_factor_assumption()


def sale_factor_assumption():
    """Prasoma -> sandorio kaina. NEPATIKRINTA prielaida (Vinted sandorio kainos nerodo)."""
    return float(config.cfg["ASKING_SALE_FACTOR"])


def spread_of(values):
    """Sklaida = standartinis nuokrypis / mediana (None, kai reiksmiu maziau nei 3)."""
    if len(values) < 3:
        return None
    med = median(values)
    return round(statistics.pstdev(values) / med, 4) if med else None


@dataclass
class Rank:
    """Kur sis skelbimas stovi tarp siuo metu parduodamu tokiu pat telefonu."""
    place: int            # 1 = pigiausias
    n: int                # kiek is viso palyginta (iskaitant ji pati)
    low: float            # pigiausias tarp ju
    high: float           # brangiausias tarp ju
    share: float          # kokia dalis KITU yra pigesni (0.0 = pigiausias)
    by_storage: bool      # lyginta su ta pacia talpa ar su visu modeliu
    peer_low: float = None  # pigiausias tarp KITU (be sio) – ar sis ne itartinai pigesnis


def seller_hash(source, seller_id):
    """Pardavejo ID saugom tik kaip trumpa maisos koda: pakanka atpazinti ta pati
    pardaveja (pakartotinai ikeltas skelbimas), bet paties ID state.json nelieka."""
    if not seller_id:
        return None
    return hashlib.sha1(f"{source}|{seller_id}".encode()).hexdigest()[:10]


def with_source(key):
    """Seni irasai buvo raktais be saltinio ('123') – dabar visi 'vinted:123'."""
    return key if ":" in key else "vinted:" + key


# Kaip buvo gauta kaina, kuria spejom tam telefonui ji pirma karta pamate.
# Kalibruojam tik pagal "s" – tik prasomu kainu vertinimas turi sistemine paklaida.
SOURCE_CODE = {"rankinė": "m", "parduoti": "d", "skelbimai": "s", "apytikslė": "t"}


def locked(method):
    """Metodai, lieciantys bendra skelbimu zodyna, vykdomi po vieną – saltiniai
    gali suktis lygiagreciai."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return wrapper


class Market:
    def __init__(self, data=None):
        data = data or {}
        items = data.get("items", {}) if isinstance(data, dict) else {}
        self.items = {with_source(str(k)): v for k, v in items.items()}
        # Saltiniai gali suktis lygiagreciai: vienas rasos nauja skelbima, kitas tuo metu
        # skaiciuoja rinkos kaina. Be spynos Python mestu "dictionary changed size".
        self.lock = threading.RLock()
        # Kiek musu vertinimas nukrypsta nuo realiu pardavimu (1.0 = nekoreguojam).
        try:
            self.calibration = float(data.get("calibration") or 1.0)
        except (TypeError, ValueError):
            self.calibration = 1.0
        # Kalibruojama ne dazniau nei karta per diena (paleidimai – kas 10 min.)
        self.calibrated_day = data.get("calibrated_day")
        # Patikimumo lygiu reiksmes, ismoktos is PATVIRTINTU pardavimu (confidence.learn_levels).
        # Kol ju nera – naudojamos pradines (confidence.PRIOR).
        self.confidence_bands = data.get("confidence_bands") or {}
        # Pardavimo faktas pagal kaina/rinka (vinted/liquidity.py) – tik matavimas.
        self.sale_fact = data.get("sale_fact") or {}
        # Ilgalaikio archyvo ivykiai siame paleidime (zr. vinted/archive.py). I state.json
        # nerasomi – paleidimo gale perkeliami i archyva.
        self.events = []

    @locked
    def to_dict(self):
        return {"items": self.items, "calibration": self.calibration, "calibrated_day": self.calibrated_day,
                "confidence_bands": self.confidence_bands, "sale_fact": self.sale_fact}

    # --- archyvas -------------------------------------------------------------
    def _event(self, kind, iid, day, **fields):
        """Vienas archyvo ivykis (kvieciama jau su spyna)."""
        event = {"t": int(time.time()), "d": day, "e": kind, "id": iid}
        event.update({k: v for k, v in fields.items() if v is not None})
        self.events.append(event)

    @locked
    def drain_events(self):
        """Siame paleidime surinkti ivykiai archyvui (ir isvalo ju sarasa)."""
        out, self.events = self.events, []
        return out

    def sale_factor(self):
        """Prasoma kaina -> reali pardavimo kaina, patikslinta pagal tikrus pardavimus."""
        return config.cfg["ASKING_SALE_FACTOR"] * self.calibration

    # --- stebejimas -------------------------------------------------------
    @locked
    def observe(self, listings, day=None):
        """Uzraso kataloge matytu telefonu kainas. Grazina {uid: ankstesne_kaina}
        tiems, kurie atpigo.

        Naujam telefonui isaugom ir savo tuometini vertinima ("q") – veliau, kai jis
        bus parduotas, galesim palyginti, kiek spejom ir kiek gavom is tikruju."""
        day = day if day is not None else today()
        drops, quotes = {}, {}
        for l in listings:
            # Aukcione kaina reiskia dabartini pasiulyma, dalyse – detales kaina,
            # rezervuotas nebeparduodamas. Tokie skaiciai rinkos kainos nerodo.
            if l.skip_reason:
                continue
            title = l.title or ""
            model = detect_model(title)
            price = l.price
            if not model or price is None or is_accessory(title) or find_defects(title):
                continue
            # Zemiau ribos krentancios kainos i rinkos statistika NEITRAUKIAMOS, bet
            # nuo v49.2 jos UZRASOMOS i archyva su priezastimi, o ne tyliai dingsta.
            #
            # Kodel tai svarbu: riba (`min_price`) yra tas pats skaicius, kuri norim
            # patikrinti matuodami. Kol filtruodavom RASANT, Pixel 7 atveju po 45 EUR
            # nebelikdavo nieko, tad klausimo „ar 45 buvo teisinga riba" nebuvo kaip
            # uzduoti – irodymai buvo ismetami prie duru. Dabar filtras taikomas
            # SKAITANT (`values()`, `peers()` ignoruoja `x`), o archyve lieka viskas.
            if price < max(40, min_price(model)):          # dezutes, dalys, sugede – ne rinkos kaina
                self._event("skip", l.uid, day, why="below_floor", src=l.source,
                            m=model, p=round(price, 2), fl=round(min_price(model), 2))
                continue
            if not condition_ok(l.condition, "Gera"):      # patenkinamos bukles – ne rinkos kaina
                continue
            iid = l.uid
            # Skelbimas, kurio pardavejo salies nustatyti nepavyko, i Lietuvos rinkos kaina
            # neitraukiamas, kol salis nepatvirtinta (`confirm_country`). Gyvai matyta, kad
            # „iphone“ sarase Lietuvos yra tik ~11 %, tad nepatikrintas skelbimas dazniausiai
            # yra uzsienio. Irasa vis tiek saugom: kitaip nebematytume atpigimu.
            unverified = "salis?" if l.country_unverified else None
            e = self.items.get(iid)
            if e is None:
                storage = extract_storage(title) or ""
                entry = {"m": model, "s": storage, "p": round(price, 2),
                         "f": day, "l": day, "c": day, "st": "active"}
                if unverified:
                    entry["x"] = unverified
                sh = seller_hash(l.source, l.seller_id)
                if sh:
                    entry["sh"] = sh
                if l.source != "vinted":
                    # Vinted adresa galima atkurti is ID, kitiems saltiniams – ne
                    entry["u"] = l.url
                if (model, storage) not in quotes:
                    quotes[(model, storage)] = self.quote(model, storage or None, day)
                q = quotes[(model, storage)]
                conf = None
                if q is not None and q.price > 0:
                    entry["q"] = round(q.price, 2)               # ka spejom si telefona vertant
                    entry["qs"] = SOURCE_CODE.get(q.source, "?")
                    # Ta pati kaina PRASOMU masteliu – su ja lyginama uzsidarymo kaina (v49).
                    entry["qa"] = round(q.ask, 2)
                    # Patikimumas TUO METU: veliau, kai pardavimas bus PATVIRTINTAS, is to
                    # mokomasi, kiek kiekvieno lygio kaina is tikruju pasiteisina (learn_levels).
                    conf = assess(q, self.confidence_bands)
                    entry["qc"] = conf.code
                    if entry["qs"] == "s":
                        # Koks pataisymas tuomet galiojo. Be sito nezinotume, kokia buvo
                        # "zalia" skelbimu kaina, ir kalibruotume nuo jau pataisyto skaiciaus –
                        # tas pats pardavimas pataisyma nustumtu kelis kartus is eiles.
                        entry["qf"] = round(self.sale_factor(), 4)
                self.items[iid] = entry
                self._event("obs", iid, day, src=l.source, m=model, s=storage or None,
                            p=entry["p"], q=entry.get("q"), qa=entry.get("qa"), qs=entry.get("qs"),
                            qn=q.samples if q is not None else None,
                            qsp=q.spread if q is not None else None,
                            qc=entry.get("qc"), qb=conf.band if conf else None,
                            qsf=conf.factor if conf else None,
                            cu=1 if unverified else None, sh=entry.get("sh"),
                            u=entry.get("u"), dt=l.created_at)
                continue
            if not unverified and e.get("x") == "salis?":
                e.pop("x", None)              # salis paaiskejo (pvz. is pardaveju atminties)
            # Atpigimas skaiciuojamas nuo kainos, uz kuria paskutini karta VERTINOM – kitaip
            # 300 -> 290 -> 280 -> 270 (kiekviena karta < 5 %) niekada nebutu pastebeta.
            ref = e.get("ev") or e["p"]
            if price < ref - 0.01:
                drops[iid] = ref
            if abs(price - e["p"]) > 0.01:
                e["pp"] = e["p"]
                e["p"] = round(price, 2)
                self._event("price", iid, day, p=e["p"], pp=e["pp"])
            e["l"] = day
            if e.get("st") != "active":
                self._event("status", iid, day, st="active", was=e.get("st"))
                e["st"] = "active"
                e.pop("sd", None)
        return drops

    @locked
    def get(self, item_id):
        return self.items.get(with_source(str(item_id)))

    @locked
    def mark_alerted(self, item_id, price):
        iid = with_source(str(item_id))
        e = self.items.get(iid)
        if e is not None:
            e["a"] = round(price, 2)
            self._event("alert", iid, today(), p=e["a"], q=e.get("q"), qc=e.get("qc"))

    @locked
    def mark_evaluated(self, item_id, price):
        e = self.items.get(with_source(str(item_id)))
        if e is not None and price is not None:
            e["ev"] = round(price, 2)

    @locked
    def exclude(self, item_id, reason):
        """Skelbimas netinka rinkos kainai (uzrakintas, sugedes, ne telefonas, uzsienio).
        Jis nebeskaiciuojamas nei i vieta tarp pigiausiu, nei i rinkos kaina."""
        iid = with_source(str(item_id))
        e = self.items.get(iid)
        if e is not None:
            if e.get("x") != reason and reason != "salis?":
                self._event("x", iid, today(), x=reason)
            e["x"] = reason

    @locked
    def needs_country(self, item_id):
        """True, jei irasas laukia salies patvirtinimo (kol kas neskaiciuojamas i rinka)."""
        e = self.items.get(with_source(str(item_id)))
        return bool(e) and e.get("x") == "salis?"

    @locked
    def confirm_country(self, item_id):
        """Salis patikrinta ir tinka – irasas vel skaiciuojamas i rinkos kaina."""
        e = self.items.get(with_source(str(item_id)))
        if e is not None and e.get("x") == "salis?":
            e.pop("x", None)

    @locked
    def already_alerted_at(self, item_id, price):
        """True, jei apie si skelbima jau pranesta uz panasia ar mazesne kaina."""
        e = self.items.get(with_source(str(item_id)))
        if not e or not e.get("a"):
            return False
        return price >= e["a"] * (1 - config.cfg["PRICE_DROP_MIN"])

    # --- pardavimu tikrinimas ---------------------------------------------
    @locked
    def sold_check_candidates(self, day=None):
        """Aktyvus skelbimai, kuriu kataloge nematem bent SOLD_CHECK_AFTER_DAYS ir siandien
        netikrinom.

        Tvarka – del pardavimu TIESOS. Gyvai (2026-10-04) is ~2300 „parduotu“ tik 4 Vinted
        tikrai parode „parduota“: eile eidavo nuo SENIAUSIU (5000+ nematytu 10+ d.), tad
        skelbimas buvo tikrinamas tik po 4–15 d., kai puslapio jau nebuvo (404 = „dingo“,
        galejo buti ir istrintas; is 121 tokio patikrinimo – 0 „parduota“). Todel:
        1. pirmiau NESENIAI dinge (nematyti <= SOLD_CHECK_FRESH_DAYS): ju puslapis dar gali
           rodyti „parduota“; tarp ju – su zinomu patikimumo lygiu (`qc`, is ju mokosi
           confidence modelis), tada nesenausiai dinge;
        2. „vis dar parduodamas“ (puslapis sake `active` – dazniausiai tiesiog nuslinko is
           perziuretu puslapiu) is naujo – ne anksciau nei po SOLD_RECHECK_ACTIVE_DAYS;
        3. seni (ilgai nematyti) – tik jei lieka vietos, kaip anksciau: `qc`, ilgiausiai
           netikrinti, neseniausiai dinge.
        Užklausu skaicius nesikeicia (SOLD_CHECKS_PER_RUN). Tą pačią dieną dingimas nieko
        nereiskia – tad tikrinama ne anksciau nei kita diena."""
        c = config.cfg
        day = day if day is not None else today()
        after = max(1, int(c["SOLD_CHECK_AFTER_DAYS"]))
        fresh_days = max(after, int(c["SOLD_CHECK_FRESH_DAYS"]))
        recheck = max(1, int(c["SOLD_RECHECK_ACTIVE_DAYS"]))
        cands = []
        for iid, e in self.items.items():
            if e.get("st") != "active" or e.get("x"):
                continue
            unseen = day - e.get("l", day)
            if unseen < after or e.get("c", 0) >= day:
                continue
            if "ca" in e and day - e["ca"] < recheck:
                continue
            no_qc = 0 if e.get("qc") else 1
            if unseen <= fresh_days:
                cands.append((0, no_qc, unseen, e.get("c", 0), iid))
            else:
                cands.append((1, no_qc, e.get("c", 0), -e.get("l", 0), iid))
        cands.sort()
        return [x[-1] for x in cands[: c["SOLD_CHECKS_PER_RUN"]]]

    @locked
    def set_status(self, item_id, status, day=None):
        day = day if day is not None else today()
        iid = with_source(str(item_id))
        e = self.items.get(iid)
        if e is None:
            return
        e["c"] = day
        if status == "active":
            # Puslapis sako „vis dar parduodamas“. Kataloge jo gal ir nebematom (botas mato tik
            # naujausius puslapius), bet pardavimo faktui (liquidity) tai – zinoma baigtis.
            e["ca"] = day
        relist = status == "gone" and self._relisted(item_id, e)
        if status not in ("active", "unknown"):
            # Archyve – tai, ka pasake puslapis, ne musu isvada (GONE_AS_SOLD). Kitaip
            # „dingo“ ir „tikrai parduota“ susilietu, o butent ju skirtumas svarbiausias.
            self._event("status", iid, day, st="relist" if relist else status, p=e.get("p"),
                        age=day - e.get("f", day), unseen=day - e.get("l", day))
        if relist:
            # Tas pats pardavejas ikele ta pati telefona is naujo (Vinted daznai taip „pakelia“
            # skelbima). Tai ne pardavimas – kitaip prasoma kaina patektu i „parduotu“ kainas.
            e["st"], e["rl"] = "gone", 1
            e.pop("sd", None)
            e.pop("sv", None)
            return
        if status == "sold" or (status == "gone" and config.cfg["GONE_AS_SOLD"]):
            e["st"], e["sd"] = "sold", day
            # "sv" = ar tikrai parduotas (puslapis taip sako), ar tik dingo (galejo buti istrintas).
            # Tikslumo skaiciavimui pirmiausia naudojam patvirtintus.
            e["sv"] = 1 if status == "sold" else 0
        elif status == "gone":
            e["st"] = "gone"

    def _relisted(self, item_id, e):
        """Ar yra naujesnis to paties pardavejo aktyvus skelbimas: tas pats modelis,
        talpa ir panasi kaina (+-20 %)."""
        sh = e.get("sh")
        if not sh:
            return False
        uid = with_source(str(item_id))
        for oid, o in self.items.items():
            if oid == uid or o.get("sh") != sh or o.get("st") != "active":
                continue
            if o.get("m") == e.get("m") and o.get("s") == e.get("s") and o.get("f", 0) >= e.get("f", 0) \
                    and abs(o["p"] - e["p"]) <= 0.2 * e["p"]:
                return True
        return False

    # --- rinkos kaina -------------------------------------------------------
    @locked
    def quote(self, model, storage, day=None, manual=True):
        """Rinkos kaina. Pirmenybe: rankine > tikri pardavimai > prasomos kainos.
        manual=False – tik is duomenu (rankines kainos patikrai)."""
        c = config.cfg
        day = day if day is not None else today()
        prices = config.market_prices() if manual else {}
        # Rankine kaina – zmogaus nurodyta VERTE (tuo paciu masteliu kaip `price`).
        sf = self.sale_factor()
        if storage and f"{model}|{storage}" in prices:
            v = prices[f"{model}|{storage}"]
            return Quote(v, -1, "rankinė", True, None, v / sf)
        if model in prices:
            return Quote(prices[model], -1, "rankinė", False, None, prices[model] / sf)

        def values(status, max_age, by_storage, day_key, max_life=None, confirmed=False):
            out = []
            for e in self.items.values():
                if e["m"] != model or e.get("st") != status or e.get("x"):
                    continue
                if confirmed and not e.get("sv"):
                    continue
                if by_storage and e.get("s") != storage:
                    continue
                if day - e.get(day_key, day) > max_age:
                    continue
                # Ilgai kabantis skelbimas nepasiduoda = kaina per didele rinkai
                if max_life is not None and day - e.get("f", day) > max_life:
                    continue
                out.append(e["p"])
            return out

        if c["USE_SOLD_PRICES"]:
            # Pirmiausia – patvirtinti pardavimai (puslapis sako „parduota“). Dinge skelbimai
            # (GONE_AS_SOLD) – tik kai patvirtintu per mazai: dalis ju buvo tiesiog istrinti.
            for by_storage in ([True, False] if storage else [False]):
                for confirmed in (True, False):
                    sold = trimmed(values("sold", c["SOLD_HISTORY_DAYS"], by_storage, "sd",
                                          confirmed=confirmed))
                    if len(sold) >= c["MIN_SOLD_SAMPLES"]:
                        # Uzsidariusiu skelbimu PASKUTINES PRASOMOS kainos – ne sandorio. Todel
                        # verte, kaip ir is skelbimu, = prasoma x prielaida (iki v49 – be jos).
                        ask = median(sold)
                        # MARKET_SCALE_UNIFIED=false (numatyta): gyva verte kaip iki v49 – be
                        # daugiklio. Matavimui ir mokymui vis tiek naudojamas `ask`.
                        value = ask * sf if c["MARKET_SCALE_UNIFIED"] else ask
                        return Quote(value, len(sold), "parduoti", by_storage, spread_of(sold), ask)
        for by_storage in ([True, False] if storage else [False]):
            asking = trimmed(values("active", c["PRICE_HISTORY_DAYS"], by_storage, "l",
                                    max_life=c["ASKING_MAX_AGE_DAYS"]))
            if len(asking) >= c["MIN_SAMPLES"]:
                ask = percentile(asking, c["MARKET_PERCENTILE"])
                return Quote(ask * sf, len(asking), "skelbimai", by_storage, spread_of(asking), ask)
        # Retiems modeliams (16e, 14 Plus, Air...) skelbimu per mazai – naudojam apytiksle kaina,
        # o jei keli skelbimai jau yra – vidurki tarp ju ir apytiksles kainos.
        if c["USE_TYPICAL_FALLBACK"] and typical_price(model):
            asking = trimmed(values("active", c["PRICE_HISTORY_DAYS"], False, "l",
                                    max_life=c["ASKING_MAX_AGE_DAYS"]))
            guess = typical_price(model)
            if len(asking) >= 3:
                guess = (guess + percentile(asking, c["MARKET_PERCENTILE"]) * self.sale_factor()) / 2
            # Lentele (v45) = percentile(prasomos) x ASKING_SALE_FACTOR, t. y. jau verte.
            return Quote(guess, len(asking), "apytikslė", False, spread_of(asking), guess / sf)
        return None

    # --- vieta tarp siuo metu parduodamu --------------------------------------
    @locked
    def rank(self, model, storage, price, exclude=None, day=None):
        """Kur si kaina stovi tarp siuo metu aktyviu to paties modelio skelbimu.

        Tai nepriklauso nuo rinkos kainos spejimo: nesvarbu, ar mediana teisinga,
        pigiausi 15% dabartiniu skelbimu vis tiek yra pigiausi 15%.
        Grazina Rank arba None, jei palyginti per mazai (tada naudojamas senasis budas)."""
        c = config.cfg
        day = day if day is not None else today()

        def peers(by_storage):
            out = []
            for uid, e in self.items.items():
                if uid == exclude or e["m"] != model or e.get("st") != "active" or e.get("x"):
                    continue
                if by_storage and e.get("s") != storage:
                    continue
                # "Dabar parduodamas" = matytas kataloge neseniai. Anksciau cia buvo 30 d.,
                # ir i palyginima patekdavo jau parduoti telefonai – nupirkti negalima,
                # o vietos skaiciavima iskreipia (pvz. „3-as pigiausias is 231“).
                if day - e.get("l", day) > c["RANK_RECENT_DAYS"]:
                    continue
                # Ilgai kabantys – per brangus rinkai, su jais lyginant viskas atrodytu pigu
                if day - e.get("f", day) > c["ASKING_MAX_AGE_DAYS"]:
                    continue
                out.append(e["p"])
            return trimmed(out)

        for by_storage in ([True, False] if storage else [False]):
            prices = peers(by_storage)
            if len(prices) < c["RANK_MIN_PEERS"]:
                continue
            cheaper = sum(1 for p in prices if p < price - 0.01)
            everyone = prices + [price]
            return Rank(place=cheaper + 1, n=len(everyone), low=min(everyone), high=max(everyone),
                        share=cheaper / len(prices), by_storage=by_storage, peer_low=min(prices))
        return None

    # --- tikslumas ir savikalibracija -----------------------------------------
    @locked
    def _accuracy_samples(self, day, confirmed_only):
        """[(modelis, reali/spejta, koks daugiklis butu buves teisingas)].

        Antrasis skaicius – kiek teko nuleisti musu vertinima; trecias – koks
        prasoma->parduota daugiklis butu tam telefonui tikes (None seniems irasams)."""
        c = config.cfg
        out = []
        for e in self.items.values():
            if e.get("st") != "sold" or not e.get("q") or e.get("qs") != "s" or e.get("x"):
                continue
            if day - e.get("sd", day) > c["SOLD_HISTORY_DAYS"]:
                continue
            if confirmed_only and not e.get("sv"):
                continue
            # v49: abu PRASOMU masteliu (uzsidarymo kaina / musu prasoma rinkos kaina).
            # Anksciau p/q lygino prasoma su jau padauginta is 0,85 – santykis issipusdavo ~x1,18.
            qa = ask_quote(e)
            factor = e["p"] * e["qf"] / e["q"] if e.get("qf") else None
            out.append((e["m"], e["p"] / qa, factor))
        return out

    @locked
    def accuracy(self, day=None):
        """Kiek musu vertinimas atitiko realia pardavimo kaina.

        Grazina {"n", "ratio", "confirmed", "rows", "target", "n_target"}.
        ratio < 1 = pervertinam (spejam brangiau, nei realiai parduota).
        target = koks prasoma->parduota daugiklis butu buves teisingas."""
        day = day if day is not None else today()
        confirmed = True
        samples = self._accuracy_samples(day, confirmed_only=True)
        if len(samples) < config.cfg["MIN_CALIBRATION_SAMPLES"]:
            # Patvirtintu "parduota" dar per mazai – imam ir tuos, kurie tiesiog dingo.
            all_samples = self._accuracy_samples(day, confirmed_only=False)
            if len(all_samples) > len(samples):
                samples, confirmed = all_samples, False
        by_model = {}
        for model, ratio, _ in samples:
            by_model.setdefault(model, []).append(ratio)
        rows = sorted(((m, len(v), median(v)) for m, v in by_model.items()), key=lambda r: -r[1])
        factors = [f for _, _, f in samples if f]
        return {"n": len(samples), "ratio": median([r for _, r, _ in samples]) if samples else None,
                "confirmed": confirmed, "rows": rows,
                "target": median(factors) if factors else None, "n_target": len(factors)}

    @locked
    def calibrate(self, day=None):
        """Patikslina vertinima pagal tai, kiek realiai gauta uz parduotus telefonus.

        Skaiciuojam absoliutu taikini – koks prasoma->parduota daugiklis butu buves
        teisingas parduotiems telefonams – ir prie jo einam ne didesniais nei
        CALIBRATION_MAX_STEP zingsniais. Taip tas pats pardavimas nestumia pataisymo
        kelis kartus is eiles (nuo to vertinimas persisverdavo i kita puse).
        Grazina pakeitimo aprasa arba None, jei duomenu dar per mazai."""
        c = config.cfg
        if not c["AUTO_CALIBRATE"]:
            return None
        day = day if day is not None else today()
        data = self.accuracy(day)
        if data["n_target"] < c["MIN_CALIBRATION_SAMPLES"] or not data["target"]:
            return None
        if self.calibrated_day == day:
            return None           # siandien jau zengta – CALIBRATION_MAX_STEP galioja per diena
        self.calibrated_day = day
        wanted = data["target"] / c["ASKING_SALE_FACTOR"]      # koks pataisymas butu teisingas
        step = c["CALIBRATION_MAX_STEP"]
        target = max(self.calibration * (1 - step), min(self.calibration * (1 + step), wanted))
        target = max(c["CALIBRATION_MIN"], min(c["CALIBRATION_MAX"], target))
        old, self.calibration = self.calibration, round(target, 4)
        return {"old": old, "new": self.calibration, "wanted": round(wanted, 4), **data}

    @locked
    def learn_confidence(self, day=None):
        """Patikimumo lygiu paklaidos is parduotu (karta per diena). Grazina naujas."""
        day = day if day is not None else today()
        if self.confidence_bands.get("day") == day:
            return None
        self.confidence_bands = learn_levels(self.items, day)
        return self.confidence_bands

    @locked
    def learn_sale_fact(self, day=None):
        """Pardavimo faktas (vinted/liquidity.py) – karta per diena, SAVO dienos zyma.

        Iki v49.1 jis buvo skaiciuojamas learn_confidence() viduje, uz jos „jau siandien“
        patikros: v47/v48 ta zyma siandienai jau buvo irase, tad v49 fakto neskaiciavo
        (gyvai: sale_fact = {}). Atskira zyma – kad vieno mokymo busena neuzblokuotu kito."""
        day = day if day is not None else today()
        if self.sale_fact.get("day") == day:
            return None
        self.sale_fact = liquidity.learn(self.items, day)
        return self.sale_fact

    @locked
    def sample_count(self, model):
        return sum(1 for e in self.items.values()
                   if e["m"] == model and e.get("st") == "active" and not e.get("x"))

    @locked
    def price_check(self, model, storage, price, day=None):
        """Kiek procentu kaina pigesne uz rinkos kaina (naudinga /kaina patikrai)."""
        q = self.quote(model, storage, day)
        return None if not q else (q, 1 - price / q.price)

    @locked
    def summary(self, day=None):
        """[(modelis, prasoma_kaina|None, n_skelb, parduota_kaina|None, n_parduota)]"""
        c = config.cfg
        day = day if day is not None else today()
        by_model = {}
        for e in self.items.values():
            if e.get("x"):
                continue
            d = by_model.setdefault(e["m"], {"active": [], "sold": []})
            if (e.get("st") == "active" and day - e.get("l", day) <= c["PRICE_HISTORY_DAYS"]
                    and day - e.get("f", day) <= c["ASKING_MAX_AGE_DAYS"]):
                d["active"].append(e["p"])
            elif e.get("st") == "sold" and day - e.get("sd", day) <= c["SOLD_HISTORY_DAYS"]:
                d["sold"].append(e["p"])
        out = []
        for model, d in by_model.items():
            act, sold = trimmed(d["active"]), trimmed(d["sold"])
            # Su MARKET_SCALE_UNIFIED abu stulpeliai – verte (prasoma x prielaida).
            sold_value = (median(sold) * (self.sale_factor() if c["MARKET_SCALE_UNIFIED"] else 1)
                          if sold else None)
            out.append((model, percentile(act, c["MARKET_PERCENTILE"]) * self.sale_factor() if act else None,
                        len(act), sold_value, len(sold)))
        return out

    # --- valymas --------------------------------------------------------------
    @locked
    def prune(self, day=None):
        c = config.cfg
        day = day if day is not None else today()
        keep = {}
        for iid, e in self.items.items():
            st = e.get("st")
            if st == "sold" and day - e.get("sd", day) > c["SOLD_HISTORY_DAYS"]:
                continue
            if st != "sold" and day - e.get("l", day) > c["PRICE_HISTORY_DAYS"]:
                continue
            keep[iid] = e
        if len(keep) > c["PRICE_HISTORY_MAX_ITEMS"]:
            ranked = sorted(keep.items(), key=lambda kv: (kv[1].get("st") == "sold", kv[1].get("l", 0)),
                            reverse=True)
            keep = dict(ranked[: c["PRICE_HISTORY_MAX_ITEMS"]])
        self.items = keep

