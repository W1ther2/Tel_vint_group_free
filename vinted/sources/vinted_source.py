# -*- coding: utf-8 -*-
"""Vinted saltinis: katalogo API + skelbimo puslapis + pardavejo profilis."""

import time

from .. import config
from ..client import VintedClient
from ..listing import Listing, Detail
from ..parsing import (parse_og_tags, get_price, get_photo_url, get_photo_count, get_condition,
                       get_created_at, seller_from_dict, seller_from_page, listing_status)
from .base import Source

SELLER_KEYS = ("country", "rating", "reviews", "sold", "account_age_days")


class VintedSource(Source):
    name = "vinted"
    label = "Vinted"
    country_needs_request = True     # katalogas salies nebeduoda – reikia pardavejo uzklausos

    def __init__(self, client=None, sleep=time.sleep):
        super().__init__()
        self.client = client if client is not None else VintedClient(sleep=sleep)
        self.catalog_ids = {}       # Vinted kategoriju ID -> kiek telefonu (filtrui nustatyti)
        self.brand_ids = {}

    # --- ka ieskoti -------------------------------------------------------
    @property
    def browse_all(self):
        return bool(config.cfg["VINTED_BROWSE_ALL"])

    def queries(self):
        """Viena paieska gamintojui: „iphone“ grazina visus iPhone – nuo 8 iki 17 Pro Max,
        „samsung galaxy“ – visus Galaxy. 34 atskiros paieskos duoda ta pati, tik daro
        30+ kartu daugiau uzklausu."""
        if not self.browse_all:
            return super().queries()
        return config.brand_queries(config.cfg["VINTED_QUERIES"])

    def describe(self, query):
        return f"'{query}' (visi modeliai)" if self.browse_all else f"'{query}'"

    def page_count(self, pages):
        """Narsant viena uzklausa galima eiti giliau – vis tiek liks kelios uzklausos."""
        if not self.browse_all:
            return max(1, pages)
        c = config.cfg
        pirmas_kartas = pages >= c["FULL_SCAN_PAGES"]
        norima = c["VINTED_FULL_SCAN_PAGES"] if pirmas_kartas else c["VINTED_BROWSE_PAGES"]
        return max(1, min(norima, c["VINTED_MAX_PAGES"]))

    # --- gyvavimo ciklas --------------------------------------------------
    def start(self):
        self.client.start()

    def _sync(self):
        self.last_error = getattr(self.client, "last_error", "") or ""
        self.blocked_queries = getattr(self.client, "blocked_queries", 0)

    # --- katalogas --------------------------------------------------------
    def search(self, query, pages, seen=None):
        raw_items = self.client.fetch_items(query, pages, self.local_ids(seen))
        self._sync()
        out = []
        for raw in raw_items:
            if isinstance(raw, dict) and raw.get("id"):
                out.append(self.to_listing(raw))
        return out

    def to_listing(self, raw):
        url = raw.get("url") or raw.get("path") or f"/items/{raw['id']}"
        if url.startswith("/"):
            url = config.BASE + url
        user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
        total = raw.get("total_item_price")
        if isinstance(total, dict) and (total.get("currency_code") or "EUR") == "EUR":
            total = get_price({"price": total})       # kaina jau su pirkejo apsaugos mokesciu
        else:
            total = None
        return Listing(
            source=self.name, id=str(raw["id"]), title=raw.get("title") or "",
            price=get_price(raw), total_price=total, url=url, photo=get_photo_url(raw),
            condition=get_condition(raw),
            seller_id=str(user.get("id") or raw.get("user_id") or ""),
            seller=seller_from_dict(user), photo_count=get_photo_count(raw),
            created_at=get_created_at(raw), raw=raw,
        )

    @property
    def country_cooldown_left(self):
        """Kiek liko iki 429 atvesinimo pabaigos – kad `Run` galetu palaukti, o ne nurasyti."""
        left = getattr(self.client, "cooldown_left", None)
        return float(left()) if callable(left) else 0.0

    @property
    def limiter(self):
        """Uzklausu skaitliukas ir greicio riba – bendra visoms Vinted uzklausoms."""
        return getattr(self.client, "limiter", None)

    @property
    def rate_limited(self):
        """Serveris siuo metu riboja uzklausas (429): nesekme laikina ir ne skelbimo kalte."""
        return bool(getattr(self.client, "users_blocked", ""))

    @property
    def country_lookups_blocked(self):
        """Vinted uzdare pardaveju API siame paleidime (429) – toliau klausti nera prasmes."""
        return bool(getattr(self.client, "users_blocked", ""))

    def seller_country(self, listing):
        """Pardavejo salis. Nuo 2026-09 katalogo API pardavejo objekte grazina tik
        `business`, `id` ir `login` – nei salies, nei miesto. Patikrinta gyvai: 45 is 45
        skelbimu salį pasake /api/v2/users/<id>, tad ji imam is ten (vienas pardavejas =
        viena uzklausa, atsakymai kesuojami ir paleidime, ir state.json)."""
        country = (listing.seller or {}).get("country")
        if country or not listing.seller_id:
            return country
        return seller_from_dict(self.client.fetch_user(_as_id(listing.seller_id))).get("country")

    def note_ids(self, listing):
        """Kategorijos / prekes zenklo ID statistika – tik tikriems telefonams."""
        raw = listing.raw
        for kind, store in (("catalog", self.catalog_ids), ("brand", self.brand_ids)):
            val = raw.get(f"{kind}_id")
            if val is None and isinstance(raw.get(kind), dict):
                val = raw[kind].get("id")
            if val is not None:
                store[val] = store.get(val, 0) + 1

    # --- vienas skelbimas -------------------------------------------------
    def detail(self, listing):
        http_status, page, final_url = self.client.fetch_item_page(listing.url)
        status = listing_status(http_status, page, final_url, listing.id)
        if status in ("sold", "gone"):
            return Detail(status=status)
        og = parse_og_tags(page)
        if status == "active" and not og:
            # 200, bet ne skelbimo puslapis (pvz. botu patikros ar klaidos puslapis):
            # aprasymo nera, tad nieko patikrinti negalim
            return Detail(status="unknown")
        return Detail(
            status=status, title=og.get("title") or listing.title,
            description=og.get("description") or "", photo=og.get("image"),
            condition=get_condition(listing.raw, page),
            seller=self._seller(listing, page),
        )

    def _seller(self, listing, page):
        """Katalogo duomenys + (jei truksta) pardavejo API + (jei truksta) puslapis."""
        info = dict(listing.seller)
        uid = listing.seller_id
        if uid and not all(k in info for k in SELLER_KEYS):
            info = {**seller_from_dict(self.client.fetch_user(_as_id(uid))), **info}
        if not all(k in info for k in ("country", "rating", "reviews")):
            info = {**seller_from_page(page), **info}
        return info

    def status(self, listing_id, url=None):
        http_status, page, final_url = self.client.fetch_item_page(url or f"/items/{listing_id}")
        return listing_status(http_status, page, final_url, listing_id)

    # --- log'as -----------------------------------------------------------
    def finish(self, run):
        """Padeda uzpildyti CATALOG_IDS / BRAND_IDS config.json faile."""
        for pavadinimas, store, key in (("kategorijos", self.catalog_ids, "CATALOG_IDS"),
                                        ("prekes zenklai", self.brand_ids, "BRAND_IDS")):
            top = sorted(store.items(), key=lambda kv: -kv[1])[:5]
            if top:
                pairs = ", ".join(f"{i} ({n} telef.)" for i, n in top)
                print(f"Daznos {pavadinimas}: {pairs}   -> config.json \"{key}\": [{top[0][0]}]")


def _as_id(value):
    """Vinted vartotojo ID API'ui – skaicius, jei imanoma."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return value
