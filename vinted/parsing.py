# -*- coding: utf-8 -*-
"""Duomenu istraukimas is Vinted atsakymu ir skelbimo puslapio."""

import html
import re
from datetime import datetime, timezone

from .util import fold, debug

# --- OpenGraph ------------------------------------------------------------
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
_PROPERTY_RE = re.compile(r'property=["\']([^"\']+)["\']', re.I)
_CONTENT_RE = re.compile(r'content=["\']([^"\']*)["\']', re.I)


def parse_og_tags(html_text):
    og = {}
    for tag in _META_TAG_RE.findall(html_text or ""):
        pm = _PROPERTY_RE.search(tag)
        cm = _CONTENT_RE.search(tag)
        if pm and cm and pm.group(1).startswith("og:"):
            og[pm.group(1)[3:]] = html.unescape(cm.group(1))
    return og


def json_value(page, key):
    """Pirma "key": reiksme skelbimo puslapio JSON'e (ir su \\" kabutemis)."""
    m = re.search(r'\\?"' + re.escape(key) + r'\\?"\s*:\s*(\\?"(.*?)\\?"|-?[\d.]+|true|false|null)', page or "")
    if not m:
        return None
    return m.group(2) if m.group(2) is not None else m.group(1)


# --- Kaina, nuotraukos ------------------------------------------------------
def _to_float(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ".").replace("€", "").replace("\u00a0", "").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def get_price(item):
    """Kaina EUR. Ne euro kaina (kita rinka) -> None."""
    p = item.get("price")
    if isinstance(p, dict):
        if p.get("currency_code") not in (None, "", "EUR"):
            return None
        for k in ("amount", "value", "price"):
            f = _to_float(p.get(k))
            if f is not None:
                return f
        return None
    f = _to_float(p)
    if f is not None:
        return f
    for key in ("price_amount", "amount", "total_item_price", "numeric_price"):
        f = _to_float(item.get(key))
        if f is not None:
            return f
    return None


def get_photo_url(item, og=None):
    photo = item.get("photo") or {}
    if isinstance(photo, dict) and (photo.get("url") or photo.get("full_size_url")):
        return photo.get("url") or photo.get("full_size_url")
    photos = item.get("photos") or []
    if photos and isinstance(photos[0], dict) and photos[0].get("url"):
        return photos[0]["url"]
    return (og or {}).get("image")


def get_photo_count(item):
    """Tikras nuotrauku skaicius, TIK jei Vinted ji pateikia atskiru lauku.
    Katalogo "photos" sarasas turi tik pagrindine nuotrauka, todel jo ilgis
    (visada 1) NENAUDOJAMAS – kitaip visi skelbimai atrodytu "su 1 nuotrauka"."""
    for key in ("photos_count", "photo_count", "total_photos"):
        try:
            return int(item[key])
        except (KeyError, TypeError, ValueError):
            continue
    return None


# --- Bukle ------------------------------------------------------------------
CONDITION_LT = {6: "Nauja su etiketėmis", 1: "Nauja be etikečių", 2: "Labai gera", 3: "Gera", 4: "Patenkinama"}
CONDITION_FOREIGN = {
    "neuf avec etiquette": 6, "neuf sans etiquette": 1, "tres bon etat": 2, "bon etat": 3, "satisfaisant": 4,
    "new with tags": 6, "new without tags": 1, "very good": 2, "good": 3, "satisfactory": 4,
    "neu mit etikett": 6, "neu ohne etikett": 1, "sehr gut": 2, "gut": 3, "zufriedenstellend": 4,
    "nowy z metka": 6, "nowy bez metki": 1, "bardzo dobry": 2, "dobry": 3, "zadowalajacy": 4,
    "nuovo con cartellino": 6, "nuovo senza cartellino": 1, "ottime condizioni": 2,
    "buone condizioni": 3, "discrete condizioni": 4,
    "nuevo con etiquetas": 6, "nuevo sin etiquetas": 1, "muy bueno": 2, "bueno": 3, "satisfactorio": 4,
    "nieuw met prijskaartje": 6, "nieuw zonder prijskaartje": 1, "heel goed": 2, "goed": 3, "redelijk": 4,
    "jauns ar birkam": 6, "jauns bez birkam": 1, "loti labs": 2, "labs": 3, "apmierinoss": 4,
    "nove s visackou": 6, "nove bez visacky": 1, "velmi dobry": 2, "uspokojivy": 4,
}
_LT_CONDITIONS = {fold(v.lower()): k for k, v in CONDITION_LT.items()}


