# -*- coding: utf-8 -*-
"""Telefono atpazinimas: modelis, priedai, defektai, talpa, baterija, vertes daugikliai."""

import re

from . import catalog, config
from .util import fold

APPLE = catalog.APPLE
ALLOWED_MODELS = set(catalog.APPLE_MIN_PRICES)
# Modeliu tvarka (sarasams /kainos komandoje) – visi ijungti gamintojai
MODEL_ORDER = catalog.order()

# Po kartos numerio – ne skaitmuo: „iPhone 128GB“ nera iPhone 12. „x“ – atskiras zodis:
# „iphone xiaomi“ nera iPhone X. „iPhone 17 Air“ – tai Air, ne 17.
_MODEL_RE = re.compile(
    r"\biphone\s*(?P<gen>1[1-7](?!\d)|8(?!\d)|xs(?=max|\b)|xr\b|x\b|air\b)\s*(?P<e>e\b)?\s*"
    r"(?P<var>pro\s*max|promax|pro|max|plus|\+|mini|air\b)?"
)
_VARIANTS = {"promax": "Pro Max", "pro": "Pro", "max": "Max", "plus": "Plus", "+": "Plus", "mini": "mini",
             "air": "Air", "": ""}


# Kartos pavadinimai pavadinime (su "iphone" arba be jo): "13", "15pro", "8plus", "6s", "XR"
_GEN_TOKEN_RE = re.compile(
    r"(?<![\w.])(?P<pre>(?:apple\s+)?iphone\s*)?(?P<gen>6s|1[0-7]|[678]|xs|xr|x|se)"
    r"(?P<var>\s*(?:pro\s*max|promax|pro|plus|max|mini)|\+)?(?![\w])"
)


def _generations(t):
    """Skirtingu iPhone kartu rinkinys pavadinime (kelioms kartoms = rinkinys/dezutes)."""
    strong, weak = set(), set()
    for m in _GEN_TOKEN_RE.finditer(t):
        if t[max(0, m.start() - 4):m.start()].strip().endswith("ios"):
            continue
        gen = m.group("gen")
        if m.group("pre") or m.group("var"):
            var = re.sub(r"\s+", "", m.group("var") or "").replace("promax", "pro max").replace("+", "plus")
            strong.add(f"{gen} {var}".strip())
        elif gen.isdigit() or gen == "6s":
            weak.add(gen)
    # "13" ir "13 pro" – skirtingi modeliai; bet "iPhone 13 128GB, 13 mėn." – ne
    if len(strong) + len(weak) >= 3:
        return strong | weak
    return strong


def detect_model(title):
    """'Apple iPhone 13ProMax 256GB' -> '13 Pro Max', 'Samsung S24 Ultra' -> 'Galaxy S24 Ultra'.

    None, jei modelio nera arba paminėti keli skirtingi (ir to paties gamintojo, ir
    skirtingu: „iPhone ar Samsung dekliukai“ nera telefonas)."""
    apple = _detect_apple(title)
    others = catalog.find_models(title, enabled_brands())
    found = ({apple} if apple else set()) | others
    return found.pop() if len(found) == 1 else None


def enabled_brands():
    return catalog.enabled(config.cfg.get("BRANDS"))


def _detect_apple(title):
    """Apple atpazinimas: atskiros taisykles, kuriu kiti gamintojai neturi."""
    if APPLE not in enabled_brands():
        return None
    t = fold((title or "").lower()).replace("i phone", "iphone")
    if len(_generations(t)) >= 2:
        return None
    found = set()
    for m in _MODEL_RE.finditer(t):
        gen = m.group("gen")
        gen = gen.upper() if gen in ("x", "xr", "xs") else ("Air" if gen == "air" else gen)
        if m.group("e") and gen.isdigit():
            gen += "e"
        var = _VARIANTS[(m.group("var") or "").replace(" ", "")]
        if var == "Air":
            name = "Air"                     # „iPhone 17 Air“ – taip daznai vadinamas iPhone Air
        elif gen == "XS" and var == "Max":
            name = "XS Max"
        elif var == "Max":
            name = f"{gen} Pro Max"          # "iPhone 13 Max" dazniausiai reiskia Pro Max
        else:
            name = f"{gen} {var}".strip()
        if name in ALLOWED_MODELS:
            found.add(name)
    return found.pop() if len(found) == 1 else None


