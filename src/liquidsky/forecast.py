"""Weather forecasting from free, no-key sources.

We build a forecast for a station's daily high temperature (Fahrenheit) by:

  1. Querying Open-Meteo's free forecast API across several global models
     (ECMWF, GFS, ICON, GEM, JMA). The spread *across models* is a cheap proxy
     for forecast uncertainty -> `sigma`.
  2. Fetching the latest METAR observation (aviationweather.gov). If the trading
     day is already in progress, the temperature observed so far is a hard floor
     on the day's eventual high, so we nudge `mu` upward when METAR exceeds it.

The result is a Gaussian `Normal(mu, sigma)` over the day's high, which
`strategy.py` turns into per-bucket probabilities. Every network call is
defensive: a source that fails is simply dropped, and we fall back to the
configured default sigma when only one model responds.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

import requests

from .cities import City

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
METAR_URL = "https://aviationweather.gov/api/data/metar"

# Global deterministic models available without a key on Open-Meteo.
OPEN_METEO_MODELS = [
    "ecmwf_ifs025",
    "gfs_seamless",
    "icon_seamless",
    "gem_seamless",
    "jma_seamless",
]


@dataclass
class ForecastResult:
    mu: float                       # expected daily high (F)
    sigma: float                    # uncertainty (F)
    n_models: int                   # how many model values fed mu
    model_highs: List[float] = field(default_factory=list)
    metar_temp_f: Optional[float] = None
    sources: Dict[str, float] = field(default_factory=dict)


def _c_to_f(celsius: float) -> float:
    return celsius * 9.0 / 5.0 + 32.0


def fetch_open_meteo_highs(
    city: City, target: date, timeout: float = 20.0
) -> List[float]:
    """Return each model's forecast daily high (F) for `target`."""
    params = {
        "latitude": city.lat,
        "longitude": city.lon,
        "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit",
        "timezone": city.tz,
        "forecast_days": 3,
        "models": ",".join(OPEN_METEO_MODELS),
    }
    resp = requests.get(OPEN_METEO_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    daily = resp.json().get("daily", {})

    times = daily.get("time", [])
    target_str = target.isoformat()
    if target_str not in times:
        return []
    idx = times.index(target_str)

    # With multiple models, Open-Meteo suffixes each key with the model name,
    # e.g. "temperature_2m_max_ecmwf_ifs025". With one model it's unsuffixed.
    highs: List[float] = []
    for key, values in daily.items():
        if not key.startswith("temperature_2m_max"):
            continue
        if idx < len(values) and values[idx] is not None:
            highs.append(float(values[idx]))
    return highs


def fetch_metar_temp_f(
    city: City, timeout: float = 20.0
) -> Optional[float]:
    """Return the latest METAR air temperature (F), or None on failure."""
    params = {"ids": city.metar_station, "format": "json", "hours": 3}
    resp = requests.get(METAR_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    obs = resp.json()
    if not obs:
        return None
    # API returns most-recent first; temp is Celsius in field "temp".
    latest = obs[0]
    temp_c = latest.get("temp")
    if temp_c is None:
        return None
    return _c_to_f(float(temp_c))


def build_forecast(
    city: City,
    target: date,
    default_sigma: float = 2.5,
    sigma_floor: float = 1.0,
    timeout: float = 20.0,
) -> Optional[ForecastResult]:
    """Blend model spread + METAR into a Gaussian forecast for the day's high.

    Returns None if no model data is available (the city is then skipped).
    """
    try:
        highs = fetch_open_meteo_highs(city, target, timeout=timeout)
    except (requests.RequestException, ValueError):
        highs = []

    if not highs:
        return None

    mu = statistics.fmean(highs)
    if len(highs) >= 2:
        sigma = max(statistics.stdev(highs), sigma_floor)
    else:
        sigma = default_sigma

    sources = {"open_meteo_mean": mu, "open_meteo_n": float(len(highs))}

    metar_f: Optional[float] = None
    try:
        metar_f = fetch_metar_temp_f(city, timeout=timeout)
    except (requests.RequestException, ValueError, KeyError):
        metar_f = None

    if metar_f is not None:
        sources["metar_temp_f"] = metar_f
        # Observed temperature is a floor on the eventual high. If it already
        # exceeds the model mean, the day is running hot -> lift mu to it.
        if metar_f > mu:
            mu = metar_f

    return ForecastResult(
        mu=round(mu, 2),
        sigma=round(sigma, 2),
        n_models=len(highs),
        model_highs=highs,
        metar_temp_f=metar_f,
        sources=sources,
    )
