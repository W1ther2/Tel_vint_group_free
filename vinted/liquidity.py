# -*- coding: utf-8 -*-
"""Pardavimo FAKTAS: ar uz tokia kaina (palyginti su rinka) telefonas apskritai parduodamas.

Atskirta nuo kainos (vinted/confidence.py) samoningai. Vinted sandorio kainos nerodo, bet
pardavimo FAKTAS stebimas patikimai: puslapis sako „parduota“ (`sv=1`). Tai vienintelis
dalykas, kuri is Vinted galima ismatuoti be jokiu prielaidu apie kaina.

Kiekvienam skelbimui, kuriam zinoma musu rinkos kaina prasomu masteliu (`qa`):

    r = dabartine (ar uzsidarymo) prasoma kaina / qa

ir rezultatas per SALE_FACT_DAYS nuo pirmo pamatymo:
    1 – patvirtintas pardavimas per ta laika;
    0 – po tiek dienu vis dar parduodamas (per paskutine diena matytas kataloge arba
        pardavimu patikra puslapyje patvirtino „aktyvus“ – `ca`);
    nezinoma – dingo (404, galejo buti istrintas), ikeltas is naujo, dingo is akiraccio
               ar dar nepraejo tiek dienu. Tokie NESKAICIUOJAMI – kitaip „dingo“ vel
               taptu „parduota“ pro kitas duris.

Kol kas tik matuojama ir rodoma (log, `python -m vinted.dataset`) – sprendimams
nenaudojama, kol grupese nebus pakankamai (SALE_FACT_MIN_SAMPLES).
"""

from . import config
from .confidence import ask_quote, learnable

# Kainos / rinkos kainos grupes (kraštai – is pirmos analizes: < 0,95 parduodama gerokai
# dazniau nei > 1,20).
BUCKETS = ((0.0, 0.85, "<0,85"), (0.85, 0.95, "0,85–0,95"), (0.95, 1.05, "0,95–1,05"),
           (1.05, 1.20, "1,05–1,20"), (1.20, float("inf"), ">1,20"))


def bucket_of(r):
    for lo, hi, name in BUCKETS:
        if lo <= r < hi:
            return name
    return None


def outcome(e, day, window):
    """1 / 0 / None (nezinoma) – zr. modulio aprasa."""
    first = e.get("f")
    if first is None or day - first < window:
        return None
    st = e.get("st")
    if st == "sold":
        if e.get("sv") != 1:
            return None                          # dingo – ne faktas
        return 1 if e.get("sd", day) - first <= window else 0
    seen = max(e.get("l", -10**6), e.get("ca", -10**6))   # kataloge ar puslapyje
    if st == "active" and day - seen <= 1:
        return 0                                 # vis dar parduodamas
    return None


def learn(items, day, window=None, min_samples=None, min_sales=None):
    """{grupe: {"n", "sold", "rate", "enough"}} + {"day", "window", "unknown"}."""
    c = config.cfg
    window = int(window or c.get("SALE_FACT_DAYS", 7))
    min_samples = int(min_samples or c.get("SALE_FACT_MIN_SAMPLES", 30))
    if min_sales is None:
        min_sales = int(c.get("SALE_FACT_MIN_SALES", 5))
    groups = {name: [0, 0] for *_, name in BUCKETS}
    unknown = 0
    for e in items.values():
        if e.get("x"):
            continue
        qa = ask_quote(e)
        if not qa or not learnable(e):
            continue
        o = outcome(e, day, window)
        if o is None:
            unknown += 1
            continue
        g = groups[bucket_of(e["p"] / qa)]
        g[0] += 1
        g[1] += o
    # "enough" = ar grupes `rate` jau galima kuo nors tiketi.
    #
    # Pirma versija tikrino tik `n >= min_samples`, t. y. STEBEJIMU kieki. Gyvai
    # (2026-10-07) tai duodavo: "<0,85": n=37, sold=0, rate=0.0, enough=true. Bet
    # 0 pardavimu is 37 nereiskia „neparsiduoda" – reiskia „dar neturim ka
    # matuoti". Is viso visose grupese tuomet buvo 3 pardavimai, o tris ketvirtis
    # grupiu rodesi kaip „pakanka".
    #
    # Rodiklis, kuris remiasi santykiu, negali buti laikomas ismatuotu, kol nera
    # pakankamai PACIU IVYKIU: su 0 pardavimu rate=0 yra duomenu nebuvimas, o ne
    # nulinis pardavimas. Todel reikalaujam abieju – ir stebejimu, ir pardavimu.
    out = {name: {"n": n, "sold": s, "rate": round(s / n, 4) if n else None,
                  "enough": n >= min_samples and s >= min_sales}
           for name, (n, s) in groups.items()}
    out.update({"day": day, "window": window, "unknown": unknown})
    return out


def describe(fact):
    """Viena eilute log'ui."""
    parts = []
    for *_, name in BUCKETS:
        g = fact.get(name) or {}
        if g.get("n"):
            parts.append(f"{name}: {g['sold']}/{g['n']}" + ("" if g["enough"] else "*"))
    if not parts:
        return f"Pardavimo faktas (per {fact.get('window')} d.): dar nera duomenu"
    total = sum((fact.get(name) or {}).get("sold") or 0 for *_, name in BUCKETS)
    return (f"Pardavimo faktas (patvirtinta per {fact['window']} d., kaina/rinka): "
            + ", ".join(parts) + f"; nezinoma {fact['unknown']}"
            + f" (is viso {total} patvirtintu; * – dar nepakanka)")
