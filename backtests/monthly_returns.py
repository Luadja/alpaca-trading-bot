"""Year x month return table for the live bot's strategy (portfolio model).

Runs the trend strategy at PORTFOLIO level (the realistic model — the per-symbol harness
overstates with full-equity sizing) over historical data and prints a year x month % return
matrix with yearly totals, benchmarked against equal-weight buy & hold. Returns are net of
transaction costs. NOTE: this is a backtest of the strategy/universe, NOT a tick replay of the
live execution path (marketable limits, intraday fills); treat it as the signal-level estimate.

    python -m backtests.monthly_returns --universe multiasset --fast 50 --slow 200 --years-back 10
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


def main() -> None:
    ap = argparse.ArgumentParser(description="Monthly/yearly return table for the bot strategy")
    ap.add_argument("--universe", default="multiasset")
    ap.add_argument("--fast", type=int, default=50)
    ap.add_argument("--slow", type=int, default=200)
    ap.add_argument("--years-back", type=float, default=10.0)
    ap.add_argument("--cost", type=float, default=0.0005)
    ap.add_argument("--cash", type=float, default=100_000.0)
    args = ap.parse_args()

    symbols, _, _ = resolve_universe(args.universe)
    bars, feed = load_bars(symbols, args.years_back, "1Day")
    panel = pf.build_panel(bars)
    bot_net = pf.backtest(pf.weights_trend_equal(panel, args.fast, args.slow), panel, args.cost).net_returns
    bh_net = pf.backtest(pf.weights_equal_buy_hold(panel), panel, args.cost).net_returns
    bm, hm = _monthly(bot_net), _monthly(bh_net)

    print(f"BOT = trend{args.fast}/{args.slow} equal-weight, {len(bars)} assets ({args.universe}) via {feed}")
    print(f"Data {panel.index[0].date()} -> {panel.index[-1].date()} | cost {args.cost*1e4:.0f}bps/side | "
          f"net monthly % returns (last column = same-year buy&hold for comparison)\n")
    print(f"{'Year':<6}" + "".join(f"{m:>7}" for m in MONTHS) + f"{'FY%':>9}{'B&H%':>8}")
    for yr in sorted(set(bm.index.year)):
        row = f"{yr:<6}"
        for mo in range(1, 13):
            v = bm[(bm.index.year == yr) & (bm.index.month == mo)]
            row += f"{v.iloc[0]*100:>7.1f}" if len(v) else f"{'-':>7}"
        fy = (1 + bm[bm.index.year == yr]).prod() - 1
        hy = (1 + hm[hm.index.year == yr]).prod() - 1
        row += f"{fy*100:>9.1f}{hy*100:>8.1f}"
        print(row)

    tot_b, tot_h = (1 + bot_net).prod() - 1, (1 + bh_net).prod() - 1
    yrs = len(bot_net) / 252
    eq = (1 + bot_net).cumprod()
    dd_b = float((eq / eq.cummax() - 1).min())
    eqh = (1 + bh_net).cumprod()
    dd_h = float((eqh / eqh.cummax() - 1).min())
    print(f"\nTOTAL  bot: {tot_b*100:>6.1f}%  CAGR {((1+tot_b)**(1/yrs)-1)*100:>5.1f}%  maxDD {dd_b*100:>6.1f}%  "
          f"-> ${args.cash*(1+tot_b):,.0f} from ${args.cash:,.0f}")
    print(f"       B&H: {tot_h*100:>6.1f}%  CAGR {((1+tot_h)**(1/yrs)-1)*100:>5.1f}%  maxDD {dd_h*100:>6.1f}%")
    print(f"       positive months {float((bm>0).mean())*100:.0f}% | best {bm.max()*100:.1f}% | worst {bm.min()*100:.1f}%")


if __name__ == "__main__":
    main()
