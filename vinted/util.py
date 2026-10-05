# -*- coding: utf-8 -*-
import math
import re
import time
import unicodedata

from . import config


def fold(text):
    """Nuima diakritikus: 'būklė' -> 'bukle'."""
    return "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c))


def word_regex(words):
    parts = sorted((re.escape(w) for w in words), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b")


def debug(msg):
    if config.cfg.get("DEBUG"):
        print("  [DEBUG]", msg)


def today():
    return int(time.time() // 86400)


def human_age(timestamp, now=None):
    """Unix laikas -> 'prieš 4 min.' / 'prieš 3 val.' / 'prieš 2 d.' (None, jei nezinoma)."""
    if not timestamp:
        return None
    seconds = (now if now is not None else time.time()) - float(timestamp)
    if seconds < 0:
        return None
    minutes = int(seconds // 60)
    if minutes < 1:
        return "ką tik"
    if minutes < 60:
        return f"prieš {minutes} min."
    hours = minutes // 60
    if hours < 24:
        return f"prieš {hours} val."
    days = hours // 24
    if days < 31:
        return f"prieš {days} d."
    return f"prieš {days // 30} mėn."


def money(value):
    """Kaina log'o eilutei. Absurdiskai dideles sutrumpinamos.

    `f"{p:.0f}€"` su sugedusia reiksme duoda „99999999€" ir isstumia is eilutes
    tai, del ko ji apskritai rasoma – pavadinima, modeli, riba. Diagnostikai
    svarbu, KAD kaina absurdiska, o ne kiek tiksliai joje nuliu."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "?"
    if not math.isfinite(v):
        return "?"                      # NaN / begalybe – zr. parsing._to_float
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.1f} mln. €"
    if abs(v) >= 10_000:
        return f"{v / 1000:.0f} tūkst. €"
    return f"{v:.0f}€"


def median(values):
    v = sorted(values)
    n = len(v)
    if not n:
        return None
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def percentile(values, q):
    v = sorted(values)
    if not v:
        return None
    pos = (len(v) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)
