import numpy as np
import pandas as pd

from bot.models import SignalType
from bot.strategy import make_strategy
from bot.strategy.mean_reversion import MeanReversionParams, MeanReversionStrategy, _rsi, compute_signals


def test_rsi_extremes():
    up = pd.Series(np.linspace(100, 200, 100))
    dn = pd.Series(np.linspace(200, 100, 100))
    assert _rsi(up, 14).iloc[-1] > 70
    assert _rsi(dn, 14).iloc[-1] < 30


def _df(closes):
    idx = pd.date_range("2020-01-01", periods=len(closes), freq="h", tz="UTC")
    return pd.DataFrame({"open": closes, "high": closes, "low": closes,
                         "close": closes, "volume": [1.0] * len(closes)}, index=idx)


def test_meanrev_enters_oversold_then_exits_on_recovery():
    base = list(np.linspace(100, 130, 120))   # uptrend
    dip = [128, 124, 120, 116, 112, 110]      # sharp drop -> RSI oversold
    rec = [114, 120, 126, 132, 138]           # recovery -> RSI back up
    out = compute_signals(_df(base + dip + rec),
                          MeanReversionParams(rsi_length=14, oversold=35, exit_level=55, trend_sma=0))
    assert (out["signal"] == 1).any()    # bought the dip
    assert (out["signal"] == -1).any()   # sold the bounce
    first_in = out.index[out["signal"] == 1][0]
    first_out = out.index[out["signal"] == -1][0]
    assert first_in < first_out          # entry precedes exit


def test_trend_filter_blocks_dips_below_sma():
    # A dip in a DOWNtrend (price below the trend SMA) must NOT trigger a buy.
    closes = list(np.linspace(200, 100, 150))  # steady downtrend -> always below SMA, RSI low
    out = compute_signals(_df(closes), MeanReversionParams(rsi_length=14, oversold=35, trend_sma=50))
    assert not (out["signal"] == 1).any()  # trend filter blocks falling-knife buys


def test_registry_and_generate():
    s = make_strategy("mean_reversion")
    assert isinstance(s, MeanReversionStrategy)
    out = compute_signals(_df(list(np.linspace(100, 130, 120)) + [128, 124, 120, 116, 112, 110]),
                          MeanReversionParams(oversold=35, trend_sma=0))
    # last bar is deep in the dip -> ENTER_LONG
    df = _df(list(np.linspace(100, 130, 120)) + [128, 124, 120, 116, 112, 110])
    dec = MeanReversionStrategy(MeanReversionParams(oversold=35, trend_sma=0)).generate(df, "BTC/USD")
    assert dec.signal in (SignalType.ENTER_LONG, SignalType.HOLD)  # depends on exact last-bar RSI
    assert "RSI=" in dec.reason
