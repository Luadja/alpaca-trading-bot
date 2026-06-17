"""Bot entry point — one decision cycle wired end to end.

Flow per cycle (matches the architecture diagram):
  data -> strategy (pure signal) -> risk (sizing + gates + kill switch) -> execution
with the SQLite ledger giving idempotency and the broker as source of truth.

For "local for now" this runs a polling loop (default 1 cycle per interval). When you
want event-driven reactions, swap in bot/data/stream.py. Streaming requires a
persistent always-on process (cron/serverless can't keep the websocket alive).

Usage:
    python -m bot.run --once          # single cycle, then exit (good for testing)
    python -m bot.run --interval 300  # poll every 300s
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import threading
import time
from datetime import date, datetime

from alpaca.trading.enums import OrderSide

from bot.alerting import alerter_from_settings
from bot.config import Settings, load_settings
from bot.data.historical import HistoricalData, parse_timeframe
from bot.execution.broker import Broker
from bot.heartbeat import write_heartbeat
from bot.logging_setup import setup_logging
from bot.models import SignalDecision, SignalType
from bot.risk import RiskConfig, RiskManager
from bot.state import Ledger
from bot.strategy import TrendMomentumParams, TrendMomentumStrategy, make_strategy

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - falls back if tzdata is missing
    _ET = None


def _trading_date() -> date:
    return (datetime.now(_ET) if _ET else datetime.utcnow()).date()


def _periods_per_year(timeframe: str) -> float:
    """Bars per year for the given timeframe, to annualize volatility correctly."""
    m = re.fullmatch(r"(\d+)\s*(min|hour|day|week|month)s?", timeframe.strip().lower())
    if not m:
        return 252.0
    amount, unit = int(m.group(1)), m.group(2)
    per = {"min": 252 * 390, "hour": 252 * 6.5, "day": 252, "week": 52, "month": 12}[unit]
    return per / amount


def _avg_price(order) -> float | None:
    """Parse an order's filled_avg_price (Optional[str]); None for unset or '0'."""
    raw = order.filled_avg_price
    if not raw:
        return None
    value = float(raw)
    return value if value > 0 else None


def _order_side(order) -> str:
    """Lower-cased order side ('buy'/'sell'), tolerating enum or raw string."""
    side = order.side
    return (side.value if hasattr(side, "value") else str(side)).lower()


# Terminal states in which an order filled ZERO shares — the logical decision was NOT carried
# out, so (unlike a 'filled' terminal) it is eligible for a fresh-coid retry while still wanted.
_TERMINAL_UNFILLED = ("rejected", "canceled", "expired", "done_for_day")


