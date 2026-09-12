# -*- coding: utf-8 -*-
"""
Vinted deal finder v4 — taisoma kainu nuskaitymas (API kainos formatas pasikeite).
"""

import requests
import json
import os
import time
import re
import html
import random

# ========== SUSIKONFIGUROK SITAS EILUTES ==========
# BOT_TOKEN ir CHAT_ID imami is aplinkos kintamuju (GitHub Secrets).
# Repo -> Settings -> Secrets and variables -> Actions -> sukurk BOT_TOKEN ir CHAT_ID.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID", "")

# ================== KONFIGURACIJA ==================
# VISKAS kraunama is config.json failo (saugomo tame paciame repo).
# Ten keici: modelius, kainu ribas, filtrus – kodo liesti NEREIKIA.
# Jei config.json nera ar sugadintas – naudojami apatiniai numatytieji.

DEFAULTS = {
    "MODELS": [
        {"query": "iPhone 13",     "min_price": 100, "max_price": 160},
        {"query": "iPhone 13 Pro", "min_price": 100, "max_price": 200},
        {"query": "iPhone 14",     "min_price": 100, "max_price": 200},
        {"query": "iPhone 14 Pro", "min_price": 100, "max_price": 350},
    ],
    "BLACKLIST_WORDS": [
        "case", "deklas", "cover", "custodia", "coque",
        "ladegerat", "charger", "kroviklis", "cable", "laidas",
        "box", "dezute", "schutzglas", "glass",
        "hulle", "folija", "grudintas",
    ],
    "ALLOWED_COUNTRY_CODES": ["LT"],
    "REQUIRE_KNOWN_COUNTRY": False,
    "ONLY_LITHUANIAN_TEXT": True,
    "PRICE_LAST_DIGITS": [],
    # Kuriu bukliu skelbimus leisti. Tuscia = leidziam visas. Priimami arba
    # skaitiniai ID (2=Labai gera, 3=Gera), arba lietuviski pavadinimai,
    # pvz. ["Labai gera", "Gera"] - kaip Vinted svetaines "bukle" filtras.
    "ALLOWED_CONDITIONS": [],
    # Jei False - visai neuzklausiam /api/v2/users/{id} (pardavejo reputacija,
    # tikslesnis salies kodas). Isjunk, jei itari, kad sis endpoint'as
    # sukelia blokavima/rate limit - liks tik sena (nemokama) salies euristika
    # ir reputacija rodys "nera duomenu".
    "FETCH_SELLER_INFO": True,
    "PAGES": 3,
    "SLEEP_SECONDS": 3,
    "DRY_RUN": False,
    "DEBUG": False,
    "SEEN_MAX_AGE_DAYS": 7,
    "SEEN_MAX_ENTRIES": 10000,
    # Realus patikrinimas ivyks ne tiksliai kas N minuciu, o atsitiktiniu
    # intervalu tarp situ dvieju reiksmiu (minutemis).
    "CHECK_INTERVAL_MIN_MINUTES": 15,
    "CHECK_INTERVAL_MAX_MINUTES": 17,
    # Rinkos kainos istorija: kiek dienu laikyti taskus ir kiek daugiausiai
    # saugoti vienai (modelis, bukle) porai.
    "PRICE_HISTORY_MAX_AGE_DAYS": 30,
    "PRICE_HISTORY_MAX_PER_KEY": 1000,
}

CONFIG_FILE = "config.json"
SEEN_FILE = "seen.json"
PRICE_HISTORY_FILE = "price_history.json"


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            if isinstance(user_cfg, dict):
                cfg.update({k: v for k, v in user_cfg.items() if k in cfg})
                print(f"Konfiguracija ikelta is {CONFIG_FILE}")
            else:
                print(f"! {CONFIG_FILE} nera JSON objektas – naudojami numatytieji.")
        except Exception as e:
            print(f"! Nepavyko nuskaityti {CONFIG_FILE} ({e}) – naudojami numatytieji.")
    else:
        print(f"! {CONFIG_FILE} nerastas – naudojami numatytieji.")
    return cfg


_CFG = load_config()