def get_condition(item, page=""):
    """Bukle lietuviskai ("Labai gera") arba None."""
    status_id = item.get("status_id")
    status = item.get("status")
    if isinstance(status, dict):
        status_id = status_id or status.get("id")
        status = status.get("title")
    try:
        if int(status_id) in CONDITION_LT:
            return CONDITION_LT[int(status_id)]
    except (TypeError, ValueError):
        pass
    text = status if isinstance(status, str) and status.strip() else None
    if not text:
        second = (item.get("item_box") or {}).get("second_line") or ""
        text = second.split("·")[-1].strip() or None
    if not text and page:
        for cond in CONDITION_LT.values():
            if f'"{cond}\\"' in page or f'"{cond}"' in page:
                return cond
    if not text:
        return None
    key = fold(text.lower()).strip()
    if key in _LT_CONDITIONS:
        return CONDITION_LT[_LT_CONDITIONS[key]]
    if key in CONDITION_FOREIGN:
        return CONDITION_LT[CONDITION_FOREIGN[key]]
    return text


# --- Pardavejas -------------------------------------------------------------
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


def _country(v):
    if not isinstance(v, str) or not v.strip():
        return None
    v = v.strip()
    if len(v) == 2 and v.isalpha():
        return v.upper()
    return COUNTRY_NAMES.get(v.lower())


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _account_age_days(user):
    for key in ("created_at", "registration_date", "registered_at", "member_since"):
        v = user.get(key)
        if v in (None, ""):
            continue
        try:
            if isinstance(v, (int, float)) or str(v).isdigit():
                dt = datetime.fromtimestamp(float(v), tz=timezone.utc)
            else:
                dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            return max(0, (datetime.now(timezone.utc) - dt).days)
        except (TypeError, ValueError, OSError):
            continue
    return None


def seller_from_dict(user):
    """Is Vinted 'user' objekto: country, city, rating, reviews, sold, active_items,
    negative, account_age_days (truksta – jei nerasta)."""
    info = {}
    if not isinstance(user, dict):
        return info
    for key in ("country_iso_code", "country_code", "country_title_local", "country_title"):
        c = _country(user.get(key))
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
    for src, dst in (("feedback_count", "reviews"), ("given_item_count", "sold"),
                     ("item_count", "active_items"), ("negative_feedback_count", "negative")):
        n = _int(user.get(src))
        if n is not None:
            info[dst] = n
    age = _account_age_days(user)
    if age is not None:
        info["account_age_days"] = age
    return info


def seller_from_page(page):
    """Is skelbimo puslapio (atsargiai: salis tik jei puslapyje ji viena)."""
    info = {}
    if not page:
        return info
    codes = set()
    for key in ("country_iso_code", "country_code", "country_title_local", "country_title"):
        for v in re.findall(r'\\?"' + key + r'\\?"\s*:\s*\\?"([^"\\]{2,40})\\?"', page):
            c = _country(v)
            if c:
                codes.add(c)
    if len(codes) == 1:
        info["country"] = codes.pop()
    elif codes:
        debug(f"puslapyje kelios salys {codes} – salis nezinoma")
    try:
        info["rating"] = round(float(json_value(page, "feedback_reputation")) * 5, 1)
        info["reviews"] = int(float(json_value(page, "feedback_count")))
    except (TypeError, ValueError):
        pass
    return info


# --- Ar skelbimas parduotas ---------------------------------------------------
_CLOSED_RE = re.compile(r'\\?"is_closed\\?"\s*:\s*(true|false)')
_SOLD_RES = [
    re.compile(r'\\?"item_closing_action\\?"\s*:\s*\\?"sold'),
    re.compile(r'\\?"is_sold\\?"\s*:\s*true'),
    re.compile(r'\\?"status\\?"\s*:\s*\\?"sold\\?"'),
]


def listing_status(http_status, page, final_url, item_id):
    """'sold' / 'gone' (istrintas) / 'active' / 'unknown'."""
    if http_status in (404, 410):
        return "gone"
    if http_status != 200 or not page:
        return "unknown"
    if str(item_id) not in (final_url or "") and "/items/" not in (final_url or ""):
        return "gone"                      # nukreipe i kataloga – skelbimo nebera
    m = _CLOSED_RE.search(page)            # pirmas "is_closed" priklauso paciam skelbimui
    if m and m.group(1) == "true":
        return "sold"
    if any(rx.search(page) for rx in _SOLD_RES):
        return "sold"
    return "active"
