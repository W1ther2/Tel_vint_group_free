# -*- coding: utf-8 -*-
"""Telefonu katalogas: kuriuos modelius sekam ir kiek jie apytiksliai verti.

Modelio ID – raktas, kuris patenka i state.json („m“), i komandas ir i MARKET_PRICES.
Apple ID istoriskai yra be gamintojo („13 Pro“), kitu gamintoju – su pavadinimu
(„Galaxy S24 Ultra“). Taip seni duomenys lieka galioti be jokios migracijos, o ID
vis tiek nesusikerta. Zmogui visada rodom `display()`.

Cia DU ATSKIRI dalykai, kuriuos iki v49.1 atstovavo vienas skaicius:

  MIN_PRICES  APSAUGA. Riba, zemiau kurios beveik visada dezute, dalys ar apgavyste.
              Keiciama TIK samoningai. NIEKADA neisvedama is rinkos kainos.
  MEASURED    MATAVIMAS. Kiek modelis realiai vertas, su imtimi ir data.

Kodel atskirta (v50). Iki tol apytiksle kaina buvo gaunama `min / 0.45`, tad
ivedant matavima tekdavo judinti RIBA. Gyvas pavyzdys: Pixel 7 ismatuota mediana
111 EUR, ir kad `typical` priartetu, riba buvo nuleista 75 -> 55. Vertinimas
pagerejo (165 -> 120), bet apgavysciu apsauga krito 27 %, o kartu i rinkos
statistika isileisti tie skelbimai, kuriuos ji turejo atmesti - t. y. matavimas
pats sau pakenke. Dabar riba lieka 75, o matavimas gyvena `MEASURED` su n=19.

Taip pat is matavimo nebelieka tik komentaro: imtis ir data yra DUOMUO, tad
`confidence.py` gali pasakyti, kiek tuo skaiciumi pasitiketi, o `dataset.py`
veliau ji perrasyti ismoktu.

Naujas gamintojas = vienas irasas `BRANDS` sarase: raktas, paieskos fraze, zodziai,
kurie PRIVALO buti pavadinime, modeliu lentele ir viena regex funkcija.
"""

import re
from dataclasses import dataclass

from .util import fold


@dataclass(frozen=True)
class Prior:
    """Ka manom apie modelio verte PRIES pamatydami sio paleidimo skelbimus.

    `samples=0` reiskia spejima is ribos (`min / 0.45`) - toks pat skaicius kaip
    anksciau, bet dabar aiskiai pazymetas kaip spejimas. `samples>0` reiskia tikra
    matavima, ir butent si skirtuma iki v49.1 sistema pamesdavo."""
    price: float
    samples: int = 0
    measured: str = ""            # "2026-09-30"
    method: str = "guess"         # guess | live-median | sold-median | manual

    @property
    def is_guess(self) -> bool:
        return self.samples <= 0


# --- Matavimai --------------------------------------------------------------
#: Kada ismatuota. Imtys dideles, bet duomenys sensta - po poros menesiu
#: perskaiciuoti is naujo (`python -m vinted.dataset`).
MEASURED_ON = "2026-10-05"

#: {modelis: (mediana EUR, imtis)} - gyvos busenos medianos, be irasu, pazymetu
#: `x` (uzsienio, kalba, defektai), t. y. is to paties srauto, kuri mato atranka.
#: Pakeicia `min / 0.45` spejima ten, kur tikri duomenys yra.
MEASURED = {
    "8": (52, 177), "8 Plus": (75, 202), "X": (70, 124), "XR": (75, 269), "XS": (81, 163),
    "XS Max": (111, 118), "11": (88, 814), "11 Pro": (116, 243), "11 Pro Max": (139, 168),
    "12 mini": (109, 428), "12": (120, 668), "12 Pro": (163, 273), "12 Pro Max": (208, 167),
    "13 mini": (174, 424), "13": (183, 1417), "13 Pro": (270, 442), "13 Pro Max": (306, 223),
    "14": (233, 648), "14 Plus": (277, 131), "14 Pro": (348, 578), "14 Pro Max": (393, 274),
    "15": (348, 611), "15 Plus": (393, 70), "15 Pro": (480, 616), "15 Pro Max": (578, 266),
    "16e": (372, 53), "16": (508, 291), "16 Plus": (533, 70), "16 Pro": (692, 468),
    "16 Pro Max": (788, 274), "17": (754, 133), "Air": (698, 23), "17 Pro": (993, 263),
    "17 Pro Max": (1071, 187),
}

