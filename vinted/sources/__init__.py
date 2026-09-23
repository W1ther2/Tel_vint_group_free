# -*- coding: utf-8 -*-
"""Saltiniu registras. Ijungiami config.json rakte "SOURCES"."""

from .. import config
from .base import Source
from .pirkpard import PirkpardSource
from .skelbiu import SkelbiuSource
from .vinted_source import VintedSource

REGISTRY = {cls.name: cls for cls in (VintedSource, SkelbiuSource, PirkpardSource)}


def build_sources(sleep=None):
    """Sukuria ijungtus saltinius ta tvarka, kokia nurodyta config.json."""
    made, unknown = [], []
    for name in config.cfg["SOURCES"]:
        cls = REGISTRY.get(str(name).lower())
        if cls is None:
            unknown.append(str(name))
            continue
        made.append(cls(sleep=sleep) if sleep else cls())
    if unknown:
        print(f"! Nezinomi saltiniai ignoruojami: {', '.join(unknown)} "
              f"(galimi: {', '.join(sorted(REGISTRY))})")
    if not made:
        print("! Nei vienas saltinis neijungtas – naudojamas Vinted.")
        made = [VintedSource(sleep=sleep) if sleep else VintedSource()]
    return made


def label(name):
    cls = REGISTRY.get(str(name).lower())
    return cls.label if cls else str(name).capitalize()


__all__ = ["Source", "VintedSource", "SkelbiuSource", "PirkpardSource",
           "REGISTRY", "build_sources", "label"]