class TradingBot:
    # Consecutive heartbeat-write failures at which we escalate warning -> critical: a
    # persistent failure means the watchdog's dead-man's-switch will soon flatten on a stale
    # heartbeat, so it's no longer a transient blip.
    _HEARTBEAT_FAIL_ESCALATE = 3

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.log = setup_logging()
        self._heartbeat_failures = 0  # consecutive write failures, for escalation
        # Serializes the kill-switch / risk-state / order-submit critical sections so the
        # fast safety_poll thread can't flatten between an entry's gate-check and its submit.
        self._lock = threading.RLock()
        self.broker = Broker(settings)
        self.data = HistoricalData(settings)
        self.strategy = self._build_strategy()
        self.ledger = Ledger(settings.ledger_path)
        self.alerter = alerter_from_settings(settings)
        self.timeframe = parse_timeframe(settings.timeframe)
        self._vol_annualization = _periods_per_year(settings.timeframe) ** 0.5
        self._risk_config = RiskConfig(
            use_vol_targeting=settings.use_vol_targeting,
            vol_target_pct=settings.vol_target_pct,
        )

        account = self.broker.account()
        self.risk = self._init_risk(account.equity)  # rehydrates kill-switch state if persisted
        # If we baselined the daily-loss anchor while the market was CLOSED (pre-market /
        # weekend restart), it's measured from a stale equity, not the session open. Re-anchor
        # at the first open-market read (see _reanchor_if_pending).
        try:
            self._anchor_needs_reopen = not self.broker.is_market_open()
        except Exception:
            self._anchor_needs_reopen = False
        self._reconcile_pending()  # resolve any orders left dangling by a prior crash
        self.log.info(
            "Started. equity=%.2f paper=%s strategy=%s halted=%s",
            account.equity, settings.paper, settings.strategy, self.risk.halted,
        )
        if self.risk.halted:
            self.alerter.notify("critical", "started with kill switch HALTED", "rehydrated from prior session")
        self._write_heartbeat(account.equity)  # liveness from the first moment

    def step(self) -> None:
        # Liveness first, so the heartbeat stays fresh even on the early returns below
        # (closed market, kill switch) and across the market-open boundary.
        self._write_heartbeat()
        # Don't poll/submit into a closed market (TIF=DAY would silently drop orders).
        if not self.broker.is_market_open():
            self.log.info("Market closed — skipping cycle.")
            return

        account = self.broker.account()
        self._maybe_roll_day(account.equity)
        self._reanchor_if_pending(account.equity)  # fix a pre-market-start daily anchor
        self._reconcile_pending()  # promptly resolve any slow/dangling fills

        positions_raw = self.broker.list_positions()
        positions = {p.symbol: float(p.qty) for p in positions_raw}

        # Kill switch: check loss limits before anything else, atomically with flatten so a
        # concurrent entry can't slip through (the lock also guards risk-state + persistence).
        with self._lock:
            halted = self.risk.update(account.equity)
            self._persist_risk()
            if halted:
                self.broker.flatten_all()
        if halted:
            reason = self.risk.halt_reason(account.equity)
            self.log.warning("KILL SWITCH active (%s) — flattened, no new entries.", reason)
            self.alerter.notify("critical", "kill switch tripped — account flattened", reason)
            self._write_heartbeat(account.equity)
            return

        # Catastrophic stop: flatten any managed position past the hard stop, before signals.
        # skip = don't act further on these this cycle; flat = CONFIRMED flattened (fill landed).
        skip, flat = self._enforce_stops(positions_raw, positions)

        # Per-symbol market value; tally exposure as we place orders so the exposure gate
        # bounds TOTAL exposure across same-cycle entries (exit frees the seeded value). A
        # stop that did NOT confirm flat stays in the tally (it's still real exposure).
        position_mv = {
            p.symbol: (abs(float(p.market_value)) if p.market_value else 0.0)
            for p in positions_raw if p.symbol not in flat
        }
        running_exposure = sum(position_mv.values())

        # Market-regime gate (Faber): only allow NEW longs when the broad market is up.
        regime_ok = self._market_regime_ok()
        if not regime_ok:
            self.log.info("Market regime DOWN (%s < %d-SMA) — exits only, no new entries.",
                          self.settings.market_regime_symbol, self.settings.market_regime_sma)

        for symbol in self.settings.symbols:
            if symbol in skip:
                continue  # stop fired (or a stop SELL is working) — don't re-act this cycle
            try:
                running_exposure = self._evaluate_symbol(
                    symbol, account.equity, positions, running_exposure, regime_ok,
                    position_mv, account.buying_power,
                )
            except Exception as exc:  # one bad symbol shouldn't take down the loop
                self.log.exception("error evaluating %s", symbol)
                self.alerter.notify("warning", f"error evaluating {symbol}", str(exc))

        self._write_heartbeat(account.equity)

    def _enforce_stops(self, positions_raw, positions: dict) -> tuple[set, set]:
        """Hard catastrophic stop on managed positions, using the broker's own entry and
        current prices. Returns (skip, flat): ``skip`` = symbols not to act on further this
        cycle (a stop fired or a stop SELL is already working); ``flat`` = symbols CONFIRMED
        flattened (their fill landed). A stop that did NOT fill is in ``skip`` but NOT ``flat``,
        so it stays in the exposure tally and is re-attempted next cycle — a failed safety
        flatten must never look like a success."""
        skip: set = set()
        flat: set = set()
        managed = set(self.settings.symbols)
        for p in positions_raw:
            if p.symbol not in managed or float(p.qty) <= 0:
                continue
            # current_price / avg_entry_price are Optional[str]; a freshly-halted name can
            # report None (-> float(None) would crash the whole cycle) or 0 (-> false stop).
            entry = float(p.avg_entry_price) if p.avg_entry_price else 0.0
            price = float(p.current_price) if p.current_price else 0.0
            if price <= 0:
                continue  # no valid mark — can't judge the stop; re-check next cycle
            if not self.risk.should_stop_out(entry, price):
                continue
            pct = (price / entry - 1) * 100 if entry else 0.0
            self.log.warning(
                "%s: CATASTROPHIC STOP (entry %.2f, price %.2f, %.1f%%) — flattening",
                p.symbol, entry, price, pct,
            )
            self.alerter.notify("warning", f"catastrophic stop: {p.symbol}", f"{pct:.1f}% from entry")
            filled, status = self._flatten_catastrophic(p.symbol, float(p.qty))
            skip.add(p.symbol)  # don't let the rest of the cycle trade a name we're flattening
            if status == "filled" or filled >= float(p.qty):
                flat.add(p.symbol)
                positions[p.symbol] = 0.0
            else:
                self.log.error(
                    "%s: catastrophic flatten INCOMPLETE (status=%s filled=%s) — still exposed",
                    p.symbol, status, filled,
                )
                self.alerter.notify(
                    "critical", f"catastrophic flatten incomplete: {p.symbol}",
                    f"status={status} filled={filled} — position still open past the hard stop",
                )
        return skip, flat

    def _flatten_catastrophic(self, symbol: str, qty: float) -> tuple[float, str | None]:
        """Flatten a position past the HARD stop, through the ledger (audit + fill verify +
        crash recovery). Returns (filled, status).

        NEVER stacks duplicate SELLs: if a non-terminal SELL already covers the outstanding
        long, let it work — a unique-coid resend each cycle (while the prior stop rests during
        an LULD halt) would oversell a crashing long into a SHORT, breaking long-only. It still
        resends after a genuinely REJECTED stop, since a rejected order is terminal and no
        longer 'working'. Does NOT hold self._lock across the network submit (the Ledger
        serializes its own writes); holding it here would starve the fast safety poll during a
        multi-symbol crash."""
        try:
            working = sum(
                float(o.qty or 0) for o in self.broker.open_orders()
                if o.symbol == symbol and _order_side(o) == "sell"
            )
        except Exception:
            working = 0.0  # can't read open orders -> fall through and submit (fail-safe)
        if working >= qty:
            self.log.warning(
                "%s: catastrophic SELL already working (%.0f sh resting) — not restacking",
                symbol, working,
            )
            return 0.0, "working"
        remaining = qty - working
        coid = f"bot-catstop-{symbol}-{time.time_ns()}"
        self.ledger.record_intent(coid, symbol, "sell", remaining, "catastrophic stop")
        order = self.broker.submit_market(symbol, remaining, OrderSide.SELL, client_order_id=coid)
        self.ledger.mark_submitted(coid, str(order.id))
        return self._await_fill(coid)

    def _evaluate_symbol(self, symbol, equity, positions, running_exposure, regime_ok=True,
                         position_mv=None, buying_power=None) -> float:
        # ~550 daily bars: enough that the 200-SMA is valid well before recent crosses
        # (a short window can miss the last golden/death cross). Reduce for sub-daily.
        df = self.data.get_bars(symbol, self.timeframe, lookback_days=800, use_cache=False)
        required = max(self.settings.warmup_bars, self.strategy.params.min_bars)
        if df.empty or len(df) < required:
            self.log.warning(
                "%s: insufficient history %d/%d bars — skipping (the symbol will not trade "
                "until it has enough; the trend-filter SMA needs the full window)",
                symbol, len(df), required,
            )
            return running_exposure

        bar_ts = df.index[-1]
        decision = self.strategy.generate(df, symbol)
        held = positions.get(symbol, 0.0)
        self.log.info("%s: %s (%s)", symbol, decision.signal.value, decision.reason)

        if decision.signal is SignalType.ENTER_LONG and held == 0:
            if not regime_ok:
                self.log.info("%s: entry blocked — market regime down", symbol)
                return running_exposure
            sigma = float(df["close"].pct_change().std() * self._vol_annualization)  # annualized
            # Size + gate on a LIVE price, not decision.price (the PRIOR session's close on a
            # daily timeframe), so the per-position / total-exposure caps match what the order
            # actually fills at. Fall back to the bar close only when no live price is available.
            ref = self.data.latest_price(symbol) if self.settings.use_marketable_limit else None
            price = ref if ref else decision.price
            risk = self.risk.evaluate_entry(equity, price, running_exposure, sigma=sigma)
            if not risk.approved:
                self.log.info("%s: entry blocked — %s", symbol, risk.reason)
                return running_exposure
            # Hard buying-power backstop (the exposure cap is vs equity; a cash account's BP
            # can be lower once mostly invested).
            order_value = risk.qty * price
            if buying_power is not None and order_value > buying_power:
                self.log.warning("%s: entry blocked — order %.0f exceeds buying power %.0f",
                                  symbol, order_value, buying_power)
                return running_exposure
            running_exposure += self._enter(symbol, risk.qty, decision, bar_ts, ref, price)

        elif decision.signal is SignalType.EXIT_LONG and held > 0:
            self._exit(symbol, held, decision, bar_ts)
            # Free the value we SEEDED for this symbol (market_value basis), not a bar-close
            # estimate, so the tally returns to its true post-exit level.
            running_exposure -= (position_mv or {}).get(symbol, held * decision.price)

        return running_exposure

    def _enter(self, symbol, qty, decision: SignalDecision, bar_ts, ref, price) -> float:
        """Submit a buy, verify the fill, and return the COMMITTED notional (qty*price, with
        price the LIVE price used for sizing) for the exposure gate — counts even if a
        marketable limit rests unfilled, so same-cycle entries can't over-allocate. The gate
        re-check + submit are atomic under the lock so a concurrent kill-switch flatten can't
        interleave; the fill poll runs outside it. ``ref`` is the live price to anchor the
        marketable limit (None -> market order)."""
        coid, retry = self._retry_coid(symbol, "buy", bar_ts)
        with self._lock:
            if self.risk.halted:
                return 0.0  # kill switch tripped between the gate check and here
            if not retry and self.ledger.already_submitted(coid):
                return 0.0  # already acted on this exact bar — idempotent
            self.ledger.record_intent(coid, symbol, "buy", qty, decision.reason)
            if ref:
                limit = round(ref * (1 + self.settings.slippage_cap_pct), 2)
                order = self.broker.submit_limit(symbol, qty, OrderSide.BUY, limit, client_order_id=coid)
            else:
                order = self.broker.submit_market(symbol, qty, OrderSide.BUY, client_order_id=coid)
            self.ledger.mark_submitted(coid, str(order.id))
        filled, status = self._await_fill(coid)
        self.log.info("%s: BUY %s/%s filled (coid=%s)", symbol, filled, qty, coid)
        # Count committed notional for the exposure gate. A working/resting limit or any
        # (partial) fill counts (conservative). But an order that reached a terminal state
        # with ZERO fill (rejected/canceled/expired) — or that the broker has no record of —
        # commits nothing, so don't let a dead order block legitimate same-cycle entries.
        if filled == 0 and status in (None, *_TERMINAL_UNFILLED):
            return 0.0
        return qty * price

    def _exit(self, symbol, held, decision: SignalDecision, bar_ts) -> None:
        """Exit via a deterministic-coid SELL (not close_position) so it dedupes and
        reconciles exactly like an entry — a retried/crashed exit can't double-sell. If a
        prior exit for this bar terminated UNFILLED (rejected/canceled/expired), retry with a
        fresh coid so a wanted liquidation isn't abandoned for the rest of the day; the
        caller's held>0 gate stops a double-sell once one attempt fills."""
        coid, retry = self._retry_coid(symbol, "sell", bar_ts)
        with self._lock:
            if not retry and self.ledger.already_submitted(coid):
                return  # idempotent: don't sell twice for the same bar
            self.ledger.record_intent(coid, symbol, "sell", held, decision.reason)
            order = self.broker.submit_market(symbol, held, OrderSide.SELL, client_order_id=coid)
            self.ledger.mark_submitted(coid, str(order.id))
        self._await_fill(coid)
        self.log.info("%s: EXIT sell qty=%s (coid=%s)", symbol, held, coid)

    def _await_fill(self, coid: str, tries: int = 4, pause: float = 1.0) -> tuple[float, str | None]:
        """Poll the order to terminal state, recording fills. Returns (filled qty, last status)
        — status is None if the broker has no record, or the last non-terminal status if it
        never settled (a working limit). Replaced by TradingStream later; REST is the fallback."""
        terminal = {"filled", "canceled", "rejected", "expired", "done_for_day"}
        filled = 0.0
        status: str | None = None
        for attempt in range(tries):
            order = self.broker.get_order(coid)
            if order is None:
                return 0.0, None
            status = order.status.value if hasattr(order.status, "value") else str(order.status)
            filled = float(order.filled_qty or 0)
            self.ledger.record_fill(coid, filled, _avg_price(order), status)
            if status in terminal:
                if status != "filled":
                    self.log.warning("%s: order %s — filled %s", coid, status, filled)
                    self.alerter.notify("warning", f"order {status}", f"{coid} filled {filled}")
                return filled, status
            if attempt < tries - 1:
                time.sleep(pause)
        return filled, status

    def _maybe_roll_day(self, equity: float) -> None:
        today = _trading_date()
        if today == self._session_date:  # fast path, no lock
            return
        # Double-checked locking: step() and safety_poll() run on separate threads and BOTH
        # call this, so the check+act must be atomic or they'd double-roll (re-clearing a
        # just-tripped latch / re-anchoring to a second, lower equity read).
        with self._lock:
            prev = self._session_date
            if today == prev:
                return  # another thread already rolled this session
            if equity <= 0:
                # reset_* raise on equity<=0; a transient bad read must not propagate out of
                # step() and wedge the loop. Don't advance _session_date — retry next cycle.
                self.log.error("day roll skipped: non-positive equity %.2f — retrying next cycle", equity)
                return
            if today.isocalendar()[:2] != prev.isocalendar()[:2]:
                self.risk.reset_week(equity)
            if (today.year, today.month) != (prev.year, prev.month):
                self.risk.reset_month(equity)
            self.risk.reset_day(equity)
            self._session_date = today
            self._persist_risk()
            self.log.info("New trading day %s — anchors rolled; halted=%s", today, self.risk.halted)

    def _reanchor_if_pending(self, equity: float) -> None:
        """Re-anchor the day-start equity to the first OPEN-market read when the baseline was
        captured at a pre-market/weekend process start, so the daily-loss threshold is measured
        from the session's real starting equity. Touches only the DAY anchor (re-anchoring the
        week mid-week would discard the week's P&L) and does NOT clear a rehydrated halt latch."""
        if not self._anchor_needs_reopen or equity <= 0:
            return
        with self._lock:
            if not self._anchor_needs_reopen:
                return
            self.risk.day_start_equity = equity
            self._anchor_needs_reopen = False
            self._persist_risk()
        self.log.info("Re-anchored day-start equity to first open-market read %.2f", equity)

    def _build_strategy(self):
        """Construct the configured strategy, injecting settings-driven params."""
        if self.settings.strategy == "trend_momentum":
            return TrendMomentumStrategy(
                TrendMomentumParams(enter_on_regime=self.settings.trend_enter_on_regime)
            )
        return make_strategy(self.settings.strategy)

    def _market_regime_ok(self) -> bool:
        """True if the broad market is in an uptrend (price > long SMA). Gates NEW longs
        only; fail-open on error/short history (the kill switch is the real safety net)."""
        if not self.settings.use_market_regime_filter:
            return True
        try:
            df = self.data.get_bars(
                self.settings.market_regime_symbol, parse_timeframe("1Day"),
                lookback_days=500, use_cache=False,
            )
            sma_len = self.settings.market_regime_sma
            if len(df) < sma_len:
                return True
            sma = df["close"].rolling(sma_len).mean().iloc[-1]
            return float(df["close"].iloc[-1]) > float(sma)
        except Exception:
            self.log.exception("market-regime check failed — allowing entries")
            return True

    # --- risk persistence + reconcile + safety poll ------------------------
    def _init_risk(self, equity: float) -> RiskManager:
        """Construct the RiskManager, rehydrating persisted kill-switch state and rolling
        any day/week/month boundary that passed while the bot was down."""
        self._session_date = _trading_date()
        saved = self.ledger.get_state("risk")
        if not saved:
            return RiskManager(self._risk_config, equity)
        try:
            s = json.loads(saved)
            legacy = s.get("halted", False)  # tolerate a pre-per-horizon snapshot
            rm = RiskManager(
                self._risk_config,
                s["day_start_equity"],
                week_start_equity=s["week_start_equity"],
                month_start_equity=s["month_start_equity"],
                high_water_mark=s["high_water_mark"],
                halted_day=s.get("halted_day", legacy),
                halted_week=s.get("halted_week", legacy),
                halted_month=s.get("halted_month", legacy),
            )
            saved_date = date.fromisoformat(s["date"])
            if saved_date.isocalendar()[:2] != self._session_date.isocalendar()[:2]:
                rm.reset_week(equity)
            if (saved_date.year, saved_date.month) != (self._session_date.year, self._session_date.month):
                rm.reset_month(equity)
            if saved_date != self._session_date:
                rm.reset_day(equity)
            if rm.halted:
                self.log.warning("Rehydrated risk: kill switch is HALTED from a prior session.")
            return rm
        except Exception:
            self.log.exception("could not rehydrate risk state — starting fresh")
            return RiskManager(self._risk_config, equity)

    def _persist_risk(self) -> None:
        snap = self.risk.snapshot()
        snap["date"] = self._session_date.isoformat()
        self.ledger.set_state("risk", json.dumps(snap))

    def _reconcile_pending(self) -> None:
        """Resolve ledger orders left in a non-terminal state by a crash against the broker."""
        pending = self.ledger.pending_orders()
        for row in pending:
            coid = row["client_order_id"]
            try:
                order = self.broker.get_order(coid)
            except Exception:
                self.log.exception("reconcile: could not fetch %s", coid)
                continue
            if order is None:
                self.ledger.mark_status(coid, "missing")  # never landed — safe to retry later
            else:
                status = order.status.value if hasattr(order.status, "value") else str(order.status)
                self.ledger.record_fill(coid, float(order.filled_qty or 0), _avg_price(order), status)
        if pending:
            self.log.info("reconciled %d pending order(s) against the broker", len(pending))

    def safety_poll(self) -> None:
        """Fast, independent equity check so the kill switch trips between trade cycles.
        The update + flatten run under the lock, mutually exclusive with entry submission."""
        try:
            self._write_heartbeat()  # refresh liveness even if step() is slow/wedged
            if not self.broker.is_market_open():
                return
            equity = self.broker.account().equity  # network read, outside the lock
            # Roll day/week/month anchors first — safety_poll usually fires before step() on a
            # new session, so without this it would measure loss vs the PRIOR period's baseline.
            self._maybe_roll_day(equity)
            self._reanchor_if_pending(equity)
            with self._lock:
                halted = self.risk.update(equity)
                self._persist_risk()
                if halted:
                    self.broker.flatten_all()
            if halted:
                reason = self.risk.halt_reason(equity)
                self.log.warning("SAFETY POLL kill switch (%s) — flattened", reason)
                self.alerter.notify("critical", "kill switch tripped (safety poll) — flattened", reason)
            self._write_heartbeat(equity)
        except Exception:
            self.log.exception("safety poll error")

    def _write_heartbeat(self, equity: float | None = None) -> None:
        payload = {"halted": self.risk.halted, "strategy": self.settings.strategy}
        if equity is not None:
            payload["equity"] = equity
        try:
            write_heartbeat(self.settings.heartbeat_path, payload)  # IO outside the lock
        except Exception as exc:  # heartbeat write must never break the cycle
            # Counter is read-modify-written from both the step() and safety_poll() threads, so
            # guard it under the lock (RLock; += is not atomic) to keep the escalation accurate.
            with self._lock:
                self._heartbeat_failures += 1
                n = self._heartbeat_failures
            self.log.exception("heartbeat write failed (%d consecutive)", n)
            # First blips are warnings; a persistent failure makes the watchdog flatten on a
            # stale heartbeat, so escalate to critical once it crosses the threshold.
            sev = "critical" if n >= self._HEARTBEAT_FAIL_ESCALATE else "warning"
            self.alerter.notify(sev, "heartbeat write failed", f"{n} consecutive: {exc}")
        else:
            with self._lock:
                recovered = self._heartbeat_failures
                self._heartbeat_failures = 0
            if recovered:
                self.log.info("heartbeat write recovered after %d failure(s)", recovered)

    @staticmethod
    def _coid(symbol: str, side: str, bar_ts) -> str:
        """Deterministic client_order_id: same symbol+side+bar => same id, so a retry
        of the same logical decision cannot create a duplicate order."""
        digest = hashlib.sha1(f"{symbol}|{side}|{bar_ts}".encode()).hexdigest()[:24]
        return f"bot-{digest}"

    def _retry_coid(self, symbol: str, side: str, bar_ts) -> tuple[str, bool]:
        """Return (coid, is_retry). Normally the deterministic per-bar coid (idempotent). But
        on a daily timeframe that coid is stable ALL session, so if a prior attempt for this
        exact decision TERMINATED UNFILLED (rejected/canceled/expired/done_for_day) it is stuck
        terminal-and-non-resubmittable and the wanted order would be abandoned for the rest of
        the day. In that case return a FRESH retry id. Double-execution is prevented by the
        caller's live-position/signal gate (exit needs held>0, entry needs held==0), so once a
        retry fills the decision is no longer re-issued."""
        coid = self._coid(symbol, side, bar_ts)
        prior = self.ledger.order_state(coid)
        if prior and prior["status"] in _TERMINAL_UNFILLED and prior["filled_qty"] == 0:
            return f"{coid}-r{time.time_ns()}", True
        return coid, False


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Alpaca trading bot")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    parser.add_argument("--interval", type=int, default=300, help="trade-cycle interval (seconds)")
    parser.add_argument("--safety-interval", type=int, default=60, help="kill-switch poll (seconds)")
    args = parser.parse_args()

    settings = load_settings()
    bot = TradingBot(settings)

    if args.once:
        bot.step()
        return

    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()
    scheduler.add_job(
        bot.step, "interval", seconds=args.interval, id="trade_cycle",
        max_instances=1, coalesce=True, misfire_grace_time=30,
    )
    # Independent fast kill-switch poll so a gap can't blow past the loss limit between cycles.
    scheduler.add_job(
        bot.safety_poll, "interval", seconds=args.safety_interval, id="safety_poll",
        max_instances=1, coalesce=True, misfire_grace_time=15,
    )
    logging.getLogger("bot").info(
        "Polling: trade every %ds, safety every %ds. Ctrl+C to stop.",
        args.interval, args.safety_interval,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
