import numpy as np
import pandas as pd

from bot.strategy import StochRsiMfiStrategy, TrendMomentumParams, TrendMomentumStrategy
from dashboard.charts import build_price_figure


def _df(close: np.ndarray, vol_wave: np.ndarray | None = None) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=len(close), freq="D")
    volume = 1_000_000.0 * (1 + 0.5 * vol_wave) if vol_wave is not None else 1_000_000.0
    return pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": volume},
        index=idx,
    )


def test_trend_figure_has_price_and_smas():
    close = 100 + np.cumsum(np.random.default_rng(0).normal(0, 1, 300))
    sig = TrendMomentumStrategy(TrendMomentumParams(fast_sma=10, slow_sma=30)).signals(_df(close))
    fig = build_price_figure(sig, "TEST", "trend_momentum")
    names = [t.name for t in fig.data]
    assert "Close" in names
    assert "SMA fast" in names and "SMA slow" in names


def test_stoch_figure_has_oscillator_panel():
    wave = np.sin(np.linspace(0, 16 * np.pi, 300))
    sig = StochRsiMfiStrategy().signals(_df(100 + 10 * wave, wave))
    fig = build_price_figure(sig, "TEST", "stoch_rsi_mfi")
    names = [t.name for t in fig.data]
    assert "MFI" in names  # oscillator panel rendered
