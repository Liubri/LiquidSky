"""Execution: paper fills keep the cost = count * price invariant."""
import math

from liquidsky.execution import PaperExecutor
from liquidsky.strategy import Signal


def _signal(count, price):
    return Signal(
        ticker="T", side="yes", price_cents=price, prob=0.6, edge=0.2,
        ev_cents=20.0, count=count, stake=count * price / 100.0,
        forecast_mu=72.0, forecast_sigma=2.0,
    )


def test_paper_buy_cost_equals_count_times_price():
    ex = PaperExecutor()
    fill = ex.buy(_signal(count=14, price=35))
    assert fill.amount == round(14 * 35 / 100.0, 2)
    assert math.isclose(fill.amount, fill.count * fill.price_cents / 100.0)


def test_paper_sell_proceeds_invariant():
    ex = PaperExecutor()
    fill = ex.sell("T", "yes", count=14, price_cents=60)
    assert fill.amount == round(14 * 60 / 100.0, 2)


def test_no_drift_over_many_round_trips():
    """Buy then sell repeatedly; net cash matches exact arithmetic, no rounding creep."""
    ex = PaperExecutor()
    net = 0.0
    for i in range(1, 201):
        count = i % 17 + 1
        buy = ex.buy(_signal(count=count, price=35))
        sell = ex.sell("T", "yes", count=count, price_cents=37)
        net += sell.amount - buy.amount
        # Each leg is exact integer-cent arithmetic.
        assert buy.amount == round(count * 35 / 100.0, 2)
        assert sell.amount == round(count * 37 / 100.0, 2)
    # Reconstruct the expected net independently.
    expected = sum(
        round((i % 17 + 1) * 37 / 100.0, 2) - round((i % 17 + 1) * 35 / 100.0, 2)
        for i in range(1, 201)
    )
    assert math.isclose(net, expected, abs_tol=1e-9)
