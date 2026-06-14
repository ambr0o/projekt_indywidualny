"""SQLite persistence layer for searches, offers, weather cache and watches.

Contains schema creation/migration helpers and raw SQL accessors. No business
or statistical logic lives here - just storage and retrieval.
"""

import json

ALLOWED_SEARCH_STATUSES = ["started", "done", "failed"]
DEFAULT_DB_PATH = "database.db"


def create_search_runs_table(conn):
    """Create the ``search_runs`` table if it does not already exist."""
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
    """Create the ``flight_offers`` table and apply lightweight migrations."""
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
    # Lightweight migration: add new columns when missing (idempotent)
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

    # Indexes for analytics queries (filter by route) and lookup by run.
    # Almost every analytics function filters by (origin, destination).
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_offers_route ON flight_offers(origin, destination)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_offers_run ON flight_offers(search_run_id)"
    )
    conn.commit()


def create_weather_cache_table(conn):
    """Create the weather cache table keyed by (iata, month).

    Caching avoids querying Open-Meteo repeatedly for the same airport/month.
    """
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
    """Return cached weather row for (iata, month), or None if not cached."""
    cur = conn.cursor()
    row = cur.execute(
        "SELECT temp_max, temp_min, rain_mm, rainy_days, kind FROM weather_cache WHERE iata=? AND month=?",
        (iata.upper(), month),
    ).fetchone()
    return row


def save_weather_cache(conn, iata, month, temp_max, temp_min, rain_mm, rainy_days, kind):
    """Insert or replace the cached weather row for (iata, month)."""
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
    """Create the ``watched_routes`` table (core of the price monitor).

    Each row is one user (chat_id) watching one route with a price threshold.
    ``last_price``/``last_checked`` are filled by the cron on each check.
    ``dep_date``/``arr_date`` are the departure date window (NULL = the cron's
    default window). ``mode`` is either 'alert' (when <= threshold) or 'always'
    (report the price on every check).
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
    # Lightweight migration: add columns when missing (idempotent)
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
    """Insert a new watched route and return its row id."""
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
    """Return watched routes: all, for a given user, optionally only active."""
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
    """Deactivate a watch only if it belongs to the given chat_id (safety).

    Returns:
        True when a row was changed, False when not found or not owned by user.
    """
    cur = conn.cursor()
    cur.execute(
        "UPDATE watched_routes SET active = 0 WHERE id = ? AND chat_id = ? AND active = 1",
        (watch_id, chat_id),
    )
    conn.commit()
    return cur.rowcount > 0


def update_watched_route_check(conn, watch_id, last_price):
    """Store the result of the last cron check for a watched route."""
    cur = conn.cursor()
    cur.execute(
        "UPDATE watched_routes SET last_price = ?, last_checked = datetime('now') WHERE id = ?",
        (last_price, watch_id),
    )
    conn.commit()


def insert_search_run(conn, search_mode, params, status):
    """Insert a search run row and return its id.

    Raises:
        ValueError: If ``status`` is not one of ALLOWED_SEARCH_STATUSES.
    """
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
    """Start a new search run (status 'started') and return its id."""
    return insert_search_run(conn, search_mode=search_mode, params=params, status="started")


def finish_search_run(conn, run_id, status):
    """Mark a search run as finished with status 'done' or 'failed'.

    Raises:
        ValueError: If ``status`` is not 'done' or 'failed'.
    """
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
    """Insert one flight offer row linked to a search run."""
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
    """Return flight offers for a specific run, or the most recent overall."""
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
    """Return the most recent search runs, newest first."""
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


# --- Analytics accessors (raw SQL, no statistical logic) ---

def fetch_price_history(conn, origin, destination, departure_date):
    """Price history of a SPECIFIC flight (route + departure date) over time.

    One point per scrape (search_run): the cheapest price of that flight in
    that run. This compares THE SAME flight against itself at different moments,
    instead of mixing different departure dates (which would distort min/deals).

    ``departure_date`` may be 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM' - we compare the
    first 10 characters (the day only).

    Returns:
        ``[(created_at, min_price), ...]`` sorted by collection time.
    """
    cur = conn.cursor()
    return cur.execute(
        """
        SELECT r.created_at, MIN(o.price)
        FROM flight_offers o
        JOIN search_runs r ON o.search_run_id = r.id
        WHERE o.origin = ? AND o.destination = ?
          AND substr(o.departure_date, 1, 10) = ?
        GROUP BY o.search_run_id
        ORDER BY r.created_at ASC, r.id ASC
        """,
        (origin.upper(), destination.upper(), departure_date[:10]),
    ).fetchall()


def fetch_prices_for_route(conn, origin, destination):
    """Return ``[(price, departure_date), ...]`` for a route (airport pair).

    The database is single-currency (EUR), so there is no currency filter.
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
    """Return ``[(destination, min_price, avg_price, count), ...]`` for an origin airport."""
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
    """Return prices of a SINGLE flight (leg) origin->destination.

    Collected from three sources, all in the same unit 'price of one flight':
      1. one-ways o->d              -> price (the whole one-way price = leg o->d)
      2. round-trips o->d           -> outbound_price (the outbound leg flies o->d)
      3. round-trips d->o           -> return_price (the return leg of trip d->o flies o->d)

    Returns:
        ``[float, ...]``.
    """
    o = origin.upper()
    d = destination.upper()
    cur = conn.cursor()

    prices = []

    # 1 + 2: rows where the trip is o->d
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
            # one-way o->d: the whole price is the leg o->d
            if price is not None:
                prices.append(price)
        else:
            # round-trip o->d: the outbound leg is the flight o->d
            if outbound_price is not None:
                prices.append(outbound_price)

    # 3: round-trips stored as d->o - their return leg flies o->d
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
    """Return ``[(weekday, avg_price, min_price, count), ...]`` for a route.

    weekday per strftime('%w'): '0'=Sunday ... '6'=Saturday.
    ``departure_date`` may be 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM' - we cut to 10 chars.
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
    """Return the cheapest offer - from a specific run or globally.

    Always sorts by ascending price, regardless of run_id.
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


