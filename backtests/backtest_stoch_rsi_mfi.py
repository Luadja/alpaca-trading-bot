"""Backtest the StochRSI + MFI strategy with backtesting.py.

Research only — backtesting.py does NOT trade live and does NOT reproduce Alpaca's
real fills. Set a commission to approximate slippage/spread; treat results as
optimistic. (Alpaca equities are commission-free, so commission here stands in for
slippage, not fees.) Uses the SAME compute_signals() as the live strategy.

Usage:
    python -m backtests.backtest_stoch_rsi_mfi --symbol AAPL --years 3 --plot
"""

from __future__ import annotations

import argparse

from backtesting import Backtest
from backtesting import Strategy as BTStrategy

from bot.config import load_settings
from bot.data.historical import HistoricalData, parse_timeframe
from bot.strategy import StochRsiMfiParams, compute_signals


def _prepare(symbol: str, years: float):
    settings = load_settings()
    data = HistoricalData(settings)
    df = data.get_bars(
        symbol, parse_timeframe("1Day"), lookback_days=int(years * 365), use_cache=False
    )
    if df.empty:
        raise SystemExit(f"No historical data returned for {symbol}.")

    signals = compute_signals(df, StochRsiMfiParams())["signal"]
    bt_df = df.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    ).copy()
    bt_df["signal"] = signals.to_numpy()
    return bt_df


class SrsiMfiBacktest(BTStrategy):
    """Acts on the precomputed signal column: +1 enter long, -1 exit."""

    def init(self) -> None:
        self.sig = self.I(lambda s: s, self.data.signal, name="signal", plot=False)

    def next(self) -> None:
        s = self.sig[-1]
        if s == 1 and not self.position:
            self.buy()
        elif s == -1 and self.position:
            self.position.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest StochRSI+MFI")
    ap.add_argument("--symbol", default="AAPL")
    ap.add_argument("--years", type=float, default=3.0)
    ap.add_argument("--cash", type=float, default=100_000.0)
    ap.add_argument(
        "--commission", type=float, default=0.0005, help="approx slippage/spread per trade"
    )
    ap.add_argument("--plot", action="store_true")
    args = ap.parse_args()

    df = _prepare(args.symbol, args.years)
    bt = Backtest(df, SrsiMfiBacktest, cash=args.cash, commission=args.commission)
    stats = bt.run()
    print(stats)
    if args.plot:
        bt.plot()


if __name__ == "__main__":
    main()
