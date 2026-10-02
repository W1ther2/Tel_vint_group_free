# -*- coding: utf-8 -*-
"""Saltinio sasaja. Nauja svetaine = vienas failas, kuris paveldi `Source`."""

from .. import config


class Source:
    """Ka privalo moketi saltinis.

    name   – trumpas raktas, patenkantis i ID ("vinted:123"). Keisti negalima:
             pagal ji atpazistami seni irasai state.json / seen.json.
    label  – kaip rodoma zmogui Telegram kortelėje.
    """

    name = "?"
    label = "?"
    # Kiek puslapiu imti, palyginti su bendru nustatymu. Skelbiu rodo 24 skelbimus
    # puslapyje, Vinted – 96, todel Skelbiu reikia daugiau puslapiu tam paciam kiekiui.
    pages_multiplier = 1
    # Ar perkant imamas pirkejo apsaugos mokestis (Vinted – taip, Skelbiu – ne).
    buyer_protection_fee = True
    # Ar `detail()` eina i tinkla. Kai saltinis aprasyma jau turi is saraso (JSON API),
    # pauze po jo nereikalinga.
    detail_needs_request = True

    def __init__(self):
        self.last_error = ""
        self.blocked_queries = 0      # kiek paiesku is eiles saltinis atmete
        self.unavailable = ""         # netuscia = saltinis siame paleidime nepasiekiamas

    # --- gyvavimo ciklas --------------------------------------------------
    def start(self):
        """Sesija, prisijungimas, antiboto apejimas – jei reikia."""

    def finish(self, run):
        """Po visu paiesku: statistika i log'a (pvz. daznos kategorijos)."""

    # --- duomenys ---------------------------------------------------------
    def page_count(self, pages):
        """Kiek puslapiu imti siam saltiniui."""
        return max(1, int(pages * self.pages_multiplier))

    def queries(self):
        """Paieskos frazes. Saltinis gali turėti savo (kitokia rasyba, kategorijos)."""
        return list(config.cfg["SEARCH_QUERIES"])

    def describe(self, query):
        """Kaip paieska atrodo log'e."""
        return f"'{query}'"

    def search(self, query, pages, seen=None):
        """[Listing, ...] – katalogo puslapiai. `seen` = jau matytu uid rinkinys."""
        raise NotImplementedError

    def detail(self, listing):
        """`Detail` vienam skelbimui (aprasymas, bukle, pardavejas, ar parduotas)."""
        raise NotImplementedError

    def status(self, listing_id, url=None):
        """'active' / 'sold' / 'gone' / 'unknown'. `url` – issaugotas adresas, jei yra."""
        return "unknown"

    def seller_country(self, listing):
        """Pardavejo salies kodas ('LT'), kai ji pasakoma jau sarase.

        Lietuviskos svetaines (Skelbiu, Pirkpard) salį zino is karto. Vinted – ne:
        jo katalogas grazina tik pardavejo ID, tad ten sis metodas eina uzklausti
        (zr. vinted_source.py). Butina ZINOTI pries rinkos statistika: uzsienio
        skelbimu kainos negali patekti i Lietuvos rinkos kaina."""
        return (listing.seller or {}).get("country")

    # Ar `seller_country()` eina i tinkla (tada uzklausu kiekis ribojamas).
    country_needs_request = False
    # True, kai saltinis siuo metu sulaikytas uzklausu greicio ribos (HTTP 429). Tada
    # nesekme (neatsidares skelbimo puslapis) yra MUSU, ne skelbimo kalte – zr.
    # Run.detail_failed: tokia nesekme neskaiciuojama i DETAIL_RETRIES.
    rate_limited = False
    # Uzklausu skaitliukas (vinted.limiter.HostLimiter) arba None, jei saltinis jo neturi.
    limiter = None
    # True, kai saltinis salies uzklausu siuo metu nebepriima (pvz. HTTP 429).
    country_lookups_blocked = False
    # Kiek sekundziu liko iki to bloko pabaigos (0 – blokas laikinas nera arba jau praejo).
    country_cooldown_left = 0.0

    # --- pagalbinės -------------------------------------------------------
    def local_ids(self, seen):
        """Is bendro uid rinkinio ({'vinted:1', 'skelbiu:2'}) palieka tik siam
        saltiniui priklausancius vietinius ID – tokius, kokius supranta API."""
        prefix = self.name + ":"
        return {k[len(prefix):] for k in (seen or ()) if k.startswith(prefix)}
