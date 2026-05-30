"""City table mapping Kalshi high-temp series to NWS resolution stations.

Kalshi daily high-temperature markets resolve on a specific weather station's
official NWS climate report (e.g. KXHIGHNY resolves on Central Park, *not* a city
centroid). Forecasting at the exact station coordinates matters because a few
degrees decides which 1-2 F bucket settles YES.

`config.cities` (a list of series tickers) subsets this table; an empty list
means "use every city defined here". True discovery of arbitrary new series is
limited by needing station coordinates, so adding a city = adding a row here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class City:
    name: str
    series_ticker: str   # Kalshi series, e.g. "KXHIGHNY"
    metar_station: str   # aviationweather.gov station id, e.g. "KNYC"
    lat: float
    lon: float
    tz: str              # IANA timezone for resolving "today's" high


CITIES: Dict[str, City] = {
    "KXHIGHNY": City("New York (Central Park)", "KXHIGHNY", "KNYC", 40.7790, -73.9692, "America/New_York"),
    "KXHIGHCHI": City("Chicago (Midway)", "KXHIGHCHI", "KMDW", 41.7868, -87.7522, "America/Chicago"),
    "KXHIGHLAX": City("Los Angeles (LAX)", "KXHIGHLAX", "KLAX", 33.9416, -118.4085, "America/Los_Angeles"),
    "KXHIGHMIA": City("Miami (Intl)", "KXHIGHMIA", "KMIA", 25.7959, -80.2870, "America/New_York"),
    "KXHIGHAUS": City("Austin (Bergstrom)", "KXHIGHAUS", "KAUS", 30.1975, -97.6664, "America/Chicago"),
    "KXHIGHDEN": City("Denver (Intl)", "KXHIGHDEN", "KDEN", 39.8617, -104.6731, "America/Denver"),
    "KXHIGHPHIL": City("Philadelphia (Intl)", "KXHIGHPHIL", "KPHL", 39.8729, -75.2437, "America/New_York"),
}


def resolve_cities(selected: Optional[List[str]]) -> List[City]:
    """Return the City rows for the configured tickers (all if none selected)."""
    if not selected:
        return list(CITIES.values())
    out: List[City] = []
    for ticker in selected:
        city = CITIES.get(ticker.upper())
        if city is None:
            raise KeyError(
                f"Unknown city/series {ticker!r}. Add it to cities.CITIES "
                f"with station coordinates. Known: {sorted(CITIES)}"
            )
        out.append(city)
    return out
