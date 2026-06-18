"""Donchian breakout / momentum strategy (long-only) — the crypto playground strategy.

Go long when price closes above the prior N-day high (buy strength), exit when it closes
below the prior M-day low (let winners run, cut losers). NO fixed take-profit — the point is
to ride trends, not cap them. This is the trend-FOLLOWING opposite of mean-reversion.

⚠️  NO DEMONSTRATED EDGE. Adversarial backtest review (see memory: crypto-swing-findings)
showed the apparent crypto edge fails walk-forward (made money 2022-24, LOST 2024-26), is
survivorship-inflated, and merely ties a BTC+cash blend. Run on PAPER as a playground only.

Design — STATELESS per-bar signal, paired with the execution layer's position gate:
  * close >= prior entry_lookback-day high  -> +1 (ENTER_LONG)   [acted on only when FLAT]
  * close <= prior exit_lookback-day low    -> -1 (EXIT_LONG)    [acted on only when HELD]
  * else                                    ->  0 (HOLD)
Because the channels are path-independent (built from prior bars only) and the execution layer
gates entries on held==0 and exits on held>0, this needs no internal position state — so it is
restart-safe: a freshly-started bot correctly exits a held position on a channel break and
waits for the next breakout to enter. The channels use .shift(1) so the CURRENT bar is excluded
(no look-ahead); get_crypto_bars drops the still-forming bar, so the latest bar is fully closed.

Same pure contract as the other strategies: data in, signals out, no broker calls.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from bot.models import SignalDecision, SignalType
from bot.strategy.base import Strategy


@dataclass(frozen=True)
class BreakoutParams:
    entry_lookback: int = 20   # enter when close >= highest high of the prior N bars
    exit_lookback: int = 5     # exit when close <= lowest low of the prior M bars

    @property
    def min_bars(self) -> int:
        # need the larger rolling window + the shift(1) + a small buffer
        return max(self.entry_lookback, self.exit_lookback) + 5


def compute_signals(df: pd.DataFrame, params: BreakoutParams | None = None) -> pd.DataFrame:
    """Append Donchian channel + ``signal`` columns. ``signal`` is +1 (breakout), -1
    (channel break), 0 (hold). Stateless: see module docstring for how the execution layer's
    position gate turns this into enter/exit/hold without internal state."""
    p = params or BreakoutParams()
    out = df.copy()
    # Prior-window channels: rolling(...).shift(1) excludes the current bar -> no look-ahead.
    out["upper"] = out["high"].rolling(p.entry_lookback).max().shift(1)
    out["lower"] = out["low"].rolling(p.exit_lookback).min().shift(1)
    close = out["close"]
    breakout = (close >= out["upper"]).fillna(False).to_numpy()
    breakdown = (close <= out["lower"]).fillna(False).to_numpy()
    # breakout and breakdown are mutually exclusive (upper >= lower always), but order the
    # np.where so a breakout wins if a degenerate window ever made them coincide.
    out["signal"] = np.where(breakout, 1, np.where(breakdown, -1, 0)).astype(int)
    out["confidence"] = 0.0
    out.loc[out["signal"] == 1, "confidence"] = 1.0
    return out


class BreakoutStrategy(Strategy):
    name = "breakout"

    def __init__(self, params: BreakoutParams | None = None) -> None:
        self.params = params or BreakoutParams()

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
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

        upper, lower = _safe(last.get("upper")), _safe(last.get("lower"))
        close = float(last["close"])
        if sig is SignalType.ENTER_LONG:
            reason = f"breakout: close {close:.4g} >= {self.params.entry_lookback}d-high {upper:.4g}"
        elif sig is SignalType.EXIT_LONG:
            reason = f"channel break: close {close:.4g} <= {self.params.exit_lookback}d-low {lower:.4g}"
        else:
            reason = f"hold: close {close:.4g} in [{lower:.4g}, {upper:.4g}]"
        return SignalDecision(
            symbol=symbol,
            signal=sig,
            price=close,
            confidence=float(last["confidence"]),
            reason=reason,
            indicators={"upper": upper, "lower": lower},
        )


def _safe(value: object) -> float:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if pd.isna(f) else f
