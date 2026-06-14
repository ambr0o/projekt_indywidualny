"""Climate weather for destinations.

Airport coordinates come from data/airports.json. Cached in SQLite (iata, month).
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
    """Map IATA code -> (lat, lon) from airports.json."""
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
    """Typical climate weather for an airport in a given month."""
    iata: str
    month: int
    temp_max: float       # average daily max
    temp_min: float       # average daily min
    rain_mm: float        # total precipitation in the month
    rainy_days: int       # number of days with precipitation > 1mm
    kind: str             # "climate" (historical average)

    def summary(self) -> str:
        """Return a short human-readable weather summary string."""
        if self.rainy_days <= 6:
            sky = "slonecznie"
        elif self.rainy_days <= 12:
            sky = "zmiennie"
        else:
            sky = "deszczowo"
        return (
            f"{sky}, ~{self.temp_max:.0f}°C / {self.temp_min:.0f}°C, "
            f"{self.rainy_days} dni z deszczem (typowo dla mies. {self.month:02d})"
        )


def _prev_year_month_range(month: int) -> tuple[str, str]:
    """Return (start, end) as YYYY-MM-DD for the given month in the previous year."""
    year = date.today().year - 1
    start = date(year, month, 1)
    # last day of the month
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1).replace(day=1)
        end = date(year, month, (end - date(year, month, 1)).days)
    return start.isoformat(), end.isoformat()


def weather_for(iata: str, month: int, db_path: str = DEFAULT_DB_PATH) -> Optional[WeatherInfo]:
    """Typical climate weather for an airport in a given month.

    Flights are usually beyond the forecast range (16 days), so we use historical
    data from the previous year for the same month. The result is cached in
    SQLite to avoid querying Open-Meteo repeatedly.

    Args:
        iata (str): IATA airport code (must be in data/airports.json).
        month (int): Month 1-12.
        db_path (str): Path to the SQLite database (cache).

    Returns:
        Optional[WeatherInfo]: Typical weather (temperatures, precipitation), or
        None when the airport is unknown, the month is invalid, or the API fails.
    """
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
