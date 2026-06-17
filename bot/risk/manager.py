"""Risk layer — the part that protects real money.

Responsibilities:
  * position sizing (risk-per-trade with a hard per-position cap),
  * pre-trade gates (max position, max total exposure),
  * a daily-loss KILL SWITCH that, once tripped, blocks all new entries until reset.

Pure and dependency-free so the kill switch can be unit-tested deterministically.
An untested kill switch is no kill switch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskConfig:
    max_position_pct: float = 0.10  # max % of equity in any single position
    max_total_exposure_pct: float = 0.60  # max % of equity invested across all positions
    risk_per_trade_pct: float = 0.01  # % of equity risked between entry and stop (sizing)
    stop_loss_pct: float = 0.05  # stop distance used for risk-per-trade SIZING only
    catastrophic_stop_pct: float = 0.10  # HARD enforced exit: flatten if price falls this far
    max_daily_loss_pct: float = 0.03  # kill-switch threshold vs. day-start equity
    max_weekly_loss_pct: float = 0.06  # kill-switch threshold vs. week-start equity
    max_monthly_loss_pct: float = 0.10  # kill-switch threshold vs. month-start equity
    # Trailing peak-to-trough breaker (0 = OFF). Latches when equity falls this far below the
    # high-water mark; clears when a new high is made. Complements the calendar-anchored
    # daily/weekly/monthly stops with an all-time-peak drawdown guard.
    max_drawdown_from_peak_pct: float = 0.0
    allow_fractional: bool = False  # whole shares avoid fractional-order constraints
    # Inverse-volatility sizing: size each position to ~vol_target_pct annualized vol when a
    # per-symbol sigma is supplied (equalizes risk across quiet/jumpy names). Off by default.
    use_vol_targeting: bool = False
    vol_target_pct: float = 0.02


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    qty: float
    reason: str


class RiskManager:
    def __init__(
        self,
        config: RiskConfig,
        day_start_equity: float,
        *,
        week_start_equity: float | None = None,
        month_start_equity: float | None = None,
        high_water_mark: float | None = None,
        halted_day: bool = False,
        halted_week: bool = False,
        halted_month: bool = False,
        halted_drawdown: bool = False,
    ) -> None:
        if day_start_equity <= 0:
            raise ValueError("day_start_equity must be positive")
        self.config = config
        self.day_start_equity = day_start_equity
        self.week_start_equity = week_start_equity if week_start_equity is not None else day_start_equity
        self.month_start_equity = (
            month_start_equity if month_start_equity is not None else day_start_equity
        )
        self.high_water_mark = high_water_mark if high_water_mark is not None else day_start_equity
        # Per-horizon latch flags: each is set by update() on a breach and cleared ONLY by
        # its own reset, so a still-active weekly/monthly halt survives a day rollover.
        self._halted_day = halted_day
        self._halted_week = halted_week
        self._halted_month = halted_month
        self._halted_drawdown = halted_drawdown

    # --- kill switch (daily / weekly / monthly / drawdown horizons) --------
    @property
    def halted(self) -> bool:
        return self._halted_day or self._halted_week or self._halted_month or self._halted_drawdown

    def daily_pnl_pct(self, equity: float) -> float:
        return (equity - self.day_start_equity) / self.day_start_equity

    def weekly_pnl_pct(self, equity: float) -> float:
        return (equity - self.week_start_equity) / self.week_start_equity

    def monthly_pnl_pct(self, equity: float) -> float:
        return (equity - self.month_start_equity) / self.month_start_equity

    def drawdown_pct(self, equity: float) -> float:
        if self.high_water_mark <= 0:
            return 0.0
        return (equity - self.high_water_mark) / self.high_water_mark

    def halt_reason(self, equity: float) -> str:
        """Human-readable reason naming the horizon(s) actually latched (not always daily)."""
        parts = []
        if self._halted_day:
            parts.append(f"day {self.daily_pnl_pct(equity) * 100:.2f}%")
        if self._halted_week:
            parts.append(f"week {self.weekly_pnl_pct(equity) * 100:.2f}%")
        if self._halted_month:
            parts.append(f"month {self.monthly_pnl_pct(equity) * 100:.2f}%")
        if self._halted_drawdown:
            parts.append(f"drawdown {self.drawdown_pct(equity) * 100:.2f}% from peak")
        return ", ".join(parts) or "loss limit"

    def breached_daily_loss(self, equity: float) -> bool:
        return self.daily_pnl_pct(equity) <= -self.config.max_daily_loss_pct

    def breached_weekly_loss(self, equity: float) -> bool:
        return (equity - self.week_start_equity) / self.week_start_equity <= -self.config.max_weekly_loss_pct

    def breached_monthly_loss(self, equity: float) -> bool:
        return (equity - self.month_start_equity) / self.month_start_equity <= -self.config.max_monthly_loss_pct

    def _breached_any(self, equity: float) -> bool:
        return (
            self.breached_daily_loss(equity)
            or self.breached_weekly_loss(equity)
            or self.breached_monthly_loss(equity)
        )

    def update(self, equity: float) -> bool:
        """Call each cycle with current equity. Latches each horizon independently on a
        breach and tracks the equity high-water mark. Returns halted."""
        self.high_water_mark = max(self.high_water_mark, equity)
        if self.breached_daily_loss(equity):
            self._halted_day = True
        if self.breached_weekly_loss(equity):
            self._halted_week = True
        if self.breached_monthly_loss(equity):
            self._halted_month = True
        # Trailing peak-to-trough breaker (opt-in). Clear on a new high; latch on a breach.
        if self.config.max_drawdown_from_peak_pct > 0:
            if equity >= self.high_water_mark:
                self._halted_drawdown = False
            elif self.drawdown_pct(equity) <= -self.config.max_drawdown_from_peak_pct:
                self._halted_drawdown = True
        else:
            # Breaker disabled: clear any latch rehydrated from a prior run that HAD it on,
            # otherwise a persisted halted_drawdown=True would halt the bot forever.
            self._halted_drawdown = False
        return self.halted

    def reset_day(self, equity: float) -> None:
        """New session: re-baseline the day anchor and clear ONLY the daily latch. A
        still-latched weekly/monthly halt persists until its own boundary rolls."""
        if equity <= 0:
            raise ValueError("equity must be positive")
        self.day_start_equity = equity
        self._halted_day = False

    def reset_week(self, equity: float) -> None:
        if equity <= 0:
            raise ValueError("equity must be positive")
        self.week_start_equity = equity
        self._halted_week = False

    def reset_month(self, equity: float) -> None:
        if equity <= 0:
            raise ValueError("equity must be positive")
        self.month_start_equity = equity
        self._halted_month = False

    def snapshot(self) -> dict:
        """Serializable state for persistence across restarts (latch order-independent)."""
        return {
            "day_start_equity": self.day_start_equity,
            "week_start_equity": self.week_start_equity,
            "month_start_equity": self.month_start_equity,
            "high_water_mark": self.high_water_mark,
            "halted_day": self._halted_day,
            "halted_week": self._halted_week,
            "halted_month": self._halted_month,
            "halted_drawdown": self._halted_drawdown,
        }

    # --- sizing & gates ----------------------------------------------------
    def position_size(
        self,
        equity: float,
        price: float,
        stop_price: float | None = None,
        sigma: float | None = None,
    ) -> float:
        """Shares to buy, always capped so the position value <= max_position_pct of equity.

        Sizing mode: inverse-volatility when vol-targeting is on and an annualized ``sigma``
        is supplied; else risk-per-trade against a stop; else the position cap.
        """
        if price <= 0:
            return 0.0
        cap_shares = (equity * self.config.max_position_pct) / price

        if self.config.use_vol_targeting and sigma and sigma > 0:
            # position_value * sigma = equity * vol_target_pct  ->  qty = budget / (price*sigma)
            qty = (equity * self.config.vol_target_pct) / (price * sigma)
        elif stop_price is not None and 0 < stop_price < price:
            risk_per_share = price - stop_price
            dollars_at_risk = equity * self.config.risk_per_trade_pct
            qty = dollars_at_risk / risk_per_share
        else:
            qty = cap_shares

        qty = max(0.0, min(qty, cap_shares))
        return qty if self.config.allow_fractional else float(math.floor(qty))

    def default_stop(self, entry_price: float) -> float:
        return entry_price * (1.0 - self.config.stop_loss_pct)

    def should_stop_out(self, entry_price: float, current_price: float) -> bool:
        """Hard catastrophic stop (disaster insurance, distinct from the sizing stop and
        the strategy's own exit): True once price falls catastrophic_stop_pct below entry."""
        if entry_price <= 0 or current_price <= 0:
            return False  # no valid mark (e.g. a freshly-halted symbol) -> don't false-trip
        return current_price <= entry_price * (1.0 - self.config.catastrophic_stop_pct)

    def evaluate_entry(
        self,
        equity: float,
        price: float,
        current_exposure_value: float,
        stop_price: float | None = None,
        sigma: float | None = None,
    ) -> RiskDecision:
        """Run all gates for a prospective long entry and size it (sigma = annualized vol
        for inverse-vol sizing when enabled)."""
        if self.halted or self._breached_any(equity):
            return RiskDecision(False, 0.0, "kill switch: loss limit reached")

        if equity <= 0:
            return RiskDecision(False, 0.0, "no equity")

        if current_exposure_value / equity > self.config.max_total_exposure_pct:
            return RiskDecision(False, 0.0, "max total exposure reached")

        stop = stop_price if stop_price is not None else self.default_stop(price)
        qty = self.position_size(equity, price, stop, sigma=sigma)
        if qty <= 0:
            return RiskDecision(False, 0.0, "sized to zero shares (position too small)")

        order_value = qty * price
        if current_exposure_value + order_value > equity * self.config.max_total_exposure_pct:
            return RiskDecision(False, 0.0, "order would exceed max total exposure")

        return RiskDecision(True, qty, "approved")
