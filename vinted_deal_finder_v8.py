# -*- coding: utf-8 -*-
"""
Vinted deal finder v12 — LT rinkos filtras (patobulintas).
Filtruoja pagal pardavėjo ŠALĮ (patikima), o ne pagal kalbą (nepatikima).
"""

import requests
import json
import os
import time
import re
import html

# ========== SUSIKONFIGUROK SITAS EILUTES ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID", "")

# ================== KONFIGURACIJA ==================
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
    # DABAR: grieztai reikalaujame, kad salis butu zinoma (LT).
    # Jei salies nustatyti nepavyksta – skelbimas atmetamas.
    "REQUIRE_KNOWN_COUNTRY": True,
    # DABAR: kalbos filtras ISJUNGTAS. Jis buvo per agresyvus ir atmesdavo
    # lietuviskus skelbimus su angliskais pavadinimais (iPhone, Pro Max...).
    # Paliekamas kaip atsarginis variantas, kai salis nezinoma (zr. zemiau).
    "ONLY_LITHUANIAN_TEXT": False,
    # Jei salis nezinoma IR ONLY_LITHUANIAN_TEXT=True, taikomas kalbos filtras
    # kaip papildoma apsauga. Jei REQUIRE_KNOWN_COUNTRY=True – tai neaktualu.
    "PRICE_LAST_DIGITS": [],
    "ALLOWED_CONDITIONS": [],
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
ALLOWED_COUNTRY_CODES = [c.upper() for c in _CFG["ALLOWED_COUNTRY_CODES"]]
ALLOWED_CONDITIONS = list(_CFG.get("ALLOWED_CONDITIONS") or [])
REQUIRE_KNOWN_COUNTRY = bool(_CFG["REQUIRE_KNOWN_COUNTRY"])
ONLY_LITHUANIAN_TEXT = bool(_CFG["ONLY_LITHUANIAN_TEXT"])
PRICE_LAST_DIGITS = set(_CFG["PRICE_LAST_DIGITS"])
PAGES = int(_CFG["PAGES"])
SLEEP_SECONDS = int(_CFG["SLEEP_SECONDS"])
DRY_RUN = bool(_CFG["DRY_RUN"])
DEBUG = bool(_CFG["DEBUG"])
SEEN_MAX_AGE_DAYS = int(_CFG["SEEN_MAX_AGE_DAYS"])
SEEN_MAX_ENTRIES = int(_CFG["SEEN_MAX_ENTRIES"])
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
    if not os.path.exists(SEEN_FILE):
        return {}
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return {}
        data = json.loads(content)
        now = time.time()
        if isinstance(data, list):
            return {str(x): now for x in data}
        if isinstance(data, dict):
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


def fetch_page_with_retry(query, page, max_retries=3):
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
        if batch is None:
            break
        if not batch:
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
    og = {}
    for tag in _META_TAG_RE.findall(html_text):
        pm = _PROPERTY_RE.search(tag)
        if not pm or not pm.group(1).startswith("og:"):
            continue
        cm = _CONTENT_RE.search(tag)
        if not cm:
            continue
        key = pm.group(1)[3:]
        og[key] = html.unescape(cm.group(1))
    return og


def fetch_item_page_og(item_id, url_path, max_bytes=200_000):
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
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ".").strip())
    except (ValueError, TypeError):
        return None


def get_price(item):
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
            return int(s) / 100.0
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


# ================== ŠALIES NUSTATYMAS (PATOBULINTAS) ==================

