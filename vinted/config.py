# -*- coding: utf-8 -*-
"""Nustatymai: numatytieji + config.json + Telegram komandomis pakeisti (overrides)."""

import json
import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

CONFIG_FILE = "config.json"
SEEN_FILE = "seen.json"
STATE_FILE = "state.json"          # rinkos kainos, parduoti skelbimai, Telegram nustatymai
OLD_PRICES_FILE = "prices.json"    # senas formatas – automatiskai perkeliamas i state.json

BASE = "https://www.vinted.lt"
API_BASE = "https://api.vinted.lt"

DEFAULTS = {
    # --- Ka ieskoti ---
    "SEARCH_QUERIES": [
        "iPhone 8", "iPhone 8 Plus", "iPhone X", "iPhone XR", "iPhone XS", "iPhone XS Max",
        "iPhone 11", "iPhone 11 Pro", "iPhone 11 Pro Max", "iPhone 12", "iPhone 12 mini",
        "iPhone 12 Pro", "iPhone 12 Pro Max", "iPhone 13", "iPhone 13 mini", "iPhone 13 Pro",
        "iPhone 13 Pro Max", "iPhone 14", "iPhone 14 Plus", "iPhone 14 Pro", "iPhone 14 Pro Max",
        "iPhone 15", "iPhone 15 Plus", "iPhone 15 Pro", "iPhone 15 Pro Max", "iPhone 16",
        "iPhone 16e", "iPhone 16 Plus", "iPhone 16 Pro", "iPhone 16 Pro Max", "iPhone 17",
        "iPhone 17 Pro", "iPhone 17 Pro Max", "iPhone Air",
    ],

    # --- Kas yra "gera kaina" ---
    "MIN_DISCOUNT": 0.10,            # bent 10% pigiau nei telefono verte
    "HARD_MIN_PRICE_RATIO": 0.40,    # pigiau nei 40% rinkos – beveik visada sugedes/dalims/ne telefonas, atmetama
    "SUSPICIOUS_PRICE_RATIO": 0.55,  # pigiau nei 55% rinkos – siunciama, bet pazymima rizika
    "MIN_BATTERY": 0,                # min. baterijos % (0 = netikrinti)
    "MODEL_MIN_PRICES": {},          # savos min. kainos modeliams, pvz. {"13": 100} (kitiems – numatytosios)

    # --- Tik tvarkingi telefonai ---
    "TIDY_ONLY": True,               # atmesti sugedusius, netestuotus, uzrakintus, su defektais
    "MIN_CONDITION": "Gera",         # blogiausia leidziama bukle: "Labai gera" / "Gera" / "Patenkinama"
    "ALLOWED_DEFECTS": ["įbrėžimai"],# kurie defektai leidziami, kai TIDY_ONLY

    # --- Rinkos kaina ---
    "MARKET_PRICES": {},             # rankines kainos: {"13": 180, "13|256 GB": 210}
    "USE_SOLD_PRICES": True,         # naudoti tikras pardavimo kainas, kai ju pakanka
    "MIN_SOLD_SAMPLES": 5,           # kiek parduotu reikia, kad kaina butu skaiciuojama is ju
    "SOLD_CHECKS_PER_RUN": 15,       # kiek senu skelbimu per paleidima patikrinti, ar parduoti
    "SOLD_CHECK_AFTER_DAYS": 2,      # tikrinti skelbimus, kuriu kataloge nematem bent tiek dienu
    "MIN_SAMPLES": 8,                # kiek prasomu kainu reikia rinkos kainai
    "USE_TYPICAL_FALLBACK": True,    # kai duomenu per mazai – naudoti apytiksle kaina (retiems modeliams)
    "MARKET_PERCENTILE": 0.5,        # prasomu kainu percentilis (0.5 = mediana, 0.35 = pigesnis trecdalis)
    "PRICE_HISTORY_DAYS": 30,
    "SOLD_HISTORY_DAYS": 60,
    "PRICE_HISTORY_MAX_ITEMS": 12000,

    # --- Kainos sumazejimas ---
    "PRICE_DROP_ALERTS": True,
    "PRICE_DROP_MIN": 0.05,          # pranesti, jei atpigo bent 5%

    # --- Pelnas perpardavus ---
    "SHOW_PROFIT": True,
    "BUYER_FEE_FIXED": 0.70,         # Vinted pirkejo apsaugos mokestis (fiksuota dalis)
    "BUYER_FEE_PCT": 0.05,           # Vinted pirkejo apsaugos mokestis (procentai)
    "SHIPPING_COST": 3.5,            # siuntimo kaina perkant

    # --- Pranesimai ---
    "LOUD_DISCOUNT": 0.30,           # nuo tiek pigiau – su garsu, maziau – tyliai
    "TELEGRAM_COMMANDS": True,       # leisti keisti nustatymus komandomis Telegram'e
    "HEARTBEAT_HOURS": 24,
    "FAIL_ALERT_RUNS": 3,            # po kiek nesekmingu paleidimu is eiles pranesti apie problema

    # --- Priedu atpazinimas (pirmas pavadinimo zodis) ---
    "ACCESSORY_FIRST_WORDS": [
        "deklas", "dekl", "case", "cover", "stiklas", "apsauginis", "folija", "kroviklis",
        "laidas", "kabelis", "dezute", "box", "hulle", "coque", "custodia", "etui", "glass",
        "screen", "ekranas", "baterija", "battery", "korpusas", "kamera", "lens", "magsafe",
    ],

    # --- Pardavejas ir kalba ---
    "ALLOWED_COUNTRY_CODES": ["LT"],
    "FILTER_BY_COUNTRY": True,
    "REQUIRE_KNOWN_COUNTRY": False,
    "MIN_SELLER_RATING": 0,
    "MIN_SELLER_REVIEWS": 0,
    "SELLER_NEW_ACCOUNT_DAYS": 30,   # jaunesne paskyra = rizikos pozymis
    "ONLY_LITHUANIAN_TEXT": True,
    "ALLOWED_LANGUAGES": ["LT", "EN"],

    # --- Veikimas ---
    "ROTATE_QUERIES": True,          # kiekviena paleidima pradeti nuo kito modelio (tolygesnis greitis)
    "PAGES": 2,                      # puslapiu (po 96 skelb.) kiekvienai paieskai iprastai
    "FULL_SCAN_PAGES": 10,           # kai seen.json tuscias (pirmas/pilnas paleidimas) – perziureti daugiau
    "SLEEP_SECONDS": 3,
    "BLOCK_BACKOFF_SECONDS": [30, 60, 120],   # pauzes, kai Vinted blokuoja (403)
    "STOP_AFTER_BLOCKED_QUERIES": 3,          # po tiek is eiles blokuotu paiesku – baigti paleidima
    "DETAIL_SLEEP_SECONDS": 1.0,
    "DRY_RUN": False,
    "PAUSED": False,                 # True = skelbimai nesiunciami (Telegram /pauze)
    "DEBUG": False,
    "SEEN_MAX_AGE_DAYS": 7,
    "SEEN_MAX_ENTRIES": 20000,
}

