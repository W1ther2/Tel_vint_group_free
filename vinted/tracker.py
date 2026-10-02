# -*- coding: utf-8 -*-
"""Pranesimu rezultatai: ar skelbimas, apie kuri pranesem, buvo nupirktas ir per kiek laiko.

Tai irodymas, kad botas randa tikrai gerus pasiulymus: jei pigus telefonas dingsta
per valanda – jis buvo vertas demesio. Duomenys state.json rakte "tracked":

  {uid: {"t": pranesimo laikas, "m": modelis, "s": talpa, "p": kaina,
         "v": ivertinta verte, "pr": galimas pelnas, "src": saltinis,
         "lc": paskutinio patikrinimo laikas, "e": kada baigesi, "r": rezultatas}}

Rezultatai ("r"):
  "sold"     – parduotas (skelbimas uzdarytas kaip parduotas)
  "reserved" – rezervuotas (kazkas jau perka)
  "gone"     – istrintas (gal parduotas ne per platforma, gal pardavejas persigalvojo)
  "unsold"   – po TRACK_DAYS vis dar parduodamas
Kol "r" nera – dar sekama.
"""

import statistics
import time

from . import config
from .phone import display

TAKEN = ("sold", "reserved")
HOUR = 3600


class Tracker:
    def __init__(self, data=None):
        self.items = dict(data or {})

    def to_dict(self):
        return dict(self.items)          # kopija vienu zingsniu – saugiai issaugoma

    # --- irasymas -------------------------------------------------------------
    def add(self, deal, now=None):
        now = now if now is not None else time.time()
        uid = deal["id"]
        old = self.items.get(uid)
        if old and not old.get("r"):
            return                        # jau sekam (pvz. pakartotinis pranesimas atpigus)
        entry = {"t": int(now), "m": deal.get("model"), "p": deal.get("price"),
                 "src": deal.get("source") or uid.split(":")[0], "lc": int(now)}
        if deal.get("storage"):
            entry["s"] = deal["storage"]
        if deal.get("value"):
            entry["v"] = round(deal["value"])
        if deal.get("profit") is not None:
            entry["pr"] = round(deal["profit"])
        if deal.get("source") not in (None, "vinted") and deal.get("url"):
            entry["u"] = deal["url"]
        self.items[uid] = entry

    def due(self, now=None, limit=None):
        """Kuriuos skelbimus tikrinti siame paleidime.

        Pirmas 2 paras – kas paleidima (butent cia matosi, ar pasiulymas buvo karstas),
        veliau – kas kelias valandas, o po TRACK_DAYS nustojam."""
        c = config.cfg
        now = now if now is not None else time.time()
        limit = limit if limit is not None else c["TRACK_CHECKS_PER_RUN"]
        out = []
        for uid, e in self.items.items():
            if e.get("r"):
                continue
            age = now - e["t"]
            if age > c["TRACK_DAYS"] * 86400:
                continue                              # uzbaigs expire()
            # ka tik issiustas – dar netikrinam (tas pats paleidimas); pirmas paras – kas paleidima
            every = (c["TRACK_MIN_MINUTES"] * 60 if age < c["TRACK_FAST_HOURS"] * HOUR
                     else c["TRACK_SLOW_EVERY_HOURS"] * HOUR)
            if now - e.get("lc", 0) >= every:
                out.append((e.get("lc", 0), uid))
        out.sort()
        return [uid for _, uid in out[:limit]]

    def update(self, uid, status, now=None):
        """status is saltinio: 'sold' / 'reserved' / 'gone' / 'active' / 'unknown'."""
        now = now if now is not None else time.time()
        e = self.items.get(uid)
        if e is None or e.get("r"):
            return
        e["lc"] = int(now)
        if status in ("sold", "reserved", "gone"):
            e["r"], e["e"] = status, int(now)

    def expire(self, now=None):
        c = config.cfg
        now = now if now is not None else time.time()
        for e in self.items.values():
            if not e.get("r") and now - e["t"] > c["TRACK_DAYS"] * 86400:
                e["r"], e["e"] = "unsold", int(now)
        keep = c["TRACK_KEEP_DAYS"] * 86400
        self.items = {k: e for k, e in self.items.items() if now - e["t"] <= keep}

    # --- ataskaita ------------------------------------------------------------
    def stats(self, days=7, now=None):
        now = now if now is not None else time.time()
        rows = [e for e in self.items.values() if now - e["t"] <= days * 86400]
        taken = [e for e in rows if e.get("r") in TAKEN]
        hours = sorted((e["e"] - e["t"]) / HOUR for e in taken)
        by_source = {}
        for e in rows:
            s = by_source.setdefault(e.get("src", "?"), [0, 0])
            s[0] += 1
            s[1] += e.get("r") in TAKEN
        return {
            "days": days,
            "sent": len(rows),
            "taken": len(taken),
            "sold": sum(1 for e in taken if e["r"] == "sold"),
            "reserved": sum(1 for e in taken if e["r"] == "reserved"),
            "gone": sum(1 for e in rows if e.get("r") == "gone"),
            "unsold": sum(1 for e in rows if e.get("r") == "unsold"),
            "open": sum(1 for e in rows if not e.get("r")),
            "within_1h": sum(1 for h in hours if h <= 1),
            "within_6h": sum(1 for h in hours if h <= 6),
            "within_24h": sum(1 for h in hours if h <= 24),
            "median_hours": statistics.median(hours) if hours else None,
            "profit_taken": sum(max(0, e.get("pr") or 0) for e in taken),
            "by_source": by_source,
            "fastest": sorted(taken, key=lambda e: e["e"] - e["t"])[:3],
        }


