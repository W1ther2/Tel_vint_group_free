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
/rinka 50 – rinkos kaina = mediana (35 – pigesnis trečdalis, griežčiau)
/pelnas ne – nerodyti galimo pelno kortelėje
/tvarkingi taip|ne – tik tvarkingi telefonai
/pauze – nesiųsti skelbimų, /testi – vėl siųsti
/statistika – kodėl atmesti skelbimai (paskutinis paleidimas)
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


TIPS = {
    "per brangu": "normalu – kaina ne žemiau rinkos. Daugiau skelbimų: /nuolaida 10",
    "ne pakankamai pigu": "pigiau už rinką, bet mažiau nei nuolaida. Daugiau: /nuolaida 10",
    "per mazai kainu duomenu": "modeliui dar trūksta kainų – kaupsis savaime arba /kaina 13 180",
    "ne telefonas / kitas modelis": "dėklai, stiklai, kiti modeliai – normalu",
    "defektai": "sugedę telefonai. Siųsti ir juos: /tvarkingi ne",
    "kalba": "užsienio kalba – normalu",
    "salis": "pardavėjas ne iš Lietuvos",
    "pardavejas": "per mažas pardavėjo įvertinimas (config.json MIN_SELLER_RATING)",
}


def _stats_text(state):
    r = state.last_run or {}
    if not r:
        return "Statistikos dar nėra – bus po kito paleidimo."
    import time as _t
    mins = int((_t.time() - r.get("time", 0)) // 60)
    totals = sorted((r.get("totals") or {}).items(), key=lambda kv: -kv[1])
    lines = [f"<b>Paskutinis paleidimas</b> (prieš {mins} min.)",
             f"Gauta: {r.get('fetched', 0)}, naujų: {r.get('new', 0)}, išsiųsta: {r.get('sent', 0)}", ""]
    for reason, n in totals[:10]:
        tip = next((v for k, v in TIPS.items() if reason.startswith(k)), "")
        lines.append(f"• {reason}: <b>{n}</b>" + (f"\n   <i>{tip}</i>" if tip else ""))
    return "\n".join(lines)


def is_admin(user_id):
    admins = [str(x) for x in (config.cfg.get("ADMIN_IDS") or [])]
    return str(user_id) in admins


def handle_callback(cb, state):
    """Mygtuko paspaudimas. Atsakyma mato tik paspaudes vartotojas."""
    data = cb.get("data") or ""
    user = state.user(cb.get("user"), cb.get("name"))
    kind, _, value = data.partition("|")
    if not value:
        return "Nežinomas mygtukas"

    if kind == "w":                                   # sekti modeli
        if value in user["watch"]:
            user["watch"].remove(value)
            return f"🔕 Nebesiųsiu asmeniškai apie iPhone {value}"
        user["watch"].append(value)
        if user.get("chat"):
            return f"🔔 Siųsiu tau asmeniškai apie kiekvieną iPhone {value} sandorį"
        return (f"🔔 Įsiminta: iPhone {value}. Kad gautum žinutes asmeniškai, "
                "parašyk man privačiai /start")

    if kind == "h":                                   # slepti pardaveja
        if value in user["hide"]:
            user["hide"].remove(value)
            return "👁 Šio pardavėjo skelbimai vėl bus siunčiami"
        user["hide"].append(value)
        return "🙈 Šio pardavėjo skelbimų tau asmeniškai nebesiųsiu"

    return "Nežinomas mygtukas"


PRIVATE_HELP = """👋 Sveikas! Čia gali gauti skelbimus asmeniškai.

Grupėje po kortele spausk 🔔 <b>Sekti šį modelį</b> – tuos skelbimus siųsiu tau čia.

/mano – ką seki
/stop – nebesiųsti asmeniškai"""


def handle_private(msg, state):
    """Komandos privačiame pokalbyje su botu (kiekvienam vartotojui atskirai).
    Administratoriui cia veikia ir visos nustatymu komandos."""
    text = msg.get("text", "").strip()
    cmd = re.sub(r"^/(\w+).*", r"\1", text.split("@")[0]).lower()
    user = state.user(msg.get("user"), msg.get("name"))
    if cmd in ("start", "pagalba", "help"):
        user["chat"] = msg.get("chat")
        extra = "\n\n<b>Administratoriaus komandos</b>\n" + HELP if is_admin(msg.get("user")) else ""
        return f"{PRIVATE_HELP}\n\n<i>Tavo ID: {msg.get('user')}</i>{extra}"
    if is_admin(msg.get("user")) and cmd not in ("mano", "stop"):
        reply = handle(text, state)     # nustatymu komandos – tik privaciai ir tik adminui
        if reply:
            return reply
    if cmd == "mano":
        watch = ", ".join(f"iPhone {m}" for m in user["watch"]) or "nieko"
        return (f"<b>Tavo nustatymai</b>\nSeki: {watch}\n"
                f"Asmeninės žinutės: {'įjungtos' if user.get('chat') else 'išjungtos (/start)'}")
    if cmd == "stop":
        user["chat"] = None
        return "⏹ Asmeniškai nebesiųsiu. Įjungti – /start"
    return PRIVATE_HELP


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

        if cmd in ("statistika", "stats"):
            return _stats_text(state)

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

        if cmd == "pelnas":
            v = args.lower() not in ("ne", "no", "0", "off", "isjungti", "nerodyti")
            _set(state, "SHOW_PROFIT", v)
            return "✅ Rodysiu galimą pelną" if v else "✅ Galimo pelno eilutės nebebus"

        if cmd == "rinka":
            v = _percent(args)
            if not 0.1 <= v <= 0.9:
                raise ValueError
            _set(state, "MARKET_PERCENTILE", v)
            return (f"✅ Rinkos kaina skaičiuojama kaip {v:.0%} percentilis "
                    f"({'mediana' if abs(v - 0.5) < 0.01 else 'pigesnė dalis' if v < 0.5 else 'brangesnė dalis'})")

        if cmd == "garsas":
            v = _percent(args)
            _set(state, "LOUD_DISCOUNT", v)
            return f"✅ Su garsu – nuo {v:.0%} pigiau, kiti tyliai"

        if cmd == "tvarkingi":
            v = args.lower() not in ("ne", "no", "0", "off", "isjungti")
            _set(state, "TIDY_ONLY", v)
            return ("✅ Siųsiu tik tvarkingus telefonus (be defektų, būklė nuo "
                    f"„{c['MIN_CONDITION']}“)" if v else "✅ Siųsiu ir su defektais (pažymėtus ⚠️)")

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
                    f"Rinkos kaina: {c['MARKET_PERCENTILE']:.0%} percentilis\n"
                    f"Su garsu nuo: {c['LOUD_DISCOUNT']:.0%}\n"
                    f"Min. baterija: {c['MIN_BATTERY'] or 'netikrinama'}\n"
                    f"Tik tvarkingi: {'taip' if c.get('TIDY_ONLY') else 'ne'}\n"
                    f"Rodyti pelną: {'taip' if c.get('SHOW_PROFIT') else 'ne'}\n"
                    f"Pauzė: {'taip' if c.get('PAUSED') else 'ne'}\n"
                    f"Rankinės kainos: {', '.join(f'{k} = {v:.0f} €' for k, v in manual.items()) or 'nėra'}")
    except (ValueError, IndexError):
        return f"Neteisinga reikšmė: {text}\n\n{HELP}"
    return None
