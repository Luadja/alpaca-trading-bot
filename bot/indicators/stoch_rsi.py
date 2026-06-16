"""RSI and Stochastic RSI.

StochRSI applies the Stochastic oscillator formula to RSI values rather than to
price, making it a more sensitive momentum oscillator. Output is scaled to 0-100
to match the common TradingView convention.

Reference: Chande & Kroll, "The New Technical Trader" (StochRSI); Wilder, "New
Concepts in Technical Trading Systems" (RSI).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder's moving average (a.k.a. RMA / SMMA): an EMA with alpha = 1/length."""
    return series.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index, 0-100."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = rma(gain, length)
    avg_loss = rma(loss, length)

    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    # Up-only window (avg_loss == 0 but avg_gain > 0) -> RSI 100. A perfectly flat
    # window (both zero) stays undefined (NaN), not a misleading 100.
    out = out.where((avg_loss != 0.0) | (avg_gain == 0.0), 100.0)
    return out


def stoch_rsi(
    close: pd.Series,
    rsi_length: int = 14,
    stoch_length: int = 14,
    k_smooth: int = 3,
    d_smooth: int = 3,
) -> pd.DataFrame:
    """Stochastic RSI with %K / %D smoothing.

    Returns a frame with columns:
      - ``stochrsi``   : raw StochRSI, 0-100
      - ``stochrsi_k`` : %K = SMA(stochrsi, k_smooth)
      - ``stochrsi_d`` : %D = SMA(%K, d_smooth)
    """
    r = rsi(close, rsi_length)
    lowest = r.rolling(stoch_length).min()
    highest = r.rolling(stoch_length).max()
    rng = (highest - lowest).replace(0.0, np.nan)  # flat RSI window -> undefined

    raw = ((r - lowest) / rng).clip(0.0, 1.0) * 100.0
    k = raw.rolling(k_smooth).mean()
    d = k.rolling(d_smooth).mean()

    return pd.DataFrame(
        {"stochrsi": raw, "stochrsi_k": k, "stochrsi_d": d},
        index=close.index,
    )