def normalize_model_name(text):
    """Vartotojo ivestis -> modelio ID arba None.

    Priimam ir su gamintoju („galaxy s24 ultra“, „xiaomi 14“), ir be jo („13 pro max“,
    „s24 ultra“, „fold 5“). Kai be gamintojo tinka keli, laimi ankstesnis BRANDS sarase:
    „14“ yra iPhone 14, o Xiaomi reikia parasyti „xiaomi 14“."""
    raw = (text or "").strip()
    if not raw:
        return None
    direct = detect_model(raw)
    if direct:
        return direct
    for brand in enabled_brands():
        if not brand.shorthand:
            continue                   # „pixel 9“, „xiaomi 14“ – gamintoja reikia parasyti
        model = detect_model(f"{brand.keywords[0]} {raw}")
        if model:
            return model
    return None


_WANTED_CACHE = {}


def wanted_models():
    """Modeliai, apie kuriuos siunciam (config MODELS arba /modeliai). Tuscias = visi.

    Grazina ta pacia tvarka, kokia ivedė zmogus, be pasikartojimu. Reiksmes normalizuojamos,
    tad config.json galima rasyti ir „13 pro max“, ir „13 Pro Max“."""
    raw = tuple(str(x) for x in (config.cfg.get("MODELS") or []))
    if raw not in _WANTED_CACHE:
        out = []
        for value in raw:
            model = normalize_model_name(value)
            if model and model not in out:
                out.append(model)
        _WANTED_CACHE.clear()          # kesas tik paskutinei reiksmei – jos keiciasi retai
        _WANTED_CACHE[raw] = out
    return _WANTED_CACHE[raw]


def model_wanted(model):
    wanted = wanted_models()
    return not wanted or model in wanted


# Zodziai BET KUR pavadinime, reiskiantys, kad parduodamas ne veikiantis telefonas.
# „ne kopija“ / „ne replika“ – priesingai, pabrezia, kad telefonas originalus.
NON_PHONE_RE = re.compile(
    r"\b(?:paveiksl\w*|remel\w*|frame\w*|framed|wall art|art\b|dekor\w*|muliaz\w*|dummy|maket\w*|"
    r"detal\w*|dalys|dalim\w*|housing|plokst\w*|motherboard|logic board|mainboard|"
    r"lcd|display|ekran\w* (?:iphone|keitim\w*)|tik dezut\w*|dezute be telefono|box only|empty box|"
    r"tuscia dezut\w*|lipduk\w*|sticker\w*|skin\b|clone)"
    r"|(?<!\bne )\b(?:replik\w*|replica|kopij\w*)"
)
# Silpni dalies pozymiai: „iPhone 11 korpusas“ – dalis, bet „iPhone 13, korpusas be ibrezimu“
# ar „all parts original“ – telefono aprasymas. Sprendziam pagal tai, kas eina po zodzio.
_WEAK_PART_RE = re.compile(r"\b(?:korpus\w*|parts?)\b")
_PART_DESCRIBES_PHONE = re.compile(
    r"^(?:\W+\w+){0,2}?\W+(?:be (?:jokiu )?(?:ibrez|defekt|skilim|subraiz|nubraiz|pazeid|dauz)\w*|"
    r"su (?:ibrez|smulk)\w*|ideal\w*|tvarking\w*|ger\w*|puik\w*|sveik\w*|nesubraiz\w*|nesudauz\w*|"
    r"nubrozin\w*|nusitryn\w*|svar\w*|good|great|perfect|mint|excellent)\b")
_PARTS_ORIGINAL = re.compile(r"^\W+(?:original\w*|work\w*|ok)\b")     # „all parts original / working“


def _weak_part(t):
    """True, jei „korpusas“ / „parts“ pavadinime reiskia parduodama dali, o ne telefona."""
    if _HAS_STORAGE_RE.search(t):
        return False                  # talpa pavadinime – telefonas („128GB, korpusas idealus“)
    for m in _WEAK_PART_RE.finditer(t):
        after = t[m.end():]
        if _PART_DESCRIBES_PHONE.match(after):
            continue
        if m.group(0).startswith("part") and _PARTS_ORIGINAL.match(after):
            continue
        return True
    return False


