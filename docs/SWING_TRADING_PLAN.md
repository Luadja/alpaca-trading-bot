# Swing-Trading Conversion — Plan

> **Framing:** This is a **playground / learning experiment**, not a profitability upgrade.
> Swing trading (holding days→weeks to catch short-term price swings) is *harder* to make
> net-profitable than buy-and-hold — more trades mean more cost, slippage, and noise, and our
> research already showed beating the market is hard. Goal here: experiment with higher-
> volatility, more-active strategies and learn. **Paper / token money only.**

Swing trading = enter on a short-term setup, hold a few days to a few weeks, exit on a profit
target / stop / trailing stop / time limit. Much more active than the current months-long
50/200 trend-following.

---

## 1. What we KEEP (the foundation is reusable — and swing needs it *more*)
No changes needed; these are the hardened parts worth keeping:
- **Safety stack:** kill switch, catastrophic stop, watchdog / dead-man's switch, heartbeat.
- **Execution integrity:** idempotent deterministic `client_order_id`, fill verification,
  retry/backoff, SQLite ledger + crash reconciliation.
- **Infra:** broker wrapper, config/strategy framework (`compute_signals` contract), data layer,
  Discord activity feed, Streamlit dashboard, alerting, the portfolio + per-symbol backtesters.

## 2. What's NEW (the actual work)

### A. Swing strategy signals (pick 1–2 to start)
Implement as new `compute_signals()` modules in the existing strategy framework:
- **Mean-reversion bounce** — RSI(2)/StochRSI oversold *within an uptrend*, or Bollinger-band
  reversion. (We have a retired `stoch_rsi_mfi` module to revive/adapt.)
- **Breakout** — N-day high / Donchian channel break, or ATR/volatility breakout.
- **Short-term momentum** — 9/21 EMA cross, gap-and-go, price+volume surge.

