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
        "iPhone 8", "iPhone X", "iPhone XR", "iPhone XS", "iPhone 11", "iPhone 12",
        "iPhone 13", "iPhone 14", "iPhone 15", "iPhone 16", "iPhone 17", "iPhone Air",
    ],

    # --- Kas yra "gera kaina" ---
    "MIN_DISCOUNT": 0.15,            # bent 15% pigiau nei telefono verte
    "HARD_MIN_PRICE_RATIO": 0.15,    # pigiau nei 15% rinkos – ne telefonas (priedas/klaida), atmetama
    "SUSPICIOUS_PRICE_RATIO": 0.55,  # pigiau nei 55% rinkos – siunciama, bet pazymima rizika
    "MIN_BATTERY": 0,                # min. baterijos % (0 = netikrinti)

    # --- Rinkos kaina ---
    "MARKET_PRICES": {},             # rankines kainos: {"13": 180, "13|256 GB": 210}
    "USE_SOLD_PRICES": True,         # naudoti tikras pardavimo kainas, kai ju pakanka
    "MIN_SOLD_SAMPLES": 5,           # kiek parduotu reikia, kad kaina butu skaiciuojama is ju
    "SOLD_CHECKS_PER_RUN": 15,       # kiek senu skelbimu per paleidima patikrinti, ar parduoti
    "SOLD_CHECK_AFTER_DAYS": 2,      # tikrinti skelbimus, kuriu kataloge nematem bent tiek dienu
    "MIN_SAMPLES": 8,                # kiek prasomu kainu reikia rinkos kainai
    "MARKET_PERCENTILE": 0.35,       # prasomu kainu percentilis (0.5 = mediana)
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
    "PAGES": 3,
    "SLEEP_SECONDS": 3,
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
OVERRIDABLE = {"MIN_DISCOUNT", "MIN_BATTERY", "LOUD_DISCOUNT", "MARKET_PRICES", "PAUSED"}


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
