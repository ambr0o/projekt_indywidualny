"""Logika sprawdzania progu cenowego dla najtanszych ofert."""

from dataclasses import dataclass
from typing import Optional

from db import DEFAULT_DB_PATH

from services.query_service import OfferRow, get_best


@dataclass
class AlertResult:
    """Wynik sprawdzenia progu cenowego."""
    triggered: bool
    reason: str  # 'below_threshold' | 'above_threshold' | 'no_offers' | 'currency_mismatch'
    offer: Optional[OfferRow] = None
    threshold: float = 0.0
    expected_currency: str = "EUR"


def check_threshold(
    threshold: float,
    expected_currency: str = "EUR",
    db_path: str = DEFAULT_DB_PATH,
    run_id: Optional[int] = None,
) -> AlertResult:
    """Sprawdza czy najtansza oferta jest ponizej zadanego progu.

    Args:
        threshold: maks. cena ktora chcemy plac.
        expected_currency: 'EUR'/'PLN'/'CZK'/.../'ANY' (ANY pomija sprawdzanie waluty).
        db_path: sciezka do bazy.
        run_id: opcjonalnie ograniczenie do konkretnego runu.

    Returns:
        AlertResult z informacja czy alert zostal aktywowany i dlaczego.
    """
    offer = get_best(db_path=db_path, run_id=run_id)

    if offer is None:
        return AlertResult(
            triggered=False,
            reason="no_offers",
            threshold=threshold,
            expected_currency=expected_currency,
        )

    if expected_currency != "ANY" and offer.currency != expected_currency:
        return AlertResult(
            triggered=False,
            reason="currency_mismatch",
            offer=offer,
            threshold=threshold,
            expected_currency=expected_currency,
        )

    if offer.price <= threshold:
        return AlertResult(
            triggered=True,
            reason="below_threshold",
            offer=offer,
            threshold=threshold,
            expected_currency=expected_currency,
        )

    return AlertResult(
        triggered=False,
        reason="above_threshold",
        offer=offer,
        threshold=threshold,
        expected_currency=expected_currency,
    )
