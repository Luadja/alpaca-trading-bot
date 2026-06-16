"""Trend-following / momentum strategy (long-only).

Where mean-reversion fights the trend, this rides it: go long on a dual-SMA
"golden cross" (fast SMA crosses above slow SMA) and exit on the "death cross"
(fast crosses below slow). Time-series momentum is one of the better-documented
return anomalies and has a far better prior than mean-reversion on a trending
mega-cap / ETF universe.

Optional filters / exits:
  * regime: require price above a long SMA (extra trend confirmation),
  * momentum: require trailing N-bar ROC above a threshold,
  * trailing stop: exit when close falls trail_pct below the peak since entry — caps the
    give-back the lagging slow-SMA would otherwise allow before a death-cross exit.

The trailing stop makes signal generation path-dependent (a stateful pass), so it assumes
entries fill as simulated. For LIVE trading the trailing stop should ultimately be enforced
against the ACTUAL broker position/entry in the execution layer; the strategy-level version
here is the backtest/validation model.

Same pure contract as the other strategies: data in, signals out, no broker calls.
`compute_signals` is shared by the live path and the backtest/validation harness.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
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
    # Trailing stop is OFF by default: validation showed it cuts drawdown only modestly
    # while gutting returns/edge (stops out on normal pullbacks, can't re-enter without a
    # new golden cross). Available as an option. See docs/PLAN.md §10.
    use_trailing_stop: bool = False
    trail_pct: float = 0.15  # exit if close falls this fraction below the peak since entry

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

    # Stateful pass: while in a position, ride until the death cross OR a trailing stop
    # (close falls trail_pct below the peak close since entry).
    closes = out["close"].to_numpy(dtype=float)
    enter_arr = enter.fillna(False).to_numpy()
    exit_arr = cross_down.fillna(False).to_numpy()
    sig = np.zeros(len(out), dtype=int)
    in_pos = False
    peak = 0.0
    for i in range(len(out)):
        if not in_pos:
            if enter_arr[i]:
                sig[i] = 1
                in_pos = True
                peak = closes[i]
        else:
            peak = max(peak, closes[i])
            stop_hit = p.use_trailing_stop and closes[i] <= peak * (1.0 - p.trail_pct)
            if exit_arr[i] or stop_hit:
                sig[i] = -1
                in_pos = False

    out["signal"] = sig
    out["confidence"] = 0.0
    out.loc[out["signal"] == 1, "confidence"] = 1.0
    return out


class TrendMomentumStrategy(Strategy):
    name = "trend_momentum"

    def __init__(self, params: TrendMomentumParams | None = None) -> None:
        self.params = params or TrendMomentumParams()

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Full indicator + signal frame (for analysis / plotting)."""
        return compute_signals(df, self.params)

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
