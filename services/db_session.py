"""Wspolny helper do otwierania polaczenia z baza danych.

Wczesniej `open_db` siedzialo w main.py - ale to jest infrastruktura
ktorej potrzebuje kazdy serwis, wiec mieszka tutaj.
"""

import sqlite3

from db import (
    DEFAULT_DB_PATH,
    create_flight_offers_table,
    create_search_runs_table,
)


def open_db(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Otwiera polaczenie i upewnia sie ze schemat istnieje (idempotentnie)."""
    conn = sqlite3.connect(db_path)
    create_search_runs_table(conn)
    create_flight_offers_table(conn)
    return conn
