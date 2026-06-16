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
import logging
from datetime import date, datetime

from alpaca.trading.enums import OrderSide

from bot.config import Settings, load_settings
from bot.data.historical import HistoricalData, parse_timeframe
from bot.execution.broker import Broker
from bot.logging_setup import setup_logging
from bot.models import SignalDecision, SignalType
from bot.risk import RiskConfig, RiskManager
from bot.state import Ledger
from bot.strategy import StochRsiMfiParams, StochRsiMfiStrategy

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - falls back if tzdata is missing
    _ET = None


def _trading_date() -> date:
    return (datetime.now(_ET) if _ET else datetime.utcnow()).date()


class TradingBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.log = setup_logging()
        self.broker = Broker(settings)
        self.data = HistoricalData(settings)
        self.strategy = StochRsiMfiStrategy(StochRsiMfiParams())
        self.ledger = Ledger(settings.ledger_path)
        self.timeframe = parse_timeframe(settings.timeframe)

        account = self.broker.account()
        self.risk = RiskManager(RiskConfig(), day_start_equity=account.equity)
        self._session_date = _trading_date()
        self.log.info("Started. equity=%.2f paper=%s", account.equity, settings.paper)

    def step(self) -> None:
        account = self.broker.account()
        self._maybe_roll_day(account.equity)

        positions_raw = self.broker.list_positions()
        positions = {p.symbol: float(p.qty) for p in positions_raw}
        self.ledger.reconcile(positions)

        # Kill switch: check daily loss before doing anything else.
        if self.risk.update(account.equity):
            self.log.warning(
                "KILL SWITCH active (daily P&L %.2f%%) — flattening, no new entries.",
                self.risk.daily_pnl_pct(account.equity) * 100,
            )
            self.broker.flatten_all()
            return

        # Use broker-reported market values; tally exposure as we place orders so the
        # exposure gate actually bounds TOTAL exposure across same-cycle entries.
        running_exposure = sum(abs(float(p.market_value)) for p in positions_raw)

        for symbol in self.settings.symbols:
            try:
                running_exposure = self._evaluate_symbol(
                    symbol, account.equity, positions, running_exposure
                )
            except Exception:  # one bad symbol shouldn't take down the loop
                self.log.exception("error evaluating %s", symbol)

    def _evaluate_symbol(self, symbol, equity, positions, running_exposure) -> float:
        df = self.data.get_bars(symbol, self.timeframe, lookback_days=500, use_cache=False)
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
            if self._enter(symbol, risk.qty, decision, bar_ts):
                running_exposure += risk.qty * decision.price

        elif decision.signal is SignalType.EXIT_LONG and held > 0:
            self._exit(symbol, held, decision, bar_ts)
            running_exposure -= held * decision.price

        return running_exposure

    def _enter(self, symbol, qty, decision: SignalDecision, bar_ts) -> bool:
        coid = self._coid(symbol, "buy", bar_ts)
        if self.ledger.already_submitted(coid):
            return False  # already acted on this exact bar — idempotent
        self.ledger.record_intent(coid, symbol, "buy", qty, decision.reason)
        order = self.broker.submit_market(symbol, qty, OrderSide.BUY, client_order_id=coid)
        self.ledger.mark_submitted(coid, str(order.id))
        self.log.info("%s: BUY qty=%s (coid=%s)", symbol, qty, coid)
        return True

    def _exit(self, symbol, held, decision: SignalDecision, bar_ts) -> None:
        coid = self._coid(symbol, "sell", bar_ts)
        self.ledger.record_intent(coid, symbol, "sell", held, decision.reason)
        order = self.broker.close_position(symbol)  # full liquidation, fractional-safe
        self.ledger.mark_submitted(coid, str(order.id))
        self.log.info("%s: EXIT close position qty=%s (coid=%s)", symbol, held, coid)

    def _maybe_roll_day(self, equity: float) -> None:
        today = _trading_date()
        if today != self._session_date:
            self.risk.reset_day(equity)
            self._session_date = today
            self.log.info("New trading day %s — kill switch reset; day_start=%.2f", today, equity)

    @staticmethod
    def _coid(symbol: str, side: str, bar_ts) -> str:
        """Deterministic client_order_id: same symbol+side+bar => same id, so a retry
        of the same logical decision cannot create a duplicate order."""
        digest = hashlib.sha1(f"{symbol}|{side}|{bar_ts}".encode()).hexdigest()[:24]
        return f"bot-{digest}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the StochRSI+MFI Alpaca bot")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    parser.add_argument("--interval", type=int, default=300, help="poll interval in seconds")
    args = parser.parse_args()

    settings = load_settings()
    bot = TradingBot(settings)

    if args.once:
        bot.step()
        return

    # Long-running poll loop. APScheduler is in requirements for richer time-based
    # jobs (market-open setup, EOD reconcile) once you outgrow a plain interval.
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()
    scheduler.add_job(bot.step, "interval", seconds=args.interval, id="trade_cycle")
    logging.getLogger("bot").info("Polling every %ds. Ctrl+C to stop.", args.interval)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
