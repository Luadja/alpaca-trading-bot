# Project Plan — Alpaca StochRSI + MFI Trading Bot

> Status as of **2026-06-16**: skeleton built, verified (full test suite + live paper smoke
> test + basket backtest + walk-forward validation), committed. Currently in the **tune &
> validate** phase. This is a living document — update the status boxes as work progresses.

---

## 1. Objective & scope

Build an automated **US equities** trading bot on Alpaca that trades a **Stochastic
RSI + Money Flow Index** strategy with **price/indicator divergence** confirmation.

- **Paper-first.** No live capital until the strategy survives paper + out-of-sample tests.
- **Local for now.** Polling loop on the dev machine; VPS/Docker deferred.
- **Long-only to start.** Shorting (margin/borrow) is explicitly out of scope for v1.
- **Equities only.** Crypto/options are possible later (the SDK supports them) but not now.

Non-goals for v1: HFT/low-latency, options/crypto, multi-account, a UI.

---

## 2. Key decisions

| Decision | Choice | Rationale |
|---|---|---|
| Language / SDK | Python + **`alpaca-py`** | Official, maintained SDK. `alpaca-trade-api` is deprecated. |
| Strategy | StochRSI + MFI, divergence as confirmation | User-specified; mean-reversion + volume confirmation. |
| Divergence target | Computed against **MFI** | Volume-weighted momentum gives a meaningful divergence signal. |
| Direction | Long-only | Avoids margin/borrow complexity in v1. |
| Data feed | Free **Basic / IEX** live; **SIP** for history (>15 min old) | $0 to start; SIP history is free when the query ends >15 min ago. |
| Execution model | Polling loop (local) | Simple, robust; streaming deferred until needed. |
| Storage | Parquet (bars) + SQLite (ledger) | Lightweight, no server. |

---

## 3. Market / API facts that shape the design (verified 2026-06)

These were fact-checked against primary sources; several **invalidate older tutorials**:

- **PDT rule retired.** FINRA's Pattern Day Trader rule and the $25k day-trading
  minimum were **retired 2026-06-04**, replaced by a real-time Intraday Margin Rule.
  → *Do not build day-trade-counting logic.*
- **SDK:** `alpaca-py` v0.43.4 (Apr 2026). `alpaca-trade-api` is deprecated.
- **Market data plans:** free **Basic** (IEX feed, ~3% of volume, 30-symbol websocket
  cap, 200 calls/min) vs **Algo Trader Plus $99/mo** (full real-time SIP, OPRA options,
  10k calls/min). Both get 7+ years of history.
- **15-minute rule:** on the free tier, historical **SIP** queries are allowed as long
  as `end` is ≥15 min in the past. The bot ends history queries 16 min back.
- **Equities history** starts 2016; **adjust bars for splits/dividends** (the bot uses
  `Adjustment.ALL`) or backtests show fake price cliffs at splits.
- **Trading API rate limit:** 200 req/min per account → prefer streams over polling at scale.
- **Order types:** market / limit / stop / stop-limit / trailing-stop; classes simple /
  bracket / OCO / OTO. TIF day/gtc/opg/cls/ioc/fok.

Sources: `docs.alpaca.markets` (trading-api, about-market-data-api, market-data-faq,
the-intraday-margin-rule, orders-at-alpaca), `pypi.org/project/alpaca-py`.

---

## 4. Architecture

Decoupled pipeline — the **strategy is pure** (data in, signal out, no broker calls),
so the exact same signal code runs in backtest, paper, and live:

```
            ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌────────────┐
  bars ───▶ │   DATA   │─▶ │ STRATEGY  │─▶ │   RISK   │─▶ │ EXECUTION  │─▶ Alpaca
            │ historical│   │ (signals) │   │ sizing,  │   │ idempotent │
            │ + stream  │   │  PURE     │   │ gates,   │   │ orders     │
            └──────────┘   └───────────┘   │ kill sw. │   └────────────┘
                 ▲                          └──────────┘         │
                 │                                               ▼
            Parquet cache                                  SQLite LEDGER
                                                      (intent → submitted)
                                          broker = source of truth (reconcile)
```

Module map (`bot/`): `config` · `models` · `indicators/` · `strategy/` · `risk/` ·
`execution/broker` · `data/{historical,stream}` · `state/ledger` · `run` (orchestrator).

