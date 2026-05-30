"""Shared test fixtures and a lightweight config stub."""
from types import SimpleNamespace

import pytest


def make_cfg(**overrides):
    """A minimal config-like object with the fields strategy/positions read."""
    base = dict(
        starting_balance=1000.0,
        max_bet=50.0,
        min_edge=0.05,
        max_entry_cents=45,
        min_volume=50,
        kelly_fraction=0.25,
        forecast_sigma_default=2.5,
        stop_loss_pct=0.20,
        trail_activate_gain=0.20,
        trail_pct=0.80,
        slippage_cents=1,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def cfg():
    return make_cfg()


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "paper"
