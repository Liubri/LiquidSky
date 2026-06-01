"""Position ledger: balance reconstruction and ratcheting stop."""
import math

from liquidsky import positions as P

from .conftest import make_cfg


def test_balance_reconstructed_from_trade_files(data_dir):
    start = 1000.0
    # Two open positions deduct their cost; cash = start - costs.
    P.open_position(data_dir, "A", "yes", 100, 5, 0.20)   # cost $5.00
    P.open_position(data_dir, "B", "yes", 200, 10, 0.20)  # cost $20.00
    assert math.isclose(
        P.calculate_balance_from_trades(data_dir, start), start - 25.0
    )

    # Closing A for 8c returns $8.00 (100 * 0.08).
    a = P.load_position(data_dir, "A")
    P.record_close(data_dir, a, exit_price_cents=8, reason="stop")
    assert math.isclose(
        P.calculate_balance_from_trades(data_dir, start), start - 25.0 + 8.0
    )


def test_open_position_records_strategy_and_entry_prob(data_dir):
    pos = P.open_position(data_dir, "T", "yes", count=10, entry_price_cents=30,
                          stop_loss_pct=0.2, strategy="ensemble", entry_prob=0.72)
    assert pos["strategy"] == "ensemble"
    assert pos["entry_prob"] == 0.72
    # Persisted to disk, not just returned.
    reloaded = P.load_position(data_dir, "T")
    assert reloaded["strategy"] == "ensemble" and reloaded["entry_prob"] == 0.72


def test_balance_is_idempotent_no_drift(data_dir):
    """Recomputing many times never drifts."""
    start = 500.0
    for i in range(50):
        P.open_position(data_dir, f"M{i}", "yes", count=10, entry_price_cents=20,
                        stop_loss_pct=0.2)
    # Close half at a profit.
    for i in range(0, 50, 2):
        pos = P.load_position(data_dir, f"M{i}")
        P.record_close(data_dir, pos, exit_price_cents=30, reason="settled_win")

    first = P.calculate_balance_from_trades(data_dir, start)
    for _ in range(5):
        assert P.calculate_balance_from_trades(data_dir, start) == first

    total_cost = 50 * 10 * 0.20            # every position: 10 * 20c = $2.00
    total_returned = 25 * 10 * 0.30        # 25 closed at 30c = $3.00 each
    assert math.isclose(first, start - total_cost + total_returned)


def test_cost_invariant_count_times_price(data_dir):
    """Cost is always exactly count * price / 100."""
    pos = P.open_position(data_dir, "X", "yes", count=14, entry_price_cents=35,
                          stop_loss_pct=0.2)
    assert pos["cost"] == round(14 * 35 / 100.0, 2)
    P.record_close(data_dir, pos, exit_price_cents=60, reason="stop")
    assert pos["returned"] == round(14 * 60 / 100.0, 2)


def test_trailing_stop_ratchets_up_and_never_down():
    """Stop progresses with gains and never decreases."""
    cfg = make_cfg(trail_activate_gain=0.20, trail_pct=0.80, stop_loss_pct=0.20)
    pos = {"entry_price_cents": 10, "stop_cents": 8, "high_water_cents": 10}

    # Below activation (10 * 1.2 = 12): stop unchanged.
    assert P.update_trailing_stop(pos, 11, cfg) == 8

    # At activation price 12: trail at 80% of high-water -> round(12*.8)=10.
    assert P.update_trailing_stop(pos, 12, cfg) == 10

    # Big gain to 50c: stop ratchets to round(50*.8)=40.
    assert P.update_trailing_stop(pos, 50, cfg) == 40

    # Price dips to 30c: high-water stays 50, stop stays 40 (never lowers).
    assert P.update_trailing_stop(pos, 30, cfg) == 40
    assert pos["high_water_cents"] == 50


def test_should_close_triggers_on_stop():
    pos = {"stop_cents": 40}
    assert P.should_close(pos, 40) == "stop"
    assert P.should_close(pos, 39) == "stop"
    assert P.should_close(pos, 41) is None


def test_settlement_pays_full_or_zero(data_dir):
    win = P.open_position(data_dir, "W", "yes", 10, 30, 0.2)
    P.record_settlement(data_dir, win, won=True)
    assert win["returned"] == round(10 * 1.0, 2)  # 10 contracts * $1

    loss = P.open_position(data_dir, "L", "yes", 10, 30, 0.2)
    P.record_settlement(data_dir, loss, won=False)
    assert loss["returned"] == 0.0