---

## 5. Strategy specification

Long-only. The single source of truth is `compute_signals()` (shared by live + backtest).

| | Trigger |
|---|---|
| **Enter long** | StochRSI %K crosses **above** %D while %K < oversold band **and** MFI < oversold band |
| **Exit long** | StochRSI %K crosses **below** %D while %K > overbought band **or** MFI > overbought band |
| **Divergence** | Bullish (price lower-low + MFI higher-low) raises confidence 0.5→1.0; `divergence_required=True` makes it mandatory (found too strict in testing — see §10) |

Default params (`StochRsiMfiParams`): RSI 14, Stoch 14, %K/%D 3/3, stoch bands 20/80,
MFI 14, MFI bands 30/80, divergence as confidence (not required), pivots 3/3, lookback 60,
**trend filter ON** (longs only when price > 200-day SMA — validated, see §10).

---

## 6. Risk management & safety (the part that protects money)

- **Daily-loss kill switch** — flattens all and blocks new entries once daily loss
  ≥ `max_daily_loss_pct`; resets on trading-day rollover. *Unit-tested.*
- **Position sizing** — risk-per-trade against a stop, hard-capped at `max_position_pct`.
- **Exposure gate** — refuses entries past `max_total_exposure_pct`, tallied across same-cycle entries.
- **Idempotent orders** — deterministic `client_order_id` (symbol+side+bar); a retry of the
  same decision can't double-order. Broker reconciled as source of truth each cycle.
- **Defaults:** 10% max position, 60% max exposure, 1% risk/trade, 5% stop, 3% daily-loss limit.

---

## 7. Reliability / correctness principles

- Strategy is pure and side-effect free → identical signals in backtest/paper/live.
- Broker is the source of truth; local ledger records intent vs. submission.
- Split/dividend-adjusted history; no look-ahead in divergence (confirmed pivot+`right`).
- Paper does **not** model slippage/fees/borrow — treat paper & backtest P&L as optimistic.

---

## 8. Phased roadmap

- [x] **Phase 0 — Scaffold.** Repo, config, logging, deps, tests, CI-able layout.
- [x] **Phase 1 — Connectivity.** `alpaca-py` wrapper, paper auth, account/positions (smoke test).
- [x] **Phase 2 — Data.** Historical bars (Parquet cache, adjusted) + stream skeleton.
- [x] **Phase 3 — Strategy.** StochRSI, MFI, divergence; `compute_signals`; unit tests.
- [x] **Phase 4 — Risk.** Sizing, gates, kill switch (tested).
- [x] **Phase 5 — Execution & state.** Idempotent orders, SQLite ledger, reconcile.
- [x] **Phase 6 — Backtest harness.** `backtesting.py` + multi-symbol parameter sweep.
- [~] **Phase 7 — Tune & validate (CURRENT).** Sweep ✅, out-of-sample/walk-forward + regime
      tests ✅, trend/regime filter added and validated ✅ (now the default). The filter cut the
      2022 bear drawdown ~73% and improved OOS robustness (see §10). Still does not beat
      buy-and-hold and is low-activity. Next: intraday timeframe sweep, then paper.
- [ ] **Phase 8 — Paper run.** ~2 weeks live on paper; watch reconnects, partial fills, restart recovery.
- [ ] **Phase 9 — Go live (small).** `ALPACA_PAPER=false`, capital you can lose, ramp slowly.
- [ ] **Phase 10 — Deploy.** Linux VPS under systemd (`Restart=on-failure`) or Docker
      (`restart: unless-stopped`); streaming needs a persistent process.

---

## 9. Current status

- ✅ Skeleton built, reviewed, committed; indicator math + every `alpaca-py` call verified.
- ✅ **21/21 tests pass**; all modules compile.
- ✅ Paper connection verified (equity $100k, data flowing); backtest + sweep + walk-forward
  validation all run end to end.
- ✅ Trend filter implemented, validated, and made the **default** (Phase 7).
- ⏳ Next: intraday timeframe sweep, then paper run (Phase 8).

---

## 10. Findings so far (parameter sweep, 3yr daily, 10-symbol basket)

