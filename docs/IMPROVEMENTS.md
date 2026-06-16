# Improvements & hardening plan — profitability and safety

> A prioritized roadmap for making the bot more **profitable** (better risk-adjusted
> returns) and **safe to run** (capital, execution, operational, and validation safety).
> Grounded in the current code; each item names the module to change. Compiled
> 2026-06-16 from a four-lens review (strategy / risk / execution-ops / validation),
> cross-checked against current literature. See [PLAN.md](PLAN.md) for project history.

---

## 0. The honest goal — read this first

A long-only, cash-equity timing strategy **reliably fails to beat SPY's total return net
of costs** — that's a well-documented fact, not a flaw in this bot. The validated trend
follower behaves exactly like the time-series-momentum literature predicts: OOS Sharpe
~0.35, strong bear protection (2022 −3.8%), but it doesn't out-return a bull market.

**So the target is: _match_ buy-and-hold with materially lower drawdown — higher Sharpe,
Sortino, and Calmar.** Every idea below is judged against that goal. Chasing "beat the
market" is how you overfit and trade away the one durable edge (drawdown control) you have.

---

## 1. Fix-first — correctness gaps already in the shipped code

These are not enhancements; they are latent defects the review found. **Do these before
any live capital.**

> **Status (2026-06-16):** ✅ **all of 1.1–1.6 done** — catastrophic stop enforced (`run.py`),
> exit idempotency via deterministic-coid sell + APScheduler coalesce (1.2), `reconcile`
> resolves dangling orders against the broker each cycle + at startup (1.3), fill verification
> / partial fills recorded (1.4), retry/backoff + idempotent submit with a lost-response
> fallback (1.5), and the kill switch is now multi-horizon (daily/weekly/monthly, per-horizon
> latch), persisted across restarts, with an independent fast safety-poll and a trade lock
> closing the kill↔submit race. Market-clock gate (§4) is in. ✅ **§4 observability done**:
> alerting (`bot/alerting.py`, Slack/email, log-only when unconfigured), a liveness
> heartbeat (`bot/heartbeat.py`), and an independent **dead-man's-switch watchdog**
> (`scripts/watchdog.py`) that flattens on a stale heartbeat — both batches **reviewed
> adversarially** (high-sev bugs found and fixed each time). ✅ **§5 research gates done**:
> survivorship-free ETF universe + point-in-time CSV loader (`backtests/universe.py`,
> default `--universe etf`; mega-cap basket labelled biased), a `--cost-sweep` breakeven
> readout in the validator, and a mechanical go-live gate (`scripts/go_live_check.py`).
> **Tier 0 is COMPLETE** — the bot is safe to paper-trade on trustworthy numbers.
> (Honest finding: on the ETF universe the trend default still validates — OOS Sharpe
> ~0.44, beats B&H on ~27% of names, edge cost-insensitive through 50 bps/side.)

