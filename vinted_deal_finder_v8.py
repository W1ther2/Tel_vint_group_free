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


def fetch_page_with_retry(query, page, max_retries=3, min_price=None, max_price=None, status_ids=None):
    """Uzklausia viena puslapi su pakartojimais:
    - 401/403 -> atnaujina sesija ir bando dar karta (sesija galejo pasenti)
    - 429     -> ilgesne pauze ir bando dar karta (rate limiting)
    - 5xx / tinklo klaida -> backoff ir bando dar karta
    - 400/404 SU price_from/price_to/status_ids filtrais -> BANDOMA DAR KARTA
      BE JU (galbut sitie parametrai/ju formatas Vinted API nebepalaikomas -
      neturiu galimybes to pries tai patikrinti be gyvo API), kad neprarastume
      viso funkcionalumo, jei problema butent siuose parametruose.
    Grazina items sarasa, tuscia sarasa (nebepuslapiuojam) arba None (viskas zlugo)."""
    url = BASE + "/api/v2/catalog/items"

    def build_params(with_filters):
        p = {"search_text": query, "per_page": 96, "page": page}
        if with_filters:
            if min_price is not None:
                p["price_from"] = min_price
            if max_price is not None:
                p["price_to"] = max_price
            if status_ids:
                p["status_ids[]"] = list(status_ids)
        return p

    has_filters = min_price is not None or max_price is not None or status_ids
    params = build_params(with_filters=True)
    filters_active = True
    fallback_tried = False

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
            if resp.status_code in (400, 404) and has_filters and filters_active and not fallback_tried:
                print(f"  ! '{query}' p.{page}: HTTP {resp.status_code} SU price_from/price_to/status_ids "
                      f"filtrais - bandau dar karta BE JU...")
                params = build_params(with_filters=False)
                filters_active = False
                fallback_tried = True
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
            if fallback_tried and not filters_active:
                print(f"  [INFO] '{query}' p.{page}: suveike TIK BE price_from/price_to/status_ids filtru "
                      f"- sie parametrai greiciausiai nebepalaikomi/neteisingi.")
            return batch
        except (requests.RequestException, ValueError) as e:
            print(f"  ! Tinklo/JSON klaida (bandymas {attempt}/{max_retries}): {e}")
            time.sleep(SLEEP_SECONDS * attempt)
    print(f"  ! Visi {max_retries} bandymai nepavyko: '{query}' p.{page}")
    return None


def fetch_items(query, pages, min_price=None, max_price=None, status_ids=None):
    items = []
    for page in range(1, pages + 1):
        batch = fetch_page_with_retry(query, page, min_price=min_price, max_price=max_price, status_ids=status_ids)
        if batch is None:      # viskas zlugo – stabdome si modeli
            break
        if not batch:          # daugiau nera – stabdome puslapiavima
            break
        items.extend(batch)
        time.sleep(SLEEP_SECONDS)
    return items


_ITEM_LINK_RE = re.compile(r'/items/(\d+)-([a-zA-Z0-9\-]{1,150})')
_debug_search_html_printed = False


def _capped_get(url, params, headers, max_bytes, timeout=30):
    """Bendra pagalbine f-ja: GET su srautiniu skaitymu, sustojant ties
    max_bytes. Grazina (status_code, tekstas, galutinis_url) arba None klaidos
    atveju."""
    resp = session.get(url, params=params, headers=headers, timeout=timeout, stream=True)
    if resp.status_code != 200:
        code = resp.status_code
        resp.close()
        return code, None, getattr(resp, "url", None)
    chunks = []
    total = 0
    for chunk in resp.iter_content(chunk_size=65536):
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total >= max_bytes:
            break
    final_url = resp.url
    resp.close()
    return 200, b"".join(chunks).decode("utf-8", errors="ignore"), final_url