MODELS = _CFG["MODELS"]
BLACKLIST_WORDS = _CFG["BLACKLIST_WORDS"]
ALLOWED_COUNTRY_CODES = list(_CFG["ALLOWED_COUNTRY_CODES"])
ALLOWED_CONDITIONS = list(_CFG.get("ALLOWED_CONDITIONS") or [])
REQUIRE_KNOWN_COUNTRY = bool(_CFG["REQUIRE_KNOWN_COUNTRY"])
ONLY_LITHUANIAN_TEXT = bool(_CFG["ONLY_LITHUANIAN_TEXT"])
FETCH_SELLER_INFO = bool(_CFG.get("FETCH_SELLER_INFO", True))
PRICE_LAST_DIGITS = set(_CFG["PRICE_LAST_DIGITS"])
PAGES = int(_CFG["PAGES"])
SLEEP_SECONDS = int(_CFG["SLEEP_SECONDS"])
DRY_RUN = bool(_CFG["DRY_RUN"])
DEBUG = bool(_CFG["DEBUG"])
SEEN_MAX_AGE_DAYS = int(_CFG["SEEN_MAX_AGE_DAYS"])
PRICE_HISTORY_MAX_AGE_DAYS = int(_CFG["PRICE_HISTORY_MAX_AGE_DAYS"])
PRICE_HISTORY_MAX_PER_KEY = int(_CFG["PRICE_HISTORY_MAX_PER_KEY"])
SEEN_MAX_ENTRIES = int(_CFG["SEEN_MAX_ENTRIES"])
CHECK_INTERVAL_MIN_MINUTES = float(_CFG["CHECK_INTERVAL_MIN_MINUTES"])
CHECK_INTERVAL_MAX_MINUTES = float(_CFG["CHECK_INTERVAL_MAX_MINUTES"])
# ===================================================

BASE = "https://www.vinted.lt"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "lt-LT,lt;q=0.9,en;q=0.8",
    "Referer": BASE + "/",
}

session = requests.Session()
_debug_price_printed = False
_debug_user_printed = False


def init_session():
    try:
        r = session.get(BASE + "/", headers=HEADERS, timeout=20)
        print(f"Sesija pradeta (statusas {r.status_code})")
    except Exception as e:
        print(f"! Nepavyko pradeti sesijos: {e}")
    time.sleep(2)


def load_seen():
    """Grazina zodyna {skelbimo_id: laiko_zyme}.

    Palaiko ir senaji formata (paprastas ID sarasas) – konvertuoja automatiskai."""
    if not os.path.exists(SEEN_FILE):
        return {}
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return {}
        data = json.loads(content)
        now = time.time()
        if isinstance(data, list):          # senas formatas
            return {str(x): now for x in data}
        if isinstance(data, dict):          # naujas formatas
            out = {}
            for k, v in data.items():
                try:
                    out[str(k)] = float(v)
                except (ValueError, TypeError):
                    out[str(k)] = now
            return out
        return {}
    except Exception as e:
        print(f"! {SEEN_FILE} sugadintas ({e}), pradedama nuo tuscio.")
        return {}


def prune_seen(seen):
    """Istrina: (a) idesenes nei SEEN_MAX_AGE_DAYS; (b) pertekliu virs SEEN_MAX_ENTRIES."""
    now = time.time()
    limit = SEEN_MAX_AGE_DAYS * 86400
    pruned = {k: v for k, v in seen.items() if now - v <= limit}
    if len(pruned) > SEEN_MAX_ENTRIES:
        by_time = sorted(pruned.items(), key=lambda kv: kv[1], reverse=True)
        pruned = dict(by_time[:SEEN_MAX_ENTRIES])
    return pruned


def save_seen(seen):
    seen = prune_seen(seen)
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f)


LAST_RUN_FILE = "last_run.json"


