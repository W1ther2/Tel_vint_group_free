# -*- coding: utf-8 -*-
"""Apgavysciu / rizikos pozymiai. Nieko neatmeta – tik pazymi kortelėje."""

import re

from . import config
from .util import fold

# (regex, pavadinimas, taskai)
RISK_PATTERNS = [
    (r"whats ?app|viber|telegram|messenger|instagram|signal\b", "prašo rašyti ne per Vinted", 2),
    (r"rasyk\w* (i |man )?(tel|nr|numer|asmen|privat)|skambink\w*|susisiek\w* telefon\w*|contact me",
     "prašo susisiekti ne per Vinted", 2),
    (r"pavedim\w*|revolut|paysera|paypal|western union|ne per vinted|be vinted|uz vinted rib\w*|"
     r"outside vinted|not through vinted", "mokėjimas ne per Vinted", 2),
    (r"tik atsiemim\w*|atsiemim\w* tik|tik is rank\w*|tik gyvai|tik susitik\w*|nesiunci\w*|siuntimo nera|"
     r"pickup only|only pickup|no shipping|collection only", "tik atsiėmimas iš rankų", 1),
    (r"be doku\w*|be ceki\w*|rast\w* telefon\w*|nezinau slaptazodz\w*|pamirst\w* slaptazodz\w*",
     "gali būti vogtas / be dokumentų", 2),
    (r"skubiai|skubus|urgent|greitai parduod\w*|isvykst\w*|emigruoj\w*", "skubus pardavimas", 1),
    # „ne kopija“, „čekio kopija“ – ne kopijos pozymis
    (r"(?<!\bne )(?<!cekio )(?<!saskaitos )(?<!pirkimo )(?<!dokumentu )(?<!garantinio )"
     r"(?:kopij\w*|replik\w*|replica|clone|klonas)|\bcopy\b(?! of (?:the )?(?:receipt|invoice))|"
     r"ne originalus telefonas", "gali būti kopija", 2),
]
_RISK_RE = [(re.compile(r"\b(?:" + p + r")"), label, pts) for p, label, pts in RISK_PATTERNS]
_PHONE_RE = re.compile(r"(?:\+\s?370|\b8)[\s-]?\(?6\d{2}\)?[\s-]?\d{2}[\s-]?\d{3}\b|\+\d{2,3}[\s-]?\d{3}[\s-]?\d{3}[\s-]?\d{2,4}\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.\w{2,}")
PICKUP_LABEL = "tik atsiėmimas iš rankų"


def assess_risk(title, description, price, market, seller=None, photo_count=None):
    """Grazina (lygis, [priezastys]). Lygis: None / 'maža' / 'vidutinė' / 'didelė'."""
    c = config.cfg
    seller = seller or {}
    desc = description or ""
    t = fold(f"{title} {desc}".lower())
    reasons, score = [], 0

    def add(label, pts):
        nonlocal score
        if label not in reasons:
            reasons.append(label)
            score += pts

    for rx, label, pts in _RISK_RE:
        if rx.search(t):
            add(label, pts)
    if _PHONE_RE.search(desc) or _EMAIL_RE.search(desc):
        add("aprašyme telefono nr. / el. paštas", 2)
    if market and price < market * c["SUSPICIOUS_PRICE_RATIO"]:
        add(f"{1 - price / market:.0%} pigiau nei rinka – per gerai, kad būtų tiesa?", 2)

    # Pardavejo profilis
    age = seller.get("account_age_days")
    if age is not None and age < c["SELLER_NEW_ACCOUNT_DAYS"]:
        add(f"nauja paskyra ({age} d.)", 2)
    reviews, sold = seller.get("reviews"), seller.get("sold")
    if reviews == 0 and (sold in (None, 0)):
        add("pardavėjas dar nieko nepardavė" if sold == 0 else "pardavėjas be atsiliepimų", 1)
    rating = seller.get("rating")
    if rating is not None and reviews and rating < 4.0:
        add(f"žemas pardavėjo įvertinimas ({rating:.1f}/5)", 1)
    neg = seller.get("negative")
    if neg and reviews and reviews >= 3 and neg / reviews > 0.10:
        add(f"daug neigiamų atsiliepimų ({neg} iš {reviews})", 2)
    active = seller.get("active_items")
    if active is not None and active >= 50:
        add(f"daug aktyvių skelbimų ({active}) – perpardavėjas?", 1)

    if photo_count == 1:
        add("tik 1 nuotrauka", 1)
    if len(desc.strip()) < 25:
        add("beveik tuščias aprašymas", 1)

    if score == 0:
        return None, []
    level = "didelė" if score >= 4 else "vidutinė" if score >= 2 else "maža"
    return level, reasons
