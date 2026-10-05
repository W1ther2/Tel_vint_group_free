# -*- coding: utf-8 -*-
"""Telegram: zinutes, skelbimu korteles, komandu gavimas."""

import html
import json
import re
import time

import requests

from . import config
from .parsing import COUNTRY_LT
from .phone import display


def _retry_after(response, default=5.0):
    """Kiek sekundziu laukti pagal Telegram 429 atsakyma (parameters.retry_after)."""
    try:
        return min(60.0, float(response.json()["parameters"]["retry_after"]) + 0.5)
    except Exception:
        return default


def plain_text(text):
    """HTML -> paprastas tekstas (kai Telegram HTML neperskaito)."""
    return html.unescape(re.sub(r"<[^>]+>", "", text or ""))


def _stars(score):
    full = int(round(score or 0))
    return "★" * full + "☆" * (5 - full)


CAPTION_LIMIT = 1024          # Telegram nuotraukos aprasymo riba
GROUP_MIN_INTERVAL = 3.1      # grupeje Telegram leidzia ne daugiau 20 zinuciu per minute


def _short(text, limit):
    if len(text) <= limit:
        return text
    cut = text[:max(0, limit)].rsplit(" ", 1)[0]
    return (cut + " ...") if cut else ""


def _card(d, desc, reasons, hidden_reasons, dropped):
    """Viena korteles versija. `dropped` – nebutinos eilutes, kuriu siai versijai nededam."""
    name = display(d["model"]) + (f" {d['storage']}" if d.get("storage") else "")
    icons = {"didelė": "🚩 ", "vidutinė": "⚠️ "}.get(d.get("risk_level"), "")
    if d.get("drop_from"):
        icons = "📉 " + icons
    lines = [f"{icons}<b>{html.escape(name)} | {d['price']:g} €</b>"]
    if d.get("drop_from"):
        lines.append(f"📉 <b>Atpigo:</b> {d['drop_from']:g} € → {d['price']:g} €")
    r = d.get("rank")
    if r is not None and config.cfg.get("SHOW_RANK") and "rank" not in dropped:
        # Pagrindinis argumentas – vieta tarp dabar parduodamu. Ji nepriklauso nuo to,
        # ar musu rinkos kainos spejimas teisingas.
        vieta = "Pigiausias" if r.place == 1 else f"{r.place}-as pigiausias"
        kas = (f"tokių pat ({html.escape(d['storage'])})" if r.by_storage and d.get("storage")
               else html.escape(display(d["model"])))
        lines.append(f"🏷 <b>{vieta} iš {r.n}</b> dabar parduodamų {kas} "
                     f"({r.low:.0f}–{r.high:.0f} €)")
    if r is not None:
        if d["discount"] > 0:
            lines.append(f"💰 ~{d['discount']:.0%} pigiau nei vertinta (~{d['value']:.0f} €)")
    else:
        lines.append(f"💰 <b>{d['discount']:.0%} pigiau nei vertė</b> (~{d['value']:.0f} €)")
    sus = d.get("suspicious")
    if sus and "suspicious" not in dropped:
        lines.append(f"⚠️ <b>Įtartinai pigu:</b> {1 - sus['ratio']:.0%} pigiau už kitą pigiausią "
                     f"({sus['peer_low']:.0f} €) – patikrink, ar veikia ir ar ne užrakintas")
    if config.cfg["SHOW_PROFIT"] and d.get("profit") is not None:
        if d["profit"] > 0:
            lines.append(f"💵 <b>Galimas pelnas:</b> ~{d['profit']:.0f} € (perpardavus už ~{d['value']:.0f} €)")
        else:
            lines.append("💵 Perpardavus pelno greičiausiai nebūtų")
    if desc:
        lines.append(html.escape(desc))
    lines.append("")

    if d.get("risk_level"):
        icon = {"didelė": "🚩", "vidutinė": "⚠️", "maža": "ℹ️"}[d["risk_level"]]
        more = f" (+{hidden_reasons} kt.)" if hidden_reasons else ""
        lines.append(f"{icon} <b>Rizika: {d['risk_level']}</b> – {html.escape('; '.join(reasons))}{more}")
    q = d["quote"]
    basis = d["storage"] if q.by_storage else "visos talpos"
    source = {"rankinė": "nustatyta ranka", "parduoti": f"{q.samples} parduotų",
              "skelbimai": f"{q.samples} skelb.", "apytikslė": "apytikslė – mažai duomenų"}[q.source]
    conf = d.get("confidence")
    if conf is not None:
        # Kiek galima pasitiketi siuo skaiciumi (vinted/confidence.py): 80 % atveju rinkos
        # kaina klysta ne daugiau nei ±band. Zemas – verte ir pelnas tik orientaciniai.
        icon = {"h": "🟢", "m": "🟡", "l": "🔴"}[conf.code]
        lines.append(f"📊 <b>Rinkos kaina:</b> {q.price:.0f} € ±{conf.band:.0%} "
                     f"({html.escape(basis or '')}, {source})")
        lines.append(f"{icon} <b>Patikimumas:</b> {conf.level}"
                     + ("" if conf.code == "h" else f" – {html.escape(conf.reason)}"))
    else:
        lines.append(f"📊 <b>Rinkos kaina:</b> {q.price:.0f} € ({html.escape(basis or '')}, {source})")
    if d.get("defects") and "defects" not in dropped:
        lines.append(f"⚠️ <b>Defektai:</b> {html.escape(', '.join(d['defects']))}")
    lines.append(f"📦 <b>Būklė:</b> {html.escape(d.get('condition') or 'nenurodyta')}")
    if d.get("battery"):
        low = " ⚠️ žema, bet kaina gera" if d.get("battery_low") else ""
        lines.append(f"🔋 <b>Baterija:</b> {d['battery']}%{low}")
    else:
        lines.append("🔋 <b>Baterija:</b> nenurodyta")
    s = d.get("seller") or {}
    if s.get("rating") is not None and s.get("reviews") is not None and "seller" not in dropped:
        extra = f", pardavė {s['sold']}" if s.get("sold") is not None else ""
        lines.append(f"⭐ <b>Pardavėjas:</b> {_stars(s['rating'])} ({s['rating']:.1f}/5, "
                     f"{s['reviews']} atsiliep.{extra})")
    if s.get("country") and "place" not in dropped:
        place = COUNTRY_LT.get(s["country"], s["country"]) + (f", {s['city']}" if s.get("city") else "")
        lines.append(f"📍 <b>Vieta:</b> {html.escape(place)}")
    where = d.get("source_label") or "Vinted"
    if "age" not in dropped:
        if d.get("age"):
            extra = f" · {where}" if len(config.cfg["SOURCES"]) > 1 else ""
            lines.append(f"⏱ <b>Įkelta:</b> {d['age']}{html.escape(extra)}")
        elif len(config.cfg["SOURCES"]) > 1:
            lines.append(f"🛍 <b>Šaltinis:</b> {html.escape(where)}")
    if "link" not in dropped:
        lines.append(f'🔗 <a href="{html.escape(d["url"])}">Atidaryti {html.escape(where)}</a>')
    return "\n".join(lines)


