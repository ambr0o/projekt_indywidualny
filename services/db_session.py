"""Shared helper for opening a database connection.

Previously ``open_db`` lived in main.py - but it is infrastructure that every
service needs, so it lives here.
"""

import sqlite3

from db import (
    DEFAULT_DB_PATH,
    create_flight_offers_table,
    create_search_runs_table,
    create_watched_routes_table,
    create_weather_cache_table,
)


def open_db(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a connection and ensure the schema exists (idempotently)."""
    conn = sqlite3.connect(db_path)
    create_search_runs_table(conn)
    create_flight_offers_table(conn)
    create_weather_cache_table(conn)
    create_watched_routes_table(conn)
    return conn
