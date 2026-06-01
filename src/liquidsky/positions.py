"""Position ledger — per-market JSON files treated as ground truth."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dollars(count: int, price_cents: int) -> float:
    """Cost/return in dollars for `count` contracts at `price_cents`."""
    return round(count * price_cents / 100.0, 2)


# --------------------------------------------------------------- file helpers
def _markets_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "markets"


def market_file(data_dir: Path, ticker: str) -> Path:
    return _markets_dir(data_dir) / f"{ticker}.json"


def load_position(data_dir: Path, ticker: str) -> Optional[Dict[str, Any]]:
    path = market_file(data_dir, ticker)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_position(data_dir: Path, pos: Dict[str, Any]) -> None:
    path = market_file(data_dir, pos["ticker"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pos, indent=2, sort_keys=True))


def load_all_positions(data_dir: Path) -> List[Dict[str, Any]]:
    mdir = _markets_dir(data_dir)
    if not mdir.exists():
        return []
    return [json.loads(p.read_text()) for p in sorted(mdir.glob("*.json"))]


def load_open_positions(data_dir: Path) -> List[Dict[str, Any]]:
    return [p for p in load_all_positions(data_dir) if p.get("status") == "open"]


# ------------------------------------------------------------- balance
def calculate_balance_from_trades(data_dir: Path, starting_balance: float) -> float:
    """Reconstruct cash balance from trade files — never from a running total."""
    total_cost = 0.0
    total_returned = 0.0
    for pos in load_all_positions(data_dir):
        total_cost += pos.get("cost", 0.0)
        if pos.get("status") == "closed":
            total_returned += pos.get("returned", 0.0) or 0.0
    return round(starting_balance - total_cost + total_returned, 2)


def open_positions_value(
    data_dir: Path, current_prices: Dict[str, int]
) -> float:
    """Mark-to-market value of open positions (dollars), for equity reporting."""
    value = 0.0
    for pos in load_open_positions(data_dir):
        price = current_prices.get(pos["ticker"])
        if price is None:
            price = pos["entry_price_cents"]
        value += dollars(pos["count"], price)
    return round(value, 2)


# -------------------------------------------------------------- lifecycle
def open_position(
    data_dir: Path,
    ticker: str,
    side: str,
    count: int,
    entry_price_cents: int,
    stop_loss_pct: float,
    city: str = "",
    forecast_mu: float = 0.0,
    forecast_sigma: float = 0.0,
    strategy: str = "",
    entry_prob: float = 0.0,
) -> Dict[str, Any]:
    """Create and persist a new open position. Returns the position dict."""
    initial_stop = max(1, round(entry_price_cents * (1.0 - stop_loss_pct)))
    pos = {
        "ticker": ticker,
        "city": city,
        "strategy": strategy,
        "side": side,
        "status": "open",
        "count": count,
        "entry_price_cents": entry_price_cents,
        "cost": dollars(count, entry_price_cents),
        "stop_cents": initial_stop,
        "high_water_cents": entry_price_cents,
        "forecast_mu": forecast_mu,
        "forecast_sigma": forecast_sigma,
        "entry_prob": entry_prob,
        "opened_at": _now_iso(),
        "exit_price_cents": None,
        "returned": None,
        "closed_at": None,
        "close_reason": None,
        "events": [
            {"t": _now_iso(), "type": "open", "price_cents": entry_price_cents,
             "count": count}
        ],
    }
    save_position(data_dir, pos)
    return pos


def update_trailing_stop(pos: Dict[str, Any], current_price_cents: int, cfg) -> int:
    """Ratchet the stop upward. Returns the (possibly raised) stop.

    The stop only ever increases. Once the position has gained
    `trail_activate_gain`, we trail at `trail_pct` of the high-water price.
    """
    high_water = max(pos.get("high_water_cents", current_price_cents), current_price_cents)
    pos["high_water_cents"] = high_water

    entry = pos["entry_price_cents"]
    activation = entry * (1.0 + cfg.trail_activate_gain)
    stop = pos["stop_cents"]

    if high_water >= activation:
        trailed = round(high_water * cfg.trail_pct)
        # Ratchet up only — never lower an existing stop.
        stop = max(stop, trailed)

    pos["stop_cents"] = stop
    return stop


def should_close(pos: Dict[str, Any], current_price_cents: int) -> Optional[str]:
    """Return a close reason if the stop has been hit, else None."""
    if current_price_cents <= pos["stop_cents"]:
        return "stop"
    return None


def record_close(
    data_dir: Path,
    pos: Dict[str, Any],
    exit_price_cents: int,
    reason: str,
) -> Dict[str, Any]:
    """Close a position by selling at `exit_price_cents`."""
    pos["status"] = "closed"
    pos["exit_price_cents"] = exit_price_cents
    pos["returned"] = dollars(pos["count"], exit_price_cents)
    pos["closed_at"] = _now_iso()
    pos["close_reason"] = reason
    pos.setdefault("events", []).append(
        {"t": _now_iso(), "type": "close", "price_cents": exit_price_cents,
         "reason": reason}
    )
    save_position(data_dir, pos)
    return pos


def record_settlement(
    data_dir: Path, pos: Dict[str, Any], won: bool
) -> Dict[str, Any]:
    """Settle a position at market resolution: $1 per contract if won, else $0."""
    settle_cents = 100 if won else 0
    pos["status"] = "closed"
    pos["exit_price_cents"] = settle_cents
    pos["returned"] = dollars(pos["count"], settle_cents)
    pos["closed_at"] = _now_iso()
    pos["close_reason"] = "settled_win" if won else "settled_loss"
    pos.setdefault("events", []).append(
        {"t": _now_iso(), "type": "settle", "won": won}
    )
    save_position(data_dir, pos)
    return pos