# Priedo zodis bet kurioje pavadinimo vietoje: „Iphone 17 pro case“, „iPhone 13 MagSafe deklas“.
# „stiklas“ cia nera – „iPhone 13, stiklas iskiles“ yra telefonas.
ACCESSORY_ANYWHERE_RE = re.compile(
    r"\b(?:case|cases|cover|dekl\w*|dekliuk\w*|krovikl\w*|charger|kabel\w*|cable|laidas|laidai|"
    r"wallet|pinigin\w*|magsafe (?:case|dekl\w*|krovikl\w*|charger|wallet|pinigin\w*|stoveli\w*))\b")
# Telefonas su priedu: „iPhone 13 + deklas“, „su dekliuku“, „deklas dovanu“
_BUNDLE_RE = re.compile(r"\+|&|\b(?:su|with|ir|bei|kartu|plius|komplekt\w*|dovan\w*|pridedu|pridedam\w*|"
                        r"gratis|free|incl\w*|included|iskaitant)\b")
_HAS_STORAGE_RE = re.compile(r"\b(?:64|128|256|512)\s?(?:gb|g)\b|\b1\s?tb\b")


def _accessory_anywhere(t):
    m = ACCESSORY_ANYWHERE_RE.search(t)
    if not m:
        return False
    # Talpa pavadinime – beveik visada telefonas (dekliukai talpos neturi)
    if _HAS_STORAGE_RE.search(t):
        return False
    # „+ deklas“, „su deklu“, „deklas dovanu“ – telefonas su priedu
    return not _BUNDLE_RE.search(t)


def is_accessory(title):
    """Priedas ar ne telefonas: 'Dėklas iPhone 13', 'Paveikslas iPhone X', 'iPhone 12 detalės'."""
    t = fold((title or "").lower()).strip()
    first = re.split(r"\W+", t, maxsplit=1)[0] if t else ""
    words = [str(w).lower() for w in config.cfg["ACCESSORY_FIRST_WORDS"]]
    if first and any(first.startswith(w) for w in words):
        # „Baterija 100% iPhone 13“ – telefonas, pavadinime tik pabreztas baterijos procentas
        if not (first.startswith(("baterij", "battery")) and re.search(r"\d{2,3}\s?%", t)):
            return True
    if NON_PHONE_RE.search(t) or _weak_part(t):
        return True
    if _accessory_anywhere(t):
        return True
    return bool(_FOR_PHONE_RE.search(t))


# „for iPhone 13“, „skirtas Samsung Galaxy S23“, „tinka Xiaomi 14“ – tai priedas telefonui
_FOR_PHONE_RE = re.compile(
    r"\b(?:for|skirtas|skirta|skirti|tinka|compatible|fur|pour|per)\s+"
    r"(?:apple\s+|samsung\s+|google\s+)?"
    r"(?:iphone|galaxy|samsung|xiaomi|redmi|poco|pixel|one\s?plus|huawei)")


