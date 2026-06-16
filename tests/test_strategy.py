import numpy as np
import pandas as pd

from bot.models import SignalType
from bot.strategy import StochRsiMfiParams, StochRsiMfiStrategy, compute_signals


def _oscillating_df(n: int = 300) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    wave = np.sin(np.linspace(0, 16 * np.pi, n))
    close = 100 + 20 * wave
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 1_000_000 * (1 + 0.5 * wave),
        },
        index=idx,
    )


def test_compute_signals_columns_and_domain():
    out = compute_signals(_oscillating_df(), StochRsiMfiParams())
    for col in ("stochrsi_k", "stochrsi_d", "mfi", "bull_div", "bear_div", "signal", "confidence"):
        assert col in out.columns
    assert set(out["signal"].unique()).issubset({-1, 0, 1})
    conf = out["confidence"]
    assert ((conf >= 0) & (conf <= 1)).all()


def test_signals_fire_with_relaxed_thresholds():
    # Relaxed thresholds guarantee crossovers translate into entries/exits; disable the
    # trend filter so this exercises the raw oscillator regardless of the shipped default.
    params = StochRsiMfiParams(
        use_divergence=False,
        use_trend_filter=False,
        stoch_oversold=50.0,
        stoch_overbought=50.0,
        mfi_oversold=100.0,
        mfi_overbought=0.0,
    )
    out = compute_signals(_oscillating_df(), params)
    assert (out["signal"] == 1).any()
    assert (out["signal"] == -1).any()


def test_generate_returns_decision():
    decision = StochRsiMfiStrategy().generate(_oscillating_df(), "TEST")
    assert decision.symbol == "TEST"
    assert isinstance(decision.signal, SignalType)
    assert 0.0 <= decision.confidence <= 1.0
    assert "mfi" in decision.indicators


def test_trend_filter_suppresses_longs_in_downtrend():
    n = 300
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    wave = np.sin(np.linspace(0, 16 * np.pi, n))
    close = 100 - 0.15 * np.arange(n) + 6 * wave  # downward drift + oscillation
    df = pd.DataFrame(
        {
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 1_000_000 * (1 + 0.5 * wave),
        },
        index=idx,
    )
    base = dict(
        use_divergence=False,
        stoch_oversold=50.0,
        stoch_overbought=50.0,
        mfi_oversold=100.0,
        mfi_overbought=0.0,
    )
    no_filter = compute_signals(df, StochRsiMfiParams(**base, use_trend_filter=False))
    filtered = compute_signals(df, StochRsiMfiParams(**base, use_trend_filter=True, trend_sma=20))
    # In a downtrend the filter blocks most "buy the dip" entries.
    assert (filtered["signal"] == 1).sum() < (no_filter["signal"] == 1).sum()


def test_min_bars_accounts_for_trend_filter():
    assert StochRsiMfiParams(use_trend_filter=False).min_bars == 80  # div_lookback 60 + 20
    assert StochRsiMfiParams().min_bars == 220  # shipped default: trend_sma 200 + 20
    assert StochRsiMfiParams(trend_sma=250).min_bars == 270


def test_trend_filter_blocks_longs_with_insufficient_history():
    # Fewer bars than trend_sma -> SMA all NaN -> no longs (the live never-trade boundary).
    n = 150
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    close = 100 + 10 * np.sin(np.linspace(0, 12 * np.pi, n))
    df = pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1_000_000.0},
        index=idx,
    )
    out = compute_signals(df, StochRsiMfiParams(trend_sma=200))  # default filter ON
    assert out["sma_trend"].isna().all()
    assert (out["signal"] == 1).sum() == 0


def test_missing_columns_raises():
    bad = pd.DataFrame({"close": [1, 2, 3]})
    try:
        StochRsiMfiStrategy().generate(bad, "X")
    except ValueError:
        return
    raise AssertionError("expected ValueError for missing OHLCV columns")


def test_make_strategy_registry():
    import pytest

    from bot.strategy import StochRsiMfiStrategy, TrendMomentumStrategy, make_strategy

    assert isinstance(make_strategy("trend_momentum"), TrendMomentumStrategy)
    assert isinstance(make_strategy("stoch_rsi_mfi"), StochRsiMfiStrategy)
    with pytest.raises(ValueError):
        make_strategy("does_not_exist")
