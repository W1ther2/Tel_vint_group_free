# -*- coding: utf-8 -*-
"""Telefono atpazinimas: modelis, priedai, defektai, talpa, baterija, vertes daugikliai."""

import re

from . import config
from .util import fold

ALLOWED_MODELS = {
    "8", "8 Plus", "X", "XR", "XS", "XS Max",
    "11", "11 Pro", "11 Pro Max",
    "12 mini", "12", "12 Pro", "12 Pro Max",
    "13 mini", "13", "13 Pro", "13 Pro Max",
    "14", "14 Plus", "14 Pro", "14 Pro Max",
    "15", "15 Plus", "15 Pro", "15 Pro Max",
    "16e", "16", "16 Plus", "16 Pro", "16 Pro Max",
    "17e", "17", "Air", "17 Pro", "17 Pro Max",
}
# Modeliu tvarka (sarasams /kainos komandoje)
MODEL_ORDER = ["8", "8 Plus", "X", "XR", "XS", "XS Max", "11", "11 Pro", "11 Pro Max",
               "12 mini", "12", "12 Pro", "12 Pro Max", "13 mini", "13", "13 Pro", "13 Pro Max",
               "14", "14 Plus", "14 Pro", "14 Pro Max", "15", "15 Plus", "15 Pro", "15 Pro Max",
               "16e", "16", "16 Plus", "16 Pro", "16 Pro Max", "17e", "17", "Air", "17 Pro", "17 Pro Max"]

_MODEL_RE = re.compile(
    r"\biphone\s*(?P<gen>1[1-7]|8|xs|xr|x|air)\s*(?P<e>e\b)?\s*"
    r"(?P<var>pro\s*max|promax|pro|max|plus|\+|mini)?"
)
_VARIANTS = {"promax": "Pro Max", "pro": "Pro", "max": "Max", "plus": "Plus", "+": "Plus", "mini": "mini", "": ""}


# Kartos pavadinimai pavadinime (su "iphone" arba be jo): "13", "15pro", "8plus", "6s", "XR"
_GEN_TOKEN_RE = re.compile(
    r"(?<![\w.])(?P<pre>(?:apple\s+)?iphone\s*)?(?P<gen>6s|1[0-7]|[678]|xs|xr|x|se)"
    r"(?P<var>\s*(?:pro\s*max|promax|pro|plus|max|mini)|\+)?(?![\w])"
)


def _generations(t):
    """Skirtingu iPhone kartu rinkinys pavadinime (kelioms kartoms = rinkinys/dezutes)."""
    strong, weak = set(), set()
    for m in _GEN_TOKEN_RE.finditer(t):
        if t[max(0, m.start() - 4):m.start()].strip().endswith("ios"):
            continue
        gen = m.group("gen")
        if m.group("pre") or m.group("var"):
            var = re.sub(r"\s+", "", m.group("var") or "").replace("promax", "pro max").replace("+", "plus")
            strong.add(f"{gen} {var}".strip())
        elif gen.isdigit() or gen == "6s":
            weak.add(gen)
    # "13" ir "13 pro" – skirtingi modeliai; bet "iPhone 13 128GB, 13 mėn." – ne
    if len(strong) + len(weak) >= 3:
        return strong | weak
    return strong


def detect_model(title):
    """'Apple iPhone 13ProMax 256GB' -> '13 Pro Max'. None, jei nera arba keli skirtingi modeliai."""
    t = fold((title or "").lower()).replace("i phone", "iphone")
    if len(_generations(t)) >= 2:
        return None
    found = set()
    for m in _MODEL_RE.finditer(t):
        gen = m.group("gen")
        gen = gen.upper() if gen in ("x", "xr", "xs") else ("Air" if gen == "air" else gen)
        if m.group("e") and gen.isdigit():
            gen += "e"
        var = _VARIANTS[(m.group("var") or "").replace(" ", "")]
        if gen == "XS" and var == "Max":
            name = "XS Max"
        elif var == "Max":
            name = f"{gen} Pro Max"          # "iPhone 13 Max" dazniausiai reiskia Pro Max
        else:
            name = f"{gen} {var}".strip()
        if name in ALLOWED_MODELS:
            found.add(name)
    return found.pop() if len(found) == 1 else None


