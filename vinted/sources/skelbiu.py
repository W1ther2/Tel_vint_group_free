# -*- coding: utf-8 -*-
"""Skelbiu.lt saltinis.

Skelbiu neturi viesos API, todel skaitomas iprastas puslapis. Jis generuojamas
serveryje, tad paprastas HTTP GET grazina viska, ko reikia – JavaScript nereikalingas.

Adresai:
  saraso p.1   /skelbimai/?keywords=iPhone+13&category_id=480&orderBy=1
  saraso p.N   /skelbimai/N?keywords=...
  skelbimas    /skelbimai/<antraste>-<id>.html

category_id=480 = "Apple" po "Mobilieji telefonai". Su juo i sarasa nebepatenka
ekranu keitimo paslaugos, dekliukai ir dalys – jos guli atskirose kategorijose.
orderBy=1 = naujausi virsuje.
"""

import html as html_lib
import re
import time
from datetime import datetime, timezone

import requests

try:
    from curl_cffi import requests as cffi_requests
    USING_CFFI = True
except ImportError:          # pragma: no cover
    cffi_requests = None
    USING_CFFI = False

from .. import config
from ..listing import Listing, Detail
from ..util import debug, fold
from .base import Source

BASE = "https://www.skelbiu.lt"
APPLE_CATEGORY = 480
PER_PAGE = 24            # Skelbiu rodo 24 skelbimus puslapyje (Vinted – 96)

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "lt-LT,lt;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}


def headers(referer=BASE + "/"):
    """Kuo panasesnes i tikra narsykle.

    SVARBU: su curl_cffi savo User-Agent NERASOM. curl_cffi imituoja konkrecios
    Chrome versijos TLS parasa ir pats prideda ta versija atitinkanti User-Agent.
    Iraseme savaji – parasas sako viena, antraste kita, ir Cloudflare tai mato."""
    h = dict(BASE_HEADERS)
    if USING_CFFI:
        h.pop("User-Agent")
    if referer:
        h["Referer"] = referer
    return h


# --- smulkios pagalbines ----------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")


def text_of(raw):
    """HTML gabalas -> svarus tekstas ('<em>iPhone</em> 13' -> 'iPhone 13')."""
    if not raw:
        return ""
    return re.sub(r"\s+", " ", html_lib.unescape(_TAG_RE.sub(" ", raw))).strip()


