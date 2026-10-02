# -*- coding: utf-8 -*-
"""Rinkos kainos tikslumo matavimas – is to paties principo, kuriuo remiasi confidence.

Kiekvienam parduotam telefonui, kuriam zinoma tuometine musu rinkos kaina:

    r = uzsidarymo kaina / musu rinkos kaina PRASOMU masteliu (confidence.ask_quote)

„Uzsidarymo kaina“ – paskutine prasoma. Sandorio kainos Vinted nerodo; uzsidarymo kaina –
jos VIRSUTINE riba (derybos ja tik sumazina). Kiekvienam patikimumo lygiui:

    factor = p20(r)          – 80 % skelbimu uzsidare ne pigiau nei musu kaina x factor.
                               VIENPUSE riba – mums svarbu, kad nepervertintume.
    band   = p80(|r - 1|)    – bendras neapibreztumas (kortelei: ±band).

Tas pats skaiciavimas naudojamas ir pradinems reiksmems (confidence.PRIOR), ir mokantis
is patvirtintu pardavimu (confidence.learn_levels) – kad prior ir learned butu palyginami.

Paleisti (busena is GitHub sakos `busena`):
    git fetch origin busena && git show FETCH_HEAD:state.json > /tmp/state.json
    python -m vinted.dataset /tmp/state.json config.json
"""

import json
import sys

from . import config
from .confidence import LEVELS, ask_quote, classify, learnable, quantile


def rows(items, level_of=None, confirmed_only=False):
    """[(lygis, r, patvirtintas)] parduotiems su zinoma tuometine kaina.

    Lygis – `qc` (uzrasytas tuo metu). Senesniems irasams jo nera; tada `level_of(e)`
    (pvz. pagal dabartine to modelio kaina) – tai APYTIKSLIS lygis, tinkamas tik pradinems
    reiksmems, ne mokymuisi."""
    out = []
    for e in items.values():
        if e.get("st") != "sold" or not e.get("q") or e.get("x") or not learnable(e):
            continue
        confirmed = e.get("sv") == 1
        if confirmed_only and not confirmed:
            continue
        level = e.get("qc") or (level_of(e) if level_of else None)
        qa = ask_quote(e)
        if level and qa:
            out.append((level, e["p"] / qa, confirmed))
    return out


# Lygiui, kuriam pavyzdziu maziau, prior'as imamas BENDRAS (visu lygiu) – skaicius is keliu
# tasku butu triukšmas, kuris atrodo kaip matavimas.
PRIOR_MIN_SAMPLES = 10


def pooled(samples):
    """Visu lygiu kartu (atsarginis prior'as lygiams su per maza imtimi)."""
    r = [x for _, x, _ in samples]
    if not r:
        return None
    return {"n": len(r), "confirmed": sum(1 for *_, ok in samples if ok),
            "factor": round(quantile(r, 0.2), 4),
            "band": round(quantile([abs(x - 1) for x in r], 0.8), 4),
            "median": round(quantile(r, 0.5), 4)}


def priors(samples):
    """{lygis: prior} – savo, jei imties >= PRIOR_MIN_SAMPLES, kitaip bendras (pazymetas)."""
    own, pool = summarize(samples), pooled(samples)
    out = {}
    for code in LEVELS:
        s = own.get(code)
        if s and s["n"] >= PRIOR_MIN_SAMPLES:
            out[code] = {**s, "pooled": False}
        elif pool:
            out[code] = {**pool, "pooled": True, "own_n": s["n"] if s else 0}
    return out


def summarize(samples):
    """{lygis: {"n", "confirmed", "factor", "band", "median"}} – kiekvienam lygiui."""
    out = {}
    for code in LEVELS:
        r = [x for c, x, _ in samples if c == code]
        if not r:
            continue
        out[code] = {"n": len(r), "confirmed": sum(1 for c, _, ok in samples if c == code and ok),
                     "factor": round(quantile(r, 0.2), 4),
                     "band": round(quantile([abs(x - 1) for x in r], 0.8), 4),
                     "median": round(quantile(r, 0.5), 4)}
    return out


def main(argv=None):
    import contextlib
    import io
    from .market import Market
    argv = sys.argv[1:] if argv is None else argv
    state_path = argv[0] if argv else config.STATE_FILE
    with contextlib.redirect_stdout(io.StringIO()):
        config.load(argv[1] if len(argv) > 1 else config.CONFIG_FILE)
    with open(state_path, encoding="utf-8") as f:
        data = json.load(f)
    market = Market(data.get("market"))
    day = max((e.get("l", 0) for e in market.items.values()), default=0)
    cache = {}

    def current_level(e):
        key = (e["m"], e.get("s") or None)
        if key not in cache:
            q = market.quote(*key, day)
            cache[key] = classify(q)[0] if q else None
        return cache[key]

    samples = rows(market.items, level_of=current_level)
    with_qc = sum(1 for e in market.items.values() if e.get("qc"))
    manual = sum(1 for e in market.items.values() if e.get("st") == "sold" and e.get("q")
                 and not e.get("x") and not learnable(e))
    print(f"{state_path}: {len(market.items)} irasu, su tuometiniu lygiu (qc) {with_qc}; "
          f"parduotu su kaina {len(samples)}, is ju patvirtintu {sum(1 for *_, ok in samples if ok)} "
          f"(rankiniu kainu atmesta: {manual})")
    print("Lygis senesniems irasams – APYTIKSLIS (dabartine to modelio kaina).\n")
    print(f"{'lygis':10} {'n':>4} {'patv.':>5} {'p20(r)':>7} {'mediana':>8} {'p80|r-1|':>9}")
    for code, s in summarize(samples).items():
        print(f"{LEVELS[code]:10} {s['n']:4d} {s['confirmed']:5d} {s['factor']:7.3f} "
              f"{s['median']:8.3f} {s['band']:9.1%}")
    pool = pooled(samples)
    if pool:
        print(f"{'visi':10} {pool['n']:4d} {pool['confirmed']:5d} {pool['factor']:7.3f} "
              f"{pool['median']:8.3f} {pool['band']:9.1%}")
    print(f"\nPrior'ai (lygiui su < {PRIOR_MIN_SAMPLES} pavyzdziu – bendras):")
    for code, p in priors(samples).items():
        src = f"BENDRAS (savo n={p['own_n']})" if p["pooled"] else "savo"
        print(f"  {LEVELS[code]:10} factor {p['factor']:.3f}  band {p['band']:.3f}  n={p['n']}  {src}")
    print("\nr = uzsidarymo (paskutine prasoma) / musu kaina prasomu masteliu – sandorio kainos "
          "Vinted nerodo,\ntad tai jos VIRSUTINE riba.\n")
    from .liquidity import describe, learn
    print(describe(learn(market.items, day)))


if __name__ == "__main__":
    main()
