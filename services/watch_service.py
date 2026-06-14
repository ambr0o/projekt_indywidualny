"""Price watch logic (core of the price monitor).

A user (chat_id) watches a route with a price threshold. The cron checks the
watches and notifies when the price drops below the threshold.

IATA code validation is reused from find_service. Operations are per chat_id -
each user can see and remove only their own watches.
"""

from dataclasses import dataclass
from datetime import date
from math import isfinite
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
    """A single price watch of a user."""
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
        """Construct a WatchedRoute from a database row tuple."""
        return cls(
            id=row[0], chat_id=row[1], origin=row[2], destination=row[3],
            threshold=row[4], currency=row[5], oneway=bool(row[6]),
            active=bool(row[7]), last_price=row[8], last_checked=row[9],
            created_at=row[10], dep_date=row[11], arr_date=row[12],
            mode=row[13] if row[13] else "alert",
        )


@dataclass
class AddWatchResult:
    """Result of an attempt to add a watch - id or a validation error."""
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
    today: Optional[date] = None,
) -> AddWatchResult:
    """Add a watch after validation. Anywhere is not allowed (threshold needs a concrete route).

    dep_date/arr_date optional (YYYY-MM-DD); None = the cron uses its default window.
    mode: 'alert' (when <= threshold) or 'always' (price on every check).
    """
    origin = origin.strip().upper()
    destination = destination.strip().upper()

    if not IATA_RE.match(origin):
        return AddWatchResult(error=f"'{origin}' to nie kod IATA wylotu (3 litery, np. WAW).")
    if not IATA_RE.match(destination):
        return AddWatchResult(error=f"'{destination}' to nie kod IATA celu (3 litery, np. TIA).")
    if not isfinite(threshold):
        return AddWatchResult(error="Prog ceny musi byc zwykla liczba (nie 'inf' ani 'nan').")
    if threshold <= 0:
        return AddWatchResult(error="Prog ceny musi byc dodatni.")
    if mode not in ("alert", "always"):
        return AddWatchResult(error="Tryb musi byc 'alert' albo 'always'.")

    # Date validation (if provided - both or neither)
    if (dep_date is None) != (arr_date is None):
        return AddWatchResult(error="Podaj obie daty (od i do) albo zadnej.")
    if dep_date is not None:
        if not _valid_date(dep_date) or not _valid_date(arr_date):
            return AddWatchResult(error="Daty w formacie RRRR-MM-DD, np. 2026-08-01.")
        if arr_date < dep_date:
            return AddWatchResult(error="Data do nie moze byc wczesniejsza niz data od.")
        # A watch with a past departure date would never work (cron skips the past).
        today = today or date.today()
        if date.fromisoformat(dep_date) < today:
            return AddWatchResult(
                error=f"Data wylotu '{dep_date}' jest w przeszlosci. Podaj date od dzis ({today.isoformat()})."
            )

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
    """Active watches of a given user."""
    conn = open_db(db_path)
    try:
        rows = fetch_watched_routes(conn, chat_id=chat_id, only_active=True)
        return [WatchedRoute.from_db_row(r) for r in rows]
    finally:
        conn.close()


def remove_watch(chat_id: int, watch_id: int, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Remove (deactivate) a watch - only if it belongs to this user.

    Returns:
        True when removed, False when not found or not owned by the user.
    """
    conn = open_db(db_path)
    try:
        return deactivate_watched_route(conn, watch_id=watch_id, chat_id=chat_id)
    finally:
        conn.close()


def all_active_watches(db_path: str = DEFAULT_DB_PATH) -> List[WatchedRoute]:
    """All active watches (of all users) - used by the cron."""
    conn = open_db(db_path)
    try:
        rows = fetch_watched_routes(conn, chat_id=None, only_active=True)
        return [WatchedRoute.from_db_row(r) for r in rows]
    finally:
        conn.close()
