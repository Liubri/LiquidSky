"""Strategy registry, config overrides, pluggable probability, and Brier score."""
import math

import pytest

from liquidsky.bot import Bot
from liquidsky.config import load_config
from liquidsky.strategies import (
    STRATEGIES,
    GaussianStrategy,
    apply_overrides,
    resolve_strategies,
)
from liquidsky.strategy import evaluate_market

from .conftest import make_cfg


def test_resolve_all_when_empty():
    assert len(resolve_strategies([])) == len(STRATEGIES)


def test_resolve_named_subset_preserves_order():
    strats = resolve_strategies(["ensemble", "gaussian"])
    assert [s.key for s in strats] == ["ensemble", "gaussian"]


def test_resolve_unknown_raises():
    with pytest.raises(KeyError):
        resolve_strategies(["does-not-exist"])


def test_apply_overrides_does_not_mutate_base_config():
    cfg = load_config(env="paper")
    eff = apply_overrides(cfg, GaussianStrategy(), {"min_edge": 0.08})
    assert eff.min_edge == 0.08
    assert cfg.min_edge != 0.08  # original untouched
    # Unknown keys are ignored rather than raising.
    assert apply_overrides(cfg, GaussianStrategy(), {"bogus": 1}).min_edge == cfg.min_edge


def test_evaluate_market_uses_supplied_prob_of_bucket():
    cfg = make_cfg()
    market = {
        "ticker": "T", "floor_strike": 71, "cap_strike": 73,
        "yes_ask": 30, "no_ask": 72, "volume": 500,
    }
    # mu/sigma would say ~0 here, but the injected prob (0.9) drives the trade.
    sig = evaluate_market(market, mu=0.0, sigma=50.0, cfg=cfg, balance=1000.0,
                          prob_of_bucket=lambda b: 0.9)
    assert sig is not None and sig.side == "yes"
    assert math.isclose(sig.prob, 0.9, abs_tol=1e-9)


def test_brier_score_only_counts_settled():
    closed = [
        {"close_reason": "settled_win", "entry_prob": 0.8},   # (0.8-1)^2 = 0.04
        {"close_reason": "settled_loss", "entry_prob": 0.3},  # (0.3-0)^2 = 0.09
        {"close_reason": "stop", "entry_prob": 0.5},          # excluded (no resolution)
    ]
    assert Bot._brier_score(closed) == round((0.04 + 0.09) / 2, 4)


def test_brier_score_none_without_settlements():
    assert Bot._brier_score([{"close_reason": "stop", "entry_prob": 0.5}]) is None
