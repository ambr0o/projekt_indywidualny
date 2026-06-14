"""Logic for the /find command: parameter validation + AZair URL building.

Takes simple parameters (IATA codes, dates) instead of a ready-made URL.
Validates them BEFORE scraping anything, so that we can:

- give the user a fast, readable error (instead of waiting 40s for scraping)
- protect the database against garbage codes

Returns either a ready URL or an error message.
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from generate_url import build_search_url

IATA_RE = re.compile(r"^[A-Z]{3}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_AIRPORTS_PATH = Path(__file__).resolve().parent.parent / "data" / "airports.json"


def _load_airport_labels() -> dict:
    """Load the IATA code -> AZair label map. Skips keys starting with '_'.

    airports.json has the format {code: {label, lat, lon}} - we extract the label only.
    """
    try:
        raw = json.loads(_AIRPORTS_PATH.read_text(encoding="utf-8"))
        return {
            k: v["label"]
            for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, dict) and "label" in v
        }
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


AIRPORT_LABELS = _load_airport_labels()


def azair_label(code: str) -> str:
    """Return the AZair label for a code. Falls back to 'CODE [CODE]' if unknown.

    NOTE: the fallback works for airports where code=city (TIA, SOF), but fails
    for hubs where code != city (BGY=Milan) - AZair requires the city name in the label.
    """
    return AIRPORT_LABELS.get(code.upper(), f"{code.upper()} [{code.upper()}]")


@dataclass
class FindRequest:
    """Result of /find validation - either a ready URL or an error."""
    url: Optional[str] = None
    error: Optional[str] = None
    origin: str = ""
    destination: str = ""
    is_anywhere: bool = False
    is_oneway: bool = False
    known_destination: bool = True   # whether the destination was in the airport map (False = risk of no results)


def _valid_date(s: str) -> bool:
    """Return True if ``s`` is a valid ISO date in ``YYYY-MM-DD`` format."""
    if not ISO_DATE_RE.match(s):
        return False
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def build_find_request(
    origin: str,
    destination: str,
    dep_date: str,
    arr_date: str,
    oneway: bool = False,
    min_days: Optional[int] = None,
    max_days: Optional[int] = None,
    today: Optional[date] = None,
) -> FindRequest:
    """Validate search parameters and build the AZair URL.

    Checks IATA codes and dates BEFORE starting the costly scraping, so the user
    gets a fast, readable error instead of waiting for an empty result.

    Args:
        origin (str): IATA code of the origin airport (3 letters, e.g. "WAW").
        destination (str): IATA code of the destination, or "ANYWHERE" for any direction.
        dep_date (str): Departure date in "YYYY-MM-DD" format.
        arr_date (str): Search window boundary / return date "YYYY-MM-DD".
        oneway (bool): True for a one-way flight. Defaults to False.
        min_days (Optional[int]): Min days of stay (round-trip only). None = default.
        max_days (Optional[int]): Max days of stay (round-trip only). None = default.
        today (Optional[date]): "Today" for date validation (injectable in tests).

    Returns:
        FindRequest: Object with a ready ``url`` or a filled-in ``error``.
        The ``known_destination`` field tells whether the destination was in the airport map.
    """
    origin = origin.strip().upper()
    destination = destination.strip().upper()

    # Validation of the origin airport
    if not IATA_RE.match(origin):
        return FindRequest(error=(
            f"'{origin}' to nie kod IATA. Uzyj 3-literowego kodu lotniska wylotu, "
            f"np. WAW (Warszawa), KRK (Krakow), GDN (Gdansk)."
        ))

    is_anywhere = destination in ("ANYWHERE", "ANY", "*")

    # Destination validation (unless anywhere)
    if not is_anywhere and not IATA_RE.match(destination):
        return FindRequest(error=(
            f"'{destination}' to nie kod IATA. Uzyj 3-literowego kodu, np. TIA (Tirana), "
            f"FCO (Rzym), albo wpisz 'anywhere' dla dowolnego kierunku."
        ))

    # Date validation
    if not _valid_date(dep_date):
        return FindRequest(error=f"Data od '{dep_date}' jest niepoprawna. Format: RRRR-MM-DD, np. 2026-08-02.")
    if not _valid_date(arr_date):
        return FindRequest(error=f"Data do '{arr_date}' jest niepoprawna. Format: RRRR-MM-DD, np. 2026-08-09.")
    if arr_date < dep_date:
        return FindRequest(error="Data do nie moze byc wczesniejsza niz data od.")
    # The departure date cannot be in the past (searching for past flights makes no sense).
    today = today or date.today()
    if date.fromisoformat(dep_date) < today:
        return FindRequest(error=f"Data wylotu '{dep_date}' jest w przeszlosci. Podaj date od dzis ({today.isoformat()}).")

    # Stay-length validation (round-trip only; one-way ignores it). Default: 2-8 (from generate_url).
    MAX_STAY = 60   # reasonable upper limit (AZair does not handle longer stays sensibly anyway)
    stay_kwargs = {}
    if not oneway and (min_days is not None or max_days is not None):
        lo = min_days if min_days is not None else 2
        hi = max_days if max_days is not None else 8
        if lo < 0 or hi < 0:
            return FindRequest(error="Liczba dni pobytu nie moze byc ujemna.")
        if lo > hi:
            return FindRequest(error="Min. dni pobytu nie moze byc wieksze niz max.")
        if hi > MAX_STAY:
            return FindRequest(error=f"Maks. dlugosc pobytu to {MAX_STAY} dni.")
        stay_kwargs = {"min_days_stay": str(lo), "max_days_stay": str(hi)}

    oneway_str = "oneway" if oneway else "return"

    if is_anywhere:
        url = build_search_url(
            azair_label(origin), "Anywhere [XXX]", dep_date, arr_date,
            src_codes=[origin], dst_codes=None, dst_typed_text="any",
            dst_mc="", currency="EUR", is_oneway=oneway_str,
            max_chng="0",   # direct flights only - the 2-leg model is always correct
            **stay_kwargs,
        )
        return FindRequest(
            url=url, origin=origin, destination="ANYWHERE",
            is_anywhere=True, is_oneway=oneway, known_destination=True,
        )

    known = destination in AIRPORT_LABELS
    url = build_search_url(
        azair_label(origin), azair_label(destination), dep_date, arr_date,
        src_codes=[origin], dst_codes=[destination],
        currency="EUR", is_oneway=oneway_str,
        max_chng="0",   # direct flights only
        **stay_kwargs,
    )

    return FindRequest(
        url=url,
        origin=origin,
        destination=destination,
        is_anywhere=False,
        is_oneway=oneway,
        known_destination=known,
    )
