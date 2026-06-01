"""Desk: runs every configured strategy as an independent paper portfolio.

Each strategy is its own `Bot` with its own ledger, balance, and equity curve,
all trading the same live markets. The Desk fans cycles out to every bot and
aggregates their status/report/equity for the dashboard — including a side-by-side
`compare()` view so you can see which strategy is actually winning.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

from .bot import Bot
from .config import Config
from .strategies import Strategy, resolve_strategies

log = logging.getLogger("liquidsky")


class Desk:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.strategies: List[Strategy] = resolve_strategies(cfg.strategies)
        self.bots: Dict[str, Bot] = {
            s.key: Bot(cfg, s) for s in self.strategies
        }

    # ------------------------------------------------------------- lookup
    @property
    def default_key(self) -> str:
        return self.strategies[0].key

    def _bot(self, key: Optional[str]) -> Bot:
        if key and key in self.bots:
            return self.bots[key]
        return self.bots[self.default_key]

    def list_strategies(self) -> List[dict]:
        return [
            {"key": s.key, "name": s.name, "blurb": s.blurb} for s in self.strategies
        ]

    # ------------------------------------------------------------- cycles
    def run_once(self) -> None:
        for bot in self.bots.values():
            bot.run_once()

    def run_forever(self) -> None:
        interval = self.cfg.scan_interval_minutes * 60
        keys = ", ".join(self.bots)
        log.info("Starting LiquidSky desk [env=%s] strategies=[%s] — every %d min",
                 self.cfg.env, keys, self.cfg.scan_interval_minutes)
        while True:
            try:
                self.run_once()
            except Exception:  # keep the loop alive across transient failures
                log.exception("Desk cycle failed")
            time.sleep(interval)

    def status_text(self) -> str:
        """Plain-text status across all strategies for the CLI."""
        lines = []
        for s in self.strategies:
            lines.append(self.bots[s.key].status())
            lines.append("")
        return "\n".join(lines).rstrip()

    def report_text(self) -> str:
        """Plain-text report with a comparison header for the CLI."""
        data = self.compare()
        width = max((len(r["name"]) for r in data["strategies"]), default=8)
        header = (f"{'strategy':<{width}}  {'equity':>10}  {'ret%':>7}  "
                  f"{'win%':>6}  {'closed':>6}  {'maxDD%':>6}  {'brier':>6}")
        lines = [f"Strategy comparison [env={data['env']}]", header, "-" * len(header)]
        for r in data["strategies"]:
            brier = "—" if r["brier_score"] is None else f"{r['brier_score']:.3f}"
            lines.append(
                f"{r['name']:<{width}}  ${r['equity']:>9,.2f}  {r['return_pct']:>6.1f}%  "
                f"{r['win_rate']:>5.1f}%  {r['trades_closed']:>6}  "
                f"{r['max_drawdown_pct']:>5.1f}%  {brier:>6}"
            )
        return "\n".join(lines)

    # --------------------------------------------------------- per-strategy
    def status_data(self, key: Optional[str] = None) -> dict:
        return self._bot(key).status_data()

    def report_data(self, key: Optional[str] = None) -> dict:
        return self._bot(key).report_data()

    def equity_series(self, key: Optional[str] = None, limit: int = 500) -> List[dict]:
        return self._bot(key).equity_series(limit=limit)

    # ------------------------------------------------------------- compare
    def compare(self) -> dict:
        """A side-by-side summary plus aligned equity curves for charting."""
        rows = []
        equity = {}
        for s in self.strategies:
            bot = self.bots[s.key]
            status = bot.status_data()
            report = bot.report_data()
            rows.append({
                "key": s.key,
                "name": s.name,
                "blurb": s.blurb,
                "equity": status["equity"],
                "cash": status["cash"],
                "open_value": status["open_value"],
                "open_count": status["open_count"],
                "starting_balance": status["starting_balance"],
                "return_pct": round(
                    (status["equity"] - status["starting_balance"])
                    / status["starting_balance"] * 100, 2
                ) if status["starting_balance"] else 0.0,
                "trades_opened": report["trades_opened"],
                "trades_closed": report["trades_closed"],
                "win_rate": report["win_rate"],
                "wins": report["wins"],
                "realized_pnl": report["realized_pnl"],
                "max_drawdown_pct": report["max_drawdown_pct"],
                "brier_score": report["brier_score"],
            })
            equity[s.key] = [
                {"t": p.get("t"), "equity": p.get("equity")}
                for p in bot.equity_series()
            ]
        return {
            "env": self.cfg.env,
            "starting_balance": self.cfg.starting_balance,
            "strategies": rows,
            "equity": equity,
        }