def _extract_country_from_obj(obj):
    """Bando iš bet kokio objekto ištraukti šalies kodą (įvairūs galimi laukai)."""
    if not isinstance(obj, dict):
        return None
    # Tiesioginiai laukai
    for key in ("country_code", "country_iso_code", "country_iso", "iso_code"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().upper()
    # country gali būti objektas
    c = obj.get("country")
    if isinstance(c, str) and c.strip():
        return c.strip().upper()
    if isinstance(c, dict):
        for k in ("code", "iso_code", "country_code"):
            v = c.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip().upper()
    return None


def get_country_code(item):
    """Atsarginis variantas – iš profile_url domeno."""
    user = item.get("user") or {}
    url = user.get("profile_url") or ""
    m = re.search(r"vinted\.([a-z.]+)/", url)
    if not m:
        return None
    domain = m.group(1)
    if domain == "co.uk":
        return "UK"
    return domain.upper()


def get_item_country(item, user_info=None):
    """Nustato pardavėjo šalies kodą iš VISŲ įmanomų šaltinių (patikimiausias
    pirmas). Grąžina 'LT', 'PL', ... arba None.

    Tvarka:
      1. Pilnas pardavėjo profilis (user_info) – patikimiausias
      2. Katalogo skelbimo lygmuo (item.country_code ir pan.)
      3. item.user.country_code
      4. profile_url domenas – mažiausiai patikimas
    """
    # 1. user_info (iš /api/v2/users/{id})
    c = _extract_country_from_obj(user_info)
    if c:
        return c

    # 2. Katalogo skelbimo lygmuo
    c = _extract_country_from_obj(item)
    if c:
        return c

    # 3. user objekte
    user = item.get("user") or {}
    c = _extract_country_from_obj(user)
    if c:
        return c

    # 4. profile_url domenas
    return get_country_code(item)

# ====================================================================


# Vinted "status_id" reiksmes stabilios visose salyse.
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


# --- Kalbos aptikimas (dabar tik kaip ATSARGINIS variantas) ----------
import re as _re


def _word_regex(words):
    parts = sorted((_re.escape(w) for w in words), key=len, reverse=True)
    return _re.compile(r"\b(?:" + "|".join(parts) + r")\b")


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

LATVIAN_ONLY_CHARS = set("āēīōūļņģ")

GERMAN_ONLY_CHARS = set("äöüß")
GERMAN_WORDS = [
    "verkaufe", "neuwertig", "versand", "zustand", "gebraucht",
    "originalverpackung", "rechnung", "funktioniert", "einwandfrei",
]
_GERMAN_RE = _word_regex(GERMAN_WORDS)

ENGLISH_WORDS = [
    "selling", "brand new", "like new", "shipping", "great condition",
    "excellent condition", "as new", "no issues", "works perfectly",
]
_ENGLISH_RE = _word_regex(ENGLISH_WORDS)

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
    t = " ".join(x for x in texts if x).lower()
    if not t:
        return None
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
    if reputation is None:
        return "nėra duomenų"
    n = max(0, min(5, round(reputation * 5)))
    stars = "\u2605" * n + "\u2606" * (5 - n)
    line = stars + f" ({reputation * 5:.1f}/5"
    if feedback_count is not None:
        line += f", {feedback_count} atsiliep."
    return line + ")"


def get_seller_info(user_info):
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


def market_median(prices):
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


def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("Nenurodyti BOT_TOKEN / CHAT_ID (GitHub Secrets)!")
        return

    print(f"Filtrai: ALLOWED_COUNTRY_CODES={ALLOWED_COUNTRY_CODES}, "
          f"REQUIRE_KNOWN_COUNTRY={REQUIRE_KNOWN_COUNTRY}, "
          f"ONLY_LITHUANIAN_TEXT={ONLY_LITHUANIAN_TEXT}")

    init_session()

    seen = load_seen()
    new_seen = dict(seen)
    alerts = []
    total_fetched = 0

    for model in MODELS:
        q = model["query"]
        print(f"Tikrinama: '{q}' ({model['min_price']}-{model['max_price']} EUR)...")
        items = fetch_items(q, PAGES)
        total_fetched += len(items)
        fresh = 0

        prices_by_condition = {}
        for it in items:
            if isinstance(it, dict):
                ap = get_price(it)
                if ap and ap > 0:
                    ckey, _ = get_condition(it)
                    prices_by_condition.setdefault(ckey, []).append(ap)
        market_by_condition = {k: market_median(v) for k, v in prices_by_condition.items()}

        excluded_by_country = 0
        excluded_unknown_country = 0
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

            price = get_price(item)
            if price is None or not (model["min_price"] <= price <= model["max_price"]):
                continue

            if PRICE_LAST_DIGITS and int(price) % 10 not in PRICE_LAST_DIGITS:
                excluded_price_digit += 1
                continue

            cond_key, cond_label = get_condition(item)
            if ALLOWED_CONDITIONS and cond_key not in ALLOWED_CONDITIONS and cond_label not in ALLOWED_CONDITIONS:
                excluded_condition += 1
                continue

            title = item.get("title") or item.get("name") or "?"
            url_path = item.get("url") or item.get("path") or item.get("web_url") or ""
            full_url = BASE + url_path if url_path.startswith("/") else url_path

            # --- ŠALIES NUSTATYMAS (patobulintas) ---
            catalog_user = item.get("user") or {}
            user_id = catalog_user.get("id")
            user_info = fetch_user_info(user_id)
            time.sleep(DETAIL_SLEEP_SECONDS)

            country = get_item_country(item, user_info)

            # --- ŠALIES FILTRAS ---
            if country:
                # Šalis žinoma – tikriname, ar ji leidžiama
                if country not in ALLOWED_COUNTRY_CODES:
                    excluded_by_country += 1
                    if DEBUG:
                        print(f"  [DEBUG] atmesta (salis={country}): {title[:60]}")
                    continue
            else:
                # Šalis NEŽINOMA
                if REQUIRE_KNOWN_COUNTRY:
                    excluded_unknown_country += 1
                    if DEBUG:
                        print(f"  [DEBUG] atmesta (salis=nezinoma): {title[:60]}")
                    continue
                # Jei REQUIRE_KNOWN_COUNTRY=False, taikome kalbos filtrą kaip atsarginį
                if ONLY_LITHUANIAN_TEXT:
                    lang = detect_foreign_language(title, "")
                    if lang:
                        excluded_foreign += 1
                        if DEBUG:
                            print(f"  [DEBUG] atmesta (salis=nezinoma, kalba={lang}): {title[:60]}")
                        continue

            # --- Aprašymas ir OG (tik praėjus šalies filtrui – taupome užklausas) ---
            og = fetch_item_page_og(item_id, url_path)
            time.sleep(DETAIL_SLEEP_SECONDS)
            if og.get("title"):
                title = og["title"]
            description = og.get("description") or ""

            # Papildomas kalbos filtras PRAĖJUS šalies filtrui (jei įjungtas)
            if ONLY_LITHUANIAN_TEXT:
                lang = detect_foreign_language(title, description)
                if lang:
                    excluded_foreign += 1
                    if DEBUG:
                        print(f"  [DEBUG] atmesta (kalba={lang}, salis={country}): {title[:60]}")
                    continue

            if is_junk(title):
                continue

            rep, cnt = get_seller_info(user_info)
            photo = og.get("image") or ""
            if photo.startswith("/"):
                photo = BASE + photo
            alerts.append({
                "query": q, "title": title, "price": price,
                "url": full_url, "desc": description,
                "rep": rep, "cnt": cnt,
                "market": market_by_condition.get(cond_key), "photo": photo,
                "condition": cond_label, "country": country,
            })
            fresh += 1
            if DEBUG:
                print(f"  [DEBUG] PRIIMTA (salis={country}, bukle={cond_label}): {title[:60]}")

        print(f"  Gauta: {len(items)}, tinkama: {fresh}, "
              f"atmesta salis: {excluded_by_country}, "
              f"atmesta nezinoma salis: {excluded_unknown_country}, "
              f"atmesta kalba: {excluded_foreign}, "
              f"atmesta kainos skaitmuo: {excluded_price_digit}, "
              f"atmesta bukle: {excluded_condition}")
        time.sleep(SLEEP_SECONDS)

    alerts.sort(key=lambda a: a["price"])
    save_seen(new_seen)

    if total_fetched == 0 and not DRY_RUN and BOT_TOKEN and CHAT_ID:
        send_telegram("<b>ISPEJIMAS</b>: negauta nei vieno skelbimo is Vinted. "
                      "Galimai pasikeite API – patikrink skripto logus.")

    if not alerts:
        print("Nauju deal'u nera.")
        return

    if DRY_RUN:
        print(f"[DRY_RUN] Rasta {len(alerts)} dealu, bet zinuciu NESIUNCIAMA.")
        for a in alerts:
            print(f"  [DRY] {a['country']} | {a['price']:.0f}€ | {a['title'][:60]}")
        return

    for a in alerts:
        desc = " ".join((a["desc"] or "").split())[:140]
        desc_esc = html.escape(desc) + ("…" if len((a["desc"] or "")) > 140 else "")

        lines = [
            "<b>" + html.escape(a["query"]) + "</b> | <b>" + f'{a["price"]:.0f} €' + "</b>",
        ]
        if desc_esc:
            lines.append(desc_esc)
        lines.append("")
        lines.append("<b>📦 Būklė:</b> " + html.escape(a["condition"]))
        lines.append("<b>🌍 Šalis:</b> " + html.escape(a["country"] or "nežinoma"))
        lines.append("<b>⭐ Pardavėjas:</b> " + stars_line(a["rep"], a["cnt"]))
        if a["market"]:
            profit = a["market"] - a["price"]
            lines.append("<b>📊 Rinkos vertė (tos pačios būklės):</b> ~" + f'{a["market"]:.0f} €')
            lines.append("<b>💰 Planuojamas pelnas:</b> ~" + f'{profit:+.0f} €')
        lines.append("")
        lines.append('<a href="' + a["url"] + '">Atidaryti skelbimą</a>')

        msg = "\n".join(lines)
        send_telegram_photo(a["photo"], msg)
        print(f'  -> [{a["country"]}] {a["query"]} {a["price"]:.0f} EUR: {a["title"][:50]}')

    print(f"Issiusta {len(alerts)} alert'u.")


def run_with_guard():
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
