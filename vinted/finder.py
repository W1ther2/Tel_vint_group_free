# -*- coding: utf-8 -*-
"""Pagrindine logika: saltiniai -> vertinimas -> Telegram."""

import concurrent.futures
import hashlib
import html
import os
import threading
import time
import traceback

from . import archive, config, commands
from .catalog import brand_of
from .liquidity import describe as describe_sale_fact
from .confidence import (LEVELS as CONFIDENCE_LEVELS, PRIOR as CONFIDENCE_PRIOR, assess,
                         pessimistic, progress as confidence_progress)
from .language import detect_foreign_language, looks_lithuanian
from .listing import split_uid
from .phone import (detect_model, is_accessory, find_defects, extract_storage, extract_battery,
                    CONDITION_FACTOR, estimate_value, estimate_profit, MODEL_ORDER, condition_ok,
                    description_not_phone, min_price, model_wanted, display)
from .risk import assess_risk, PICKUP_LABEL
from .sources import build_sources, label as source_label
from .limiter import rate_floor
from .sources.vinted_source import VintedSource
from .state import State, load_seen, save_seen
from .telegram import Telegram
from .tracker import report_text
from .util import human_age, money


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
        self.source_stats = {}      # {saltinis: {"fetched", "error", "crash"}} – siam paleidimui
        # {saltinis: {"fetched", "new", "sent", "rejects": {priezastis: n}}} – zr.
        # print_diagnostics. Atskirai nuo source_stats: tas reikalingas saltinio
        # sveikatai (ar veikia), sis – klausimui „kodel nieko neatejo".
        self.diag = {}
        self.step_errors = []       # papildomi darbai, kurie nuluzo (pranesama kas valanda)
        self.statuses = {}          # {uid: busena} – tam paciam skelbimui neuzklausiam du kartus
        self.limit_announced = False  # ar jau parasem, kad pasiekta MAX_ALERTS_PER_RUN riba
        self.limit_skipped = 0        # kiek skelbimu liko neivertintu, kai riba buvo pasiekta
        self.country_limited = set()   # saltiniai, kuriems siame paleidime neuzteko salies uzklausu
        self.country_used = {}         # {saltinis: kiek salies uzklausu jau padaryta} – riba bendra
        self.country_waited = set()    # saltiniai, kuriems jau laukem 429 atvesinimo
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

    def diag_of(self, name=None):
        """Sio saltinio skaitikliai. Kviesti su self.lock.

        Saltinio vardas imamas is gijos (saltiniai gali suktis lygiagreciai, tad
        perdavineti ji per kiekviena reject() butu reikeje keisti ~20 vietu)."""
        name = name or getattr(self._local, "source_name", "") or "?"
        d = self.diag.get(name)
        if d is None:
            d = self.diag[name] = {"fetched": 0, "new": 0, "sent": 0, "rejects": {}}
        return d

    def reject_bad(self, uid, code, reason, example=None):
        """Atmetam IR isimam is rinkos palyginimo: uzrakintas, sugedes, ne telefonas ar
        uzsienio skelbimas neturi nei stumti tvarkingu telefonu vietos, nei keisti rinkos kainos."""
        self.state.market.exclude(uid, code)
        return self.reject(reason, example)

    def reject(self, reason, example=None):
        with self.lock:
            self.totals[reason] = self.totals.get(reason, 0) + 1
            by_source = self.diag_of()["rejects"]
            by_source[reason] = by_source.get(reason, 0) + 1
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
                self.diag_of()["new"] += 0 if drop_from else 1
            self.new_seen[uid] = time.time()
            return self.reject("ne telefonas / kitas modelis")
        if not model_wanted(model):
            # Pasirinkti modeliai (/modeliai). Atmetam cia – dar pries skelbimo puslapi,
            # tad nesekami modeliai nekainuoja nei uzklausu, nei laiko.
            self.new_seen[uid] = time.time()
            return self.reject("nesekamas modelis", f"{display(model)} ({title[:35]})")

        # 1) Greitas patikrinimas pagal rinkos kaina (be skelbimo puslapio)
        storage = extract_storage(title)
        quote = self.state.market.quote(model, storage)
        if quote is None:
            # nezymim kaip matyto – kai atsiras duomenu, ivertinsim
            return self.reject("per mazai kainu duomenu",
                               f"{display(model)} ({self.state.market.sample_count(model)} skelb.)")
        if not drop_from:
            with self.lock:
                self.new_count += 1
                self.diag_of()["new"] += 1
        # Nuo sios kainos skaiciuosim kita atpigima (ir laipsniska: 300 -> 290 -> 280)
        self.state.market.mark_evaluated(uid, price)
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
                                   f"{display(model)} {money(price)} – {rank.place}-as iš {rank.n} "
                                   f"({money(rank.low)}–{money(rank.high)})")
            # Gerokai pigesnis uz kita pigiausia tokį pat telefona – beveik visada kazkas
            # negerai (pvz. „iPhone 14 uzbluokuotas be akumo“ uz 130 €, kai kiti nuo 200 €).
            ratio = self.suspicious_ratio(rank, price)
            if ratio is not None and ratio < c["SUSPICIOUS_REJECT_RATIO"]:
                self.new_seen[uid] = time.time()
                return self.reject_bad(uid, "itartinai", "įtartinai pigu",
                                   f"{display(model)} {money(price)} – kitas pigiausias "
                                   f"{money(rank.peer_low)} ({1 - ratio:.0%} pigiau)")
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
                               f"{title[:40]} {money(price)} (riba {money(floor)}, "
                               f"rinka {money(quote.price)})")
        self.new_seen[uid] = time.time()
        if c["PAUSED"]:
            # Pauze tikrinam PRIES skelbimo puslapi: kainu istorija toliau kaupiasi
            # (ji naudinga ir per pauze), bet desimciu puslapiu ir pauzeliu po ju
            # nebegaistam – anksciau tas pats darbas buvo nudirbamas ir isbraukiamas.
            return self.reject("pauzė (/testi – įjungti)")

        # 2) Skelbimo puslapis
        detail = source.detail(listing)
        if getattr(source, "detail_needs_request", True):
            self.sleep(c["DETAIL_SLEEP_SECONDS"])
        if detail.status == "unknown":
            # Puslapio gauti nepavyko (403, laiko limitas, iššūkio puslapis). Be aprašymo nežinom,
            # ar telefonas neužrakintas ir veikia, todėl nesiunčiam – bandysim kitame paleidime.
            return self.detail_failed(uid, title, source)
        with self.lock:
            self.state.forget_detail_failure(uid)
        if detail.status in ("sold", "gone"):
            self.state.market.set_status(uid, detail.status)
            return self.reject("jau parduotas" if detail.status == "sold" else "skelbimo nebėra")
        description = detail.description or ""
        if description_not_phone(description) or detect_model(detail.title or title) is None:
            return self.reject_bad(uid, "ne_telefonas", "ne telefonas (pagal aprašymą)",
                                   f"{title[:40]} | {description[:60]}")

        if c["ONLY_LITHUANIAN_TEXT"]:
            lang = detect_foreign_language(detail.title or title, description)
            if lang and lang not in config.allowed_languages():
                return self.reject_bad(uid, "kalba", "kalba", f"{lang}: {title[:40]} | {description[:50]}")

        defects = find_defects(title, description)
        if any(f == 0 for _, f in defects):
            return self.reject_bad(uid, "neveikia", "neveikiantis / užrakintas / netestuotas",
                                   f"{title[:40]} {[d for d, _ in defects]}")
        if c["TIDY_ONLY"]:
            allowed = set(c["ALLOWED_DEFECTS"])
            bad = [d for d, _ in defects if d not in allowed]
            if bad:
                return self.reject_bad(uid, "defektai", "defektai", f"{title[:40]} {bad}")
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
                                       f"{display(model)} {storage} {price:.0f}€ – "
                                       f"{rank.place}-as iš {rank.n}")
        battery = extract_battery(title, description)
        value = estimate_value(quote.price, condition, battery, defects)
        discount = 1 - price / value
        confidence = assess(quote, self.state.market.confidence_bands)
        # Pigiausiu budu spejama verte nebera sprendimo pagrindas – ji lieka tik korteles
        # informacijai ir pelno ivertinimui.
        if rank is None and discount < c["MIN_DISCOUNT"]:
            how = f"tik {discount:.0%} pigiau" if discount > 0 else f"{-discount:.0%} brangiau"
            return self.reject("ne pakankamai pigu",
                               f"{display(model)} {price:.0f}€, vertė {value:.0f}€ ({how})")
        if rank is None:
            # Nuolaidos budu sprendimas remiasi TIK rinkos kaina, tad ji turi buti patikima:
            # nuolaida skaiciuojama nuo ATSARGIOS vertes – tokios, kurios nesieke tik 20 %
            # pardavimu siam lygiui (p20, zr. confidence.py). Butent taip v45 Android lentele
            # (iki 50 % per auksta) paversdavo normaliai ikainota telefona „nuolaida“.
            # „Pigiausiu“ budas (rank) cia neateina – jo logika nekeiciama.
            if quote.source == "apytikslė" and c["REQUIRE_MEASURED_PRICE"] \
                    and getattr(brand_of(model), "key", None) != "apple":
                # Retas ANDROID modelis (iPhone retiems paliekama): kaina is lenteles, ne is skelbimu ar pardavimu.
                # 2026-10-08: Huawei P40 Pro 65€ ir Mi 11 60€ atejo vien pagal spejima.
                return self.reject("nepatikima rinkos kaina",
                                   f"{display(model)} {price:.0f}€, kaina tik apytikslė (per mažai duomenų)")
            safe = pessimistic(value, confidence)
            if 1 - price / safe < c["MIN_DISCOUNT"]:
                how = "išmokta" if confidence.learned else "pradinė"
                return self.reject("nepatikima rinkos kaina",
                                   f"{display(model)} {price:.0f}€, vertė {value:.0f}€ -> atsargiai "
                                   f"{safe:.0f}€ (x{confidence.factor:.2f} {how}, {confidence.reason})")

        # Baterija: nurodyta ir per maza – atmetam, nebent kaina tikrai gera.
        # Nenurodyta – praleidziam, bet kortelėje parasom "nenurodyta".
        battery_low = False
        if c["MIN_BATTERY"] and battery is not None and battery < c["MIN_BATTERY"]:
            # Isimtis silpnai baterijai – tik kai kaina tikrai isskirtine:
            # pigiausiu budu – pats pigiausias; nuolaidos budu – didele nuolaida.
            isskirtine = (rank.place == 1) if rank is not None \
                else discount >= c["LOW_BATTERY_MIN_DISCOUNT"]
            if not isskirtine:
                return self.reject_bad(uid, "baterija", "baterija", f"{title[:40]} {battery}%")
            battery_low = True

        # 3) Pardavejas
        seller = detail.seller or listing.seller or {}
        country = seller.get("country")
        if c["FILTER_BY_COUNTRY"]:
            if country:
                ok = country in c["ALLOWED_COUNTRY_CODES"]
            elif c["REQUIRE_KNOWN_COUNTRY"]:
                ok = False
            elif c["UNKNOWN_COUNTRY_NEEDS_LT_TEXT"]:
                # Salies nustatyti nepavyko (pardavejo uzklausa neatsake ar pasiekta riba).
                # Tada reikia POZITYVAUS lietuviskumo: „iPhone 12 64g“ be jokio lietuvisko
                # zodzio daznai yra lenku skelbimas, kurio kalbos filtras nepagauna.
                ok = looks_lithuanian(detail.title or title, description)
            else:
                ok = True
            if not ok:
                # Salis nezinoma tik todel, kad siame paleidime pasiekta uzklausu riba (ar
                # Vinted atsake 429) – tai laikina. Nezymim matytu, kad kitas paleidimas
                # patikrintu is tikruju, o ne nurasytu tvarkingo lietuvisko skelbimo.
                if not country and source.name in self.country_limited \
                        and self.retried_later(uid, "cc:", "šalies nustatyti nepavyko", title):
                    return None
                return self.reject_bad(uid, "salis", "salis",
                                       f"{country or 'šalis nežinoma, tekstas ne lietuviškas'}: {title[:40]}")
            if country:
                self.state.market.confirm_country(uid)     # vel skaiciuojam i rinkos kaina
                with self.lock:
                    self.state.forget_detail_failure("cc:" + uid)
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
            "id": uid, "fp": fp, "source": source.name, "source_label": getattr(source, "label", source.name),
            "model": model, "seller_id": seller_id, "storage": storage, "title": title,
            # Gamintojo raktas – pagal ji parenkama grupes skiltis (TOPIC_BY_BRAND).
            "brand": getattr(brand_of(model), "key", None),
            "price": price, "url": listing.url, "photo": listing.photo or detail.photo,
            "description": description, "condition": condition, "battery": battery,
            "battery_low": battery_low, "defects": [d for d, _ in defects],
            "quote": quote, "value": value, "discount": discount, "profit": profit,
            "seller": seller, "risk_level": risk_level, "risk_reasons": risk_reasons,
            "drop_from": drop_from, "created_at": listing.created_at, "rank": rank,
            "confidence": confidence,
            "age": human_age(listing.created_at),
        }
        if c["MIN_PROFIT_EUR"] and profit is not None and profit < c["MIN_PROFIT_EUR"]:
            return self.reject("per mažas pelnas",
                               f"{display(model)} {price:.0f}€ – pelnas ~{profit:.0f}€ "
                               f"(riba {c['MIN_PROFIT_EUR']:.0f}€)")
        ratio = self.suspicious_ratio(rank, price)
        if ratio is not None and ratio < c["SUSPICIOUS_WARN_RATIO"]:
            deal["suspicious"] = {"ratio": ratio, "peer_low": rank.peer_low}
        self.new_seen[fp] = time.time()
        return deal

    def detail_failed(self, uid, title, source=None):
        """Skelbimo puslapis neatsidare. Iki DETAIL_RETRIES kartu – bandom kitame paleidime.

        Isimtis – uzklausu greicio riba (429). Tada puslapio net neklausem arba serveris
        atsake „per daug uzklausu“: skelbimas cia nieko neprasikalto, tad bandymo
        neskaiciuojam. Kitaip ilgesnis atvesinimas (pvz. bendras runner'io IP) per tris
        paleidimus nurasydavo tvarkingus skelbimus su „skelbimo atidaryti nepavyko“ –
        lygiai ta pati bėda, kuri salies patikrai jau istaisyta (zr. country_limited)."""
        if source is not None and getattr(source, "rate_limited", False):
            self.new_seen.pop(uid, None)
            return self.reject("užklausų riba – bandysiu vėliau", title[:45])
        if self.retried_later(uid, "", "skelbimo atidaryti nepavyko", title):
            return None
        return self.reject("skelbimo atidaryti nepavyko", title[:45])

    def retried_later(self, uid, prefix, reason, title):
        """Laikina nesekme (puslapis neatsidare, pasiekta salies uzklausu riba).

        Grazina True, kai skelbimas atidedamas: matytu jo nezymim, tad kitas paleidimas
        bandys dar karta. Bet ne be galo – po DETAIL_RETRIES kartu grazina False, ir
        skambinantysis atmeta galutinai."""
        with self.lock:
            n = self.state.note_detail_failure(prefix + uid)
        if n >= max(1, int(config.cfg["DETAIL_RETRIES"])):
            return False
        self.new_seen.pop(uid, None)
        self.reject(f"{reason} – bandysiu vėliau", title[:45])
        return True

    @staticmethod
    def suspicious_ratio(rank, price):
        """Kiek sis skelbimas kainuoja, palyginus su kitu pigiausiu (1.0 = tiek pat)."""
        if rank is None or not rank.peer_low or price is None:
            return None
        return price / rank.peer_low

    # --- visas paleidimas --------------------------------------------------------
    def alert_limit_reached(self):
        """Ar jau issiuntem tiek, kiek leidzia MAX_ALERTS_PER_RUN.

        Likusiu skelbimu net nevertinam, tad matytais jie nepazymimi ir nedingsta –
        juos ivertins kitas paleidimas. Kitaip, pasimetus busenai, visi skelbimai
        atrodytu nauji ir Telegram'as gautu desimtis korteliu vienu ypu."""
        limit = config.cfg["MAX_ALERTS_PER_RUN"]
        if limit <= 0:
            return False
        with self.lock:
            if len(self.alerts) < limit:
                return False
            if not self.limit_announced:
                self.limit_announced = True
                print(f"! Pasiekta {limit} pranesimu riba (MAX_ALERTS_PER_RUN) – "
                      "likusius skelbimus vertinsiu kitame paleidime.")
        return True

    def apply_learned_rate(self):
        """Issimokta uzklausu greicio riba is ankstesniu paleidimu (state.json)."""
        if not config.cfg["VINTED_RATE_ADAPT"]:
            return
        for source in self.sources:
            limiter = getattr(source, "limiter", None)
            learned = (self.state.rate_limit.get(source.name) or {}).get("per_minute")
            if limiter is None or not learned:
                continue
            limiter.rate = float(learned)
            if limiter.limit() < int(config.cfg["VINTED_MAX_PER_MINUTE"] or 0):
                print(f"[{source.label}] greicio riba is ankstesniu paleidimu: "
                      f"{limiter.limit()}/min (nustatymuose {config.cfg['VINTED_MAX_PER_MINUTE']})")

    def learn_rate_limit(self):
        """AIMD su atmintimi: po 429 greitis mazinamas, po tyliu paleidimu po truputi grazinamas.

        45/min yra spejimas vienam runner'iui, bet GitHub Actions IP yra BENDRI: kai tuo paciu
        IP tuo paciu metu dirba kitas darbas, tikroji riba zemesne, ir jokia staline reiksme to
        nenumatys. Todel paleidimas pats pasimatuoja:
        - gavo 429 – greitis jau sumazintas paleidimo metu (x0,7, zr. HostLimiter._slow_down),
          cia ji tik issaugom, kartu su greiciu, kuriam serveris atsake „per daug“;
        - neivo – +3/min, bet VINTED_RATE_MEMORY_HOURS laikotarpiu ne arciau nei 3/min iki
          to atsakyto greicio. Be sios atminties greitis kas kelis paleidimus vel atsitrenkia
          i ta pacia siena (simuliacijoje: 429 kas ketvirtame paleidime, ir kiekvienas toks
          prarasdavo skelbimu puslapius).

        Lubos visada is nustatymu, tad issimoktas skaicius niekada nebuna didesnis, nei
        leido zmogus. Imtis mazesne nei 10 uzklausu nieko nepasako – tada nesimokom."""
        c = config.cfg
        ceiling = int(c["VINTED_MAX_PER_MINUTE"] or 0)
        if not c["VINTED_RATE_ADAPT"] or ceiling <= 0:
            return
        memory = float(c["VINTED_RATE_MEMORY_HOURS"]) * 3600
        floor = min(float(ceiling), rate_floor())
        now = time.time()
        for source in self.sources:
            limiter = getattr(source, "limiter", None)
            if limiter is None:
                continue
            stats = limiter.stats()
            if stats["total"] < 10:
                continue
            entry = self.state.rate_limit.get(source.name) or {}
            was = float(entry.get("per_minute") or ceiling)
            bad, bad_at = entry.get("bad"), float(entry.get("bad_at") or 0)
            if stats["rate_limits"]:
                new_rate = float(limiter.rate or limiter.limit())
                bad, bad_at = limiter.refused_at or was, now
                why = f"{stats['rate_limits']} x 429 prie {bad:.0f}/min"
            else:
                if bad and now - bad_at > memory:
                    bad = None              # sena patirtis – runner'iu salygos pasikeite
                cap = min(float(ceiling), float(bad) - 3) if bad else float(ceiling)
                # Atmintis riboja KILIMA, bet niekada nenuleidzia zemiau VINTED_RATE_MIN:
                # 429 prie 15/min neturi reiksti, kad kitas tylus paleidimas eis 12/min.
                cap = max(floor, cap)
                new_rate = min(cap, was + 3) if was < cap else was
                why = f"{stats['total']} uzklausu be 429"
            # Grindys ir lubos – visais atvejais (ir senam state.json skaiciui).
            new_rate = min(float(ceiling), max(floor, new_rate))
            if round(new_rate) != round(was):
                print(f"[{source.label}] greicio riba: {was:.0f} -> {new_rate:.0f}/min ({why})")
            self.state.rate_limit[source.name] = {
                "per_minute": round(new_rate, 1), "updated": int(now),
                "hits": stats["rate_limits"], "bad": bad, "bad_at": int(bad_at) if bad else None}

    def request_report(self):
        """Uzklausu skaitliukai i log'a ir i state.json („last_run“).

        Siame projekte ribos nustatomos is gyvu matavimu, ne is nuojautos – tad paleidimas
        pats pasako, kiek uzklausu kuriai rusiai isleido, kiek buvo piko greitis, kiek laiko
        sulaikyta ir ar riba vis dar pasiekiama. Pagal tai tikslinami VINTED_MAX_PER_MINUTE
        ir SELLER_COUNTRY_LOOKUPS."""
        stats = {}
        for source in self.sources:
            limiter = getattr(source, "limiter", None)
            if limiter is None:
                continue
            line = limiter.report()
            if line:
                print(line)
            stats[source.name] = limiter.stats()
        return stats

    def report_alert_limit(self):
        """Pasiekta MAX_ALERTS_PER_RUN – pranesam, bet ne dazniau nei kas valanda.

        Anksciau riba buvo tik log'o eilute: i Telegram atkeliaudavo 20 korteliu, o kad eileje
        liko dar tiek pat neivertintu skelbimu, savininkas nesuzinodavo – atrodydavo, lyg
        daugiau nieko ir nebuvo."""
        if not self.limit_announced:
            return
        now = time.time()
        if now - float(self.state.source_alerts.get("__alert_limit__") or 0) < 3600:
            return
        self.state.source_alerts["__alert_limit__"] = now
        limit = config.cfg["MAX_ALERTS_PER_RUN"]
        left = (f"liko <b>{self.limit_skipped}</b> neįvertintų skelbimų"
                if self.limit_skipped else "likę skelbimai neįvertinti")
        self.tg.send_admin(
            f"ℹ️ <b>Pasiekta {limit} pranešimų riba</b> (MAX_ALERTS_PER_RUN)\n"
            f"Šiame paleidime {left} – juos įvertins kitas paleidimas, matytais jie "
            f"nepažymėti, tad nedings.\n"
            f"<i>Jei kartojasi, ribą pakelk: /riba {min(100, limit * 2) or 40}</i>", silent=True)

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
        try:
            for m in messages:
                # Vienos komandos klaida neturi nutraukti kitu IR (svarbiausia) negali
                # sustabdyti offset'o irasymo: neirasytas offset reiskia, kad ta pati
                # komanda vykdoma is naujo kas 10 min., ir botas niekada nepajudes toliau.
                told_setup = self.step(f"Komanda {m.get('text', '')[:20]}",
                                       lambda m=m, t=told_setup: self.one_command(m, t)) or told_setup
            for cb in callbacks:
                self.step(f"Mygtukas {cb.get('data', '')[:20]}", lambda cb=cb: self.one_callback(cb))
        finally:
            if offset != self.state.telegram_offset:
                self.state.telegram_offset = offset
                self.state.save()

    def one_command(self, m, told_setup):
        """Viena zinute. Grazina True, jei jau pasakem, kaip nustatyti ADMIN_IDS."""
        if m["private"]:
            reply = commands.handle_private(m, self.state)
            print(f"Asmenine komanda ({m['name']}): {m['text'][:40]}")
            self.tg.send_message(reply, chat_id=m["chat"])
        elif commands.is_admin(m["user"]):
            reply = commands.handle(m["text"], self.state)
            if reply:
                print(f"Komanda ({m['name']}): {m['text'][:50]}")
                self.tg.send_message(reply)
        elif not commands.admin_ids() and not told_setup:
            # Administratorius dar nenustatytas – anksciau komanda buvo tyliai ignoruojama,
            # ir savininkas nesuprasdavo, kodel botas neatsako. Pasakom, ka daryti
            # (ir jo paties ID – tai nieko neatskleidzia apie kitus).
            #
            # Tikrinam admin_ids(), o ne tik cfg["ADMIN_IDS"]: administratorius gali
            # buti zinomas is ADMIN_CHAT_ID, ir tada siulyti ka nors nustatyti butu
            # klaidinga – komandos jam veikia.
            print(f"Komanda neivykdyta – administratorius nenustatytas "
                  f"({m['name']}, ID {m['user']}): {m['text'][:40]}")
            self.tg.send_message(commands.admin_setup_message(m["user"]))
            return True
        else:
            # Grupeje komandos is kitu zmoniu ignoruojamos – nieko neatskleidziam
            print(f"Ignoruota komanda grupeje ({m['name']}, ID {m['user']}): {m['text'][:40]}")
        return False

    def one_callback(self, cb):
        answer = commands.handle_callback(cb, self.state)
        print(f"Mygtukas ({cb['name']}): {cb['data']} -> {answer[:40]}")
        self.tg.answer_callback(cb["id"], answer)

    def status_of(self, source, uid, local_id, url=None):
        """Skelbimo busena, uzklausiant saltini ne daugiau nei karta per paleidima.

        Ta pati skelbima tikrina dvi dalys: „pardavimu patikra“ (rinkos kainoms) ir
        „pranesimu rezultatai“ (ar nupirkta). Anksciau kiekviena kraudavo puslapi
        atskirai – Vinted tai dvi uzklausos ir dvi pauzes tam paciam skelbimui."""
        with self.lock:
            if uid in self.statuses:
                return self.statuses[uid]
        st = source.status(local_id, url)
        with self.lock:
            self.statuses[uid] = st
        if getattr(source, "detail_needs_request", True):
            self.sleep(config.cfg["DETAIL_SLEEP_SECONDS"])
        return st

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

        found = {"sold": 0, "gone": 0, "qc": 0, "active": 0, "checked": 0}

        def check(source_name, items):
            source = by_name[source_name]
            for uid, local_id in items:
                if self.out_of_time():
                    break
                entry = self.state.market.get(uid) or {}
                st = self.status_of(source, uid, local_id, entry.get("u"))
                with self.lock:
                    self.state.market.set_status(uid, st)
                    found["checked"] += 1
                    if st in found:
                        found[st] += 1
                    if st == "sold" and entry.get("qc"):
                        found["qc"] += 1      # patvirtinta + zinomas lygis = mokymo pavyzdys

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
            print(f"Pardavimu patikra: patikrinta {found['checked']} – parduota {found['sold']} "
                  f"(is ju su patikimumo lygiu {found['qc']}), dingo {found['gone']}{extra}, "
                  f"vis dar parduodama {found['active']}")

    def track_results(self):
        """Ar skelbimai, apie kuriuos pranesem, buvo nupirkti – ir per kiek laiko.
        Pirmas 2 paras tikrinama kas paleidima, tad laikas tikslus ~10 min."""
        if not config.cfg["TRACK_RESULTS"]:
            return
        tracker = self.state.tracker
        tracker.expire()
        by_name = {s.name: s for s in self.sources}
        groups = {}
        for uid in tracker.due():
            source_name, local_id = split_uid(uid)
            if source_name in by_name:
                groups.setdefault(source_name, []).append((uid, local_id))
        if not groups:
            return
        found = {}

        def check(source_name, items):
            source = by_name[source_name]
            for uid, local_id in items:
                if self.out_of_time():
                    break
                st = self.status_of(source, uid, local_id, tracker.items.get(uid, {}).get("u"))
                with self.lock:
                    tracker.update(uid, st)
                    # Ta pati zinia naudinga ir rinkos kainoms: butent apie siuos skelbimus
                    # zinom, kiek spejom (savikalibracijai), o cia ju pardavima pamatom
                    # per minutes, ne po dvieju dienu, kai jie taps „pardavimu patikros“ eileje.
                    if st in ("sold", "gone"):
                        self.state.market.set_status(uid, st)
                    if st in ("sold", "reserved", "gone"):
                        found[st] = found.get(st, 0) + 1
                        e = tracker.items[uid]
                        print(f"  Rezultatas: {display(e.get('m'))} {e.get('p', 0):.0f} EUR – {st} "
                              f"po {(e['e'] - e['t']) / 60:.0f} min.")

        workers = [(n, items) for n, items in groups.items()]
        if config.cfg["PARALLEL_SOURCES"] and len(workers) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(workers)) as pool:
                futures = {pool.submit(check, n, items): n for n, items in workers}
                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        print(f"! Rezultatu patikra ({futures[future]}) nutruko: {e}")
        else:
            for n, items in workers:
                try:
                    check(n, items)
                except Exception as e:
                    print(f"! Rezultatu patikra ({n}) nutruko: {e}")
        checked = sum(len(i) for _, i in workers)
        print(f"Pranesimu rezultatai: patikrinta {checked}"
              + (", " + ", ".join(f"{k} {v}" for k, v in found.items()) if found else ""))

    def send_report(self):
        """Kas REPORT_EVERY_DAYS – ataskaita Telegram'e: kiek pranestu nupirkta ir per kiek."""
        days = config.cfg["REPORT_EVERY_DAYS"]
        if not days or not config.cfg["TRACK_RESULTS"]:
            return
        now = time.time()
        if not self.state.last_report:
            self.state.last_report = now          # laikrodis pradedamas – pirma ataskaita po savaites
            return
        if now - self.state.last_report < days * 86400:
            return
        self.state.last_report = now
        labels = {name: source_label(name) for name in config.cfg["SOURCES"]}
        self.tg.send_message(report_text(self.state.tracker, days=days, labels=labels))

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

    def wait_out_cooldown(self, source, has_work, queries_left=1):
        """Jei saltinis atvesinamas po 429 – palaukiam, o ne nurasom likusius gamintojus.

        Sauktis ir pries katalogo paieska, ir pries salies patikra: atvesinimo metu uzklausu
        nedarom, tad nepalaukes gamintojas tyliai liktu visai neperziuretas.

        Paleidimas paprastai trunka ~3 min. is 25 leidziamu, tad minute palaukti pigu.
        Alternatyva – palikti 2-aji, 3-iaji ir 4-aji gamintojus be salies patikros, o tada
        ju skelbimai neitraukiami nei i rinkos kaina, nei i pranesimus. Kiekviena paieska
        (gamintojas) gali palaukti po viena karta, bet tik kol lieka laiko paleidimui."""
        if not has_work or self.out_of_time():
            return
        key = (source.name, queries_left)
        with self.lock:
            if key in self.country_waited:
                return
        left = float(getattr(source, "country_cooldown_left", 0.0) or 0.0)
        if left <= 0:
            return
        limit = config.cfg["MAX_RUN_MINUTES"]
        spare = (limit * 60 - (time.time() - self.started)) if limit > 0 else left
        wait = min(left + 1, max(0.0, spare - 60))      # minute paliekam likusiam darbui
        if wait <= 0:
            return
        with self.lock:
            self.country_waited.add(key)
        print(f"  [{source.label}] Vinted riba – laukiu {wait:.0f}s, kad ir sis gamintojas "
              f"gautu salies patikra")
        self.sleep(wait)

    @staticmethod
    def _country_queue(todo, backfill, budget):
        """Kuria tvarka leisti salies uzklausas: nauji PIRMI, bet dalis rezervuojama
        laukiantiems.

        Iki v50 cia buvo paprastas `todo + backfill`, ir tai veike tol, kol nauju
        skelbimu buvo keliolika. Prijungus Android nauju atsirado simtais, tad
        laukianciuju eile nustojo judeti visai: gyvai matuota 2026-10-05 – is 100
        Android irasu 61 (61 %) stovejo „salis?" busenoje, o Apple, kurio pardaveju
        1146 jau atmintyje, – tik 2 %. Tie 61 nedalyvavo nei rinkos kainoje, nei
        „pigiausiu" palyginime, t. y. duomenys kaupesi, bet nesiskaiciavo.

        Rezervas nedidelis (COUNTRY_BACKFILL_SHARE), nes nauji skelbimai gali buti
        dealai SIANDIEN, o laukiantys tik papildo statistika. Bet rezervas butinas:
        be jo badavimas yra ne laikinas, o nuolatinis."""
        if budget <= 0:
            return list(todo) + list(backfill)
        share = float(config.cfg.get("COUNTRY_BACKFILL_SHARE") or 0)
        reserve = min(len(backfill), int(budget * share)) if share > 0 else 0
        first = max(0, budget - reserve)
        return list(todo[:first]) + list(backfill[:reserve]) + list(todo[first:]) \
            + list(backfill[reserve:])

    def country_share(self, source, limit, queries_left):
        """Kiek salies uzklausu tenka siai paieskai: likutis, padalytas is likusiu paiesku.

        Paskutine paieska gauna visa likuti, tad nepanaudotos uzklausos neprazuva."""
        with self.lock:
            left = max(0, limit - self.country_used.get(source.name, 0))
        if queries_left <= 1:
            return left
        return max(1, -(-left // queries_left)) if left else 0

    def resolve_countries(self, source, listings, queries_left=1):
        """Pardavejo salis – PRIES rinkos statistika.

        Kodel butent cia: Vinted katalogas (nuo 2026-09) salies nebeduoda, o ja pasako
        tik pardavejo uzklausa. Anksciau ji buvo daroma atidarant skelbima, t. y. JAU PO
        `market.observe()`. Gyvai patikrinta: is 45 „iphone“ paieskos telefonu 31 buvo
        lenku, 6 suomiu, 2 latviu ir tik 5 lietuviu. Uzsienio skelbimas, atmestas ankstyvame
        etape (pvz. „ne tarp pigiausiu“), iki pardavejo taip ir nepriejo ir likdavo istorijoje
        kaip Lietuvos kaina – o PL kainos sistemiskai zemesnes, tad Lietuvos rinkos kaina
        buvo nuvertinta, o „pigiausiu 15 %“ skaiciuojami lenku atzvilgiu.

        Uzklausu kiekis ribotas (`SELLER_COUNTRY_LOOKUPS`), tad tikrinami tik nauji
        telefonai, o pirmi eileje – apvalios kainos: perskaiciuota PLN kaina beveik visada
        su centais (692.64 €), o tikra lietuviska – apvali (550 €).

        `queries_left` – kiek paiesku (gamintoju) siam saltiniui dar liko. Riba yra VISO
        PALEIDIMO, o ne vienos paieskos: Vinted 429 atsako po ~45 uzklausu is eiles,
        nesvarbu, kiek paiesku jas isbarste. Kol riba buvo skaiciuojama kiekvienai paieskai
        atskirai, pirmieji gamintojai suvalgydavo visa kvota, o paskutiniams likdavo nulis –
        ju skelbimai likdavo „nepatikrinti“, iskrisdavo is rinkos ir buvo atmetami. Todel
        likutis dalijamas is likusiu paiesku, o nepanaudotas kiekis pereina kitai."""
        c = config.cfg
        if not c["FILTER_BY_COUNTRY"]:
            return
        allowed = set(c["ALLOWED_COUNTRY_CODES"])
        needs_request = bool(getattr(source, "country_needs_request", False))
        # Uzklausos isjungtos (SELLER_COUNTRY_LOOKUPS = 0): salis kaip ir anksciau paaiskes
        # atidarant skelbima, o rinkos istorija nefiltruojama – kitaip Vinted skelbimu i ja
        # nepatektu nei vieno.
        limit = int(c["SELLER_COUNTRY_LOOKUPS"])
        resolution_on = needs_request and limit > 0
        budget_share = self.country_share(source, limit, queries_left) if needs_request else -1
        budget = budget_share
        from_cache, asked, unknown = 0, 0, 0
        todo, backfill = [], []
        for listing in listings:
            if listing.skip_reason:
                continue
            title = listing.title or ""
            model = detect_model(title)
            if listing.price is None or not model or is_accessory(title):
                continue                       # ne telefonas – i rinkos kaina vis tiek nepateks
            if listing.price < max(40, min_price(model)):
                # Ta pati riba kaip market.observe(): dekliukas „Pixel 8“ uz 6 € ar ekranas
                # uz 30 € nei i rinkos kaina pateks, nei bus issiustas. Gyvai matuota: is 16
                # puslapiu tokiu buvo 91, ir kiekvienas suvalgydavo po salies uzklausa.
                continue
            if not model_wanted(model):
                continue                       # nesekamas modelis – uzklausos jam negaistam
            key = f"{listing.source}:{listing.seller_id}"
            known = ((listing.seller or {}).get("country")
                     or (self.state.seller_country(key) if listing.seller_id else None))
            if known:
                listing.seller = {**(listing.seller or {}), "country": known}
                from_cache += 1
                continue
            if listing.uid in self.new_seen:
                # Jau matytas, bet salies vis dar nezinom – rinkos kainoje jis neskaiciuojamas.
                # Tokie tikrinami po naujuju: jei uzklausu liko (o iprastame paleidime nauju
                # skelbimu vos keliolika), kiekvienas patikrintas grazina i rinka dar viena
                # tikra lietuviska kaina.
                if self.state.market.needs_country(listing.uid):
                    backfill.append(listing)
                continue
            todo.append(listing)
        if needs_request and not resolution_on:
            todo, backfill = [], []
        def cents_last(l):
            """Apvalios kainos – pirmos. Perskaiciuota PLN kaina beveik visada su centais
            (692.64 €), o lietuviska – apvali (550 €), tad ribotos uzklausos eina tiems,
            kurie labiau panasus i lietuviskus."""
            return abs(l.price - round(l.price)) > 0.004

        todo.sort(key=cents_last)
        backfill.sort(key=cents_last)
        self.wait_out_cooldown(source, bool(todo), queries_left)
        for listing in self._country_queue(todo, backfill, budget):
            if budget == 0 or source.country_lookups_blocked or self.out_of_time():
                with self.lock:
                    self.country_limited.add(source.name)   # laikina: kitas paleidimas patikrins
                break
            country = source.seller_country(listing)
            if needs_request:
                budget -= 1
                asked += 1
                self.sleep(c["DETAIL_SLEEP_SECONDS"])
            if not country:
                unknown += 1
                if source.country_lookups_blocked:
                    with self.lock:
                        self.country_limited.add(source.name)
                continue
            listing.seller = {**(listing.seller or {}), "country": country}
            if listing.seller_id:
                with self.lock:
                    self.state.remember_seller(f"{listing.source}:{listing.seller_id}", country)
        if asked:
            with self.lock:
                self.country_used[source.name] = self.country_used.get(source.name, 0) + asked
        for listing in listings:
            country = (listing.seller or {}).get("country")
            if country and country not in allowed and not listing.skip_reason:
                listing.skip_reason = f"salis ({country})"
            elif not country and resolution_on and not listing.skip_reason:
                # Salies nezinom – i rinkos kaina neitraukiam (zr. market.observe)
                listing.country_unverified = True
        if asked or from_cache or todo:
            left = sum(1 for l in todo if not (l.seller or {}).get("country"))
            parts = [f"{asked} uzklausu", f"{from_cache} is atminties"]
            if unknown:
                parts.append(f"{unknown} be atsakymo")
            if left > unknown:
                with self.lock:
                    used = self.country_used.get(source.name, 0)
                parts.append(f"{left - unknown} nepatikrinta (siai paieskai {budget_share}, "
                             f"paleidime {used}/{limit})")
            print("  Pardavejo salys: " + ", ".join(parts))

    def scan(self, source, seen, pages):
        """Viena saltinio perziura. Grazina, kiek skelbimu gauta.

        Saltinio klaida nenutraukia nei kitu saltiniu, nei viso paleidimo (anksciau tai
        galiojo tik lygiagreciam rezimui). Rezultatas irasomas i source_stats – pagal ji
        pranesama, kai saltinis neveikia."""
        fetched, crash = 0, ""
        # Kuriam saltiniui priskirti skaitiklius – zr. diag_of. Lygiagreciame
        # rezime kiekvienas saltinis sukasi savo gijoje, tad reiksme ju nemaiso.
        self._local.source_name = source.name
        with self.lock:
            self.diag_of(source.name)        # irasas atsiranda net jei viskas luzo
        try:
            fetched = self._scan(source, seen, pages)
        except Exception as e:
            crash = f"klaida kode: {type(e).__name__}: {e}"
            self.last_error = f"{source.label}: {crash}"
            print(f"! {source.label} nutruko: {crash}")
            print(traceback.format_exc())
        with self.lock:
            self.source_stats[source.name] = {
                "fetched": fetched, "crash": crash,
                "error": crash or source.unavailable or getattr(source, "last_error", "") or ""}
        return fetched

    def _scan(self, source, seen, pages):
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
        for done, q in enumerate(queries):
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
            if self.alert_limit_reached():
                break
            print(f"Tikrinama [{source.label}]: {source.describe(q)}...")
            # Jei galioja 429 atvesinimas – palaukiam PRIES paieska. Atvesinimo metu uzklausu
            # nedarom visai, tad be sio laukimo sio gamintojo katalogas butu praleistas,
            # o ne atidetas (lygiai ta pati bėda, kuria salies patikrai sutvarke v44).
            self.wait_out_cooldown(source, True, queries_left=len(queries) - done)
            listings = source.search(q, pages, seen)
            self.last_error = source.last_error or self.last_error
            fetched += len(listings)
            with self.lock:
                self.diag_of(source.name)["fetched"] += len(listings)
            self.mark_foreign(listings)
            # Salis butina zinoti PRIES observe(): uzsienio kainos neturi patekti
            # nei i Lietuvos rinkos kaina, nei i „pigiausiu“ palyginima.
            self.step(f"Pardavėjų šalys ({source.label})",
                      lambda: self.resolve_countries(source, listings,
                                                     queries_left=len(queries) - done))
            drops = self.state.market.observe(listings)
            self.examples, sent_before = [], len(self.alerts)
            for done_here, listing in enumerate(listings):
                if self.out_of_time():
                    break
                if self.alert_limit_reached():
                    # Kiek sio saltinio skelbimu liko neivertintu – kad pranesime butu
                    # skaicius, o ne „liko dar kazkiek“ (zr. report_alert_limit).
                    with self.lock:
                        self.limit_skipped += len(listings) - done_here
                    break
                deal = self.evaluate(source, listing, drops)
                if not deal:
                    continue
                # Siuntimas ir irasymas – po vieną: kitaip lygiagretus saltiniai
                # persidengtu Telegram zinutemis ir pustuciais failais.
                with self.lock:
                    rank = deal.get("rank")
                    # Pigiausiu budu garsiai – tik pats pigiausias (nuolaida cia remiasi
                    # ta pacia spejama verte, kuria ir nepasitikim)
                    silent = (rank.place != 1) if rank is not None \
                        else deal["discount"] < c["LOUD_DISCOUNT"]
                    if not self.tg.send_deal(deal, silent=silent):
                        # Telegram neatsake (tinklo klaida, blokas). Anksciau dealas vis tiek
                        # buvo pazymimas matytu ir „pranestu“, tad geras pasiulymas dingdavo
                        # visam laikui. Dabar zymes nusiimam – kitas paleidimas bandys dar karta.
                        self.new_seen.pop(deal["id"], None)
                        self.new_seen.pop(deal.get("fp"), None)
                        self.reject("nepavyko išsiųsti – bandysiu vėliau", deal["title"][:45])
                        print(f"  ! [{source.label}] nepavyko issiusti {display(deal['model'])} "
                              f"{deal['price']:.0f} EUR – bandysiu kitame paleidime")
                        continue
                    self.alerts.append(deal)
                    self.diag_of(source.name)["sent"] += 1
                    self.state.market.mark_alerted(deal["id"], deal["price"])
                    if c["TRACK_RESULTS"]:
                        self.state.tracker.add(deal)
                    self.send_personal(deal)
                    tag = f"atpigo nuo {deal['drop_from']:.0f}, " if deal["drop_from"] else ""
                    kiek = (f"{rank.place}-as pigiausias is {rank.n}" if rank is not None
                            else f"-{deal['discount']:.0%}")
                    print(f"  -> [{source.label}] {display(deal['model'])} {deal['price']:.0f} EUR "
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

    def print_diagnostics(self):
        """Kodel sis paleidimas baigesi butent taip – pagal saltini.

        Trys visiskai skirtingos situacijos log'e atrodydavo vienodai, nes
        suvestine maisydavo visus saltinius i viena krūva:

          saltinis sulūzo    gauta 0, yra klaida -> taisyti kliento koda ar IP
          filtrai atmeta     gauta daug, issiusta 0 -> ziureti, KURI priezastis
          nieko naujo nebuvo gauta daug, nauju 0  -> viskas gerai, nieko nedaryti

        Pirma ir trecia tylejo vienodai. Butent del to 2026-10-05 „61 % be
        salies" buvo palaikyta badejimu, nors is tiesu filtras veike teisingai.
        Grazina teksta (ir ji atspausdina), kad ta pati suvestine tiktu ir
        heartbeat zinutei."""
        lines = []
        for source in self.sources:
            d = self.diag.get(source.name)
            if d is None:
                continue                      # siame paleidime netikrintas
            rejects = sorted(d["rejects"].items(), key=lambda kv: -kv[1])
            top = ", ".join(f"{why} {n}" for why, n in rejects[:4])
            if len(rejects) > 4:
                top += f", kita {sum(n for _, n in rejects[4:])}"
            lines.append(f"  {source.label}: gauta {d['fetched']}, naujų {d['new']}, "
                         f"išsiųsta {d['sent']}")
            if top:
                lines.append(f"      atmesta – {top}")
            why = (self.source_stats.get(source.name) or {}).get("error")
            if why:
                lines.append(f"      ! {why[:120]}")
            elif d["fetched"] == 0:
                lines.append("      ! negauta nė vieno skelbimo, bet klaidos nėra – "
                             "gali būti pasikeitęs atsakymo formatas")
            elif d["new"] == 0:
                lines.append("      (naujų nebuvo – tai normalu, ne gedimas)")
        errors = getattr(self.tg, "errors", None) or {}
        tg_line = f"  Telegram: išsiųsta {getattr(self.tg, 'sent', 0)}"
        if errors:
            tg_line += ", klaidos – " + ", ".join(f"{k} {n}" for k, n in sorted(errors.items()))
        else:
            tg_line += ", klaidų nėra"
        lines.append(tg_line)
        text = "DIAGNOSTIKA\n" + "\n".join(lines)
        print(text)
        return text

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
                    fetched += future.result()          # scan() pats sugauna klaidas
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
        self.apply_learned_rate()

        self.new_seen = dict(seen)
        self.new_seen.pop("__heartbeat__", None)
        self.new_count = 0
        fetched = 0
        try:
            self.report_config_error()
            self.step("Telegram komandos", self.process_commands)

            pages = c["PAGES"] if seen else max(c["PAGES"], c["FULL_SCAN_PAGES"])
            if not seen:
                print(f"seen.json tuscias – pilnas perziurejimas ({pages} psl. kiekvienai paieskai)")

            fetched = self.scan_all(seen, pages)

            queries = config.cfg["SEARCH_QUERIES"]
            if c["ROTATE_QUERIES"] and queries:
                start = self.state.query_offset % len(queries)
                self.state.query_offset = (start + max(1, len(queries) // 3)) % len(queries)

            # Papildomi darbai: vieno klaida neturi sustabdyti kitu nei issaugojimo
            self.step("Pranesimu rezultatai", self.track_results)
            self.step("Ataskaita", self.send_report)
            if c["USE_SOLD_PRICES"]:
                self.step("Pardavimu patikra", self.check_sold)
            self.step("Saltiniu busena", self.update_source_health)
            self.step("Kalibravimas", self.calibrate)
            self.step("Rankines kainos", self.check_manual_prices)
            self.step("Rinkos kainos", self.print_market)
            summary = ", ".join(f"{k}: {n}" for k, n in sorted(self.totals.items(), key=lambda kv: -kv[1]))
            print(f"IS VISO: gauta {fetched}, tinkama {len(self.alerts)}. Atmesta – {summary}")
            self.step("Diagnostika", self.print_diagnostics)
            requests_made = self.step("Uzklausu statistika", self.request_report) or {}
            self.step("Greicio ribos mokymasis", self.learn_rate_limit)
            self.step("Pranesimu ribos pranesimas", self.report_alert_limit)
            self.step("Heartbeat", lambda: self.heartbeat(fetched, summary))
            self.state.last_run = {"time": int(time.time()), "fetched": fetched, "new": self.new_count,
                                   "sent": len(self.alerts), "totals": self.totals,
                                   "requests": requests_made,
                                   "sources": {k: v["fetched"] for k, v in self.source_stats.items()},
                                   # Pagal saltini – kad kito paleidimo metu butu su kuo palyginti
                                   # („vakar vinted atmesdavo 380 kaip jau matytus, siandien 0")
                                   "diag": self.diag,
                                   "telegram": {"sent": getattr(self.tg, "sent", 0),
                                                "errors": getattr(self.tg, "errors", None) or {}}}
            if fetched == 0:
                # Ispejimai siunciami kiekvienam saltiniui atskirai (update_source_health)
                self.state.fail_streak += 1
                print(f"! Negauta skelbimu ({self.state.fail_streak} paleidimas is eiles): {self.last_error}")
            else:
                self.state.fail_streak = 0
            self.step("Klaidu pranesimas", self.report_step_errors)
        finally:
            # Archyvas – pirmiau busenos: jei paleidimas nuluzo, jau irasyti stebejimai
            # vis tiek tikri, o be ju toje vietoje archyve liktu skyle.
            self.save_archive()
            # Busena issaugoma VISADA – net jei kazkas nuluzo. Kitaip kitas paleidimas
            # is naujo atidarinetu tuos pacius skelbimus.
            self.state.save()
            save_seen(self.new_seen)

        print(f"Issiusta {len(self.alerts)} alert'u." if self.alerts else "Nauju deal'u nera.")

    def save_archive(self):
        """Paleidimo ivykiai -> ilgalaikis archyvas (zr. vinted/archive.py). Archyvo klaida
        nesustabdo busenos issaugojimo – ji tik pranesama."""
        try:
            n = archive.append(self.state.market.drain_events())
            if n:
                print(f"Archyvas: +{n} ivykiu ({config.ARCHIVE_DIR})")
        except Exception as e:
            self.step_errors.append(f"Archyvas: {type(e).__name__}: {e}")
            print(f"! Archyvo irasyti nepavyko: {e}")

    def step(self, name, fn):
        """Vykdo papildoma darba; klaida uzrasoma, bet paleidimas tesiamas."""
        try:
            return fn()
        except Exception as e:
            self.step_errors.append(f"{name}: {type(e).__name__}: {e}")
            print(f"! {name} nepavyko: {e}")
            print(traceback.format_exc())
            return None

    def report_step_errors(self):
        """Nuluzusios papildomos dalys – pranesam, bet ne dazniau nei kas valanda."""
        if not self.step_errors:
            return
        now = time.time()
        if now - float(self.state.source_alerts.get("__steps__") or 0) < 3600:
            return
        self.state.source_alerts["__steps__"] = now
        text = "\n".join(html.escape(e[:200]) for e in self.step_errors[:5])
        self.tg.send_admin(f"⚠️ <b>Dalis darbų nepavyko</b> (skelbimai tikrinami toliau)\n<code>{text}</code>",
                             silent=True)

    def report_config_error(self):
        """Sugadintas config.json (pvz. dingo kablelis redaguojant GitHub'e) – botas veikia
        su numatytaisiais nustatymais, bet apie tai pranesa (ne dazniau nei kas valanda)."""
        if not config.load_error:
            return
        now = time.time()
        if now - float(self.state.source_alerts.get("__config__") or 0) < 3600:
            return
        self.state.source_alerts["__config__"] = now
        self.tg.send_admin(
            "⚠️ <b>config.json sugadintas</b> – kol kas naudoju numatytuosius nustatymus.\n"
            f"Klaida: <code>{html.escape(config.load_error[:200])}</code>\n"
            "<i>Dažniausiai trūksta kablelio eilutės gale arba kabutės. "
            "Eilutės numeris nurodytas klaidoje (line …).</i>")

    def update_source_health(self):
        """Kiekvieno saltinio busena: pranesam, kai neveikia, ir kai vel atsigauna.

        Anksciau Vinted blokavimas likdavo nepastebetas, kol Pirkpard grazindavo bent kelis
        skelbimus, o kai neveikdavo viskas, ispejimas kartodavosi kas 3 paleidimus (~48 per
        para). Dabar: aiskus blokas (Cloudflare, klaida kode) – pranesam is karto;
        0 skelbimu – po FAIL_ALERT_RUNS paleidimu is eiles; kartojam ne dazniau nei kas
        SOURCE_ALERT_HOURS; atsigavus – trumpa zinute."""
        c = config.cfg
        now = time.time()
        hours = c["SOURCE_ALERT_HOURS"]
        blocked = [s for s in self.sources if s.unavailable]
        if blocked:
            print("! Nepasiekiami saltiniai: "
                  + ", ".join(f"{s.label} ({s.unavailable})" for s in blocked))
        for source in self.sources:
            st = self.source_stats.get(source.name)
            if st is None:
                continue                     # siame paleidime netikrintas (pvz. baigesi laikas)
            name = source.name
            if st["fetched"] > 0:
                self.state.source_zero.pop(name, None)
                since = self.state.source_down.pop(name, None)
                if since:
                    self.state.source_alerts.pop(name, None)
                    down_h = max(1, round((now - float(since)) / 3600))
                    self.tg.send_admin(f"✅ <b>{html.escape(source.label)} vėl veikia</b> "
                                         f"(neveikė ~{down_h} val.)", silent=True)
                continue
            streak = int(self.state.source_zero.get(name) or 0) + 1
            self.state.source_zero[name] = streak
            reason = source.unavailable or st.get("crash") or ""
            if not reason and streak < c["FAIL_ALERT_RUNS"]:
                continue                     # vienkartinis 403 – laikinas; pranesam, jei kartojasi
            self.state.source_down.setdefault(name, now)
            last = float(self.state.source_alerts.get(name) or 0)
            if hours > 0 and now - last < hours * 3600:
                continue
            self.state.source_alerts[name] = now
            if reason:
                self.tg.send_admin(
                    f"⚠️ <b>{html.escape(source.label)} nepasiekiamas</b>\n"
                    f"Priežastis: {html.escape(reason[:300])}\n"
                    f"Kiti šaltiniai veikia toliau. Jei kartojasi – gali reikėti paleisti "
                    f"iš kito IP (ne GitHub serverio).", silent=True)
            else:
                self.tg.send_admin(
                    f"⚠️ <b>ISPEJIMAS</b>: {html.escape(source.label)} – {streak} paleidimus iš eilės "
                    "negauta nė vieno skelbimo.\nGalimai pasikeitė API arba šaltinis blokuoja – "
                    f"patikrink logus.\nPriežastis: <code>{html.escape((st.get('error') or 'nežinoma')[:300])}</code>")

    def check_manual_prices(self, now=None):
        """Rankine kaina, smarkiai nesutampanti su rinka, tyliai isjungia dealus: pvz. 180 €
        visiems iPhone 13 reiske, kad per pelno filtra praeidavo tik pigesni nei ~150 €.
        Pranesam ne dazniau nei karta per para kiekvienai kainai."""
        manual = config.market_prices()
        now = now if now is not None else time.time()
        for key, price in manual.items():
            model, _, storage = key.partition("|")
            data = self.state.market.quote(model, storage or None, manual=False)
            if data is None or data.source not in ("parduoti", "skelbimai"):
                continue                       # duomenu per mazai – palyginti nera su kuo
            if abs(price / data.price - 1) < 0.25:
                continue
            print(f"! Rankinė kaina {display(model)}: {price:.0f} € – rinka rodo ~{data.price:.0f} € "
                  f"({data.source}, {data.samples})")
            alert_key = f"__manual__{key}"
            if now - float(self.state.source_alerts.get(alert_key) or 0) < 86400:
                continue
            self.state.source_alerts[alert_key] = now
            name = display(model) + (f" {storage}" if storage else " (visos talpos)")
            cmd = f"/kaina {model}" + (f" {storage.replace(' GB', '').replace(' ', '')}" if storage else "")
            kiek = f"{data.samples} {'parduotų' if data.source == 'parduoti' else 'skelb.'}"
            self.tg.send_admin(
                f"⚠️ <b>Rankinė kaina gal pasenusi</b>: {html.escape(name)} = {price:.0f} €, "
                f"o rinka rodo ~{data.price:.0f} € ({kiek}).\n"
                f"Kol taip, pelno filtras gali atmesti visus šio modelio pasiūlymus.\n"
                f"Grąžinti automatinę kainą: <code>{html.escape(cmd)} trinti</code>", silent=True)

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
                print(f"    {display(model):<20} {n:>3} parduoti, realiai {ratio:.0%} musu vertinimo")
        changed = market.calibrate()
        if changed:
            print(f"Pataisymas atnaujintas: x{changed['old']:.3f} -> x{changed['new']:.3f} "
                  f"(taikinys x{changed['wanted']:.3f} pagal {changed['n_target']} parduotus)")
        elif config.cfg["AUTO_CALIBRATE"]:
            truksta = config.cfg["MIN_CALIBRATION_SAMPLES"] - acc["n_target"]
            if truksta > 0:
                print(f"  (savikalibracijai dar reikia {truksta} parduotu telefonu)")
        bands = market.learn_confidence()
        if bands is not None:
            # Kartą per dieną – kad būtų matyti, ar modelis jau išmoktas, ar dar pradinis.
            need = int(config.cfg["CONFIDENCE_MIN_SAMPLES"])
            have = confidence_progress(market.items)
            parts = []
            for code, name in CONFIDENCE_LEVELS.items():
                if code in bands:
                    parts.append(f"{name} ismoktas x{bands[code]['factor']:.2f} ({bands[code]['n']} patv.)")
                else:
                    parts.append(f"{name} pradinis x{CONFIDENCE_PRIOR[code]['factor']:.2f} "
                                 f"({have[code]}/{need} patv.)")
            print("Patikimumas: " + ", ".join(parts))
        fact = market.learn_sale_fact()
        if fact is not None:
            print(describe_sale_fact(fact))

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
            print(f"  {display(model):<20} rankinė: {manual.get(model, 0):>4.0f}  "
                  f"parduoti: {s or 0:>4.0f} ({ns})  įvertinta: {a or 0:>4.0f} ({n})  "
                  f"-> naudojama: {q.price if q else 0:>4.0f} ({kodel})")

    def heartbeat(self, fetched, summary):
        hours = config.cfg["HEARTBEAT_HOURS"]
        if hours <= 0 or time.time() - self.state.heartbeat < hours * 3600:
            return
        sources = ", ".join(f"{s.label} {self.source_stats.get(s.name, {}).get('fetched', 0)}"
                            for s in self.sources)
        self.tg.send_admin("✅ <b>Skriptas veikia</b>\n"
                             f"Šaltiniai: {html.escape(sources)}\n"
                             f"Šis paleidimas: gauta {fetched} skelb., naujų {self.new_count}, "
                             f"išsiųsta {len(self.alerts)}.\n"
                             f"<i>Atmesta – {html.escape(summary or 'nieko')}</i>", silent=True)
        self.state.heartbeat = time.time()


def main():
    """Grazina proceso isejimo koda: 0 – gerai, 1 – nuluzo, 2 – nenurodyti BOT_TOKEN / CHAT_ID.
    Ne nulis GitHub'e rodomas raudonai, ir GitHub pats atsiuncia laiska apie klaida."""
    config.load()
    if not config.BOT_TOKEN or not config.CHAT_ID:
        print("Nenurodyti BOT_TOKEN / CHAT_ID! GitHub'e jie paduodami is Secrets BOT ir TEL – "
              "patikrink, ar tokie Secrets yra ir ar .github/workflows/vinted.yml juos naudoja.")
        return 2
    tg = Telegram()
    run = None
    try:
        run = Run(build_sources(), tg)
        run.run()
        return 0
    except Exception as e:
        print(traceback.format_exc())
        notify_crash(tg, run, e)
        return 1


def notify_crash(tg, run, error, now=None):
    """Pranesimas apie luzima – ne dazniau nei kas valanda (kitaip kas 10 min.)."""
    now = now if now is not None else time.time()
    state = getattr(run, "state", None)
    if state is not None:
        last = float(state.source_alerts.get("__crash__") or 0)
        if now - last < 3600:
            print(f"(apie klaida jau pranesta pries {int((now - last) // 60)} min.)")
            return
        state.source_alerts["__crash__"] = now
        try:
            state.save()
        except Exception as e:                       # pragma: no cover
            print(f"! Nepavyko issaugoti busenos: {e}")
    frames = traceback.extract_tb(error.__traceback__)
    where = f"{os.path.basename(frames[-1].filename)}:{frames[-1].lineno}" if frames else "?"
    send = getattr(tg, "send_admin", tg.send_message)
    send("<b>SKRIPTAS UZLUZO</b>\n"
         f"<code>{html.escape(type(error).__name__)}: {html.escape(str(error)[:300])}</code>\n"
         f"Vieta: {html.escape(where)}\n<i>Kitas pranešimas apie klaidą – ne anksčiau nei po valandos.</i>")