# Stiprios frazes APRASYME, kad parduodamas ne telefonas (dezutes, detales, keli vnt.).
# Atsargiai su linksniais: „tik dėklas“ (parduodamas tik deklas) – taip, bet „laikytas tik
# dėkle“ – ne. „iPhone 13 telefoną“ – ne lotas; „2 telefonai“ / „3 vnt. dėžučių“ – lotas.
# „kopija“ cia nera: „pridedu čekio kopiją“, „originalus, ne kopija“ – iprasti sakiniai
# (kopijos pozymis lieka rizikos ivertinime).
DESCRIPTION_NOT_PHONE_RE = re.compile(
    r"\b(?:tusci\w* dezut\w*|dezut\w* be telefon\w*|empty box\w*|box only|only (the )?box|"
    r"tik dezut(?:e|es)\b|be telefono\b(?! dekl)|telefono nera|telefonas nepridedamas|phone not included|"
    r"tik korpus(?:as|a)\b|tik ekran(?:as|a|ai)\b|tik dekl(?:as|ai|a|us)\b|"
    r"paveiksl(?:as|ai|a|o|u|us)\b|framed|kaina uz vis(?:us|as)\b|uz visus kartu|lotas|lot of|"
    r"muliaz\w*|dummy|detalem\w*|atsargin\w* dal\w*|"
    # Vinted apgavyste: skelbime telefonas, o parduodamas tik popieriaus lapas / nuotrauka
    r"a4 lapas|a4 formato|lap\w* su (siais |tokiais )?vaizdais|siuntoje (rasite|gausite) tik|"
    r"gausite tik (lapa|lapas|nuotrauk\w*|foto|spaudin\w*|popieri\w*)|"
    r"parduodam\w* tik (lapa|lapas|lapelis|nuotrauk\w*|foto|spaudin\w*|popieri\w*|aprasym\w*)|"
    r"tik (popieri\w* )?lapas|tik lapelis|spausdint\w* (lapas|nuotrauk\w*)|popieri\w* lapas|"
    r"sheet of paper|printed (photo|picture|paper)|you are buying (a )?(photo|picture|paper|sheet)|"
    r"not the phone|nera telefonas)"
    r"|(?<!\bne )\b(?:replika|replica)\b"
    # keli vienetai: skaicius 2..99 + daugiskaita; ne po „iphone“ (modelio nr.) ir ne „turiu 2 telefonus“
    r"|(?<!iphone )(?<!turiu )\b(?:[2-9]|[1-9]\d)\s?(?:vnt\.?\s?)?(?:telefon(?:ai|u|us)|dezut(?:es|ciu))\b"
)


def description_not_phone(description):
    return bool(DESCRIPTION_NOT_PHONE_RE.search(fold((description or "").lower())))


# Minimali realistiška tvarkingo telefono kaina (~45% iprastos naudoto kainos).
# Pigiau = beveik visada dezute, dalys, sugedes ar apgavyste. Keiciama config.json "MODEL_MIN_PRICES".
# Lentele – vinted/catalog.py (visi gamintojai vienoje vietoje).
DEFAULT_MIN_PRICES = {model: price for brand in catalog.BRANDS
                      for model, price in brand.prices.items()}


def typical_price(model):
    """Apytiksle iprasta tvarkingo naudoto telefono kaina.

    Nuo v50 imama is `catalog.prior()`, o ne isvedama is ribos. Praktinis
    skirtumas: kainos patikslinimas nebejudina apgavysciu ribos, o riba
    nebeiskreipia kainos. Zr. catalog.GUESS_RATIO komentara - tikrasis
    santykis riba/mediana svyruoja 0.37..0.68, tad vienu daugikliu jo
    pakeisti neimanoma."""
    p = catalog.prior(model)
    return p.price if p else None


def typical_prior(model):
    """Tas pats skaicius, bet su imtimi ir data.

    `confidence.py` reikia zinoti, ar tai 1417 skelbimu mediana, ar spejimas
    is ribos - iki v49.1 abu atrode vienodai."""
    return catalog.prior(model)


def min_price(model):
    custom = config.cfg.get("MODEL_MIN_PRICES") or {}
    try:
        return float(custom[model]) if model in custom else float(DEFAULT_MIN_PRICES.get(model, 0))
    except (TypeError, ValueError):
        return float(DEFAULT_MIN_PRICES.get(model, 0))


def display(model):
    """Modelio ID -> kaip rodom zmogui: '13 Pro' -> 'iPhone 13 Pro', o
    'Galaxy S24 Ultra' lieka toks pat (gamintojas jau pavadinime)."""
    return catalog.display(model)


CONDITION_RANK = {"Nauja su etiketėmis": 5, "Nauja be etikečių": 4, "Labai gera": 3, "Gera": 2, "Patenkinama": 1}


def condition_ok(condition, minimum):
    """Nezinoma bukle praleidziama (tikrinama kitais budais)."""
    if condition not in CONDITION_RANK:
        return True
    return CONDITION_RANK[condition] >= CONDITION_RANK.get(minimum, 0)


