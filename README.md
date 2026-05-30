# LiquidSky 🌤️

An automated trading bot for **Kalshi daily high-temperature markets**. It compares
real weather forecasts against market prices and buys contracts with positive expected
value, sizing positions with fractional Kelly and managing exits with a ratcheting
trailing stop.

> ⚠️ Trading involves risk. This software is provided for educational/research purposes.
> Paper mode is the default; live trading requires explicit opt-in (see below).

## How it works

Each cycle, for every configured city:

1. **Discover markets** — fetch open temperature-bucket markets for the city's Kalshi
   series (e.g. `KXHIGHNY` = NYC Central Park).
2. **Forecast** — query Open-Meteo across several global models (ECMWF, GFS, ICON, GEM,
   JMA) at the market's *resolution station* coordinates, plus the latest METAR
   observation. This yields a Gaussian `Normal(mu, sigma)` for the day's high.
3. **Price each bucket** — `P(high ∈ bucket)` from the Gaussian, compared to the market
   ask, gives an edge. Trades pass only if `edge ≥ min_edge`, `ask ≤ max_entry_cents`,
   and `volume ≥ min_volume`.
4. **Size** — fractional Kelly on the bankroll, capped by `max_bet`.
5. **Monitor** — refresh open positions, ratchet the trailing stop upward as they gain,
   exit on a stop hit, and settle when the market resolves.

## Install

```bash
pip install -r requirements.txt
pip install -e .          # makes `python -m liquidsky.cli` importable
```

The package lives under `src/`, so the editable install (`pip install -e .`) is what
puts `liquidsky` on the import path. If you'd rather not install, prefix commands with
`PYTHONPATH=src` instead (e.g. `PYTHONPATH=src python -m liquidsky.cli status`).

## Run (paper mode — default, no orders sent)

```bash
python -m liquidsky.cli once     # one scan + monitor cycle against live market data
python -m liquidsky.cli run      # continuous loop (every scan_interval_minutes)
python -m liquidsky.cli status   # cash balance + open positions
python -m liquidsky.cli report   # win rate, realized P&L, peak equity, drawdown
```

Paper mode reads **real** Kalshi market data (no API key needed) and simulates fills at
the live quoted prices. Its ledger lives in `data/paper/`.

## Dashboard (web UI)

```bash
python -m liquidsky.cli serve --env paper        # then open http://127.0.0.1:8787
```

A single-page dashboard to watch everything live and drive the bot from the browser:

- **Stat cards** — total equity, cash, open positions, realized P&L.
- **Equity curve** — hand-drawn SVG from the `equity.jsonl` snapshots.
- **Open positions table** — side, qty, entry, last price, stop, cost, value, unrealized P&L.
- **Report panel** — trades opened/closed, win rate, realized P&L, peak equity, drawdown.
- **Command deck** — buttons for `run once`, `start loop`, `stop loop` (these are the same
  actions as the CLI `once` / `run` commands, run in a background thread).
- **Activity log** — the bot's live log stream (forecasts, buys, sells, settlements).

The page polls the backend (`/api/status`, `/api/report`, `/api/equity`, `/api/logs`) and
auto-refreshes. Use `--host`/`--port` to change the bind address.

## Configuration

Edit `config.json`:

| Key | Meaning | Default |
|---|---|---|
| `starting_balance` | paper bankroll (USD) | 1000 |
| `max_bet` | max stake per position (USD) | 50 |
| `min_edge` | minimum edge to trade | 0.05 |
| `max_entry_cents` | skip asks above this (cents) | 45 |
| `min_volume` | minimum market volume | 50 |
| `kelly_fraction` | fraction of full Kelly | 0.25 |
| `forecast_sigma_default` | fallback uncertainty (°F) | 2.5 |
| `stop_loss_pct` | initial hard stop below entry | 0.20 |
| `trail_activate_gain` | gain that activates trailing | 0.20 |
| `trail_pct` | trail at this % of high-water | 0.80 |
| `scan_interval_minutes` | loop cadence | 60 |
| `skip_today_after_local_hour` | stop opening *today's* market after this local hour | 14 |
| `max_open_positions` | cap on concurrent open positions | 20 |
| `cities` | list of series tickers (empty = all) | `[]` |
| `confirm_live` | must be `true` to trade live | false |

Supported cities live in `src/liquidsky/cities.py` (NYC, Chicago, LA, Miami, Austin,
Denver, Philadelphia). Add a row with the resolution-station coordinates to support more.

## Live / demo trading (opt-in)

1. Create an API key in the Kalshi web app and download the RSA private key (`.pem`).
2. `cp .env.example .env` and set `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH`.
3. **Demo sandbox** (real orders, fake money):
   ```bash
   python -m liquidsky.cli once --env demo
   ```
4. **Production** (real money) — additionally set `"confirm_live": true` in `config.json`:
   ```bash
   python -m liquidsky.cli run --env live
   ```

Authentication uses RSA-PSS request signing (`KALSHI-ACCESS-KEY/-TIMESTAMP/-SIGNATURE`)
handled in `src/liquidsky/kalshi_client.py`.

## Strategy notes & limitations

- **Same-day staleness.** A daily high usually occurs mid-afternoon. Once the day
  is mostly over, the live market reflects information a fresh forecast doesn't, so
  the bot's "edge" against it is illusory. LiquidSky defers to the market by skipping
  the current day's contracts after `skip_today_after_local_hour` (per city's local
  time) and never opens positions on a past day awaiting settlement. The real edge is
  on **future-day** markets, where the forecast genuinely leads the market.
- **Trading-day vs. close time.** The measurement date is parsed from the ticker
  (e.g. `KXHIGHNY-26MAY30`), not `close_time` — which falls just after midnight the
  following day and would otherwise forecast the wrong date.
- **Gaussian model.** The day's high is modeled as `Normal(mu, sigma)` where `sigma`
  comes from the spread across forecast models (floored). This is a simple, transparent
  baseline; per-city calibration of `sigma` against realized highs is a natural next step.
- **Penny longshots.** Very cheap contracts (1–3¢) can show large percentage "edges"
  and noisy stop-outs. Tune `min_edge`, `max_entry_cents`, and `min_volume` before
  trusting them, and validate in paper mode first.
- **Resolution stations.** Forecasts use each market's NWS resolution-station
  coordinates (see `cities.py`). Verify these match Kalshi's settlement source when
  adding cities.

## Tests

```bash
pytest
```

Unit tests cover the strategy math and the RSA-PSS signature — all with no network access.

## Project layout

```
src/liquidsky/
  config.py        # config.json + .env -> Config; env = paper|demo|live
  kalshi_client.py # RSA-PSS signed REST client
  cities.py        # series ticker -> resolution station coordinates
  marketdata.py    # normalize Kalshi *_dollars / *_fp fields, parse ticker dates
  forecast.py      # Open-Meteo + METAR -> Normal(mu, sigma)
  strategy.py      # bucket parsing, probability, EV, Kelly (pure functions)
  positions.py     # trade-file ledger, balance recompute, ratcheting stop
  execution.py     # PaperExecutor / LiveExecutor
  bot.py           # scan / monitor / run / status / report (+ structured data for UI)
  cli.py           # command-line entry point (once / run / status / report / serve)
  web.py           # Flask dashboard API + background run/loop threads
  web_static/      # dashboard frontend (index.html, styles.css, app.js)
```
