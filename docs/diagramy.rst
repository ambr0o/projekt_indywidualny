Diagramy UML
============

Diagramy opisuja realna strukture i przeplywy projektu travel_agent.

Diagram klas
------------

Glowne dataklasy (kontrakty miedzy warstwami) i serwisy.

.. mermaid::

   classDiagram
       class FlightIntent {
           +str action
           +str origin
           +str destination
           +str dep_date
           +str arr_date
           +bool oneway
           +float threshold
           +int min_days
           +int max_days
           +str error
           +is_anywhere() bool
       }
       class FindRequest {
           +str url
           +str error
           +str origin
           +str destination
           +bool is_anywhere
           +bool is_oneway
           +bool known_destination
       }
       class SearchResult {
           +int run_id
           +int offers_count
           +bool success
           +str error_message
       }
       class WatchedRoute {
           +int id
           +int chat_id
           +str origin
           +str destination
           +float threshold
           +str mode
           +from_db_row(row) WatchedRoute
       }
       class PriceHistory {
           +str origin
           +str destination
           +str departure_date
           +list points
           +change() float
           +trend() str
       }
       FlightIntent ..> FindRequest : mapowana na
       FindRequest ..> SearchResult : scraping zwraca

Diagram sekwencji
-----------------

Przeplyw zapytania w jezyku naturalnym: od wiadomosci uzytkownika do ofert.

.. mermaid::

   sequenceDiagram
       actor User
       participant Bot as bot/telegram_bot
       participant LLM as llm_service
       participant Find as find_service
       participant Search as search_service
       participant AZair
       User->>Bot: "lot z Krakowa do Barcelony w sierpniu"
       Bot->>LLM: parse_query(text)
       LLM-->>Bot: FlightIntent (zwalidowany)
       Bot->>Find: build_find_request(...)
       Find-->>Bot: FindRequest (URL)
       Bot->>Search: search_and_save(url)
       Search->>AZair: scraping (Playwright)
       AZair-->>Search: oferty (HTML)
       Search-->>Bot: SearchResult
       Bot-->>User: lista ofert + pogoda

Diagram aktywnosci
------------------

Przejscie monitora cen (cron) po jednej obserwacji.

.. mermaid::

   flowchart TD
       Start([Start cyklu]) --> Fetch[Pobierz aktywne obserwacje]
       Fetch --> Loop{Kolejna obserwacja?}
       Loop -- nie --> End([Koniec cyklu])
       Loop -- tak --> Build[Zbuduj URL i scrapuj]
       Build --> Ok{Sa wyniki?}
       Ok -- nie --> Loop
       Ok -- tak --> Cross{Cena spadla ponizej progu?}
       Cross -- nie --> Save[Zapisz last_price]
       Cross -- tak --> Alert[Wyslij push na Telegram]
       Alert --> Save
       Save --> Loop

Diagram przypadkow uzycia
-------------------------

.. mermaid::

   flowchart LR
       User((Uzytkownik))
       User --> UC1[Wyszukaj loty]
       User --> UC2[Obserwuj cene]
       User --> UC3[Analityka trasy]
       User --> UC4[Sprawdz pogode]
       User --> UC5[Zapytanie naturalnym jezykiem]

Diagram komponentow
-------------------

.. mermaid::

   flowchart TD
       bot[bot/telegram_bot] --> services[services/]
       cron[cron_check] --> services
       cli[main.py] --> services
       services --> db[db.py - SQLite]
       services --> flights[flights.py]
       services --> llm[llm_service - Ollama]
       flights --> azair[(AZair)]
       services --> meteo[(Open-Meteo)]

Diagram stanow
--------------

Cykl zycia wyszukiwania (``search_run``).

.. mermaid::

   stateDiagram-v2
       [*] --> started: start_search_run
       started --> done: oferty zapisane
       started --> failed: blad scrapingu / brak wynikow
       done --> [*]
       failed --> [*]
