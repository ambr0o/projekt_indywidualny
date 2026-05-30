"""Formatery wynikow z service layer do tekstu czytelnego w Telegramie.

Telegram wspiera MarkdownV2, ale wymaga eskejpu wielu znakow specjalnych.
Dla bezpieczenstwa uzywamy zwyklego tekstu z emoji - dziala wszedzie.
"""

import json
from typing import List, Optional

from services.alert_service import AlertResult
from services.query_service import ComparisonResult, OfferRow, RunRow


def format_offer(offer: OfferRow) -> str:
    """Pojedyncza oferta w jednej linii."""
    ret = offer.return_date if offer.return_date else "-"
    return (
        f"✈️ {offer.origin} → {offer.destination}  "
        f"💶 {offer.price:.2f} {offer.currency}\n"
        f"   {offer.airline} {offer.flight_number}  "
        f"out: {offer.departure_date}  ret: {ret}"
    )


def format_offers_list(offers: List[OfferRow], max_items: int = 10) -> str:
    if not offers:
        return "Brak ofert w bazie. Uruchom /search z URL-em do AZair."
    lines = [f"📋 {len(offers)} ofert (top {min(len(offers), max_items)}):", ""]
    for offer in offers[:max_items]:
        lines.append(format_offer(offer))
        lines.append("")
    return "\n".join(lines).strip()


def format_best(offer: Optional[OfferRow]) -> str:
    if offer is None:
        return "Brak ofert w bazie."
    return "🏆 Najtansza oferta:\n\n" + format_offer(offer)


def format_runs_list(runs: List[RunRow], max_items: int = 10) -> str:
    if not runs:
        return "Brak wyszukiwan w bazie."
    lines = ["📜 Historia wyszukiwan:", ""]
    for run in runs[:max_items]:
        params = json.loads(run.params_json) if run.params_json else {}
        emoji = {"done": "✅", "failed": "❌", "started": "⏳"}.get(run.status, "❓")
        lines.append(
            f"{emoji} #{run.id}  {params.get('from', '?')} → {params.get('to', '?')}  "
            f"({run.created_at})"
        )
    return "\n".join(lines)


def format_comparison(result: Optional[ComparisonResult]) -> str:
    if result is None:
        return "Za malo udanych wyszukiwan do porownania (potrzeba 2)."

    if result.diff > 0:
        direction, emoji = "taniej", "📉"
    elif result.diff < 0:
        direction, emoji = "drozej", "📈"
    else:
        direction, emoji = "bez zmian", "➖"

    return (
        f"{emoji} Porownanie 2 ostatnich runow:\n\n"
        f"Run #{result.newer_run_id} ({result.newer_at})\n"
        f"   {result.newer_price:.2f} {result.currency}\n\n"
        f"Run #{result.older_run_id} ({result.older_at})\n"
        f"   {result.older_price:.2f} {result.currency}\n\n"
        f"Roznica: {abs(result.diff):.2f} {result.currency} ({direction})"
    )


def format_alert(result: AlertResult) -> str:
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
            f"🔔 ALERT: {offer.price:.2f} {offer.currency} "
            f"<= {result.threshold:.2f} {result.expected_currency}\n\n"
            + format_offer(offer)
        )

    return (
        f"OK: najtansza {offer.price:.2f} {offer.currency} > prog "
        f"{result.threshold:.2f} {result.expected_currency}"
    )
