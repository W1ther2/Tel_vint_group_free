# -*- coding: utf-8 -*-
"""Busena tarp paleidimu: seen.json (matyti skelbimai) ir state.json (visa kita)."""

import json
import os
import time

from . import config
from .market import Market, migrate_old_prices


def load_seen(path=None):
    path = path or config.SEEN_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        data = json.loads(content) if content else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"! {path} sugadintas ({e}) – pradedama nuo tuscio.")
        return {}
    now = time.time()
    if isinstance(data, list):
        return {str(x): now for x in data}
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                out[str(k)] = now
    return out


def save_seen(seen, path=None):
    path = path or config.SEEN_FILE
    c = config.cfg
    now = time.time()
    fresh = {k: v for k, v in seen.items()
             if not k.startswith("__") and now - v <= c["SEEN_MAX_AGE_DAYS"] * 86400}
    newest = sorted(fresh.items(), key=lambda kv: kv[1], reverse=True)[: c["SEEN_MAX_ENTRIES"]]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict(newest), f)


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

    @classmethod
    def load(cls, path=None, old_prices_path=None, seen=None):
        path = path or config.STATE_FILE
        old_prices_path = old_prices_path or config.OLD_PRICES_FILE
        state = None
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = cls(json.load(f))
        except FileNotFoundError:
            state = cls()
            if False and os.path.exists(old_prices_path):   # sena istorija uztersta sugedusiais – nenaudojam
                try:
                    with open(old_prices_path, "r", encoding="utf-8") as f:
                        state.market = migrate_old_prices(json.load(f))
                    print(f"Perkelta kainu istorija is {old_prices_path} ({len(state.market.items)} skelb.)")
                except Exception as e:
                    print(f"! Nepavyko perkelti {old_prices_path}: {e}")
        except Exception as e:
            print(f"! {path} sugadintas ({e}) – pradedama nuo tuscio.")
            state = cls()
        if seen and "__heartbeat__" in seen and not state.heartbeat:
            state.heartbeat = seen["__heartbeat__"]
        return state

    def user(self, user_id, name=""):
        u = self.users.setdefault(str(user_id), {"watch": [], "hide": []})
        if name:
            u["name"] = name
        u.setdefault("watch", [])
        u.setdefault("hide", [])
        return u

    def save(self, path=None):
        path = path or config.STATE_FILE
        self.market.prune()
        data = {"market_version": self.MARKET_VERSION, "market": self.market.to_dict(),
                "telegram_offset": self.telegram_offset,
                "overrides": self.overrides, "heartbeat": self.heartbeat, "last_run": self.last_run,
                "fail_streak": self.fail_streak, "query_offset": self.query_offset,
                "users": self.users}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
