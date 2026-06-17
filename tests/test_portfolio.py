import numpy as np
import pandas as pd

from backtests import portfolio as pf


def _idx(n, start="2020-01-01"):
    return pd.date_range(start, periods=n, freq="D", tz="UTC")


def test_no_lookahead_weight_cannot_earn_concurrent_return():
    # Price jumps +100% from day1->day2. A weight set ON the jump day (day2) must NOT capture
    # it — it can only earn the day2->day3 return (= 0). If the shift were missing, it would.
    idx = _idx(4)
    panel = pd.DataFrame({"A": [100.0, 100.0, 200.0, 200.0]}, index=idx)
    weights = pd.DataFrame({"A": [0.0, 0.0, 1.0, 0.0]}, index=idx)  # long only on the jump day
    res = pf.backtest(weights, panel, cost_per_side=0.0)
    assert abs(res.net_returns.sum()) < 1e-12  # captured nothing -> no look-ahead


def test_weight_earns_next_bar_return():
    # A weight set the bar BEFORE the jump earns it in full.
    idx = _idx(4)
    panel = pd.DataFrame({"A": [100.0, 100.0, 200.0, 200.0]}, index=idx)
    weights = pd.DataFrame({"A": [0.0, 1.0, 0.0, 0.0]}, index=idx)  # long on day1 (pre-jump)
    res = pf.backtest(weights, panel, cost_per_side=0.0)
    assert abs(res.net_returns.loc[idx[2]] - 1.0) < 1e-12  # +100% earned on day2


def test_turnover_cost_is_charged():
    idx = _idx(4)
    panel = pd.DataFrame({"A": [100.0, 100.0, 100.0, 100.0]}, index=idx)  # flat -> gross 0
    weights = pd.DataFrame({"A": [0.0, 1.0, 0.0, 0.0]}, index=idx)        # buy then sell
    free = pf.backtest(weights, panel, cost_per_side=0.0).net_returns.sum()
    paid = pf.backtest(weights, panel, cost_per_side=0.01).net_returns.sum()
    assert abs(free) < 1e-12 and paid < -1e-9  # cost only drags when turnover is charged


def test_trend_equal_weights_sum_to_breadth():
    # All symbols in a clean uptrend -> all in-trend -> each gets 1/N, gross == 1.
    idx = _idx(300)
    t = np.arange(300)
    panel = pd.DataFrame({"A": 100 * 1.001 ** t, "B": 50 * 1.001 ** t}, index=idx)
    w = pf.weights_trend_equal(panel, fast=20, slow=100)
    assert abs(w.iloc[-1].sum() - 1.0) < 1e-9
    assert (w.iloc[-1] == 0.5).all()


def test_invvol_same_gross_more_weight_to_low_vol():
    idx = _idx(300)
    t = np.arange(300)
    a = 100 * 1.0008 ** t                          # smooth -> low vol
    b = 100 * 1.0008 ** t * (1 + 0.04 * np.sin(t / 3.0))  # oscillating -> higher vol
    panel = pd.DataFrame({"A": a, "B": b}, index=idx)
    eq = pf.weights_trend_equal(panel, 20, 100).iloc[-1]
    iv = pf.weights_trend_invvol(panel, 20, 100, vol_lookback=60).iloc[-1]
    assert abs(iv.sum() - eq.sum()) < 1e-6   # inverse-vol keeps the SAME gross exposure
    assert iv["A"] > iv["B"]                 # ...just tilted toward the lower-vol name


def test_xsec_holds_top_n():
    idx = _idx(80)
    t = np.arange(80)
    # four symbols, increasing drift D > C > B > A
    panel = pd.DataFrame({
        "A": 100 * 1.0000 ** t, "B": 100 * 1.0005 ** t,
        "C": 100 * 1.0010 ** t, "D": 100 * 1.0020 ** t,
    }, index=idx)
    w = pf.weights_xsec_momentum(panel, lookback=20, skip=2, top_n=2, rebalance_days=5)
    last = w.iloc[-1]
    assert (last > 0).sum() == 2 and abs(last.sum() - 1.0) < 1e-9
    assert set(last[last > 0].index) == {"C", "D"}  # the two strongest


def test_dual_momentum_cash_filter():
    idx = _idx(80)
    t = np.arange(80)
    # rising: every asset has positive momentum -> top_n all pass the absolute filter -> gross 1
    up = pd.DataFrame({c: 100 * 1.001 ** (t * (i + 1)) for i, c in enumerate("ABCD")}, index=idx)
    w_up = pf.weights_dual_momentum(up, lookback=20, skip=2, top_n=2, rebalance_days=5)
    assert abs(w_up.iloc[-1].sum() - 1.0) < 1e-9
    # falling: every asset has negative momentum -> all filtered out -> 100% cash (crash defense)
    down = pd.DataFrame({c: 100 * 0.999 ** (t * (i + 1)) for i, c in enumerate("ABCD")}, index=idx)
    w_dn = pf.weights_dual_momentum(down, lookback=20, skip=2, top_n=2, rebalance_days=5)
    assert w_dn.iloc[-1].sum() == 0.0


def test_vol_target_scales_gross_inversely_to_vol():
    # Calm asset -> low realized vol -> overlay levers gross UP toward the target (capped).
    idx = _idx(300)
    t = np.arange(300)
    calm = pd.DataFrame({"A": 100 * 1.0002 ** t}, index=idx)  # smooth, very low vol
    base = pf.weights_equal_buy_hold(calm)  # gross 1.0
    scaled = pf.vol_target(base, calm, target_vol=0.10, vol_lookback=60, max_gross=3.0)
    assert scaled.iloc[-1].sum() > 1.0  # low-vol regime -> levered above 1x (toward target)
    assert scaled.iloc[-1].sum() <= 3.0 + 1e-9  # never exceeds the cap


def test_xsec_invvol_tilts_to_low_vol_within_topn():
    idx = _idx(300)
    t = np.arange(300)
    a = 100 * 1.0015 ** t                                  # strong, smooth (low vol)
    b = 100 * 1.0015 ** t * (1 + 0.05 * np.sin(t / 3.0))   # strong, jagged (high vol)
    panel = pd.DataFrame({"A": a, "B": b}, index=idx)
    w = pf.weights_xsec(panel, lookbacks=(60,), skip=2, top_n=2, rebalance_days=5,
                        scheme="invvol", vol_lookback=60).iloc[-1]
    assert abs(w.sum() - 1.0) < 1e-6 and w["A"] > w["B"]  # both held, tilted to lower-vol A


def test_risk_parity_underweights_volatile_asset():
    idx = _idx(120)
    t = np.arange(120)
    a = 100 * 1.0005 ** t                                  # low vol
    b = 100 * 1.0005 ** t * (1 + 0.06 * np.sin(t / 2.0))   # high vol
    panel = pd.DataFrame({"A": a, "B": b}, index=idx)
    w = pf.weights_risk_parity(panel, vol_lookback=40, rebalance_days=5).iloc[-1]
    assert abs(w.sum() - 1.0) < 1e-6 and w["A"] > w["B"]


def test_build_panel_inner_join():
    a = pd.DataFrame({"close": [1, 2, 3]}, index=_idx(3))
    b = pd.DataFrame({"close": [10, 20]}, index=_idx(2, "2020-01-02"))  # offset by a day
    panel = pf.build_panel({"A": a, "B": b})
    assert list(panel.columns) == ["A", "B"] and len(panel) == 2  # only common dates