| # | Gap | Where | Fix | Sev |
|---|---|---|---|---|
| 1.1 | **The stop-loss is fictional.** `stop_loss_pct`/`default_stop()` are used *only* to size the entry; the live loop never compares price to a stop. A losing trade has no downside bound until the next death cross (~1–3/yr) — this is the source of the ~−26% single-symbol drawdown. | [risk/manager.py](../bot/risk/manager.py), [run.py](../bot/run.py) | Add `should_stop_out(entry, price, pct)` (pure, testable); persist entry fill price in the ledger; in `run.py._evaluate_symbol`, for every held symbol exit via `close_position` if breached. Make it a **hard catastrophic stop (8–10%, wider than signal noise)** — disaster insurance, *not* the alpha trailing stop that was correctly reverted. Optionally place a native Alpaca stop/bracket order so it survives downtime. | **High** |
| 1.2 | **Exit path isn't idempotent.** `_enter` checks `ledger.already_submitted(coid)`; `_exit` does not — a re-processed bar can fire `close_position` twice. | [run.py](../bot/run.py) | Mirror the `already_submitted` guard in `_exit`. For true broker-side idempotency on exits, submit a sell order with the deterministic `coid` (keep `close_position` for the kill-switch/flatten path). Set `coalesce=True` + `misfire_grace_time` on the APScheduler job. | Med |
| 1.3 | **`ledger.reconcile` is a no-op** — it echoes broker positions back, never detecting drift. A crash between `record_intent` and `mark_submitted` leaves a dangling `intended` row that `already_submitted` returns False for → possible duplicate on restart. | [state/ledger.py](../bot/state/ledger.py), [run.py](../bot/run.py) | On startup + each cycle, query the broker by `client_order_id` for open `intended`/`submitted` rows, update to real status, and alert on any position mismatch. Add a one-time startup reconcile in `TradingBot.__init__`. | **High** |
| 1.4 | **No fill verification / partial-fill handling.** Submit → `mark_submitted` → move on. Paper (and live) partial-fill ~10% of the time; `running_exposure` and sizing then use the wrong qty. | [run.py](../bot/run.py), [state/ledger.py](../bot/state/ledger.py) | After submit, poll `get_order_by_client_order_id` until terminal; record `filled_qty`/`filled_avg_price`; use `filled_qty` for exposure. (Later replaced by `TradingStream` — §4.) | **High** |
| 1.5 | **No retry/backoff.** Bare `self.client.*` calls; a single 429 (200 req/min limit) or transient error throws out of the cycle — *before* the kill-switch check, which runs after `account()`/`list_positions()`. | [execution/broker.py](../bot/execution/broker.py) | Wrap calls in `_retry()` with exponential backoff + jitter honoring `Retry-After`. **On order-submit timeout, verify by `coid` before resending** (the module docstring already mandates this). Reads are freely retryable. | **High** |
| 1.6 | **Kill switch is in-memory, daily-only, sampled at trade cadence.** A gap can blow past −3% before the next 300 s poll; a crash-restart clears the `_halted` latch and re-arms mid-drawdown; no weekly/monthly limit. | [risk/manager.py](../bot/risk/manager.py), [run.py](../bot/run.py) | Persist `_halted`, `day_start_equity`, HWM, week/month anchors in the ledger and rehydrate on startup; add a separate fast (30–60 s) safety poll job; add `max_weekly_loss_pct`/`max_monthly_loss_pct`. | **High** |

---

## 2. Profitability — the structural levers (in priority order)

The gains here are **structural and sizing-based, not parameter-level**. Resist signal
sophistication (see §6).

