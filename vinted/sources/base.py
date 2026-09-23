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

    # --- pagalbinės -------------------------------------------------------
    def local_ids(self, seen):
        """Is bendro uid rinkinio ({'vinted:1', 'skelbiu:2'}) palieka tik siam
        saltiniui priklausancius vietinius ID – tokius, kokius supranta API."""
        prefix = self.name + ":"
        return {k[len(prefix):] for k in (seen or ()) if k.startswith(prefix)}
