"""Historical bar fetching with a local Parquet cache.

Returns a tidy single-symbol DataFrame indexed by timestamp with lowercase
open/high/low/close/volume columns — the shape every strategy expects.

Free-tier note: on the Basic plan you can pull full-market (SIP) history as long
as the query window ends >= 15 minutes ago. The default IEX feed is real-time but
covers only ~3% of volume; for accurate historical bars prefer feed="delayed_sip".
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from bot.config import Settings

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - falls back if tzdata is missing
    _ET = None

# Timeframes whose final bar is "still forming" intraday (a day/week/month is not closed
# until its session ends). Intraday bars are excluded by the 16-min end clamp instead.
_PERIOD_UNITS = (TimeFrameUnit.Day, TimeFrameUnit.Week, TimeFrameUnit.Month)


def _drop_unclosed_period(df: pd.DataFrame, timeframe: TimeFrame) -> pd.DataFrame:
    """Drop the trailing bar if its period has not closed yet.

    On a daily timeframe, polling intraday returns TODAY's partial bar; computing the 50/200
    cross, the regime gate, or the entry price on it reads a half-formed close (an intraday
    spike that reverses by the close would fire a phantom signal). The live loop runs only
    during market hours, so the current period's bar is always incomplete when we fetch."""
    unit = getattr(timeframe, "unit", None)
    if df.empty or unit not in _PERIOD_UNITS:
        return df
    today = (datetime.now(_ET) if _ET else datetime.now(timezone.utc)).date()
    bar_date = df.index[-1].date()  # daily bars are stamped ~04:00-05:00 UTC => same calendar date
    if unit is TimeFrameUnit.Day:
        unclosed = bar_date >= today
    elif unit is TimeFrameUnit.Week:
        unclosed = bar_date.isocalendar()[:2] >= today.isocalendar()[:2]
    else:  # Month
        unclosed = (bar_date.year, bar_date.month) >= (today.year, today.month)
    return df.iloc[:-1] if unclosed else df

_UNIT_MAP = {
    "min": TimeFrameUnit.Minute,
    "hour": TimeFrameUnit.Hour,
    "day": TimeFrameUnit.Day,
    "week": TimeFrameUnit.Week,
    "month": TimeFrameUnit.Month,
}


def parse_timeframe(text: str) -> TimeFrame:
    """Parse strings like '1Day', '15Min', '1Hour' into an alpaca-py TimeFrame."""
    m = re.fullmatch(r"(\d+)\s*(min|hour|day|week|month)s?", text.strip().lower())
    if not m:
        raise ValueError(f"Unrecognized timeframe: {text!r}")
    amount, unit = int(m.group(1)), _UNIT_MAP[m.group(2)]
    return TimeFrame(amount, unit)


def _feed(name: str) -> DataFeed:
    return {
        "iex": DataFeed.IEX,
        "sip": DataFeed.SIP,
        "delayed_sip": DataFeed.DELAYED_SIP,
    }.get(name.lower(), DataFeed.IEX)


class HistoricalData:
    def __init__(self, settings: Settings, cache_dir: str = "data/cache") -> None:
        settings.assert_keys()
        self.client = StockHistoricalDataClient(settings.api_key, settings.api_secret)
        self.feed = _feed(settings.feed)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def latest_price(self, symbol: str) -> float | None:
        """Most recent trade price (real-time IEX, free) for anchoring marketable limits;
        None on any error so the caller can fall back to a market order."""
        try:
            req = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
            return float(self.client.get_stock_latest_trade(req)[symbol].price)
        except Exception:
            return None

    def get_bars(
        self,
        symbol: str,
        timeframe: TimeFrame,
        lookback_days: int = 400,
        use_cache: bool = True,
        feed: str | None = None,
    ) -> pd.DataFrame:
        cache_file = self.cache_dir / f"{symbol}_{timeframe.value}.parquet"
        if use_cache and cache_file.exists():
            return pd.read_parquet(cache_file)

        # End 16 min in the past so free-tier SIP queries are never gated.
        end = datetime.now(timezone.utc) - timedelta(minutes=16)
        start = end - timedelta(days=lookback_days)

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            feed=self.feed if feed is None else _feed(feed),
            # Split/dividend-adjusted so indicators see a continuous series across
            # corporate actions (raw bars would show fake gaps at splits).
            adjustment=Adjustment.ALL,
        )
        bars = self.client.get_stock_bars(request)
        df = bars.df
        if df.empty:
            return df

        # alpaca-py returns a (symbol, timestamp) multi-index; flatten to one symbol.
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level="symbol")
        df = df[["open", "high", "low", "close", "volume"]].sort_index()
        # Never expose the current, still-forming period's bar (look-ahead / live divergence).
        df = _drop_unclosed_period(df, timeframe)

        if use_cache:
            df.to_parquet(cache_file)
        return df