# --- Defektai --------------------------------------------------------------
# (saknis be diakritiku, pavadinimas, vertes daugiklis). 0 = netinkamas (visada atmetama)
DEFECT_PATTERNS = [
    (r"icloud", "iCloud užraktas", 0.0),
    # ir daznos rasybos klaidos: „uzbluokuotas“, „blukuotas“, „uzlockintas“, „lokintas“
    (r"uzbl\w{0,3}k\w*|bl[aou]{1,2}kuot\w*|\blocked\b|uzrakint\w*|(?:uz)?loc?kint\w*",
     "užblokuotas", 0.0),
    # Be baterijos telefono neisbandysi. „be baterijos keitimo / problemu“ – ne tas pats.
    (r"(?:be|nera|truksta|neturi|isimt\w*|no|without|missing) (?:akum\w*|baterij\w*|batarej\w*|battery)\b"
     r"(?! (?:problem|keit|pakeit|bed|defekt|gedim|nusidev|susidev|sveikat|degrad|isnaud|issues?|replace|health)\w*)",
     "be baterijos", 0.0),
    (r"dalims|for parts|parts only|detalem\w*|donor\w*", "dalims", 0.0),
    # „kodą/kodo“, „PIN“, „PIN kodą“ – bet ne „kodėl“ ir ne „pinigų“ („reikia pinigų“ ≠ užrakintas)
    (r"(prasyt?\w*|praso|reikia|nezin\w*|pamirs\w*|ivesti|uzrakint\w*) (\w+ ){0,3}"
     r"(kod(?:as|o|a|u|ai|us)\b|slaptazod\w*|pin\b|pin\s?kod\w*)|"
     r"(kod(?:as|o|a|u|ai|us)|slaptazod\w*) (\w+ ){0,3}(nezin\w*|pamirs\w*)|passcode|activation lock|"
     r"aktyvacij\w* uzrakt\w*",
     "užrakintas kodu", 0.0),
    # „parduodu kaip yra“ = netestuotas; „būklė tokia, kaip yra nuotraukose“ – ne
    (r"netestuot\w*|neistestuot\w*|netikrint\w*|nepatikrint\w*|untested|not tested|nezinau ar veikia|"
     r"nezinom\w* ar veikia|sold as.is|kaip yra\b(?!,?\s*(?:nuotrauk|foto|matyt|matosi|pavaizd|aprasyt|parasyt))",
     "netestuotas", 0.0),
    (r"neisijung\w*|nesijung\w*|neuzsikraun\w*|nesikrauna\w*|nekrauna|nekraun\w*|won.?t turn on|"
     r"does ?n.?t turn on|no power|juodas ekranas|black screen|bootloop|persikraun\w*|uzstring\w*",
     "neįsijungia / nesikrauna", 0.0),
    (r"linij\w* (\w+ ){0,3}ekran\w*|ekran\w* (\w+ ){0,3}linij\w*|dem\w* (\w+ ){0,3}ekran\w*|"
     r"ekran\w* (\w+ ){0,3}dem\w*|green line|lines on screen|"
     r"mirga|mirkcioj\w*|neveikia liet\w*|lietimas neveik\w*|touch not working|ghost touch",
     "ekrano gedimas", 0.50),
    (r"be garantij\w* ir be\b|nezinau istorij\w*|rastas", "neaiški kilmė", 0.80),
    (r"i?skil\w*", "skilęs", 0.70),
    (r"sudauz\w*|dauzt\w*", "sudaužtas", 0.65),
    (r"cracked|crack", "įskilęs (cracked)", 0.70),
    (r"broken", "sugedęs (broken)", 0.55),
    (r"sugad\w*|sugedes|sugedo", "sugedęs", 0.55),
    (r"damaged|water damage|sulyt\w*|pasemt\w*|dregm\w*", "pažeistas", 0.60),
    (r"no face ?id|be face ?id|face ?id neveik\w*|neveik\w* face ?id", "neveikia Face ID", 0.75),
    (r"neveik\w*|not working|doesn.?t work", "kažkas neveikia", 0.70),
    (r"keist\w* ekran\w*|replaced screen|neoriginal\w* ekran\w*|ne originalus ekranas", "keistas ekranas", 0.85),
    (r"keist\w* baterij\w*|neoriginal\w* baterij\w*|service battery", "keista baterija", 0.92),
    (r"remont\w*|reikia keisti|taisyt\w*", "reikia remonto", 0.65),
    (r"defekt\w*", "defektai", 0.85),
    (r"ibrez\w*|subraiz\w*|scratch\w*|nubraiz\w*", "įbrėžimai", 0.95),
]
_DEFECT_RE = [(re.compile(r"\b(?:" + p + r")"), label, f) for p, label, f in DEFECT_PATTERNS]
_NE_IS_DEFECT = {"kažkas neveikia", "neveikia Face ID", "neįsijungia / nesikrauna", "netestuotas",
                 "užrakintas kodu", "ekrano gedimas", "neaiški kilmė", "be baterijos"}
