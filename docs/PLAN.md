# Project Plan — Alpaca StochRSI + MFI Trading Bot

> Status as of **2026-06-16**: skeleton built, verified (18 tests + live paper smoke
> test + basket backtest), committed (`703f9f4`). Currently in the **tune & validate**
> phase. This is a living document — update the status boxes as work progresses.

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
MFI 14, MFI bands 30/80, divergence as confidence (not required), pivots 3/3, lookback 60.

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
- [ ] **Phase 7 — Tune & validate (CURRENT).** Out-of-sample / walk-forward, regime tests,
      intraday timeframe; lock defaults only after out-of-sample confirmation.
- [ ] **Phase 8 — Paper run.** ~2 weeks live on paper; watch reconnects, partial fills, restart recovery.
- [ ] **Phase 9 — Go live (small).** `ALPACA_PAPER=false`, capital you can lose, ramp slowly.
- [ ] **Phase 10 — Deploy.** Linux VPS under systemd (`Restart=on-failure`) or Docker
      (`restart: unless-stopped`); streaming needs a persistent process.

---

## 9. Current status

- ✅ Skeleton built and committed (`703f9f4`, 33 files).
- ✅ **18/18 tests pass**; all modules compile.
- ✅ Indicator math + every `alpaca-py` call adversarially reviewed and fixed.
- ✅ Paper connection verified (equity $100k, data flowing).
- ✅ End-to-end backtest + 10-symbol parameter sweep run successfully.
- ⏳ Uncommitted: SIP/IEX feed-override tweaks used by the sweep.

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

---

## 11. Open decisions & next steps

1. **Out-of-sample / walk-forward validation** (recommended next) — include the 2022
   drawdown/sideways tape and range-bound names; that's the regime this strategy should suit.
2. **Intraday sweep** (15Min / 1Hour) — more natural for this signal style; more trades.
3. **Adopt looser defaults** only if they survive (1).
4. Decide if/when to wire the **websocket stream** (Phase 8+) vs. staying on polling.

---

## 12. References

- Alpaca docs: <https://docs.alpaca.markets>
- alpaca-py SDK: <https://github.com/alpacahq/alpaca-py> · <https://alpaca.markets/sdks/python>
- Intraday Margin Rule (PDT replacement): <https://docs.alpaca.markets/us/docs/the-intraday-margin-rule>
- Repo usage: see [README.md](../README.md)
