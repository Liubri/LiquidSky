"""Pricing strategy: turn a Gaussian forecast into trade signals.

All functions here are pure and side-effect free so they can be unit tested
without any network access. The flow per market:

  bucket -> probability YES settles -> edge vs. market ask -> fractional Kelly
  -> contract count.

Kalshi mechanics: prices are integer cents in [1, 99]; one contract costs its
price in cents and pays 100 cents if it settles in your favour. We evaluate both
the YES and NO side of each market and take whichever has the larger edge.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from scipy.stats import norm

from . import marketdata

NEG_INF = float("-inf")
POS_INF = float("inf")

# A bucket labelled "32-33" settles on an *integer* reported high of 32 or 33,
# i.e. a continuous high in [31.5, 33.5). Pad strikes by half a degree so the
# Gaussian probability matches the rounding rule.
BOUNDARY_PAD = 0.5


@dataclass
class Bucket:
    lo: float  # inclusive lower edge of continuous high (may be -inf)
    hi: float  # exclusive upper edge of continuous high (may be +inf)


@dataclass
class Signal:
    ticker: str
    side: str            # "yes" or "no"
    price_cents: int     # ask we expect to pay (incl. slippage), in cents
    prob: float          # modeled probability this side settles in-the-money
    edge: float          # prob - price/100
    ev_cents: float      # prob*100 - price
    count: int           # contracts to buy
    stake: float         # dollars deployed (count * price/100)
    forecast_mu: float
    forecast_sigma: float


# --------------------------------------------------------------- bucket parsing
def parse_bucket(market: Dict[str, Any], pad: float = BOUNDARY_PAD) -> Optional[Bucket]:
    """Extract the continuous YES-settling temperature range from a market.

    Kalshi reports an *integer* daily high, so a degree threshold maps to a
    half-degree continuous boundary. The direction of the pad depends on
    `strike_type`:

      between           floor=78 cap=79 ("78-79")     -> [77.5, 79.5]
      greater           floor=79 ("80 or above")      -> [79.5, +inf]
      greater_or_equal  floor=80                       -> [79.5, +inf]
      less              cap=72 ("71 or below")         -> [-inf, 71.5]
      less_or_equal     cap=71                         -> [-inf, 71.5]

    Falls back to floor/cap presence, then to parsing the subtitle text.
    """
    floor = market.get("floor_strike")
    cap = market.get("cap_strike")
    strike_type = (market.get("strike_type") or "").lower()

    if strike_type == "between" and floor is not None and cap is not None:
        return Bucket(lo=float(floor) - pad, hi=float(cap) + pad)
    if strike_type == "greater" and floor is not None:
        return Bucket(lo=float(floor) + pad, hi=POS_INF)
    if strike_type == "greater_or_equal" and floor is not None:
        return Bucket(lo=float(floor) - pad, hi=POS_INF)
    if strike_type == "less" and cap is not None:
        return Bucket(lo=NEG_INF, hi=float(cap) - pad)
    if strike_type == "less_or_equal" and cap is not None:
        return Bucket(lo=NEG_INF, hi=float(cap) + pad)

    # No usable strike_type: infer from which strikes are present.
    if floor is not None and cap is not None:
        return Bucket(lo=float(floor) - pad, hi=float(cap) + pad)
    if floor is not None:
        return Bucket(lo=float(floor) - pad, hi=POS_INF)
    if cap is not None:
        return Bucket(lo=NEG_INF, hi=float(cap) + pad)

    return _parse_bucket_from_text(market, pad)


def _parse_bucket_from_text(market: Dict[str, Any], pad: float) -> Optional[Bucket]:
    text = " ".join(
        str(market.get(k, ""))
        for k in ("yes_sub_title", "subtitle", "title")
    ).strip()
    if not text:
        return None

    # Open-above: ">= 50", "above 49", "50 or above", "50 or higher".
    m = re.search(r"(?:>=?|above)\s*(\d+)", text, re.I) or \
        re.search(r"(\d+)\s*or\s*(?:above|higher|more)", text, re.I)
    if m:
        return Bucket(lo=float(m.group(1)) - pad, hi=POS_INF)
    # Open-below: "<= 31", "below 32", "31 or below", "31 or lower".
    m = re.search(r"(?:<=?|below)\s*(\d+)", text, re.I) or \
        re.search(r"(\d+)\s*or\s*(?:below|lower|less)", text, re.I)
    if m:
        return Bucket(lo=NEG_INF, hi=float(m.group(1)) + pad)
    # "32-33", "32 to 33"
    m = re.search(r"(\d+)\s*(?:-|to|–)\s*(\d+)", text, re.I)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        return Bucket(lo=a - pad, hi=b + pad)
    return None


# ----------------------------------------------------------------- probability
def bucket_probability(mu: float, sigma: float, bucket: Bucket) -> float:
    """P(high in [lo, hi)) under Normal(mu, sigma)."""
    if sigma <= 0:
        return 1.0 if bucket.lo <= mu < bucket.hi else 0.0
    lo_cdf = 0.0 if bucket.lo == NEG_INF else norm.cdf(bucket.lo, mu, sigma)
    hi_cdf = 1.0 if bucket.hi == POS_INF else norm.cdf(bucket.hi, mu, sigma)
    return max(0.0, min(1.0, hi_cdf - lo_cdf))


# ----------------------------------------------------------------------- kelly
def kelly_fraction(prob: float, price_cents: int) -> float:
    """Full-Kelly fraction of bankroll for a contract at `price_cents`.

    Buying at price p (cents) risks p to win (100 - p). Net odds b = (100-p)/p.
    f* = (prob*b - (1-prob)) / b. Clamped to >= 0 (never bet a negative edge).
    """
    if price_cents <= 0 or price_cents >= 100:
        return 0.0
    b = (100 - price_cents) / price_cents
    f = (prob * b - (1 - prob)) / b
    return max(0.0, f)


# --------------------------------------------------------------- side ask price
def _ask_cents(market: Dict[str, Any], side: str) -> Optional[int]:
    """Best ask (cents) to BUY `side`, read from Kalshi's *_dollars fields."""
    ask = marketdata.price_cents(market, "yes_ask" if side == "yes" else "no_ask")
    return ask if ask is not None and ask < 100 else None