_NEGATION_IS_DEFECT = {"be baterijos"}
# Neiginiai PRIES defekta: „be įskilimų“, „nebuvo daužtas“, „niekada nebuvo taisytas“
_NEGATE_BEFORE = {"be", "nera", "no", "not", "without", "jokiu", "jokio", "nieko", "neturi", "zero", "0",
                  "nebuvo", "niekada", "niekad", "nei", "never", "nebus"}
# Zodziai PO defekto, kurie ji paneigia: "iCloud atristas", "defektų neturi".
# Tokia formuluote lietuviskuose skelbimuose iprasta, todel ziurim kelis zodzius i prieki.
_NEGATE_AFTER = {"atristas", "atrista", "atrisiu", "atrisamas",
                 "atsietas", "atsieta", "atsiesiu", "atsiejamas",
                 "laisvas", "isjungtas", "isjungta", "nera", "free", "off",
                 "clean", "unlocked", "nepriristas", "neprisietas",
                 "atrakintas", "atrakinta", "pasalintas", "pasalinta",
                 "islogintas", "isloginta", "removed",
                 "neturi", "neturiu", "nepastebeta", "nepastebejau", "nerasta", "none"}
_NEGATE_AFTER_WORDS = 4
# Uzrakto etiketems paneigimas gali buti ir PRIES zodi: „atrištas nuo iCloud“, „iCloud švarus“.
_LOCK_LABELS = {"iCloud užraktas", "užblokuotas", "užrakintas kodu"}
_UNLINK_STEMS = ("atrist", "atsiet", "atjung", "atsijung", "islogin", "atsilogin", "isjung", "nepririst",
                 "neprisiet", "atrakint", "pasalint", "laisv", "svarus", "svari", "svaru", "clean", "free",
                 "unlock", "remov")
_LOCK_STEMS = ("uzrakint", "uzblok", "uzbl", "blokuot", "blukuot", "locked", "pririst", "prisiet")
# „Apsauginis stikliukas įskilęs, ekranas sveikas“ – skilo apsauga, ne telefonas
_CRACK_LABELS = {"skilęs", "įskilęs (cracked)", "sudaužtas"}
_PROTECTOR_STEMS = ("apsaug", "stikliuk", "plevel", "protector", "dekl", "case")


def _words(text):
    return [w.strip(",.;:!-()") for w in text.split()]