def normalize_model_name(text):
    """Vartotojo ivestis ('13 pro max', '13promax', 'xs max') -> '13 Pro Max' arba None."""
    return detect_model("iphone " + (text or ""))


# Zodziai BET KUR pavadinime, reiskiantys, kad parduodamas ne veikiantis telefonas
NON_PHONE_RE = re.compile(
    r"\b(?:paveiksl\w*|remel\w*|frame\w*|framed|wall art|art\b|dekor\w*|muliaz\w*|dummy|maket\w*|"
    r"detal\w*|dalys|dalim\w*|parts?\b|korpus\w*|housing|plokst\w*|motherboard|logic board|mainboard|"
    r"lcd|display|ekran\w* (?:iphone|keitim\w*)|tik dezut\w*|dezute be telefono|box only|empty box|"
    r"tuscia dezut\w*|lipduk\w*|sticker\w*|skin\b|replika|replica|kopija|clone)"
)


def is_accessory(title):
    """Priedas ar ne telefonas: 'Dėklas iPhone 13', 'Paveikslas iPhone X', 'iPhone 12 detalės'."""
    t = fold((title or "").lower()).strip()
    first = re.split(r"\W+", t, maxsplit=1)[0] if t else ""
    words = [str(w).lower() for w in config.cfg["ACCESSORY_FIRST_WORDS"]]
    if first and any(first.startswith(w) for w in words):
        return True
    if NON_PHONE_RE.search(t):
        return True
    return bool(re.search(r"\b(for|skirtas|skirta|tinka|compatible|fur|pour|per)\s+(apple\s+)?iphone", t))


# Stiprios frazes APRASYME, kad parduodamas ne telefonas (dezutes, detales, keli vnt.)
DESCRIPTION_NOT_PHONE_RE = re.compile(
    r"\b(?:tusci\w* dezut\w*|dezut\w* be telefon\w*|empty box\w*|box only|only (the )?box|tik dezut\w*|"
    r"be telefono|telefono nera|telefonas nepridedamas|phone not included|tik korpus\w*|tik ekran\w*|"
    r"tik dekl\w*|paveiksl\w*|remel\w*|framed|kaina uz visas|uz visus|\d+\s?(?:vnt\.?\s)?(?:telefon|dezut)\w*|lotas|lot of|"
    r"muliaz\w*|dummy|replika|replica|kopija|detalem\w*|atsargin\w* dal\w*|"
    # Vinted apgavyste: skelbime telefonas, o parduodamas tik popieriaus lapas / nuotrauka
    r"a4 lapas|a4 formato|lap\w* su (siais |tokiais )?vaizdais|siuntoje (rasite|gausite) tik|"
    r"gausite tik (lapa|lapas|nuotrauk\w*|foto|spaudin\w*|popieri\w*)|"
    r"parduodam\w* tik (lapa|lapas|lapelis|nuotrauk\w*|foto|spaudin\w*|popieri\w*|aprasym\w*)|"
    r"tik (popieri\w* )?lapas|tik lapelis|spausdint\w* (lapas|nuotrauk\w*)|popieri\w* lapas|"
    r"sheet of paper|printed (photo|picture|paper)|you are buying (a )?(photo|picture|paper|sheet)|"
    r"not the phone|nera telefonas)"
)


def description_not_phone(description):
    return bool(DESCRIPTION_NOT_PHONE_RE.search(fold((description or "").lower())))