# ------------------------------------------------------------------- evaluation
def _evaluate_side(
    market: Dict[str, Any],
    p_yes: float,
    mu: float,
    sigma: float,
    side: str,
    cfg,
    balance: float,
) -> Optional[Signal]:
    raw_ask = _ask_cents(market, side)
    if raw_ask is None:
        return None

    # Model probability that THIS side settles in-the-money.
    prob = p_yes if side == "yes" else 1.0 - p_yes

    # Pay a little above the quoted ask to be conservative about slippage.
    price = min(99, raw_ask + cfg.slippage_cents)

    edge = prob - price / 100.0
    ev_cents = prob * 100.0 - price

    if price > cfg.max_entry_cents:
        return None
    if edge < cfg.min_edge:
        return None

    if marketdata.volume(market) < cfg.min_volume:
        return None

    f = kelly_fraction(prob, price) * cfg.kelly_fraction
    stake = min(balance * f, cfg.max_bet)
    cost_per = price / 100.0
    count = int(math.floor(stake / cost_per)) if cost_per > 0 else 0
    if count < 1:
        return None

    return Signal(
        ticker=market["ticker"],
        side=side,
        price_cents=price,
        prob=round(prob, 4),
        edge=round(edge, 4),
        ev_cents=round(ev_cents, 2),
        count=count,
        stake=round(count * cost_per, 2),
        forecast_mu=mu,
        forecast_sigma=sigma,
    )


def evaluate_market(
    market: Dict[str, Any],
    mu: float,
    sigma: float,
    cfg,
    balance: float,
    prob_of_bucket: Optional[Callable[[Bucket], float]] = None,
) -> Optional[Signal]:
    """Best positive-edge signal for a market, or None if nothing qualifies.

    `prob_of_bucket` lets a strategy supply its own probability model. When it
    is None we fall back to the Gaussian `Normal(mu, sigma)` CDF, so the legacy
    `(mu, sigma)` call path is unchanged. `mu`/`sigma` are still recorded on the
    Signal for display regardless of which probability model is used.
    """
    bucket = parse_bucket(market)
    if bucket is None:
        return None

    if prob_of_bucket is None:
        p_yes = bucket_probability(mu, sigma, bucket)
    else:
        p_yes = prob_of_bucket(bucket)

    candidates = [
        _evaluate_side(market, p_yes, mu, sigma, "yes", cfg, balance),
        _evaluate_side(market, p_yes, mu, sigma, "no", cfg, balance),
    ]
    qualified = [s for s in candidates if s is not None]
    if not qualified:
        return None
    return max(qualified, key=lambda s: s.edge)
