"""Strategy math: bucket parsing, probability, Kelly sizing, signal filtering."""
import math

from scipy.stats import norm

from liquidsky.strategy import (
    Bucket,
    bucket_probability,
    evaluate_market,
    kelly_fraction,
    parse_bucket,
)

from .conftest import make_cfg


def test_parse_bucket_closed_range_pads_half_degree():
    b = parse_bucket({"floor_strike": 32, "cap_strike": 33})
    assert b == Bucket(lo=31.5, hi=33.5)


def test_parse_bucket_open_above_and_below():
    above = parse_bucket({"floor_strike": 50, "cap_strike": None})
    assert above.lo == 49.5 and above.hi == float("inf")

    below = parse_bucket({"floor_strike": None, "cap_strike": 31})
    assert below.lo == float("-inf") and below.hi == 31.5


def test_parse_bucket_uses_strike_type_direction():
    # 'greater' floor=79 means "80 or above" -> high >= 80 -> lo = 79.5.
    g = parse_bucket({"strike_type": "greater", "floor_strike": 79, "cap_strike": None})
    assert g.lo == 79.5 and g.hi == float("inf")
    # 'less' cap=72 means "71 or below" -> high <= 71 -> hi = 71.5.
    l = parse_bucket({"strike_type": "less", "floor_strike": None, "cap_strike": 72})
    assert l.lo == float("-inf") and l.hi == 71.5
    # 'between' floor=78 cap=79 -> [77.5, 79.5].
    b = parse_bucket({"strike_type": "between", "floor_strike": 78, "cap_strike": 79})
    assert b == Bucket(lo=77.5, hi=79.5)


def test_parse_bucket_text_fallback():
    assert parse_bucket({"title": "32 to 33"}) == Bucket(lo=31.5, hi=33.5)
    assert parse_bucket({"yes_sub_title": ">= 50"}).hi == float("inf")
    assert parse_bucket({"subtitle": "31 or below"}).lo == float("-inf")
    assert parse_bucket({"title": "no numbers here"}) is None


def test_bucket_probability_matches_normal_cdf():
    mu, sigma = 72.0, 2.5
    b = Bucket(lo=71.5, hi=73.5)
    expected = norm.cdf(73.5, mu, sigma) - norm.cdf(71.5, mu, sigma)
    assert math.isclose(bucket_probability(mu, sigma, b), expected, rel_tol=1e-9)


def test_bucket_probability_tails_sum_to_one():
    mu, sigma = 70.0, 3.0
    left = bucket_probability(mu, sigma, Bucket(float("-inf"), 70.0))
    right = bucket_probability(mu, sigma, Bucket(70.0, float("inf")))
    assert math.isclose(left + right, 1.0, rel_tol=1e-9)


def test_kelly_zero_when_no_edge():
    # Fair price equals probability -> no edge -> ~zero stake (float tolerance).
    assert abs(kelly_fraction(0.40, 40)) < 1e-9
    # Negative edge clamps to exactly zero.
    assert kelly_fraction(0.30, 40) == 0.0


def test_kelly_positive_when_edge_exists():
    f = kelly_fraction(0.60, 40)
    assert f > 0
    # Full Kelly for p=0.6 at 40c: f = p - (1-p)*price/(100-price) = .6-.4*40/60
    assert math.isclose(f, 0.6 - 0.4 * 40 / 60, rel_tol=1e-9)


def test_evaluate_market_emits_signal_for_mispriced_yes():
    cfg = make_cfg()
    # Forecast centered in the bucket -> high YES probability; ask is cheap.
    market = {
        "ticker": "KXHIGHNY-TEST",
        "floor_strike": 71,
        "cap_strike": 73,
        "yes_ask": 30,
        "no_ask": 72,
        "yes_bid": 28,
        "volume": 500,
    }
    sig = evaluate_market(market, mu=72.0, sigma=2.0, cfg=cfg, balance=1000.0)
    assert sig is not None
    assert sig.side == "yes"
    assert sig.price_cents == 31  # 30 ask + 1c slippage
    assert sig.edge >= cfg.min_edge
    assert sig.count >= 1
    # Stake invariant: dollars == count * price/100.
    assert math.isclose(sig.stake, sig.count * sig.price_cents / 100.0, abs_tol=1e-9)


def test_evaluate_market_skips_low_volume():
    cfg = make_cfg()
    market = {
        "ticker": "T", "floor_strike": 71, "cap_strike": 73,
        "yes_ask": 30, "no_ask": 72, "volume": 5,
    }
    assert evaluate_market(market, 72.0, 2.0, cfg, 1000.0) is None


def test_evaluate_market_skips_overpriced_entry():
    cfg = make_cfg()
    # Even with a real edge, an ask above max_entry_cents is rejected.
    market = {
        "ticker": "T", "floor_strike": 71, "cap_strike": 73,
        "yes_ask": 80, "no_ask": 22, "volume": 500,
    }
    sig = evaluate_market(market, 72.0, 1.0, cfg, 1000.0)
    # YES is too expensive; NO has tiny prob -> negative edge -> no trade.
    assert sig is None or sig.price_cents <= cfg.max_entry_cents
