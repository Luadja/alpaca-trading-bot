"""Out-of-sample / walk-forward validation.

The single-window sweep can overfit: ranking #1 on one bull run proves nothing. This
script guards against that:

  1. IN-SAMPLE: rank the param grid by mean Sharpe across a diversified basket on an
     early window (default 2018-2021, incl. the 2018 selloff + 2020 COVID crash).
  2. OUT-OF-SAMPLE: take the IS-best params (chosen WITHOUT seeing OOS) and evaluate
     them on a later, non-overlapping window (2022 bear -> 2026 bull). The edge is only
     credible if OOS performance holds up near IS.
  3. REGIME breakdown: evaluate the IS-best across distinct market regimes.

Signals are computed on the FULL series (indicators are causal) then sliced to each
window, so windows start fully warmed up with no look-ahead. Backtests use full-equity
sizing (signal-quality test), not the live risk manager.

Usage:
    python -m backtests.validate
    python -m backtests.validate --years-back 8 --is-end 2022-01-01
"""

from __future__ import annotations

import argparse
import dataclasses
import warnings

import numpy as np
import pandas as pd
from alpaca.common.exceptions import APIError
from backtesting import Backtest

from backtests.backtest_stoch_rsi_mfi import SrsiMfiBacktest
from backtests.param_sweep import _bt_df
from backtests.strategies import REGISTRY
from backtests.universe import resolve_universe
from bot.config import load_settings
from bot.data.historical import HistoricalData, parse_timeframe

warnings.filterwarnings("ignore")

REGIMES = [
    ("2018 (Q4 selloff)", "2018-01-01", "2019-01-01"),
    ("2020 (COVID)", "2020-01-01", "2021-01-01"),
    ("2022 (bear)", "2022-01-01", "2023-01-01"),
    ("2023-24 (bull)", "2023-01-01", "2025-01-01"),
    ("2025-26", "2025-01-01", "2027-01-01"),
]

HEADER = (
    f"{'window / config':<34} {'ret%':>7} {'sharpe':>7} {'trades':>7} "
    f"{'win%':>6} {'maxdd%':>7} {'beatBH%':>8} {'n':>3}"
)


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def _metrics(stats) -> dict:
    n = int(stats["# Trades"])
    return {
        "ret": float(stats["Return [%]"]),
        "sharpe": float(stats["Sharpe Ratio"]),
        "trades": n,
        "win": float(stats["Win Rate [%]"]) if n > 0 else np.nan,
        "dd": float(stats["Max. Drawdown [%]"]),
        "bh": float(stats["Buy & Hold Return [%]"]),
    }


def _run_window(df, signals, start, end, cash, commission):
    mask = (df.index >= _ts(start)) & (df.index < _ts(end))
    if mask.sum() < 30:
        return None
    return Backtest(
        _bt_df(df.loc[mask], signals.loc[mask]),
        SrsiMfiBacktest,
        cash=cash,
        commission=commission,
    ).run()


def _aggregate(records: list[dict]) -> dict:
    if not records:  # e.g. a regime window with no data in the loaded range
        nan = float("nan")
        return {"ret": nan, "sharpe": nan, "trades": 0.0, "win": nan, "dd": nan, "beat": nan, "n": 0}
    d = pd.DataFrame(records)
    d["beat"] = d["ret"] > d["bh"]
    return {
        "ret": d["ret"].mean(),
        "sharpe": d["sharpe"].mean(),
        "trades": d["trades"].mean(),
        "win": d["win"].mean(),
        "dd": d["dd"].mean(),
        "beat": 100 * d["beat"].mean(),
        "n": len(d),
    }


