"""CLI dla travel_agent.

Cienka warstwa prezentacji: parsuje argumenty z terminala, wola service layer
i formatuje wynik. Cala logika biznesowa siedzi w services/.
"""

import argparse
import json
import sys
from typing import Optional

from db import DEFAULT_DB_PATH
from services.alert_service import check_threshold
from services.query_service import (
    ComparisonResult,
    OfferRow,
    RunRow,
    compare_runs,
    get_best,
    list_offers,
    list_runs,
)
from services.search_service import search_and_save


def format_offer(offer: OfferRow) -> str:
    ret = offer.return_date if offer.return_date else "-"
    return (
        f"#{offer.id} run={offer.run_id} {offer.origin}->{offer.destination} "
        f"out={offer.departure_date} ret={ret} "
        f"{offer.price:.2f} {offer.currency} {offer.airline} {offer.flight_number} "
        f"({offer.created_at})"
    )


def format_run(run: RunRow) -> str:
    params = json.loads(run.params_json) if run.params_json else {}
    return (
        f"#{run.id} [{run.status}] {run.search_mode} "
        f"{params.get('from', '?')}->{params.get('to', '?')} ({run.created_at})"
    )


def cmd_search(args) -> int:
    print("start")
    result = search_and_save(args.url, max_results=args.max_results, db_path=args.db)

    if not result.success:
        print(f"blad: {result.error_message}")
        return 1

    print(f"zapisano {result.offers_count} ofert (run #{result.run_id})")
    for offer in list_offers(db_path=args.db, run_id=result.run_id, limit=args.max_results):
        print(format_offer(offer))
    return 0


def cmd_list(args) -> int:
    offers = list_offers(db_path=args.db, run_id=args.run_id, limit=args.limit)
    if args.run_id:
        print(f"Oferty dla run #{args.run_id}:")
    else:
        print(f"Ostatnie oferty (max {args.limit}):")
    if not offers:
        print("Brak ofert w bazie.")
        return 0
    for offer in offers:
        print(format_offer(offer))
    return 0


def cmd_best(args) -> int:
    offer = get_best(db_path=args.db, run_id=args.run_id)
    if offer is None:
        print("Brak ofert.")
        return 0
    print(format_offer(offer))
    return 0


def cmd_runs(args) -> int:
    runs = list_runs(db_path=args.db, limit=args.limit)
    if not runs:
        print("Brak wyszukiwan w bazie.")
        return 0
    for run in runs:
        print(format_run(run))
    return 0


def cmd_compare(args) -> int:
    result: Optional[ComparisonResult] = compare_runs(
        db_path=args.db, origin=args.origin, destination=args.destination
    )
    if result is None:
        print("Za malo udanych wyszukiwan do porownania (potrzeba 2).")
        return 0

    if result.diff > 0:
        direction = "taniej"
    elif result.diff < 0:
        direction = "drozej"
    else:
        direction = "bez zmian"

    print(
        f"Run #{result.newer_run_id} ({result.newer_at}): "
        f"{result.newer_price:.2f} {result.currency}"
    )
    print(
        f"Run #{result.older_run_id} ({result.older_at}): "
        f"{result.older_price:.2f} {result.currency}"
    )
    print(f"Roznica: {abs(result.diff):.2f} {result.currency} ({direction})")
    return 0


def cmd_alert(args) -> int:
    result = check_threshold(
        threshold=args.threshold,
        expected_currency=args.currency,
        db_path=args.db,
        run_id=args.run_id,
    )

    if result.reason == "no_offers":
        print("Brak ofert do sprawdzenia.")
        return 1

    if result.reason == "currency_mismatch":
        print(
            f"Waluta oferty ({result.offer.currency}) != oczekiwana "
            f"({result.expected_currency}), pomijam."
        )
        return 1

    offer = result.offer
    if result.triggered:
        print(
            f"ALERT: {offer.price:.2f} {offer.currency} "
            f"<= {result.threshold:.2f} {result.expected_currency}"
        )
        print(format_offer(offer))
        return 0

    print(f"OK: {offer.price:.2f} {offer.currency} > prog {result.threshold:.2f}")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Travel agent: scraping AZair i historia cen w SQLite.",
    )
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Sciezka do bazy SQLite")
    sub = parser.add_subparsers(dest="command")

    search_p = sub.add_parser("search", help="Scrapuj wyniki z URL AZair")
    search_p.add_argument("url", help="Pelny link do strony wynikow")
    search_p.add_argument("--max-results", type=int, default=20)

    list_p = sub.add_parser("list", help="Wyswietl zapisane oferty")
    list_p.add_argument("--run-id", type=int, default=None)
    list_p.add_argument("--limit", type=int, default=20)

    best_p = sub.add_parser("best", help="Najtansza oferta (z runu lub globalnie)")
    best_p.add_argument("--run-id", type=int, default=None)

    runs_p = sub.add_parser("runs", help="Historia wyszukiwan")
    runs_p.add_argument("--limit", type=int, default=10)

    compare_p = sub.add_parser("compare", help="Porownaj 2 ostatnie udane wyszukiwania")
    compare_p.add_argument("--origin", default=None)
    compare_p.add_argument("--destination", default=None)

    alert_p = sub.add_parser("alert", help="Sprawdz prog ceny najtanszej oferty")
    alert_p.add_argument("--threshold", type=float, required=True)
    alert_p.add_argument("--currency", default="EUR")
    alert_p.add_argument("--run-id", type=int, default=None)

    return parser


def main() -> None:
    # Skrot: 'python main.py https://...' bez subkomendy
    if len(sys.argv) > 1 and sys.argv[1].startswith("http"):
        print("start")
        result = search_and_save(sys.argv[1].strip())
        if not result.success:
            print(f"blad: {result.error_message}")
            sys.exit(1)
        print(f"zapisano {result.offers_count} ofert (run #{result.run_id})")
        for offer in list_offers(run_id=result.run_id, limit=result.offers_count or 20):
            print(format_offer(offer))
        sys.exit(0)

    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        print("\nLub: python main.py 'https://www.azair.eu/...'")
        sys.exit(1)

    handlers = {
        "search": cmd_search,
        "list": cmd_list,
        "best": cmd_best,
        "runs": cmd_runs,
        "compare": cmd_compare,
        "alert": cmd_alert,
    }
    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
