# -*- coding: utf-8 -*-
"""Pirkpard.lt saltinis.

Skirtingai nei Skelbiu, Pirkpard turi tvarkinga JSON API, kuria naudoja jo paties
svetaine, ir prisijungimo jai nereikia:

    GET https://pirkpard.lt/api/v1/products?search=iphone&sort=newest&per_page=100&page=1

Svarbiausia: atsakyme jau yra PILNAS aprasymas, bukle, nuotraukos, pardavejas ir
tiksli ikelimo data. Todel atskiro skelbimo puslapio traukti nereikia – visas
paleidimas telpa i viena uzklausa.

API taip pat tiesiogiai sako, kas parduota (`sold_out`, `marked_sold_at`), ko
Vinted ir Skelbiu nesako niekada. Tokie pardavimai yra patikimiausi duomenys
vertinimo tikslumui matuoti.

Ko API NEIMAM: pardavejo el. pasto, telefono numerio, vardo ir nuotraukos.
Tai asmens duomenys, o deal'ui ivertinti jie nereikalingi.
"""

import json
import time
from datetime import datetime, timezone

import requests

try:
    from curl_cffi import requests as cffi_requests
    USING_CFFI = True
except ImportError:          # pragma: no cover
    cffi_requests = None
    USING_CFFI = False

from .. import config
from ..listing import Listing, Detail
from ..util import debug
from .base import Source

SITE = "https://pirkpard.lt"
API_URL = SITE + "/api/v1/products"
PRODUCT_PATH = "/lt/product/"

HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "lt-LT,lt;q=0.9,en;q=0.8",
    "Referer": SITE + "/lt/search",
}

# Pirkpard bukles -> musu (tos pacios, kurias naudoja Vinted)
CONDITION_MAP = {
    "new": "Nauja su etiketėmis",
    "like_new": "Nauja be etikečių",
    "excellent": "Labai gera",
    "good": "Gera",
    "fair": "Patenkinama",
    "for_parts": None,          # tvarkomas atskirai – tai ne telefonas, o dalys
}


