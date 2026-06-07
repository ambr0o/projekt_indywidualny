"""Logika komendy /find: walidacja parametrow + budowa URL AZair.

Bierze proste parametry (kody IATA, daty) zamiast gotowego URL-a.
Waliduje je ZANIM cokolwiek scrapujemy, zeby:
  - dac uzytkownikowi szybki, czytelny blad (nie czekac 40s na scraping)
  - chronic baze przed smieciowymi kodami

Zwraca albo gotowy URL, albo komunikat bledu.
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
    """Wczytuje mape kod IATA -> etykieta AZair. Pomija klucze zaczynajace sie od '_'.

    airports.json ma format {kod: {label, lat, lon}} - wyciagamy sam label.
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
    """Zwraca etykiete AZair dla kodu. Fallback 'KOD [KOD]' gdy brak w mapie.

    UWAGA: fallback dziala dla lotnisk gdzie kod=miasto (TIA, SOF), ale zawodzi
    dla hubow gdzie kod != miasto (BGY=Milan) - AZair wymaga nazwy miasta w etykiecie.
    """
    return AIRPORT_LABELS.get(code.upper(), f"{code.upper()} [{code.upper()}]")


@dataclass
class FindRequest:
    """Wynik walidacji /find - albo gotowy URL, albo blad."""
    url: Optional[str] = None
    error: Optional[str] = None
    origin: str = ""
    destination: str = ""
    is_anywhere: bool = False
    is_oneway: bool = False
    known_destination: bool = True   # czy cel byl w mapie lotnisk (False = ryzyko braku wynikow)


def _valid_date(s: str) -> bool:
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
) -> FindRequest:
    """Waliduje parametry i buduje URL. Zwraca FindRequest z url albo error."""
    origin = origin.strip().upper()
    destination = destination.strip().upper()

    # Walidacja lotniska wylotu
    if not IATA_RE.match(origin):
        return FindRequest(error=(
            f"'{origin}' to nie kod IATA. Uzyj 3-literowego kodu lotniska wylotu, "
            f"np. WAW (Warszawa), KRK (Krakow), GDN (Gdansk)."
        ))

    is_anywhere = destination in ("ANYWHERE", "ANY", "*")

    # Walidacja celu (chyba ze anywhere)
    if not is_anywhere and not IATA_RE.match(destination):
        return FindRequest(error=(
            f"'{destination}' to nie kod IATA. Uzyj 3-literowego kodu, np. TIA (Tirana), "
            f"FCO (Rzym), albo wpisz 'anywhere' dla dowolnego kierunku."
        ))

    # Walidacja dat
    if not _valid_date(dep_date):
        return FindRequest(error=f"Data od '{dep_date}' jest niepoprawna. Format: RRRR-MM-DD, np. 2026-08-02.")
    if not _valid_date(arr_date):
        return FindRequest(error=f"Data do '{arr_date}' jest niepoprawna. Format: RRRR-MM-DD, np. 2026-08-09.")
    if arr_date < dep_date:
        return FindRequest(error="Data do nie moze byc wczesniejsza niz data od.")

    oneway_str = "oneway" if oneway else "return"

    if is_anywhere:
        url = build_search_url(
            azair_label(origin), "Anywhere [XXX]", dep_date, arr_date,
            src_codes=[origin], dst_codes=None, dst_typed_text="any",
            dst_mc="", currency="EUR", is_oneway=oneway_str,
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
    )

    return FindRequest(
        url=url,
        origin=origin,
        destination=destination,
        is_anywhere=False,
        is_oneway=oneway,
        known_destination=known,
    )
