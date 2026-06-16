"""Parameter sweep across a basket of symbols.

Backtests a grid of StochRSI/MFI thresholds (and divergence on/off) over several
symbols, then ranks parameter sets by their MEAN performance across the basket so
you don't overfit to a single name. Uses the full-market delayed_sip feed for bar
fidelity (free, since the data is >15 min old) and split/dividend-adjusted bars.

Research only — same caveats as any backtest (optimistic fills, no live slippage).

Usage:
    python -m backtests.param_sweep
    python -m backtests.param_sweep --symbols AAPL MSFT SPY --years 4 --timeframe 1Day
"""

from __future__ import annotations

import argparse
import warnings

import numpy as np
import pandas as pd
from backtesting import Backtest

from backtests.backtest_stoch_rsi_mfi import SrsiMfiBacktest
from bot.config import load_settings
from bot.data.historical import HistoricalData, parse_timeframe
from bot.strategy import StochRsiMfiParams, compute_signals

warnings.filterwarnings("ignore")

DEFAULT_BASKET = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM", "SPY", "QQQ"]


def build_grid() -> list[StochRsiMfiParams]:
    """Symmetric oversold/overbought bands + divergence on/off. 3x3x2 = 18 sets."""
    grid = []
    for stoch_os in (20, 30, 40):
        for mfi_os in (25, 35, 45):
            for div_required in (False, True):
                grid.append(
                    StochRsiMfiParams(
                        stoch_oversold=stoch_os,
                        stoch_overbought=100 - stoch_os,
                        mfi_oversold=mfi_os,
                        mfi_overbought=100 - mfi_os,
                        use_divergence=True,
                        divergence_required=div_required,
                    )
                )
    return grid


def pkey(p: StochRsiMfiParams) -> str:
    return (
        f"stoch{int(p.stoch_oversold)}/{int(p.stoch_overbought)} "
        f"mfi{int(p.mfi_oversold)}/{int(p.mfi_overbought)} "
        f"div={'Y' if p.divergence_required else 'N'}"
    )


def _bt_df(df: pd.DataFrame, signals: pd.Series) -> pd.DataFrame:
    out = df.rename(
        columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
    ).copy()
    out["signal"] = signals.to_numpy()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="StochRSI+MFI parameter sweep")
    ap.add_argument("--symbols", nargs="*", default=DEFAULT_BASKET)
    ap.add_argument("--years", type=float, default=3.0)
    ap.add_argument("--timeframe", default="1Day")
    ap.add_argument("--cash", type=float, default=100_000.0)
    ap.add_argument("--commission", type=float, default=0.0005)
    args = ap.parse_args()

    settings = load_settings()
    settings.feed = "delayed_sip"  # accurate full-market bars, free for data >15 min old
    data = HistoricalData(settings)
    tf = parse_timeframe(args.timeframe)

    # Fetch each symbol once and reuse across the whole grid.
    bars: dict[str, pd.DataFrame] = {}
    for sym in args.symbols:
        df = data.get_bars(sym, tf, lookback_days=int(args.years * 365), use_cache=False)
        if df.empty or len(df) < 150:
            print(f"  skip {sym}: only {len(df)} bars")
            continue
        bars[sym] = df
    print(f"Loaded {len(bars)} symbols, {len(build_grid())} param sets each "
          f"({args.years}yr {args.timeframe}).\n")

    grid = build_grid()
    rows = []
    for sym, df in bars.items():
        for p in grid:
            signals = compute_signals(df, p)["signal"]
            stats = Backtest(
                _bt_df(df, signals), SrsiMfiBacktest, cash=args.cash, commission=args.commission
            ).run()
            n = int(stats["# Trades"])
            rows.append(
                {
                    "key": pkey(p),
                    "symbol": sym,
                    "ret": float(stats["Return [%]"]),
                    "sharpe": float(stats["Sharpe Ratio"]),
                    "trades": n,
                    "win": float(stats["Win Rate [%]"]) if n > 0 else np.nan,
                    "dd": float(stats["Max. Drawdown [%]"]),
                    "bh": float(stats["Buy & Hold Return [%]"]),
                }
            )

    res = pd.DataFrame(rows)
    res["beat"] = res["ret"] > res["bh"]
    agg = (
        res.groupby("key")
        .agg(
            mean_ret=("ret", "mean"),
            mean_sharpe=("sharpe", "mean"),
            avg_trades=("trades", "mean"),
            mean_win=("win", "mean"),
            mean_dd=("dd", "mean"),
            beat_bh_pct=("beat", "mean"),
        )
        .reset_index()
    )
    agg["beat_bh_pct"] *= 100
    agg = agg.sort_values("mean_sharpe", ascending=False)

    bh_mean = float(res.groupby("symbol")["bh"].first().mean())
    print(f"Basket buy & hold mean return: {bh_mean:+.1f}%")
    print(f"Ranked by mean Sharpe across {len(bars)} symbols:\n")
    header = f"{'param set':<34} {'ret%':>7} {'sharpe':>7} {'trades':>7} {'win%':>6} {'maxdd%':>7} {'beatBH%':>8}"
    print(header)
    print("-" * len(header))
    for _, r in agg.iterrows():
        win = f"{r.mean_win:.0f}" if not np.isnan(r.mean_win) else "—"
        print(
            f"{r.key:<34} {r.mean_ret:>7.1f} {r.mean_sharpe:>7.2f} "
            f"{r.avg_trades:>7.1f} {win:>6} {r.mean_dd:>7.1f} {r.beat_bh_pct:>8.0f}"
        )


if __name__ == "__main__":
    main()
