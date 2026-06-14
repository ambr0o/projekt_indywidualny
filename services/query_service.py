"""Logic for querying offers and searches in the database.

Returns simple objects (dataclasses) that each interface formats on its own.
"""

from dataclasses import dataclass
from typing import List, Optional

from db import (
    DEFAULT_DB_PATH,
    compare_latest_runs,
    fetch_flight_offers,
    fetch_search_runs,
    get_best_offer,
)

from services.db_session import open_db


@dataclass
class OfferRow:
    """Represents a single flight offer from the database."""
    id: int
    run_id: int
    origin: str
    destination: str
    departure_date: str
    return_date: Optional[str]
    price: float
    currency: str
    airline: str
    flight_number: str
    created_at: str

    @classmethod
    def from_db_row(cls, row: tuple) -> "OfferRow":
        """Construct from a tuple returned by fetch_flight_offers (11 columns)."""
        return cls(
            id=row[0],
            run_id=row[1],
            origin=row[2],
            destination=row[3],
            departure_date=row[4],
            return_date=row[5],
            price=row[6],
            currency=row[7],
            airline=row[8],
            flight_number=row[9],
            created_at=row[10],
        )


@dataclass
class RunRow:
    """Represents a single search_run from the database."""
    id: int
    search_mode: str
    params_json: str
    status: str
    created_at: str

    @classmethod
    def from_db_row(cls, row: tuple) -> "RunRow":
        """Construct a RunRow from a database row tuple."""
        return cls(*row)


@dataclass
class ComparisonResult:
    """Result of comparing the two latest successful runs."""
    newer_run_id: int
    older_run_id: int
    newer_price: float
    older_price: float
    currency: str
    diff: float
    newer_at: str
    older_at: str


def list_offers(
    db_path: str = DEFAULT_DB_PATH,
    run_id: Optional[int] = None,
    limit: int = 20,
) -> List[OfferRow]:
    """Return offers - from a specific run or the latest overall."""
    conn = open_db(db_path)
    try:
        rows = fetch_flight_offers(conn, search_run_id=run_id, limit=limit)
        return [OfferRow.from_db_row(r) for r in rows]
    finally:
        conn.close()


def get_best(
    db_path: str = DEFAULT_DB_PATH,
    run_id: Optional[int] = None,
) -> Optional[OfferRow]:
    """Return the cheapest offer - from a run or globally."""
    conn = open_db(db_path)
    try:
        row = get_best_offer(conn, search_run_id=run_id)
        if row is None:
            return None
        return OfferRow.from_db_row(row)
    finally:
        conn.close()


def list_runs(db_path: str = DEFAULT_DB_PATH, limit: int = 10) -> List[RunRow]:
    """Return the search history."""
    conn = open_db(db_path)
    try:
        rows = fetch_search_runs(conn, limit=limit)
        return [RunRow.from_db_row(r) for r in rows]
    finally:
        conn.close()


def compare_runs(
    db_path: str = DEFAULT_DB_PATH,
    origin: Optional[str] = None,
    destination: Optional[str] = None,
) -> Optional[ComparisonResult]:
    """Compare the 2 latest successful runs (optionally filtered by origin/destination)."""
    conn = open_db(db_path)
    try:
        result = compare_latest_runs(conn, origin=origin, destination=destination)
        if result is None:
            return None
        return ComparisonResult(
            newer_run_id=result["newer_run_id"],
            older_run_id=result["older_run_id"],
            newer_price=result["newer_price"],
            older_price=result["older_price"],
            currency=result["currency"],
            diff=result["diff"],
            newer_at=result["newer_at"],
            older_at=result["older_at"],
        )
    finally:
        conn.close()
