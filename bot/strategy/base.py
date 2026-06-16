"""Strategy interface.

A Strategy turns a bar DataFrame into a SignalDecision. It must remain pure — no
broker calls, no order placement, no I/O. Sizing and execution happen downstream
so the exact same code runs in backtest, paper, and live.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from bot.models import SignalDecision

# Columns every strategy can rely on (lowercase OHLCV indexed by timestamp).
REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def generate(self, df: pd.DataFrame, symbol: str) -> SignalDecision:
        """Return the decision for the LATEST bar in ``df``."""

    @staticmethod
    def _validate(df: pd.DataFrame) -> None:
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"DataFrame missing required columns: {missing}")
