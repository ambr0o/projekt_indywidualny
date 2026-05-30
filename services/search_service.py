"""Logika scrapowania i zapisu ofert lotow w bazie.

Funkcja `search_and_save` jest czysta:
- nie printuje
- nie konczy procesu
- zwraca obiekt SearchResult opisujacy co sie udalo

Przez to mozna ja wywolac z CLI, bota Telegrama, REST API, crona - skadkolwiek.
"""

from dataclasses import dataclass
from typing import Optional

from db import (
    DEFAULT_DB_PATH,
    finish_search_run,
    insert_flight_offer,
    start_search_run,
)
from flights import (
    parse_flight_url,
    parse_price_text,
    scrape_flight_from_results_url_full,
)

from services.db_session import open_db


@dataclass
class SearchResult:
    """Wynik wyszukiwania zwracany przez search_and_save."""
    run_id: int
    offers_count: int
    success: bool
    error_message: Optional[str] = None


def _normalize_offer(raw_offer: dict) -> dict:
    """Konwertuje surowa oferte ze scrapera na format gotowy do zapisu w DB."""
    price_val, currency_val = parse_price_text(raw_offer["price_text"])
    return {
        "origin": raw_offer["origin"],
        "destination": raw_offer["destination"],
        "departure_date": raw_offer["departure_date"],
        "return_date": raw_offer["return_date"],
        "price": price_val,
        "currency": currency_val,
        "airline": raw_offer["airline"],
        "airline_code": raw_offer.get("airline_code"),
        "flight_number": raw_offer["flight_number"],
        "return_airline": raw_offer.get("return_airline"),
        "return_airline_code": raw_offer.get("return_airline_code"),
        "return_flight_number": raw_offer.get("return_flight_number"),
    }


def search_and_save(
    flight_url: str,
    db_path: str = DEFAULT_DB_PATH,
    max_results: int = 20,
) -> SearchResult:
    """Pobiera wyniki ze AZair, normalizuje i zapisuje w bazie.

    Args:
        flight_url: pelny link do strony wynikow AZair.
        db_path: sciezka do pliku SQLite.
        max_results: maks. liczba ofert do zapisania (od najtanszej).

    Returns:
        SearchResult z pelna informacja co sie udalo.
    """
    conn = open_db(db_path)
    ctx = parse_flight_url(flight_url)

    run_params = {
        "from": ctx.get("origin", "?"),
        "to": ctx.get("destination", "?"),
        "outbound": ctx.get("departure_date", ""),
        "inbound": ctx.get("return_date", ""),
        "source": "url",
    }
    run_id = start_search_run(conn, search_mode="route", params=run_params)

    try:
        scrape_result = scrape_flight_from_results_url_full(
            flight_url, max_results=max_results
        )

        if scrape_result.error:
            finish_search_run(conn, run_id=run_id, status="failed")
            return SearchResult(
                run_id=run_id,
                offers_count=0,
                success=False,
                error_message=scrape_result.message,
            )

        for raw_offer in scrape_result.offers:
            offer = _normalize_offer(raw_offer)
            insert_flight_offer(
                conn,
                run_id=run_id,
                origin=offer["origin"],
                destination=offer["destination"],
                departure_date=offer["departure_date"],
                return_date=offer["return_date"],
                price=offer["price"],
                currency=offer["currency"],
                airline=offer["airline"],
                flight_number=offer["flight_number"],
                airline_code=offer["airline_code"],
                return_airline=offer["return_airline"],
                return_airline_code=offer["return_airline_code"],
                return_flight_number=offer["return_flight_number"],
            )

        finish_search_run(conn, run_id=run_id, status="done")
        return SearchResult(
            run_id=run_id,
            offers_count=len(scrape_result.offers),
            success=True,
        )

    except Exception as exc:
        finish_search_run(conn, run_id=run_id, status="failed")
        return SearchResult(
            run_id=run_id,
            offers_count=0,
            success=False,
            error_message=f"Blad nieoczekiwany: {exc}",
        )
    finally:
        conn.close()