def parse_price(raw):
    """'350 &euro;' -> 350.0. Kai kainos nera ('Sutartine') -> None."""
    t = text_of(raw).replace(" ", " ")
    if "€" not in t and "eur" not in t.lower():
        return None
    m = re.search(r"(\d[\d\s]*(?:[.,]\d+)?)", t)
    if not m:
        return None
    try:
        return float(m.group(1).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


MONTHS = {"sausio": 1, "vasario": 2, "kovo": 3, "balandzio": 4, "geguzes": 5, "birzelio": 6,
          "liepos": 7, "rugpjucio": 8, "rugsejo": 9, "spalio": 10, "lapkricio": 11, "gruodzio": 12}


def parse_place_and_time(raw, now=None):
    """'Panevėžys, prieš 7 min.' -> ('Panevėžys', unix_laikas).

    Skelbiu rodo arba santykini laika ('prieš 2 val.'), arba data ('rugsėjo 14 d.')."""
    now = now if now is not None else time.time()
    t = text_of(raw)
    if not t:
        return None, None
    city, _, when = t.partition(",")
    city, when = city.strip() or None, when.strip()
    low = fold(when.lower())

    m = re.search(r"pries\s+(\d+)\s*(min|val|d|sav|men)", low)
    if m:
        n = int(m.group(1))
        seconds = {"min": 60, "val": 3600, "d": 86400, "sav": 604800, "men": 2592000}[m.group(2)]
        return city, now - n * seconds
    if "ka tik" in low or "dabar" in low:
        return city, now

    m = re.search(r"(" + "|".join(MONTHS) + r")\s+(\d{1,2})\s*d", low)
    if m:
        month, day = MONTHS[m.group(1)], int(m.group(2))
        today = datetime.fromtimestamp(now, tz=timezone.utc)
        year = today.year if (month, day) <= (today.month, today.day) else today.year - 1
        try:
            return city, datetime(year, month, day, 12, 0, tzinfo=timezone.utc).timestamp()
        except ValueError:
            return city, None
    return city, None


# Skelbiu bukle: tik "Nauja" / "Naudota". "Naudota" nieko nesako apie nusidevejima,
# todel paliekam nezinoma – toliau sprendzia aprasymas (defektai, baterija).
CONDITION_MAP = {"nauja": "Nauja be etikečių", "naujas": "Nauja be etikečių",
                 "atnaujinta": "Labai gera", "naudota": None, "naudotas": None}


def parse_condition(params):
    for p in params:
        key, _, value = p.partition(":")
        if fold(key.strip().lower()).startswith("bukle"):
            return CONDITION_MAP.get(fold(value.strip().lower()), None)
    return None


def normalize_title(title):
    """Skelbiu daznai raso be 'iPhone' ('13 pro max 256gb'). Kategorijoje yra tik
    Apple telefonai, todel truksatama zodi saugu pridėti – kitaip modelio neatpazintume."""
    t = fold((title or "").lower())
    if "iphone" in t or "i phone" in t:
        return title or ""
    return ("iPhone " + (title or "")).strip()


# --- saraso puslapis --------------------------------------------------------
_ITEM_START = re.compile(
    r'<a\s+href="(?P<href>/skelbimai/[^"]*?-(?P<id>\d+)\.html)"\s*'
    r'class="[^"]*standard-list-item', re.S)
_TITLE_RE = re.compile(r'<div class="title">(.*?)</div>', re.S)
_DESC_RE = re.compile(r'<div class="first-dataline">(.*?)</div>', re.S)
_WHEN_RE = re.compile(r'<div class="second-dataline">(.*?)</div>', re.S)
_WHEN_FALLBACK_RE = re.compile(r'<div class="second-line">(.*?)</div>', re.S)
_PARAM_RE = re.compile(r"""<div class=['"]param['"]>(.*?)</div>""", re.S)
_PRICE_RE = re.compile(r'<div class="price">(.*?)</div>', re.S)
_IMG_RE = re.compile(r'<img src="([^"]+)"')


def parse_list(page, now=None):
    """Saraso puslapis -> [{id, url, title, price, photo, condition, city, created_at}]."""
    starts = list(_ITEM_START.finditer(page or ""))
    out = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else m.end() + 6000
        block = page[m.start():end]
        body = block.split('class="content-block"', 1)[-1]
        titles = _TITLE_RE.findall(body) or _TITLE_RE.findall(block)
        when = _WHEN_RE.search(block) or _WHEN_FALLBACK_RE.search(block)
        city, created = parse_place_and_time(when.group(1) if when else "", now)
        price = _PRICE_RE.search(block)
        photo = _IMG_RE.search(block)
        desc = _DESC_RE.search(block)
        out.append({
            "id": m.group("id"),
            "url": BASE + m.group("href"),
            "title": text_of(titles[0]) if titles else "",
            "snippet": text_of(desc.group(1)) if desc else "",
            "price": parse_price(price.group(1)) if price else None,
            "photo": photo.group(1) if photo else None,
            "condition": parse_condition([text_of(p) for p in _PARAM_RE.findall(block)]),
            "city": city,
            "created_at": created,
        })
    return out


# --- skelbimo puslapis ------------------------------------------------------
_OG_RE = re.compile(r'<meta property="og:(\w+)" content="([^"]*)"', re.S)
_DESCRIPTION_RE = re.compile(r'<div class="description">(.*?)</div>', re.S)
_ROW_RE = re.compile(r"<div class=\"details-row\"><label>(.*?)</label><span>(.*?)</span></div>", re.S)
_OWNER_RE = re.compile(r'<div class="profile-data-owner-name">(.*?)</div>', re.S)
_PROFILE_RE = re.compile(r'<div class="profile-info-line">(.*?)</div>', re.S)
_ACTIVE_RE = re.compile(r'class="all-sellers-items">.*?<span>\s*(\d+)', re.S)
_SELLER_SLUG_RE = re.compile(r'href="/pasiulymai/([^/"]+)/?"')
_GONE_RE = re.compile(r"Puslapis nerastas|tokio puslapio neradome|skelbimas negalioja|"
                      r"skelbimas neaktyvus|skelbimo nebėra", re.I)


def parse_registered(text):
    """'Užsiregistravo 2024 gegužę.' -> paskyros amzius dienomis."""
    m = re.search(r"uzsiregistravo\s+(\d{4})(?:\s+(\w+))?", fold((text or "").lower()))
    if not m:
        return None
    year = int(m.group(1))
    month = 1
    for name, num in MONTHS.items():
        if m.group(2) and fold(m.group(2)).startswith(name[:5]):
            month = num
            break
    try:
        since = datetime(year, month, 15, tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, (datetime.now(timezone.utc) - since).days)


def parse_detail(page, city=None):
    """Skelbimo puslapis -> {status, title, description, photo, condition, seller, model}."""
    if not page:
        return {"status": "unknown"}
    if _GONE_RE.search(page[:40000]):
        return {"status": "gone"}
    og = {k: html_lib.unescape(v) for k, v in _OG_RE.findall(page)}
    rows = {fold(text_of(k).lower().rstrip(":")): text_of(v) for k, v in _ROW_RE.findall(page)}
    body = _DESCRIPTION_RE.search(page)
    if not og and not body:
        return {"status": "unknown"}      # ne skelbimo puslapis (pvz. Cloudflare patikra)
    description = text_of(body.group(1)) if body else og.get("description", "")

    seller = {"country": "LT"}
    if city:
        seller["city"] = city
    profile = _PROFILE_RE.search(page)
    if profile:
        info = text_of(profile.group(1))
        age = parse_registered(info)
        if age is not None:
            seller["account_age_days"] = age
        m = re.search(r"(\d+)", info.split(":")[-1]) if ":" in info else None
        if m:
            seller["ads_total"] = int(m.group(1))
    active = _ACTIVE_RE.search(page)
    if active:
        seller["active_items"] = int(active.group(1))
    owner = _OWNER_RE.search(page)
    if owner:
        seller["name"] = text_of(owner.group(1))
    # Pardavejo tapatybe: profilio nuoroda, o jei jos nera – rodomas vardas
    # (privatiems tai uzdengtas telefono numeris, pvz. "+3706231****").
    slug = _SELLER_SLUG_RE.search(page)
    identity = slug.group(1) if slug else seller.get("name")
    if identity:
        seller["id"] = identity
    seller["verified"] = "icon-user-verified" in page

    return {"status": "active", "title": og.get("title") or "", "description": description,
            "photo": og.get("image"), "condition": CONDITION_MAP.get(fold(rows.get("bukle", "").lower())),
            "model": rows.get("modelis"), "seller": seller}


# --- HTTP -------------------------------------------------------------------
_CLOUDFLARE_RE = re.compile(
    r"cloudflare|cf-mitigated|attention required|just a moment|"
    r"checking your browser|ray id", re.I)
_RATE_RE = re.compile(r"too many requests|per daug uzklausu|rate limit|slow down", re.I)

# curl_cffi "chrome" gali rodyti i sena versija, kurios parasas jau pazymetas.
# Bandom kelis is eiles – kuris praeina, ta ir naudojam visa paleidima.
IMPERSONATE_CHAIN = ["chrome", "chrome131", "chrome124", "chrome120",
                     "safari17_0", "firefox133", "edge101"]


def why_blocked(body, headers=None):
    """Kas mus atmete. Kai pozymiu nera, sakom tiesiai – nezinom."""
    text = body or ""
    headers = {str(k).lower(): str(v) for k, v in dict(headers or {}).items()}
    if _CLOUDFLARE_RE.search(text) or "cf-ray" in headers or "cloudflare" in headers.get("server", "").lower():
        return "Cloudflare apsauga"
    if _RATE_RE.search(text):
        return "uzklausu ribojimas"
    server = headers.get("server")
    return f"neaiski priezastis (serveris: {server})" if server else "neaiski priezastis"


def block_details(response):
    """Trumpa eilute log'ui: kas atsake ir ka pasake. Be sito spelioti neimanoma."""
    try:
        headers = dict(getattr(response, "headers", {}) or {})
    except Exception:
        headers = {}
    interesting = {k: v for k, v in headers.items()
                   if str(k).lower() in ("server", "cf-ray", "cf-mitigated", "x-powered-by",
                                         "retry-after", "content-type", "x-cache", "via")}
    body = re.sub(r"\s+", " ", (getattr(response, "text", "") or ""))[:200]
    return f"antrastes={interesting or '{}'} | tekstas: {body or '(tuscias)'}"


class SkelbiuClient:
    """Paprastas puslapiu parsisiuntimas su pakartojimais."""

    def __init__(self, sleep=time.sleep):
        self.sleep = sleep
        self.session = None
        self.last_error = ""
        self.blocked = ""       # netuscias = svetaine mus atmete, nebandom toliau
        self.ok_count = 0       # kiek uzklausu pavyko (skiria "IP blokas" nuo "per greitai")
        self.profile = None     # kuris narsykles parasas suveike

    def _new_session(self, profile):
        if not USING_CFFI:
            return requests.Session()
        return cffi_requests.Session(impersonate=profile)

    def profiles(self):
        pinned = config.cfg.get("SKELBIU_IMPERSONATE")
        if pinned:
            return [pinned]
        return IMPERSONATE_CHAIN if USING_CFFI else [None]

    def start(self):
        """Bandom kelis narsykles parasus: „chrome“ gali rodyti i sena versija,
        kurios pirstu atspaudas jau pazymetas kaip botas."""
        if not USING_CFFI:
            print("! curl_cffi neidiegtas – Skelbiu tikriausiai blokuos. "
                  "Workflow faile: pip install requests curl_cffi")
        last = None
        for profile in self.profiles():
            try:
                self.session = self._new_session(profile)
            except Exception as e:           # nezinomas profilis siai curl_cffi versijai
                debug(f"Skelbiu profilis {profile} netinka: {e}")
                continue
            try:
                r = self.session.get(BASE + "/", headers=headers(referer=None), timeout=20)
            except Exception as e:
                self.last_error = f"Skelbiu sesija nepavyko: {e}"
                print(f"! {self.last_error}")
                return
            vardas = profile or "requests (BE Chrome paraso!)"
            if r.status_code == 200:
                self.ok_count += 1
                self.blocked = ""
                self.profile = profile
                print(f"Skelbiu sesija pradeta (statusas 200, parasas: {vardas})")
                return
            last = r
            print(f"  ! Skelbiu atmete parasa „{vardas}“ (HTTP {r.status_code})")
            if r.status_code not in (403, 429):
                break
            self.sleep(2)

        if last is not None:
            self.blocked = why_blocked(getattr(last, "text", ""), getattr(last, "headers", {}))
            self.last_error = f"Skelbiu HTTP {last.status_code}: {self.blocked}"
            print(f"! Skelbiu atmete visus bandytus parasus – {self.blocked}. "
                  f"Sio paleidimo metu Skelbiu praleidziu.")
            print(f"  Skelbiu atsakymas: {block_details(last)}")
        else:
            self.last_error = "Skelbiu: nepavyko sukurti sesijos"
            self.blocked = "nepavyko sukurti sesijos"
            print(f"! {self.last_error}")

    def get(self, url, tries=3):
        """(http_statusas, tekstas). Klaidos atveju (0, '')."""
        if self.blocked:
            return 0, ""
        if self.session is None:
            self.start()
            if self.blocked:
                return 0, ""
        wait = config.cfg["SLEEP_SECONDS"]
        for attempt in range(1, tries + 1):
            try:
                r = self.session.get(url, headers=headers(), timeout=25)
                if r.status_code in (403, 429):
                    reason = why_blocked(r.text, getattr(r, "headers", {}))
                    self.last_error = f"Skelbiu HTTP {r.status_code} ({reason})"
                    # Jei dar NE VIENA uzklausa nepavyko, tai ne greitis – mus tiesiog
                    # neileidzia. Laukti 30+60+120s nera prasmes: sustojam is karto.
                    if not self.ok_count:
                        self.blocked = reason
                        print(f"  ! Skelbiu {r.status_code} nuo pirmos uzklausos ({reason}) – "
                              "nebeaikvoju laiko, praleidziu Skelbiu siame paleidime.")
                        print(f"    Skelbiu atsakymas: {block_details(r)}")
                        return 0, ""
                    backoff = config.cfg["BLOCK_BACKOFF_SECONDS"]
                    pause = backoff[min(attempt - 1, len(backoff) - 1)]
                    if attempt < tries:
                        print(f"  ! Skelbiu {r.status_code} ({reason}) – laukiu {pause}s "
                              f"({attempt}/{tries})...")
                        self.sleep(pause)
                    continue
                if r.status_code >= 500:
                    self.last_error = f"Skelbiu HTTP {r.status_code}"
                    if attempt < tries:
                        self.sleep(wait * attempt)
                    continue
                self.ok_count += 1
                return r.status_code, (r.text or "")
            except Exception as e:
                self.last_error = f"Skelbiu tinklo klaida: {e}"
                debug(self.last_error)
                if attempt < tries:
                    self.sleep(wait * attempt)
        return 0, ""


# --- saltinis ---------------------------------------------------------------
class SkelbiuSource(Source):
    name = "skelbiu"
    label = "Skelbiu"
    buyer_protection_fee = False  # Skelbiu atsiskaitoma tiesiogiai, mokescio nera

    def __init__(self, client=None, sleep=time.sleep):
        super().__init__()
        self.client = client if client is not None else SkelbiuClient(sleep=sleep)
        self._cities = {}         # skelbimo id -> miestas (is saraso, skelbime jo nera)

    @property
    def browse_all(self):
        return bool(config.cfg["SKELBIU_BROWSE_ALL"])

    @property
    def pages_multiplier(self):
        # Puslapyje 24 skelbimai (Vinted – 96), tad reikia daugiau puslapiu. Narsant
        # kategorija uzklausa tik viena, todel galim sau leisti gilintis toliau.
        return 3 if self.browse_all else 4

    def queries(self):
        """Kategorija 480 jau yra „Apple telefonai“, tad be raktazodziu vienas sarasas
        grazina VISUS naujausius iPhone skelbimus. 34 atskiros paieskos ne tik brangios –
        jos dar ir praleidzia tuos skelbimus, kuriu pavadinimas nesutampa su raktazodziu."""
        return [""] if self.browse_all else super().queries()

    def describe(self, query):
        return "visi Apple telefonai" if not query else f"'{query}'"

    def start(self):
        self.client.start()
        self.last_error = getattr(self.client, "last_error", "") or ""
        self.unavailable = getattr(self.client, "blocked", "") or ""

    def search_url(self, query, page):
        params = f"category_id={APPLE_CATEGORY}&orderBy=1&user_type=0&type=0"
        if query:
            params = f"keywords={requests.utils.quote(query)}&" + params
        return f"{BASE}/skelbimai/{'' if page <= 1 else page}?{params}"

    def search(self, query, pages, seen=None):
        known = self.local_ids(seen)
        out, collected = [], set()
        for page in range(1, max(1, pages) + 1):
            status, body = self.client.get(self.search_url(query, page))
            self.last_error = self.client.last_error
            self.unavailable = getattr(self.client, "blocked", "") or ""
            if status != 200 or not body:
                if page == 1:
                    self.blocked_queries += 1
                break
            self.blocked_queries = 0
            rows = parse_list(body)
            if not rows:
                break
            for row in rows:
                if row["id"] in collected:
                    continue
                collected.add(row["id"])
                out.append(self.to_listing(row))
            if page == 1:
                print(f"  p.1: {len(rows)} skelb., naujausi ID: {[r['id'] for r in rows[:3]]}")
            # Sarasas rikiuotas nuo naujausiu – jei visas puslapis jau matytas, toliau nera prasmes
            if known and all(r["id"] in known for r in rows):
                print(f"  p.{page}: visi skelbimai jau matyti – toliau nebetikrinu")
                break
            if page < pages:
                self.sleep_between()
        return out

    def sleep_between(self):
        getattr(self.client, "sleep", time.sleep)(config.cfg["SLEEP_SECONDS"])

    def to_listing(self, row):
        if row.get("city"):
            self._cities[row["id"]] = row["city"]
        seller = {"country": "LT"}
        if row.get("city"):
            seller["city"] = row["city"]
        return Listing(
            source=self.name, id=row["id"], title=normalize_title(row["title"]),
            price=row["price"], url=row["url"], photo=row.get("photo"),
            condition=row.get("condition"), seller_id="", seller=seller,
            photo_count=None, created_at=row.get("created_at"), raw=row,
        )

    def detail(self, listing):
        status, body = self.client.get(listing.url)
        if status in (404, 410):
            return Detail(status="gone")
        if status != 200 or not body:
            return Detail(status="unknown")
        d = parse_detail(body, city=self._cities.get(listing.id))
        if d["status"] != "active":
            return Detail(status=d["status"])
        # Aprasymas is saraso jau buvo matytas – prijungiam, jei skelbime jo nebutu
        description = d["description"] or (listing.raw or {}).get("snippet") or ""
        title = normalize_title(d.get("title") or listing.title)
        seller = dict(d["seller"])
        seller.pop("verified", None)
        return Detail(status="active", title=title, description=description,
                      photo=d.get("photo") or listing.photo,
                      condition=d.get("condition") or listing.condition, seller=seller)

    def status(self, listing_id, url=None):
        # Skelbiu neturi "parduota" zymes – dinges skelbimas laikomas parduotu (GONE_AS_SOLD)
        status, body = self.client.get(url or f"{BASE}/skelbimai/x-{listing_id}.html")
        if status in (404, 410):
            return "gone"
        if status != 200 or not body:
            return "unknown"
        return "gone" if _GONE_RE.search(body[:40000]) else "active"
