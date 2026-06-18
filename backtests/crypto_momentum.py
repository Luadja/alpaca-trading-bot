"""Crypto momentum / breakout backtest (trend-FOLLOWING, the opposite of mean-reversion).

Donchian-channel turtle logic: enter when price breaks above the prior N-bar high (buy
strength), exit when it breaks below the prior M-bar low (let winners run, cut losers). NO
fixed take-profit -- the whole point is to ride a trend instead of capping it at +X%. An
optional trailing stop caps give-back. Long-only, one position per pair.

NO look-ahead: the breakout/exit channels are built from bars strictly BEFORE the current bar
(rolling().shift(1)); an entry signalled by bar i's close fills at bar i+1's open.

    python -m backtests.crypto_momentum --pairs BTC/USD ETH/USD SOL/USD --timeframe 1Day --days 1460
"""

from __future__ import annotations

import argparse
import warnings

import numpy as np

from bot.config import load_settings
from bot.data.historical import HistoricalData, parse_timeframe

warnings.filterwarnings("ignore")


def backtest_pair(df, entry_lb: int, exit_lb: int, trail: float, fee: float):
    """Donchian breakout entry / channel exit, optional trailing stop. Returns (rets, holds, curve)."""
    high, low, close, opn = (df[x].to_numpy(dtype=float) for x in ("high", "low", "close", "open"))
    n = len(df)
    # Channels from the PRIOR window only (shift by 1) -> no look-ahead.
    upper = np.full(n, np.nan)   # highest high of the prior entry_lb bars
    lower = np.full(n, np.nan)   # lowest low of the prior exit_lb bars
    for i in range(n):
        if i - 1 >= entry_lb:
            upper[i] = high[i - entry_lb:i].max()
        if i - 1 >= exit_lb:
            lower[i] = low[i - exit_lb:i].min()

    equity, in_pos, entry, hw, held = 1.0, False, 0.0, 0.0, 0
    rets, holds, curve = [], [], [1.0]
    enter_next = False
    for i in range(1, n):
        if enter_next and not in_pos:
            entry, hw, held, in_pos = opn[i], high[i], 0, True
            enter_next = False
        elif in_pos:
            held += 1
            hw = max(hw, high[i])
            stop = hw * (1.0 - trail) if trail > 0 else -np.inf
            exit_px = why = None
            if low[i] <= stop:                       # trailing stop hit intrabar
                exit_px, why = stop, "trail"
            elif not np.isnan(lower[i]) and close[i] <= lower[i]:  # broke the channel low
                exit_px, why = close[i], "channel"
            if exit_px is not None:
                r = exit_px / entry - 1.0 - 2.0 * fee
                equity *= (1.0 + r)
                rets.append(r); holds.append(held); curve.append(equity); in_pos = False
        # signal for NEXT bar's open: breakout above the prior-N-bar high
        if not in_pos and not np.isnan(upper[i]) and close[i] >= upper[i]:
            enter_next = True
    return rets, holds, curve


def main() -> None:
    ap = argparse.ArgumentParser(description="Crypto momentum / breakout backtest")
    ap.add_argument("--pairs", nargs="*", default=["BTC/USD", "ETH/USD", "SOL/USD"])
    ap.add_argument("--timeframe", default="1Day")
    ap.add_argument("--days", type=int, default=1460)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--entry-lb", type=int, default=20, help="breakout lookback (prior N-bar high)")
    ap.add_argument("--exit-lb", type=int, default=10, help="exit lookback (prior M-bar low)")
    ap.add_argument("--trail", type=float, default=0.0, help="trailing stop %% off the high (0 = off)")
    args = ap.parse_args()

    data = HistoricalData(load_settings())
    tf = parse_timeframe(args.timeframe)
    print(f"momentum/breakout | {args.timeframe} | {args.days}d | fee {args.fee*1e4:.0f}bps/side | "
          f"enter>{args.entry_lb}bar-high, exit<{args.exit_lb}bar-low, trail {args.trail:.0%}\n")
    print(f"{'pair':<10}{'trades':>7}{'win%':>6}{'avgHold':>8}{'avgRet%':>8}{'total%':>8}{'CAGR%':>7}{'maxDD%':>7}{'B&H%':>8}")
    tot_eq = []
    for sym in args.pairs:
        df = data.get_crypto_bars(sym, tf, lookback_days=args.days)
        if df.empty or len(df) < args.entry_lb + 50:
            print(f"{sym:<10} insufficient data ({len(df)} bars)")
            continue
        rets, holds, curve = backtest_pair(df, args.entry_lb, args.exit_lb, args.trail, args.fee)
        bh = df["close"].iloc[-1] / df["close"].iloc[0] - 1.0
        if not rets:
            print(f"{sym:<10}{0:>7}  (no trades)                                          {bh*100:>8.0f}")
            continue
        arr, eq = np.array(rets), np.array(curve)
        years = max((df.index[-1] - df.index[0]).days / 365.25, 1e-9)
        total = eq[-1] - 1.0
        dd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
        tot_eq.append(total)
        print(f"{sym:<10}{len(arr):>7}{(arr>0).mean()*100:>6.0f}{np.mean(holds):>8.0f}{arr.mean()*100:>8.2f}"
              f"{total*100:>8.0f}{((1+total)**(1/years)-1)*100:>7.0f}{dd*100:>7.0f}{bh*100:>8.0f}")
    if tot_eq:
        print(f"\n{'BASKET avg total%':<24}{np.mean(tot_eq)*100:>8.0f}   (equal-weight across pairs)")


if __name__ == "__main__":
    main()