def compare_latest_runs(conn, origin=None, destination=None):
    """Compare the price of the flight origin->destination between the 2 latest runs.

    Computes at the leg level (like fetch_direction_leg_prices), so it does not
    mix round-trips with one-ways - a consistent 'price of one flight' unit.
    Requires origin and destination (comparison only makes sense for a direction).
    """
    if not origin or not destination:
        return None
    o, d = origin.upper(), destination.upper()
    cur = conn.cursor()

    # The two latest successful runs that contain direction o->d (as outbound, return or one-way)
    runs = cur.execute(
        """
        SELECT DISTINCT r.id, r.created_at
        FROM search_runs r
        JOIN flight_offers o ON o.search_run_id = r.id
        WHERE r.status = 'done'
          AND (
            (o.origin = ? AND o.destination = ?)        -- o->d (one-way price or outbound_price)
            OR (o.origin = ? AND o.destination = ? AND o.return_date IS NOT NULL)  -- d->o, return_price
          )
        ORDER BY r.id DESC
        LIMIT 2
        """,
        (o, d, d, o),
    ).fetchall()
    if len(runs) < 2:
        return None

    def min_leg_price(run_id):
        """Cheapest price of the flight o->d in the given run (from legs)."""
        prices = []
        for price, outbound_price, return_date in cur.execute(
            "SELECT price, outbound_price, return_date FROM flight_offers "
            "WHERE search_run_id = ? AND origin = ? AND destination = ?",
            (run_id, o, d),
        ).fetchall():
            if return_date is None:
                if price is not None:
                    prices.append(price)
            elif outbound_price is not None:
                prices.append(outbound_price)
        for (return_price,) in cur.execute(
            "SELECT return_price FROM flight_offers "
            "WHERE search_run_id = ? AND origin = ? AND destination = ? AND return_date IS NOT NULL",
            (run_id, d, o),
        ).fetchall():
            if return_price is not None:
                prices.append(return_price)
        return min(prices) if prices else None

    newer_id, newer_at = runs[0]
    older_id, older_at = runs[1]
    newer_price = min_leg_price(newer_id)
    older_price = min_leg_price(older_id)
    if newer_price is None or older_price is None:
        return None

    return {
        "newer_run_id": newer_id,
        "older_run_id": older_id,
        "newer_price": newer_price,
        "older_price": older_price,
        "currency": "EUR",
        "diff": older_price - newer_price,
        "newer_at": newer_at,
        "older_at": older_at,
    }

