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
from bot.strategy import make_strategy

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - falls back if tzdata is missing
    _ET = None


def _trading_date() -> date:
    return (datetime.now(_ET) if _ET else datetime.utcnow()).date()


def _avg_price(order) -> float | None:
    """Parse an order's filled_avg_price (Optional[str]); None for unset or '0'."""
    raw = order.filled_avg_price
    if not raw:
        return None
    value = float(raw)
    return value if value > 0 else None


class TradingBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.log = setup_logging()
        # Serializes the kill-switch / risk-state / order-submit critical sections so the
        # fast safety_poll thread can't flatten between an entry's gate-check and its submit.
        self._lock = threading.RLock()
        self.broker = Broker(settings)
        self.data = HistoricalData(settings)
        self.strategy = make_strategy(settings.strategy)
        self.ledger = Ledger(settings.ledger_path)
        self.alerter = alerter_from_settings(settings)
        self.timeframe = parse_timeframe(settings.timeframe)

        account = self.broker.account()
        self.risk = self._init_risk(account.equity)  # rehydrates kill-switch state if persisted
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
            pnl = self.risk.daily_pnl_pct(account.equity) * 100
            self.log.warning("KILL SWITCH active (daily P&L %.2f%%) — flattened, no new entries.", pnl)
            self.alerter.notify("critical", "kill switch tripped — account flattened", f"daily P&L {pnl:.2f}%")
            self._write_heartbeat(account.equity)
            return

        # Catastrophic stop: flatten any managed position past the hard stop, before signals.
        stopped = self._enforce_stops(positions_raw, positions)

        # Use broker-reported market values; tally exposure as we place orders so the
        # exposure gate actually bounds TOTAL exposure across same-cycle entries.
        running_exposure = sum(
            abs(float(p.market_value)) for p in positions_raw if p.symbol not in stopped
        )

        for symbol in self.settings.symbols:
            if symbol in stopped:
                continue  # just flattened on the stop — don't re-act this cycle
            try:
                running_exposure = self._evaluate_symbol(
                    symbol, account.equity, positions, running_exposure
                )
            except Exception as exc:  # one bad symbol shouldn't take down the loop
                self.log.exception("error evaluating %s", symbol)
                self.alerter.notify("warning", f"error evaluating {symbol}", str(exc))

        self._write_heartbeat(account.equity)

    def _enforce_stops(self, positions_raw, positions: dict) -> set:
        """Hard catastrophic stop on managed positions, using the broker's own entry and
        current prices. Returns the set of symbols flattened this cycle."""
        stopped: set = set()
        managed = set(self.settings.symbols)
        for p in positions_raw:
            if p.symbol not in managed or float(p.qty) <= 0:
                continue
            entry, price = float(p.avg_entry_price), float(p.current_price)
            if self.risk.should_stop_out(entry, price):
                pct = (price / entry - 1) * 100 if entry else 0.0
                self.log.warning(
                    "%s: CATASTROPHIC STOP (entry %.2f, price %.2f, %.1f%%) — flattening",
                    p.symbol, entry, price, pct,
                )
                self.alerter.notify("warning", f"catastrophic stop: {p.symbol}", f"{pct:.1f}% from entry")
                self.broker.close_position(p.symbol)
                stopped.add(p.symbol)
                positions[p.symbol] = 0.0
        return stopped

    def _evaluate_symbol(self, symbol, equity, positions, running_exposure) -> float:
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
            risk = self.risk.evaluate_entry(equity, decision.price, running_exposure)
            if not risk.approved:
                self.log.info("%s: entry blocked — %s", symbol, risk.reason)
                return running_exposure
            running_exposure += self._enter(symbol, risk.qty, decision, bar_ts)

        elif decision.signal is SignalType.EXIT_LONG and held > 0:
            self._exit(symbol, held, decision, bar_ts)
            running_exposure -= held * decision.price

        return running_exposure

    def _enter(self, symbol, qty, decision: SignalDecision, bar_ts) -> float:
        """Submit a buy, verify the fill, and return the FILLED notional added to exposure.
        The gate re-check + submit are atomic under the lock so a concurrent kill-switch
        flatten can't interleave; the (potentially slow) fill poll runs outside the lock."""
        coid = self._coid(symbol, "buy", bar_ts)
        with self._lock:
            if self.risk.halted:
                return 0.0  # kill switch tripped between the gate check and here
            if self.ledger.already_submitted(coid):
                return 0.0  # already acted on this exact bar — idempotent
            self.ledger.record_intent(coid, symbol, "buy", qty, decision.reason)
            order = self.broker.submit_market(symbol, qty, OrderSide.BUY, client_order_id=coid)
            self.ledger.mark_submitted(coid, str(order.id))
        filled = self._await_fill(coid)
        self.log.info("%s: BUY %s/%s filled (coid=%s)", symbol, filled, qty, coid)
        return filled * decision.price

    def _exit(self, symbol, held, decision: SignalDecision, bar_ts) -> None:
        """Exit via a deterministic-coid SELL (not close_position) so it dedupes and
        reconciles exactly like an entry — a retried/crashed exit can't double-sell."""
        coid = self._coid(symbol, "sell", bar_ts)
        with self._lock:
            if self.ledger.already_submitted(coid):
                return  # idempotent: don't sell twice for the same bar
            self.ledger.record_intent(coid, symbol, "sell", held, decision.reason)
            order = self.broker.submit_market(symbol, held, OrderSide.SELL, client_order_id=coid)
            self.ledger.mark_submitted(coid, str(order.id))
        self._await_fill(coid)
        self.log.info("%s: EXIT sell qty=%s (coid=%s)", symbol, held, coid)

    def _await_fill(self, coid: str, tries: int = 4, pause: float = 1.0) -> float:
        """Poll the order to terminal state, recording fills. Returns filled qty (may be
        partial). Replaced by TradingStream later; REST polling is the fallback."""
        terminal = {"filled", "canceled", "rejected", "expired", "done_for_day"}
        filled = 0.0
        for attempt in range(tries):
            order = self.broker.get_order(coid)
            if order is None:
                return 0.0
            status = order.status.value if hasattr(order.status, "value") else str(order.status)
            filled = float(order.filled_qty or 0)
            self.ledger.record_fill(coid, filled, _avg_price(order), status)
            if status in terminal:
                if status != "filled":
                    self.log.warning("%s: order %s — filled %s", coid, status, filled)
                    self.alerter.notify("warning", f"order {status}", f"{coid} filled {filled}")
                return filled
            if attempt < tries - 1:
                time.sleep(pause)
        return filled

    def _maybe_roll_day(self, equity: float) -> None:
        today = _trading_date()
        if today == self._session_date:
            return
        prev = self._session_date
        # Under the lock so a concurrent safety_poll can't snapshot a half-rolled state.
        # Per-horizon latches make the reset order irrelevant.
        with self._lock:
            if today.isocalendar()[:2] != prev.isocalendar()[:2]:
                self.risk.reset_week(equity)
            if (today.year, today.month) != (prev.year, prev.month):
                self.risk.reset_month(equity)
            self.risk.reset_day(equity)
            self._session_date = today
            self._persist_risk()
        self.log.info("New trading day %s — anchors rolled; halted=%s", today, self.risk.halted)

    # --- risk persistence + reconcile + safety poll ------------------------
    def _init_risk(self, equity: float) -> RiskManager:
        """Construct the RiskManager, rehydrating persisted kill-switch state and rolling
        any day/week/month boundary that passed while the bot was down."""
        self._session_date = _trading_date()
        saved = self.ledger.get_state("risk")
        if not saved:
            return RiskManager(RiskConfig(), equity)
        try:
            s = json.loads(saved)
            legacy = s.get("halted", False)  # tolerate a pre-per-horizon snapshot
            rm = RiskManager(
                RiskConfig(),
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
            return RiskManager(RiskConfig(), equity)

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
            with self._lock:
                halted = self.risk.update(equity)
                self._persist_risk()
                if halted:
                    self.broker.flatten_all()
            if halted:
                pnl = self.risk.daily_pnl_pct(equity) * 100
                self.log.warning("SAFETY POLL kill switch (daily P&L %.2f%%) — flattened", pnl)
                self.alerter.notify("critical", "kill switch tripped (safety poll) — flattened", f"daily P&L {pnl:.2f}%")
            self._write_heartbeat(equity)
        except Exception:
            self.log.exception("safety poll error")

    def _write_heartbeat(self, equity: float | None = None) -> None:
        payload = {"halted": self.risk.halted, "strategy": self.settings.strategy}
        if equity is not None:
            payload["equity"] = equity
        try:
            write_heartbeat(self.settings.heartbeat_path, payload)
        except Exception as exc:  # heartbeat write must never break the cycle
            self.log.exception("heartbeat write failed")
            # A persistent write failure makes the watchdog flatten; surface the root cause.
            self.alerter.notify("warning", "heartbeat write failed", str(exc))

    @staticmethod
    def _coid(symbol: str, side: str, bar_ts) -> str:
        """Deterministic client_order_id: same symbol+side+bar => same id, so a retry
        of the same logical decision cannot create a duplicate order."""
        digest = hashlib.sha1(f"{symbol}|{side}|{bar_ts}".encode()).hexdigest()[:24]
        return f"bot-{digest}"


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
