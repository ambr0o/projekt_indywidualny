"""Pogoda klimatyczna dla destynacji .

Wspolrzedne lotnisk z data/airports.json. Cache w SQLite (iata, month).
"""

import json
import statistics
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import httpx

from db import DEFAULT_DB_PATH, get_cached_weather, save_weather_cache
from services.db_session import open_db

_AIRPORTS_PATH = Path(__file__).resolve().parent.parent / "data" / "airports.json"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def _load_coords() -> dict:
    """Mapa kod IATA -> (lat, lon) z airports.json."""
    try:
        raw = json.loads(_AIRPORTS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    coords = {}
    for k, v in raw.items():
        if k.startswith("_") or not isinstance(v, dict):
            continue
        if "lat" in v and "lon" in v:
            coords[k] = (v["lat"], v["lon"])
    return coords


AIRPORT_COORDS = _load_coords()


@dataclass
class WeatherInfo:
    """Typowa pogoda klimatyczna dla lotniska w danym miesiacu."""
    iata: str
    month: int
    temp_max: float       # srednia dzienna maks.
    temp_min: float       # srednia dzienna min.
    rain_mm: float        # suma opadow w miesiacu
    rainy_days: int       # liczba dni z opadem > 1mm
    kind: str             # "climate" (srednia historyczna)

    def summary(self) -> str:
        emoji = "☀️" if self.rainy_days <= 6 else ("🌦️" if self.rainy_days <= 12 else "🌧️")
        return (
            f"{emoji} ~{self.temp_max:.0f}°C / {self.temp_min:.0f}°C, "
            f"{self.rainy_days} dni z deszczem (typowo dla mies. {self.month:02d})"
        )


def _prev_year_month_range(month: int) -> tuple[str, str]:
    """Zwraca (start, end) jako YYYY-MM-DD dla danego miesiaca w poprzednim roku."""
    year = date.today().year - 1
    start = date(year, month, 1)
    # ostatni dzien miesiaca
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1).replace(day=1)
        end = date(year, month, (end - date(year, month, 1)).days)
    return start.isoformat(), end.isoformat()


def weather_for(iata: str, month: int, db_path: str = DEFAULT_DB_PATH) -> Optional[WeatherInfo]:

    iata = iata.upper()
    if iata not in AIRPORT_COORDS:
        return None
    if not (1 <= month <= 12):
        return None

    conn = open_db(db_path)
    try:
        cached = get_cached_weather(conn, iata, month)
        if cached is not None:
            tmax, tmin, rain, rdays, kind = cached
            return WeatherInfo(iata, month, tmax, tmin, rain, rdays, kind)

        lat, lon = AIRPORT_COORDS[iata]
        start, end = _prev_year_month_range(month)
        try:
            resp = httpx.get(ARCHIVE_URL, params={
                "latitude": lat, "longitude": lon,
                "start_date": start, "end_date": end,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                "timezone": "auto",
            }, timeout=30)
            daily = resp.json()["daily"]
        except Exception:
            return None

        tmaxes = [x for x in daily["temperature_2m_max"] if x is not None]
        tmins = [x for x in daily["temperature_2m_min"] if x is not None]
        rains = [x for x in daily["precipitation_sum"] if x is not None]
        if not tmaxes:
            return None

        info = WeatherInfo(
            iata=iata,
            month=month,
            temp_max=round(statistics.mean(tmaxes), 1),
            temp_min=round(statistics.mean(tmins), 1),
            rain_mm=round(sum(rains), 1),
            rainy_days=sum(1 for x in rains if x > 1),
            kind="climate",
        )
        save_weather_cache(conn, iata, month, info.temp_max, info.temp_min,
                           info.rain_mm, info.rainy_days, info.kind)
        return info
    finally:
        conn.close()