#: Spejimo daugiklis modeliams BE matavimo: riba / GUESS_RATIO.
#:
#: Tai grubus pakaitalas, ir dabar zinom, KIEK grubus. Ismatuotas tikrasis
#: santykis riba/mediana nera konstanta - jis tolygiai kyla pigejant telefonui:
#:
#:     iPhone 17 Pro  mediana  993    santykis 0.38
#:     iPhone 15 Pro  mediana  480    santykis 0.42
#:     iPhone 13      mediana  183    santykis 0.49
#:     iPhone 12      mediana  120    santykis 0.62
#:     iPhone 11      mediana   88    santykis 0.68
#:
#: Ir tai logiska: sudauzytas iPhone 11 dalimis vertas ~2/3 savo kainos, o
#: sudauzytas 17 Pro - ~1/3. Butent todel vienas daugiklis negali tikti abiem
#: galams: su 0.45 „11“ buvo pervertintas 53 %, o „17“ nuvertintas 18 %.
#: Modeliams, kuriems matavimo dar nera, 0.45 lieka kaip vidurkis - bet tai
#: SPEJIMAS, ir `Prior.samples == 0` tai pasako garsiai.
GUESS_RATIO = 0.45


# --- Apple ------------------------------------------------------------------
# ID be gamintojo (istoriskai). Atpazinimas – phone.py (_MODEL_RE), nes ten daug
# atskiru taisykliu („iPhone 128GB“ nera 12, „17 Air“ = Air ir t. t.).
APPLE_MIN_PRICES = {
    "8": 30, "8 Plus": 35, "X": 40, "XR": 40, "XS": 45, "XS Max": 55,
    "11": 60, "11 Pro": 70, "11 Pro Max": 85,
    "12 mini": 65, "12": 75, "12 Pro": 100, "12 Pro Max": 120,
    "13 mini": 80, "13": 90, "13 Pro": 120, "13 Pro Max": 140,
    "14": 110, "14 Plus": 120, "14 Pro": 150, "14 Pro Max": 180,
    "15": 150, "15 Plus": 165, "15 Pro": 200, "15 Pro Max": 240,
    "16e": 170, "16": 200, "16 Plus": 225, "16 Pro": 280, "16 Pro Max": 320,
    "17e": 200, "17": 280, "Air": 290, "17 Pro": 380, "17 Pro Max": 450,
}
APPLE_ORDER = ["8", "8 Plus", "X", "XR", "XS", "XS Max", "11", "11 Pro", "11 Pro Max",
               "12 mini", "12", "12 Pro", "12 Pro Max", "13 mini", "13", "13 Pro", "13 Pro Max",
               "14", "14 Plus", "14 Pro", "14 Pro Max", "15", "15 Plus", "15 Pro", "15 Pro Max",
               "16e", "16", "16 Plus", "16 Pro", "16 Pro Max", "17e", "17", "Air", "17 Pro", "17 Pro Max"]

