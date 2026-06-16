"""Purged walk-forward validation — a distribution of out-of-sample results.

validate.py uses ONE in-sample/out-of-sample split, which is high-variance and lets the
cutoff be (consciously or not) tuned. This walks forward over N folds: for each fold the
best param is chosen using ONLY prior data (with an embargo gap before the test fold), then
evaluated on the unseen fold. The spread of those out-of-fold results — median, worst, and
the fraction of folds that stayed positive — is a far better honesty check than one number,
and the selected-param stability across folds shows whether the "best" config is robust.

(This is the practical contiguous walk-forward relative of combinatorial purged CV: each
train/test window is contiguous so it fits a single Backtest run; full CPCV with
non-contiguous test combinations is a further extension.)

    python -m backtests.walk_forward --strategy trend_momentum --universe etf --folds 5
"""

from __future__ import annotations

import argparse
import warnings
from datetime import date, timedelta

import numpy as np
from alpaca.common.exceptions import APIError

from backtests.strategies import REGISTRY
from backtests.universe import resolve_universe
from backtests.validate import _aggregate, _metrics, _run_window
from bot.config import load_settings
from bot.data.historical import HistoricalData, parse_timeframe

warnings.filterwarnings("ignore")


def _fold_windows(start: date, end: date, folds: int, embargo_days: int):
    """Anchored walk-forward windows. Returns a list of (train_start, train_end, test_start,
    test_end) ISO-string tuples: train is everything before the fold (minus an embargo gap),
    test is the fold. The first segment seeds the initial train, so there are `folds` tests."""
    span = (end - start).days
    seg = span / (folds + 1)
    points = [start + timedelta(days=int(seg * i)) for i in range(folds + 2)]
    windows = []
    for i in range(folds):
        test_start, test_end = points[i + 1], points[i + 2]
        train_end = test_start - timedelta(days=embargo_days)
        windows.append((str(start), str(train_end), str(test_start), str(test_end)))
    return windows


def main() -> None:
    ap = argparse.ArgumentParser(description="Purged walk-forward validation")
    ap.add_argument("--strategy", choices=list(REGISTRY), default="trend_momentum")
    ap.add_argument("--universe", default="etf")
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--years-back", type=float, default=8.0)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--embargo-days", type=int, default=5)
    ap.add_argument("--min-trades", type=float, default=4.0)
    ap.add_argument("--timeframe", default="1Day")
    ap.add_argument("--cash", type=float, default=100_000.0)
    ap.add_argument("--commission", type=float, default=0.0005)
    args = ap.parse_args()

    entry = REGISTRY[args.strategy]
    compute, build_grid, pkey = entry["signals"], entry["grid"], entry["pkey"]
    symbols, windows_pit, biased = (args.symbols, None, True) if args.symbols else resolve_universe(args.universe)
    if biased:
        print("WARNING: survivorship-biased universe — results are an upper bound.\n")

    settings = load_settings()
    data = HistoricalData(settings)
    tf = parse_timeframe(args.timeframe)
    lookback = int(args.years_back * 365)

    feed = "sip"
    bars = {}
    for sym in symbols:
        try:
            df = data.get_bars(sym, tf, lookback_days=lookback, use_cache=False, feed=feed)
        except APIError:
            feed = "iex"
            df = data.get_bars(sym, tf, lookback_days=lookback, use_cache=False, feed=feed)
        if windows_pit and sym in windows_pit:
            lo, hi = windows_pit[sym]
            df = df[(df.index.date >= lo) & (df.index.date <= hi)]
        if not df.empty and len(df) >= 200:
            bars[sym] = df

    grid = build_grid()
    sig_cache = {(sym, pkey(p)): compute(df, p)["signal"] for sym, df in bars.items() for p in grid}

    def eval_param(key, start, end):
        recs = [m for sym, df in bars.items()
                if (s := _run_window(df, sig_cache[(sym, key)], start, end, args.cash, args.commission))
                is not None and (m := _metrics(s))]
        return _aggregate(recs)

    start = min(df.index.min().date() for df in bars.values())
    end = max(df.index.max().date() for df in bars.values())
    print(f"[{args.strategy}] {len(bars)} symbols via {feed}; {args.folds} walk-forward folds "
          f"{start} -> {end}\n")

    header = f"{'fold (train_end -> test)':<34} {'pick':<22} {'IS-shrp':>7} {'OOS-shrp':>8} {'OOS-ret%':>8} {'beatBH%':>8}"
    print(header)
    print("-" * len(header))
    oos_sharpes, oos_rets, picks = [], [], []
    for i, (tr_s, tr_e, te_s, te_e) in enumerate(_fold_windows(start, end, args.folds, args.embargo_days)):
        ranked = sorted(
            ((pkey(p), eval_param(pkey(p), tr_s, tr_e)) for p in grid),
            key=lambda kv: kv[1]["sharpe"] if not np.isnan(kv[1]["sharpe"]) else -9, reverse=True,
        )
        eligible = [x for x in ranked if x[1]["trades"] >= args.min_trades]
        best_key, is_m = (eligible or ranked)[0]
        test_m = eval_param(best_key, te_s, te_e)
        picks.append(best_key)
        oos_sharpes.append(test_m["sharpe"])
        oos_rets.append(test_m["ret"])
        label = f"{tr_e} -> {te_s[:7]}"
        print(f"{label:<34} {best_key:<22} {is_m['sharpe']:>7.2f} {test_m['sharpe']:>8.2f} "
              f"{test_m['ret']:>8.1f} {test_m['beat']:>8.0f}")

    valid = [s for s in oos_sharpes if s == s]
    pos = sum(1 for r in oos_rets if r == r and r > 0)
    stability = "stable" if len(set(picks)) <= 2 else "unstable -> overfit risk"
    tail = (f"folds positive: {pos}/{len(oos_rets)} | "
            f"distinct configs picked: {len(set(picks))}/{len(picks)} ({stability})")
    if valid:
        print(f"\nOOS Sharpe: median {np.nanmedian(valid):.2f}, worst {min(valid):.2f} "
              f"(n={len(valid)}) | {tail}")
    else:
        print(f"\nNo valid OOS folds (no trades produced a Sharpe) | {tail}")


if __name__ == "__main__":
    main()