def should_run_now():
    """Vietoj to, kad tikrintume skelbimus TIKSLIAI kas N minuciu, laukiam
    ATSITIKTINIO intervalo is [CHECK_INTERVAL_MIN_MINUTES, CHECK_INTERVAL_MAX_MINUTES].
    Jei nuo paskutinio TIKRO patikrinimo dar nepraejo tiek laiko - grazina False
    ir main() is karto baigia darba (jokiu API uzklausu, jokio Telegram).

    SVARBU: kad tai realiai duotu 15-17 min efektyvu intervala, GitHub Actions
    workflow .yml faile cron TURI vykti DAZNIAU nei 15 min (pvz. '*/5 * * * *')
    - sitas kodas tik SPRENDZIA, ar praleisti konkretu iskvietima, jis pats
    savęs periodiskai neiskviecia. .yml failo neturiu, tad ji reikia
    pakoreguoti atskirai repo nustatymuose."""
    now = time.time()
    try:
        with open(LAST_RUN_FILE, "r", encoding="utf-8") as f:
            last_run = float(json.load(f).get("last_run", 0))
    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError, OSError):
        last_run = 0

    target_gap = random.uniform(CHECK_INTERVAL_MIN_MINUTES * 60, CHECK_INTERVAL_MAX_MINUTES * 60)
    elapsed = now - last_run
    if elapsed < target_gap:
        print(f"Dar ne laikas tikrinti (praejo {elapsed/60:.1f} min., reikia ~{target_gap/60:.1f} min.) - praleidziama.")
        return False

    try:
        with open(LAST_RUN_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_run": now}, f)
    except Exception as e:
        print(f"! Nepavyko issaugoti {LAST_RUN_FILE}: {e}")
    return True


def fetch_page_with_retry(query, page, max_retries=3):
    """Uzklausia viena puslapi su pakartojimais:
    - 401/403 -> atnaujina sesija ir bando dar karta (sesija galejo pasenti)
    - 429     -> ilgesne pauze ir bando dar karta (rate limiting)
    - 5xx / tinklo klaida -> backoff ir bando dar karta
    Grazina items sarasa, tuscia sarasa (nebepuslapiuojam) arba None (viskas zlugo)."""
    url = BASE + "/api/v2/catalog/items"
    params = {"search_text": query, "per_page": 96, "page": page}
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(url, params=params, headers=HEADERS, timeout=20)
            if resp.status_code in (401, 403):
                print(f"  ! {resp.status_code} – atnaujinu sesija (bandymas {attempt}/{max_retries})...")
                init_session()
                time.sleep(SLEEP_SECONDS)
                continue
            if resp.status_code == 429:
                wait = SLEEP_SECONDS * attempt * 2
                print(f"  ! 429 per daug uzklausu – laukiu {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                print(f"  ! Serverio klaida {resp.status_code} (bandymas {attempt}/{max_retries})")
                time.sleep(SLEEP_SECONDS * attempt)
                continue
            if resp.status_code != 200:
                print(f"  ! '{query}' p.{page}: HTTP {resp.status_code}: {resp.text[:200]}")
                return None
            data = resp.json()
            if not isinstance(data, dict):
                print(f"  ! '{query}' p.{page}: netiketas atsakymo formatas (ne JSON objektas)")
                return None
            batch = data.get("items") or []
            if not isinstance(batch, list):
                print(f"  ! '{query}' p.{page}: 'items' nera sarasas – API struktura galejo pasikeisti")
                return None
            return batch
        except (requests.RequestException, ValueError) as e:
            print(f"  ! Tinklo/JSON klaida (bandymas {attempt}/{max_retries}): {e}")
            time.sleep(SLEEP_SECONDS * attempt)
    print(f"  ! Visi {max_retries} bandymai nepavyko: '{query}' p.{page}")
    return None


def fetch_items(query, pages):
    items = []
    for page in range(1, pages + 1):
        batch = fetch_page_with_retry(query, page)
        if batch is None:      # viskas zlugo – stabdome si modeli
            break
        if not batch:          # daugiau nera – stabdome puslapiavima
            break
        items.extend(batch)
        time.sleep(SLEEP_SECONDS)
    return items


_debug_og_printed = False
DETAIL_SLEEP_SECONDS = 1.0

_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_PROPERTY_RE = re.compile(r'property=["\']([^"\']+)["\']', re.IGNORECASE)
_CONTENT_RE = re.compile(r'content=["\']([^"\']*)["\']', re.IGNORECASE)


def _parse_og_tags(html_text):
    """Israsko visas 'og:*' meta zymas is HTML teksto, nepriklausomai nuo
    property/content atributu tvarkos tage."""
    og = {}
    for tag in _META_TAG_RE.findall(html_text):
        pm = _PROPERTY_RE.search(tag)
        if not pm or not pm.group(1).startswith("og:"):
            continue
        cm = _CONTENT_RE.search(tag)
        if not cm:
            continue
        key = pm.group(1)[3:]  # nuimam "og:" prefiksa
        og[key] = html.unescape(cm.group(1))
    return og


def fetch_item_page_og(item_id, url_path, max_bytes=200_000):
    """Katalogo/paieskos API skelbimo objekte NERA aprasymo, o atskiras JSON
    endpoint'as (/api/v2/items/{id}) Vinted DAZNIAUSIAI BLOKUOJA (403, anti-bot
    apsauga - tai patvirtinta ir populiariuose atviro kodo Vinted scraper'iuose).

    Todel aprasyma skaitome is vieso skelbimo puslapio OpenGraph <meta> zymu
    (title/description/image/url), kurios visada yra HTML <head> dalyje - siam
    keliui pakanka atsiusti tik pirmus kelis desimtis KB puslapio, o ne visa
    JSON API atsakyma, tad jis maziau panasus i "bot" elgesi.

    DEMESIO: jei og:description formatas skiriasi nuo tiketo (pvz. Vinted
    kartais dubliuoja kaina ar kt. teksta prieky), DEBUG isvestis parodys
    tiksliai, ka gavome - pagal tai galesim koreguoti."""
    global _debug_og_printed
    full_url = BASE + url_path if url_path.startswith("/") else url_path
    try:
        resp = session.get(full_url, headers=HEADERS, timeout=20, stream=True)
        if resp.status_code != 200:
            if DEBUG:
                print(f"  [DEBUG] skelbimo puslapio {item_id} uzklausa: HTTP {resp.status_code}")
            resp.close()
            return {}
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=8192):
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total >= max_bytes:
                break
        resp.close()
        html_text = b"".join(chunks).decode("utf-8", errors="ignore")
        og = _parse_og_tags(html_text)
        if DEBUG and not _debug_og_printed:
            print(f"  [DEBUG] skelbimo {item_id} OG duomenys (is puslapio <head>):")
            print(" ", json.dumps(og, ensure_ascii=False))
            _debug_og_printed = True
        return og
    except Exception as e:
        if DEBUG:
            print(f"  [DEBUG] nepavyko gauti skelbimo {item_id} puslapio: {e}")
        return {}


def _to_float(v):
    """Saugiai vercia reiksme i float; None jei nepavyksta."""
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ".").strip())
    except (ValueError, TypeError):
        return None


