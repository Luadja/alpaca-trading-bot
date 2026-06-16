import numpy as np
import pandas as pd

from bot.models import SignalType
from bot.strategy import TrendMomentumParams, TrendMomentumStrategy
from bot.strategy.trend_momentum import compute_signals


def _df(close: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=len(close), freq="D")
    return pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1_000_000.0},
        index=idx,
    )


def test_min_bars():
    assert TrendMomentumParams().min_bars == 220  # max(50, 200) + 20
    assert TrendMomentumParams(fast_sma=20, slow_sma=100).min_bars == 120
    assert TrendMomentumParams(slow_sma=100, use_regime_filter=True, regime_sma=200).min_bars == 220


def test_signal_domain_and_columns():
    close = 100 + np.cumsum(np.random.default_rng(0).normal(0, 1, 400))
    out = compute_signals(_df(close), TrendMomentumParams(fast_sma=10, slow_sma=30))
    for col in ("sma_fast", "sma_slow", "signal", "confidence"):
        assert col in out.columns
    assert set(out["signal"].unique()).issubset({-1, 0, 1})
    assert ((out["confidence"] >= 0) & (out["confidence"] <= 1)).all()


def test_golden_and_death_cross_fire():
    # Flat -> uptrend -> downtrend produces a golden cross then a death cross.
    close = np.concatenate([
        np.full(40, 100.0),
        np.linspace(100, 160, 50),
        np.linspace(160, 100, 50),
    ])
    out = compute_signals(_df(close), TrendMomentumParams(fast_sma=10, slow_sma=30))
    assert (out["signal"] == 1).any()   # golden cross (enter)
    assert (out["signal"] == -1).any()  # death cross (exit)
    # Entry must precede the exit.
    first_entry = out.index[out["signal"] == 1][0]
    first_exit = out.index[out["signal"] == -1][0]
    assert first_entry < first_exit


def test_regime_filter_blocks_entry_below_regime():
    close = np.concatenate([np.full(40, 100.0), np.linspace(100, 160, 50)])
    df = _df(close)
    p = dict(fast_sma=10, slow_sma=30, regime_sma=200)  # 90 bars < 200 -> regime NaN
    no_regime = compute_signals(df, TrendMomentumParams(**p, use_regime_filter=False))
    with_regime = compute_signals(df, TrendMomentumParams(**p, use_regime_filter=True))
    assert (no_regime["signal"] == 1).any()
    assert (with_regime["signal"] == 1).sum() == 0  # regime SMA never valid -> no entries


def test_generate_returns_decision():
    close = 100 + np.cumsum(np.random.default_rng(1).normal(0, 1, 300))
    decision = TrendMomentumStrategy(TrendMomentumParams(fast_sma=10, slow_sma=30)).generate(
        _df(close), "TEST"
    )
    assert decision.symbol == "TEST"
    assert isinstance(decision.signal, SignalType)
    assert 0.0 <= decision.confidence <= 1.0
