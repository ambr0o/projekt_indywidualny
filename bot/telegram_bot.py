"""Bot Telegrama dla travel_agent.

Komendy:
    /start              - powitanie
    /help               - lista komend
    /best               - najtansza oferta z bazy
    /list [limit]       - ostatnie zapisane oferty (domyslnie 10)
    /runs [limit]       - historia wyszukiwan
    /compare [origin] [destination]   - porownaj 2 ostatnie udane runy
    /alert <prog> [waluta]            - sprawdz prog cenowy (domyslnie EUR)
    /search <url>       - uruchom scraping URL-a AZair (sync, moze potrwac 30-60s)

Konfiguracja przez .env:
    TELEGRAM_BOT_TOKEN          - token od @BotFather (wymagane)
    TELEGRAM_ALLOWED_CHAT_IDS   - lista chat_id (oddzielone przecinkami) ktore maja dostep
                                  Puste = wszyscy.
    TRAVEL_AGENT_DB             - sciezka do bazy SQLite (opcjonalne, default: database.db)

Uruchomienie:
    python -m bot.telegram_bot
"""

import asyncio
import logging
import os
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from bot.formatters import (
    format_alert,
    format_best,
    format_comparison,
    format_direction,
    format_offers_list,
    format_ranking,
    format_runs_list,
    format_stats,
)
from services.alert_service import check_threshold
from services.analytics_service import (
    cheapest_weekday,
    destination_ranking,
    direction_stats,
    price_percentile,
    route_price_stats,
)
from services.find_service import build_find_request
from services.query_service import compare_runs, get_best, list_offers, list_runs
from services.search_service import search_and_save
from services.weather_service import weather_for


load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("travel_agent.bot")


def _allowed_chat_ids() -> set[int]:
    raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    if not raw:
        return set()
    return {int(x.strip()) for x in raw.split(",") if x.strip()}


def _db_path() -> str:
    return os.getenv("TRAVEL_AGENT_DB", "database.db")


def authorized_only(handler):
    """Dekorator: jesli ustawiono whiteliste, odrzuc obce chat_id."""
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        whitelist = _allowed_chat_ids()
        chat_id = update.effective_chat.id
        if whitelist and chat_id not in whitelist:
            log.warning("Odrzucony chat_id %s (nie na whiteliscie)", chat_id)
            await update.message.reply_text(
                "🚫 Brak dostepu. Skontaktuj sie z wlascicielem bota."
            )
            return
        return await handler(update, context)
    return wrapper


# --- HANDLERY KOMEND ---

@authorized_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "👋 Witaj w travel_agent!\n\n"
        f"Twoj chat_id: {update.effective_chat.id}\n\n"
        "Komendy: /help"
    )
    await update.message.reply_text(text)


@authorized_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "📖 Komendy:\n\n"
        "/best - najtansza oferta z bazy\n"
        "/list [limit] - ostatnie oferty\n"
        "/runs [limit] - historia wyszukiwan\n"
        "/compare [origin] [dest] - porownaj 2 ostatnie runy\n"
        "/stats <origin> <dest> [cena] - statystyki cen trasy\n"
        "/rank <origin> - najtansze kierunki z lotniska\n"
        "/leg <origin> <dest> - cena pojedynczego przelotu (obie strony)\n"
        "/weather <iata> <miesiac> - typowa pogoda (np. /weather TIA 8)\n"
        "/alert <prog> [waluta] - sprawdz prog cenowy\n"
        "/find <skad> <dokad> <od> <do> [oneway] - wyszukaj loty\n"
        "/search <url> - scrapuj URL AZair (30-60s)\n"
    )
    await update.message.reply_text(text)