def get_price(item):
    """Lankstus kainos nuskaitymas – bando kelis formatus ir laukus,
    kad kintant API strukturai kuo ilgiau veiktu be taisymu:
    - {"amount": "299.0"} (dict)
    - "29900" (string centais)
    - 29900 (int centais)
    - atsarginiai laukai price_amount / amount / total_item_price"""
    global _debug_price_printed
    p = item.get("price")
    if DEBUG and not _debug_price_printed:
        print(f"  [DEBUG] price: {repr(p)}")
        _debug_price_printed = True
    if isinstance(p, dict):
        for k in ("amount", "value", "price"):
            f = _to_float(p.get(k))
            if f is not None:
                return f
        for v in p.values():
            f = _to_float(v)
            if f is not None:
                return f
        return None
    if p is not None and str(p).strip() != "":
        s = str(p).strip()
        try:
            return int(s) / 100.0          # senas formatas – centai
        except ValueError:
            f = _to_float(s)
            if f is not None:
                return f
    for key in ("price_amount", "amount", "total_item_price", "numeric_price"):
        if key in item:
            f = _to_float(item[key])
            if f is not None:
                return f
    return None


def get_country_code(item):
    """Grazina pardavejo salies koda is profilio URL domeno.
    Pvz. https://www.vinted.pl/member/... -> "PL",  vinted.fr -> "FR",
    vinted.co.uk -> "UK". Jei nepavyksta - None.

    DEMESIO: si euristika gali neveikti, jei API visada grazina profile_url
    su tuo paciu domenu, per kuri siunciama uzklausa (t.y. visada vinted.lt),
    nepriklausomai nuo tikros pardavejo salies. Jei DEBUG=True, pirmam
    skelbimui bus atspausdintas visas 'user' objektas - patikrink, ar jame
    yra kitas laukas (pvz. country_id / country_title), kuri reiketu naudoti
    vietoj profile_url domeno."""
    global _debug_user_printed
    user = item.get("user") or {}
    if DEBUG and not _debug_user_printed:
        print("  [DEBUG] pilnas 'user' objektas (ieskok salies lauko):")
        print(" ", json.dumps(user, ensure_ascii=False))
        _debug_user_printed = True
    url = user.get("profile_url") or ""
    m = re.search(r"vinted\.([a-z.]+)/", url)
    if not m:
        return None
    domain = m.group(1)          # pvz. "pl", "fr", "co.uk"
    if domain == "co.uk":
        return "UK"
    return domain.upper()


# Vinted "status_id" reiksmes stabilios visose salyse (patvirtinta per keliu
# nepriklausomu Vinted API dokumentacijos saltiniu).
STATUS_LABELS_LT = {
    6: "Nauja su etiketėmis",
    1: "Nauja be etikečių",
    2: "Labai gera",
    3: "Gera",
    4: "Patenkinama",
    7: "Neveikianti",
}

_debug_status_printed = False


def get_condition(item):
    """Grazina (raktas, lietuviskas_pavadinimas) bukles grupavimui/rodymui.

    Katalogo API skelbimo objekte bukle gali ateiti kaip:
    - 'status_id' (skaitinis, stabilus visose rinkose - pageidautina)
    - 'status'    (jau tekstinis pavadinimas, lokalizuotas pagal Accept-Language)
    DEMESIO: tiksliai, kuris laukas realiai ateina is /api/v2/catalog/items,
    neturiu galimybes pats patikrinti - DEBUG=True parodys abu laukus pirmam
    skelbimui, kad galetume patvirtinti/pataisyti."""
    global _debug_status_printed
    status_id = item.get("status_id")
    status_text = item.get("status")
    if DEBUG and not _debug_status_printed:
        print(f"  [DEBUG] bukle: status={status_text!r}, status_id={status_id!r}")
        _debug_status_printed = True

    label = STATUS_LABELS_LT.get(status_id) if isinstance(status_id, int) else None
    if not label:
        label = status_text if isinstance(status_text, str) and status_text else "nežinoma"

    key = status_id if isinstance(status_id, int) else label
    return key, label


# --- Kalbos aptikimas -------------------------------------------------
# Tikslas: praleisti tik lietuviskus (arba kalbos pozymiu neturincius)
# skelbimus, atmesti aiskiai uzsienietiskus.

import re as _re


