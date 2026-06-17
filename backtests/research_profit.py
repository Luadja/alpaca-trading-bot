"""Broad profitability battery on recent data: score many strategy levers IS / OOS / recent-3yr
with a Deflated-Sharpe multiple-testing guard + costs, all vs equal-weight buy & hold.

Caches the price panel to data/cache so re-runs (and parallel audit agents) don't re-hit the API.

    python -m backtests.research_profit --universe wide --years-back 10 [--refresh]
"""

from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import pandas as pd

from backtests import portfolio as pf
from backtests.deflated_sharpe import deflated_sharpe, expected_max_sharpe
from backtests.research_portfolio import load_bars
from backtests.universe import resolve_universe

warnings.filterwarnings("ignore")

OOS = "2022-01-01"      # out-of-sample cutoff
RECENT = "2023-06-20"   # ~last 3 years


def get_panel(universe: str, years: float, refresh: bool):
    cache = Path(f"data/cache/panel_{universe}_{int(years)}y.parquet")
    if cache.exists() and not refresh:
        return pd.read_parquet(cache), "cache"
    symbols, _, _ = resolve_universe(universe)
    bars, feed = load_bars(symbols, years, "1Day")
    panel = pf.build_panel(bars)
    cache.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(cache)
    return panel, feed


def _w(net, start, end):
    return pf.metrics(pf.slice_window(net.to_frame("r"), start, end)["r"])


def build(P):
    x252 = pf.weights_xsec(P, lookbacks=(252,), top_n=4)
    return {
        "buy&hold (benchmark)": pf.weights_equal_buy_hold(P),
        "risk-parity (smart B&H)": pf.weights_risk_parity(P),
        "trend 50/200 (the bot)": pf.weights_trend_equal(P, 50, 200),
        "trend 20/100 (fast)": pf.weights_trend_equal(P, 20, 100),
        "xsec mom 252 top4": x252,
        "xsec mom 252 top4 invvol": pf.weights_xsec(P, lookbacks=(252,), top_n=4, scheme="invvol"),
        "xsec mom blend 63/126/252 t4": pf.weights_xsec(P, lookbacks=(63, 126, 252), top_n=4),
        "xsec mom 252 t4 qtr-rebal": pf.weights_xsec(P, lookbacks=(252,), top_n=4, rebalance_days=63),
        "xsec mom 252 t3 (concentrated)": pf.weights_xsec(P, lookbacks=(252,), top_n=3),
        "dual mom 252 t4": pf.weights_dual_momentum(P, lookback=252, top_n=4),
        "dual mom 126 t4 (fast)": pf.weights_dual_momentum(P, lookback=126, top_n=4),
        "voltgt 12% on xsec252": pf.vol_target(x252, P, target_vol=0.12, max_gross=1.5),
        "50% xsec252 + 50% B&H": pf.blend((x252, 0.5), (pf.weights_equal_buy_hold(P), 0.5)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Profitability battery on recent data")
    ap.add_argument("--universe", default="wide")
    ap.add_argument("--years-back", type=float, default=10.0)
    ap.add_argument("--cost", type=float, default=0.0005)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    P, src = get_panel(args.universe, args.years_back, args.refresh)
    print(f"universe={args.universe} ({src}) | {P.shape[1]} assets | {P.index[0].date()} -> "
          f"{P.index[-1].date()} ({len(P)}d) | cost {args.cost*1e4:.0f}bps | OOS>={OOS}, recent>={RECENT}\n")

    nets = {k: pf.backtest(w, P, args.cost).net_returns for k, w in build(P).items()}
    bh_o = _w(nets["buy&hold (benchmark)"], OOS, None)["sharpe"]
    bh_r = _w(nets["buy&hold (benchmark)"], RECENT, None)["sharpe"]

    rows = [(k, _w(n, None, OOS), _w(n, OOS, None), _w(n, RECENT, None)) for k, n in nets.items()]
    rows.sort(key=lambda x: x[2]["sharpe"] if x[2]["sharpe"] == x[2]["sharpe"] else -9, reverse=True)

    print(f"{'strategy':<32}{'IS-sh':>6}{'OOS-sh':>7}{'OOS-CAGR':>9}{'OOS-DD':>8}{'REC-sh':>7}{'REC-CAGR':>9}{'beat':>6}")
    for k, i, o, r in rows:
        beat = "BOTH" if (o["sharpe"] > bh_o and r["sharpe"] > bh_r) else ("oos" if o["sharpe"] > bh_o else "")
        print(f"{k:<32}{i['sharpe']:>6.2f}{o['sharpe']:>7.2f}{o['cagr']*100:>8.1f}%{o['maxdd']*100:>7.1f}%"
              f"{r['sharpe']:>7.2f}{r['cagr']*100:>8.1f}%{beat:>6}")
    print(f"\nbenchmark buy&hold: OOS Sharpe {bh_o:.2f} | recent-3yr Sharpe {bh_r:.2f}")

    trials = [t for t in (_w(n, OOS, None)["sharpe_pp"] for n in nets.values()) if t == t]
    nobs = max(_w(n, OOS, None)["n"] for n in nets.values())
    print(f"DATA-MINING GUARD: best-of-{len(trials)} OOS Deflated Sharpe = "
          f"{deflated_sharpe(max(trials), trials, nobs):.2f} (>=0.95 => the winner survives the "
          f"multiple-testing penalty; below => likely luck) | luck SR0={expected_max_sharpe(trials):.3f}")


if __name__ == "__main__":
    main()
