"""Text formatters for the Telegram bot.

They turn objects from the service layer (offers, comparisons, alerts, rankings)
into readable text messages ready to send to the user.
"""

from typing import List, Optional

from services.alert_service import AlertResult
from services.analytics_service import (
    DestinationRank,
    DirectionStats,
)
from services.query_service import ComparisonResult, OfferRow


def format_offer(offer: OfferRow) -> str:
    """A single offer on one line."""
    ret = offer.return_date if offer.return_date else "-"
    return (
        f"{offer.origin} -> {offer.destination}  "
        f"{offer.price:.2f} {offer.currency}\n"
        f"   {offer.airline} {offer.flight_number}  "
        f"out: {offer.departure_date}  ret: {ret}"
    )


def format_offers_list(offers: List[OfferRow], max_items: int = 10) -> str:
    """Format a list of offers into a message with a header and item limit.

    Args:
        offers (List[OfferRow]): Offers to display.
        max_items (int): Max number of offers in the message. Defaults to 10.

    Returns:
        str: A formatted list of offers, or "Brak ofert." when the list is empty.
    """
    if not offers:
        return "Brak ofert."
    lines = [f"{len(offers)} ofert (top {min(len(offers), max_items)}):", ""]
    for offer in offers[:max_items]:
        lines.append(format_offer(offer))
        lines.append("")
    return "\n".join(lines).strip()


def format_comparison(result: Optional[ComparisonResult]) -> str:
    """Format a comparison of flight price from the 2 latest searches.

    Args:
        result (Optional[ComparisonResult]): The comparison result, or None when
            there is too little data.

    Returns:
        str: Text with the price difference and direction of change, or a message
        about insufficient data.
    """
    if result is None:
        return "Za malo udanych wyszukiwan do porownania (potrzeba 2)."

    if result.diff > 0:
        direction = "taniej"
    elif result.diff < 0:
        direction = "drozej"
    else:
        direction = "bez zmian"

    return (
        f"Cena przelotu - porownanie 2 ostatnich wyszukiwan:\n\n"
        f"Nowsze (#{result.newer_run_id}, {result.newer_at})\n"
        f"   {result.newer_price:.2f} {result.currency}\n\n"
        f"Starsze (#{result.older_run_id}, {result.older_at})\n"
        f"   {result.older_price:.2f} {result.currency}\n\n"
        f"Roznica: {abs(result.diff):.2f} {result.currency} ({direction})"
    )


def format_alert(result: AlertResult) -> str:
    """Format the result of a price threshold check (alert).

    Args:
        result (AlertResult): The threshold check result from alert_service.

    Returns:
        str: A message depending on the state: no offers, currency mismatch,
        alert (price <= threshold), or that the price is above the threshold.
    """
    if result.reason == "no_offers":
        return "Brak ofert do sprawdzenia."

    if result.reason == "currency_mismatch":
        return (
            f"Waluta oferty ({result.offer.currency}) != oczekiwana "
            f"({result.expected_currency}). Pomijam."
        )

    offer = result.offer
    if result.triggered:
        return (
            f"ALERT: {offer.price:.2f} {offer.currency} "
            f"<= {result.threshold:.2f} {result.expected_currency}\n\n"
            + format_offer(offer)
        )

    return (
        f"OK: najtansza {offer.price:.2f} {offer.currency} > prog "
        f"{result.threshold:.2f} {result.expected_currency}"
    )


def format_price_history(hist) -> str:
    """Price history of a specific flight over time (Path A - honest analytics)."""
    if hist is None:
        return (
            "Brak historii dla tego lotu.\n"
            "Podaj trase i konkretna date wylotu, np: /stats KRK BCN 2026-08-15\n"
            "(lot musi byc wczesniej zebrany - przez /find lub monitor)."
        )

    lines = [
        f"{hist.origin} -> {hist.destination}  wylot {hist.departure_date}",
        f"Sledzony {hist.count}x (cena jednego lotu w czasie):",
        "",
        f"pierwsza:  {hist.first_price:.2f} EUR",
        f"teraz:     {hist.last_price:.2f} EUR",
        f"najtaniej: {hist.min_price:.2f} EUR",
        f"najdrozej: {hist.max_price:.2f} EUR",
    ]
    if hist.count >= 2:
        change = hist.change
        if change < 0:
            lines.append(f"\nstanial o {abs(change):.2f} EUR od pierwszego sprawdzenia")
        elif change > 0:
            lines.append(f"\npodrozal o {change:.2f} EUR od pierwszego sprawdzenia")
        else:
            lines.append("\ncena bez zmian")
        # mini-trace of the most recent measurements
        recent = hist.points[-5:]
        lines.append("")
        lines.append("Ostatnie pomiary:")
        for p in recent:
            lines.append(f"  {p.checked_at[:16]}  {p.price:.2f} EUR")
    return "\n".join(lines)


def format_ranking(origin: str, ranking: List[DestinationRank]) -> str:
    """Format a ranking of cheapest destinations from a given airport.

    Args:
        origin (str): IATA code of the origin airport.
        ranking (List[DestinationRank]): Ranking positions (cheapest first).

    Returns:
        str: A list of destinations with prices, or a no-data message.
    """
    if not ranking:
        return f"Brak danych dla wylotow z {origin}."
    lines = [f"Najtansze kierunki z {origin} (EUR):", ""]
    for r in ranking:
        lines.append(f"{r.destination}: min {r.min_price:.2f}  (sr. {r.avg_price:.2f}, {r.count} ofert)")
    return "\n".join(lines)


def format_direction(
    there: Optional[DirectionStats],
    back: Optional[DirectionStats],
    origin: str,
    destination: str,
) -> str:
    """Single-leg price for both directions + direction asymmetry."""
    if there is None and back is None:
        return f"Brak danych o przelotach {origin} <-> {destination}."

    lines = ["Ceny pojedynczego przelotu (na poziomie nog):", ""]

    if there is not None:
        lines.append(
            f"{there.origin} -> {there.destination}  "
            f"min {there.min_price:.2f}  mediana {there.median_price:.2f}  "
            f"({there.count} obs.)"
        )
    else:
        lines.append(f"{origin} -> {destination}: brak danych")

    if back is not None:
        lines.append(
            f"{back.origin} -> {back.destination}  "
            f"min {back.min_price:.2f}  mediana {back.median_price:.2f}  "
            f"({back.count} obs.)"
        )
    else:
        lines.append(f"{destination} -> {origin}: brak danych")

    # Direction asymmetry - if we have both medians
    if there is not None and back is not None:
        diff = back.median_price - there.median_price
        if abs(diff) >= 0.01:
            pricier = destination if diff < 0 else origin
            lines.append("")
            lines.append(f"Asymetria: powrot do {pricier} drozszy o {abs(diff):.2f} EUR (mediana)")

    return "\n".join(lines)
