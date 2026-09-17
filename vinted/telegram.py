# -*- coding: utf-8 -*-
"""Telegram: zinutes, skelbimu korteles, komandu gavimas."""

import html
import json
import re

import requests

from . import config
from .parsing import COUNTRY_LT


def _stars(score):
    full = int(round(score or 0))
    return "★" * full + "☆" * (5 - full)


def format_card(d):
    """Korteles tekstas (HTML, iki 1024 simboliu – nuotraukos aprasymo riba)."""
    name = f"iPhone {d['model']}" + (f" {d['storage']}" if d.get("storage") else "")
    icons = {"didelė": "🚩 ", "vidutinė": "⚠️ "}.get(d.get("risk_level"), "")
    if d.get("drop_from"):
        icons = "📉 " + icons
    lines = [f"{icons}<b>{html.escape(name)} | {d['price']:g} €</b>"]
    if d.get("drop_from"):
        lines.append(f"📉 <b>Atpigo:</b> {d['drop_from']:g} € → {d['price']:g} €")
    lines.append(f"💰 <b>{d['discount']:.0%} pigiau nei vertė</b> (~{d['value']:.0f} €)")
    if config.cfg["SHOW_PROFIT"] and d.get("profit") is not None:
        if d["profit"] > 0:
            lines.append(f"💵 <b>Galimas pelnas:</b> ~{d['profit']:.0f} € (perpardavus už ~{d['value']:.0f} €)")
        else:
            lines.append("💵 Perpardavus pelno greičiausiai nebūtų")

    desc = re.sub(r"\s+", " ", d.get("description") or "").strip()
    if len(desc) > 160:
        desc = desc[:160].rsplit(" ", 1)[0] + " ..."
    if desc:
        lines.append(html.escape(desc))
    lines.append("")

    if d.get("risk_level"):
        icon = {"didelė": "🚩", "vidutinė": "⚠️", "maža": "ℹ️"}[d["risk_level"]]
        lines.append(f"{icon} <b>Rizika: {d['risk_level']}</b> – {html.escape('; '.join(d['risk_reasons']))}")
    q = d["quote"]
    basis = d["storage"] if q.by_storage else "visos talpos"
    source = {"rankinė": "nustatyta ranka", "parduoti": f"{q.samples} parduotų",
              "skelbimai": f"{q.samples} skelb.", "apytikslė": "apytikslė – mažai duomenų"}[q.source]
    lines.append(f"📊 <b>Rinkos kaina:</b> {q.price:.0f} € ({html.escape(basis)}, {source})")
    if d.get("defects"):
        lines.append(f"⚠️ <b>Defektai:</b> {html.escape(', '.join(d['defects']))}")
    lines.append(f"📦 <b>Būklė:</b> {html.escape(d.get('condition') or 'nenurodyta')}")
    lines.append(f"🔋 <b>Baterija:</b> {str(d['battery']) + '%' if d.get('battery') else 'nenurodyta'}")
    s = d.get("seller") or {}
    if s.get("rating") is not None and s.get("reviews") is not None:
        extra = f", pardavė {s['sold']}" if s.get("sold") is not None else ""
        lines.append(f"⭐ <b>Pardavėjas:</b> {_stars(s['rating'])} ({s['rating']:.1f}/5, "
                     f"{s['reviews']} atsiliep.{extra})")
    if s.get("country"):
        place = COUNTRY_LT.get(s["country"], s["country"]) + (f", {s['city']}" if s.get("city") else "")
        lines.append(f"📍 <b>Vieta:</b> {html.escape(place)}")
    lines.append(f'🔗 <a href="{html.escape(d["url"])}">Atidaryti Vinted</a>')
    return "\n".join(lines)[:1024]


class Telegram:
    def __init__(self, token=None, chat_id=None, http=None):
        self.token = token if token is not None else config.BOT_TOKEN
        self.chat_id = str(chat_id if chat_id is not None else config.CHAT_ID)
        self.http = http or requests

    def _url(self, method):
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def send_message(self, text, silent=False):
        if config.cfg["DRY_RUN"]:
            print("[DRY_RUN] Telegram:", text.replace("\n", " | ")[:200])
            return True
        try:
            r = self.http.post(self._url("sendMessage"), data={
                "chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                "disable_web_page_preview": True, "disable_notification": silent}, timeout=15)
            if r.status_code != 200:
                print(f"  ! Telegram klaida: {r.text[:150]}")
                return False
            return True
        except Exception as e:
            print(f"  ! Nepavyko issiusti Telegram: {e}")
            return False

    def send_deal(self, deal, silent=False):
        """Kortele su nuotrauka ir mygtuku. Jei nuotrauka nesiuncia – tekstu."""
        caption = format_card(deal)
        if config.cfg["DRY_RUN"]:
            print("[DRY_RUN] kortele:", caption.replace("\n", " | ")[:300])
            return True
        keyboard = json.dumps({"inline_keyboard": [[{"text": "🛒 Atidaryti Vinted", "url": deal["url"]}]]})
        if deal.get("photo"):
            try:
                r = self.http.post(self._url("sendPhoto"), data={
                    "chat_id": self.chat_id, "photo": deal["photo"], "caption": caption,
                    "parse_mode": "HTML", "reply_markup": keyboard, "disable_notification": silent},
                    timeout=20)
                if r.status_code == 200:
                    return True
                print(f"  ! Telegram nuotraukos klaida: {r.text[:150]} – siunciu be nuotraukos")
            except requests.Timeout:
                print("  ! Telegram neatsake laiku – antra karta nesiunciu, kad nebutu dublikato")
                return True
            except Exception as e:
                print(f"  ! Nepavyko issiusti nuotraukos: {e} – siunciu be nuotraukos")
        return self.send_message(caption, silent=silent)

    def get_updates(self, offset):
        """Naujos zinutes is CHAT_ID pokalbio. Grazina ([(update_id, tekstas)], naujas_offset)."""
        try:
            r = self.http.get(self._url("getUpdates"), params={
                "offset": offset + 1 if offset else None, "timeout": 0,
                "allowed_updates": json.dumps(["message"])}, timeout=15)
            data = r.json()
        except Exception as e:
            print(f"  ! Nepavyko gauti Telegram komandu: {e}")
            return [], offset
        if not data.get("ok"):
            print(f"  ! Telegram getUpdates: {str(data.get('description'))[:150]}")
            return [], offset
        out, new_offset = [], offset
        for upd in data.get("result", []):
            new_offset = max(new_offset, int(upd.get("update_id", 0)))
            msg = upd.get("message") or {}
            text = msg.get("text") or ""
            if str((msg.get("chat") or {}).get("id")) == self.chat_id and text.startswith("/"):
                out.append((upd["update_id"], text))
        return out, new_offset
