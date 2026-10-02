# -*- coding: utf-8 -*-
"""Ilgalaikis rinkos archyvas – kiekvienas stebejimas ir busenos pokytis.

state.json yra darbine atmintis: aktyvus skelbimai is jos isvalomi po PRICE_HISTORY_DAYS
(30 d.), parduoti – po SOLD_HISTORY_DAYS (60 d.). To pakanka sprendimams, bet ne tam, kad
butu galima patikrinti, ar rinkos kaina ir jos patikimumas (vinted/confidence.py) apskritai
pasiteisina – po dvieju menesiu tiesos nebelieka.

Todel cia rasomi IVYKIAI, kurie niekada neperrasomi ir nevalomi (JSON Lines, gzip, po
faila per diena – archive/market-2026-10-01.jsonl.gz):

  obs     pirma karta pamatytas telefonas: kaina, rinkos kaina, jos saltinis, imtis, sklaida
          ir patikimumas TUO METU (ne veliau perskaiciuoti – kitaip tikrintume save pagal save)
  price   kaina pasikeite
  status  pardavimo patikra: sold (puslapis sako „parduota“), gone (dingo), relist (ikeltas is naujo)
  x       atmestas rinkai (uzrakintas, uzsienio, defektai...)
  alert   issiustas pranesimas

Kiekvienas paleidimas prideda nauja gzip „nari“ failo gale – gzip tai leidzia, o skaitant
visi nariai perskaitomi kaip vienas srautas. Taip nereikia perrasyti viso failo.
Vienas ivykis ~120 B, suspaustas ~25 B: ~10 tukst. ivykiu per diena = ~250 KB per diena.
Failas – DIENOS, ne menesio: busenos saka kas paleidima perrasoma visa (--force), tad
menesio failas mėnesio gale kas 10 min. butu keliamas is naujo (~8 MB), o dienos – ne
daugiau nei ~250 KB.
"""

import glob
import gzip
import json
import os
import time

from . import config


def _path(directory, when):
    return os.path.join(directory, time.strftime("market-%Y-%m-%d.jsonl.gz", time.gmtime(when)))


def append(events, directory=None, now=None):
    """Prideda ivykius prie sios dienos failo. Grazina, kiek irasyta."""
    if not events or not config.cfg.get("ARCHIVE_ENABLED", True):
        return 0
    directory = directory or config.ARCHIVE_DIR
    now = time.time() if now is None else now
    os.makedirs(directory, exist_ok=True)
    lines = "".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n" for e in events)
    with gzip.open(_path(directory, now), "ab") as f:
        f.write(lines.encode("utf-8"))
    return len(events)


def read(directory=None):
    """Visi archyvo ivykiai chronologine tvarka (failai pagal diena).

    Sugadinta failo pabaiga (nutrauktas irasymas) neturi sugadinti viso archyvo –
    perskaitom, kiek pavyksta, ir einam prie kito failo."""
    directory = directory or config.ARCHIVE_DIR
    for path in sorted(glob.glob(os.path.join(directory, "market-*.jsonl.gz"))):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
        except (OSError, EOFError) as e:
            print(f"! Archyvo failas {path} nuskaitytas ne visas: {e}")
