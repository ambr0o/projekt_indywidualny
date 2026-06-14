"""Flight price analytics.

Statistical logic operating on the database data. The signatures are
route-centric (origin, destination) - so the same functions work both on data
from a single scrape (Path 1) and on history collected over time (Path 2),
without rewriting.

The database is single-currency (EUR), so the functions do not deal with currency.
"""

import statistics
from dataclasses import dataclass
from typing import List, Optional

from db import (
    DEFAULT_DB_PATH,
    fetch_destinations_from,
    fetch_direction_leg_prices,
    fetch_price_history,
    fetch_prices_by_weekday,
    fetch_prices_for_route,
)

from services.db_session import open_db

WEEKDAY_NAMES = {
    "0": "niedziela",
    "1": "poniedzialek",
    "2": "wtorek",
    "3": "sroda",
    "4": "czwartek",
    "5": "piatek",
    "6": "sobota",
}


@dataclass
class PriceStats:
    """Price statistics for a route."""
    origin: str
    destination: str
    count: int
    min_price: float
    max_price: float
    avg_price: float
    median_price: float
    stdev_price: float  # 0.0 when < 2 observations


@dataclass
class WeekdayStat:
    """Average price for a specific departure weekday."""
    weekday: str          # name in Polish
    avg_price: float
    min_price: float
    count: int


@dataclass
class DestinationRank:
    """One position in the ranking of destinations from a given airport."""
    destination: str
    min_price: float
    avg_price: float
    count: int


@dataclass
class PercentileResult:
    """Where a given price lies in the route's historical distribution."""
    origin: str
    destination: str
    price: float
    percentile: float     # 0-100; lower = cheaper than most
    cheaper_than_pct: float  # % of observations more expensive than the given price
    sample_size: int


@dataclass
class DirectionStats:
    """Price statistics for a SINGLE flight (leg) origin->destination.

    Unlike PriceStats (price of the whole trip), here we compute the price of
    one flight - collected from one-ways and round-trip legs in this direction.
    """
    origin: str
    destination: str
    count: int
    min_price: float
    max_price: float
    avg_price: float
    median_price: float


