# -*- coding: utf-8 -*-
"""
iPhone deal finder v34.

Iesko iPhone 8 … 17 Pro Max, kurie pigesni uz rinkos kaina (ivertinus bukle,
baterija ir defektus), ir siuncia juos i Telegram.

Saltiniai ijungiami config.json rakte "SOURCES" (dabar: vinted, pirkpard; skelbiu paruostas, bet isjungtas). Naujas saltinis –
vienas failas vinted/sources/, zr. README.

Paleidimas:   python vinted_deal_finder.py
Reikia:       pip install requests curl_cffi
Aplinka:      BOT_TOKEN, CHAT_ID (GitHub Secrets)
Failai:       config.json (nustatymai), seen.json ir state.json (issaugomi tarp paleidimu)
Testai:       python -m unittest discover tests
"""

import sys

try:
    from vinted.finder import main
except Exception as e:      # dazniausiai – ikeltas ne visas atnaujinimas arba failas ne tame aplanke
    print("!" * 70)
    print(f"Nepavyko uzkrauti programos: {type(e).__name__}: {e}")
    print("Greiciausiai GitHub'e truksta failo arba jis ne tame aplanke.")
    print("Turi buti: vinted/finder.py, vinted/commands.py, vinted/sources/pirkpard.py ir t. t.")
    print("Sprendimas: ikelti VISA naujausio zip turini (su aplankais).")
    print("!" * 70)
    raise

if __name__ == "__main__":
    sys.exit(main())