def fetch_search_page_links(query, page, min_price=None, max_price=None, max_bytes=8_000_000):
    """/api/v2/catalog/items PANASU, KAD VINTED IŠJUNGĖ - grazina JSON klaida
    (404, code 104/not_found) VISOMS parametru kombinacijoms, tuo tarpu kiti
    endpoint'ai (/api/v2/catalog/filters, /api/v2/users/{id}, patys skelbimo
    puslapiai) veikia normaliai. Tai reiskia route'as pasalintas, ne blokuojamas.

    Vietoj to naudojame PATI REZULTATU PUSLAPI, kuri mato bet kuris lankytojas
    narsykleje (server-rendered). PIRMA bandome LENGVA variacija - Next.js App
    Router turi standartini vidini mechanizma ('RSC: 1' antraste), kuri
    naudoja pats svetaines klientas naviguodamas BE VISO puslapio perkrovimo
    (CSS/JS/vertimu bloku ir t.t.) - jei serveris ja atpazista, atsakymas
    buna zymiai lengvesnis (KB, ne MB). Tai NE apejimas - tai vieša, standartine
    Next.js funkcija, kuria naudoja kiekvienas lankytojas.

    Jei sis lengvas kelias nesuveikia (serveris ji ignoruoja ar grazina kazka
    nenaudinga), grieztame prie PILNO puslapio (jau patikrinta, kad veikia,
    tiesiog sunkesnis).

    Bet kuriuo atveju ismenamas TIK PATS PATIKIMIAUSIAS elementas - nuorodos
    i skelbimus, nes tai nepriklauso nuo tikslios vidines duomenu formos.

    Grazina sarasa [{"id": "...", "url": "/items/...", "price_amount": ...?,
    "status_id": ...?}, ...] arba [] jei nieko nerasta/klaida."""
    global _debug_search_html_printed
    url_path_only = "/catalog"
    url = BASE + url_path_only
    params = {"search_text": query, "page": page}
    if min_price is not None:
        params["price_from"] = min_price
    if max_price is not None:
        params["price_to"] = max_price

    used_rsc = False  # RSC bandymas ATSISAKYTA - Vinted ignoruoja antraste siam
    # marsrutui (atsakymo dydis liko toks pats, ~6MB), tad tai tik papildoma
    # uzklausa be jokios naudos.
    try:
        status, text, final_url = _capped_get(url, params, HEADERS, max_bytes)
        if status != 200:
            print(f"  ! Paieskos puslapis '{query}' p.{page}: HTTP {status}")
            return []
        if final_url and BASE.split("//")[1] not in final_url:
            print(f"  ! ISPEJIMAS: uzklausa buvo nukreipta (redirect) i kitokia URL: {final_url}")
        html_text = text
        total = len(text.encode("utf-8", errors="ignore"))
    except Exception as e:
        print(f"  ! Nepavyko gauti paieskos puslapio '{query}' p.{page}: {e}")
        return []

    title_m = re.search(r"<title>([^<]{0,120})</title>", html_text, re.IGNORECASE)
    seen_ids = set()
    results = []
    for m in _ITEM_LINK_RE.finditer(html_text):
        item_id, slug = m.group(1), m.group(2)
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        href = f"/items/{item_id}-{slug}"
        entry = {"id": item_id, "url": href}

        # PIGI (be papildomos uzklausos) kainos/bukles paieska SALIA sios
        # nuorodos siame jau atsiustame puslapyje - taupo brangu (8MB) atskiro
        # skelbimo puslapio fetch'a daugumai skelbimu, kurie bet kokiu atveju
        # nepraeitu kainos/bukles filtro. NEGARANTUOTA tiksliai priskirta -
        # tankiai sudetame saraso faile gali pagauti kaimynines preces lauka -
        # todel tai naudojama TIK isankstiniam atmetimui, ne galutiniam
        # priemimui (galutinis patvirtinimas visada per pilna skelbimo fetch'a).
        window = html_text[max(0, m.start() - 1500):m.end() + 1500]
        pm = _PRICE_JSON_RE.search(window)
        if pm:
            entry["price_amount"] = pm.group(1)
            entry["price_currency"] = pm.group(2)
        sm = _STATUS_ID_JSON_RE.search(window)
        if sm:
            entry["status_id"] = int(sm.group(1))

        results.append(entry)

    with_price = sum(1 for r in results if "price_amount" in r)
    print(f"  [INFO] paieskos puslapis '{query}' p.{page}: budas={'RSC(lengvas)' if used_rsc else 'pilnas HTML'}, "
          f"atsiusta {total} baitu, antraste={title_m.group(1) if title_m else '?'!r}, "
          f"rasta {len(results)} unikaliu skelbimu nuorodu ({with_price} su pigiai rasta kaina).")
    if not results and not _debug_search_html_printed:
        print(f"  [INFO] NUORODU NERASTA - HTML atkarpa diagnostikai (nepriklausomai nuo DEBUG, nes tai kritinis signalas):")
        print(" ", html_text[:2000].replace(chr(10), " "))
        print(f"  [INFO] atsakymo dydis is viso: {total} baitu (jei labai mazas, gal ne tikras paieskos puslapis atkeliavo).")
        _debug_search_html_printed = True
    return results


