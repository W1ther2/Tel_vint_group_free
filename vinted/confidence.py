# -*- coding: utf-8 -*-
"""Kiek galima pasitiketi rinkos kaina – lygis, atsargus koeficientas ir neapibreztumas.

Rinkos kaina (market.quote) visada buvo vienas skaicius, nors jos patikimumas labai skiriasi:
iPhone 13 su 590 skelbimu ir Galaxy Z Fold 5 is apytiksles lenteles atrode vienodai tvirti.
v45 parode, kuo tai baigiasi: Android lentele buvo iki 50 % per auksta, ir normaliai
ikainotas telefonas atrodydavo kaip nuolaida.

Kiekvienam lygiui (aukštas / vidutinis / žemas – zr. `classify`) du skaiciai, abu is

    r = UZSIDARYMO kaina / musu rinkos kaina PRASOMU masteliu (Quote.ask, irase `qa`)

„Uzsidarymo kaina“ – paskutine prasoma kaina skelbimo, kuri Vinted pazymejo „parduota“.
Tai NE sandorio kaina (jos Vinted nerodo), o jos VIRSUTINE riba: derybos ja gali tik
sumazinti. Todel factor yra veikiau per optimistiškas nei per griežtas. Abi kainos – tuo
paciu masteliu (iki v49 prasoma buvo lyginama su jau padauginta is 0,85).

    factor = p20(r)         ATSARGI verte = verte x factor. Vienpuse riba: 80 % skelbimu
                            uzsidare ne pigiau. Klaidos asimetriskos (dazniau pervertinam),
                            tad simetrine |klaida| siam tikslui netinka.
    band   = p80(|r - 1|)   tik kortelei: „±30 %“.

Pardavimo FAKTAS (ar uz tokia kaina apskritai parduodama) – atskirai, vinted/liquidity.py.

PRADINES reiksmes (PRIOR) – ne isgalvotos, o apskaiciuotos `python -m vinted.dataset` is
gyvos busenos 2026-10-01 (6455 irasai, 54 parduoti su tuometine kaina, tik 4 patvirtinti).
Svarbu zinoti, kokios jos silpnos:
  - lygis siems irasams APYTIKSLIS (tuometinio `qc` nebuvo – priskirtas pagal dabartine
    to modelio kaina);
  - „pardavimas“ dazniausiai = skelbimas dingo (gali buti ir istrintas);
  - aukštas lygis – tik 5 pavyzdziai.
Todel tai PRADINIS sluoksnis, o ne ismokytas modelis. Ismokta reiksme lygiui atsiranda
tik kai sukaupiama CONFIDENCE_MIN_SAMPLES PATVIRTINTU („parduota“) pardavimu su tuo lygiu,
uzrasytu TUO METU (`qc`) – zr. `learn_levels`. Dingusiu (404) mokymuisi neimam.

Atranka (CONFIDENCE_STRICT) – tik nuolaidos budu (reti modeliai, Android): ten sprendima
lemia TIK rinkos kaina. „Pigiausiu“ budo (DEAL_MODE=rank) logika nekeiciama.
"""

import math
from dataclasses import dataclass

from . import config

LEVELS = {"h": "aukštas", "m": "vidutinis", "l": "žemas"}

# python -m vinted.dataset, busena 2026-10-01 17:52 UTC (zr. aukščiau, kokios silpnos),
# v49: nearest-rank, abi kainos prasomu masteliu, be rankiniu kainu (ju mastelis nezinomas):
#   lygis       n  patv.  p20(r)  mediana  p80|r-1|
#   vidutinis  32    1    0.785    0.949     30.0 %
#   žemas      15    3    0.758    0.955     24.2 %
#   visi       47    4    0.776    0.955     28.1 %
# „Aukštas“ – 0 pavyzdziu (iki v49 jo 5 buvo VIENA rankine kaina), tad imamas BENDRAS:
# kol nera irodymu, kad aukštas lygis tikslesnis, jam nesuteikiama nuolaida.
# r = uzsidarymo (paskutine prasoma) / musu kaina prasomu masteliu; ne sandorio kaina.
PRIOR = {
    "h": {"factor": 0.776, "band": 0.281, "n": 0, "pooled": True},
    "m": {"factor": 0.785, "band": 0.300, "n": 32},
    "l": {"factor": 0.758, "band": 0.242, "n": 15},
}
PRIOR_DATE = "2026-10-01"

# Lygiu ribos (imtis ir kainu sklaida) – is to paties matavimo: sklaida < 0,15 -> p80
# klaida 7 %, > 0,25 -> 33 %. Segmentavimas (modelis, talpa...) – tik kai bus duomenu.
HIGH_SAMPLES, HIGH_SPREAD = 30, 0.15
MID_SAMPLES, MID_SPREAD = 10, 0.25

# Ismokta reiksme ribojama: kelios keistos imtys neturi paversti vertes x0,1 ar x1,5.
MIN_FACTOR, MAX_FACTOR = 0.40, 1.00
MIN_BAND, MAX_BAND = 0.04, 0.60


def learnable(e):
    """Ar is sio iraso galima mokytis musu RINKOS IVERCIO tikslumo.

    Rankine kaina (qs="m") – ne musu ivertis, o zmogaus skaicius, kurio mastelis nezinomas
    (gyvai: „iPhone 13 = 180 €“, o uzsidarymo kainos 174–209 € – panasiau i prasoma nei i
    verte). Iki v49 butent penki tokie irasai sudare VISA „aukšto“ lygio pradine reiksme."""
    return e.get("qs") != "m"