def find_defects(*texts):
    """[(pavadinimas, daugiklis), ...]. Ignoruoja paneigimus ('be įskilimų', 'iCloud atrištas')."""
    t = fold(" ".join(x for x in texts if x).lower())
    found = {}
    for rx, label, factor in _DEFECT_RE:
        for m in rx.finditer(t):
            clause = re.split(r"[.,;!?\n]|\bbet\b|\bbut\b", t[:m.start()])[-1]
            before_list = _words(clause)[-4:]
            before = set(before_list)
            after_list = _words(re.split(r"[.,;!?\n]|\bbet\b|\bbut\b", t[m.end():])[0])[:_NEGATE_AFTER_WORDS]
            after = set(after_list)
            inside = set(t[m.start():m.end()].split())
            # „be akumo“ – pats „be“ ir yra defektas, tad cia paneigimo nebetikrinam
            negation_is_defect = label in _NEGATION_IS_DEFECT
            if not negation_is_defect and (before & _NEGATE_BEFORE or inside & _NEGATE_BEFORE):
                continue
            if label in _LOCK_LABELS:
                # „iCloud užrakintas bus atrištas“ – pirmas zodis po jo sako, kad DABAR uzrakintas:
                # tolesni pazadai atristi jo nepaneigia
                locked_now = bool(after_list) and after_list[0].startswith(_LOCK_STEMS)
                if not locked_now and (any(w.startswith(_UNLINK_STEMS) for w in before_list + after_list)
                                       or after & _NEGATE_AFTER):
                    continue
            else:
                if label in _CRACK_LABELS and any(w.startswith(_PROTECTOR_STEMS) for w in before_list):
                    continue
                if after & _NEGATE_AFTER:
                    continue
            # "nesudaužytas", "neskilęs" = paneigimas; bet "neveikia", "neįsijungia" – pats defektas
            if label not in _NE_IS_DEFECT and t[m.start():m.end()].startswith("ne"):
                continue
            found.setdefault(label, factor)
            break
    if "neveikia Face ID" in found:
        found.pop("kažkas neveikia", None)
    return list(found.items())


def extract_storage(*texts):
    t = " ".join(x for x in texts if x).lower()
    m = re.search(r"\b(64|128|256|512)\s?(?:gb|g|gigabait\w*)\b", t)
    if m:
        return f"{m.group(1)} GB"
    if re.search(r"\b1\s?tb\b", t):
        return "1 TB"
    return None


def normalize_storage(text):
    t = (text or "").lower().replace(" ", "")
    m = re.fullmatch(r"(64|128|256|512)(gb|g)?", t)
    if m:
        return f"{m.group(1)} GB"
    if t in ("1tb", "1024", "1024gb"):
        return "1 TB"
    return None


def extract_battery(*texts):
    t = fold(" ".join(x for x in texts if x).lower())
    # „87%“, „87 proc.“, „87 procentai“
    pct = r"\s?(?:%|proc\b\.?|procent\w*)"
    pats = [
        r"(?:baterij\w*|akumuliator\w*|battery(?: health)?|\bbh\b|\bbat\b\.?|sveikat\w*|talpa)\D{0,20}?(\d{2,3})"
        + pct,
        r"(\d{2,3})" + pct + r"\s?(?:baterij\w*|battery|\bbh\b|sveikat\w*|talp\w*)",
    ]
    for pat in pats:
        m = re.search(pat, t)
        if m and 50 <= int(m.group(1)) <= 100:
            return int(m.group(1))
    return None


# --- Verte ------------------------------------------------------------------
CONDITION_FACTOR = {
    "Nauja su etiketėmis": 1.15, "Nauja be etikečių": 1.08, "Labai gera": 1.00,
    "Gera": 0.92, "Patenkinama": 0.80,
}


def battery_factor(battery):
    if battery is None:
        return 0.98                     # dauguma nenurodo – beveik nebaudziam
    if battery >= 95:
        return 1.03
    if battery >= 90:
        return 1.00
    if battery >= 85:
        return 0.97
    if battery >= 80:
        return 0.93
    return 0.85


def estimate_value(market, condition, battery, defects):
    """Konkretaus telefono verte = rinkos kaina x bukle x baterija x defektai."""
    value = market * CONDITION_FACTOR.get(condition, 0.95) * battery_factor(battery)
    for _, factor in defects:
        value *= factor
    return value


def estimate_profit(price, resale_value, pickup_only=False, buyer_fee=True, total_price=None):
    """Pelnas perpardavus: verte - (kaina + pirkejo apsaugos mokestis + siuntimas).

    `total_price` – tikra suma su mokesciu, kai saltinis ja pasako (Vinted pasako).
    `buyer_fee` – ar mokestis apskritai imamas (Skelbiu/Pirkpard – ne)."""
    c = config.cfg
    cost = price
    if total_price and total_price > 0:
        cost = float(total_price)
    elif buyer_fee:
        cost += c["BUYER_FEE_FIXED"] + price * c["BUYER_FEE_PCT"]
    if not pickup_only:
        cost += c["SHIPPING_COST"]
    return resale_value - cost
