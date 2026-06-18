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
1. **Timeframe** — daily (recommended start) vs intraday?
2. **Strategy flavor** — mean-reversion / breakout / momentum (one, or a couple)?
3. **Universe** — volatile stocks / leveraged ETFs / crypto?
4. **Long-only** (reuse) vs **add shorting** (bigger lift)?
