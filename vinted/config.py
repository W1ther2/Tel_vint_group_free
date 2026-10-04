# -*- coding: utf-8 -*-
"""Nustatymai: numatytieji + config.json + Telegram komandomis pakeisti (overrides)."""

import json
import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
# Grupes skiltis (forum topic). Tuscia = rasoma i bendra srauta. Reikalinga, kai tame
# paciame pokalbyje nori kelis botus: pvz. iPhone – vienoje skiltyje, Android – kitoje.
# ID paimsi paspaudes ant skilties -> „Copy link“: .../c/123456/<SKILTIES_ID>
CHAT_TOPIC_ID = os.environ.get("CHAT_TOPIC_ID", "")
# Kur siusti tarnybines zinutes: „SKRIPTAS UZLUZO“, „saltinis nepasiekiamas“, heartbeat,
# sugadintas config.json. Viesoje grupeje tokioms zinutems ne vieta – nurodyk savo
# asmeninio pokalbio su botu ID. Tuscia = kaip ir anksciau, i ta pacia grupe.
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")

# Failu vardus galima pakeisti aplinkos kintamaisiais – taip tas pats kodas gali
# suktis ir "gyvai", ir testams (su kitu botu ir atskira busena).
CONFIG_FILE = os.environ.get("CONFIG_FILE", "config.json")
SEEN_FILE = os.environ.get("SEEN_FILE", "seen.json")
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
# Ilgalaikis rinkos archyvas (vinted/archive.py) – saugomas kartu su busena, bet nevalomas.
ARCHIVE_DIR = os.environ.get("ARCHIVE_DIR", "archive")

BASE = "https://www.vinted.lt"
API_BASE = "https://api.vinted.lt"

