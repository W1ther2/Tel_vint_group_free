# -*- coding: utf-8 -*-
"""Telegram komandos nustatymams keisti. Pakeitimai saugomi state.json (overrides)."""

import re

from . import config
from .phone import normalize_model_name, normalize_storage, MODEL_ORDER

HELP = """<b>Komandos</b>
/kaina 13 180 – iPhone 13 rinkos kaina 180 €
/kaina 13 Pro Max 256 390 – konkrečiai talpai
/kaina 13 trinti – grąžinti automatinę kainą
/kainos – visos rinkos kainos
/nuolaida 20 – siųsti nuo 20% pigiau nei vertė
/baterija 80 – min. baterija (0 – netikrinti)
/garsas 30 – su garsu tik nuo 30% pigiau
/pauze – nesiųsti skelbimų, /testi – vėl siųsti
/nustatymai – dabartiniai nustatymai
<i>Komandos įvykdomos kito paleidimo metu.</i>"""


def _percent(arg):
    v = float(arg.replace("%", "").replace(",", "."))
    return v / 100 if v > 1 else v


def _set(state, key, value):
    state.overrides[key] = value
    config.apply_overrides({key: value})


def _prices_text(state):
    manual = config.market_prices()
    rows = {m: (a, n, s, ns) for m, a, n, s, ns in state.market.summary()}
    lines = ["<b>Rinkos kainos</b> (rankinė / parduotų / skelbimų)"]
    for model in MODEL_ORDER:
        man = manual.get(model)
        a, n, s, ns = rows.get(model, (None, 0, None, 0))
        extra_manual = [f"{k.split('|')[1]}: {v:.0f} €" for k, v in manual.items() if k.startswith(model + "|")]
        if not (man or a or s or extra_manual):
            continue
        parts = []
        if man:
            parts.append(f"✏️ {man:.0f} €")
        parts += [f"✏️ {x}" for x in extra_manual]
        if s:
            parts.append(f"✅ {s:.0f} € ({ns})")
        if a:
            parts.append(f"🏷 {a:.0f} € ({n})")
        lines.append(f"iPhone {model}: " + " · ".join(parts))
    return "\n".join(lines) if len(lines) > 1 else "Kainų duomenų dar nėra."


def handle(text, state):
    """Ivykdo komanda. Grazina atsakymo teksta (HTML) arba None, jei komanda nezinoma."""
    text = text.strip()
    m = re.match(r"^/(\w+)(?:@\w+)?\s*(.*)$", text, re.S)
    if not m:
        return None
    cmd, args = m.group(1).lower(), m.group(2).strip()
    c = config.cfg
    try:
        if cmd in ("pagalba", "help", "start"):
            return HELP

        if cmd == "kaina":
            parts = args.split()
            if len(parts) < 2:
                return "Naudojimas: /kaina 13 180 arba /kaina 13 Pro 256 250"
            value = parts[-1].lower()
            storage = normalize_storage(parts[-2]) if len(parts) >= 3 else None
            model_text = " ".join(parts[:-2] if storage else parts[:-1])
            model = normalize_model_name(model_text)
            if not model:
                return f"Nežinomas modelis: {model_text}"
            key = f"{model}|{storage}" if storage else model
            name = f"iPhone {model}" + (f" {storage}" if storage else "")
            if value in ("trinti", "-", "0", "auto"):
                prices = dict(state.overrides.get("MARKET_PRICES") or {})
                prices[key] = None
                _set(state, "MARKET_PRICES", prices)
                return f"✅ {name}: rinkos kaina vėl skaičiuojama automatiškai"
            price = float(value.replace("€", "").replace(",", "."))
            if price <= 0:
                raise ValueError
            prices = dict(state.overrides.get("MARKET_PRICES") or {})
            prices[key] = price
            _set(state, "MARKET_PRICES", prices)
            return f"✅ {name}: rinkos kaina {price:.0f} €"

        if cmd == "kainos":
            return _prices_text(state)

        if cmd == "nuolaida":
            v = _percent(args)
            if not 0 < v < 0.9:
                raise ValueError
            _set(state, "MIN_DISCOUNT", v)
            return f"✅ Siųsiu skelbimus nuo {v:.0%} pigiau nei vertė"

        if cmd == "baterija":
            v = int(float(args.replace("%", "")))
            if not 0 <= v <= 100:
                raise ValueError
            _set(state, "MIN_BATTERY", v)
            return "✅ Baterija netikrinama" if v == 0 else f"✅ Min. baterija: {v}%"

        if cmd == "garsas":
            v = _percent(args)
            _set(state, "LOUD_DISCOUNT", v)
            return f"✅ Su garsu – nuo {v:.0%} pigiau, kiti tyliai"

        if cmd in ("pauze", "pauzė"):
            _set(state, "PAUSED", True)
            return "⏸ Skelbimai nesiunčiami. /testi – vėl įjungti"

        if cmd in ("testi", "tęsti"):
            _set(state, "PAUSED", False)
            return "▶️ Skelbimai vėl siunčiami"

        if cmd == "nustatymai":
            manual = config.market_prices()
            return ("<b>Nustatymai</b>\n"
                    f"Min. nuolaida: {c['MIN_DISCOUNT']:.0%}\n"
                    f"Su garsu nuo: {c['LOUD_DISCOUNT']:.0%}\n"
                    f"Min. baterija: {c['MIN_BATTERY'] or 'netikrinama'}\n"
                    f"Pauzė: {'taip' if c.get('PAUSED') else 'ne'}\n"
                    f"Rankinės kainos: {', '.join(f'{k} = {v:.0f} €' for k, v in manual.items()) or 'nėra'}")
    except (ValueError, IndexError):
        return f"Neteisinga reikšmė: {text}\n\n{HELP}"
    return None
