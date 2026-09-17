# -*- coding: utf-8 -*-
"""Rinkos kainos: prasomu kainu istorija, tikros pardavimo kainos, kainos sumazejimai.

Kiekvienas matytas telefonas saugomas state.json:
  {"m": modelis, "s": talpa, "p": dabartine kaina, "pp": ankstesne kaina,
   "f": pirma diena, "l": paskutine diena kataloge, "c": paskutinio patikrinimo diena,
   "st": "active"/"sold"/"gone", "sd": pardavimo diena, "a": kaina, uz kuria jau pranesta}
Dienos – sveikas skaicius (dienos nuo 1970-01-01).
"""

from dataclasses import dataclass

from . import config
from .phone import detect_model, extract_storage, is_accessory, find_defects, condition_ok, min_price, typical_price
from .parsing import get_condition
from .parsing import get_price
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


class Market:
    def __init__(self, data=None):
        data = data or {}
        self.items = data.get("items", {}) if isinstance(data, dict) else {}

    def to_dict(self):
        return {"items": self.items}

    # --- stebejimas -------------------------------------------------------
    def observe(self, items, day=None):
        """Uzraso kataloge matytu telefonu kainas. Grazina {id: ankstesne_kaina}
        tiems, kurie atpigo."""
        day = day if day is not None else today()
        drops = {}
        for it in items:
            if not isinstance(it, dict) or not it.get("id"):
                continue
            title = it.get("title") or ""
            model = detect_model(title)
            price = get_price(it)
            if not model or price is None or is_accessory(title) or find_defects(title):
                continue
            if price < max(40, min_price(model)):          # dezutes, dalys, sugede – ne rinkos kaina
                continue
            if not condition_ok(get_condition(it), "Gera"):   # patenkinamos bukles – ne rinkos kaina
                continue
            iid = str(it["id"])
            e = self.items.get(iid)
            if e is None:
                self.items[iid] = {"m": model, "s": extract_storage(title) or "", "p": round(price, 2),
                                   "f": day, "l": day, "c": day, "st": "active"}
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

    def get(self, item_id):
        return self.items.get(str(item_id))

    def mark_alerted(self, item_id, price):
        e = self.items.get(str(item_id))
        if e is not None:
            e["a"] = round(price, 2)

    def already_alerted_at(self, item_id, price):
        """True, jei apie si skelbima jau pranesta uz panasia ar mazesne kaina."""
        e = self.items.get(str(item_id))
        if not e or not e.get("a"):
            return False
        return price >= e["a"] * (1 - config.cfg["PRICE_DROP_MIN"])

    # --- pardavimu tikrinimas ---------------------------------------------
    def sold_check_candidates(self, day=None):
        """Aktyvus skelbimai, kuriu kataloge seniai nematem ir siandien netikrinom."""
        day = day if day is not None else today()
        after = config.cfg["SOLD_CHECK_AFTER_DAYS"]
        cands = [(e.get("c", 0), iid) for iid, e in self.items.items()
                 if e.get("st") == "active" and day - e.get("l", day) >= after and e.get("c", 0) < day]
        cands.sort()
        return [iid for _, iid in cands[: config.cfg["SOLD_CHECKS_PER_RUN"]]]

    def set_status(self, item_id, status, day=None):
        day = day if day is not None else today()
        e = self.items.get(str(item_id))
        if e is None:
            return
        e["c"] = day
        if status == "sold":
            e["st"], e["sd"] = "sold", day
        elif status == "gone":
            e["st"] = "gone"

    # --- rinkos kaina -------------------------------------------------------
    def quote(self, model, storage, day=None):
        """Rinkos kaina. Pirmenybe: rankine > tikri pardavimai > prasomos kainos."""
        c = config.cfg
        day = day if day is not None else today()
        manual = config.market_prices()
        if storage and f"{model}|{storage}" in manual:
            return Quote(manual[f"{model}|{storage}"], -1, "rankinė", True)
        if model in manual:
            return Quote(manual[model], -1, "rankinė", False)

        def values(status, max_age, by_storage, day_key):
            return [e["p"] for e in self.items.values()
                    if e["m"] == model and e.get("st") == status
                    and (not by_storage or e.get("s") == storage)
                    and day - e.get(day_key, day) <= max_age]

        if c["USE_SOLD_PRICES"]:
            for by_storage in ([True, False] if storage else [False]):
                sold = trimmed(values("sold", c["SOLD_HISTORY_DAYS"], by_storage, "sd"))
                if len(sold) >= c["MIN_SOLD_SAMPLES"]:
                    return Quote(median(sold), len(sold), "parduoti", by_storage)
        for by_storage in ([True, False] if storage else [False]):
            asking = trimmed(values("active", c["PRICE_HISTORY_DAYS"], by_storage, "l"))
            if len(asking) >= c["MIN_SAMPLES"]:
                return Quote(percentile(asking, c["MARKET_PERCENTILE"]), len(asking), "skelbimai", by_storage)
        # Retiems modeliams (16e, 14 Plus, Air...) skelbimu per mazai – naudojam apytiksle kaina,
        # o jei keli skelbimai jau yra – vidurki tarp ju ir apytiksles kainos.
        if c["USE_TYPICAL_FALLBACK"] and typical_price(model):
            asking = trimmed(values("active", c["PRICE_HISTORY_DAYS"], False, "l"))
            guess = typical_price(model)
            if len(asking) >= 3:
                guess = (guess + percentile(asking, c["MARKET_PERCENTILE"])) / 2
            return Quote(guess, len(asking), "apytikslė", False)
        return None

    def sample_count(self, model):
        return sum(1 for e in self.items.values() if e["m"] == model and e.get("st") == "active")

    def summary(self, day=None):
        """[(modelis, prasoma_kaina|None, n_skelb, parduota_kaina|None, n_parduota)]"""
        c = config.cfg
        day = day if day is not None else today()
        by_model = {}
        for e in self.items.values():
            d = by_model.setdefault(e["m"], {"active": [], "sold": []})
            if e.get("st") == "active" and day - e.get("l", day) <= c["PRICE_HISTORY_DAYS"]:
                d["active"].append(e["p"])
            elif e.get("st") == "sold" and day - e.get("sd", day) <= c["SOLD_HISTORY_DAYS"]:
                d["sold"].append(e["p"])
        out = []
        for model, d in by_model.items():
            act, sold = trimmed(d["active"]), trimmed(d["sold"])
            out.append((model, percentile(act, c["MARKET_PERCENTILE"]) if act else None,
                        len(act), median(sold) if sold else None, len(sold)))
        return out

    # --- valymas --------------------------------------------------------------
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
            e = m.items.get(iid)
            if e is None:
                m.items[iid] = {"m": model, "s": "" if storage == "*" else storage, "p": price,
                                "f": day, "l": day, "c": day, "st": "active"}
            elif storage != "*":
                e["s"] = storage
    return m