def _word_regex(words):
    """Sudaro viena regex su \\b riboms is zodziu/fraziu sarasa (case jau lower)."""
    parts = sorted((_re.escape(w) for w in words), key=len, reverse=True)
    return _re.compile(r"\b(?:" + "|".join(parts) + r")\b")


# Raidziu, kuriu nera lietuviu kalboje (beveik visada = lenkiska kalba)
POLISH_ONLY_CHARS = set("łńśźżć")
POLISH_WORDS = [
    "sprzedam", "sprzedaje", "kupie", "telefon", "oryginalny", "oryginalne",
    "stan", "stanie", "wysylka", "wysylke", "zestaw", "paragon", "faktura",
    "nieuszkodzony", "uszkodzony", "ladny", "przesylka", "polecam", "okazja",
    "komplet", "kondycja", "sprawny", "sprawna", "pudelko", "gwarancja",
    "cena", "pekniety", "peknieta", "zbite", "zbita", "wyswietlacz",
    "bateria", "akumulator", "dziala", "pilne", "negocjacje", "akcesoria",
]
_POLISH_RE = _word_regex(POLISH_WORDS)

# Raidziu, kuriu nera lietuviu kalboje, bet yra latviu
LATVIAN_ONLY_CHARS = set("āēīōūļņģ")

# Vokiskos raides ir dazni zodziai
GERMAN_ONLY_CHARS = set("äöüß")
GERMAN_WORDS = [
    "verkaufe", "neuwertig", "versand", "zustand", "gebraucht",
    "originalverpackung", "rechnung", "funktioniert", "einwandfrei",
]
_GERMAN_RE = _word_regex(GERMAN_WORDS)

# Dazni angliski zodziai/frazes skelbimuose
ENGLISH_WORDS = [
    "selling", "brand new", "like new", "shipping", "great condition",
    "excellent condition", "as new", "no issues", "works perfectly",
]
_ENGLISH_RE = _word_regex(ENGLISH_WORDS)

# Lietuviski pozymiai – jei jie yra, skelbimas laikomas lietuvisku
# (net jei atsitiktinai atsirado viena "uzsienietiska" raide).
LITHUANIAN_WORDS = [
    "parduodu", "pardodu", "parduosiu", "bukle", "puiki", "puikus", "puikioje",
    "gera", "geras", "geros", "tvarkingas", "tvarkinga", "veikia", "kaina",
    "euru", "originalus", "originali", "idealios", "baterija", "irasyta",
    "naujas", "nauja", "naudotas", "naudota", "su deklu", "deklas pridedamas",
    "be defektu", "be jokiu defektu", "kokybiskas", "mazai naudotas",
]
_LITHUANIAN_RE = _word_regex(LITHUANIAN_WORDS)


def _has_cyrillic(text):
    return any("\u0400" <= ch <= "\u04ff" for ch in text)


def detect_foreign_language(*texts):
    """Grazina 'PL' / 'LV' / 'DE' / 'RU' / 'EN' jei tekstas atrodo parasytas ne
    lietuviskai, arba None jei atrodo lietuviskas arba kalbos nustatyti
    negalima (per mazai teksto / vien modelio pavadinimas).

    Sie zodziu sarasai sudaryti is zodziu, kuriu praktiskai nepasitaiko
    lietuviu kalboje, tad UZTENKA VIENO atitikimo (naudojant \\b zodzio
    ribas, kad neuzkabintu dalies kito zodzio)."""
    t = " ".join(x for x in texts if x).lower()
    if not t:
        return None

    # Jei yra aiskiu lietuvisku pozymiu – laikome lietuvisku (nepriklausomai
    # nuo atsitiktiniu raidziu). Tai apsaugo nuo klaidingu atmetimu, kai
    # aprasyme nera "tikru" lietuvisku raidziu, pvz. parasyta svelnai.
    if _LITHUANIAN_RE.search(t):
        return None

    if _has_cyrillic(t):
        return "RU"

    if (sum(1 for ch in t if ch in POLISH_ONLY_CHARS) >= 1) or _POLISH_RE.search(t):
        return "PL"

    if (sum(1 for ch in t if ch in GERMAN_ONLY_CHARS) >= 1) or _GERMAN_RE.search(t):
        return "DE"

    if sum(1 for ch in t if ch in LATVIAN_ONLY_CHARS) >= 1:
        return "LV"

    if _ENGLISH_RE.search(t):
        return "EN"

    return None


def stars_line(reputation, feedback_count):
    """Verčia feedback_reputation (0..1) i žvaigždutes, pvz. '★★★☆☆ (3.1/5, 33 atsiliep.)'."""
    if reputation is None:
        return "nėra duomenų"
    n = max(0, min(5, round(reputation * 5)))
    stars = "\u2605" * n + "\u2606" * (5 - n)
    line = stars + f" ({reputation * 5:.1f}/5"
    if feedback_count is not None:
        line += f", {feedback_count} atsiliep."
    return line + ")"


