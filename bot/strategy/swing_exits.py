"""Shared swing exit logic — take-profit / stop / trailing / time.

Used by BOTH the crypto backtest and (next) the live bot's position manager, so backtest and
live behave identically. Pure and dependency-free.

Evaluated once per bar (backtest) or per cycle (live) for each open long trade.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExitParams:
    take_profit_pct: float = 0.04   # exit at entry * (1 + this)
    stop_loss_pct: float = 0.025    # exit at entry * (1 - this)
    trail_pct: float = 0.03         # exit if price falls this far below the high since entry (0 = off)
    max_bars: int = 48              # time-exit after this many bars held (e.g. 48 = 2 days on 1H)


def check_exit(entry_price: float, high_water: float, bars_held: int,
               bar_high: float, bar_low: float, bar_close: float, p: ExitParams):
    """Decide whether to exit an open long this bar. Returns (exit, price, reason).

    Conservative ordering for backtest honesty: stop/trailing are checked against the bar LOW
    (assume the worst intrabar path), take-profit against the bar HIGH. If a bar could have hit
    both a stop and the target, the stop is taken first."""
    stop = entry_price * (1.0 - p.stop_loss_pct)
    if bar_low <= stop:
        return True, stop, "stop"
    if p.trail_pct > 0.0:
        trail = high_water * (1.0 - p.trail_pct)
        if trail > stop and bar_low <= trail:
            return True, trail, "trail"
    target = entry_price * (1.0 + p.take_profit_pct)
    if bar_high >= target:
        return True, target, "target"
    if bars_held >= p.max_bars:
        return True, bar_close, "time"
    return False, None, ""
