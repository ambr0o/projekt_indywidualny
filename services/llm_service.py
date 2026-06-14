"""Natural language parser -> structured flight query (via Ollama).

Idea: the user writes to the bot in a normal sentence ("flight from Krakow to
Barcelona on July 15 one-way"), and a local LLM (Ollama) turns it into JSON,
which we map onto the existing /find and /watch commands.

Why this way:
  - Ollama runs locally (privacy, no API cost, works offline).
  - We talk to it over HTTP (the /api/chat endpoint) using only the stdlib
    (urllib), so as NOT to add dependencies to the project.
  - We force format="json" - Ollama then guarantees syntactically valid JSON.
  - The LLM can be unpredictable, so we HARD-validate the result (IATA codes,
    dates, ranges) before doing anything with it. The model suggests - the code decides.

All the logic is pure and testable: parsing the model's response (_coerce_intent)
is separated from the network call (_call_ollama), so tests do not need a running Ollama.
"""

from __future__ import annotations

import calendar
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from services.find_service import AIRPORT_LABELS, IATA_RE, _valid_date

# --- CONFIGURATION (from .env, with sensible defaults) ---

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
# qwen2.5:7b - tested on this hardware: ~6s, clean JSON, no thinking, fits in RAM.
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "60"))

# Default search window when the user gives no dates.
DEFAULT_TRIP_OFFSET_DAYS = 7    # start: today + 7
DEFAULT_TRIP_WINDOW_DAYS = 60   # end: start + 60

VALID_ACTIONS = ("find", "watch")


@dataclass
class FlightIntent:
    """A validated intent extracted from a natural-language sentence.

    This is the contract between the LLM and the rest of the bot: after
    validation we are sure the fields are correct (IATA codes, ISO dates, a known
    action) or ``error`` describes what went wrong.
    """
    action: str = "find"            # 'find' | 'watch'
    origin: str = ""
    destination: str = ""           # IATA code or 'ANYWHERE'
    dep_date: Optional[str] = None  # YYYY-MM-DD
    arr_date: Optional[str] = None  # YYYY-MM-DD
    oneway: bool = False
    threshold: Optional[float] = None
    min_days: Optional[int] = None   # min days at the destination (round-trip)
    max_days: Optional[int] = None   # max days at the destination (round-trip)
    error: Optional[str] = None
    raw: dict = field(default_factory=dict)   # raw model response (debug)

    @property
    def is_anywhere(self) -> bool:
        """True when the destination is 'ANYWHERE'."""
        return self.destination == "ANYWHERE"


def _today() -> date:
    """Isolated so tests can patch 'today' (monkeypatch)."""
    return date.today()


def _airport_hint() -> str:
    """List of known IATA codes for the prompt - the model picks from our map.

    This keeps the model from making up codes (e.g. returning BCN, which we have
    in the database, instead of a random Barcelona airport we do not support).
    """
    return ", ".join(sorted(AIRPORT_LABELS.keys()))


