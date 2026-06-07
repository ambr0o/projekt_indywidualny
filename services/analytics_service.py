"""Analityka cen lotow.

Logika statystyczna operujaca na danych z bazy. Sygnatury sa trasa-centryczne
(origin, destination) - dzieki temu te same funkcje dzialaja na danych z jednego
scrape (Droga 1) jak i na historii zebranej w czasie (Droga 2), bez przepisywania.

Baza jest jednowalutowa (EUR), wiec funkcje nie przejmuja sie waluta.
"""

import statistics
from dataclasses import dataclass
from typing import List, Optional

from db import (
    DEFAULT_DB_PATH,
    fetch_destinations_from,
    fetch_direction_leg_prices,
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
    """Statystyki cenowe dla trasy."""
    origin: str
    destination: str
    count: int
    min_price: float
    max_price: float
    avg_price: float
    median_price: float
    stdev_price: float  # 0.0 gdy < 2 obserwacji


@dataclass
class WeekdayStat:
    """Srednia cena dla konkretnego dnia tygodnia wylotu."""
    weekday: str          # nazwa po polsku
    avg_price: float
    min_price: float
    count: int


@dataclass
class DestinationRank:
    """Jedna pozycja w rankingu destynacji z danego lotniska."""
    destination: str
    min_price: float
    avg_price: float
    count: int


@dataclass
class PercentileResult:
    """Gdzie podana cena lezy w rozkladzie historycznym trasy."""
    origin: str
    destination: str
    price: float
    percentile: float     # 0-100; nizszy = taniej niz wiekszosc
    cheaper_than_pct: float  # % obserwacji drozszych od podanej ceny
    sample_size: int


@dataclass
class DirectionStats:
    """Statystyki ceny POJEDYNCZEGO przelotu (nogi) origin->destination.

    W przeciwienstwie do PriceStats (cena calej podrozy), tu liczymy cene
    jednego lotu - zbierana z one-wayow i nog round-tripow w tym kierunku.
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
    """Zwraca statystyki cenowe trasy albo None gdy brak danych."""
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
    """Zwraca statystyki cen per dzien tygodnia wylotu, posortowane od najtanszego."""
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
    """Ranking destynacji z danego lotniska, od najtanszej (po min cenie)."""
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
    """Liczy gdzie podana cena lezy w rozkladzie historycznym trasy.

    percentile = % obserwacji <= podanej ceny (niski = okazja).
    Zwraca None gdy brak danych dla trasy.
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
    """Statystyki ceny pojedynczego przelotu origin->destination (na poziomie nog).

    Laczy ceny z one-wayow + nog round-tripow w tym kierunku. Wszystko w jednej
    jednostce 'cena jednego lotu', wiec porownywalne. Zwraca None gdy brak danych.
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
