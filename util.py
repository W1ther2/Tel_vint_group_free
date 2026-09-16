# -*- coding: utf-8 -*-
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
