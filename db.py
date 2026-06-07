import json
import sqlite3

ALLOWED_SEARCH_STATUSES = ["started", "done", "failed"]
DEFAULT_DB_PATH = "database.db"


def create_search_runs_table(conn):
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS search_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_mode TEXT NOT NULL,
            params_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def create_flight_offers_table(conn):
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS flight_offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_run_id INTEGER NOT NULL,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            departure_date TEXT NOT NULL,
            return_date TEXT,
            price REAL NOT NULL,
            currency TEXT NOT NULL,
            airline TEXT NOT NULL,
            flight_number TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (search_run_id) REFERENCES search_runs(id)
        )
        """
    )
    # Lekka migracja: dodaj nowe kolumny gdy ich brak (idempotentne)
    existing = {row[1] for row in cur.execute("PRAGMA table_info(flight_offers)").fetchall()}
    new_columns = {
        "airline_code": "TEXT",
        "return_airline": "TEXT",
        "return_airline_code": "TEXT",
        "return_flight_number": "TEXT",
        "outbound_price": "REAL",
        "return_price": "REAL",
    }
    for col, col_type in new_columns.items():
        if col not in existing:
            cur.execute(f"ALTER TABLE flight_offers ADD COLUMN {col} {col_type}")
    conn.commit()


def create_weather_cache_table(conn):
    """Cache pogodowy - klucz (iata, month), zeby nie pytac Open-Meteo wielokrotnie."""
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS weather_cache (
            iata TEXT NOT NULL,
            month INTEGER NOT NULL,
            temp_max REAL,
            temp_min REAL,
            rain_mm REAL,
            rainy_days INTEGER,
            kind TEXT,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (iata, month)
        )
        """
    )
    conn.commit()


def get_cached_weather(conn, iata, month):
    cur = conn.cursor()
    row = cur.execute(
        "SELECT temp_max, temp_min, rain_mm, rainy_days, kind FROM weather_cache WHERE iata=? AND month=?",
        (iata.upper(), month),
    ).fetchone()
    return row


def save_weather_cache(conn, iata, month, temp_max, temp_min, rain_mm, rainy_days, kind):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO weather_cache(iata, month, temp_max, temp_min, rain_mm, rainy_days, kind, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (iata.upper(), month, temp_max, temp_min, rain_mm, rainy_days, kind),
    )
    conn.commit()


def create_watched_routes_table(conn):
    """Obserwacje cenowe uzytkownikow (rdzen monitora cen).

    Kazdy wiersz = jeden uzytkownik (chat_id) obserwujacy jedna trase z progiem.
    last_price/last_checked uzupelniane przez cron przy kazdym sprawdzeniu.
    dep_date/arr_date - okno dat wylotu (NULL = domyslne okno crona).
    mode - 'alert' (gdy <= prog) lub 'always' (cena przy kazdym sprawdzeniu).
    """
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS watched_routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            threshold REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'EUR',
            oneway INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            last_price REAL,
            last_checked TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    # Lekka migracja: dodaj kolumny gdy brak (idempotentne)
    existing = {row[1] for row in cur.execute("PRAGMA table_info(watched_routes)").fetchall()}
    new_columns = {
        "dep_date": "TEXT",
        "arr_date": "TEXT",
        "mode": "TEXT NOT NULL DEFAULT 'alert'",
    }
    for col, col_type in new_columns.items():
        if col not in existing:
            cur.execute(f"ALTER TABLE watched_routes ADD COLUMN {col} {col_type}")
    conn.commit()


