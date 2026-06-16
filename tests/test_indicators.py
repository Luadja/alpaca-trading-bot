import numpy as np
import pandas as pd

from bot.indicators import mfi, regular_divergence, rsi, stoch_rsi


def _sine_close(n: int = 300) -> pd.Series:
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.Series(100 + 20 * np.sin(np.linspace(0, 12 * np.pi, n)), index=idx)


def test_rsi_all_gains_is_100():
    close = pd.Series(np.arange(1, 60, dtype=float))
    assert rsi(close, 14).iloc[-1] == 100.0


def test_rsi_in_range():
    r = rsi(_sine_close(), 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_rsi_flat_market_is_not_100():
    # No price movement -> RSI undefined (NaN), not a misleading 100.
    assert pd.isna(rsi(pd.Series([50.0] * 40), 14).iloc[-1])


def test_mfi_first_valid_uses_full_window():
    close = _sine_close(40)
    high, low = close + 1, close - 1
    volume = pd.Series(1_000_000, index=close.index, dtype=float)
    m = mfi(high, low, close, volume, 14)
    # 14-period MFI needs 14 real price-changes -> first value at position 14, not 13.
    assert m.iloc[:14].isna().all()
    assert not pd.isna(m.iloc[14])


def test_stoch_rsi_columns_and_range():
    out = stoch_rsi(_sine_close())
    assert list(out.columns) == ["stochrsi", "stochrsi_k", "stochrsi_d"]
    k = out["stochrsi_k"].dropna()
    assert ((k >= 0) & (k <= 100)).all()


def test_mfi_in_range():
    close = _sine_close()
    high, low = close + 1, close - 1
    volume = pd.Series(1_000_000, index=close.index, dtype=float)
    m = mfi(high, low, close, volume, 14).dropna()
    assert ((m >= 0) & (m <= 100)).all()


def test_bullish_divergence_detected():
    # Price makes a lower low (7 -> 6); indicator makes a higher low (30 -> 35).
    price = pd.Series([10, 9, 8, 7, 8, 9, 10, 9, 8, 6, 7, 8, 9, 10], dtype=float)
    indicator = pd.Series([50, 45, 40, 30, 40, 45, 48, 46, 44, 35, 42, 46, 49, 50], dtype=float)
    res = regular_divergence(price, indicator, left=2, right=2, lookback=60)
    assert res["bull_div"].any()
    # Confirmed at the second pivot (index 9) + right (2) = index 11.
    assert bool(res["bull_div"].iloc[11])
    assert not res["bear_div"].any()
