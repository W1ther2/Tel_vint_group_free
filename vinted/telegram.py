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
    r = d.get("rank")
    if r is not None and config.cfg.get("SHOW_RANK"):
        # Pagrindinis argumentas – vieta tarp dabar parduodamu. Ji nepriklauso nuo to,
        # ar musu rinkos kainos spejimas teisingas.
        vieta = "Pigiausias" if r.place == 1 else f"{r.place}-as pigiausias"
        kas = (f"tokių pat ({html.escape(d['storage'])})" if r.by_storage and d.get("storage")
               else f"iPhone {html.escape(d['model'])}")
        lines.append(f"🏷 <b>{vieta} iš {r.n}</b> dabar parduodamų {kas} "
                     f"({r.low:.0f}–{r.high:.0f} €)")
    if r is not None:
        if d["discount"] > 0:
            lines.append(f"💰 ~{d['discount']:.0%} pigiau nei vertinta (~{d['value']:.0f} €)")
    else:
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
    if d.get("battery"):
        low = " ⚠️ žema, bet kaina gera" if d.get("battery_low") else ""
        lines.append(f"🔋 <b>Baterija:</b> {d['battery']}%{low}")
    else:
        lines.append("🔋 <b>Baterija:</b> nenurodyta")
    s = d.get("seller") or {}
    if s.get("rating") is not None and s.get("reviews") is not None:
        extra = f", pardavė {s['sold']}" if s.get("sold") is not None else ""
        lines.append(f"⭐ <b>Pardavėjas:</b> {_stars(s['rating'])} ({s['rating']:.1f}/5, "
                     f"{s['reviews']} atsiliep.{extra})")
    if s.get("country"):
        place = COUNTRY_LT.get(s["country"], s["country"]) + (f", {s['city']}" if s.get("city") else "")
        lines.append(f"📍 <b>Vieta:</b> {html.escape(place)}")
    where = d.get("source_label") or "Vinted"
    if d.get("age"):
        extra = f" · {where}" if len(config.cfg["SOURCES"]) > 1 else ""
        lines.append(f"⏱ <b>Įkelta:</b> {d['age']}{html.escape(extra)}")
    elif len(config.cfg["SOURCES"]) > 1:
        lines.append(f"🛍 <b>Šaltinis:</b> {html.escape(where)}")
    lines.append(f'🔗 <a href="{html.escape(d["url"])}">Atidaryti {html.escape(where)}</a>')
    return "\n".join(lines)[:1024]


class Telegram:
    def __init__(self, token=None, chat_id=None, http=None):
        self.token = token if token is not None else config.BOT_TOKEN
        self.chat_id = str(chat_id if chat_id is not None else config.CHAT_ID)
        self.http = http or requests

    def _url(self, method):
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def send_message(self, text, silent=False, chat_id=None):
        if config.cfg["DRY_RUN"]:
            print("[DRY_RUN] Telegram:", text.replace("\n", " | ")[:200])
            return True
        try:
            r = self.http.post(self._url("sendMessage"), data={
                "chat_id": chat_id or self.chat_id, "text": text, "parse_mode": "HTML",
                "disable_web_page_preview": True, "disable_notification": silent}, timeout=15)
            if r.status_code != 200:
                print(f"  ! Telegram klaida: {r.text[:150]}")
                return False
            return True
        except Exception as e:
            print(f"  ! Nepavyko issiusti Telegram: {e}")
            return False

    @staticmethod
    def deal_keyboard(deal):
        """Mygtukai po kortele. Antros eiles mygtukai veikia kiekvienam vartotojui
        atskirai (Telegram pasako, kas paspaude, o botas atsako tik jam)."""
        rows = [[{"text": f"🛒 Atidaryti {deal.get('source_label') or 'Vinted'}", "url": deal["url"]}],
                [{"text": "🔔 Sekti šį modelį", "callback_data": f"w|{deal['model']}"[:64]}]]
        return json.dumps({"inline_keyboard": rows})

    def send_deal(self, deal, silent=False, chat_id=None):
        """Kortele su nuotrauka ir mygtukais. Jei nuotrauka nesiuncia – tekstu."""
        caption = format_card(deal)
        if config.cfg["DRY_RUN"]:
            print("[DRY_RUN] kortele:", caption.replace("\n", " | ")[:300])
            return True
        keyboard = self.deal_keyboard(deal)
        if deal.get("photo"):
            try:
                r = self.http.post(self._url("sendPhoto"), data={
                    "chat_id": chat_id or self.chat_id, "photo": deal["photo"], "caption": caption,
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
        return self.send_message(caption, silent=silent, chat_id=chat_id)

    def get_updates(self, offset):
        """Naujos komandos ir mygtuku paspaudimai.

        Grazina (zinutes, paspaudimai, naujas_offset):
          zinutes    – [{"text", "chat", "user", "name", "private"}]
          paspaudimai– [{"data", "user", "name", "id"}]  (id = callback_query id)"""
        try:
            r = self.http.get(self._url("getUpdates"), params={
                "offset": offset + 1 if offset else None, "timeout": 0,
                "allowed_updates": json.dumps(["message", "callback_query"])}, timeout=15)
            data = r.json()
        except Exception as e:
            print(f"  ! Nepavyko gauti Telegram komandu: {e}")
            return [], [], offset
        if not data.get("ok"):
            print(f"  ! Telegram getUpdates: {str(data.get('description'))[:150]}")
            return [], [], offset

        messages, callbacks, new_offset = [], [], offset
        for upd in data.get("result", []):
            new_offset = max(new_offset, int(upd.get("update_id", 0)))
            msg = upd.get("message")
            cq = upd.get("callback_query")
            if msg and (msg.get("text") or "").startswith("/"):
                chat = msg.get("chat") or {}
                user = msg.get("from") or {}
                private = chat.get("type") == "private"
                if private or str(chat.get("id")) == self.chat_id:
                    messages.append({"text": msg["text"], "chat": str(chat.get("id")),
                                     "user": str(user.get("id") or ""), "name": user.get("first_name") or "",
                                     "private": private})
            elif cq:
                user = cq.get("from") or {}
                callbacks.append({"data": cq.get("data") or "", "user": str(user.get("id") or ""),
                                  "name": user.get("first_name") or "", "id": cq.get("id")})
        return messages, callbacks, new_offset

    def answer_callback(self, callback_id, text, alert=False):
        """Atsakymas mato TIK paspaudes vartotojas."""
        if config.cfg["DRY_RUN"]:
            print("[DRY_RUN] atsakymas:", text[:120])
            return True
        try:
            self.http.post(self._url("answerCallbackQuery"), data={
                "callback_query_id": callback_id, "text": text[:200], "show_alert": alert}, timeout=15)
            return True
        except Exception as e:
            print(f"  ! Nepavyko atsakyti i paspaudima: {e}")
            return False
