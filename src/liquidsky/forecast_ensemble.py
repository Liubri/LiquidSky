"""Ensemble forecasting: a non-parametric alternative to the Gaussian model.

`forecast.py` averages a handful of *deterministic* models and treats their
spread as `sigma`, assuming the daily high is Gaussian. That spread is a poor
proxy for true uncertainty (the models are correlated) and systematically
over-prices tail buckets.

This module instead pulls Open-Meteo's **Ensemble API** — many perturbed members
of GFS and ECMWF (~31 + ~51 = ~82 members). The members *are* the distribution:
the probability a bucket settles YES is simply the fraction of members whose
forecast high lands in it (`empirical_bucket_probability`). No Gaussian
assumption, so skew and fat tails come for free.

Kalshi settles on the NWS climate report, so we optionally anchor the ensemble
to the official NWS daily-high forecast for the settlement station
(`fetch_nws_high`) by recentering the members — bias correction that preserves
the ensemble's shape (spread) while shifting its center toward the source the
market actually resolves on.

Every network call is defensive: a source that fails is dropped, and the caller
gets `None` (so the city is skipped) only when no member data is available.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

import requests

from .cities import City
from .strategy import Bucket

ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
NWS_POINTS_URL = "https://api.weather.gov/points/{lat},{lon}"

# Ensemble systems available without a key on Open-Meteo. gfs025 contributes the
# 31-member GEFS; ecmwf_ifs025 the 51-member ECMWF ENS — the "82-member dual
# ensemble" the better public Kalshi weather bots use.
ENSEMBLE_MODELS = ["gfs025", "ecmwf_ifs025"]

# Open-Meteo wants a contact string in the User-Agent for api.weather.gov too.
_HTTP_HEADERS = {"User-Agent": "liquidsky-weather-bot (contact: trader@example.com)"}


@dataclass
class EnsembleForecast:
    mu: float                       # mean member daily high (F)
    sigma: float                    # member spread (F)
    n_members: int                  # how many members fed the distribution
    members: List[float] = field(default_factory=list)
    nws_high_f: Optional[float] = None
    anchor_shift: float = 0.0       # F added to every member by NWS anchoring


def _c_to_f(celsius: float) -> float:
    return celsius * 9.0 / 5.0 + 32.0


# ------------------------------------------------------------- ensemble members
def fetch_ensemble_member_highs(
    city: City, target: date, timeout: float = 25.0
) -> List[float]:
    """Return every ensemble member's forecast daily high (F) for `target`.

    With multiple models Open-Meteo suffixes each daily key with the model and
    member, e.g. ``temperature_2m_max_gfs025_member01``. We collect the value at
    the target index from *every* key starting with ``temperature_2m_max``.
    """
    params = {
        "latitude": city.lat,
        "longitude": city.lon,
        "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit",
        "timezone": city.tz,
        "forecast_days": 4,
        "models": ",".join(ENSEMBLE_MODELS),
    }
    resp = requests.get(ENSEMBLE_URL, params=params, timeout=timeout,
                        headers=_HTTP_HEADERS)
    resp.raise_for_status()
    daily = resp.json().get("daily", {})

    times = daily.get("time", [])
    target_str = target.isoformat()
    if target_str not in times:
        return []
    idx = times.index(target_str)

    members: List[float] = []
    for key, values in daily.items():
        if not key.startswith("temperature_2m_max"):
            continue
        if idx < len(values) and values[idx] is not None:
            members.append(float(values[idx]))
    return members


# --------------------------------------------------------------- NWS anchor
def fetch_nws_high(
    city: City, target: date, timeout: float = 20.0
) -> Optional[float]:
    """Official NWS daytime-high forecast (F) for the settlement station, or None.

    Two hops: /points resolves the gridpoint forecast URL, which carries the
    per-period highs. We match the daytime period whose date is `target`.
    """
    pts = requests.get(
        NWS_POINTS_URL.format(lat=city.lat, lon=city.lon),
        timeout=timeout, headers=_HTTP_HEADERS,
    )
    pts.raise_for_status()
    forecast_url = pts.json().get("properties", {}).get("forecast")
    if not forecast_url:
        return None

    fc = requests.get(forecast_url, timeout=timeout, headers=_HTTP_HEADERS)
    fc.raise_for_status()
    periods = fc.json().get("properties", {}).get("periods", [])

    target_str = target.isoformat()
    for period in periods:
        if not period.get("isDaytime"):
            continue
        start = (period.get("startTime") or "")[:10]
        if start == target_str and period.get("temperature") is not None:
            unit = (period.get("temperatureUnit") or "F").upper()
            temp = float(period["temperature"])
            return temp if unit == "F" else _c_to_f(temp)
    return None


# --------------------------------------------------------------- probability
def empirical_bucket_probability(members: List[float], bucket: Bucket) -> float:
    """P(high in [lo, hi)) as the fraction of ensemble members landing there.

    Uses the same half-open convention as the Gaussian path (lo inclusive, hi
    exclusive) so probabilities across a bucket strip partition cleanly.
    """
    if not members:
        return 0.0
    hits = sum(1 for m in members if bucket.lo <= m < bucket.hi)
    return hits / len(members)


# ------------------------------------------------------------------- builder
def build_ensemble_forecast(
    city: City,
    target: date,
    nws_weight: float = 0.5,
    sigma_floor: float = 1.0,
    timeout: float = 25.0,
) -> Optional[EnsembleForecast]:
    """Fetch ensemble members and (optionally) anchor them to the NWS forecast.

    Returns None when no members are available (the city is then skipped).
    `nws_weight` in [0, 1] is how far to pull the member mean toward the NWS
    high; every member is shifted by the same amount so the spread is preserved.
    """
    try:
        members = fetch_ensemble_member_highs(city, target, timeout=timeout)
    except (requests.RequestException, ValueError):
        members = []

    if not members:
        return None

    raw_mu = statistics.fmean(members)

    nws_high: Optional[float] = None
    try:
        nws_high = fetch_nws_high(city, target, timeout=timeout)
    except (requests.RequestException, ValueError, KeyError):
        nws_high = None

    shift = 0.0
    if nws_high is not None and 0.0 < nws_weight <= 1.0:
        # Recenter toward the settlement source, keeping the ensemble's shape.
        target_mu = (1.0 - nws_weight) * raw_mu + nws_weight * nws_high
        shift = target_mu - raw_mu
        members = [m + shift for m in members]

    mu = statistics.fmean(members)
    sigma = (max(statistics.stdev(members), sigma_floor)
             if len(members) >= 2 else sigma_floor)

    return EnsembleForecast(
        mu=round(mu, 2),
        sigma=round(sigma, 2),
        n_members=len(members),
        members=members,
        nws_high_f=nws_high,
        anchor_shift=round(shift, 2),
    )
