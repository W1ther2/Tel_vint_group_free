# -*- coding: utf-8 -*-
"""
Vinted iPhone deal finder v16.

Iesko iPhone 8 … 17 Pro Max, kurie pigesni uz rinkos kaina (ivertinus bukle,
baterija ir defektus), ir siuncia juos i Telegram.

Paleidimas:   python vinted_deal_finder.py
Reikia:       pip install requests curl_cffi
Aplinka:      BOT_TOKEN, CHAT_ID (GitHub Secrets)
Failai:       config.json (nustatymai), seen.json ir state.json (issaugomi tarp paleidimu)
Testai:       python -m unittest discover tests
"""

from vinted.finder import main

if __name__ == "__main__":
    main()
