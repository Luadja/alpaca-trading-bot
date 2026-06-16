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
    # Relaxed thresholds guarantee crossovers translate into entries/exits.
    params = StochRsiMfiParams(
        use_divergence=False,
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


def test_missing_columns_raises():
    bad = pd.DataFrame({"close": [1, 2, 3]})
    try:
        StochRsiMfiStrategy().generate(bad, "X")
    except ValueError:
        return
    raise AssertionError("expected ValueError for missing OHLCV columns")
