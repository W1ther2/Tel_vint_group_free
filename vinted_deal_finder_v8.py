# -*- coding: utf-8 -*-
"""
Vinted deal finder v12 — tik Lietuvos ir patikimi pardavejai, kalba atpazistama su diakritikais ir be.

Filtrai, Telegram zinuciu ir log'u isvaizda – tokie patys kaip v8.
Pakeista tik tai, kaip gaunami skelbimai is Vinted:
  - naujas katalogo adresas (senasis /api/v2/catalog/items grazina 404)
  - curl_cffi (apsimeta Chrome, kad Vinted neblokuotu). Idiegti: pip install curl_cffi
  - ispejime Telegram'e parasoma tiksli klaidos priezastis
"""

import requests
try:
    from curl_cffi import requests as cffi_requests
    USING_CFFI = True
except ImportError:
    USING_CFFI = False
import json
import os
import time
import re
import html

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
    "FILTER_BY_COUNTRY": True,         # atmesti pardavejus ne is ALLOWED_COUNTRY_CODES
    "REQUIRE_KNOWN_COUNTRY": False,    # True = atmesti ir tuos, kuriu salies nepavyko nustatyti
    "MIN_SELLER_RATING": 4.5,          # minimalus pardavejo ivertinimas (0-5)
    "MIN_SELLER_REVIEWS": 3,           # minimalus atsiliepimu skaicius
    "ONLY_LITHUANIAN_TEXT": True,      # kalbos filtras ijungtas
    "ALLOWED_LANGUAGES": ["LT", "EN"], # kokiomis kalbomis skelbimai praleidziami
    "PRICE_LAST_DIGITS": [],
    "PAGES": 3,
    "SLEEP_SECONDS": 3,
    "DRY_RUN": False,
    "DEBUG": False,
    "SEEN_MAX_AGE_DAYS": 7,
    "SEEN_MAX_ENTRIES": 10000,
}

CONFIG_FILE = "config.json"
SEEN_FILE = "seen.json"


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
FILTER_BY_COUNTRY = bool(_CFG["FILTER_BY_COUNTRY"])
REQUIRE_KNOWN_COUNTRY = bool(_CFG["REQUIRE_KNOWN_COUNTRY"])
MIN_SELLER_RATING = float(_CFG["MIN_SELLER_RATING"])
MIN_SELLER_REVIEWS = int(_CFG["MIN_SELLER_REVIEWS"])
ONLY_LITHUANIAN_TEXT = bool(_CFG["ONLY_LITHUANIAN_TEXT"])
ALLOWED_LANGUAGES = {str(x).upper() for x in _CFG["ALLOWED_LANGUAGES"]} | {"LT"}
PRICE_LAST_DIGITS = set(_CFG["PRICE_LAST_DIGITS"])
PAGES = int(_CFG["PAGES"])
SLEEP_SECONDS = int(_CFG["SLEEP_SECONDS"])
DRY_RUN = bool(_CFG["DRY_RUN"])
DEBUG = bool(_CFG["DEBUG"])
SEEN_MAX_AGE_DAYS = int(_CFG["SEEN_MAX_AGE_DAYS"])
SEEN_MAX_ENTRIES = int(_CFG["SEEN_MAX_ENTRIES"])
# ===================================================

BASE = "https://www.vinted.lt"
API_BASE = "https://api.vinted.lt"

# Katalogo adresai – bandomi is eiles, kol vienas suveikia.
CATALOG_ENDPOINTS = [
    API_BASE + "/svc-catalogue/items",   # naujas (nuo 2026-09)
    BASE + "/api/v2/catalog/items",      # senas
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "lt-LT,lt;q=0.9,en;q=0.8",
    "Referer": BASE + "/",
}

def _new_session():
    if USING_CFFI:
        return cffi_requests.Session(impersonate="chrome")
    return requests.Session()


def _headers(json_api=True):
    h = dict(HEADERS)
    if USING_CFFI:
        h.pop("User-Agent")          # curl_cffi pats nustato tikra Chrome User-Agent
    if json_api:
        # BE SITU api.vinted.lt nezino rinkos: grazina skelbimus is viso pasaulio
        # (ir JAV), bukles prancuziskai, kainas doleriais. Locale = Lietuvos rinka.
        h["Locale"] = "lt-LT"
        h["X-Next-App"] = "marketplace-web"
        h["Platform"] = "web"
        if _auth["anon_id"]:
            h["X-Anon-Id"] = _auth["anon_id"]
        if _auth["csrf"]:
            h["X-Csrf-Token"] = _auth["csrf"]
    else:
        h["Accept"] = "text/html,application/xhtml+xml,*/*"
    return h


session = _new_session()
_auth = {"anon_id": None, "csrf": None}
_working_endpoint = None
last_error = ""                     # paskutine klaida – rodoma Telegram ispejime
_debug_price_printed = False
_debug_user_printed = False
_debug_item_printed = False