@authorized_only
async def cmd_best(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    offer = get_best(db_path=_db_path())
    await update.message.reply_text(format_best(offer))


@authorized_only
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    limit = 10
    if context.args:
        try:
            limit = max(1, min(50, int(context.args[0])))
        except ValueError:
            await update.message.reply_text("Limit musi byc liczba.")
            return
    offers = list_offers(db_path=_db_path(), limit=limit)
    await update.message.reply_text(format_offers_list(offers, max_items=limit))


@authorized_only
async def cmd_runs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    limit = 10
    if context.args:
        try:
            limit = max(1, min(50, int(context.args[0])))
        except ValueError:
            await update.message.reply_text("Limit musi byc liczba.")
            return
    runs = list_runs(db_path=_db_path(), limit=limit)
    await update.message.reply_text(format_runs_list(runs, max_items=limit))


@authorized_only
async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    origin = context.args[0] if len(context.args) >= 1 else None
    destination = context.args[1] if len(context.args) >= 2 else None
    result = compare_runs(db_path=_db_path(), origin=origin, destination=destination)
    await update.message.reply_text(format_comparison(result))


@authorized_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Uzycie: /stats <origin> <destination> [cena]\nNp: /stats WMI MXP 30")
        return
    origin = context.args[0].upper()
    destination = context.args[1].upper()

    price = None
    if len(context.args) >= 3:
        try:
            price = float(context.args[2].replace(",", "."))
        except ValueError:
            await update.message.reply_text("Cena musi byc liczba.")
            return

    db = _db_path()
    stats = route_price_stats(origin, destination, db_path=db)
    weekdays = cheapest_weekday(origin, destination, db_path=db)
    percentile = price_percentile(price, origin, destination, db_path=db) if price is not None else None
    await update.message.reply_text(format_stats(stats, weekdays, percentile))


@authorized_only
async def cmd_rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Uzycie: /rank <origin>\nNp: /rank KRK")
        return
    origin = context.args[0].upper()
    ranking = destination_ranking(origin, db_path=_db_path(), limit=15)
    await update.message.reply_text(format_ranking(origin, ranking))


@authorized_only
async def cmd_weather(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text(
            "Uzycie: /weather <iata> <miesiac>\n"
            "Typowa pogoda klimatyczna dla lotniska.\n"
            "Np: /weather TIA 8  (Tirana w sierpniu)"
        )
        return
    iata = context.args[0].upper()
    try:
        month = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Miesiac musi byc liczba 1-12.")
        return
    if not (1 <= month <= 12):
        await update.message.reply_text("Miesiac musi byc 1-12.")
        return

    w = await asyncio.to_thread(weather_for, iata, month, _db_path())
    if w is None:
        await update.message.reply_text(
            f"Brak danych pogodowych dla {iata} (nieznane lotnisko lub blad API)."
        )
        return
    await update.message.reply_text(f"🌍 {iata}, miesiac {month:02d}\n{w.summary()}")


@authorized_only
async def cmd_leg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text(
            "Uzycie: /leg <origin> <destination>\n"
            "Cena POJEDYNCZEGO przelotu (nie calej podrozy) w obie strony.\n"
            "Np: /leg WAW TIA"
        )
        return
    origin = context.args[0].upper()
    destination = context.args[1].upper()
    db = _db_path()
    there = direction_stats(origin, destination, db_path=db)
    back = direction_stats(destination, origin, db_path=db)
    await update.message.reply_text(format_direction(there, back, origin, destination))


@authorized_only
async def cmd_alert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Uzycie: /alert <prog> [waluta]\nNp: /alert 30 EUR")
        return
    try:
        threshold = float(context.args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("Prog musi byc liczba (np. 30 lub 30.50).")
        return
    currency = context.args[1].upper() if len(context.args) >= 2 else "EUR"
    result = check_threshold(threshold=threshold, expected_currency=currency, db_path=_db_path())
    await update.message.reply_text(format_alert(result))


@authorized_only
async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if len(args) < 4:
        await update.message.reply_text(
            "Uzycie: /find <skad> <dokad> <data-od> <data-do> [oneway]\n\n"
            "Przyklady:\n"
            "/find WAW TIA 2026-08-02 2026-08-09\n"
            "/find KRK FCO 2026-07-01 2026-07-31 oneway\n"
            "/find GDN anywhere 2026-07-01 2026-08-31"
        )
        return

    origin, destination, dep_date, arr_date = args[0], args[1], args[2], args[3]
    oneway = len(args) >= 5 and args[4].lower() == "oneway"

    req = build_find_request(origin, destination, dep_date, arr_date, oneway=oneway)
    if req.error:
        await update.message.reply_text(f"❌ {req.error}")
        return

    typ = "one-way" if req.is_oneway else "round-trip"
    cel = "dowolny kierunek" if req.is_anywhere else req.destination
    prefix = ""
    if not req.is_anywhere and not req.known_destination:
        prefix = (
            f"⚠️ Nie znam lotniska {req.destination} - jesli to czesc wiekszego miasta "
            f"(jak BGY=Milan), wyszukiwanie moze nie zadzialac.\n\n"
        )
    await update.message.reply_text(f"{prefix}🔎 Szukam: {req.origin} → {cel} ({typ})... (30-60s)")

    result = await asyncio.to_thread(search_and_save, req.url, _db_path(), 20)
    if not result.success:
        await update.message.reply_text(
            f"❌ Brak wynikow: {result.error_message}\n"
            "Sprawdz czy kody lotnisk sa poprawne i czy w tym terminie sa loty."
        )
        return

    offers = list_offers(db_path=_db_path(), run_id=result.run_id, limit=10)
    msg = (
        f"✅ Znalazlem {result.offers_count} ofert (run #{result.run_id})\n\n"
        + format_offers_list(offers, max_items=5)
    )

    # Pogoda w destynacji (tylko dla konkretnego celu, nie anywhere)
    if not req.is_anywhere:
        try:
            month = int(dep_date.split("-")[1])
            w = await asyncio.to_thread(weather_for, req.destination, month, _db_path())
            if w is not None:
                msg += f"\n\n🌍 Pogoda {req.destination} (typowo):\n{w.summary()}"
        except (ValueError, IndexError):
            pass

    await update.message.reply_text(msg)


@authorized_only
async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "Uzycie: /search <url do AZair>\nLink wygenerujesz przez generate_url.py"
        )
        return
    url = " ".join(context.args).strip()
    if not url.startswith("http"):
        await update.message.reply_text("URL musi zaczynac sie od http(s)://")
        return

    await update.message.reply_text("🔎 Szukam... (30-60s)")
    # Playwright dziala synchronicznie - puszczamy w threadpoolu zeby nie blokowac event loopu
    result = await asyncio.to_thread(search_and_save, url, _db_path(), 20)

    if not result.success:
        await update.message.reply_text(f"❌ Blad: {result.error_message}")
        return

    offers = list_offers(db_path=_db_path(), run_id=result.run_id, limit=10)
    msg = (
        f"✅ Run #{result.run_id}: zapisano {result.offers_count} ofert\n\n"
        + format_offers_list(offers, max_items=5)
    )
    await update.message.reply_text(msg)


# --- BOOTSTRAP ---

def build_application() -> Application:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Brak TELEGRAM_BOT_TOKEN. Skopiuj .env.example -> .env i uzupelnij token."
        )

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("best", cmd_best))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("runs", cmd_runs))
    app.add_handler(CommandHandler("compare", cmd_compare))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("rank", cmd_rank))
    app.add_handler(CommandHandler("leg", cmd_leg))
    app.add_handler(CommandHandler("weather", cmd_weather))
    app.add_handler(CommandHandler("alert", cmd_alert))
    app.add_handler(CommandHandler("find", cmd_find))
    app.add_handler(CommandHandler("search", cmd_search))
    return app


def main() -> None:
    app = build_application()
    whitelist = _allowed_chat_ids()
    if whitelist:
        log.info("Whitelist aktywna: %s", whitelist)
    else:
        log.warning("Whitelist PUSTA - kazdy chat_id ma dostep do bota.")
    log.info("Bot startuje (Ctrl+C zatrzymuje)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
