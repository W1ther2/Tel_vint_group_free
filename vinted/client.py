# -*- coding: utf-8 -*-
"""Vinted HTTP klientas: sesija, katalogas, skelbimo puslapis, pardavejo profilis."""

import html
import json
import re
import time

import requests

try:
    from curl_cffi import requests as cffi_requests
    USING_CFFI = True
except ImportError:          # pragma: no cover
    cffi_requests = None
    USING_CFFI = False

from . import config
from .config import BASE, API_BASE
from .util import debug

# Katalogo adresai – bandomi is eiles, kol vienas suveikia.
CATALOG_ENDPOINTS = [
    API_BASE + "/svc-catalogue/items",   # naujas (nuo 2026-09)
    BASE + "/api/v2/catalog/items",      # senas
]

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "lt-LT,lt;q=0.9,en;q=0.8",
    "Referer": BASE + "/",
}


def short_body(text, limit=150):
    """Is HTML klaidos puslapio padaro trumpa skaitoma teksta."""
    t = re.search(r"<title>(.*?)</title>", text or "", re.I | re.S)
    if t:
        return "puslapis: " + html.unescape(t.group(1)).strip()[:limit]
    return re.sub(r"\s+", " ", text or "")[:limit]


def looks_newest_first(batch):
    """Vinted skelbimu ID dideja laikui begant. Jei >= 80% gretimu poru ID mazeja –
    sarasas surikiuotas nuo naujausiu ir stabdyti puslapiavima saugu."""
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