def _short_body(text, limit=150):
    """Is HTML klaidos puslapio padaro trumpa skaitoma teksta."""
    t = re.search(r"<title>(.*?)</title>", text, re.I | re.S)
    if t:
        return "puslapis: " + html.unescape(t.group(1)).strip()[:limit]
    return re.sub(r"\s+", " ", text)[:limit]


def init_session():
    global session, last_error
    session = _new_session()
    try:
        r = session.get(BASE + "/", headers=_headers(json_api=False), timeout=20)
        print(f"Sesija pradeta (statusas {r.status_code})")
        _auth["anon_id"] = r.headers.get("x-anon-id") or session.cookies.get("anon_id")
        m = (re.search(r'CSRF_TOKEN\\?"\s*:\s*\\?"([0-9a-f-]{36})', r.text) or
             re.search(r'"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"', r.text))
        _auth["csrf"] = m.group(1) if m else None
        if r.status_code != 200:
            last_error = f"Pagrindinis puslapis: HTTP {r.status_code}"
        if DEBUG:
            print(f"  [DEBUG] klientas={'curl_cffi' if USING_CFFI else 'requests'}, "
                  f"slapukai={list(session.cookies.keys())}, anon_id={_auth['anon_id']}, csrf={_auth['csrf']}")
    except Exception as e:
        last_error = f"Nepavyko pradeti sesijos: {e}"
        print(f"! {last_error}")
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


def _request_catalog(url, query, page, max_retries=3):
    """Uzklausia viena puslapi su pakartojimais:
    - 401/403 -> atnaujina sesija ir bando dar karta (sesija galejo pasenti)
    - 429     -> ilgesne pauze ir bando dar karta (rate limiting)
    - 5xx / tinklo klaida -> backoff ir bando dar karta
    Grazina items sarasa, tuscia sarasa, "404" arba None (viskas zlugo)."""
    global last_error, _debug_item_printed
    params = {"search_text": query, "order": "newest_first", "per_page": 96, "page": page,
              "currency": "EUR"}
    short_url = url.split("//", 1)[-1]
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(url, params=params, headers=_headers(), timeout=20)
            if resp.status_code in (401, 403):
                last_error = f"HTTP {resp.status_code} ({short_url}): {_short_body(resp.text)}"
                print(f"  ! {resp.status_code} – atnaujinu sesija (bandymas {attempt}/{max_retries})...")
                init_session()
                time.sleep(SLEEP_SECONDS)
                continue
            if resp.status_code == 429:
                last_error = f"HTTP 429 ({short_url}): per daug uzklausu"
                wait = SLEEP_SECONDS * attempt * 2
                print(f"  ! 429 per daug uzklausu – laukiu {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code} ({short_url}): serverio klaida"
                print(f"  ! Serverio klaida {resp.status_code} (bandymas {attempt}/{max_retries})")
                time.sleep(SLEEP_SECONDS * attempt)
                continue
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code} ({short_url}): {_short_body(resp.text)}"
                print(f"  ! '{query}' p.{page}: {last_error}")
                return "404" if resp.status_code == 404 else None
            data = resp.json()
            if not isinstance(data, dict):
                last_error = f"{short_url}: netiketas atsakymo formatas (ne JSON objektas)"
                print(f"  ! '{query}' p.{page}: {last_error}")
                return None
            batch = data.get("items") or []
            if not isinstance(batch, list):
                last_error = f"{short_url}: 'items' nera sarasas – API struktura galejo pasikeisti"
                print(f"  ! '{query}' p.{page}: {last_error}")
                return None
            if DEBUG and batch and not _debug_item_printed:
                print("  [DEBUG] pirmas skelbimas is API:")
                print(" ", json.dumps(batch[0], ensure_ascii=False)[:1500])
                _debug_item_printed = True
            return batch
        except Exception as e:
            last_error = f"Tinklo/JSON klaida ({short_url}): {e}"
            print(f"  ! Tinklo/JSON klaida (bandymas {attempt}/{max_retries}): {e}")
            time.sleep(SLEEP_SECONDS * attempt)
    print(f"  ! Visi {max_retries} bandymai nepavyko: '{query}' p.{page}")
    return None


def fetch_page_with_retry(query, page):
    """Bando naujaji katalogo adresa, jei 404 – senaji. Suveikusi atsimena."""
    global _working_endpoint
    global last_error
    endpoints = [_working_endpoint] if _working_endpoint else CATALOG_ENDPOINTS
    first_error = None
    for url in endpoints:
        batch = _request_catalog(url, query, page)
        if isinstance(batch, list):
            if _working_endpoint != url:
                print(f"  Naudojamas API adresas: {url}")
                _working_endpoint = url
            return batch
        first_error = first_error or last_error
        if batch != "404":
            break
    last_error = first_error or last_error   # ispejime rodoma naujojo adreso klaida
    return None


def _looks_newest_first(batch):
    """Vinted skelbimu ID didėja laikui bėgant. Jei bent 80% gretimu porų ID mažėja –
    sąrašas tikrai surikiuotas nuo naujausių ir stabdyti puslapiavimą saugu."""
    ids = []
    for b in batch:
        try:
            ids.append(int(b.get("id")))
        except (TypeError, ValueError, AttributeError):
            return False
    if len(ids) < 5:
        return True
    desc = sum(1 for x, y in zip(ids, ids[1:]) if x > y)
    return desc / (len(ids) - 1) >= 0.8