def build_system_prompt(today: Optional[date] = None) -> str:
    """Build the system prompt. Dates are relative to 'today', codes from our map."""
    today = today or _today()
    return (
        "Jestes parserem zapytan o loty. Zwracasz WYLACZNIE obiekt JSON, "
        "bez tekstu przed ani po, bez znacznikow markdown.\n\n"
        "Pola JSON:\n"
        '  "action": "find" gdy user chce wyszukac loty teraz; '
        '"watch" gdy chce byc powiadomiony / pilnowac ceny / ustawic alert.\n'
        '  "origin": kod IATA lotniska WYLOTU (skad user leci, slowo po "z"/"ze"). '
        '3 wielkie litery. Domyslnie "WAW" tylko gdy user nie podal miasta wylotu.\n'
        '  "destination": kod IATA celu, albo "ANYWHERE" gdy user mowi '
        '"gdziekolwiek", "tanio", "dokolwiek", "anywhere".\n'
        '  "dep_date": data wylotu "RRRR-MM-DD". Gdy user nie poda dnia, uzyj null.\n'
        '  "arr_date": data powrotu "RRRR-MM-DD" albo null.\n'
        '  "oneway": true gdy "w jedna strone"/"one way", inaczej false.\n'
        '  "threshold": prog ceny w EUR jako liczba (tylko gdy user mowi o progu/'
        'alercie/powiadomieniu, np. "ponizej 60 euro"). Inaczej null.\n'
        '  "min_days": min liczba dni na miejscu (gdy user mowi np. "na tydzien", '
        '"3-5 dni", "na weekend"). Inaczej null.\n'
        '  "max_days": max liczba dni na miejscu. Inaczej null.\n\n'
        f"Dzisiejsza data: {today.isoformat()}.\n"
        "Gdy user podaje miesiac bez roku (np. 'w sierpniu'), uzyj najblizszego "
        "przyszlego wystapienia tego miesiaca; dep_date = pierwszy dzien miesiaca, "
        "arr_date = ostatni dzien miesiaca.\n"
        "Tlumacz kraje na glowne lotnisko: Wlochy=FCO, Hiszpania=BCN, Grecja=ATH, "
        "Francja=CDG, Niemcy=BER, Portugalia=LIS, Albania=TIA, Bulgaria=SOF.\n"
        f"Znane kody IATA: {_airport_hint()}.\n"
        "Wybieraj kody wylacznie z tej listy gdy to mozliwe.\n\n"
        "Przyklady:\n"
        'Zapytanie: "gdziekolwiek tanio z Gdanska w wakacje" -> '
        '{"action":"find","origin":"GDN","destination":"ANYWHERE","dep_date":null,'
        '"arr_date":null,"oneway":false,"threshold":null}\n'
        'Zapytanie: "lot z Krakowa do Rzymu 10 lipca" -> '
        '{"action":"find","origin":"KRK","destination":"FCO","dep_date":"2026-07-10",'
        '"arr_date":null,"oneway":false,"threshold":null}\n'
        'Zapytanie: "loty z Warszawy do Rzymu w lipcu" -> '
        '{"action":"find","origin":"WAW","destination":"FCO","dep_date":"2026-07-01",'
        '"arr_date":"2026-07-31","oneway":false,"threshold":null}\n'
        'Zapytanie: "Barcelona z Krakowa w sierpniu na 5-7 dni" -> '
        '{"action":"find","origin":"KRK","destination":"BCN","dep_date":"2026-08-01",'
        '"arr_date":"2026-08-31","oneway":false,"threshold":null,"min_days":5,"max_days":7}\n'
    )


def _build_user_prompt(text: str) -> str:
    """Wrap the user's text into the prompt sent to the model."""
    return f'Zapytanie uzytkownika: "{text.strip()}"\nZwroc sam JSON.'


# --- VALIDATION / COERCION OF THE MODEL RESPONSE ---

_MONTH_DEFAULT_WINDOW = re.compile(r"^\d{4}-\d{2}$")


def _normalize_date(value, today: date) -> Optional[str]:
    """Accept 'YYYY-MM-DD' or 'YYYY-MM' (-> first day). Bad data -> None."""
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip()
    if _valid_date(v):
        return v
    if _MONTH_DEFAULT_WINDOW.match(v):
        return f"{v}-01"
    return None