def parse_time(value):
    """'2026-09-23T09:05:58.000000Z' -> unix laikas."""
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        if "." in text:                       # mikrosekundes kartais turi 6+ skaitmenu
            head, _, tail = text.partition(".")
            frac = "".join(c for c in tail if c.isdigit())[:6]
            rest = tail[len(frac):].lstrip("0123456789")
            text = f"{head}.{frac or '0'}{rest}"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def city_of(value):
    """['Vilnius', 'Visa Lietuva'] -> 'Vilnius'."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (list, tuple)):
        named = [str(v).strip() for v in value if str(v).strip()]
        real = [v for v in named if v.lower() != "visa lietuva"]
        return (real or named or [None])[0]
    return None


def seller_of(raw):
    """Tik tai, ko reikia rizikai ivertinti. Jokiu asmens duomenu."""
    vendor = raw.get("vendor") if isinstance(raw.get("vendor"), dict) else {}
    info = {"country": (vendor.get("country") or "LT").upper()}
    city = city_of(raw.get("city"))
    if city:
        info["city"] = city
    if vendor.get("id") is not None:
        info["id"] = str(vendor["id"])
    reviews = vendor.get("total_ratings")
    try:
        reviews = int(reviews)
    except (TypeError, ValueError):
        reviews = None
    if reviews is not None:
        info["reviews"] = reviews
        # Be atsiliepimu ivertinimas yra "0.00" – tai ne prastas pardavejas, o nezinia
        if reviews > 0:
            try:
                info["rating"] = round(float(vendor.get("average_rating") or 0), 1)
            except (TypeError, ValueError):
                pass
    info["verified"] = bool(vendor.get("is_verified"))
    return info


def skip_reason(raw):
    """Ar saltinis jau dabar mato, kad skelbimas netinka."""
    c = config.cfg
    if raw.get("is_service") or raw.get("is_looking_for"):
        return "paslauga / ieskomas skelbimas"
    if raw.get("condition") == "for_parts":
        return "dalims / ne telefonas"
    if raw.get("is_reserved"):
        return "rezervuotas"
    if raw.get("is_auction") and c["PIRKPARD_SKIP_AUCTIONS"]:
        return "aukcionas"
    return ""


def listing_state(raw):
    """'sold' / 'gone' / 'active'."""
    if raw.get("sold_out") or raw.get("marked_sold_at"):
        return "sold"
    if raw.get("deleted_at") or raw.get("expired"):
        return "gone"
    if raw.get("is_active") is False:
        return "sold"           # isjungtas po pardavimo – daznesnis atvejis nei istrintas
    return "active"


class PirkpardClient:
    """JSON uzklausos su pakartojimais."""

    def __init__(self, sleep=time.sleep):
        self.sleep = sleep
        self.session = None
        self.last_error = ""
        self.blocked = ""
        self.ok_count = 0

    def start(self):
        self.session = (cffi_requests.Session(impersonate="chrome") if USING_CFFI
                        else requests.Session())

    def get_json(self, params, tries=3):
        """Grazina atsakyma (dict) arba None."""
        if self.blocked:
            return None
        if self.session is None:
            self.start()
        wait = config.cfg["SLEEP_SECONDS"]
        for attempt in range(1, tries + 1):
            try:
                r = self.session.get(API_URL, params=params, headers=HEADERS, timeout=25)
                if r.status_code in (401, 403, 429):
                    body = (r.text or "")[:200]
                    headers = {str(k).lower(): str(v) for k, v in dict(r.headers or {}).items()}
                    cloudflare = "cf-ray" in headers or "cloudflare" in headers.get("server", "")
                    reason = "Cloudflare apsauga" if cloudflare else f"HTTP {r.status_code}"
                    self.last_error = f"Pirkpard {reason}"
                    if not self.ok_count:
                        # Atmete nuo pirmos uzklausos – laukti nera prasmes
                        self.blocked = reason
                        print(f"  ! Pirkpard {r.status_code} nuo pirmos uzklausos ({reason}) – "
                              "praleidziu si saltini siame paleidime.")
                        print(f"    Pirkpard atsakymas: {body or '(tuscias)'}")
                        return None
                    backoff = config.cfg["BLOCK_BACKOFF_SECONDS"]
                    self.sleep(backoff[min(attempt - 1, len(backoff) - 1)])
                    continue
                if r.status_code >= 500:
                    self.last_error = f"Pirkpard HTTP {r.status_code}"
                    self.sleep(wait * attempt)
                    continue
                if r.status_code != 200:
                    self.last_error = f"Pirkpard HTTP {r.status_code}"
                    return None
                data = r.json() if callable(getattr(r, "json", None)) else json.loads(r.text)
                if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                    self.last_error = "Pirkpard: netiketas atsakymo formatas"
                    return None
                self.ok_count += 1
                return data
            except Exception as e:
                self.last_error = f"Pirkpard tinklo klaida: {e}"
                debug(self.last_error)
                self.sleep(wait * attempt)
        return None


class PirkpardSource(Source):
    name = "pirkpard"
    label = "Pirkpard"
    buyer_protection_fee = False    # atsiskaitoma platformoje, atskiro pirkejo mokescio nera
    detail_needs_request = False    # aprasymas ateina kartu su sarasu

    def __init__(self, client=None, sleep=time.sleep):
        super().__init__()
        self.client = client if client is not None else PirkpardClient(sleep=sleep)
        self._states = None         # {id: "active"/"sold"/"gone"} pardavimu patikrai

    def queries(self):
        return list(config.cfg["PIRKPARD_QUERIES"])

    def describe(self, query):
        return f"'{query}' (visa svetaine)"

    def start(self):
        self.client.start()
        self.last_error = getattr(self.client, "last_error", "") or ""
        self.unavailable = getattr(self.client, "blocked", "") or ""

    # --- sarasas ----------------------------------------------------------
    def search(self, query, pages, seen=None):
        c = config.cfg
        known = self.local_ids(seen)
        out, collected = [], set()
        for page in range(1, max(1, pages) + 1):
            data = self.client.get_json({"search": query, "sort": "newest",
                                         "per_page": c["PIRKPARD_PER_PAGE"], "page": page})
            self.last_error = self.client.last_error
            self.unavailable = getattr(self.client, "blocked", "") or ""
            if data is None:
                if page == 1:
                    self.blocked_queries += 1
                break
            self.blocked_queries = 0
            rows = data["data"]
            if not rows:
                break
            for raw in rows:
                iid = str(raw.get("id") or "")
                if not iid or iid in collected:
                    continue
                collected.add(iid)
                out.append(self.to_listing(raw))
            if page == 1:
                print(f"  p.1: {len(rows)} skelb., is viso {(data.get('meta') or {}).get('total')}")
            if known and all(str(r.get("id")) in known for r in rows):
                print(f"  p.{page}: visi skelbimai jau matyti – toliau nebetikrinu")
                break
            meta = data.get("meta") or {}
            if meta.get("current_page") and meta.get("last_page") and \
                    meta["current_page"] >= meta["last_page"]:
                break
            if page < pages:
                self.client.sleep(c["SLEEP_SECONDS"])
        return out

    def to_listing(self, raw):
        price = raw.get("price")
        try:
            price = float(price)
        except (TypeError, ValueError):
            price = None
        if (raw.get("currency_code") or "EUR").upper() != "EUR":
            price = None
        handle = raw.get("handle") or ""
        seller = seller_of(raw)
        return Listing(
            source=self.name, id=str(raw.get("id")), title=raw.get("title") or "",
            price=price, url=SITE + PRODUCT_PATH + handle if handle else SITE,
            photo=raw.get("featured_image"),
            condition=CONDITION_MAP.get(raw.get("condition")),
            seller_id=seller.get("id", ""), seller=seller,
            photo_count=len(raw.get("images") or []) or None,
            created_at=parse_time(raw.get("created_at") or raw.get("listing_activated_at")),
            skip_reason=skip_reason(raw), raw=raw,
        )

    # --- vienas skelbimas (be tinklo) --------------------------------------
    def detail(self, listing):
        raw = listing.raw or {}
        state = listing_state(raw)
        if state != "active":
            return Detail(status=state)
        seller = dict(listing.seller)
        seller.pop("verified", None)
        return Detail(status="active", title=listing.title,
                      description=raw.get("description") or "",
                      photo=listing.photo, condition=listing.condition, seller=seller)

    # --- pardavimu patikra -------------------------------------------------
    def states(self):
        """Visi skelbimai (ir parduoti) viena uzklausa – is jos matyti kiekvieno busena."""
        if self._states is not None:
            return self._states
        c = config.cfg
        states, page = {}, 1
        while page <= c["PIRKPARD_STATUS_PAGES"]:
            data = self.client.get_json({"search": c["PIRKPARD_QUERIES"][0] if c["PIRKPARD_QUERIES"] else "iphone",
                                         "sort": "newest", "include_sold": 1,
                                         "per_page": c["PIRKPARD_PER_PAGE"], "page": page})
            if data is None:
                break
            for raw in data["data"]:
                states[str(raw.get("id"))] = listing_state(raw)
            meta = data.get("meta") or {}
            if not data["data"] or (meta.get("last_page") and page >= meta["last_page"]):
                break
            page += 1
        self._states = states
        return states

    def status(self, listing_id, url=None):
        states = self.states()
        if not states:
            return "unknown"
        return states.get(str(listing_id), "gone")
