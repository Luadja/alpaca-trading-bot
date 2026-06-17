"""Year x month return tables for the bot's candidate strategies, side by side (portfolio model).

Runs at PORTFOLIO level (the realistic model — the per-symbol harness overstates with
full-equity sizing) over historical data, net of transaction costs, and prints:
  * a yearly side-by-side comparison of all strategies,
  * a summary (total return / CAGR / maxDD / $ on starting cash),
  * each strategy's full year x month % matrix.
This is the SIGNAL-LEVEL estimate of the strategy/universe, not a tick replay of live execution.

    python -m backtests.monthly_returns --universe multiasset --years-back 10
"""

from __future__ import annotations

import argparse
import warnings

from backtests import portfolio as pf
from backtests.research_portfolio import load_bars
from backtests.universe import resolve_universe

warnings.filterwarnings("ignore")

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _monthly(net):
    return (1.0 + net).resample("ME").prod() - 1.0


def _summary(net, cash):
    tot = (1.0 + net).prod() - 1.0
    yrs = len(net) / 252.0
    eq = (1.0 + net).cumprod()
    maxdd = float((eq / eq.cummax() - 1.0).min())
    cagr = (1.0 + tot) ** (1.0 / yrs) - 1.0 if yrs > 0 else float("nan")
    return {"total": tot, "cagr": cagr, "maxdd": maxdd, "end": cash * (1.0 + tot)}


def _print_matrix(name, net):
    m = _monthly(net)
    print(f"\n--- {name}: net monthly % ---")
    print(f"{'Year':<6}" + "".join(f"{mo:>7}" for mo in MONTHS) + f"{'FY%':>9}")
    for yr in sorted(set(m.index.year)):
        row = f"{yr:<6}"
        for mo in range(1, 13):
            v = m[(m.index.year == yr) & (m.index.month == mo)]
            row += f"{v.iloc[0]*100:>7.1f}" if len(v) else f"{'-':>7}"
        fy = (1 + m[m.index.year == yr]).prod() - 1
        print(row + f"{fy*100:>9.1f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Side-by-side monthly/yearly return tables")
    ap.add_argument("--universe", default="multiasset")
    ap.add_argument("--years-back", type=float, default=10.0)
    ap.add_argument("--cost", type=float, default=0.0005)
    ap.add_argument("--cash", type=float, default=100_000.0)
    args = ap.parse_args()

    symbols, _, _ = resolve_universe(args.universe)
    bars, feed = load_bars(symbols, args.years_back, "1Day")
    panel = pf.build_panel(bars)

    strategies = {
        "TREND 50/200 (the bot)": pf.weights_trend_equal(panel, 50, 200),
        "BUY & HOLD (equal-wt)": pf.weights_equal_buy_hold(panel),
        "X-SECTIONAL MOM (L252,top4)": pf.weights_xsec_momentum(panel, lookback=252, top_n=4, rebalance_days=21),
        "DUAL MOMENTUM (L252,top4)": pf.weights_dual_momentum(panel, lookback=252, top_n=4, rebalance_days=21),
    }
    nets = {name: pf.backtest(w, panel, args.cost).net_returns for name, w in strategies.items()}
    monthlies = {name: _monthly(net) for name, net in nets.items()}
    names = list(strategies)

    print(f"{len(bars)} assets ({args.universe}) via {feed} | {panel.index[0].date()} -> "
          f"{panel.index[-1].date()} | cost {args.cost*1e4:.0f}bps/side\n")

    # 1) yearly side-by-side
    print("=== YEARLY RETURN (%) — side by side ===")
    print(f"{'Year':<6}" + "".join(f"{n.split()[0][:9]:>12}" for n in names))
    years = sorted(set(monthlies[names[0]].index.year))
    for yr in years:
        row = f"{yr:<6}"
        for n in names:
            mm = monthlies[n]
            fy = (1 + mm[mm.index.year == yr]).prod() - 1
            row += f"{fy*100:>12.1f}"
        print(row)

    # 2) summary
    print("\n=== SUMMARY (full period) ===")
    print(f"{'strategy':<30}{'total%':>9}{'CAGR%':>8}{'maxDD%':>9}{'$100k->':>12}")
    for n in names:
        s = _summary(nets[n], args.cash)
        print(f"{n:<30}{s['total']*100:>9.1f}{s['cagr']*100:>8.1f}{s['maxdd']*100:>9.1f}{('$'+format(s['end'],',.0f')):>12}")

    # 3) full monthly matrices
    for n in names:
        _print_matrix(n, nets[n])


if __name__ == "__main__":
    main()
