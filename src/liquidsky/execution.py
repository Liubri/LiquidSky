"""Execution engines: paper (simulated) vs. live (real Kalshi orders).

Both return a `Fill` with the contract count and the price actually paid/received
in cents. Cost/return dollars are always derived from count * price, so
the paper ledger can never drift from the integer contract math.

Paper mode prices come from real, live orderbook data (passed in via the signal /
current price), so simulated fills track the real market — only the order send is
skipped. Live mode (`demo`/`live`) routes through KalshiClient.create_order.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional, Protocol

from .kalshi_client import KalshiClient
from .positions import dollars
from .strategy import Signal


@dataclass
class Fill:
    ticker: str
    side: str
    count: int
    price_cents: int
    amount: float          # dollars (cost for a buy, proceeds for a sell)
    raw: Optional[dict] = None


class Executor(Protocol):
    name: str

    def buy(self, signal: Signal) -> Fill: ...

    def sell(self, ticker: str, side: str, count: int, price_cents: int) -> Fill: ...


class PaperExecutor:
    """Simulated fills at the quoted price. No orders are ever sent."""

    name = "paper"

    def buy(self, signal: Signal) -> Fill:
        return Fill(
            ticker=signal.ticker,
            side=signal.side,
            count=signal.count,
            price_cents=signal.price_cents,
            amount=dollars(signal.count, signal.price_cents),
        )

    def sell(self, ticker: str, side: str, count: int, price_cents: int) -> Fill:
        return Fill(
            ticker=ticker,
            side=side,
            count=count,
            price_cents=price_cents,
            amount=dollars(count, price_cents),
        )


class LiveExecutor:
    """Routes real limit orders through the Kalshi API (demo or production)."""

    name = "live"

    def __init__(self, client: KalshiClient):
        self.client = client

    @staticmethod
    def _client_order_id() -> str:
        return f"liquidsky-{uuid.uuid4().hex[:16]}"

    def _price_kwargs(self, side: str, price_cents: int) -> dict:
        return {"yes_price": price_cents} if side == "yes" else {"no_price": price_cents}

    def buy(self, signal: Signal) -> Fill:
        resp = self.client.create_order(
            ticker=signal.ticker,
            action="buy",
            side=signal.side,
            count=signal.count,
            client_order_id=self._client_order_id(),
            order_type="limit",
            **self._price_kwargs(signal.side, signal.price_cents),
        )
        return Fill(
            ticker=signal.ticker,
            side=signal.side,
            count=signal.count,
            price_cents=signal.price_cents,
            amount=dollars(signal.count, signal.price_cents),
            raw=resp,
        )

    def sell(self, ticker: str, side: str, count: int, price_cents: int) -> Fill:
        resp = self.client.create_order(
            ticker=ticker,
            action="sell",
            side=side,
            count=count,
            client_order_id=self._client_order_id(),
            order_type="limit",
            **self._price_kwargs(side, price_cents),
        )
        return Fill(
            ticker=ticker,
            side=side,
            count=count,
            price_cents=price_cents,
            amount=dollars(count, price_cents),
            raw=resp,
        )


def build_executor(cfg, client: KalshiClient) -> Executor:
    """Choose the executor for the configured environment."""
    if cfg.env == "paper":
        return PaperExecutor()
    return LiveExecutor(client)
