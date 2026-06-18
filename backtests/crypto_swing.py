"""Crypto swing backtest: mean-reversion entries + the shared swing exit logic + crypto fees.

Event-loop over intraday crypto bars (NO look-ahead: signal on bar i's close -> enter at bar
i+1's open; exits use each subsequent bar's high/low/close via bot.strategy.swing_exits, the
SAME logic the live bot will use). Reports per-pair trade stats + return vs buy & hold.

    python -m backtests.crypto_swing --pairs BTC/USD ETH/USD SOL/USD --timeframe 1Hour --days 365
"""

from __future__ import annotations

import argparse
import warnings

import numpy as np

from bot.config import load_settings
from bot.data.historical import HistoricalData, parse_timeframe
from bot.strategy.mean_reversion import MeanReversionParams, compute_signals
from bot.strategy.swing_exits import ExitParams, check_exit

warnings.filterwarnings("ignore")


def backtest_pair(df, mr: MeanReversionParams, ep: ExitParams, fee: float):
    sig = compute_signals(df, mr)["signal"].to_numpy()
    o, h, l, c = (df[x].to_numpy(dtype=float) for x in ("open", "high", "low", "close"))
    equity, in_pos, entry, hw, held = 1.0, False, 0.0, 0.0, 0
    rets, holds, reasons = [], [], []
    curve = [1.0]
    for i in range(1, len(df)):
        if in_pos:
            held += 1
            hw = max(hw, h[i])
            ex, px, why = check_exit(entry, hw, held, h[i], l[i], c[i], ep)
            if not ex and sig[i] == -1:
                ex, px, why = True, c[i], "signal"
            if ex:
                r = px / entry - 1.0 - 2.0 * fee  # round-trip fee
                equity *= (1.0 + r)
                rets.append(r); holds.append(held); reasons.append(why)
                curve.append(equity); in_pos = False
        elif sig[i - 1] == 1:  # entry signalled on the prior bar -> fill at this bar's open
            entry, hw, held, in_pos = o[i], h[i], 0, True
    return rets, holds, reasons, curve


def main() -> None:
    ap = argparse.ArgumentParser(description="Crypto swing (mean-reversion) backtest")
    ap.add_argument("--pairs", nargs="*", default=["BTC/USD", "ETH/USD", "SOL/USD"])
    ap.add_argument("--timeframe", default="1Hour")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--fee", type=float, default=0.001, help="per-side crypto fee (0.1% default)")
    ap.add_argument("--rsi", type=int, default=14)
    ap.add_argument("--oversold", type=float, default=30.0)
    ap.add_argument("--trend-sma", type=int, default=100)
    ap.add_argument("--tp", type=float, default=0.04)
    ap.add_argument("--sl", type=float, default=0.025)
    ap.add_argument("--trail", type=float, default=0.03)
    ap.add_argument("--max-bars", type=int, default=48)
    args = ap.parse_args()

    data = HistoricalData(load_settings())
    tf = parse_timeframe(args.timeframe)
    mr = MeanReversionParams(rsi_length=args.rsi, oversold=args.oversold, trend_sma=args.trend_sma)
    ep = ExitParams(args.tp, args.sl, args.trail, args.max_bars)
    print(f"mean-reversion swing | {args.timeframe} | {args.days}d | fee {args.fee*1e4:.0f}bps/side | "
          f"RSI{args.rsi}<{args.oversold:.0f}, trendSMA{args.trend_sma} | TP {args.tp:.0%}/SL {args.sl:.0%}/"
          f"trail {args.trail:.0%}/{args.max_bars}bars\n")
    print(f"{'pair':<10}{'trades':>7}{'win%':>6}{'avgHold':>8}{'avgRet%':>8}{'total%':>8}{'CAGR%':>7}{'maxDD%':>7}{'B&H%':>8}")
    for sym in args.pairs:
        df = data.get_crypto_bars(sym, tf, lookback_days=args.days)
        if df.empty or len(df) < args.trend_sma + 50:
            print(f"{sym:<10} insufficient data ({len(df)} bars)")
            continue
        rets, holds, reasons, curve = backtest_pair(df, mr, ep, args.fee)
        bh = df["close"].iloc[-1] / df["close"].iloc[0] - 1.0
        if not rets:
            print(f"{sym:<10}{0:>7}  (no trades)                                          {bh*100:>8.0f}")
            continue
        arr, eq = np.array(rets), np.array(curve)
        years = max((df.index[-1] - df.index[0]).days / 365.25, 1e-9)
        total = eq[-1] - 1.0
        dd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
        print(f"{sym:<10}{len(arr):>7}{(arr>0).mean()*100:>6.0f}{np.mean(holds):>8.0f}{arr.mean()*100:>8.2f}"
              f"{total*100:>8.0f}{((1+total)**(1/years)-1)*100:>7.0f}{dd*100:>7.0f}{bh*100:>8.0f}")


if __name__ == "__main__":
    main()
