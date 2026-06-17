import pandas as pd

from backtests.validate import _carry_in_signals


def _signals(values, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="D", tz="UTC")
    return pd.Series(values, index=idx)


def _mask_from(signals, start):
    return signals.index >= pd.Timestamp(start, tz="UTC")


def test_carry_in_seeds_open_long_at_window_start():
    # Entry (+1) at bar 0, exit (-1) never before the window -> still long entering the window.
    s = _signals([1, 0, 0, 0, 0])
    mask = _mask_from(s, "2020-01-03")  # window starts at bar 2, all zeros
    w = _carry_in_signals(s, mask)
    assert int(w.iloc[0]) == 1  # carried-in long seeded so the spanning trade isn't dropped


def test_carry_in_no_seed_when_flat_entering_window():
    # Entered then exited before the window -> flat at the boundary, nothing to carry in.
    s = _signals([1, -1, 0, 0, 0])
    mask = _mask_from(s, "2020-01-03")
    w = _carry_in_signals(s, mask)
    assert int(w.iloc[0]) == 0


def test_carry_in_does_not_clobber_existing_signal():
    # Already long, and the first in-window bar is itself an exit -> leave it (valid exit).
    s = _signals([1, 0, -1, 0, 0])
    mask = _mask_from(s, "2020-01-03")  # bar 2 is the -1 exit
    w = _carry_in_signals(s, mask)
    assert int(w.iloc[0]) == -1
