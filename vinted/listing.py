# -*- coding: utf-8 -*-
"""Bendra skelbimo forma, nepriklausoma nuo saltinio (Vinted, Skelbiu, ...).

Visa toliau einanti logika (rinkos kaina, filtrai, korteles) dirba su `Listing`
ir `Detail`, o ne su konkretaus saltinio JSON'u. Naujas saltinis = vienas failas
`vinted/sources/`, kuris moka gauti `Listing` sarasa ir `Detail` vienam skelbimui.
"""

from dataclasses import dataclass, field


@dataclass
class Listing:
    """Tai, ka matome saraso (katalogo) puslapyje – be aprasymo."""
    source: str                       # "vinted" / "skelbiu" / ...
    id: str                           # saltinio viduje unikalus ID
    title: str
    price: float                      # EUR (None = kaina nezinoma / kita valiuta)
    url: str                          # pilnas adresas
    photo: str = None
    condition: str = None             # bukle lietuviskai ("Labai gera")
    seller_id: str = ""
    seller: dict = field(default_factory=dict)
    photo_count: int = None
    created_at: float = None          # unix laikas, kada skelbimas ikeltas
    # Kai saltinis jau is saraso mato, kad skelbimas netinka (aukcionas, dalims,
    # rezervuotas), cia irasoma priezastis – ji pateks i atmetimu statistika.
    skip_reason: str = ""
    # Kiek is tikruju sumoketum: kaina + saltinio pirkejo apsaugos mokestis.
    # Vinted ji pateikia tiesiogiai, tad spelioti nebereikia.
    total_price: float = None
    raw: dict = field(default_factory=dict)

    @property
    def uid(self):
        """Visuose failuose naudojamas raktas: 'vinted:123456'."""
        return f"{self.source}:{self.id}"


@dataclass
class Detail:
    """Tai, ka matome atsidare pati skelbima."""
    status: str = "unknown"           # "active" / "sold" / "gone" / "unknown"
    title: str = None
    description: str = ""
    photo: str = None
    condition: str = None
    seller: dict = field(default_factory=dict)


def split_uid(uid):
    """'vinted:123' -> ('vinted', '123'). Be priesdelio laikom Vinted skelbimu."""
    source, sep, local = str(uid).partition(":")
    return (source, local) if sep else ("vinted", source)
