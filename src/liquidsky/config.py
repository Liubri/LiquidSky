"""Configuration loading: merges config.json with environment variables.

The trading environment (`paper` | `demo` | `live`) controls both which Kalshi
base URL is used and whether real orders are placed. `paper` never sends an
order; `demo` and `live` do (`live` additionally requires `confirm_live`).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import List, Optional

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv is optional at runtime
    def load_dotenv(*_args, **_kwargs):  # type: ignore
        return False


# Repo root = two levels up from this file (src/liquidsky/config.py).
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.json"

VALID_ENVS = ("paper", "demo", "live")

# Kalshi REST base URLs (verified against docs.kalshi.com).
KALSHI_BASE_URLS = {
    # paper trades against real production market data but never sends orders.
    "paper": "https://api.elections.kalshi.com/trade-api/v2",
    "demo": "https://demo-api.kalshi.co/trade-api/v2",
    "live": "https://api.elections.kalshi.com/trade-api/v2",
}


@dataclass
class Config:
    # --- strategy / risk tunables (from config.json) ---
    starting_balance: float = 1000.0
    max_bet: float = 50.0
    min_edge: float = 0.05
    max_entry_cents: int = 45
    min_volume: int = 50
    kelly_fraction: float = 0.25
    forecast_sigma_default: float = 2.5
    stop_loss_pct: float = 0.20
    trail_activate_gain: float = 0.20
    trail_pct: float = 0.80
    scan_interval_minutes: int = 60
    # After this local hour, stop opening positions on the *current* day's market:
    # the daily high is usually set by mid-afternoon, so the live market is a
    # better estimator than a fresh forecast. Future-day markets still trade.
    skip_today_after_local_hour: int = 14
    max_open_positions: int = 20
    slippage_cents: int = 1
    calibration_min_observations: int = 30
    cities: List[str] = field(default_factory=list)
    confirm_live: bool = False

    # --- strategy comparison ---
    # Which strategies run as independent paper portfolios (empty = all known).
    # Each gets its own ledger under data/<env>/<strategy_key>/.
    strategies: List[str] = field(default_factory=list)
    # Per-strategy config tweaks, e.g. {"ensemble": {"min_edge": 0.08}}.
    strategy_overrides: dict = field(default_factory=dict)

    # --- environment / credentials (from env vars + CLI) ---
    env: str = "paper"
    api_key_id: Optional[str] = None
    private_key_path: Optional[str] = None

    @property
    def base_url(self) -> str:
        return KALSHI_BASE_URLS[self.env]

    @property
    def is_live_trading(self) -> bool:
        """True when real orders will be sent (demo or live)."""
        return self.env in ("demo", "live")

    @property
    def data_dir(self) -> Path:
        return REPO_ROOT / "data" / self.env

    def validate_for_trading(self) -> None:
        """Raise if the configured environment can't safely place orders."""
        if self.env == "paper":
            return
        if not self.api_key_id or not self.private_key_path:
            raise ValueError(
                f"env={self.env} requires KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH"
            )
        if self.env == "live" and not self.confirm_live:
            raise ValueError(
                "Refusing to trade live: set \"confirm_live\": true in config.json "
                "to acknowledge real-money trading."
            )


def load_config(
    env: Optional[str] = None,
    config_path: Path | str = DEFAULT_CONFIG_PATH,
) -> Config:
    """Load config.json, overlay .env credentials, and resolve the trading env.

    Precedence for env: explicit `env` arg > KALSHI_ENV var > "paper".
    """
    load_dotenv(REPO_ROOT / ".env")

    config_path = Path(config_path)
    raw = {}
    if config_path.exists():
        raw = json.loads(config_path.read_text())

    # Only keep keys that map to known dataclass fields.
    known = {f.name for f in fields(Config)}
    kwargs = {k: v for k, v in raw.items() if k in known}

    resolved_env = (env or os.getenv("KALSHI_ENV") or "paper").lower()
    if resolved_env not in VALID_ENVS:
        raise ValueError(f"Invalid env {resolved_env!r}; expected one of {VALID_ENVS}")
    kwargs["env"] = resolved_env
    kwargs["api_key_id"] = os.getenv("KALSHI_API_KEY_ID")
    kwargs["private_key_path"] = os.getenv("KALSHI_PRIVATE_KEY_PATH")

    return Config(**kwargs)