def _duration(hours):
    if hours < 1:
        return f"{max(1, round(hours * 60))} min."
    if hours < 48:
        if hours >= 10:
            return f"{hours:.0f} val."
        return f"{hours:.1f}".replace(".0", "").replace(".", ",") + " val."
    return f"{hours / 24:.0f} d."


def _share(part, whole):
    return f"{part / whole:.0%}" if whole else "–"


def report_text(tracker, days=7, now=None, labels=None):
    """Telegram ataskaita (HTML)."""
    s = tracker.stats(days, now)
    labels = labels or {}
    head = f"📈 <b>Rezultatai per {days} d.</b>"
    if not s["sent"]:
        return f"{head}\nPer šį laiką pranešimų nebuvo."
    lines = [head, f"Pranešta: <b>{s['sent']}</b>"]
    finished = s["sent"] - s["open"]
    lines.append(f"Nupirkta: <b>{s['taken']}</b> ({_share(s['taken'], s['sent'])})"
                 + (f" – iš jų rezervuota {s['reserved']}" if s["reserved"] else ""))
    if s["taken"]:
        lines.append(f"  ⚡ per 1 val.: {s['within_1h']} · per 6 val.: {s['within_6h']} · "
                     f"per parą: {s['within_24h']}")
        lines.append(f"  ⏱ vidutiniškai (mediana): {_duration(s['median_hours'])}")
        if s["profit_taken"]:
            lines.append(f"  💵 galimas pelnas iš nupirktų: ~{s['profit_taken']:.0f} €")
    other = []
    if s["gone"]:
        other.append(f"ištrinta {s['gone']}")
    if s["unsold"]:
        other.append(f"neparduota per {config.cfg['TRACK_DAYS']} d. {s['unsold']}")
    if s["open"]:
        other.append(f"dar parduodama {s['open']}")
    if other:
        lines.append("Kita: " + ", ".join(other))
    if len(s["by_source"]) > 1:
        parts = [f"{labels.get(k, k)} {v[1]}/{v[0]}" for k, v in sorted(s["by_source"].items())]
        lines.append("Pagal šaltinį (nupirkta/pranešta): " + ", ".join(parts))
    if s["fastest"]:
        lines.append("")
        lines.append("<b>Greičiausiai nupirkti:</b>")
        for e in s["fastest"]:
            name = display(e.get("m")) + (f" {e['s']}" if e.get("s") else "")
            lines.append(f"• {name} už {e.get('p', 0):.0f} € – per {_duration((e['e'] - e['t']) / HOUR)}")
    if finished < s["sent"] / 2:
        lines.append("\n<i>Dalis skelbimų dar sekama – skaičiai tikslės.</i>")
    return "\n".join(lines)
