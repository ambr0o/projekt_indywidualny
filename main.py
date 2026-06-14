"""CLI for travel_agent.

A thin presentation layer: it parses terminal arguments, calls the service
layer, and formats the result. All business logic lives in services/.
"""

import argparse
import json
import sys
from typing import Optional

from db import DEFAULT_DB_PATH
from services.alert_service import check_threshold
from services.analytics_service import (
    destination_ranking,
    direction_stats,
    price_history,
)
from services.query_service import (
    ComparisonResult,
    OfferRow,
    RunRow,
    compare_runs,
    list_offers,
    list_runs,
)
from services.search_service import search_and_save


def format_offer(offer: OfferRow) -> str:
    """Format a single offer into a one-line CLI string."""
    ret = offer.return_date if offer.return_date else "-"
    return (
        f"#{offer.id} run={offer.run_id} {offer.origin}->{offer.destination} "
        f"out={offer.departure_date} ret={ret} "
        f"{offer.price:.2f} {offer.currency} {offer.airline} {offer.flight_number} "
        f"({offer.created_at})"
    )


def format_run(run: RunRow) -> str:
    """Format a single search run into a one-line CLI string."""
    params = json.loads(run.params_json) if run.params_json else {}
    return (
        f"#{run.id} [{run.status}] {run.search_mode} "
        f"{params.get('from', '?')}->{params.get('to', '?')} ({run.created_at})"
    )


def cmd_search(args) -> int:
    """Handle the ``search`` command: scrape a URL and print saved offers."""
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
    """Handle the ``list`` command: print stored offers."""
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


def cmd_runs(args) -> int:
    """Handle the ``runs`` command: print the search history."""
    runs = list_runs(db_path=args.db, limit=args.limit)
    if not runs:
        print("Brak wyszukiwan w bazie.")
        return 0
    for run in runs:
        print(format_run(run))
    return 0


def cmd_compare(args) -> int:
    """Handle the ``compare`` command: compare the 2 latest runs for a route."""
    if not args.origin or not args.destination:
        print("compare wymaga --origin i --destination (np. --origin WAW --destination TIA)")
        return 1
    result: Optional[ComparisonResult] = compare_runs(
        db_path=args.db, origin=args.origin, destination=args.destination
    )
    if result is None:
        print("Za malo udanych wyszukiwan tej trasy do porownania (potrzeba 2).")
        return 0

    if result.diff > 0:
        direction = "taniej"
    elif result.diff < 0:
        direction = "drozej"
    else:
        direction = "bez zmian"

    print(f"Cena przelotu {args.origin.upper()}->{args.destination.upper()}:")
    print(
        f"  nowsze (#{result.newer_run_id}, {result.newer_at}): "
        f"{result.newer_price:.2f} {result.currency}"
    )
    print(
        f"  starsze (#{result.older_run_id}, {result.older_at}): "
        f"{result.older_price:.2f} {result.currency}"
    )
    print(f"  roznica: {abs(result.diff):.2f} {result.currency} ({direction})")
    return 0


def cmd_stats(args) -> int:
    """Handle the ``stats`` command: print price history of a specific flight."""
    origin = args.origin.upper()
    destination = args.destination.upper()

    if not args.date:
        print("Podaj date wylotu: --date RRRR-MM-DD (np. --date 2026-08-15)")
        return 1

    hist = price_history(origin, destination, args.date, db_path=args.db)
    if hist is None:
        print(f"Brak historii dla {origin}->{destination} {args.date}.")
        return 0

    print(f"Cena lotu {origin}->{destination} wylot {hist.departure_date} "
          f"(sledzony {hist.count}x):")
    print(f"  pierwsza:  {hist.first_price:.2f} EUR")
    print(f"  teraz:     {hist.last_price:.2f} EUR")
    print(f"  najtaniej: {hist.min_price:.2f} EUR")
    print(f"  najdrozej: {hist.max_price:.2f} EUR")
    if hist.count >= 2:
        change = hist.change
        label = "staniał" if change < 0 else ("podrozal" if change > 0 else "bez zmian")
        print(f"  -> {label} o {abs(change):.2f} EUR od pierwszego sprawdzenia")
    return 0


def cmd_rank(args) -> int:
    """Handle the ``rank`` command: print cheapest destinations from an airport."""
    origin = args.origin.upper()
    ranking = destination_ranking(origin, db_path=args.db, limit=args.limit)
    if not ranking:
        print(f"Brak danych dla wylotow z {origin}.")
        return 0
    print(f"Najtansze kierunki z {origin} (EUR):")
    for r in ranking:
        print(f"  {r.destination}: min {r.min_price:.2f}, sr. {r.avg_price:.2f} ({r.count} ofert)")
    return 0


def cmd_leg(args) -> int:
    """Handle the ``leg`` command: print single-leg prices for both directions."""
    o, d = args.origin.upper(), args.destination.upper()
    there = direction_stats(o, d, db_path=args.db)
    back = direction_stats(d, o, db_path=args.db)
    if there is None and back is None:
        print(f"Brak danych o przelotach {o} <-> {d}.")
        return 0
    print("Ceny pojedynczego przelotu (na poziomie nog, EUR):")
    if there:
        print(f"  {o}->{d}: min {there.min_price:.2f} mediana {there.median_price:.2f} ({there.count} obs.)")
    else:
        print(f"  {o}->{d}: brak danych")
    if back:
        print(f"  {d}->{o}: min {back.min_price:.2f} mediana {back.median_price:.2f} ({back.count} obs.)")
    else:
        print(f"  {d}->{o}: brak danych")
    if there and back:
        diff = back.median_price - there.median_price
        if abs(diff) >= 0.01:
            pricier = d if diff < 0 else o
            print(f"  Asymetria: powrot do {pricier} drozszy o {abs(diff):.2f} EUR (mediana)")
    return 0


def cmd_alert(args) -> int:
    """Handle the ``alert`` command: check the cheapest offer against a threshold."""
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
    """Build and return the argparse parser with all subcommands."""
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

    stats_p = sub.add_parser("stats", help="Historia ceny konkretnego lotu w czasie")
    stats_p.add_argument("--origin", required=True)
    stats_p.add_argument("--destination", required=True)
    stats_p.add_argument("--date", default=None,
                         help="Data wylotu RRRR-MM-DD (wymagana)")

    rank_p = sub.add_parser("rank", help="Ranking najtanszych kierunkow z lotniska")
    rank_p.add_argument("--origin", required=True)
    rank_p.add_argument("--limit", type=int, default=20)

    leg_p = sub.add_parser("leg", help="Cena pojedynczego przelotu (obie strony)")
    leg_p.add_argument("--origin", required=True)
    leg_p.add_argument("--destination", required=True)

    return parser


def main() -> None:
    """CLI entry point: dispatch to the selected subcommand handler."""
    # Shortcut: 'python main.py https://...' without a subcommand
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
        "runs": cmd_runs,
        "compare": cmd_compare,
        "alert": cmd_alert,
        "stats": cmd_stats,
        "rank": cmd_rank,
        "leg": cmd_leg,
    }
    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
