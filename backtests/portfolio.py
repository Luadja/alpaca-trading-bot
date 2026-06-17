"""Portfolio-level backtester: daily-rebalanced, weight-based, multi-symbol.

The per-symbol harness (backtest_stoch_rsi_mfi via backtesting.py) tests each symbol
independently at full equity, so it CANNOT express two things we want to validate:
  * vol-targeting  -> position sizing ACROSS the portfolio (inverse-vol weights), and
  * cross-sectional momentum -> ranking ACROSS symbols and holding the strongest N.

This module models a single portfolio: a weight per symbol per day, transaction costs on
turnover, and one equity curve.

NO LOOK-AHEAD (the cardinal rule): a weight decided from data up to close[t] earns the
return close[t] -> close[t+1]. Every strategy returns target weights indexed by the date
they are DECIDED; the backtester shifts them one bar before multiplying by realized returns,
so today's weight can never earn today's (already-known) return.

Pure / dependency-light (numpy + pandas) so it is unit-testable without network or a broker.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def build_panel(bars: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Align per-symbol close prices into one (date x symbol) panel on the dates where ALL
    symbols have data (inner join) — cross-sectional ranking needs a common cross-section."""
    closes = {sym: df["close"] for sym, df in bars.items() if not df.empty}
    panel = pd.DataFrame(closes).dropna(how="any").sort_index()
    return panel


# --- strategy weight builders (each: data up to t -> target weight decided at close[t]) ----
def trend_state(panel: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    """1.0 while SMA(fast) > SMA(slow) (long), else 0.0 — the trend HOLDING state, per symbol.
    Causal: the SMAs at t use only closes up to t; the warmup NaNs resolve to 0 (flat)."""
    f = panel.rolling(fast).mean()
    s = panel.rolling(slow).mean()
    return (f > s).fillna(False).astype(float)


def weights_trend_equal(panel: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    """Equal-weight trend: each in-trend symbol gets 1/N, the rest cash. Gross exposure scales
    with breadth (more trends on -> more invested), which de-risks weak regimes automatically."""
    n = panel.shape[1]
    return trend_state(panel, fast, slow) / n


def weights_trend_invvol(panel: pd.DataFrame, fast: int, slow: int, vol_lookback: int = 60) -> pd.DataFrame:
    """Inverse-volatility trend (#2): same in-trend set as equal-weight and the SAME gross
    exposure (count_longs / N) each day, but redistributed toward lower-vol names — so the
    comparison isolates the SIZING effect, not added leverage."""
    n = panel.shape[1]
    state = trend_state(panel, fast, slow)
    rets = panel.pct_change()
    vol = rets.rolling(vol_lookback).std()
    inv = (1.0 / vol).replace([np.inf, -np.inf], np.nan)
    raw = (state * inv).fillna(0.0)
    rawsum = raw.sum(axis=1).replace(0.0, np.nan)
    gross_target = state.sum(axis=1) / n  # == equal-weight's gross (count_longs / N)
    w = raw.div(rawsum, axis=0).mul(gross_target, axis=0).fillna(0.0)
    return w


def weights_xsec_momentum(
    panel: pd.DataFrame, lookback: int = 252, skip: int = 21, top_n: int = 3, rebalance_days: int = 21
) -> pd.DataFrame:
    """Cross-sectional momentum (#3): every ``rebalance_days``, rank symbols by trailing return
    over [t-lookback, t-skip] (skip the last month to avoid short-term reversal — the classic
    12-1 momentum), hold the top_n equal-weighted (1/top_n each), cash otherwise; hold between
    rebalances. Long-only, fully invested in the top_n. Causal: momentum uses only past closes."""
    mom = panel.shift(skip) / panel.shift(lookback) - 1.0
    w = pd.DataFrame(np.nan, index=panel.index, columns=panel.columns)
    rebal_dates = panel.index[::rebalance_days]
    for d in rebal_dates:
        row = mom.loc[d].dropna()
        w.loc[d] = 0.0
        if len(row) >= top_n:
            w.loc[d, row.nlargest(top_n).index] = 1.0 / top_n
    return w.ffill().fillna(0.0)


def weights_equal_buy_hold(panel: pd.DataFrame) -> pd.DataFrame:
    """Benchmark: hold every symbol at 1/N, rebalanced daily (the 'just own the universe' line)."""
    n = panel.shape[1]
    return pd.DataFrame(1.0 / n, index=panel.index, columns=panel.columns)


# --- backtest + metrics --------------------------------------------------------------------
@dataclass(frozen=True)
class PortfolioResult:
    net_returns: pd.Series
    equity: pd.Series
    metrics: dict


def backtest(weights: pd.DataFrame, panel: pd.DataFrame, cost_per_side: float = 0.0005) -> PortfolioResult:
    """Run target weights against realized returns with turnover costs. NO LOOK-AHEAD: weights
    are shifted one bar, so the weight set at close[t-1] earns the t-1->t return, and its
    rebalancing cost is charged the bar it is established (one bar before it earns)."""
    rets = panel.pct_change()
    ws = weights.shift(1)  # the weight working today was decided at yesterday's close
    gross = (ws * rets).sum(axis=1)
    # turnover establishing `ws` was done the prior bar; charge its cost then (no look-ahead).
    cost = cost_per_side * ws.diff().abs().sum(axis=1)
    net = (gross - cost).dropna()
    equity = (1.0 + net).cumprod()
    return PortfolioResult(net, equity, metrics(net))


def metrics(net: pd.Series, periods_per_year: int = 252) -> dict:
    net = net.dropna()
    if len(net) < 2 or net.std() == 0:
        return {"cagr": float("nan"), "sharpe": float("nan"), "sortino": float("nan"),
                "maxdd": float("nan"), "vol": float("nan"), "n": len(net)}
    equity = (1.0 + net).cumprod()
    years = len(net) / periods_per_year
    cagr = equity.iloc[-1] ** (1.0 / years) - 1.0 if equity.iloc[-1] > 0 else float("nan")
    sharpe = net.mean() / net.std() * np.sqrt(periods_per_year)
    downside = net[net < 0].std()
    sortino = net.mean() / downside * np.sqrt(periods_per_year) if downside and downside > 0 else float("nan")
    maxdd = float((equity / equity.cummax() - 1.0).min())
    return {
        "cagr": float(cagr), "sharpe": float(sharpe), "sortino": float(sortino),
        "maxdd": maxdd, "vol": float(net.std() * np.sqrt(periods_per_year)), "n": len(net),
        "sharpe_pp": float(net.mean() / net.std()),  # per-period (for Deflated Sharpe)
    }


def slice_window(panel: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    out = panel
    if start is not None:
        out = out[out.index >= pd.Timestamp(start, tz=out.index.tz)]
    if end is not None:
        out = out[out.index < pd.Timestamp(end, tz=out.index.tz)]
    return out
