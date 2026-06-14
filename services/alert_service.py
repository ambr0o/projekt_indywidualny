"""Logic for checking a price threshold against the cheapest offers."""

from dataclasses import dataclass
from typing import Optional

from db import DEFAULT_DB_PATH

from services.query_service import OfferRow, get_best


@dataclass
class AlertResult:
    """Result of a price threshold check."""
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
    """Check whether the cheapest offer is below the given threshold.

    Args:
        threshold: max price we are willing to pay.
        expected_currency: 'EUR'/'PLN'/'CZK'/.../'ANY' (ANY skips the currency check).
        db_path: path to the database.
        run_id: optionally restrict to a specific run.

    Returns:
        AlertResult with whether the alert was triggered and why.
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