def get_seller_info(user_info):
    """Grazina (reputation 0..1 arba None, feedback_count arba None) is
    PILNO pardavejo profilio atsakymo (fetch_user_info rezultato) - kataloginiame
    'user' objekte siu lauku NERA (patvirtinta DEBUG isvestimi)."""
    try:
        rep = float(user_info.get("feedback_reputation"))
    except (TypeError, ValueError):
        rep = None
    try:
        cnt = int(user_info.get("feedback_count"))
    except (TypeError, ValueError):
        cnt = None
    return rep, cnt


_debug_userinfo_printed = False


def fetch_user_info(user_id):
    """Pilnas pardavejo profilis - /api/v2/users/{id}. Katalogo skelbimuose
    ideklota 'user' objekte (id/login/profile_url/photo/business) NERA
    feedback_reputation/feedback_count/country_code lauku - jie yra TIK siame
    atskirame endpoint'e (patvirtinta per keliu nepriklausomu Vinted API
    "wrapper'iu" dokumentacija). Tai KITAS endpoint'as nei /api/v2/items/{id}
    (kuris zinomai blokuojamas 403) - garantijos, kad ir sitas neblokuojamas,
    neturiu, tad DEBUG parodys realu atsakyma pirmam iskvietimui."""
    global _debug_userinfo_printed
    if not user_id:
        return {}
    url = f"{BASE}/api/v2/users/{user_id}"
    try:
        resp = session.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            if DEBUG:
                print(f"  [DEBUG] pardavejo {user_id} info uzklausa: HTTP {resp.status_code}")
            return {}
        data = resp.json()
        info = data.get("user") if isinstance(data, dict) else None
        if info is None and isinstance(data, dict):
            info = data
        if DEBUG and not _debug_userinfo_printed:
            print(f"  [DEBUG] pardavejo {user_id} pilnas atsakymas:")
            print(" ", json.dumps(data, ensure_ascii=False)[:2000])
            _debug_userinfo_printed = True
        return info or {}
    except Exception as e:
        if DEBUG:
            print(f"  [DEBUG] nepavyko gauti pardavejo {user_id} info: {e}")
        return {}


