# -*- coding: utf-8 -*-
"""Rinkos kainos: prasomu kainu istorija, tikros pardavimo kainos, kainos sumazejimai.

Kiekvienas matytas telefonas saugomas state.json:
  {"m": modelis, "s": talpa, "p": dabartine kaina, "pp": ankstesne kaina,
   "f": pirma diena, "l": paskutine diena kataloge, "c": paskutinio patikrinimo diena,
   "st": "active"/"sold"/"gone", "sd": pardavimo diena, "a": kaina, uz kuria jau pranesta}
Dienos – sveikas skaicius (dienos nuo 1970-01-01).
"""

import functools
import threading
from dataclasses import dataclass

from . import config
from .phone import detect_model, extract_storage, is_accessory, find_defects, condition_ok, min_price, typical_price
from .util import today, median, percentile


def trimmed(values):
    """Atmeta isskirtis: pigiau nei pusė medianos (sugede/dalims) ar brangiau nei dviguba."""
    if len(values) < 3:
        return list(values)
    med = median(values)
    return [v for v in values if 0.5 * med <= v <= 2 * med]


@dataclass
class Quote:
    price: float
    samples: int          # -1 = rankine kaina (config / Telegram)
    source: str           # "rankinė" / "parduoti" / "skelbimai"
    by_storage: bool