def _row(label: str, m: dict) -> str:
    win = f"{m['win']:.0f}" if not np.isnan(m["win"]) else "-"
    return (
        f"{label:<34} {m['ret']:>7.1f} {m['sharpe']:>7.2f} {m['trades']:>7.1f} "
        f"{win:>6} {m['dd']:>7.1f} {m['beat']:>8.0f} {m['n']:>3}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Out-of-sample validation")
    ap.add_argument("--strategy", choices=list(REGISTRY), default="stoch_rsi_mfi")
    ap.add_argument("--universe", default="etf",
                    help="'etf' (survivorship-free, default), 'megacap' (biased), or a CSV path")
    ap.add_argument("--symbols", nargs="*", default=None, help="explicit symbols (overrides --universe)")
    ap.add_argument("--years-back", type=float, default=8.0)
    ap.add_argument("--is-end", default="2022-01-01", help="in-sample/out-of-sample cutoff")
    ap.add_argument("--timeframe", default="1Day")
    ap.add_argument("--cash", type=float, default=100_000.0)
    ap.add_argument("--commission", type=float, default=0.0005)
    ap.add_argument("--cost-sweep", action="store_true",
                    help="after ranking, sweep round-trip cost to find the breakeven where the edge dies")
    ap.add_argument(
        "--min-trades", type=float, default=4.0,
        help="min avg in-sample trades for a param set to be eligible as 'best' "
        "(ranking by Sharpe alone rewards configs that barely trade)",
    )
    ap.add_argument("--trend-filter", action="store_true",
                    help="(stoch_rsi_mfi only) enable the price>SMA trend filter")
    ap.add_argument("--trend-sma", type=int, default=200)
    args = ap.parse_args()

    entry = REGISTRY[args.strategy]
    compute, build_grid, pkey = entry["signals"], entry["grid"], entry["pkey"]

    if args.symbols:
        symbols, windows, biased = args.symbols, None, True
    else:
        symbols, windows, biased = resolve_universe(args.universe)
    if biased:
        print("WARNING: survivorship-BIASED universe (hand-picked survivors) - results are "
              "an upper bound, not an estimate. Use --universe etf for an honest baseline.\n")

    settings = load_settings()
    data = HistoricalData(settings)
    tf = parse_timeframe(args.timeframe)
    lookback = int(args.years_back * 365)

    feed = "sip"
    bars: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            df = data.get_bars(sym, tf, lookback_days=lookback, use_cache=False, feed=feed)
        except APIError as exc:
            if feed == "sip":
                print(f"  SIP unavailable ({exc}); falling back to IEX.")
                feed = "iex"
                df = data.get_bars(sym, tf, lookback_days=lookback, use_cache=False, feed=feed)
            else:
                raise
        if windows and sym in windows:  # point-in-time clip: only bars while investable
            lo, hi = windows[sym]
            df = df[(df.index.date >= lo) & (df.index.date <= hi)]
        if not df.empty and len(df) >= 200:
            bars[sym] = df
        else:
            print(f"  skip {sym}: {len(df)} bars")

    is_start = str(df.index.min().date())  # earliest available
    is_end = args.is_end
    oos_start, oos_end = args.is_end, "2027-01-01"
    print(f"Loaded {len(bars)} symbols via {feed} feed.")
    print(f"In-sample: {is_start} -> {is_end}  |  Out-of-sample: {oos_start} -> now\n")

    grid = build_grid()
    default_p = entry["default"]()
    # --trend-filter only applies to a strategy that has that field (stoch_rsi_mfi).
    has_trend_field = any(f.name == "use_trend_filter" for f in dataclasses.fields(default_p))
    if args.trend_filter and has_trend_field:
        grid = [dataclasses.replace(p, use_trend_filter=True, trend_sma=args.trend_sma) for p in grid]
        default_p = dataclasses.replace(default_p, use_trend_filter=True, trend_sma=args.trend_sma)
        print(f"Trend filter ON: longs only when price > {args.trend_sma}-bar SMA.\n")
    elif args.trend_filter:
        print(f"  (--trend-filter ignored: {args.strategy} has no use_trend_filter param)\n")

    # Compute every (symbol, param) signal once; reuse across all windows.
    sig_cache: dict[tuple[str, str], pd.Series] = {}
    is_records: dict[str, list[dict]] = {}
    for sym, df in bars.items():
        for p in grid:
            key = pkey(p)
            sig = compute(df, p)["signal"]
            sig_cache[(sym, key)] = sig
            stats = _run_window(df, sig, is_start, is_end, args.cash, args.commission)
            if stats is not None:
                is_records.setdefault(key, []).append(_metrics(stats))
        sig_cache[(sym, "DEFAULT")] = compute(df, default_p)["signal"]

    is_agg = {k: _aggregate(v) for k, v in is_records.items()}
    ranked = sorted(
        is_agg.items(),
        key=lambda kv: kv[1]["sharpe"] if not np.isnan(kv[1]["sharpe"]) else -9,
        reverse=True,
    )
    # Sharpe-ranking alone rewards near-inactive configs (tiny drawdown). Require a
    # minimum trade count so the chosen "best" is actually an active strategy.
    eligible = [(k, m) for k, m in ranked if m["trades"] >= args.min_trades]
    best_key = (eligible or ranked)[0][0]

    def eval_across(param_key: str, start: str, end: str, commission: float | None = None) -> dict:
        commission = args.commission if commission is None else commission
        recs = []
        for sym, df in bars.items():
            stats = _run_window(df, sig_cache[(sym, param_key)], start, end, args.cash, commission)
            if stats is not None:
                recs.append(_metrics(stats))
        return _aggregate(recs)

    print("=== IN-SAMPLE ranking (top 6 by mean Sharpe) ===")
    print(HEADER)
    print("-" * len(HEADER))
    for key, m in ranked[:6]:
        print(_row(key, m))
    print(f"\nIS-best chosen: {best_key}\n")

    print("=== OVERFITTING CHECK: IS-best & default, in-sample vs out-of-sample ===")
    print(HEADER)
    print("-" * len(HEADER))
    print(_row(f"IS-best  | in-sample", is_agg[best_key]))
    print(_row(f"IS-best  | OUT-of-sample", eval_across(best_key, oos_start, oos_end)))
    print(_row(f"default  | in-sample", eval_across("DEFAULT", is_start, is_end)))
    print(_row(f"default  | OUT-of-sample", eval_across("DEFAULT", oos_start, oos_end)))

    print(f"\n=== REGIME breakdown - IS-best ({best_key}) ===")
    print(HEADER)
    print("-" * len(HEADER))
    for name, start, end in REGIMES:
        print(_row(name, eval_across(best_key, start, end)))

    if args.cost_sweep:
        print("\n=== COST SENSITIVITY (IS-best, out-of-sample) ===")
        print(f"{'cost/side bps':>13} {'ret%':>7} {'sharpe':>7} {'beatBH%':>8}")
        breakeven = None
        for bps in (0, 5, 10, 20, 35, 50):
            m = eval_across(best_key, oos_start, oos_end, commission=bps / 10_000.0)
            print(f"{bps:>13} {m['ret']:>7.1f} {m['sharpe']:>7.2f} {m['beat']:>8.0f}")
            if breakeven is None and m["ret"] <= 0:
                breakeven = bps
        print(
            f"Edge dies (mean OOS return <= 0) at ~{breakeven} bps/side."
            if breakeven is not None
            else "Edge survives through 50 bps/side."
        )


if __name__ == "__main__":
    main()
