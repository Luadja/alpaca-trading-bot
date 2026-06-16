"""Shared, dependency-free value types passed between modules.

Kept pure (stdlib only) so the strategy/risk layers stay testable without
pulling in Alpaca or pydantic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SignalType(str, Enum):
    """What the strategy wants to do on the latest bar."""

    ENTER_LONG = "enter_long"
    EXIT_LONG = "exit_long"
    HOLD = "hold"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class SignalDecision:
    """A strategy's verdict for one symbol on one bar.

    `confidence` is in [0, 1]; the StochRSI+MFI strategy raises it to 1.0 when a
    matching price/indicator divergence confirms the crossover, 0.5 otherwise.
    """

    symbol: str
    signal: SignalType
    price: float
    confidence: float = 0.0
    reason: str = ""
    indicators: dict[str, float] = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.signal is not SignalType.HOLD