DEFAULTS = {
    # --- Is kur ieskoti ---
    # Saltiniai ta tvarka, kuria tikrinami. Galimi: "vinted".
    # Naujas saltinis = vienas failas vinted/sources/ + jo vardas cia.
    "SOURCES": ["vinted"],
    # Saltiniai tikrinami vienu metu (jie eina i skirtingus serverius, tad vienas kito
    # nestabdo). false = paeiliui, tada laikas dalijamas po lygiai.
    "PARALLEL_SOURCES": True,
    # Vinted: viena paieska "iphone" grazina VISUS modelius, tad 34 atskiru nereikia.
    # Tai maziausiai 30 kartu maziau uzklausu ir tiek pat kartu greiciau.
    # false = ieskoti kiekvieno modelio atskirai pagal SEARCH_QUERIES.
    "VINTED_BROWSE_ALL": True,
    # Tuscias = paieskos frazes imamos is BRANDS ("iphone", "samsung galaxy", "xiaomi"...).
    "VINTED_QUERIES": [],
    # Po 96 skelb. Gyvai matyta, kad „iphone“ sarase Lietuvos skelbimu tik ~11 % (likusieji –
    # PL, FI, LV), tad 3 puslapiai duoda vos ~30 lietuvisku. Kad „pigiausiu“ palyginimui
    # (RANK_RECENT_DAYS) uztektu tokiu pat telefonu, imam giliau – puslapiai pigus, o jau
    # matyti skelbimai pardavejo uzklausu nebereikalauja.
    "VINTED_BROWSE_PAGES": 6,
    "VINTED_FULL_SCAN_PAGES": 10,    # pirmam paleidimui (seen.json tuscias)
    "VINTED_MAX_PAGES": 10,          # Vinted giliau neleidzia: 11-as puslapis = HTTP 400

    # Skelbiu: narsyti visa Apple kategorija vienu sarasu, o ne ieskoti kiekvieno modelio
    # atskirai. Greiciau ir randa daugiau (skelbimu pavadinimai ne visada sutampa su
    # raktazodziu). false = naudoti SEARCH_QUERIES kaip Vinted.
    "SKELBIU_BROWSE_ALL": True,
    # Narsykles parasas Skelbiu (curl_cffi). Tuscia = bandom kelis is eiles ir
    # naudojam ta, kuris praeina. Pvz. "chrome131".
    "SKELBIU_IMPERSONATE": "",
    # Pirkpard: JSON API, aprasymas ateina kartu su sarasu, tad uzteks vienos uzklausos.
    "PIRKPARD_QUERIES": [],           # tuscias = is BRANDS
    "PIRKPARD_PER_PAGE": 100,
    "PIRKPARD_SKIP_AUCTIONS": True,   # aukcione kaina reiskia dabartini pasiulyma, ne kaina
    "PIRKPARD_STATUS_PAGES": 3,       # kiek puslapiu perziureti tikrinant, kas parduota

    # --- Ka ieskoti ---
    # Gamintojai. Galimi: "apple", "samsung", "xiaomi", "google", "oneplus".
    # Tuscias sarasas = visi. Kiekvienas gamintojas = viena papildoma paieska per paleidima,
    # tad ir daugiau nauju skelbimu, kuriems reikia pardavejo salies uzklausos
    # (SELLER_COUNTRY_LOOKUPS riba – 40). Nori tik telefonu, kuriuos perparduosi –
    # susiaurink "MODELS" arba palik maziau gamintoju.
    "BRANDS": ["apple", "samsung", "xiaomi", "google", "oneplus"],
    # Apie kuriuos modelius siusti. Tuscias sarasas = visi. Keiciama ir Telegram'e: /modeliai.
    # Pvz. ["13", "13 Pro", "14 Pro Max"]. Nesekami modeliai atmetami dar pries skelbimo
    # puslapi ir pries pardavejo salies uzklausa – tad ribotos uzklausos atitenka tiems,
    # kurie tau rupi.
    "MODELS": [],
    "SEARCH_QUERIES": [
        "iPhone 8", "iPhone 8 Plus", "iPhone X", "iPhone XR", "iPhone XS", "iPhone XS Max",
        "iPhone 11", "iPhone 11 Pro", "iPhone 11 Pro Max", "iPhone 12", "iPhone 12 mini",
        "iPhone 12 Pro", "iPhone 12 Pro Max", "iPhone 13", "iPhone 13 mini", "iPhone 13 Pro",
        "iPhone 13 Pro Max", "iPhone 14", "iPhone 14 Plus", "iPhone 14 Pro", "iPhone 14 Pro Max",
        "iPhone 15", "iPhone 15 Plus", "iPhone 15 Pro", "iPhone 15 Pro Max", "iPhone 16",
        "iPhone 16e", "iPhone 16 Plus", "iPhone 16 Pro", "iPhone 16 Pro Max", "iPhone 17",
        "iPhone 17 Pro", "iPhone 17 Pro Max", "iPhone Air",
    ],

    # --- Kas yra "gera kaina" ---
    # "rank"     – siunciam, jei skelbimas tarp pigiausiu SIUO METU parduodamu tokiu pat
    #              telefonu (tas pats modelis ir talpa). Rinkos kainos zinoti nereikia.
    # "discount" – siunciam, jei pigiau nei musu ivertinta verte (senasis budas).
    # Kai palyginti per mazai (reti modeliai), "rank" pats grizta prie "discount".
    "DEAL_MODE": "rank",
    "RANK_TOP_PCT": 0.15,            # tarp 15% pigiausiu (23 skelbimai -> 1-4 vietos)
    "RANK_MIN_PEERS": 8,             # maziau aktyviu skelbimu – vieta nieko nereiskia
    # "Dabar parduodami" = matyti kataloge per paskutines tiek dienu. Senesni greiciausiai
    # jau parduoti – su jais lyginti butu tas pats, kas lyginti su nebeegzistuojanciais.
    "RANK_RECENT_DAYS": 2,
    "MIN_DISCOUNT": 0.10,            # bent 10% pigiau nei telefono verte
    # „Per pigu“ – penkios ribos, kiekviena savo vietoje:
    #   HARD_MIN_PRICE_RATIO    – x rinkos kainos: ATMETAMA pries atidarant skelbima (dalys, dezute)
    #   SUSPICIOUS_REJECT_RATIO – x KITO pigiausio tokio pat skelbimo: ATMETAMA („rank“ rezime)
    #   NO_RANK_REJECT_RATIO    – x rinkos kainos: ATMETAMA, kai palyginti nera su kuo (retas modelis)
    #   SUSPICIOUS_WARN_RATIO   – x kito pigiausio: siunciama su ⚠️ „Įtartinai pigu“
    #   SUSPICIOUS_PRICE_RATIO  – x rinkos kainos: siunciama, bet rizikos eiluteje prirasoma priezastis
    "HARD_MIN_PRICE_RATIO": 0.40,    # pigiau nei 40% rinkos – beveik visada sugedes/dalims/ne telefonas, atmetama
    "SUSPICIOUS_PRICE_RATIO": 0.55,  # pigiau nei 55% rinkos – siunciama, bet pazymima rizika
    "NO_RANK_REJECT_RATIO": 0.50,    # be palyginimo: pigiau nei 50% rinkos – atmetama (įtartinai pigu)
    # Baterija: nurodyta ir per maza -> atmetama; nenurodyta -> praleidziama su zyma kortelėje.
    "MIN_BATTERY": 80,               # min. baterijos % (0 = netikrinti)
    "LOW_BATTERY_MIN_DISCOUNT": 0.30,  # isimtis: labai pigus telefonas praleidziamas ir su mazesne baterija
    "MODEL_MIN_PRICES": {},          # savos min. kainos modeliams, pvz. {"13": 100} (kitiems – numatytosios)

    # --- Tik tvarkingi telefonai ---
    "TIDY_ONLY": True,               # atmesti sugedusius, netestuotus, uzrakintus, su defektais
    "MIN_CONDITION": "Gera",         # blogiausia leidziama bukle: "Labai gera" / "Gera" / "Patenkinama"
    "ALLOWED_DEFECTS": ["įbrėžimai"],# kurie defektai leidziami, kai TIDY_ONLY

    # --- Rinkos kaina ---
    "MARKET_PRICES": {},             # rankines kainos: {"13": 180, "13|256 GB": 210}
    "USE_SOLD_PRICES": True,         # naudoti tikras pardavimo kainas, kai ju pakanka
    "MIN_SOLD_SAMPLES": 5,           # kiek parduotu reikia, kad kaina butu skaiciuojama is ju
    "SOLD_CHECKS_PER_RUN": 15,       # kiek senu skelbimu per paleidima patikrinti, ar parduoti
    # Tikrinti skelbimus, kuriu kataloge nematem bent tiek dienu (maziausiai 1: ta pacia diena
    # dingimas nieko nereiskia). Anksciau 2 – tada Vinted puslapio dazniausiai jau nebudavo, ir
    # „parduota“ patvirtinti nepavykdavo (is 717 tik 4). Zr. market.sold_check_candidates.
    "SOLD_CHECK_AFTER_DAYS": 1,
    "MIN_SAMPLES": 8,                # kiek prasomu kainu reikia rinkos kainai
    "USE_TYPICAL_FALLBACK": True,    # kai duomenu per mazai – naudoti apytiksle kaina (retiems modeliams)
    "MARKET_PERCENTILE": 0.4,        # prasomu kainu percentilis (0.5 = mediana, 0.35 = pigesnis trecdalis)
    "ASKING_MAX_AGE_DAYS": 21,       # skelbimai, kabantys ilgiau – per brangus, i rinkos kaina neiskaiciuojami
    # Prasoma kaina -> sandorio kaina. NEPATIKRINTA PRIELAIDA: Vinted sandorio kainos nerodo,
    # o uzsidariusio skelbimo kaina – tik jos virsutine riba. Visos vertes = prasoma x sis
    # daugiklis (v49: ir is pardavimu, ir is skelbimu – vienu masteliu, zr. market.Quote).
    "ASKING_SALE_FACTOR": 0.85,
    # v49: ar ir is pardavimu („parduoti“) gauta verte dauginti is ASKING_SALE_FACTOR, kaip
    # ir is skelbimu. Teisinga (uzsidarymo kaina – prasoma, ne sandorio), bet GYVAI kol kas
    # isjungta: tai sumazintu iPhone verte ~15 %, ir pelno riba (MIN_PROFIT_EUR) butu atmetusi
    # ~140 is 152 ankstesniu pranesimu – o 0,85 yra nepatikrinta prielaida. Ijungti, kai
    # archyvas ir pardavimo faktas parodys, kiek ji teisinga. Matavimas ir mokymas jau dabar
    # naudoja vienodą prasomu masteli (Quote.ask, `qa`) nepriklausomai nuo sio jungiklio.
    "MARKET_SCALE_UNIFIED": False,

    # --- Savikalibracija ---
    # Kodas isimena, kiek spejo uz kiekviena telefona, ir kai tas telefonas parduodamas,
    # palygina su realia kaina. Sistemine paklaida automatiskai istaisoma.
    # Isjungta (v49). Kalibracija „taiso“ ASKING_SALE_FACTOR pagal uzsidariusiu skelbimu
    # PASKUTINES PRASOMAS kainas – bet jos sandorio kainos nematuoja (Vinted jos nerodo), tad
    # prielaida butu „kalibruojama“ is duomenu, kurie jos neliecia. /kalibruoti taip – ijungti.
    "AUTO_CALIBRATE": False,
    "MIN_CALIBRATION_SAMPLES": 20,   # kiek parduotu reikia, kad pataisymas butu daromas
    "CALIBRATION_MAX_STEP": 0.05,    # daugiausiai 5% pokytis per diena (be soliu)
    "CALIBRATION_MIN": 0.70,         # ribos, kad klaidingi duomenys nenuvestu i absurda
    "CALIBRATION_MAX": 1.15,

    "GONE_AS_SOLD": True,            # dinges skelbimas laikomas parduotu (Vinted pardave dazniausiai istrina)
    "PRICE_HISTORY_DAYS": 30,
    "SOLD_HISTORY_DAYS": 60,
    "PRICE_HISTORY_MAX_ITEMS": 12000,

    # --- Kainos sumazejimas ---
    "PRICE_DROP_ALERTS": True,
    "PRICE_DROP_MIN": 0.05,          # pranesti, jei atpigo bent 5%

    # --- Pelnas perpardavus ---
    "SHOW_PROFIT": True,
    "MIN_PROFIT_EUR": 15,            # nesiusti, jei galimas pelnas mazesnis (0 = nesvarbu)
    # Kai skelbimas gerokai pigesnis uz KITA pigiausia – beveik visada kazkas negerai
    # (uzrakintas, be dalies, apgavyste). Santykis su kitu pigiausiu tokiu pat telefonu:
    "SUSPICIOUS_REJECT_RATIO": 0.60, # pigiau nei 60% kito pigiausio – atmesti
    "SUSPICIOUS_WARN_RATIO": 0.75,   # pigiau nei 75% – siusti, bet pazymeti ⚠️
    "SHOW_RANK": False,              # rodyti kortelej „12-as pigiausias iš 64 ...“ (atrankai naudojama visada)

    # --- Rezultatu sekimas: ar praneseti skelbimai buvo nupirkti ir per kiek laiko ---
    "TRACK_RESULTS": True,
    "TRACK_DAYS": 7,                 # kiek dienu sekti kiekviena pranesima
    "TRACK_FAST_HOURS": 48,          # pirmas 48 val. – tikrinti kas paleidima
    "TRACK_MIN_MINUTES": 5,          # bet ne dazniau nei kas 5 min.
    "TRACK_SLOW_EVERY_HOURS": 6,     # veliau – kas 6 val.
    "TRACK_CHECKS_PER_RUN": 25,      # daugiausia patikrinimu per paleidima
    "TRACK_KEEP_DAYS": 90,           # kiek laiko laikyti rezultatus
    "REPORT_EVERY_DAYS": 7,          # automatine ataskaita Telegram'e (0 = nesiusti)
    "BUYER_FEE_FIXED": 0.70,         # Vinted pirkejo apsaugos mokestis (fiksuota dalis)
    "BUYER_FEE_PCT": 0.05,           # Vinted pirkejo apsaugos mokestis (procentai)
    "SHIPPING_COST": 3.5,            # siuntimo kaina perkant

    # --- Pranesimai ---
    # Daugiausiai korteliu per viena paleidima (0 = be ribos). Apsauga nuo lavinos:
    # jei seen.json / state.json kada nors pasimestu, visi skelbimai atrodytu nauji ir
    # Telegram'as gautu desimtis korteliu is karto (o Telegram grupeje leidzia 20/min.).
    # Likusieji nezymimi matytais – juos ivertins kitas paleidimas.
    "MAX_ALERTS_PER_RUN": 20,
    "LOUD_DISCOUNT": 0.30,           # nuo tiek pigiau – su garsu, maziau – tyliai
    "TELEGRAM_COMMANDS": True,       # leisti keisti nustatymus komandomis Telegram'e
    # Kas gali keisti nustatymus. Tuscia = niekas (komandos grupeje ignoruojamos).
    # Savo ID suzinosi parases botui privaciai /start.
    "ADMIN_IDS": [6157710734],
    "HEARTBEAT_HOURS": 24,
    "FAIL_ALERT_RUNS": 3,            # po kiek nesekmingu paleidimu is eiles pranesti apie problema
    "SOURCE_ALERT_HOURS": 12,        # kaip daznai pranesti apie blokuojama saltini (0 = kas karta)

    # --- Priedu atpazinimas (pirmas pavadinimo zodis) ---
    "ACCESSORY_FIRST_WORDS": [
        "deklas", "dekl", "case", "cover", "stiklas", "apsauginis", "folija", "kroviklis",
        "laidas", "kabelis", "dezute", "box", "hulle", "coque", "custodia", "etui", "glass",
        "screen", "ekranas", "baterija", "battery", "korpusas", "kamera", "lens", "magsafe",
    ],

    # --- Pardavejas ir kalba ---
    "ALLOWED_COUNTRY_CODES": ["LT"],
    "FILTER_BY_COUNTRY": True,
    "REQUIRE_KNOWN_COUNTRY": False,
    # Kiek pardavejo salies uzklausu per viena paieska. Vinted katalogas salies nebeduoda,
    # o zinoti ja BUTINA pries rinkos statistika (uzsienio kainos iskreipia Lietuvos rinka).
    # Tikrinami tik nauji telefonai, atsakymai isimenami (state.json "sellers"), tad
    # praktikoje uzklausu maziau nei nauju skelbimu. 0 = neuzklausti.
    # Riba yra VISO PALEIDIMO (dalijama visoms paieskoms po lygiai), o ne vienos paieskos –
    # kitaip pirmasis gamintojas suvalgo viska ir i Telegram krenta tik jo telefonai.
    # Nuo v46 sios ribos paskirtis pasikeite: 429 nebevengiam ja (tuo rupinasi
    # VINTED_MAX_PER_MINUTE), tad cia ribojamas PALEIDIMO LAIKAS. Kiekviena uzklausa kainuoja
    # DETAIL_SLEEP_SECONDS + sulaikyma, tad 30 uzklausu ~ pusе minutes is MAX_RUN_MINUTES.
    # Ta pacia pardaveju API kvieciа ir skelbimo atidarymas (reitingui, atsiliepimams);
    # i SIA riba tos uzklausos neisiskaiciuoja, bet i greicio riba – jau taip.
    "SELLER_COUNTRY_LOOKUPS": 30,
    # Kai salies nustatyti nepavyko: praleisti tik tada, jei tekste yra tikrai lietuvisku
    # zodziu. „iPhone 12 64g“ be lietuvisko zodzio daznai yra lenku skelbimas, kurio
    # kalbos filtras nepagauna.
    "UNKNOWN_COUNTRY_NEEDS_LT_TEXT": True,
    "MIN_SELLER_RATING": 0,
    "MIN_SELLER_REVIEWS": 0,
    "SELLER_NEW_ACCOUNT_DAYS": 30,   # jaunesne paskyra = rizikos pozymis
    "ONLY_LITHUANIAN_TEXT": True,
    "ALLOWED_LANGUAGES": ["LT", "EN"],

    # --- Veikimas ---
    # Vinted kategorijos/prekes zenklo filtras. Tuscia = filtras nenaudojamas.
    # ID suzinosi is log'o eilutes "Daznos kategorijos/brandai" po paleidimo.
    "CATALOG_IDS": [],               # pvz. [2342] – mobilieji telefonai
    "BRAND_IDS": [],                 # pvz. [12] – Apple
    "MAX_RUN_MINUTES": 25,           # ilgiausias paleidimo laikas – po to sustoja ir tesia kitame
    "ROTATE_QUERIES": True,          # kiekviena paleidima pradeti nuo kito modelio (tolygesnis greitis)
    "PAGES": 2,                      # puslapiu (po 96 skelb.) kiekvienai paieskai iprastai
    "FULL_SCAN_PAGES": 10,           # kai seen.json tuscias (pirmas/pilnas paleidimas) – perziureti daugiau
    "SLEEP_SECONDS": 3,
    "BLOCK_BACKOFF_SECONDS": [30, 60, 120],   # pauzes, kai Vinted blokuoja (403)
    "STOP_AFTER_BLOCKED_QUERIES": 3,          # po tiek is eiles blokuotu paiesku – baigti paleidima
    "DETAIL_SLEEP_SECONDS": 1.0,
    # Kiek laukti po HTTP 429 (www.vinted.lt riboja uzklausu dazni – matuota ~55 per minute).
    # Riba laikina, tad po sios pauzes klausiam toliau; kitaip vienas 429 paliktu be salies
    # patikros visus likusius gamintojus, ir i Telegram kristu tik pirmojo telefonai.
    "VINTED_RATE_COOLDOWN": 60,
    # Daugiausiai uzklausu i www.vinted.lt per minute – VISU rusiu kartu (katalogo puslapiai,
    # skelbimu puslapiai, pardaveju profiliai). Gyvai matuota riba ~55/min, tad 45 palieka
    # atsargos ir bendram runner'io IP. Virsijus – uzklausa sulaikoma, kol atsiras vieta.
    # Taip pigiau: sulaikymas kainuoja ~1 s, o 429 kainuoja visa VINTED_RATE_COOLDOWN
    # minute tylos. 0 = greicio neriboti (kaip iki v46).
    # Tai yra LUBOS: su VINTED_RATE_ADAPT botas mokosi tik zemiau ju.
    "VINTED_MAX_PER_MINUTE": 45,
    # Mokytis tikrosios ribos pagal 429 (AIMD). 45/min yra spejimas vienam runner'iui, bet
    # GitHub Actions IP yra bendri: kai juo tuo paciu metu dirba kitas darbas, tikroji riba
    # zemesne. Po paleidimo su 429 greitis mazinamas karta (x0,7), po tyliu – po truputi
    # grazinamas (+3/min), kol vel pasiekia VINTED_MAX_PER_MINUTE. Issimoktas skaicius
    # saugomas state.json ("rate_limit"). false = visada laikyti VINTED_MAX_PER_MINUTE.
    "VINTED_RATE_ADAPT": True,
    # Zemiausias greitis, iki kurio dar mazinam (zemiau – paleidimas nebespetu nieko).
    "VINTED_RATE_MIN": 15,
    # Kiek valandu atsiminti greiti, kuriam serveris atsake 429. Tiek laiko grizdami
    # aukstyn sustojam 3/min zemiau jo – kitaip kas kelis paleidimus vel atsitrenktume i ta
    # pacia siena, o toks paleidimas praranda didele dali skelbimu puslapiu (pjuklas).
    "VINTED_RATE_MEMORY_HOURS": 6,
    # Kai skelbimo puslapio atidaryti nepavyksta (403, laiko limitas), skelbimas nesiunciamas
    # „aklai“ – bandoma dar kituose paleidimuose, bet ne daugiau nei tiek kartu.
    "DETAIL_RETRIES": 3,
    "DRY_RUN": False,
    "PAUSED": False,                 # True = skelbimai nesiunciami (Telegram /pauze)
    "DEBUG": False,
    # Rinkos kainos patikimumas (vinted/confidence.py). Griezta atranka TIK nuolaidos budu:
    # nuolaida skaiciuojama nuo ATSARGIOS vertes = verte x p20(pardavimo / musu kaina) to
    # lygio. „Pigiausiu“ (rank) logika nekeiciama.
    "CONFIDENCE_STRICT": True,
    # Kiek PATVIRTINTU pardavimu su tuometiniu lygiu (qc) reikia, kad lygio reiksme butu
    # ismokta, o ne pradine (confidence.PRIOR).
    "CONFIDENCE_MIN_SAMPLES": 20,
    # Pardavimo faktas (vinted/liquidity.py): kiek dienu nuo pirmo pamatymo laukti, ar
    # skelbimas patvirtintai parduodamas; kiek pavyzdziu grupei, kad rodiklis butu patikimas.
    # Kol kas tik matuojama – sprendimams nenaudojama.
    "SALE_FACT_DAYS": 7,
    "SALE_FACT_MIN_SAMPLES": 30,
    # Rasyti ilgalaiki rinkos archyva (archive/market-YYYY-MM.jsonl.gz). Is jo tikrinama, ar
    # rinkos kaina ir jos patikimumas pasiteisina: `python -m vinted.dataset`.
    "ARCHIVE_ENABLED": True,
    "SEEN_MAX_AGE_DAYS": 7,
    "SEEN_MAX_ENTRIES": 20000,
}

