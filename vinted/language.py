# -*- coding: utf-8 -*-
"""Kalbos atpazinimas: lietuviski pozymiai (su diakritikais ir be) pries kitas kalbas."""

import re

from . import config
from .util import fold, word_regex, debug

LITHUANIAN_CHARS = set("ąčęėįšųūž")

LITHUANIAN_STEMS = [
    "parduod", "pardod", "parduos", "bukl", "busen", "puik", "tvarking", "veik", "kain",
    "euru", "originalu", "originali", "idealu", "idealio", "ideali", "baterij", "irasyt",
    "nauj", "naudot", "dekl", "defektu", "defektai", "kokybisk", "mazai", "telefonas",
    "telefonui", "telefona", "ekranas", "ekrano", "ekrane", "ikrov", "krovikl", "laidas", "laidu",
    "dezut", "dezes", "talpa", "talpos", "atsiim", "siunc", "siunt", "pristat", "keic", "keist",
    "komplekt", "pilnas", "pilnai", "brezim", "ibrez", "isbandyt", "garantij", "pirkt",
    "labai", "gerai", "gera", "geras", "geros", "geroje", "grazus", "grazi", "funkcij",
    "problemu", "sveikat", "skilim", "skiles", "sudauz", "nesider", "derin", "vilni", "kaun",
    "klaiped", "siaul", "panevez", "alyt", "marijampol", "utena", "palanga", "priedas",
    "pridedu", "pridedam", "kartu", "nieko", "jokiu", "nera", "yra", "turi", "turiu",
    "procent", "busima", "rasykit", "rasyk", "skambin", "zinut", "kraun", "naudoj",
]
_LT_RE = re.compile(r"\b(?:" + "|".join(sorted(map(re.escape, LITHUANIAN_STEMS), key=len, reverse=True)) + r")\w*")
_LT_SHORT = word_regex(["ir", "su", "be jokiu", "tik", "del", "nes", "arba", "kaip", "ar", "jau", "labai"])

FOREIGN_WORDS = {
    "PL": ["sprzedam", "sprzedaje", "kupie", "telefon", "oryginalny", "oryginalne", "stan", "stanie",
           "wysylka", "wysylke", "zestaw", "paragon", "faktura", "nieuszkodzony", "uszkodzony", "ladny",
           "przesylka", "polecam", "okazja", "komplet", "kondycja", "sprawny", "sprawna", "pudelko",
           "gwarancja", "cena", "pekniety", "peknieta", "zbite", "zbita", "wyswietlacz", "bateria",
           "akumulator", "dziala", "pilne", "negocjacje", "akcesoria", "bardzo", "dobry", "jest", "sie", "oraz"],
    "DE": ["verkaufe", "neuwertig", "versand", "zustand", "gebraucht", "originalverpackung", "rechnung",
           "funktioniert", "einwandfrei", "und", "mit", "ohne", "sehr", "gut", "kratzer", "akku", "ist",
           "nicht", "keine"],
    "EN": ["selling", "brand new", "like new", "shipping", "great condition", "condition",
           "excellent condition", "as new", "no issues", "works perfectly", "the", "and", "with",
           "without", "for", "comes", "battery health", "health", "unlocked", "scratches", "perfect",
           "working", "used", "very good", "good", "fully"],
    "FR": ["vends", "vend", "etat", "tres", "bon", "avec", "sans", "pour", "neuf", "batterie", "rayure",
           "rayures", "fonctionne", "parfait", "chargeur", "boite", "comme", "est"],
    "IT": ["vendo", "perfetto", "perfette", "condizioni", "batteria", "graffi", "funzionante", "come",
           "nuovo", "con", "senza", "scatola", "ottime"],
    "ES": ["vendo", "estado", "perfecto", "funciona", "bateria", "nuevo", "caja", "aranazos", "sin",
           "muy", "bueno"],
    "NL": ["verkoop", "staat", "goede", "nieuw", "zonder", "krassen", "doos", "werkt", "met", "een"],
    "LV": ["stavoklis", "stavokli", "labs", "jauns", "telefons", "kaste", "bez", "pardodu telefonu"],
    "CZ": ["prodam", "stav", "velmi", "dobry", "baterie", "krabice", "funkcni"],
}
_FOREIGN_RE = {lang: word_regex([fold(w) for w in words]) for lang, words in FOREIGN_WORDS.items()}
FOREIGN_CHARS = {
    "PL": set("łńśźżć"), "DE": set("äöüß"), "LV": set("āēīōļņģ"),
    "FR": set("éèêàçôœ"), "ES": set("ñ¿¡"), "CZ": set("řěůť"),
}
NEUTRAL_WORDS = set("""
iphone apple pro max plus mini gb tb gen generation se ios airpods watch ipad macbook
unlocked icloud face id truedepth esim sim dual black white blue gold silver graphite
sierra alpine green purple deep space midnight starlight red pink natural titanium
""".split())


def lithuanian_score(*texts):
    """Kiek tekste TIKRAI lietuvisku pozymiu: diakritikai, saknys, trumpi zodziai.
    0 = nieko lietuviško (pvz. „iPhone 13 128GB“ – tokia antraste beskalbe)."""
    raw = " ".join(x for x in texts if x).lower()
    folded = fold(raw)
    return (2 * len(LITHUANIAN_CHARS & set(raw))
            + 2 * len(set(_LT_RE.findall(folded)))
            + len(set(_LT_SHORT.findall(folded))))


def looks_lithuanian(*texts, minimum=2):
    """Ar tekstas tikrai lietuviskas.

    Kitaip nei `detect_foreign_language`, kuri None grazina IR lietuviskam, IR
    neatpazintam tekstui, cia reikia POZITYVAUS irodymo – bent lietuviskos raides
    ar saknies. Naudojama tada, kai pardavejo salies nustatyti nepavyko."""
    return lithuanian_score(*texts) >= minimum


def detect_foreign_language(*texts):
    """Kalbos kodas ('PL', 'EN', ..., '??'), jei tekstas ne lietuviskas; None – lietuviskas/nezinoma."""
    raw = " ".join(x for x in texts if x).lower()
    if not raw.strip():
        return None
    folded = fold(raw)
    if any("\u0400" <= ch <= "\u04ff" for ch in raw):
        return "RU"
    allowed = config.allowed_languages()

    lt_score = lithuanian_score(*texts)

    foreign = {}
    for lang, chars in FOREIGN_CHARS.items():
        n = len(chars & set(raw))
        if n:
            foreign[lang] = foreign.get(lang, 0) + 2 * n
    for lang, rx in _FOREIGN_RE.items():
        n = len(set(rx.findall(folded)))
        if n:
            foreign[lang] = foreign.get(lang, 0) + 2 * n
    best_lang, best_score = (max(foreign.items(), key=lambda kv: (kv[1], kv[0] in allowed))
                             if foreign else (None, 0))
    debug(f"kalba: LT={lt_score}, kitos={foreign}")

    if lt_score > 0 and lt_score >= best_score:
        return None
    if best_lang:
        return best_lang
    words = [w for w in re.findall(r"[a-z]{3,}", folded) if w not in NEUTRAL_WORDS]
    if len(set(words)) >= 4 and "EN" not in allowed:
        return "??"
    return None
