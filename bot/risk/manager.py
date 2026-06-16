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
    max_daily_loss_pct: float = 0.03  # kill-switch threshold (loss vs. day-start equity)
    allow_fractional: bool = False  # whole shares avoid fractional-order constraints


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    qty: float
    reason: str


class RiskManager:
    def __init__(self, config: RiskConfig, day_start_equity: float) -> None:
        if day_start_equity <= 0:
            raise ValueError("day_start_equity must be positive")
        self.config = config
        self.day_start_equity = day_start_equity
        self._halted = False

    # --- kill switch -------------------------------------------------------
    @property
    def halted(self) -> bool:
        return self._halted

    def daily_pnl_pct(self, equity: float) -> float:
        return (equity - self.day_start_equity) / self.day_start_equity

    def breached_daily_loss(self, equity: float) -> bool:
        return self.daily_pnl_pct(equity) <= -self.config.max_daily_loss_pct

    def update(self, equity: float) -> bool:
        """Call each cycle with current equity. Latches the kill switch. Returns halted."""
        if self.breached_daily_loss(equity):
            self._halted = True
        return self._halted

    def reset_day(self, day_start_equity: float) -> None:
        """Clear the kill switch at the start of a new trading day."""
        if day_start_equity <= 0:
            raise ValueError("day_start_equity must be positive")
        self.day_start_equity = day_start_equity
        self._halted = False

    # --- sizing & gates ----------------------------------------------------
    def position_size(self, equity: float, price: float, stop_price: float | None = None) -> float:
        """Shares to buy: risk-per-trade if a stop is given, else the position cap.

        Always capped so the position value <= max_position_pct of equity.
        """
        if price <= 0:
            return 0.0
        cap_shares = (equity * self.config.max_position_pct) / price

        if stop_price is not None and 0 < stop_price < price:
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
        if entry_price <= 0:
            return False
        return current_price <= entry_price * (1.0 - self.config.catastrophic_stop_pct)

    def evaluate_entry(
        self,
        equity: float,
        price: float,
        current_exposure_value: float,
        stop_price: float | None = None,
    ) -> RiskDecision:
        """Run all gates for a prospective long entry and size it."""
        if self._halted or self.breached_daily_loss(equity):
            return RiskDecision(False, 0.0, "kill switch: daily loss limit reached")

        if equity <= 0:
            return RiskDecision(False, 0.0, "no equity")

        if current_exposure_value / equity > self.config.max_total_exposure_pct:
            return RiskDecision(False, 0.0, "max total exposure reached")

        stop = stop_price if stop_price is not None else self.default_stop(price)
        qty = self.position_size(equity, price, stop)
        if qty <= 0:
            return RiskDecision(False, 0.0, "sized to zero shares (position too small)")

        order_value = qty * price
        if current_exposure_value + order_value > equity * self.config.max_total_exposure_pct:
            return RiskDecision(False, 0.0, "order would exceed max total exposure")

        return RiskDecision(True, qty, "approved")
