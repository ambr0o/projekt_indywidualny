"""Scraping and persistence of flight offers.

The ``search_and_save`` function is pure:
- it does not print
- it does not exit the process
- it returns a SearchResult object describing what happened

Because of that it can be called from the CLI, the Telegram bot, a REST API,
the cron - from anywhere.
"""

from dataclasses import dataclass
from math import isfinite
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
    """Result of a search returned by ``search_and_save``."""
    run_id: int
    offers_count: int
    success: bool
    error_message: Optional[str] = None


def _normalize_offer(raw_offer: dict) -> dict:
    """Convert a raw scraped offer into a DB-ready dictionary."""
    price_val, currency_val = parse_price_text(raw_offer["price_text"])

    # Per-leg sub-prices - may be None (e.g. when AZair did not provide subPrice)
    out_text = raw_offer.get("outbound_price_text")
    ret_text = raw_offer.get("return_price_text")
    outbound_price = parse_price_text(out_text)[0] if out_text else None
    return_price = parse_price_text(ret_text)[0] if ret_text else None

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
        "outbound_price": outbound_price,
        "return_price": return_price,
    }


def search_and_save(
    flight_url: str,
    db_path: str = DEFAULT_DB_PATH,
    max_results: int = 20,
) -> SearchResult:
    """Scrape results from AZair, normalize them and save them in the database.

    Args:
        flight_url: full link to the AZair results page.
        db_path: path to the SQLite file.
        max_results: max number of offers to save (cheapest first).

    Returns:
        SearchResult with full information about what happened.
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

        saved = 0
        for raw_offer in scrape_result.offers:
            offer = _normalize_offer(raw_offer)
            # Skip offers with an invalid price (parser returned 0.0 / inf on error).
            # Without this a 0.0 offer ends up in the DB as the "cheapest" and triggers a false alert.
            price = offer["price"]
            if price is None or price <= 0 or not isfinite(price):
                continue
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
                outbound_price=offer["outbound_price"],
                return_price=offer["return_price"],
            )
            saved += 1

        finish_search_run(conn, run_id=run_id, status="done")
        return SearchResult(
            run_id=run_id,
            offers_count=saved,
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