def insert_watched_route(conn, chat_id, origin, destination, threshold,
                         currency="EUR", oneway=False,
                         dep_date=None, arr_date=None, mode="alert"):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO watched_routes(chat_id, origin, destination, threshold, currency,
                                   oneway, active, created_at, dep_date, arr_date, mode)
        VALUES (?, ?, ?, ?, ?, ?, 1, datetime('now'), ?, ?, ?)
        """,
        (chat_id, origin.upper(), destination.upper(), threshold, currency,
         1 if oneway else 0, dep_date, arr_date, mode),
    )
    conn.commit()
    return cur.lastrowid


def fetch_watched_routes(conn, chat_id=None, only_active=True):
    """Obserwacje - wszystkie, danego uzytkownika, opcjonalnie tylko aktywne."""
    cur = conn.cursor()
    query = """
        SELECT id, chat_id, origin, destination, threshold, currency, oneway,
               active, last_price, last_checked, created_at, dep_date, arr_date, mode
        FROM watched_routes
        WHERE 1=1
    """
    args = []
    if chat_id is not None:
        query += " AND chat_id = ?"
        args.append(chat_id)
    if only_active:
        query += " AND active = 1"
    query += " ORDER BY id ASC"
    return cur.execute(query, args).fetchall()


def deactivate_watched_route(conn, watch_id, chat_id):
    """Dezaktywuje obserwacje - tylko jesli nalezy do danego chat_id (bezpieczenstwo).

    Zwraca True gdy cos zmieniono, False gdy nie znaleziono/nie nalezy do usera.
    """
    cur = conn.cursor()
    cur.execute(
        "UPDATE watched_routes SET active = 0 WHERE id = ? AND chat_id = ? AND active = 1",
        (watch_id, chat_id),
    )
    conn.commit()
    return cur.rowcount > 0


def update_watched_route_check(conn, watch_id, last_price):
    """Zapisuje wynik ostatniego sprawdzenia przez cron."""
    cur = conn.cursor()
    cur.execute(
        "UPDATE watched_routes SET last_price = ?, last_checked = datetime('now') WHERE id = ?",
        (last_price, watch_id),
    )
    conn.commit()


def insert_search_run(conn, search_mode, params, status):
    if status not in ALLOWED_SEARCH_STATUSES:
        raise ValueError(f"Invalid status '{status}'.")

    cur = conn.cursor()

    params_json = json.dumps(params, ensure_ascii=False)

    cur.execute(
        """
        INSERT INTO search_runs(search_mode, params_json, status, created_at)
        VALUES (?, ?, ?, datetime('now'))
        """,
        (search_mode, params_json, status),
    )
    conn.commit()
    return cur.lastrowid


def start_search_run(conn, search_mode, params):
    return insert_search_run(conn, search_mode=search_mode, params=params, status="started")


def finish_search_run(conn, run_id, status):
    if status not in ["done", "failed"]:
        raise ValueError("finish_search_run status must be 'done' or 'failed'")

    cur = conn.cursor()
    cur.execute(
        "UPDATE search_runs SET status = ? WHERE id = ?",
        (status, run_id),
    )
    conn.commit()


def insert_flight_offer(
    conn,
    run_id,
    origin,
    destination,
    departure_date,
    return_date,
    price,
    currency,
    airline,
    flight_number,
    airline_code=None,
    return_airline=None,
    return_airline_code=None,
    return_flight_number=None,
    outbound_price=None,
    return_price=None,
):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO flight_offers(
            search_run_id, origin, destination, departure_date, return_date,
            price, currency, airline, flight_number,
            airline_code, return_airline, return_airline_code, return_flight_number,
            outbound_price, return_price,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            run_id,
            origin,
            destination,
            departure_date,
            return_date,
            price,
            currency,
            airline,
            flight_number,
            airline_code,
            return_airline,
            return_airline_code,
            return_flight_number,
            outbound_price,
            return_price,
        ),
    )
    conn.commit()


def fetch_flight_offers(conn, search_run_id=None, limit=50):
    cur = conn.cursor()
    if search_run_id is not None:
        rows = cur.execute(
            """
            SELECT id, search_run_id, origin, destination, departure_date, return_date,
                   price, currency, airline, flight_number, created_at
            FROM flight_offers
            WHERE search_run_id = ?
            ORDER BY price ASC, id ASC
            LIMIT ?
            """,
            (search_run_id, limit),
        ).fetchall()
    else:
        rows = cur.execute(
            """
            SELECT id, search_run_id, origin, destination, departure_date, return_date,
                   price, currency, airline, flight_number, created_at
            FROM flight_offers
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return rows