def format_card(d, limit=CAPTION_LIMIT):
    """Korteles tekstas (HTML), telpantis i `limit` simboliu.

    Anksciau baigtas HTML buvo tiesiog nukerpamas ties 1024 simboliu – ilgoje kortelėje
    pjuvis pakliudavo i <a href=...> vidury, Telegram atmesdavo ir nuotrauka, ir teksta,
    o dealas dingdavo. Dabar trumpinama taip: aprasymas -> rizikos priezasciu sarasas ->
    nebutinos eilutes (vieta, ikelimo laikas, pardavejas, nuoroda – ji yra ir mygtuke)."""
    desc = re.sub(r"\s+", " ", d.get("description") or "").strip()
    reasons = list(d.get("risk_reasons") or [])
    dropped = set()

    def build(desc_len, n_reasons):
        return _card(d, _short(desc, desc_len), reasons[:n_reasons], len(reasons) - n_reasons, dropped)

    card = build(160, len(reasons))
    if len(card) <= limit:
        return card
    desc_len = min(160, limit - len(build(0, len(reasons))) - 6)
    while desc_len >= 30:
        card = build(desc_len, len(reasons))
        if len(card) <= limit:
            return card
        desc_len -= 10
    n = len(reasons)
    while n > 1 and len(build(0, n)) > limit:
        n -= 1
    for key in ("place", "age", "seller", "rank", "suspicious", "defects", "link"):
        card = build(0, n)
        if len(card) <= limit:
            return card
        dropped.add(key)
    card = build(0, n)
    return card if len(card) <= limit else re.sub(r"<[^>]+>", "", card)[:limit]