# --- Android kainos ---------------------------------------------------------
# Ismatuota gyvai 2026-09-30: kiekvienam modeliui atskira Vinted paieska, po 3 puslapius,
# imamos tik apvalios kainos (su centais = perskaiciuota is PLN, t. y. kita rinka).
# 25 modeliams is 66 uzteko imties (4-19 skelbimu) – jiems kaina is ju pacios:
#   typical = percentile(prasomos, 0.4) * ASKING_SALE_FACTOR,  min = 0.45 * typical
# t. y. lygiai taip, kaip kaina skaiciuoja pats botas is duomenu. Likusiems taikyta to
# gamintojo bendra pataisa (mediana is ismatuotu): Samsung x0.78, Xiaomi x0.85,
# Pixel x0.86, OnePlus x0.72.
#
# Pirmasis spejimas buvo sistemiskai per aukstas (iki -50 %): Android pinga gerokai
# greiciau nei iPhone, o pirmoji lentele buvo braizyta pagal Apple kreive. Tai ne galutine
# tiesa – savikalibracija ir /kaina sitas kainas toliau tikslins pagal tikrus pardavimus.
SAMSUNG_MIN_PRICES = {
    "Galaxy Note 20": 55, "Galaxy Note 20 Ultra": 75, "Galaxy S21 FE": 45, "Galaxy S21": 45,
    "Galaxy S21+": 65, "Galaxy S21 Ultra": 80, "Galaxy S22": 50, "Galaxy S22+": 85,
    "Galaxy S22 Ultra": 100, "Galaxy S23 FE": 75, "Galaxy S23": 85, "Galaxy S23+": 105,
    "Galaxy S23 Ultra": 145, "Galaxy S24 FE": 115, "Galaxy S24": 115, "Galaxy S24+": 135,
    "Galaxy S24 Ultra": 190, "Galaxy S25 FE": 160, "Galaxy S25": 160, "Galaxy S25+": 180,
    "Galaxy S25 Ultra": 280, "Galaxy S26": 210, "Galaxy S26+": 245, "Galaxy S26 Ultra": 325,
    "Galaxy Z Flip 4": 65, "Galaxy Z Flip 5": 70, "Galaxy Z Flip 6": 135,
    "Galaxy Z Flip 7": 175, "Galaxy Z Fold 4": 155, "Galaxy Z Fold 5": 155,
    "Galaxy Z Fold 6": 310, "Galaxy Z Fold 7": 335
}
SAMSUNG_ORDER = ["Galaxy Note 20", "Galaxy Note 20 Ultra",
                 "Galaxy S21 FE", "Galaxy S21", "Galaxy S21+", "Galaxy S21 Ultra",
                 "Galaxy S22", "Galaxy S22+", "Galaxy S22 Ultra",
                 "Galaxy S23 FE", "Galaxy S23", "Galaxy S23+", "Galaxy S23 Ultra",
                 "Galaxy S24 FE", "Galaxy S24", "Galaxy S24+", "Galaxy S24 Ultra",
                 "Galaxy S25 FE", "Galaxy S25", "Galaxy S25+", "Galaxy S25 Ultra",
                 "Galaxy S26", "Galaxy S26+", "Galaxy S26 Ultra",
                 "Galaxy Z Flip 4", "Galaxy Z Flip 5", "Galaxy Z Flip 6", "Galaxy Z Flip 7",
                 "Galaxy Z Fold 4", "Galaxy Z Fold 5", "Galaxy Z Fold 6", "Galaxy Z Fold 7"]

# „Galaxy S24 Ultra“, „samsung s23+“, „S22 FE 5G“. Po numerio – ne skaitmuo, kad
# „s24“ nesutaptu su „SM-S928“ ar atminties dydziu.
# Pabaigoje (?!\w), o ne \b: po „+“ zodzio ribos nera, tad „s22+“ butu virtę „S22“.
_SAMSUNG_S = re.compile(r"\b(?:galaxy\s*)?s\s?(2[0-9])(?!\d)\s*(ultra|plus|\+|fe)?(?!\w)")
_SAMSUNG_ZED = re.compile(r"\b(?:galaxy\s*)?(?:z\s*)?(fold|flip)\s?(\d)(?!\d)(?!\w)")
_SAMSUNG_NOTE = re.compile(r"\bnote\s?(\d{1,2})(?!\d)\s*(ultra|plus|\+)?(?!\w)")
_SAMSUNG_VARIANT = {"plus": "+", "+": "+", "ultra": " Ultra", "fe": " FE", "": ""}

# --- Xiaomi -----------------------------------------------------------------
XIAOMI_MIN_PRICES = {
    "Xiaomi Mi 11": 50, "Xiaomi Mi 11 Ultra": 75, "Xiaomi 12": 60, "Xiaomi 12 Pro": 75,
    "Xiaomi 13": 85, "Xiaomi 13T": 70, "Xiaomi 13T Pro": 95, "Xiaomi 13 Pro": 110,
    "Xiaomi 13 Ultra": 135, "Xiaomi 14": 115, "Xiaomi 14T": 110, "Xiaomi 14T Pro": 140,
    "Xiaomi 14 Pro": 135, "Xiaomi 14 Ultra": 170, "Xiaomi 15": 155, "Xiaomi 15T": 135,
    "Xiaomi 15T Pro": 160, "Xiaomi 15 Pro": 185, "Xiaomi 15 Ultra": 230
}
XIAOMI_ORDER = ["Xiaomi Mi 11", "Xiaomi Mi 11 Ultra", "Xiaomi 12", "Xiaomi 12 Pro",
                "Xiaomi 13", "Xiaomi 13T", "Xiaomi 13T Pro", "Xiaomi 13 Pro", "Xiaomi 13 Ultra",
                "Xiaomi 14", "Xiaomi 14T", "Xiaomi 14T Pro", "Xiaomi 14 Pro", "Xiaomi 14 Ultra",
                "Xiaomi 15", "Xiaomi 15T", "Xiaomi 15T Pro", "Xiaomi 15 Pro", "Xiaomi 15 Ultra"]