| Idea | Why | Where | Impact / Effort |
|---|---|---|---|
| **Widen the universe to 30–100 liquid, sector-diversified names** | The thin per-symbol edge only aggregates into real Sharpe + diversification via many weakly-correlated bets; the 2-symbol default has none. | `BOT_SYMBOLS` in [config.py](../bot/config.py)/`.env` (the validate.py basket is a good seed) | High / **Low** |
| **Volatility-targeted position sizing** (inverse-vol / ATR), replace fixed-fraction | Best-documented Sharpe/drawdown improver; equalizes *risk* per position and auto-shrinks in turbulence. Near-zero overfitting risk (it's sizing, not signal). | `position_size()` in [risk/manager.py](../bot/risk/manager.py); add `target_portfolio_vol` to `RiskConfig`; add an inverse-vol mode to the backtest harness | High / Med |
| **Market-regime gate: only enter longs when SPY > its 200-day SMA** | Faber's classic drawdown reducer — concentrates long risk in the regime where the edge exists. Code already half-supports it (per-symbol, not market). | Portfolio-level check in `run.py.step()` (fetch SPY once/cycle); mirror in [validate.py](../backtests/validate.py) to measure the DD-vs-return tradeoff first | High / **Low** |
| **Fix the mid-trend entry gap** (state-based entry option) | Trend entries fire only on the cross *event*, so a freshly-started mid-uptrend bot sits in cash for a whole trend — hurts return and trade count. | [strategy/trend_momentum.py](../bot/strategy/trend_momentum.py): optional "enter when fast>slow & flat", gated by the SPY regime to avoid buying tops; re-validate | Med / **Low** |
| **Cross-sectional momentum sleeve (rank-and-hold top-N), ensembled with the trend rule** | A genuinely different, partially-uncorrelated return stream; combining two low-correlation positive-expectancy sleeves raises portfolio Sharpe. Also adds *real* trade frequency → fixes thin statistical power honestly. | New `bot/strategy/xsec_momentum.py` (needs a cross-section → portfolio-level entry point, not the per-symbol loop); register in [strategies.py](../backtests/strategies.py); 11-1 month momentum | High / **High** |
| **Adopt Calmar/Sortino + "match-B&H-with-lower-DD" as the explicit objective** | Optimizing Sharpe alone undersells the real edge (drawdown protection) and tempts return-chasing changes that erode it. | `_metrics`/`_aggregate` in [validate.py](../backtests/validate.py): add Calmar/Sortino + "return vs B&H" and "maxDD vs B&H" headlines; define the ship criterion | Med / **Low** |
| **Correlation-aware portfolio sizing** | A wide mega-cap universe is still one big tech-beta bet; the 60% gross cap understates true risk. Diversify *risk*, not capital. | New `bot/risk/portfolio.py` or extend `manager.py`: down-weight correlated candidates (sector buckets or correlation penalty — *not* full mean-variance, which overfits) | Med / **High** |

---

## 3. Capital safety — portfolio-level controls

(§1.1 enforce-stop and §1.6 kill-switch-hardening are the prerequisites; the rest layer on.)

| Idea | Why | Where | Impact / Effort |
|---|---|---|---|
| **Half-Kelly (≤) sizing scalar with a hard fractional cap** | Full Kelly assumes known probabilities; with this thin, uncertain edge even quarter-Kelly is defensible. A multiplier on top of vol targeting. | `RiskConfig.kelly_fraction` (default 0.5, allow 0.25) clamped by `max_position_pct`; estimate edge from `validate.py` stats, not live | Med / Med |
| **Portfolio peak-to-trough drawdown de-risking (tiered throttle)** | The kill switch is binary/daily; nothing graduates exposure down as a slow bleed deepens (the convex "volatility tax"). | `RiskManager.exposure_scalar(equity, hwm)` → multiplier by DD tier (e.g. <10%→1.0, 10–15%→0.5, 15–20%→0.25, >20%→freeze); persist HWM | High / Med |
| **Max-concurrent-positions cap** | Simplest, most robust ruin/correlation control; stops the gross cap being spread across many names that gap together. | `RiskConfig.max_concurrent_positions` (e.g. 5); reject in `evaluate_entry` when held ≥ cap | Med / **Low** |
| **Correlation-aware / sector exposure caps** | AAPL+MSFT default is ~0.65 correlated — two "10%" positions ≈ one 18% bet. | Static sector map + per-sector cap (cheap v1); rolling correlation later (see §2) | Med / **High** |
| **Starting-capital ramp discipline** | Bot runs at full size from bar one on a strategy that doesn't beat B&H and has thin live evidence. | `RiskConfig.capital_fraction` ramp tier (e.g. 0.25 for first N live days); step up only on objective ledger metrics | Med / **Low** |
| **Per-order notional cap + min-liquidity gate** | Market orders on thin names can fill far from `decision.price`; bound fat-finger/illiquidity tails. | `RiskConfig.max_order_notional`; skip symbols below an avg-dollar-volume floor | Med / **Low** |

---

## 4. Execution & operations — reliability

| Idea | Why | Where | Impact / Effort |
|---|---|---|---|
| **Market-clock / calendar gate** | Bot polls and submits 24/7, relying on TIF=DAY to silently drop after-hours orders (brittle, wastes rate-limit). Day-rollover uses wall-clock, not the session calendar (mishandles holidays/half-days). | `broker.clock()`/`is_open()`/`calendar()`; early-return in `run.py.step()` when closed; drive `_maybe_roll_day` off the session date | High / **Low** |
| **Watchdog heartbeat + dead-man's switch** | If the process dies, positions are left with no protection (the stop is only a sizing input). | `run.py` writes a heartbeat each cycle; separate `scripts/watchdog.py` calls `flatten_all()` + critical alert when the heartbeat goes stale (must not share a failure domain) | High / Med |
| **Monitoring + alerting** | A latched kill switch, rejected order, or crash currently produces a log line nobody sees. | JSON event logs in [logging_setup.py](../bot/logging_setup.py); `bot/alerting.py` (Slack webhook / email / Pushover) on kill-switch, reject/expire, repeated 429s, heartbeat loss; a `/healthz` heartbeat | High / Med |
| **Marketable-limit orders to bound slippage** | Market orders have unbounded slippage (esp. on the free IEX feed / gaps); a marketable limit fills near-immediately but caps worst-case price. | `broker.submit_limit` already exists; use it in `_enter` with a `slippage_cap_pct`; keep `close_position` for the flatten/kill path | Med / Med |
| **Wire `TradingStream` for real-time fills** | No real-time order-lifecycle view; complements/replaces REST fill-polling and spares the rate limit. | New `TradingStream` wrapper alongside [data/stream.py](../bot/data/stream.py); update the ledger on each `trade_update`; one connection per endpoint | Med / **High** |
| **Live-money guardrails on secrets** | A live key with `ALPACA_PAPER=true` (or vice-versa) silently hits the wrong endpoint. | Require an explicit `I_UNDERSTAND_THIS_IS_LIVE=true` when `paper=False` + loud banner; redact keys in logs; verify `.env` is git-ignored (it is) | Med / **Low** |
| **Deployment hardening** | A `BlockingScheduler` daemon with no supervisor = crash means permanent downtime with open positions. | `deploy/` systemd unit (`Restart=on-failure`, `EnvironmentFile` 0600, journald) + the watchdog as a *second* unit; or Docker `restart: unless-stopped` + non-root + `HEALTHCHECK` | Med / Med |
| **NTP / clock-skew guard** | `_trading_date()` and the bar-timestamp `coid` depend on a correct local clock (Windows drifts). | Compare local time to `broker.clock().timestamp` at startup; prefer broker time for rollover logic; require host NTP | Low / **Low** |

---

## 5. Validation rigor — so you can trust the numbers

The harness is already better than most (real IS/OOS, min-trades gate, regime breakdown,
causal signals). These close the gaps that systematically inflate confidence.

| Idea | Why | Where | Impact / Effort |
|---|---|---|---|
| **Fix survivorship/selection bias in the basket** | The hardcoded baskets are *today's* mega-cap winners — backtesting a long rule on names selected *because* they 10×'d biases every number (and juices the B&H benchmark). | `--universe` from a point-in-time constituent CSV (or use survivorship-free ETFs as the honest panel; mega-caps as a clearly-labeled "biased" panel) in [validate.py](../backtests/validate.py)/[param_sweep.py](../backtests/param_sweep.py) | High / Med |
| **Realistic costs + a cost-sensitivity sweep** | Flat 5 bps + close fills is optimistic; market orders cross the spread. A point estimate hides the breakeven cost where the edge dies. | Model `half_spread + impact` in the backtest; add a `--cost-bps` sweep printing the breakeven; require the edge to survive ~20 bps round-trip | High / Med |
| **Deflated Sharpe Ratio as the selection criterion** | Picking max-Sharpe over an 18/12-set grid × basket is textbook multiple-testing — the winner's Sharpe is upward-biased. | `backtests/deflated_sharpe.py` (Bailey & López de Prado); gate go-live on DSR ≥ 0.95 using *effective* trial count | High / Med |
| **Quantify statistical power (Sharpe CI + Minimum Track Record Length)** | ~1–3 trades/yr → the Sharpe SE is so large its 95% CI almost certainly includes 0; the `--min-trades=4` floor is ad-hoc. | Add `SE(SR)=sqrt((1+0.5·SR²)/n)` ± CI and MinTRL to `_metrics`; reject configs whose CI lower bound ≤ 0; bootstrap cross-check | High / Med |
| **Combinatorial purged CV (CPCV) + purge/embargo** | One arbitrary IS/OOS cut (2022-01-01) is high-variance and itself a tunable choice; CPCV yields a *distribution* of OOS Sharpe + a probability-of-overfitting. | `backtests/cpcv.py`; purge by `params.min_bars`, embargo ~1–2%; keep single-split `validate.py` as the fast smoke check | High / **High** |
| **Go-live gates (mechanical)** | "Paper-first" has no measurable bar to clear — go-live is a judgment call. | `docs/GO_LIVE_GATES.md` + `scripts/go_live_check.py` (exits non-zero if unmet): min *trades* in paper, paper-vs-backtest tolerance, realized slippage within budget, zero risk-control violations, DSR/CPCV passed | High / Med |
| **Live-vs-backtest divergence monitor** | The ledger logs orders but no equity curve or realized slippage — no way to detect the edge decaying or an execution bug. | Log per-cycle equity + per-fill `fill − decision.price`; `scripts/monitor.py` (or a dashboard panel) alerts on K-period underperformance / slippage / frequency drift | High / Med |
| **Strategy decommission criteria** | The kill switch protects capital intraday but nothing retires a *strategy* whose edge decayed — it can bleed forever. | Pre-agreed death conditions enforced in `monitor.py` (live DD > worst CPCV path, rolling Sharpe < floor for N months, persistent divergence); logged + reversible | Med / **Low** |
| **Pre-registration of each hypothesis** | Nothing records what was hypothesized before touching data → silent HARKing/p-hacking; DSR/CPCV math is only valid if N is counted honestly. | `docs/preregistration/` (rationale, exact rule, full grid, basket, windows, pass threshold) + an append-only experiments log; record killed strategies (stoch_rsi_mfi, trailing stop) | Med / **Low** |
| **Stop suppressing warnings + add a leakage self-test** | Both scripts do `filterwarnings('ignore')`, hiding NaN/divide-by-zero/look-ahead signals. | Scope suppression narrowly; assert no-NaN + post-warmup window + signals∈{−1,0,1}; shift signal +1 bar and confirm performance *drops* (no look-ahead) | Med / **Low** |

---

## 6. What NOT to do (skeptic's list)

- **Don't run live before Tier 0 (§1 + the safety/validity gates).** The stop isn't real yet.
- **Don't chase "beat SPY total return"** — accept match-with-lower-drawdown (§0).
- **Don't go intraday/HF on this stack.** `stoch_rsi_mfi` already proved no edge at 1H/15M; intraday pays far more spread/slippage and needs the unwired streaming stack.
- **Don't add ML signal generation or a factor zoo.** They explode the trial count (overfitting) for retail data/compute and rarely add net edge.
- **Don't trust a single "best-fit" parameter.** Prefer robust plateaus; report the full grid's OOS distribution and a Deflated Sharpe.
- **Don't re-introduce a trailing stop for alpha** — validated worse (cuts winners short). A *catastrophic* stop for operational safety (§1.1) is a different thing.

---

## 7. Recommended sequencing

- **Tier 0 — before any live capital (safety + validity):** §1.1–1.6 fix-first items;
  §4 market-clock gate, monitoring/alerting, watchdog; §5 survivorship fix, cost realism,
  go-live gates. *Goal: the bot can't silently lose money or run on biased numbers.*
- **Tier 1 — profitability, high-impact/low-effort:** §2 widen universe, SPY regime gate,
  vol-targeted sizing, mid-trend entry fix, Calmar objective; §5 DSR + power CIs.
- **Tier 2 — bigger structural bets:** §2 cross-sectional sleeve, correlation-aware sizing;
  §3 drawdown de-risking, Kelly cap; §4 marketable-limit orders, `TradingStream`, deploy;
  §5 CPCV.
- **Tier 3 — ongoing discipline:** §3 ramp discipline; §5 pre-registration, live-vs-backtest
  monitor, decommission rules.

If you do only five things: **(1) enforce a real stop, (2) widen the universe, (3) add the
SPY regime gate, (4) volatility-target the sizing, (5) add monitoring/alerting + a watchdog.**
That converts a fragile single-name bot into a diversified, drawdown-aware, observable one —
the realistic path to "safe to run."