class Telegram:
    def __init__(self, token=None, chat_id=None, http=None, sleep=time.sleep, clock=time.monotonic,
                 topic_id=None):
        self.token = token if token is not None else config.BOT_TOKEN
        self.chat_id = str(chat_id if chat_id is not None else config.CHAT_ID)
        # Grupes skiltis (forum topic). Zinutes i ja siunciamos su message_thread_id.
        self.topic_id = str(topic_id if topic_id is not None else config.CHAT_TOPIC_ID or "").strip()
        self.http = http or requests
        self.sleep = sleep
        self.clock = clock
        self._last_sent = {}

    def send_admin(self, text, silent=True):
        """Tarnybine zinute (klaidos, saltiniu busena, heartbeat).

        Eina i ADMIN_CHAT_ID, jei jis nurodytas – viesoje grupeje „SKRIPTAS UZLUZO“ ar
        „config.json sugadintas“ tik gasdina skaitytojus. Nenurodzius – kaip anksciau."""
        return self.send_message(text, silent=silent, chat_id=config.ADMIN_CHAT_ID or None)

    def topic_for(self, brand):
        """Kurios skilties ID atitinka gamintoja.

        v44 Android buvo atskiras PALEIDIMAS: savas botas, sava busena, savas
        workflow – nes `CHAT_TOPIC_ID` buvo vienas visam procesui. Nuo v49.2
        skiltis parenkama pagal gamintoja, tad uztenka vieno boto ir vienos
        rinkos istorijos (tai net geriau: Apple ir Android kalibracija bendra).

        `TOPIC_BY_BRAND` – {"apple": "12", "samsung": "34"}; ko nera, tas eina
        i `CHAT_TOPIC_ID`, o jo nenurodzius – i bendra srauta."""
        if brand:
            mapped = (config.cfg.get("TOPIC_BY_BRAND") or {}).get(str(brand).lower())
            if mapped:
                return str(mapped).strip()
        return self.topic_id

    def _thread(self, chat_id, brand=None):
        """message_thread_id – tik pagrindiniam pokalbiui. Asmeninėse zinutese skilciu
        nera, ir su svetimu thread ID Telegram atsakytu klaida."""
        topic = self.topic_for(brand)
        if topic and str(chat_id) == self.chat_id:
            return {"message_thread_id": topic}
        return {}

    def safe(self, text):
        """Pasleps bot'o token'a. `requests` klaidos tekste yra VISAS adresas, o jame –
        token'as: „...Max retries exceeded with url: /bot123:ABC/sendMessage“. Toks
        tekstas keliaudavo tiesiai i log'a, o log'ai (ypac savo kompiuteryje ar
        viesame repozitoriume) matomi. Token'as leidzia raso bot'o vardu, tad
        pakeiciam ji zvaigdutemis."""
        text = str(text)
        if self.token:
            text = text.replace(self.token, "***")
            head = self.token.split(":")[0]       # „123456789“ – irgi nerodom
            if head and len(head) > 4:
                text = text.replace(head, "***")
        return text

    def _throttle(self, chat_id):
        """Grupeje – ne dazniau nei kas GROUP_MIN_INTERVAL s (Telegram riba: 20 per minute).
        Grupiu ID neigiami; asmeniniams pokalbiams sios ribos nereikia."""
        chat = str(chat_id)
        if not chat.startswith("-"):
            return
        last = self._last_sent.get(chat)
        if last is not None:
            wait = GROUP_MIN_INTERVAL - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last_sent[chat] = self.clock()

    def _post(self, method, data, timeout):
        """POST; jei Telegram sako 429 (per daug zinuciu) – palaukiam, kiek praso, ir kartojam."""
        r = None
        for _ in range(3):
            r = self.http.post(self._url(method), data=data, timeout=timeout)
            if getattr(r, "status_code", 200) != 429:
                return r
            wait = _retry_after(r)
            print(f"  ! Telegram 429 (per daug zinuciu) – laukiu {wait:.0f}s")
            self.sleep(wait)
        return r

    def _url(self, method):
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def send_message(self, text, silent=False, chat_id=None, brand=None):
        if config.cfg["DRY_RUN"]:
            print("[DRY_RUN] Telegram:", text.replace("\n", " | ")[:200])
            return True
        chat = chat_id or self.chat_id
        try:
            self._throttle(chat)
            data = {**self._thread(chat, brand),
                    "chat_id": chat, "text": text[:4096], "parse_mode": "HTML",
                    "disable_web_page_preview": True, "disable_notification": silent}
            r = self._post("sendMessage", data, 15)
            if r.status_code == 400 and "parse" in (r.text or "").lower():
                # Sugadintas HTML – siunciam paprastu tekstu, kad zinute nedingtu
                print(f"  ! Telegram neperskaite HTML ({self.safe(r.text[:100])}) – siunciu paprastu tekstu")
                data = {k: v for k, v in data.items() if k != "parse_mode"}
                data["text"] = plain_text(text)[:4096]
                r = self._post("sendMessage", data, 15)
            if r.status_code != 200:
                print(f"  ! Telegram klaida: {self.safe(r.text[:150])}")
                return False
            return True
        except Exception as e:
            print(f"  ! Nepavyko issiusti Telegram: {self.safe(e)}")
            return False

    @staticmethod
    def deal_keyboard(deal):
        """Mygtukai po kortele. Antros eiles mygtukai veikia kiekvienam vartotojui
        atskirai (Telegram pasako, kas paspaude, o botas atsako tik jam)."""
        rows = [[{"text": f"🛒 Atidaryti {deal.get('source_label') or 'Vinted'}", "url": deal["url"]}],
                [{"text": "🔔 Sekti šį modelį", "callback_data": f"w|{deal['model']}"[:64]}]]
        return json.dumps({"inline_keyboard": rows})

    def send_deal(self, deal, silent=False, chat_id=None):
        """Kortele su nuotrauka ir mygtukais. Jei nuotrauka nesiuncia – tekstu.

        Skiltis parenkama pagal gamintoja (`TOPIC_BY_BRAND`), tad Apple ir Android
        gali kristi i skirtingas grupes skiltis is to paties paleidimo."""
        caption = format_card(deal)
        brand = deal.get("brand")
        if config.cfg["DRY_RUN"]:
            print("[DRY_RUN] kortele:", caption.replace("\n", " | ")[:300])
            return True
        keyboard = self.deal_keyboard(deal)
        if deal.get("photo"):
            try:
                self._throttle(chat_id or self.chat_id)
                r = self._post("sendPhoto", {
                    **self._thread(chat_id or self.chat_id, brand),
                    "chat_id": chat_id or self.chat_id, "photo": deal["photo"], "caption": caption,
                    "parse_mode": "HTML", "reply_markup": keyboard, "disable_notification": silent}, 20)
                if r.status_code == 200:
                    return True
                print(f"  ! Telegram nuotraukos klaida: {self.safe(r.text[:150])} – siunciu be nuotraukos")
            except requests.Timeout:
                print("  ! Telegram neatsake laiku – antra karta nesiunciu, kad nebutu dublikato")
                return True
            except Exception as e:
                print(f"  ! Nepavyko issiusti nuotraukos: {self.safe(e)} – siunciu be nuotraukos")
        return self.send_message(caption, silent=silent, chat_id=chat_id, brand=brand)

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
            print(f"  ! Nepavyko gauti Telegram komandu: {self.safe(e)}")
            return [], [], offset
        if not isinstance(data, dict) or not data.get("ok"):
            why = data.get("description") if isinstance(data, dict) else f"netiketas atsakymas: {type(data).__name__}"
            print(f"  ! Telegram getUpdates: {self.safe(str(why)[:150])}")
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
                # Kai dirbam konkrecioje skiltyje, kitu skilciu komandos ne mums:
                # tame paciame pokalbyje gali suktis antras botas (pvz. Android skiltyje).
                in_topic = (not self.topic_id
                            or str(msg.get("message_thread_id") or "") == self.topic_id)
                if private or (str(chat.get("id")) == self.chat_id and in_topic):
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
            print(f"  ! Nepavyko atsakyti i paspaudima: {self.safe(e)}")
            return False
