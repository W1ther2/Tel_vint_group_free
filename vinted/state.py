# -*- coding: utf-8 -*-
"""Busena tarp paleidimu: seen.json (matyti skelbimai) ir state.json (visa kita)."""

import hashlib
import json
import os
import time

from . import config
from .market import Market
from .tracker import Tracker


def write_json(path, data, **dump_args):
    """Irasymas, kurio negali sugadinti nutrauktas paleidimas.

    Anksciau buvo rasoma tiesiai i state.json. Jei GitHub tuo metu nutraukia darba
    (laiko limitas, cancel, runner'io perkrovimas), failas likdavo pusiau irasytas,
    o kitas paleidimas sakydavo „sugadintas – pradedama nuo tuscio“: dingdavo visa
    rinkos istorija (iki 12000 skelbimu), kalibracija ir sekami pranesimai.

    Dabar rasoma i .tmp, tada `os.replace` – jis arba ivyksta visas, arba neivyksta
    visai. Ankstesne versija paliekama .bak (papildoma atsarga)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, **dump_args)
        f.flush()
        os.fsync(f.fileno())
    if os.path.exists(path):
        try:
            os.replace(path, path + ".bak")
        except OSError as e:                      # pragma: no cover – reta failu sistemos klaida
            print(f"! Nepavyko issaugoti atsargines {path}.bak kopijos ({e})")
    os.replace(tmp, path)


def read_json(path):
    """JSON is `path`, o jei jo nera ar jis sugadintas – is .bak kopijos.

    Grazina (duomenys, klaida). Nei vieno nepavykus – (None, klaida); klaida tuscia,
    kai failo tiesiog dar nera (pirmas paleidimas – tai ne problema)."""
    problem = ""
    for candidate in (path, path + ".bak"):
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                content = f.read().strip()
            data = json.loads(content) if content else {}
        except FileNotFoundError:
            continue
        except Exception as e:
            problem = problem or f"{candidate} sugadintas ({e})"
            continue
        if candidate != path:
            print(f"! {problem or path + ' nerastas'} – naudojama atsargine kopija {candidate}")
        return data, ""
    return None, problem


def seen_key(key):
    """Seni irasai ('123') -> 'vinted:123'. Fingerprintai ('fp:...') nekeiciami."""
    k = str(key)
    return k if ":" in k or k.startswith("__") else "vinted:" + k


def load_seen(path=None):
    path = path or config.SEEN_FILE
    data, problem = read_json(path)
    if data is None:
        if problem:
            print(f"! {problem} – pradedama nuo tuscio.")
        return {}
    now = time.time()
    if isinstance(data, list):
        return {seen_key(x): now for x in data}
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            try:
                out[seen_key(k)] = float(v)
            except (TypeError, ValueError):
                out[seen_key(k)] = now
    return out


def save_seen(seen, path=None):
    path = path or config.SEEN_FILE
    c = config.cfg
    now = time.time()
    # Kopija vienu zingsniu: kol vienas saltinis issaugo, kitas (kitoje gijoje) gali
    # prideti nauju irasu – iteruojant tiesiai zodyna tai baigdavosi
    # „dictionary changed size during iteration“ ir nutraukdavo to saltinio patikra.
    seen = dict(seen)
    fresh = {k: v for k, v in seen.items()
             if not k.startswith("__") and now - v <= c["SEEN_MAX_AGE_DAYS"] * 86400}
    newest = sorted(fresh.items(), key=lambda kv: kv[1], reverse=True)[: c["SEEN_MAX_ENTRIES"]]
    write_json(path, dict(newest))


class State:
    """state.json: {"market": {...}, "telegram_offset": 0, "overrides": {}, "heartbeat": 0}"""

    # Padidinus – sena rinkos istorija isvaloma (pvz. kai pakeiciamos atrankos taisykles)
    MARKET_VERSION = 3

    def __init__(self, data=None):
        data = data or {}
        if data.get("market") and data.get("market_version", 1) < self.MARKET_VERSION:
            print("Rinkos kainu istorija isvalyta (nauja atranka: be sugedusiu ir priedu) – kaupsis is naujo.")
            data = {**data, "market": None}
        self.market = Market(data.get("market"))
        self.telegram_offset = int(data.get("telegram_offset") or 0)
        self.overrides = data.get("overrides") or {}
        self.heartbeat = float(data.get("heartbeat") or 0)
        self.last_run = data.get("last_run") or {}
        self.fail_streak = int(data.get("fail_streak") or 0)
        self.query_offset = int(data.get("query_offset") or 0)
        # {vartotojo_id: {"chat": privataus pokalbio id, "name": vardas,
        #                 "watch": [modeliai], "hide": [pardaveju id]}}
        self.users = data.get("users") or {}
        # {saltinio vardas: kada paskutini karta pranesta apie blokavima}
        self.source_alerts = data.get("source_alerts") or {}
        # {saltinio vardas: kiek paleidimu is eiles negauta nei vieno skelbimo}
        self.source_zero = data.get("source_zero") or {}
        # {saltinio vardas: nuo kada neveikia} – kad atsigavus galetume pranesti
        self.source_down = data.get("source_down") or {}
        # Pranesimu rezultatai (ar nupirkta ir per kiek) – zr. tracker.py
        self.tracker = Tracker(data.get("tracked"))
        self.last_report = float(data.get("last_report") or 0)
        # {uid: {"n": kiek kartu nepavyko atidaryti skelbimo, "t": kada paskutini karta}}
        self.detail_failures = data.get("detail_failures") or {}
        # {"vinted:123": ["PL", diena]} – pardavejo salis. Naujasis Vinted katalogo API
        # salies nebeduoda (tik business/id/login), tad ji atskirai uzklausiama vienai
        # pardavejo paskyrai. Kesas reiskia, kad tas pats pardavejas su 20 skelbimu
        # kainuoja viena uzklausa, o ne 20.
        self.sellers = data.get("sellers") or {}
        self._migrate_seller_keys()
        # {saltinis: {"per_minute": 31.5, "updated": ts, "hits": kiek 429 buvo}} – issimokta
        # uzklausu greicio riba. GitHub Actions runner'iu IP bendri, tad tikroji riba kinta:
        # zr. finder.Run.learn_rate_limit (po 429 mazinam, po tyliu paleidimu grazinam).
        self.rate_limit = data.get("rate_limit") or {}

    SELLER_MAX_ENTRIES = 20000

    @staticmethod
    def seller_key(key):
        """Pardavejo raktas – maisos kodas, ne pats ID.

        Kesui reikia tik atpazinti TA PATI pardaveja, o ne zinoti, kas jis. Tad
        saugom `sha1(raktas)`, kaip jau daroma rinkos istorijoje (`sh`). Taip
        is busenos, kuri GitHub'e gula i viesa `busena` saka, dingsta 1200+
        tikru Vinted vartotoju ID. Paieska veikia taip pat: ieskodami
        perskaiciuojam ta pati maisos koda."""
        return hashlib.sha1(str(key).encode()).hexdigest()[:16]

    def _migrate_seller_keys(self):
        """Seni irasai buvo raktuoti tikru ID – perrasom i maisos kodus.

        Be sito perejimas butu kainaves visa kesa (1285 irasai) ir sukeles
        uzklausu banga: salis butu atsistacius, bet per ~11 paleidimu."""
        out, moved = {}, 0
        for k, v in self.sellers.items():
            if len(str(k)) == 16 and all(ch in "0123456789abcdef" for ch in str(k)):
                out[k] = v                      # jau maisos kodas
            else:
                out[self.seller_key(k)] = v
                moved += 1
        if moved:
            print(f"Pardaveju kesas: {moved} raktu perrasyta i maisos kodus")
        self.sellers = out

    def seller_country(self, key):
        """Isimtas pardavejo salies kodas arba None."""
        e = self.sellers.get(self.seller_key(key))
        if isinstance(e, (list, tuple)) and e:
            return e[0] or None
        return e or None if isinstance(e, str) else None

    def remember_seller(self, key, country, day=None):
        if key and country:
            self.sellers[self.seller_key(key)] = [
                str(country), int(day if day is not None else time.time() // 86400)]

    def note_detail_failure(self, uid, now=None):
        """Dar vienas nepavykes skelbimo atidarymas. Grazina, kiek kartu is viso."""
        e = self.detail_failures.get(uid) or {}
        e = {"n": int(e.get("n") or 0) + 1, "t": int(now if now is not None else time.time())}
        self.detail_failures[uid] = e
        return e["n"]

    def forget_detail_failure(self, uid):
        self.detail_failures.pop(uid, None)

    @classmethod
    def load(cls, path=None, seen=None):
        path = path or config.STATE_FILE
        data, problem = read_json(path)
        if data is None and problem:
            print(f"! {problem} – pradedama nuo tuscio.")
        try:
            state = cls(data if isinstance(data, dict) else None)
        except Exception as e:
            print(f"! {path} turinys netinkamas ({e}) – pradedama nuo tuscio.")
            state = cls()
        if seen and "__heartbeat__" in seen and not state.heartbeat:
            state.heartbeat = seen["__heartbeat__"]
        return state

    def user(self, user_id, name=""):
        """Vartotojo irasas. VARDAS NESAUGOMAS.

        Jis buvo rasomas, bet niekur neskaitomas – gryna asmens duomenu ekspozicija
        be jokios funkcijos. Svarbu todel, kad busena GitHub'e gula i vieša `busena`
        saka. Privataus pokalbio ID be jo isversti neimanoma (be jo nera kur siusti),
        tad jis lieka – ir butent del to DM funkcijos neverta plesti, kol busena
        nepersikele i serveri."""
        u = self.users.setdefault(str(user_id), {"watch": [], "hide": []})
        u.pop("name", None)            # isvalom ir senus irasus
        u.setdefault("watch", [])
        u.setdefault("hide", [])
        return u

    def save(self, path=None):
        path = path or config.STATE_FILE
        # Spyna laikoma per visa irasyma: kitas saltinis tuo metu gali rasyti naujus
        # skelbimus, o json.dump ju zodyno keisti nebegali.
        with self.market.lock:
            return self._save(path)

    def _save(self, path):
        self.market.prune()
        week_ago = time.time() - 7 * 86400
        self.detail_failures = {k: v for k, v in dict(self.detail_failures).items()
                                if isinstance(v, dict) and v.get("t", 0) >= week_ago}
        if len(self.sellers) > self.SELLER_MAX_ENTRIES:
            # Pardavejo salis nesikeicia, tad senumas nesvarbus – tik dydis.
            newest = sorted(dict(self.sellers).items(),
                            key=lambda kv: (kv[1][1] if isinstance(kv[1], (list, tuple)) and len(kv[1]) > 1 else 0),
                            reverse=True)
            self.sellers = dict(newest[: self.SELLER_MAX_ENTRIES])
        data = {"market_version": self.MARKET_VERSION, "market": self.market.to_dict(),
                "telegram_offset": self.telegram_offset,
                "overrides": self.overrides, "heartbeat": self.heartbeat, "last_run": self.last_run,
                "rate_limit": self.rate_limit,
                "fail_streak": self.fail_streak, "query_offset": self.query_offset,
                "users": self.users, "source_alerts": self.source_alerts,
                "source_zero": self.source_zero, "source_down": self.source_down,
                "tracked": self.tracker.to_dict(), "last_report": self.last_report,
                "detail_failures": self.detail_failures, "sellers": self.sellers}
        write_json(path, data, ensure_ascii=False, separators=(",", ":"))
