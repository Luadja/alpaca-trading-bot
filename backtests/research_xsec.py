"""Deepen cross-sectional momentum (#3) validation beyond the single IS/OOS split:

  1. WALK-FORWARD across folds — pick the best config on each train window, score it OOS, and
     check whether it beats equal-weight buy & hold in EACH fold (robust across regimes?).
  2. PARAM SENSITIVITY — sweep lookback / top_n / rebalance over the full OOS window and count
     how many combos beat buy & hold (a broad plateau = robust edge; one spike = luck).

    python -m backtests.research_xsec --universe multiasset --folds 5
"""

from __future__ import annotations

import argparse
import warnings

import numpy as np

from backtests import portfolio as pf
from backtests.research_portfolio import load_bars
from backtests.universe import resolve_universe
from backtests.walk_forward import _fold_windows

warnings.filterwarnings("ignore")

OOS_START = "2022-01-01"

BUILDERS = {"xsec": pf.weights_xsec_momentum, "dual": pf.weights_dual_momentum}
_BUILDER = pf.weights_xsec_momentum  # set in main() from --strategy


def _net(panel, cfg, cost):
    w = _BUILDER(panel, lookback=cfg["lookback"], skip=21,
                 top_n=cfg["top_n"], rebalance_days=cfg["rebalance"])
    return pf.backtest(w, panel, cost).net_returns


def _wm(net, start, end):
    return pf.metrics(pf.slice_window(net.to_frame("r"), start, end)["r"])


def _sharpe(m):
    return m["sharpe"] if not np.isnan(m["sharpe"]) else -9.0


def main() -> None:
    ap = argparse.ArgumentParser(description="Deep momentum validation (cross-sectional or dual)")
    ap.add_argument("--universe", default="multiasset")
    ap.add_argument("--strategy", choices=list(BUILDERS), default="xsec")
    ap.add_argument("--years-back", type=float, default=8.0)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--embargo-days", type=int, default=21)
    ap.add_argument("--cost", type=float, default=0.0005)
    args = ap.parse_args()

    global _BUILDER
    _BUILDER = BUILDERS[args.strategy]

    symbols, _, _ = resolve_universe(args.universe)
    bars, feed = load_bars(symbols, args.years_back, "1Day")
    panel = pf.build_panel(bars)
    print(f"STRATEGY={args.strategy} | {len(bars)} symbols via {feed}; panel {panel.index[0].date()} -> "
          f"{panel.index[-1].date()} ({len(panel)} days); cost {args.cost*1e4:.0f}bps/side\n")

    bench_net = pf.backtest(pf.weights_equal_buy_hold(panel), panel, args.cost).net_returns

    # ---- 1) WALK-FORWARD ----
    wf_grid = [{"lookback": lb, "top_n": n, "rebalance": 21} for lb in (126, 252) for n in (3, 4)]
    netmap = {tuple(c.values()): _net(panel, c, args.cost) for c in wf_grid}
    windows = _fold_windows(panel.index[0].date(), panel.index[-1].date(), args.folds, args.embargo_days)

    print("=== WALK-FORWARD: cross-sectional momentum (pick on train, score OOS) ===")
    hdr = f"{'fold test':<10} {'pick':<12} {'IS-shrp':>7} {'OOS-shrp':>8} {'OOS-CAGR':>8} {'B&H-shrp':>8} {'beatB&H':>7}"
    print(hdr); print("-" * len(hdr))
    oos_sh, beats, picks = [], [], []
    for tr_s, tr_e, te_s, te_e in windows:
        best = max(wf_grid, key=lambda c: _sharpe(_wm(netmap[tuple(c.values())], tr_s, tr_e)))
        ism = _wm(netmap[tuple(best.values())], tr_s, tr_e)
        oom = _wm(netmap[tuple(best.values())], te_s, te_e)
        bm = _wm(bench_net, te_s, te_e)
        beat = _sharpe(oom) > _sharpe(bm)
        lab = f"L{best['lookback']}t{best['top_n']}"
        oos_sh.append(oom["sharpe"]); beats.append(beat); picks.append(lab)
        print(f"{te_s[:7]:<10} {lab:<12} {ism['sharpe']:>7.2f} {oom['sharpe']:>8.2f} "
              f"{oom['cagr']:>8.1%} {bm['sharpe']:>8.2f} {('YES' if beat else 'no'):>7}")
    valid = [s for s in oos_sh if s == s]
    print(f"\nOOS Sharpe: median {np.nanmedian(valid):.2f}, worst {min(valid):.2f} | "
          f"beat B&H: {sum(beats)}/{len(beats)} folds | distinct configs: {len(set(picks))}/{len(picks)}")

    # ---- 2) PARAM SENSITIVITY (full OOS) ----
    bench_oos = _sharpe(_wm(bench_net, OOS_START, None))
    print(f"\n=== PARAM SENSITIVITY (OOS {OOS_START} -> now; benchmark B&H OOS Sharpe = {bench_oos:.2f}) ===")
    print(f"{'lookback':>8} {'top_n':>6} {'rebal':>6} {'OOS-shrp':>9} {'OOS-CAGR':>8} {'maxDD':>7}")
    sens = [{"lookback": lb, "top_n": n, "rebalance": rb}
            for lb in (126, 189, 252) for n in (2, 3, 4, 5) for rb in (21, 63)]
    n_beat = 0
    for c in sens:
        m = _wm(_net(panel, c, args.cost), OOS_START, None)
        won = _sharpe(m) > bench_oos
        n_beat += won
        print(f"{c['lookback']:>8} {c['top_n']:>6} {c['rebalance']:>6} "
              f"{m['sharpe']:>8.2f}{'*' if won else ' '} {m['cagr']:>8.1%} {m['maxdd']:>7.1%}")
    print(f"\n{n_beat}/{len(sens)} param combos beat buy & hold OOS  "
          f"(broad plateau => robust; only a few => likely luck).")


if __name__ == "__main__":
    main()