# Minimali realistiška tvarkingo telefono kaina (~45% iprastos naudoto kainos).
# Pigiau = beveik visada dezute, dalys, sugedes ar apgavyste. Keiciama config.json "MODEL_MIN_PRICES".
DEFAULT_MIN_PRICES = {
    "8": 30, "8 Plus": 35, "X": 40, "XR": 40, "XS": 45, "XS Max": 55,
    "11": 60, "11 Pro": 70, "11 Pro Max": 85,
    "12 mini": 65, "12": 75, "12 Pro": 100, "12 Pro Max": 120,
    "13 mini": 80, "13": 90, "13 Pro": 120, "13 Pro Max": 140,
    "14": 110, "14 Plus": 120, "14 Pro": 150, "14 Pro Max": 180,
    "15": 150, "15 Plus": 165, "15 Pro": 200, "15 Pro Max": 240,
    "16e": 170, "16": 200, "16 Plus": 225, "16 Pro": 280, "16 Pro Max": 320,
    "17e": 200, "17": 280, "Air": 290, "17 Pro": 380, "17 Pro Max": 450,
}


def typical_price(model):
    """Apytiksle iprasta tvarkingo naudoto telefono kaina (is DEFAULT_MIN_PRICES, ~45%)."""
    floor = DEFAULT_MIN_PRICES.get(model)
    return round(floor / 0.45 / 5) * 5 if floor else None


def min_price(model):
    custom = config.cfg.get("MODEL_MIN_PRICES") or {}
    try:
        return float(custom[model]) if model in custom else float(DEFAULT_MIN_PRICES.get(model, 0))
    except (TypeError, ValueError):
        return float(DEFAULT_MIN_PRICES.get(model, 0))


CONDITION_RANK = {"Nauja su etiketėmis": 5, "Nauja be etikečių": 4, "Labai gera": 3, "Gera": 2, "Patenkinama": 1}


def condition_ok(condition, minimum):
    """Nezinoma bukle praleidziama (tikrinama kitais budais)."""
    if condition not in CONDITION_RANK:
        return True
    return CONDITION_RANK[condition] >= CONDITION_RANK.get(minimum, 0)