def load_price_history():
    """Grazina {raktas: [(kaina, laiko_zyme), ...]} - kaupiama per VISUS
    paleidimus (ne tik siandienos), kad rinkos mediana remtusi realiu dideliu
    imties dydziu, o ne vieno paleidimo ~200-300 skelbimu."""
    if not os.path.exists(PRICE_HISTORY_FILE):
        return {}
    try:
        with open(PRICE_HISTORY_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {k: [(float(p), float(t)) for p, t in v] for k, v in raw.items()}
    except (json.JSONDecodeError, ValueError, TypeError, OSError):
        print(f"! {PRICE_HISTORY_FILE} sugadintas, pradedama nuo tuscios istorijos.")
        return {}


def save_price_history(history):
    """Apkarpo pasenusius taskus (> PRICE_HISTORY_MAX_AGE_DAYS) ir perteklu
    virs PRICE_HISTORY_MAX_PER_KEY, tada issaugo."""
    now = time.time()
    limit = PRICE_HISTORY_MAX_AGE_DAYS * 86400
    pruned = {}
    for key, points in history.items():
        fresh_points = [(p, t) for p, t in points if now - t <= limit]
        if len(fresh_points) > PRICE_HISTORY_MAX_PER_KEY:
            fresh_points.sort(key=lambda pt: pt[1], reverse=True)
            fresh_points = fresh_points[:PRICE_HISTORY_MAX_PER_KEY]
        if fresh_points:
            pruned[key] = fresh_points
    with open(PRICE_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(pruned, f)
    return pruned


def record_prices(history, key, prices):
    """Prideda siandien pastebetas kainas prie sukauptos istorijos (in-place)."""
    now = time.time()
    history.setdefault(key, [])
    history[key].extend((p, now) for p in prices)


def market_median(prices):
    """Apkarpyta mediana – nukertame 10% pigiausių ir 10% brangiausių,
    kad vienetiniai 'sukčių' ar šlamšto įkainiai nepaveiktų įverčio."""
    if not prices:
        return None
    prices = sorted(prices)
    cut = max(1, int(len(prices) * 0.1)) if len(prices) >= 10 else 0
    trimmed = prices[cut:len(prices) - cut] or prices
    mid = len(trimmed) // 2
    if len(trimmed) % 2:
        return trimmed[mid]
    return (trimmed[mid - 1] + trimmed[mid]) / 2.0


def is_junk(title):
    t = title.lower()
    return any(w in t for w in BLACKLIST_WORDS)


def send_telegram(text):
    url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code != 200:
            print(f"  ! Telegram klaida: {r.text[:150]}")
    except Exception as e:
        print(f"  ! Nepavyko issiusti Telegram: {e}")


def send_telegram_photo(photo_url, caption):
    """Siunčia nuotrauką su aprašu (kaip pavyzdyje). Jei nepavyksta – tik tekstą."""
    if not photo_url:
        send_telegram(caption)
        return
    url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendPhoto"
    payload = {"chat_id": CHAT_ID, "photo": photo_url, "caption": caption,
               "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=payload, timeout=15)
        if r.status_code != 200:
            print(f"  ! Telegram photo klaida: {r.text[:120]} – siunčiu be nuotraukos")
            send_telegram(caption)
    except Exception as e:
        print(f"  ! Nepavyko issiusti nuotraukos: {e}")
        send_telegram(caption)


def format_alert_message(a):
    desc = " ".join((a["desc"] or "").split())[:140]
    desc_esc = html.escape(desc) + ("…" if len((a["desc"] or "")) > 140 else "")

    lines = [
        "<b>" + html.escape(a["query"]) + "</b> | <b>" + f'{a["price"]:.0f} €' + "</b>",
    ]
    if desc_esc:
        lines.append(desc_esc)
    lines.append("")
    lines.append("<b>📦 Būklė:</b> " + html.escape(a["condition"]))
    lines.append("<b>⭐ Pardavėjas:</b> " + stars_line(a["rep"], a["cnt"]))
    if a["market"]:
        lines.append("<b>📊 Rinkos vertė (istorinė, ta pati būklė):</b> ~" + f'{a["market"]:.0f} €')
    lines.append("")
    lines.append('<a href="' + a["url"] + '">Atidaryti skelbimą</a>')
    return "\n".join(lines)


def main():
    if not should_run_now():
        return

    if not BOT_TOKEN or not CHAT_ID:
        print("Nenurodyti BOT_TOKEN / CHAT_ID (GitHub Secrets)!")
        return

    init_session()

    seen = load_seen()
    new_seen = dict(seen) # <-- IŠTAISYTA ČIA
    price_history = load_price_history()
    total_fetched = 0
    total_alerts = 0
    
    for model in MODELS:
        q = model["query"]
        print(f"Tikrinama: '{q}' ({model['min_price']}-{model['max_price']} EUR)...")
        items = fetch_items(q, PAGES)
        total_fetched += len(items)
        fresh = 0

        # Rinkos verte: MEDIANA ATSKIRAI KIEKVIENAI BUKLEI, skaiciuojama is
        # SUKAUPTOS ISTORIJOS (visu ankstesniu paleidimu per PRICE_HISTORY_MAX_AGE_DAYS
        # dienu), o ne tik siandienos ~200-300 skelbimu - imtis daug didesne
        # ir stabilesne, o vis tiek tik LT rinkoje (be papildomo apkrovimo).
        prices_by_condition = {}
        for it in items:
            if isinstance(it, dict):
                ap = get_price(it)
                if ap and ap > 0:
                    ckey, _ = get_condition(it)
                    prices_by_condition.setdefault(ckey, []).append(ap)
        for ckey, prices in prices_by_condition.items():
            record_prices(price_history, f"{q}|{ckey}", prices)
        market_by_condition = {
            ckey: market_median([p for p, _ in price_history.get(f"{q}|{ckey}", [])])
            for ckey in prices_by_condition
        }
        excluded_by_country = 0
        excluded_foreign = 0
        excluded_price_digit = 0
        excluded_condition = 0

        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id") or item.get("item_id") or item.get("entity_id")
            if item_id is None:
                continue
            item_id = str(item_id)
            if item_id in seen:
                continue
            new_seen[item_id] = time.time()
            save_seen(new_seen)

            price = get_price(item)
            if price is None or not (model["min_price"] <= price <= model["max_price"]):
                continue

            if PRICE_LAST_DIGITS and int(price) % 10 not in PRICE_LAST_DIGITS:
                excluded_price_digit += 1
                continue

            # Bukle jau yra kataloginiame atsakyme - papildomos uzklausos nereikia.
            cond_key, cond_label = get_condition(item)
            # SAUGIKLIS: jei bukles nustatyti nepavyko ("nežinoma" - reiskia
            # realus API laukas neatitiko lauktos formos), NEATMETAME - kitaip
            # baltasis sarasas atmestu VISKA, jei musu spejimas apie lauka klaidingas.
            if ALLOWED_CONDITIONS and cond_label != "nežinoma" and cond_key not in ALLOWED_CONDITIONS and cond_label not in ALLOWED_CONDITIONS:
                excluded_condition += 1
                continue

            title = item.get("title") or item.get("name") or "?"
            url_path = item.get("url") or item.get("path") or item.get("web_url") or ""
            full_url = BASE + url_path if url_path.startswith("/") else url_path

            # Pirminis (nemokamas, be tinklo) salies patikrinimas - filtruojame
            # anksti, kad nereiketu tinklo uzklausu skelbimams, kurie bet kokiu
            # atveju bus atmesti.
            country = get_country_code(item)
            country_ok = (country in ALLOWED_COUNTRY_CODES) if country else (not REQUIRE_KNOWN_COUNTRY)
            if not country_ok:
                excluded_by_country += 1
                if DEBUG:
                    print(f"  [DEBUG] atmesta (salis={country}): {title[:60]}")
                continue

            # Katalogo API nera aprasymo, o JSON detaliu endpoint'as Vinted
            # dazniausiai blokuoja (403). Todel aprasyma skaitome is vieso
            # skelbimo puslapio OpenGraph zymu.
            og = fetch_item_page_og(item_id, url_path)
            time.sleep(DETAIL_SLEEP_SECONDS)
            if og.get("title"):
                title = og["title"]
            description = og.get("description") or ""

            if ONLY_LITHUANIAN_TEXT:
                lang = detect_foreign_language(title, description)
                if lang:
                    excluded_foreign += 1
                    if DEBUG:
                        print(f"  [DEBUG] atmesta (kalba={lang}, salis={country}): {title[:60]}")
                    continue

            if is_junk(title):
                continue

            # Pardavejo profilis (reputacija + patikimesnis salies kodas) -
            # siciama TIK dabar, skelbimams, kurie jau praejo visus kitus
            # filtrus. Tai naujausias, PAPILDOMAS endpoint'as - jei jis kelia
            # problemu (blokavimas/rate limit), issijunk per FETCH_SELLER_INFO=false.
            user_info = {}
            if FETCH_SELLER_INFO:
                catalog_user = item.get("user") or {}
                user_id = catalog_user.get("id")
                user_info = fetch_user_info(user_id)
                time.sleep(DETAIL_SLEEP_SECONDS)
                real_country = (user_info.get("country_code") or "").upper() or None
                if real_country and real_country not in ALLOWED_COUNTRY_CODES:
                    excluded_by_country += 1
                    if DEBUG:
                        print(f"  [DEBUG] atmesta (tikslesne salis={real_country}): {title[:60]}")
                    continue

            rep, cnt = get_seller_info(user_info)
            photo = og.get("image") or ""
            if photo.startswith("/"):
                photo = BASE + photo
            a = {
                "query": q, "title": title, "price": price,
                "url": full_url, "desc": description,
                "rep": rep, "cnt": cnt,
                "market": market_by_condition.get(cond_key), "photo": photo,
                "condition": cond_label,
            }
            fresh += 1
            total_alerts += 1
            if DEBUG:
                print(f"  [DEBUG] PRIIMTA (salis={country}, bukle={cond_label}): {title[:60]}")

            # Siunciame IS KARTO, kai tik skelbimas praeina visus filtrus -
            # nebelaukiame, kol patikrinsim visus modelius, ir nebeberikiuojame
            # pagal kaina (zinutes ateis ta tvarka, kokia skelbimai rasti).
            if DRY_RUN:
                print(f"  [DRY_RUN] Rastas deal'as (NESIUNCIAMA): {q} {price:.0f} EUR: {title[:50]}")
            else:
                send_telegram_photo(photo, format_alert_message(a))
                print(f'  -> {q} {price:.0f} EUR: {title[:50]}')

        print(f"  Gauta: {len(items)}, tinkama: {fresh}, atmesta salis: {excluded_by_country}, atmesta uzsienio kalba: {excluded_foreign}, atmesta kainos skaitmuo: {excluded_price_digit}, atmesta bukle: {excluded_condition}")
        price_history = save_price_history(price_history)
        time.sleep(SLEEP_SECONDS)

    save_seen(new_seen)

    # Savaime diagnostika: jei is VISU paiesku negauta nei vieno skelbimo,
    # tai zenklas, kad Vinted galejo ka nors pakeisti – pranesame i Telegram.
    if total_fetched == 0 and not DRY_RUN and BOT_TOKEN and CHAT_ID:
        send_telegram("<b>ISPEJIMAS</b>: negauta nei vieno skelbimo is Vinted. "
                      "Galimai pasikeite API – patikrink skripto logus.")

    if total_alerts == 0:
        print("Nauju deal'u nera.")
        return

    if DRY_RUN:
        print(f"[DRY_RUN] Iso rasta {total_alerts} dealu, bet zinuciu NESIUNCIAMA.")
        print("[DRY_RUN] Pakeisk DRY_RUN = False ir paleisk dar karta.")
    else:
        print(f"Issiusta {total_alerts} alert'u.")


def run_with_guard():
    """Visa programa apsupta apsauga: bet kokia netiketa klaida – pranesimas
    i Telegram, kad Vinted pakeitus kazka nezaltum be zinios."""
    try:
        main()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        try:
            send_telegram("<b>SKRIPTAS UZLUZO</b>\n" + str(e)[:400])
        except Exception:
            pass


if __name__ == "__main__":
    run_with_guard()