_debug_og_printed = False
DETAIL_SLEEP_SECONDS = 1.0

_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_PROPERTY_RE = re.compile(r'property=["\']([^"\']+)["\']', re.IGNORECASE)
_CONTENT_RE = re.compile(r'content=["\']([^"\']*)["\']', re.IGNORECASE)


def _parse_og_tags(html_text):
    """Israsko visas property-turincias meta zymas is HTML teksto (ne tik
    'og:*', bet ir pvz. 'product:price:amount'), nepriklausomai nuo
    property/content atributu tvarkos tage."""
    og = {}
    for tag in _META_TAG_RE.findall(html_text):
        pm = _PROPERTY_RE.search(tag)
        if not pm:
            continue
        cm = _CONTENT_RE.search(tag)
        if not cm:
            continue
        prop = pm.group(1)
        key = prop[3:] if prop.startswith("og:") else prop
        og[key] = html.unescape(cm.group(1))
    return og


# Atsargines (regex) paieskos tiesiai HTML/JSON tekste - naudojamos, kai
# reikiamu duomenu NERA <meta> zymose. Sios reiksmes NEGARANTUOTOS: jos
# ieskomos tiesiog kaip teksto fragmentai, nepriklausomai nuo to, kokia
# tiksliai yra Vinted vidine duomenu struktura (kurios negaliu patikrinti
# be gyvos prieigos) - DEBUG isvestis parodys, ar kas nors rasta.
_PRICE_JSON_RE = re.compile(r'"amount"\s*:\s*"?(\d+(?:\.\d+)?)"?\s*,\s*"currency_code"\s*:\s*"([A-Z]{3})"')
_STATUS_ID_JSON_RE = re.compile(r'"status_id"\s*:\s*(\d+)')
_MEMBER_ID_RE = re.compile(r'/member/(\d+)-')
_TITLE_JSON_RE = re.compile(r'"title"\s*:\s*"((?:[^"\\]|\\.){1,200})"')
_DESC_JSON_RE = re.compile(r'"description"\s*:\s*"((?:[^"\\]|\\.){1,3000})"')


def _json_str_unescape(raw):
    """Saugiai iskoduoja JSON eilutes escape simbolius (\\n, \\uXXXX ir t.t.)."""
    try:
        return json.loads('"' + raw + '"')
    except (ValueError, json.JSONDecodeError):
        return raw


def _extract_fallback_fields(html_text):
    """Bando rasti kaina/bukle/pardavejo ID/pavadinima/aprasyma tiesiog kaip
    teksto fragmentus puslapyje (embedded JSON gabalai), NEPARSINANT viso
    puslapio struktoros - tai patikimiau, kai vidine forma nezinoma/kinta,
    bet gali ir nerasti. title/description cia reikalingi TIK kaip atsargine
    priemone - jei puslapyje NERA <meta property='og:*'> zymu (pvz. gavus
    'lengva' RSC atsakyma be HTML apvalkalo), nes vien JSON reiksme (be
    konteksto) galima klaidingai pagauti KITO elemento (pvz. 'panasus
    skelbimai' bloko) lauka, jei jis atsiranda anksciau tekste nei paties
    skelbimo."""
    out = {}
    m = _PRICE_JSON_RE.search(html_text)
    if m:
        out["price_amount"] = m.group(1)
        out["price_currency"] = m.group(2)
    m = _STATUS_ID_JSON_RE.search(html_text)
    if m:
        out["status_id"] = int(m.group(1))
    m = _MEMBER_ID_RE.search(html_text)
    if m:
        out["seller_id"] = m.group(1)
    m = _TITLE_JSON_RE.search(html_text)
    if m:
        out["title"] = _json_str_unescape(m.group(1))
    m = _DESC_JSON_RE.search(html_text)
    if m:
        out["description"] = _json_str_unescape(m.group(1))
    return out