# --- Defektai --------------------------------------------------------------
# (saknis be diakritiku, pavadinimas, vertes daugiklis). 0 = netinkamas (visada atmetama)
DEFECT_PATTERNS = [
    (r"icloud", "iCloud užraktas", 0.0),
    # ir daznos rasybos klaidos: „uzbluokuotas“, „blukuotas“, „uzlockintas“, „lokintas“
    (r"uzbl\w{0,3}k\w*|bl[aou]{1,2}kuot\w*|\blocked\b|uzrakint\w*|(?:uz)?loc?kint\w*",
     "užblokuotas", 0.0),
    # Be baterijos telefono neisbandysi. „be baterijos keitimo / problemu“ – ne tas pats.
    (r"(?:be|nera|truksta|neturi|isimt\w*|no|without|missing) (?:akum\w*|baterij\w*|batarej\w*|battery)\b"
     r"(?! (?:problem|keit|pakeit|bed|defekt|gedim|nusidev|susidev|sveikat|degrad|isnaud|issues?|replace|health)\w*)",
     "be baterijos", 0.0),
    (r"dalims|for parts|parts only|detalem\w*|donor\w*", "dalims", 0.0),
    (r"(prasyt?\w*|praso|reikia|nezin\w*|pamirs\w*|ivesti|uzrakint\w*) (\w+ ){0,3}(kod\w*|slaptazod\w*|pin\w*)|"
     r"(kod\w*|slaptazod\w*) (\w+ ){0,3}(nezin\w*|pamirs\w*)|passcode|activation lock|aktyvacij\w* uzrakt\w*",
     "užrakintas kodu", 0.0),
    (r"netestuot\w*|neistestuot\w*|netikrint\w*|nepatikrint\w*|untested|not tested|nezinau ar veikia|"
     r"nezinom\w* ar veikia|kaip yra", "netestuotas", 0.0),
    (r"neisijung\w*|nesijung\w*|neuzsikraun\w*|nesikrauna\w*|nekrauna|nekraun\w*|won.?t turn on|"
     r"does ?n.?t turn on|no power|juodas ekranas|black screen|bootloop|persikraun\w*|uzstring\w*",
     "neįsijungia / nesikrauna", 0.0),
    (r"linij\w* (\w+ ){0,3}ekran\w*|ekran\w* (\w+ ){0,3}linij\w*|dem\w* (\w+ ){0,3}ekran\w*|"
     r"ekran\w* (\w+ ){0,3}dem\w*|green line|lines on screen|"
     r"mirga|mirkcioj\w*|neveikia liet\w*|lietimas neveik\w*|touch not working|ghost touch",
     "ekrano gedimas", 0.50),
    (r"be garantij\w* ir be\b|nezinau istorij\w*|rastas", "neaiški kilmė", 0.80),
    (r"i?skil\w*", "skilęs", 0.70),
    (r"sudauz\w*|dauzt\w*", "sudaužtas", 0.65),
    (r"cracked|crack", "įskilęs (cracked)", 0.70),
    (r"broken", "sugedęs (broken)", 0.55),
    (r"sugad\w*|sugedes|sugedo", "sugedęs", 0.55),
    (r"damaged|water damage|sulyt\w*|pasemt\w*|dregm\w*", "pažeistas", 0.60),
    (r"no face ?id|be face ?id|face ?id neveik\w*", "neveikia Face ID", 0.75),
    (r"neveik\w*|not working|doesn.?t work", "kažkas neveikia", 0.70),
    (r"keist\w* ekran\w*|replaced screen|neoriginal\w* ekran\w*|ne originalus ekranas", "keistas ekranas", 0.85),
    (r"keist\w* baterij\w*|neoriginal\w* baterij\w*|service battery", "keista baterija", 0.92),
    (r"remont\w*|reikia keisti|taisyt\w*", "reikia remonto", 0.65),
    (r"defekt\w*", "defektai", 0.85),
    (r"ibrez\w*|subraiz\w*|scratch\w*|nubraiz\w*", "įbrėžimai", 0.95),
]
_DEFECT_RE = [(re.compile(r"\b(?:" + p + r")"), label, f) for p, label, f in DEFECT_PATTERNS]
_NE_IS_DEFECT = {"kažkas neveikia", "neveikia Face ID", "neįsijungia / nesikrauna", "netestuotas",
                 "užrakintas kodu", "ekrano gedimas", "neaiški kilmė", "be baterijos"}
_NEGATION_IS_DEFECT = {"be baterijos"}
_NEGATE_BEFORE = {"be", "nera", "no", "not", "without", "jokiu", "jokio", "nieko", "neturi", "zero", "0"}
# Zodziai PO defekto, kurie ji paneigia: "iCloud atristas", "iCloud paskyra bus atsieta".
# Tokia formuluote lietuviskuose skelbimuose iprasta, todel ziurim kelis zodzius i prieki.
_NEGATE_AFTER = {"atristas", "atrista", "atrista", "atrisiu", "atrisamas",
                 "atsietas", "atsieta", "atsiesiu", "atsiejamas",
                 "laisvas", "isjungtas", "isjungta", "nera", "free", "off",
                 "clean", "unlocked", "nepriristas", "neprisietas",
                 "atrakintas", "atrakinta", "pasalintas", "pasalinta",
                 "islogintas", "isloginta", "removed"}
_NEGATE_AFTER_WORDS = 4