# „Xiaomi 14 Ultra“, „xiaomi mi 11“, „Xiaomi 14T Pro“. Numeris turi eiti is karto po
# „xiaomi“ (arba po „xiaomi mi“) – kitaip „Xiaomi Redmi Note 13“ taptu „Xiaomi 13“.
_XIAOMI = re.compile(r"\bxiaomi\s*(mi\s*)?(1[1-9])\s*(t)?\s*(pro|ultra)?\b")
# Pigios serijos – ne flagmanai; radus jas modelio nenustatinejam.
_XIAOMI_CHEAP = re.compile(r"\b(?:redmi|poco|note\s?\d)\b")

# --- Google -----------------------------------------------------------------
PIXEL_MIN_PRICES = {
    "Pixel 6": 45, "Pixel 6 Pro": 65, "Pixel 7": 45, "Pixel 7 Pro": 85, "Pixel 8": 100,
    "Pixel 8 Pro": 120, "Pixel 9": 135, "Pixel 9 Pro": 175, "Pixel 9 Pro XL": 195,
    "Pixel 10": 175, "Pixel 10 Pro": 215, "Pixel 10 Pro XL": 240
}
PIXEL_ORDER = ["Pixel 6", "Pixel 6 Pro", "Pixel 7", "Pixel 7 Pro", "Pixel 8", "Pixel 8 Pro",
               "Pixel 9", "Pixel 9 Pro", "Pixel 9 Pro XL", "Pixel 10", "Pixel 10 Pro", "Pixel 10 Pro XL"]
_PIXEL = re.compile(r"\bpixel\s?(\d{1,2})(?!\d)\s*(pro\s?xl|pro|xl)?\b")

# --- OnePlus ----------------------------------------------------------------
ONEPLUS_MIN_PRICES = {
    "OnePlus 11": 75, "OnePlus 12": 110, "OnePlus 13": 145
}
ONEPLUS_ORDER = ["OnePlus 11", "OnePlus 12", "OnePlus 13"]
_ONEPLUS = re.compile(r"\bone\s?plus\s?(\d{1,2})(?!\d)\b")


def _samsung(t):
    found = set()
    for m in _SAMSUNG_S.finditer(t):
        found.add(f"Galaxy S{m.group(1)}{_SAMSUNG_VARIANT.get(m.group(2) or '', '')}")
    for m in _SAMSUNG_ZED.finditer(t):
        found.add(f"Galaxy Z {m.group(1).capitalize()} {m.group(2)}")
    for m in _SAMSUNG_NOTE.finditer(t):
        found.add(f"Galaxy Note {m.group(1)}{' Ultra' if m.group(2) == 'ultra' else ''}")
    return found


def _xiaomi(t):
    if _XIAOMI_CHEAP.search(t):
        return set()                       # Redmi / Poco – ne flagmanai
    found = set()
    for m in _XIAOMI.finditer(t):
        mi, gen, tee, var = m.group(1), m.group(2), m.group(3), m.group(4)
        name = f"Xiaomi {'Mi ' if mi else ''}{gen}{'T' if tee else ''}"
        if var:
            name += f" {var.capitalize()}"
        found.add(name)
    return found


def _pixel(t):
    found = set()
    for m in _PIXEL.finditer(t):
        var = re.sub(r"\s+", " ", (m.group(2) or "")).strip()
        var = {"pro xl": " Pro XL", "proxl": " Pro XL", "pro": " Pro", "xl": " XL", "": ""}.get(var, "")
        found.add(f"Pixel {m.group(1)}{var}")
    return found


def _oneplus(t):
    return {f"OnePlus {m.group(1)}" for m in _ONEPLUS.finditer(t)}