def fetch_item_page_og(item_id, url_path, max_bytes=8_000_000):
    """Katalogo/paieskos API skelbimo objekte NERA aprasymo, o atskiras JSON
    endpoint'as (/api/v2/items/{id}) Vinted DAZNIAUSIAI BLOKUOJA (403, anti-bot
    apsauga - tai patvirtinta ir populiariuose atviro kodo Vinted scraper'iuose).

    Todel aprasyma skaitome is vieso skelbimo puslapio OpenGraph <meta> zymu
    (title/description/image/url). PAPILDOMAI (nuo tada, kai /api/v2/catalog/items
    pats dingo) - is siuo puslapio taip pat bandome atsargiai (regex) istraukti
    kaina/bukle/pardavejo ID, nes kitaip ju visai neturetume.

    PIRMA bandome LENGVA Next.js RSC uzklausa (ta pati technika kaip
    fetch_search_page_links) - jei suveikia, sutaupome daug duomenu. RSC
    atsakyme NERA <meta> zymu (nera HTML apvalkalo is viso), tad tokiu atveju
    title/description ismenami is JSON teksto fragmentu vietoj OG zymu.

    DEMESIO: jei og:description formatas skiriasi nuo tiketo, arba atsargines
    paieskos nieko neranda, DEBUG isvestis parodys tiksliai, ka gavome - pagal
    tai galesim koreguoti. max_bytes=8MB, nes nustatyta, kad puslapio pradzioje
    yra didziulis (kelis MB) vertimu/lokalizacijos JSON blokas PRIES pasiekiant
    tikruosius prekes duomenis - su mazesniu limitu jo net nepasiekdavome."""
    global _debug_og_printed
    full_url = BASE + url_path if url_path.startswith("/") else url_path
    try:
        used_rsc = False  # RSC bandymas ATSISAKYTA - patvirtinta, kad realiame
        # RSC atsakyme duomenys nera paprastas tekstas su JSON fragmentais, o
        # eiluciu/nuorodu ("$"-refs) sistema, kuria musu naivus regex kartais
        # KLAIDINGAI priskiria vieno skelbimo duomenis kitam (stebeta: gauta
        # kito skelbimo kaina PLN valiuta ir lenkiskas tekstas vietoj tikro,
        # o "title" tapo pazodiui "$undefined" - RSC protokolo simbolis, ne
        # tikras tekstas). Duomenu teisingumas svarbiau uz sutaupyma.
        status, text, _ = _capped_get(full_url, None, HEADERS, max_bytes, timeout=20)
        if status != 200:
            if DEBUG:
                print(f"  [DEBUG] skelbimo puslapio {item_id} uzklausa: HTTP {status}")
            return {}
        html_text = text
        total = len(text.encode("utf-8", errors="ignore"))

        og = _parse_og_tags(html_text)
        for k, v in _extract_fallback_fields(html_text).items():
            og.setdefault(k, v)  # OG zymos turi pirmenyba - fallback tik uzpildo spragas
        if not _debug_og_printed:
            print(f"  [INFO] skelbimo {item_id} isgauti duomenys (budas={'RSC(lengvas)' if used_rsc else 'pilnas HTML'}, "
                  f"atsiusta {total} baitu):")
            print(" ", json.dumps(og, ensure_ascii=False))
            if "price_amount" not in og:
                pos = html_text.find('"amount"')
                if pos == -1:
                    pos = html_text.find('"status_id"')
                if pos == -1:
                    print(f"  [INFO] KAINOS NEPAVYKO RASTI skelbimui {item_id} - zodziu 'amount'/'status_id' "
                          f"NERA visame atsiustame {total} baitu turinyje (arba duomenys toliau uz limito, "
                          f"arba visai kitoks laukas).")
                else:
                    print(f"  [INFO] KAINOS NEPAVYKO RASTI reikiama forma skelbimui {item_id} - bet rastas "
                          f"artimiausias 'amount'/'status_id' paminejimas ({total} baitu ribose), kontekstas:")
                    print(" ", html_text[max(0, pos - 300):pos + 700].replace(chr(10), " "))
            _debug_og_printed = True
        return og
    except Exception as e:
        print(f"  ! nepavyko gauti skelbimo {item_id} puslapio: {e}")
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