def fetch_search_runs(conn, limit=10):
    cur = conn.cursor()
    return cur.execute(
        """
        SELECT id, search_mode, params_json, status, created_at
        FROM search_runs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


# --- Gettery analityczne (surowe SQL, bez logiki statystycznej) ---

def fetch_prices_for_route(conn, origin, destination):
    """Zwraca [(price, departure_date), ...] dla danej trasy (para lotnisk).

    Baza jest jednowalutowa (EUR), wiec bez filtra waluty.
    """
    cur = conn.cursor()
    return cur.execute(
        """
        SELECT price, departure_date
        FROM flight_offers
        WHERE origin = ? AND destination = ?
        ORDER BY price ASC
        """,
        (origin.upper(), destination.upper()),
    ).fetchall()


def fetch_destinations_from(conn, origin):
    """Zwraca [(destination, min_price, avg_price, count), ...] dla danego lotniska wylotu."""
    cur = conn.cursor()
    return cur.execute(
        """
        SELECT destination, MIN(price), AVG(price), COUNT(*)
        FROM flight_offers
        WHERE origin = ?
        GROUP BY destination
        ORDER BY MIN(price) ASC
        """,
        (origin.upper(),),
    ).fetchall()


def fetch_direction_leg_prices(conn, origin, destination):
    """Zwraca liste cen POJEDYNCZEGO przelotu origin->destination (nogi).

    Zbiera z trzech zrodel, wszystko w jednej jednostce 'cena jednego lotu':
      1. one-waye o->d              -> price (cala cena one-waya = przelot o->d)
      2. round-tripy o->d           -> outbound_price (noga "tam" leci o->d)
      3. round-tripy d->o           -> return_price (noga "powrot" podrozy d->o leci o->d)

    Zwraca [float, ...].
    """
    o = origin.upper()
    d = destination.upper()
    cur = conn.cursor()

    prices = []

    # 1 + 2: wiersze gdzie podroz to o->d
    rows = cur.execute(
        """
        SELECT price, outbound_price, return_date
        FROM flight_offers
        WHERE origin = ? AND destination = ?
        """,
        (o, d),
    ).fetchall()
    for price, outbound_price, return_date in rows:
        if return_date is None:
            # one-way o->d: cala cena to przelot o->d
            if price is not None:
                prices.append(price)
        else:
            # round-trip o->d: noga "tam" to przelot o->d
            if outbound_price is not None:
                prices.append(outbound_price)

    # 3: round-tripy zapisane jako d->o - ich noga "powrot" leci o->d
    rows = cur.execute(
        """
        SELECT return_price
        FROM flight_offers
        WHERE origin = ? AND destination = ? AND return_date IS NOT NULL
        """,
        (d, o),
    ).fetchall()
    for (return_price,) in rows:
        if return_price is not None:
            prices.append(return_price)

    return prices


def fetch_prices_by_weekday(conn, origin, destination):
    """Zwraca [(weekday, avg_price, min_price, count), ...] dla trasy.

    weekday wg strftime('%w'): '0'=niedziela ... '6'=sobota.
    departure_date bywa 'YYYY-MM-DD' lub 'YYYY-MM-DDTHH:MM' - tniemy do 10 znakow.
    """
    cur = conn.cursor()
    return cur.execute(
        """
        SELECT strftime('%w', substr(departure_date, 1, 10)) AS wd,
               AVG(price), MIN(price), COUNT(*)
        FROM flight_offers
        WHERE origin = ? AND destination = ?
          AND substr(departure_date, 1, 10) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
        GROUP BY wd
        ORDER BY wd
        """,
        (origin.upper(), destination.upper()),
    ).fetchall()


def get_best_offer(conn, search_run_id=None):
    """Najtansza oferta - z konkretnego runu lub globalnie.

    Zawsze sortuje po cenie rosnaco, niezaleznie od run_id.
    """
    cur = conn.cursor()
    if search_run_id is not None:
        rows = cur.execute(
            """
            SELECT id, search_run_id, origin, destination, departure_date, return_date,
                   price, currency, airline, flight_number, created_at
            FROM flight_offers
            WHERE search_run_id = ?
            ORDER BY price ASC, id ASC
            LIMIT 1
            """,
            (search_run_id,),
        ).fetchall()
    else:
        rows = cur.execute(
            """
            SELECT id, search_run_id, origin, destination, departure_date, return_date,
                   price, currency, airline, flight_number, created_at
            FROM flight_offers
            ORDER BY price ASC, id ASC
            LIMIT 1
            """
        ).fetchall()
    if len(rows) == 0:
        return None
    return rows[0]


def get_latest_done_run(conn, origin=None, destination=None):
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT id, search_mode, params_json, status, created_at
        FROM search_runs
        WHERE status = 'done'
        ORDER BY id DESC
        LIMIT 50
        """
    ).fetchall()

    for row in rows:
        params = json.loads(row[2])
        if origin and params.get("from", "").upper() != origin.upper():
            continue
        if destination and params.get("to", "").upper() != destination.upper():
            continue
        return row
    if len(rows) > 0 and origin is None and destination is None:
        return rows[0]
    return None


