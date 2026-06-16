"""Stochastic RSI + Money Flow Index strategy with optional divergence confirmation.

Logic (long-only — shorting equities adds margin/borrow complexity; keep it simple
to start):

  ENTER LONG  when StochRSI %K crosses ABOVE %D while %K is in oversold territory,
              AND MFI is below its oversold threshold (volume confirms the buyers).
  EXIT  LONG  when StochRSI %K crosses BELOW %D while %K is in overbought territory,
              OR MFI is overbought.

Divergence acts as a confidence filter:
  - If ``divergence_required`` is True, a long requires a recent BULLISH divergence
    (price lower-low + MFI higher-low) within ``div_confirm_window`` bars.
  - Otherwise divergence only raises ``confidence`` from 0.5 to 1.0 when present.

`compute_signals` is the single source of truth and is shared by the live strategy
and the backtest harness.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from bot.indicators import mfi, regular_divergence, stoch_rsi
from bot.models import SignalDecision, SignalType
from bot.strategy.base import Strategy


@dataclass(frozen=True)
class StochRsiMfiParams:
    # StochRSI
    rsi_length: int = 14
    stoch_length: int = 14
    k_smooth: int = 3
    d_smooth: int = 3
    stoch_oversold: float = 20.0
    stoch_overbought: float = 80.0
    # MFI
    mfi_length: int = 14
    mfi_oversold: float = 30.0
    mfi_overbought: float = 80.0
    # Divergence (computed against MFI)
    use_divergence: bool = True
    divergence_required: bool = False
    div_left: int = 3
    div_right: int = 3
    div_lookback: int = 60
    div_confirm_window: int = 5
    # Trend/regime filter: only take longs when price is above its long SMA. ON by
    # default — validated to cut the 2022 bear drawdown ~73% and improve out-of-sample
    # robustness (see docs/PLAN.md §10). Set False to trade the raw oscillator.
    use_trend_filter: bool = True
    trend_sma: int = 200

    @property
    def min_bars(self) -> int:
        """Bars needed before signals are meaningful: the longest active indicator
        window plus headroom. The live guard uses this so changing trend_sma (or the
        timeframe) can't silently leave the regime gate invalid on the latest bar."""
        oscillator = self.rsi_length + self.stoch_length + self.k_smooth + self.d_smooth
        need = max(oscillator, self.mfi_length)
        if self.use_divergence:
            need = max(need, self.div_lookback)
        if self.use_trend_filter:
            need = max(need, self.trend_sma)
        return need + 20


def compute_signals(df: pd.DataFrame, params: StochRsiMfiParams | None = None) -> pd.DataFrame:
    """Append indicator + signal columns to ``df`` and return a new frame.

    Adds: stochrsi, stochrsi_k, stochrsi_d, mfi, bull_div, bear_div, signal,
    confidence (plus sma_trend when use_trend_filter). ``signal`` is +1 (enter long),
    -1 (exit long), 0 (hold).
    """
    p = params or StochRsiMfiParams()
    out = df.copy()

    srsi = stoch_rsi(out["close"], p.rsi_length, p.stoch_length, p.k_smooth, p.d_smooth)
    out = out.join(srsi)
    out["mfi"] = mfi(out["high"], out["low"], out["close"], out["volume"], p.mfi_length)

    if p.use_divergence:
        div = regular_divergence(out["close"], out["mfi"], p.div_left, p.div_right, p.div_lookback)
        out = out.join(div)
    else:
        out["bull_div"] = False
        out["bear_div"] = False

    k, d = out["stochrsi_k"], out["stochrsi_d"]
    cross_up = (k > d) & (k.shift(1) <= d.shift(1))
    cross_down = (k < d) & (k.shift(1) >= d.shift(1))

    # Trend filter: only allow longs when price is above its long SMA, so the bot
    # doesn't buy oversold dips in a sustained downtrend. Open (always True) when off.
    if p.use_trend_filter:
        out["sma_trend"] = out["close"].rolling(p.trend_sma).mean()
        trend_ok = out["close"] > out["sma_trend"]
    else:
        trend_ok = pd.Series(True, index=out.index)

    # Base long trigger, independent of divergence (used for the confidence tiers).
    base_long = cross_up & (k < p.stoch_oversold) & (out["mfi"] < p.mfi_oversold) & trend_ok
    exit_cond = cross_down & ((k > p.stoch_overbought) | (out["mfi"] > p.mfi_overbought))

    recent_bull = (
        out["bull_div"].rolling(p.div_confirm_window, min_periods=1).max().fillna(0).astype(bool)
    )

    # Divergence gate applies to the SIGNAL only; confidence still reflects both tiers.
    long_cond = base_long & recent_bull if (p.use_divergence and p.divergence_required) else base_long

    out["signal"] = 0
    out.loc[long_cond.fillna(False), "signal"] = 1
    out.loc[exit_cond.fillna(False), "signal"] = -1

    out["confidence"] = 0.0
    out.loc[base_long.fillna(False), "confidence"] = 0.5
    out.loc[(base_long & recent_bull).fillna(False), "confidence"] = 1.0
    return out


class StochRsiMfiStrategy(Strategy):
    name = "stoch_rsi_mfi"

    def __init__(self, params: StochRsiMfiParams | None = None) -> None:
        self.params = params or StochRsiMfiParams()

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

        return SignalDecision(
            symbol=symbol,
            signal=sig,
            price=float(last["close"]),
            confidence=float(last["confidence"]),
            reason=self._describe(last, sig),
            indicators={
                "stochrsi_k": _safe(last.get("stochrsi_k")),
                "stochrsi_d": _safe(last.get("stochrsi_d")),
                "mfi": _safe(last.get("mfi")),
                "bull_div": bool(last.get("bull_div", False)),
                "bear_div": bool(last.get("bear_div", False)),
            },
        )

    @staticmethod
    def _describe(row: pd.Series, sig: SignalType) -> str:
        if sig is SignalType.HOLD:
            return "no crossover/threshold trigger"
        parts = [
            f"StochRSI %K={_safe(row.get('stochrsi_k')):.1f}",
            f"%D={_safe(row.get('stochrsi_d')):.1f}",
            f"MFI={_safe(row.get('mfi')):.1f}",
        ]
        if bool(row.get("bull_div", False)):
            parts.append("bullish divergence")
        return ", ".join(parts)


def _safe(value: object) -> float:
    """NaN/None -> 0.0 so SignalDecision stays JSON-friendly."""
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if pd.isna(f) else f
