# -*- coding: utf-8 -*-
"""Pagrindine logika: saltiniai -> vertinimas -> Telegram."""

import concurrent.futures
import hashlib
import html
import threading
import time
import traceback

from . import config, commands
from .language import detect_foreign_language
from .listing import split_uid
from .phone import (detect_model, is_accessory, find_defects, extract_storage, extract_battery,
                    CONDITION_FACTOR, estimate_value, estimate_profit, MODEL_ORDER, condition_ok,
                    description_not_phone, min_price)
from .risk import assess_risk, PICKUP_LABEL
from .sources import build_sources
from .sources.vinted_source import VintedSource
from .state import State, load_seen, save_seen
from .telegram import Telegram
from .util import human_age


class Run:
    def __init__(self, sources, telegram, sleep=time.sleep):
        # Suderinamumas: perdavus viena klienta (senas budas / testai) apvyniojam Vinted saltiniu.
        if not isinstance(sources, (list, tuple)):
            sources = [VintedSource(client=sources)]
        self.started = time.time()
        self.sources = list(sources)
        self.tg = telegram
        self.sleep = sleep
        self.totals = {}
        self.alerts = []
        self.last_error = ""
        self.deadline = None        # kada baigti si saltini (kad kitiems liktu laiko)
        # Saltiniai gali suktis lygiagreciai, todel bendri duomenys (rinkos istorija,
        # matytu sarasas, Telegram) liecami tik su sia spyna.
        self.lock = threading.RLock()
        self._local = threading.local()

    # Atmetimo pavyzdziai renkami atskirai kiekvienam saltiniui – kitaip lygiagreciai
    # dirbantys saltiniai maisytu vienas kito eilutes.
    @property
    def examples(self):
        if not hasattr(self._local, "examples"):
            self._local.examples = []
        return self._local.examples

    @examples.setter
    def examples(self, value):
        self._local.examples = value

    def reject(self, reason, example=None):
        with self.lock:
            self.totals[reason] = self.totals.get(reason, 0) + 1
        if example and len(self.examples) < 5:
            self.examples.append(f"{reason}: {example}")

    # --- vieno skelbimo vertinimas ---------------------------------------------
    def evaluate(self, source, listing, drops):
        c = config.cfg
        uid = listing.uid
        title = listing.title or "?"
        price = listing.price
        drop_from = None

        if uid in self.new_seen:
            # Skelbimas vis dar sarase – atnaujinam laika. Kitaip po SEEN_MAX_AGE_DAYS
            # jis „pamirstamas“ ir issiunciamas is naujo (Pirkpard skelbimai sarase
            # isbuna savaites, tad tai kartodavosi kas 7 dienas).
            self.new_seen[uid] = time.time()
            prev = drops.get(uid)
            if not (c["PRICE_DROP_ALERTS"] and prev and price is not None
                    and price <= prev * (1 - c["PRICE_DROP_MIN"])):
                return self.reject("jau matyti")
            if self.state.market.already_alerted_at(uid, price):
                return self.reject("jau matyti")
            drop_from = prev

        if listing.skip_reason:
            # Saltinis jau is saraso zino, kad netinka – nebegaistam skelbimo puslapiui
            self.new_seen[uid] = time.time()
            return self.reject(listing.skip_reason, title[:45])

        model = detect_model(title)
        if model and hasattr(source, "note_ids"):
            source.note_ids(listing)      # renkam ID tik tikriems telefonams – pagal juos nustatysim filtra
        if not model or is_accessory(title) or price is None:
            with self.lock:
                self.new_count += 0 if drop_from else 1
            self.new_seen[uid] = time.time()
            return self.reject("ne telefonas / kitas modelis")

        # 1) Greitas patikrinimas pagal rinkos kaina (be skelbimo puslapio)
        storage = extract_storage(title)
        quote = self.state.market.quote(model, storage)
        if quote is None:
            # nezymim kaip matyto – kai atsiras duomenu, ivertinsim
            return self.reject("per mazai kainu duomenu",
                               f"iPhone {model} ({self.state.market.sample_count(model)} skelb.)")
        if not drop_from:
            with self.lock:
                self.new_count += 1
        cat_condition = listing.condition
        if c["TIDY_ONLY"] and not condition_ok(cat_condition, c["MIN_CONDITION"]):
            self.new_seen[uid] = time.time()
            return self.reject(f"būklė {cat_condition}")

        # Ar pigu? Du budai:
        #  "rank"     – ar skelbimas tarp pigiausiu siuo metu parduodamu tokiu pat telefonu.
        #               Nereikia zinoti rinkos kainos, tad isputa mediana netrukdo.
        #  "discount" – ar pigiau nei musu ivertinta verte (senasis budas; naudojamas ir
        #               tada, kai palyginti per mazai – pvz. retiems modeliams).
        rank = None
        if c["DEAL_MODE"] == "rank":
            rank = self.state.market.rank(model, storage, price, exclude=uid)
            if rank is None:
                with self.lock:
                    self.totals["(retas modelis – vertinta pagal nuolaidą)"] = \
                        self.totals.get("(retas modelis – vertinta pagal nuolaidą)", 0) + 1
        if rank is not None:
            if rank.share > c["RANK_TOP_PCT"]:
                self.new_seen[uid] = time.time()
                return self.reject("ne tarp pigiausių",
                                   f"iPhone {model} {price:.0f}€ – {rank.place}-as iš {rank.n} "
                                   f"({rank.low:.0f}–{rank.high:.0f}€)")
        else:
            best_case = quote.price * CONDITION_FACTOR.get(cat_condition, 1.08) * 1.03
            if price > best_case * (1 - c["MIN_DISCOUNT"]):
                self.new_seen[uid] = time.time()
                return self.reject("per brangu")
        # Riba: 40% rinkos kainos arba modelio minimali kaina – kuri mazesne (kad
        # apytiksle kaina ar mano ivertinta minimali kaina neatmestu tikru pigiu telefonu)
        floor = min(quote.price * c["HARD_MIN_PRICE_RATIO"], min_price(model) or float("inf"))
        if price < floor:
            self.new_seen[uid] = time.time()
            return self.reject("per pigu (sugedęs / dalims / ne telefonas?)",
                               f"{title[:40]} {price:.0f}€ (riba {floor:.0f}€, rinka {quote.price:.0f}€)")
        self.new_seen[uid] = time.time()

        # 2) Skelbimo puslapis
        detail = source.detail(listing)
        if getattr(source, "detail_needs_request", True):
            self.sleep(c["DETAIL_SLEEP_SECONDS"])
        if detail.status in ("sold", "gone"):
            self.state.market.set_status(uid, detail.status)
            return self.reject("jau parduotas" if detail.status == "sold" else "skelbimo nebėra")
        description = detail.description or ""
        if description_not_phone(description) or detect_model(detail.title or title) is None:
            return self.reject("ne telefonas (pagal aprašymą)", f"{title[:40]} | {description[:60]}")

        if c["ONLY_LITHUANIAN_TEXT"]:
            lang = detect_foreign_language(detail.title or title, description)
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
        condition = detail.condition or cat_condition
        if c["TIDY_ONLY"] and not condition_ok(condition, c["MIN_CONDITION"]):
            return self.reject(f"būklė {condition}")

        title_storage = storage
        storage = storage or extract_storage(description)
        quote = self.state.market.quote(model, storage) or quote
        # Talpa paaiskejo tik is aprasymo – palyginam dar karta, jau su ta pacia talpa
        # (64 GB ir 512 GB tame paciame sarase iskreiptu vieta).
        if rank is not None and storage and storage != title_storage:
            tikslesnis = self.state.market.rank(model, storage, price, exclude=uid)
            if tikslesnis is not None:
                rank = tikslesnis
                if rank.share > c["RANK_TOP_PCT"]:
                    return self.reject("ne tarp pigiausių",
                                       f"iPhone {model} {storage} {price:.0f}€ – "
                                       f"{rank.place}-as iš {rank.n}")
        battery = extract_battery(title, description)
        value = estimate_value(quote.price, condition, battery, defects)
        discount = 1 - price / value
        # Pigiausiu budu spejama verte nebera sprendimo pagrindas – ji lieka tik korteles
        # informacijai ir pelno ivertinimui.
        if rank is None and discount < c["MIN_DISCOUNT"]:
            how = f"tik {discount:.0%} pigiau" if discount > 0 else f"{-discount:.0%} brangiau"
            return self.reject("ne pakankamai pigu", f"iPhone {model} {price:.0f}€, vertė {value:.0f}€ ({how})")

        # Baterija: nurodyta ir per maza – atmetam, nebent kaina tikrai gera.
        # Nenurodyta – praleidziam, bet kortelėje parasom "nenurodyta".
        battery_low = False
        if c["MIN_BATTERY"] and battery is not None and battery < c["MIN_BATTERY"]:
            # Isimtis silpnai baterijai – tik kai kaina tikrai isskirtine:
            # pigiausiu budu – pats pigiausias; nuolaidos budu – didele nuolaida.
            isskirtine = (rank.place == 1) if rank is not None \
                else discount >= c["LOW_BATTERY_MIN_DISCOUNT"]
            if not isskirtine:
                return self.reject("baterija", f"{title[:40]} {battery}%")
            battery_low = True

        # 3) Pardavejas
        seller = detail.seller or listing.seller or {}
        country = seller.get("country")
        if c["FILTER_BY_COUNTRY"]:
            ok = (country in c["ALLOWED_COUNTRY_CODES"]) if country else (not c["REQUIRE_KNOWN_COUNTRY"])
            if not ok:
                return self.reject("salis", f"{country}: {title[:40]}")
        rating, reviews = seller.get("rating"), seller.get("reviews")
        if rating is not None and reviews is not None and (
                rating < c["MIN_SELLER_RATING"] or reviews < c["MIN_SELLER_REVIEWS"]):
            return self.reject("pardavejas", f"{rating}/5, {reviews} atsil.: {title[:40]}")

        # Tas pats telefonas, is naujo ikeltas kitu skelbimu, atpazistamas pagal pardaveja.
        # Kai pardavejo nustatyti nepavyksta, imam pati skelbima – tada dublikatu nebus
        # ieskoma, bet ir skirtingi zmones nebus supainioti tarpusavyje.
        seller_id = str(seller.get("id") or listing.seller_id or uid)
        fp = "fp:" + hashlib.sha1(
            f"{source.name}|{seller_id}|{model}|{storage}|{price:.0f}".encode()).hexdigest()[:16]
        if fp in self.new_seen:
            return self.reject("dublikatai")

        risk_level, risk_reasons = assess_risk(detail.title or title, description, price, quote.price,
                                               seller, listing.photo_count)
        profit = estimate_profit(price, value, pickup_only=PICKUP_LABEL in risk_reasons,
                                 buyer_fee=getattr(source, "buyer_protection_fee", True),
                                 total_price=listing.total_price)

        deal = {
            "id": uid, "source": source.name, "source_label": getattr(source, "label", source.name),
            "model": model, "seller_id": seller_id, "storage": storage, "title": title,
            "price": price, "url": listing.url, "photo": listing.photo or detail.photo,
            "description": description, "condition": condition, "battery": battery,
            "battery_low": battery_low, "defects": [d for d, _ in defects],
            "quote": quote, "value": value, "discount": discount, "profit": profit,
            "seller": seller, "risk_level": risk_level, "risk_reasons": risk_reasons,
            "drop_from": drop_from, "created_at": listing.created_at, "rank": rank,
            "age": human_age(listing.created_at),
        }
        if c["PAUSED"]:
            return self.reject("pauzė (/testi – įjungti)")
        self.new_seen[fp] = time.time()
        return deal

    # --- visas paleidimas --------------------------------------------------------
    def out_of_time(self):
        limit = config.cfg["MAX_RUN_MINUTES"]
        if limit > 0 and (time.time() - self.started) / 60 >= limit:
            return True
        return self.deadline is not None and time.time() >= self.deadline

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
        told_setup = False
        for m in messages:
            if m["private"]:
                reply = commands.handle_private(m, self.state)
                print(f"Asmenine komanda ({m['name']}): {m['text'][:40]}")
                self.tg.send_message(reply, chat_id=m["chat"])
            elif commands.is_admin(m["user"]):
                reply = commands.handle(m["text"], self.state)
                if reply:
                    print(f"Komanda ({m['name']}): {m['text'][:50]}")
                    self.tg.send_message(reply)
            elif not config.cfg.get("ADMIN_IDS") and not told_setup:
                # Administratorius dar nenustatytas – anksciau komanda buvo tyliai ignoruojama,
                # ir savininkas nesuprasdavo, kodel botas neatsako. Pasakom, ka daryti
                # (ir jo paties ID – tai nieko neatskleidzia apie kitus).
                told_setup = True
                print(f"Komanda neivykdyta – ADMIN_IDS tuscias ({m['name']}, ID {m['user']}): {m['text'][:40]}")
                self.tg.send_message(commands.admin_setup_message(m["user"]))
            else:
                # Grupeje komandos is kitu zmoniu ignoruojamos – nieko neatskleidziam
                print(f"Ignoruota komanda grupeje ({m['name']}, ID {m['user']}): {m['text'][:40]}")
        for cb in callbacks:
            answer = commands.handle_callback(cb, self.state)
            print(f"Mygtukas ({cb['name']}): {cb['data']} -> {answer[:40]}")
            self.tg.answer_callback(cb["id"], answer)
        if offset != self.state.telegram_offset:
            self.state.telegram_offset = offset
            self.state.save()

    def check_sold(self):
        """Senus skelbimus tikrinam tame saltinyje, is kurio jie atejo.

        Kaip ir paieska, saltiniai tikrinami vienu metu: Vinted kiekvienam skelbimui
        atidaro atskira puslapi (letai), o Pirkpard busena visiems gauna viena
        uzklausa – nera prasmes jam laukti, kol Vinted baigs."""
        by_name = {s.name: s for s in self.sources}
        groups = {}
        for uid in self.state.market.sold_check_candidates():
            source_name, local_id = split_uid(uid)
            if source_name in by_name:
                groups.setdefault(source_name, []).append((uid, local_id))
        if not groups:
            return

        found = {"sold": 0, "gone": 0}

        def check(source_name, items):
            source = by_name[source_name]
            for uid, local_id in items:
                if self.out_of_time():
                    break
                url = (self.state.market.get(uid) or {}).get("u")
                st = source.status(local_id, url)
                with self.lock:
                    self.state.market.set_status(uid, st)
                    if st in found:
                        found[st] += 1
                if getattr(source, "detail_needs_request", True):
                    self.sleep(config.cfg["DETAIL_SLEEP_SECONDS"])

        if config.cfg["PARALLEL_SOURCES"] and len(groups) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(groups)) as pool:
                futures = {pool.submit(check, name, items): name
                           for name, items in groups.items()}
                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        print(f"! Pardavimu patikra ({futures[future]}) nutruko: {e}")
        else:
            for name, items in groups.items():
                check(name, items)

        if any(found.values()):
            extra = " (dingusius laikom parduotais)" if config.cfg["GONE_AS_SOLD"] else ""
            print(f"Pardavimu patikra: parduota {found['sold']}, dingo {found['gone']}{extra}")

    def rotated(self, queries):
        """Kiekviena paleidima pradedam nuo kito modelio – kad tie patys nebutu
        visada tikrinami paskutiniai (ir apie ju dealus suzinotum veliausiai)."""
        c = config.cfg
        if not c["ROTATE_QUERIES"] or not queries:
            return list(queries)
        start = self.state.query_offset % len(queries)
        return list(queries[start:]) + list(queries[:start])

    @staticmethod
    def mark_foreign(listings):
        """Vinted rodo ir kitu saliu skelbimus („Sprzedam, stan idealny“). Jie ne tik
        netinka – ju kainos iskraipo lyginima su Lietuvos rinka. Pavadinimo uztenka,
        kad daugumas butu atpazinti dar pries atidarant skelbimo puslapi."""
        if not config.cfg["ONLY_LITHUANIAN_TEXT"]:
            return
        allowed = config.allowed_languages()
        for listing in listings:
            if listing.skip_reason:
                continue
            lang = detect_foreign_language(listing.title or "", "")
            if lang and lang not in allowed:
                listing.skip_reason = f"kalba ({lang}, pagal pavadinimą)"

    def scan(self, source, seen, pages):
        """Viena saltinio perziura. Grazina, kiek skelbimu gauta."""
        c = config.cfg
        fetched = 0
        source.start()
        if source.unavailable:
            print(f"! [{source.label}] siame paleidime nepasiekiamas: {source.unavailable}")
            return 0
        pages = source.page_count(pages)
        queries = self.rotated(source.queries())
        if c["ROTATE_QUERIES"] and len(queries) > 1:
            print(f"[{source.label}] pradedama nuo: {source.describe(queries[0])}")
        for q in queries:
            if self.out_of_time():
                print(f"! Pasiektas laiko limitas ({c['MAX_RUN_MINUTES']} min.) – "
                      "likusius modelius tikrinsiu kitame paleidime.")
                break
            if source.unavailable:
                print(f"! [{source.label}] nebepasiekiamas ({source.unavailable}) – "
                      "baigiu si saltini, tesim kitame paleidime.")
                break
            if source.blocked_queries >= c["STOP_AFTER_BLOCKED_QUERIES"]:
                print(f"! {source.label} blokuoja uzklausas ({source.blocked_queries} paieskos is eiles) – "
                      "baigiu si saltini, tesim kitame paleidime.")
                break
            print(f"Tikrinama [{source.label}]: {source.describe(q)}...")
            listings = source.search(q, pages, seen)
            self.last_error = source.last_error or self.last_error
            fetched += len(listings)
            self.mark_foreign(listings)
            drops = self.state.market.observe(listings)
            self.examples, sent_before = [], len(self.alerts)
            for listing in listings:
                if self.out_of_time():
                    break
                deal = self.evaluate(source, listing, drops)
                if not deal:
                    continue
                # Siuntimas ir irasymas – po vieną: kitaip lygiagretus saltiniai
                # persidengtu Telegram zinutemis ir pustuciais failais.
                with self.lock:
                    self.alerts.append(deal)
                    self.state.market.mark_alerted(deal["id"], deal["price"])
                    rank = deal.get("rank")
                    # Pigiausiu budu garsiai – tik pats pigiausias (nuolaida cia remiasi
                    # ta pacia spejama verte, kuria ir nepasitikim)
                    silent = (rank.place != 1) if rank is not None \
                        else deal["discount"] < c["LOUD_DISCOUNT"]
                    self.tg.send_deal(deal, silent=silent)
                    self.send_personal(deal)
                    tag = f"atpigo nuo {deal['drop_from']:.0f}, " if deal["drop_from"] else ""
                    kiek = (f"{rank.place}-as pigiausias is {rank.n}" if rank is not None
                            else f"-{deal['discount']:.0%}")
                    print(f"  -> [{source.label}] iPhone {deal['model']} {deal['price']:.0f} EUR "
                          f"({tag}{kiek}{', tyliai' if silent else ''}): {deal['title'][:45]}")
                    save_seen(self.new_seen)
                    self.state.save()
                self.sleep(1)
            with self.lock:
                print(f"  [{source.label}] gauta: {len(listings)}, "
                      f"tinkama: {len(self.alerts) - sent_before}")
                for ex in self.examples:
                    print(f"    atmesta – {ex}")
            self.sleep(c["SLEEP_SECONDS"])
        source.finish(self)
        return fetched

    def scan_all(self, seen, pages):
        """Perziuri visus saltinius. Lygiagreciai (jie eina i skirtingus serverius,
        tad viens kito nelaukia) arba paeiliui, jei taip nustatyta."""
        c = config.cfg
        sources = list(self.sources)
        if len(sources) < 2:
            return sum(self.scan(s, seen, pages) for s in sources)

        if c["PARALLEL_SOURCES"]:
            print(f"Tikrinami lygiagreciai: {', '.join(s.label for s in sources)}")
            fetched = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(sources)) as pool:
                futures = {pool.submit(self.scan, s, seen, pages): s for s in sources}
                for future in concurrent.futures.as_completed(futures):
                    source = futures[future]
                    try:
                        fetched += future.result()
                    except Exception as e:
                        # Vieno saltinio gedimas neturi nutraukti kito
                        self.last_error = f"{source.label}: {e}"
                        print(f"! {source.label} nutruko: {e}")
                        print(traceback.format_exc())
            return fetched

        # Paeiliui: saltiniai dalijasi laiku po lygiai, o ju eiliskumas kas paleidima
        # keiciasi – kitaip antrasis prie ilgo Vinted patikrinimo niekada neprieitu.
        shift = self.state.query_offset % len(sources)
        sources = sources[shift:] + sources[:shift]
        limit = c["MAX_RUN_MINUTES"]
        fetched = 0
        for i, source in enumerate(sources):
            # Terminas nustatomas pries patikra – kitaip praejes pirmojo saltinio
            # terminas iskart nutrauktu ir antraji.
            self.deadline = (self.started + limit * 60 * (i + 1) / len(sources)
                             if limit > 0 else None)
            if i and self.out_of_time():
                break
            fetched += self.scan(source, seen, pages)
        self.deadline = None
        return fetched

    def run(self):
        c = config.cfg
        seen = load_seen()
        self.state = State.load(seen=seen)
        config.apply_overrides(self.state.overrides)
        self.report_config_error()
        self.process_commands()

        self.new_seen = dict(seen)
        self.new_seen.pop("__heartbeat__", None)
        self.new_count = 0

        pages = c["PAGES"] if seen else max(c["PAGES"], c["FULL_SCAN_PAGES"])
        if not seen:
            print(f"seen.json tuscias – pilnas perziurejimas ({pages} psl. kiekvienai paieskai)")

        fetched = self.scan_all(seen, pages)

        queries = config.cfg["SEARCH_QUERIES"]
        if c["ROTATE_QUERIES"] and queries:
            start = self.state.query_offset % len(queries)
            self.state.query_offset = (start + max(1, len(queries) // 3)) % len(queries)

        if c["USE_SOLD_PRICES"]:
            self.check_sold()

        self.report_blocked_sources()
        self.calibrate()
        self.print_market()
        summary = ", ".join(f"{k}: {n}" for k, n in sorted(self.totals.items(), key=lambda kv: -kv[1]))
        print(f"IS VISO: gauta {fetched}, tinkama {len(self.alerts)}. Atmesta – {summary}")
        self.heartbeat(fetched, summary)
        self.state.last_run = {"time": int(time.time()), "fetched": fetched, "new": self.new_count,
                               "sent": len(self.alerts), "totals": self.totals}
        if fetched == 0:
            self.state.fail_streak += 1
            print(f"! Negauta skelbimu ({self.state.fail_streak} paleidimas is eiles): {self.last_error}")
            # Vienkartinis 403 = laikinas blokas serverio IP; pranesam tik jei kartojasi
            if self.state.fail_streak >= c["FAIL_ALERT_RUNS"]:
                self.tg.send_message(
                    f"<b>ISPEJIMAS</b>: {self.state.fail_streak} paleidimus is eiles negauta nei vieno "
                    "skelbimo.\nGalimai pasikeite API arba saltinis blokuoja – patikrink logus.\n"
                    f"Priezastis: <code>{html.escape(self.last_error or 'nezinoma')}</code>")
                self.state.fail_streak = 0
        else:
            self.state.fail_streak = 0

        self.state.save()
        save_seen(self.new_seen)

        print(f"Issiusta {len(self.alerts)} alert'u." if self.alerts else "Nauju deal'u nera.")

    def report_config_error(self):
        """Sugadintas config.json (pvz. dingo kablelis redaguojant GitHub'e) – botas veikia
        su numatytaisiais nustatymais, bet apie tai pranesa (ne dazniau nei kas valanda)."""
        if not config.load_error:
            return
        now = time.time()
        if now - float(self.state.source_alerts.get("__config__") or 0) < 3600:
            return
        self.state.source_alerts["__config__"] = now
        self.tg.send_message(
            "⚠️ <b>config.json sugadintas</b> – kol kas naudoju numatytuosius nustatymus.\n"
            f"Klaida: <code>{html.escape(config.load_error[:200])}</code>\n"
            "<i>Dažniausiai trūksta kablelio eilutės gale arba kabutės. "
            "Eilutės numeris nurodytas klaidoje (line …).</i>")

    def report_blocked_sources(self):
        """Kai vienas saltinis blokuojamas, o kitas veikia, bendras skaicius atrodo
        normaliai ir problema lieka nepastebeta. Todel pranesam atskirai – bet ne
        kas paleidima, kitaip Telegram uzsikimstu."""
        blocked = [s for s in self.sources if s.unavailable]
        if not blocked:
            return
        print("! Nepasiekiami saltiniai: "
              + ", ".join(f"{s.label} ({s.unavailable})" for s in blocked))
        hours = config.cfg["SOURCE_ALERT_HOURS"]
        now = time.time()
        for source in blocked:
            last = float(self.state.source_alerts.get(source.name) or 0)
            if hours > 0 and now - last < hours * 3600:
                continue
            self.state.source_alerts[source.name] = now
            self.tg.send_message(
                f"⚠️ <b>{html.escape(source.label)} nepasiekiamas</b>\n"
                f"Priežastis: {html.escape(source.unavailable)}\n"
                f"Kiti šaltiniai veikia toliau. Jei kartojasi – gali reikėti paleisti "
                f"iš kito IP (ne GitHub serverio).", silent=True)

    def calibrate(self):
        """Palygina, kiek spejom, su tuo, kiek realiai gauta uz parduotus telefonus."""
        market = self.state.market
        acc = market.accuracy()
        if acc["n"]:
            how = "patvirtinti pardavimai" if acc["confirmed"] else "dingę skelbimai"
            bias = 1 - acc["ratio"]
            word = "pervertinam" if bias > 0 else "nuvertinam"
            print(f"Vertinimo tikslumas ({acc['n']} parduoti, {how}): "
                  f"{word} {abs(bias):.0%} (pataisymas dabar x{market.calibration:.3f})")
            for model, n, ratio in acc["rows"][:8]:
                print(f"    iPhone {model:<11} {n:>3} parduoti, realiai {ratio:.0%} musu vertinimo")
        changed = market.calibrate()
        if changed:
            print(f"Pataisymas atnaujintas: x{changed['old']:.3f} -> x{changed['new']:.3f} "
                  f"(taikinys x{changed['wanted']:.3f} pagal {changed['n_target']} parduotus)")
        elif config.cfg["AUTO_CALIBRATE"]:
            truksta = config.cfg["MIN_CALIBRATION_SAMPLES"] - acc["n_target"]
            if truksta > 0:
                print(f"  (savikalibracijai dar reikia {truksta} parduotu telefonu)")

    def print_market(self):
        manual = config.market_prices()
        rows = {m: r for m, *r in self.state.market.summary()}
        print("Rinkos kainos (rankinė / parduotų mediana / įvertinta pagal skelbimus):")
        for model in MODEL_ORDER:
            if model not in rows and model not in manual:
                continue
            a, n, s, ns = rows.get(model, (None, 0, None, 0))
            # "naudojama" – tai, ka IS TIKRUJU grazina quote(), o ne spejimas pagal stulpelius.
            # Anksciau cia buvo "rankine arba parduoti arba ivertinta", ir su 2 pardavimais
            # rode parduotu mediana, nors kodas jos nenaudoja, kol ju maziau nei MIN_SOLD_SAMPLES.
            q = self.state.market.quote(model, None)
            kodel = {"rankinė": "ranka", "parduoti": "parduoti", "skelbimai": "skelb.",
                     "apytikslė": "apytiksl."}.get(q.source, "") if q else ""
            print(f"  iPhone {model:<11} rankinė: {manual.get(model, 0):>4.0f}  "
                  f"parduoti: {s or 0:>4.0f} ({ns})  įvertinta: {a or 0:>4.0f} ({n})  "
                  f"-> naudojama: {q.price if q else 0:>4.0f} ({kodel})")

    def heartbeat(self, fetched, summary):
        hours = config.cfg["HEARTBEAT_HOURS"]
        if hours <= 0 or time.time() - self.state.heartbeat < hours * 3600:
            return
        sources = ", ".join(s.label for s in self.sources)
        self.tg.send_message("✅ <b>Skriptas veikia</b>\n"
                             f"Šaltiniai: {html.escape(sources)}\n"
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
        Run(build_sources(), tg).run()
    except Exception as e:
        print(traceback.format_exc())
        tg.send_message("<b>SKRIPTAS UZLUZO</b>\n" + html.escape(str(e)[:400]))