def route_price_stats(
    origin: str,
    destination: str,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[PriceStats]:
    """Return price statistics for a route, or None when there is no data."""
    conn = open_db(db_path)
    try:
        rows = fetch_prices_for_route(conn, origin, destination)
    finally:
        conn.close()

    prices = [r[0] for r in rows]
    if not prices:
        return None

    return PriceStats(
        origin=origin.upper(),
        destination=destination.upper(),
        count=len(prices),
        min_price=min(prices),
        max_price=max(prices),
        avg_price=statistics.mean(prices),
        median_price=statistics.median(prices),
        stdev_price=statistics.stdev(prices) if len(prices) >= 2 else 0.0,
    )


def cheapest_weekday(
    origin: str,
    destination: str,
    db_path: str = DEFAULT_DB_PATH,
) -> List[WeekdayStat]:
    """Return price stats per departure weekday, sorted cheapest first."""
    conn = open_db(db_path)
    try:
        rows = fetch_prices_by_weekday(conn, origin, destination)
    finally:
        conn.close()

    stats = [
        WeekdayStat(
            weekday=WEEKDAY_NAMES.get(wd, wd),
            avg_price=avg,
            min_price=mn,
            count=cnt,
        )
        for wd, avg, mn, cnt in rows
    ]
    return sorted(stats, key=lambda s: s.avg_price)


def destination_ranking(
    origin: str,
    db_path: str = DEFAULT_DB_PATH,
    limit: int = 20,
) -> List[DestinationRank]:
    """Ranking of destinations from a given airport, cheapest first (by min price)."""
    conn = open_db(db_path)
    try:
        rows = fetch_destinations_from(conn, origin)
    finally:
        conn.close()

    ranking = [
        DestinationRank(destination=dst, min_price=mn, avg_price=avg, count=cnt)
        for dst, mn, avg, cnt in rows
    ]
    return ranking[:limit]


def price_percentile(
    price: float,
    origin: str,
    destination: str,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[PercentileResult]:
    """Compute where a given price lies in the route's historical distribution.

    percentile = % of observations <= the given price (low = a deal).
    Returns None when there is no data for the route.
    """
    conn = open_db(db_path)
    try:
        rows = fetch_prices_for_route(conn, origin, destination)
    finally:
        conn.close()

    prices = [r[0] for r in rows]
    if not prices:
        return None

    n = len(prices)
    at_or_below = sum(1 for p in prices if p <= price)
    above = sum(1 for p in prices if p > price)

    return PercentileResult(
        origin=origin.upper(),
        destination=destination.upper(),
        price=price,
        percentile=round(100.0 * at_or_below / n, 1),
        cheaper_than_pct=round(100.0 * above / n, 1),
        sample_size=n,
    )


def direction_stats(
    origin: str,
    destination: str,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[DirectionStats]:
    """Price statistics for a single flight origin->destination (at the leg level).

    Combines prices from one-ways + round-trip legs in this direction. Everything
    in one unit 'price of one flight', so comparable. Returns None when no data.
    """
    conn = open_db(db_path)
    try:
        prices = fetch_direction_leg_prices(conn, origin, destination)
    finally:
        conn.close()

    if not prices:
        return None

    return DirectionStats(
        origin=origin.upper(),
        destination=destination.upper(),
        count=len(prices),
        min_price=min(prices),
        max_price=max(prices),
        avg_price=statistics.mean(prices),
        median_price=statistics.median(prices),
    )


@dataclass
class PriceHistoryPoint:
    """A single price measurement of a flight over time."""
    checked_at: str   # the run's created_at (when collected)
    price: float


@dataclass
class PriceHistory:
    """Price history of a SPECIFIC flight (route + departure date) over time.

    This is the 'honest' analytics of Path A: it compares the same flight with
    itself at different collection moments, instead of mixing departure dates.
    """
    origin: str
    destination: str
    departure_date: str
    points: List[PriceHistoryPoint]

    @property
    def count(self) -> int:
        """Number of recorded measurements."""
        return len(self.points)

    @property
    def first_price(self) -> float:
        """Price at the first measurement."""
        return self.points[0].price

    @property
    def last_price(self) -> float:
        """Price at the most recent measurement."""
        return self.points[-1].price

    @property
    def min_price(self) -> float:
        """Lowest recorded price."""
        return min(p.price for p in self.points)

    @property
    def max_price(self) -> float:
        """Highest recorded price."""
        return max(p.price for p in self.points)

    @property
    def change(self) -> float:
        """Difference last - first (negative = price dropped)."""
        return self.last_price - self.first_price

    @property
    def trend(self) -> str:
        """'down' | 'up' | 'flat' based on the latest price movement."""
        if self.count < 2:
            return "flat"
        diff = self.change
        if diff < -0.01:
            return "down"
        if diff > 0.01:
            return "up"
        return "flat"


def price_history(
    origin: str,
    destination: str,
    departure_date: str,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[PriceHistory]:
    """Price history of a specific flight (route + departure date) over time.

    This is "honest" analytics: it compares THE SAME flight with itself at
    different collection moments, instead of mixing different departure dates
    (which would distort averages and minima).

    Args:
        origin (str): IATA code of the origin airport.
        destination (str): IATA code of the destination.
        departure_date (str): Departure date "YYYY-MM-DD" (day only).
        db_path (str): Path to the SQLite database.

    Returns:
        Optional[PriceHistory]: History with points over time and a trend,
        or None when there is no data for this flight.
    """
    conn = open_db(db_path)
    try:
        rows = fetch_price_history(conn, origin, destination, departure_date)
    finally:
        conn.close()

    if not rows:
        return None

    points = [PriceHistoryPoint(checked_at=ca, price=p) for ca, p in rows]
    return PriceHistory(
        origin=origin.upper(),
        destination=destination.upper(),
        departure_date=departure_date[:10],
        points=points,
    )
