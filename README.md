# Vinted iPhone deal finder

Ieško iPhone 8 … 17 Pro Max, kurie pigesni už rinkos kainą, ir siunčia juos į Telegram.

## Failai

| Failas | Kas tai |
|---|---|
| `vinted_deal_finder.py` | paleidimo failas |
| `vinted/` | kodas (modulis per temą) |
| `tests/` | automatiniai testai (`python -m unittest discover tests`) |
| `config.json` | nustatymai |
| `seen.json`, `state.json` | sukuriami automatiškai, išsaugomi tarp paleidimų |
| `.github/workflows/vinted.yml` | GitHub Actions pavyzdys |

## Kodo struktūra

| Modulis | Atsakingas už |
|---|---|
| `config.py` | nustatymai, Telegram komandų pakeitimai |
| `client.py` | Vinted API: sesija, katalogas, skelbimo puslapis, pardavėjas |
| `parsing.py` | kaina, būklė, pardavėjo duomenys, ar skelbimas parduotas |
| `language.py` | kalbos atpažinimas |
| `phone.py` | modelis, priedai, defektai, talpa, baterija, vertė, pelnas |
| `risk.py` | apgavysčių požymiai |
| `market.py` | rinkos kainos, pardavimai, kainų sumažėjimai |
| `state.py` | `seen.json`, `state.json` |
| `telegram.py` | kortelės, žinutės, komandų gavimas |
| `commands.py` | Telegram komandos |
| `finder.py` | pagrindinė eiga |

## Telegram komandos

`/pagalba`, `/kaina 13 180`, `/kaina 13 Pro 256 250`, `/kaina 13 trinti`, `/kainos`,
`/nuolaida 20`, `/baterija 80`, `/garsas 30`, `/pauze`, `/testi`, `/nustatymai`

Komandos įvykdomos kito paleidimo metu. Botas turi būti grupėje, o jei naudotas webhook – jį reikia išjungti.
