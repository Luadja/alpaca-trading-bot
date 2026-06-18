"""Mean-reversion (RSI oversold-bounce) — a swing/short-term strategy, long-only.

Buy when RSI dips into oversold *while price is still above a longer trend SMA* (so we buy
pullbacks in an uptrend, not falling knives), and emit a soft exit when RSI recovers. In live
trading the real exit is the bot-side position manager (take-profit / stop / trailing / time);
this RSI-recovery exit is the fallback / the one the backtest also models.

Same pure contract as the other strategies: data in, signals out, no broker calls.
`compute_signals` is shared by the live path and the backtest harness.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from bot.models import SignalDecision, SignalType
from bot.strategy.base import Strategy


@dataclass(frozen=True)
class MeanReversionParams:
    rsi_length: int = 14
    oversold: float = 30.0       # enter long when RSI < this
    exit_level: float = 55.0     # soft exit when RSI > this
    trend_sma: int = 100         # only buy dips while close > this SMA (0 = no trend filter)

    @property
    def min_bars(self) -> int:
        return max(self.rsi_length, self.trend_sma) + 20


def _rsi(close: pd.Series, length: int) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def compute_signals(df: pd.DataFrame, params: MeanReversionParams | None = None) -> pd.DataFrame:
    """Append rsi + signal columns. ``signal`` is +1 (enter long), -1 (soft exit), 0 (hold)."""
    p = params or MeanReversionParams()
    out = df.copy()
    out["rsi"] = _rsi(out["close"], p.rsi_length)
    if p.trend_sma:
        out["sma_trend"] = out["close"].rolling(p.trend_sma).mean()
        trend_ok = (out["close"] > out["sma_trend"]).fillna(False).to_numpy()
    else:
        trend_ok = np.ones(len(out), dtype=bool)

    rsi = out["rsi"].to_numpy()
    sig = np.zeros(len(out), dtype=int)
    in_pos = False
    for i in range(len(out)):
        if np.isnan(rsi[i]):
            continue
        if not in_pos:
            if rsi[i] < p.oversold and trend_ok[i]:
                sig[i] = 1
                in_pos = True
        elif rsi[i] > p.exit_level:
            sig[i] = -1
            in_pos = False

    out["signal"] = sig
    out["confidence"] = 0.0
    out.loc[out["signal"] == 1, "confidence"] = 1.0
    return out


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"

    def __init__(self, params: MeanReversionParams | None = None) -> None:
        self.params = params or MeanReversionParams()

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        return compute_signals(df, self.params)

    def generate(self, df: pd.DataFrame, symbol: str) -> SignalDecision:
        self._validate(df)
        last = compute_signals(df, self.params).iloc[-1]
        raw = int(last["signal"])
        sig = SignalType.ENTER_LONG if raw == 1 else SignalType.EXIT_LONG if raw == -1 else SignalType.HOLD
        rsi = _safe(last.get("rsi"))
        reason = f"RSI={rsi:.0f}" + ("" if sig is SignalType.HOLD else f" ({sig.value})")
        return SignalDecision(
            symbol=symbol, signal=sig, price=float(last["close"]),
            confidence=float(last["confidence"]), reason=reason, indicators={"rsi": rsi},
        )


def _safe(value: object) -> float:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if pd.isna(f) else f
