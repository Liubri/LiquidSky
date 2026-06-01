"""Pluggable trading strategies, run side-by-side as independent portfolios.

Each strategy differs only in how it prices a day's temperature buckets — the
edge/Kelly/exit machinery in `strategy.py` and `positions.py` is shared, so a
head-to-head comparison isolates the *forecast*, not the plumbing.

A strategy produces a `DayForecast` per (city, trading day): a `prob_of_bucket`
callable the bot feeds to `evaluate_market`, plus `mu`/`sigma` for display and a
short label for the activity log.

  gaussian  — the original: 5 deterministic models + METAR, Normal(mu, sigma).
  ensemble  — ~82 ensemble members (GFS+ECMWF) + NWS anchor, empirical CDF.

`config.strategies` (a list of keys) selects which run; an empty list means all.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date
from typing import Callable, Dict, List, Optional, Protocol

from .cities import City
from .forecast import build_forecast
from .forecast_ensemble import build_ensemble_forecast, empirical_bucket_probability
from .strategy import Bucket, bucket_probability


@dataclass
class DayForecast:
    """Everything a strategy needs to price one trading day's buckets."""
    prob_of_bucket: Callable[[Bucket], float]
    mu: float
    sigma: float
    n: int               # models / members behind the forecast
    label: str           # one-line summary for the activity log


class Strategy(Protocol):
    key: str                       # short id; also the ledger subdirectory name
    name: str                      # human-readable name for the UI
    blurb: str                     # one-line description for the UI
    config_overrides: Dict         # per-strategy config tweaks (e.g. min_edge)

    def forecast(self, city: City, target: date, cfg) -> Optional[DayForecast]: ...


# --------------------------------------------------------------- gaussian
class GaussianStrategy:
    key = "gaussian"
    name = "Gaussian Value"
    blurb = "5 deterministic models + METAR · Normal(mu, sigma) · cheap-tail value"
    config_overrides: Dict = {}

    def forecast(self, city: City, target: date, cfg) -> Optional[DayForecast]:
        fc = build_forecast(city, target, default_sigma=cfg.forecast_sigma_default)
        if fc is None:
            return None
        mu, sigma = fc.mu, fc.sigma
        return DayForecast(
            prob_of_bucket=lambda b: bucket_probability(mu, sigma, b),
            mu=mu,
            sigma=sigma,
            n=fc.n_models,
            label=f"mu={mu:.1f}F sigma={sigma:.1f}F (n={fc.n_models} models)",
        )


# --------------------------------------------------------------- ensemble
class EnsembleStrategy:
    key = "ensemble"
    name = "Ensemble Empirical"
    blurb = "GFS+ECMWF ensemble (~82 members) + NWS anchor · empirical distribution"
    # The ensemble gives a sharper distribution; the literature runs it a touch
    # tighter on edge. Left empty by default so the comparison isolates the
    # forecast — set in config.json strategy_overrides to diverge.
    config_overrides: Dict = {}

    def forecast(self, city: City, target: date, cfg) -> Optional[DayForecast]:
        fc = build_ensemble_forecast(city, target)
        if fc is None:
            return None
        members = fc.members
        anchor = (f" anchored {fc.anchor_shift:+.1f}F->NWS {fc.nws_high_f:.0f}F"
                  if fc.nws_high_f is not None else "")
        return DayForecast(
            prob_of_bucket=lambda b: empirical_bucket_probability(members, b),
            mu=fc.mu,
            sigma=fc.sigma,
            n=fc.n_members,
            label=f"mu={fc.mu:.1f}F sigma={fc.sigma:.1f}F "
                  f"(n={fc.n_members} members){anchor}",
        )


# --------------------------------------------------------------- registry
STRATEGIES: Dict[str, Strategy] = {
    GaussianStrategy.key: GaussianStrategy(),
    EnsembleStrategy.key: EnsembleStrategy(),
}


def resolve_strategies(selected: Optional[List[str]]) -> List[Strategy]:
    """Return the Strategy instances for the configured keys (all if none)."""
    if not selected:
        return list(STRATEGIES.values())
    out: List[Strategy] = []
    for key in selected:
        strat = STRATEGIES.get(key.lower())
        if strat is None:
            raise KeyError(
                f"Unknown strategy {key!r}. Known: {sorted(STRATEGIES)}"
            )
        out.append(strat)
    return out


def apply_overrides(cfg, strategy: Strategy, extra: Optional[Dict] = None):
    """Build the effective config for a strategy from its overrides + config.json.

    Only keys that are real Config fields are applied; unknown keys are ignored.
    """
    merged: Dict = {}
    merged.update(getattr(strategy, "config_overrides", {}) or {})
    if extra:
        merged.update(extra)
    if not merged or not is_dataclass(cfg):
        return cfg
    valid = {f.name for f in fields(cfg)}
    safe = {k: v for k, v in merged.items() if k in valid}
    return replace(cfg, **safe) if safe else cfg
