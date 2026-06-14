Architektura
============

Projekt jest trojwarstwowy — logika jest niezalezna od interfejsu, dzieki czemu
CLI, bot Telegram i monitor (cron) wolaja ten sam kod, bez duplikacji.

::

   PREZENTACJA   main.py (CLI)   bot/ (Telegram)   cron_check.py (monitor)
                         |
   LOGIKA         services/ — search, query, alert, analytics, find, watch, weather
                         |
   DANE          db.py (SQLite)        flights.py (Playwright + parser)

Warstwy
-------

**Prezentacja** — cienkie interfejsy. Parsuja wejscie uzytkownika i formatuja
wynik, ale nie zawieraja logiki biznesowej.

**Logika** (``services/``) — czyste funkcje zwracajace dataklasy. Nie printuja,
nie koncza procesu, nie wiedza kto je wola. Jeden serwis = jedna odpowiedzialnosc.

**Dane** — ``db.py`` (warstwa SQLite) oraz ``flights.py`` (scraping przez
Playwright + parser ofert regexami).

Kluczowe decyzje
----------------

* **Model 2-nogowy** — kazda oferta ma najwyzej dwie nogi (tam, powrot). Tylko
  loty bezposrednie (``maxChng=0``), dzieki czemu parser jest zawsze poprawny.
* **Jedna jednostka analityczna** — "cena jednego przelotu" laczy one-waye i nogi
  round-tripow, co pozwala porownywac loty miedzy soba.
* **Walidacja przed kosztem** — kody IATA i daty sa sprawdzane zanim ruszy
  kosztowny scraping.
* **Model sugeruje, kod decyduje** — odpowiedz LLM (jezyk naturalny) jest twardo
  walidowana zanim cokolwiek z niej zrobimy.
