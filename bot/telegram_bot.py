"""Telegram bot for travel_agent.

Commands::

    /start              - greeting
    /help               - list of commands
    /compare [origin] [destination]   - compare the 2 latest successful runs
    /alert <threshold> [currency]     - check a price threshold (EUR by default)

Configuration via .env::

    TELEGRAM_BOT_TOKEN          - token from @BotFather (required)
    TELEGRAM_ALLOWED_CHAT_IDS   - list of chat_ids (comma-separated) with access
                                  Empty = everyone.
    TRAVEL_AGENT_DB             - path to the SQLite database (optional, default: database.db)

Run::

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
    MessageHandler,
    filters,
)

from bot.formatters import (
    format_alert,
    format_comparison,
    format_direction,
    format_offers_list,
    format_price_history,
    format_ranking,
)
from services.alert_service import check_threshold
from services.analytics_service import (
    destination_ranking,
    direction_stats,
    price_history,
)
from services.find_service import build_find_request
from services.llm_service import LLMUnavailable, parse_query
from services.query_service import compare_runs, list_offers
from services.search_service import search_and_save
from services.watch_service import add_watch, list_watches, remove_watch
from services.weather_service import weather_for


load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("travel_agent.bot")


def _allowed_chat_ids() -> set[int]:
    """Return the set of allowed chat_ids from .env (empty = no whitelist)."""
    raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    if not raw:
        return set()
    return {int(x.strip()) for x in raw.split(",") if x.strip()}


def _db_path() -> str:
    """Return the SQLite database path from .env (default: database.db)."""
    return os.getenv("TRAVEL_AGENT_DB", "database.db")


def authorized_only(handler):
    """Decorator: if a whitelist is set, reject foreign chat_ids."""
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Check the whitelist before invoking the wrapped handler."""
        whitelist = _allowed_chat_ids()
        chat_id = update.effective_chat.id
        if whitelist and chat_id not in whitelist:
            log.warning("Odrzucony chat_id %s (nie na whiteliscie)", chat_id)
            await update.message.reply_text(
                "Brak dostepu. Skontaktuj sie z wlascicielem bota."
            )
            return
        return await handler(update, context)
    return wrapper


# --- COMMAND HANDLERS ---

@authorized_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /start: greet the user and show their chat_id."""
    text = (
        "Witaj w travel_agent!\n\n"
        f"Twoj chat_id: {update.effective_chat.id}\n\n"
        "Komendy: /help"
    )
    await update.message.reply_text(text)


@authorized_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /help: display the list of available commands."""
    text = (
        "Komendy:\n\n"
        "Mozesz tez pisac normalnie, np:\n"
        "   „lot z Krakowa do Barcelony w sierpniu”\n"
        "   „powiadom gdy WAW Tirana ponizej 50 euro”\n\n"
        "/compare <skad> <dokad> - porownaj cene przelotu (2 ost. wyszukiwania)\n"
        "/stats <skad> <dokad> <data> - jak zmieniala sie cena tego lotu w czasie\n"
        "/rank <origin> - najtansze kierunki z lotniska\n"
        "/leg <origin> <dest> - cena pojedynczego przelotu (obie strony)\n"
        "/weather <iata> <miesiac> - typowa pogoda (np. /weather TIA 8)\n"
        "/watch <skad> <dokad> <prog> [oneway] - pilnuj ceny, powiadom gdy spadnie\n"
        "/mywatches - twoje obserwacje\n"
        "/unwatch <numer> - usun obserwacje\n"
        "/alert <prog> [waluta] - sprawdz prog cenowy\n"
        "/find <skad> <dokad> <od> <do> [oneway] [dni N-M] - wyszukaj loty\n"
    )
    await update.message.reply_text(text)


@authorized_only
async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /compare: compare flight price from the 2 latest searches."""
    if len(context.args) < 2:
        await update.message.reply_text(
            "Uzycie: /compare <skad> <dokad>\n"
            "Porownuje cene przelotu miedzy 2 ostatnimi wyszukiwaniami.\n"
            "Np: /compare WAW TIA"
        )
        return
    origin = context.args[0].upper()
    destination = context.args[1].upper()
    result = compare_runs(db_path=_db_path(), origin=origin, destination=destination)
    await update.message.reply_text(format_comparison(result))


@authorized_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /stats: show how the price of a given flight changed over time."""
    if len(context.args) < 3:
        await update.message.reply_text(
            "Uzycie: /stats <skad> <dokad> <data-wylotu>\n"
            "Pokazuje jak zmieniala sie cena TEGO lotu w czasie.\n"
            "Np: /stats KRK BCN 2026-08-15"
        )
        return
    origin = context.args[0].upper()
    destination = context.args[1].upper()
    dep_date = context.args[2]
    hist = price_history(origin, destination, dep_date, db_path=_db_path())
    await update.message.reply_text(format_price_history(hist))


