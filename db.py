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
    }
    for col, col_type in new_columns.items():
        if col not in existing:
            cur.execute(f"ALTER TABLE flight_offers ADD COLUMN {col} {col_type}")
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
):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO flight_offers(
            search_run_id, origin, destination, departure_date, return_date,
            price, currency, airline, flight_number,
            airline_code, return_airline, return_airline_code, return_flight_number,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
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