def ask_quote(e):
    """Musu rinkos kaina PRASOMU masteliu irasui (v49 – `qa`; senesniems – isvedama).

    Senuose irasuose `q` mastelis priklausė nuo saltinio: „parduoti“ (d) – prasomas,
    „skelbimai“ (s) – x qf (tuometinis daugiklis), „apytikslė“ (t) ir „rankinė“ (m) – verte,
    t. y. x ASKING_SALE_FACTOR. None – kainos nera."""
    if e.get("qa"):
        return float(e["qa"])
    q = e.get("q")
    if not q:
        return None
    qs = e.get("qs")
    if qs == "d":
        return float(q)
    if qs == "s":
        return float(q) / float(e.get("qf") or config.cfg["ASKING_SALE_FACTOR"])
    return float(q) / float(config.cfg["ASKING_SALE_FACTOR"])


def quantile(values, p):
    """Nearest-rank kvantilis: maziausia reiksme, kurios nesieke ne daugiau kaip p dalis imties
    (rangas ceil(p*n), skaiciuojant nuo 1). Be interpoliacijos – tas pats prior'ams ir mokymuisi.

    Iki v48.1 buvo `int(p*n)` (skaiciuojant nuo 0): kai p*n sveikasis, imdavo VIENA reiksme
    per auksta (n=10, p=0.2 -> 3-ias elementas vietoj 2-ojo). Epsilonas – del slankiojo kablelio:
    0.2*15 ar 0.7*10 gali buti 3.0000000000000004 ir „pakelti“ ranga."""
    values = sorted(values)
    rank = math.ceil(p * len(values) - 1e-9)
    return values[min(len(values), max(1, rank)) - 1]


@dataclass(frozen=True)
class Confidence:
    code: str        # "h" / "m" / "l" – state.json ir archyvui
    factor: float    # atsargi verte = rinkos kaina x factor (p20 pardavimo/musu kainos)
    band: float      # bendras neapibreztumas kortelei (p80 |r - 1|)
    reason: str      # kodel toks lygis – zmogui
    learned: bool    # is patvirtintu pardavimu (True) ar pradine reiksme (False)
    samples: int     # is kiek pavyzdziu gauta

    @property
    def level(self):
        return LEVELS[self.code]


def classify(quote):
    """(lygio kodas, priezastis) – tik is to, kaip kaina gauta."""
    src, n, spread = quote.source, quote.samples, quote.spread
    if src == "apytikslė":
        return "l", "apytikslė lentelė, mažai skelbimų"
    if src == "rankinė":
        return "m", "nustatyta ranka"
    what = "parduotų" if src == "parduoti" else "skelb."
    if spread is None:
        return "l", f"{n} {what}, sklaida nežinoma"
    if n >= HIGH_SAMPLES and spread < HIGH_SPREAD:
        return "h", f"{n} {what}, kainos panašios"
    if n >= MID_SAMPLES and spread < MID_SPREAD:
        return "m", f"{n} {what}"
    if n < MID_SAMPLES:
        return "l", f"tik {n} {what}"
    return "l", f"{n} {what}, kainos labai skiriasi"


def assess(quote, learned=None):
    """Rinkos kainos patikimumas. `learned` – Market.confidence_bands (learn_levels)."""
    code, reason = classify(quote)
    entry = (learned or {}).get(code) or {}
    if entry.get("factor"):
        return Confidence(code, float(entry["factor"]), float(entry.get("band") or PRIOR[code]["band"]),
                          reason, True, int(entry.get("n") or 0))
    p = PRIOR[code]
    return Confidence(code, p["factor"], p["band"], reason, False, p["n"])


def pessimistic(value, conf):
    """Atsargi verte: tokios ar didesnes kainos uzsidare 80 % skelbimu (siam lygiui).

    Niekada ne didesne uz pacia verte – jei pardavimai buvo brangesni nei musu kaina,
    tai nereiskia, kad galim reikalauti mazesnes nuolaidos."""
    if value is None or conf is None or not config.cfg.get("CONFIDENCE_STRICT", True):
        return value
    return value * min(1.0, conf.factor)


def progress(items):
    """{lygis: kiek PATVIRTINTU pardavimu su tuometiniu lygiu jau sukaupta} – log'ui."""
    out = dict.fromkeys(LEVELS, 0)
    for e in items.values():
        if e.get("st") == "sold" and e.get("sv") == 1 and e.get("q") and e.get("qc") in out \
                and not e.get("x"):
            out[e["qc"]] += 1
    return out


def learn_levels(items, day=None, min_samples=None):
    """Lygio reiksmes is PATVIRTINTU pardavimu su `qc` (tuo metu uzrasytu lygiu).

    Tik `sv == 1` – Vinted puslapis sake „parduota“. Dingusiu (404) neimam: jie galejo
    buti ir istrinti, o butent del tokios silpnos tiesos v47 negalejo nieko ismokti.
    Grazina {kodas: {"factor", "band", "n"}} tik lygiams, kuriu imties pakanka."""
    min_samples = min_samples or int(config.cfg.get("CONFIDENCE_MIN_SAMPLES", 20))
    out = {}
    for code in LEVELS:
        r = [e["p"] / ask_quote(e) for e in items.values()
             if e.get("st") == "sold" and e.get("sv") == 1 and e.get("qc") == code
             and ask_quote(e) and not e.get("x") and learnable(e)]
        if len(r) < min_samples:
            continue
        out[code] = {
            "factor": round(max(MIN_FACTOR, min(MAX_FACTOR, quantile(r, 0.2))), 4),
            "band": round(max(MIN_BAND, min(MAX_BAND, quantile([abs(x - 1) for x in r], 0.8))), 4),
            "n": len(r)}
    if day is not None:
        out["day"] = day
    return out
