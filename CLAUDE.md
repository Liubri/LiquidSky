# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (required once — package lives under src/ so must be editable-installed)
pip install -r requirements.txt
pip install -e .

# Run tests (all / single file / single test)
pytest
pytest tests/test_strategy.py
pytest tests/test_positions.py::test_trailing_stop_ratchets_up_and_never_down

# CLI commands
python -m liquidsky.cli once    --env paper   # one scan + monitor cycle
python -m liquidsky.cli run     --env paper   # continuous loop
python -m liquidsky.cli status  --env paper
python -m liquidsky.cli report  --env paper
python -m liquidsky.cli serve   --env paper   # web dashboard → http://127.0.0.1:8787

# Inspect a live market object (useful when debugging schema changes)
PYTHONPATH=src python3 -c "
from liquidsky.config import load_config
from liquidsky.kalshi_client import KalshiClient
c = KalshiClient(load_config(env='paper').base_url)
import json; print(json.dumps(c.get_markets(series_ticker='KXHIGHNY')[0], indent=2))
"
```

## Architecture

```
src/liquidsky/
  config.py       → Config dataclass; load_config() merges config.json + .env
  kalshi_client.py → RSA-PSS signed REST client; market reads (public) + order writes (auth)
  cities.py       → CITIES dict: series ticker → {metar_station, lat, lon, tz}
  marketdata.py   → normalizers: price_cents(), volume(), parse_event_date()
  forecast.py     → build_forecast(): Open-Meteo (5 deterministic models) + METAR → ForecastResult(mu, sigma)
  forecast_ensemble.py → build_ensemble_forecast(): Open-Meteo Ensemble API (GFS+ECMWF members) + NWS anchor → EnsembleForecast; empirical_bucket_probability()
  strategy.py     → pure functions: parse_bucket() → bucket_probability() → evaluate_market(prob_of_bucket?) → Signal
  strategies.py   → Strategy registry: GaussianStrategy / EnsembleStrategy; each yields a DayForecast(prob_of_bucket, mu, sigma)
  positions.py    → file-based ledger; calculate_balance_from_trades(); update_trailing_stop()
  execution.py    → PaperExecutor (no-op) / LiveExecutor (Kalshi orders); both return Fill
  bot.py          → Bot(cfg, strategy): one strategy's portfolio; scan_and_update(), monitor_positions(); structured data for UI
  desk.py         → Desk: runs every configured strategy as an independent Bot; run_once() fans out; compare() aggregates
  web.py          → Flask app; Runner owns a Desk; read APIs take ?strategy=; /api/strategies + /api/compare; LogBuffer captures liquidsky logger
  web_static/     → vanilla JS SPA (index.html + styles.css + app.js); strategy switcher + compare view; polls /api/* every 1.5–6s
```

### Strategy comparison (parallel paper portfolios)

`config.strategies` (list of keys, empty = all) selects which strategies run. Each is a separate `Bot` with its **own ledger** under `data/<env>/<strategy_key>/` and its own `starting_balance`, so they trade the same live markets as independent portfolios that can be compared head-to-head. The only thing that differs between strategies is the **forecast** (`DayForecast.prob_of_bucket`) — the edge/Kelly/filter/exit machinery in `strategy.py`/`positions.py` is shared, so a comparison isolates the signal. `strategy_overrides` in config.json applies per-strategy Config tweaks (e.g. `{"ensemble": {"min_edge": 0.08}}`). Calibration is tracked via a Brier score over settled positions (`Bot._brier_score`, lower = better).

### Data flow per cycle

`Desk.run_once()` → for each strategy `Bot.run_once()` → `monitor_positions()` then `scan_and_update()`.

**Scan:** for each city → `get_markets(series_ticker)` → group by `_target_date()` (parsed from ticker, not `close_time`) → `strategy.forecast()` (Gaussian or ensemble) → per bucket market → `evaluate_market(prob_of_bucket=...)` → if Signal qualifies → `executor.buy()` → `open_position(strategy=, entry_prob=)`.

**Monitor:** for each open position → `get_market(ticker)` → if resolved (`result` in yes/no) → `record_settlement()` → else refresh price → `update_trailing_stop()` → if `should_close()` → `executor.sell()` → `record_close()`.

### Ledger design (three invariants to preserve)

1. **Balance is always recomputed from files** (`calculate_balance_from_trades`). Never mutate a running balance with `+=`/`-=`. The equity curve in `equity.jsonl` is the source for peak/drawdown.
2. **Trailing stop only ratchets upward** (`update_trailing_stop`). Once `high_water >= entry * (1 + trail_activate_gain)`, stop = `max(stop, round(high_water * trail_pct))`. Never decrease it.
3. **`cost = count * price_cents / 100` exactly** (the `dollars()` helper). `returned` uses the same formula at exit. These two values must be consistent or P&L math breaks.

Each position is one JSON file under `data/<env>/markets/<ticker>.json`. The ledger is the ground truth; the in-memory `Bot` instance has no separate state.

### Kalshi API quirks

- **Prices are dollar strings**, not integers: `yes_ask_dollars: "0.0600"` = 6¢. Plain `yes_ask` is often `null`. Always use `marketdata.price_cents(market, "yes_ask")`.
- **Volume is a fixed-point string**: `volume_fp: "8698.28"`. Use `marketdata.volume(market)`.
- **Trading-day date is in the ticker**, not `close_time`. `KXHIGHNY-26MAY30` → 2026-05-30; `close_time` is just after midnight the following day. Use `marketdata.parse_event_date(market["event_ticker"])`.
- **`strike_type`** (`"between"`, `"greater"`, `"less"`) determines pad direction in `parse_bucket()`. A `greater` market with `floor=79` means "80 or above" → `lo = 79.5`. This is not obvious from the floor/cap values alone.
- Auth: sign `f"{timestamp_ms}{METHOD}{path}"` where path has **no query string**, with RSA-PSS (SHA256, MGF1-SHA256, salt=32). Headers: `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP` (ms), `KALSHI-ACCESS-SIGNATURE`.

### Trading environments

| env | orders sent | base URL | credentials needed |
|-----|-------------|----------|--------------------|
| `paper` | never | production (for market data) | none |
| `demo` | yes | `demo-api.kalshi.co` | API key + PEM |
| `live` | yes | production | API key + PEM + `confirm_live: true` in config.json |

`paper` is the default. The `data/` directory is gitignored; each env gets its own subdirectory (`data/paper/`, `data/demo/`, `data/live/`).

### Strategy filters (all must pass to generate a Signal)

- `edge = prob - ask/100 >= min_edge` (default 0.05)
- `ask <= max_entry_cents` (default 45) — after adding `slippage_cents`
- `volume >= min_volume` (default 50)
- Not already in `data/<env>/markets/<ticker>.json`
- `len(open_positions) < max_open_positions`
- Not a same-day market after `skip_today_after_local_hour` in the city's local time

### Adding a new city

Add a row to `CITIES` in `cities.py` with the **resolution-station** coordinates (the specific airport/park that Kalshi uses to settle, not the city centroid), the METAR station id, and the IANA timezone. Then add the corresponding city-filter button in `web_static/index.html` and the entry in the `CITIES` object in `web_static/app.js`.

### Web dashboard

The frontend (`web_static/`) is a vanilla JS SPA with no build step. It polls:
- `/api/status` every 3s — positions + runner state + stat cards
- `/api/report` and `/api/equity` every 6s
- `/api/logs?after=<id>` every 1.5s — incremental log lines from `LogBuffer`

`Runner` in `web.py` owns a single `threading.Lock` that serializes cycles. A `run-once` POST returns immediately (`started: true`) and runs in a daemon thread; `/api/loop/start` runs a loop that sleeps in 1-second slices so `/api/loop/stop` is responsive without polling.