# Senu versiju raktai -> nauji
_RENAMED = {"PRICE_HISTORY_MAX": None, "MIN_PRICE_RATIO": None}

cfg = dict(DEFAULTS)

# Jei config.json nepavyko perskaityti – kodel (pranesama Telegram'e, kad nelikt nepastebeta)
load_error = ""


def load(path=CONFIG_FILE):
    """Ikelia config.json i `cfg` (vietoje). Grazina cfg."""
    global load_error
    load_error = ""
    cfg.clear()
    cfg.update(json.loads(json.dumps(DEFAULTS)))
    if not os.path.exists(path):
        print(f"! {path} nerastas – naudojami numatytieji.")
        return cfg
    try:
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
        if not isinstance(user, dict):
            raise ValueError("ne JSON objektas")
        unknown = [k for k in user if k not in DEFAULTS and k not in _RENAMED]
        cfg.update({k: v for k, v in user.items() if k in DEFAULTS})
        print(f"Konfiguracija ikelta is {path}")
        if unknown:
            print(f"  (nezinomi raktai ignoruojami: {', '.join(unknown)})")
    except Exception as e:
        load_error = str(e)
        print(f"! Nepavyko nuskaityti {path} ({e}) – naudojami numatytieji.")
    return cfg


# Raktai, kuriuos galima keisti Telegram komandomis
OVERRIDABLE = {"MIN_PROFIT_EUR", "MIN_DISCOUNT", "MIN_BATTERY", "LOUD_DISCOUNT", "MARKET_PRICES", "PAUSED", "TIDY_ONLY",
               "MARKET_PERCENTILE", "SHOW_PROFIT", "AUTO_CALIBRATE", "DEAL_MODE", "RANK_TOP_PCT", "MODELS",
               "MAX_ALERTS_PER_RUN"}


def apply_overrides(overrides):
    """Telegram komandomis nustatytos reiksmes turi pirmenybe pries config.json."""
    for key, value in (overrides or {}).items():
        if key not in OVERRIDABLE:
            continue
        if key == "MARKET_PRICES":
            merged = dict(cfg.get("MARKET_PRICES") or {})
            for k, v in value.items():
                if v is None:
                    merged.pop(k, None)
                else:
                    merged[k] = v
            cfg["MARKET_PRICES"] = merged
        else:
            cfg[key] = value


def market_prices():
    out = {}
    for k, v in (cfg.get("MARKET_PRICES") or {}).items():
        try:
            if float(v) > 0:
                out[str(k).strip()] = float(v)
        except (TypeError, ValueError):
            pass
    return out


def allowed_languages():
    return {str(x).upper() for x in cfg["ALLOWED_LANGUAGES"]} | {"LT"}


def brand_queries(configured):
    """Paieskos frazes: kas nurodyta rankomis, o jei tuscia – pagal ijungtus gamintojus."""
    from .catalog import enabled, queries
    manual = [str(q) for q in (configured or []) if str(q).strip()]
    return manual or queries(enabled(cfg.get("BRANDS")))
