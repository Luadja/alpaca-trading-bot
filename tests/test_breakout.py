import numpy as np
import pandas as pd

from bot.models import SignalType
from bot.strategy import make_strategy
from bot.strategy.breakout import BreakoutParams, BreakoutStrategy, compute_signals


def _df(closes, highs=None, lows=None):
    idx = pd.date_range("2021-01-01", periods=len(closes), freq="D", tz="UTC")
    h = highs if highs is not None else closes
    low = lows if lows is not None else closes
    return pd.DataFrame({"open": closes, "high": h, "low": low,
                         "close": closes, "volume": [1.0] * len(closes)}, index=idx)


def test_breakout_signal_on_new_high():
    closes = list(range(10, 30))  # strictly increasing -> every bar breaks the prior high
    out = compute_signals(_df(closes), BreakoutParams(entry_lookback=3, exit_lookback=2))
    assert int(out["signal"].iloc[-1]) == 1


def test_channel_break_signal_on_new_low():
    closes = list(range(30, 10, -1))  # strictly decreasing -> breaks the prior low
    out = compute_signals(_df(closes), BreakoutParams(entry_lookback=3, exit_lookback=2))
    assert int(out["signal"].iloc[-1]) == -1


def test_hold_inside_channel():
    # Oscillate inside a band so the last close is neither a new high nor a new low.
    closes = [10, 12, 11, 13, 12, 11.5, 12.0]
    out = compute_signals(_df(closes), BreakoutParams(entry_lookback=4, exit_lookback=4))
    assert int(out["signal"].iloc[-1]) == 0


def test_no_lookahead_channel_excludes_current_bar():
    # A spike on the LAST bar must not appear in its own entry channel (shift(1) excludes it).
    closes = [10, 10, 10, 10, 100]
    out = compute_signals(_df(closes), BreakoutParams(entry_lookback=3, exit_lookback=2))
    # upper for the last bar = max high of the 3 PRIOR bars (all 10), not the 100 spike.
    assert out["upper"].iloc[-1] == 10
    assert int(out["signal"].iloc[-1]) == 1  # 100 >= prior-high 10 -> breakout


def test_registry_and_generate():
    s = make_strategy("breakout")  # default params: entry_lookback=20 -> needs >20 bars
    assert isinstance(s, BreakoutStrategy)
    dec = s.generate(_df(list(range(10, 60))), "BTC/USD")  # 50 increasing bars
    assert dec.signal is SignalType.ENTER_LONG
    assert "breakout" in dec.reason
    assert dec.indicators["upper"] > 0


def test_min_bars():
    assert BreakoutParams(entry_lookback=20, exit_lookback=5).min_bars == 25
