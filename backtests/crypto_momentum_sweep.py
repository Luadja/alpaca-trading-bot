"""Robustness sweep for the crypto momentum/breakout strategy.

Grids entry-lookback x exit-lookback across a BROAD pair universe and scores each config by
robustness, not peak return: a config that only works because of one outlier coin (e.g. SOL
+226%) is data-mining, not edge. We therefore rank by MEDIAN pair return and the FRACTION of
pairs profitable, and look for a plateau of good neighbouring cells rather than a lone spike.

Fetches each pair's daily bars ONCE, then reuses backtest_pair() from crypto_momentum across
every grid cell. NO look-ahead (channels are built from prior bars only).

    python -m backtests.crypto_momentum_sweep --days 1460 --trail 0
"""

from __future__ import annotations

import argparse
import warnings

import numpy as np

from backtests.crypto_momentum import backtest_pair
from bot.config import load_settings
from bot.data.historical import HistoricalData, parse_timeframe

warnings.filterwarnings("ignore")

# Broad-ish Alpaca USD spot universe; pairs without enough history are skipped automatically.
DEFAULT_PAIRS = [
    "BTC/USD", "ETH/USD", "SOL/USD", "LTC/USD", "LINK/USD", "AVAX/USD", "DOGE/USD",
    "BCH/USD", "UNI/USD", "AAVE/USD", "DOT/USD", "SHIB/USD", "CRV/USD", "MKR/USD",
    "GRT/USD", "BAT/USD", "XTZ/USD", "YFI/USD", "SUSHI/USD",
]

ENTRY_LBS = [10, 15, 20, 30, 40, 50, 60]
EXIT_LBS = [5, 10, 15, 20, 30]


def _pair_result(df, entry_lb, exit_lb, trail, fee):
    """Return (total_return, maxDD) for one pair/config, or None if it never traded."""
    if exit_lb >= entry_lb:  # exit channel should be shorter than entry channel
        return None
    rets, _holds, curve = backtest_pair(df, entry_lb, exit_lb, trail, fee)
    if not rets:
        return None
    eq = np.array(curve)
    total = eq[-1] - 1.0
    dd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
    return total, dd


def main() -> None:
    ap = argparse.ArgumentParser(description="Crypto momentum robustness sweep")
    ap.add_argument("--pairs", nargs="*", default=DEFAULT_PAIRS)
    ap.add_argument("--timeframe", default="1Day")
    ap.add_argument("--days", type=int, default=1460)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--trail", type=float, default=0.0)
    ap.add_argument("--min-pairs", type=int, default=8, help="min pairs that must trade for a valid cell")
    args = ap.parse_args()

    data = HistoricalData(load_settings())
    tf = parse_timeframe(args.timeframe)

    # Fetch each pair once.
    frames = {}
    for sym in args.pairs:
        try:
            df = data.get_crypto_bars(sym, tf, lookback_days=args.days)
        except Exception as e:  # noqa: BLE001 - sweep should survive one bad symbol
            print(f"  skip {sym}: {e}")
            continue
        if not df.empty and len(df) >= 80:
            frames[sym] = df
    print(f"loaded {len(frames)} pairs with daily history: {', '.join(frames)}\n")

    # Evaluate the grid.
    cells = {}  # (entry, exit) -> list of (sym, total, dd)
    for e in ENTRY_LBS:
        for x in EXIT_LBS:
            if x >= e:
                continue
            rows = []
            for sym, df in frames.items():
                if len(df) < e + 50:
                    continue
                r = _pair_result(df, e, x, args.trail, args.fee)
                if r is not None:
                    rows.append((sym, r[0], r[1]))
            if len(rows) >= args.min_pairs:
                cells[(e, x)] = rows

    def stats(rows):
        tot = np.array([r[1] for r in rows])
        dd = np.array([r[2] for r in rows])
        return (float(np.median(tot)), float(tot.mean()), float((tot > 0).mean()),
                float(dd.mean()), len(rows))

    print(f"momentum sweep | {args.timeframe} | {args.days}d | trail {args.trail:.0%} | "
          f"basket of {len(frames)} pairs\n")
    print("MEDIAN pair total% per (entry x exit) cell  [robustness: median, not mean -> outlier-proof]")
    header = "entry\\exit" + "".join(f"{x:>8}" for x in EXIT_LBS)
    print(header)
    for e in ENTRY_LBS:
        line = f"{e:>9}"
        for x in EXIT_LBS:
            if (e, x) in cells:
                med = stats(cells[(e, x)])[0]
                line += f"{med*100:>8.0f}"
            else:
                line += f"{'.':>8}"
        print(line)

    print("\n% of pairs PROFITABLE per cell  [consistency]")
    print(header)
    for e in ENTRY_LBS:
        line = f"{e:>9}"
        for x in EXIT_LBS:
            if (e, x) in cells:
                frac = stats(cells[(e, x)])[2]
                line += f"{frac*100:>8.0f}"
            else:
                line += f"{'.':>8}"
        print(line)

    # Rank by a robustness score: median return + a bonus for breadth of winners,
    # penalised by drawdown. Deliberately NOT peak mean (which one outlier can dominate).
    scored = []
    for (e, x), rows in cells.items():
        med, mean, frac, dd, n = stats(rows)
        score = med + 0.5 * frac + 0.25 * dd  # dd is negative -> penalty
        scored.append((score, e, x, med, mean, frac, dd, n))
    scored.sort(reverse=True)

    print("\nTop configs by robustness score (median + breadth - drawdown):")
    print(f"{'entry':>6}{'exit':>6}{'median%':>9}{'mean%':>8}{'%win':>7}{'avgDD%':>8}{'pairs':>7}")
    for s, e, x, med, mean, frac, dd, n in scored[:8]:
        print(f"{e:>6}{x:>6}{med*100:>9.0f}{mean*100:>8.0f}{frac*100:>7.0f}{dd*100:>8.0f}{n:>7}")

    if scored:
        _s, be, bx, *_ = scored[0]
        print(f"\nMost robust cell: enter>{be}-day high / exit<{bx}-day low. Per-pair breakdown:")
        rows = sorted(cells[(be, bx)], key=lambda r: -r[1])
        print(f"{'pair':<10}{'total%':>8}{'maxDD%':>8}")
        for sym, tot, dd in rows:
            print(f"{sym:<10}{tot*100:>8.0f}{dd*100:>8.0f}")


if __name__ == "__main__":
    main()
