"""Adversarial robustness battery for the chosen crypto-momentum config (default enter>20-day
high / exit<5-day low). Tries to BREAK the edge, not confirm it:

  1. Full-sample basket (median/mean/%win/DD) -- the baseline claim.
  2. Walk-forward halves: run first-half and second-half separately. A real edge survives BOTH;
     a config that only works in one regime is fitted.
  3. Outlier robustness: drop the top-3 winning pairs and recompute. If the median collapses,
     the "edge" was 3 lucky coins.
  4. Exit-lookback sensitivity: fine sweep exit in {3..10} at the chosen entry. A cliff = fragile;
     a plateau = robust.
  5. Cost stress: re-run at 25 bps/side (3x the base fee) for slippage/spread realism.

NOTE on survivorship: the universe is pairs that EXIST with 4y history on Alpaca TODAY, so dead
coins are excluded -> live results will be worse than any backtest here. Reported, not fixable.

    python -m backtests.crypto_momentum_validate --entry-lb 20 --exit-lb 5 --days 1460
"""

from __future__ import annotations

import argparse
import warnings

import numpy as np

from backtests.crypto_momentum import backtest_pair
from backtests.crypto_momentum_sweep import DEFAULT_PAIRS
from bot.config import load_settings
from bot.data.historical import HistoricalData, parse_timeframe

warnings.filterwarnings("ignore")


def _total_dd(df, e, x, trail, fee):
    rets, _h, curve = backtest_pair(df, e, x, trail, fee)
    if not rets:
        return None
    eq = np.array(curve)
    return eq[-1] - 1.0, float((eq / np.maximum.accumulate(eq) - 1.0).min())


def _basket(frames, e, x, trail, fee, min_len=None):
    rows = []
    for sym, df in frames.items():
        if min_len and len(df) < min_len:
            continue
        if len(df) < e + 50:
            continue
        r = _total_dd(df, e, x, trail, fee)
        if r is not None:
            rows.append((sym, r[0], r[1]))
    if not rows:
        return None
    tot = np.array([r[1] for r in rows])
    dd = np.array([r[2] for r in rows])
    return {"rows": rows, "median": float(np.median(tot)), "mean": float(tot.mean()),
            "winfrac": float((tot > 0).mean()), "dd": float(dd.mean()), "n": len(rows)}


def _line(label, b):
    if b is None:
        print(f"  {label:<28} (no trades)")
    else:
        print(f"  {label:<28} median {b['median']*100:>6.0f}%  mean {b['mean']*100:>6.0f}%  "
              f"win {b['winfrac']*100:>3.0f}%  avgDD {b['dd']*100:>5.0f}%  pairs {b['n']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", nargs="*", default=DEFAULT_PAIRS)
    ap.add_argument("--timeframe", default="1Day")
    ap.add_argument("--days", type=int, default=1460)
    ap.add_argument("--entry-lb", type=int, default=20)
    ap.add_argument("--exit-lb", type=int, default=5)
    ap.add_argument("--trail", type=float, default=0.0)
    ap.add_argument("--fee", type=float, default=0.001)
    args = ap.parse_args()

    data = HistoricalData(load_settings())
    tf = parse_timeframe(args.timeframe)
    frames = {}
    for sym in args.pairs:
        try:
            df = data.get_crypto_bars(sym, tf, lookback_days=args.days)
        except Exception as e:  # noqa: BLE001
            continue
        if not df.empty and len(df) >= 80:
            frames[sym] = df
    e, x = args.entry_lb, args.exit_lb
    print(f"VALIDATION | enter>{e}-day high / exit<{x}-day low | {args.days}d | "
          f"{len(frames)} pairs | fee {args.fee*1e4:.0f}bps\n")

    print("1. Full sample:")
    full = _basket(frames, e, x, args.trail, args.fee)
    _line("baseline", full)

    print("\n2. Walk-forward halves (edge must survive BOTH):")
    first = {s: df.iloc[: len(df) // 2] for s, df in frames.items()}
    second = {s: df.iloc[len(df) // 2:] for s, df in frames.items()}
    _line("first half", _basket(first, e, x, args.trail, args.fee))
    _line("second half", _basket(second, e, x, args.trail, args.fee))

    print("\n3. Outlier robustness (drop top-3 winners):")
    if full:
        top3 = {s for s, _t, _d in sorted(full["rows"], key=lambda r: -r[1])[:3]}
        kept = {s: df for s, df in frames.items() if s not in top3}
        print(f"  dropped: {', '.join(sorted(top3))}")
        _line("without top-3", _basket(kept, e, x, args.trail, args.fee))

    print("\n4. Exit-lookback sensitivity at entry %d (plateau vs cliff):" % e)
    for xx in [3, 4, 5, 6, 7, 8, 10]:
        _line(f"exit<{xx}-day low", _basket(frames, e, xx, args.trail, args.fee))

    print("\n5. Cost stress (25 bps/side = 3x):")
    _line("fee 25bps", _basket(frames, e, x, args.trail, 0.0025))


if __name__ == "__main__":
    main()
