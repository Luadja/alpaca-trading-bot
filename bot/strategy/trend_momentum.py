"""Trend-following / momentum strategy (long-only).

Where mean-reversion fights the trend, this rides it: go long on a dual-SMA
"golden cross" (fast SMA crosses above slow SMA) and exit on the "death cross"
(fast crosses below slow). Time-series momentum is one of the better-documented
return anomalies and has a far better prior than mean-reversion on a trending
mega-cap / ETF universe.

Optional filters:
  * regime: require price above a long SMA (extra trend confirmation),
  * momentum: require trailing N-bar ROC above a threshold.

Same pure contract as the other strategies: data in, signals out, no broker calls.
`compute_signals` is shared by the live path and the backtest/validation harness.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from bot.models import SignalDecision, SignalType
from bot.strategy.base import Strategy


@dataclass(frozen=True)
class TrendMomentumParams:
    fast_sma: int = 50
    slow_sma: int = 200  # 50/200 = classic golden/death cross
    use_regime_filter: bool = False  # also require close > regime_sma
    regime_sma: int = 200
    roc_length: int = 0  # 0 = off; else require ROC(roc_length) > roc_min to enter
    roc_min: float = 0.0

    @property
    def min_bars(self) -> int:
        need = max(self.fast_sma, self.slow_sma)
        if self.use_regime_filter:
            need = max(need, self.regime_sma)
        if self.roc_length:
            need = max(need, self.roc_length)
        return need + 20


def compute_signals(df: pd.DataFrame, params: TrendMomentumParams | None = None) -> pd.DataFrame:
    """Append SMA + signal columns. ``signal`` is +1 (enter long), -1 (exit), 0 (hold)."""
    p = params or TrendMomentumParams()
    out = df.copy()

    out["sma_fast"] = out["close"].rolling(p.fast_sma).mean()
    out["sma_slow"] = out["close"].rolling(p.slow_sma).mean()
    f, s = out["sma_fast"], out["sma_slow"]

    cross_up = (f > s) & (f.shift(1) <= s.shift(1))
    cross_down = (f < s) & (f.shift(1) >= s.shift(1))

    enter = cross_up
    if p.use_regime_filter:
        out["sma_regime"] = out["close"].rolling(p.regime_sma).mean()
        enter = enter & (out["close"] > out["sma_regime"])
    if p.roc_length:
        out["roc"] = out["close"].pct_change(p.roc_length, fill_method=None) * 100.0
        enter = enter & (out["roc"] > p.roc_min)

    out["signal"] = 0
    out.loc[enter.fillna(False), "signal"] = 1
    out.loc[cross_down.fillna(False), "signal"] = -1
    out["confidence"] = 0.0
    out.loc[enter.fillna(False), "confidence"] = 1.0
    return out


class TrendMomentumStrategy(Strategy):
    name = "trend_momentum"

    def __init__(self, params: TrendMomentumParams | None = None) -> None:
        self.params = params or TrendMomentumParams()

    def generate(self, df: pd.DataFrame, symbol: str) -> SignalDecision:
        self._validate(df)
        signals = compute_signals(df, self.params)
        last = signals.iloc[-1]

        raw = int(last["signal"])
        if raw == 1:
            sig = SignalType.ENTER_LONG
        elif raw == -1:
            sig = SignalType.EXIT_LONG
        else:
            sig = SignalType.HOLD

        fast, slow = _safe(last.get("sma_fast")), _safe(last.get("sma_slow"))
        reason = (
            "no cross"
            if sig is SignalType.HOLD
            else f"SMA{self.params.fast_sma}={fast:.2f} vs SMA{self.params.slow_sma}={slow:.2f}"
        )
        return SignalDecision(
            symbol=symbol,
            signal=sig,
            price=float(last["close"]),
            confidence=float(last["confidence"]),
            reason=reason,
            indicators={"sma_fast": fast, "sma_slow": slow},
        )


def _safe(value: object) -> float:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if pd.isna(f) else f
