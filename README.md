# travel_agent

Wyszukiwanie tanich lotów na AZair, zapis ofert w SQLite i porównywanie cen w czasie.

`test_url.py` i `main.py` są **osobne**: najpierw generujesz link, potem scrapujesz.

## Wymagania

```bash
python -m venv .venv
source .venv/bin/activate
pip install playwright
playwright install
```

## 1. Wygeneruj link (`test_url.py`)

```bash
python test_url.py \
  --src-label 'Warsaw [WAW] (+WMI,KTW,RZE,KRK)' \
  --src-codes WMI,KTW,RZE,KRK \
  --warsaw-src \
  --dst-label 'Milan [MXP] (+LIN,BGY)' \
  --dst-codes LIN,BGY \
  --dst-mc MIL_ALL \
  --dep 2026-05-24 \
  --arr 2027-01-31 \
  --currency EUR
```

`--warsaw-src` ustawia sloty `srcap` jak w przeglądarce (`0,6,7,8`). Własne: `--src-slots 0,6,7,8`.

## 2. Scrapuj i zapisz (`main.py`)

```bash
python main.py 'https://www.azair.eu/azfin.php?...'
# lub
python main.py search 'https://www.azair.eu/azfin.php?...'
```

## 3. Przegląd bazy

```bash
python main.py runs              # historia wyszukiwań
python main.py list              # ostatnie oferty
python main.py list --run-id 3   # oferty z konkretnego runu
python main.py best              # najtańsza oferta
python main.py compare           # porównanie 2 ostatnich udanych runów
python main.py compare --origin WMI --destination LIN
python main.py alert --threshold 50 --currency EUR
```

## Testy (bez Playwrighta)

```bash
python -m unittest tests/test_flights.py
```

## Pliki

| Plik | Rola |
|------|------|
| `test_url.py` | Generator linków AZair (CLI) |
| `main.py` | Scraping, baza, list/best/compare/alert |
| `flights.py` | Parsowanie i Playwright |
| `db.py` | SQLite |
| `database.db` | Dane (tworzy się przy pierwszym uruchomieniu) |

## Typowy workflow

```bash
# terminal 1 – link
python test_url.py --src-label '...' --dst-label '...' --dep ... --arr ...

# terminal 2 – scrape (wklej URL z kroku 1)
python main.py search 'URL'

# później
python main.py compare
```