def _coerce_intent(data: dict, text: str, today: Optional[date] = None) -> FlightIntent:
    """Turn the raw dict from the model into a validated FlightIntent.

    Here is all the hard defensive logic - the model can return anything, we let
    through only sensible values and fill gaps with reasonable defaults.
    """
    today = today or _today()
    intent = FlightIntent(raw=data)

    # action
    action = str(data.get("action", "find")).strip().lower()
    intent.action = action if action in VALID_ACTIONS else "find"

    # origin (default WAW)
    origin = str(data.get("origin") or "WAW").strip().upper()
    if not IATA_RE.match(origin):
        return FlightIntent(
            raw=data,
            error=f"Nie rozpoznalem lotniska wylotu ('{origin}'). Podaj kod IATA, np. WAW.",
        )
    intent.origin = origin

    # destination (IATA code or ANYWHERE)
    dest_raw = str(data.get("destination") or "").strip().upper()
    if dest_raw in ("ANYWHERE", "ANY", "*", ""):
        intent.destination = "ANYWHERE" if dest_raw else ""
    elif IATA_RE.match(dest_raw):
        intent.destination = dest_raw
    else:
        return FlightIntent(
            raw=data,
            error=f"Nie rozpoznalem celu ('{dest_raw}'). Podaj kod IATA albo napisz 'gdziekolwiek'.",
        )

    if not intent.destination:
        return FlightIntent(
            raw=data,
            error="Nie wiem dokad chcesz leciec. Podaj cel (np. Barcelona) albo 'gdziekolwiek'.",
        )

    # oneway
    intent.oneway = bool(data.get("oneway", False))

    # threshold
    thr = data.get("threshold")
    if thr is not None:
        try:
            thr_val = float(thr)
            intent.threshold = thr_val if thr_val > 0 else None
        except (TypeError, ValueError):
            intent.threshold = None

    # days at the destination (min/max) - only sensible positive numbers
    def _pos_int(v):
        """Coerce a value to a non-negative int, or None if invalid."""
        try:
            n = int(v)
            return n if n >= 0 else None
        except (TypeError, ValueError):
            return None

    intent.min_days = _pos_int(data.get("min_days"))
    intent.max_days = _pos_int(data.get("max_days"))
    # consistency: if both given and min > max -> swap
    if (intent.min_days is not None and intent.max_days is not None
            and intent.min_days > intent.max_days):
        intent.min_days, intent.max_days = intent.max_days, intent.min_days

    # dates - normalize and fill the default window
    dep = _normalize_date(data.get("dep_date"), today)
    arr = _normalize_date(data.get("arr_date"), today)

    if dep is None:
        dep = (today + timedelta(days=DEFAULT_TRIP_OFFSET_DAYS)).isoformat()

    if arr is None:
        dep_d = date.fromisoformat(dep)
        if dep_d.day == 1:
            # dep is the 1st of the month (typically "in July") -> close it to the
            # end of THAT month, not a generic +60 days (the user spoke of a month).
            last_day = calendar.monthrange(dep_d.year, dep_d.month)[1]
            arr = dep_d.replace(day=last_day).isoformat()
        else:
            arr = (dep_d + timedelta(days=DEFAULT_TRIP_WINDOW_DAYS)).isoformat()

    # consistency: arr >= dep
    if arr < dep:
        arr = (date.fromisoformat(dep) + timedelta(days=DEFAULT_TRIP_WINDOW_DAYS)).isoformat()

    intent.dep_date = dep
    intent.arr_date = arr

    # watch requires a concrete destination and a threshold
    if intent.action == "watch":
        if intent.is_anywhere:
            return FlightIntent(
                raw=data,
                error="Obserwacje (watch) wymagaja konkretnego celu, nie 'gdziekolwiek'.",
            )
        if intent.threshold is None:
            return FlightIntent(
                raw=data,
                error="Zeby pilnowac ceny podaj prog, np. 'powiadom gdy ponizej 60 euro'.",
            )

    return intent


# --- NETWORK LAYER (Ollama HTTP) ---

class LLMUnavailable(RuntimeError):
    """Ollama is not responding / not running."""


def _call_ollama(system_prompt: str, user_prompt: str,
                 model: Optional[str] = None) -> dict:
    """Send a request to Ollama /api/chat with the JSON format forced.

    Returns the parsed dict from the model's response. Raises LLMUnavailable
    when the server does not respond, or ValueError when the response is not JSON.
    """
    model = model or OLLAMA_MODEL
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "format": "json",     # Ollama forces syntactically valid JSON
        "stream": False,
        "options": {"temperature": 0},   # deterministic - this is parsing, not creativity
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise LLMUnavailable(
            f"Nie moge polaczyc sie z Ollama pod {OLLAMA_HOST}. "
            f"Uruchom 'ollama serve' i pobierz model ({model}). Szczegoly: {exc}"
        ) from exc

    content = body.get("message", {}).get("content", "")
    if not content:
        raise ValueError("Pusta odpowiedz modelu.")
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model zwrocil nie-JSON: {content[:200]}") from exc


def parse_query(text: str, model: Optional[str] = None,
                today: Optional[date] = None) -> FlightIntent:
    """Full pipeline: text -> Ollama -> validation -> FlightIntent.

    Raises LLMUnavailable when Ollama is down (the bot catches it and advises
    the user). Validation errors come back as FlightIntent.error (not an exception).
    """
    if not text or not text.strip():
        return FlightIntent(error="Puste zapytanie.")
    system_prompt = build_system_prompt(today)
    user_prompt = _build_user_prompt(text)
    raw = _call_ollama(system_prompt, user_prompt, model=model)
    return _coerce_intent(raw, text, today=today)


def is_available(model: Optional[str] = None) -> bool:
    """Quick ping - whether Ollama responds. Used when the bot starts."""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False
