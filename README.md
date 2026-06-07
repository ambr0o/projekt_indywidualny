# travel_agent

Agent do wyszukiwania tanich lotów, budowania własnej historii
cen w SQLite, analizy cenowej i interakcji przez bota Telegram.

Wyszukujesz loty prostą komendą, system zapisuje oferty, liczy statystyki,
porównuje kierunki i dorzuca typową pogodę w destynacji.

## Wymagania i instalacja

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install
```

## Konfiguracja

Skopiuj `.env.example` jako `.env` i uzupełnij:

```
TELEGRAM_BOT_TOKEN=<token od @BotFather>
TELEGRAM_ALLOWED_CHAT_IDS=<twoj chat_id>   # puste = każdy ma dostęp
TRAVEL_AGENT_DB=database.db
```

## Użycie — CLI

```bash

# przeglądanie
python main.py runs                       # historia wyszukiwań
python main.py list --limit 10            # ostatnie oferty
python main.py best                        # najtańsza oferta

# analityka
python main.py stats --origin WAW --destination TIA --price 30
python main.py rank --origin KRK           # najtańsze kierunki z lotniska
python main.py leg --origin WAW --destination TIA   # cena pojedynczego przelotu
python main.py compare --origin WAW --destination TIA
python main.py alert --threshold 50 --currency EUR
```

## Użycie — bot Telegram

```bash
python -m bot.telegram_bot
```

Komendy w czacie:

| Komenda | Opis |
|---------|------|
| `/find WAW TIA 2026-08-02 2026-08-09 [oneway]` | wyszukaj loty (z prostych parametrów, + pogoda celu) |
| `/find GDN anywhere 2026-07-01 2026-08-31` | tryb Anywhere — dowolny kierunek |
| `/best` | najtańsza oferta w bazie |
| `/list [limit]` | ostatnie oferty |
| `/runs [limit]` | historia wyszukiwań |
| `/rank KRK` | najtańsze kierunki z lotniska |
| `/stats WAW TIA [cena]` | statystyki trasy + ocena ceny (percentyl) |
| `/leg WAW TIA` | cena pojedynczego przelotu w obie strony + asymetria |
| `/compare WAW TIA` | porównanie dwóch ostatnich wyszukiwań |
| `/weather TIA 8` | typowa pogoda klimatyczna dla lotniska w danym miesiącu |
| `/alert <próg> [waluta]` | sprawdź próg cenowy |

## Model danych

Dwie tabele połączone relacją 1:N:

- **`search_runs`** — jedno wyszukiwanie (kiedy, jakie parametry, status)
- **`flight_offers`** — oferty znalezione w danym wyszukiwaniu

Każda oferta = jedna **podróż**:
- round-trip — ma `return_date` + ceny obu nóg (`outbound_price`, `return_price`)
- one-way — `return_date = NULL`, puste pola powrotu

Ceny cząstkowe nóg (`subPrice` z AZair) pozwalają na **analitykę kierunkową** —
liczenie ceny pojedynczego przelotu, niezależnie czy pochodzi z one-waya czy
z nogi round-tripa. Dzięki temu da się porównać loty w jednej jednostce
i pokazać asymetrię kierunku (lot tam vs powrót).

Baza jest jednowalutowa (EUR).

## Pogoda

Typowa pogoda klimatyczna z Open-Meteo. Loty są zwykle
1-2 miesiące do przodu — poza zasięgiem prognozy (16 dni) — więc używamy danych
historycznych z poprzedniego roku dla tego samego miesiąca ("typowa pogoda dla
okresu"). Wyniki cache'owane w SQLite.


## Status

Działa: wyszukiwanie, przeglądanie, ranking kierunków, cena przelotu, pogoda.
