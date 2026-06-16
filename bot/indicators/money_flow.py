"""Money Flow Index (MFI) — a volume-weighted RSI, 0-100.

Bars where the typical price is unchanged contribute to neither positive nor
negative flow, per the standard definition. Overbought > 80, oversold < 20.
"""

from __future__ import annotations

import pandas as pd


def mfi(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    length: int = 14,
) -> pd.Series:
    """Compute the Money Flow Index over ``length`` bars."""
    typical_price = (high + low + close) / 3.0
    raw_money_flow = typical_price * volume

    tp_change = typical_price.diff()
    # Mask the leading NaN price-change so the rolling window sums only real
    # changes; otherwise the first MFI value prints a bar early off length-1 changes.
    positive_flow = raw_money_flow.where(tp_change > 0.0, 0.0).mask(tp_change.isna())
    negative_flow = raw_money_flow.where(tp_change < 0.0, 0.0).mask(tp_change.isna())

    positive_mf = positive_flow.rolling(length).sum()
    negative_mf = negative_flow.rolling(length).sum()

    money_ratio = positive_mf / negative_mf
    out = 100.0 - (100.0 / (1.0 + money_ratio))
    # Up-only window (no negative flow, some positive) -> MFI 100. A fully flat
    # window (no money flow at all) stays undefined (NaN), not a misleading 100.
    out = out.where((negative_mf != 0.0) | (positive_mf == 0.0), 100.0)
    return out
