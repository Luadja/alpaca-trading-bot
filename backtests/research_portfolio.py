"""Portfolio research runner: validate vol-targeting (#2) and cross-sectional momentum (#3)
against an equal-weight buy-and-hold benchmark, honestly.

For each strategy family it picks the in-sample-best config by Sharpe, reports OUT-OF-SAMPLE
(the overfitting check), guards the selection with a Deflated Sharpe over the configs tried,
and sweeps transaction cost. Everything is relative to just holding the universe equal-weight.

    python -m backtests.research_portfolio --universe multiasset --years-back 8
"""

from __future__ import annotations

import argparse
import warnings

import numpy as np
from alpaca.common.exceptions import APIError

from backtests import portfolio as pf
from backtests.deflated_sharpe import deflated_sharpe, expected_max_sharpe
from backtests.universe import resolve_universe
from bot.config import load_settings
from bot.data.historical import HistoricalData, parse_timeframe

warnings.filterwarnings("ignore")

IS_END = "2022-01-01"  # in-sample / out-of-sample cutoff (matches validate.py)


def load_bars(symbols, years_back, timeframe):
    settings = load_settings()
    data = HistoricalData(settings)
    tf = parse_timeframe(timeframe)
    lookback = int(years_back * 365)
    feed = "sip"
    bars = {}
    for sym in symbols:
        try:
            df = data.get_bars(sym, tf, lookback_days=lookback, use_cache=False, feed=feed)
        except APIError:
            feed = "iex"
            df = data.get_bars(sym, tf, lookback_days=lookback, use_cache=False, feed=feed)
        if not df.empty and len(df) >= 250:
            bars[sym] = df
        else:
            print(f"  skip {sym}: {len(df)} bars")
    return bars, feed


def eval_window(weights, panel, start, end, cost):
    """Backtest on the full panel (preserves warmup) then score only the [start,end) slice."""
    net = pf.backtest(weights, panel, cost).net_returns
    net_win = pf.slice_window(net.to_frame("r"), start, end)["r"]
    return pf.metrics(net_win)


def _row(label, m):
    def g(k, spec):
        v = m.get(k)
        return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else format(v, spec)
    return (f"{label:<28} {g('cagr','>7.1%')} {g('sharpe','>7.2f')} {g('sortino','>7.2f')} "
            f"{g('maxdd','>7.1%')} {g('vol','>6.1%')} {m.get('n','-'):>5}")


HDR = f"{'strategy':<28} {'CAGR':>7} {'sharpe':>7} {'sortino':>7} {'maxDD':>7} {'vol':>6} {'days':>5}"


def select_and_report(name, configs, panel, cost):
    """configs: list of (label, weights_df). Pick IS-best by Sharpe, report OOS, DSR over trials."""
    is_metrics = {lab: eval_window(w, panel, None, IS_END, cost) for lab, w in configs}
    ranked = sorted(is_metrics.items(),
                    key=lambda kv: kv[1]["sharpe"] if not np.isnan(kv[1]["sharpe"]) else -9, reverse=True)
    best_lab = ranked[0][0]
    best_w = dict(configs)[best_lab]
    is_best, oos_best = is_metrics[best_lab], eval_window(best_w, panel, IS_END, None, cost)

    trials_pp = [m["sharpe_pp"] for m in is_metrics.values() if not np.isnan(m.get("sharpe_pp", np.nan))]
    n_obs = max((m["n"] for m in is_metrics.values()), default=0)
    dsr = deflated_sharpe(is_best["sharpe_pp"], trials_pp, n_obs) if trials_pp and n_obs else float("nan")
    sr0 = expected_max_sharpe(trials_pp) if trials_pp else float("nan")

    print(f"\n=== {name} (IS-best of {len(configs)}: {best_lab}) ===")
    print(HDR)
    print(_row(f"{best_lab} | in-sample", is_best))
    print(_row(f"{best_lab} | OUT-of-sample", oos_best))
    print(f"Deflated Sharpe: {dsr:.2f} (bless if >=0.95) | luck SR0={sr0:.3f} vs observed "
          f"{is_best['sharpe_pp']:.3f} over {len(trials_pp)} trials")
    return best_lab, best_w, oos_best


def main() -> None:
    ap = argparse.ArgumentParser(description="Portfolio research: vol-targeting + cross-sectional momentum")
    ap.add_argument("--universe", default="multiasset")
    ap.add_argument("--years-back", type=float, default=8.0)
    ap.add_argument("--timeframe", default="1Day")
    ap.add_argument("--cost", type=float, default=0.0005, help="per-side cost (5bps default)")
    args = ap.parse_args()

    symbols, _, _ = resolve_universe(args.universe)
    bars, feed = load_bars(symbols, args.years_back, args.timeframe)
    if len(bars) < 3:
        raise SystemExit(f"need >=3 symbols with history; got {len(bars)}")
    panel = pf.build_panel(bars)
    print(f"Universe '{args.universe}': {len(bars)} symbols via {feed}; panel "
          f"{panel.index[0].date()} -> {panel.index[-1].date()} ({len(panel)} common days)")
    print(f"In-sample: start -> {IS_END} | Out-of-sample: {IS_END} -> now | cost {args.cost*1e4:.0f}bps/side\n")

    # Benchmark: equal-weight buy & hold (just own the universe).
    bh = pf.weights_equal_buy_hold(panel)
    print("=== BENCHMARK: equal-weight buy & hold ===")
    print(HDR)
    print(_row("buy&hold | in-sample", eval_window(bh, panel, None, IS_END, args.cost)))
    print(_row("buy&hold | OUT-of-sample", eval_window(bh, panel, IS_END, None, args.cost)))

    grid = [(20, 100), (50, 100), (50, 200)]

    # #2 Risk-adjusted sizing: equal-weight trend vs inverse-vol trend (same in-trend set + gross).
    eq = [(f"trend{f}/{s} equal", pf.weights_trend_equal(panel, f, s)) for f, s in grid]
    select_and_report("#2a EQUAL-WEIGHT TREND", eq, panel, args.cost)
    iv = [(f"trend{f}/{s} invvol", pf.weights_trend_invvol(panel, f, s)) for f, s in grid]
    select_and_report("#2b INVERSE-VOL TREND (vol-targeting)", iv, panel, args.cost)

    # #3 Cross-sectional momentum: rank-and-hold top-N, monthly rebalance.
    xs = [(f"xsec L{lb} top{n}", pf.weights_xsec_momentum(panel, lookback=lb, top_n=n))
          for lb in (126, 252) for n in (3, 4)]
    best_lab, best_w, _ = select_and_report("#3 CROSS-SECTIONAL MOMENTUM", xs, panel, args.cost)

    # Cost sensitivity on the cross-sectional winner (it rebalances monthly -> most cost-exposed).
    print(f"\n=== COST SENSITIVITY (#3 {best_lab}, out-of-sample) ===")
    print(f"{'cost/side bps':>13} {'CAGR':>8} {'sharpe':>8} {'maxDD':>8}")
    for bps in (0, 5, 10, 20, 35, 50):
        w = pf.weights_xsec_momentum(panel, **_parse_xsec(best_lab))
        m = eval_window(w, panel, IS_END, None, bps / 1e4)
        print(f"{bps:>13} {m['cagr']:>8.1%} {m['sharpe']:>8.2f} {m['maxdd']:>8.1%}")


def _parse_xsec(label: str) -> dict:
    # "xsec L252 top3" -> {lookback:252, top_n:3}
    parts = label.split()
    return {"lookback": int(parts[1][1:]), "top_n": int(parts[2][3:])}


if __name__ == "__main__":
    main()