def compare_latest_runs(conn, origin=None, destination=None):
    cur = conn.cursor()
    query = """
        SELECT r.id, r.params_json, r.created_at,
               MIN(o.price) AS min_price, o.currency
        FROM search_runs r
        JOIN flight_offers o ON o.search_run_id = r.id
        WHERE r.status = 'done'
    """
    args = []
    if origin:
        query += " AND o.origin = ?"
        args.append(origin.upper())
    if destination:
        query += " AND o.destination LIKE ?"
        args.append(f"%{destination.upper()}%")
    query += """
        GROUP BY r.id
        ORDER BY r.id DESC
        LIMIT 2
    """
    rows = cur.execute(query, args).fetchall()
    if len(rows) < 2:
        return None
    newer, older = rows[0], rows[1]
    diff = older[3] - newer[3]
    return {
        "newer_run_id": newer[0],
        "older_run_id": older[0],
        "newer_price": newer[3],
        "older_price": older[3],
        "currency": newer[4],
        "diff": diff,
        "newer_at": newer[2],
        "older_at": older[2],
    }


def format_offer_row(row):
    (
        offer_id,
        run_id,
        origin,
        destination,
        departure_date,
        return_date,
        price,
        currency,
        airline,
        flight_number,
        created_at,
    ) = row
    ret = return_date if return_date else "-"
    return (
        f"#{offer_id} run={run_id} {origin}->{destination} "
        f"out={departure_date} ret={ret} "
        f"{price:.2f} {currency} {airline} {flight_number} ({created_at})"
    )


def print_offers(conn, search_run_id=None, limit=20):
    rows = fetch_flight_offers(conn, search_run_id=search_run_id, limit=limit)
    if len(rows) == 0:
        print("Brak ofert w bazie.")
        return
    for row in rows:
        print(format_offer_row(row))


def print_search_runs(conn, limit=10):
    rows = fetch_search_runs(conn, limit=limit)
    if len(rows) == 0:
        print("Brak wyszukiwan w bazie.")
        return
    for row in rows:
        run_id, mode, params_json, status, created_at = row
        params = json.loads(params_json)
        print(
            f"#{run_id} [{status}] {mode} "
            f"{params.get('from', '?')}->{params.get('to', '?')} ({created_at})"
        )


def list_flight_offers(conn, search_run_id):
    print_offers(conn, search_run_id=search_run_id)


def list_search_runs(conn, limit=5):
    print_search_runs(conn, limit=limit)
