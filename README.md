# Alpaca Trading Bot — StochRSI + MFI with divergence

An equities trading bot for [Alpaca](https://alpaca.markets) built on the official
**`alpaca-py`** SDK. The strategy combines **Stochastic RSI** and the **Money Flow
Index (MFI)** with optional **price/indicator divergence** confirmation. Paper-first,
runs locally.

> ⚠️ **Not financial advice.** Algorithmic trading can lose money fast. Run on a
> **paper** account for at least ~2 weeks before risking a cent, then start tiny.

## What's current as of 2026 (don't trust older tutorials)

- **SDK:** `alpaca-py` (the old `alpaca-trade-api` is deprecated — do not use it).
- **No more PDT rule:** FINRA retired the Pattern Day Trader rule and the $25k
  day-trading minimum on **June 4, 2026**. Don't build day-trade-counting logic;
  it's replaced by a real-time [Intraday Margin Rule](https://docs.alpaca.markets/us/docs/the-intraday-margin-rule).
- **Market data:** the free **Basic** plan (IEX feed) is enough to start. You get
  7+ years of full-market history (queries ending ≥15 min ago) and a free
  `delayed_sip` feed. Real-time full-market SIP + live options need **Algo Trader
  Plus ($99/mo)** — not required here.
- **TA-Lib** now ships official Windows wheels; **`pandas-ta`** is end-of-life (use
  `pandas-ta-classic` if you want it). This bot implements its indicators directly,
  so neither is required.

## The strategy

Long-only (shorting adds margin/borrow complexity — add it later):

| | Condition |
|---|---|
| **Enter long** | StochRSI %K crosses **above** %D while oversold, **and** MFI is oversold (volume confirms buyers) |
| **Exit long** | StochRSI %K crosses **below** %D while overbought, **or** MFI overbought |
| **Trend filter** | **On by default** — longs only when price is above its 200-day SMA, so the bot doesn't buy dips in a downtrend. Validated to cut the 2022 bear drawdown ~73% (see [docs/PLAN.md](docs/PLAN.md) §10). Disable with `use_trend_filter=False`. |
| **Divergence** | Bullish divergence (price lower-low + MFI higher-low) raises confidence to 1.0; set `divergence_required=True` to make it mandatory for entries |

All thresholds live in [`StochRsiMfiParams`](bot/strategy/stoch_rsi_mfi.py). The same
`compute_signals()` runs in both the live bot and the backtest, so what you test is
what you trade.

## Project layout

```
bot/
  config.py            # typed settings from .env (pydantic-settings)
  models.py            # SignalDecision / enums (pure)
  indicators/          # stoch_rsi, money_flow (MFI), divergence  (pure)
  strategy/            # StochRSI+MFI signal logic                (pure)
  risk/                # sizing, gates, daily-loss KILL SWITCH    (pure)
  execution/broker.py  # alpaca-py TradingClient, idempotent orders
  data/                # historical bars (+Parquet cache) & live stream
  state/ledger.py      # SQLite order ledger + reconciliation
  run.py               # orchestrator: data -> strategy -> risk -> execution
backtests/             # backtesting.py harness
scripts/smoke_test.py  # verify paper connection + data (read-only)
tests/                 # pytest — indicators, strategy, kill switch
```

## Setup (Windows / PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env   # then edit .env with your PAPER keys
```

Get paper keys at <https://app.alpaca.markets/paper/dashboard/overview>.

## Run it

```powershell
# 1. Verify connection + data (read-only)
python -m scripts.smoke_test

# 2. Backtest the strategy (research only — optimistic vs. live)
python -m backtests.backtest_stoch_rsi_mfi --symbol AAPL --years 3 --plot

# 3. Run one live decision cycle on paper, then exit
python -m bot.run --once

# 4. Run continuously, polling every 5 minutes
python -m bot.run --interval 300

# Tests (no keys/network needed — pure indicator/strategy/risk math)
pytest
```

## Risk controls (built in)

- **Daily-loss kill switch** — flattens everything and blocks new entries once the
  daily loss limit is hit (`max_daily_loss_pct`). Tested in `tests/test_risk.py`.
- **Position sizing** — risk-per-trade against a stop, hard-capped at
  `max_position_pct` of equity.
- **Exposure gate** — refuses entries past `max_total_exposure_pct`.
- **Idempotent orders** — every order carries a unique `client_order_id`; on a
  timeout, verify via the API rather than resending (Alpaca doesn't guarantee
  duplicate rejection).
- **Broker is source of truth** — positions are reconciled from Alpaca each cycle.

Tune everything in [`RiskConfig`](bot/risk/manager.py).

## Roadmap to live

1. Backtest and tune parameters (walk-forward; don't overfit).
2. Paper trade ~2 weeks; watch reconnects, partial fills, restart recovery.
3. Go live with `ALPACA_PAPER=false` and capital you can lose; ramp slowly.
4. Deploy: a small always-on Linux VPS under `systemd` (`Restart=on-failure`) or
   Docker (`restart: unless-stopped`). Streaming needs a persistent process.
```