@authorized_only
async def cmd_rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /rank: display the cheapest destinations from a given airport."""
    if not context.args:
        await update.message.reply_text("Uzycie: /rank <origin>\nNp: /rank KRK")
        return
    origin = context.args[0].upper()
    ranking = destination_ranking(origin, db_path=_db_path(), limit=15)
    await update.message.reply_text(format_ranking(origin, ranking))


@authorized_only
async def cmd_weather(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /weather: typical climate weather for an airport in a given month."""
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
    await update.message.reply_text(f"{iata}, miesiac {month:02d}\n{w.summary()}")


@authorized_only
async def cmd_leg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /leg: single-leg flight price for both directions."""
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
async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /watch: set up a price watch for a route with threshold and options."""
    if len(context.args) < 3:
        await update.message.reply_text(
            "Uzycie: /watch <skad> <dokad> <prog> [data-od data-do] [oneway] [always]\n\n"
            "Przyklady:\n"
            "/watch WAW TIA 60                          (najblizsze ~2 mies.)\n"
            "/watch WAW TIA 130 2026-08-15 2026-08-18   (konkretny termin)\n"
            "/watch WAW TIA 50 2026-07-01 2026-08-31    (elastycznie, cale lato)\n"
            "/watch KRK FCO 100 always                  (cena przy kazdym sprawdzeniu)\n\n"
            "always = powiadom z aktualna cena zawsze; domyslnie tylko gdy <= prog."
        )
        return

    origin = context.args[0].upper()
    destination = context.args[1].upper()
    try:
        threshold = float(context.args[2].replace(",", "."))
    except ValueError:
        await update.message.reply_text("Prog musi byc liczba (np. 60 lub 59.99).")
        return

    # Parse the remaining arguments by type - order does not matter
    rest = context.args[3:]
    oneway = False
    mode = "alert"
    dates = []
    for arg in rest:
        a = arg.lower()
        if a == "oneway":
            oneway = True
        elif a == "always":
            mode = "always"
        elif a == "alert":
            mode = "alert"
        elif len(arg) == 10 and arg.count("-") == 2:
            dates.append(arg)
        # unknown arguments are ignored

    dep_date = dates[0] if len(dates) >= 1 else None
    arr_date = dates[1] if len(dates) >= 2 else None
    if len(dates) == 1:
        await update.message.reply_text("Podaj obie daty (od i do) albo zadnej.")
        return

    result = add_watch(
        chat_id=update.effective_chat.id,
        origin=origin, destination=destination,
        threshold=threshold, oneway=oneway,
        dep_date=dep_date, arr_date=arr_date, mode=mode,
        db_path=_db_path(),
    )
    if result.error:
        await update.message.reply_text(f"{result.error}")
        return

    trip_type = "one-way" if oneway else "round-trip"
    window = f"{dep_date} - {arr_date}" if dep_date else "najblizsze ~2 mies."
    mode_label = "zawsze podaje cene" if mode == "always" else f"gdy <= {threshold:.0f} EUR"
    await update.message.reply_text(
        f"Obserwacja #{result.watch_id}: {origin} -> {destination} ({trip_type})\n"
        f"Termin: {window}\n"
        f"Alert: {mode_label}"
    )


@authorized_only
async def cmd_mywatches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /mywatches: list the user's active watches."""
    watches = list_watches(chat_id=update.effective_chat.id, db_path=_db_path())
    if not watches:
        await update.message.reply_text(
            "Nie masz aktywnych obserwacji. Dodaj: /watch WAW TIA 60"
        )
        return
    lines = ["Twoje obserwacje:", ""]
    for w in watches:
        trip_type = "OW" if w.oneway else "RT"
        window = f"{w.dep_date} - {w.arr_date}" if w.dep_date else "~2 mies."
        mode_label = "always" if w.mode == "always" else "alert"
        last = f", ost. {w.last_price:.2f}" if w.last_price is not None else ""
        lines.append(
            f"#{w.id} {w.origin} -> {w.destination} [{trip_type}] prog {w.threshold:.0f} "
            f"| {window} | {mode_label}{last}"
        )
    lines.append("")
    lines.append("Usun: /unwatch <numer>")
    await update.message.reply_text("\n".join(lines))


