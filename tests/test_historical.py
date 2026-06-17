from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from bot.data.historical import _ET, _drop_unclosed_period


def _today():
    return (datetime.now(_ET) if _ET else datetime.now(timezone.utc)).date()


def _df(dates):
    # Daily bars are stamped ~04:00-05:00 UTC, so their UTC calendar date is the trading date.
    idx = pd.DatetimeIndex([pd.Timestamp(d.year, d.month, d.day, 4, 0, tz="UTC") for d in dates])
    return pd.DataFrame({"close": [float(i) for i in range(len(dates))]}, index=idx)


def test_drop_unclosed_daily_drops_todays_partial_bar():
    today = _today()
    df = _df([today - timedelta(days=2), today - timedelta(days=1), today])
    out = _drop_unclosed_period(df, TimeFrame(1, TimeFrameUnit.Day))
    assert len(out) == 2
    assert out.index[-1].date() == today - timedelta(days=1)  # last bar is the last CLOSED session


def test_drop_unclosed_daily_keeps_fully_closed_history():
    today = _today()
    df = _df([today - timedelta(days=3), today - timedelta(days=2), today - timedelta(days=1)])
    out = _drop_unclosed_period(df, TimeFrame(1, TimeFrameUnit.Day))
    assert len(out) == 3  # nothing in-progress -> unchanged


def test_drop_unclosed_intraday_is_noop():
    today = _today()
    df = _df([today - timedelta(days=1), today])
    out = _drop_unclosed_period(df, TimeFrame(15, TimeFrameUnit.Minute))
    assert len(out) == 2  # intraday is handled by the 16-min end clamp, not by period-dropping


def test_drop_unclosed_empty_safe():
    assert _drop_unclosed_period(pd.DataFrame(), TimeFrame(1, TimeFrameUnit.Day)).empty