def find_defects(*texts):
    """[(pavadinimas, daugiklis), ...]. Ignoruoja paneigimus ('be įskilimų', 'iCloud atrištas')."""
    t = fold(" ".join(x for x in texts if x).lower())
    found = {}
    for rx, label, factor in _DEFECT_RE:
        for m in rx.finditer(t):
            clause = re.split(r"[.,;!?\n]|\bbet\b|\bbut\b", t[:m.start()])[-1]
            before = {w.strip(",.;:!-()") for w in clause.split()[-4:]}
            after = {w.strip(",.;:!-()") for w in
                     re.split(r"[.,;!?\n]", t[m.end():])[0].split()[:_NEGATE_AFTER_WORDS]}
            inside = set(t[m.start():m.end()].split())
            # „be akumo“ – pats „be“ ir yra defektas, tad cia paneigimo nebetikrinam
            negation_is_defect = label in _NEGATION_IS_DEFECT
            if not negation_is_defect and (before & _NEGATE_BEFORE or inside & _NEGATE_BEFORE):
                continue
            if after & _NEGATE_AFTER:
                continue
            # "nesudaužytas", "neskilęs" = paneigimas; bet "neveikia", "neįsijungia" – pats defektas
            if label not in _NE_IS_DEFECT and t[m.start():m.end()].startswith("ne"):
                continue
            found.setdefault(label, factor)
            break
    if "neveikia Face ID" in found:
        found.pop("kažkas neveikia", None)
    return list(found.items())


def extract_storage(*texts):
    t = " ".join(x for x in texts if x).lower()
    m = re.search(r"\b(64|128|256|512)\s?(?:gb|g|gigabait\w*)\b", t)
    if m:
        return f"{m.group(1)} GB"
    if re.search(r"\b1\s?tb\b", t):
        return "1 TB"
    return None


def normalize_storage(text):
    t = (text or "").lower().replace(" ", "")
    m = re.fullmatch(r"(64|128|256|512)(gb|g)?", t)
    if m:
        return f"{m.group(1)} GB"
    if t in ("1tb", "1024", "1024gb"):
        return "1 TB"
    return None


def extract_battery(*texts):
    t = fold(" ".join(x for x in texts if x).lower())
    pats = [
        r"(?:baterij\w*|akumuliator\w*|battery(?: health)?|\bbh\b|\bbat\b\.?|sveikat\w*|talpa)\D{0,20}?(\d{2,3})\s?%",
        r"(\d{2,3})\s?%\s?(?:baterij\w*|battery|\bbh\b|sveikat\w*|talp\w*)",
    ]
    for pat in pats:
        m = re.search(pat, t)
        if m and 50 <= int(m.group(1)) <= 100:
            return int(m.group(1))
    return None


# --- Verte ------------------------------------------------------------------
CONDITION_FACTOR = {
    "Nauja su etiketėmis": 1.15, "Nauja be etikečių": 1.08, "Labai gera": 1.00,
    "Gera": 0.92, "Patenkinama": 0.80,
}


def battery_factor(battery):
    if battery is None:
        return 0.98                     # dauguma nenurodo – beveik nebaudziam
    if battery >= 95:
        return 1.03
    if battery >= 90:
        return 1.00
    if battery >= 85:
        return 0.97
    if battery >= 80:
        return 0.93
    return 0.85


def estimate_value(market, condition, battery, defects):
    """Konkretaus telefono verte = rinkos kaina x bukle x baterija x defektai."""
    value = market * CONDITION_FACTOR.get(condition, 0.95) * battery_factor(battery)
    for _, factor in defects:
        value *= factor
    return value


def estimate_profit(price, resale_value, pickup_only=False, buyer_fee=True, total_price=None):
    """Pelnas perpardavus: verte - (kaina + pirkejo apsaugos mokestis + siuntimas).

    `total_price` – tikra suma su mokesciu, kai saltinis ja pasako (Vinted pasako).
    `buyer_fee` – ar mokestis apskritai imamas (Skelbiu/Pirkpard – ne)."""
    c = config.cfg
    cost = price
    if total_price and total_price > 0:
        cost = float(total_price)
    elif buyer_fee:
        cost += c["BUYER_FEE_FIXED"] + price * c["BUYER_FEE_PCT"]
    if not pickup_only:
        cost += c["SHIPPING_COST"]
    return resale_value - cost
