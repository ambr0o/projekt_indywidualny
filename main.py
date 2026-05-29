import argparse
import sqlite3
import sys

from flights import parse_price_text, scrape_flight_from_results_url_full
from db import (
    DEFAULT_DB_PATH,
    compare_latest_runs,
    create_flight_offers_table,
    create_search_runs_table,
    finish_search_run,
    format_offer_row,
    get_best_offer,
    insert_flight_offer,
    print_offers,
    print_search_runs,
    start_search_run,
)


def normalize_offer(raw_offer):
    price_val, currency_val = parse_price_text(raw_offer["price_text"])

    return {
        "origin": raw_offer["origin"],
        "destination": raw_offer["destination"],
        "departure_date": raw_offer["departure_date"],
        "return_date": raw_offer["return_date"],
        "price": price_val,
        "currency": currency_val,
        "airline": raw_offer["airline"],
        "flight_number": raw_offer["flight_number"],
    }


def open_db(db_path):
    conn = sqlite3.connect(db_path)
    create_search_runs_table(conn)
    create_flight_offers_table(conn)
    return conn


def run_search(flight_url, max_results=20, db_path=DEFAULT_DB_PATH):
    from flights import parse_flight_url

    print("start")
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
        scrape_result = scrape_flight_from_results_url_full(flight_url, max_results=max_results)

        if scrape_result.error:
            print(f"blad scraping: {scrape_result.message}")
            finish_search_run(conn, run_id=run_id, status="failed")
            return 1

        raw_offers = scrape_result.offers
        print(f"zapis ({len(raw_offers)} ofert)")
        for raw_offer in raw_offers:
            offer = normalize_offer(raw_offer)
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
            )

        finish_search_run(conn, run_id=run_id, status="done")
        print_offers(conn, search_run_id=run_id)
        print_search_runs(conn, limit=1)
        return 0
    except Exception as exc:
        print(f"blad nieoczekiwany: {exc}")
        finish_search_run(conn, run_id=run_id, status="failed")
        return 1
    finally:
        conn.close()


def cmd_list(args):
    conn = open_db(args.db)
    if args.run_id:
        print(f"Oferty dla run #{args.run_id}:")
        print_offers(conn, search_run_id=args.run_id, limit=args.limit)
    else:
        print(f"Ostatnie oferty (max {args.limit}):")
        print_offers(conn, search_run_id=None, limit=args.limit)
    conn.close()


def cmd_best(args):
    conn = open_db(args.db)
    row = get_best_offer(conn, search_run_id=args.run_id)
    if row is None:
        print("Brak ofert.")
    else:
        print(format_offer_row(row))
    conn.close()


def cmd_runs(args):
    conn = open_db(args.db)
    print_search_runs(conn, limit=args.limit)
    conn.close()


def cmd_compare(args):
    conn = open_db(args.db)
    result = compare_latest_runs(conn, origin=args.origin, destination=args.destination)
    if result is None:
        print("Za malo udanych wyszukiwan do porownania (potrzeba 2).")
    else:
        direction = "taniej" if result["diff"] > 0 else "drozej"
        if result["diff"] == 0:
            direction = "bez zmian"
        print(
            f"Run #{result['newer_run_id']} ({result['newer_at']}): "
            f"{result['newer_price']:.2f} {result['currency']}"
        )
        print(
            f"Run #{result['older_run_id']} ({result['older_at']}): "
            f"{result['older_price']:.2f} {result['currency']}"
        )
        print(f"Roznica: {abs(result['diff']):.2f} {result['currency']} ({direction})")
    conn.close()


def cmd_alert(args):
    conn = open_db(args.db)
    row = get_best_offer(conn, search_run_id=args.run_id)
    conn.close()
    if row is None:
        print("Brak ofert do sprawdzenia.")
        return 1
    price = row[6]
    currency = row[7]
    if currency != args.currency and args.currency != "ANY":
        print(f"Waluta oferty ({currency}) != oczekiwana ({args.currency}), pomijam.")
        return 1
    if price <= args.threshold:
        print(f"ALERT: {price:.2f} {currency} <= {args.threshold:.2f} {args.currency}")
        print(format_offer_row(row))
        return 0
    print(f"OK: {price:.2f} {currency} > próg {args.threshold:.2f}")
    return 2


def build_parser():
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


def main():
    if len(sys.argv) > 1 and sys.argv[1].startswith("http"):
        sys.exit(run_search(sys.argv[1].strip()))

    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        print("\nLub: python main.py 'https://www.azair.eu/...'")
        print("Link wygeneruj osobno: python test_url.py --src-label ...")
        sys.exit(1)

    if args.command == "search":
        sys.exit(run_search(args.url, max_results=args.max_results, db_path=args.db))
    if args.command == "list":
        cmd_list(args)
    elif args.command == "best":
        cmd_best(args)
    elif args.command == "runs":
        cmd_runs(args)
    elif args.command == "compare":
        cmd_compare(args)
    elif args.command == "alert":
        sys.exit(cmd_alert(args))


if __name__ == "__main__":
    main()
