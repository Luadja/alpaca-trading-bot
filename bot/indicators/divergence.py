"""Regular price/indicator divergence detection.

- Bullish divergence: price makes a LOWER low while the indicator makes a HIGHER
  low -> waning downside momentum -> confirms a potential long.
- Bearish divergence: price makes a HIGHER high while the indicator makes a LOWER
  high -> waning upside momentum.

Pivots are confirmed fractals: a bar is a pivot low if it is the strict minimum of
the window [i-left, i+right]. Because a pivot can only be *known* ``right`` bars
after it prints, each divergence is flagged at the confirmation bar (pivot + right),
which keeps the signal free of look-ahead bias when used live or in a backtest.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _pivot_indices(values: np.ndarray, left: int, right: int, kind: str) -> list[int]:
    """Indices of strict local extrema with `left` bars before and `right` after."""
    n = len(values)
    out: list[int] = []
    for i in range(left, n - right):
        window = values[i - left : i + right + 1]
        center = values[i]
        if np.isnan(center) or np.isnan(window).any():
            continue
        if kind == "low" and center == window.min() and (window == center).sum() == 1:
            out.append(i)
        elif kind == "high" and center == window.max() and (window == center).sum() == 1:
            out.append(i)
    return out


def regular_divergence(
    price: pd.Series,
    indicator: pd.Series,
    left: int = 3,
    right: int = 3,
    lookback: int = 60,
) -> pd.DataFrame:
    """Flag regular bullish / bearish divergence between consecutive pivots.

    ``lookback`` caps how many bars apart the two compared pivots may be.
    Returns a frame with boolean columns ``bull_div`` and ``bear_div`` aligned to
    ``price.index``, each True on the confirmation bar.
    """
    bull = pd.Series(False, index=price.index)
    bear = pd.Series(False, index=price.index)

    pv = price.to_numpy(dtype=float)
    iv = indicator.to_numpy(dtype=float)

    lows = _pivot_indices(pv, left, right, "low")
    for j in range(1, len(lows)):
        b = lows[j]
        for i in range(j - 1, -1, -1):  # nearest prior pivot first
            a = lows[i]
            if b - a > lookback:
                break  # pivots are sorted, so everything earlier is too far too
            if pv[b] < pv[a] and iv[b] > iv[a]:
                bull.iloc[b + right] = True
                break

    highs = _pivot_indices(pv, left, right, "high")
    for j in range(1, len(highs)):
        b = highs[j]
        for i in range(j - 1, -1, -1):
            a = highs[i]
            if b - a > lookback:
                break
            if pv[b] > pv[a] and iv[b] < iv[a]:
                bear.iloc[b + right] = True
                break

    return pd.DataFrame({"bull_div": bull, "bear_div": bear}, index=price.index)