class VintedClient:
    def __init__(self, session_factory=None, sleep=time.sleep):
        self._factory = session_factory or self._default_session
        self.sleep = sleep
        self.session = None
        self.anon_id = None
        self.csrf = None
        self.working_endpoint = None
        self.last_error = ""
        self._user_cache = {}
        self._printed_first_item = False
        self.blocked_queries = 0        # kiek paieskų is eiles Vinted atmete (403)
        self.filters_off = False        # True, kai kategoriju filtras neveikia (grazina 0 skelbimu)

    # --- sesija ---------------------------------------------------------
    @staticmethod
    def _default_session():
        if USING_CFFI:
            return cffi_requests.Session(impersonate="chrome")
        return requests.Session()

    def headers(self, json_api=True):
        h = dict(BASE_HEADERS)
        if USING_CFFI:
            h.pop("User-Agent")          # curl_cffi pats nustato tikra Chrome User-Agent
        if json_api:
            # Be situ api.vinted.lt nezino rinkos: grazina skelbimus is viso pasaulio,
            # bukles prancuziskai, kainas doleriais. Locale = Lietuvos rinka.
            h["Locale"] = "lt-LT"
            h["X-Next-App"] = "marketplace-web"
            h["Platform"] = "web"
            if self.anon_id:
                h["X-Anon-Id"] = self.anon_id
            if self.csrf:
                h["X-Csrf-Token"] = self.csrf
        else:
            h["Accept"] = "text/html,application/xhtml+xml,*/*"
        return h

    def start(self, attempts=3):
        """Vinted kartais laikinai blokuoja serverio IP (403) – bandom kelis kartus."""
        for attempt in range(1, attempts + 1):
            self._start_once()
            if not self.last_error or "403" not in self.last_error:
                return
            if attempt < attempts:
                wait = 10 * attempt
                print(f"  ! Vinted laikinai blokuoja (403) – laukiu {wait}s ir bandau dar karta "
                      f"({attempt}/{attempts})...")
                self.sleep(wait)

    def _start_once(self):
        self.session = self._factory()
        self.last_error = ""
        try:
            r = self.session.get(BASE + "/", headers=self.headers(json_api=False), timeout=20)
            print(f"Sesija pradeta (statusas {r.status_code})")
            self.anon_id = r.headers.get("x-anon-id") or self.session.cookies.get("anon_id")
            m = (re.search(r'CSRF_TOKEN\\?"\s*:\s*\\?"([0-9a-f-]{36})', r.text or "") or
                 re.search(r'"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"', r.text or ""))
            self.csrf = m.group(1) if m else None
            if r.status_code != 200:
                self.last_error = f"Pagrindinis puslapis: HTTP {r.status_code}"
            debug(f"klientas={'curl_cffi' if USING_CFFI else 'requests'}, anon_id={self.anon_id}, csrf={self.csrf}")
        except Exception as e:
            self.last_error = f"Nepavyko pradeti sesijos: {e}"
            print(f"! {self.last_error}")
        self.sleep(2)

    # --- katalogas ------------------------------------------------------
    def _request_catalog(self, url, query, page, max_retries=3):
        """Grazina items sarasa, "404" arba None."""
        wait = config.cfg["SLEEP_SECONDS"]
        params = {"search_text": query, "order": "newest_first", "per_page": 96,
                  "page": page, "currency": "EUR"}
        params.update(self.filter_params())
        short = url.split("//", 1)[-1]
        for attempt in range(1, max_retries + 1):
            try:
                resp = self.session.get(url, params=params, headers=self.headers(), timeout=20)
                code = resp.status_code
                if code in (401, 403):
                    # Vinted laikinai blokuoja serverio IP – tik nauja sesija nepadeda,
                    # reikia ilgesnes pauzes (30 s, 60 s, 120 s).
                    self.last_error = f"HTTP {code} ({short}): {short_body(resp.text)}"
                    backoff = config.cfg["BLOCK_BACKOFF_SECONDS"]
                    pause = backoff[min(attempt - 1, len(backoff) - 1)]
                    print(f"  ! {code} – Vinted blokuoja, laukiu {pause}s "
                          f"(bandymas {attempt}/{max_retries})...")
                    self.sleep(pause)
                    if attempt == max_retries - 1:
                        self.start(attempts=1)      # paskutinis bandymas – dar ir nauja sesija
                    continue
                if code == 429:
                    self.last_error = f"HTTP 429 ({short}): per daug uzklausu"
                    print(f"  ! 429 per daug uzklausu – laukiu {wait * attempt * 2}s...")
                    self.sleep(wait * attempt * 2)
                    continue
                if code >= 500:
                    self.last_error = f"HTTP {code} ({short}): serverio klaida"
                    self.sleep(wait * attempt)
                    continue
                if code != 200:
                    self.last_error = f"HTTP {code} ({short}): {short_body(resp.text)}"
                    print(f"  ! '{query}' p.{page}: {self.last_error}")
                    return "404" if code == 404 else None
                data = resp.json()
                batch = data.get("items") if isinstance(data, dict) else None
                if not isinstance(batch, list):
                    self.last_error = f"{short}: netiketas atsakymo formatas"
                    print(f"  ! '{query}' p.{page}: {self.last_error}")
                    return None
                if batch and not self._printed_first_item:
                    debug("pirmas skelbimas is API: " + json.dumps(batch[0], ensure_ascii=False)[:1500])
                    self._printed_first_item = True
                return batch
            except Exception as e:
                self.last_error = f"Tinklo/JSON klaida ({short}): {e}"
                print(f"  ! {self.last_error} (bandymas {attempt}/{max_retries})")
                self.sleep(wait * attempt)
        return None

    def filter_params(self):
        """Kategorijos / prekes zenklo filtras (naujas API naudoja attribute_ids[...])."""
        if self.filters_off:
            return {}
        c = config.cfg
        params = {}
        if c["CATALOG_IDS"]:
            params["attribute_ids[catalog]"] = ",".join(str(x) for x in c["CATALOG_IDS"])
            params["catalog_ids"] = ",".join(str(x) for x in c["CATALOG_IDS"])
        if c["BRAND_IDS"]:
            params["attribute_ids[brand]"] = ",".join(str(x) for x in c["BRAND_IDS"])
            params["brand_ids"] = ",".join(str(x) for x in c["BRAND_IDS"])
        return params

    def fetch_page(self, query, page):
        endpoints = [self.working_endpoint] if self.working_endpoint else CATALOG_ENDPOINTS
        first_error = None
        for url in endpoints:
            batch = self._request_catalog(url, query, page)
            if isinstance(batch, list):
                if self.working_endpoint != url:
                    print(f"  Naudojamas API adresas: {url}")
                    self.working_endpoint = url
                return batch
            first_error = first_error or self.last_error
            if batch != "404":
                break
        self.last_error = first_error or self.last_error
        return None

    def fetch_items(self, query, pages, seen=None):
        """Visu puslapiu skelbimai (be pasikartojimu). Jei visi puslapio skelbimai
        jau matyti ir sarasas rikiuotas nuo naujausiu – toliau nebeziurim."""
        items, known = [], set()
        for page in range(1, pages + 1):
            batch = self.fetch_page(query, page)
            if page == 1 and batch == [] and not self.filters_off and self.filter_params():
                # filtras negrazino nieko – tikriausiai blogi ID; kartojam be filtro
                self.filters_off = True
                print("  ! Kategorijos/brando filtras negrazino nieko – toliau be filtro "
                      "(patikrink CATALOG_IDS / BRAND_IDS).")
                batch = self.fetch_page(query, page)
            if not batch:
                if page == 1 and "403" in (self.last_error or ""):
                    self.blocked_queries += 1
                break
            self.blocked_queries = 0
            for b in batch:
                if isinstance(b, dict) and str(b.get("id")) not in known:
                    known.add(str(b.get("id")))
                    items.append(b)
            if page == 1:
                ids = [b.get("id") for b in batch[:3] if isinstance(b, dict)]
                print(f"  p.1: {len(batch)} skelb., naujausi ID: {ids}, "
                      f"rikiuota nuo naujausiu: {'taip' if looks_newest_first(batch) else 'NE'}")
            if seen and all(isinstance(b, dict) and str(b.get("id")) in seen for b in batch):
                if looks_newest_first(batch):
                    print(f"  p.{page}: visi skelbimai jau matyti – toliau nebetikrinu")
                    break
            if page < pages:
                self.sleep(config.cfg["SLEEP_SECONDS"])
        return items

    # --- skelbimo puslapis ----------------------------------------------
    def fetch_item_page(self, url, max_bytes=3_000_000):
        """Grazina (http_statusas, html, galutinis_url). Klaidos atveju (0, "", url)."""
        full = BASE + url if url.startswith("/") else url
        try:
            r = self.session.get(full, headers=self.headers(json_api=False), timeout=20)
            return r.status_code, (r.text or "")[:max_bytes], str(getattr(r, "url", full) or full)
        except Exception as e:
            debug(f"skelbimo puslapio klaida: {e}")
            return 0, "", full

    # --- pardavejas -----------------------------------------------------
    def fetch_user(self, user_id):
        """Pardavejo profilis per Vinted API (gali buti blokuojamas – tada {})."""
        if not user_id:
            return {}
        if user_id in self._user_cache:
            return self._user_cache[user_id]
        user = {}
        for base in (BASE, API_BASE):
            try:
                r = self.session.get(f"{base}/api/v2/users/{user_id}", headers=self.headers(), timeout=20)
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, dict) and isinstance(data.get("user"), dict):
                        user = data["user"]
                        break
                else:
                    debug(f"vartotojo {user_id} API ({base}): HTTP {r.status_code}")
            except Exception as e:
                debug(f"vartotojo {user_id} API klaida: {e}")
        self._user_cache[user_id] = user
        return user