- **No parameter set beat buy-and-hold** on a +125% mega-cap/ETF bull-run basket — expected
  for a mean-reversion oscillator that sits in cash most of the time. Its edge (if any) is
  selective, high-win-rate entries with lower exposure, *not* trend capture.
- Best risk-adjusted set: `stoch_oversold=30 / mfi_oversold=45 / divergence off` →
  Sharpe ~0.70, ~13 trades, **75% win rate**, −19% max DD.
- `divergence_required=True` is **too strict** (<2 trades / 3yr across the basket) — keep
  divergence as a confidence booster, not a hard gate.
- ⚠️ These rankings are from one bull window — **do not adopt as defaults without
  out-of-sample validation** (overfitting risk).

### Out-of-sample / walk-forward validation (8yr, 15-symbol diversified basket)

In-sample 2018-06→2021-12 (picks params), out-of-sample 2022-01→now (tests them):

- **No validated edge — raw oscillator, trend filter OFF; superseded by the trend-filter
  result below.** The best *active* in-sample config (`stoch40/60 mfi45/55 div=N`)
  degraded out-of-sample (Sharpe **0.21 → 0.10**, win 65%→59%, DD −24%→−28%) and beat
  buy-and-hold on only **1 of 15** symbols in both windows.
- **Bear-market blow-up (the key risk).** In the 2022 bear it returned **−14.7%
  (Sharpe −0.89, 40% win)** — a long-only "buy the oversold dip" strategy catches falling
  knives in a downtrend. It only "beat B&H" there because B&H fell further; that's
  under-participation, not capital protection.
- **The conservative default is more robust** than the looser "optimized" config
  (default Sharpe 0.16→0.13 stable, DD ~−18% vs −28%) — so loosening thresholds is *not*
  an improvement once judged out-of-sample.
- **Ranking by Sharpe alone is gamed by inactivity** — the unfiltered IS-best traded
  ~0.3 times in 3.5 years. `validate.py` now applies a `--min-trades` eligibility filter.
- `divergence_required=True` confirmed near-inert across 8 years → keep divergence as a
  confidence booster only.
- **Verdict:** do **not** trade the raw oscillator. The signal works in up/sideways
  regimes but must not buy dips in sustained downtrends → fixed by the trend filter below.

### Trend filter result (now the default)

Re-ran the same walk-forward with `use_trend_filter=True` (longs only when price > 200-SMA):

- **2022 bear blow-up fixed:** best-active config went from **−14.7% (DD −22.8%)** to
  **−3.9% (DD −9.3%)** — the bot stops buying dips in a downtrend (2022 trades 5.0 → 1.6).
- **More robust out-of-sample:** that config's Sharpe held at **0.13 → 0.19** IS→OOS (vs
  0.21 → 0.10 unfiltered), with OOS drawdown −27.9% → −17.2%.
- **Best overall config = default params + trend filter:** OOS Sharpe **0.32**, **80% win
  rate**, max DD **−8.6%**, +9.8% — and it improved rather than degraded out-of-sample.
- ⚠️ Still **does not beat buy-and-hold** (1/15 names) and is **low-activity** (~4 trades /
  4yr OOS → thin statistical power). Positioning: a conservative, drawdown-controlled
  strategy, not a market-beater. More activity/signal is the job of an intraday timeframe.

---

## 11. Open decisions & next steps

1. ~~Add a trend / regime filter~~ ✅ **Done** — per-symbol price > 200-SMA, now the default
   (`validate.py --trend-filter` reproduces the result). A market-wide SPY-regime gate is a
   possible follow-up (needs cross-symbol data into the strategy).
2. ~~Keep the conservative default params~~ ✅ confirmed more robust than the looser set.
3. **Intraday sweep (top priority now)** — 15Min / 1Hour, where this signal style is more
   natural and trades more often (the current edge is real but too low-activity for confidence).
4. **Paper run (Phase 8)** once an intraday/daily config is settled.
5. Decide if/when to wire the **websocket stream** (Phase 8+) vs. staying on polling.

---

## 12. References

- Alpaca docs: <https://docs.alpaca.markets>
- alpaca-py SDK: <https://github.com/alpacahq/alpaca-py> · <https://alpaca.markets/sdks/python>
- Intraday Margin Rule (PDT replacement): <https://docs.alpaca.markets/us/docs/the-intraday-margin-rule>
- Repo usage: see [README.md](../README.md)