class Brand:
    """Vienas gamintojas: kaip ji atpazinti ir kiek jo modeliai verti."""

    def __init__(self, key, label, query, keywords, prices, order, matcher, prefix="", shorthand=False):
        self.key = key                 # config.json "BRANDS" raktas
        self.label = label             # zmogui
        self.query = query             # paieskos fraze Vinted / Pirkpard
        self.keywords = keywords       # bent vienas PRIVALO buti pavadinime
        self.prices = prices
        self.order = order
        self.matcher = matcher
        self.prefix = prefix           # kaip rodyti ID zmogui ("iPhone " + ID)
        # Ar komandose modeli galima rasyti BE gamintojo („13 pro“, „s24 ultra“).
        # Xiaomi/Pixel/OnePlus to nereikia – ju pavadinimas jau yra ID dalis, todel
        # be sio zymens „/kaina 7 100“ butu virtę Pixel 7, nors visada reiske iPhone.
        self.shorthand = shorthand

    def find(self, text):
        """Modeliu ID rinkinys pavadinime (tuscias, jei gamintojo zodzio nera)."""
        if not any(k in text for k in self.keywords):
            return set()
        return {m for m in self.matcher(text) if m in self.prices}

    def display(self, model):
        return self.prefix + model


APPLE = Brand("apple", "iPhone", "iphone", ("iphone", "i phone"), APPLE_MIN_PRICES,
              APPLE_ORDER, lambda t: set(), prefix="iPhone ", shorthand=True)
SAMSUNG = Brand("samsung", "Samsung", "samsung galaxy", ("samsung", "galaxy"),
                SAMSUNG_MIN_PRICES, SAMSUNG_ORDER, _samsung, shorthand=True)
XIAOMI = Brand("xiaomi", "Xiaomi", "xiaomi", ("xiaomi",), XIAOMI_MIN_PRICES, XIAOMI_ORDER, _xiaomi)
GOOGLE = Brand("google", "Google Pixel", "google pixel", ("pixel",), PIXEL_MIN_PRICES,
               PIXEL_ORDER, _pixel)
ONEPLUS = Brand("oneplus", "OnePlus", "oneplus", ("oneplus", "one plus"), ONEPLUS_MIN_PRICES,
                ONEPLUS_ORDER, _oneplus)

BRANDS = [APPLE, SAMSUNG, XIAOMI, GOOGLE, ONEPLUS]
BY_KEY = {b.key: b for b in BRANDS}
# Modelio ID -> gamintojas. Apple ID be priesdelio, tad jie ir lieka tokie, kokie buvo.
BRAND_OF = {m: b for b in BRANDS for m in b.prices}


def enabled(config_brands=None):
    """Ijungti gamintojai ta tvarka, kokia nurodyta. Tuscia / nezinoma = visi."""
    keys = [str(k).strip().lower() for k in (config_brands or []) if str(k).strip()]
    chosen = [BY_KEY[k] for k in keys if k in BY_KEY]
    return chosen or list(BRANDS)


def brand_of(model):
    return BRAND_OF.get(model)


def display(model):
    """Modelio ID -> kaip rodyti zmogui ('13 Pro' -> 'iPhone 13 Pro')."""
    brand = BRAND_OF.get(model)
    if brand:
        return brand.display(model)
    return f"iPhone {model}" if model else str(model or "")


def min_price(model):
    """APSAUGOS riba. Nepriklauso nuo rinkos kainos ir nuo matavimu."""
    brand = BRAND_OF.get(model)
    return float(brand.prices[model]) if brand else 0.0


def prior(model):
    """Ka manom, kad modelis vertas, PRIES siandienos skelbimus.

    Du sluoksniai, ir skirtumas tarp ju matomas is `Prior.samples`:
      MEASURED  tikra mediana su imtimi (samples > 0)
      spejimas  riba / GUESS_RATIO (samples == 0)

    Iki v49.1 abu atvejai grazindavo paprasta skaiciu, tad `confidence.py`
    negalejo atskirti „183 EUR is 1417 skelbimu" nuo „200 EUR, nes riba 90"."""
    found = MEASURED.get(model)
    if found:
        return Prior(float(found[0]), samples=int(found[1]),
                     measured=MEASURED_ON, method="live-median")
    floor = min_price(model)
    if not floor:
        return None
    return Prior(round(floor / GUESS_RATIO / 5) * 5, samples=0, method="guess")


def order(brands=None):
    """Visi modeliai tvarkingai: pagal gamintoja, viduje – nuo seniausio."""
    out = []
    for brand in (brands or BRANDS):
        out += list(brand.order)
    return out


def find_models(text, brands=None):
    """Visi modeliai, paminėti tekste (visu ijungtu gamintoju)."""
    t = fold((text or "").lower())
    found = set()
    for brand in (brands or BRANDS):
        found |= brand.find(t)
    return found


def queries(brands=None):
    """Paieskos frazes ijungtiems gamintojams (viena fraze = visi to gamintojo modeliai)."""
    return [b.query for b in (brands or BRANDS)]