@authorized_only
async def cmd_unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /unwatch: remove (deactivate) a user's watch by number."""
    if not context.args:
        await update.message.reply_text("Uzycie: /unwatch <numer>\nNumery zobaczysz przez /mywatches")
        return
    try:
        watch_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Numer musi byc liczba.")
        return
    ok = remove_watch(chat_id=update.effective_chat.id, watch_id=watch_id, db_path=_db_path())
    if ok:
        await update.message.reply_text(f"Usunieto obserwacje #{watch_id}.")
    else:
        await update.message.reply_text(f"Nie znaleziono Twojej obserwacji #{watch_id}.")


@authorized_only
async def cmd_alert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /alert: check a price threshold against the cheapest offer in the DB."""
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
    """Handler /find: search flights, save them and return top offers with weather."""
    args = context.args
    if len(args) < 4:
        await update.message.reply_text(
            "Uzycie: /find <skad> <dokad> <data-od> <data-do> [oneway] [dni: N lub N-M]\n\n"
            "Przyklady:\n"
            "/find WAW TIA 2026-08-02 2026-08-09\n"
            "/find KRK FCO 2026-07-01 2026-07-31 oneway\n"
            "/find KRK BCN 2026-08-01 2026-08-31 3-5   (3-5 dni na miejscu)\n"
            "/find GDN anywhere 2026-07-01 2026-08-31"
        )
        return

    origin, destination, dep_date, arr_date = args[0], args[1], args[2], args[3]

    # Remaining arguments by type - order does not matter: 'oneway' or day range 'N' / 'N-M'
    oneway = False
    min_days = None
    max_days = None
    for a in args[4:]:
        al = a.lower()
        if al == "oneway":
            oneway = True
        elif "-" in al and all(p.isdigit() for p in al.split("-", 1)):
            lo, hi = al.split("-", 1)
            min_days, max_days = int(lo), int(hi)
        elif al.isdigit():
            min_days = max_days = int(al)

    req = build_find_request(
        origin, destination, dep_date, arr_date, oneway=oneway,
        min_days=min_days, max_days=max_days,
    )
    if req.error:
        await update.message.reply_text(f"{req.error}")
        return

    trip_type = "one-way" if req.is_oneway else "round-trip"
    dest_label = "dowolny kierunek" if req.is_anywhere else req.destination
    prefix = ""
    if not req.is_anywhere and not req.known_destination:
        prefix = (
            f"Uwaga: nie znam lotniska {req.destination} - jesli to czesc wiekszego miasta "
            f"(jak BGY=Milan), wyszukiwanie moze nie zadzialac.\n\n"
        )
    await update.message.reply_text(f"{prefix}Szukam: {req.origin} -> {dest_label} ({trip_type})... (30-60s)")

    result = await asyncio.to_thread(search_and_save, req.url, _db_path(), 20)
    if not result.success:
        await update.message.reply_text(
            f"Brak wynikow: {result.error_message}\n"
            "Sprawdz czy kody lotnisk sa poprawne i czy w tym terminie sa loty."
        )
        return

    offers = list_offers(db_path=_db_path(), run_id=result.run_id, limit=10)
    msg = (
        f"Znalazlem {result.offers_count} ofert (run #{result.run_id})\n\n"
        + format_offers_list(offers, max_items=5)
    )

    # Weather at the destination (only for a concrete destination, not anywhere)
    if not req.is_anywhere:
        try:
            month = int(dep_date.split("-")[1])
            w = await asyncio.to_thread(weather_for, req.destination, month, _db_path())
            if w is not None:
                msg += f"\n\nPogoda {req.destination} (typowo):\n{w.summary()}"
        except (ValueError, IndexError):
            pass

    await update.message.reply_text(msg)


# --- NATURAL LANGUAGE (LLM via Ollama) ---

async def _run_find_intent(update: Update, intent) -> None:
    """Run a search from a ready intent (used by the NL handler)."""
    req = build_find_request(
        intent.origin, intent.destination, intent.dep_date, intent.arr_date,
        oneway=intent.oneway, min_days=intent.min_days, max_days=intent.max_days,
    )
    if req.error:
        await update.message.reply_text(f"{req.error}")
        return

    trip_type = "one-way" if req.is_oneway else "round-trip"
    dest_label = "dowolny kierunek" if req.is_anywhere else req.destination
    prefix = ""
    if not req.is_anywhere and not req.known_destination:
        prefix = (
            f"Uwaga: nie znam lotniska {req.destination} - jesli to czesc wiekszego miasta "
            f"(jak BGY=Milan), wyszukiwanie moze nie zadzialac.\n\n"
        )
    await update.message.reply_text(
        f"{prefix}Szukam: {req.origin} -> {dest_label} ({trip_type}), "
        f"{intent.dep_date} - {intent.arr_date}... (30-60s)"
    )

    result = await asyncio.to_thread(search_and_save, req.url, _db_path(), 20)
    if not result.success:
        await update.message.reply_text(
            f"Brak wynikow: {result.error_message}\n"
            "Sprobuj inny termin lub kierunek."
        )
        return

    offers = list_offers(db_path=_db_path(), run_id=result.run_id, limit=10)
    msg = (
        f"Znalazlem {result.offers_count} ofert (run #{result.run_id})\n\n"
        + format_offers_list(offers, max_items=5)
    )
    if not req.is_anywhere:
        try:
            month = int(intent.dep_date.split("-")[1])
            w = await asyncio.to_thread(weather_for, req.destination, month, _db_path())
            if w is not None:
                msg += f"\n\nPogoda {req.destination} (typowo):\n{w.summary()}"
        except (ValueError, IndexError):
            pass
    await update.message.reply_text(msg)


async def _run_watch_intent(update: Update, intent) -> None:
    """Set up a watch from a ready intent (used by the NL handler)."""
    result = add_watch(
        chat_id=update.effective_chat.id,
        origin=intent.origin, destination=intent.destination,
        threshold=intent.threshold, oneway=intent.oneway,
        dep_date=intent.dep_date, arr_date=intent.arr_date, mode="alert",
        db_path=_db_path(),
    )
    if result.error:
        await update.message.reply_text(f"{result.error}")
        return
    trip_type = "one-way" if intent.oneway else "round-trip"
    await update.message.reply_text(
        f"Obserwacja #{result.watch_id}: {intent.origin} -> {intent.destination} ({trip_type})\n"
        f"Termin: {intent.dep_date} - {intent.arr_date}\n"
        f"Alert: powiadomie gdy <= {intent.threshold:.0f} EUR"
    )


@authorized_only
async def on_natural_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Plain message (not a command) -> LLM turns it into an intent -> find/watch.

    The model SUGGESTS, the code DECIDES: the result is validated in llm_service,
    and here we handle errors (Ollama down, not understood) with a readable message.
    """
    text = (update.message.text or "").strip()
    if not text:
        return

    await update.message.reply_text("Rozumiem zapytanie...")

    try:
        intent = await asyncio.to_thread(parse_query, text)
    except LLMUnavailable:
        await update.message.reply_text(
            "Asystent jezyka naturalnego jest niedostepny (Ollama nie odpowiada).\n"
            "Uzyj komend, np: /find WAW BCN 2026-08-01 2026-08-31"
        )
        return
    except Exception as exc:
        log.warning("Blad parsowania NL: %s", exc)
        await update.message.reply_text(
            "Nie zrozumialem zapytania. Sprobuj prosciej, np:\n"
            "„lot z Krakowa do Barcelony w sierpniu” albo uzyj /help"
        )
        return

    if intent.error:
        await update.message.reply_text(f"{intent.error}")
        return

    if intent.action == "watch":
        await _run_watch_intent(update, intent)
    else:
        await _run_find_intent(update, intent)


# --- BOOTSTRAP ---

def build_application() -> Application:
    """Build the Telegram Application and register all handlers."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Brak TELEGRAM_BOT_TOKEN. Skopiuj .env.example -> .env i uzupelnij token."
        )

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("compare", cmd_compare))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("rank", cmd_rank))
    app.add_handler(CommandHandler("leg", cmd_leg))
    app.add_handler(CommandHandler("weather", cmd_weather))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("mywatches", cmd_mywatches))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))
    app.add_handler(CommandHandler("alert", cmd_alert))
    app.add_handler(CommandHandler("find", cmd_find))
    # Natural language: any text message that is NOT a command.
    # Must be the LAST handler, so it does not intercept commands.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_natural_language))
    return app


def main() -> None:
    """Bot entry point: build the application and start polling."""
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
