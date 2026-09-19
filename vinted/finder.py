# -*- coding: utf-8 -*-
"""Pagrindine logika: paieska -> vertinimas -> Telegram."""

import hashlib
import html
import time
import traceback

from . import config, commands
from .client import VintedClient
from .language import detect_foreign_language
from .parsing import (parse_og_tags, get_price, get_photo_url, get_photo_count, get_condition,
                      seller_from_dict, seller_from_page, listing_status)
from .phone import (detect_model, is_accessory, find_defects, extract_storage, extract_battery,
                    CONDITION_FACTOR, estimate_value, estimate_profit, MODEL_ORDER, condition_ok,
                    description_not_phone, min_price)
from .risk import assess_risk, PICKUP_LABEL
from .state import State, load_seen, save_seen
from .telegram import Telegram

SELLER_KEYS = ("country", "rating", "reviews", "sold", "account_age_days")


class Run:
    def __init__(self, client, telegram, sleep=time.sleep):
        self.client = client
        self.tg = telegram
        self.sleep = sleep
        self.totals = {}
        self.catalog_ids = {}
        self.brand_ids = {}
        self.examples = []
        self.alerts = []

    @staticmethod
    def _count_id(item, kind, store):
        val = item.get(f"{kind}_id")
        if val is None:
            nested = item.get(kind)
            if isinstance(nested, dict):
                val = nested.get("id")
        if val is not None:
            store[val] = store.get(val, 0) + 1

    def reject(self, reason, example=None):
        self.totals[reason] = self.totals.get(reason, 0) + 1
        if example and len(self.examples) < 5:
            self.examples.append(f"{reason}: {example}")

    # --- pagalbines ---------------------------------------------------------
    def seller_info(self, item, page):
        user = item.get("user") or {}
        info = seller_from_dict(user)
        uid = user.get("id") or item.get("user_id")
        if uid and not all(k in info for k in SELLER_KEYS):
            info = {**seller_from_dict(self.client.fetch_user(uid)), **info}
        if not all(k in info for k in ("country", "rating", "reviews")):
            info = {**seller_from_page(page), **info}
        return info

    # --- vieno skelbimo vertinimas ---------------------------------------------
    def evaluate(self, item, drops):
        c = config.cfg
        iid = str(item["id"])
        title = item.get("title") or "?"
        price = get_price(item)
        drop_from = None

        if iid in self.new_seen:
            prev = drops.get(iid)
            if not (c["PRICE_DROP_ALERTS"] and prev and price is not None
                    and price <= prev * (1 - c["PRICE_DROP_MIN"])):
                return self.reject("jau matyti")
            if self.state.market.already_alerted_at(iid, price):
                return self.reject("jau matyti")
            drop_from = prev

        model = detect_model(title)
        if model:            # renkam ID tik tikriems telefonams – pagal juos nustatysim filtra
            self._count_id(item, "catalog", self.catalog_ids)
            self._count_id(item, "brand", self.brand_ids)
        if not model or is_accessory(title) or price is None:
            self.new_count += 0 if drop_from else 1
            self.new_seen[iid] = time.time()
            return self.reject("ne telefonas / kitas modelis")

        # 1) Greitas patikrinimas pagal rinkos kaina (be skelbimo puslapio)
        storage = extract_storage(title)
        quote = self.state.market.quote(model, storage)
        if quote is None:
            # nezymim kaip matyto – kai atsiras duomenu, ivertinsim
            return self.reject("per mazai kainu duomenu", f"iPhone {model} ({self.state.market.sample_count(model)} skelb.)")
        if not drop_from:
            self.new_count += 1
        cat_condition = get_condition(item)
        if c["TIDY_ONLY"] and not condition_ok(cat_condition, c["MIN_CONDITION"]):
            self.new_seen[iid] = time.time()
            return self.reject(f"būklė {cat_condition}")
        best_case = quote.price * CONDITION_FACTOR.get(cat_condition, 1.08) * 1.03
        if price > best_case * (1 - c["MIN_DISCOUNT"]):
            self.new_seen[iid] = time.time()
            return self.reject("per brangu")
        # Riba: 40% rinkos kainos arba modelio minimali kaina – kuri mazesne (kad
        # apytiksle kaina ar mano ivertinta minimali kaina neatmestu tikru pigiu telefonu)
        floor = min(quote.price * c["HARD_MIN_PRICE_RATIO"], min_price(model) or float("inf"))
        if price < floor:
            self.new_seen[iid] = time.time()
            return self.reject("per pigu (sugedęs / dalims / ne telefonas?)",
                               f"{title[:40]} {price:.0f}€ (riba {floor:.0f}€, rinka {quote.price:.0f}€)")
        self.new_seen[iid] = time.time()

        # 2) Skelbimo puslapis
        url_path = item.get("url") or item.get("path") or f"/items/{iid}"
        status, page, final_url = self.client.fetch_item_page(url_path)
        self.sleep(c["DETAIL_SLEEP_SECONDS"])
        if listing_status(status, page, final_url, iid) == "sold":
            self.state.market.set_status(iid, "sold")
            return self.reject("jau parduotas")
        og = parse_og_tags(page)
        description = og.get("description") or ""
        if description_not_phone(description) or detect_model(og.get("title") or title) is None:
            return self.reject("ne telefonas (pagal aprašymą)", f"{title[:40]} | {description[:60]}")

        if c["ONLY_LITHUANIAN_TEXT"]:
            lang = detect_foreign_language(og.get("title") or title, description)
            if lang and lang not in config.allowed_languages():
                return self.reject("kalba", f"{lang}: {title[:40]} | {description[:50]}")

        defects = find_defects(title, description)
        if any(f == 0 for _, f in defects):
            return self.reject("neveikiantis / užrakintas / netestuotas", f"{title[:40]} {[d for d, _ in defects]}")
        if c["TIDY_ONLY"]:
            allowed = set(c["ALLOWED_DEFECTS"])
            bad = [d for d, _ in defects if d not in allowed]
            if bad:
                return self.reject("defektai", f"{title[:40]} {bad}")
        condition = get_condition(item, page)
        if c["TIDY_ONLY"] and not condition_ok(condition, c["MIN_CONDITION"]):
            return self.reject(f"būklė {condition}")
        battery = extract_battery(title, description)
        if c["MIN_BATTERY"] and battery is not None and battery < c["MIN_BATTERY"]:
            return self.reject("baterija", f"{title[:40]} {battery}%")

        storage = storage or extract_storage(description)
        quote = self.state.market.quote(model, storage) or quote
        value = estimate_value(quote.price, condition, battery, defects)
        discount = 1 - price / value
        if discount < c["MIN_DISCOUNT"]:
            how = f"tik {discount:.0%} pigiau" if discount > 0 else f"{-discount:.0%} brangiau"
            return self.reject("ne pakankamai pigu", f"iPhone {model} {price:.0f}€, vertė {value:.0f}€ ({how})")

        # 3) Pardavejas
        seller = self.seller_info(item, page)
        country = seller.get("country")
        if c["FILTER_BY_COUNTRY"]:
            ok = (country in c["ALLOWED_COUNTRY_CODES"]) if country else (not c["REQUIRE_KNOWN_COUNTRY"])
            if not ok:
                return self.reject("salis", f"{country}: {title[:40]}")
        rating, reviews = seller.get("rating"), seller.get("reviews")
        if rating is not None and reviews is not None and (
                rating < c["MIN_SELLER_RATING"] or reviews < c["MIN_SELLER_REVIEWS"]):
            return self.reject("pardavejas", f"{rating}/5, {reviews} atsil.: {title[:40]}")

        seller_id = (item.get("user") or {}).get("id") or item.get("user_id") or ""
        fp = "fp:" + hashlib.sha1(f"{seller_id}|{model}|{storage}|{price:.0f}".encode()).hexdigest()[:16]
        if fp in self.new_seen:
            return self.reject("dublikatai")

        risk_level, risk_reasons = assess_risk(og.get("title") or title, description, price, quote.price,
                                               seller, get_photo_count(item))
        profit = estimate_profit(price, value, pickup_only=PICKUP_LABEL in risk_reasons)
        full_url = config.BASE + url_path if url_path.startswith("/") else url_path

        deal = {
            "id": iid, "model": model, "seller_id": str(seller_id), "storage": storage, "title": title, "price": price,
            "url": full_url, "photo": get_photo_url(item, og), "description": description,
            "condition": condition, "battery": battery, "defects": [d for d, _ in defects],
            "quote": quote, "value": value, "discount": discount, "profit": profit,
            "seller": seller, "risk_level": risk_level, "risk_reasons": risk_reasons,
            "drop_from": drop_from,
        }
        if c["PAUSED"]:
            return self.reject("pauzė (/testi – įjungti)")
        self.new_seen[fp] = time.time()
        return deal

    # --- visas paleidimas --------------------------------------------------------
    def send_personal(self, deal):
        """Asmenines zinutes tiems, kas paspaude 🔔 ties siuo modeliu."""
        for uid, u in self.state.users.items():
            chat = u.get("chat")
            if not chat or deal["model"] not in (u.get("watch") or []):
                continue
            if deal.get("seller_id") and deal["seller_id"] in (u.get("hide") or []):
                continue
            self.tg.send_deal(deal, chat_id=chat)
            print(f"     -> asmeniskai: {u.get('name') or uid}")

    def process_commands(self):
        if not config.cfg["TELEGRAM_COMMANDS"]:
            return
        messages, callbacks, offset = self.tg.get_updates(self.state.telegram_offset)
        for m in messages:
            if m["private"]:
                reply = commands.handle_private(m, self.state)
                print(f"Asmenine komanda ({m['name']}): {m['text'][:40]}")
                self.tg.send_message(reply, chat_id=m["chat"])
            else:
                reply = commands.handle(m["text"], self.state)
                if reply:
                    print(f"Komanda: {m['text'][:50]}")
                    self.tg.send_message(reply)
        for cb in callbacks:
            answer = commands.handle_callback(cb, self.state)
            print(f"Mygtukas ({cb['name']}): {cb['data']} -> {answer[:40]}")
            self.tg.answer_callback(cb["id"], answer)
        if offset != self.state.telegram_offset:
            self.state.telegram_offset = offset
            self.state.save()

    def check_sold(self):
        c = config.cfg
        found = {"sold": 0, "gone": 0}
        for iid in self.state.market.sold_check_candidates():
            status, page, final_url = self.client.fetch_item_page(f"/items/{iid}")
            st = listing_status(status, page, final_url, iid)
            self.state.market.set_status(iid, st)
            if st in found:
                found[st] += 1
            self.sleep(c["DETAIL_SLEEP_SECONDS"])
        if any(found.values()):
            print(f"Pardavimu patikra: parduota {found['sold']}, istrinta {found['gone']}")

    def run(self):
        c = config.cfg
        seen = load_seen()
        self.state = State.load(seen=seen)
        config.apply_overrides(self.state.overrides)
        self.process_commands()

        self.client.start()
        self.new_seen = dict(seen)
        self.new_seen.pop("__heartbeat__", None)
        fetched, self.new_count = 0, 0

        pages = c["PAGES"] if seen else max(c["PAGES"], c["FULL_SCAN_PAGES"])
        if not seen:
            print(f"seen.json tuscias – pilnas perziurejimas ({pages} psl. kiekvienai paieskai)")

        # Kad tie patys modeliai nebutu visada tikrinami paskutiniai (ir apie ju
        # dealus suzinotum veliausiai), kiekviena paleidima pradedam nuo kito modelio.
        queries = list(c["SEARCH_QUERIES"])
        if c["ROTATE_QUERIES"] and queries:
            start = self.state.query_offset % len(queries)
            queries = queries[start:] + queries[:start]
            self.state.query_offset = (start + max(1, len(queries) // 3)) % len(queries)
            print(f"Pradedama nuo: '{queries[0]}'")
        for q in queries:
            if self.client.blocked_queries >= c["STOP_AFTER_BLOCKED_QUERIES"]:
                print(f"! Vinted blokuoja uzklausas ({self.client.blocked_queries} paieskos is eiles) – "
                      "baigiu si paleidima, tesim kitame.")
                break
            print(f"Tikrinama: '{q}'...")
            items = self.client.fetch_items(q, pages, seen)
            fetched += len(items)
            drops = self.state.market.observe(items)
            self.examples, sent_before = [], len(self.alerts)
            for item in items:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                deal = self.evaluate(item, drops)
                if not deal:
                    continue
                self.alerts.append(deal)
                self.state.market.mark_alerted(deal["id"], deal["price"])
                silent = deal["discount"] < c["LOUD_DISCOUNT"]
                self.tg.send_deal(deal, silent=silent)
                self.send_personal(deal)
                tag = f"atpigo nuo {deal['drop_from']:.0f}, " if deal["drop_from"] else ""
                print(f"  -> iPhone {deal['model']} {deal['price']:.0f} EUR ({tag}-{deal['discount']:.0%}"
                      f"{', tyliai' if silent else ''}): {deal['title'][:45]}")
                save_seen(self.new_seen)
                self.state.save()
                self.sleep(1)
            print(f"  Gauta: {len(items)}, tinkama: {len(self.alerts) - sent_before}")
            for ex in self.examples:
                print(f"    atmesta – {ex}")
            self.sleep(c["SLEEP_SECONDS"])

        if c["USE_SOLD_PRICES"]:
            self.check_sold()

        self.print_market()
        self.print_ids()
        summary = ", ".join(f"{k}: {n}" for k, n in sorted(self.totals.items(), key=lambda kv: -kv[1]))
        print(f"IS VISO: gauta {fetched}, tinkama {len(self.alerts)}. Atmesta – {summary}")
        self.heartbeat(fetched, summary)
        self.state.last_run = {"time": int(time.time()), "fetched": fetched, "new": self.new_count,
                               "sent": len(self.alerts), "totals": self.totals}
        if fetched == 0:
            self.state.fail_streak += 1
            print(f"! Negauta skelbimu ({self.state.fail_streak} paleidimas is eiles): {self.client.last_error}")
            # Vienkartinis 403 = laikinas Vinted blokas serverio IP; pranesam tik jei kartojasi
            if self.state.fail_streak >= c["FAIL_ALERT_RUNS"]:
                self.tg.send_message(
                    f"<b>ISPEJIMAS</b>: {self.state.fail_streak} paleidimus is eiles negauta nei vieno "
                    "skelbimo is Vinted.\nGalimai pasikeite API arba Vinted blokuoja – patikrink logus.\n"
                    f"Priezastis: <code>{html.escape(self.client.last_error or 'nezinoma')}</code>")
                self.state.fail_streak = 0
        else:
            self.state.fail_streak = 0

        self.state.save()
        save_seen(self.new_seen)

        print(f"Issiusta {len(self.alerts)} alert'u." if self.alerts else "Nauju deal'u nera.")

    def print_ids(self):
        """Padeda uzpildyti CATALOG_IDS / BRAND_IDS config.json faile."""
        for name, store, key in (("kategorijos", self.catalog_ids, "CATALOG_IDS"),
                                 ("prekes zenklai", self.brand_ids, "BRAND_IDS")):
            top = sorted(store.items(), key=lambda kv: -kv[1])[:5]
            if top:
                pairs = ", ".join(f"{i} ({n} telef.)" for i, n in top)
                print(f"Daznos {name}: {pairs}   -> config.json \"{key}\": [{top[0][0]}]")

    def print_market(self):
        manual = config.market_prices()
        rows = {m: r for m, *r in self.state.market.summary()}
        print("Rinkos kainos (rankinė / parduotų mediana / prasomu kainu percentilis):")
        for model in MODEL_ORDER:
            if model not in rows and model not in manual:
                continue
            a, n, s, ns = rows.get(model, (None, 0, None, 0))
            print(f"  iPhone {model:<11} rankinė: {manual.get(model, 0):>4.0f}  "
                  f"parduoti: {s or 0:>4.0f} ({ns})  skelbimai: {a or 0:>4.0f} ({n})")

    def heartbeat(self, fetched, summary):
        hours = config.cfg["HEARTBEAT_HOURS"]
        if hours <= 0 or time.time() - self.state.heartbeat < hours * 3600:
            return
        self.tg.send_message("✅ <b>Vinted skriptas veikia</b>\n"
                             f"Šis paleidimas: gauta {fetched} skelb., naujų {self.new_count}, "
                             f"išsiųsta {len(self.alerts)}.\n"
                             f"<i>Atmesta – {html.escape(summary or 'nieko')}</i>", silent=True)
        self.state.heartbeat = time.time()


def main():
    config.load()
    if not config.BOT_TOKEN or not config.CHAT_ID:
        print("Nenurodyti BOT_TOKEN / CHAT_ID (GitHub Secrets)!")
        return
    tg = Telegram()
    try:
        Run(VintedClient(), tg).run()
    except Exception as e:
        print(traceback.format_exc())
        tg.send_message("<b>SKRIPTAS UZLUZO</b>\n" + html.escape(str(e)[:400]))