@dataclass
class Rank:
    """Kur sis skelbimas stovi tarp siuo metu parduodamu tokiu pat telefonu."""
    place: int            # 1 = pigiausias
    n: int                # kiek is viso palyginta (iskaitant ji pati)
    low: float            # pigiausias tarp ju
    high: float           # brangiausias tarp ju
    share: float          # kokia dalis KITU yra pigesni (0.0 = pigiausias)
    by_storage: bool      # lyginta su ta pacia talpa ar su visu modeliu


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

    @locked
    def to_dict(self):
        return {"items": self.items, "calibration": self.calibration}

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
            if price < max(40, min_price(model)):          # dezutes, dalys, sugede – ne rinkos kaina
                continue
            if not condition_ok(l.condition, "Gera"):      # patenkinamos bukles – ne rinkos kaina
                continue
            iid = l.uid
            e = self.items.get(iid)
            if e is None:
                storage = extract_storage(title) or ""
                entry = {"m": model, "s": storage, "p": round(price, 2),
                         "f": day, "l": day, "c": day, "st": "active"}
                if l.source != "vinted":
                    # Vinted adresa galima atkurti is ID, kitiems saltiniams – ne
                    entry["u"] = l.url
                if (model, storage) not in quotes:
                    quotes[(model, storage)] = self.quote(model, storage or None, day)
                q = quotes[(model, storage)]
                if q is not None and q.price > 0:
                    entry["q"] = round(q.price, 2)               # ka spejom si telefona vertant
                    entry["qs"] = SOURCE_CODE.get(q.source, "?")
                    if entry["qs"] == "s":
                        # Koks pataisymas tuomet galiojo. Be sito nezinotume, kokia buvo
                        # "zalia" skelbimu kaina, ir kalibruotume nuo jau pataisyto skaiciaus –
                        # tas pats pardavimas pataisyma nustumtu kelis kartus is eiles.
                        entry["qf"] = round(self.sale_factor(), 4)
                self.items[iid] = entry
                continue
            if price < e["p"] - 0.01:
                drops[iid] = e["p"]
            if abs(price - e["p"]) > 0.01:
                e["pp"] = e["p"]
                e["p"] = round(price, 2)
            e["l"] = day
            if e.get("st") != "active":
                e["st"] = "active"
                e.pop("sd", None)
        return drops

    @locked
    def get(self, item_id):
        return self.items.get(with_source(str(item_id)))

    @locked
    def mark_alerted(self, item_id, price):
        e = self.items.get(with_source(str(item_id)))
        if e is not None:
            e["a"] = round(price, 2)

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
        """Aktyvus skelbimai, kuriu kataloge seniai nematem ir siandien netikrinom."""
        day = day if day is not None else today()
        after = config.cfg["SOLD_CHECK_AFTER_DAYS"]
        cands = [(e.get("c", 0), iid) for iid, e in self.items.items()
                 if e.get("st") == "active" and day - e.get("l", day) >= after and e.get("c", 0) < day]
        cands.sort()
        return [iid for _, iid in cands[: config.cfg["SOLD_CHECKS_PER_RUN"]]]

    @locked
    def set_status(self, item_id, status, day=None):
        day = day if day is not None else today()
        e = self.items.get(with_source(str(item_id)))
        if e is None:
            return
        e["c"] = day
        if status == "sold" or (status == "gone" and config.cfg["GONE_AS_SOLD"]):
            e["st"], e["sd"] = "sold", day
            # "sv" = ar tikrai parduotas (puslapis taip sako), ar tik dingo (galejo buti istrintas).
            # Tikslumo skaiciavimui pirmiausia naudojam patvirtintus.
            e["sv"] = 1 if status == "sold" else 0
        elif status == "gone":
            e["st"] = "gone"

    # --- rinkos kaina -------------------------------------------------------
    @locked
    def quote(self, model, storage, day=None):
        """Rinkos kaina. Pirmenybe: rankine > tikri pardavimai > prasomos kainos."""
        c = config.cfg
        day = day if day is not None else today()
        manual = config.market_prices()
        if storage and f"{model}|{storage}" in manual:
            return Quote(manual[f"{model}|{storage}"], -1, "rankinė", True)
        if model in manual:
            return Quote(manual[model], -1, "rankinė", False)

        def values(status, max_age, by_storage, day_key, max_life=None):
            out = []
            for e in self.items.values():
                if e["m"] != model or e.get("st") != status:
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
            for by_storage in ([True, False] if storage else [False]):
                sold = trimmed(values("sold", c["SOLD_HISTORY_DAYS"], by_storage, "sd"))
                if len(sold) >= c["MIN_SOLD_SAMPLES"]:
                    return Quote(median(sold), len(sold), "parduoti", by_storage)
        for by_storage in ([True, False] if storage else [False]):
            asking = trimmed(values("active", c["PRICE_HISTORY_DAYS"], by_storage, "l",
                                    max_life=c["ASKING_MAX_AGE_DAYS"]))
            if len(asking) >= c["MIN_SAMPLES"]:
                price = percentile(asking, c["MARKET_PERCENTILE"]) * self.sale_factor()
                return Quote(price, len(asking), "skelbimai", by_storage)
        # Retiems modeliams (16e, 14 Plus, Air...) skelbimu per mazai – naudojam apytiksle kaina,
        # o jei keli skelbimai jau yra – vidurki tarp ju ir apytiksles kainos.
        if c["USE_TYPICAL_FALLBACK"] and typical_price(model):
            asking = trimmed(values("active", c["PRICE_HISTORY_DAYS"], False, "l",
                                    max_life=c["ASKING_MAX_AGE_DAYS"]))
            guess = typical_price(model)
            if len(asking) >= 3:
                guess = (guess + percentile(asking, c["MARKET_PERCENTILE"]) * self.sale_factor()) / 2
            return Quote(guess, len(asking), "apytikslė", False)
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
                if uid == exclude or e["m"] != model or e.get("st") != "active":
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
                        share=cheaper / len(prices), by_storage=by_storage)
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
            if e.get("st") != "sold" or not e.get("q") or e.get("qs") != "s":
                continue
            if day - e.get("sd", day) > c["SOLD_HISTORY_DAYS"]:
                continue
            if confirmed_only and not e.get("sv"):
                continue
            factor = e["p"] * e["qf"] / e["q"] if e.get("qf") else None
            out.append((e["m"], e["p"] / e["q"], factor))
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
        data = self.accuracy(day)
        if data["n_target"] < c["MIN_CALIBRATION_SAMPLES"] or not data["target"]:
            return None
        wanted = data["target"] / c["ASKING_SALE_FACTOR"]      # koks pataisymas butu teisingas
        step = c["CALIBRATION_MAX_STEP"]
        target = max(self.calibration * (1 - step), min(self.calibration * (1 + step), wanted))
        target = max(c["CALIBRATION_MIN"], min(c["CALIBRATION_MAX"], target))
        old, self.calibration = self.calibration, round(target, 4)
        return {"old": old, "new": self.calibration, "wanted": round(wanted, 4), **data}

    @locked
    def sample_count(self, model):
        return sum(1 for e in self.items.values() if e["m"] == model and e.get("st") == "active")

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
            d = by_model.setdefault(e["m"], {"active": [], "sold": []})
            if (e.get("st") == "active" and day - e.get("l", day) <= c["PRICE_HISTORY_DAYS"]
                    and day - e.get("f", day) <= c["ASKING_MAX_AGE_DAYS"]):
                d["active"].append(e["p"])
            elif e.get("st") == "sold" and day - e.get("sd", day) <= c["SOLD_HISTORY_DAYS"]:
                d["sold"].append(e["p"])
        out = []
        for model, d in by_model.items():
            act, sold = trimmed(d["active"]), trimmed(d["sold"])
            out.append((model, percentile(act, c["MARKET_PERCENTILE"]) * self.sale_factor() if act else None,
                        len(act), median(sold) if sold else None, len(sold)))
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


def migrate_old_prices(old):
    """Senas prices.json formatas {"13|128 GB": {id: [kaina, diena]}} -> Market."""
    m = Market()
    for key, entries in (old or {}).items():
        if not isinstance(entries, dict) or "|" not in key:
            continue
        model, storage = key.split("|", 1)
        for iid, v in entries.items():
            try:
                price, day = float(v[0]), int(v[1])
            except (TypeError, ValueError, IndexError):
                continue
            iid = with_source(str(iid))
            e = m.items.get(iid)
            if e is None:
                m.items[iid] = {"m": model, "s": "" if storage == "*" else storage, "p": price,
                                "f": day, "l": day, "c": day, "st": "active"}
            elif storage != "*":
                e["s"] = storage
    return m
