"""Price monitor cron - the heart of the product.

Iterates over ALL active watches (across all users), scrapes each route, and
when the price drops below the threshold sends a push to the watch owner (by
chat_id). Each scrape also collects data into the database (feeding the
historical analytics).

Two modes:
    python cron_check.py            # one-off pass (for testing / Task Scheduler / launchd)
    python cron_check.py --loop     # loop every INTERVAL_HOURS (standalone daemon, cross-platform)

Anti-spam: alert only when price < threshold AND it was previously >= threshold
(a downward threshold crossing). State is kept in watched_routes.last_price.

Configuration from .env:
    TELEGRAM_BOT_TOKEN   - for sending push notifications
    TRAVEL_AGENT_DB      - database
"""

import argparse
import asyncio
import logging
from datetime import date, timedelta
import os
import random
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot

from db import update_watched_route_check
from services.analytics_service import price_percentile
from services.db_session import open_db
from services.find_service import build_find_request
from services.query_service import get_best
from services.search_service import search_and_save
from services.watch_service import all_active_watches

load_dotenv(Path(__file__).resolve().parent / ".env")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("travel_agent.cron")

INTERVAL_HOURS = float(os.getenv("CRON_INTERVAL_HOURS", "6"))
MIN_PAUSE = 15.0
MAX_PAUSE = 30.0


def _db_path() -> str:
    """Return the SQLite database path from .env (default: database.db)."""
    return os.getenv("TRAVEL_AGENT_DB", "database.db")


def _offer_details(offer) -> str:
    """Details of the cheapest offer: dates, airline, flight number."""
    ret = f" - powrot {offer.return_date}" if offer.return_date else " (one-way)"
    line = offer.airline if offer.airline and offer.airline != "UNKNOWN" else ""
    num = offer.flight_number if offer.flight_number and offer.flight_number != "UNKNOWN" else ""
    flight = f"{line} {num}".strip()
    parts = [f"wylot {offer.departure_date}{ret}"]
    if flight:
        parts.append(f"lot: {flight}")
    return "\n".join(parts)


def _build_alert_message(watch, offer) -> str:
    """Rich alert: price + flight details + drop + percentile."""
    price = offer.price
    window = f"{watch.dep_date} - {watch.arr_date}" if watch.dep_date else "~2 mies."
    lines = [
        f"[#{watch.id}] {watch.origin} -> {watch.destination} - OKAZJA!",
        f"{price:.2f} EUR (prog {watch.threshold:.0f}, szukane: {window})",
        _offer_details(offer),
    ]
    if watch.last_price is not None and watch.last_price > price:
        lines.append(f"spadek z {watch.last_price:.2f} EUR")

    pct = price_percentile(price, watch.origin, watch.destination, db_path=_db_path())
    if pct is not None and pct.sample_size >= 5:
        lines.append(f"{pct.percentile:.0f}. percentyl ({pct.sample_size} obs.)")

    return "\n".join(lines)


async def _send(bot: Bot, chat_id: int, text: str) -> None:
    """Send a Telegram message, logging a warning if delivery fails."""
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception as exc:
        log.warning("Nie udalo sie wyslac do %s: %s", chat_id, exc)


async def run_once() -> None:
    """Run a single pass over all active watches."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        log.error("Brak TELEGRAM_BOT_TOKEN - nie moge wysylac alertow.")
        return

    watches = all_active_watches(db_path=_db_path())
    if not watches:
        log.info("Brak aktywnych obserwacji.")
        return

    log.info("Sprawdzam %d obserwacji...", len(watches))
    bot = Bot(token)
    conn = open_db(_db_path())

    try:
        for i, watch in enumerate(watches, start=1):
            log.info("[%d/%d] %s->%s prog %.2f (chat %s)",
                     i, len(watches), watch.origin, watch.destination,
                     watch.threshold, watch.chat_id)

            # Date window: from the watch if set, otherwise default (today+7 .. today+60)
            if watch.dep_date and watch.arr_date:
                dep, arr = watch.dep_date, watch.arr_date
            else:
                dep = (date.today() + timedelta(days=7)).isoformat()
                arr = (date.today() + timedelta(days=60)).isoformat()
            req = build_find_request(
                watch.origin, watch.destination, dep, arr, oneway=watch.oneway
            )
            if req.error:
                log.warning("  walidacja: %s", req.error)
                continue

            try:
                result = await asyncio.to_thread(search_and_save, req.url, _db_path(), 20)
            except Exception as exc:
                log.warning("  scrape blad: %s", exc)
                continue

            if not result.success:
                log.info("  brak wynikow: %s", result.error_message)
                continue

            best = get_best(db_path=_db_path(), run_id=result.run_id)
            if best is None:
                continue
            current_price = best.price

            if watch.mode == "always":
                # 'always' mode: notify with the current price on every check
                trend = ""
                if watch.last_price is not None:
                    if current_price < watch.last_price:
                        trend = f" (-{watch.last_price - current_price:.2f})"
                    elif current_price > watch.last_price:
                        trend = f" (+{current_price - watch.last_price:.2f})"
                window = f"{watch.dep_date} - {watch.arr_date}" if watch.dep_date else "~2 mies."
                if current_price <= watch.threshold:
                    rel = f"ponizej progu {watch.threshold:.0f} - okazja!"
                else:
                    rel = f"powyzej progu {watch.threshold:.0f}"
                msg = (
                    f"[#{watch.id}] {watch.origin} -> {watch.destination}\n"
                    f"{current_price:.2f} EUR{trend} ({rel})\n"
                    f"{_offer_details(best)}\n"
                    f"(szukane: {window})"
                )
                await _send(bot, watch.chat_id, msg)
                log.info("  always: %.2f wyslane", current_price)
            else:
                # 'alert' mode: only on a downward threshold crossing (anti-spam)
                crossed_down = (
                    current_price <= watch.threshold
                    and (watch.last_price is None or watch.last_price > watch.threshold)
                )
                if crossed_down:
                    msg = _build_alert_message(watch, best)
                    await _send(bot, watch.chat_id, msg)
                    log.info("  ALERT wyslany: %.2f <= %.2f", current_price, watch.threshold)
                else:
                    log.info("  cena %.2f (prog %.2f) - bez alertu", current_price, watch.threshold)

            update_watched_route_check(conn, watch.id, current_price)

            if i < len(watches):
                # async sleep - does not block the event loop (we are in an async function)
                await asyncio.sleep(random.uniform(MIN_PAUSE, MAX_PAUSE))
    finally:
        conn.close()

    log.info("Przejscie zakonczone.")


async def run_loop() -> None:
    """Loop: run a check every INTERVAL_HOURS. Cross-platform (no launchd/cron)."""
    log.info("Demon startuje, interwal %.1f h. Ctrl+C zatrzymuje.", INTERVAL_HOURS)
    while True:
        try:
            await run_once()
        except Exception as exc:
            log.error("Blad w przejsciu: %s", exc)
        await asyncio.sleep(INTERVAL_HOURS * 3600)


def main() -> None:
    """Parse CLI arguments and run either a single pass or the daemon loop."""
    parser = argparse.ArgumentParser(description="Cron monitora cen lotow.")
    parser.add_argument("--loop", action="store_true",
                        help="Tryb demona: sprawdzaj co CRON_INTERVAL_HOURS")
    args = parser.parse_args()

    if args.loop:
        asyncio.run(run_loop())
    else:
        asyncio.run(run_once())


if __name__ == "__main__":
    main()
