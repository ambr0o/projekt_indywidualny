"""Logika obserwacji cenowych (rdzen monitora cen).

Uzytkownik (chat_id) obserwuje trase z progiem ceny. Cron sprawdza obserwacje
i powiadamia gdy cena spadnie ponizej progu.

Walidacja kodow IATA reuzywana z find_service. Operacje per chat_id - kazdy
uzytkownik widzi i moze usuwac tylko swoje obserwacje.
"""

from dataclasses import dataclass
from typing import List, Optional

from db import (
    DEFAULT_DB_PATH,
    deactivate_watched_route,
    fetch_watched_routes,
    insert_watched_route,
)
from services.db_session import open_db
from services.find_service import IATA_RE, _valid_date


@dataclass
class WatchedRoute:
    """Jedna obserwacja cenowa uzytkownika."""
    id: int
    chat_id: int
    origin: str
    destination: str
    threshold: float
    currency: str
    oneway: bool
    active: bool
    last_price: Optional[float]
    last_checked: Optional[str]
    created_at: str
    dep_date: Optional[str]
    arr_date: Optional[str]
    mode: str   # 'alert' | 'always'

    @classmethod
    def from_db_row(cls, row: tuple) -> "WatchedRoute":
        return cls(
            id=row[0], chat_id=row[1], origin=row[2], destination=row[3],
            threshold=row[4], currency=row[5], oneway=bool(row[6]),
            active=bool(row[7]), last_price=row[8], last_checked=row[9],
            created_at=row[10], dep_date=row[11], arr_date=row[12],
            mode=row[13] if row[13] else "alert",
        )


@dataclass
class AddWatchResult:
    """Wynik proby dodania obserwacji - id albo blad walidacji."""
    watch_id: Optional[int] = None
    error: Optional[str] = None


def add_watch(
    chat_id: int,
    origin: str,
    destination: str,
    threshold: float,
    oneway: bool = False,
    currency: str = "EUR",
    dep_date: Optional[str] = None,
    arr_date: Optional[str] = None,
    mode: str = "alert",
    db_path: str = DEFAULT_DB_PATH,
) -> AddWatchResult:
    """Dodaje obserwacje po walidacji. Anywhere niedozwolone (prog na konkretna trase).

    dep_date/arr_date opcjonalne (RRRR-MM-DD); None = cron uzyje domyslnego okna.
    mode: 'alert' (gdy <= prog) lub 'always' (cena przy kazdym sprawdzeniu).
    """
    origin = origin.strip().upper()
    destination = destination.strip().upper()

    if not IATA_RE.match(origin):
        return AddWatchResult(error=f"'{origin}' to nie kod IATA wylotu (3 litery, np. WAW).")
    if not IATA_RE.match(destination):
        return AddWatchResult(error=f"'{destination}' to nie kod IATA celu (3 litery, np. TIA).")
    if threshold <= 0:
        return AddWatchResult(error="Prog ceny musi byc dodatni.")
    if mode not in ("alert", "always"):
        return AddWatchResult(error="Tryb musi byc 'alert' albo 'always'.")

    # Walidacja dat (jesli podane - obie albo zadna)
    if (dep_date is None) != (arr_date is None):
        return AddWatchResult(error="Podaj obie daty (od i do) albo zadnej.")
    if dep_date is not None:
        if not _valid_date(dep_date) or not _valid_date(arr_date):
            return AddWatchResult(error="Daty w formacie RRRR-MM-DD, np. 2026-08-01.")
        if arr_date < dep_date:
            return AddWatchResult(error="Data do nie moze byc wczesniejsza niz data od.")

    conn = open_db(db_path)
    try:
        watch_id = insert_watched_route(
            conn, chat_id=chat_id, origin=origin, destination=destination,
            threshold=threshold, currency=currency, oneway=oneway,
            dep_date=dep_date, arr_date=arr_date, mode=mode,
        )
        return AddWatchResult(watch_id=watch_id)
    finally:
        conn.close()


def list_watches(chat_id: int, db_path: str = DEFAULT_DB_PATH) -> List[WatchedRoute]:
    """Aktywne obserwacje danego uzytkownika."""
    conn = open_db(db_path)
    try:
        rows = fetch_watched_routes(conn, chat_id=chat_id, only_active=True)
        return [WatchedRoute.from_db_row(r) for r in rows]
    finally:
        conn.close()


def remove_watch(chat_id: int, watch_id: int, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Usuwa (dezaktywuje) obserwacje - tylko jesli nalezy do tego uzytkownika.

    Zwraca True gdy usunieto, False gdy nie znaleziono/nie nalezy do usera.
    """
    conn = open_db(db_path)
    try:
        return deactivate_watched_route(conn, watch_id=watch_id, chat_id=chat_id)
    finally:
        conn.close()


def all_active_watches(db_path: str = DEFAULT_DB_PATH) -> List[WatchedRoute]:
    """Wszystkie aktywne obserwacje (wszystkich userow) - uzywane przez cron."""
    conn = open_db(db_path)
    try:
        rows = fetch_watched_routes(conn, chat_id=None, only_active=True)
        return [WatchedRoute.from_db_row(r) for r in rows]
    finally:
        conn.close()
