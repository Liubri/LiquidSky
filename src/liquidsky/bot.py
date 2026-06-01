"""Bot orchestration: scan for edges, place trades, monitor and exit positions."""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from . import marketdata
from . import positions as P
from .cities import City, resolve_cities
from .config import Config
from .execution import Executor, build_executor
from .kalshi_client import KalshiClient
from .strategies import GaussianStrategy, Strategy, apply_overrides
from .strategy import Signal, evaluate_market

log = logging.getLogger("liquidsky")


class Bot:
    """One strategy's paper/live portfolio: scans, trades, and reports on its
    own ledger under ``data/<env>/<strategy_key>/``.

    Multiple Bots (one per strategy) run side-by-side over the same live markets
    via the `Desk` orchestrator; each keeps a fully independent balance and
    equity curve so the strategies can be compared head-to-head.
    """

    def __init__(self, cfg: Config, strategy: Optional[Strategy] = None):
        self.cfg = cfg
        self.strategy: Strategy = strategy or GaussianStrategy()
        # Effective config = base config + this strategy's overrides.
        self.eff_cfg = apply_overrides(
            cfg, self.strategy, (cfg.strategy_overrides or {}).get(self.strategy.key)
        )
        self.client = KalshiClient(
            base_url=cfg.base_url,
            api_key_id=cfg.api_key_id,
            private_key_path=cfg.private_key_path,
        )
        self.executor: Executor = build_executor(cfg, self.client)
        self.data_dir = cfg.data_dir / self.strategy.key
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- helpers
    def balance(self) -> float:
        return P.calculate_balance_from_trades(self.data_dir, self.cfg.starting_balance)

    @staticmethod
    def _local_date(iso_ts: Optional[str], tz: str) -> Optional[date]:
        if not iso_ts:
            return None
        try:
            dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt.astimezone(ZoneInfo(tz)).date()

    def _target_date(self, market: dict, tz: str) -> date:
        # The measurement date is encoded in the ticker; close_time is the day
        # after, so prefer the ticker date and only fall back if it's missing.
        for key in ("event_ticker", "ticker"):
            d = marketdata.parse_event_date(market.get(key))
            if d is not None:
                return d
        d = self._local_date(market.get("close_time"), tz)
        return d if d is not None else datetime.now(ZoneInfo(tz)).date()

    def _best_exit_price(self, market: dict, side: str) -> Optional[int]:
        """Price (cents) we could sell our `side` at = best bid on that side."""
        return marketdata.price_cents(market, "yes_bid" if side == "yes" else "no_bid")

    def _log_equity(self, current_prices: Dict[str, int]) -> Dict[str, float]:
        cash = self.balance()
        open_value = P.open_positions_value(self.data_dir, current_prices)
        snapshot = {
            "t": datetime.now().astimezone().isoformat(),
            "cash": cash,
            "open_value": open_value,
            "equity": round(cash + open_value, 2),
        }
        with (self.data_dir / "equity.jsonl").open("a") as f:
            f.write(json.dumps(snapshot) + "\n")
        return snapshot

    # ----------------------------------------------------------- scanning
    def scan_and_update(self) -> List[Signal]:
        """Find edges across all configured cities and open new positions."""
        if self.cfg.is_live_trading:
            self.cfg.validate_for_trading()

        cities = resolve_cities(self.cfg.cities)
        placed: List[Signal] = []
        current_prices: Dict[str, int] = {}

        for city in cities:
            try:
                markets = self.client.get_markets(series_ticker=city.series_ticker)
            except Exception as exc:  # network/API hiccup: skip this city
                log.warning("Failed to fetch markets for %s: %s", city.series_ticker, exc)
                continue
            if not markets:
                continue

            # Group buckets by event/date so we forecast once per trading day.
            by_date: Dict[date, List[dict]] = defaultdict(list)
            for m in markets:
                by_date[self._target_date(m, city.tz)].append(m)

            now_local = datetime.now(ZoneInfo(city.tz))
            for target, bucket_markets in by_date.items():
                # Past day awaiting settlement — nothing new to open.
                if target < now_local.date():
                    continue
                # Current day after the cutoff: the high is likely already set,
                # so defer to the market rather than a stale forecast.
                if (target == now_local.date()
                        and now_local.hour >= self.cfg.skip_today_after_local_hour):
                    log.info("Skipping %s %s: past %02d:00 local, high likely set",
                             city.name, target, self.cfg.skip_today_after_local_hour)
                    continue
                forecast = self.strategy.forecast(city, target, self.eff_cfg)
                if forecast is None:
                    log.info("[%s] No forecast for %s %s; skipping",
                             self.strategy.key, city.name, target)
                    continue
                log.info(
                    "[%s] %s %s forecast: %s",
                    self.strategy.key, city.name, target, forecast.label,
                )
                for market in bucket_markets:
                    current_prices[market["ticker"]] = (
                        marketdata.price_cents(market, "yes_bid") or 0
                    )
                    signal = self._consider(market, forecast, city)
                    if signal is not None:
                        placed.append(signal)

        self._log_equity(current_prices)
        return placed

    def _consider(self, market: dict, forecast, city: City) -> Optional[Signal]:
        ticker = market["ticker"]
        cfg = self.eff_cfg
        # Never re-enter a market we already have a record for (open or closed).
        if P.load_position(self.data_dir, ticker) is not None:
            return None
        if len(P.load_open_positions(self.data_dir)) >= cfg.max_open_positions:
            return None

        balance = self.balance()
        signal = evaluate_market(
            market, forecast.mu, forecast.sigma, cfg, balance,
            prob_of_bucket=forecast.prob_of_bucket,
        )
        if signal is None:
            return None
        if signal.stake > balance:
            log.info("[%s] Insufficient balance for %s (need %.2f, have %.2f)",
                     self.strategy.key, ticker, signal.stake, balance)
            return None

        fill = self.executor.buy(signal)
        P.open_position(
            self.data_dir,
            ticker=fill.ticker,
            side=fill.side,
            count=fill.count,
            entry_price_cents=fill.price_cents,
            stop_loss_pct=cfg.stop_loss_pct,
            city=city.series_ticker,
            forecast_mu=forecast.mu,
            forecast_sigma=forecast.sigma,
            strategy=self.strategy.key,
            entry_prob=signal.prob,
        )
        log.info(
            "[%s/%s] BUY %s %dx %s @ %dc  edge=%.1f%% EV=%.1fc cost=$%.2f",
            self.strategy.key, self.executor.name, fill.side.upper(), fill.count,
            ticker, fill.price_cents, signal.edge * 100, signal.ev_cents, fill.amount,
        )
        return signal

    # --------------------------------------------------------- monitoring
    def monitor_positions(self) -> List[dict]:
        """Refresh open positions: ratchet stops, exit on stop, settle resolved."""
        closed: List[dict] = []
        current_prices: Dict[str, int] = {}

        for pos in P.load_open_positions(self.data_dir):
            ticker = pos["ticker"]
            try:
                market = self.client.get_market(ticker)
            except Exception as exc:
                log.warning("Failed to refresh %s: %s", ticker, exc)
                continue

            # A non-empty result ("yes"/"no") means the market has resolved.
            result = (market.get("result") or "").lower()
            status = (market.get("status") or "").lower()
            if result in ("yes", "no") or status in ("settled", "finalized"):
                won = result == pos["side"]
                P.record_settlement(self.data_dir, pos, won)
                closed.append(pos)
                log.info("SETTLE %s won=%s returned=$%.2f", ticker, won, pos["returned"])
                continue

            exit_price = self._best_exit_price(market, pos["side"])
            if exit_price is None:
                continue
            current_prices[ticker] = exit_price
            pos["last_price_cents"] = exit_price  # cached for the dashboard

            P.update_trailing_stop(pos, exit_price, self.eff_cfg)
            reason = P.should_close(pos, exit_price)
            if reason:
                fill = self.executor.sell(ticker, pos["side"], pos["count"], exit_price)
                P.record_close(self.data_dir, pos, fill.price_cents, reason)
                closed.append(pos)
                log.info("[%s] SELL %s %dx @ %dc (%s) -> $%.2f",
                         self.executor.name, ticker, fill.count, fill.price_cents,
                         reason, fill.amount)
            else:
                P.save_position(self.data_dir, pos)  # persist ratcheted stop

        self._log_equity(current_prices)
        return closed

    # ------------------------------------------------------------- loops
    def run_once(self) -> None:
        self.monitor_positions()
        self.scan_and_update()

    def run_forever(self) -> None:
        interval = self.cfg.scan_interval_minutes * 60
        log.info("Starting LiquidSky [env=%s] — scanning every %d min",
                 self.cfg.env, self.cfg.scan_interval_minutes)
        while True:
            try:
                self.run_once()
            except Exception:  # keep the loop alive across transient failures
                log.exception("Cycle failed")
            time.sleep(interval)

    # ----------------------------------------------------------- reporting
    def status(self) -> str:
        balance = self.balance()
        open_pos = P.load_open_positions(self.data_dir)
        lines = [
            f"Environment : {self.cfg.env}",
            f"Cash balance: ${balance:,.2f}  (start ${self.cfg.starting_balance:,.2f})",
            f"Open positions: {len(open_pos)}",
        ]
        for p in open_pos:
            lines.append(
                f"  {p['ticker']:<24} {p['side'].upper():<3} {p['count']:>4}x "
                f"entry {p['entry_price_cents']}c  stop {p['stop_cents']}c  "
                f"cost ${p['cost']:.2f}"
            )
        return "\n".join(lines)

    def report(self) -> str:
        all_pos = P.load_all_positions(self.data_dir)
        closed = [p for p in all_pos if p.get("status") == "closed"]
        wins = [p for p in closed if (p.get("returned") or 0) > p.get("cost", 0)]
        realized = sum((p.get("returned") or 0) - p.get("cost", 0) for p in closed)

        peak, max_dd = self._equity_stats()
        lines = [
            f"Environment      : {self.cfg.env}",
            f"Trades opened    : {len(all_pos)}",
            f"Trades closed    : {len(closed)}",
            f"Win rate         : "
            f"{(len(wins) / len(closed) * 100) if closed else 0:.1f}%  "
            f"({len(wins)}/{len(closed)})",
            f"Realized P&L     : ${realized:,.2f}",
            f"Cash balance     : ${self.balance():,.2f}",
            f"Peak equity      : ${peak:,.2f}",
            f"Max drawdown     : {max_dd * 100:.1f}%",
        ]
        return "\n".join(lines)

    # ------------------------------------------------- structured (for the UI)
    def _position_view(self, p: dict) -> dict:
        """A position enriched with mark-to-market fields for the dashboard."""
        last = p.get("last_price_cents")
        entry = p["entry_price_cents"]
        count = p["count"]
        cost = p.get("cost", 0.0)
        if p.get("status") == "closed":
            value = p.get("returned") or 0.0
            pnl = value - cost
        elif last is not None:
            value = round(count * last / 100.0, 2)
            pnl = round(value - cost, 2)
        else:
            value = cost
            pnl = 0.0
        return {
            **p,
            "value": value,
            "unrealized_pnl": pnl,
            "pnl_pct": round((pnl / cost * 100.0), 1) if cost else 0.0,
        }

    def status_data(self) -> dict:
        cash = self.balance()
        open_views = [self._position_view(p) for p in P.load_open_positions(self.data_dir)]
        open_value = round(sum(v["value"] for v in open_views), 2)
        return {
            "env": self.cfg.env,
            "strategy": self.strategy.key,
            "strategy_name": self.strategy.name,
            "is_live_trading": self.cfg.is_live_trading,
            "starting_balance": self.cfg.starting_balance,
            "cash": cash,
            "open_value": open_value,
            "equity": round(cash + open_value, 2),
            "open_count": len(open_views),
            "positions": open_views,
        }

    @staticmethod
    def _brier_score(closed: List[dict]) -> Optional[float]:
        """Mean Brier score over settled positions (lower = better calibrated).

        Only positions that reached resolution carry a meaningful outcome; stop
        exits are excluded since they never settled. Returns None if there is
        nothing settled yet.
        """
        settled = [p for p in closed
                   if (p.get("close_reason") or "").startswith("settled")
                   and "entry_prob" in p]
        if not settled:
            return None
        total = 0.0
        for p in settled:
            won = 1.0 if p.get("close_reason") == "settled_win" else 0.0
            prob = float(p.get("entry_prob") or 0.0)
            total += (prob - won) ** 2
        return round(total / len(settled), 4)

    def report_data(self) -> dict:
        all_pos = P.load_all_positions(self.data_dir)
        closed = [p for p in all_pos if p.get("status") == "closed"]
        wins = [p for p in closed if (p.get("returned") or 0) > p.get("cost", 0)]
        realized = round(
            sum((p.get("returned") or 0) - p.get("cost", 0) for p in closed), 2
        )
        peak, max_dd = self._equity_stats()
        return {
            "env": self.cfg.env,
            "strategy": self.strategy.key,
            "strategy_name": self.strategy.name,
            "trades_opened": len(all_pos),
            "trades_closed": len(closed),
            "wins": len(wins),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
            "realized_pnl": realized,
            "cash": self.balance(),
            "peak_equity": peak,
            "max_drawdown_pct": round(max_dd * 100, 1),
            "brier_score": self._brier_score(closed),
            "closed_positions": [self._position_view(p) for p in closed],
        }

    def equity_series(self, limit: int = 500) -> List[dict]:
        path = self.data_dir / "equity.jsonl"
        if not path.exists():
            return []
        points = [
            json.loads(line)
            for line in path.read_text().splitlines()
            if line.strip()
        ]
        return points[-limit:]

    def _equity_stats(self) -> Tuple[float, float]:
        """Peak equity and max drawdown computed from the equity curve."""
        path = self.data_dir / "equity.jsonl"
        if not path.exists():
            return self.cfg.starting_balance, 0.0
        peak = self.cfg.starting_balance
        max_dd = 0.0
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            equity = json.loads(line).get("equity", 0.0)
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak)
        return round(peak, 2), max_dd