# Senu versiju raktai -> nauji
_RENAMED = {"PRICE_HISTORY_MAX": None, "MIN_PRICE_RATIO": None}

cfg = dict(DEFAULTS)


def load(path=CONFIG_FILE):
    """Ikelia config.json i `cfg` (vietoje). Grazina cfg."""
    cfg.clear()
    cfg.update(json.loads(json.dumps(DEFAULTS)))
    if not os.path.exists(path):
        print(f"! {path} nerastas – naudojami numatytieji.")
        return cfg
    try:
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
        if not isinstance(user, dict):
            raise ValueError("ne JSON objektas")
        unknown = [k for k in user if k not in DEFAULTS and k not in _RENAMED]
        cfg.update({k: v for k, v in user.items() if k in DEFAULTS})
        print(f"Konfiguracija ikelta is {path}")
        if unknown:
            print(f"  (nezinomi raktai ignoruojami: {', '.join(unknown)})")
    except Exception as e:
        print(f"! Nepavyko nuskaityti {path} ({e}) – naudojami numatytieji.")
    return cfg


# Raktai, kuriuos galima keisti Telegram komandomis
OVERRIDABLE = {"MIN_DISCOUNT", "MIN_BATTERY", "LOUD_DISCOUNT", "MARKET_PRICES", "PAUSED", "TIDY_ONLY",
               "MARKET_PERCENTILE", "SHOW_PROFIT"}


def apply_overrides(overrides):
    """Telegram komandomis nustatytos reiksmes turi pirmenybe pries config.json."""
    for key, value in (overrides or {}).items():
        if key not in OVERRIDABLE:
            continue
        if key == "MARKET_PRICES":
            merged = dict(cfg.get("MARKET_PRICES") or {})
            for k, v in value.items():
                if v is None:
                    merged.pop(k, None)
                else:
                    merged[k] = v
            cfg["MARKET_PRICES"] = merged
        else:
            cfg[key] = value


def market_prices():
    out = {}
    for k, v in (cfg.get("MARKET_PRICES") or {}).items():
        try:
            if float(v) > 0:
                out[str(k).strip()] = float(v)
        except (TypeError, ValueError):
            pass
    return out


def allowed_languages():
    return {str(x).upper() for x in cfg["ALLOWED_LANGUAGES"]} | {"LT"}
