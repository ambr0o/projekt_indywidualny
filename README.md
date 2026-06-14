# travel_agent

**Monitor cen lotów** na Telegramie. Ustawiasz obserwację trasy z progiem ceny,
a bot sam sprawdza ceny w tle i powiadamia gdy spadną — bez ręcznego sprawdzania.
Pod spodem: scraping AZair, własna historia cen w SQLite, analiza cenowa i pogoda
w destynacji.

Główna idea: **push zamiast pull** — nie pytasz „ile teraz kosztuje", tylko
ustawiasz raz i dostajesz alert gdy jest okazja.

## Architektura

Trójwarstwowa — logika niezależna od interfejsu, więc CLI, bot i cron wołają
ten sam kod (zero duplikacji).

```
PREZENTACJA   main.py (CLI)   bot/ (Telegram)   cron_check.py (monitor)
                      |
LOGIKA         services/ — search, query, alert, analytics, find, watch, weather
                      |
DANE           db.py (SQLite)        flights.py (Playwright + parser)
```

| Plik / katalog | Rola |
|----------------|------|
| `generate_url.py` | Generator URL AZair (round-trip / one-way / Anywhere) |
| `flights.py` | Scraping (Playwright) + parser ofert |
| `db.py` | Warstwa danych SQLite |
| `services/` | Logika biznesowa (jeden serwis = jedna odpowiedzialność) |
| `main.py` | Interfejs CLI |
| `bot/` | Bot Telegram |
| `cron_check.py` | Monitor: sprawdza obserwacje, wysyła alerty |
| `data/airports.json` | Mapa lotnisk: etykieta AZair + współrzędne (pogoda) |

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
CRON_INTERVAL_HOURS=6                       # jak często monitor sprawdza ceny
```

`.env` jest w `.gitignore` — token nie trafia do repozytorium.

## Monitor cen — główny przepływ

```
1. /watch WAW TIA 60          ustawiasz obserwację (próg 60 EUR)
2. cron_check.py --loop       monitor sprawdza w tle co N godzin
3. push na Telegram           gdy cena <= próg: alert z lotem, datami, percentylem
```

Uruchomienie monitora (demon, cross-platform):

```bash
python cron_check.py          # jednorazowe sprawdzenie (test / harmonogram systemowy)
python cron_check.py --loop   # demon: sprawdza co CRON_INTERVAL_HOURS
```

## Bot Telegram

```bash
python -m bot.telegram_bot
```

Komendy:

| Komenda | Opis |
|---------|------|
| **Monitor** | |
| `/watch <skąd> <dokąd> <próg> [data-od data-do] [oneway] [always]` | obserwuj cenę, powiadom gdy spadnie |
| `/mywatches` | twoje obserwacje |
| `/unwatch <numer>` | usuń obserwację |
| **Wyszukiwanie** | |
| `/find WAW TIA 2026-08-02 2026-08-09 [oneway]` | znajdź loty (+ pogoda celu) |
| `/find GDN anywhere 2026-07-01 2026-08-31` | tryb Anywhere — dowolny kierunek |
| **Analityka** | |
| `/rank KRK` | najtańsze kierunki z lotniska |
| `/stats WAW TIA <data>` | historia ceny tego lotu w czasie (jak się zmieniała) |
| `/leg WAW TIA` | cena pojedynczego przelotu + asymetria tam/powrót |
| `/compare WAW TIA` | zmiana ceny przelotu między 2 ostatnimi wyszukiwaniami |
| `/weather TIA 8` | typowa pogoda dla lotniska w danym miesiącu |

Tryby `/watch`:
- domyślnie **alert** — powiadom tylko gdy cena ≤ próg (z anty-spamem)
- **always** — cena przy każdym sprawdzeniu (śledzenie)
- daty opcjonalne (domyślnie ~2 miesiące); wąskie okno = konkretny termin,
  szerokie = elastyczne łowienie okazji

## Model danych

Tabele połączone relacją 1:N:

- **`search_runs`** — jedno wyszukiwanie (kiedy, parametry, status)
- **`flight_offers`** — oferty z danego wyszukiwania
- **`watched_routes`** — obserwacje cenowe per użytkownik (chat_id, próg, daty, tryb)
- **`weather_cache`** — cache pogody (iata, miesiąc)

Każda oferta = jedna **podróż**:
- round-trip — `return_date` + ceny obu nóg (`outbound_price`, `return_price`)
- one-way — `return_date = NULL`, puste pola powrotu

Ceny cząstkowe nóg (`subPrice` z AZair) umożliwiają **analitykę kierunkową** —
liczenie ceny pojedynczego przelotu, niezależnie czy z one-waya czy z nogi
round-tripa. Pozwala to porównywać loty w jednej jednostce i pokazać asymetrię
kierunku (tam vs powrót). Na tym też opiera się `/compare`.

Baza jednowalutowa (EUR).

## Zakres (świadome decyzje)

- **tylko loty bezpośrednie** (`maxChng=0`) — model 2-nogowy zawsze poprawny;
  przesiadki (zmienna liczba segmentów) wykluczone
- **wyszukiwanie per lotnisko** — `/watch WAW` szuka z konkretnego lotniska,
  nie scala wszystkich lotnisk miasta

## Pogoda

Typowa pogoda klimatyczna z Open-Meteo (darmowe, bez klucza). Loty są zwykle
poza zasięgiem prognozy (16 dni), więc używamy danych historycznych z poprzedniego
roku dla tego samego miesiąca. Wyniki cache'owane w SQLite.

## Status

Działa: monitor cen (watch + cron + alerty), wyszukiwanie, ranking kierunków,
cena przelotu, porównanie, pogoda.

W rozwoju: analityka historyczna (percentyl/trendy) nabiera dokładności w miarę
jak monitor zbiera dane w czasie — infrastruktura gotowa, potrzebne dłuższe okno
obserwacji.