def is_relevant_title(query, title):
    """Apsaugiklis nuo Vinted pilno teksto paieskos klaidingu atitikmenu -
    'search_text' ieskoma per VISA teksta (net apraseme), tad gali grazinti
    visai kitoki daikta (rankine su 'iPhone 13' apraseme) ARBA VISAI KITA TO
    PACIO PREKES ZENKLO MODELI (pvz. 'iPhone 12' po 'iPhone 13' paieskos -
    abu turi zodi 'iPhone', bet tai NE ta pati preke).

    Reikalaujame, kad VISI reiksminiai uzklausos zodziai (issk. modelio
    numeris) butu PACIAME skelbimo pavadinime kaip atskiri zodziai (\\b
    ribos, kad '13' neuzkabintu '130' ar pan.)."""
    tokens = re.findall(r"\w+", (query or "").lower())
    if not tokens:
        return True
    t = (title or "").lower()
    return all(re.search(r"\b" + re.escape(tok) + r"\b", t) for tok in tokens)


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
        "<b>" + html.escape(a["title"][:100]) + "</b> | <b>" + f'{a["price"]:.0f} €' + "</b>",
        "<i>(paieška: " + html.escape(a["query"]) + ")</i>",
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

        links = []
        for page in range(1, PAGES + 1):
            page_links = fetch_search_page_links(q, page, min_price=model["min_price"], max_price=model["max_price"])
            if not page_links:
                break
            links.extend(page_links)
            time.sleep(SLEEP_SECONDS)
        total_fetched += len(links)
        fresh = 0
        market_cache = {}

        excluded_by_country = 0
        excluded_foreign = 0
        excluded_price_digit = 0
        excluded_condition = 0
        excluded_irrelevant = 0
        excluded_no_price = 0
        skipped_cheap = 0
        fetched_full_page = 0

        for link in links:
            item_id = link.get("id")
            if not item_id:
                continue
            item_id = str(item_id)
            if item_id in new_seen:
                continue
            new_seen[item_id] = time.time()
            save_seen(new_seen)

            url_path = link.get("url") or ""
            full_url = BASE + url_path if url_path.startswith("/") else url_path

            # PIGUS isankstinis patikrinimas - jei paieskos puslapyje jau
            # radome sio skelbimo kaina/bukle (be papildomos uzklausos),
            # is karto atmetame akivaizdziai netinkancius, KAD NEREIKETU
            # brangios (iki 8MB) atskiro skelbimo puslapio uzklausos.
            # NEGARANTUOTA tiksliai priskirta (tankiame saraso faile galima
            # pagauti kaimynines preces lauka), todel naudojama TIK atmetimui,
            # ne galutiniam priemimui.
            cheap_price = get_price(link) if "price_amount" in link else None
            if cheap_price is not None and not (model["min_price"] <= cheap_price <= model["max_price"]):
                skipped_cheap += 1
                continue
            if cheap_price is not None and PRICE_LAST_DIGITS and int(cheap_price) % 10 not in PRICE_LAST_DIGITS:
                skipped_cheap += 1
                excluded_price_digit += 1
                continue
            if ALLOWED_CONDITIONS and "status_id" in link:
                cheap_key, cheap_label = get_condition(link)
                if cheap_label != "nežinoma" and cheap_key not in ALLOWED_CONDITIONS and cheap_label not in ALLOWED_CONDITIONS:
                    skipped_cheap += 1
                    excluded_condition += 1
                    continue

            # Paieskos puslapis duoda TIK nuoroda (+galbut pigiai rasta kaina/
            # bukle) - pavadinimui/aprasymui/nuotraukai/patvirtintai kainai vis
            # tiek reikia apsilankyti paciame skelbime. Bet dabar tai darome
            # TIK kandidatams, kurie jau praejo pigu isankstini filtra.
            fetched_full_page += 1
            og = fetch_item_page_og(item_id, url_path)
            time.sleep(DETAIL_SLEEP_SECONDS)

            price = get_price(og)
            if price is None:
                excluded_no_price += 1
                continue
            if not (model["min_price"] <= price <= model["max_price"]):
                continue

            if PRICE_LAST_DIGITS and int(price) % 10 not in PRICE_LAST_DIGITS:
                excluded_price_digit += 1
                continue

            cond_key, cond_label = get_condition(og)
            if ALLOWED_CONDITIONS and cond_label != "nežinoma" and cond_key not in ALLOWED_CONDITIONS and cond_label not in ALLOWED_CONDITIONS:
                excluded_condition += 1
                continue

            # Rinkos istorija: kaupiame IR skaiciuojame medianą PO to, kai
            # suzinome sio konkretaus skelbimo kaina+bukle (anksciau tai
            # darydavome is anksto is viso katalogo puslapio, dabar tokio
            # nebeturime).
            record_prices(price_history, f"{q}|{cond_key}", [price])
            if cond_key not in market_cache:
                market_cache[cond_key] = market_median([p for p, _ in price_history.get(f"{q}|{cond_key}", [])])
            mkt = market_cache[cond_key]

            title = og.get("title") or "?"
            cleaned_og_title = re.sub(r"\s*\|\s*Vinted\s*$", "", title, flags=re.IGNORECASE).strip()
            if cleaned_og_title:
                title = cleaned_og_title
            description = og.get("description") or ""

            if not is_relevant_title(q, title):
                excluded_irrelevant += 1
                if DEBUG:
                    print(f"  [DEBUG] atmesta (nerelevantiskas pavadinimas): {title[:60]}")
                continue

            if ONLY_LITHUANIAN_TEXT:
                lang = detect_foreign_language(title, description)
                if lang:
                    excluded_foreign += 1
                    if DEBUG:
                        print(f"  [DEBUG] atmesta (kalba={lang}): {title[:60]}")
                    continue

            if is_junk(title):
                continue

            # Pardavejo profilis (reputacija + salies kodas) - /api/v2/users/{id}
            # PATVIRTINTA VIS DAR VEIKIA (skirtingai nei katalogo API), tad tai
            # dabar vienintelis patikimas salies saltinis. seller_id gautas
            # is paties skelbimo puslapio (regex, nes katalogo JSON nebeturime).
            user_info = {}
            country = None
            if FETCH_SELLER_INFO:
                seller_id = og.get("seller_id")
                user_info = fetch_user_info(seller_id)
                time.sleep(DETAIL_SLEEP_SECONDS)
                country = (user_info.get("country_code") or "").upper() or None

            country_ok = (country in ALLOWED_COUNTRY_CODES) if country else (not REQUIRE_KNOWN_COUNTRY)
            if not country_ok:
                excluded_by_country += 1
                if DEBUG:
                    print(f"  [DEBUG] atmesta (salis={country}): {title[:60]}")
                continue

            rep, cnt = get_seller_info(user_info)
            photo = og.get("image") or ""
            if photo.startswith("/"):
                photo = BASE + photo
            a = {
                "query": q, "title": title, "price": price,
                "url": full_url, "desc": description,
                "rep": rep, "cnt": cnt,
                "market": mkt, "photo": photo,
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

        print(f"  Gauta nuorodu: {len(links)}, tinkama: {fresh}, atmesta salis: {excluded_by_country}, atmesta uzsienio kalba: {excluded_foreign}, atmesta kainos skaitmuo: {excluded_price_digit}, atmesta bukle: {excluded_condition}, atmesta nerelevantiska: {excluded_irrelevant}, nerasta kainos: {excluded_no_price}")
        print(f"  [KASTAI] pigiai atmesta (be papildomos uzklausos): {skipped_cheap}, brangiu skelbimo puslapio uzklausu: {fetched_full_page} (is {len(links)} nuorodu)")
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