### B. Active position-management layer  ← **the big new piece**
Today the bot is "enter on signal → exit on the *opposite* signal." Swing trading needs each
open trade actively managed every cycle (and between cycles):
- **Take-profit** (target = +X% or an ATR multiple / resistance).
- **Tight stop-loss** (ATR-based or below the swing low — far tighter than today's 10% catastrophic stop).
- **Trailing stop** (lock in gains as the swing runs).
- **Time-based exit** (close after N days if it hasn't worked).
Design: persist each open trade's `{entry, stop, target, age, high-water}` in the ledger; evaluate exits each cycle.

### C. Execution: bracket / OCO orders
Use Alpaca's native **bracket orders** (entry + attached take-profit + stop-loss) so the broker
manages TP/SL even *between* bot cycles (robust if the bot is down). Bot-side logic handles the
trailing + time exits. New broker methods: `submit_bracket()`, OCO/leg handling, partial-fill of legs.

### D. Volatile universe + ATR position sizing
- A configurable **watchlist of higher-volatility names** (high-beta stocks, leveraged ETFs like
  TQQQ/SOXL, momentum names — or crypto, if we go there).
- **ATR / volatility-based sizing** — risk a fixed % per trade against an ATR-sized stop, so a
  jumpy name and a calm name carry the same $ risk.

### E. Risk retune for higher volatility
- Tighter per-trade ATR stops (distinct from the 10% catastrophic backstop).
- Re-tune kill-switch limits (today 3%/6%/10% daily/weekly/monthly) — volatile names swing more,
  so these may trip too often or too late.
- `max concurrent positions` + faster rotation; cap total exposure.
- (PDT day-trade rule was retired 2026-06-04, and swings are held overnight anyway → no constraint.)

### F. Timeframe / data
- **Start: daily bars** (hold days→weeks). Reuses the current daily polling architecture →
  lowest complexity. The bot already supports this.
- **Later: intraday** (1Hour / 15Min) for faster swings. Needs continuous market-hours running +
  intraday data. ⚠️ free SIP requires data ≥15 min old, so *fresh* intraday signals need real-time
  IEX (thin ~3% volume) or accepting a 15-min-delayed signal. More API volume / rate-limit care.

### G. Backtesting for swing
- Model **TP / SL / trailing / time exits** in the backtest. `backtesting.py` natively supports
  `self.buy(sl=, tp=)` → extend the per-symbol harness to use bracket exits.
- Validate every swing strategy **walk-forward + cost-swept** (swing is cost-sensitive — many
  more trades). Honest expectation: most will *not* beat buy-and-hold net of costs.

### H. Observability
- Reuse the Discord activity feed (perfect for swing — lots of fills/P&L to watch) + dashboard.
- Add an **open-trades view** (entry / stop / target / age / unrealized P&L) and per-trade realized P&L.

## 3. Suggested build order
- **Phase 1 (MVP):** one daily swing strategy + bracket orders (TP+SL) + ATR sizing + a swing
  backtest. Paper. Reuse the entire safety stack. → smallest end-to-end working version.
- **Phase 2:** position-management (trailing + time exits), a 2nd strategy, the volatile watchlist,
  the open-trades dashboard.
- **Phase 3 (optional, bigger):** intraday timeframe, **shorting** (long/short — a substantial
  change; the bot is long-only today), leveraged-ETF / crypto universe.

## 4. Honest caveats
- Active/swing trading underperforms buy-and-hold *more often than not* once costs + slippage +
  human error are counted. Treat purely as a sandbox.
- Higher volatility = bigger drawdowns + **overnight gap risk** (single-stock earnings, leveraged-
  ETF decay). Stops don't help across gaps.
- Keep it paper or token money — consistent with your (sound) decision not to fund the edge.

## 5. Decisions to lock before building
1. **Timeframe** — daily (recommended start) vs intraday?  → **CHOSEN: intraday (1H/15min)**
2. **Strategy flavor** — mean-reversion / breakout / momentum?  → **CHOSEN: mean-reversion bounce**
3. **Universe** — volatile stocks / leveraged ETFs / crypto?  → **CHOSEN: crypto**
4. **Long-only vs add shorting?**  → **RESOLVED: crypto LONG-ONLY** (Alpaca crypto is spot — no shorting; mean-reversion is long-biased anyway)

---

## 6. Chosen direction: Crypto · Intraday · Mean-reversion · **Long-only**  ✅ locked
This is the most ambitious combination — effectively a **new bot** that reuses the
safety/ledger/alerting/infra but rewrites the data, market-clock, order, and run-loop layers.

### ⚠️ Blocking conflict: Alpaca crypto is SPOT-ONLY — you can't short it
Shorting on Alpaca is an **equities/margin** feature; crypto has no borrow/margin/short. So
"crypto **and** shorting" cannot both be true on Alpaca. (Confirm against current Alpaca docs,
but this has long been the case.) Resolve one way:
- **(A) Crypto, LONG-ONLY** ✅ **CHOSEN** — drop shorting. Mean-reversion is naturally
  long-biased (buy oversold dips), so we lose little, and it's the simplest crypto path.
- ~~(B) Stocks / leveraged-ETFs, LONG/SHORT~~ — not chosen.
- ~~(C) Crypto long + inverse-ETF "shorts"~~ — not chosen (messy).

### Crypto-specific new plumbing (on top of §2)
- **24/7 run loop** — remove the `is_market_open()` gating that early-returns when closed;
  crypto trades around the clock. Scheduler, heartbeat, and watchdog all run 24/7 (no
  market-hours logic). Touches the core loop, watchdog, and go-live checks.
- **Crypto data layer** — `CryptoHistoricalDataClient` + `CryptoBarsRequest` (separate from
  stocks). No SIP/IEX feed, no 15-min rule, no 16-min clamp — real-time and free. Symbols like
  `BTC/USD`, `ETH/USD`, `SOL/USD`.
- **Crypto orders** — natively fractional (no whole-share floor). ⚠️ crypto likely does **not**
  support bracket/OCO orders, so the position-management layer (§2B) **must be bot-side**
  (monitor TP/SL/trailing/time each cycle and submit exits); §2C broker-brackets won't apply.
- **Crypto fees** — ~0.1–0.25%/side (vs commission-free stocks) → costs bite hard on
  high-frequency mean-reversion; model them in the backtest.
- **Risk/annualization** — crypto is far more volatile (−50%+ drawdowns are normal); ATR stops
  + kill-switch limits need crypto tuning; metrics annualize on 365×24h, not 252 trading days.

### Revised phasing (crypto)
- **Phase 1 (MVP):** 24/7 loop + crypto data + crypto orders (fractional, bot-side exits) + one
  mean-reversion strategy (RSI/StochRSI bounce) on 2–3 liquid pairs (BTC/ETH/SOL) + a crypto
  backtest with realistic fees. Paper, long-only.
- **Phase 2:** trailing/time exits, more pairs, open-trades dashboard, fee/cost tuning.
- **Phase 3:** *only if you choose option (B)* — shorting on stocks (not crypto).

---

## 7. BUILT — Phase 1 status & the honest verdict (2026-06-18)

**Built end-to-end and shipped (paper):**
- Crypto data layer (`get_crypto_bars`, `crypto_price`), 24/7 run-loop mode (`bot/run.py` `market="crypto"`),
  crypto GTC fractional orders (`submit_crypto_market`), bot-side channel exit, ATR-free Donchian
  sizing via the existing risk caps, crypto-armed watchdog, one-command launcher (`scripts/run_crypto.ps1`).
- Strategies tried + backtested: intraday mean-reversion, daily mean-reversion, **daily momentum/breakout**
  (`bot/strategy/breakout.py`). Backtesters: `crypto_swing.py`, `crypto_momentum.py`, `_sweep.py`, `_validate.py`.
- 147 tests pass. Two adversarial multi-agent reviews (research + wiring) — no must-fix bugs.

**The honest verdict — NO demonstrated edge (this is why it ships as a *playground*, not a strategy):**
- Mean-reversion: dead on crypto (intraday = fee bleed; daily = no edge).
- Momentum/breakout looked good in-sample (median +35%, 68% pairs profitable) but the adversarial
  teardown broke it: **fails walk-forward** (H1 +54% → H2 −16%/28% win — the recent regime LOST),
  **survivorship-inflated** (universe = coins alive today), **wrong benchmark** (only beats the dead-alt
  basket; ties a zero-effort BTC+cash blend; captures ~⅓ of just-holding-BTC). Honest forward
  expectation: **flat-to-negative**, bounded losses, big upside only if a fresh sustained bull recurs.
- Same lesson as the equities research: trends pay only *in* trends, and you can't time the regime.

**Run it:** `powershell -ExecutionPolicy Bypass -File scripts\run_crypto.ps1` (paper, breakout, 1Day,
7 liquid pairs, isolated ledger, watchdog armed). Kill switch auto-widened for crypto vol
(15%/30%/50%; catastrophic 25%). Startup logs a loud "no edge / paper only" caveat. **Do not fund.**