def fetch_items(query, pages, seen=None):
    """Skelbimai rikiuojami nuo naujausiu, tad jei VISI puslapio skelbimai jau
    matyti – toliau nebeverta ziureti (sutaupo daug laiko)."""
    items = []
    for page in range(1, pages + 1):
        batch = fetch_page_with_retry(query, page)
        if batch is None:      # viskas zlugo – stabdome si modeli
            break
        if not batch:          # daugiau nera – stabdome puslapiavima
            break
        items.extend(batch)
        if page == 1:
            ids = [b.get("id") for b in batch[:3] if isinstance(b, dict)]
            print(f"  p.1: {len(batch)} skelb., naujausi ID: {ids}, "
                  f"rikiuota nuo naujausiu: {'taip' if _looks_newest_first(batch) else 'NE'}")
        if seen and all(isinstance(b, dict) and str(b.get("id")) in seen for b in batch):
            if _looks_newest_first(batch):
                print(f"  p.{page}: visi skelbimai jau matyti – toliau nebetikrinu")
                break
            print(f"  p.{page}: visi matyti, bet API nerikiuoja nuo naujausiu – tikrinu toliau")
        if page < pages:
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


def fetch_item_page_og(item_id, url_path, max_bytes=3_000_000):
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
        resp = session.get(full_url, headers=_headers(json_api=False), timeout=20)
        if resp.status_code != 200:
            if DEBUG:
                print(f"  [DEBUG] skelbimo puslapio {item_id} uzklausa: HTTP {resp.status_code}")
            return {}
        html_text = resp.text[:max_bytes]
        og = _parse_og_tags(html_text)
        og["_html"] = html_text          # pilnas puslapis – bukle ir pardavejo reitingui
        if DEBUG and not _debug_og_printed:
            print(f"  [DEBUG] skelbimo {item_id} OG duomenys (is puslapio <head>):")
            print(" ", json.dumps({k: v for k, v in og.items() if k != "_html"}, ensure_ascii=False))
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
    - "299.0" (string)
    - atsarginiai laukai price_amount / amount / total_item_price"""
    global _debug_price_printed
    p = item.get("price")
    if isinstance(p, dict) and p.get("currency_code") not in (None, "", "EUR"):
        return None                        # ne euro kaina = ne Lietuvos rinka
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
        f = _to_float(str(p).replace("€", "").replace("\u00a0", "").replace(" ", ""))
        if f is not None:
            return f
    for key in ("price_amount", "amount", "total_item_price", "numeric_price"):
        if key in item:
            f = _to_float(item[key])
            if f is not None:
                return f
    return None


# --- Pardavejas: salis, miestas, reitingas -----------------------------

COUNTRY_NAMES = {"lietuva": "LT", "lithuania": "LT", "litauen": "LT", "lituanie": "LT",
                 "latvija": "LV", "latvia": "LV", "polska": "PL", "poland": "PL",
                 "france": "FR", "deutschland": "DE", "germany": "DE", "italia": "IT",
                 "espana": "ES", "españa": "ES", "nederland": "NL", "eesti": "EE"}
COUNTRY_LT = {"LT": "Lietuva", "LV": "Latvija", "EE": "Estija", "PL": "Lenkija", "DE": "Vokietija",
              "FR": "Prancūzija", "IT": "Italija", "ES": "Ispanija", "NL": "Nyderlandai",
              "BE": "Belgija", "CZ": "Čekija", "SK": "Slovakija", "AT": "Austrija",
              "PT": "Portugalija", "UK": "JK", "GB": "JK", "FI": "Suomija", "SE": "Švedija",
              "US": "JAV", "CA": "Kanada", "IE": "Airija", "LU": "Liuksemburgas", "HU": "Vengrija",
              "RO": "Rumunija", "HR": "Kroatija", "GR": "Graikija", "DK": "Danija", "SI": "Slovėnija"}

_seller_cache = {}


def _country_from_value(v):
    if not isinstance(v, str) or not v.strip():
        return None
    v = v.strip()
    if len(v) == 2 and v.isalpha():
        return v.upper()
    return COUNTRY_NAMES.get(v.lower())


def _seller_from_dict(user):
    """Is Vinted 'user' objekto istraukia {country, city, rating, reviews}."""
    info = {}
    for key in ("country_iso_code", "country_code", "country_title_local", "country_title"):
        c = _country_from_value(user.get(key))
        if c:
            info["country"] = c
            break
    city = user.get("city")
    if isinstance(city, dict):
        city = city.get("title")
    if isinstance(city, str) and city.strip():
        info["city"] = city.strip()
    try:
        info["rating"] = round(float(user["feedback_reputation"]) * 5, 1)
    except (KeyError, TypeError, ValueError):
        pass
    try:
        info["reviews"] = int(user["feedback_count"])
    except (KeyError, TypeError, ValueError):
        pass
    return info


def _fetch_user_api(user_id):
    """Pardavejo profilis per Vinted API (gali buti blokuojamas – tada {})."""
    for base in (BASE, API_BASE):
        try:
            r = session.get(f"{base}/api/v2/users/{user_id}", headers=_headers(), timeout=20)
            if r.status_code == 200:
                data = r.json()
                user = data.get("user") if isinstance(data, dict) else None
                if isinstance(user, dict):
                    return user
            elif DEBUG:
                print(f"  [DEBUG] vartotojo {user_id} API ({base}): HTTP {r.status_code}")
        except Exception as e:
            if DEBUG:
                print(f"  [DEBUG] vartotojo {user_id} API klaida: {e}")
    return {}


def _seller_from_page(page):
    """Atsargiai skaito skelbimo puslapi: sali imame tik jei puslapyje ji viena."""
    info = {}
    if not page:
        return info
    codes = set()
    for key in ("country_iso_code", "country_code", "country_title_local", "country_title"):
        for v in re.findall(r'\\?"' + key + r'\\?"\s*:\s*\\?"([^"\\]{2,40})\\?"', page):
            c = _country_from_value(v)
            if c:
                codes.add(c)
    if len(codes) == 1:
        info["country"] = codes.pop()
    elif DEBUG and codes:
        print(f"  [DEBUG] puslapyje kelios salys {codes} – salis nezinoma")
    rating = _json_value(page, "feedback_reputation")
    count = _json_value(page, "feedback_count")
    try:
        info["rating"] = round(float(rating) * 5, 1)
        info["reviews"] = int(float(count))
    except (TypeError, ValueError):
        pass
    return info


def get_seller_info(item, page):
    """Grazina {country, city, rating, reviews} (truksta – jei nerasta).
    Tvarka: katalogo 'user' objektas -> vartotojo API -> skelbimo puslapis."""
    global _debug_user_printed
    user = item.get("user") or {}
    if DEBUG and not _debug_user_printed:
        print("  [DEBUG] katalogo 'user' objektas:", json.dumps(user, ensure_ascii=False)[:800])
        _debug_user_printed = True
    info = _seller_from_dict(user)

    user_id = user.get("id") or item.get("user_id")
    if user_id and not all(k in info for k in ("country", "rating", "reviews")):
        if user_id not in _seller_cache:
            api_user = _fetch_user_api(user_id)
            if DEBUG and api_user:
                print("  [DEBUG] vartotojo API:", json.dumps(
                    {k: api_user.get(k) for k in ("login", "country_iso_code", "country_code", "country_title",
                                                   "city", "feedback_reputation", "feedback_count")},
                    ensure_ascii=False))
            _seller_cache[user_id] = _seller_from_dict(api_user)
        info = {**_seller_cache[user_id], **info}

    if not all(k in info for k in ("country", "rating", "reviews")):
        info = {**_seller_from_page(page), **info}
    return info


# --- Kalbos aptikimas -------------------------------------------------
# Tikslas: praleisti TIK lietuviskus skelbimus.
#   1. Yra lietuvisku raidziu (ąčęėįšųž) ar zodziu -> lietuviskas.
#   2. Yra kitos kalbos raidziu ar zodziu        -> atmetamas.
#   3. Jokiu pozymiu, bet aprasyme >= 4 "tikri" zodziai (ne modelio/techniniai)
#      -> atmetamas (tikriausiai uzsienietiskas sakinys be lietuvisku pozymiu).
#   4. Tik trumpas tekstas, pvz. "iPhone 14 Pro 128GB" -> praleidziamas.

import unicodedata


def _fold(text):
    """Nuima diakritikus: 'būklė' -> 'bukle'."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _word_regex(words):
    parts = sorted((re.escape(w) for w in words), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b")


LITHUANIAN_CHARS = set("ąčęėįšųūž")

# Lietuvisku zodziu SAKNYS (be diakritiku) – atpazista ir "būklė", ir "bukle",
# ir visas galunes: parduodu/parduodamas/parduodama, baterija/baterijos...
LITHUANIAN_STEMS = [
    "parduod", "pardod", "parduos", "bukl", "busen", "puik", "tvarking", "veik", "kain",
    "euru", "originalu", "originali", "idealu", "idealio", "ideali", "baterij", "irasyt",
    "nauj", "naudot", "dekl", "defektu", "defektai", "kokybisk", "mazai", "telefonas",
    "telefonui", "telefoną", "ekranas", "ekrano", "ekrane", "ikrov", "krovikl", "laidas", "laidu",
    "dezut", "dezes", "talpa", "talpos", "atsiim", "siunc", "siunt", "pristat", "keic", "keist",
    "komplekt", "pilnas", "pilnai", "brezim", "ibrez", "isbandyt", "garantij", "pirkt",
    "labai", "gerai", "gera", "geras", "geros", "geroje", "grazus", "grazi", "funkcij",
    "problemu", "sveikat", "skilim", "skiles", "sudauz", "nesider", "derin", "vilni", "kaun",
    "klaiped", "siaul", "panevez", "alyt", "marijampol", "utena", "palanga", "priedas",
    "pridedu", "pridedam", "kartu", "nieko", "jokiu", "nera", "yra", "turi", "turiu",
    "procent", "busima", "rasykit", "rasyk", "skambin", "zinut", "kraun", "naudoj",
]
_LITHUANIAN_RE = re.compile(r"\b(?:" + "|".join(sorted(map(re.escape, LITHUANIAN_STEMS), key=len, reverse=True)) + r")\w*")

# Trumpi lietuviski zodziai, kurie saknimis butu per daug bendri
LITHUANIAN_SHORT = _word_regex(["ir", "su", "be jokiu", "tik", "del", "nes", "arba", "kaip", "ar", "jau", "labai"])

FOREIGN_WORDS = {
    "PL": ["sprzedam", "sprzedaje", "kupie", "telefon", "oryginalny", "oryginalne",
           "stan", "stanie", "wysylka", "wysylke", "zestaw", "paragon", "faktura",
           "nieuszkodzony", "uszkodzony", "ladny", "przesylka", "polecam", "okazja",
           "komplet", "kondycja", "sprawny", "sprawna", "pudelko", "gwarancja",
           "cena", "pekniety", "peknieta", "zbite", "zbita", "wyswietlacz",
           "bateria", "akumulator", "dziala", "pilne", "negocjacje", "akcesoria",
           "bardzo", "dobry", "jest", "sie", "oraz"],
    "DE": ["verkaufe", "neuwertig", "versand", "zustand", "gebraucht", "originalverpackung",
           "rechnung", "funktioniert", "einwandfrei", "und", "mit", "ohne", "sehr", "gut",
           "kratzer", "akku", "ist", "nicht", "keine"],
    "EN": ["selling", "brand new", "like new", "shipping", "great condition", "condition",
           "excellent condition", "as new", "no issues", "works perfectly", "the", "and",
           "with", "without", "for", "comes", "battery health", "health", "unlocked",
           "scratches", "perfect", "working", "used", "very good", "good", "fully"],
    "FR": ["vends", "vend", "etat", "tres", "bon", "avec", "sans", "pour", "neuf", "batterie",
           "rayure", "rayures", "fonctionne", "parfait", "chargeur", "boite", "comme", "est"],
    "IT": ["vendo", "perfetto", "perfette", "condizioni", "batteria", "graffi", "funzionante",
           "come", "nuovo", "con", "senza", "scatola", "ottime"],
    "ES": ["vendo", "estado", "perfecto", "funciona", "bateria", "nuevo", "caja", "arañazos",
           "aranazos", "sin", "muy", "bueno"],
    "NL": ["verkoop", "staat", "goede", "nieuw", "zonder", "krassen", "doos", "werkt", "met", "een"],
    "LV": ["stavoklis", "stavokli", "labs", "jauns", "telefons", "kaste", "bez", "pardodu telefonu"],
    "CZ": ["prodam", "stav", "velmi", "dobry", "baterie", "krabice", "funkcni"],
}
_FOREIGN_RE = {lang: _word_regex([_fold(w) for w in words]) for lang, words in FOREIGN_WORDS.items()}

FOREIGN_CHARS = {
    "PL": set("łńśźżć"),
    "DE": set("äöüß"),
    "LV": set("āēīōļņģ"),      # 'ū' nera cia – ji yra ir lietuviu kalboje
    "FR": set("éèêàçôœ"),
    "ES": set("ñ¿¡"),
    "CZ": set("řěůť"),
}

# Zodziai, kurie nieko nesako apie kalba (modeliai, techniniai terminai)
NEUTRAL_WORDS = set("""
iphone apple pro max plus mini gb tb gen generation se ios airpods watch ipad macbook
unlocked icloud face id truedepth esim sim dual black white blue gold silver graphite
sierra alpine green purple deep space midnight starlight red pink natural titanium
""".split())


def _has_cyrillic(text):
    return any("Ѐ" <= ch <= "ӿ" for ch in text)


def detect_foreign_language(*texts):
    """Grazina kalbos koda ('PL', 'EN', 'FR', ..., '??'), jei tekstas ne lietuviskas,
    arba None, jei lietuviskas / per mazai teksto nustatyti.

    Skaiciuojami taskai: lietuviski pozymiai (raides, zodziu saknys – su
    diakritikais ir be) pries kitu kalbu pozymius. Laimi daugiau tasku."""
    raw = " ".join(x for x in texts if x).lower()
    if not raw.strip():
        return None
    folded = _fold(raw)

    if _has_cyrillic(raw):
        return "RU"

    lt_score = 2 * len(LITHUANIAN_CHARS & set(raw))
    lt_score += 2 * len(set(_LITHUANIAN_RE.findall(folded)))
    lt_score += len(set(LITHUANIAN_SHORT.findall(folded)))

    foreign = {}
    for lang, chars in FOREIGN_CHARS.items():
        n = len(chars & set(raw))
        if n:
            foreign[lang] = foreign.get(lang, 0) + 2 * n
    for lang, rx in _FOREIGN_RE.items():
        n = len(set(rx.findall(folded)))
        if n:
            foreign[lang] = foreign.get(lang, 0) + 2 * n
    # Lygiosiose pirmenybe leidziamai kalbai (pvz. EN), kad atsitiktinis
    # "con"/"est" nepadarytu angliško teksto itališku/prancūzišku
    best_lang, best_score = (max(foreign.items(), key=lambda kv: (kv[1], kv[0] in ALLOWED_LANGUAGES))
                             if foreign else (None, 0))

    if DEBUG:
        print(f"  [DEBUG] kalba: LT={lt_score}, kitos={foreign}")

    if lt_score > 0 and lt_score >= best_score:
        return None
    if best_lang:
        return best_lang

    words = [w for w in re.findall(r"[a-z]{3,}", folded) if w not in NEUTRAL_WORDS]
    if len(set(words)) >= 4 and "EN" not in ALLOWED_LANGUAGES:
        return "??"      # ilgas lotyniskas tekstas be jokiu pozymiu (kai EN leidziama – praleidziam)
    return None


def is_junk(title):
    t = title.lower()
    return any(w in t for w in BLACKLIST_WORDS)


# --- Papildoma informacija Telegram kortelei ---------------------------

def _json_value(page, key):
    """Randa "key": reiksme skelbimo puslapio JSON'e (ir su \\" kabutemis)."""
    m = re.search(r'\\?"' + re.escape(key) + r'\\?"\s*:\s*(\\?"(.*?)\\?"|-?[\d.]+)', page)
    if not m:
        return None
    return m.group(2) if m.group(2) is not None else m.group(1)


def get_photo_url(item, og):
    photo = item.get("photo") or {}
    if isinstance(photo, dict):
        url = photo.get("url") or photo.get("full_size_url")
        if url:
            return url
    photos = item.get("photos") or []
    if photos and isinstance(photos[0], dict) and photos[0].get("url"):
        return photos[0]["url"]
    return og.get("image")


# Vinted bukles (status_id -> lietuviskas pavadinimas)
CONDITION_LT = {6: "Nauja su etiketėmis", 1: "Nauja be etikečių", 2: "Labai gera", 3: "Gera", 4: "Patenkinama"}

# Bukles pavadinimai kitomis kalbomis (be diakritiku, mazosiomis) -> status_id
CONDITION_FOREIGN = {
    # FR
    "neuf avec etiquette": 6, "neuf sans etiquette": 1, "tres bon etat": 2, "bon etat": 3, "satisfaisant": 4,
    # EN
    "new with tags": 6, "new without tags": 1, "very good": 2, "good": 3, "satisfactory": 4,
    # DE
    "neu mit etikett": 6, "neu ohne etikett": 1, "sehr gut": 2, "gut": 3, "zufriedenstellend": 4,
    # PL
    "nowy z metka": 6, "nowy bez metki": 1, "bardzo dobry": 2, "dobry": 3, "zadowalajacy": 4,
    # IT
    "nuovo con cartellino": 6, "nuovo senza cartellino": 1, "ottime condizioni": 2,
    "buone condizioni": 3, "discrete condizioni": 4,
    # ES
    "nuevo con etiquetas": 6, "nuevo sin etiquetas": 1, "muy bueno": 2, "bueno": 3, "satisfactorio": 4,
    # NL
    "nieuw met prijskaartje": 6, "nieuw zonder prijskaartje": 1, "heel goed": 2, "goed": 3, "redelijk": 4,
    # LV
    "jauns ar birkam": 6, "jauns bez birkam": 1, "loti labs": 2, "labs": 3, "apmierinoss": 4,
    # CZ
    "nove s visackou": 6, "nove bez visacky": 1, "velmi dobry": 2, "dobry": 3, "uspokojivy": 4,
}
_LT_CONDITIONS = {_fold(v.lower()): k for k, v in CONDITION_LT.items()}


def _raw_condition(item, page):
    status_id = item.get("status_id")
    if isinstance(item.get("status"), dict):
        status_id = status_id or item["status"].get("id")
    try:
        if int(status_id) in CONDITION_LT:
            return None, int(status_id)
    except (TypeError, ValueError):
        pass
    status = item.get("status")
    if isinstance(status, dict):
        status = status.get("title")
    if isinstance(status, str) and status.strip():
        return status.strip(), None
    second = (item.get("item_box") or {}).get("second_line") or ""
    if second:
        return second.split("·")[-1].strip(), None
    for cond in CONDITION_LT.values():
        if page and ('"' + cond + '\\"' in page or '"' + cond + '"' in page):
            return cond, None
    return None, None


def get_condition(item, page):
    """Grazina (bukle lietuviskai, ar_bukle_buvo_uzsienio_kalba).
    Pvz. "Très bon état" -> ("Labai gera", True)."""
    text, status_id = _raw_condition(item, page)
    if status_id:
        return CONDITION_LT[status_id], False
    if not text:
        return None, False
    key = _fold(text.lower()).strip()
    if key in _LT_CONDITIONS:
        return CONDITION_LT[_LT_CONDITIONS[key]], False
    if key in CONDITION_FOREIGN:
        return CONDITION_LT[CONDITION_FOREIGN[key]], True
    return text, False


def _stars(score):
    full = int(round(score or 0))
    return "★" * full + "☆" * (5 - full)


def format_card(a):
    """Telegram korteles tekstas (HTML, iki 1024 simboliu – nuotraukos aprasymo riba)."""
    lines = [
        f"<b>{html.escape(a['title'])} | {a['price']:g} €</b>",
        f"<i>(paieška: {html.escape(a['query'])})</i>",
    ]
    desc = re.sub(r"\s+", " ", a.get("description") or "").strip()
    if len(desc) > 180:
        desc = desc[:180].rsplit(" ", 1)[0] + " ..."
    if desc:
        lines.append(html.escape(desc))
    lines.append("")
    if a.get("condition"):
        lines.append(f"📦 <b>Būklė:</b> {html.escape(a['condition'])}")
    score, count = a.get("rating", (None, None))
    if score is not None:
        lines.append(f"⭐ <b>Pardavėjas:</b> {_stars(score)} ({score:.1f}/5, {count} atsiliep.)")
    if a.get("country"):
        place = COUNTRY_LT.get(a["country"], a["country"])
        if a.get("city"):
            place += ", " + a["city"]
        lines.append(f"📍 <b>Vieta:</b> {html.escape(place)}")
    lines.append(f'🔗 <a href="{html.escape(a["url"])}">Atidaryti Vinted</a>')
    return "\n".join(lines)[:1024]


def send_deal(a):
    """Siuncia skelbima kaip kortele su nuotrauka ir mygtuku. Jei nuotrauka
    nesiuncia – siuncia paprasta teksta."""
    caption = format_card(a)
    keyboard = json.dumps({"inline_keyboard": [[{"text": "🛒 Atidaryti Vinted", "url": a["url"]}]]})
    if a.get("photo"):
        try:
            r = requests.post(
                "https://api.telegram.org/bot" + BOT_TOKEN + "/sendPhoto",
                data={"chat_id": CHAT_ID, "photo": a["photo"], "caption": caption,
                      "parse_mode": "HTML", "reply_markup": keyboard},
                timeout=20,
            )
            if r.status_code == 200:
                return
            print(f"  ! Telegram nuotraukos klaida: {r.text[:150]} – siunciu be nuotraukos")
        except Exception as e:
            print(f"  ! Nepavyko issiusti nuotraukos: {e} – siunciu be nuotraukos")
    send_telegram(caption)


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


def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("Nenurodyti BOT_TOKEN / CHAT_ID (GitHub Secrets)!")
        return

    init_session()

    seen = load_seen()
    new_seen = dict(seen) # <-- IŠTAISYTA ČIA
    alerts = []
    total_fetched = 0
    unknown_country = 0
    checked_sellers = 0

    for model in MODELS:
        q = model["query"]
        print(f"Tikrinama: '{q}' ({model['min_price']}-{model['max_price']} EUR)...")
        items = fetch_items(q, PAGES, seen)
        total_fetched += len(items)
        fresh = 0
        excluded_by_country = 0
        excluded_foreign = 0
        excluded_price_digit = 0
        excluded_seller = 0
        excluded_price = 0
        already_seen = 0
        excluded_junk = 0
        page_failed = 0
        examples = []                      # keli atmestu skelbimu pavyzdziai log'ui

        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id") or item.get("item_id") or item.get("entity_id")
            if item_id is None:
                continue
            item_id = str(item_id)
            if item_id in seen:
                already_seen += 1
                continue
            new_seen[item_id] = time.time()

            price = get_price(item)
            if price is None or not (model["min_price"] <= price <= model["max_price"]):
                excluded_price += 1
                continue

            if PRICE_LAST_DIGITS and int(price) % 10 not in PRICE_LAST_DIGITS:
                excluded_price_digit += 1
                continue

            title = item.get("title") or item.get("name") or "?"
            url_path = item.get("url") or item.get("path") or item.get("web_url") or f"/items/{item_id}"
            full_url = BASE + url_path if url_path.startswith("/") else url_path

            # Katalogo API nera aprasymo, o JSON detaliu endpoint'as Vinted
            # dazniausiai blokuoja (403). Todel aprasyma skaitome is vieso
            # skelbimo puslapio OpenGraph zymu.
            og = fetch_item_page_og(item_id, url_path)
            time.sleep(DETAIL_SLEEP_SECONDS)
            if og.get("title"):
                title = og["title"]
            description = og.get("description") or ""
            page_html = og.get("_html", "")
            if not page_html:
                page_failed += 1

            condition, condition_foreign = get_condition(item, page_html)

            # PASTABA: bukles kalba NEBENAUDOJAMA kaip filtras – Vinted API ja
            # grazina ne pardavejo, o serverio kalba (pvz. prancuziskai visiems).
            if ONLY_LITHUANIAN_TEXT:
                lang = detect_foreign_language(title, description)
                if lang and lang not in ALLOWED_LANGUAGES:
                    excluded_foreign += 1
                    if len(examples) < 5:
                        examples.append(f"kalba={lang}: {title[:40]} | {description[:70]}")
                    continue

            if is_junk(title):
                excluded_junk += 1
                continue

            # Pardavejas: salis ir patikimumas (tikrinama paskutini – brangiausia)
            seller = get_seller_info(item, page_html)
            country = seller.get("country")
            if country is None:
                unknown_country += 1
            if FILTER_BY_COUNTRY:
                country_ok = (country in ALLOWED_COUNTRY_CODES) if country else (not REQUIRE_KNOWN_COUNTRY)
            else:
                country_ok = True
            if not country_ok:
                excluded_by_country += 1
                if len(examples) < 5:
                    examples.append(f"salis={country}: {title[:40]}")
                if DEBUG:
                    print(f"  [DEBUG] atmesta (salis={country}): {title[:60]}")
                continue

            rating, reviews = seller.get("rating"), seller.get("reviews")
            # Jei reitingo nustatyti nepavyko – neatmetam (kortelėje jo tiesiog nebus)
            if rating is not None and reviews is not None and (
                    rating < MIN_SELLER_RATING or reviews < MIN_SELLER_REVIEWS):
                excluded_seller += 1
                if len(examples) < 5:
                    examples.append(f"pardavejas {rating}/5, {reviews} atsil.: {title[:40]}")
                if DEBUG:
                    print(f"  [DEBUG] atmesta (pardavejas {rating}/5, {reviews} atsil.): {title[:60]}")
                continue

            deal = {
                "query": q,
                "title": item.get("title") or title,
                "price": price,
                "url": full_url,
                "description": description,
                "photo": get_photo_url(item, og),
                "condition": condition,
                "rating": (rating, reviews) if rating is not None and reviews is not None else (None, None),
                "country": country,
                "city": seller.get("city"),
            }
            alerts.append(deal)
            fresh += 1

            # Siunciam IS KARTO, nelaukiant, kol bus patikrinti visi modeliai
            if DRY_RUN:
                print(f"  [DRY_RUN] rastas: {q} {price:.0f} EUR: {deal['title'][:50]}")
            else:
                send_deal(deal)
                print(f"  -> {q} {price:.0f} EUR: {deal['title'][:50]}")
                save_seen(new_seen)       # kad nuluzus skriptui neateitu dublikatai
                time.sleep(1)             # Telegram riboja zinuciu greiti
            checked_sellers += 1
            if DEBUG:
                print(f"  [DEBUG] PRIIMTA (salis={country}, {rating}/5): {title[:60]}")

        print(f"  Gauta: {len(items)}, jau matyti: {already_seen}, nauji: {len(items) - already_seen}, tinkama: {fresh}, atmesta salis: {excluded_by_country}, atmesta uzsienio kalba: {excluded_foreign}, atmesta kainos skaitmuo: {excluded_price_digit}, atmesta pardavejas: {excluded_seller}, "
              f"ne kainos ribose: {excluded_price}, slamstas: {excluded_junk}, "
              f"skelbimo puslapis nepasiekiamas: {page_failed}")
        for ex in examples:
            print(f"    atmesta – {ex}")
        time.sleep(SLEEP_SECONDS)

    save_seen(new_seen)

    # Savaime diagnostika: jei is VISU paiesku negauta nei vieno skelbimo,
    # tai zenklas, kad Vinted galejo ka nors pakeisti – pranesame i Telegram.
    if total_fetched == 0 and not DRY_RUN and BOT_TOKEN and CHAT_ID:
        send_telegram("<b>ISPEJIMAS</b>: negauta nei vieno skelbimo is Vinted. "
                      "Galimai pasikeite API – patikrink skripto logus.\n"
                      "Priezastis: <code>" + html.escape(last_error or "nezinoma") + "</code>")

    # Diagnostika: jei nei vienam pardavejui salies nustatyti nepavyko – pranesam
    if FILTER_BY_COUNTRY and REQUIRE_KNOWN_COUNTRY and unknown_country and not checked_sellers:
        print(f"! {unknown_country} pardaveju salies nustatyti nepavyko – visi atmesti.")
        send_telegram("<b>ISPEJIMAS</b>: nepavyko nustatyti pardaveju salies "
                      f"({unknown_country} skelb.), todel visi atmesti.\n"
                      "Ijunk DEBUG: true faile config.json ir atsiusk log'a.")

    if not alerts:
        print("Nauju deal'u nera.")
        return

    if DRY_RUN:
        print(f"[DRY_RUN] Rasta {len(alerts)} dealu, bet zinuciu NESIUNCIAMA.")
        print("[DRY_RUN] Pakeisk DRY_RUN = False ir paleisk dar karta.")
        return

    print(f"Issiusta {len(alerts)} alert'u.")


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
            send_telegram("<b>SKRIPTAS UZLUZO</b>\n" + html.escape(str(e)[:400]))
        except Exception:
            pass


if __name__ == "__main__":
    run_with_guard()
